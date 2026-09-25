# SPDX-License-Identifier: AGPL-3.0-or-later
"""Graceful-degrade orchestrator for the SCIP-backed Rust analyzer (WI-nohah).

When a caller wants "use rust-analyzer if available, otherwise tell me
to fall through to :mod:`hypergumbo_lang_mainstream.rust`", it should
call :func:`try_analyze_with_rust_analyzer`. The helper returns
``(symbols, edges)`` on the happy path and ``None`` on any of the
three fall-through conditions WI-nohah enumerates:

1. ``rust-analyzer`` is not resolvable on ``PATH``
   (:class:`RustAnalyzerNotInstalled` — no install, or an install in a
   dir we don't scan).
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

Returning ``None`` is the contract — the caller (the analyzer-registry
wrapper in ``analyzer.py``, which is shipped and passes a real ``log``) is
responsible for the actual fall-through to ``rust.py``. Keeping the
decision point narrow lets the fall-through logic stay testable without
mounting a real analyzer registry. Any other :class:`RustAnalyzerError`
falls to the base handler and degrades with its own message rather than
escaping.

Beyond the ``None``/``(symbols, edges)`` contract this module owns the
degrade DIAGNOSTICS (WI-todon), whose whole purpose is that a silent
fall-through is indistinguishable from a backend that simply did nothing.
It logs once per ``(exception type, workspace)`` so a repeated failure in
a multi-crate workspace does not bury the console, tails the child's
stderr to a bounded length, recognises the SIGKILL/OOM shape and says so
by name, and scopes each invocation to a ``tmp_artifact`` scratch dir.

The ``invoke`` and ``translate`` callables are injectable so tests can
exercise every failure mode without shelling out to a real
``rust-analyzer`` binary. Production callers pass ``None`` to pick up
the default :func:`run_rust_analyzer_scip` /
:func:`translate_scip_to_hg` surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Protocol, Tuple

from google.protobuf.message import DecodeError

from hypergumbo_core.ir import Edge, Symbol
from hypergumbo_core.safety_zones import tmp_artifact_dir

from hypergumbo_core.pass_silence import (
    DEPENDENCY_UNAVAILABLE as _DEPENDENCY_UNAVAILABLE,
    PASS_CRASHED as _PASS_CRASHED,
    UNREPORTED as _UNREPORTED,
)

from .invoke import (
    RustAnalyzerError,
    RustAnalyzerInvocationFailed,
    RustAnalyzerNoOutput,
    RustAnalyzerNotInstalled,
    run_rust_analyzer_scip,
)
from .translate import SourceReader, translate_scip_to_hg
# The axis constants are ``Final[str]`` in hypergumbo-core but resolve as
# ``Any`` across the package boundary under ``mypy --strict``, so returning
# them directly trips no-any-return. Re-bind with an explicit annotation
# rather than widening the function's return type or adding an ignore.
DEPENDENCY_UNAVAILABLE: str = _DEPENDENCY_UNAVAILABLE
PASS_CRASHED: str = _PASS_CRASHED
UNREPORTED: str = _UNREPORTED

InvokeFn = Callable[..., bytes]
class TranslateFn(Protocol):
    """The translator's call shape, as a Protocol rather than a Callable alias.

    ``Callable[[bytes, SourceReader], ...]`` cannot express the keyword-only
    ``run_id`` this function now forwards (WI-didag), and widening it to
    ``Callable[..., ...]`` would drop argument checking on the injection seam
    that every graceful-degrade test uses.
    """

    def __call__(
        self, scip_bytes: bytes, source_reader: SourceReader, *, run_id: str = ...,
    ) -> Tuple[List[Symbol], List[Edge]]: ...

@dataclass(frozen=True)
class ScipAttempt:
    """What one SCIP attempt actually did — WI-luvud's discriminated outcome.

    This replaces a bare ``Optional[Tuple[symbols, edges]]`` whose ``None``
    collapsed FOUR different states the exception taxonomy already told apart:
    a missing binary, a non-zero exit, an exit-0-with-no-index, and an
    undecodable SCIP blob. The caller filed all four under one prose string
    ("rust-analyzer backend produced no output") and one code (``unreported``),
    so a user could not tell "install rust-analyzer" from "your workspace is
    not a cargo project" from "the indexer crashed".

    ``silence_code`` is a value on the closed pass-silence-reason axis
    (``hypergumbo_core.pass_silence``), chosen per state:

    ``dependency_unavailable``
        The binary is not resolvable. "Install the package" is the right
        advice, which is exactly what the value means.
    ``pass_crashed``
        A non-zero exit, a timeout, or an undecodable index — a contained
        raise. ADR-0056's init-failure ruling put caught constructor
        exceptions here on the explicit grounds that the value "says nothing
        about WHO contained the raise"; a contained subprocess failure is the
        same shape, and "install the package" would be wrong advice for it.
    ``unreported``
        rust-analyzer exited 0 and wrote no index — it could not parse the
        workspace as a cargo project. This is NOT a crash and NOT a missing
        dependency, and the axis has no value for "an enabled backend
        completed without producing anything to read". It keeps the declared
        residue rather than inventing a value, and the gap stays filed on
        WI-luvud rather than being papered over with a plausible neighbour.

    A SUCCESS carries no silence code: ``failed`` is False and the pass RAN,
    even when it produced nothing. That case is the one WI-luvud is named for
    and it belongs in the ran population, not in ``skipped_passes``.
    """

    symbols: List[Symbol]
    edges: List[Edge]
    failed: bool
    silence_code: str = ""
    detail: str = ""

    @classmethod
    def success(cls, symbols: List[Symbol], edges: List[Edge]) -> "ScipAttempt":
        return cls(symbols=symbols, edges=edges, failed=False)

    @classmethod
    def failure(cls, silence_code: str, detail: str) -> "ScipAttempt":
        return cls(
            symbols=[], edges=[], failed=True,
            silence_code=silence_code, detail=detail,
        )


# One-time log marker so repeated fall-through attempts don't spam the
# user's terminal. A set because the helper may be called across
# multiple workspaces in a single process (monorepo analysis).
_LOGGED_FALLBACK: set[str] = set()

# Stderr tail length when surfacing rust-analyzer / cargo diagnostics. ~512
# chars keeps the failure cause readable without dragging a 200-line panic
# trace into the user's terminal.
_STDERR_TAIL_BYTES = 512

# Exit codes that indicate SIGKILL — almost always OOM-kill in practice for
# rust-analyzer on memory-pressured machines (e.g. wasmtime-class workspaces
# on <32 GiB RAM, the original WI-todon trigger). -9 = Linux signal-based,
# 137 = shell convention (128 + signal number).
_SIGKILL_EXIT_CODES = frozenset({-9, 137})


def _stderr_tail(stderr: bytes) -> str:
    """Decode the last ``_STDERR_TAIL_BYTES`` of stderr for surfacing in a log line.

    Trailing newlines are stripped so the result lays out cleanly inline; the
    caller wraps it in its own framing. Decoding uses ``errors="replace"`` so
    a stray non-UTF-8 byte from rust-analyzer cannot crash the diagnostic
    pipeline.
    """
    if not stderr:
        return ""
    tail = stderr[-_STDERR_TAIL_BYTES:] if len(stderr) > _STDERR_TAIL_BYTES else stderr
    return tail.decode("utf-8", errors="replace").rstrip()


def _format_invocation_failed(exc: RustAnalyzerInvocationFailed) -> str:
    """Build the user-facing diagnostic for an invocation failure.

    Layered: base failure line, optional exit-code chunk (omitted on timeout
    where we killed the process), optional OOM hint when the exit code matches
    a SIGKILL signature, optional stderr tail (omitted when empty so we don't
    print 'stderr: ' with nothing after it).
    """
    parts: list[str] = [str(exc)]
    if exc.returncode is not None:
        parts.append(f"exit={exc.returncode}")
        if exc.returncode in _SIGKILL_EXIT_CODES:
            parts.append(
                "process killed by SIGKILL (likely OOM — rust-analyzer on "
                "wasmtime-class workspaces may need >16 GiB RAM)",
            )
    tail = _stderr_tail(exc.stderr)
    if tail:
        parts.append(f"stderr-tail: {tail}")
    return " | ".join(parts)


def _format_no_output(exc: RustAnalyzerNoOutput) -> str:
    """Build the user-facing diagnostic for the no-output failure mode.

    No exit code (the process exited 0) — just the message and a stderr tail
    when present, since cargo's diagnostic is the load-bearing signal here.
    """
    parts: list[str] = [str(exc)]
    tail = _stderr_tail(exc.stderr)
    if tail:
        parts.append(f"stderr-tail: {tail}")
    return " | ".join(parts)


def _reset_logged_fallback_for_tests() -> None:
    """Clear the once-per-process log marker; test-only helper."""
    _LOGGED_FALLBACK.clear()


def _silence_code_for(exc: RustAnalyzerError) -> str:
    """Map an invocation failure onto the closed pass-silence-reason axis.

    See :class:`ScipAttempt` for why each state gets the value it does. The
    ordering matters: ``RustAnalyzerNoOutput`` is checked before the generic
    fallback because an exit-0-with-no-index is emphatically NOT a crash.
    """
    if isinstance(exc, RustAnalyzerNotInstalled):
        return DEPENDENCY_UNAVAILABLE
    if isinstance(exc, RustAnalyzerNoOutput):
        # Exited 0, wrote nothing — the workspace is not a parseable cargo
        # project. Not a crash, not a missing dependency, and the axis has no
        # value for it. The declared residue is the honest answer; inventing a
        # neighbour would be a fabricated disclosure (WI-luvud).
        return UNREPORTED
    return PASS_CRASHED


def try_analyze_with_rust_analyzer(
    workspace: Path,
    source_reader: SourceReader,
    *,
    invoke: Optional[InvokeFn] = None,
    translate: Optional[TranslateFn] = None,
    log: Optional[Callable[[str], None]] = None,
    run_id: str = "",
) -> ScipAttempt:
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

    ``log`` defaults to a no-op; tests and the analyzer-registry wrapper
    in ``analyzer.py`` pass a real logger so the user sees one line
    explaining why the backend degraded.

    ``run_id`` is forwarded verbatim to the translator so the emitted
    Symbols and Edges name the CALLER's ``AnalysisRun`` (WI-didag). This
    parameter is the missing link that made ``translate_scip_to_hg``'s
    documented ``run_id`` escape hatch unreachable from production: without
    it every real survey fell into the translator's fabricating branch and
    emitted provenance pointing at a run nobody serializes. Empty is still
    accepted — the translator owns that fallback, and a direct library
    caller that has no parent run is not required to invent one.
    """
    invoke_fn = invoke if invoke is not None else run_rust_analyzer_scip
    translate_fn = translate if translate is not None else translate_scip_to_hg
    emit = log if log is not None else (lambda _msg: None)

    with tmp_artifact_dir(prefix="hg_rust_analyzer_") as tmpdir:
        scratch = Path(tmpdir)
        try:
            scip_bytes = invoke_fn(workspace, cwd=scratch)
        except RustAnalyzerError as exc:
            key = f"{type(exc).__name__}:{workspace}"
            if key not in _LOGGED_FALLBACK:
                _LOGGED_FALLBACK.add(key)
                if isinstance(exc, RustAnalyzerInvocationFailed):
                    detail = _format_invocation_failed(exc)
                elif isinstance(exc, RustAnalyzerNoOutput):
                    detail = _format_no_output(exc)
                else:
                    detail = str(exc)
                emit(
                    f"rust-analyzer backend unavailable for {workspace}: "
                    f"{type(exc).__name__}: {detail} — falling through to rust.py",
                )
            # WI-luvud: the taxonomy already knows which state this is; before
            # this the information died here and all four became one ``None``.
            return ScipAttempt.failure(_silence_code_for(exc), str(exc))

    try:
        symbols, edges = translate_fn(scip_bytes, source_reader, run_id=run_id)
        # An EMPTY index is a success, not a failure: rust-analyzer ran to
        # completion and found nothing to index. Reporting it as a failure is
        # what put a pass that RAN into the "did not run" bucket (WI-luvud).
        return ScipAttempt.success(symbols, edges)
    except DecodeError as exc:
        key = f"DecodeError:{workspace}"
        if key not in _LOGGED_FALLBACK:
            _LOGGED_FALLBACK.add(key)
            emit(
                f"rust-analyzer SCIP decode failed for {workspace}: "
                f"{exc} — falling through to rust.py",
            )
        # An index we cannot decode is a contained raise, same as a non-zero
        # exit: the pass started and blew up, and "install the package" would
        # be the wrong advice (ADR-0056's init-failure ruling).
        return ScipAttempt.failure(PASS_CRASHED, f"SCIP decode failed: {exc}")
