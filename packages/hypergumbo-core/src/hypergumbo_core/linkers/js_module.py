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

Path Alias Resolution
---------------------
Non-relative imports that aren't npm packages may be project aliases:
- **tsconfig.json paths**: `@/*` -> `./src/*` (Next.js, Vite, Angular)
- **Vite resolve.alias**: `dashboard` -> `./app/javascript/dashboard`
The linker reads these config files at startup and expands aliases before
resolving to files. This handles 68% of GrowthBook's TS imports and 33%
of Chatwoot's JS imports.

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

import json as json_module
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

# Config file search order for tsconfig-style path aliases.
_TSCONFIG_NAMES = ("tsconfig.json", "tsconfig.base.json", "jsconfig.json")

# Vite config file names in search priority order.
_VITE_CONFIG_NAMES = (
    "vite.config.ts", "vite.config.js",
    "vite.config.mts", "vite.config.mjs",
)

# Regex for extracting alias entries from Vite config files.
# Matches patterns like:
#   dashboard: resolve('./app/javascript/dashboard'),
#   '@': path.resolve(__dirname, 'src'),
#   'utils': resolve("./src/utils"),
_VITE_ALIAS_RE = re.compile(
    r"""
    ['"]?                           # optional quotes around key
    ([\w@][\w@/.-]*)                # alias name (group 1)
    ['"]?                           # optional closing quote
    \s*:\s*                         # colon separator
    (?:                             # value alternatives:
        (?:path\.)?resolve\s*\(     #   resolve( or path.resolve(
        (?:__dirname\s*,\s*)?       #   optional __dirname,
        ['"`]([^'"`]+)['"`]         #   quoted path (group 2)
        \s*\)                       #   closing paren
    |                               # OR:
        ['"`](\./[^'"`]+)['"`]      #   plain quoted relative path (group 3)
    )
    """,
    re.VERBOSE,
)

# Maximum depth for following tsconfig extends chains (prevents infinite loops).
_MAX_EXTENDS_DEPTH = 5


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


def _strip_jsonc_comments(text: str) -> str:
    """Strip JSONC comments (// and /* */) and trailing commas from text.

    tsconfig.json files commonly use JSONC format with single-line comments,
    block comments, and trailing commas. Python's json module can't parse these,
    so we strip them before parsing.

    Handles strings correctly: // inside "..." is preserved (not a comment).

    Args:
        text: JSONC text content.

    Returns:
        JSON-compatible text with comments and trailing commas removed.
    """
    result: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        # String literal — pass through unchanged
        if text[i] in ('"', "'"):
            quote = text[i]
            result.append(quote)
            i += 1
            while i < n and text[i] != quote:
                if text[i] == "\\" and i + 1 < n:
                    result.append(text[i : i + 2])
                    i += 2
                else:
                    result.append(text[i])
                    i += 1
            if i < n:
                result.append(text[i])
                i += 1
        # Single-line comment
        elif text[i : i + 2] == "//":
            # Skip until end of line
            while i < n and text[i] != "\n":
                i += 1
        # Block comment
        elif text[i : i + 2] == "/*":
            i += 2
            while i < n - 1 and text[i : i + 2] != "*/":
                i += 1
            if i < n - 1:
                i += 2  # skip */
        else:
            result.append(text[i])
            i += 1

    cleaned = "".join(result)
    # Remove trailing commas before } or ]
    cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)
    return cleaned


def _load_tsconfig_aliases(repo_root: Path) -> list[tuple[str, Path]]:
    """Load path aliases from tsconfig.json (or jsconfig.json).

    Parses compilerOptions.paths with baseUrl to produce a list of
    (prefix, target_dir) tuples. Follows 'extends' chains up to
    _MAX_EXTENDS_DEPTH to find inherited paths.

    For wildcard patterns like '@/*' → ['./src/*']:
      Returns ("@/", repo_root / "src")
    For exact patterns like 'shared' → ['./packages/shared/src/index']:
      Returns ("shared", repo_root / "packages/shared/src/index")

    Args:
        repo_root: Repository root directory.

    Returns:
        List of (prefix, target_dir) tuples sorted by prefix length
        (longest first for most-specific matching).
    """
    # Find the config file
    config_path = None
    for name in _TSCONFIG_NAMES:
        candidate = repo_root / name
        if candidate.is_file():
            config_path = candidate
            break

    if config_path is None:
        return []

    # Follow extends chain to find compilerOptions with paths
    base_url = "."
    paths: dict[str, list[str]] = {}
    visited: set[str] = set()

    current = config_path
    for _ in range(_MAX_EXTENDS_DEPTH):
        current_str = str(current)
        if current_str in visited or not current.is_file():
            break
        visited.add(current_str)

        try:
            raw = current.read_text(encoding="utf-8")
            data = json_module.loads(_strip_jsonc_comments(raw))
        except (json_module.JSONDecodeError, OSError):
            break

        compiler_opts = data.get("compilerOptions", {})

        # Inherit baseUrl and paths from this level if present
        if "baseUrl" in compiler_opts and not paths:
            base_url = compiler_opts["baseUrl"]
        if "paths" in compiler_opts and not paths:
            paths = compiler_opts["paths"]

        # Follow extends chain
        extends = data.get("extends")
        if extends and isinstance(extends, str):
            # Resolve relative to current config file's directory
            parent_dir = current.parent
            ext_path = (parent_dir / extends).resolve()
            # Add .json if no extension
            if not ext_path.suffix:
                ext_path = ext_path.with_suffix(".json")
            current = ext_path
        else:
            break

    if not paths:
        return []

    # Resolve baseUrl relative to the original config file's directory
    base_dir = (config_path.parent / base_url).resolve()

    aliases: list[tuple[str, Path]] = []
    for pattern, targets in paths.items():
        if not targets:
            continue
        target = targets[0]  # TypeScript supports fallbacks; use first

        if pattern.endswith("/*") and target.endswith("/*"):
            # Wildcard: @/* → ./src/*
            prefix = pattern[:-1]  # "@/" (strip trailing *)
            target_rel = target[:-1]  # "./src/" (strip trailing *)
            # Strip leading ./ for path resolution
            if target_rel.startswith("./"):
                target_rel = target_rel[2:]
            target_dir = (base_dir / target_rel).resolve()
            aliases.append((prefix, target_dir))
        else:
            # Exact: shared → ./packages/shared/src/index
            prefix = pattern
            target_rel = target
            if target_rel.startswith("./"):
                target_rel = target_rel[2:]
            target_path = (base_dir / target_rel).resolve()
            aliases.append((prefix, target_path))

    # Sort by prefix length (longest first) for most-specific matching
    aliases.sort(key=lambda a: len(a[0]), reverse=True)
    return aliases


def _load_vite_aliases(repo_root: Path) -> list[tuple[str, Path]]:
    """Load path aliases from Vite config files (vite.config.ts/js).

    Uses regex to extract alias definitions from the JavaScript/TypeScript
    config file. Handles common patterns:
    - resolve('./path') and resolve("./path")
    - path.resolve(__dirname, 'path')
    - Quoted and unquoted alias keys

    Args:
        repo_root: Repository root directory.

    Returns:
        List of (alias_name, target_dir) tuples.
    """
    config_path = None
    for name in _VITE_CONFIG_NAMES:
        candidate = repo_root / name
        if candidate.is_file():
            config_path = candidate
            break

    if config_path is None:
        return []

    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return []

    aliases: list[tuple[str, Path]] = []
    for match in _VITE_ALIAS_RE.finditer(content):
        alias_name = match.group(1)
        # group(2) is from resolve() call, group(3) is plain string
        raw_path = match.group(2) or match.group(3)
        if not raw_path:
            continue  # pragma: no cover

        # Strip leading ./ for path resolution
        clean_path = raw_path
        if clean_path.startswith("./"):
            clean_path = clean_path[2:]

        target_dir = (repo_root / clean_path).resolve()
        aliases.append((alias_name, target_dir))

    return aliases


def _resolve_alias(
    import_path: str,
    aliases: list[tuple[str, Path]],
) -> Path | None:
    """Try to resolve an import path using project aliases.

    Supports two alias types:
    1. Wildcard (prefix ends with '/'): '@/' matches '@/foo/bar'
       - Strips prefix, appends remainder to target directory
    2. Exact: 'dashboard' matches 'dashboard' and 'dashboard/foo'
       - Exact match resolves to target directory itself
       - Subpath match appends the path after alias name

    Aliases should be sorted by prefix length (longest first) so that
    more specific aliases take priority over general ones.

    Args:
        import_path: The non-relative import path to resolve.
        aliases: List of (prefix, target_dir) tuples.

    Returns:
        Resolved file Path if found, None otherwise.
    """
    for prefix, target_dir in aliases:
        if prefix.endswith("/"):
            # Wildcard alias: @/ matches @/foo
            if import_path.startswith(prefix):
                remainder = import_path[len(prefix):]
                base_path = target_dir / remainder if remainder else target_dir
                resolved = _probe_file(base_path)
                if resolved is not None:
                    return resolved
        else:
            # Exact alias: 'dashboard' matches 'dashboard' and 'dashboard/foo'
            if import_path == prefix:
                resolved = _probe_file(target_dir)
                if resolved is not None:
                    return resolved
            elif import_path.startswith(prefix + "/"):
                remainder = import_path[len(prefix) + 1:]
                base_path = target_dir / remainder
                resolved = _probe_file(base_path)
                if resolved is not None:
                    return resolved

    return None


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

    # Load path aliases from tsconfig.json and vite.config.* (once per run)
    aliases = _load_tsconfig_aliases(repo_root) + _load_vite_aliases(repo_root)

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

        # Resolve import path to actual file on disk
        resolved: Path | None = None
        evidence_type = "import_resolution"
        import_confidence = 0.90

        if _is_relative_import(import_path):
            # Relative import: resolve from source file's directory
            source_dir = Path(src_path).parent
            base_path = (source_dir / import_path).resolve()
            resolved = _probe_file(base_path)
        else:
            # Non-relative: try alias resolution first, then npm package
            if aliases:
                resolved = _resolve_alias(import_path, aliases)
                if resolved is not None:
                    evidence_type = "alias_resolution"
                    import_confidence = 0.85

        if resolved is not None:
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
                evidence_type=evidence_type,
                confidence=import_confidence,
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

        elif not _is_relative_import(import_path):
            # Non-relative import that didn't resolve via alias → npm package
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
