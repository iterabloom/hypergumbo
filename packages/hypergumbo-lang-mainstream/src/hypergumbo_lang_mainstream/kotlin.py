"""Kotlin analysis pass using tree-sitter-kotlin.

This analyzer uses tree-sitter to parse Kotlin files and extract:
- Function declarations (fun)
- Class declarations (class, data class)
- Object declarations (object)
- Interface declarations (interface)
- Method declarations (inside classes/objects)
- Function call relationships
- Import statements

If tree-sitter with Kotlin support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-kotlin is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-kotlin package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Go/Ruby/Rust/Elixir/Java/PHP/C analyzers for consistency
"""
from __future__ import annotations

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    populate_docstrings_from_tree,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    make_typed_stable_id,
    node_text,
    visibility_from_modifiers,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("kotlin")


def find_kotlin_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Kotlin files in the repository."""
    yield from find_files(repo_root, ["*.kt"])


def _is_kotlin_tree_sitter_available_legacy() -> bool:  # pragma: no cover - replaced by TreeSitterAnalyzer
    """Legacy availability check -- replaced by TreeSitterAnalyzer._check_grammar_available."""
    pass


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:
    """Find child by field name."""
    return node.child_by_field_name(field_name)


# Kotlin modifier keyword types grouped by category.
# tree-sitter-kotlin wraps each in a typed node (visibility_modifier,
# inheritance_modifier, etc.) whose single child is the keyword.
KOTLIN_MODIFIER_KEYWORDS = {
    # visibility
    "public", "private", "protected", "internal",
    # inheritance
    "open", "final", "abstract", "sealed",
    # member
    "override", "lateinit",
    # class
    "data", "inner", "enum", "annotation", "value",
    # function
    "inline", "tailrec", "operator", "infix", "suspend",
}

# Node types that wrap modifier keywords
_KOTLIN_MODIFIER_NODE_TYPES = {
    "visibility_modifier", "inheritance_modifier", "member_modifier",
    "class_modifier", "function_modifier", "type_modifier",
    "property_modifier", "parameter_modifier",
}


def _extract_modifiers(node: "tree_sitter.Node") -> list[str]:
    """Extract all modifiers from a Kotlin declaration node.

    Kotlin tree-sitter groups modifiers under a ``modifiers`` container.
    Each child is a typed wrapper (e.g. ``visibility_modifier``) whose
    single child is the keyword token (e.g. ``public``).

    Returns a list of modifier strings like ``["public", "open"]``.
    """
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "modifiers":
            for mod_node in child.children:
                if mod_node.type in _KOTLIN_MODIFIER_NODE_TYPES:
                    for kw in mod_node.children:
                        if kw.type in KOTLIN_MODIFIER_KEYWORDS:
                            modifiers.append(kw.type)
    return modifiers


def _get_enclosing_class(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing class/object/interface name."""
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "object_declaration"):
            name_node = _find_child_by_field(current, "name")
            if not name_node:  # pragma: no cover - defensive fallback
                name_node = find_child_by_type(current, "identifier")
                if not name_node:
                    name_node = find_child_by_type(current, "type_identifier")
            if name_node:
                return node_text(name_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing function/method."""
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            name_node = _find_child_by_field(current, "name")
            if not name_node:  # pragma: no cover - defensive fallback
                name_node = find_child_by_type(current, "identifier")
            if name_node:
                func_name = node_text(name_node, source)
                if func_name in local_symbols:
                    return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_kotlin_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Kotlin function declaration.

    Returns signature like:
    - "(x: Int, y: Int): Int" for regular functions
    - "(message: String)" for Unit (void) functions

    Args:
        node: The function_declaration node.
        source: The source code bytes.

    Returns:
        The signature string, or None if extraction fails.
    """
    params: list[str] = []
    return_type = None
    found_params = False

    # Iterate through children to find parameters and return type
    for child in node.children:
        if child.type == "function_value_parameters":
            found_params = True
            for subchild in child.children:
                if subchild.type == "parameter":
                    param_name = None
                    param_type = None
                    for pc in subchild.children:
                        if pc.type == "identifier" and param_name is None:
                            param_name = node_text(pc, source)
                        elif pc.type in ("user_type", "nullable_type", "function_type"):
                            param_type = node_text(pc, source)
                    if param_name and param_type:
                        params.append(f"{param_name}: {param_type}")
        # Return type comes after function_value_parameters and before function_body
        elif found_params and child.type in ("user_type", "nullable_type", "function_type"):
            return_type = node_text(child, source)

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type and return_type != "Unit":
        signature += f": {return_type}"

    return signature


def normalize_kotlin_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Kotlin signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_names_first
    return normalize_signature_names_first(signature, type_params, return_sep=":")


def _extract_kotlin_return_type_name(signature: str | None) -> str | None:
    """Extract the return type name from a Kotlin function signature.

    Kotlin signatures use the format ``(params): ReturnType``.
    Returns the type name only if it is a simple identifier (no generics,
    no nullable ``?`` suffix). Returns None for Unit return type (which is
    already omitted from the signature by ``_extract_kotlin_signature``).

    Examples:
        ``"(): ServiceClient"`` → ``"ServiceClient"``
        ``"(name: String): User"`` → ``"User"``
        ``"()"`` → ``None``
        ``"(): List<String>"`` → ``None``
        ``"(): String?"`` → ``None``
    """
    if not signature:
        return None
    paren_idx = signature.rfind(")")
    if paren_idx < 0:
        return None  # pragma: no cover - all Kotlin signatures contain parens
    after_paren = signature[paren_idx + 1:].strip()
    if not after_paren.startswith(":"):
        return None
    ret_part = after_paren[1:].strip()
    if ret_part and ret_part.isidentifier():
        return ret_part
    return None


def _extract_param_types(
    node: "tree_sitter.Node", source: bytes
) -> dict[str, str]:
    """Extract parameter name -> type mapping from a function declaration.

    This enables type inference for method calls on parameters, e.g.:
        fun process(client: Client) {
            client.send()  // resolves to Client.send
        }

    Returns:
        Dict mapping parameter names to their type names (simple name only).
    """
    param_types: dict[str, str] = {}

    for child in node.children:
        if child.type == "function_value_parameters":
            for subchild in child.children:
                if subchild.type == "parameter":
                    param_name = None
                    param_type = None
                    for pc in subchild.children:
                        if pc.type == "identifier" and param_name is None:
                            param_name = node_text(pc, source)
                        elif pc.type in ("user_type", "nullable_type", "function_type"):
                            type_text = node_text(pc, source)
                            # Extract base type name (strip nullable ?, generics <>, etc.)
                            if type_text:
                                # Remove nullable suffix
                                type_text = type_text.rstrip("?")
                                # Remove generic parameters
                                if "<" in type_text:  # pragma: no cover
                                    type_text = type_text.split("<")[0]
                                param_type = type_text
                    if param_name and param_type:
                        param_types[param_name] = param_type

    return param_types


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file.

    Type inference tracks types from:
    - Constructor calls: val obj = MyClass() -> obj has type MyClass
    - Function parameters: fun process(client: Client) -> client has type Client

    Type inference does NOT track types from function returns (val obj = getMyClass()).
    """

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)
    imports: dict[str, str] = field(default_factory=dict)


def _extract_imports(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import mappings from a parsed Kotlin tree.

    Tracks: import com.example.ClassName -> ClassName: com.example.ClassName

    Returns dict mapping simple class name -> fully qualified name.
    """
    imports: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import":
            continue

        # Find the qualified_identifier
        id_node = find_child_by_type(node, "qualified_identifier")
        if not id_node:
            id_node = find_child_by_type(node, "identifier")
        if id_node:
            full_name = node_text(id_node, source)
            # Extract simple name (last part of qualified name)
            simple_name = full_name.split(".")[-1]
            imports[simple_name] = full_name

    return imports


def _extract_delegation_specifiers(
    node: "tree_sitter.Node",
    source: bytes,
) -> list[str]:
    """Extract base classes and interfaces from delegation_specifiers (META-001).

    Kotlin syntax: class Foo : Bar(), Interface1, Interface2 { }
    AST structure:
        class_declaration
            identifier "Foo"
            delegation_specifiers
                delegation_specifier
                    constructor_invocation (for classes with ())
                        user_type
                            identifier "Bar"
                delegation_specifier
                    user_type (for interfaces without ())
                        identifier "Interface1"

    Returns list of base type names (without parentheses).
    """
    base_classes: list[str] = []

    # Find delegation_specifiers child
    for child in node.children:
        if child.type == "delegation_specifiers":
            # Iterate through each delegation_specifier
            for spec in child.children:
                if spec.type != "delegation_specifier":
                    continue

                # Look for user_type directly or inside constructor_invocation
                for spec_child in spec.children:
                    if spec_child.type == "user_type":
                        # Interface implementation (no parentheses)
                        base_name = _extract_user_type_name(spec_child, source)
                        if base_name:
                            base_classes.append(base_name)
                        break
                    elif spec_child.type == "constructor_invocation":
                        # Class inheritance (with parentheses)
                        for inv_child in spec_child.children:
                            if inv_child.type == "user_type":
                                base_name = _extract_user_type_name(inv_child, source)
                                if base_name:
                                    base_classes.append(base_name)
                                break
                        break

    return base_classes


def _extract_user_type_name(user_type_node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract the type name from a user_type node.

    Handles both simple names (List) and qualified names (kotlin.collections.List).
    Returns just the simple name.
    """
    for child in user_type_node.children:
        if child.type in ("simple_identifier", "identifier", "type_identifier"):
            return node_text(child, source)
    # Defensive fallback for unexpected AST shapes
    if True:  # pragma: no cover
        text = node_text(user_type_node, source)
        if "<" in text:
            text = text.split("<")[0]
        return text if text else None
    return None  # unreachable, but keeps mypy happy


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single Kotlin file."""
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return FileAnalysis()

    analysis = FileAnalysis()
    analysis.imports = _extract_imports(tree, source)

    for node in iter_tree(tree.root_node):
        # Function declaration
        if node.type == "function_declaration":
            name_node = _find_child_by_field(node, "name")
            if not name_node:  # pragma: no cover - grammar fallback
                name_node = find_child_by_type(node, "identifier")

            if name_node:
                func_name = node_text(name_node, source)
                enclosing_class = _get_enclosing_class(node, source)
                if enclosing_class:
                    full_name = f"{enclosing_class}.{func_name}"
                    kind = "method"
                else:
                    full_name = func_name
                    kind = "function"

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract signature
                signature = _extract_kotlin_signature(node, source)
                modifiers = _extract_modifiers(node)

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_kotlin_signature(signature)
                stable_id = make_typed_stable_id(
                    kind, norm_sig, visibility_from_modifiers(modifiers),
                ) if norm_sig else None

                symbol = Symbol(
                    id=make_symbol_id("kotlin", str(file_path), start_line, end_line, full_name, kind),
                    name=full_name,
                    kind=kind,
                    language="kotlin",
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
                analysis.symbol_by_name[full_name] = symbol

        # Class declaration (also handles interfaces in Kotlin AST)
        elif node.type == "class_declaration":
            # Check if it's an interface
            is_interface = find_child_by_type(node, "interface") is not None

            name_node = _find_child_by_field(node, "name")
            if not name_node:  # pragma: no cover - grammar fallback
                name_node = find_child_by_type(node, "identifier")

            if name_node:
                type_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                kind = "interface" if is_interface else "class"

                # Extract base classes/interfaces (META-001)
                meta: dict[str, object] | None = None
                base_classes = _extract_delegation_specifiers(node, source)
                if base_classes:
                    meta = {"base_classes": base_classes}

                symbol = Symbol(
                    id=make_symbol_id("kotlin", str(file_path), start_line, end_line, type_name, kind),
                    name=type_name,
                    kind=kind,
                    language="kotlin",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta=meta,
                    modifiers=_extract_modifiers(node),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[type_name] = symbol

        # Object declaration
        elif node.type == "object_declaration":
            name_node = _find_child_by_field(node, "name")
            if not name_node:  # pragma: no cover - grammar fallback
                name_node = find_child_by_type(node, "type_identifier")

            if name_node:
                object_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=make_symbol_id("kotlin", str(file_path), start_line, end_line, object_name, "object"),
                    name=object_name,
                    kind="object",
                    language="kotlin",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    modifiers=_extract_modifiers(node),
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[object_name] = symbol

    populate_docstrings_from_tree(tree.root_node, source, analysis.symbols)

    return analysis


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    imports: dict[str, str],
    run: AnalysisRun,
    resolver: NameResolver | None = None,
    method_resolver: ListNameResolver | None = None,
) -> list[Edge]:
    """Extract call and import edges from a file.

    Handles:
    - Simple function calls: helper()
    - Navigation calls: Object.method(), instance.method()
    - Type inference from constructor assignments: val x = ClassName()
    """
    if resolver is None:
        resolver = NameResolver(global_symbols)
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return []

    edges: list[Edge] = []
    file_id = make_file_id("kotlin", str(file_path))

    # Track variable types from constructor calls: val x = ClassName()
    var_types: dict[str, str] = {}

    # Build class/object symbols dict for static call resolution
    class_symbols: dict[str, Symbol] = {
        s.name: s for s in global_symbols.values()
        if s.kind in ("class", "object", "interface")
    }

    for node in iter_tree(tree.root_node):
        # Detect import statements
        if node.type == "import":
            # Get the qualified identifier being imported
            id_node = find_child_by_type(node, "qualified_identifier")
            if not id_node:
                id_node = find_child_by_type(node, "identifier")
            if id_node:
                import_path = node_text(id_node, source)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"kotlin:{import_path}:0-0:package:package",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="import_statement",
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                ))

        # Track constructor parameter types: class Ctrl(private val svc: Service)
        elif node.type == "class_parameter":
            param_name_node = find_child_by_type(node, "identifier")
            type_node = find_child_by_type(node, "user_type")
            if param_name_node and type_node:
                param_name = node_text(param_name_node, source)
                type_name = node_text(type_node, source)
                if type_name:
                    # Strip generics
                    if "<" in type_name:
                        type_name = type_name.split("<")[0]
                    if type_name[0].isupper():
                        var_types[param_name] = type_name

        # Track variable types from property declarations: val x = ClassName()
        elif node.type == "property_declaration":
            # Find variable_declaration and call_expression children
            var_decl = find_child_by_type(node, "variable_declaration")
            call_expr = find_child_by_type(node, "call_expression")
            if var_decl and call_expr:
                var_name_node = find_child_by_type(var_decl, "identifier")
                # Check if call is a simple constructor (identifier, not navigation)
                callee_node = find_child_by_type(call_expr, "identifier")
                if var_name_node and callee_node:
                    var_name = node_text(var_name_node, source)
                    type_name = node_text(callee_node, source)
                    # Only track if type_name looks like a class (capitalized)
                    if type_name and type_name[0].isupper():
                        var_types[var_name] = type_name

        # Function declarations - extract parameter types for type inference
        elif node.type == "function_declaration":
            param_types = _extract_param_types(node, source)
            # Add parameter types to var_types for method call resolution
            for param_name, param_type in param_types.items():
                var_types[param_name] = param_type

        # Detect function calls
        elif node.type == "call_expression":
            current_function = _get_enclosing_function(node, source, local_symbols)
            if current_function is None:  # pragma: no cover
                continue

            # Check for navigation_expression (Object.method() or instance.method())
            nav_node = find_child_by_type(node, "navigation_expression")
            if nav_node:
                # Navigation expression structure varies:
                # - Object.method(): identifier . identifier
                # - this.method(): this_expression . identifier
                # - instance.method(): identifier . identifier
                receiver_node = None
                method_node = None

                for child in nav_node.children:
                    if child.type == "this_expression":
                        receiver_node = child
                    elif child.type == "navigation_expression":
                        # Nested: this.property.method() — inner nav is
                        # this.property, outer identifier is method
                        receiver_node = child
                    elif child.type == "identifier":
                        if receiver_node is None:
                            receiver_node = child
                        else:
                            method_node = child

                if receiver_node and method_node:
                    method_name = node_text(method_node, source)
                    edge_added = False

                    # Case 1: this.method() - call on current instance
                    if receiver_node.type == "this_expression":
                        # Look for method in enclosing class
                        enclosing_class = _get_enclosing_class(node, source)
                        if enclosing_class:
                            candidate = f"{enclosing_class}.{method_name}"
                            lookup_result = resolver.lookup(candidate)
                            if lookup_result.found and lookup_result.symbol is not None:
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    confidence=0.90 * lookup_result.confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_call_this",
                                ))
                                edge_added = True

                    # Case 1b: this.property.method() - call on injected dep
                    elif receiver_node.type == "navigation_expression":
                        this_node = find_child_by_type(
                            receiver_node, "this_expression"
                        )
                        prop_node = find_child_by_type(
                            receiver_node, "identifier"
                        )
                        if this_node and prop_node:
                            prop_name = node_text(prop_node, source)
                            if prop_name in var_types:
                                type_name = var_types[prop_name]
                                candidate = f"{type_name}.{method_name}"
                                import_hint = imports.get(type_name)
                                lookup_result = resolver.lookup(
                                    candidate, path_hint=import_hint
                                )
                                if (
                                    lookup_result.found
                                    and lookup_result.symbol is not None
                                ):
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=lookup_result.symbol.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        confidence=0.85
                                        * lookup_result.confidence,
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                        evidence_type=(
                                            "ast_call_this_property"
                                        ),
                                    ))
                                    edge_added = True

                    # For non-this cases, get receiver name from identifier node
                    else:
                        receiver_name = node_text(receiver_node, source)

                        resolved_nav_sym = None

                        # Case 2: Object.method() - static/object call
                        if receiver_name in class_symbols:
                            candidate = f"{receiver_name}.{method_name}"
                            # Use import path as hint for disambiguation
                            import_hint = imports.get(receiver_name)
                            lookup_result = resolver.lookup(candidate, path_hint=import_hint)
                            if lookup_result.found and lookup_result.symbol is not None:
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    confidence=0.95 * lookup_result.confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_call_static",
                                ))
                                edge_added = True
                                resolved_nav_sym = lookup_result.symbol

                        # Case 3: instance.method() - use type inference
                        elif receiver_name in var_types:
                            type_class_name = var_types[receiver_name]
                            candidate = f"{type_class_name}.{method_name}"
                            # Use import path of the type as hint for disambiguation
                            import_hint = imports.get(type_class_name)
                            lookup_result = resolver.lookup(candidate, path_hint=import_hint)
                            if lookup_result.found and lookup_result.symbol is not None:
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    confidence=0.85 * lookup_result.confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_call_type_inferred",
                                ))
                                edge_added = True
                                resolved_nav_sym = lookup_result.symbol

                        # Return type tracking for navigation calls
                        if resolved_nav_sym and resolved_nav_sym.kind in ("function", "method"):
                            ret_name = _extract_kotlin_return_type_name(
                                resolved_nav_sym.signature
                            )
                            if ret_name and ret_name in class_symbols:
                                prop_decl = node.parent
                                if prop_decl and prop_decl.type == "property_declaration":
                                    var_decl = find_child_by_type(
                                        prop_decl, "variable_declaration"
                                    )
                                    if var_decl:
                                        var_name_node = find_child_by_type(
                                            var_decl, "identifier"
                                        )
                                        if var_name_node:
                                            var_types[
                                                node_text(var_name_node, source)
                                            ] = ret_name

                        # Case 4: Fallback - try qualified name directly
                        if not edge_added:  # pragma: no cover
                            candidate = f"{receiver_name}.{method_name}"
                            # Use import path as hint if receiver is an imported name
                            import_hint = imports.get(receiver_name)
                            lookup_result = resolver.lookup(candidate, path_hint=import_hint)
                            if lookup_result.found and lookup_result.symbol is not None:
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    confidence=0.75 * lookup_result.confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_call_direct",
                                ))
            else:
                # Simple function call: helper()
                callee_node = find_child_by_type(node, "identifier")
                if callee_node:
                    callee_name = node_text(callee_node, source)
                    resolved_simple_sym = None

                    # AMB-METHOD guard: when 3+ classes define the same
                    # method name, suppress the edge to avoid false positives.
                    if method_resolver is not None:
                        amb_check = method_resolver.lookup(callee_name)
                        if not amb_check.found and amb_check.candidates:
                            continue  # 3+ method candidates, suppress
                    # Check local symbols first
                    if callee_name in local_symbols:
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
                        resolved_simple_sym = callee
                    # Check global symbols via resolver
                    else:
                        import_hint = imports.get(callee_name)
                        lookup_result = resolver.lookup(callee_name, path_hint=import_hint)
                        if lookup_result.found and lookup_result.symbol is not None:
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=lookup_result.symbol.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="function_call",
                                confidence=0.80 * lookup_result.confidence,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))
                            resolved_simple_sym = lookup_result.symbol

                    # Return type tracking for simple function calls
                    if (
                        resolved_simple_sym
                        and resolved_simple_sym.kind in ("function", "method")
                    ):
                        ret_name = _extract_kotlin_return_type_name(
                            resolved_simple_sym.signature
                        )
                        if ret_name and ret_name in class_symbols:
                            prop_decl = node.parent
                            if (
                                prop_decl
                                and prop_decl.type == "property_declaration"
                            ):
                                var_decl = find_child_by_type(
                                    prop_decl, "variable_declaration"
                                )
                                if var_decl:
                                    var_name_node = find_child_by_type(
                                        var_decl, "identifier"
                                    )
                                    if var_name_node:
                                        var_types[
                                            node_text(var_name_node, source)
                                        ] = ret_name

    return edges


def _resolve_base_class_kotlin(
    base_name: str,
    child_sym: Symbol,
    candidates_by_name: dict[str, list[Symbol]],
    sym_file_imports: dict[str, dict[str, str]],
) -> Symbol | None:
    """Resolve a base class/interface name to a specific Symbol, disambiguating collisions.

    When multiple classes or interfaces share the same name (e.g., test stubs named
    'Model'), uses a priority cascade:

    1. Same-file match: base class defined in the same file as the child
    2. Import match: child's file has ``import com.example.models.Model`` and a
       candidate's fully qualified name or file path matches
    3. First by ID: deterministic fallback (sorted by symbol ID)

    Args:
        base_name: The base class/interface name to resolve (e.g., 'Model')
        child_sym: The child class symbol (for file context)
        candidates_by_name: Multi-value lookup: name -> list of Symbol candidates
        sym_file_imports: Maps symbol ID -> file-level imports dict
            (simple name -> fully qualified name)

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

    # 2. Import match: check if child's file imports resolve to a candidate
    imports = sym_file_imports.get(child_sym.id, {})
    if base_name in imports:
        fqn = imports[base_name]
        # Convert FQN to path segments for matching against file paths
        # e.g., "com.example.models.Model" -> try "com/example/models/Model",
        # then progressively shorter suffixes
        fqn_as_path = fqn.replace(".", "/")
        for cand in candidates:
            cand_path = cand.path or ""
            cand_no_ext = cand_path.rsplit(".kt", 1)[0]
            if cand_no_ext.endswith(fqn_as_path):
                return cand
        # Try matching just the last package segment + class name
        # e.g., "com.example.models.Model" -> "models/Model" matches ".../models/Model.kt"
        fqn_parts = fqn.split(".")
        if len(fqn_parts) >= 2:
            short_path = "/".join(fqn_parts[-2:])
            for cand in candidates:
                cand_path = cand.path or ""
                cand_no_ext = cand_path.rsplit(".kt", 1)[0]
                if cand_no_ext.endswith(short_path):
                    return cand

    # 3. Deterministic fallback: first by symbol ID (sorted for stability)
    candidates_sorted = sorted(candidates, key=lambda c: c.id)
    return candidates_sorted[0]


def _extract_inheritance_edges(
    symbols: list[Symbol],
    class_by_name: dict[str, list[Symbol]],
    interface_by_name: dict[str, list[Symbol]],
    sym_file_imports: dict[str, dict[str, str]],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract extends/implements edges from class inheritance (META-001).

    For each class with base_classes metadata, creates:
    - extends edges to base classes
    - implements edges to interfaces

    When multiple classes or interfaces share the same name (common in repos with
    test stubs), uses import-aware disambiguation via ``_resolve_base_class_kotlin()``
    to find the correct target.

    Args:
        symbols: All extracted symbols
        class_by_name: Multi-value lookup: class name -> list of Symbol candidates
        interface_by_name: Multi-value lookup: interface name -> list of Symbol candidates
        sym_file_imports: Maps symbol ID -> file-level imports dict
        run: Current analysis run for provenance

    Returns:
        List of extends/implements edges for inheritance relationships
    """
    edges: list[Edge] = []

    for sym in symbols:
        if sym.kind not in ("class", "interface"):
            continue

        base_classes = sym.meta.get("base_classes", []) if sym.meta else []
        if not base_classes:
            continue

        for base_class_name in base_classes:
            # Strip generics: List<Int> -> List
            base_name = base_class_name.split("<")[0] if "<" in base_class_name else base_class_name

            # Determine edge type based on whether target is interface or class
            # Try interface first, then class, using disambiguation
            iface_sym = _resolve_base_class_kotlin(
                base_name, sym, interface_by_name, sym_file_imports
            )
            if iface_sym is not None and iface_sym.id != sym.id:
                edge_type = "implements"
                base_sym = iface_sym
            else:
                class_sym = _resolve_base_class_kotlin(
                    base_name, sym, class_by_name, sym_file_imports
                )
                if class_sym is not None and class_sym.id != sym.id:
                    edge_type = "extends"
                    base_sym = class_sym
                else:
                    continue  # External type, no edge

            edge = Edge.create(
                src=sym.id,
                dst=base_sym.id,
                edge_type=edge_type,
                line=sym.span.start_line if sym.span else 0,
                confidence=0.95,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="ast_extends" if edge_type == "extends" else "ast_implements",
            )
            edges.append(edge)

    return edges


class KotlinAnalyzer(TreeSitterAnalyzer):
    """Kotlin analyzer using tree-sitter-kotlin grammar."""

    lang = "kotlin"
    file_patterns: ClassVar[list[str]] = ["*.kt"]
    grammar_module = "tree_sitter_kotlin"

    def analyze(self, repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        """Analyze all Kotlin files in a repository.

        Returns an AnalysisResult with symbols, edges, and provenance.
        If tree-sitter-kotlin is not available, returns a skipped result.
        """
        if not self._check_grammar_available():
            warnings.warn(
                "tree-sitter-kotlin not available. Install with: pip install hypergumbo[kotlin]",
                stacklevel=2,
            )
            return AnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-kotlin not available",
            )

        start_time = time.time()
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        # Import tree-sitter-kotlin
        try:
            parser = self._create_parser()
        except Exception as e:
            run.duration_ms = int((time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"Failed to load Kotlin parser: {e}",
            )

        # Pass 1: Extract all symbols
        file_analyses: dict[Path, FileAnalysis] = {}
        files_skipped = 0

        for kt_file in find_kotlin_files(repo_root):
            analysis = _extract_symbols_from_file(kt_file, parser, run)
            if analysis.symbols:
                file_analyses[kt_file] = analysis
            else:
                files_skipped += 1

        # Build global symbol registry
        global_symbols: dict[str, Symbol] = {}
        # AMB-METHOD: multi-value method registry for ambiguity guard
        global_methods: dict[str, list[Symbol]] = {}
        for analysis in file_analyses.values():
            for symbol in analysis.symbols:
                # Store by short name for cross-file resolution
                short_name = symbol.name.split(".")[-1] if "." in symbol.name else symbol.name
                global_symbols[short_name] = symbol
                global_symbols[symbol.name] = symbol
                if symbol.kind == "method":
                    global_methods.setdefault(short_name, []).append(symbol)

        # Pass 2: Extract edges
        resolver = NameResolver(global_symbols)
        method_resolver = ListNameResolver(global_methods, ambiguity_threshold=3)
        all_symbols: list[Symbol] = []
        all_edges: list[Edge] = []

        for kt_file, analysis in file_analyses.items():
            all_symbols.extend(analysis.symbols)

            edges = _extract_edges_from_file(
                kt_file, parser, analysis.symbol_by_name, global_symbols,
                analysis.imports, run, resolver, method_resolver=method_resolver,
            )
            all_edges.extend(edges)

        run.files_analyzed = len(file_analyses)
        run.files_skipped = files_skipped
        run.duration_ms = int((time.time() - start_time) * 1000)

        # Extract inheritance edges (META-001: base_classes metadata -> extends/implements edges)
        # Build multi-value lookups for disambiguation (INV-015)
        class_by_name: dict[str, list[Symbol]] = {}
        interface_by_name: dict[str, list[Symbol]] = {}
        for s in all_symbols:
            if s.kind == "class":
                if s.name not in class_by_name:
                    class_by_name[s.name] = []
                class_by_name[s.name].append(s)
            elif s.kind == "interface":
                if s.name not in interface_by_name:
                    interface_by_name[s.name] = []
                interface_by_name[s.name].append(s)
        # Build per-symbol imports mapping for disambiguation
        sym_file_imports: dict[str, dict[str, str]] = {}
        for _kt_file, analysis in file_analyses.items():
            for sym in analysis.symbols:
                sym_file_imports[sym.id] = analysis.imports
        inheritance_edges = _extract_inheritance_edges(
            all_symbols, class_by_name, interface_by_name, sym_file_imports, run
        )
        all_edges.extend(inheritance_edges)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            run=run,
        )


_analyzer = KotlinAnalyzer()


def is_kotlin_tree_sitter_available() -> bool:
    """Check if tree-sitter-kotlin grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("kotlin")
def analyze_kotlin(repo_root: Path) -> AnalysisResult:
    """Analyze all Kotlin files in a repository (wrapper)."""
    return _analyzer.analyze(repo_root)
