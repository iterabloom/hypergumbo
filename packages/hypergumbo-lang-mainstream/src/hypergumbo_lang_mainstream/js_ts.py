# SPDX-License-Identifier: AGPL-3.0-or-later
"""JavaScript/TypeScript/Svelte analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse JS/TS/Svelte/Vue files and
extract:
- Function and class declarations (symbols)
- Import/require statements (edges)
- Function call relationships (edges)
- Method call relationships (edges)
- Object instantiation relationships (edges)
- Inheritance: ``extends`` / ``implements`` (edges)
- Decorator application: ``decorated_by`` (edges)
- TypeScript type references — type alias / interface signatures —
  emitted as ``type_ref`` edges (refactoring blast radius)

Cross-file call edges populate ``Edge.dst_ref`` with the canonical
``(lang, module_path, name)`` triple resolved through the per-file
import scope's ``named_import_originals`` map, so renamed imports
(``import { foo as bar }``) attribute to ``foo``, not ``bar``.

Rich Metadata (ADR-3aaa)
------------------------
Class and method symbols include rich metadata in their `meta` field:

**Class metadata:**
- `decorators`: List of decorator dicts with name, args, kwargs
  Example: `@Controller('/users')` → `{"name": "Controller", "args": ["/users"], "kwargs": {}}`
- `base_classes`: List of base class/interface names including generics
  Example: `extends Repository<User> implements IService` → `["Repository<User>", "IService"]`

**Method metadata:**
- `decorators`: List of decorator dicts with name, args, kwargs
- `route_path`: NestJS route path if detected (legacy, also in decorators)

If tree-sitter is not installed, the analyzer gracefully degrades and
reports the pass as skipped with reason.

How It Works
------------
1. Check if tree-sitter and language grammars are available
2. If not available, return empty result with skip reason
3. Three-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
   - Pass 3: Extract usage contexts for resolved call edges
4. For Svelte / Vue files, extract <script> blocks and parse as TS/JS

Svelte Support
--------------
Svelte files contain <script> blocks with TypeScript or JavaScript.
We extract these blocks, preserving line numbers for accurate spans,
and analyze them using the appropriate tree-sitter grammar.

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Graceful degradation ensures CLI still works without tree-sitter
- Tree-sitter provides accurate parsing even for complex syntax
- Three-pass allows cross-file call resolution and usage-context capture
- Svelte / Vue support reuses existing TS/JS parsing infrastructure
- Uses iterative traversal to avoid RecursionError on deeply nested code
"""
from __future__ import annotations

import re
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional, TypeAlias

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import (
    AnalysisRun, Edge, ExternalRef, PASS_VERSION, Span, Symbol, UsageContext,
    make_pass_id,
)
from hypergumbo_core.paths import normalize_path
from hypergumbo_core.qualified_name_axis import separator_for_language
from hypergumbo_core.symbol_resolution import NameResolver, ListNameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    defer_bare_method_call,
    emit_module_attribute_refs,
    make_symbol_id,
    make_unresolved_edge,
    populate_docstrings_from_tree,
    find_child_by_field,
    iter_tree,
    make_file_id,
    make_file_stable_id,
    make_route_stable_id,
    make_route_symbol,
    make_typed_stable_id,
    make_variable_stable_id,
    node_text as _node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.dataflow import annotate_dataflow, get_dataflow_config
from hypergumbo_lang_mainstream.symbol_introspection import (
    compute_cyclomatic_complexity,
    extract_preceding_doc_comment,
)

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("javascript")

# ADR-0015: Dataflow config for JS/TS — loaded once at module level
_df_config = get_dataflow_config("javascript")


def find_js_ts_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all JS/TS files in the repository, excluding common non-source dirs.

    Includes the module-variant extensions ``.mjs``/``.cjs`` (ES-module /
    CommonJS JavaScript) and ``.mts``/``.cts`` (their TypeScript counterparts);
    omitting them left whole CommonJS service files undiscovered (WI-zavad)."""
    yield from find_files(
        repo_root,
        ["*.js", "*.jsx", "*.ts", "*.tsx", "*.mjs", "*.cjs", "*.mts", "*.cts"],
        max_files=max_files,
    )


def find_svelte_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all Svelte files in the repository."""
    yield from find_files(repo_root, ["*.svelte"], max_files=max_files)


def find_vue_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all Vue SFC files in the repository."""
    yield from find_files(repo_root, ["*.vue"], max_files=max_files)


# Regex to extract <script> blocks from Svelte files
# Captures: lang attribute (if present) and script content
_SVELTE_SCRIPT_RE = re.compile(
    r'<script(?:\s+lang=["\']?(ts|typescript)["\']?)?[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# Regex to extract <script> blocks from Vue SFC files
# Handles both regular <script> and <script setup> variants
# Captures: lang attribute (if present) and script content
_VUE_SCRIPT_RE = re.compile(
    r'<script(?:\s+setup)?(?:\s+lang=["\']?(ts|typescript)["\']?)?'
    r'(?:\s+setup)?[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class SvelteScriptBlock:
    """Extracted script block from a Svelte file."""

    content: str
    start_line: int  # 1-indexed line where script content starts
    is_typescript: bool


def extract_svelte_scripts(source: str) -> list[SvelteScriptBlock]:
    """Extract <script> blocks from Svelte file content.

    Returns list of script blocks with their content and line offsets.
    Handles both TypeScript (lang="ts") and JavaScript scripts.
    """
    blocks: list[SvelteScriptBlock] = []

    # Find all script tags with their positions
    for match in _SVELTE_SCRIPT_RE.finditer(source):
        lang = match.group(1)
        content = match.group(2)
        is_ts = lang is not None and lang.lower() in ("ts", "typescript")

        # Calculate line number where content starts
        # Count newlines before the match start
        prefix = source[: match.start()]
        tag_start_line = prefix.count("\n") + 1

        # Find where the actual content starts (after the opening tag)
        tag_text = match.group(0)
        opening_tag_end = tag_text.find(">") + 1
        opening_tag_lines = tag_text[:opening_tag_end].count("\n")
        content_start_line = tag_start_line + opening_tag_lines

        blocks.append(
            SvelteScriptBlock(
                content=content,
                start_line=content_start_line,
                is_typescript=is_ts,
            )
        )

    return blocks


@dataclass
class VueScriptBlock:
    """Extracted script block from a Vue SFC file."""

    content: str
    start_line: int  # 1-indexed line where script content starts
    is_typescript: bool


def extract_vue_scripts(source: str) -> list[VueScriptBlock]:
    """Extract <script> blocks from Vue SFC file content.

    Returns list of script blocks with their content and line offsets.
    Handles both TypeScript (lang="ts") and JavaScript scripts.
    Also handles <script setup> blocks.
    """
    blocks: list[VueScriptBlock] = []

    # Find all script tags with their positions
    for match in _VUE_SCRIPT_RE.finditer(source):
        lang = match.group(1)
        content = match.group(2)
        is_ts = lang is not None and lang.lower() in ("ts", "typescript")

        # Calculate line number where content starts
        # Count newlines before the match start
        prefix = source[: match.start()]
        tag_start_line = prefix.count("\n") + 1

        # Find where the actual content starts (after the opening tag)
        tag_text = match.group(0)
        opening_tag_end = tag_text.find(">") + 1
        opening_tag_lines = tag_text[:opening_tag_end].count("\n")
        content_start_line = tag_start_line + opening_tag_lines

        blocks.append(
            VueScriptBlock(
                content=content,
                start_line=content_start_line,
                is_typescript=is_ts,
            )
        )

    return blocks


class JstsTreeSitterAnalyzer(TreeSitterAnalyzer):
    """TreeSitterAnalyzer wrapper for JavaScript/TypeScript/Svelte/Vue files.

    Overrides ``analyze()`` entirely because JS/TS analysis is extremely
    complex: it handles multiple file types (JS, TS, Svelte, Vue), uses
    three passes (symbols, edges, usage contexts), and has custom resolvers
    (NameResolver, ListNameResolver). The base class provides grammar
    availability checking via ``_check_grammar_available()``.
    """

    lang = "javascript"
    file_patterns: ClassVar[list[str]] = [
        "*.js", "*.jsx", "*.ts", "*.tsx", "*.mjs", "*.cjs", "*.mts", "*.cts",
        "*.svelte", "*.vue",
    ]
    grammar_module = "tree_sitter_javascript"

    def analyze(self, repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        """Run the JS/TS analysis using the existing analyze logic."""
        return _analyze_javascript_impl(repo_root, max_files=max_files)


_jsts_analyzer = JstsTreeSitterAnalyzer()


def is_tree_sitter_available() -> bool:
    """Check if tree-sitter and required grammars are available."""
    return _jsts_analyzer._check_grammar_available()


# Backwards compatibility alias
JsAnalysisResult: TypeAlias = AnalysisResult


@dataclass
class _ParsedFile:
    """Holds parsed file data for two-pass analysis.

    Type inference sources for variable method call resolution (e.g., client.send()):
    1. Direct constructor calls: client = new Client() → var_types['client'] = 'Client'
    2. Return type annotations (TypeScript): client = getClient() where
       getClient(): Client → var_types['client'] = 'Client'
    3. Parameter type annotations: constructor(private db: Database) → var_types['db'] = 'Database'
    """

    path: Path
    tree: "tree_sitter.Tree"
    source: bytes
    lang: str
    line_offset: int = 0  # For Svelte script blocks
    # Maps local alias -> module name for 'import * as alias' and 'import alias'
    namespace_imports: dict[str, str] | None = None
    # Maps imported local-name (alias or original) -> module path for
    # 'import { Foo as Bar } from "module"' (Bar -> module)
    named_imports: dict[str, str] | None = None
    # WI-kujom: maps local-alias -> original imported name for
    # 'import { Foo as Bar } from "module"' (Bar -> Foo). Powers
    # dst_ref / dst-string population: at the call site, the analyzer
    # sees ``Bar(...)`` but the canonical name for downstream
    # consumers is ``Foo``. Same key set as named_imports.
    named_import_originals: dict[str, str] | None = None


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str, lang: str) -> str:
    """Generate a location-based ID via the shared ADR-0036 minter.

    Delegates rather than re-implementing the grammar as an f-string: a private
    copy silently opts out of the minter's name-slot sanitization (WI-sikar).
    The argument order differs from ``make_symbol_id`` (``lang`` last) because
    every call site in this module passes it that way.
    """
    return make_symbol_id(lang, path, start_line, end_line, name, kind)


def _get_language_for_file(file_path: Path) -> str:
    """Determine language based on file extension.

    ``.mts``/``.cts`` are TypeScript (ES-module / CommonJS variants);
    ``.mjs``/``.cjs`` fall through to JavaScript like ``.js``."""
    suffix = file_path.suffix.lower()
    if suffix in (".ts", ".tsx", ".mts", ".cts"):
        return "typescript"
    return "javascript"


def _get_parser_for_file(file_path: Path) -> Optional["tree_sitter.Parser"]:
    """Get appropriate tree-sitter parser for file type."""
    try:
        import tree_sitter
        import tree_sitter_javascript
    except ImportError:
        return None

    suffix = file_path.suffix.lower()
    parser = tree_sitter.Parser()

    if suffix in (".ts", ".tsx", ".mts", ".cts"):
        try:
            import tree_sitter_typescript

            if suffix == ".tsx":
                lang_ptr = tree_sitter_typescript.language_tsx()
            else:
                lang_ptr = tree_sitter_typescript.language_typescript()
            parser.language = tree_sitter.Language(lang_ptr)
            return parser
        except ImportError:
            # Fall back to JavaScript parser for TS files
            parser.language = tree_sitter.Language(tree_sitter_javascript.language())
            return parser
    else:
        parser.language = tree_sitter.Language(tree_sitter_javascript.language())
        return parser


def _normalize_import_module_hint(module: str) -> str:
    """Normalise an import path to a module hint usable in symbol IDs.

    Symbol IDs are colon-delimited (``lang:module:span:name:kind``) so a
    raw ``node:fs`` import path would corrupt the parse downstream.  We
    strip the ``node:`` prefix (Node 16+ canonical form for built-ins)
    and the relative-path leaders (``./``, ``../``) so the module hint
    becomes the bare module name expected by the I/O catalog
    (``fs``, ``child_process``, ``axios``).

    URL imports (``https://cdn/x``) and Deno specifiers (``npm:lit``,
    ``jsr:@std/foo``) carry their own ``:`` which would inject extra segments
    into the colon-delimited symbol ID, so their scheme is stripped and any
    residual ``:`` (e.g. a URL port) is sanitised to ``_``.

    Examples:
        node:fs           -> fs
        node:child_process -> child_process
        npm:lit@3         -> lit@3
        jsr:@std/foo      -> @std/foo
        https://cdn/x     -> cdn/x
        ./utils           -> utils
        ../helpers/git    -> helpers/git
        @scope/pkg        -> @scope/pkg (unchanged)
    """
    for prefix in ("https://", "http://", "node:", "npm:", "jsr:"):
        if module.startswith(prefix):
            module = module[len(prefix):]
            break
    while module.startswith("./") or module.startswith("../"):
        module = module[2:] if module.startswith("./") else module[3:]
    # Any residual ``:`` (URL port, deep deno specifier) would break the
    # colon-delimited symbol-id grammar (lang:module:span:name:kind).
    return module.replace(":", "_")


def _require_module_string(
    call_node: "tree_sitter.Node", source: bytes
) -> str | None:
    """Module string of a ``require('<literal>')`` call expression, else None.

    Mirrors the require-detection in :func:`_extract_edges`: the callee must be
    the bare identifier ``require`` and the first argument a string literal.
    Returns None for non-require calls (e.g. ``compute(x)``, ``obj.load(x)``)
    and for dynamic ``require(name)`` (no string literal), so a non-module
    binding never masquerades as a CommonJS import alias.
    """
    func_node = None
    args_node = None
    for child in call_node.children:
        if child.type in ("identifier", "member_expression"):
            func_node = child
        elif child.type == "arguments":
            args_node = child
    if func_node is None or func_node.type != "identifier":
        return None
    if _node_text(func_node, source) != "require" or args_node is None:
        return None
    for arg in args_node.children:
        if arg.type == "string":
            return _node_text(arg, source).strip("'\"")
    return None


def _extract_namespace_imports(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract namespace imports from a parsed tree.

    Tracks:
    - import * as alias from 'module' -> alias: module
    - import alias from 'module' (default import) -> alias: module
    - CommonJS ``const alias = require('module')`` -> alias: module (WI-zavad)

    Returns dict mapping alias -> module name.
    """
    namespace_imports: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import_statement":
            continue

        module_name = None
        alias = None

        for child in node.children:
            if child.type == "string":
                module_name = _node_text(child, source).strip("'\"")
            elif child.type == "import_clause":
                # Look for namespace_import or default import identifier
                for clause_child in child.children:
                    if clause_child.type == "namespace_import":
                        # import * as alias from 'module'
                        for ns_child in clause_child.children:
                            if ns_child.type == "identifier":
                                alias = _node_text(ns_child, source)
                    elif clause_child.type == "identifier":
                        # import alias from 'module' (default import)
                        alias = _node_text(clause_child, source)

        if module_name and alias:
            namespace_imports[alias] = module_name

    # CommonJS default-style binding: ``const fs = require('fs')`` /
    # ``var http = require('http')`` binds a module to a name exactly like an
    # ESM default/namespace import (WI-zavad). Registering it here lets member
    # calls (``fs.readFileSync()``) route through the same namespace resolution
    # (Case 2 / WI-vurop) in _extract_edges instead of vanishing — the gap that
    # left CommonJS Node I/O invisible to the io-boundaries layer.
    for node in iter_tree(tree.root_node):
        if node.type != "variable_declarator":
            continue
        name_node = None
        value_node = None
        for child in node.children:
            if child.type in ("identifier", "object_pattern") and name_node is None:
                name_node = child
            elif child.type == "call_expression":
                value_node = child
        if value_node is None:
            continue
        module = _require_module_string(value_node, source)
        if module is None:
            continue
        # Destructuring (``const { x } = require(...)``) is a *named* import,
        # handled by _extract_named_imports; only a plain identifier name binds
        # the whole module as a namespace alias.
        if name_node is not None and name_node.type == "identifier":
            namespace_imports[_node_text(name_node, source)] = module

    return namespace_imports


def _extract_named_imports(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> tuple[dict[str, str], dict[str, str]]:
    """Extract named imports from a parsed tree.

    Tracks: ``import { Foo, Bar as Baz } from 'module'`` →
    named_imports = {Foo: module, Baz: module},
    originals = {Foo: Foo, Baz: Bar}.

    Returns a pair of dicts:
    1. ``named_imports``: local-name → module path. Used to
       disambiguate type references when multiple files define the
       same class name (e.g., monorepos with duplicate CatsService).
    2. ``named_import_originals``: local-name → original imported
       name (the name in the exporting module). For unaliased
       imports the value equals the key. WI-kujom: preserves the
       underlying name so call-emit can attribute the call to the
       canonical name, not the local alias.
    """
    named_imports: dict[str, str] = {}
    named_import_originals: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import_statement":
            continue

        module_name = None
        # Pairs of (local_name, original_name)
        import_pairs: list[tuple[str, str]] = []

        for child in node.children:
            if child.type == "string":
                module_name = _node_text(child, source).strip("'\"")
            elif child.type == "import_clause":
                for clause_child in child.children:
                    if clause_child.type == "named_imports":
                        for spec in clause_child.children:
                            if spec.type == "import_specifier":
                                # import { Foo as Bar } — use alias (Bar)
                                # import { Foo } — use original name (Foo)
                                alias_node = None
                                original_node = None
                                for sc in spec.children:
                                    if sc.type == "identifier":
                                        if original_node is None:
                                            original_node = sc
                                        else:
                                            alias_node = sc
                                if alias_node and original_node:
                                    import_pairs.append((
                                        _node_text(alias_node, source),
                                        _node_text(original_node, source),
                                    ))
                                elif original_node:
                                    orig = _node_text(original_node, source)
                                    import_pairs.append((orig, orig))

        if module_name:
            for local_name, original_name in import_pairs:
                named_imports[local_name] = module_name
                named_import_originals[local_name] = original_name

    # CommonJS destructuring: ``const { exec, spawn: sp } = require('cp')``
    # binds named exports exactly like ``import { exec, spawn as sp } from 'cp'``
    # (WI-zavad). Registering these lets a bare call (``exec()``) route through
    # the WI-banaf unresolved-named-import path in _extract_edges. The aliased
    # form preserves the canonical export name (``spawn``) over the local alias
    # (``sp``) — io-boundary catalogs key on the canonical name (cf. WI-kujom).
    for node in iter_tree(tree.root_node):
        if node.type != "variable_declarator":
            continue
        pattern_node = None
        value_node = None
        for child in node.children:
            if child.type == "object_pattern" and pattern_node is None:
                pattern_node = child
            elif child.type == "call_expression":
                value_node = child
        if pattern_node is None or value_node is None:
            continue
        module = _require_module_string(value_node, source)
        if module is None:
            continue
        for spec in pattern_node.children:
            if spec.type == "shorthand_property_identifier_pattern":
                local = _node_text(spec, source)
                named_imports[local] = module
                named_import_originals[local] = local
            elif spec.type == "pair_pattern":
                # ``{ readFile: rf }`` -> property_identifier (original) then
                # identifier (local alias).
                key_name = None
                local_name = None
                for sc in spec.children:
                    if sc.type == "property_identifier":
                        key_name = _node_text(sc, source)
                    elif sc.type == "identifier":
                        local_name = _node_text(sc, source)
                if key_name is not None and local_name is not None:
                    named_imports[local_name] = module
                    named_import_originals[local_name] = key_name

    return named_imports, named_import_originals


def _disambiguate_by_import(
    import_path: str,
    file_path: Path,
    full_name: str,
    symbols_by_name: dict[str, list["Symbol"]],
) -> "Symbol | None":
    """Disambiguate same-named symbols using the import path.

    When multiple files define the same symbol name (e.g., NestJS monorepo with
    duplicate CatsService in different apps), uses the relative import path from
    ``import { Foo } from './module'`` to select the correct symbol.

    Resolves the import path relative to the importing file's directory and
    matches against candidate symbol file paths (with extension stripped).
    Only handles relative imports (starting with '.').

    Returns the matching Symbol, or None if disambiguation fails.
    """
    if not import_path.startswith("."):
        return None  # Non-relative imports can't be disambiguated this way

    candidates = symbols_by_name.get(full_name)
    if not candidates or len(candidates) < 2:
        return None  # No disambiguation needed

    # Resolve import path relative to importing file's directory
    # e.g., './cats.service' relative to '/repo/dir_b/controller.ts'
    # -> '/repo/dir_b/cats.service'
    resolved = (file_path.parent / import_path).resolve()
    resolved_str = str(resolved)

    for candidate in candidates:
        # Strip file extension: '/repo/dir_b/cats.service.ts' -> '/repo/dir_b/cats.service'
        candidate_stem = str(Path(candidate.path).with_suffix(""))
        if candidate_stem == resolved_str:
            return candidate

    return None


def _find_package_root(file_path: Path) -> Path | None:
    """Walk up from *file_path* to find the nearest directory containing package.json."""
    current = file_path.parent if file_path.is_file() else file_path
    while current != current.parent:
        if (current / "package.json").exists():
            return current
        current = current.parent
    return None


def _same_package_candidate(
    file_path: Path,
    func_name: str,
    symbols_by_name: dict[str, list["Symbol"]],
) -> "Symbol | None":
    """Prefer same-package symbol when multiple packages define the same name.

    When ``symbols_by_name[func_name]`` has multiple candidates, picks the one
    whose file lives under the same npm package root (nearest ``package.json``
    ancestor) as *file_path*.  Returns None if there's only one candidate, if
    no package root is found, or if no candidate matches.
    """
    candidates = symbols_by_name.get(func_name)
    if not candidates or len(candidates) < 2:
        return None

    pkg_root = _find_package_root(file_path)
    if pkg_root is None:
        return None

    pkg_root_str = str(pkg_root)
    for candidate in candidates:
        if candidate.path.startswith(pkg_root_str):
            return candidate

    return None


def _is_cross_package(file_path: Path, target_path: str) -> bool:
    """Check if *target_path* is in a different npm package than *file_path*.

    Returns True when both paths have package.json ancestors and those
    ancestors differ.  Returns False when either path lacks a package.json
    ancestor (can't determine package boundary) or when both are in the
    same package.
    """
    src_root = _find_package_root(file_path)
    if src_root is None:
        return False
    target = Path(target_path)
    dst_root = _find_package_root(target)
    if dst_root is None:
        return False
    return src_root != dst_root


# JavaScript built-in constructor and global function names.
# Calls like `Number(x)`, `String(x)`, `Boolean(x)` are type conversions,
# not calls to user-defined functions.  Skip these during call resolution
# to prevent false-positive edges to user-defined components that shadow
# built-in names (e.g., a React component named `Number`).
# Built-in method names that should not be resolved via the method name
# fallback (Case 4).  These are methods on Array, Map, Set, Object, etc.
# that appear on virtually every JS object.  Without this blocklist,
# `items.forEach(cb)` resolves to a user-defined class that happens to
# define `forEach`, inflating its in-degree and corrupting centrality.
# Analogous to Rust's _RUST_GENERIC_TRAIT_METHODS blocklist.
JS_BUILTIN_METHODS: frozenset[str] = frozenset({
    # Array methods
    "push", "pop", "shift", "unshift", "splice", "slice",
    "concat", "join", "reverse", "sort", "indexOf", "lastIndexOf",
    "find", "findIndex", "includes", "every", "some",
    "filter", "map", "reduce", "reduceRight", "flat", "flatMap",
    "fill", "copyWithin", "entries", "keys", "values",
    "forEach", "at",
    # Map/Set methods
    "get", "set", "has", "delete", "clear",
    # Object methods
    "hasOwnProperty", "toString", "valueOf", "toJSON",
    "toLocaleString",
    # Promise methods
    "then", "catch", "finally",
    # EventEmitter (handled by event sourcing linker)
    "emit", "on", "once", "off", "addListener", "removeListener",
    "addEventListener", "removeEventListener",
    # String methods
    "trim", "split", "replace", "match", "search", "startsWith",
    "endsWith", "padStart", "padEnd", "repeat", "substring",
    "charAt", "charCodeAt", "normalize",
    # Iterator / generator
    "next", "return", "throw",
    # Console / logging — ubiquitous methods that resolve to wrong targets
    # (e.g., this.logger.warn() → test file's warn:method)
    "log", "warn", "error", "info", "debug", "trace",
})

JS_BUILTIN_NAMES: set[str] = {
    # Primitives / wrapper constructors
    "Number", "String", "Boolean", "Symbol", "BigInt",
    # Structural types
    "Object", "Array", "Function", "RegExp", "Date",
    # Error hierarchy
    "Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError",
    "URIError", "EvalError",
    # Collections
    "Map", "Set", "WeakMap", "WeakSet",
    # Async / promise
    "Promise",
    # Typed arrays
    "ArrayBuffer", "DataView", "Int8Array", "Uint8Array",
    "Uint8ClampedArray", "Int16Array", "Uint16Array",
    "Int32Array", "Uint32Array", "Float32Array", "Float64Array",
    "BigInt64Array", "BigUint64Array",
    # Other globals
    "JSON", "Math", "Reflect", "Proxy", "Intl",
    # Global functions
    "parseInt", "parseFloat", "isNaN", "isFinite",
    "encodeURI", "encodeURIComponent", "decodeURI", "decodeURIComponent",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "console", "require",
}

# Browser/runtime globals that may be called as ``obj.method()`` without an
# explicit import. Mirrors the module names used in
# ``hypergumbo-core/src/hypergumbo_core/io_primitives/javascript.yaml`` so
# the io-boundaries layer can tag the resulting unresolved-call edges.
# Only includes names actually used in the bare-global ``Object.method()``
# pattern — constructor-style globals (WebSocket, XMLHttpRequest,
# EventSource, BroadcastChannel) are typically ``new``'d first and reach
# io-boundaries through the instance path, not this fallback.
# See WI-pinop / WI-banaf / WI-vurop (UAT 2026-04-13 BUG-09a).
JS_KNOWN_GLOBALS: frozenset[str] = frozenset({
    "console",        # logging (console.log/info/warn/error/debug/trace)
    "localStorage",   # fs_read/fs_write (getItem, setItem, removeItem, clear)
    "sessionStorage", # fs_read/fs_write (same methods)
    "document",       # env_read (cookie, location, referrer attributes; DOM methods)
    "navigator",      # net_send / env_read (sendBeacon, userAgent, geolocation)
    "window",         # net_send / env_read (fetch, location, navigator, screen)
    "Deno",           # Deno runtime (readFile, writeFile, connect, listen, ...)
    "caches",         # Service Worker CacheStorage (open, match, has, keys)
    "indexedDB",      # Browser IndexedDB (open, databases)
})

# Bare global functions (called as ``fn(...)``, not ``obj.fn(...)``) that the
# io-boundary catalog recognises. ``fetch`` is the global network primitive
# (Node 18+ and browsers); unlike the JS_KNOWN_GLOBALS member receivers it is
# invoked directly, so it needs its own emission path. Resolution to an
# intra-repo or imported ``fetch`` takes precedence — only a truly-global bare
# ``fetch()`` reaches this set (WI-zavad / emission-parity F2).
JS_KNOWN_GLOBAL_CALLS: frozenset[str] = frozenset({
    "fetch",          # net_send (catalog: module fetch, functions [fetch])
})

# HTTP methods recognized as route handlers (Express, Fastify, Koa, etc.)
# Note: Express-style route detection uses function calls (app.get, router.post) rather
# than decorators. These are now matched via UsageContext (ADR-3aaa v1.1.x) which
# enables YAML patterns for call-based frameworks.
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# Known router/app receiver names for route detection (ADR-3aaa)
# Only calls like app.get(), router.post(), etc. are treated as routes.
# This prevents false positives from test mocks like fetchMock.get().
ROUTER_RECEIVER_NAMES = {"app", "router", "express", "server", "fastify", "koa"}

# Use find_child_by_field from base.py (imported above)
_find_child_by_field = find_child_by_field


def _extract_jsts_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a JS/TS function node.

    Returns a signature string like "(x: number, y: string): boolean" for TS
    or "(x, y)" for JS. None if extraction fails.

    Args:
        node: A tree-sitter function_declaration, arrow_function, or method node.
        source: Source bytes of the file.
    """
    # Find parameters - node type depends on function type
    params_node = None
    return_type_node = None

    if node.type == "function_declaration":
        params_node = _find_child_by_field(node, "parameters")
        return_type_node = _find_child_by_field(node, "return_type")
    elif node.type == "arrow_function":
        # Arrow functions: (params) => body or param => body
        params_node = _find_child_by_field(node, "parameters")
        if not params_node:  # pragma: no cover
            # Single parameter without parens: x => x
            params_node = _find_child_by_field(node, "parameter")
        return_type_node = _find_child_by_field(node, "return_type")
    elif node.type in (
        "method_definition", "function", "function_expression",
        "generator_function_declaration", "generator_function",
    ):
        # ``function`` / ``function_expression`` cover both anonymous and named
        # function expressions used as values — e.g. Express route handlers
        # ``app.get('/x', function h(req, res) {})`` (INV-golap route-handler gap).
        # ``generator_function_declaration`` (``function* g() {}``) and
        # ``generator_function`` (``const g = function*() {}``) reach signature
        # parity via the same ``parameters``/``return_type`` fields (WI-zavad
        # named function-node slice).
        params_node = _find_child_by_field(node, "parameters")
        return_type_node = _find_child_by_field(node, "return_type")
    else:
        return None  # pragma: no cover

    if not params_node:
        return None  # pragma: no cover

    # Build parameter list
    param_strs: list[str] = []
    for child in params_node.children:
        if child.type in ("required_parameter", "optional_parameter"):
            # TypeScript: name: type or name?: type
            param_text = _node_text(child, source)
            param_strs.append(param_text)
        elif child.type == "identifier":
            # JavaScript: just the name
            param_strs.append(_node_text(child, source))
        elif child.type == "assignment_pattern":
            # Default parameter: x = 5
            pattern_text = _node_text(child, source)
            # Simplify to show ... for default value
            if "=" in pattern_text:
                parts = pattern_text.split("=", 1)
                param_strs.append(f"{parts[0].strip()} = ...")
            else:
                param_strs.append(pattern_text)  # pragma: no cover
        elif child.type == "rest_pattern":
            # Rest parameter: ...args
            param_strs.append(_node_text(child, source))

    # Handle single parameter arrow functions (x => x without parens)
    if node.type == "arrow_function" and not param_strs and params_node.type == "identifier":  # pragma: no cover
        param_strs.append(_node_text(params_node, source))

    sig = "(" + ", ".join(param_strs) + ")"

    # Add return type for TypeScript
    if return_type_node:
        # Return type includes the ": Type" or just "Type"
        ret_text = _node_text(return_type_node, source)
        if not ret_text.startswith(":"):
            ret_text = f": {ret_text}"
        sig += ret_text

    return sig


def normalize_jsts_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a JS/TS signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_names_first
    return normalize_signature_names_first(signature, type_params, return_sep=":")


def _extract_jsts_return_type_name(signature: str | None) -> str | None:
    """Extract simple return type name from a JS/TS function signature.

    Parses signatures like "(x: number): MyClass" and returns "MyClass".
    Only handles simple (non-generic) return types — returns None for
    complex types like "Promise<X>", "X | Y", "X[]", etc.

    The return type in JS/TS signatures appears after "):" at the end,
    unlike Python which uses " -> ".

    Args:
        signature: Function signature string from Symbol.signature.

    Returns:
        The simple class name if found, None otherwise.
    """
    if not signature:
        return None
    # Find the closing paren, then look for ": ReturnType" after it
    paren_idx = signature.rfind(")")
    if paren_idx < 0:
        return None
    after_paren = signature[paren_idx + 1:].strip()
    if not after_paren.startswith(":"):
        return None
    ret_part = after_paren[1:].strip()
    # Only handle simple names (identifiers), not generics or unions
    if ret_part and ret_part.isidentifier():
        return ret_part
    return None


def _extract_param_types(
    node: "tree_sitter.Node", source: bytes
) -> dict[str, str]:
    """Extract parameter name -> type mapping from a function declaration.

    This enables type inference for method calls on parameters, e.g.:
        function process(client: Client) {
            client.send();  // resolves to Client.send
        }

    Only works for TypeScript code with explicit type annotations.

    Returns:
        Dict mapping parameter names to their type names (simple name only).
    """
    param_types: dict[str, str] = {}

    # Find parameters node - structure varies by function type
    params_node = None
    if node.type in (
        "function_declaration",
        "generator_function_declaration",
        "generator_function",
    ):
        # WI-zavad: typed params of generators feed method-call resolution in
        # the generator body (parity with ``function_declaration``).
        params_node = _find_child_by_field(node, "parameters")
    elif node.type == "arrow_function":
        params_node = _find_child_by_field(node, "parameters")
    elif node.type in ("method_definition", "function"):
        params_node = _find_child_by_field(node, "parameters")

    if not params_node:
        return param_types

    for child in params_node.children:
        if child.type in ("required_parameter", "optional_parameter"):
            param_name = None
            param_type = None

            for subchild in child.children:
                if subchild.type == "identifier" and param_name is None:
                    param_name = _node_text(subchild, source)
                elif subchild.type == "type_annotation":
                    # type_annotation contains type_identifier or other type nodes
                    for type_child in subchild.children:
                        if type_child.type == "type_identifier":
                            param_type = _node_text(type_child, source)
                            break
                        elif type_child.type == "generic_type":  # pragma: no cover
                            # Extract base type from generic: Array<T> -> Array
                            for gc in type_child.children:
                                if gc.type == "type_identifier":
                                    param_type = _node_text(gc, source)
                                    break
                            break

            if param_name and param_type:
                param_types[param_name] = param_type

    return param_types


def _build_jsx_route_meta(
    route_path: str,
    component_name: str | None,
    lazy_import_map: dict[str, str],
) -> dict[str, object]:
    """Build metadata dict for a JSX Route symbol.

    If the component is a React.lazy() wrapper, adds lazy_import metadata
    so the route can be traced through to the actual module.
    """
    meta: dict[str, object] = {
        "route_path": route_path,
        "http_method": "GET",
        "handler_ref": component_name,
    }
    if component_name and component_name in lazy_import_map:
        meta["lazy_import"] = lazy_import_map[component_name]
    return meta


def _collect_react_lazy_declaration(
    node: "tree_sitter.Node",
    source: bytes,
    lazy_map: dict[str, str],
) -> None:
    """Collect React.lazy(() => import('./path')) variable declarations.

    Detects both ``React.lazy(...)`` and ``lazy(...)`` forms (the latter when
    lazy is imported directly: ``import { lazy } from 'react'``).

    Populates lazy_map with variable_name → import_path mappings so that
    JSX route detection can annotate routes with lazy_import metadata.

    Handles:
    - ``const Foo = React.lazy(() => import('./Foo'))``
    - ``const Foo = lazy(() => import('./Foo'))``
    - Both ``variable_declarator`` nodes and ``lexical_declaration`` parents
    """
    # We want variable_declarator nodes: const Foo = React.lazy(...)
    declarators: list["tree_sitter.Node"] = []
    if node.type == "variable_declarator":
        declarators.append(node)
    elif node.type == "lexical_declaration":
        for child in node.children:
            if child.type == "variable_declarator":
                declarators.append(child)

    for decl in declarators:
        var_name = None
        call_node = None
        for child in decl.children:
            if child.type == "identifier":
                var_name = _node_text(child, source)
            elif child.type == "call_expression":
                call_node = child

        if var_name is None or call_node is None:
            continue

        # Check if the call is React.lazy(...) or lazy(...)
        fn_node = call_node.children[0] if call_node.children else None
        if fn_node is None:  # pragma: no cover - call_expression always has function node
            continue

        is_lazy = False
        if fn_node.type == "member_expression":
            text = _node_text(fn_node, source)
            if text == "React.lazy":
                is_lazy = True
        elif fn_node.type == "identifier":
            if _node_text(fn_node, source) == "lazy":
                is_lazy = True

        if not is_lazy:
            continue

        # Extract the dynamic import path from the arrow function argument:
        # React.lazy(() => import('./path'))
        args_node = None
        for child in call_node.children:
            if child.type == "arguments":
                args_node = child
                break

        if args_node is None:  # pragma: no cover - call_expression always has arguments
            continue

        # The argument should be an arrow function containing import()
        for arg in args_node.children:
            if arg.type == "arrow_function":
                import_path = _extract_lazy_import_path(arg, source)
                if import_path:
                    lazy_map[var_name] = import_path
                break


def _detect_jsx_route(
    node: "tree_sitter.Node", source: bytes,
) -> tuple[str | None, str | None]:
    """Detect React Router <Route path="..." /> JSX elements.

    Returns (route_path, component_name) if the node is a Route JSX element
    with a path attribute, else (None, None).

    Supported patterns:
    - <Route path="/users" element={<Users />} />
    - <Route path="/users" component={Users} />
    - <Route path="/users">...</Route>
    - <Route path="/" />  (index route)

    The tag name must be exactly "Route" (case-sensitive). The path attribute
    must have a string value. The component name is extracted from the element
    or component prop if present.
    """
    # Handle both self-closing and opening elements
    if node.type == "jsx_self_closing_element":
        tag_node = node
    elif node.type == "jsx_element":
        # Get the opening tag
        tag_node = None
        for child in node.children:
            if child.type == "jsx_opening_element":
                tag_node = child
                break
        if tag_node is None:  # pragma: no cover - valid JSX always has opening tag
            return None, None
    else:
        return None, None  # pragma: no cover - only called for JSX node types

    # tag_node is non-None on every reachable path above (self-closing → node;
    # jsx_element → the guarded opening tag); narrow it for the .children scans.
    assert tag_node is not None
    # Check the tag name is "Route"
    tag_name = None
    for child in tag_node.children:
        if child.type == "identifier":
            tag_name = _node_text(child, source)
            break
        elif child.type == "member_expression":
            # e.g., ReactRouter.Route
            tag_name = _node_text(child, source)
            if tag_name and tag_name.endswith(".Route"):
                tag_name = "Route"
            break

    if tag_name != "Route":
        return None, None

    # Extract path and component/element attributes
    route_path = None
    component_name = None

    for child in tag_node.children:
        if child.type != "jsx_attribute":
            continue

        attr_name = None
        attr_value = None
        # INV-dogif gap (4): only accept path values from string literals.
        # `<Route path={someVar}>` resolves the JSX expression to the identifier
        # *name*, not its runtime value — emitting that as route_path produces
        # a junk entry in routes.txt. Track the source so we can keep accepting
        # identifiers for component-shaped attrs (element/component/render)
        # while rejecting them for path.
        attr_value_from_string = False
        for attr_child in child.children:
            if attr_child.type == "property_identifier":
                attr_name = _node_text(attr_child, source)
            elif attr_child.type == "string":
                attr_value = _node_text(attr_child, source).strip("'\"")
                attr_value_from_string = True
            elif attr_child.type == "jsx_expression":
                # {<Users />} or {Users}
                for expr_child in attr_child.children:
                    if expr_child.type == "jsx_self_closing_element":
                        # <Users /> — extract tag name
                        for jsx_child in expr_child.children:
                            if jsx_child.type == "identifier":
                                attr_value = _node_text(jsx_child, source)
                                break
                    elif expr_child.type == "identifier":
                        attr_value = _node_text(expr_child, source)

        if attr_name == "path" and attr_value is not None and attr_value_from_string:
            route_path = attr_value
        # INV-dogif gap (3): React Router v5 render-prop is a component-shaped
        # attr alongside element/component (and v6 dropped render but legacy
        # codebases still ship it).
        elif attr_name in ("element", "component", "render") and attr_value is not None:
            component_name = attr_value

    if route_path is None:
        return None, None

    return route_path, component_name


def _detect_create_browser_router(
    node: "tree_sitter.Node",
    source: bytes,
) -> list[tuple[str, str | None, dict[str, str | None]]]:
    """Detect createBrowserRouter/createRoutesFromElements route config objects.

    Parses call expressions like:
        createBrowserRouter([
            { path: "/", element: <Root /> },
            { path: "/users", element: <Users />, children: [...] },
            { path: "/lazy", lazy: () => import("./LazyPage") },
            { path: "/data", loader: loadData, action: saveData },
        ])

    Returns a list of (route_path, component_name, extra_meta) tuples.
    extra_meta may contain 'loader_ref', 'action_ref', and 'lazy_import'.
    """
    if node.type != "call_expression":  # pragma: no cover
        return []

    # Check the function name
    func_name = None
    args_node = None
    for child in node.children:
        if child.type == "identifier":
            func_name = _node_text(child, source)
        elif child.type == "arguments":
            args_node = child

    if func_name not in ("createBrowserRouter", "createRoutesFromElements",
                         "createHashRouter", "createMemoryRouter"):
        return []
    if args_node is None:  # pragma: no cover
        return []

    # Find the array argument
    routes: list[tuple[str, str | None, dict[str, str | None]]] = []
    for arg in args_node.children:
        if arg.type == "array":
            _extract_route_objects(arg, source, "", routes)
            break

    return routes


def _extract_lazy_import_path(
    node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract the import path from a lazy route function.

    Handles patterns like:
        lazy: () => import('./Page')
        lazy: () => import("./components/Dashboard")

    Traverses the arrow function body to find a dynamic import() call
    and extracts its string argument.
    """
    # The value node for a lazy property is typically an arrow_function
    # containing a call_expression with import()
    nodes_to_check = [node]
    while nodes_to_check:
        current = nodes_to_check.pop()
        if current.type == "call_expression":
            # Check if it's import('...')
            for child in current.children:
                if child.type == "import":
                    # Found dynamic import — extract string argument
                    for arg_child in current.children:
                        if arg_child.type == "arguments":
                            for a in arg_child.children:
                                if a.type == "string":
                                    return _node_text(a, source).strip("'\"")
        for child in current.children:
            nodes_to_check.append(child)
    return None


def _extract_route_objects(
    array_node: "tree_sitter.Node",
    source: bytes,
    parent_path: str,
    routes: list[tuple[str, str | None, dict[str, str | None]]],
) -> None:
    """Recursively extract route path/component from route config objects.

    Handles nested children arrays for path composition. Also extracts
    loader, action, and lazy properties from React Router v6.4+ route
    config objects.

    Each entry in routes is (full_path, component_name, extra_meta) where
    extra_meta may contain 'loader_ref', 'action_ref', and 'lazy_import'.
    """
    for child in array_node.children:
        if child.type != "object":
            continue

        path = None
        component = None
        children_array = None
        loader_ref = None
        action_ref = None
        lazy_import = None

        for prop in child.children:
            if prop.type != "pair":
                continue
            key_node = None
            value_node = None
            for pc in prop.children:
                if pc.type in ("property_identifier", "string"):
                    if key_node is None:
                        key_node = pc
                    else:
                        value_node = pc
                elif key_node is not None:
                    value_node = pc

            if key_node is None or value_node is None:  # pragma: no cover
                continue

            key = _node_text(key_node, source).strip("'\"")
            if key == "path":
                # INV-dogif gap (5): only accept path values from string
                # literals. {path: someVar, ...} would otherwise emit a Route
                # node whose route_path is the variable's identifier text — a
                # junk entry in routes.txt. Dynamic paths can't be resolved
                # statically; skip emission to match the JSX-side behavior.
                if value_node.type == "string":
                    path = _node_text(value_node, source).strip("'\"")
            elif key == "element":
                # Extract component name from JSX: <Users /> → "Users"
                component = _extract_component_from_jsx(value_node, source)
            elif key == "Component":  # pragma: no cover - React Router v6.4+ lazy
                component = _node_text(value_node, source)
            elif key == "children" and value_node.type == "array":
                children_array = value_node
            elif key == "loader":
                loader_ref = _node_text(value_node, source)
            elif key == "action":
                action_ref = _node_text(value_node, source)
            elif key == "lazy":
                lazy_import = _extract_lazy_import_path(value_node, source)

        extra_meta: dict[str, str | None] = {}
        if loader_ref is not None:
            extra_meta["loader_ref"] = loader_ref
        if action_ref is not None:
            extra_meta["action_ref"] = action_ref
        if lazy_import is not None:
            extra_meta["lazy_import"] = lazy_import

        if path is not None:
            full_path = parent_path.rstrip("/") + "/" + path.lstrip("/") if parent_path else path
            routes.append((full_path, component, extra_meta))
            if children_array is not None:
                _extract_route_objects(children_array, source, full_path, routes)
        elif children_array is not None:  # pragma: no cover - layout route (no path)
            # Layout route (no path) — children inherit parent path
            _extract_route_objects(children_array, source, parent_path, routes)


def _extract_component_from_jsx(
    node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract component name from a JSX element node.

    Given <Users /> or <UserDetail />, returns "Users" or "UserDetail".
    """
    if node.type in ("jsx_self_closing_element", "jsx_element"):
        for child in node.children:
            if child.type == "identifier":
                return _node_text(child, source)
            if child.type == "jsx_opening_element":  # pragma: no cover
                for subchild in child.children:
                    if subchild.type == "identifier":
                        return _node_text(subchild, source)
    # Try nested JSX expression: {<Users />}
    if node.type == "jsx_expression":  # pragma: no cover
        for child in node.children:
            result = _extract_component_from_jsx(child, source)
            if result:
                return result
    return None  # pragma: no cover - non-JSX element nodes


def _find_route_path_in_chain(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Find route path from a .route('/path') call in a chained expression.

    Traverses up the call chain looking for router.route('/path') patterns.
    Used for Express chained routes like: router.route('/').post(handler)

    Args:
        node: A member_expression node (the callee of an HTTP method call)
        source: Source bytes for text extraction

    Returns:
        The route path if found, else None
    """
    # Walk up the member_expression chain looking for .route('/path')
    current = node
    while current is not None:
        # Look for call_expression that might be .route('/path')
        if current.type == "call_expression":
            # Check if this is a .route() call
            for child in current.children:
                if child.type == "member_expression":
                    for subchild in child.children:
                        if subchild.type == "property_identifier":
                            if _node_text(subchild, source).lower() == "route":
                                # Found .route() - extract path from arguments
                                for args_child in current.children:
                                    if args_child.type == "arguments":
                                        for arg in args_child.children:
                                            if arg.type == "string":
                                                return _node_text(arg, source).strip("'\"")
        # Move to parent or nested call in member_expression
        if current.type == "member_expression":
            for child in current.children:
                if child.type == "call_expression":
                    current = child
                    break
            else:
                current = None  # pragma: no cover
        elif current.type == "call_expression":
            for child in current.children:
                if child.type == "member_expression":
                    current = child
                    break
            else:
                current = None  # pragma: no cover
        else:
            current = None  # pragma: no cover
    return None  # pragma: no cover


def _get_receiver_name(member_expr: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract the receiver (object) name from a member_expression.

    For 'app.get()', returns 'app'.
    For 'router.route("/path").get()', returns 'router' (traverses chain).
    For 'fetchMock.get()', returns 'fetchMock'.

    Returns None if the receiver cannot be determined.
    """
    # Get the object part of the member_expression (first child before '.')
    for child in member_expr.children:
        if child.type == "identifier":
            return _node_text(child, source).lower()
        elif child.type == "call_expression":
            # Chained call: router.route('/path').get()
            # Recurse into the call's callee to find the root receiver
            for subchild in child.children:
                if subchild.type == "member_expression":
                    return _get_receiver_name(subchild, source)
        elif child.type == "member_expression":  # pragma: no cover
            # Nested member: express.Router().get()
            return _get_receiver_name(child, source)
    return None


def _detect_route_call(node: "tree_sitter.Node", source: bytes) -> tuple[str | None, str | None]:
    """Detect if a call_expression is an Express-style route registration.

    Returns (http_method, route_path) if this is a route call, else (None, None).

    Supported patterns:
    - app.get('/path', handler)
    - router.post('/path', handler)
    - app.delete('/path', handler)
    - router.route('/path').get(handler)  (chained syntax)
    - router.route('/path').post(handler).get(handler)  (multiple chained)

    The call must be of form <receiver>.<http_method>('/path', ...) where:
    - receiver is in ROUTER_RECEIVER_NAMES (app, router, express, server, fastify, koa)
    - http_method is get, post, put, patch, delete, head, or options

    This prevents false positives from test mocks like fetchMock.get().
    """
    if node.type != "call_expression":  # pragma: no cover
        return None, None

    # Find the callee (member_expression) and arguments
    callee_node = None
    args_node = None
    for child in node.children:
        if child.type == "member_expression":
            callee_node = child
        elif child.type == "arguments":
            args_node = child

    if callee_node is None or args_node is None:
        return None, None

    # Validate the receiver is a known router/app name (ADR-3aaa)
    receiver_name = _get_receiver_name(callee_node, source)
    if receiver_name not in ROUTER_RECEIVER_NAMES:
        return None, None

    # Get the method name from the member_expression
    method_name = None
    for child in callee_node.children:
        if child.type == "property_identifier":
            method_name = _node_text(child, source).lower()
            break

    if method_name not in HTTP_METHODS:
        return None, None

    # Extract the route path from the first argument (should be a string)
    route_path = None
    for child in args_node.children:
        if child.type == "string":
            # Remove quotes
            route_path = _node_text(child, source).strip("'\"")
            break

    # If no path in arguments, check for chained .route('/path') syntax
    if route_path is None:
        route_path = _find_route_path_in_chain(callee_node, source)

    # No string path found — not a route registration. In Express, route
    # handlers always require a path argument. Calls like NestJS
    # app.get(AppService) (DI lookup) have no string path and should not
    # be detected as routes.
    if route_path is None:
        return None, None

    # Return uppercase HTTP method for consistency with other analyzers
    return method_name.upper() if method_name else None, route_path


def _find_route_handler_in_call(
    node: "tree_sitter.Node", source: bytes
) -> tuple["tree_sitter.Node | None", str | None, bool]:
    """Find the handler function in an Express-style route call.

    Looks for function_expression, arrow_function, or external handler references
    (member_expression or identifier) as the last argument.

    Returns (handler_node, handler_name, is_external) where:
    - handler_node: The AST node of the handler
    - handler_name: Name of the handler (for external refs like 'userController.createUser')
    - is_external: True if handler is an external reference, False if inline function
    """
    if node.type != "call_expression":  # pragma: no cover
        return None, None, False

    for child in node.children:
        if child.type == "arguments":
            # Collect all non-comma arguments
            args = [arg for arg in child.children if arg.type not in (",", "(", ")")]
            if not args:  # pragma: no cover
                return None, None, False

            # Check for inline function handlers first (anywhere in args)
            for arg in args:
                # ``generator_function`` covers Koa-v1-style generator handlers
                # (``app.use(function*(){})``) — WI-zavad parity so an inline
                # generator route handler is not silently dropped.
                if arg.type in ("function_expression", "function", "generator_function"):
                    return arg, None, False
                if arg.type == "arrow_function":
                    return arg, None, False

            # If no inline handler, the last argument might be an external handler
            # Pattern: router.post('/path', middleware, userController.createUser)
            last_arg = args[-1]

            # External handler as member expression: userController.createUser
            if last_arg.type == "member_expression":
                handler_name = _node_text(last_arg, source)
                return last_arg, handler_name, True

            # External handler as identifier: createUser
            if last_arg.type == "identifier":
                handler_name = _node_text(last_arg, source)
                return last_arg, handler_name, True

    return None, None, False  # pragma: no cover


def _extract_express_usage_contexts(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
    line_offset: int = 0,
    symbol_by_position: dict[tuple[str, int, int], Symbol] | None = None,
) -> list[UsageContext]:
    """Extract UsageContext records for Express-style route calls.

    Creates UsageContext records that capture how handler functions are used
    in app.get(), router.post(), etc. calls. These are matched against YAML
    patterns in the enrichment phase.

    Args:
        tree: The parsed tree-sitter tree
        source: Source file bytes
        file_path: Path to the source file
        symbol_by_name: Lookup table for symbols by name
        line_offset: Line offset for Svelte/Vue script blocks
        symbol_by_position: Lookup table for symbols by (path, line, col) - enables
            linking inline handlers to their Symbol objects

    Returns:
        List of UsageContext records for Express route patterns.
    """
    contexts: list[UsageContext] = []

    for node in iter_tree(tree.root_node):
        if node.type != "call_expression":
            continue

        http_method, route_path = _detect_route_call(node, source)
        if not http_method:
            continue

        # Find the handler in this route call
        handler_node, handler_name, is_external = _find_route_handler_in_call(node, source)
        if not handler_node:  # pragma: no cover
            continue

        # Try to resolve handler to a symbol reference
        handler_ref = None
        if handler_name and handler_name in symbol_by_name:
            # External handler - look up by name
            handler_ref = symbol_by_name[handler_name].id
        elif handler_node and symbol_by_position:
            # Inline handler - look up by position
            # The Symbol was created at the handler node's position
            handler_line = handler_node.start_point[0] + 1 + line_offset
            handler_col = handler_node.start_point[1]
            position_key = (str(file_path), handler_line, handler_col)
            if position_key in symbol_by_position:
                handler_ref = symbol_by_position[position_key].id

        # Get the receiver name (app, router, express, etc.)
        # _detect_route_call requires a member_expression callee, so one always exists
        receiver_name = None
        for child in node.children:  # pragma: no branch
            if child.type == "member_expression":  # pragma: no branch
                receiver_name = _get_receiver_name(child, source)
                break

        # Build the full call name (e.g., "app.get", "router.post")
        call_name = f"{receiver_name}.{http_method.lower()}" if receiver_name else http_method.lower()

        span = Span(
            start_line=node.start_point[0] + 1 + line_offset,
            end_line=node.end_point[0] + 1 + line_offset,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        )

        # Normalize route path
        normalized_path = route_path if route_path and route_path.startswith("/") else f"/{route_path}" if route_path else "/"

        ctx = UsageContext.create(
            kind="call",
            context_name=call_name,
            position="args[last]",  # Handler is typically last argument
            path=str(file_path),
            span=span,
            symbol_ref=handler_ref,
            metadata={
                "route_path": normalized_path,
                "http_method": http_method,
                "handler_name": handler_name,
                "receiver": receiver_name,
                "is_external_handler": is_external,
            },
        )
        contexts.append(ctx)

    return contexts


def _extract_object_properties(
    node: "tree_sitter.Node", source: bytes
) -> dict[str, str | None]:
    """Extract key-value pairs from a JavaScript object literal.

    Handles:
    - Regular properties: { method: 'GET', path: '/users' }
    - Shorthand properties: { method, path }
    - Function values: { handler: function() {} }

    Returns a dict of property names to their string values (or None for complex values).
    """
    properties: dict[str, str | None] = {}

    if node.type != "object":  # pragma: no cover
        return properties

    for child in node.children:
        if child.type == "pair":
            # Regular property: key: value
            # Key is before the colon, value is after
            key_node = None
            value_node = None
            seen_colon = False
            for pair_child in child.children:
                if pair_child.type == ":":
                    seen_colon = True
                elif not seen_colon:
                    # Before colon: this is the key
                    if pair_child.type in ("property_identifier", "string"):
                        key_node = pair_child
                else:
                    # After colon: this is the value
                    if pair_child.type not in (",", ):
                        value_node = pair_child

            if key_node:
                key = _node_text(key_node, source)
                if key.startswith(("'", '"')):  # pragma: no cover
                    key = key[1:-1]

                # Extract value based on type
                if value_node:
                    if value_node.type == "string":
                        val = _node_text(value_node, source)
                        properties[key] = val[1:-1] if len(val) >= 2 else val
                    elif value_node.type == "identifier":
                        properties[key] = _node_text(value_node, source)
                    elif value_node.type == "member_expression":
                        # Member access like this.getAllStrategies —
                        # extract the property name for handler resolution.
                        prop_id = None
                        for me_child in value_node.children:
                            if me_child.type == "property_identifier":
                                prop_id = _node_text(me_child, source)
                        properties[key] = prop_id or _node_text(value_node, source)
                    elif value_node.type in ("function_expression", "arrow_function"):
                        # For inline functions, record a special marker
                        properties[key] = "<inline_function>"
                    else:  # pragma: no cover
                        properties[key] = None  # Complex value

        elif child.type == "shorthand_property_identifier":
            # Shorthand: { method } -> method: method
            name = _node_text(child, source)
            properties[name] = name

    return properties


def _extract_hapi_usage_contexts(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
    line_offset: int = 0,
) -> list[UsageContext]:
    """Extract UsageContext records for Hapi server.route() calls.

    Hapi uses config objects for routing:
    - server.route({ method: 'GET', path: '/users', handler: getUsersHandler })
    - server.route([{ method: 'GET', path: '/' }, { method: 'POST', path: '/' }])

    Args:
        tree: The parsed tree-sitter tree
        source: Source file bytes
        file_path: Path to the source file
        symbol_by_name: Lookup table for symbols defined in this file
        line_offset: Line offset for embedded script blocks

    Returns:
        List of UsageContext records for Hapi route patterns.
    """
    contexts: list[UsageContext] = []

    for node in iter_tree(tree.root_node):
        if node.type != "call_expression":
            continue

        # Check if this is a server.route() or server.routes() call
        func_node = None
        for child in node.children:
            if child.type == "member_expression":
                func_node = child
                break

        if not func_node:
            continue

        # Check for .route or .routes method
        method_name = None
        receiver_name = None
        for child in func_node.children:
            if child.type == "property_identifier":
                method_name = _node_text(child, source)
            elif child.type == "identifier":
                receiver_name = _node_text(child, source)
            elif child.type == "member_expression":  # pragma: no cover
                receiver_name = _node_text(child, source)

        if method_name not in ("route", "routes"):
            continue

        # Find arguments
        args_node = None
        for child in node.children:
            if child.type == "arguments":
                args_node = child
                break

        if not args_node:  # pragma: no cover
            continue

        # Extract route configs from arguments
        route_configs: list[dict[str, str | None]] = []

        for arg in args_node.children:
            if arg.type == "object":
                # Single route config: { method, path, handler }
                props = _extract_object_properties(arg, source)
                if props.get("path") or props.get("method"):
                    route_configs.append(props)
            elif arg.type == "array":
                # Array of route configs: [{ ... }, { ... }]
                for elem in arg.children:
                    if elem.type == "object":
                        props = _extract_object_properties(elem, source)
                        if props.get("path") or props.get("method"):
                            route_configs.append(props)

        # Create UsageContext for each route config
        for config in route_configs:
            route_path = config.get("path")
            http_method = config.get("method")
            handler_name = config.get("handler")

            # Skip if no useful info
            if not route_path and not http_method:  # pragma: no cover
                continue

            # Try to resolve handler to a symbol reference
            handler_ref = None
            if handler_name and handler_name != "<inline_function>" and handler_name in symbol_by_name:
                handler_ref = symbol_by_name[handler_name].id

            call_name = f"{receiver_name}.{method_name}" if receiver_name else method_name

            span = Span(
                start_line=node.start_point[0] + 1 + line_offset,
                end_line=node.end_point[0] + 1 + line_offset,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            )

            # Normalize route path
            normalized_path = route_path if route_path and route_path.startswith("/") else f"/{route_path}" if route_path else "/"

            ctx = UsageContext.create(
                kind="call",
                context_name=call_name,
                position="args[0]",  # Config object is first argument
                path=str(file_path),
                span=span,
                symbol_ref=handler_ref,
                metadata={
                    "route_path": normalized_path,
                    "http_method": http_method.upper() if http_method else "GET",
                    "handler_name": handler_name if handler_name != "<inline_function>" else None,
                    "receiver": receiver_name,
                    "config_based": True,  # Mark as config-object pattern
                },
            )
            contexts.append(ctx)

    return contexts


def _infer_nextjs_route(file_path: Path) -> str | None:
    """Infer Next.js route from file path.

    Converts file paths to routes:
    - pages/index.js → /
    - pages/about.js → /about
    - pages/api/users.js → /api/users
    - pages/posts/[id].js → /posts/:id
    - pages/posts/[...slug].js → /posts/*
    - app/page.tsx → /
    - app/about/page.tsx → /about
    - app/api/users/route.ts → /api/users

    Returns None if file is not a Next.js page/route.
    """
    parts = file_path.parts

    # Find pages/ or app/ directory
    page_index = None
    route_type = None
    for i, part in enumerate(parts):
        if part == "pages":
            page_index = i
            route_type = "pages"
            break
        elif part == "app":
            page_index = i
            route_type = "app"
            break

    if page_index is None:
        return None

    # Get the path parts after pages/ or app/
    route_parts = list(parts[page_index + 1:])
    if not route_parts:  # pragma: no cover
        return None

    # Get filename without extension
    filename = route_parts[-1]
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename

    # Handle App Router conventions
    if route_type == "app":
        # Only page.tsx, route.ts, etc. are valid routes
        if stem not in ("page", "route", "loading", "error", "layout"):  # pragma: no cover
            return None
        # Remove the special filename from route
        route_parts = route_parts[:-1]
    else:
        # Pages Router: replace filename stem
        route_parts[-1] = stem

    # Build the route path
    route_segments = []
    for part in route_parts:
        if part == "index":
            continue  # index.js → /
        elif part.startswith("[...") and part.endswith("]"):
            # Catch-all route: [...slug] → *
            route_segments.append("*")
        elif part.startswith("[") and part.endswith("]"):
            # Dynamic route: [id] → :id
            param = part[1:-1]
            route_segments.append(f":{param}")
        else:
            route_segments.append(part)

    route = "/" + "/".join(route_segments) if route_segments else "/"
    return route


def _extract_nextjs_usage_contexts(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
    line_offset: int = 0,
) -> list[UsageContext]:
    """Extract UsageContext records for Next.js file-based routing.

    Detects:
    - Files in pages/ or app/ directories
    - Default exports (page components)
    - Named exports (getServerSideProps, getStaticProps, etc.)

    Returns a list of UsageContext records for YAML pattern matching.
    """
    contexts: list[UsageContext] = []

    # Check if this file is a Next.js page
    route_path = _infer_nextjs_route(file_path)
    if not route_path:
        return contexts

    # Determine if this is an API route
    is_api_route = "/api/" in route_path or route_path.startswith("/api")

    # Check if this is an App Router route.ts file
    filename = file_path.name
    is_route_file = filename.startswith("route.")  # route.ts, route.js

    # App Router HTTP method handlers (exported from route.ts files)
    HTTP_HANDLERS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}

    # Look for exports
    for node in iter_tree(tree.root_node):
        if node.type != "export_statement":
            continue

        # Check for export default
        is_default = False
        export_name = None

        for child in node.children:
            if child.type == "default":
                is_default = True
            elif child.type in ("function_declaration", "generator_function_declaration"):
                name = _find_name_in_children(child, source)
                if name:  # pragma: no branch — function_declaration always has a name
                    export_name = name
            elif child.type == "identifier":  # pragma: no cover
                export_name = _node_text(child, source)
            elif child.type == "export_clause":  # pragma: no cover
                # Named exports: export { getServerSideProps }
                for ec_child in child.children:
                    if ec_child.type == "export_specifier":
                        for spec_child in ec_child.children:
                            if spec_child.type == "identifier":
                                export_name = _node_text(spec_child, source)
                                break

        # Meaningful exports for Next.js
        meaningful_exports = {"getServerSideProps", "getStaticProps", "getStaticPaths",
                              "generateStaticParams", "generateMetadata"}

        # For route.ts files, also include HTTP method handlers
        if is_route_file:
            meaningful_exports.update(HTTP_HANDLERS)

        # Create UsageContext for meaningful exports
        if is_default or export_name in meaningful_exports:
            span = Span(
                start_line=node.start_point[0] + 1 + line_offset,
                end_line=node.end_point[0] + 1 + line_offset,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            )

            # Resolve symbol reference
            handler_ref = None
            if export_name and export_name in symbol_by_name:
                handler_ref = symbol_by_name[export_name].id

            context_name = "export.default" if is_default else f"export.{export_name}"
            concept_type = "api_route" if is_api_route else "page"

            ctx = UsageContext.create(
                kind="export",
                context_name=context_name,
                position="file",  # File-based pattern
                path=str(file_path),
                span=span,
                symbol_ref=handler_ref,
                metadata={
                    "route_path": route_path,
                    "http_method": "GET" if not is_api_route else "ANY",
                    "export_name": export_name,
                    "is_default": is_default,
                    "is_api_route": is_api_route,
                    "concept": concept_type,
                },
            )
            contexts.append(ctx)

    return contexts


def _is_index_file(file_path: Path) -> bool:
    """Check if a file is an index file (library entry point).

    Index files are the entry points for libraries, defining the public API.
    Supports various extensions used in JavaScript/TypeScript projects.
    """
    stem = file_path.stem  # filename without extension
    return stem == "index"


def _extract_library_export_contexts(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
    line_offset: int = 0,
) -> list[UsageContext]:
    """Extract UsageContext records for library exports from index files.

    Libraries (as opposed to applications) expose their public API through
    exports from index files (index.ts, index.js, etc.). These exports are
    entry points for library consumers.

    Detects:
    - export default X
    - export function name() {}
    - export class Name {}
    - export const name = ...
    - export { name1, name2 }
    - export { name as alias }

    Note: Re-exports (export * from './module') are not currently detected
    as they require import resolution to determine the exported symbols.

    Returns a list of UsageContext records for YAML pattern matching.
    """
    contexts: list[UsageContext] = []

    # Only process index files
    if not _is_index_file(file_path):
        return contexts

    # Look for exports
    for node in iter_tree(tree.root_node):
        if node.type != "export_statement":
            continue

        # Check for export default
        is_default = False
        export_names: list[str] = []

        for child in node.children:
            if child.type == "default":
                is_default = True
            elif child.type in ("function_declaration", "generator_function_declaration"):
                # WI-zavad: ``export function* g(){}`` is a public library export
                # like ``export function f(){}`` — surface its UsageContext too.
                name = _find_name_in_children(child, source)
                if name:
                    export_names.append(name)
            elif child.type in ("class_declaration", "abstract_class_declaration"):
                name = _find_name_in_children(child, source)
                if name:
                    export_names.append(name)
            elif child.type == "lexical_declaration":
                # export const x = ..., export let y = ...
                for decl in child.children:
                    if decl.type == "variable_declarator":
                        for dc in decl.children:
                            if dc.type == "identifier":
                                export_names.append(_node_text(dc, source))
                                break
            elif child.type == "identifier":
                # export default SomeIdentifier
                export_names.append(_node_text(child, source))
            elif child.type == "export_clause":
                # Named exports: export { name1, name2, name3 as alias }
                for ec_child in child.children:
                    if ec_child.type == "export_specifier":
                        # Get the local name (first identifier) for symbol lookup
                        # and the exported name (second identifier or alias)
                        local_name = None
                        for spec_child in ec_child.children:
                            if spec_child.type == "identifier":
                                if local_name is None:
                                    local_name = _node_text(spec_child, source)
                                # If there's an alias, we still use local name for lookup
                        if local_name:
                            export_names.append(local_name)

        # Create span for the export statement
        span = Span(
            start_line=node.start_point[0] + 1 + line_offset,
            end_line=node.end_point[0] + 1 + line_offset,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        )

        if is_default:
            # Default export - may or may not have a name
            export_name = export_names[0] if export_names else None
            handler_ref = None
            if export_name and export_name in symbol_by_name:
                handler_ref = symbol_by_name[export_name].id

            ctx = UsageContext.create(
                kind="library_export",
                context_name="export.default",
                position="default",
                path=str(file_path),
                span=span,
                symbol_ref=handler_ref,
                metadata={
                    "export_name": export_name,
                    "is_default": True,
                },
            )
            contexts.append(ctx)
        else:
            # Named exports - create a context for each export
            for export_name in export_names:
                handler_ref = None
                if export_name in symbol_by_name:
                    handler_ref = symbol_by_name[export_name].id

                ctx = UsageContext.create(
                    kind="library_export",
                    context_name=f"export.{export_name}",
                    position="named",
                    path=str(file_path),
                    span=span,
                    symbol_ref=handler_ref,
                    metadata={
                        "export_name": export_name,
                        "is_default": False,
                    },
                )
                contexts.append(ctx)

    return contexts


# App bootstrap function names that indicate application initialization.
# These are module-level calls (not decorators) that mount a component tree
# or initialize an application framework.
_APP_BOOTSTRAP_NAMES: frozenset[str] = frozenset({
    # React 18+
    "createRoot",
    "hydrateRoot",
    # React 17 and earlier (qualified: ReactDOM.render)
    "render",
    "hydrate",
    # React Router v6 app-level router creation
    "createBrowserRouter",
    "createHashRouter",
    "createMemoryRouter",
})

# Qualified forms where the callee is a member expression (e.g., ReactDOM.render).
# Maps receiver.method -> context_name.
_APP_BOOTSTRAP_QUALIFIED: dict[str, str] = {
    "ReactDOM.render": "ReactDOM.render",
    "ReactDOM.hydrate": "ReactDOM.hydrate",
    "ReactDOM.createRoot": "ReactDOM.createRoot",
    "ReactDOM.hydrateRoot": "ReactDOM.hydrateRoot",
    # Electron app lifecycle
    "app.whenReady": "app.whenReady",
    "app.on": "app.on",
    "app.once": "app.once",
}


def _extract_app_bootstrap_contexts(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    module_symbol: Symbol,
    line_offset: int = 0,
) -> list[UsageContext]:
    """Extract UsageContext records for app bootstrap and lifecycle calls.

    Detects module-level calls to functions like createRoot(), ReactDOM.render(),
    hydrateRoot(), createBrowserRouter() (React SPA), and app.whenReady(),
    app.on('ready') (Electron). These initialize the application and serve
    as entry points.

    The UsageContext has symbol_ref pointing to the module symbol, so that
    framework YAML usage patterns can assign concepts (app_bootstrap, entrypoint)
    to the module, enabling entrypoint detection in entrypoints.py.

    Args:
        tree: The parsed tree-sitter tree
        source: Source file bytes
        file_path: Path to the source file
        module_symbol: The module symbol for this file
        line_offset: Line offset for Svelte/Vue script blocks

    Returns:
        List of UsageContext records for SPA bootstrap calls.
    """
    contexts: list[UsageContext] = []
    seen_names: set[str] = set()  # Deduplicate (e.g., two createRoot calls)

    for node in iter_tree(tree.root_node):
        if node.type != "call_expression":
            continue

        # Get the callee node (first child of call_expression)
        callee = node.children[0] if node.children else None
        if callee is None:
            continue  # pragma: no cover

        context_name: str | None = None

        if callee.type == "identifier":
            # Bare call: createRoot(...), hydrateRoot(...)
            name = _node_text(callee, source)
            if name in _APP_BOOTSTRAP_NAMES:
                context_name = name
        elif callee.type == "member_expression":
            # Qualified call: ReactDOM.render(...), ReactDOM.createRoot(...)
            qualified = _node_text(callee, source)
            if qualified in _APP_BOOTSTRAP_QUALIFIED:
                context_name = _APP_BOOTSTRAP_QUALIFIED[qualified]
            else:
                # Check if the method name alone matches (e.g., someAlias.createRoot)
                for child in callee.children:
                    if child.type == "property_identifier":
                        method_name = _node_text(child, source)
                        if method_name in _APP_BOOTSTRAP_NAMES:
                            context_name = f"{_node_text(callee, source)}"
                        break

        if context_name is None or context_name in seen_names:
            continue

        seen_names.add(context_name)

        span = Span(
            start_line=node.start_point[0] + 1 + line_offset,
            end_line=node.end_point[0] + 1 + line_offset,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        )

        ctx = UsageContext.create(
            kind="call",
            context_name=context_name,
            position="caller",  # The module is the caller, not a handler
            path=str(file_path),
            span=span,
            symbol_ref=module_symbol.id,
            metadata={
                "bootstrap_function": context_name,
            },
        )
        contexts.append(ctx)

    return contexts


# HTTP / GraphQL server handler function names. These are module-level
# calls that start an HTTP listener or process an HTTP/GraphQL request.
# The framework_patterns YAML in graphql.yaml and node-http.yaml target
# these names via ``usage: kind: "^call$"`` (WI-tisam).
#
# INV-rolul scope: this set closes the WI-tisam end-to-end gap. Other
# JS/TS framework YAMLs whose kind=call patterns are still not reachable
# from the analyzer's UC-emission pipeline (adonisjs, mcp, restify,
# fastify.route, web_audio.*) remain documented as INV-rolul follow-ups.
_HTTP_HANDLER_NAMES: frozenset[str] = frozenset({
    # Apollo Server v4 standalone HTTP listener
    "startStandaloneServer",
    # Apollo Server v3 / v4 underlying HTTP-request entrypoints
    "runHttpQuery",
    "executeHTTPGraphQLRequest",
    # Node.js HTTP server module (destructured-import shape:
    # ``import { createServer } from 'http'`` then bare ``createServer(...)``)
    "createServer",
})

# Qualified forms for Node stdlib HTTP servers. Maps ``receiver.method`` →
# context_name; the receiver may be ``http`` / ``https`` / ``http2`` (the
# Node modules) or ``Http`` (TypeScript-typed import alias).
_HTTP_HANDLER_QUALIFIED: dict[str, str] = {
    "http.createServer": "http.createServer",
    "https.createServer": "https.createServer",
    "http2.createServer": "http2.createServer",
    "Http.createServer": "Http.createServer",
}


def _extract_http_handler_contexts(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    module_symbol: Symbol,
    line_offset: int = 0,
) -> list[UsageContext]:
    """Extract UsageContext records for HTTP/GraphQL server-handler calls.

    Detects module-level calls to functions like ``startStandaloneServer``
    (Apollo v4), ``runHttpQuery`` / ``executeHTTPGraphQLRequest`` (Apollo
    v3/v4), the Node stdlib ``http.createServer`` family, and bare
    ``createServer`` (destructured-import shape). Each emits a
    ``kind="call"`` UsageContext whose ``context_name`` matches the
    source-text callee form. ``graphql.yaml`` and ``node-http.yaml`` then
    route these UCs through ``match_usage_patterns`` to assign route
    concepts to the calling module.

    Closes the WI-tisam end-to-end gap (INV-rolul scope).
    """
    contexts: list[UsageContext] = []
    seen_names: set[str] = set()  # dedupe repeated calls in one file

    for node in iter_tree(tree.root_node):
        if node.type != "call_expression":
            continue

        callee = node.children[0] if node.children else None
        if callee is None:
            continue  # pragma: no cover

        context_name: str | None = None

        if callee.type == "identifier":
            name = _node_text(callee, source)
            if name in _HTTP_HANDLER_NAMES:
                context_name = name
        elif callee.type == "member_expression":
            qualified = _node_text(callee, source)
            if qualified in _HTTP_HANDLER_QUALIFIED:
                context_name = _HTTP_HANDLER_QUALIFIED[qualified]

        if context_name is None or context_name in seen_names:
            continue

        seen_names.add(context_name)

        span = Span(
            start_line=node.start_point[0] + 1 + line_offset,
            end_line=node.end_point[0] + 1 + line_offset,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        )

        ctx = UsageContext.create(
            kind="call",
            context_name=context_name,
            position="caller",
            path=str(file_path),
            span=span,
            symbol_ref=module_symbol.id,
            metadata={
                "http_handler_function": context_name,
            },
        )
        contexts.append(ctx)

    return contexts


def _resolve_base_class_js(
    base_name: str,
    child_sym: Symbol,
    candidates_by_name: dict[str, list[Symbol]],
    parsed_files: list["_ParsedFile"],
) -> Symbol | None:
    """Resolve a base class/interface name to a specific Symbol, disambiguating collisions.

    When multiple classes or interfaces share the same name (e.g., NestJS monorepo
    with multiple CatsService, or test stubs named 'Model'), uses a priority cascade:

    1. Same-file match: base class defined in the same file as the child
    2. Import-path match: child's file has ``import { Name } from './path'`` matching
       a candidate's file path (via ``_disambiguate_by_import``)
    3. First by ID: deterministic fallback (sorted by symbol ID)

    Args:
        base_name: The base class/interface name to resolve (e.g., 'Model')
        child_sym: The child class symbol (for file context)
        candidates_by_name: Multi-value lookup: name -> list of Symbol candidates
        parsed_files: All parsed files (for named_imports lookup)

    Returns:
        The resolved base class/interface Symbol, or None if no match found.
    """
    candidates = candidates_by_name.get(base_name)
    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0]

    child_path = child_sym.path or ""

    # 1. Same-file match: prefer base defined in the same file
    same_file = [c for c in candidates if c.path == child_path]
    if len(same_file) == 1:
        return same_file[0]

    # 2. Import-path match: check if child's file imports resolve to a candidate
    # Find the parsed file for the child symbol to get its named_imports
    child_file_path = Path(child_path) if child_path else None
    if child_file_path is not None:
        for pf in parsed_files:
            if pf.path == child_file_path:
                named_imports = pf.named_imports or {}
                if base_name in named_imports:
                    import_path = named_imports[base_name]
                    # Build a symbols_by_name with just our candidates for disambiguation
                    cand_by_name: dict[str, list[Symbol]] = {base_name: candidates}
                    match = _disambiguate_by_import(
                        import_path, pf.path, base_name, cand_by_name
                    )
                    if match is not None:
                        return match
                break

    # 3. Deterministic fallback: first by symbol ID (sorted for stability)
    candidates_sorted = sorted(candidates, key=lambda c: c.id)
    return candidates_sorted[0]


def _extract_inheritance_edges(
    symbols: list[Symbol],
    classes_by_name: dict[str, list[Symbol]],
    parsed_files: list["_ParsedFile"],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract extends/implements edges from class inheritance.

    For each class with base_classes metadata, creates extends/implements edges
    to base classes/interfaces that exist in the analyzed codebase. This enables
    the type hierarchy linker to create dispatches_to edges for polymorphic dispatch.

    When multiple classes or interfaces share the same name (common in monorepos
    and repos with test stubs), uses import-aware disambiguation via
    ``_resolve_base_class_js()`` to find the correct target.

    Args:
        symbols: All extracted symbols
        classes_by_name: Multi-value lookup: class name -> list of Symbol candidates
        parsed_files: All parsed files (for named_imports lookup during disambiguation)
        run: Current analysis run for provenance

    Returns:
        List of extends/implements edges for inheritance relationships
    """
    edges: list[Edge] = []

    # Build multi-value interface lookup
    interfaces_by_name: dict[str, list[Symbol]] = {}
    for sym in symbols:
        if sym.kind == "interface":
            if sym.name not in interfaces_by_name:
                interfaces_by_name[sym.name] = []
            interfaces_by_name[sym.name].append(sym)

    # F4/A2 inputs for the unresolved-external fallback:
    # (1) per-(file, class) ``implements``-clause base names — gives an unresolved
    #     external base the correct edge type (``implements`` vs ``extends``);
    # (2) per-file import maps — give the external base a real module hint
    #     (``LitElement`` -> ``lit``) and let an *aliased* import be re-resolved
    #     to its canonical name before being declared external.
    implements_by_class: dict[tuple[str, str], set[str]] = {}
    named_imports_by_path: dict[str, dict[str, str]] = {}
    namespace_imports_by_path: dict[str, dict[str, str]] = {}
    named_import_originals_by_path: dict[str, dict[str, str]] = {}
    for pf in parsed_files:
        pf_path = str(pf.path)
        named_imports_by_path[pf_path] = pf.named_imports or {}
        namespace_imports_by_path[pf_path] = pf.namespace_imports or {}
        named_import_originals_by_path[pf_path] = pf.named_import_originals or {}
        for node in iter_tree(pf.tree.root_node):
            if node.type in ("class_declaration", "abstract_class_declaration"):
                cname = _find_name_in_children(node, pf.source)
                if not cname:  # pragma: no cover - class_declaration always names
                    continue
                impls = _extract_implements_names(node, pf.source)
                if impls:
                    # normalise to the same form the resolution loop uses
                    implements_by_class.setdefault((pf_path, cname), set()).update(
                        n.split("<")[0].split(".")[-1] for n in impls
                    )

    for sym in symbols:
        if sym.kind != "class":
            continue

        base_classes = sym.meta.get("base_classes", []) if sym.meta else []
        if not base_classes:
            continue

        sym_path = sym.path or ""
        for base_class_name in base_classes:
            # Strip generics from base class name (e.g., "Repository<User>" -> "Repository")
            de_generic = base_class_name.split("<")[0]
            # Qualified base (``React.Component``): keep the qualifier (``React``)
            # to recover its namespace-import module; resolve on the member name.
            qualifier = de_generic.split(".")[0] if "." in de_generic else None
            base_name = de_generic.split(".")[-1] if "." in de_generic else de_generic

            # Resolve against project classes and interfaces (disambiguated).
            base_sym = _resolve_base_class_js(
                base_name, sym, classes_by_name, parsed_files
            )
            iface_sym = _resolve_base_class_js(
                base_name, sym, interfaces_by_name, parsed_files
            )
            if base_sym is not None and base_sym.id != sym.id:
                edges.append(Edge.create(
                    src=sym.id,
                    dst=base_sym.id,
                    edge_type="extends",
                    line=sym.span.start_line if sym.span else 0,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_extends",
                ))
            elif iface_sym is not None and iface_sym.id != sym.id:
                edges.append(Edge.create(
                    src=sym.id,
                    dst=iface_sym.id,
                    edge_type="implements",
                    line=sym.span.start_line if sym.span else 0,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_implements",
                ))
            elif base_sym is None and iface_sym is None:
                # F4/A2: the base resolves to neither a project class nor a
                # project interface. Before declaring it external, re-resolve an
                # *aliased* import (``import { Base as B }``) on its canonical
                # name — otherwise a local class imported under an alias would be
                # mislabeled as an external base.
                canonical = named_import_originals_by_path.get(
                    sym_path, {}
                ).get(base_name, base_name)
                if canonical != base_name:
                    rb = _resolve_base_class_js(
                        canonical, sym, classes_by_name, parsed_files
                    )
                    ri = _resolve_base_class_js(
                        canonical, sym, interfaces_by_name, parsed_files
                    )
                    if rb is not None and rb.id != sym.id:
                        edges.append(Edge.create(
                            src=sym.id, dst=rb.id, edge_type="extends",
                            line=sym.span.start_line if sym.span else 0,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="ast_extends",
                        ))
                        continue
                    if ri is not None and ri.id != sym.id:
                        edges.append(Edge.create(
                            src=sym.id, dst=ri.id, edge_type="implements",
                            line=sym.span.start_line if sym.span else 0,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="ast_implements",
                        ))
                        continue

                # Module hint: named import of the member, else a default/
                # namespace import of the member, else the namespace qualifier
                # (``React`` in ``React.Component``).
                ni = named_imports_by_path.get(sym_path, {})
                nsi = namespace_imports_by_path.get(sym_path, {})
                raw_module = (
                    ni.get(base_name)
                    or nsi.get(base_name)
                    or (nsi.get(qualifier) if qualifier else None)
                )
                # A base imported from a RELATIVE path is intra-repo (a local
                # file whose symbol was not extracted), NOT a library — drop it
                # rather than mislabel it as external.
                if raw_module and (
                    raw_module.startswith("./") or raw_module.startswith("../")
                ):
                    continue

                is_impl = base_name in implements_by_class.get(
                    (sym_path, sym.name), set()
                )
                edge_type = "implements" if is_impl else "extends"
                lang = sym.language
                if raw_module:
                    module_hint = _normalize_import_module_hint(raw_module)
                    dst_ref: ExternalRef | None = ExternalRef(
                        lang=lang, module_path=module_hint, name=canonical,
                    )
                else:
                    module_hint = "external"
                    dst_ref = None
                edges.append(Edge.create(
                    src=sym.id,
                    dst=f"{lang}:{module_hint}:0-0:{canonical}:unresolved",
                    edge_type=edge_type,
                    line=sym.span.start_line if sym.span else 0,
                    confidence=0.5,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type=f"ast_{edge_type}",
                    is_resolved=False,
                    dst_ref=dst_ref,
                ))

    return edges


# TypeScript built-in types that should not generate type_ref edges.
_TS_BUILTIN_TYPES = frozenset({
    "string", "number", "boolean", "void", "null", "undefined",
    "never", "any", "unknown", "object", "symbol", "bigint",
    "String", "Number", "Boolean", "Object", "Symbol", "BigInt",
    "Array", "Promise", "Map", "Set", "Record", "Partial", "Readonly",
    "Pick", "Omit", "Exclude", "Extract", "Required", "NonNullable",
    "ReturnType", "Parameters", "ConstructorParameters", "InstanceType",
    "ThisType", "Awaited", "Uppercase", "Lowercase", "Capitalize",
    "Uncapitalize",
})


def _collect_type_identifiers(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Recursively collect type_identifier names from an AST subtree.

    Walks all descendants looking for type_identifier nodes (user-defined type
    references in TypeScript type expressions). Excludes built-in types like
    string, number, boolean, etc.
    """
    names: list[str] = []
    if node.type == "type_identifier":
        name = _node_text(node, source)
        if name and name not in _TS_BUILTIN_TYPES:
            names.append(name)
    for child in node.children:
        names.extend(_collect_type_identifiers(child, source))
    return names


def _extract_type_reference_edges(
    symbols: list[Symbol],
    parsed_files: list["_ParsedFile"],
    run: "AnalysisRun",
) -> list[Edge]:
    """Extract type_ref edges from TypeScript type alias bodies and interface signatures.

    For each type alias declaration (``type Foo = Bar & Baz``), creates type_ref
    edges from the Foo symbol to Bar and Baz symbols. For each interface declaration,
    creates type_ref edges from the interface to user-defined types referenced in
    method signatures and property types.

    This enables refactoring blast radius analysis: changing type User affects all
    type aliases and interfaces that reference it.

    Built-in types (string, number, boolean, etc.) are excluded.
    """
    edges: list[Edge] = []

    # Build lookup: (file_path, type_name) -> Symbol for resolution
    types_by_name: dict[str, list[Symbol]] = {}
    for sym in symbols:
        if sym.kind in ("type", "interface", "class", "enum"):
            if sym.name not in types_by_name:
                types_by_name[sym.name] = []
            types_by_name[sym.name].append(sym)

    # Also build symbol lookup by (path, name) for disambiguation
    symbols_by_path_name: dict[tuple[str, str], Symbol] = {}
    for sym in symbols:
        if sym.kind in ("type", "interface", "class", "enum"):
            symbols_by_path_name[(sym.path, sym.name)] = sym

    for pf in parsed_files:
        file_path_str = str(pf.path)

        for node in pf.tree.root_node.children:
            if node.type == "export_statement":
                # Unwrap: export type Foo = ...
                for child in node.children:
                    if child.type in ("type_alias_declaration", "interface_declaration"):
                        node = child
                        break

            if node.type == "type_alias_declaration":
                # Find the declaration name and the type body
                decl_name = None
                body_node = None
                for child in node.children:
                    if child.type == "type_identifier" and decl_name is None:
                        decl_name = _node_text(child, pf.source)
                    elif child.type not in ("type", "=", ";", "type_identifier",
                                             "type_parameters"):
                        # This is the type body (after the =)
                        body_node = child

                if decl_name and body_node:
                    # Find the source symbol
                    src_sym = symbols_by_path_name.get((file_path_str, decl_name))
                    if src_sym is None:  # pragma: no cover - defensive
                        continue

                    ref_names = _collect_type_identifiers(body_node, pf.source)
                    seen: set[str] = set()
                    for ref_name in ref_names:
                        if ref_name == decl_name or ref_name in seen:
                            continue
                        seen.add(ref_name)

                        # Resolve to target symbol — prefer same file
                        candidates = types_by_name.get(ref_name, [])
                        if not candidates:
                            continue
                        dst_sym = (
                            symbols_by_path_name.get((file_path_str, ref_name))
                            or candidates[0]
                        )
                        edges.append(Edge.create(
                            src=src_sym.id,
                            dst=dst_sym.id,
                            edge_type="references",
                            line=src_sym.span.start_line if src_sym.span else 0,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="ast_type_ref",
                        ))

            elif node.type == "interface_declaration":
                # Find the interface name and body
                decl_name = None
                body_node = None
                for child in node.children:
                    if child.type == "type_identifier" and decl_name is None:
                        decl_name = _node_text(child, pf.source)
                    elif child.type == "interface_body":
                        body_node = child

                if decl_name and body_node:
                    src_sym = symbols_by_path_name.get((file_path_str, decl_name))
                    if src_sym is None:  # pragma: no cover - defensive
                        continue

                    ref_names = _collect_type_identifiers(body_node, pf.source)
                    seen = set()
                    for ref_name in ref_names:
                        if ref_name == decl_name or ref_name in seen:
                            continue
                        seen.add(ref_name)

                        candidates = types_by_name.get(ref_name, [])
                        if not candidates:
                            continue
                        dst_sym = (
                            symbols_by_path_name.get((file_path_str, ref_name))
                            or candidates[0]
                        )
                        edges.append(Edge.create(
                            src=src_sym.id,
                            dst=dst_sym.id,
                            edge_type="references",
                            line=src_sym.span.start_line if src_sym.span else 0,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="ast_type_ref",
                        ))

    return edges


def _extract_decorator_edges(
    symbols: list[Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract decorated_by edges from decorator metadata.

    For each symbol (class, method, function) with decorators metadata,
    creates decorated_by edges to decorator functions that exist in the
    analyzed codebase. This enables visibility of decorator patterns
    like NestJS @Controller, @Injectable, @Get, etc.

    Args:
        symbols: All extracted symbols
        global_symbols: Map of name -> Symbol for decorator lookup
        run: Current analysis run for provenance

    Returns:
        List of decorated_by edges for decorator relationships
    """
    edges: list[Edge] = []

    for sym in symbols:
        if sym.meta is None:
            continue

        decorators = sym.meta.get("decorators")
        if not decorators or not isinstance(decorators, list):
            continue

        for decorator in decorators:
            if not isinstance(decorator, dict):  # pragma: no cover
                continue

            dec_name = decorator.get("name")
            if not dec_name or not isinstance(dec_name, str):  # pragma: no cover
                continue

            # Try to resolve the decorator to a symbol.
            # Only accept function-like symbols as decorator targets — a class,
            # interface, or type named "Post" is not the @Post() decorator.
            # This prevents name collision false positives (e.g., NestJS @Post()
            # resolving to a GraphQL Post data class).
            # ADR-0027 Phase-2 audit (WI-jukav): all members are
            # AXIS_LANGUAGE_CONSTRUCT (Cluster A) and stable across
            # Phase 3. JS/TS decorators only resolve to callable
            # source-language constructs. Forward-compatible.
            _DECORATOR_KINDS = {"function", "method", "arrow_function"}
            decorator_sym = global_symbols.get(dec_name)
            if decorator_sym and decorator_sym.kind not in _DECORATOR_KINDS:
                decorator_sym = None  # Wrong kind — leave as unresolved

            line = sym.span.start_line if sym.span else 0

            if decorator_sym:
                edge = Edge.create(
                    src=sym.id,
                    dst=decorator_sym.id,
                    edge_type="decorated_by",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_decorator",
                )
                edges.append(edge)
            else:
                # Emit unresolved edge for decorators we can't resolve
                # This helps track framework decorators like @Injectable
                dst_id = f"typescript:unresolved:0-0:{dec_name}:unresolved"
                edge = Edge.create(
                    src=sym.id,
                    dst=dst_id,
                    edge_type="decorated_by",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_decorator",
                    is_resolved=False,
                )
                edges.append(edge)

    return edges


def _detect_nestjs_decorator(
    node: "tree_sitter.Node", source: bytes
) -> tuple[str | None, str | None]:
    """Detect NestJS HTTP method decorators on a method.

    Returns (http_method, route_path) if a NestJS route decorator is found.

    Supported patterns:
    - @Get(), @Get(':id')
    - @Post(), @Post('/create')
    - @Put(), @Patch(), @Delete(), @Head(), @Options()

    Decorators appear as siblings to the method_definition in the class body.
    """
    # NestJS decorators are typically in a decorator node before the method
    # In tree-sitter, we need to look at previous siblings
    parent = node.parent
    if parent is None:  # pragma: no cover
        return None, None

    # Find the index of this node in parent's children
    idx = None
    for i, child in enumerate(parent.children):
        if child == node:
            idx = i
            break

    if idx is None or idx == 0:
        return None, None

    # Look at previous sibling(s) for decorator
    for i in range(idx - 1, -1, -1):
        sibling = parent.children[i]
        if sibling.type == "decorator":
            # Get the decorator content
            for child in sibling.children:
                # @Get() -> call_expression
                if child.type == "call_expression":
                    # Get the function name
                    for grandchild in child.children:
                        if grandchild.type == "identifier":
                            name = _node_text(grandchild, source).lower()
                            if name in HTTP_METHODS:
                                # Extract route path from first argument if present
                                route_path = None
                                for args_child in child.children:
                                    if args_child.type == "arguments":
                                        for arg in args_child.children:
                                            if arg.type == "string":
                                                route_path = _node_text(arg, source).strip("'\"")
                                                break
                                # Return uppercase HTTP method for consistency
                                return name.upper(), route_path
                # @Get without () -> just identifier (rare in NestJS)
                elif child.type == "identifier":  # pragma: no cover
                    name = _node_text(child, source).lower()
                    if name in HTTP_METHODS:
                        return name.upper(), None
        # Stop if we hit another method or non-decorator
        elif sibling.type in ("method_definition", "public_field_definition"):
            break

    return None, None


def _find_name_in_children(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Find identifier name in node's children."""
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
        if child.type == "property_identifier":
            return _node_text(child, source)
        # TypeScript uses type_identifier for class names
        if child.type == "type_identifier":
            return _node_text(child, source)
    return None


def _get_class_context(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing class name.

    Returns the class name if inside a class, or None if not.
    Used to build qualified method names without recursion.
    """
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "abstract_class_declaration"):
            name = _find_name_in_children(current, source)
            if name:
                return name
        current = current.parent
    return None


def _get_jsts_class_ancestors(
    node: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Walk up the tree collecting all enclosing class names.

    Returns the chain from outermost to innermost (excluding the current
    node itself). Used to build qualified names for nested classes/methods.
    """
    chain: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "abstract_class_declaration"):
            name = _find_name_in_children(current, source)
            if name:
                chain.append(name)
        current = current.parent
    return list(reversed(chain))


def _jsts_enclosing_class(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """INV-fahub: the innermost enclosing class name of a call site, or None.

    Used by the bare-/untyped-receiver -> method magnet gate
    (``defer_bare_method_call``) to decide whether a weak short-name resolver
    hit is an implicit-``this`` call to the call site's OWN class (bind) or a
    cross-class magnet (withhold + stamp ``enclosing_class`` for Site-1).
    ``_get_jsts_class_ancestors`` returns outermost -> innermost, so the
    innermost enclosing class (the implicit-``this`` owner) is the last entry —
    the same short name a ``method`` Symbol carries as its ``Owner.method``
    prefix (``full_name = f"{_get_class_context(...)}.{name}"``).
    """
    ancestors = _get_jsts_class_ancestors(node, source)
    return ancestors[-1] if ancestors else None


def _make_jsts_qualified_name(
    ancestors: list[str], name: str, lang: str
) -> str:
    """Build a JS/TS qualified name: ``Class1.Class2.symbol_name``.

    JS/TS has no source-level package concept (modules are file-scoped),
    so qualified_name comprises only the class-ancestor chain plus the
    symbol name. The ``lang`` argument selects the separator (always ``.``
    for both javascript and typescript, but passed through the catalog
    for consistency with the other analyzers).
    """
    sep = separator_for_language(lang)  # "." for both ts and js
    parts: list[str] = list(ancestors)
    parts.append(name)
    return sep.join(parts)


def _ts_value_to_python(node: "tree_sitter.Node", source: bytes) -> str | int | float | bool | list | None:
    """Convert a tree-sitter AST node to a Python value representation.

    Handles strings, numbers, booleans, arrays, and identifiers.
    Returns the value or a string representation for identifiers.
    """
    if node.type == "string":
        # Strip quotes from string literals
        text = _node_text(node, source)
        # Handle both single and double quotes
        if len(text) >= 2:
            if (text[0] == '"' and text[-1] == '"') or (text[0] == "'" and text[-1] == "'"):
                return text[1:-1]
        return text  # pragma: no cover
    elif node.type == "template_string":
        # Template string (backtick): extract content without quotes
        text = _node_text(node, source)
        if len(text) >= 2 and text[0] == '`' and text[-1] == '`':
            return text[1:-1]
        return text  # pragma: no cover
    elif node.type == "number":
        text = _node_text(node, source)
        try:
            if '.' in text:
                return float(text)
            return int(text)
        except ValueError:  # pragma: no cover
            return text
    elif node.type in ("true", "false"):
        return node.type == "true"
    elif node.type == "array":
        result = []
        for child in node.children:
            if child.type not in ("[", "]", ","):
                result.append(_ts_value_to_python(child, source))
        return result
    elif node.type == "identifier":
        # Return identifier as a string (variable reference)
        return _node_text(node, source)
    elif node.type == "member_expression":
        # Handle qualified names like AuthGuard.jwt
        return _node_text(node, source)
    # For other types, return the text representation
    return _node_text(node, source)  # pragma: no cover


def _extract_decorator_info(
    dec_node: "tree_sitter.Node", source: bytes
) -> dict[str, object]:
    """Extract full decorator information including arguments.

    Returns a dict with:
    - name: decorator name (e.g., "Injectable", "Controller")
    - args: list of positional arguments
    - kwargs: dict of keyword arguments (always empty for JS/TS decorators)

    TypeScript decorators don't have named kwargs like Python, so kwargs is always {}.
    """
    name = ""
    args: list[object] = []
    kwargs: dict[str, object] = {}

    # Decorator can be: @Name, @Name(), @Name(arg1, arg2)
    for child in dec_node.children:
        if child.type == "call_expression":
            # @Decorator() or @Decorator(args)
            for call_child in child.children:
                if call_child.type == "identifier":
                    name = _node_text(call_child, source)
                elif call_child.type == "member_expression":
                    name = _node_text(call_child, source)
                elif call_child.type == "arguments":
                    for arg in call_child.children:
                        if arg.type not in ("(", ")", ","):
                            args.append(_ts_value_to_python(arg, source))
        elif child.type == "identifier":  # pragma: no cover
            # @Decorator without parens (rare in TS but possible)
            name = _node_text(child, source)
        elif child.type == "member_expression":  # pragma: no cover
            # @module.Decorator without parens
            name = _node_text(child, source)

    return {"name": name, "args": args, "kwargs": kwargs}


def _extract_decorators(
    node: "tree_sitter.Node", source: bytes
) -> list[dict[str, object]]:
    """Extract all decorators for a class or method node.

    Decorators appear as sibling nodes before the decorated node,
    or as children with type 'decorator' in some grammars.

    Handles TypeScript export patterns:
    - @Decorator export class Foo {} -> decorator is sibling in export_statement
    - The decorator comes before 'export' keyword but decorates the class

    Returns list of decorator info dicts: [{"name": str, "args": list, "kwargs": dict}]
    """
    decorators: list[dict[str, object]] = []

    # Check for decorator children (some grammars nest decorators inside the declaration)
    for child in node.children:
        if child.type == "decorator":
            dec_info = _extract_decorator_info(child, source)
            if dec_info["name"]:
                decorators.append(dec_info)

    # Check siblings before this node (TypeScript pattern)
    parent = node.parent
    if parent is not None:
        idx = None
        for i, sibling in enumerate(parent.children):
            if sibling == node:
                idx = i
                break

        if idx is not None:
            # Look backward for decorator siblings
            # For export_statement: children are [decorator, export, class_declaration]
            # We need to skip 'export' keyword to find decorators
            for i in range(idx - 1, -1, -1):
                sibling = parent.children[i]
                if sibling.type == "decorator":
                    dec_info = _extract_decorator_info(sibling, source)
                    if dec_info["name"]:
                        decorators.insert(0, dec_info)  # Maintain order
                elif sibling.type in ("comment", "export"):
                    # Skip comments and 'export' keyword to find decorators
                    continue
                else:
                    # Stop at any other node (e.g., another statement)
                    break

    return decorators


def _find_field_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Return a class field's declared name — its direct ``property_identifier``
    or ``private_property_identifier`` (JS ``#x``) child — or None for a computed
    field name (``[Symbol.iterator]``), which has no stable string identity.

    Reads only the field's own name slot, NOT the decorator child (which carries
    its own identifier, e.g. ``property`` in ``@property() count``).
    """
    for child in node.children:
        if child.type in ("property_identifier", "private_property_identifier"):
            return _node_text(child, source)
    return None


def _extract_field_modifiers(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Collect a class field's modifiers: the keyword nodes ``static`` /
    ``readonly`` / ``abstract`` / ``declare`` / ``override`` plus the
    accessibility modifier (``public`` / ``private`` / ``protected``, whose value
    is the node's text). Mirrors the public-API ``modifiers`` set py.py populates
    for variables (WI-zimum)."""
    mods: list[str] = []
    for child in node.children:
        if child.type in ("static", "readonly", "abstract", "declare", "override"):
            mods.append(child.type)
        elif child.type == "accessibility_modifier":
            mods.append(_node_text(child, source))
    return mods


def _jsts_constructed_from(
    declarator: "tree_sitter.Node", source: bytes,
) -> "str | None":
    """The callee of a declarator's initializer, for ``meta['constructed_from']``.

    ``const app = new Koa()`` -> ``"Koa"``; ``const r = express.Router()`` ->
    ``"express.Router"``. Both ``new_expression`` and a plain
    ``call_expression`` count: JS frameworks use each (``new Koa()`` vs
    ``express()``), and a YAML author keying on the framework's export does
    not care which. Qualification is kept so a namespaced callee stays
    distinguishable from a same-named local.

    A computed callee (``registry[k]()``) has no static name and yields None
    rather than a guess — a pattern matching a fiction would fail silently.
    """
    value = declarator.child_by_field_name("value")
    if value is None or value.type not in ("new_expression", "call_expression"):
        return None
    callee = value.child_by_field_name(
        "constructor" if value.type == "new_expression" else "function",
    )
    if callee is None:  # pragma: no cover - the JS/TS grammar always fills
        # the callee slot: `new (f())()` and `(0,f)()` yield a
        # `parenthesized_expression` rather than nothing, and those fall
        # through to the name-shape check below. Kept as a guard against a
        # damaged parse.
        return None
    if callee.type == "identifier":
        return _node_text(callee, source)
    if callee.type == "member_expression":
        text = _node_text(callee, source)
        # Only a dotted static path qualifies; `a[b].c` is computed.
        return text if all(part.isidentifier() for part in text.split(".")) else None
    return None


def _extract_field_type(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Return the type-annotation text (without the leading ``: ``, e.g.
    ``"string[]"``) of a class field (``public_field_definition``) or a variable
    declarator (``const x: T``), or None when untyped (every JS binding and
    untyped TS bindings). The annotation is a direct ``type_annotation`` child in
    both shapes."""
    for child in node.children:
        if child.type == "type_annotation":
            return _node_text(child, source).lstrip(": ").strip() or None
    return None


def _method_signature_text(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """TS-idiomatic signature of an interface ``method_signature``: ``"(): string"``.

    Built from the node's own ``formal_parameters`` plus its return
    ``type_annotation``, the same two slots the class ``method_definition`` path
    reads. Returns None when there are no parameters to read, which is the
    signal the caller uses to leave ``Symbol.signature`` unset rather than
    storing an empty string.
    """
    params = next(
        (c for c in node.children if c.type == "formal_parameters"), None
    )
    if params is None:  # pragma: no cover - a method_signature always has them
        return None
    text = _node_text(params, source)
    ret = next((c for c in node.children if c.type == "type_annotation"), None)
    if ret is not None:
        text += ": " + _node_text(ret, source).lstrip(": ").strip()
    return text


def _container_member_specs(
    body: "tree_sitter.Node", source: bytes
) -> list[tuple["tree_sitter.Node", str, str, Optional[str]]]:
    """``(node, member_name, kind, signature)`` per NAMED member of a TS
    ``enum_body`` / ``interface_body`` (WI-duguk).

    Kinds mirror the class-member branches so a consumer sees one vocabulary:
    a callable member is a ``method``, a value member is a ``field``. Enum
    members are ``field`` for the same reason the D and Nim analyzers chose it
    — they are named values of the type.

    Members with no ``property_identifier`` are skipped and thereby left out of
    the graph: ``construct_signature`` (``new (x: number): D``) and
    ``index_signature`` (``[key: string]: unknown``) have no name to anchor, and
    inventing one would be a wrong-name phantom — strictly worse than the
    fails-safe recall miss of omitting them.
    """
    specs: list[tuple["tree_sitter.Node", str, str, Optional[str]]] = []
    for child in body.children:
        if child.type == "enum_assignment":
            # ``Red = 'red'`` — the name is the assignment's own identifier.
            name = _find_field_name(child, source)
            if name:
                specs.append((child, name, "field", None))
        elif child.type == "property_identifier":
            # A bare ``Green`` member sits directly under the enum body.
            specs.append((child, _node_text(child, source), "field", None))
        elif child.type == "method_signature":
            name = _find_field_name(child, source)
            if name:
                specs.append(
                    (child, name, "method", _method_signature_text(child, source))
                )
        elif child.type == "property_signature":
            name = _find_field_name(child, source)
            if name:
                specs.append(
                    (child, name, "field", _extract_field_type(child, source))
                )
    return specs


def _make_container_member_symbols(
    container: "tree_sitter.Node",
    body_type: str,
    source: bytes,
    container_name: str,
    file_path: object,
    lang: str,
    run_id: str,
    line_offset: int,
    file_stable_id: str,
) -> list["Symbol"]:
    """Symbols for an enum's / interface's named members (WI-duguk).

    Emitted from inside the container's own branch rather than as a top-level
    node-type case, so the owner name is already in hand and no span- or
    ancestor-walk is needed to find it — which is what keeps an interface and an
    implementing class in the same file from claiming each other's members.
    """
    body = next((c for c in container.children if c.type == body_type), None)
    if body is None:  # pragma: no cover - a named container always has a body
        return []
    out: list["Symbol"] = []
    for member, member_name, kind, signature in _container_member_specs(
        body, source
    ):
        span = Span(
            start_line=member.start_point[0] + 1 + line_offset,
            end_line=member.end_point[0] + 1 + line_offset,
            start_col=member.start_point[1],
            end_col=member.end_point[1],
        )
        # ``Owner.member`` — ``.`` is the JS/TS separator the containment
        # linker splits on, matching the class-member branches.
        full_name = f"{container_name}.{member_name}"
        qualified_name = _make_jsts_qualified_name(
            [container_name], member_name, lang,
        )
        out.append(Symbol(
            id=_make_symbol_id(
                str(file_path), span.start_line, span.end_line,
                full_name, kind, lang,
            ),
            name=full_name,
            kind=kind,
            language=lang,
            path=str(file_path),
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
            stable_id=make_typed_stable_id(
                kind, signature or "",
                name=member_name,
                qualified_name=qualified_name,
                file_stable_id=file_stable_id,
            ),
            qualified_name=qualified_name,
            line_span=span.end_line - span.start_line + 1,
        ))
    return out


def _is_module_level_declaration(node: "tree_sitter.Node") -> bool:
    """True when a ``lexical_declaration`` / ``variable_declaration`` sits at
    module (program) scope — directly under ``program``, or under a top-level
    ``export_statement`` (``export const X = ...``). Mirrors the Python
    analyzer's module-level-only variable contract (WI-jusus F5): function-body
    locals and block-scoped bindings are deliberately excluded to bound the
    blast radius to module constants / module state."""
    parent = node.parent
    if parent is None:  # pragma: no cover - declarations always have a parent
        return False
    if parent.type == "program":
        return True
    if parent.type == "export_statement":
        grandparent = parent.parent
        return grandparent is not None and grandparent.type == "program"
    return False


def _unwrap_paren_extends(
    paren_node: "tree_sitter.Node", source: bytes,
) -> str:
    """Extract the base class name from a parenthesized extends expression.

    TypeScript allows cast expressions in extends clauses::

        class Room extends (EventEmitter as new () => TypedEmitter<Callbacks>) {}

    The AST is: parenthesized_expression > as_expression > identifier.
    This function walks down to find the first identifier, which is the
    actual base class being extended.
    """
    for child in paren_node.children:
        if child.type in ("identifier", "type_identifier"):
            return _node_text(child, source)  # pragma: no cover — bare (Foo) not seen in practice
        if child.type == "as_expression":
            # as_expression: first child is the value being cast
            for as_child in child.children:
                if as_child.type in ("identifier", "type_identifier"):
                    return _node_text(as_child, source)
                if as_child.type == "member_expression":  # pragma: no cover
                    return _node_text(as_child, source)
    return ""  # pragma: no cover


def _extract_base_classes(
    node: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Extract base classes from a class_declaration or abstract_class_declaration node.

    Handles:
    - extends clause: class Foo extends Bar
    - implements clause: class Foo implements IBar, IBaz
    - generic types: class Foo extends Bar<T>
    - parenthesized cast: class Foo extends (Bar as new () => Baz<T>)

    Supports both TypeScript (nested extends_clause) and JavaScript (flat) grammars.

    Returns list of base class/interface names.
    """
    base_classes: list[str] = []

    for child in node.children:
        if child.type == "class_heritage":
            # class_heritage contains extends_clause and/or implements_clause
            for heritage_child in child.children:
                if heritage_child.type == "extends_clause":
                    # TypeScript: extends_clause contains the base class
                    # May have identifier/type_identifier followed by type_arguments
                    base_name = ""
                    type_args = ""
                    for extends_child in heritage_child.children:
                        if extends_child.type in ("identifier", "type_identifier"):
                            base_name = _node_text(extends_child, source)
                        elif extends_child.type == "member_expression":
                            # React.Component style
                            base_name = _node_text(extends_child, source)
                        elif extends_child.type == "generic_type":
                            # Explicit generic type like Repository<User>
                            base_name = _node_text(extends_child, source)  # pragma: no cover
                        elif extends_child.type == "parenthesized_expression":
                            # Cast expression: (EventEmitter as new () => TypedEmitter<T>)
                            # Unwrap to find the first identifier (the actual base class)
                            base_name = _unwrap_paren_extends(extends_child, source)
                        elif extends_child.type == "type_arguments":
                            # Separate type arguments like <User>
                            type_args = _node_text(extends_child, source)
                    if base_name:
                        base_classes.append(base_name + type_args)
                elif heritage_child.type == "implements_clause":
                    # implements_clause contains interface list
                    for impl_child in heritage_child.children:
                        if impl_child.type in ("identifier", "type_identifier"):
                            base_classes.append(_node_text(impl_child, source))
                        elif impl_child.type == "generic_type":
                            base_classes.append(_node_text(impl_child, source))
                elif heritage_child.type == "identifier":
                    # JavaScript: class_heritage directly contains identifier
                    base_classes.append(_node_text(heritage_child, source))
                elif heritage_child.type == "member_expression":
                    # JavaScript: qualified base class like React.Component
                    base_classes.append(_node_text(heritage_child, source))

    return base_classes


def _extract_implements_names(
    node: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Return the ``implements`` clause base names of a class node.

    ``_extract_base_classes`` flattens ``extends`` and ``implements`` bases into
    one list, which is fine for the resolved path (the target Symbol's kind
    disambiguates: class -> extends, interface -> implements). The
    unresolved-external fallback (F4) has no target kind, so it needs the clause
    origin to label the edge correctly — an external ``implements OnInit``
    (Angular) must not be mislabeled ``extends``. JavaScript has no
    ``implements`` clause, so this returns ``[]`` for JS classes.
    """
    names: list[str] = []
    for child in node.children:
        if child.type == "class_heritage":
            for heritage_child in child.children:
                if heritage_child.type == "implements_clause":
                    for impl_child in heritage_child.children:
                        if impl_child.type in (
                            "identifier", "type_identifier", "generic_type",
                        ):
                            names.append(_node_text(impl_child, source))
    return names


def _callee_last_name(
    call: "tree_sitter.Node", source: bytes,
) -> Optional[str]:
    """Return the last identifier of a call_expression's callee.

    ``foo(...)`` -> ``foo``; ``a.b.forEach(...)`` -> ``forEach``. Used to name
    anonymous call-argument callbacks ``_cb_<callee>``. Returns ``None`` when
    the callee is itself an expression with no trailing name (e.g. the curried
    ``getHandler()(cb)``), in which case the caller falls back to a generic
    name.
    """
    fn = call.child_by_field_name("function")
    if fn is None:
        # ``new X(cb)`` is a ``new_expression`` whose callee is the
        # ``constructor`` field, not ``function``.
        fn = call.child_by_field_name("constructor")
    if fn is None:  # pragma: no cover - defensive: a call/new always has a callee
        return None
    if fn.type == "identifier":
        return _node_text(fn, source)
    if fn.type == "member_expression":
        prop = fn.child_by_field_name("property")
        if prop is not None:  # pragma: no branch - member_expression always has a property
            return _node_text(prop, source)
    return None


def _classify_anon_function(
    node: "tree_sitter.Node", source: bytes,
) -> Optional[tuple[str, str]]:
    """Classify an anonymous arrow / function-expression / generator node.

    WI-zavad anonymous-callback function-node slice (emission-parity F2,
    Option 1 — documented-idiom scope). Returns ``(category, name)``:

    * ``("call_arg", "_cb_<callee>")`` — the function is passed as an argument
      to a call (``arr.forEach(x => {})``, ``el.addEventListener('x', cb)``).
    * ``("iife", "_iife")`` — an immediately-invoked function expression
      ``(function () {})()`` / ``(() => {})()``.

    Returns ``None`` for any other position — variable-bound (extracted by the
    ``lexical_declaration`` path), object property, or return / ternary /
    template-substitution position — which is out of scope for this slice (those
    have no call-site anchor for a companion incoming edge, so extracting them
    would inflate dead-code false-positives; an explicitly-deferred follow-up).
    """
    parent = node.parent
    if parent is None:  # pragma: no cover - defensive: root is always 'program'
        return None
    # IIFE: the function sits inside a parenthesized_expression that is itself
    # the *callee* (the ``function`` field) of a call_expression. A bare
    # parenthesized function that is NOT invoked (``const x = (() => 1)``) has a
    # parenthesized_expression parent but no invoking call_expression, so it is
    # correctly skipped.
    if parent.type == "parenthesized_expression":
        grandparent = parent.parent
        if grandparent is not None and grandparent.type == "call_expression":
            callee = grandparent.child_by_field_name("function")
            if callee is not None and callee.id == parent.id:
                return ("iife", "_iife")
        return None
    # Call-argument callback: the function is a direct child of a call's
    # ``arguments`` list. Both ``foo(cb)`` (call_expression) and the canonical
    # ``new Promise((res, rej) => {})`` / ``new Observable(cb)`` constructor-
    # executor form (new_expression) qualify — the executor IS the most common
    # anonymous callback.
    if parent.type == "arguments":
        call = parent.parent
        if call is not None and call.type in ("call_expression", "new_expression"):
            callee_name = _callee_last_name(call, source)
            return (
                "call_arg",
                f"_cb_{callee_name}" if callee_name else "_cb_anonymous",
            )
    return None


def _extract_symbols(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    lang: str,
    run: AnalysisRun,
    line_offset: int = 0,
    repo_root: Optional[Path] = None,
) -> list[Symbol]:
    """Extract symbols from a parsed tree (pass 1).

    Uses iterative traversal to avoid RecursionError on deeply nested code.

    Args:
        tree: Parsed tree-sitter tree
        source: Source bytes
        file_path: Path to the file
        lang: Language (javascript or typescript)
        run: Analysis run for provenance
        line_offset: Line offset for Svelte script blocks
        repo_root: Repository root for path normalisation. When provided,
            the file pseudo-node Symbol's ``name`` is the repo-relative
            path (INV-kokaj mirror of INV-vaguj). Tests calling this
            helper directly may pass ``None``.
    """
    symbols: list[Symbol] = []

    # INV-kokaj: emit the file pseudo-node as kind="file" with the
    # canonical file-id shape so the orchestrator file-symbol synthesizer
    # dedups against it (existing_ids check). Before this fix, every
    # JS/TS file emitted both a kind="module" Symbol (here) and a
    # kind="file" Symbol (from the synthesizer when edges targeted the
    # file id). File-kind is the cross-language canonical for "this file"
    # (see analyze.base.make_file_id). The Symbol provides an enclosing
    # scope for module-level edges (route calls, bootstrap calls,
    # attribute reads on ``process``/``window``/``document``) so files
    # without explicit functions remain reachable in slice traversal.
    end_line = tree.root_node.end_point[0] + 1 + line_offset
    module_span = Span(
        start_line=1 + line_offset,
        end_line=end_line,
        start_col=0,
        end_col=0,
    )
    file_name = str(file_path)
    if repo_root is not None:
        try:
            file_name = str(file_path.relative_to(repo_root))
        except ValueError:  # pragma: no cover - defensive
            pass
    # WI-bokab (v7): file-identity anchor for this file's symbols. ``file_name``
    # is the repo-relative path (``file_path`` is ABSOLUTE here — it comes from
    # ``find_js_ts_files(repo_root)`` etc.; the absolute-path trap), so we fold
    # the relativized form, NOT ``str(file_path)``. Folded into
    # make_typed_stable_id's containing slot so same-(kind, name, qualified_name)
    # functions/methods in different files hash distinctly. Keyed on the per-file
    # runtime ``lang`` to match the file Symbol's ``language=lang`` and the
    # orchestrator's make_file_stable_id(s.language, s.path) backstop.
    file_stable_id = make_file_stable_id(lang, normalize_path(file_name))
    module_symbol = Symbol(
        id=make_file_id(lang, str(file_path)),
        name=file_name,
        kind="file",
        language=lang,
        path=str(file_path),
        span=module_span,
        origin=PASS_ID,
        origin_run_id=run.execution_id,
        line_span=module_span.end_line - module_span.start_line + 1,
    )
    symbols.append(module_symbol)

    # Track nodes we've already processed as route handlers (to avoid duplicates)
    processed_handlers: set[int] = set()

    # Pre-pass: collect React.lazy(() => import('./path')) declarations.
    # Maps variable name → dynamic import path for lazy_import metadata on routes.
    lazy_import_map: dict[str, str] = {}
    for node in iter_tree(tree.root_node):
        if node.type in ("variable_declarator", "lexical_declaration"):
            _collect_react_lazy_declaration(node, source, lazy_import_map)

    for node in iter_tree(tree.root_node):
        # Skip nodes we've already processed as route handlers
        if id(node) in processed_handlers:
            continue

        # Express-style route handler detection: app.get('/path', handler)
        # This also emits UsageContext records (v1.1.x) for YAML pattern matching.
        if node.type == "call_expression":
            http_method, route_path = _detect_route_call(node, source)
            if http_method:
                handler_node, handler_name, is_external = _find_route_handler_in_call(node, source)
                if handler_node:
                    # Mark the handler as processed to avoid extracting it again
                    processed_handlers.add(id(handler_node))

                    if is_external:
                        # External handler: router.post('/path', userController.createUser)
                        span = Span(
                            start_line=handler_node.start_point[0] + 1 + line_offset,
                            end_line=handler_node.end_point[0] + 1 + line_offset,
                            start_col=handler_node.start_point[1],
                            end_col=handler_node.end_point[1],
                        )
                        # WI-zugob: Symbol.name changes from the handler name
                        # to "{METHOD} {path}". Handler identity is preserved in
                        # meta["handler_ref"] — the key linkers/route_handler
                        # already resolved against; it never read Symbol.name.
                        symbols.append(make_route_symbol(
                            language=lang,
                            path=str(file_path),
                            span=span,
                            method=http_method,
                            route_path=route_path or "",
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            handler_ref=handler_name,
                        ))
                    else:
                        # Inline handler: router.get('/path', (req, res) => {})
                        name = None
                        if handler_node.type in (
                            "function_expression", "function", "generator_function",
                        ):
                            # named generator handler ``function* h(){}`` keeps
                            # its declared name (WI-zavad parity)
                            name = _find_name_in_children(handler_node, source)
                        if not name:
                            clean_path = route_path.replace("/", "_").replace(":", "").replace("{", "").replace("}", "") if route_path else ""
                            name = f"_{http_method}{clean_path}_handler"

                        span = Span(
                            start_line=handler_node.start_point[0] + 1 + line_offset,
                            end_line=handler_node.end_point[0] + 1 + line_offset,
                            start_col=handler_node.start_point[1],
                            end_col=handler_node.end_point[1],
                        )
                        # INV-golap: the inline handler IS a real function node
                        # (arrow / function / function_expression), so extract
                        # its signature like any other function symbol — the
                        # earlier omission left route handlers as the lone
                        # null-signature function|method nodes vs TS parity.
                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function", lang),
                            name=name,
                            kind="function",
                            language=lang,
                            path=str(file_path),
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            stable_id=make_route_stable_id(http_method, route_path) if route_path else None,
                            meta={"route_path": route_path, "http_method": http_method} if route_path else None,
                            signature=_extract_jsts_signature(handler_node, source),
                            line_span=span.end_line - span.start_line + 1,
                        )
                        symbols.append(symbol)
                    continue  # Skip further processing of this call_expression

        # React Router JSX route detection: <Route path="/users" element={<Users />} />
        if node.type in ("jsx_self_closing_element", "jsx_element"):
            route_path, component_name = _detect_jsx_route(node, source)
            if route_path is not None:
                handler_name = component_name or f"_route{route_path.replace('/', '_').replace(':', '').replace('*', 'splat')}_handler"
                span = Span(
                    start_line=node.start_point[0] + 1 + line_offset,
                    end_line=node.end_point[0] + 1 + line_offset,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                jsx_route_meta = _build_jsx_route_meta(
                    route_path, component_name, lazy_import_map,
                ) or {}
                jsx_route_meta["framework_role"] = "route"
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, handler_name, "route", lang),
                    name=handler_name,
                    kind="function",
                    language=lang,
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=make_route_stable_id("GET", route_path),
                    meta=jsx_route_meta,
                    line_span=span.end_line - span.start_line + 1,
                )
                symbols.append(symbol)
                # Don't continue — let tree walk also process child nodes

        # React Router v6.4+ createBrowserRouter/createHashRouter/createMemoryRouter
        if node.type == "call_expression":
            config_routes = _detect_create_browser_router(node, source)
            for rpath, comp, extra_meta in config_routes:
                handler_name = comp or f"_route{rpath.replace('/', '_').replace(':', '').replace('*', 'splat')}_handler"
                span = Span(
                    start_line=node.start_point[0] + 1 + line_offset,
                    end_line=node.end_point[0] + 1 + line_offset,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbols.append(make_route_symbol(
                    language=lang,
                    path=str(file_path),
                    span=span,
                    method="GET",
                    route_path=rpath,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    handler_ref=comp,
                    extra_meta=dict(extra_meta),
                ))

        # Function declarations, incl. generators ``function* g() {}`` (WI-zavad
        # named function-node slice — ``generator_function_declaration`` was
        # never matched, so generators emitted zero function symbols).
        # (skip if inside an export_statement - handled below)
        if node.type in ("function_declaration", "generator_function_declaration"):
            # Check if parent is export_statement - if so, skip (handled in export_statement case)
            if node.parent and node.parent.type == "export_statement":
                continue
            name = _find_name_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1 + line_offset,
                    end_line=node.end_point[0] + 1 + line_offset,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                signature = _extract_jsts_signature(node, source)

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_jsts_signature(signature)
                stable_id = make_typed_stable_id(
                    "function", norm_sig,
                    name=name,
                    qualified_name=_make_jsts_qualified_name(
                        _get_jsts_class_ancestors(node, source), name, lang,
                    ),
                    file_stable_id=file_stable_id,
                ) if norm_sig else None

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function", lang),
                    name=name,
                    kind="function",
                    language=lang,
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=stable_id,
                    signature=signature,
                    docstring=extract_preceding_doc_comment(node, source, lang),
                    shape_id=_jsts_analyzer.compute_shape_id(node),
                    line_span=span.end_line - span.start_line + 1,
                    qualified_name=_make_jsts_qualified_name(
                        _get_jsts_class_ancestors(node, source), name, lang,
                    ),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, lang),
                )
                symbols.append(symbol)

        # Arrow functions assigned to variables: const foo = () => {}
        elif node.type in ("lexical_declaration", "variable_declaration"):
            for child in node.children:
                if child.type == "variable_declarator":
                    name_node = None
                    value_node = None
                    for grandchild in child.children:
                        if grandchild.type == "identifier":
                            name_node = grandchild
                        elif grandchild.type in (
                            "arrow_function", "function_expression",
                            "generator_function",
                        ):
                            # WI-zavad named function-node slice: ``const f =
                            # function () {}`` (function_expression) and
                            # ``const g = function* () {}`` (generator_function)
                            # reach parity with the existing arrow-function path.
                            value_node = grandchild
                        elif grandchild.type == "call_expression":
                            # Pattern: const handler = catchAsync(async (req, res) => {})
                            for call_child in grandchild.children:
                                if call_child.type == "arguments":
                                    for arg in call_child.children:
                                        if arg.type in (
                                            "arrow_function",
                                            "function_expression",
                                            "generator_function",
                                        ):
                                            value_node = arg
                                            break
                                    if value_node:
                                        break
                    if name_node and value_node:
                        name = _node_text(name_node, source)
                        span = Span(
                            start_line=value_node.start_point[0] + 1 + line_offset,
                            end_line=value_node.end_point[0] + 1 + line_offset,
                            start_col=value_node.start_point[1],
                            end_col=value_node.end_point[1],
                        )
                        signature = _extract_jsts_signature(value_node, source)

                        # Typed stable_id (ADR-0014 §3)
                        norm_sig = normalize_jsts_signature(signature)
                        stable_id = make_typed_stable_id(
                            "function", norm_sig,
                            name=name,
                            qualified_name=_make_jsts_qualified_name(
                                _get_jsts_class_ancestors(node, source), name, lang,
                            ),
                            file_stable_id=file_stable_id,
                        ) if norm_sig else None

                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function", lang),
                            name=name,
                            kind="function",
                            language=lang,
                            path=str(file_path),
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            stable_id=stable_id,
                            signature=signature,
                            docstring=extract_preceding_doc_comment(node, source, lang),
                            shape_id=_jsts_analyzer.compute_shape_id(value_node),
                            line_span=span.end_line - span.start_line + 1,
                            qualified_name=_make_jsts_qualified_name(
                                _get_jsts_class_ancestors(node, source), name, lang,
                            ),
                            cyclomatic_complexity=compute_cyclomatic_complexity(value_node, lang),
                        )
                        symbols.append(symbol)
                        # WI-zavad anon-callback slice: this arrow/function-
                        # expression is already extracted (named after its
                        # variable), so mark it processed to keep the bare
                        # anonymous-callback branch below from re-emitting it.
                        processed_handlers.add(id(value_node))
                    elif _is_module_level_declaration(node):
                        # WI-jusus F5 slice 2: a module-level value declaration
                        # (const/let/var X = ...; or a bare `let s;`) that is NOT
                        # a function emits a kind='variable' symbol so module
                        # constants / module state are visible to search,
                        # centrality, and io-boundaries. Use the declarator's
                        # `name` FIELD (not the loop's name_node, which catches a
                        # destructuring RHS identifier): only a simple identifier
                        # name is emitted — object/array destructuring patterns
                        # are a follow-up. Function-valued declarations are
                        # handled by the branch above (value_node set), so they
                        # never reach here.
                        var_name_node = child.child_by_field_name("name")
                        if var_name_node is not None and var_name_node.type == "identifier":
                            var_name = _node_text(var_name_node, source)
                            vspan = Span(
                                start_line=child.start_point[0] + 1 + line_offset,
                                end_line=child.end_point[0] + 1 + line_offset,
                                start_col=child.start_point[1],
                                end_col=child.end_point[1],
                            )
                            symbols.append(Symbol(
                                id=_make_symbol_id(str(file_path), vspan.start_line, vspan.end_line, var_name, "variable", lang),
                                name=var_name,
                                kind="variable",
                                language=lang,
                                path=str(file_path),
                                span=vspan,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                # INV-sotiv variable identity: name-scoped to the
                                # declaring file (repo-relative for location
                                # independence, WI-bokab). const/let cannot
                                # redeclare at module scope; a rare `var`
                                # redeclaration is split by the within-file
                                # collision post-pass (ADR-0035).
                                stable_id=make_variable_stable_id(lang, normalize_path(file_name), var_name),
                                signature=_extract_field_type(child, source),
                                meta=(
                                    {"constructed_from": _jsts_cf}
                                    if (_jsts_cf := _jsts_constructed_from(child, source))
                                    else None
                                ),
                                is_exported=node.parent is not None and node.parent.type == "export_statement",
                                line_span=vspan.end_line - vspan.start_line + 1,
                            ))

        # Class declarations (including abstract classes)
        elif node.type in ("class_declaration", "abstract_class_declaration"):
            name = _find_name_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1 + line_offset,
                    end_line=node.end_point[0] + 1 + line_offset,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )

                # Extract decorator and base class metadata
                meta: dict[str, object] | None = None
                decorators = _extract_decorators(node, source)
                base_classes = _extract_base_classes(node, source)
                if decorators or base_classes:
                    meta = {}
                    if decorators:
                        meta["decorators"] = decorators
                    if base_classes:
                        meta["base_classes"] = base_classes

                # audit-findings 0018: the grammar hands us a distinct
                # `abstract_class_declaration`, so abstractness needs no
                # inference — but modifiers was left empty, making TS abstract
                # classes indistinguishable from concrete ones to
                # `is_abstract_type`. Five other languages record it here.
                class_modifiers = (
                    ["abstract"]
                    if node.type == "abstract_class_declaration"
                    else []
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "class", lang),
                    name=name,
                    kind="class",
                    language=lang,
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta=meta,
                    modifiers=class_modifiers,
                    shape_id=_jsts_analyzer.compute_shape_id(node),
                    line_span=span.end_line - span.start_line + 1,
                    qualified_name=_make_jsts_qualified_name(
                        _get_jsts_class_ancestors(node, source), name, lang,
                    ),
                )
                symbols.append(symbol)

        # TypeScript interface declarations
        elif node.type == "interface_declaration":
            name = _find_name_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1 + line_offset,
                    end_line=node.end_point[0] + 1 + line_offset,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "interface", lang),
                    name=name,
                    kind="interface",
                    language=lang,
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    shape_id=_jsts_analyzer.compute_shape_id(node),
                    line_span=span.end_line - span.start_line + 1,
                    qualified_name=_make_jsts_qualified_name(
                        _get_jsts_class_ancestors(node, source), name, lang,
                    ),
                )
                symbols.append(symbol)
                # WI-duguk: the interface's own member signatures.
                symbols.extend(_make_container_member_symbols(
                    node, "interface_body", source, name, file_path, lang,
                    run.execution_id, line_offset, file_stable_id,
                ))

        # TypeScript type alias declarations
        elif node.type == "type_alias_declaration":
            name = _find_name_in_children(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1 + line_offset,
                    end_line=node.end_point[0] + 1 + line_offset,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "type", lang),
                    name=name,
                    kind="type",
                    language=lang,
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    shape_id=_jsts_analyzer.compute_shape_id(node),
                    line_span=span.end_line - span.start_line + 1,
                )
                symbols.append(symbol)

        # TypeScript enum declarations
        elif node.type == "enum_declaration":
            name = None
            for child in node.children:
                if child.type == "identifier":
                    name = _node_text(child, source)
                    break
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1 + line_offset,
                    end_line=node.end_point[0] + 1 + line_offset,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "enum", lang),
                    name=name,
                    kind="enum",
                    language=lang,
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    shape_id=_jsts_analyzer.compute_shape_id(node),
                    line_span=span.end_line - span.start_line + 1,
                    qualified_name=_make_jsts_qualified_name(
                        _get_jsts_class_ancestors(node, source), name, lang,
                    ),
                )
                symbols.append(symbol)
                # WI-duguk: the enum's own named members.
                symbols.extend(_make_container_member_symbols(
                    node, "enum_body", source, name, file_path, lang,
                    run.execution_id, line_offset, file_stable_id,
                ))

        # Method definitions inside classes (including getters/setters)
        elif node.type == "method_definition":
            name = _find_name_in_children(node, source)
            if name:
                kind = "method"
                for child in node.children:
                    if child.type == "get":
                        kind = "getter"
                        break
                    elif child.type == "set":
                        kind = "setter"
                        break

                span = Span(
                    start_line=node.start_point[0] + 1 + line_offset,
                    end_line=node.end_point[0] + 1 + line_offset,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                # Use parent-walking to get class context
                current_class_name = _get_class_context(node, source)
                full_name = f"{current_class_name}.{name}" if current_class_name else name

                http_method, _method_route_path = _detect_nestjs_decorator(node, source)
                if http_method:
                    # Use route path if available, fall back to method name for identity
                    _nestjs_path = _method_route_path or full_name
                    stable_id = make_route_stable_id(http_method, _nestjs_path)
                else:
                    stable_id = None

                # Build meta with decorators
                # Note: Route path combination is handled by enrichment via prefix_from_parent
                # in the NestJS YAML pattern definition (see nestjs.yaml)
                meta = None
                decorators = _extract_decorators(node, source)
                if decorators:
                    meta = {"decorators": decorators}

                signature = _extract_jsts_signature(node, source)

                # Typed stable_id for non-route methods (ADR-0014 §3)
                if stable_id is None:
                    norm_sig = normalize_jsts_signature(signature)
                    if norm_sig:
                        stable_id = make_typed_stable_id(
                            kind, norm_sig,
                            name=name,
                            qualified_name=_make_jsts_qualified_name(
                                _get_jsts_class_ancestors(node, source), name, lang,
                            ),
                            file_stable_id=file_stable_id,
                        )

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, kind, lang),
                    name=full_name,
                    kind=kind,
                    language=lang,
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=stable_id,
                    meta=meta,
                    signature=signature,
                    docstring=extract_preceding_doc_comment(node, source, lang),
                    shape_id=_jsts_analyzer.compute_shape_id(node),
                    line_span=span.end_line - span.start_line + 1,
                    qualified_name=_make_jsts_qualified_name(
                        _get_jsts_class_ancestors(node, source), name, lang,
                    ),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, lang),
                )
                symbols.append(symbol)

        # WI-jusus (emission-parity F5): class FIELD symbols. Class fields parse
        # as public_field_definition (TS) / field_definition (JS); before this
        # they emitted no symbol, so module/class state had no anchor and field
        # decorators (lit @property/@state) had nothing to attach a decorated_by
        # edge to. Mirrors the method_definition branch (fields are class members
        # too): class-scoped identity via qualified_name, modifiers, and a
        # type-annotation signature. Decorators flow into _extract_decorator_edges
        # through meta["decorators"], exactly like classes/methods.
        elif node.type in ("public_field_definition", "field_definition"):
            name = _find_field_name(node, source)
            if name:
                span = Span(
                    start_line=node.start_point[0] + 1 + line_offset,
                    end_line=node.end_point[0] + 1 + line_offset,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                current_class_name = _get_class_context(node, source)
                full_name = f"{current_class_name}.{name}" if current_class_name else name

                meta_f: dict[str, object] | None = None
                decorators = _extract_decorators(node, source)
                if decorators:
                    meta_f = {"decorators": decorators}

                field_type = _extract_field_type(node, source)
                qualified_name = _make_jsts_qualified_name(
                    _get_jsts_class_ancestors(node, source), name, lang,
                )
                # Class-scoped canonical identity: name + qualified_name +
                # file fold make same-named fields in different classes/files
                # distinct even when both are untyped (empty signature slot).
                stable_id = make_typed_stable_id(
                    "field", field_type or "",
                    name=name,
                    qualified_name=qualified_name,
                    file_stable_id=file_stable_id,
                )

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "field", lang),
                    name=full_name,
                    kind="field",
                    language=lang,
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=stable_id,
                    meta=meta_f,
                    signature=field_type,
                    modifiers=_extract_field_modifiers(node, source),
                    qualified_name=qualified_name,
                    line_span=span.end_line - span.start_line + 1,
                )
                symbols.append(symbol)

        # Export default function - extract the function symbol
        # (incl. ``export function* g() {}`` — WI-zavad: the direct branch
        # above skips export children, so generators must be matched here too)
        elif node.type == "export_statement":
            for child in node.children:
                if child.type in ("function_declaration", "generator_function_declaration"):
                    name = _find_name_in_children(child, source)
                    if name:
                        span = Span(
                            start_line=child.start_point[0] + 1 + line_offset,
                            end_line=child.end_point[0] + 1 + line_offset,
                            start_col=child.start_point[1],
                            end_col=child.end_point[1],
                        )
                        signature = _extract_jsts_signature(child, source)

                        # Typed stable_id (ADR-0014 §3)
                        norm_sig = normalize_jsts_signature(signature)
                        stable_id = make_typed_stable_id(
                            "function", norm_sig,
                            name=name,
                            qualified_name=_make_jsts_qualified_name(
                                _get_jsts_class_ancestors(child, source), name, lang,
                            ),
                            file_stable_id=file_stable_id,
                        ) if norm_sig else None

                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function", lang),
                            name=name,
                            kind="function",
                            language=lang,
                            path=str(file_path),
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            stable_id=stable_id,
                            signature=signature,
                            docstring=extract_preceding_doc_comment(node, source, lang),
                            shape_id=_jsts_analyzer.compute_shape_id(child),
                            line_span=span.end_line - span.start_line + 1,
                            qualified_name=_make_jsts_qualified_name(
                                _get_jsts_class_ancestors(child, source), name, lang,
                            ),
                            cyclomatic_complexity=compute_cyclomatic_complexity(child, lang),
                        )
                        symbols.append(symbol)
                    break  # Only handle one function_declaration per export

        # WI-zavad anonymous-callback function-node slice (emission-parity F2,
        # Option 1 — documented-idiom scope): anonymous arrow / function-
        # expression / generator callbacks passed as call arguments, and IIFEs,
        # emit function symbols so body-calls attribute to them (not the file
        # pseudo-node) and linkers anchor on a real call-site symbol (the
        # F159.A2-c WS-linker file-anchor facet). Route-handler and variable-
        # bound callbacks are already extracted (and added to
        # ``processed_handlers``), so the skip at the top of this loop prevents
        # double-emission. ``_classify_anon_function`` returns ``None`` for
        # return / ternary / template-substitution arrows (deferred follow-up).
        elif node.type in (
            "arrow_function", "function_expression", "generator_function",
        ):
            classification = _classify_anon_function(node, source)
            if classification is not None:
                _category, anon_name = classification
                span = Span(
                    start_line=node.start_point[0] + 1 + line_offset,
                    end_line=node.end_point[0] + 1 + line_offset,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                signature = _extract_jsts_signature(node, source)
                norm_sig = normalize_jsts_signature(signature)
                qualified_name = _make_jsts_qualified_name(
                    _get_jsts_class_ancestors(node, source), anon_name, lang,
                )
                stable_id = make_typed_stable_id(
                    "function", norm_sig,
                    name=anon_name,
                    qualified_name=qualified_name,
                    file_stable_id=file_stable_id,
                ) if norm_sig else None
                symbols.append(Symbol(
                    id=_make_symbol_id(
                        str(file_path), span.start_line, span.end_line,
                        # Fold start_col into the id name-slot so two same-callee
                        # callbacks on ONE line (``p.then(a => a, e => e)``, two
                        # ``_iife``s) get distinct ids; Symbol.name stays the
                        # clean display name. The id round-trip validator only
                        # requires the name-slot be non-empty + colon-free.
                        f"{anon_name}@{span.start_col}", "function", lang,
                    ),
                    name=anon_name,
                    kind="function",
                    language=lang,
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=stable_id,
                    signature=signature,
                    docstring=extract_preceding_doc_comment(node, source, lang),
                    shape_id=_jsts_analyzer.compute_shape_id(node),
                    line_span=span.end_line - span.start_line + 1,
                    qualified_name=qualified_name,
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, lang),
                    meta={"anonymous": True},
                ))
                processed_handlers.add(id(node))

    # WI-nimug / WI-zimum Phase 2b: mark symbols as is_exported=True when
    # their defining declaration sits under a top-level export_statement.
    _mark_exported_symbols(tree.root_node, source, symbols)

    return symbols


def _collect_exported_names(
    root: "tree_sitter.Node", source: bytes,
) -> set[str]:
    """Return the set of identifier names reachable via top-level exports.

    WI-nimug / WI-zimum Phase 2b: finds names brought into the module's
    public API by any of these TypeScript / JavaScript ``export``
    syntaxes under the root node:

    - ``export function foo() {}`` / ``export class Bar {}`` —
      named export of a declaration
    - ``export default function foo() {}`` /
      ``export default class Bar {}`` — default export of a named decl
    - ``export const foo = ...`` / ``export let ...`` / ``export var ...``
    - ``export { foo, bar }`` — named re-export (export_clause)
    - ``export { foo } from './bar'`` — named re-export-from
    - ``export default identifier`` where identifier is a bare name

    Default exports without an explicit name (e.g. ``export default
    () => {}`` or ``export default 42``) are NOT added because there's
    no symbol name to match against the analyzer's Symbol.name field.

    Only the *top-level* export_statement children of the root module
    are considered. Nested re-exports inside a function body are rare
    and not part of the public module surface.
    """
    names: set[str] = set()
    for child in root.children:
        if child.type != "export_statement":
            continue
        _extract_export_names_from_statement(child, source, names)
    return names


def _extract_export_names_from_statement(
    node: "tree_sitter.Node", source: bytes, out: set[str],
) -> None:
    """Populate *out* with the identifier names exported by *node*."""
    for child in node.children:
        ctype = child.type
        if ctype in (
            "function_declaration", "class_declaration",
            "generator_function_declaration",
        ):
            # WI-zavad: ``export function* g(){}`` must mark its symbol
            # is_exported=True like ``export function f(){}`` does.
            name = _find_name_in_children(child, source)
            if name:
                out.add(name)
        elif ctype == "lexical_declaration" or ctype == "variable_declaration":
            # export const foo = ... / export let ... / export var ...
            for var_child in child.children:
                if var_child.type == "variable_declarator":
                    name_node = var_child.child_by_field_name("name")
                    if name_node is not None and name_node.type == "identifier":
                        out.add(_node_text(name_node, source))
        elif ctype == "export_clause":
            # export { foo, bar } or export { foo as bar }
            for clause_child in child.children:
                if clause_child.type == "export_specifier":
                    # Prefer the alias (``alias`` field) when present so
                    # the exported name matches the public surface.
                    alias = clause_child.child_by_field_name("alias")
                    name_node = (
                        alias
                        if alias is not None
                        else clause_child.child_by_field_name("name")
                    )
                    if name_node is not None:
                        out.add(_node_text(name_node, source))
        elif ctype == "identifier":
            # ``export default foo`` — bare identifier after ``default``.
            out.add(_node_text(child, source))


def _mark_exported_symbols(
    root: "tree_sitter.Node",
    source: bytes,
    symbols: list[Symbol],
) -> None:
    """Set ``Symbol.is_exported = True`` for each symbol whose short name
    is in the exported-name set for this file.

    WI-nimug: match by short name (split on the last dot) so ``Class.method``
    style symbols created for TypeScript class members do not accidentally
    get flagged when only the class is exported — class members stay
    un-exported unless the class was the only thing exported, in which case
    they are still un-exported here (the class symbol is the public
    API entry point). The file pseudo-node remains un-exported — the
    field is about individual declarations, not the per-file anchor
    (INV-kokaj renamed kind from "module" to "file").
    """
    exported_names = _collect_exported_names(root, source)
    if not exported_names:
        return
    for sym in symbols:
        if sym.kind == "file":
            continue
        short = sym.name.rsplit(".", 1)[-1] if "." in sym.name else sym.name
        if short in exported_names and "." not in sym.name:
            sym.is_exported = True


def _is_shadowed_by_param(node: "tree_sitter.Node", name: str, source: bytes) -> bool:
    """Check if *name* is shadowed by a parameter of an enclosing function.

    Walks up the AST from *node* looking for ``arrow_function``,
    ``function_declaration``, ``function_expression``, or ``method_definition``
    ancestors whose ``formal_parameters`` contain an ``identifier`` child
    matching *name*.

    This prevents false cross-file edges when a callback parameter (e.g.,
    ``resolve`` / ``reject`` inside ``new Promise((resolve, reject) => {...})``)
    happens to share a name with a globally-defined function.

    Walks through ALL enclosing function boundaries (not just the nearest)
    because JavaScript has lexical scoping — parameters from outer functions
    are visible in nested callbacks.  For example::

        new Promise(function(resolve, reject) {
            doAsync(function(err) {
                resolve(42);  // resolve is from the OUTER function
            });
        });

    Stops at ``function_declaration`` boundaries since those represent
    named top-level functions that form the analysis unit.
    """
    current = node.parent
    while current is not None:
        if current.type in (
            "arrow_function",
            "function_declaration",
            "function_expression",
            "method_definition",
            # WI-zavad: generator declaration/expression scopes also bind params
            "generator_function_declaration",
            "generator_function",
        ):
            for child in current.children:
                if child.type == "formal_parameters":
                    for param in child.children:
                        # JS: direct identifier params
                        if param.type == "identifier" and _node_text(param, source) == name:
                            return True
                        # TS: params wrapped in required_parameter or optional_parameter
                        if param.type in ("required_parameter", "optional_parameter"):
                            for pc in param.children:
                                if pc.type == "identifier" and _node_text(pc, source) == name:
                                    return True
                    break
                # arrow_function with single param (no parens): (x) => ... vs x => ...
                if current.type == "arrow_function" and child.type == "identifier":
                    if _node_text(child, source) == name:
                        return True
            # Not found in this function's params.  For named function
            # declarations (top-level), stop — these are analysis units.
            # For closures (arrow_function, function_expression), continue
            # walking up since JS lexical scoping makes outer params visible.
            if current.type in ("function_declaration", "generator_function_declaration"):
                return False
            # else: keep walking up through closure scopes
        current = current.parent
    return False


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    global_symbols: dict[str, Symbol],
    symbol_by_position: dict[tuple[str, int, int], Symbol] | None = None,
    line_offset: int = 0,
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/method.

    Returns the Symbol for the enclosing function, or None if not inside one.

    Uses symbol_by_position (keyed by file path + start position) for lookup,
    which correctly handles monorepos where multiple files define methods with
    the same name (e.g., CatsController.create in 11 NestJS sample apps).
    Falls back to global_symbols when symbol_by_position is unavailable.

    For arrow functions passed as callbacks (not assigned to variables), looks up
    the symbol by position using symbol_by_position. This enables call attribution
    for patterns like: app.get('/', (req, res) => { helper(); })
    """
    file_path_str = str(file_path)
    current = node.parent
    while current is not None:
        # ``generator_function_declaration`` (``function* g() {}``) is a named
        # top-level analysis unit like ``function_declaration`` — a call in its
        # body attributes to the generator symbol (WI-zavad call-graph parity).
        if current.type in ("function_declaration", "generator_function_declaration"):
            # Position-based lookup handles duplicate names across files
            if symbol_by_position:
                pos_key = (file_path_str, current.start_point[0] + 1 + line_offset, current.start_point[1])
                sym = symbol_by_position.get(pos_key)
                if sym:
                    return sym
            # Fallback to name-based lookup
            name = _find_name_in_children(current, source)
            if name and name in global_symbols:
                sym = global_symbols[name]
                if sym.path == file_path_str:
                    return sym
            return None  # pragma: no cover

        if current.type == "method_definition":
            # Position-based lookup handles duplicate names across files
            if symbol_by_position:
                pos_key = (file_path_str, current.start_point[0] + 1 + line_offset, current.start_point[1])
                sym = symbol_by_position.get(pos_key)
                if sym:
                    return sym
            # Fallback to name-based lookup
            name = _find_name_in_children(current, source)
            if name:
                class_ctx = _get_class_context(current, source)
                if class_ctx:
                    full_name = f"{class_ctx}.{name}"
                    if full_name in global_symbols:
                        sym = global_symbols[full_name]
                        if sym.path == file_path_str:
                            return sym
            return None  # pragma: no cover

        # Arrow functions and const-bound function expressions / generator
        # expressions - try variable assignment first, then position lookup.
        # (WI-zavad: ``const f = function () {}`` / ``function* () {}`` attribute
        # their body calls to the variable-named symbol, like arrow functions.)
        if current.type in ("arrow_function", "function_expression", "generator_function"):
            # First, try to find a variable_declarator parent (assigned fn)
            parent = current.parent
            while parent is not None:
                if parent.type == "variable_declarator":
                    for child in parent.children:
                        if child.type == "identifier":
                            name = _node_text(child, source)
                            if name in global_symbols:
                                sym = global_symbols[name]
                                if sym.path == file_path_str:
                                    return sym
                    break  # pragma: no cover
                # Don't go too far up
                if parent.type in ("lexical_declaration", "variable_declaration", "program"):
                    break
                parent = parent.parent

            # If not assigned to variable, try position-based lookup
            # This handles callback arrow functions like route handlers
            if symbol_by_position:
                arrow_line = current.start_point[0] + 1  # 1-indexed
                arrow_col = current.start_point[1]
                position_key = (str(file_path), arrow_line, arrow_col)
                if position_key in symbol_by_position:
                    return symbol_by_position[position_key]

            # Not found by position - continue walking up to find containing
            # named function (e.g., callback inside a named function)
            # Don't return None here; let the loop continue

        current = current.parent
    return None  # pragma: no cover


def _emit_anon_callback_reference_edges(
    call_node: "tree_sitter.Node",
    args_node: Optional["tree_sitter.Node"],
    current_function: Optional[Symbol],
    symbol_by_position: Optional[dict[tuple[str, int, int], Symbol]],
    file_path: Path,
    line_offset: int,
    run: AnalysisRun,
    edges: list[Edge],
) -> None:
    """Emit a companion ``references`` edge from *current_function* to each
    inline anonymous-callback argument of *call_node* (WI-zavad anon-callback
    slice) so the new callback symbols are not dead-code false-positives.

    The bare-identifier callback-reference path covers only *named* callbacks;
    inline anonymous ones (``foo(() => {})``, ``new Promise(cb)``) need this.
    Gated on ``meta.anonymous`` so it fires ONLY for the symbols this slice
    extracts — never for a route-handler or variable-bound arrow that happens
    to occupy the same source position (those keep their existing edges; this
    avoids minting a spurious ``file -> route_handler`` reference that would
    shift in-degree/centrality for every Express inline route handler).
    """
    if (
        args_node is not None
        and symbol_by_position is not None
        and current_function is not None
    ):
        for arg in args_node.children:
            if arg.type not in (
                "arrow_function", "function_expression", "generator_function",
            ):
                continue
            cb_sym = symbol_by_position.get((
                str(file_path),
                arg.start_point[0] + 1 + line_offset,
                arg.start_point[1],
            ))
            if (
                cb_sym is not None
                and (cb_sym.meta or {}).get("anonymous") is True
                and cb_sym.id != current_function.id
            ):
                edges.append(Edge.create(
                    src=current_function.id,
                    dst=cb_sym.id,
                    edge_type="references",
                    line=call_node.start_point[0] + 1 + line_offset,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="callback_argument_reference",
                ))


def _extract_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    lang: str,
    run: AnalysisRun,
    global_symbols: dict[str, Symbol],
    global_methods: dict[str, list[Symbol]],
    global_classes: dict[str, Symbol],
    line_offset: int = 0,
    namespace_imports: dict[str, str] | None = None,
    resolver: NameResolver | None = None,
    method_resolver: ListNameResolver | None = None,
    class_resolver: NameResolver | None = None,
    symbol_by_position: dict[tuple[str, int, int], Symbol] | None = None,
    named_imports: dict[str, str] | None = None,
    symbols_by_name: dict[str, list[Symbol]] | None = None,
    module_symbol: Symbol | None = None,
    named_import_originals: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract edges from a parsed tree (pass 2).

    Uses global symbol registries to resolve cross-file references.
    Uses iterative traversal to avoid RecursionError on deeply nested code.
    Optionally uses NameResolver for suffix-based matching and confidence tracking.
    Uses symbol_by_position to attribute calls inside callback arrow functions.

    Handles:
    - Direct calls: helper(), ClassName()
    - Method calls: this.method(), variable.method() (with type inference)
    - Namespace calls: alias.func(), alias.Class() (via namespace_imports)
    - Object instantiation: new ClassName()

    Type inference tracks types from:
    - Constructor calls: const client = new Client() -> client has type Client
    - Function parameters (TypeScript): function process(client: Client) -> client has type Client

    Type inference does NOT track types from function returns (const client = getClient()).

    Import-path disambiguation (INV-013):
    When multiple files define the same class name (e.g., NestJS monorepos),
    ``named_imports`` and ``symbols_by_name`` are used to pick the correct
    symbol by matching the relative import path against candidate file paths.
    This applies to Cases 1b (this.property.method()) and 3 (variable.method()).
    """
    if namespace_imports is None:
        namespace_imports = {}
    if resolver is None:  # pragma: no cover - defensive
        resolver = NameResolver(global_symbols)
    if method_resolver is None:  # pragma: no cover - defensive
        method_resolver = ListNameResolver(global_methods, ambiguity_threshold=3)
    if class_resolver is None:  # pragma: no cover - defensive
        class_resolver = NameResolver(global_classes)
    _caller_path = str(file_path)
    edges: list[Edge] = []
    # Track variable types for type inference: var_name -> class_name
    var_types: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        # Import statements
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "string":
                    module_name = _node_text(child, source).strip("'\"")
                    file_id = make_file_id(lang, str(file_path))
                    dst_id = f"{lang}:{module_name}:0-0:module:module"
                    edge = Edge.create(
                        src=file_id,
                        dst=dst_id,
                        edge_type="imports",
                        line=node.start_point[0] + 1 + line_offset,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="import_static",
                    )
                    edges.append(edge)
                    break

        # Function/method declarations - extract parameter types for type inference
        # (incl. generator declarations/expressions — WI-zavad parity)
        elif node.type in (
            "function_declaration", "method_definition", "arrow_function",
            "generator_function_declaration", "generator_function",
        ):
            param_types = _extract_param_types(node, source)
            # Add parameter types to var_types for method call resolution
            for param_name, param_type in param_types.items():
                var_types[param_name] = param_type

        # Call expressions
        elif node.type == "call_expression":
            func_node = None
            args_node = None
            for child in node.children:
                if child.type == "identifier":
                    func_node = child
                elif child.type == "member_expression":
                    func_node = child
                elif child.type == "arguments":
                    args_node = child

            # WI-zavad anon-callback slice: IIFE companion edge. When a call's
            # callee is a parenthesized anonymous function ``(function(){})()``,
            # emit a ``calls`` edge from the enclosing scope to the IIFE symbol
            # (it runs immediately at load) so the new symbol is not a dead-code
            # false-positive and its invocation is visible in the graph.
            iife_callee = node.child_by_field_name("function")
            if (
                iife_callee is not None
                and iife_callee.type == "parenthesized_expression"
                and symbol_by_position is not None
            ):
                for inner in iife_callee.children:
                    if inner.type in (
                        "arrow_function", "function_expression",
                        "generator_function",
                    ):
                        iife_sym = symbol_by_position.get((
                            str(file_path),
                            inner.start_point[0] + 1 + line_offset,
                            inner.start_point[1],
                        ))
                        if iife_sym is not None and (iife_sym.meta or {}).get("anonymous") is True:
                            enclosing = _get_enclosing_function(
                                node, source, file_path, global_symbols,
                                symbol_by_position, line_offset,
                            ) or module_symbol
                            if enclosing is not None:
                                edges.append(Edge.create(
                                    src=enclosing.id,
                                    dst=iife_sym.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1 + line_offset,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_call_direct",
                                ))
                        break

            # Require calls
            if func_node and func_node.type == "identifier":
                func_name = _node_text(func_node, source)
                if func_name == "require" and args_node:
                    for arg in args_node.children:
                        if arg.type == "string":
                            module_name = _node_text(arg, source).strip("'\"")
                            file_id = make_file_id(lang, str(file_path))
                            dst_id = f"{lang}:{module_name}:0-0:module:module"
                            edge = Edge.create(
                                src=file_id,
                                dst=dst_id,
                                edge_type="imports",
                                line=node.start_point[0] + 1 + line_offset,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="require_static",
                            )
                            edges.append(edge)
                            break
                        elif arg.type == "identifier":
                            var_name = _node_text(arg, source)
                            file_id = make_file_id(lang, str(file_path))
                            dst_id = f"{lang}:<dynamic:{var_name}>:0-0:module:module"
                            edge = Edge.create(
                                src=file_id,
                                dst=dst_id,
                                edge_type="imports",
                                line=node.start_point[0] + 1 + line_offset,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="require_dynamic",
                            )
                            edges.append(edge)
                            break
                else:
                    # Regular function call - use resolver for suffix matching
                    # Skip JS built-in names to prevent false edges to
                    # user-defined functions that shadow built-ins
                    # (e.g., Number(x) → React component named Number).
                    if func_name in JS_BUILTIN_NAMES:
                        pass  # fall through to callback/middleware handling below
                    elif _is_shadowed_by_param(node, func_name, source):
                        pass  # local parameter shadows global — skip resolution
                    elif (current_function := (_get_enclosing_function(node, source, file_path, global_symbols, symbol_by_position, line_offset) or module_symbol)):
                        # Try import-path disambiguation first (cross-package
                        # same-name functions, e.g. two packages both export
                        # ``process()`` but main.js imports from one specific
                        # module).  Falls back to resolver if no named import
                        # exists for this function name.
                        callee = None
                        edge_confidence = 0.85
                        import_module = (named_imports or {}).get(func_name)
                        if import_module and symbols_by_name:
                            callee = _disambiguate_by_import(
                                import_module, file_path, func_name, symbols_by_name,
                            )
                            if callee is not None:
                                edge_confidence = 0.90  # explicit import match
                        # Try same-package preference (avoids cross-package
                        # false positives for common names like error,
                        # resolve, reject when there's no import).
                        if callee is None and symbols_by_name:
                            callee = _same_package_candidate(
                                file_path, func_name, symbols_by_name,
                            )
                            if callee is not None:
                                edge_confidence = 0.85  # same-package heuristic
                        # INV-fahub: a bare ``foo()`` that resolves only via a
                        # weak short-name SUFFIX to a DIFFERENT class's method is
                        # the magnet (dozens of call sites -> one arbitrary
                        # ``Beta.persist``). Withhold that bind and stamp the
                        # enclosing class so the inherited_calls Site-1 walker can
                        # recover a genuine inherited implicit-``this`` call; free
                        # functions, same-class methods, and exact matches bind.
                        magnet_deferred = False
                        if callee is None:
                            lookup_result = resolver.lookup(func_name, caller_path=_caller_path)
                            if lookup_result.found and lookup_result.symbol is not None:
                                # Cross-package guard: the resolver fallback
                                # should not cross npm packages (import-path
                                # and same-package checks above already had
                                # their chance to find a valid callee).
                                if not _is_cross_package(
                                    file_path, lookup_result.symbol.path,
                                ):
                                    _sym = lookup_result.symbol
                                    _enclosing_type = _jsts_enclosing_class(node, source)
                                    if defer_bare_method_call(
                                        _sym.kind, _sym.name,
                                        lookup_result.match_type, _enclosing_type,
                                    ):
                                        edges.append(make_unresolved_edge(
                                            lang, current_function.id, func_name,
                                            node.start_point[0] + 1 + line_offset,
                                            PASS_ID, run.execution_id,
                                            enclosing_class=_enclosing_type,
                                        ))
                                        magnet_deferred = True
                                    else:
                                        callee = _sym
                                        edge_confidence = 0.85 * lookup_result.confidence
                        if callee is not None:
                            edge = Edge.create(
                                src=current_function.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1 + line_offset,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_call_direct",
                                confidence=edge_confidence,
                            )
                            edges.append(edge)
                        elif magnet_deferred:
                            # Deferred to Site-1 above (unresolved edge already
                            # emitted with the enclosing_class hint); do not also
                            # emit a named-import / known-global fallback edge.
                            pass
                        elif (named_imports or {}).get(func_name):
                            # WI-banaf: when a named-imported function is
                            # called but doesn't resolve to an intra-repo
                            # symbol (the common case for Node/browser
                            # built-ins like ``existsSync`` from ``node:fs``),
                            # emit an unresolved-call edge with the import
                            # path as the module hint. The io-boundaries
                            # layer matches the callee name against the
                            # JavaScript catalog and tags the edge.
                            #
                            # WI-kujom: prefer the original imported name
                            # (``writeFile``) over the local alias (``wf``)
                            # when ``import { writeFile as wf }`` was used.
                            # Cross-language linkers and io-boundary catalogs
                            # key on the canonical name, not the alias.
                            module_hint = _normalize_import_module_hint(
                                named_imports[func_name]
                            )
                            canonical_name = (
                                named_import_originals or {}
                            ).get(func_name, func_name)
                            dst_id = f"{lang}:{module_hint}:0-0:{canonical_name}:unresolved"
                            edge = Edge.create(
                                src=current_function.id,
                                dst=dst_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1 + line_offset,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_call_direct",
                                is_resolved=False,
                                dst_ref=ExternalRef(
                                    lang=lang,
                                    module_path=module_hint,
                                    name=canonical_name,
                                ),
                            )
                            edges.append(edge)
                        elif func_name in JS_KNOWN_GLOBAL_CALLS:
                            # Bare global I/O function (``fetch(url)``) that did
                            # not resolve intra-repo and was not imported: emit
                            # an unresolved-call edge whose module hint AND name
                            # are the function itself, matching the catalog's
                            # ``module: fetch, functions: [fetch]`` shape so the
                            # io-boundaries layer can tag the network call
                            # (WI-zavad / emission-parity F2).
                            dst_id = f"{lang}:{func_name}:0-0:{func_name}:unresolved"
                            edge = Edge.create(
                                src=current_function.id,
                                dst=dst_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1 + line_offset,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_call_direct",
                                is_resolved=False,
                                dst_ref=ExternalRef(
                                    lang=lang,
                                    module_path=func_name,
                                    name=func_name,
                                ),
                            )
                            edges.append(edge)

                        if callee is not None:
                            # Return type inference: if function has a return
                            # type annotation, track the variable's type
                            if callee.kind in ("function", "method"):
                                ret_name = _extract_jsts_return_type_name(
                                    callee.signature
                                )
                                if ret_name and node.parent and node.parent.type == "variable_declarator":
                                    # Check return type is a known class
                                    class_result = class_resolver.lookup(ret_name)
                                    if class_result.found:
                                        for pc in node.parent.children:
                                            if pc.type == "identifier":
                                                var_types[_node_text(pc, source)] = ret_name
                                                break

            # Method calls: obj.method()
            if func_node and func_node.type == "member_expression":
                current_function = _get_enclosing_function(node, source, file_path, global_symbols, symbol_by_position, line_offset) or module_symbol
                if current_function:
                    method_name = None
                    obj_node = None
                    for child in func_node.children:
                        if child.type == "property_identifier":
                            method_name = _node_text(child, source)
                        elif child.type in ("identifier", "this", "member_expression"):
                            obj_node = child

                    if method_name:
                        is_this_call = obj_node and obj_node.type == "this"
                        current_class_name = _get_class_context(node, source)
                        obj_name = _node_text(obj_node, source) if obj_node and obj_node.type == "identifier" else None
                        edge_added = False

                        # Case 1: this.method()
                        if is_this_call and current_class_name:
                            full_name = f"{current_class_name}.{method_name}"
                            lookup_result = resolver.lookup(full_name, caller_path=_caller_path)
                            if lookup_result.found and lookup_result.symbol is not None:
                                edge = Edge.create(
                                    src=current_function.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1 + line_offset,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_method_this",
                                    confidence=0.95 * lookup_result.confidence,
                                )
                                edges.append(edge)
                                edge_added = True

                        # Case 1b: this.property.method() via constructor injection
                        # Handles NestJS/Angular patterns like this.catsService.create()
                        # where catsService is a constructor-injected dependency
                        elif obj_node and obj_node.type == "member_expression":
                            # Check if it's this.property pattern
                            this_node = None
                            property_name = None
                            for mc in obj_node.children:
                                if mc.type == "this":
                                    this_node = mc
                                elif mc.type == "property_identifier":
                                    property_name = _node_text(mc, source)
                            # If we have this.propertyName and propertyName has a known type
                            if this_node and property_name and property_name in var_types:
                                type_class_name = var_types[property_name]
                                full_name = f"{type_class_name}.{method_name}"
                                # Try import-path disambiguation first (monorepo duplicate names)
                                import_module = (named_imports or {}).get(type_class_name)
                                callee = None
                                if import_module and symbols_by_name:
                                    callee = _disambiguate_by_import(
                                        import_module, file_path, full_name, symbols_by_name,
                                    )
                                if callee is None:
                                    lookup_result = resolver.lookup(full_name, caller_path=_caller_path)
                                    if lookup_result.found and lookup_result.symbol is not None:
                                        callee = lookup_result.symbol
                                if callee is not None:
                                    edge = Edge.create(
                                        src=current_function.id,
                                        dst=callee.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1 + line_offset,
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                        evidence_type="ast_method_this_property",
                                    )
                                    edges.append(edge)
                                    edge_added = True

                        # Case 2: alias.func() via namespace import
                        elif obj_name and obj_name in namespace_imports:
                            # This is a namespace call: alias.func() or alias.Class()
                            # Resolve via global symbols using import path as hint
                            # to disambiguate when same name exists in multiple modules
                            import_path = namespace_imports[obj_name]
                            lookup_result = resolver.lookup(method_name, path_hint=import_path, caller_path=_caller_path)
                            if lookup_result.found and lookup_result.symbol is not None:
                                # Cross-package guard: block resolution when
                                # the target lives in a different npm package.
                                if _is_cross_package(file_path, lookup_result.symbol.path):
                                    pass  # suppress cross-package namespace call
                                else:
                                    is_class = lookup_result.symbol.kind == "class"
                                    edge = Edge.create(
                                        src=current_function.id,
                                        dst=lookup_result.symbol.id,
                                        edge_type="instantiates" if is_class else "calls",
                                        line=node.start_point[0] + 1 + line_offset,
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                        evidence_type="ast_new" if is_class else "ast_call_namespace",
                                        confidence=0.90 * lookup_result.confidence,
                                    )
                                    edges.append(edge)
                                    edge_added = True
                            if not edge_added:
                                # WI-vurop: fall back to an unresolved-call
                                # edge so io-boundaries can match the catalog
                                # against Node built-ins and third-party HTTP
                                # clients imported via ``import * as fs`` or
                                # ``import axios from 'axios'``.
                                module_hint = _normalize_import_module_hint(import_path)
                                dst_id = f"{lang}:{module_hint}:0-0:{method_name}:unresolved"
                                edge = Edge.create(
                                    src=current_function.id,
                                    dst=dst_id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1 + line_offset,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_method_inferred",
                                    is_resolved=False,
                                )
                                edges.append(edge)
                                edge_added = True

                        # Case 3: variable.method() via type inference
                        elif obj_name and obj_name in var_types:
                            type_class_name = var_types[obj_name]
                            full_name = f"{type_class_name}.{method_name}"
                            # Try import-path disambiguation first (monorepo duplicate names)
                            import_module = (named_imports or {}).get(type_class_name)
                            callee = None
                            if import_module and symbols_by_name:
                                callee = _disambiguate_by_import(
                                    import_module, file_path, full_name, symbols_by_name,
                                )
                            if callee is None:
                                lookup_result = resolver.lookup(full_name, caller_path=_caller_path)
                                if lookup_result.found and lookup_result.symbol is not None:
                                    callee = lookup_result.symbol
                            if callee is not None:
                                edge = Edge.create(
                                    src=current_function.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1 + line_offset,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_method_type_inferred",
                                )
                                edges.append(edge)
                                edge_added = True

                        # Case 3b (WI-pinop): bare global ``Object.method()`` not
                        # shadowed by an import or a locally-typed variable.
                        # Common in browser projects that never import console,
                        # localStorage, navigator, window, document, or Deno.
                        # Emits an unresolved-call edge with the global name as
                        # module hint so the io-boundaries layer can match the
                        # catalog (javascript.yaml uses the same module names).
                        #
                        # Shadowing checks: a named/namespace import or a
                        # typed-parameter/var binding with the same name must
                        # route through the existing cases, never this global
                        # fallback. (Namespace shadowing is guaranteed by the
                        # Case 2 elif — if obj_name is in namespace_imports the
                        # WI-vurop fallback already set edge_added.)
                        if (
                            not edge_added
                            and obj_name
                            and obj_name in JS_KNOWN_GLOBALS
                            and obj_name not in (named_imports or {})
                            and obj_name not in var_types
                        ):
                            dst_id = f"{lang}:{obj_name}:0-0:{method_name}:unresolved"
                            edge = Edge.create(
                                src=current_function.id,
                                dst=dst_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1 + line_offset,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_method_inferred",
                                is_resolved=False,
                            )
                            edges.append(edge)
                            edge_added = True

                        # Case 4: Fallback - method name match with low confidence.
                        # Emit only one edge to the best candidate (not all
                        # candidates) to avoid name-collision fanout where
                        # every class with the same method name gets linked.
                        # Skip built-in method names (get, set, forEach, etc.)
                        # that exist on Array/Map/Set/Object — resolving these
                        # to user-defined classes inflates in-degree.
                        if not edge_added and method_name not in JS_BUILTIN_METHODS:
                            lookup_result = method_resolver.lookup(method_name)
                            if lookup_result.found and lookup_result.symbol is not None:
                                # Cross-package guard: low-confidence method
                                # inference should not cross npm packages.
                                if not _is_cross_package(file_path, lookup_result.symbol.path):
                                    _sym = lookup_result.symbol
                                    _enclosing_type = _jsts_enclosing_class(node, source)
                                    # INV-fahub: the untyped ``obj.method()``
                                    # fanout — an AMBIGUOUS (2-way) short-name
                                    # match to an UNRELATED class's method is the
                                    # magnet. Withhold it and stamp the enclosing
                                    # class for Site-1 recovery; a single-candidate
                                    # (``exact``) match and a same-class method
                                    # still bind.
                                    if defer_bare_method_call(
                                        _sym.kind, _sym.name,
                                        lookup_result.match_type, _enclosing_type,
                                    ):
                                        edges.append(make_unresolved_edge(
                                            lang, current_function.id, method_name,
                                            node.start_point[0] + 1 + line_offset,
                                            PASS_ID, run.execution_id,
                                            enclosing_class=_enclosing_type,
                                        ))
                                    else:
                                        edge = Edge.create(
                                            src=current_function.id,
                                            dst=_sym.id,
                                            edge_type="calls",
                                            line=node.start_point[0] + 1 + line_offset,
                                            origin=PASS_ID,
                                            origin_run_id=run.execution_id,
                                            evidence_type="ast_method_inferred",
                                            confidence=0.60 * lookup_result.confidence,
                                        )
                                        edges.append(edge)

            # Callback argument references: func(handler) or app.get("/path", handler)
            # When a bare identifier in the arguments resolves to a function,
            # create a references edge. Common with Express route handlers,
            # Array.forEach/map callbacks, and event listener patterns.
            if args_node is not None:
                current_function = _get_enclosing_function(
                    node, source, file_path, global_symbols,
                    symbol_by_position, line_offset,
                ) or module_symbol
                if current_function is not None:
                    for arg in args_node.children:
                        if arg.type != "identifier":
                            continue
                        arg_name = _node_text(arg, source)
                        if arg_name in JS_BUILTIN_NAMES:
                            continue
                        # Parameter shadowing: skip if arg name matches a
                        # param of an enclosing function (e.g., resolve
                        # passed to forEach inside a Promise callback).
                        if _is_shadowed_by_param(arg, arg_name, source):
                            continue
                        # Same resolution strategy as direct calls:
                        # import-path first, then same-package, then global
                        target: Symbol | None = None
                        import_module = (named_imports or {}).get(arg_name)
                        if import_module and symbols_by_name:
                            target = _disambiguate_by_import(
                                import_module, file_path, arg_name, symbols_by_name,
                            )
                        if target is None and symbols_by_name:
                            target = _same_package_candidate(
                                file_path, arg_name, symbols_by_name,
                            )
                        if target is None:
                            target = global_symbols.get(arg_name)
                        if target is None:  # pragma: no cover - defensive resolver fallback
                            lookup_result = resolver.lookup(arg_name, caller_path=_caller_path)
                            if lookup_result.found and lookup_result.symbol is not None:
                                target = lookup_result.symbol
                        # Route symbols can shadow function symbols in
                        # global_symbols (last-one-wins).
                        #
                        # WI-zugob: a route-marker disambiguation used to live
                        # here — when this name lookup returned a route rather
                        # than the handler, it preferred the function with the
                        # same name. That collision was only possible because
                        # route markers were NAMED AFTER THEIR HANDLER, which is
                        # the defect the make_route_symbol migration removed: a
                        # marker is now "{METHOD} {path}", which is never a valid
                        # identifier, so an identifier lookup can no longer
                        # return one. The branch became unreachable and is gone.
                        if (
                            target is not None
                            and target.kind in ("function", "method", "route")
                            and target.id != current_function.id
                        ):
                            # Cross-package guard: callback args that resolve
                            # to a different npm package without an explicit
                            # import are likely false positives (e.g., error()
                            # in client-admin resolving as a callback ref from
                            # server code that passes error as argument).
                            if _is_cross_package(file_path, target.path):
                                continue
                            # Avoid duplicate: skip if we already created a
                            # direct call edge to the same target (the callee
                            # identifier itself is also an argument child).
                            if func_node is not None and _node_text(func_node, source) == arg_name:  # pragma: no cover - dedup with direct call
                                continue
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=target.id,
                                edge_type="references",
                                line=node.start_point[0] + 1 + line_offset,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="callback_argument_reference",
                            ))
                    # WI-zavad anon-callback slice: companion references edge for
                    # inline anonymous callbacks (see helper).
                    _emit_anon_callback_reference_edges(
                        node, args_node, current_function, symbol_by_position,
                        file_path, line_offset, run, edges,
                    )

            # Middleware chain edges: for Express-style route registrations
            # with multiple middleware/handler arguments, create edges between
            # consecutive handlers so the execution pipeline is visible in
            # forward/reverse slices.
            # Example: app.post('/path', auth, validate, handler) creates
            #   auth→validate and validate→handler edges.
            if args_node is not None:
                http_method, route_path = _detect_route_call(node, source)
                if http_method is not None:
                    # Collect middleware/handler arguments (skip path string, parens, commas)
                    chain_symbols: list[Symbol] = []
                    for arg in args_node.children:
                        if arg.type in ("(", ")", ",", "string", "template_string"):
                            continue
                        resolved: Symbol | None = None
                        if arg.type == "identifier":
                            arg_name = _node_text(arg, source)
                            # WI-zugob: the sibling route-marker
                            # disambiguation is gone for the same reason as
                            # above — "{METHOD} {path}" cannot collide with an
                            # identifier.
                            resolved = global_symbols.get(arg_name)
                        elif arg.type == "call_expression":
                            # Factory call like need('txt') — resolve the
                            # factory function itself.
                            for child in arg.children:
                                if child.type == "identifier":
                                    fn_name = _node_text(child, source)
                                    resolved = global_symbols.get(fn_name)
                                    break
                        if resolved is not None and resolved.kind in ("function", "method", "route"):
                            # Cross-package guard: middleware chain symbols
                            # should come from the same package as the
                            # route registration file.
                            if not _is_cross_package(file_path, resolved.path):
                                chain_symbols.append(resolved)
                    # Create edges between consecutive chain entries
                    for i in range(len(chain_symbols) - 1):
                        src_sym = chain_symbols[i]
                        dst_sym = chain_symbols[i + 1]
                        if src_sym.id != dst_sym.id:
                            edges.append(Edge.create(
                                src=src_sym.id,
                                dst=dst_sym.id,
                                edge_type="references",
                                line=node.start_point[0] + 1 + line_offset,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_call_direct",
                                meta={"framework_dispatch": "middleware_chain"},
                            ))

        # new ClassName() or new namespace.ClassName()
        elif node.type == "new_expression":
            current_function = _get_enclosing_function(node, source, file_path, global_symbols, symbol_by_position, line_offset) or module_symbol
            class_name = None
            target_sym = None
            lookup_confidence = 1.0  # Default for exact match
            ns_import_path: str | None = None  # Path hint for namespace imports

            for child in node.children:
                if child.type == "identifier":
                    # new ClassName()
                    class_name = _node_text(child, source)
                    break
                elif child.type == "member_expression":
                    # new namespace.ClassName()
                    ns_name = None
                    cls_name = None
                    for mc in child.children:
                        if mc.type == "identifier":
                            ns_name = _node_text(mc, source)
                        elif mc.type == "property_identifier":
                            cls_name = _node_text(mc, source)
                    if ns_name and ns_name in namespace_imports and cls_name:
                        class_name = cls_name
                        # Track import path for disambiguation
                        ns_import_path = namespace_imports[ns_name]
                    break

            # Resolve class via class_resolver, using import path for disambiguation
            if class_name:
                lookup_result = class_resolver.lookup(class_name, path_hint=ns_import_path)
                if lookup_result.found and lookup_result.symbol is not None:
                    target_sym = lookup_result.symbol
                    lookup_confidence = lookup_result.confidence

            # Emit instantiates edge
            if current_function and target_sym:
                edge = Edge.create(
                    src=current_function.id,
                    dst=target_sym.id,
                    edge_type="instantiates",
                    line=node.start_point[0] + 1 + line_offset,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_new",
                    confidence=0.95 * lookup_confidence,
                )
                edges.append(edge)

            # WI-zavad anon-callback slice: companion references edge for an
            # inline anonymous callback passed to a constructor — the canonical
            # ``new Promise((res, rej) => {})`` / ``new Observable(cb)`` form.
            _emit_anon_callback_reference_edges(
                node, node.child_by_field_name("arguments"), current_function,
                symbol_by_position, file_path, line_offset, run, edges,
            )

            # Track variable type for type inference
            # Check if this new_expression is part of a variable assignment
            if class_name and node.parent:
                parent = node.parent
                if parent.type == "variable_declarator":
                    # Find variable name
                    for pc in parent.children:
                        if pc.type == "identifier":
                            var_name = _node_text(pc, source)
                            var_types[var_name] = class_name
                            break

        # Object literal function references: {onClick: handleClick}
        # AST: pair → property_identifier : identifier
        # When the value is a bare identifier that resolves to a function,
        # create a references edge. Common in React, Express config, etc.
        elif node.type == "pair":
            value_node = None
            seen_colon = False
            for pair_child in node.children:
                if pair_child.type == ":":
                    seen_colon = True
                elif seen_colon and pair_child.type == "identifier":
                    value_node = pair_child
                    break

            if value_node is not None:
                ref_name = _node_text(value_node, source)
                target = global_symbols.get(ref_name)
                if target is None:  # pragma: no cover - defensive resolver fallback
                    lookup_result = resolver.lookup(ref_name, caller_path=_caller_path)
                    if lookup_result.found and lookup_result.symbol is not None:
                        target = lookup_result.symbol
                # Cross-package guard: object field refs should not
                # cross npm package boundaries.
                if (
                    target is not None
                    and target.kind in ("function", "method")
                    and not _is_cross_package(file_path, target.path)
                ):
                    current_function = _get_enclosing_function(
                        node, source, file_path, global_symbols,
                        symbol_by_position, line_offset,
                    ) or module_symbol
                    if current_function is not None and target.id != current_function.id:
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=target.id,
                            edge_type="references",
                            line=node.start_point[0] + 1 + line_offset,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="object_field_reference",
                        ))

        # Shorthand property: {handleClick} — equivalent to {handleClick: handleClick}
        elif node.type == "shorthand_property_identifier":
            ref_name = _node_text(node, source)
            target = global_symbols.get(ref_name)
            if target is None:  # pragma: no cover - defensive resolver fallback
                lookup_result = resolver.lookup(ref_name, caller_path=_caller_path)
                if lookup_result.found and lookup_result.symbol is not None:
                    target = lookup_result.symbol
            # Cross-package guard: shorthand props should not cross packages
            if (
                target is not None
                and target.kind in ("function", "method")
                and not _is_cross_package(file_path, target.path)
            ):
                current_function = _get_enclosing_function(
                    node, source, file_path, global_symbols,
                    symbol_by_position, line_offset,
                ) or module_symbol
                if current_function is not None and target.id != current_function.id:
                    edges.append(Edge.create(
                        src=current_function.id,
                        dst=target.id,
                        edge_type="references",
                        line=node.start_point[0] + 1 + line_offset,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="object_field_reference",
                    ))

    return edges


def _extract_symbols_and_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    lang: str,
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge]]:
    """Extract symbols and edges from a parsed tree (legacy single-file).

    This function is kept for backwards compatibility with single-file analysis.
    For cross-file resolution, use the two-pass approach in analyze_javascript.
    """
    symbols = _extract_symbols(tree, source, file_path, lang, run)
    populate_docstrings_from_tree(tree.root_node, source, symbols)

    # Build local symbol registry
    global_symbols: dict[str, Symbol] = {}
    global_methods: dict[str, list[Symbol]] = {}
    global_classes: dict[str, Symbol] = {}

    for sym in symbols:
        global_symbols[sym.name] = sym
        if sym.kind == "method":
            method_name = sym.name.split(".")[-1] if "." in sym.name else sym.name
            if method_name not in global_methods:
                global_methods[method_name] = []
            global_methods[method_name].append(sym)
        elif sym.kind == "class":
            global_classes[sym.name] = sym

    # INV-kokaj: find the file pseudo-node (kind="file") for top-level
    # call attribution. Filtered by language because non-JS/TS file
    # Symbols may appear in this list when called from polyglot wrappers.
    mod_sym = next(
        (s for s in symbols if s.kind == "file" and s.language == lang),
        None,
    )
    edges = _extract_edges(tree, source, file_path, lang, run, global_symbols, global_methods, global_classes,
                           module_symbol=mod_sym)
    return symbols, edges


def _get_parser_for_lang(is_typescript: bool) -> Optional["tree_sitter.Parser"]:
    """Get tree-sitter parser for TypeScript or JavaScript."""
    try:
        import tree_sitter
        import tree_sitter_javascript
    except ImportError:
        return None

    parser = tree_sitter.Parser()

    if is_typescript:
        try:
            import tree_sitter_typescript

            lang_ptr = tree_sitter_typescript.language_typescript()
            parser.language = tree_sitter.Language(lang_ptr)
            return parser
        except ImportError:
            # Fall back to JavaScript parser
            parser.language = tree_sitter.Language(tree_sitter_javascript.language())
            return parser
    else:
        parser.language = tree_sitter.Language(tree_sitter_javascript.language())
        return parser


def _analyze_svelte_file(
    file_path: Path,
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge], bool]:
    """Analyze a Svelte file by extracting and parsing <script> blocks.

    Returns (symbols, edges, success).
    """
    try:
        source_text = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, IOError):
        return [], [], False

    script_blocks = extract_svelte_scripts(source_text)
    if not script_blocks:
        # No script blocks found - not an error, just empty
        return [], [], True

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for block in script_blocks:
        parser = _get_parser_for_lang(block.is_typescript)
        if parser is None:
            continue

        source_bytes = block.content.encode("utf-8")
        tree = parser.parse(source_bytes)

        lang = "typescript" if block.is_typescript else "javascript"
        line_offset = block.start_line - 1

        symbols = _extract_symbols(tree, source_bytes, file_path, lang, run, line_offset)
        populate_docstrings_from_tree(tree.root_node, source_bytes, symbols)

        # Build local symbol registry for this block
        local_symbols: dict[str, Symbol] = {}
        local_methods: dict[str, list[Symbol]] = {}
        local_classes: dict[str, Symbol] = {}

        for sym in symbols:
            local_symbols[sym.name] = sym
            if sym.kind == "method":
                method_name = sym.name.split(".")[-1] if "." in sym.name else sym.name
                if method_name not in local_methods:
                    local_methods[method_name] = []
                local_methods[method_name].append(sym)
            elif sym.kind == "class":
                local_classes[sym.name] = sym

        edges = _extract_edges(
            tree, source_bytes, file_path, lang, run,
            local_symbols, local_methods, local_classes, line_offset
        )

        all_symbols.extend(symbols)
        all_edges.extend(edges)

    return all_symbols, all_edges, True


def _analyze_vue_file(
    file_path: Path,
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge], bool]:
    """Analyze a Vue SFC file by extracting and parsing <script> blocks.

    Returns (symbols, edges, success).
    """
    try:
        source_text = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, IOError):
        return [], [], False

    script_blocks = extract_vue_scripts(source_text)
    if not script_blocks:
        # No script blocks found - not an error, just empty
        return [], [], True

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for block in script_blocks:
        parser = _get_parser_for_lang(block.is_typescript)
        if parser is None:
            continue

        source_bytes = block.content.encode("utf-8")
        tree = parser.parse(source_bytes)

        lang = "typescript" if block.is_typescript else "javascript"
        line_offset = block.start_line - 1

        symbols = _extract_symbols(tree, source_bytes, file_path, lang, run, line_offset)
        populate_docstrings_from_tree(tree.root_node, source_bytes, symbols)

        # Build local symbol registry for this block
        local_symbols: dict[str, Symbol] = {}
        local_methods: dict[str, list[Symbol]] = {}
        local_classes: dict[str, Symbol] = {}

        for sym in symbols:
            local_symbols[sym.name] = sym
            if sym.kind == "method":
                method_name = sym.name.split(".")[-1] if "." in sym.name else sym.name
                if method_name not in local_methods:
                    local_methods[method_name] = []
                local_methods[method_name].append(sym)
            elif sym.kind == "class":
                local_classes[sym.name] = sym

        edges = _extract_edges(
            tree, source_bytes, file_path, lang, run,
            local_symbols, local_methods, local_classes, line_offset
        )

        all_symbols.extend(symbols)
        all_edges.extend(edges)

    return all_symbols, all_edges, True


@register_analyzer(
    "javascript",
    supports_max_files=True,
    languages=["javascript", "typescript", "vue", "svelte"],
)
def analyze_javascript(
    repo_root: Path, max_files: int | None = None
) -> JsAnalysisResult:
    """Analyze all JavaScript/TypeScript/Svelte/Vue files in a repository.

    Uses a two-pass approach:
    1. Parse all files and extract symbols into global registry
    2. Detect calls and resolve against global symbol registry

    Returns a JsAnalysisResult with symbols, edges, and provenance.
    If tree-sitter is not available, returns empty result with skip info.

    Args:
        repo_root: Root directory of the repository
        max_files: Optional limit on number of files to analyze
    """
    return _jsts_analyzer.analyze(repo_root, max_files=max_files)


def _analyze_javascript_impl(
    repo_root: Path, max_files: int | None = None
) -> JsAnalysisResult:
    """Internal implementation of JS/TS analysis.

    Called by JstsTreeSitterAnalyzer.analyze() after grammar availability
    has been checked by the base class.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Check for tree-sitter availability
    if not _jsts_analyzer._check_grammar_available():
        skip_reason = "javascript analysis skipped: grammar not available. " \
                      "Install the required tree-sitter grammar package."
        warnings.warn(skip_reason, UserWarning, stacklevel=3)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return JsAnalysisResult(
            run=run,
            skipped=True,
            skip_reason="javascript tree-sitter grammar not available",
        )

    # Pass 1: Parse all files and extract symbols
    parsed_files: list[_ParsedFile] = []
    all_symbols: list[Symbol] = []
    files_analyzed = 0
    files_skipped = 0

    # Analyze JS/TS files
    for file_path in find_js_ts_files(repo_root, max_files=max_files):
        parser = _get_parser_for_file(file_path)
        if parser is None:
            files_skipped += 1
            continue

        try:
            source = file_path.read_bytes()
            # Skip binary files: .ts extension is ambiguous between TypeScript
            # and MPEG Transport Stream.  Null bytes in the first 8 KB indicate
            # binary content (same heuristic git uses).
            if b"\x00" in source[:8192]:
                files_skipped += 1
                continue
            tree = parser.parse(source)
            lang = _get_language_for_file(file_path)
            ns_imports = _extract_namespace_imports(tree, source)
            nm_imports, nm_originals = _extract_named_imports(tree, source)
            parsed_files.append(_ParsedFile(
                path=file_path, tree=tree, source=source, lang=lang,
                namespace_imports=ns_imports, named_imports=nm_imports,
                named_import_originals=nm_originals,
            ))
            symbols = _extract_symbols(tree, source, file_path, lang, run, repo_root=repo_root)
            populate_docstrings_from_tree(tree.root_node, source, symbols)
            all_symbols.extend(symbols)
            files_analyzed += 1
        except (OSError, IOError) as e:  # pragma: no cover - IO errors hard to trigger in tests
            files_skipped += 1
            run.record_failed_file(str(file_path.relative_to(repo_root)), f"{type(e).__name__}: {e}")

    # Analyze Svelte files
    for file_path in find_svelte_files(repo_root, max_files=max_files):
        try:
            source_text = file_path.read_text(encoding="utf-8", errors="replace")
            script_blocks = extract_svelte_scripts(source_text)
            if not script_blocks:
                files_analyzed += 1
                continue

            for block in script_blocks:
                parser = _get_parser_for_lang(block.is_typescript)
                if parser is None:
                    continue

                source_bytes = block.content.encode("utf-8")
                tree = parser.parse(source_bytes)
                lang = "typescript" if block.is_typescript else "javascript"
                line_offset = block.start_line - 1
                ns_imports = _extract_namespace_imports(tree, source_bytes)
                nm_imports, nm_originals = _extract_named_imports(tree, source_bytes)

                parsed_files.append(_ParsedFile(
                    path=file_path, tree=tree, source=source_bytes,
                    lang=lang, line_offset=line_offset, namespace_imports=ns_imports,
                    named_imports=nm_imports,
                    named_import_originals=nm_originals,
                ))
                symbols = _extract_symbols(tree, source_bytes, file_path, lang, run, line_offset, repo_root=repo_root)
                populate_docstrings_from_tree(tree.root_node, source_bytes, symbols)
                all_symbols.extend(symbols)

            files_analyzed += 1
        except (OSError, IOError) as e:  # pragma: no cover - IO errors hard to trigger in tests
            files_skipped += 1
            run.record_failed_file(str(file_path.relative_to(repo_root)), f"{type(e).__name__}: {e}")

    # Analyze Vue SFC files
    for file_path in find_vue_files(repo_root, max_files=max_files):
        try:
            source_text = file_path.read_text(encoding="utf-8", errors="replace")
            script_blocks = extract_vue_scripts(source_text)
            if not script_blocks:
                files_analyzed += 1
                continue

            for block in script_blocks:
                parser = _get_parser_for_lang(block.is_typescript)
                if parser is None:
                    continue

                source_bytes = block.content.encode("utf-8")
                tree = parser.parse(source_bytes)
                lang = "typescript" if block.is_typescript else "javascript"
                line_offset = block.start_line - 1
                ns_imports = _extract_namespace_imports(tree, source_bytes)
                nm_imports, nm_originals = _extract_named_imports(tree, source_bytes)

                parsed_files.append(_ParsedFile(
                    path=file_path, tree=tree, source=source_bytes,
                    lang=lang, line_offset=line_offset, namespace_imports=ns_imports,
                    named_imports=nm_imports,
                    named_import_originals=nm_originals,
                ))
                symbols = _extract_symbols(tree, source_bytes, file_path, lang, run, line_offset, repo_root=repo_root)
                populate_docstrings_from_tree(tree.root_node, source_bytes, symbols)
                all_symbols.extend(symbols)

            files_analyzed += 1
        except (OSError, IOError) as e:  # pragma: no cover - IO errors hard to trigger in tests
            files_skipped += 1
            run.record_failed_file(str(file_path.relative_to(repo_root)), f"{type(e).__name__}: {e}")

    # Build global symbol registries
    global_symbols: dict[str, Symbol] = {}
    global_methods: dict[str, list[Symbol]] = {}
    global_classes: dict[str, Symbol] = {}
    # All symbols indexed by name (supports multiple with same name for disambiguation)
    symbols_by_name: dict[str, list[Symbol]] = {}
    # Position-based lookup for inline route handlers: (file_path, start_line, start_col) -> Symbol
    symbol_by_position: dict[tuple[str, int, int], Symbol] = {}

    for sym in all_symbols:
        global_symbols[sym.name] = sym
        # Multi-value index for import-path disambiguation
        if sym.name not in symbols_by_name:
            symbols_by_name[sym.name] = []
        symbols_by_name[sym.name].append(sym)
        # Index by position for inline handler lookup in UsageContext creation
        symbol_by_position[(sym.path, sym.span.start_line, sym.span.start_col)] = sym
        if sym.kind == "method":
            method_name = sym.name.split(".")[-1] if "." in sym.name else sym.name
            if method_name not in global_methods:
                global_methods[method_name] = []
            global_methods[method_name].append(sym)
        elif sym.kind == "class":
            global_classes[sym.name] = sym

    # Pass 2: Extract edges using global symbol registry
    resolver = NameResolver(global_symbols)
    method_resolver = ListNameResolver(global_methods, ambiguity_threshold=3)
    class_resolver = NameResolver(global_classes)
    all_edges: list[Edge] = []
    for pf in parsed_files:
        # INV-kokaj: look up the file pseudo-node by its new canonical
        # name (the repo-relative path the Pass 1 emitter stamped). Fall
        # back to absolute path for repo_root-less callers.
        try:
            pf_name = str(pf.path.relative_to(repo_root))
        except ValueError:  # pragma: no cover - defensive
            pf_name = str(pf.path)
        file_mod_sym = global_symbols.get(pf_name)
        edges = _extract_edges(
            pf.tree, pf.source, pf.path, pf.lang, run,
            global_symbols, global_methods, global_classes, pf.line_offset,
            pf.namespace_imports or {},
            resolver, method_resolver, class_resolver,
            symbol_by_position,
            pf.named_imports or {},
            symbols_by_name,
            module_symbol=file_mod_sym,
            named_import_originals=pf.named_import_originals or {},
        )
        # WI-lozug: emit module_attr_ref edges for attribute reads on
        # imported modules and well-known JS/Node globals (``process``,
        # ``window``, ``document``, ``navigator``).  Pairs with the
        # ``attributes:`` entries in io_primitives/javascript.yaml —
        # without this emission, env_read / ipc_send / ipc_recv chains
        # that target ``process.env`` / ``process.stdout`` / etc. were
        # silently inert.  The src is a file-level module pseudo-symbol
        # (``file_mod_sym`` when registered by Pass 1, or a synthetic
        # file-id symbol otherwise) — Python-side per-function granularity
        # is not plumbed through the io_boundary pipeline's attribute
        # matching, so the file-level caller matches the Go PR 1
        # convention and is sufficient for the tagging pipeline.
        combined_imports = {
            **(pf.namespace_imports or {}),
            **(pf.named_imports or {}),
            "process": "process",
            "window": "window",
            "document": "document",
            "navigator": "navigator",
        }
        # file_mod_sym is registered by Pass 1 for every file in
        # parsed_files (see line 2827), so it is non-None here.  A
        # defensive ``is not None`` check is omitted intentionally.
        emit_module_attribute_refs(
            pf.tree.root_node,
            pf.source,
            combined_imports,
            file_mod_sym,
            pf.lang,
            edges,
            node_kinds=("member_expression",),
            object_field_names=("object",),
            property_field_names=("property",),
            pass_id=PASS_ID,
            run_id=run.execution_id,
            call_node_kinds=("call_expression",),
            call_function_field_names=("function",),
        )
        # ADR-0015 Tier 1: automatic dataflow annotation from AST context
        if _df_config is not None:
            annotate_dataflow(edges, pf.tree, pf.source, _df_config)
        all_edges.extend(edges)

    # Pass 3: Extract usage contexts for call-based frameworks (v1.1.x)
    all_usage_contexts: list[UsageContext] = []
    for pf in parsed_files:
        # Express-style route calls (app.get, router.post, etc.)
        usage_contexts = _extract_express_usage_contexts(
            pf.tree, pf.source, pf.path, global_symbols, pf.line_offset,
            symbol_by_position,
        )
        all_usage_contexts.extend(usage_contexts)

        # Hapi config-object route calls (server.route({ method, path, handler }))
        hapi_contexts = _extract_hapi_usage_contexts(
            pf.tree, pf.source, pf.path, global_symbols, pf.line_offset
        )
        all_usage_contexts.extend(hapi_contexts)

        # Next.js file-based route exports (pages/ and app/ directories)
        nextjs_contexts = _extract_nextjs_usage_contexts(
            pf.tree, pf.source, pf.path, global_symbols, pf.line_offset
        )
        all_usage_contexts.extend(nextjs_contexts)

        # Library exports from index files (index.ts, index.js, etc.)
        library_contexts = _extract_library_export_contexts(
            pf.tree, pf.source, pf.path, global_symbols, pf.line_offset
        )
        all_usage_contexts.extend(library_contexts)

        # SPA bootstrap calls (createRoot, ReactDOM.render, hydrateRoot, etc.)
        try:
            pf_name = str(pf.path.relative_to(repo_root))
        except ValueError:  # pragma: no cover - defensive
            pf_name = str(pf.path)
        file_mod_sym = global_symbols.get(pf_name)
        if file_mod_sym is not None:
            bootstrap_contexts = _extract_app_bootstrap_contexts(
                pf.tree, pf.source, pf.path, file_mod_sym, pf.line_offset,
            )
            all_usage_contexts.extend(bootstrap_contexts)

            # INV-rolul: HTTP/GraphQL server-handler calls
            # (startStandaloneServer, runHttpQuery, executeHTTPGraphQLRequest,
            # http.createServer family, bare createServer). Closes the
            # WI-tisam end-to-end gap where graphql.yaml / node-http.yaml
            # YAML usage:kind:call patterns had no UC producer feeding them.
            http_handler_contexts = _extract_http_handler_contexts(
                pf.tree, pf.source, pf.path, file_mod_sym, pf.line_offset,
            )
            all_usage_contexts.extend(http_handler_contexts)

    # Extract inheritance edges (META-001: base_classes metadata -> extends/implements edges)
    # Build multi-value class lookup for disambiguation (INV-015)
    classes_by_name: dict[str, list[Symbol]] = {}
    for sym in all_symbols:
        if sym.kind == "class":
            if sym.name not in classes_by_name:
                classes_by_name[sym.name] = []
            classes_by_name[sym.name].append(sym)
    inheritance_edges = _extract_inheritance_edges(
        all_symbols, classes_by_name, parsed_files, run
    )
    all_edges.extend(inheritance_edges)

    # Extract decorator edges (INV-012: decorators metadata -> decorated_by edges)
    decorator_edges = _extract_decorator_edges(all_symbols, global_symbols, run)
    all_edges.extend(decorator_edges)

    # Extract type reference edges (WI-jivip: type-level dependency tracking)
    type_ref_edges = _extract_type_reference_edges(all_symbols, parsed_files, run)
    all_edges.extend(type_ref_edges)

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return JsAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        usage_contexts=all_usage_contexts,
        run=run,
    )
