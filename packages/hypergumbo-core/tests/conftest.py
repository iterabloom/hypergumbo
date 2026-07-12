# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pytest configuration for hypergumbo-core tests.

Includes self-healing pytest wrapper repair (ADR-0010) and an
autouse fixture that isolates the hypergumbo cache directory per
test to prevent state leakage between pytest sessions.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _hg_discover_analyzers_before_any_test() -> None:
    """Populate the analyzer registry once per (xdist worker) session, before
    any test can snapshot an incomplete copy of it.

    Root cause (INV-tajik): ``analyze.registry._ANALYZER_REGISTRY`` is filled by
    import-time ``@register_analyzer`` decorators, which do NOT re-fire on
    cached re-imports. So once ``clear_registry()`` runs, a decorator-registered
    analyzer is recoverable only from a saved snapshot — ``ensure_discovered()``
    cannot repopulate it (the modules are already in ``sys.modules``). Tests
    that clear+save+restore the registry (``test_analyzer_registry``,
    ``test_orchestrator_fail_open``) capture an INCOMPLETE (sometimes empty)
    snapshot when full discovery has not run yet, and restore that incomplete
    state; a later consumer's ``ensure_discovered()`` then no-ops on the stale
    ``_discovered`` flag, so ``test_emission_parity_matrix``'s
    ``run_analyzer('python'/'go')`` raises ``KeyError: ... none registered``
    under some pytest-xdist orderings.

    Forcing full discovery here — as a session-scoped autouse fixture, which
    instantiates before any function-scoped clearing fixture on the first
    test — guarantees the registry is fully populated before the first
    snapshot, so every save/restore round-trips the complete set. Mirrored in
    the repo-root ``conftest.py`` for cross-package runs whose rootdir is the
    repo root rather than this package.
    """
    from hypergumbo_core.analyze.registry import ensure_discovered

    ensure_discovered()


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

    Hypergumbo caches analysis results under
    ``~/.cache/hypergumbo/<fingerprint>/results/<state_hash>/`` keyed
    by the absolute path of the analyzed repo.  pytest rotates only
    the last few ``pytest-<N>`` tmpdirs, so the same ``tmp_path``
    string is recreated across pytest sessions and yields the same
    fingerprint.  Because the cache is never cleaned, a stale results
    file from a previous session can survive into a later one and
    short-circuit auto-run code paths whose tests assert that no
    cache hit occurred.

    Tests that explicitly want the default ``XDG_CACHE_HOME`` (e.g.,
    ``test_xdg_cache_base_default``) can still call
    ``monkeypatch.delenv("XDG_CACHE_HOME", raising=False)`` to undo
    this isolation for the duration of the test.

    ``HF_HOME`` is pinned to the user's real cache so HuggingFace Hub
    doesn't see the per-test ``XDG_CACHE_HOME`` as an empty cache and
    re-download ~1.5GB of model weights (``microsoft/unixcoder-base`` +
    ``nomic-ai/modernbert-embed-base``) into each pytest-xdist worker's
    tmpdir. Without this pin, /tmp tmpfs fills within a single suite run.

    ``HF_HUB_OFFLINE=1`` forces HF Hub to skip every network call —
    including the xet daemon's cache-freshness ping that ignores
    ``local_files_only=True`` and hangs forever on a CLOSE-WAIT'd proxy
    connection. Tests don't need fresh model weights; they need
    reproducibility, which offline-mode delivers cheaply.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "_xdg_cache"))
    monkeypatch.setenv("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")


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
