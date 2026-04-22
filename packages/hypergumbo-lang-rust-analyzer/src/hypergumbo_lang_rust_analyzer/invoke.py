# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shell-out wrapper for ``rust-analyzer scip`` (WI-duzul Slice B-first).

How It Works
------------
Calling ``rust-analyzer scip /path/to/workspace`` emits ``index.scip``
in the current working directory. This module wraps that invocation in
a single function, :func:`run_rust_analyzer_scip`, that:

1. Confirms ``rust-analyzer`` is resolvable on ``PATH`` (or at the
   explicit binary path the caller passes in).
2. Invokes ``rust-analyzer scip <workspace>`` with stdout/stderr
   captured, a configurable timeout, and a scratch cwd.
3. Returns the emitted SCIP index as ``bytes`` suitable for feeding
   straight into :func:`translate_scip_to_hg`.

Failure modes are mapped to three dedicated exceptions so callers can
discriminate between "binary not installed" (WI-nohah territory — fall
through to ``rust.py``), "invocation failed or timed out" (genuine
error — surface to the user), and "binary ran but produced no .scip
file" (likely a cargo metadata error — surface with the captured
stderr).

Why This Design
---------------
* WI-zakub established that rust-analyzer SCIP indexing is 10x slower
  than tree-sitter at every realistic size. The ``timeout`` argument
  defaults to 600 seconds so an unexpectedly large workspace fails
  visibly rather than hanging the analyzer pipeline.
* A dedicated ``rust_analyzer_bin`` kwarg (default ``"rust-analyzer"``)
  keeps the function testable without shell PATH mutations: tests
  pass an absolute path to a fake script. Production callers use the
  default to pick up the user's install.
* The scratch ``cwd`` is supplied by the caller (typically a
  ``tempfile.mkdtemp()``); this module does not own temporary
  directories so callers can manage cleanup and reuse for caching if
  they choose.

Out of scope for this module (tracked under WI-duzul Slice C+):
* Analyzer-registry wiring — ``RustAnalyzerAnalyzer`` class that hooks
  into the analyzer base class and is registered at higher priority
  than ``rust.py``.
* The opt-in flag (``HYPERGUMBO_RUST_ANALYZER`` env var +
  ``--backend rust-analyzer`` CLI flag) that gates whether the shell-
  out fires at all.
* Graceful-degrade when the binary is absent (WI-nohah). Callers
  decide what to do with :class:`RustAnalyzerNotInstalled`.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 — required for rust-analyzer scip invocation
from pathlib import Path
from typing import Optional


class RustAnalyzerError(Exception):
    """Base class for :func:`run_rust_analyzer_scip` failures."""


class RustAnalyzerNotInstalled(RustAnalyzerError):
    """Raised when the ``rust-analyzer`` binary is not resolvable.

    Callers should catch this and fall through to the tree-sitter
    ``rust.py`` analyzer (WI-nohah). The message carries the binary
    name the caller asked for so a misconfigured ``rust_analyzer_bin``
    kwarg is visible in the error text.
    """


class RustAnalyzerInvocationFailed(RustAnalyzerError):
    """Raised when the binary ran but exited non-zero or timed out.

    The exception carries the captured stderr (best-effort; empty
    bytes when the process was killed before producing any output) so
    the caller can surface it to the user.
    """

    def __init__(self, message: str, stderr: bytes) -> None:
        super().__init__(message)
        self.stderr = stderr


class RustAnalyzerNoOutput(RustAnalyzerError):
    """Raised when the invocation succeeded but no ``index.scip`` was written.

    This happens when ``rust-analyzer`` cannot parse the workspace
    (e.g. missing ``Cargo.toml`` at the requested path, or a
    ``cargo metadata`` error) but still exits 0. The captured stderr is
    attached so the caller can surface the underlying cargo diagnostic.
    """

    def __init__(self, message: str, stderr: bytes) -> None:
        super().__init__(message)
        self.stderr = stderr


def run_rust_analyzer_scip(
    workspace: Path,
    *,
    cwd: Path,
    rust_analyzer_bin: str = "rust-analyzer",
    timeout_sec: float = 600.0,
    which: Optional[callable] = None,  # type: ignore[valid-type]
    runner: Optional[callable] = None,  # type: ignore[valid-type]
) -> bytes:
    """Run ``rust-analyzer scip`` on *workspace* and return the SCIP bytes.

    ``cwd`` is the directory in which the subprocess runs; this is where
    ``index.scip`` is written and subsequently read. Callers are
    expected to pass a fresh empty directory (``tempfile.mkdtemp()`` is
    the usual pattern) so the read is unambiguous.

    ``rust_analyzer_bin`` is looked up via ``shutil.which`` unless it is
    already an absolute path. The lookup is injectable via the ``which``
    kwarg so tests can simulate a missing binary without mutating the
    process environment.

    ``runner`` is an injectable ``subprocess.run`` stand-in (signature
    ``runner(cmd, *, cwd, capture_output, timeout) -> CompletedProcess``)
    so tests can exercise the success / non-zero-exit / timeout /
    no-output paths without actually spawning rust-analyzer.

    Raises :class:`RustAnalyzerNotInstalled`, :class:`RustAnalyzerInvocationFailed`,
    or :class:`RustAnalyzerNoOutput` per the module docstring.
    """
    resolve = which if which is not None else shutil.which
    resolved = resolve(rust_analyzer_bin)
    if resolved is None:
        raise RustAnalyzerNotInstalled(
            f"rust-analyzer binary not found on PATH "
            f"(requested: {rust_analyzer_bin!r})",
        )

    # resolved comes from shutil.which (or caller-supplied absolute
    # path); never shell=True; args are list-form.
    run = runner if runner is not None else subprocess.run
    cmd = [resolved, "scip", str(workspace)]
    try:
        completed = run(  # nosec B603
            cmd,
            cwd=str(cwd),
            capture_output=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise RustAnalyzerInvocationFailed(
            f"rust-analyzer scip timed out after {timeout_sec}s",
            exc.stderr or b"",
        ) from exc

    if completed.returncode != 0:
        raise RustAnalyzerInvocationFailed(
            f"rust-analyzer scip exited {completed.returncode}",
            completed.stderr or b"",
        )

    index_path = cwd / "index.scip"
    if not index_path.is_file():
        raise RustAnalyzerNoOutput(
            "rust-analyzer scip exited 0 but produced no index.scip",
            completed.stderr or b"",
        )
    return index_path.read_bytes()
