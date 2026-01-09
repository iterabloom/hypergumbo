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

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import (
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
# Deprecated - use express.yaml, hapi.yaml, koa.yaml patterns
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# Known router/app receiver names for route detection (ADR-0003)
# Only calls like app.get(), router.post(), etc. are treated as routes.
# This prevents false positives from test mocks like fetchMock.get().
ROUTER_RECEIVER_NAMES = {"app", "router", "express", "server", "fastify", "koa"}

# Deprecation tracking for analyzer-level route detection (ADR-0003 v1.0.x)
# Framework-specific route detection is deprecated in favor of YAML patterns
_deprecated_route_warnings_emitted: set[str] = set()


def _emit_route_deprecation_warning(framework: str) -> None:
    """Emit deprecation warning for analyzer-level route detection.

    This is deprecated in ADR-0003 v1.0.x. Use YAML patterns instead.
    Warning emitted once per framework per session.
    """
    if framework in _deprecated_route_warnings_emitted:
        return
    _deprecated_route_warnings_emitted.add(framework)
    warnings.warn(
        f"{framework} analyzer-level route detection is deprecated. "
        f"Use framework YAML patterns (--frameworks) for semantic detection. "
        f"See ADR-0003 for migration guidance.",
        DeprecationWarning,
        stacklevel=4,
    )


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
            for i in range(idx - 1, -1, -1):
                sibling = parent.children[i]
                if sibling.type == "decorator":
                    dec_info = _extract_decorator_info(sibling, source)
                    if dec_info["name"]:
                        decorators.insert(0, dec_info)  # Maintain order
                else:
                    # Stop at non-decorator (e.g., another method or statement)
                    if sibling.type not in ("comment", "decorator"):
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

        # Express-style route handler detection: app.get('/path', handler) - deprecated
        if node.type == "call_expression":
            http_method, route_path = _detect_route_call(node, source)
            if http_method:
                _emit_route_deprecation_warning("Express")
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

                http_method, route_path = _detect_nestjs_decorator(node, source)
                stable_id = http_method if http_method else None

                # Build meta with decorators and route_path
                meta: dict[str, object] | None = None
                decorators = _extract_decorators(node, source)
                if decorators or route_path:
                    meta = {}
                    if decorators:
                        meta["decorators"] = decorators
                    if route_path:
                        meta["route_path"] = route_path

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
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/method.

    Returns the Symbol for the enclosing function, or None if not inside one.
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

        # Arrow functions assigned to variables
        if current.type == "arrow_function":
            # Walk up to find the variable_declarator
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
            return None  # pragma: no cover

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
) -> list[Edge]:
    """Extract edges from a parsed tree (pass 2).

    Uses global symbol registries to resolve cross-file references.
    Uses iterative traversal to avoid RecursionError on deeply nested code.

    Handles:
    - Direct calls: helper(), ClassName()
    - Method calls: this.method(), variable.method() (with type inference)
    - Namespace calls: alias.func(), alias.Class() (via namespace_imports)
    - Object instantiation: new ClassName()

    Note: Type inference only tracks types from direct constructor calls
    (client = new Client()), not from function returns (client = getClient()).
    """
    if namespace_imports is None:
        namespace_imports = {}
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
                    # Regular function call
                    current_function = _get_enclosing_function(node, source, file_path, global_symbols)
                    if current_function and func_name in global_symbols:
                        callee_symbol = global_symbols[func_name]
                        edge = Edge.create(
                            src=current_function.id,
                            dst=callee_symbol.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1 + line_offset,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="ast_call_direct",
                            confidence=0.85,
                        )
                        edges.append(edge)

            # Method calls: obj.method()
            if func_node and func_node.type == "member_expression":
                current_function = _get_enclosing_function(node, source, file_path, global_symbols)
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
                            if full_name in global_symbols:
                                target_sym = global_symbols[full_name]
                                edge = Edge.create(
                                    src=current_function.id,
                                    dst=target_sym.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1 + line_offset,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_method_this",
                                    confidence=0.95,
                                )
                                edges.append(edge)
                                edge_added = True

                        # Case 2: alias.func() via namespace import
                        elif obj_name and obj_name in namespace_imports:
                            # This is a namespace call: alias.func() or alias.Class()
                            # Resolve via global symbols using method_name directly
                            if method_name in global_symbols:
                                target_sym = global_symbols[method_name]
                                is_class = target_sym.kind == "class"
                                edge = Edge.create(
                                    src=current_function.id,
                                    dst=target_sym.id,
                                    edge_type="instantiates" if is_class else "calls",
                                    line=node.start_point[0] + 1 + line_offset,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_new" if is_class else "ast_call_namespace",
                                    confidence=0.90,
                                )
                                edges.append(edge)
                                edge_added = True

                        # Case 3: variable.method() via type inference
                        elif obj_name and obj_name in var_types:
                            type_class_name = var_types[obj_name]
                            full_name = f"{type_class_name}.{method_name}"
                            if full_name in global_symbols:
                                target_sym = global_symbols[full_name]
                                edge = Edge.create(
                                    src=current_function.id,
                                    dst=target_sym.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1 + line_offset,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_method_type_inferred",
                                    confidence=0.85,
                                )
                                edges.append(edge)
                                edge_added = True

                        # Case 4: Fallback - method name match with low confidence
                        if not edge_added and method_name in global_methods:
                            for target_sym in global_methods[method_name]:
                                edge = Edge.create(
                                    src=current_function.id,
                                    dst=target_sym.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1 + line_offset,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_method_inferred",
                                    confidence=0.60,
                                )
                                edges.append(edge)

        # new ClassName() or new namespace.ClassName()
        elif node.type == "new_expression":
            current_function = _get_enclosing_function(node, source, file_path, global_symbols)
            class_name = None
            target_sym = None

            for child in node.children:
                if child.type == "identifier":
                    # new ClassName()
                    class_name = _node_text(child, source)
                    if class_name in global_classes:
                        target_sym = global_classes[class_name]
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
                        if cls_name in global_classes:
                            target_sym = global_classes[cls_name]
                    break

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
                    confidence=0.95,
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

    for sym in all_symbols:
        global_symbols[sym.name] = sym
        if sym.kind == "method":
            method_name = sym.name.split(".")[-1] if "." in sym.name else sym.name
            if method_name not in global_methods:
                global_methods[method_name] = []
            global_methods[method_name].append(sym)
        elif sym.kind == "class":
            global_classes[sym.name] = sym

    # Pass 2: Extract edges using global symbol registry
    all_edges: list[Edge] = []
    for pf in parsed_files:
        edges = _extract_edges(
            pf.tree, pf.source, pf.path, pf.lang, run,
            global_symbols, global_methods, global_classes, pf.line_offset,
            pf.namespace_imports or {}
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return JsAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
