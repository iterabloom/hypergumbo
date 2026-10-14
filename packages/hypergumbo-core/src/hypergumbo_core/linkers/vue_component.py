# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker: Vue component for resolving cross-file component imports.

The Vue analyzer emits one `imports` edge per component reference, with
the source file's id (`make_file_id`) as src and a synthetic component
id as dst: `vue:{import_path}:0-0:{name}:component` when a companion
import statement names the path, or a dangling
`vue:component:{tag}:0-0:{tag}:component` when it does not. The raw path
(e.g., './Header.vue', '@/components/Button.vue') rides in
`meta['import_path']`. Neither dst form is a real Symbol id, so the edge
dangles until this linker resolves the path to an actual file symbol,
creating proper edges that connect Vue component composition graphs.

How It Works
------------
1. Scan all existing edges for `imports` carrying `meta['import_path']`
   (edges with an empty path, the unresolved-tag form, are skipped)
2. For each, resolve the import path (relative, subdirectory, @ alias)
   to an actual .vue file on disk
3. Get or create a canonical `kind="file"` symbol (`make_file_id` shape,
   `meta['component_framework'] = "vue"`) for the target and source
   .vue files, reusing one already in the graph when present (INV-bahov)
4. Create a resolved `imports` edge from the original src (the source
   file id) to the target file symbol; the producer's dangling edge is
   left in place

Why This Matters
----------------
Without this linker, every Vue component `imports` edge points at a
synthetic dst that matches no Symbol, so the component composition graph
cannot be traversed. Before the producer emitted file-sourced edges
(when it still minted per-reference `component_ref` symbols), this gap
left 100% of Chatwoot's 1093 Vue component nodes orphaned. With this
linker, component composition graphs become traversable.

Import Path Resolution
----------------------
Vue projects use several import path formats:
- Relative: './Header.vue', '../layout/Footer.vue'
- @ alias: '@/components/Modal.vue' (@ = src/ by convention)
- No extension: './Header' (try appending .vue)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..analyze.base import make_file_id, make_file_stable_id
from ..ir import PASS_VERSION, AnalysisRun, Edge, Span, Symbol, make_pass_id
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)

if TYPE_CHECKING:
    pass

PASS_ID = make_pass_id("vue-component-linker")

# Common @ alias targets in Vue/Nuxt projects
_AT_ALIAS_CANDIDATES = ("src", "app", ".")


def _resolve_import_path(
    import_path: str,
    source_file: Path,
    repo_root: Path,
) -> Path | None:
    """Resolve a Vue import path to an actual file on disk.

    Handles:
    - Relative paths: './Header.vue' resolved from source file directory
    - @ alias: '@/components/Modal.vue' tries src/, app/, and repo root
    - Missing .vue extension: './Header' tries with .vue appended

    Args:
        import_path: The raw import path string from the Vue analyzer
        source_file: The .vue file containing the import
        repo_root: Repository root for @ alias resolution

    Returns:
        Resolved Path if the target file exists, None otherwise.
    """
    # Handle @ alias
    if import_path.startswith("@/"):
        suffix = import_path[2:]  # Strip '@/'
        for candidate_dir in _AT_ALIAS_CANDIDATES:
            candidate = repo_root / candidate_dir / suffix
            if candidate.exists():
                return candidate
            # Try adding .vue extension
            if not suffix.endswith(".vue"):
                candidate_vue = candidate.with_suffix(".vue")
                if candidate_vue.exists():
                    return candidate_vue
        return None

    # Handle relative paths
    if import_path.startswith("./") or import_path.startswith("../"):
        # Resolve relative to the directory containing the source file
        source_dir = (repo_root / source_file).parent
        candidate = (source_dir / import_path).resolve()
        if candidate.exists():
            return candidate
        # Try adding .vue extension
        if not import_path.endswith(".vue"):
            candidate_vue = candidate.with_suffix(".vue")
            if candidate_vue.exists():
                return candidate_vue
        return None

    # Bare path (no ./ prefix) — treat as relative to source dir
    source_dir = (repo_root / source_file).parent
    candidate = (source_dir / import_path).resolve()
    if candidate.exists():
        return candidate
    if not import_path.endswith(".vue"):
        candidate_vue = candidate.with_suffix(".vue")
        if candidate_vue.exists():
            return candidate_vue
    return None


@register_linker(
    "vue-component-linker",
    priority=25,
    description="Vue component import resolution",
    activation=LinkerActivation(
        frameworks=["vue", "nuxt"],
        language_pairs=[("vue", "vue")],
    ),
    # CNF: Vue SFCs are parsed by the javascript analyzer.
    depends_on=[["javascript"]],
)
def link_vue_components(ctx: LinkerContext) -> LinkerResult:
    """Resolve Vue component imports to symbol-to-symbol edges.

    Finds all `imports` edges the Vue analyzer emitted with a dangling
    component dst (the raw path is in ``meta['import_path']``), resolves
    the paths to actual .vue files, gets or creates the canonical
    ``kind="file"`` symbol for each file, and creates proper edges.

    Args:
        ctx: LinkerContext with repo_root, symbols, and edges.

    Returns:
        LinkerResult with new file symbols and resolved edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    new_symbols: list[Symbol] = []
    new_edges: list[Edge] = []

    # ADR-0023 §6 Phase 3 (WI-mokam-jalig): the Vue analyzer now emits
    # canonical 'imports' edges with the raw component path stored in
    # meta['import_path']. The presence of that key marks an
    # unresolved-import edge this linker is responsible for resolving.
    import_edges = [
        e for e in ctx.edges
        if e.edge_type == "imports"
        and (e.meta or {}).get("import_path") is not None
    ]
    if not import_edges:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(symbols=[], edges=[], run=run)

    # Build map of source symbol IDs to their paths (for resolving relative imports)
    symbol_path_map: dict[str, str] = {}
    for sym in ctx.symbols:
        symbol_path_map[sym.id] = sym.path

    # Track file symbols already returned to avoid duplicates
    # within this run. Keyed on rel_path.
    file_symbol_cache: dict[str, Symbol] = {}

    # INV-bahov: cross-producer canonical-id lookup. When the orchestrator's
    # file-symbol synthesizer or the Vue analyzer has already emitted a
    # canonical kind="file" Symbol for a given path, this linker must reuse
    # that Symbol rather than minting a parallel shadow node. Sibling of the
    # INV-ronuf (websocket, WI-hifol) and INV-movor (js_module) fixes.
    existing_file_symbol_by_canonical_id: dict[str, Symbol] = {}
    for sym in ctx.symbols:
        if sym.kind == "file" and sym.language == "vue":
            existing_file_symbol_by_canonical_id[
                make_file_id("vue", sym.path)
            ] = sym

    # Collect all .vue file paths involved (for creating file symbols)
    vue_file_paths: set[str] = set()
    for sym in ctx.symbols:
        if sym.language == "vue" and sym.path.endswith(".vue"):
            vue_file_paths.add(sym.path)

    def get_or_create_file_symbol(rel_path: str) -> Symbol:
        """Get or create a canonical-shape file Symbol for a .vue file.

        INV-bahov: emits the canonical ``make_file_id`` shape
        (``vue:{path}:1-1:file:file``) rather than the legacy
        ``:component_file:1:{name}`` literal so id-equality dedup works
        cross-producer against the orchestrator's file-symbol synthesizer
        and against the Vue analyzer. When a canonical Symbol already
        exists in ``ctx.symbols`` for this path, reuse it.
        """
        if rel_path in file_symbol_cache:
            return file_symbol_cache[rel_path]

        sym_id = make_file_id("vue", rel_path)
        existing = existing_file_symbol_by_canonical_id.get(sym_id)
        if existing is not None:
            file_symbol_cache[rel_path] = existing
            return existing

        name = Path(rel_path).stem
        sym = Symbol(
            id=sym_id,
            stable_id=make_file_stable_id("vue", rel_path),
            name=name,
            kind="file",
            language="vue",
            path=rel_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            meta={"component_framework": "vue"},
        )
        file_symbol_cache[rel_path] = sym
        new_symbols.append(sym)
        return sym

    # Process each import edge
    for edge in import_edges:
        # WI-vobiv: edge.dst is now a properly-formed 5-part id
        # (vue:{path}:0-0:{name}:component). The raw import path lives in
        # edge.meta["import_path"]; fall back to dst for back-compat with
        # any pre-WI-vobiv edges.
        raw_path = (edge.meta or {}).get("import_path", edge.dst)
        if not raw_path:
            # Unresolved component reference (no companion import statement);
            # the producer emits a dangling component dst that this linker
            # cannot resolve to a real file. Skip.
            continue

        # Get the source file path for relative import resolution.
        # WI-mihiz / audit-findings 0011: prefer edge.meta["source_path"]
        # (set by the producer when component_ref Symbol was dropped and
        # Edge.src moved to make_file_id). Fall back to symbol_path_map
        # for pre-refactor edges and for non-component import callers.
        src_path = (edge.meta or {}).get("source_path") or symbol_path_map.get(
            edge.src, ""
        )
        if not src_path:
            continue

        # Resolve the import path to an actual file
        resolved = _resolve_import_path(raw_path, Path(src_path), ctx.repo_root)
        if resolved is None:
            continue

        # Get relative path for the resolved file
        try:
            rel_resolved = str(resolved.relative_to(ctx.repo_root))
        except ValueError:  # pragma: no cover — resolved is always under repo_root
            continue  # pragma: no cover

        # Get or create the canonical file symbol for the target
        target_file_sym = get_or_create_file_symbol(rel_resolved)

        # Also get or create the canonical file symbol for the source .vue file
        if src_path.endswith(".vue"):
            get_or_create_file_symbol(src_path)

        # Create resolved edge
        resolved_edge = Edge.create(
            src=edge.src,
            dst=target_file_sym.id,
            edge_type="imports",
            line=edge.line,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="ast_import",
            confidence=0.90,
            meta={"framework_dispatch": "vue_component"},
            derived_from=[edge.src, edge.id],
        )
        new_edges.append(resolved_edge)

    run.files_analyzed = len(file_symbol_cache)
    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(symbols=new_symbols, edges=new_edges, run=run)
