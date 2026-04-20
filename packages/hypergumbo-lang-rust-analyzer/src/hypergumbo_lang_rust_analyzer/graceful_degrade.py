# SPDX-License-Identifier: AGPL-3.0-or-later
"""Graceful-degrade orchestrator for the SCIP-backed Rust analyzer (WI-nohah).

When a caller wants "use rust-analyzer if available, otherwise tell me
to fall through to :mod:`hypergumbo_lang_mainstream.rust`", it should
call :func:`try_analyze_with_rust_analyzer`. The helper returns
``(symbols, edges)`` on the happy path and ``None`` on any of the
three fall-through conditions WI-nohah enumerates:

1. ``rust-analyzer`` is not resolvable on ``PATH`` (no install, or
   install in a dir we don't scan).
2. ``rust-analyzer scip`` exits non-zero / times out
   (:class:`RustAnalyzerInvocationFailed`), or the workspace does not
   produce an ``index.scip`` file (:class:`RustAnalyzerNoOutput` —
   typically a ``cargo metadata`` error on a workspace with private
   deps or an unusual target triple).
3. The SCIP bytes decode fails
   (:class:`google.protobuf.message.DecodeError`) — defensive; the
   only known way to trip this is a truncated file from a killed
   ``rust-analyzer`` process, so treat it identically to failure
   mode 2.

Returning ``None`` is the contract — the caller (WI-duzul Slice C's
analyzer-registry wrapper) is responsible for the actual fall-through
to ``rust.py``. Keeping the decision point pure lets the fall-through
logic stay testable without mounting a real analyzer registry.

The ``invoke`` and ``translate`` callables are injectable so tests can
exercise every failure mode without shelling out to a real
``rust-analyzer`` binary. Production callers pass ``None`` to pick up
the default :func:`run_rust_analyzer_scip` /
:func:`translate_scip_to_hg` surfaces.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from google.protobuf.message import DecodeError

from hypergumbo_core.ir import Edge, Symbol

from .invoke import (
    RustAnalyzerError,
    run_rust_analyzer_scip,
)
from .translate import SourceReader, translate_scip_to_hg

InvokeFn = Callable[..., bytes]
TranslateFn = Callable[[bytes, SourceReader], Tuple[List[Symbol], List[Edge]]]

# One-time log marker so repeated fall-through attempts don't spam the
# user's terminal. A set because the helper may be called across
# multiple workspaces in a single process (monorepo analysis).
_LOGGED_FALLBACK: set[str] = set()


def _reset_logged_fallback_for_tests() -> None:
    """Clear the once-per-process log marker; test-only helper."""
    _LOGGED_FALLBACK.clear()


def try_analyze_with_rust_analyzer(
    workspace: Path,
    source_reader: SourceReader,
    *,
    invoke: Optional[InvokeFn] = None,
    translate: Optional[TranslateFn] = None,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[Tuple[List[Symbol], List[Edge]]]:
    """Run rust-analyzer + SCIP translate on *workspace*, or ``None``.

    The return type is intentionally ``None | (symbols, edges)`` rather
    than raising — the caller wants the decision "was this a real
    result, or should I fall through?" packaged as a single expression.
    Every failure mode WI-nohah lists maps to ``None``; only a
    successful invoke+translate produces a non-None return.

    ``source_reader`` is forwarded to :func:`translate_scip_to_hg` for
    the rust.py stable-id parity pass.

    ``invoke`` defaults to :func:`run_rust_analyzer_scip`;
    ``translate`` defaults to :func:`translate_scip_to_hg`. Both are
    injected in tests to simulate each failure shape without spawning
    a subprocess or constructing a SCIP fixture.

    ``log`` defaults to a no-op; tests (and the future analyzer
    registry wrapper) pass a real logger so the user sees one line
    explaining why the backend degraded.
    """
    invoke_fn = invoke if invoke is not None else run_rust_analyzer_scip
    translate_fn = translate if translate is not None else translate_scip_to_hg
    emit = log if log is not None else (lambda _msg: None)

    with tempfile.TemporaryDirectory(prefix="hg_rust_analyzer_") as tmpdir:
        scratch = Path(tmpdir)
        try:
            scip_bytes = invoke_fn(workspace, cwd=scratch)
        except RustAnalyzerError as exc:
            key = f"{type(exc).__name__}:{workspace}"
            if key not in _LOGGED_FALLBACK:
                _LOGGED_FALLBACK.add(key)
                emit(
                    f"rust-analyzer backend unavailable for {workspace}: "
                    f"{type(exc).__name__} — falling through to rust.py",
                )
            return None

    try:
        return translate_fn(scip_bytes, source_reader)
    except DecodeError as exc:
        key = f"DecodeError:{workspace}"
        if key not in _LOGGED_FALLBACK:
            _LOGGED_FALLBACK.add(key)
            emit(
                f"rust-analyzer SCIP decode failed for {workspace}: "
                f"{exc} — falling through to rust.py",
            )
        return None
