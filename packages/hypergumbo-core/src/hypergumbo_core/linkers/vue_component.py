"""Vue component linker for resolving cross-file component imports.

The Vue analyzer creates `imports_component` edges with raw import paths
(e.g., './Header.vue', '@/components/Button.vue') as their dst instead
of Symbol IDs. This linker resolves those paths to actual file symbols,
creating proper edges that connect Vue component composition graphs.

How It Works
------------
1. Scan all existing edges for `imports_component` type
2. For each, resolve the import path (relative, subdirectory, @ alias)
   to an actual .vue file on disk
3. Create `component_file` symbols for each .vue file involved
4. Create resolved `imports_component` edges from component_ref to
   component_file symbols

Why This Matters
----------------
Without this linker, ALL Vue component_ref symbols are orphaned because
their `imports_component` edges point to raw strings instead of Symbol IDs.
On Chatwoot (1093 Vue component nodes), this caused 100% orphan rate.
With this linker, component composition graphs become traversable.

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

from ..ir import AnalysisRun, Edge, Span, Symbol
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)

if TYPE_CHECKING:
    pass

PASS_ID = "vue-component-linker-v1"

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


def _make_component_file_id(rel_path: str) -> str:
    """Create a stable symbol ID for a .vue component file.

    Args:
        rel_path: Path relative to repo root (e.g., 'src/components/Header.vue')

    Returns:
        Symbol ID in format 'vue:{path}:component_file:1:{name}'
    """
    name = Path(rel_path).stem  # Header.vue -> Header
    return f"vue:{rel_path}:component_file:1:{name}"


@register_linker(
    "vue-components",
    priority=25,
    description="Vue component import resolution",
    activation=LinkerActivation(
        frameworks=["vue", "nuxt"],
        language_pairs=[("vue", "vue")],
    ),
)
def link_vue_components(ctx: LinkerContext) -> LinkerResult:
    """Resolve Vue component imports to symbol-to-symbol edges.

    Finds all `imports_component` edges (created by the Vue analyzer with
    raw import paths as dst), resolves the paths to actual .vue files,
    creates component_file symbols for each file, and creates proper edges.

    Args:
        ctx: LinkerContext with repo_root, symbols, and edges.

    Returns:
        LinkerResult with new component_file symbols and resolved edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version="0.1.0")

    new_symbols: list[Symbol] = []
    new_edges: list[Edge] = []

    # Find all imports_component edges with raw paths
    import_edges = [e for e in ctx.edges if e.edge_type == "imports_component"]
    if not import_edges:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(symbols=[], edges=[], run=run)

    # Build map of source symbol IDs to their paths (for resolving relative imports)
    symbol_path_map: dict[str, str] = {}
    for sym in ctx.symbols:
        symbol_path_map[sym.id] = sym.path

    # Track created component_file symbols to avoid duplicates
    file_symbol_cache: dict[str, Symbol] = {}

    # Collect all .vue file paths involved (for creating file symbols)
    vue_file_paths: set[str] = set()
    for sym in ctx.symbols:
        if sym.language == "vue" and sym.path.endswith(".vue"):
            vue_file_paths.add(sym.path)

    def get_or_create_file_symbol(rel_path: str) -> Symbol:
        """Get or create a component_file symbol for a .vue file."""
        if rel_path in file_symbol_cache:
            return file_symbol_cache[rel_path]

        sym_id = _make_component_file_id(rel_path)
        name = Path(rel_path).stem
        sym = Symbol(
            id=sym_id,
            stable_id=sym_id,
            name=name,
            kind="component_file",
            language="vue",
            path=rel_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            meta={},
        )
        file_symbol_cache[rel_path] = sym
        new_symbols.append(sym)
        return sym

    # Process each import edge
    for edge in import_edges:
        # The dst is a raw import path like './Header.vue'
        raw_path = edge.dst

        # Get the source file path for relative import resolution
        src_path = symbol_path_map.get(edge.src, "")
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

        # Create component_file symbol for the target
        target_file_sym = get_or_create_file_symbol(rel_resolved)

        # Also create component_file symbol for the source .vue file
        if src_path.endswith(".vue"):
            get_or_create_file_symbol(src_path)

        # Create resolved edge
        resolved_edge = Edge.create(
            src=edge.src,
            dst=target_file_sym.id,
            edge_type="imports_component",
            line=edge.line,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="vue_component_import",
            confidence=0.90,
        )
        new_edges.append(resolved_edge)

    run.files_analyzed = len(file_symbol_cache)
    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(symbols=new_symbols, edges=new_edges, run=run)
