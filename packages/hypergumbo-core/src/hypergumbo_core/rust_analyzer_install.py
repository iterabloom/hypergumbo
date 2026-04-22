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
``--check`` flag, by the ``hypergumbo install-extras --check`` umbrella
(WI-huham), and by WI-nohah's graceful-degrade fall-through logic when
WI-duzul's registry wiring lands.
"""
from __future__ import annotations

import shutil
import subprocess  # nosec B404 — required for rustup invocations
import sys
from typing import Callable, Optional


def is_rust_analyzer_available(
    *,
    which: Optional[Callable[[str], Optional[str]]] = None,
) -> bool:
    """Return True when the ``rust-analyzer`` binary is resolvable.

    The ``which`` kwarg is injectable (defaults to :func:`shutil.which`)
    so callers can stub the check in tests without mutating ``PATH``.
    Any non-None return from the resolver counts as "available" — we
    don't try to invoke the binary because a ``rust-analyzer --version``
    round-trip is slow enough to be user-visible and the shell-out
    wrapper (:mod:`hypergumbo_lang_rust_analyzer.invoke`) catches the
    "binary present but broken" case at first use.
    """
    resolve = which if which is not None else shutil.which
    return resolve("rust-analyzer") is not None


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
