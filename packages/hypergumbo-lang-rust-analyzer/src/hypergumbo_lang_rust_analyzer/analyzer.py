# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registered analyzer entry point for the SCIP-backed Rust backend (WI-duzul Slice C-final).

This module threads the four primitives the earlier slices delivered
into the hypergumbo-core analyzer-registry surface:

1. :func:`hypergumbo_lang_rust_analyzer.gate.should_use_rust_analyzer_backend`
   gates the whole function on the user's opt-in + binary availability.
2. :func:`hypergumbo_lang_rust_analyzer.graceful_degrade.try_analyze_with_rust_analyzer`
   handles the actual shell-out + SCIP → IR translation and returns
   ``None`` on any of the WI-nohah fall-through conditions.
3. Stable-id parity is already threaded inside
   :func:`~hypergumbo_lang_rust_analyzer.translate.translate_scip_to_hg`,
   so the Symbol objects this analyzer emits are deduplicable against
   the tree-sitter ``rust.py`` analyzer's output when cross-pass dedup
   runs (WI-bajuz).

Registration
------------
Registered as analyzer name ``"rust_analyzer"`` (distinct from
``rust.py``'s ``"rust"`` registration) so both analyzers coexist in
the registry: ``rust.py`` remains the default, this one only produces
output when the user opts in. Priority 45 (vs. the registry default
50) so the SCIP pass runs slightly earlier when active — not load-
bearing, just consistent with the "higher quality backend runs first"
convention.

When the opt-in gate returns False (the default for every session
that hasn't set ``HYPERGUMBO_RUST_ANALYZER=1`` or passed
``--backend rust-analyzer``), this analyzer returns an empty
:class:`AnalysisResult` immediately. No file walk, no subprocess.
``rust.py`` takes care of Rust analysis in that case.

Provenance
----------
Successful SCIP-sourced Symbols already carry ``origin="scip"`` from
:func:`~hypergumbo_core.scip.index.scip_index_to_symbols`, which
preserves the provenance marker the WI-nohah description calls out.
Downstream tools can filter on ``symbol.origin`` to trust-weight SCIP-
derived symbols separately from tree-sitter-derived ones.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import register_analyzer

from hypergumbo_lang_rust_analyzer.gate import should_use_rust_analyzer_backend
from hypergumbo_lang_rust_analyzer.graceful_degrade import (
    try_analyze_with_rust_analyzer,
)


def _disk_source_reader(path: str) -> bytes | None:
    """Read *path* from disk, swallowing I/O errors as ``None``.

    The translate-layer's reassign_rust_stable_ids takes a caller-owned
    reader callable so tests can inject in-memory fakes. Production
    callers (this function) pass a disk-backed reader with exception
    handling that matches the reassignment pass's contract: any read
    failure maps to "source unavailable, skip parity rewrite" rather
    than crashing the analyzer.
    """
    try:
        return Path(path).read_bytes()
    except (OSError, ValueError):  # pragma: no cover — pure defensive
        return None


@register_analyzer("rust_analyzer", priority=45)
def analyze_rust_with_scip(repo_root: Path) -> AnalysisResult:
    """Entry point for the SCIP-backed Rust analyzer.

    Returns an empty result when the opt-in gate is False; otherwise
    shells out to ``rust-analyzer scip <repo_root>``, translates the
    emitted SCIP index into hypergumbo ``Symbol`` / ``Edge`` objects
    (with rust.py stable_id parity), and returns them. All three
    WI-nohah fall-through conditions are handled inside
    :func:`try_analyze_with_rust_analyzer` and surface here as a
    ``None`` return — this function swallows that to an empty
    AnalysisResult so the registry treats "no SCIP run" identically
    to "SCIP run produced nothing".

    The registry calls this with ``repo_root`` being the workspace
    root, which is exactly what ``cargo metadata`` + ``rust-analyzer
    scip`` expect.
    """
    if not should_use_rust_analyzer_backend():
        return AnalysisResult()

    result = try_analyze_with_rust_analyzer(repo_root, _disk_source_reader)
    if result is None:
        return AnalysisResult()

    symbols, edges = result
    return AnalysisResult(symbols=symbols, edges=edges)
