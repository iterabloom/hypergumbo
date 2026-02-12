"""JS/TS module resolution linker for cross-file import edges.

The JS/TS analyzer creates `imports` edges with synthetic dst IDs like
'javascript:./utils:0-0:module:module' where the import path is embedded
in the ID string. These edges are orphaned because no symbol has that ID.

This linker resolves those import paths to actual files on disk, creates
`module_file` symbols for each resolved target, and creates two types of
edges:

1. `imports_module` - from the importing file symbol to the module_file
   (file-level dependency)
2. `module_exports` - from the module_file to each function/method/class
   defined in that file (enabling cross-file reachability)

Together these create a traversable path:
  file_A --imports_module--> module_file_B --module_exports--> functionInB

For npm packages (bare/scoped imports like 'lodash', '@vue/test-utils'),
the linker creates `npm_package` symbols and `imports_module` edges to them.

File Extension Probing
----------------------
When an import path has no extension (e.g., './utils'), we probe in order:
  .js, .ts, .jsx, .tsx, .mjs, .mts, .vue, .json
Also checks for directory index files: {dir}/index.{ext}

Why This Matters
----------------
On Chatwoot, 9,192 import edges exist but 0% resolve to known nodes.
3,023 JS functions/methods are orphaned primarily because their files
are imported by other files but no cross-file edges exist. This linker
creates the cross-file edge chain needed to de-orphan them.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
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

PASS_ID = "js-module-linker-v1"

# Extensions to probe when import path has no extension, in priority order.
# .js first because it's the most common in real-world JS/TS projects.
_PROBE_EXTENSIONS = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".mts", ".vue", ".json")

# Symbol kinds that represent "exported" callable code in a JS/TS module.
_EXPORTABLE_KINDS = frozenset({
    "function", "method", "class", "getter", "setter", "constructor",
})

# Languages that use JS-style module imports.
_JS_LANGUAGES = frozenset({"javascript", "typescript"})

# Regex to parse file symbol IDs: {lang}:{path}:{start}-{end}:{name}:{kind}
# Works with absolute paths (/foo/bar.js) and relative paths (src/bar.js).
_FILE_ID_RE = re.compile(r"^[^:]+:(.+?):\d+-\d+:[^:]+:[^:]+$")


def _extract_path_from_id(symbol_id: str) -> str | None:
    """Extract file path from a symbol ID.

    Symbol IDs follow the format: {lang}:{path}:{start}-{end}:{name}:{kind}
    This extracts the {path} component.

    Args:
        symbol_id: The full symbol ID string.

    Returns:
        The file path or None if the ID doesn't match the expected format.
    """
    m = _FILE_ID_RE.match(symbol_id)
    if m:
        return m.group(1)
    return None


def _extract_import_path(dst: str) -> tuple[str, str] | None:
    """Extract language and import path from an unresolved module dst ID.

    The JS/TS analyzer creates dst IDs with format:
        {lang}:{import_path}:0-0:module:module

    Args:
        dst: The edge dst string.

    Returns:
        Tuple of (language, import_path) or None if not a module reference.
    """
    # Must end with :0-0:module:module
    if not dst.endswith(":0-0:module:module"):
        return None

    # Strip the suffix
    prefix = dst[: -len(":0-0:module:module")]

    # Split on first colon to get language
    colon_idx = prefix.find(":")
    if colon_idx < 0:
        return None

    lang = prefix[:colon_idx]
    import_path = prefix[colon_idx + 1 :]

    if not import_path:
        return None

    return (lang, import_path)


def _probe_file(base_path: Path) -> Path | None:
    """Probe for a file with various extensions and index patterns.

    Given a base path (without extension), tries to find the actual file:
    1. If the path already has an extension and exists, return it.
    2. Try appending each extension in _PROBE_EXTENSIONS.
    3. If it's a directory, try {dir}/index.{ext}.

    Args:
        base_path: The base file path (possibly without extension).

    Returns:
        Resolved Path if found, None otherwise.
    """
    # If the path already exists as a file, return it
    if base_path.is_file():
        return base_path

    # Try appending extensions
    for ext in _PROBE_EXTENSIONS:
        candidate = base_path.with_suffix(ext)
        if candidate.is_file():
            return candidate

    # If it's a directory, try index files
    if base_path.is_dir():
        for ext in _PROBE_EXTENSIONS:
            index = base_path / f"index{ext}"
            if index.is_file():
                return index

    return None


def _make_module_file_id(rel_path: str, lang: str) -> str:
    """Create a stable symbol ID for a module_file symbol.

    Args:
        rel_path: Path relative to repo root.
        lang: Language of the module (javascript or typescript).

    Returns:
        Symbol ID in format '{lang}:{path}:module_file:1:{stem}'
    """
    stem = Path(rel_path).stem
    return f"{lang}:{rel_path}:module_file:1:{stem}"


def _make_npm_package_id(package_name: str, lang: str) -> str:
    """Create a stable symbol ID for an npm_package symbol.

    Args:
        package_name: The npm package name (e.g., 'lodash', '@vue/test-utils').
        lang: Language of the import.

    Returns:
        Symbol ID in format '{lang}:npm:{package_name}:npm_package'
    """
    return f"{lang}:npm:{package_name}:npm_package"


def _is_relative_import(import_path: str) -> bool:
    """Check if an import path is relative (starts with ./ or ../)."""
    return import_path.startswith("./") or import_path.startswith("../")


def _is_dynamic_import(import_path: str) -> bool:
    """Check if an import path is a dynamic require (variable-based)."""
    return import_path.startswith("<dynamic:")


def _is_npm_package(import_path: str) -> bool:
    """Check if an import path refers to an npm package (bare or scoped).

    npm packages are non-relative, non-dynamic imports. Examples:
    - 'lodash' (bare)
    - '@vue/test-utils' (scoped)
    - 'tailwindcss/defaultTheme' (bare with subpath)
    """
    if _is_relative_import(import_path):
        return False
    if _is_dynamic_import(import_path):
        return False
    # At this point it's either a bare module or a project alias
    return True


def _get_npm_package_name(import_path: str) -> str:
    """Extract the npm package name from an import path.

    For scoped packages (@scope/name), returns '@scope/name'.
    For bare packages with subpaths (lodash/get), returns 'lodash'.

    Args:
        import_path: The import path string.

    Returns:
        The npm package name.
    """
    if import_path.startswith("@"):
        # Scoped: @scope/name or @scope/name/subpath
        parts = import_path.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return import_path
    # Bare: name or name/subpath
    return import_path.split("/")[0]


def link_js_modules(
    *,
    repo_root: Path,
    symbols: list[Symbol],
    edges: list[Edge],
) -> LinkerResult:
    """Resolve JS/TS import edges to file-level and npm package symbols.

    For each unresolved import edge:
    1. Relative imports (./foo, ../bar) → resolve to actual file, create
       module_file symbol and imports_module + module_exports edges.
    2. npm packages (lodash, @vue/x) → create npm_package symbol and
       imports_module edge.
    3. Dynamic imports (<dynamic:var>) → skip.

    Args:
        repo_root: Repository root path.
        symbols: All symbols in the graph.
        edges: All edges in the graph.

    Returns:
        LinkerResult with new symbols and edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version="0.1.0")

    new_symbols: list[Symbol] = []
    new_edges: list[Edge] = []

    # Build map: symbol_id -> path (for resolving src file location)
    path_by_id: dict[str, str] = {}
    for sym in symbols:
        path_by_id[sym.id] = sym.path

    # Build map: normalized_file_path -> list of exportable symbols
    symbols_by_file: dict[str, list[Symbol]] = defaultdict(list)
    for sym in symbols:
        if sym.kind in _EXPORTABLE_KINDS and sym.language in _JS_LANGUAGES:
            symbols_by_file[sym.path].append(sym)

    # Cache for module_file and npm_package symbols to avoid duplicates
    module_file_cache: dict[str, Symbol] = {}
    npm_package_cache: dict[str, Symbol] = {}
    # Track which module_files have had their exports wired
    wired_exports: set[str] = set()

    # Find import edges
    import_edges = [e for e in edges if e.edge_type == "imports"]
    if not import_edges:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(symbols=[], edges=[], run=run)

    for edge in import_edges:
        # Parse the dst to extract import path
        parsed = _extract_import_path(edge.dst)
        if parsed is None:
            continue
        lang, import_path = parsed

        # Skip dynamic imports
        if _is_dynamic_import(import_path):
            continue

        # Get source file path (from symbol list, or fallback to parsing ID)
        src_path = path_by_id.get(edge.src) or _extract_path_from_id(edge.src)
        if not src_path:
            continue

        if _is_relative_import(import_path):
            # Resolve relative import to actual file
            source_dir = Path(src_path).parent
            base_path = (source_dir / import_path).resolve()
            resolved = _probe_file(base_path)
            if resolved is None:
                continue

            # Compute path relative to repo_root for the symbol
            try:
                rel_path = str(resolved.relative_to(repo_root))
            except ValueError:
                # Resolved path outside repo — use absolute
                rel_path = str(resolved)

            resolved_str = str(resolved)

            # Get or create module_file symbol
            if rel_path not in module_file_cache:
                sym_id = _make_module_file_id(rel_path, lang)
                mod_sym = Symbol(
                    id=sym_id,
                    stable_id=sym_id,
                    name=resolved.stem,
                    kind="module_file",
                    language=lang,
                    path=rel_path,
                    span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={},
                )
                module_file_cache[rel_path] = mod_sym
                new_symbols.append(mod_sym)

            mod_sym = module_file_cache[rel_path]

            # Create imports_module edge: importing file -> module_file
            new_edges.append(Edge.create(
                src=edge.src,
                dst=mod_sym.id,
                edge_type="imports_module",
                line=edge.line,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="import_resolution",
                confidence=0.90,
            ))

            # Create module_exports edges (only once per module_file)
            if rel_path not in wired_exports:
                wired_exports.add(rel_path)
                # Find symbols in the resolved file (try both abs and rel paths)
                file_symbols = symbols_by_file.get(
                    resolved_str, symbols_by_file.get(rel_path, [])
                )
                for file_sym in file_symbols:
                    new_edges.append(Edge.create(
                        src=mod_sym.id,
                        dst=file_sym.id,
                        edge_type="module_exports",
                        line=0,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="module_export_heuristic",
                        confidence=0.75,
                    ))

        elif _is_npm_package(import_path):
            # npm package import
            pkg_name = _get_npm_package_name(import_path)

            if pkg_name not in npm_package_cache:
                pkg_id = _make_npm_package_id(pkg_name, lang)
                pkg_sym = Symbol(
                    id=pkg_id,
                    stable_id=pkg_id,
                    name=pkg_name,
                    kind="npm_package",
                    language=lang,
                    path="",
                    span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"package_name": pkg_name},
                )
                npm_package_cache[pkg_name] = pkg_sym
                new_symbols.append(pkg_sym)

            pkg_sym = npm_package_cache[pkg_name]

            new_edges.append(Edge.create(
                src=edge.src,
                dst=pkg_sym.id,
                edge_type="imports_module",
                line=edge.line,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="npm_package_import",
                confidence=0.95,
            ))

    run.files_analyzed = len(module_file_cache)
    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(symbols=new_symbols, edges=new_edges, run=run)


def _count_js_import_edges(ctx: LinkerContext) -> int:
    """Count JS/TS import edges with unresolved module destinations.

    Used as activation check for the linker.

    Args:
        ctx: LinkerContext with edges.

    Returns:
        Count of import edges with module:module dst format.
    """
    count = 0
    for edge in ctx.edges:
        if edge.edge_type != "imports":
            continue
        if _extract_import_path(edge.dst) is not None:
            count += 1
    return count


@register_linker(
    "js-modules",
    priority=20,  # Before Vue component linker (25) since it handles JS imports
    description="JS/TS module import resolution",
    activation=LinkerActivation(
        language_pairs=[
            ("javascript", "javascript"),
            ("typescript", "typescript"),
            ("javascript", "typescript"),
        ],
    ),
)
def link_js_module(ctx: LinkerContext) -> LinkerResult:
    """Entry point for the JS module linker via the registry.

    Args:
        ctx: LinkerContext with repo_root, symbols, and edges.

    Returns:
        LinkerResult with resolved module symbols and edges.
    """
    return link_js_modules(
        repo_root=ctx.repo_root,
        symbols=ctx.symbols,
        edges=ctx.edges,
    )
