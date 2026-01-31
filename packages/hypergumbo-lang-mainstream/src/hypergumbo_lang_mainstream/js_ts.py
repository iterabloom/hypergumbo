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
from typing import TYPE_CHECKING, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, UsageContext
from hypergumbo_core.symbol_resolution import NameResolver, ListNameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    find_child_by_field,
    is_grammar_available,
    iter_tree,
    node_text as _node_text,
)

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "javascript-ts-v1"
PASS_VERSION = "hypergumbo-0.1.0"


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


def is_tree_sitter_available() -> bool:
    """Check if tree-sitter and required grammars are available."""
    return is_grammar_available("tree_sitter_javascript")


# Backwards compatibility alias
JsAnalysisResult = AnalysisResult


@dataclass
class _ParsedFile:
    """Holds parsed file data for two-pass analysis.

    Note on type inference: Variable method calls (e.g., client.send()) are resolved
    using constructor-only type inference. This tracks types from direct constructor
    calls (client = new Client()) but NOT from function returns (client = getClient()).
    This covers ~90% of real-world cases with minimal complexity.
    """

    path: Path
    tree: "tree_sitter.Tree"
    source: bytes
    lang: str
    line_offset: int = 0  # For Svelte script blocks
    # Maps local alias -> module name for 'import * as alias' and 'import alias'
    namespace_imports: dict[str, str] | None = None


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
        receiver_name = None
        for child in node.children:
            if child.type == "member_expression":
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
                if name:
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
            elif child.type == "class_declaration":
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


def _extract_inheritance_edges(
    symbols: list[Symbol],
    class_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract extends/implements edges from class inheritance.

    For each class with base_classes metadata, creates extends/implements edges
    to base classes/interfaces that exist in the analyzed codebase. This enables
    the type hierarchy linker to create dispatches_to edges for polymorphic dispatch.

    Args:
        symbols: All extracted symbols
        class_symbols: Map of class name -> Symbol for class lookup
        run: Current analysis run for provenance

    Returns:
        List of extends/implements edges for inheritance relationships
    """
    edges: list[Edge] = []

    # Also build interface symbol lookup
    interface_symbols: dict[str, Symbol] = {}
    for sym in symbols:
        if sym.kind == "interface":
            interface_symbols[sym.name] = sym

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

            # Check if it's a class (extends) or interface (implements)
            if base_name in class_symbols:
                base_sym = class_symbols[base_name]
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
            elif base_name in interface_symbols:
                iface_sym = interface_symbols[base_name]
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
        if current.type == "class_declaration":
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


def _extract_base_classes(
    node: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Extract base classes from a class_declaration node.

    Handles:
    - extends clause: class Foo extends Bar
    - implements clause: class Foo implements IBar, IBaz
    - generic types: class Foo extends Bar<T>

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
                            stable_id=http_method,
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
                            stable_id=http_method,
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
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function", lang),
                    name=name,
                    kind="function",
                    language=lang,
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
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
                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function", lang),
                            name=name,
                            kind="function",
                            language=lang,
                            path=str(file_path),
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            signature=signature,
                        )
                        symbols.append(symbol)

        # Class declarations
        elif node.type == "class_declaration":
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
                stable_id = http_method if http_method else None

                # Build meta with decorators
                # Note: Route path combination is handled by enrichment via prefix_from_parent
                # in the NestJS YAML pattern definition (see nestjs.yaml)
                meta: dict[str, object] | None = None
                decorators = _extract_decorators(node, source)
                if decorators:
                    meta = {"decorators": decorators}

                signature = _extract_jsts_signature(node, source)

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
                        symbol = Symbol(
                            id=_make_symbol_id(str(file_path), span.start_line, span.end_line, name, "function", lang),
                            name=name,
                            kind="function",
                            language=lang,
                            path=str(file_path),
                            span=span,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            signature=signature,
                        )
                        symbols.append(symbol)
                    break  # Only handle one function_declaration per export

    return symbols


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    global_symbols: dict[str, Symbol],
    symbol_by_position: dict[tuple[str, int, int], Symbol] | None = None,
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/method.

    Returns the Symbol for the enclosing function, or None if not inside one.

    For arrow functions passed as callbacks (not assigned to variables), looks up
    the symbol by position using symbol_by_position. This enables call attribution
    for patterns like: app.get('/', (req, res) => { helper(); })
    """
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            name = _find_name_in_children(current, source)
            if name and name in global_symbols:
                sym = global_symbols[name]
                if sym.path == str(file_path):
                    return sym
            return None  # pragma: no cover

        if current.type == "method_definition":
            name = _find_name_in_children(current, source)
            if name:
                class_ctx = _get_class_context(current, source)
                if class_ctx:
                    full_name = f"{class_ctx}.{name}"
                    if full_name in global_symbols:
                        sym = global_symbols[full_name]
                        if sym.path == str(file_path):
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
                                if sym.path == str(file_path):
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
    """
    if namespace_imports is None:
        namespace_imports = {}
    if resolver is None:  # pragma: no cover - defensive
        resolver = NameResolver(global_symbols)
    if method_resolver is None:  # pragma: no cover - defensive
        method_resolver = ListNameResolver(global_methods)
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
                    current_function = _get_enclosing_function(node, source, file_path, global_symbols, symbol_by_position)
                    if current_function:
                        lookup_result = resolver.lookup(func_name)
                        if lookup_result.found:
                            # Scale confidence by resolver's confidence multiplier
                            edge_confidence = 0.85 * lookup_result.confidence
                            edge = Edge.create(
                                src=current_function.id,
                                dst=lookup_result.symbol.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1 + line_offset,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_call_direct",
                                confidence=edge_confidence,
                            )
                            edges.append(edge)

            # Method calls: obj.method()
            if func_node and func_node.type == "member_expression":
                current_function = _get_enclosing_function(node, source, file_path, global_symbols, symbol_by_position)
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

                        # Case 2: alias.func() via namespace import
                        elif obj_name and obj_name in namespace_imports:
                            # This is a namespace call: alias.func() or alias.Class()
                            # Resolve via global symbols using import path as hint
                            # to disambiguate when same name exists in multiple modules
                            import_path = namespace_imports[obj_name]
                            lookup_result = resolver.lookup(method_name, path_hint=import_path)
                            if lookup_result.found and lookup_result.symbol is not None:
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
                            lookup_result = resolver.lookup(full_name)
                            if lookup_result.found and lookup_result.symbol is not None:
                                edge = Edge.create(
                                    src=current_function.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1 + line_offset,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_method_type_inferred",
                                    confidence=0.85 * lookup_result.confidence,
                                )
                                edges.append(edge)
                                edge_added = True

                        # Case 4: Fallback - method name match with low confidence
                        if not edge_added:
                            lookup_result = method_resolver.lookup(method_name)
                            if lookup_result.found and lookup_result.candidates:
                                for target_sym in lookup_result.candidates:
                                    edge = Edge.create(
                                        src=current_function.id,
                                        dst=target_sym.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1 + line_offset,
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                        evidence_type="ast_method_inferred",
                                        confidence=0.60 * lookup_result.confidence,
                                    )
                                    edges.append(edge)

        # new ClassName() or new namespace.ClassName()
        elif node.type == "new_expression":
            current_function = _get_enclosing_function(node, source, file_path, global_symbols, symbol_by_position)
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

    edges = _extract_edges(tree, source, file_path, lang, run, global_symbols, global_methods, global_classes)
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
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Check for tree-sitter availability
    if not is_tree_sitter_available():
        skip_reason = "JS/TS analysis skipped: requires tree-sitter (pip install hypergumbo[javascript])"
        warnings.warn(skip_reason, stacklevel=2)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return JsAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
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
            parsed_files.append(_ParsedFile(
                path=file_path, tree=tree, source=source, lang=lang,
                namespace_imports=ns_imports
            ))
            symbols = _extract_symbols(tree, source, file_path, lang, run)
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

                parsed_files.append(_ParsedFile(
                    path=file_path, tree=tree, source=source_bytes,
                    lang=lang, line_offset=line_offset, namespace_imports=ns_imports
                ))
                symbols = _extract_symbols(tree, source_bytes, file_path, lang, run, line_offset)
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

                parsed_files.append(_ParsedFile(
                    path=file_path, tree=tree, source=source_bytes,
                    lang=lang, line_offset=line_offset, namespace_imports=ns_imports
                ))
                symbols = _extract_symbols(tree, source_bytes, file_path, lang, run, line_offset)
                all_symbols.extend(symbols)

            files_analyzed += 1
        except (OSError, IOError):
            files_skipped += 1

    # Build global symbol registries
    global_symbols: dict[str, Symbol] = {}
    global_methods: dict[str, list[Symbol]] = {}
    global_classes: dict[str, Symbol] = {}
    # Position-based lookup for inline route handlers: (file_path, start_line, start_col) -> Symbol
    symbol_by_position: dict[tuple[str, int, int], Symbol] = {}

    for sym in all_symbols:
        global_symbols[sym.name] = sym
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
    method_resolver = ListNameResolver(global_methods)
    class_resolver = NameResolver(global_classes)
    all_edges: list[Edge] = []
    for pf in parsed_files:
        edges = _extract_edges(
            pf.tree, pf.source, pf.path, pf.lang, run,
            global_symbols, global_methods, global_classes, pf.line_offset,
            pf.namespace_imports or {},
            resolver, method_resolver, class_resolver,
            symbol_by_position,
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
    inheritance_edges = _extract_inheritance_edges(all_symbols, global_classes, run)
    all_edges.extend(inheritance_edges)

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return JsAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        usage_contexts=all_usage_contexts,
        run=run,
    )
