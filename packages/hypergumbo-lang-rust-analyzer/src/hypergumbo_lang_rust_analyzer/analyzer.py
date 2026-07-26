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

import warnings
from collections.abc import Callable
from pathlib import Path

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.ir import Edge

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
    except (OSError, ValueError):
        return None


def _repo_anchored_reader(repo_root: Path) -> Callable[[str], bytes | None]:
    """Build a source reader that resolves SCIP ``doc.relative_path`` against
    ``repo_root``, not the process CWD (WI-kilih).

    ``rust-analyzer scip`` emits paths relative to the indexed workspace, so the
    reassignment pass hands this reader a repo-relative path like ``src/lib.rs``.
    Reading it bare resolves against ``os.getcwd()``; when the survey runs from
    anywhere other than ``repo_root`` (absolute-path surveys, monorepo sub-roots,
    CI runners) the read fails, the stable_id parity reassignment is silently
    skipped, and the SCIP symbol keeps a raw-moniker stable_id that diverges from
    the tree-sitter ``rust.py`` anchor — breaking the WI-zakub byte-parity contract
    (ADR-0035 v7). Anchoring at ``repo_root`` closes the gap; ``pathlib`` leaves an
    already-absolute path unchanged.
    """

    def _read(relative_path: str) -> bytes | None:
        return _disk_source_reader(str(repo_root / relative_path))

    return _read


def _emit_user_warning(message: str) -> None:
    """Surface graceful-degrade diagnostics as Python ``UserWarning``.

    WI-todon: the prior implementation passed no logger into
    :func:`try_analyze_with_rust_analyzer`, which meant every
    invoke/translate failure (including OOM-kills) was swallowed
    silently. Wiring this real log callable as the default makes the
    diagnostics from :mod:`graceful_degrade._format_invocation_failed`
    visible to the user without requiring CLI plumbing changes.
    ``stacklevel=3`` so the warning points at the analyzer call site
    (registry dispatch) rather than this helper.
    """
    warnings.warn(message, UserWarning, stacklevel=3)


def _repo_has_rs_files(repo_root: Path) -> bool:
    """Return True iff ``repo_root`` contains at least one ``.rs`` file.

    Used by the engagement check to suppress false-positive warnings on
    repos that simply have no Rust code (where "no SCIP edges" is the
    correct answer, not a silent fall-through). The scan short-circuits
    on first match so cost scales with depth-to-first-Rust-file, not
    total tree size. ``rglob`` swallows :class:`OSError` for unreadable
    subdirectories rather than crashing the analyzer.
    """
    try:
        return next(iter(repo_root.rglob("*.rs")), None) is not None
    except OSError:  # pragma: no cover — pure defensive
        return False


def _has_scip_origin_edge(edges: list[Edge]) -> bool:
    """Return True iff at least one edge carries ``origin='scip'``.

    The artifact-engagement check uses this signature: a successful
    rust-analyzer + SCIP translation produces edges with
    ``origin='scip'``. A SCIP run that exits 0 but produces no usable
    output (the wasmtime-OOM-class scenario) yields zero such edges,
    even when ``.rs`` files are present — that is exactly the silent
    fall-through WI-todon makes visible.
    """
    return any("scip" in getattr(edge, "origin", []) for edge in edges)


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

    WI-todon engagement diagnostics: a real log callable
    (:func:`_emit_user_warning`) is wired into
    :func:`try_analyze_with_rust_analyzer` so invoke/translate
    failures surface as ``UserWarning`` instead of being swallowed.
    After a successful translate, an artifact-level engagement check
    fires when zero ``origin='scip'`` edges were produced on a repo
    that contains ``.rs`` files — catching the OOM-class scenario
    where rust-analyzer exits 0 but emits no usable SCIP output.

    The registry calls this with ``repo_root`` being the workspace
    root, which is exactly what ``cargo metadata`` + ``rust-analyzer
    scip`` expect.
    """
    if not should_use_rust_analyzer_backend():
        # Opt-in SCIP backend disabled (the default). Self-declare the skip so
        # the orchestrator records an honest ``skipped_passes`` entry with this
        # reason rather than the generic "no files matched" (which would be
        # wrong — the repo may well contain .rs files; the backend simply did
        # not run). WI-didil.
        return AnalysisResult(
            skipped=True, skip_reason="rust-analyzer backend not enabled",
        )

    result = try_analyze_with_rust_analyzer(
        repo_root, _repo_anchored_reader(repo_root), log=_emit_user_warning,
    )
    if result is None:
        # Backend on but SCIP invoke/translate produced nothing (WI-nohah
        # fall-through). Self-declare so this surfaces as a reasoned skip.
        return AnalysisResult(
            skipped=True, skip_reason="rust-analyzer backend produced no output",
        )

    symbols, edges = result
    if not _has_scip_origin_edge(edges) and _repo_has_rs_files(repo_root):
        _emit_user_warning(
            f"rust-analyzer backend produced no SCIP-origin edges for "
            f"{repo_root} despite .rs files being present — likely silent "
            f"engagement failure (see WI-todon). Inspect prior warnings for "
            f"invoke-time diagnostics.",
        )
    return AnalysisResult(symbols=symbols, edges=edges)
