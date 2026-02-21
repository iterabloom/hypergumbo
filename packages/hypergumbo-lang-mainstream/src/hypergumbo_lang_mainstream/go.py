"""Go analysis pass using tree-sitter-go.

This analyzer uses tree-sitter to parse Go files and extract:
- Function declarations (func)
- Method declarations (func with receiver)
- Struct declarations (type X struct)
- Interface declarations (type X interface)
- Function call relationships
- Import relationships (import statements)
- Web framework routes (Gin, Echo, Fiber, Gorilla mux)

If tree-sitter with Go support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-go is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Extract variable-type bindings, detect calls, imports, and routes
5. Receiver-type disambiguation:
   - Tracks variable types **per function scope** from ``:=`` composite
     literals, ``var`` declarations, and function parameters
     (e.g., in ``func foo() { s := &Server{} }``, s has type Server only in foo)
   - When resolving ``s.Method()``, looks up ``Server.Method`` before falling
     back to short-name resolution, preventing incorrect disambiguation when
     multiple types define the same method name
6. Ambiguous method call guard:
   - When a method call ``x.Method()`` has no inferred receiver type and the
     method name has 3+ candidates in global symbols, creates an unresolved
     edge with ``evidence_type="ambiguous_method_call"`` instead of picking
     an arbitrary candidate (which would produce a false-positive call edge)
4. Route detection:
   - Gin/Echo: r.GET("/path", handler), e.POST("/path", handler)
   - Fiber: app.Get("/path", handler) (lowercase methods)
   - Gorilla mux: router.HandleFunc("/path", handler), router.Handle("/path", handler)
   - Gorilla mux builder: router.Path("/path").Methods("GET").Handler(handler)
   - Creates route symbols with stable_id = sha256("route:{method}:{path}")

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-go package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Rust/Elixir/Java/PHP/C analyzers for consistency
- Route detection enables `hypergumbo routes` command for Go
"""
from __future__ import annotations

import time
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, UsageContext, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    populate_docstrings_from_tree,
    find_child_by_field,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_route_stable_id,
    make_symbol_id,
    make_typed_stable_id,
    node_text,
    visibility_from_modifiers,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.symbol_resolution import ListNameResolver

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("go")

# Go web framework HTTP method names
# Gin/Echo use uppercase: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS
# Fiber uses lowercase: Get, Post, Put, Delete, Patch, Head, Options
#
# Route detection produces two outputs from the same extraction pass:
# 1. UsageContext records — matched by YAML patterns (ADR-0003 v1.1.x) to enrich
#    handler symbols with concept: route metadata.
# 2. Route symbols (kind="route") — consumed by the route_handler linker to
#    create routes_to edges. See py.py docstring for full architecture notes.
GO_HTTP_METHODS = {
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
    "Get", "Post", "Put", "Delete", "Patch", "Head", "Options",
}

# Gorilla mux simple methods: router.HandleFunc("/path", handler)
GORILLA_HANDLE_METHODS = {"HandleFunc", "Handle"}

# Gorilla mux builder chain terminators: .Handler(h) or .HandlerFunc(h)
GORILLA_CHAIN_TERMINATORS = {"Handler", "HandlerFunc"}

# Gorilla mux builder chain path methods: .Path("/x") or .PathPrefix("/x")
GORILLA_PATH_METHODS = {"Path", "PathPrefix"}


def find_go_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Go files in the repository."""
    yield from find_files(repo_root, ["*.go"])


class GoTreeSitterAnalyzer(TreeSitterAnalyzer):
    """TreeSitterAnalyzer wrapper for Go files.

    Overrides ``analyze()`` entirely because Go analysis uses a custom
    two-pass approach with routes, usage contexts, import alias extraction,
    and list-based global symbol resolution. The base class provides grammar
    availability checking via ``_check_grammar_available()``.
    """

    lang = "go"
    file_patterns: ClassVar[list[str]] = ["*.go"]
    grammar_module = "tree_sitter_go"

    def analyze(self, repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        """Run the Go analysis using the existing analyze_go logic."""
        return _analyze_go_impl(repo_root, max_files=max_files)


_analyzer = GoTreeSitterAnalyzer()


def is_go_tree_sitter_available() -> bool:
    """Check if tree-sitter with Go grammar is available."""
    return _analyzer._check_grammar_available()


# Keep GoAnalysisResult as an alias for backwards compatibility
GoAnalysisResult = AnalysisResult


def _extract_go_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Go function/method declaration.

    Returns a signature string like "(x int, y string) error" or "(a, b int) (int, error)"
    for Go functions. None if extraction fails.

    Args:
        node: A tree-sitter function_declaration or method_declaration node.
        source: Source bytes of the file.
    """
    if node.type not in ("function_declaration", "method_declaration"):
        return None  # pragma: no cover

    params_node = find_child_by_field(node, "parameters")
    if not params_node:
        return None  # pragma: no cover

    # Extract parameters from parameter_list
    param_strs: list[str] = []
    for child in params_node.children:
        if child.type == "parameter_declaration":
            # Go parameters: can have multiple names sharing a type
            # e.g., "a, b int" or "x string"
            names: list[str] = []
            type_str = ""
            for param_child in child.children:
                if param_child.type == "identifier":
                    names.append(node_text(param_child, source))
                elif param_child.type in ("type_identifier", "pointer_type",
                                          "slice_type", "map_type", "array_type",
                                          "interface_type", "struct_type",
                                          "function_type", "channel_type",
                                          "qualified_type"):
                    type_str = node_text(param_child, source)

            if names and type_str:
                param_strs.append(f"{', '.join(names)} {type_str}")
            elif type_str:  # pragma: no cover
                # Unnamed parameter (rare but valid in Go interfaces)
                param_strs.append(type_str)

    sig = "(" + ", ".join(param_strs) + ")"

    # Extract return type(s) from result field
    result_node = find_child_by_field(node, "result")
    if result_node:
        ret_text = node_text(result_node, source)
        sig += f" {ret_text}"

    return sig


def normalize_go_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Go signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_go
    return normalize_signature_go(signature, type_params)


def _extract_import_aliases(
    root_node: "tree_sitter.Node",
    source: bytes,
) -> dict[str, str]:
    """Extract import alias → import path mappings from a Go file.

    Returns a dict mapping alias names to their import paths.
    For imports without explicit aliases, uses the last path component.

    Example:
        import (
            "fmt"                    -> {"fmt": "fmt"}
            pb "github.com/foo/bar"  -> {"pb": "github.com/foo/bar"}
        )
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(root_node):
        if node.type == "import_declaration":
            for child in node.children:
                if child.type == "import_spec":
                    _process_import_spec(child, source, aliases)
                elif child.type == "import_spec_list":
                    for spec in child.children:
                        if spec.type == "import_spec":
                            _process_import_spec(spec, source, aliases)

    return aliases


def _process_import_spec(
    spec: "tree_sitter.Node",
    source: bytes,
    aliases: dict[str, str],
) -> None:
    """Process a single import_spec node and add to aliases dict."""
    path_node = find_child_by_field(spec, "path")
    if not path_node:
        return  # pragma: no cover - defensive for malformed AST

    import_path = node_text(path_node, source).strip('"')

    # Check for explicit alias
    name_node = find_child_by_field(spec, "name")
    if name_node:
        alias = node_text(name_node, source)
        if alias != "_" and alias != ".":  # Ignore blank and dot imports
            aliases[alias] = import_path
    else:
        # No explicit alias - use last component of path
        # e.g., "github.com/foo/bar" -> "bar"
        alias = import_path.rsplit("/", 1)[-1]
        aliases[alias] = import_path


def _import_path_to_dir_hint(import_path: str) -> str | None:
    """Convert an import path to a directory hint for matching.

    For paths like "github.com/example/src/checkoutservice/genproto",
    returns "/src/checkoutservice/genproto" or similar suffix that
    can be used to match against file paths.
    """
    # Look for common patterns that indicate local paths
    if "/src/" in import_path:
        # Extract from /src/ onwards
        tail = import_path.split("/src/", 1)[1]
        return f"/src/{tail}"

    # For other paths, use the last 2-3 components
    parts = import_path.split("/")
    if len(parts) >= 2:
        return "/" + "/".join(parts[-2:])

    return None


def _detect_interface_assertion(
    var_spec: "tree_sitter.Node",
    source: bytes,
    impl_assertions: dict[str, list[str]],
) -> None:
    """Detect Go interface-implementation assertions in var specs.

    Parses patterns like ``var _ Interface = &Struct{}``,
    ``var _ Interface = (*Struct)(nil)``, and generic forms like
    ``var _ Cache[string] = &StringCache{}`` which are compile-time
    assertions that a struct satisfies an interface. Populates
    impl_assertions mapping struct names to interface names.

    Interface type forms: ``type_identifier`` (simple), ``qualified_type``
    (e.g., ``io.Reader``), ``generic_type`` (e.g., ``Cache[string]`` or
    ``entity.Interface[T]``).

    Two RHS forms are recognized:
    1. ``&Struct{}`` — unary_expression(&) → composite_literal → type_identifier
    2. ``(*Struct)(nil)`` — call_expression → parenthesized_expression →
       pointer_type → type_identifier
    """
    # Check that the variable name is "_" (blank identifier)
    name_node = find_child_by_field(var_spec, "name")
    if name_node is None or node_text(name_node, source) != "_":
        return

    # Extract the interface type from the type annotation
    type_node = find_child_by_field(var_spec, "type")
    if type_node is None:
        return

    if type_node.type == "type_identifier":
        iface_name = node_text(type_node, source)
    elif type_node.type == "qualified_type":
        # e.g., io.Writer → use "io.Writer"
        iface_name = node_text(type_node, source)
    elif type_node.type == "generic_type":
        # e.g., Cache[string] or entity.Interface[T]
        # The base type is a child type_identifier or qualified_type
        base_tid = find_child_by_type(type_node, "type_identifier")
        if base_tid:
            iface_name = node_text(base_tid, source)
        else:
            qual = find_child_by_type(type_node, "qualified_type")
            if qual:
                iface_name = node_text(qual, source)
            else:
                return  # pragma: no cover — generic_type always has a base type
    else:
        return

    # Extract the implementing struct from the RHS expression
    value_node = find_child_by_field(var_spec, "value")
    if value_node is None:  # pragma: no cover — Go syntax requires RHS for var _ T = ...
        return

    struct_name = _extract_struct_from_assertion_rhs(value_node, source)
    if struct_name is None:
        return

    if struct_name not in impl_assertions:
        impl_assertions[struct_name] = []
    impl_assertions[struct_name].append(iface_name)


def _extract_struct_from_assertion_rhs(
    expr_list: "tree_sitter.Node",
    source: bytes,
) -> Optional[str]:
    """Extract struct name from the RHS of a var _ Interface = ... assertion.

    Handles two patterns:
    1. ``&Struct{}`` — expression_list → unary_expression → composite_literal
    2. ``(*Struct)(nil)`` — expression_list → call_expression →
       parenthesized_expression → pointer_type → type_identifier
    """
    # The value is wrapped in an expression_list
    for child in expr_list.children:
        if child.type == "unary_expression":
            # Pattern: &Struct{} or &pkg.Struct{}
            for sub in child.children:
                if sub.type == "composite_literal":
                    type_node = find_child_by_type(sub, "type_identifier")
                    if type_node:
                        return node_text(type_node, source)
                    # qualified: &pkg.Struct{}
                    qual_node = find_child_by_type(sub, "qualified_type")
                    if qual_node:
                        tid = find_child_by_type(qual_node, "type_identifier")
                        if tid:
                            return node_text(tid, source)
        elif child.type == "call_expression":
            # Pattern: (*Struct)(nil)
            # call_expression → function: parenthesized_expression, arguments: argument_list
            func_node = find_child_by_field(child, "function")
            if func_node and func_node.type == "parenthesized_expression":
                # Inside parenthesized_expression: unary_expression(*) → identifier
                for sub in func_node.children:
                    if sub.type == "unary_expression":
                        tid = find_child_by_type(sub, "identifier")
                        if tid:
                            return node_text(tid, source)
    return None


def _extract_receiver_type_from_node(
    receiver_node: "tree_sitter.Node",
    source: bytes,
) -> str:
    """Extract the receiver type name from a method_declaration's receiver node.

    Given the parameter_list node that serves as the receiver, walks through
    its children to find the parameter_declaration and extracts the type name.
    Handles both value receivers (``u User``) and pointer receivers (``u *User``),
    returning just the type name in either case (e.g., ``"User"``).

    Returns an empty string if no type can be extracted.
    """
    for child in receiver_node.children:
        if child.type == "parameter_declaration":
            type_node = find_child_by_field(child, "type")
            if type_node:
                if type_node.type == "pointer_type":
                    elem_node = find_child_by_type(type_node, "type_identifier")
                    if elem_node:
                        return node_text(elem_node, source)
                elif type_node.type == "type_identifier":
                    return node_text(type_node, source)
    return ""  # pragma: no cover - well-formed Go always has a typed receiver


def _get_enclosing_func_name(
    node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Walk up the tree to find the enclosing function/method name.

    For method declarations, returns the qualified name (``Type.Method``).
    For function declarations, returns the simple function name.
    For positions outside any function, returns None.

    Used to scope variable type bindings to their enclosing function.
    """
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            name_node = find_child_by_field(current, "name")
            if name_node:
                return node_text(name_node, source)
        elif current.type == "method_declaration":
            name_node = find_child_by_field(current, "name")
            receiver_node = find_child_by_field(current, "receiver")
            if name_node:
                method_name = node_text(name_node, source)
                if receiver_node:
                    receiver_type = _extract_receiver_type_from_node(
                        receiver_node, source,
                    )
                    if receiver_type:
                        return f"{receiver_type}.{method_name}"
                return method_name  # pragma: no cover - methods always have receivers
        current = current.parent
    return None


def _go_visibility_modifiers(name: str) -> list[str]:
    """Derive visibility modifiers from Go naming convention.

    In Go, identifiers starting with an uppercase letter are exported (public).
    All others are unexported (package-private).  We use Go-native terms
    so the cross-language normalization layer (WI-silik) can map them.
    """
    # For qualified names like "Receiver.Method", check the method part
    short = name.rsplit(".", 1)[-1] if "." in name else name
    if short and short[0].isupper():
        return ["exported"]
    return ["unexported"]


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Go file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    """
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()

    # Extract import aliases for this file (used later in edge extraction)
    analysis.import_aliases = _extract_import_aliases(tree.root_node, source)

    # Collect interface-implementation assertions: struct_name -> [interface_names]
    # Populated during tree walk, applied to struct symbols after extraction.
    impl_assertions: dict[str, list[str]] = {}

    for node in iter_tree(tree.root_node):
        # Function declaration (including methods with receivers)
        if node.type == "function_declaration":
            name_node = find_child_by_field(node, "name")
            if name_node:
                func_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract function signature
                signature = _extract_go_signature(node, source)
                modifiers = _go_visibility_modifiers(func_name)

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_go_signature(signature)
                stable_id = make_typed_stable_id(
                    "function", norm_sig, visibility_from_modifiers(modifiers),
                ) if norm_sig else None

                symbol = Symbol(
                    id=make_symbol_id("go", str(file_path), start_line, end_line, func_name, "function"),
                    name=func_name,
                    kind="function",
                    language="go",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=stable_id,
                    signature=signature,
                    modifiers=modifiers,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[func_name] = symbol

        # Method declaration (function with receiver)
        elif node.type == "method_declaration":
            name_node = find_child_by_field(node, "name")
            receiver_node = find_child_by_field(node, "receiver")

            if name_node:
                method_name = node_text(name_node, source)
                receiver_type = ""

                if receiver_node:
                    receiver_type = _extract_receiver_type_from_node(
                        receiver_node, source,
                    )

                full_name = f"{receiver_type}.{method_name}" if receiver_type else method_name
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract method signature
                signature = _extract_go_signature(node, source)
                modifiers = _go_visibility_modifiers(full_name)

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_go_signature(signature)
                stable_id = make_typed_stable_id(
                    "method", norm_sig, visibility_from_modifiers(modifiers),
                ) if norm_sig else None

                symbol = Symbol(
                    id=make_symbol_id("go", str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="go",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=stable_id,
                    signature=signature,
                    modifiers=modifiers,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Type declaration (struct or interface)
        elif node.type == "type_declaration":
            for child in node.children:
                if child.type == "type_spec":
                    name_node = find_child_by_field(child, "name")
                    type_node = find_child_by_field(child, "type")

                    if name_node and type_node:
                        type_name = node_text(name_node, source)
                        start_line = child.start_point[0] + 1
                        end_line = child.end_point[0] + 1

                        if type_node.type == "struct_type":
                            kind = "struct"
                        elif type_node.type == "interface_type":
                            kind = "interface"
                        else:
                            kind = "type"

                        symbol = Symbol(
                            id=make_symbol_id("go", str(file_path), start_line, end_line, type_name, kind),
                            name=type_name,
                            kind=kind,
                            language="go",
                            path=str(file_path),
                            span=Span(
                                start_line=start_line,
                                end_line=end_line,
                                start_col=child.start_point[1],
                                end_col=child.end_point[1],
                            ),
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            modifiers=_go_visibility_modifiers(type_name),
                        )
                        analysis.symbols.append(symbol)
                        analysis.symbol_by_name[type_name] = symbol

        # Interface implementation assertion: var _ Interface = &Struct{}
        elif node.type == "var_declaration":
            for child in node.children:
                if child.type == "var_spec":
                    _detect_interface_assertion(child, source, impl_assertions)

    # Populate docstrings from tree-sitter comments in a single pass
    populate_docstrings_from_tree(tree.root_node, source, analysis.symbols)

    # Apply interface assertions to struct symbols as base_classes metadata
    for sym in analysis.symbols:
        if sym.kind == "struct" and sym.name in impl_assertions:
            if sym.meta is None:
                sym.meta = {}
            sym.meta.setdefault("base_classes", []).extend(
                impl_assertions[sym.name]
            )

    return analysis


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/method.

    For calls inside anonymous functions (func_literal), continues walking up
    to find the containing named function. This enables call attribution for
    patterns like: go func() { helper() }()

    For method declarations, tries the qualified name first (``Type.Method``)
    to avoid incorrect attribution when multiple types define the same method
    name. Falls back to short name if the qualified name isn't in local_symbols.

    Args:
        node: The current node.
        source: Source bytes for extracting text.
        local_symbols: Map of function names to Symbol objects.

    Returns:
        The Symbol for the enclosing function, or None if not inside a function.
    """
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            name_node = find_child_by_field(current, "name")
            if name_node:
                func_name = node_text(name_node, source)
                if func_name in local_symbols:
                    return local_symbols[func_name]
        elif current.type == "method_declaration":
            name_node = find_child_by_field(current, "name")
            if name_node:
                method_name = node_text(name_node, source)
                # Try qualified name first to handle same-name methods
                receiver_node = find_child_by_field(current, "receiver")
                if receiver_node:
                    receiver_type = _extract_receiver_type_from_node(
                        receiver_node, source,
                    )
                    if receiver_type:
                        qualified = f"{receiver_type}.{method_name}"
                        if qualified in local_symbols:
                            return local_symbols[qualified]
                # Fall back to short name (when qualified name not in local_symbols)
                if method_name in local_symbols:  # pragma: no cover - qualified always present
                    return local_symbols[method_name]  # pragma: no cover
        # For func_literal (anonymous functions), continue walking up
        # to find the containing named function rather than returning None
        # This handles: go func() { helper() }(), callbacks, etc.
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_go_var_types(
    root_node: "tree_sitter.Node",
    source: bytes,
) -> dict[str, dict[str, str]]:
    """Extract function-scoped variable-to-type-name mappings from Go code.

    Scans for statements and declarations where a variable is bound to a
    known type, scoped by the enclosing function/method name.

    Recognized patterns:

        s := &Server{}            → s has type Server (in enclosing func)
        s := Server{}             → s has type Server (in enclosing func)
        var c Client              → c has type Client (in enclosing func)
        var p *Client             → p has type Client (in enclosing func)
        func foo(s *Server)       → s has type Server (in foo)
        func (s *Server) Foo()    → s has type Server (in Server.Foo)

    Only the first assignment to a variable within a function wins
    (single-assignment SSA assumption per function scope). Built-in types
    (string, int, etc.) are excluded because they don't correspond to
    user-defined methods.

    Returns a nested dict: ``{func_name: {var_name: type_name}}``.
    For methods, func_name is qualified (``Type.Method``).
    """
    # Go built-in types that don't have user-defined methods
    _GO_BUILTINS = frozenset({
        "string", "int", "int8", "int16", "int32", "int64",
        "uint", "uint8", "uint16", "uint32", "uint64",
        "float32", "float64", "complex64", "complex128",
        "bool", "byte", "rune", "error", "any",
    })
    scoped_var_types: dict[str, dict[str, str]] = {}

    for node in iter_tree(root_node):
        # Pattern 1: Short var declaration  s := &Server{} or s := Server{}
        if node.type == "short_var_declaration":
            # Left side: expression_list with identifier(s)
            lhs = node.children[0] if node.children else None
            rhs = node.children[-1] if len(node.children) >= 3 else None
            if lhs is None or rhs is None:
                continue  # pragma: no cover - tree-sitter always produces valid AST

            # Get var name from first identifier in left expression_list
            var_name = None
            if lhs.type == "expression_list":
                for child in lhs.children:
                    if child.type == "identifier":
                        var_name = node_text(child, source)
                        break
            if var_name is None:
                continue  # pragma: no cover - short var always has identifier LHS

            # Scope to enclosing function
            func_name = _get_enclosing_func_name(node, source)
            if func_name is None:
                continue  # pragma: no cover - short vars always inside functions
            func_vars = scoped_var_types.setdefault(func_name, {})
            if var_name in func_vars:
                continue  # pragma: no cover - Go forbids redeclaration in same scope

            # Get type from right side
            type_name = _type_from_rhs(rhs, source)
            if type_name and type_name not in _GO_BUILTINS:
                func_vars[var_name] = type_name

        # Pattern 2: Var declaration  var c Client  or  var p *Client
        elif node.type == "var_spec":
            # var_spec children: identifier, type_identifier (or pointer_type)
            name_node = None
            type_node = None
            for child in node.children:
                if child.type == "identifier" and name_node is None:
                    name_node = child
                elif child.type in ("type_identifier", "pointer_type"):
                    type_node = child

            if name_node is None or type_node is None:
                continue

            var_name = node_text(name_node, source)

            # Scope to enclosing function (file-level vars have no enclosing func)
            func_name = _get_enclosing_func_name(node, source)
            if func_name is None:
                continue
            func_vars = scoped_var_types.setdefault(func_name, {})
            if var_name in func_vars:
                continue  # pragma: no cover - Go forbids redeclaration in same scope

            type_name = _type_identifier_from_node(type_node, source)
            if type_name and type_name not in _GO_BUILTINS:
                func_vars[var_name] = type_name

        # Pattern 3: Function/method parameters
        elif node.type == "parameter_declaration":
            # Only extract params that are inside a parameter_list of a
            # function_declaration or method_declaration (not return types)
            parent = node.parent
            if parent is None or parent.type != "parameter_list":
                continue  # pragma: no cover - params always in parameter_list
            grandparent = parent.parent
            if grandparent is None or grandparent.type not in (
                "function_declaration", "method_declaration",
            ):
                continue
            # Must be the *parameters* list, not the result list
            params_node = find_child_by_field(grandparent, "parameters")
            if params_node is None or params_node.id != parent.id:
                continue

            name_node = None
            type_node = None
            for child in node.children:
                if child.type == "identifier" and name_node is None:
                    name_node = child
                elif child.type in ("type_identifier", "pointer_type"):
                    type_node = child

            if name_node is None or type_node is None:
                continue

            var_name = node_text(name_node, source)

            # Scope to enclosing function
            func_name = _get_enclosing_func_name(node, source)
            if func_name is None:
                continue  # pragma: no cover - params always inside functions
            func_vars = scoped_var_types.setdefault(func_name, {})
            if var_name in func_vars:
                continue  # pragma: no cover - Go forbids duplicate param names

            type_name = _type_identifier_from_node(type_node, source)
            if type_name and type_name not in _GO_BUILTINS:
                func_vars[var_name] = type_name

        # Pattern 4: Method receiver  func (s *Server) Foo()
        # The receiver variable (e.g. ``s``) has the receiver type (e.g.
        # ``Server``), scoped to the method's qualified name (``Server.Foo``).
        # This enables self-method calls like ``s.Close()`` inside
        # ``Server.Cleanup()`` to resolve via typed_receiver_call.
        elif node.type == "method_declaration":
            receiver_node = find_child_by_field(node, "receiver")
            name_node = find_child_by_field(node, "name")
            if receiver_node is None or name_node is None:
                continue  # pragma: no cover - well-formed Go methods have both
            method_name = node_text(name_node, source)
            receiver_type = _extract_receiver_type_from_node(
                receiver_node, source,
            )
            if not receiver_type:
                continue  # pragma: no cover - well-formed Go receivers have types
            qualified_name = f"{receiver_type}.{method_name}"
            # Extract receiver parameter name from the parameter_declaration
            for child in receiver_node.children:
                if child.type == "parameter_declaration":
                    for param_child in child.children:
                        if param_child.type == "identifier":
                            var_name = node_text(param_child, source)
                            func_vars = scoped_var_types.setdefault(
                                qualified_name, {},
                            )
                            func_vars[var_name] = receiver_type
                            break
                    break

    return scoped_var_types


def _type_from_rhs(
    rhs_node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract a type name from the right side of a short var declaration.

    Handles:
    - expression_list wrapping: ``expression_list -> unary_expression / composite_literal``
    - ``&Server{}`` → unary_expression(&, composite_literal(type_identifier=Server))
    - ``Server{}`` → composite_literal(type_identifier=Server)

    Returns the type name string or None.
    """
    node = rhs_node
    # Unwrap expression_list
    if node.type == "expression_list":
        for child in node.children:
            if child.type in ("unary_expression", "composite_literal"):
                node = child
                break
        else:
            return None

    # &Server{} → unary_expression with & operator
    if node.type == "unary_expression":
        for child in node.children:
            if child.type == "composite_literal":
                node = child
                break
        else:
            return None  # pragma: no cover - defensive for unrecognized unary

    # Server{} → composite_literal with type_identifier
    if node.type == "composite_literal":
        for child in node.children:
            if child.type == "type_identifier":
                return node_text(child, source)
    return None  # pragma: no cover - defensive for non-composite literal RHS


def _type_identifier_from_node(
    type_node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract the type name from a type node, stripping pointer indirection.

    Handles:
    - ``type_identifier`` → direct type name
    - ``pointer_type`` → unwrap * to get type_identifier
    """
    if type_node.type == "type_identifier":
        return node_text(type_node, source)
    elif type_node.type == "pointer_type":
        for child in type_node.children:
            if child.type == "type_identifier":
                return node_text(child, source)
    return None


def _extract_function_reference_edges(
    args_node: "tree_sitter.Node",
    source: bytes,
    current_function: Symbol,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, list[Symbol]],
    resolver: ListNameResolver,
    line: int,
    edges: list[Edge],
    run: AnalysisRun,
) -> None:
    """Detect function identifiers passed as arguments and create call edges.

    In Go, functions are first-class values.  When a known function is passed
    as an argument to another function — e.g. ``r.Get("/path", ViewIssue)`` —
    it will typically be called by the receiving function.  This creates a
    ``calls`` edge with ``evidence_type="function_reference_arg"`` and lower
    confidence (0.70) to enable reverse-slice navigation from route handlers
    and callback targets.

    Handles two forms:
    - ``identifier``: simple function reference like ``handler``
    - ``selector_expression``: qualified reference like ``h.GetAPI``
    """
    for arg in args_node.children:
        if arg.type == "identifier":
            ref_name = node_text(arg, source)
            # Check if it's a known function/method (local or global)
            if ref_name in local_symbols:
                sym = local_symbols[ref_name]
                if sym.kind in ("function", "method") and sym.id != current_function.id:
                    edges.append(Edge.create(
                        src=current_function.id,
                        dst=sym.id,
                        edge_type="calls",
                        line=line,
                        evidence_type="function_reference_arg",
                        confidence=0.70,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))
            else:
                lookup_result = resolver.lookup(ref_name)
                if lookup_result.found and lookup_result.symbol.kind in ("function", "method"):
                    edges.append(Edge.create(
                        src=current_function.id,
                        dst=lookup_result.symbol.id,
                        edge_type="calls",
                        line=line,
                        evidence_type="function_reference_arg",
                        confidence=0.70 * lookup_result.confidence,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))
        elif arg.type == "selector_expression":
            # h.GetAPI or pkg.Handler
            field_node = find_child_by_field(arg, "field")
            if field_node:
                ref_name = node_text(field_node, source)
                # Try full selector text first (e.g., "handlers.GetAPI")
                full_ref = node_text(arg, source)
                if full_ref in local_symbols:  # pragma: no cover - selector text rarely matches symbol name
                    sym = local_symbols[full_ref]
                    if sym.kind in ("function", "method") and sym.id != current_function.id:
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=sym.id,
                            edge_type="calls",
                            line=line,
                            evidence_type="function_reference_arg",
                            confidence=0.70,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))
                        continue
                # Try short name via resolver
                lookup_result = resolver.lookup(ref_name)
                if lookup_result.found and lookup_result.symbol.kind in ("function", "method"):
                    edges.append(Edge.create(
                        src=current_function.id,
                        dst=lookup_result.symbol.id,
                        edge_type="calls",
                        line=line,
                        evidence_type="function_reference_arg",
                        confidence=0.70 * lookup_result.confidence,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, list[Symbol]],
    run: AnalysisRun,
    import_aliases: dict[str, str] | None = None,
    resolver: ListNameResolver | None = None,
) -> list[Edge]:
    """Extract call and import edges from a file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    Uses import_aliases to disambiguate when multiple files define the same symbol.
    Tracks variable types from assignments and parameters to disambiguate
    method calls when multiple types define the same method name.
    """
    if import_aliases is None:
        import_aliases = {}
    if resolver is None:
        resolver = ListNameResolver(global_symbols, ambiguity_threshold=3)

    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return []

    edges: list[Edge] = []
    file_id = make_file_id("go", str(file_path))

    # Extract function-scoped variable-to-type bindings for receiver disambiguation
    scoped_var_types = _extract_go_var_types(tree.root_node, source)

    for node in iter_tree(tree.root_node):
        # Detect import statements
        if node.type == "import_declaration":
            # Handle both single imports and import blocks
            for child in node.children:
                if child.type == "import_spec":
                    path_node = find_child_by_field(child, "path")
                    if path_node:
                        import_path = node_text(path_node, source).strip('"')
                        edges.append(Edge.create(
                            src=file_id,
                            dst=f"go:{import_path}:0-0:package:package",
                            edge_type="imports",
                            line=child.start_point[0] + 1,
                            evidence_type="import_declaration",
                            confidence=0.95,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))
                elif child.type == "import_spec_list":
                    for spec in child.children:
                        if spec.type == "import_spec":
                            path_node = find_child_by_field(spec, "path")
                            if path_node:
                                import_path = node_text(path_node, source).strip('"')
                                edges.append(Edge.create(
                                    src=file_id,
                                    dst=f"go:{import_path}:0-0:package:package",
                                    edge_type="imports",
                                    line=spec.start_point[0] + 1,
                                    evidence_type="import_declaration",
                                    confidence=0.95,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))

        # Detect function calls
        elif node.type == "call_expression":
            current_function = _get_enclosing_function(node, source, local_symbols)
            if current_function is not None:
                # Get var_types scoped to the current enclosing function
                var_types = scoped_var_types.get(current_function.name, {})

                func_node = find_child_by_field(node, "function")
                if func_node:
                    callee_name = None
                    import_path_hint = None

                    if func_node.type == "identifier":
                        # Simple call: helper()
                        callee_name = node_text(func_node, source)
                    elif func_node.type == "selector_expression":
                        # Method call: obj.Method() or pkg.Func()
                        operand_node = find_child_by_field(func_node, "operand")
                        field_node = find_child_by_field(func_node, "field")
                        if field_node:
                            callee_name = node_text(field_node, source)
                        # Check if operand is a package alias
                        if operand_node and operand_node.type == "identifier":
                            alias = node_text(operand_node, source)
                            if alias in import_aliases:
                                import_path_hint = import_aliases[alias]
                            # Check if operand has an inferred type for
                            # receiver-type method disambiguation
                            elif alias in var_types:
                                receiver_type = var_types[alias]
                                qualified_name = f"{receiver_type}.{callee_name}"
                                # Try qualified name in local or global symbols
                                if qualified_name in local_symbols:
                                    callee = local_symbols[qualified_name]
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=callee.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="typed_receiver_call",
                                        confidence=0.85,
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                    ))
                                    callee_name = None  # Already resolved
                                elif qualified_name in global_symbols:
                                    # Direct lookup by qualified name
                                    candidates = global_symbols[qualified_name]
                                    if candidates:
                                        edges.append(Edge.create(
                                            src=current_function.id,
                                            dst=candidates[0].id,
                                            edge_type="calls",
                                            line=node.start_point[0] + 1,
                                            evidence_type="typed_receiver_call",
                                            confidence=0.85,
                                            origin=PASS_ID,
                                            origin_run_id=run.execution_id,
                                        ))
                                        callee_name = None  # Already resolved
                        # Unified ambiguity guard for ALL selector expressions:
                        # covers simple identifiers (x.Close()), chained calls
                        # (getWriter().Close()), field access (resp.Body.Close()),
                        # and index expressions (items[0].Close()).
                        # When receiver type is unknown and 3+ types define the
                        # method, produce an unresolved edge instead of picking
                        # an arbitrary candidate.
                        if (
                            callee_name
                            and import_path_hint is None
                            and callee_name in global_symbols
                            and len(global_symbols[callee_name]) >= 3
                        ):
                            dst_id = f"go:external:0-0:{callee_name}:unresolved"
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=dst_id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ambiguous_method_call",
                                confidence=0.50,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))
                            callee_name = None  # Already handled

                    if callee_name:
                        # Check local symbols first — but NOT when the call
                        # is package-qualified (import_path_hint set), because
                        # e.g. bug.AddComment() should resolve to the imported
                        # package, not a local method named AddComment.
                        if callee_name in local_symbols and import_path_hint is None:
                            callee = local_symbols[callee_name]
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="function_call",
                                confidence=0.85,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))
                        # Check global symbols with disambiguation via ListNameResolver
                        else:
                            lookup_result = resolver.lookup(callee_name, path_hint=import_path_hint)
                            if lookup_result.found:
                                # Scale base confidence by resolver's confidence multiplier
                                edge_confidence = 0.80 * lookup_result.confidence
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="function_call",
                                    confidence=edge_confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))
                            # Bug #2 fix: Create edge for external/unresolved method calls
                            # This enables linkers to potentially match across languages
                            elif func_node.type == "selector_expression":
                                # For s.Method() where Method is external, create unresolved edge
                                # Use the import path if available to make the ID more specific
                                if import_path_hint:
                                    # e.g., go:google.golang.org/grpc:0-0:RegisterService:unresolved
                                    dst_id = f"go:{import_path_hint}:0-0:{callee_name}:unresolved"
                                else:
                                    # Fallback: use "external" as the path
                                    dst_id = f"go:external:0-0:{callee_name}:unresolved"
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=dst_id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="unresolved_method_call",
                                    confidence=0.50,  # Lower confidence for unresolved
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))

                # Detect function references passed as arguments
                # e.g., register(handler), r.Get("/path", ViewIssue)
                args_node = find_child_by_field(node, "arguments")
                if args_node and current_function is not None:
                    _extract_function_reference_edges(
                        args_node, source, current_function,
                        local_symbols, global_symbols, resolver,
                        node.start_point[0] + 1, edges, run,
                    )

    return edges


def _extract_handler_name(
    node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract handler name from a call argument node.

    Handles three cases:
    - identifier: ``listUsers`` -> ``"listUsers"``
    - selector_expression: ``handlers.GetAPI`` -> ``"handlers.GetAPI"``
    - call_expression: ``httpapi.NewHandler(env)`` -> ``"httpapi.NewHandler"``

    Returns None if the node type is not recognized.
    """
    if node.type == "identifier":
        return node_text(node, source)
    elif node.type == "selector_expression":
        return node_text(node, source)
    elif node.type == "call_expression":
        func_node = find_child_by_field(node, "function")
        if func_node:
            return node_text(func_node, source)
    return None


def _extract_first_string_arg(
    args_node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract the first string literal from an argument list.

    Returns the string content without quotes, or None if no string arg found.
    """
    for arg in args_node.children:
        if arg.type == "interpreted_string_literal":
            content_node = find_child_by_type(arg, "interpreted_string_literal_content")
            if content_node:
                return node_text(content_node, source)
            return node_text(arg, source).strip('"')  # pragma: no cover
    return None  # pragma: no cover - only called with argument lists containing strings


def _extract_gorilla_chain_route(
    call_node: "tree_sitter.Node",
    source: bytes,
) -> tuple[str | None, str | None, str | None]:
    """Walk a Gorilla mux builder chain to extract route path, HTTP method, and handler.

    Given an outermost call_expression whose method is Handler/HandlerFunc,
    walks the chain backwards to find Path/PathPrefix and optional Methods calls.

    Example chains:
    - ``router.Path("/api").Handler(h)`` -> ("/api", None, "h")
    - ``router.Path("/api").Methods("GET").Handler(h)`` -> ("/api", "GET", "h")
    - ``router.PathPrefix("/").Handler(h)`` -> ("/", None, "h")

    Returns:
        Tuple of (route_path, http_method, handler_name).
        Any element may be None if not found.
    """
    # Step 1: Extract handler from the outermost call's arguments
    args_node = find_child_by_field(call_node, "arguments")
    handler_name = None
    if args_node:
        for arg in args_node.children:
            handler_name = _extract_handler_name(arg, source)
            if handler_name is not None:
                break

    # Step 2: Walk the chain backwards through the selector/call nesting
    route_path = None
    http_method = None

    # The function field of the outer call is a selector_expression
    # e.g., for .Handler(h), the operand of that selector is the previous call
    func_node = find_child_by_field(call_node, "function")
    if not func_node or func_node.type != "selector_expression":
        return (None, None, handler_name)  # pragma: no cover - defensive for malformed AST

    # Walk the chain: operand is the inner call_expression
    current = find_child_by_field(func_node, "operand")

    while current is not None and current.type == "call_expression":
        # Get this call's method name from its function (selector_expression)
        inner_func = find_child_by_field(current, "function")
        if not inner_func or inner_func.type != "selector_expression":
            break  # pragma: no cover - defensive for malformed AST

        inner_field = find_child_by_field(inner_func, "field")
        if not inner_field:
            break  # pragma: no cover

        method_name = node_text(inner_field, source)

        inner_args = find_child_by_field(current, "arguments")

        if method_name in GORILLA_PATH_METHODS and inner_args:
            route_path = _extract_first_string_arg(inner_args, source)
            break  # Path/PathPrefix is the start of the chain

        if method_name == "Methods" and inner_args:
            method_str = _extract_first_string_arg(inner_args, source)
            if method_str:
                http_method = method_str.upper()

        # Continue walking: the operand of this selector is the next inner call
        current = find_child_by_field(inner_func, "operand")

    return (route_path, http_method, handler_name)


def _extract_go_routes(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
) -> list[Symbol]:
    """Extract Go web framework route symbols from a tree-sitter node.

    Detects patterns like:
    - Gin/Echo: r.GET("/path", handler), e.POST("/users", createUser)
    - Fiber: app.Get("/path", handler) (lowercase methods)
    - Gorilla mux: router.HandleFunc("/path", handler), router.Handle("/path", handler)
    - Gorilla mux builder: router.Path("/path").Methods("GET").Handler(handler)

    Creates symbols with stable_id = HTTP method for route discovery.
    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    """
    routes: list[Symbol] = []

    for n in iter_tree(node):
        # Look for call_expression with selector_expression function
        if n.type == "call_expression":
            func_node = find_child_by_field(n, "function")

            if func_node and func_node.type == "selector_expression":
                # Get the method name (e.g., GET, POST, Get, Post)
                field_node = find_child_by_field(func_node, "field")

                if field_node:
                    method_name = node_text(field_node, source)

                    if method_name in GO_HTTP_METHODS:
                        # Gin/Echo/Fiber: r.GET("/path", handler)
                        args_node = find_child_by_field(n, "arguments")
                        if args_node:
                            route_path = _extract_first_string_arg(args_node, source)
                            handler_name = None

                            if route_path:
                                for arg in args_node.children:
                                    if arg.type == "interpreted_string_literal":
                                        continue  # Skip the path arg
                                    handler_name = _extract_handler_name(arg, source)
                                    if handler_name is not None:
                                        break

                            if route_path and handler_name:
                                normalized_method = method_name.upper()
                                start_line = n.start_point[0] + 1
                                end_line = n.end_point[0] + 1

                                route_sym = Symbol(
                                    id=make_symbol_id(
                                        "go", str(file_path), start_line, end_line,
                                        f"{normalized_method} {route_path}", "route"
                                    ),
                                    stable_id=make_route_stable_id(normalized_method, route_path),
                                    name=handler_name,
                                    kind="route",
                                    language="go",
                                    path=str(file_path),
                                    span=Span(
                                        start_line=start_line,
                                        end_line=end_line,
                                        start_col=n.start_point[1],
                                        end_col=n.end_point[1],
                                    ),
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    meta={
                                        "route_path": route_path,
                                        "http_method": normalized_method,
                                        "handler_name": handler_name,
                                    },
                                )
                                routes.append(route_sym)

                    elif method_name in GORILLA_HANDLE_METHODS:
                        # Gorilla mux simple: router.HandleFunc("/path", handler)
                        args_node = find_child_by_field(n, "arguments")
                        if args_node:
                            route_path = _extract_first_string_arg(args_node, source)
                            handler_name = None

                            if route_path:
                                for arg in args_node.children:
                                    if arg.type == "interpreted_string_literal":
                                        continue
                                    handler_name = _extract_handler_name(arg, source)
                                    if handler_name is not None:
                                        break

                            if route_path and handler_name:
                                start_line = n.start_point[0] + 1
                                end_line = n.end_point[0] + 1

                                route_sym = Symbol(
                                    id=make_symbol_id(
                                        "go", str(file_path), start_line, end_line,
                                        f"ANY {route_path}", "route"
                                    ),
                                    stable_id=make_route_stable_id("ANY", route_path),
                                    name=handler_name,
                                    kind="route",
                                    language="go",
                                    path=str(file_path),
                                    span=Span(
                                        start_line=start_line,
                                        end_line=end_line,
                                        start_col=n.start_point[1],
                                        end_col=n.end_point[1],
                                    ),
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    meta={
                                        "route_path": route_path,
                                        "http_method": "ANY",
                                        "handler_name": handler_name,
                                    },
                                )
                                routes.append(route_sym)

                    elif method_name in GORILLA_CHAIN_TERMINATORS:
                        # Gorilla mux builder: router.Path("/x").Methods("GET").Handler(h)
                        route_path, http_method, handler_name = (
                            _extract_gorilla_chain_route(n, source)
                        )

                        if route_path and handler_name:
                            normalized_method = http_method or "ANY"
                            start_line = n.start_point[0] + 1
                            end_line = n.end_point[0] + 1

                            route_sym = Symbol(
                                id=make_symbol_id(
                                    "go", str(file_path), start_line, end_line,
                                    f"{normalized_method} {route_path}", "route"
                                ),
                                stable_id=make_route_stable_id(normalized_method, route_path),
                                name=handler_name,
                                kind="route",
                                language="go",
                                path=str(file_path),
                                span=Span(
                                    start_line=start_line,
                                    end_line=end_line,
                                    start_col=n.start_point[1],
                                    end_col=n.end_point[1],
                                ),
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                meta={
                                    "route_path": route_path,
                                    "http_method": normalized_method,
                                    "handler_name": handler_name,
                                },
                            )
                            routes.append(route_sym)

    return routes


def _extract_go_usage_contexts(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
) -> list[UsageContext]:
    """Extract UsageContext records for Go web framework route calls.

    Creates UsageContext records that capture how handler functions are used
    in Gin/Echo/Chi/Fiber route registration calls. These are matched against
    YAML patterns in the enrichment phase.

    Supported patterns:
    - Gin: r.GET("/path", handler), router.POST("/users", createUser)
    - Echo: e.GET("/path", handler), echo.POST("/users", createUser)
    - Chi: r.Get("/path", handler), router.Post("/users", createUser)
    - Fiber: app.Get("/path", handler), app.Post("/users", createUser)

    Args:
        node: The root tree-sitter node
        source: Source file bytes
        file_path: Path to the source file
        symbol_by_name: Lookup table for symbols defined in this file

    Returns:
        List of UsageContext records for Go route patterns.
    """
    contexts: list[UsageContext] = []

    for n in iter_tree(node):
        if n.type != "call_expression":
            continue

        func_node = find_child_by_field(n, "function")
        if not func_node or func_node.type != "selector_expression":
            continue

        # Get the method name (e.g., GET, POST, Get, Post)
        field_node = find_child_by_field(func_node, "field")
        if not field_node:  # pragma: no cover
            continue

        method_name = node_text(field_node, source)
        if method_name not in GO_HTTP_METHODS:
            continue

        # Get the receiver name (e.g., r, router, e, echo, app)
        operand_node = find_child_by_field(func_node, "operand")
        receiver_name = node_text(operand_node, source) if operand_node else None

        # Extract arguments
        args_node = find_child_by_field(n, "arguments")
        if not args_node:  # pragma: no cover
            continue

        route_path = None
        handler_name = None

        for arg in args_node.children:
            # First string literal is the route path
            if arg.type == "interpreted_string_literal" and route_path is None:
                content_node = find_child_by_type(arg, "interpreted_string_literal_content")
                if content_node:
                    route_path = node_text(content_node, source)
                else:  # pragma: no cover
                    route_path = node_text(arg, source).strip('"')

            # Handler is usually an identifier after the path
            elif arg.type == "identifier" and route_path is not None:
                handler_name = node_text(arg, source)
                break

            # Handler could also be a selector (pkg.Handler)
            elif arg.type == "selector_expression" and route_path is not None:
                handler_name = node_text(arg, source)
                break

        if not route_path:  # pragma: no cover
            continue

        # Try to resolve handler to a symbol reference
        handler_ref = None
        if handler_name and handler_name in symbol_by_name:
            handler_ref = symbol_by_name[handler_name].id

        # Normalize method name to uppercase
        normalized_method = method_name.upper()

        # Build full call name (e.g., "r.GET", "router.Post")
        call_name = f"{receiver_name}.{method_name}" if receiver_name else method_name

        # Normalize route path
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"

        span = Span(
            start_line=n.start_point[0] + 1,
            end_line=n.end_point[0] + 1,
            start_col=n.start_point[1],
            end_col=n.end_point[1],
        )

        ctx = UsageContext.create(
            kind="call",
            context_name=call_name,
            position="args[last]",  # Handler is typically last argument
            path=str(file_path),
            span=span,
            symbol_ref=handler_ref,
            metadata={
                "route_path": normalized_path,
                "http_method": normalized_method,
                "handler_name": handler_name,
                "receiver": receiver_name,
            },
        )
        contexts.append(ctx)

    return contexts


@register_analyzer("go", priority=50)
def analyze_go(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
    """Analyze all Go files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-go is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root, max_files=max_files)


def _analyze_go_impl(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
    """Internal implementation of Go analysis.

    Called by GoTreeSitterAnalyzer.analyze() after grammar availability
    has been checked by the base class.
    """
    if not _analyzer._check_grammar_available():
        warnings.warn(
            "go analysis skipped: grammar not available. "
            "Install the required tree-sitter grammar package.",
            UserWarning,
            stacklevel=3,
        )
        return AnalysisResult(
            skipped=True,
            skip_reason="go tree-sitter grammar not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-go
    try:
        import tree_sitter_go
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_go.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return AnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load Go parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0
    files_processed = 0

    for go_file in find_go_files(repo_root):
        if max_files is not None and files_processed >= max_files:  # pragma: no cover
            break
        analysis = _extract_symbols_from_file(go_file, parser, run)
        if analysis.symbols:
            file_analyses[go_file] = analysis
            files_processed += 1
        else:
            files_skipped += 1

    # Build global symbol registry - store ALL symbols with same name
    # This enables disambiguation using import paths
    global_symbols: dict[str, list[Symbol]] = {}
    for analysis in file_analyses.values():
        for symbol in analysis.symbols:
            # Store by short name for cross-file resolution
            short_name = symbol.name.split(".")[-1] if "." in symbol.name else symbol.name
            if short_name not in global_symbols:
                global_symbols[short_name] = []
            global_symbols[short_name].append(symbol)
            if symbol.name != short_name:
                if symbol.name not in global_symbols:
                    global_symbols[symbol.name] = []
                global_symbols[symbol.name].append(symbol)

    # Pass 2: Extract edges, routes, and usage contexts
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []
    all_usage_contexts: list[UsageContext] = []

    for go_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            go_file, parser, analysis.symbol_by_name, global_symbols, run,
            analysis.import_aliases,
        )
        all_edges.extend(edges)

        # Extract web framework routes (Gin, Echo, Fiber, Gorilla mux)
        try:
            source = go_file.read_bytes()
            tree = parser.parse(source)
            routes = _extract_go_routes(tree.root_node, source, go_file, run)
            all_symbols.extend(routes)

            # Extract usage contexts for YAML pattern matching (v1.1.x)
            usage_contexts = _extract_go_usage_contexts(
                tree.root_node, source, go_file, analysis.symbol_by_name
            )
            all_usage_contexts.extend(usage_contexts)
        except (OSError, IOError):  # pragma: no cover
            pass  # Skip files that can't be read

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return AnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        usage_contexts=all_usage_contexts,
        run=run,
    )
