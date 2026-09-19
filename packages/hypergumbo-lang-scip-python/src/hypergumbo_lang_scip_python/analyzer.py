# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registered analyzer entry point for the scip-python backend (WI-nanom).

Registered as ``scip_python``: a second BACKEND for the language ``python``,
beside the ast-based ``python`` analyzer (ADR-0057 §10). The registry can
see that the two are producers for one language and how their records
pair: this arm's ``Symbol.name`` is the SCIP descriptor name as emitted
(``area``, never ``Shape.area``) and its ``Symbol.span`` is the identifier
TOKEN of the Definition occurrence, so the merge pass tests token-inside-item
against the syntax arm's item span. No measured authority is declared —
the first two-arm table (lab notebook 2026-09-19) showed the arms agree on
``kind`` 4,721 times out of 4,723 — and ``executes_analysed_code`` is
False: pyright infers, it does not run the analysed code, which is why the
opt-in may live in a config file (ADR-0045 ruling 5).

When the gate is False (the default) the analyzer returns a skipped result
with ``backend_disabled``, so the orchestrator records an honest
``skipped_passes`` entry. When the binary is missing, exits non-zero or
writes no index, the corresponding pass-silence code travels with the skip
(``dependency_unavailable`` / ``pass_crashed`` / ``unreported``) — the same
discrimination WI-luvud gave the Rust arm. A completed run that produced
nothing is NOT a skip: the pass ran, and says so.
"""
from __future__ import annotations

import tempfile
import warnings
from pathlib import Path
from typing import Callable, Optional

from google.protobuf.message import DecodeError

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import (
    SPAN_ROLE_TOKEN,
    MergeAnchor,
    as_emitted,
    register_analyzer,
)
from hypergumbo_core.ir import PASS_VERSION, AnalysisRun, Edge, Symbol
from hypergumbo_core.pass_silence import (
    BACKEND_DISABLED,
    DEPENDENCY_UNAVAILABLE,
    PASS_CRASHED,
    UNREPORTED,
    silence_reason_for_candidates,
)

from hypergumbo_lang_scip_python.gate import should_use_scip_python_backend
from hypergumbo_lang_scip_python.invoke import (
    ScipPythonError,
    ScipPythonNoOutput,
    ScipPythonNotInstalled,
    run_scip_python_index,
)
from hypergumbo_lang_scip_python.translate import translate_scip_python_to_hg

InvokeFn = Callable[..., bytes]
TranslateFn = Callable[..., "tuple[list[Symbol], list[Edge]]"]


def _silence_code_for(exc: Exception) -> str:
    """Map an invocation failure onto the closed pass-silence axis (see the module docstring)."""
    if isinstance(exc, ScipPythonNotInstalled):
        return DEPENDENCY_UNAVAILABLE
    if isinstance(exc, ScipPythonNoOutput):
        return UNREPORTED
    return PASS_CRASHED


def _emit_user_warning(message: str) -> None:
    warnings.warn(message, UserWarning, stacklevel=3)


def _repo_has_py_files(repo_root: Path) -> bool:
    try:
        return next(iter(repo_root.rglob("*.py")), None) is not None
    except OSError:  # pragma: no cover — pure defensive
        return False


def analyze_python_with_scip_impl(
    repo_root: Path,
    *,
    invoke: Optional[InvokeFn] = None,
    translate: Optional[TranslateFn] = None,
    gate: Optional[Callable[..., bool]] = None,
    log: Optional[Callable[[str], None]] = None,
) -> AnalysisResult:
    """The body of the registered analyzer, with every collaborator injectable."""
    decide = gate if gate is not None else should_use_scip_python_backend
    if not decide(repo_root=repo_root):
        return AnalysisResult(
            skipped=True,
            skip_reason="scip-python backend not enabled",
            skip_reason_code=BACKEND_DISABLED,
        )
    run = AnalysisRun.create(  # nosec B106 — a pass NAME, not a password
        pass_id="scip_python", version=PASS_VERSION,
    )
    do_invoke = invoke if invoke is not None else run_scip_python_index
    do_translate = translate if translate is not None else translate_scip_python_to_hg
    emit = log if log is not None else _emit_user_warning
    try:
        with tempfile.TemporaryDirectory(prefix="hypergumbo-scip-python-") as scratch:
            scip_bytes = do_invoke(repo_root, cwd=Path(scratch), project_name=repo_root.name or "project")
        symbols, edges = do_translate(scip_bytes, run_id=run.execution_id)
    except (ScipPythonError, DecodeError) as exc:
        emit(f"scip-python backend did not run on {repo_root}: {exc}")
        return AnalysisResult(
            skipped=True,
            skip_reason=f"scip-python backend: {exc}",
            skip_reason_code=_silence_code_for(exc),
        )
    if not symbols and _repo_has_py_files(repo_root):
        emit(
            f"scip-python backend produced no symbols for {repo_root} despite .py "
            f"files being present — likely a project scip-python could not index.",
        )
    run.silence_reason = silence_reason_for_candidates(symbols)
    return AnalysisResult(run=run, symbols=symbols, edges=edges)


@register_analyzer(
    "scip_python",
    priority=45,
    languages=["python"],
    backend="scip",
    executes_analysed_code=False,
    merge=MergeAnchor(name_key=as_emitted, span_role=SPAN_ROLE_TOKEN),
)
def analyze_python_with_scip(repo_root: Path) -> AnalysisResult:
    """Entry point for the SCIP-backed Python analyzer (see the module docstring)."""
    return analyze_python_with_scip_impl(repo_root)
