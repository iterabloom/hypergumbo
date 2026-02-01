"""Pytest configuration for hypergumbo (repo root).

Includes self-healing pytest wrapper repair (ADR-0010).

When pytest is invoked, this conftest.py checks if the .venv/bin/pytest
wrapper is intact. If pip has overwritten it with the standard entry point,
this module repairs it and re-execs through the fixed wrapper, ensuring
smart-test is always used for test selection.

Note: Each package (hypergumbo-core, hypergumbo-lang-*, etc.) has its own
conftest.py with the same self-healing logic. This ensures repair works
regardless of which directory pytest is run from.

Escape hatches:
    python -m pytest ...              # Bypass wrapper completely
    SMART_TEST_ACTIVE=1 pytest ...    # Skip smart-test logic
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Repo root is the directory containing this file
_REPO_ROOT = Path(__file__).resolve().parent


def _find_venv_dir() -> Path | None:
    """Find the venv directory, checking common names."""
    for candidate in [".venv", "venv", ".env", "env"]:
        venv_path = _REPO_ROOT / candidate
        if venv_path.is_dir() and (venv_path / "bin" / "activate").exists():
            return venv_path
    return None


def _wrapper_is_valid(venv_dir: Path) -> bool:
    """Check if the pytest wrapper is our smart-test wrapper, not pip's entry point.

    We check for the SMART_TEST_ACTIVE marker which is unique to our wrapper.
    pip's entry point contains 'from pytest import console_main' instead.
    """
    wrapper = venv_dir / "bin" / "pytest"
    if not wrapper.exists():
        return False

    try:
        content = wrapper.read_text()
        # Our wrapper contains this env var check; pip's entry point doesn't
        return "SMART_TEST_ACTIVE" in content
    except Exception:
        return False


def _repair_wrapper() -> bool:
    """Repair the pytest wrapper by running install-hooks --repair-shims."""
    install_hooks = _REPO_ROOT / "scripts" / "install-hooks"

    if not install_hooks.exists():
        return False

    try:
        result = subprocess.run(  # noqa: S603 - trusted internal script
            [str(install_hooks), "--repair-shims"],
            capture_output=True,
            timeout=10,
            cwd=str(_REPO_ROOT),
        )
        return result.returncode == 0
    except Exception:
        return False


def pytest_configure(config):
    """Ensure pytest wrapper is intact; repair and re-exec if needed.

    This hook runs early in pytest startup. If we detect the wrapper has been
    overwritten (e.g., by pip install pytest), we repair it and re-exec so
    the test run goes through smart-test.
    """
    # Skip if we're already running through smart-test
    if os.environ.get("SMART_TEST_ACTIVE"):
        return

    # Find the venv
    venv_dir = _find_venv_dir()
    if venv_dir is None:
        return  # No venv, nothing to repair

    # Check if wrapper is valid
    if _wrapper_is_valid(venv_dir):
        return  # Wrapper is intact

    # Wrapper is broken (probably pip overwrote it), try to repair
    if _repair_wrapper():
        print(
            "\n⚠️  pytest wrapper repaired. Re-running through smart-test...\n",
            file=sys.stderr,
        )
        # Re-exec through the now-fixed wrapper
        # This will go through smart-test for proper test selection
        os.execv(sys.argv[0], sys.argv)  # noqa: S606 - intentional re-exec
    else:
        # Repair failed, warn but continue with real pytest
        import warnings

        warnings.warn(
            "pytest wrapper repair failed. Run ./scripts/install-hooks to fix.",
            stacklevel=1,
        )
