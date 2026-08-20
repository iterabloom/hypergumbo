# SPDX-License-Identifier: AGPL-3.0-or-later
"""Installer + availability helpers for the ``rust-analyzer`` binary (WI-dotud).

The rust-analyzer backend (WI-duzul) depends on the ``rust-analyzer``
executable being resolvable on the user's ``PATH``. This module mirrors
the ``gitleaks`` / ``embeddings`` installer pattern already in this
package so ``hypergumbo install-rust-analyzer`` slots cleanly next to
``hypergumbo install-gitleaks`` and ``hypergumbo install-embeddings``.

Install strategy
----------------
``rust-analyzer`` is primarily distributed through two channels:

1. **rustup component** — ``rustup component add rust-analyzer`` on a
   developer machine that already has ``rustup`` will install the
   matching rust-analyzer for the active toolchain and surface it via
   the rustup shim that's already on ``PATH``. This is the upstream-
   blessed path: it picks up a version compatible with the installed
   Rust toolchain automatically and handles updates through the usual
   ``rustup update`` flow.

2. **Static binary download** — GitHub Releases pre-built binaries from
   ``rust-lang/rust-analyzer``. This is the fallback when ``rustup`` is
   not present on ``PATH`` (some CI environments, some containers).

This first cut only implements path 1 because ``rustup`` is ubiquitous
on the kinds of machines that would benefit from the SCIP backend
(development boxes with a full Rust toolchain). Path 2 (GitHub-release
binary download, extraction, XDG cache install, PATH wiring) is tracked
as a follow-up WI if the rustup-only path proves insufficient.

Uninstall is symmetric: when we installed via rustup, we remove via
``rustup component remove rust-analyzer``. Components installed outside
this module (e.g. a system package or a hand-copied binary) are left
alone — we never clobber user-managed installs.

Availability check
------------------
:func:`is_rust_analyzer_available` is the single source of truth for
"can the rust-analyzer backend activate?" — it's called by the
``--check`` flag, by the ``hypergumbo add-extras --check`` umbrella
(WI-huham → WI-josif), and by WI-nohah's graceful-degrade fall-through
logic in the rust-analyzer backend (WI-duzul).
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess  # nosec B404 — required for rustup invocations
import sys
from typing import Callable, Optional

from .safety_zones import repo_inspect_probe


def is_rust_analyzer_integration_installed() -> bool:
    """Return True iff the ``hypergumbo-lang-rust-analyzer`` Python wrapper imports.

    Distinct from :func:`is_rust_analyzer_available`, which only checks the
    rustup-installed binary on ``PATH``. The SCIP backend needs both —
    the binary to invoke and the Python integration package to drive it.

    BUG-06 (WI-jinoh): the published v4.1.0 ``hypergumbo`` distribution
    has the binary installer but does not ship the integration package
    in ``Requires-Dist``, so ``--backend rust-analyzer`` silently
    no-ops. Callers gate on this helper to surface the structural
    absence at CLI parse time rather than letting the user think they
    engaged the SCIP backend when they didn't.

    Uses :func:`importlib.util.find_spec` rather than a real ``import``
    so the check is side-effect-free even when the package is present —
    we don't want to load the integration's import-time module-level
    code as a side effect of every CLI invocation that happens to pass
    ``--backend``.
    """
    return importlib.util.find_spec("hypergumbo_lang_rust_analyzer") is not None


def is_rust_analyzer_available(
    *,
    which: Optional[Callable[[str], Optional[str]]] = None,
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> bool:
    """Return True when the ``rust-analyzer`` binary is functional.

    Two-step check. First, ``shutil.which("rust-analyzer")`` resolves to
    a binary path. Second, the binary actually runs: ``<binary>
    --version`` with a 5-second timeout, exit code 0.

    Step 2 catches the rustup-proxy-without-component edge case —
    ``~/.cargo/bin/rust-analyzer`` exists and is executable (rustup
    ships a proxy for every Rust binary), but the proxy errors out
    (``error: Unknown binary 'rust-analyzer' in official toolchain
    ...``) when the matching ``rust-analyzer`` rustup component has
    not been added. Existence-only detection passed; the smoke test
    fails. This closes a v5.0.0 partial-fix gap where ``--backend
    rust-analyzer`` silently fell through to tree-sitter on machines
    in this rustup state, with no user-visible signal.

    The earlier shape skipped the smoke test on the assumption that a
    ``--version`` round-trip is "slow enough to be user-visible" — in
    practice it is sub-100ms, well below the threshold for any caller
    we have, so the cost no longer outweighs the correctness gain.

    The ``which`` and ``runner`` kwargs are injectable so tests can
    exercise every branch without mutating ``PATH`` or shelling out.
    """
    resolve = which if which is not None else shutil.which
    resolved = resolve("rust-analyzer")
    if resolved is None:
        return False

    # WI-fasuv: repo_inspection zone. The injectable `runner` is preserved for
    # tests; the DEFAULT is the wrapper, which is what production takes.
    run = runner if runner is not None else repo_inspect_probe
    try:
        completed = run(
            [resolved, "--version"],
            capture_output=True,
            timeout=5.0,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return completed.returncode == 0


def install_rust_analyzer(
    *,
    quiet: bool = False,
    which: Optional[Callable[[str], Optional[str]]] = None,
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> bool:
    """Install ``rust-analyzer`` via rustup, returning success.

    When ``rustup`` is not on ``PATH`` the function prints a pointer to
    the upstream install instructions and returns False — explicitly
    not attempting the static-binary-download fallback in this first
    cut (see module docstring).

    ``which`` and ``runner`` are injectable to make the function
    testable without a real ``rustup`` invocation.
    """
    resolve = which if which is not None else shutil.which
    run = runner if runner is not None else subprocess.run

    rustup = resolve("rustup")
    if rustup is None:
        print(
            "Error: rustup is not on PATH. Install rustup from "
            "https://rustup.rs/ first, or install rust-analyzer from "
            "https://rust-lang.github.io/rust-analyzer/ manually and "
            "ensure it is on PATH.",
            file=sys.stderr,
        )
        return False

    if not quiet:
        print("Installing rust-analyzer via rustup...")

    try:
        completed = run(  # nosec B603
            [rustup, "component", "add", "rust-analyzer"],
            capture_output=True,
            timeout=300.0,
        )
    except subprocess.TimeoutExpired:
        print(
            "Error: rustup component add rust-analyzer timed out.",
            file=sys.stderr,
        )
        return False
    except OSError as exc:
        print(f"Error invoking rustup: {exc}", file=sys.stderr)
        return False

    if completed.returncode != 0:
        stderr = completed.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        print(
            f"Error: rustup component add rust-analyzer exited "
            f"{completed.returncode}. {stderr.strip()}",
            file=sys.stderr,
        )
        return False

    if not quiet:
        print("  Done!")
    return True


def uninstall_rust_analyzer(
    *,
    quiet: bool = False,
    which: Optional[Callable[[str], Optional[str]]] = None,
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> bool:
    """Remove ``rust-analyzer`` via rustup, returning success.

    Non-rustup installs (system package manager, hand-copied binary)
    are left alone — uninstall is a no-op success in that branch so the
    CLI surface can be idempotent without clobbering user-managed
    binaries.
    """
    resolve = which if which is not None else shutil.which
    run = runner if runner is not None else subprocess.run

    rustup = resolve("rustup")
    if rustup is None:
        if not quiet:
            print(
                "rustup not on PATH; nothing to uninstall via "
                "hypergumbo. If rust-analyzer was installed outside "
                "rustup, remove it manually.",
            )
        return True

    if not quiet:
        print("Removing rust-analyzer via rustup...")

    try:
        completed = run(  # nosec B603
            [rustup, "component", "remove", "rust-analyzer"],
            capture_output=True,
            timeout=120.0,
        )
    except subprocess.TimeoutExpired:
        print(
            "Error: rustup component remove rust-analyzer timed out.",
            file=sys.stderr,
        )
        return False
    except OSError as exc:
        print(f"Error invoking rustup: {exc}", file=sys.stderr)
        return False

    if completed.returncode != 0:
        stderr = completed.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        print(
            f"Error: rustup component remove rust-analyzer exited "
            f"{completed.returncode}. {stderr.strip()}",
            file=sys.stderr,
        )
        return False

    if not quiet:
        print("  Done!")
    return True
