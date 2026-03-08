"""JavaScript/TypeScript/Svelte analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse JS/TS/Svelte files and extract:
- Function and class declarations (symbols)
- Import/require statements (edges)
- Function call relationships (edges)
- Method call relationships (edges)
- Object instantiation relationships (edges)

Rich Metadata (ADR-0003)
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
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. For Svelte files, extract <script> blocks and parse as TS/JS

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
- Two-pass allows cross-file call resolution
- Svelte support reuses existing TS/JS parsing infrastructure
- Uses iterative traversal to avoid RecursionError on deeply nested code
"""
from __future__ import annotations

import re
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, UsageContext, make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver, ListNameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    populate_docstrings_from_tree,
    find_child_by_field,
    iter_tree,
    make_route_stable_id,
    make_typed_stable_id,
    node_text as _node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("javascript")


def find_js_ts_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all JS/TS files in the repository, excluding common non-source dirs."""
    yield from find_files(repo_root, ["*.js", "*.jsx", "*.ts", "*.tsx"], max_files=max_files)


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
    file_patterns: ClassVar[list[str]] = ["*.js", "*.jsx", "*.ts", "*.tsx", "*.svelte", "*.vue"]
    grammar_module = "tree_sitter_javascript"

    def analyze(self, repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        """Run the JS/TS analysis using the existing analyze logic."""
        return _analyze_javascript_impl(repo_root, max_files=max_files)


_jsts_analyzer = JstsTreeSitterAnalyzer()


def is_tree_sitter_available() -> bool:
    """Check if tree-sitter and required grammars are available."""
    return _jsts_analyzer._check_grammar_available()


# Backwards compatibility alias
JsAnalysisResult = AnalysisResult


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
    # Maps imported name -> module path for 'import { Foo } from "module"'
    named_imports: dict[str, str] | None = None


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str, lang: str) -> str:
    """Generate location-based ID."""
    return f"{lang}:{path}:{start_line}-{end_line}:{name}:{kind}"


def _get_language_for_file(file_path: Path) -> str:
    """Determine language based on file extension."""
    suffix = file_path.suffix.lower()
    if suffix in (".ts", ".tsx"):
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

    if suffix in (".ts", ".tsx"):
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


def _extract_namespace_imports(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract namespace imports from a parsed tree.

    Tracks:
    - import * as alias from 'module' -> alias: module
    - import alias from 'module' (default import) -> alias: module

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

    return namespace_imports


def _extract_named_imports(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract named imports from a parsed tree.

    Tracks: import { Foo, Bar as Baz } from 'module' -> Foo: module, Baz: module

    Returns dict mapping imported name (or alias) -> module path.
    Used to disambiguate type references when multiple files define
    the same class name (e.g., monorepos with duplicate CatsService).
    """
    named_imports: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import_statement":
            continue

        module_name = None
        import_names: list[str] = []

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
                                if alias_node:
                                    import_names.append(_node_text(alias_node, source))
                                elif original_node:
                                    import_names.append(_node_text(original_node, source))

        if module_name:
            for name in import_names:
                named_imports[name] = module_name

    return named_imports


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

# HTTP methods recognized as route handlers (Express, Fastify, Koa, etc.)
# Note: Express-style route detection uses function calls (app.get, router.post) rather
# than decorators. These are now matched via UsageContext (ADR-0003 v1.1.x) which
# enables YAML patterns for call-based frameworks.
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# Known router/app receiver names for route detection (ADR-0003)
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
    elif node.type in ("method_definition", "function"):
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
    if node.type == "function_declaration":
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

    # Validate the receiver is a known router/app name (ADR-0003)
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
                if arg.type == "function_expression" or arg.type == "function":
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
            elif child.type == "function_declaration":
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
            elif child.type == "function_declaration":
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

    for sym in symbols:
        if sym.kind != "class":
            continue

        base_classes = sym.meta.get("base_classes", []) if sym.meta else []
        if not base_classes:
            continue

        for base_class_name in base_classes:
            # Strip generics from base class name (e.g., "Repository<User>" -> "Repository")
            base_name = base_class_name.split("<")[0]
            # Handle qualified names like "React.Component" -> use just "Component"
            if "." in base_name:
                base_name = base_name.split(".")[-1]

            # Try class first, then interface, using disambiguation
            base_sym = _resolve_base_class_js(
                base_name, sym, classes_by_name, parsed_files
            )
            if base_sym is not None and base_sym.id != sym.id:
                edge = Edge.create(
                    src=sym.id,
                    dst=base_sym.id,
                    edge_type="extends",
                    line=sym.span.start_line if sym.span else 0,
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_extends",
                )
                edges.append(edge)
            else:
                # Check interfaces
                iface_sym = _resolve_base_class_js(
                    base_name, sym, interfaces_by_name, parsed_files
                )
                if iface_sym is not None and iface_sym.id != sym.id:
                    edge = Edge.create(
                        src=sym.id,
                        dst=iface_sym.id,
                        edge_type="implements",
                        line=sym.span.start_line if sym.span else 0,
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="ast_implements",
                    )
                    edges.append(edge)

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
                    confidence=0.95,
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
                    confidence=0.50,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_decorator_unresolved",
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


def _extract_symbols(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    lang: str,
    run: AnalysisRun,
    line_offset: int = 0,
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
    """
    symbols: list[Symbol] = []

    # Create module-level symbol for top-level code attribution
    module_name = file_path.name
    end_line = tree.root_node.end_point[0] + 1 + line_offset
    module_span = Span(
        start_line=1 + line_offset,
        end_line=end_line,
        start_col=0,
        end_col=0,
    )
    module_symbol = Symbol(
        id=_make_symbol_id(str(file_path), 1 + line_offset, end_line, f"<module:{module_name}>", "module", lang),
        name=f"<module:{module_name}>",
        kind="module",
        language=lang,
        path=str(file_path),
        span=module_span,
        origin=PASS_ID,
        origin_run_id=run.execution_id,
    )
    symbols.append(module_symbol)

    # Track nodes we've already processed as route handlers (to avoid duplicates)
    processed_handlers: set[int] = set()

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
                        name = handler_name or f"_{http_method}_handler"
                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "route", lang),
                            name=name,
                            kind="route",
                            language=lang,
                            path=str(file_path),
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            stable_id=make_route_stable_id(http_method, route_path) if route_path else None,
                            meta={"route_path": route_path, "http_method": http_method, "handler_ref": handler_name},
                        )
                        symbols.append(symbol)
                    else:
                        # Inline handler: router.get('/path', (req, res) => {})
                        name = None
                        if handler_node.type == "function_expression" or handler_node.type == "function":
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
                        )
                        symbols.append(symbol)
                    continue  # Skip further processing of this call_expression

        # Function declarations (skip if inside an export_statement - handled below)
        if node.type == "function_declaration":
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
                        elif grandchild.type == "arrow_function":
                            value_node = grandchild
                        elif grandchild.type == "call_expression":
                            # Pattern: const handler = catchAsync(async (req, res) => {})
                            for call_child in grandchild.children:
                                if call_child.type == "arguments":
                                    for arg in call_child.children:
                                        if arg.type == "arrow_function":
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
                        )
                        symbols.append(symbol)

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

                )
                symbols.append(symbol)

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

                )
                symbols.append(symbol)

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
                meta: dict[str, object] | None = None
                decorators = _extract_decorators(node, source)
                if decorators:
                    meta = {"decorators": decorators}

                signature = _extract_jsts_signature(node, source)

                # Typed stable_id for non-route methods (ADR-0014 §3)
                if stable_id is None:
                    norm_sig = normalize_jsts_signature(signature)
                    if norm_sig:
                        stable_id = make_typed_stable_id(kind, norm_sig)

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

                )
                symbols.append(symbol)

        # Export default function - extract the function symbol
        elif node.type == "export_statement":
            for child in node.children:
                if child.type == "function_declaration":
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
                        )
                        symbols.append(symbol)
                    break  # Only handle one function_declaration per export

    return symbols


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
            if current.type == "function_declaration":
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
        if current.type == "function_declaration":
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

        # Arrow functions - try variable assignment first, then position lookup
        if current.type == "arrow_function":
            # First, try to find a variable_declarator parent (assigned arrow fn)
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
    edges: list[Edge] = []
    # Track variable types for type inference: var_name -> class_name
    var_types: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        # Import statements
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "string":
                    module_name = _node_text(child, source).strip("'\"")
                    file_id = _make_symbol_id(str(file_path), 1, 1, file_path.name, "file", lang)
                    dst_id = f"{lang}:{module_name}:0-0:module:module"
                    edge = Edge.create(
                        src=file_id,
                        dst=dst_id,
                        edge_type="imports",
                        line=node.start_point[0] + 1 + line_offset,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="import_static",
                        confidence=0.95,
                    )
                    edges.append(edge)
                    break

        # Function/method declarations - extract parameter types for type inference
        elif node.type in ("function_declaration", "method_definition", "arrow_function"):
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

            # Require calls
            if func_node and func_node.type == "identifier":
                func_name = _node_text(func_node, source)
                if func_name == "require" and args_node:
                    for arg in args_node.children:
                        if arg.type == "string":
                            module_name = _node_text(arg, source).strip("'\"")
                            file_id = _make_symbol_id(str(file_path), 1, 1, file_path.name, "file", lang)
                            dst_id = f"{lang}:{module_name}:0-0:module:module"
                            edge = Edge.create(
                                src=file_id,
                                dst=dst_id,
                                edge_type="imports",
                                line=node.start_point[0] + 1 + line_offset,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="require_static",
                                confidence=0.90,
                            )
                            edges.append(edge)
                            break
                        elif arg.type == "identifier":
                            var_name = _node_text(arg, source)
                            file_id = _make_symbol_id(str(file_path), 1, 1, file_path.name, "file", lang)
                            dst_id = f"{lang}:<dynamic:{var_name}>:0-0:module:module"
                            edge = Edge.create(
                                src=file_id,
                                dst=dst_id,
                                edge_type="imports",
                                line=node.start_point[0] + 1 + line_offset,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="require_dynamic",
                                confidence=0.40,
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
                        if callee is None:
                            lookup_result = resolver.lookup(func_name)
                            if lookup_result.found and lookup_result.symbol is not None:
                                # Cross-package guard: the resolver fallback
                                # should not cross npm packages (import-path
                                # and same-package checks above already had
                                # their chance to find a valid callee).
                                if not _is_cross_package(
                                    file_path, lookup_result.symbol.path,
                                ):
                                    callee = lookup_result.symbol
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
                            lookup_result = resolver.lookup(full_name)
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
                                    lookup_result = resolver.lookup(full_name)
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
                                        confidence=0.90,
                                    )
                                    edges.append(edge)
                                    edge_added = True

                        # Case 2: alias.func() via namespace import
                        elif obj_name and obj_name in namespace_imports:
                            # This is a namespace call: alias.func() or alias.Class()
                            # Resolve via global symbols using import path as hint
                            # to disambiguate when same name exists in multiple modules
                            import_path = namespace_imports[obj_name]
                            lookup_result = resolver.lookup(method_name, path_hint=import_path)
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
                                lookup_result = resolver.lookup(full_name)
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
                                    confidence=0.85,
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
                                    edge = Edge.create(
                                        src=current_function.id,
                                        dst=lookup_result.symbol.id,
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
                            lookup_result = resolver.lookup(arg_name)
                            if lookup_result.found and lookup_result.symbol is not None:
                                target = lookup_result.symbol
                        # Route symbols can shadow function symbols in
                        # global_symbols (last-one-wins).  When target is
                        # a route, prefer the function symbol with the
                        # same name via symbols_by_name, so the edge
                        # points to the function definition.
                        if target is not None and target.kind == "route" and symbols_by_name:
                            fn_candidates = [
                                s for s in symbols_by_name.get(arg_name, [])
                                if s.kind in ("function", "method")
                            ]
                            if fn_candidates:
                                target = fn_candidates[0]
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
                                confidence=0.75,
                            ))

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
                            resolved = global_symbols.get(arg_name)
                            if resolved is not None and resolved.kind == "route" and symbols_by_name:
                                fn_cands = [
                                    s for s in symbols_by_name.get(arg_name, [])
                                    if s.kind in ("function", "method")
                                ]
                                if fn_cands:
                                    resolved = fn_cands[0]
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
                                evidence_type="middleware_chain",
                                confidence=0.70,
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
                    lookup_result = resolver.lookup(ref_name)
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
                            confidence=0.80,
                        ))

        # Shorthand property: {handleClick} — equivalent to {handleClick: handleClick}
        elif node.type == "shorthand_property_identifier":
            ref_name = _node_text(node, source)
            target = global_symbols.get(ref_name)
            if target is None:  # pragma: no cover - defensive resolver fallback
                lookup_result = resolver.lookup(ref_name)
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
                        confidence=0.80,
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

    # Find module symbol for top-level call attribution
    mod_sym = next((s for s in symbols if s.kind == "module"), None)
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


@register_analyzer("javascript", supports_max_files=True)
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
            tree = parser.parse(source)
            lang = _get_language_for_file(file_path)
            ns_imports = _extract_namespace_imports(tree, source)
            nm_imports = _extract_named_imports(tree, source)
            parsed_files.append(_ParsedFile(
                path=file_path, tree=tree, source=source, lang=lang,
                namespace_imports=ns_imports, named_imports=nm_imports
            ))
            symbols = _extract_symbols(tree, source, file_path, lang, run)
            populate_docstrings_from_tree(tree.root_node, source, symbols)
            all_symbols.extend(symbols)
            files_analyzed += 1
        except (OSError, IOError):
            files_skipped += 1

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
                nm_imports = _extract_named_imports(tree, source_bytes)

                parsed_files.append(_ParsedFile(
                    path=file_path, tree=tree, source=source_bytes,
                    lang=lang, line_offset=line_offset, namespace_imports=ns_imports,
                    named_imports=nm_imports
                ))
                symbols = _extract_symbols(tree, source_bytes, file_path, lang, run, line_offset)
                populate_docstrings_from_tree(tree.root_node, source_bytes, symbols)
                all_symbols.extend(symbols)

            files_analyzed += 1
        except (OSError, IOError):
            files_skipped += 1

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
                nm_imports = _extract_named_imports(tree, source_bytes)

                parsed_files.append(_ParsedFile(
                    path=file_path, tree=tree, source=source_bytes,
                    lang=lang, line_offset=line_offset, namespace_imports=ns_imports,
                    named_imports=nm_imports
                ))
                symbols = _extract_symbols(tree, source_bytes, file_path, lang, run, line_offset)
                populate_docstrings_from_tree(tree.root_node, source_bytes, symbols)
                all_symbols.extend(symbols)

            files_analyzed += 1
        except (OSError, IOError):
            files_skipped += 1

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
        # Look up module symbol for this file (top-level call attribution)
        mod_sym_name = f"<module:{pf.path.name}>"
        file_mod_sym = global_symbols.get(mod_sym_name)
        edges = _extract_edges(
            pf.tree, pf.source, pf.path, pf.lang, run,
            global_symbols, global_methods, global_classes, pf.line_offset,
            pf.namespace_imports or {},
            resolver, method_resolver, class_resolver,
            symbol_by_position,
            pf.named_imports or {},
            symbols_by_name,
            module_symbol=file_mod_sym,
        )
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

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return JsAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        usage_contexts=all_usage_contexts,
        run=run,
    )
