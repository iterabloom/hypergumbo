# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pytest configuration for hypergumbo-lang-common tests.

Includes self-healing pytest wrapper repair (ADR-0010).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _find_repo_root() -> Path | None:
    """Find the repo root by looking for .git directory."""
    current = Path(__file__).resolve().parent
    for _ in range(10):  # Limit search depth
        if (current / ".git").exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def _find_venv_dir(repo_root: Path) -> Path | None:
    """Find the venv directory, checking common names."""
    for candidate in [".venv", "venv", ".env", "env"]:
        venv_path = repo_root / candidate
        if venv_path.is_dir() and (venv_path / "bin" / "activate").exists():
            return venv_path
    return None


def _wrapper_is_valid(venv_dir: Path) -> bool:
    """Check if the pytest wrapper is our smart-test wrapper."""
    wrapper = venv_dir / "bin" / "pytest"
    if not wrapper.exists():
        return False
    try:
        content = wrapper.read_text()
        return "SMART_TEST_ACTIVE" in content
    except Exception:
        return False


def _repair_wrapper(repo_root: Path) -> bool:
    """Repair the pytest wrapper by running install-hooks --repair-shims."""
    install_hooks = repo_root / "scripts" / "install-hooks"
    if not install_hooks.exists():
        return False
    try:
        result = subprocess.run(
            [str(install_hooks), "--repair-shims"],
            capture_output=True,
            timeout=10,
            cwd=str(repo_root),
        )
        return result.returncode == 0
    except Exception:
        return False


@pytest.fixture(autouse=True)
def isolate_hypergumbo_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate ``~/.cache/hypergumbo/`` per test to prevent state leakage.

    Mirrors the fixture in hypergumbo-core/tests/conftest.py.  BRANCHES
    tests call ``run_behavior_map(tmp_path, ...)`` which uses the cache;
    without isolation, stale results from prior pytest sessions can
    short-circuit auto-run checks and cause flaky failures under xdist.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "_xdg_cache"))


def pytest_configure(config):
    """Ensure pytest wrapper is intact; repair and re-exec if needed."""
    if os.environ.get("SMART_TEST_ACTIVE"):
        return

    repo_root = _find_repo_root()
    if repo_root is None:
        return

    venv_dir = _find_venv_dir(repo_root)
    if venv_dir is None:
        return

    if _wrapper_is_valid(venv_dir):
        return

    if _repair_wrapper(repo_root):
        print(
            "\n⚠️  pytest wrapper repaired. Re-running through smart-test...\n",
            file=sys.stderr,
        )
        os.execv(sys.argv[0], sys.argv)  # noqa: S606 - intentional re-exec
    else:
        import warnings
        warnings.warn(
            "pytest wrapper repair failed. Run ./scripts/install-hooks to fix.",
            stacklevel=1,
        )
