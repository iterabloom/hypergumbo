# SPDX-License-Identifier: AGPL-3.0-or-later
"""Java analysis pass using tree-sitter-java.

This analyzer uses tree-sitter-java to parse Java files and extract:
- Class declarations (symbols)
- Interface declarations (symbols)
- Enum declarations (symbols)
- Method declarations (symbols)
- Constructor declarations (symbols)
- Method call relationships (edges)
- Inheritance relationships: extends, implements (edges)
- Instantiation: new ClassName() (edges)
- Native method declarations for JNI bridge detection

Rich Metadata Extraction (ADR-0003)
-----------------------------------
Symbols include rich metadata in the `meta` field:

- **decorators**: List of annotation info dicts with:
  - name: Annotation name (e.g., "Entity", "Table", "GetMapping")
  - args: Positional arguments (e.g., ["/users"] for @GetMapping("/users"))
  - kwargs: Keyword arguments (e.g., {"name": "users"} for @Table(name = "users"))
  - Supports string, integer, float, boolean, and array values

- **base_classes**: List of extended/implemented classes/interfaces
  - Includes generic type parameters (e.g., "Repository<User, Long>")
  - Combines extends clause and implements clause

Example:
    @Entity
    @Table(name = "users")
    public class User extends BaseModel implements Serializable {}

    Results in meta:
    {
        "decorators": [
            {"name": "Entity", "args": [], "kwargs": {}},
            {"name": "Table", "args": [], "kwargs": {"name": "users"}}
        ],
        "base_classes": ["BaseModel", "Serializable"]
    }

If tree-sitter-java is not installed, the analyzer gracefully degrades
and returns an empty result.

How It Works
------------
1. Check if tree-sitter and tree-sitter-java are available
2. If not available, return empty result (not an error, just no Java analysis)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls/inheritance and resolve against global symbol registry
4. Detect method calls, inheritance, and instantiation patterns

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Java support is separate from other languages to keep modules focused
- Two-pass allows cross-file call resolution and inheritance tracking
- Same pattern as C/PHP/JS analyzers for consistency
- Uses iterative traversal to avoid RecursionError on deeply nested code
"""
from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.dataflow import annotate_dataflow as _annotate_dataflow, get_dataflow_config as _get_dataflow_config
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    emit_module_attribute_refs,
    populate_docstrings_from_tree,
    iter_tree,
    make_file_id,
    make_symbol_id as _base_make_symbol_id,
    make_typed_stable_id,
    make_unresolved_edge,
    node_text as _node_text,
    visibility_from_modifiers,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("java")

# Backwards compatibility alias
JavaAnalysisResult = AnalysisResult


def find_java_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Java files in the repository."""
    yield from find_files(repo_root, ["*.java"])


class JavaTreeSitterAnalyzer(TreeSitterAnalyzer):
    """TreeSitterAnalyzer wrapper for Java files.

    Overrides ``analyze()`` entirely because Java analysis uses a complex
    two-pass approach with annotations, JNI detection, class resolution,
    position-based symbol lookup, and per-symbol file imports disambiguation.
    The base class provides grammar availability checking via
    ``_check_grammar_available()``.
    """

    lang = "java"
    file_patterns: ClassVar[list[str]] = ["*.java"]
    grammar_module = "tree_sitter_java"

    def analyze(self, repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        """Run the Java analysis using the existing analyze_java logic."""
        return _analyze_java_impl(repo_root)


_java_analyzer = JavaTreeSitterAnalyzer()


def is_java_tree_sitter_available() -> bool:
    """Check if tree-sitter and Java grammar are available."""
    return _java_analyzer._check_grammar_available()


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return _base_make_symbol_id("java", path, start_line, end_line, name, kind)


def _find_identifier_in_children(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Find identifier name in node's children."""
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
    return None


def _get_class_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract class/interface/enum name from declaration."""
    return _find_identifier_in_children(node, source)


def _get_method_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract method name from method_declaration or constructor_declaration."""
    return _find_identifier_in_children(node, source)


def _extract_type_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract type text from a type node, handling generics and arrays."""
    return _node_text(node, source)


def _extract_java_signature(
    node: "tree_sitter.Node", source: bytes, is_constructor: bool = False
) -> Optional[str]:
    """Extract function signature from a Java method or constructor declaration.

    Returns signature like:
    - "(int a, int b) int" for regular methods
    - "(String message)" for void methods (no return type shown)
    - "(String name, int age)" for constructors (no return type)

    Args:
        node: The method_declaration or constructor_declaration node.
        source: The source code bytes.
        is_constructor: True if this is a constructor (no return type).

    Returns:
        The signature string, or None if extraction fails.
    """
    params_node = None
    return_type = None

    # Find formal_parameters and return type
    for child in node.children:
        if child.type == "formal_parameters":
            params_node = child
        # Return type appears before the identifier for methods
        # Types we care about: void_type, type_identifier, generic_type, array_type, and primitives
        elif child.type in ("void_type", "type_identifier", "generic_type", "array_type",
                            "integral_type", "floating_point_type", "boolean_type"):
            # Only capture if we haven't found params yet (return type comes before name)
            if params_node is None:
                return_type = _extract_type_text(child, source)

    if params_node is None:
        return None  # pragma: no cover

    # Extract parameters
    params: list[str] = []
    for child in params_node.children:
        if child.type == "formal_parameter":
            param_type = None
            param_name = None
            for subchild in child.children:
                if subchild.type in ("type_identifier", "generic_type", "array_type",
                                      "integral_type", "floating_point_type", "boolean_type"):
                    param_type = _extract_type_text(subchild, source)
                elif subchild.type == "identifier":
                    param_name = _node_text(subchild, source)
                elif subchild.type == "dimensions":
                    # Array notation after variable name: String[] args
                    if param_type:
                        param_type += _node_text(subchild, source)
            if param_type and param_name:
                params.append(f"{param_type} {param_name}")
        elif child.type == "spread_parameter":
            # Varargs: String... args
            param_type = None
            param_name = None
            for subchild in child.children:
                if subchild.type in ("type_identifier", "generic_type", "array_type"):
                    param_type = _extract_type_text(subchild, source)
                elif subchild.type == "variable_declarator":
                    for vchild in subchild.children:
                        if vchild.type == "identifier":
                            param_name = _node_text(vchild, source)
                elif subchild.type == "identifier":  # pragma: no cover
                    param_name = _node_text(subchild, source)  # pragma: no cover
            if param_type and param_name:
                params.append(f"{param_type}... {param_name}")

    params_str = ", ".join(params)
    signature = f"({params_str})"

    # Add return type for methods (not constructors), but omit void
    if not is_constructor and return_type and return_type != "void":
        signature += f" {return_type}"

    return signature


def normalize_java_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Java signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_types_first
    return normalize_signature_types_first(signature, type_params)


def _extract_java_return_type_name(signature: str | None) -> str | None:
    """Extract simple return type name from a Java method signature.

    Parses signatures like "(int a, int b) Client" and returns "Client".
    For generic types like "(int a) Response<User>", extracts the outer type
    name "Response".  Returns None for void methods (no return type in
    signature), primitive types, and array types.

    Args:
        signature: Method signature string from Symbol.signature.

    Returns:
        The simple class name if found, None otherwise.
    """
    if not signature:
        return None
    # Java signatures: "(params) ReturnType" or "(params)" for void
    paren_idx = signature.rfind(")")
    if paren_idx < 0 or paren_idx == len(signature) - 1:
        return None
    ret_part = signature[paren_idx + 1:].strip()
    # Handle simple class names (identifiers starting with uppercase)
    if ret_part and ret_part.isidentifier() and ret_part[0].isupper():
        return ret_part
    # Handle generic types: extract outer type name before '<'
    # e.g., Response<User> -> Response, CompletionStage<Response> -> CompletionStage
    if "<" in ret_part:
        outer = ret_part[:ret_part.index("<")]
        if outer and outer.isidentifier() and outer[0].isupper():
            return outer
    return None


def _infer_return_type_from_body(
    method_node: "tree_sitter.Node", source: bytes
) -> str | None:
    """Infer concrete return type from 'return new X(...)' in a method body.

    When a Java method declares return type Object but always returns a specific
    type via 'return new TokenEndpoint(...)', infer 'TokenEndpoint' as the
    concrete type.  This enables JAX-RS subresource locator path chaining for
    methods like keycloak's OIDCLoginProtocolService.token() which returns
    Object but constructs a TokenEndpoint.

    Only infers when ALL return statements in the method body return the same
    concrete type via 'new X(...)'.  Returns None if there are multiple
    different types or non-new returns.
    """
    body = method_node.child_by_field_name("body")
    if body is None:
        return None

    inferred_type: str | None = None
    for child in _walk_tree(body):
        if child.type != "return_statement":
            continue
        # return_statement children: "return" keyword, then the expression
        expr = None
        for rc in child.children:
            if rc.type not in ("return", ";"):
                expr = rc
                break
        if expr is None:
            # Bare "return;" — can't infer
            return None
        if expr.type == "object_creation_expression":
            # "new X(...)" — extract X
            type_node = expr.child_by_field_name("type")
            if type_node is None:  # pragma: no cover
                return None
            type_name = _node_text(type_node, source)
            if inferred_type is None:
                inferred_type = type_name
            elif inferred_type != type_name:
                # Multiple different types — can't infer a single one
                return None
        else:
            # Non-new return (e.g., return someVariable;) — can't infer
            return None

    return inferred_type


def _walk_tree(node: "tree_sitter.Node"):
    """Yield all descendant nodes (depth-first) without recursion.

    Used by _infer_return_type_from_body to find return statements in a
    method body without deep recursion on large ASTs.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        # Reverse children so left-to-right order is preserved
        stack.extend(reversed(current.children))


def _extract_param_types(
    node: "tree_sitter.Node", source: bytes
) -> dict[str, str]:
    """Extract parameter name -> type mapping from a method/constructor declaration.

    This enables type inference for method calls on parameters, e.g.:
        void process(Client client) {
            client.send();  // resolves to Client.send
        }

    Returns:
        Dict mapping parameter names to their type names (simple name only, not qualified).
    """
    param_types: dict[str, str] = {}

    # Find formal_parameters node
    params_node = None
    for child in node.children:
        if child.type == "formal_parameters":
            params_node = child
            break

    if params_node is None:  # pragma: no cover
        return param_types

    # Extract parameter types
    for child in params_node.children:
        if child.type == "formal_parameter":
            param_type = None
            param_name = None
            for subchild in child.children:
                if subchild.type in ("type_identifier", "generic_type", "array_type"):
                    # Extract just the base type name (e.g., "Client" from "Client<T>")
                    param_type = _extract_type_text(subchild, source)
                    # Strip generic parameters for lookup
                    if "<" in param_type:
                        param_type = param_type.split("<")[0]
                    # Strip array brackets
                    if "[" in param_type:
                        param_type = param_type.split("[")[0]
                elif subchild.type == "identifier":
                    param_name = _node_text(subchild, source)
            if param_type and param_name:
                param_types[param_name] = param_type
        elif child.type == "spread_parameter":  # pragma: no cover
            # Varargs: String... args - rarely used
            param_type = None
            param_name = None
            for subchild in child.children:
                if subchild.type in ("type_identifier", "generic_type", "array_type"):
                    param_type = _extract_type_text(subchild, source)
                    if "<" in param_type:
                        param_type = param_type.split("<")[0]
                elif subchild.type == "variable_declarator":
                    for vchild in subchild.children:
                        if vchild.type == "identifier":
                            param_name = _node_text(vchild, source)
                elif subchild.type == "identifier":
                    param_name = _node_text(subchild, source)
            if param_type and param_name:
                param_types[param_name] = param_type

    return param_types


def _has_native_modifier(node: "tree_sitter.Node", source: bytes) -> bool:
    """Check if a method declaration has the 'native' modifier."""
    for child in node.children:
        if child.type == "modifiers":
            modifiers_text = _node_text(child, source)
            if "native" in modifiers_text:
                return True
    return False


# Java modifiers that can appear on methods
JAVA_METHOD_MODIFIERS = {
    "public", "private", "protected",
    "static", "final", "abstract",
    "native", "synchronized", "strictfp",
}


def _extract_modifiers(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract all modifiers from a declaration (class, interface, enum, method).

    Returns a list of modifier strings like ["public", "static", "abstract"].

    Tree-sitter-java uses modifier keywords as node types directly (e.g., "public",
    "static", "native"), so we can match against the node type.  Works for class,
    interface, enum, method, and constructor declarations.
    """
    del source  # unused - modifiers are captured via node types
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "modifiers":
            # The modifiers node contains individual modifier nodes
            for mod_child in child.children:
                # tree-sitter-java uses modifier keywords as node types
                if mod_child.type in JAVA_METHOD_MODIFIERS:
                    modifiers.append(mod_child.type)
    return modifiers


def _get_java_parser() -> Optional["tree_sitter.Parser"]:
    """Get tree-sitter parser for Java."""
    try:
        import tree_sitter
        import tree_sitter_java
    except ImportError:
        return None

    parser = tree_sitter.Parser()
    lang_ptr = tree_sitter_java.language()
    parser.language = tree_sitter.Language(lang_ptr)
    return parser


@dataclass
class _ParsedFile:
    """Holds parsed file data for two-pass analysis.

    Type inference sources for variable method call resolution (e.g., stub.method()):
    1. Direct constructor calls: stub = new Client() → var_types['stub'] = 'Client'
    2. Return type annotations: stub = factory.createClient() where createClient returns
       Client → var_types['stub'] = 'Client'
    3. Parameter type annotations: void process(Client client) → var_types['client'] = 'Client'
    4. Field declarations: private Repository repo → var_types['repo'] = 'Repository'
    """

    path: Path
    tree: "tree_sitter.Tree"
    source: bytes
    # Maps simple class name -> fully qualified name (from imports)
    imports: dict[str, str] | None = None


def _extract_imports(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import mappings from a parsed Java tree.

    Tracks:
    - import com.example.ClassName; -> ClassName: com.example.ClassName
    - import static com.example.ClassName.method; -> (not tracked, static methods)

    Returns dict mapping simple class name -> fully qualified name.
    """
    imports: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import_declaration":
            continue

        # Skip static imports for now (they import methods, not classes)
        is_static = any(c.type == "static" for c in node.children)
        if is_static:
            continue

        # Find the scoped_identifier (the fully qualified name)
        for child in node.children:
            if child.type == "scoped_identifier":
                full_name = _node_text(child, source)
                # Extract simple name (last part of qualified name)
                simple_name = full_name.split(".")[-1]
                imports[simple_name] = full_name
                break

    return imports


def _get_class_ancestors(
    node: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Walk up the tree to find enclosing class/interface/enum names.

    Returns a list of class names from outermost to innermost (excluding current node).
    Used to build qualified names for nested types without recursion.
    """
    ancestors: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "interface_declaration", "enum_declaration"):
            name = _get_class_name(current, source)
            if name:
                ancestors.append(name)
        current = current.parent
    # Reverse because we walked from inner to outer
    return list(reversed(ancestors))


def _get_parent_class_base_classes(
    node: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Get base classes of the immediate parent class/interface containing this node.

    Walks up the tree to find the first enclosing class/interface declaration,
    then extracts its base classes (extends/implements).

    This is used to enrich method metadata with parent class inheritance info,
    enabling framework patterns to match methods by their parent class's base classes
    (e.g., matching onCreate() in classes that extend Activity).

    Args:
        node: The node to start from (typically a method_declaration).
        source: The source code bytes.

    Returns:
        List of base class names, or empty list if no parent class or no base classes.
    """
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "interface_declaration"):
            return _extract_base_classes(current, source)
        current = current.parent
    return []  # pragma: no cover - defensive: methods always inside a class in valid Java


def _java_value_to_python(
    node: "tree_sitter.Node", source: bytes
) -> str | int | float | bool | list | None:
    """Convert a Java tree-sitter AST node to a Python value representation.

    Handles strings, numbers, booleans, and identifiers.
    Returns the value or a string representation for identifiers.
    """
    if node.type == "string_literal":
        # Strip quotes from string literals
        text = _node_text(node, source)
        return text.strip('"')
    elif node.type in ("decimal_integer_literal", "hex_integer_literal"):
        text = _node_text(node, source)
        try:
            if text.startswith("0x") or text.startswith("0X"):
                return int(text, 16)
            return int(text)
        except ValueError:  # pragma: no cover
            return text
    elif node.type in ("decimal_floating_point_literal",):
        text = _node_text(node, source)
        try:
            return float(text.rstrip("fFdD"))
        except ValueError:  # pragma: no cover
            return text
    elif node.type in ("true", "false"):
        return node.type == "true"
    elif node.type == "identifier":
        return _node_text(node, source)
    elif node.type == "field_access":
        # Handle Enum.VALUE or Class.constant
        return _node_text(node, source)
    elif node.type in ("array_initializer", "element_value_array_initializer"):
        # Handle array values: {value1, value2}
        result = []
        for child in node.children:
            if child.type not in ("{", "}", ","):
                result.append(_java_value_to_python(child, source))
        return result
    # For other types, return the text representation
    return _node_text(node, source)  # pragma: no cover


def _extract_annotation_info(
    annotation_node: "tree_sitter.Node", source: bytes
) -> dict[str, object]:
    """Extract full annotation information including arguments.

    Returns a dict with:
    - name: annotation name (e.g., "Entity", "Table")
    - args: list of positional arguments (string values without names)
    - kwargs: dict of keyword arguments (name=value pairs)
    """
    name = ""
    args: list[object] = []
    kwargs: dict[str, object] = {}

    for child in annotation_node.children:
        if child.type == "identifier":
            name = _node_text(child, source)
        elif child.type == "annotation_argument_list":
            for arg_child in child.children:
                if arg_child.type == "element_value_pair":
                    # Named argument: @Annotation(key = value)
                    key = None
                    value = None
                    found_key = False
                    for pair_child in arg_child.children:
                        if pair_child.type == "identifier" and not found_key:
                            key = _node_text(pair_child, source)
                            found_key = True
                        elif pair_child.type not in ("=", "identifier") or found_key:
                            if pair_child.type != "=":
                                value = _java_value_to_python(pair_child, source)
                    if key and value is not None:
                        kwargs[key] = value
                elif arg_child.type not in ("(", ")", ","):
                    # Positional argument: @Annotation("value"),
                    # @Annotation(CONSTANT), @Annotation("a" + "b")
                    args.append(_java_value_to_python(arg_child, source))

    return {"name": name, "args": args, "kwargs": kwargs}


def _extract_annotations(
    node: "tree_sitter.Node", source: bytes
) -> list[dict[str, object]]:
    """Extract all annotations from a Java node (class, interface, method, etc).

    Annotations appear in a 'modifiers' child node.

    Returns list of annotation info dicts: [{"name": str, "args": list, "kwargs": dict}]
    """
    decorators: list[dict[str, object]] = []

    for child in node.children:
        if child.type == "modifiers":
            for mod_child in child.children:
                if mod_child.type in ("annotation", "marker_annotation"):
                    dec_info = _extract_annotation_info(mod_child, source)
                    if dec_info["name"]:
                        decorators.append(dec_info)

    return decorators


def _extract_base_classes(
    node: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Extract base classes/interfaces from a Java class or interface declaration.

    Handles:
    - extends clause: class Foo extends Bar
    - implements clause: class Foo implements IBar, IBaz
    - interface extends: interface Foo extends IBar, IBaz
    - generic types: class Foo extends Bar<T>

    Returns list of base class/interface names.
    """
    base_classes: list[str] = []

    for child in node.children:
        if child.type == "superclass":
            # extends clause for classes
            for super_child in child.children:
                if super_child.type == "type_identifier":
                    base_classes.append(_node_text(super_child, source))
                elif super_child.type == "generic_type":
                    base_classes.append(_node_text(super_child, source))
        elif child.type == "super_interfaces":
            # implements clause for classes
            for iface_child in child.children:
                if iface_child.type == "type_list":
                    for type_child in iface_child.children:
                        if type_child.type == "type_identifier":
                            base_classes.append(_node_text(type_child, source))
                        elif type_child.type == "generic_type":
                            base_classes.append(_node_text(type_child, source))
        elif child.type == "extends_interfaces":
            # extends clause for interfaces
            for ext_child in child.children:
                if ext_child.type == "type_list":
                    for type_child in ext_child.children:
                        if type_child.type == "type_identifier":
                            base_classes.append(_node_text(type_child, source))
                        elif type_child.type == "generic_type":
                            base_classes.append(_node_text(type_child, source))

    return base_classes


def _extract_symbols(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
) -> list[Symbol]:
    """Extract symbols from a parsed Java tree (pass 1).

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    symbols: list[Symbol] = []

    for node in iter_tree(tree.root_node):
        # Class declarations
        if node.type == "class_declaration":
            name = _get_class_name(node, source)
            if name:
                ancestors = _get_class_ancestors(node, source)
                full_name = ".".join(ancestors + [name]) if ancestors else name
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )

                # Extract annotations and base class metadata
                meta: dict[str, object] | None = None
                decorators = _extract_annotations(node, source)
                base_classes = _extract_base_classes(node, source)
                if decorators or base_classes:
                    meta = {}
                    if decorators:
                        meta["decorators"] = decorators
                    if base_classes:
                        meta["base_classes"] = base_classes

                modifiers = _extract_modifiers(node, source)
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "class"),
                    name=full_name,
                    kind="class",
                    language="java",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta=meta,
                    modifiers=modifiers,
                    shape_id=_java_analyzer.compute_shape_id(node),
                )
                symbols.append(symbol)

        # Interface declarations
        elif node.type == "interface_declaration":
            name = _get_class_name(node, source)
            if name:
                ancestors = _get_class_ancestors(node, source)
                full_name = ".".join(ancestors + [name]) if ancestors else name
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )

                # Extract annotations and base class metadata
                meta: dict[str, object] | None = None
                decorators = _extract_annotations(node, source)
                base_classes = _extract_base_classes(node, source)
                if decorators or base_classes:
                    meta = {}
                    if decorators:
                        meta["decorators"] = decorators
                    if base_classes:
                        meta["base_classes"] = base_classes

                modifiers = _extract_modifiers(node, source)
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "interface"),
                    name=full_name,
                    kind="interface",
                    language="java",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta=meta,
                    modifiers=modifiers,
                    shape_id=_java_analyzer.compute_shape_id(node),
                )
                symbols.append(symbol)

        # Enum declarations
        elif node.type == "enum_declaration":
            name = _get_class_name(node, source)
            if name:
                ancestors = _get_class_ancestors(node, source)
                full_name = ".".join(ancestors + [name]) if ancestors else name
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                modifiers = _extract_modifiers(node, source)
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "enum"),
                    name=full_name,
                    kind="enum",
                    language="java",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    modifiers=modifiers,
                    shape_id=_java_analyzer.compute_shape_id(node),
                )
                symbols.append(symbol)

        # Method declarations
        elif node.type == "method_declaration":
            name = _get_method_name(node, source)
            ancestors = _get_class_ancestors(node, source)
            if name and ancestors:
                # Name methods with class prefix
                full_name = f"{'.'.join(ancestors)}.{name}"
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                # Check for native modifier
                is_native = _has_native_modifier(node, source)

                # Extract all modifiers for the modifiers field
                modifiers = _extract_modifiers(node, source)

                # Build meta dict
                meta: dict[str, object] | None = None

                # Extract all annotations for rich metadata
                # Route detection is now handled by YAML patterns (ADR-0003 v1.0.x)
                decorators = _extract_annotations(node, source)
                if decorators:
                    meta = {"decorators": decorators}

                if is_native:
                    if meta is None:
                        meta = {}
                    meta["is_native"] = True

                # Extract parent class base_classes for lifecycle hook detection (ADR-0003 v1.1.x)
                # This enables YAML patterns to match methods like onCreate() in Activity subclasses
                parent_base_classes = _get_parent_class_base_classes(node, source)
                if parent_base_classes:
                    if meta is None:
                        meta = {}
                    meta["parent_base_classes"] = parent_base_classes

                # Extract signature
                signature = _extract_java_signature(node, source, is_constructor=False)

                # Add return type for subresource locator detection (JAX-RS)
                ret_type_name = _extract_java_return_type_name(signature)
                if ret_type_name:
                    if meta is None:
                        meta = {}
                    meta["return_type"] = ret_type_name

                    # When return type is Object, infer concrete type from
                    # "return new X(...)" in the method body.  Common in JAX-RS
                    # subresource locators (keycloak: token() returns Object but
                    # body is "return new TokenEndpoint(...)").
                    if ret_type_name == "Object":
                        inferred = _infer_return_type_from_body(node, source)
                        if inferred:
                            meta["inferred_return_type"] = inferred

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_java_signature(signature)
                stable_id = make_typed_stable_id(
                    "method", norm_sig, visibility_from_modifiers(modifiers),
                ) if norm_sig else None

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="java",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta=meta,
                    stable_id=stable_id,
                    signature=signature,
                    modifiers=modifiers,
                    shape_id=_java_analyzer.compute_shape_id(node),
                )
                symbols.append(symbol)

        # Constructor declarations
        elif node.type == "constructor_declaration":
            name = _get_method_name(node, source)
            ancestors = _get_class_ancestors(node, source)
            if name and ancestors:
                full_name = f"{'.'.join(ancestors)}.{name}"
                span = Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                )
                # Extract signature (constructors have no return type)
                signature = _extract_java_signature(node, source, is_constructor=True)

                # Extract modifiers for constructors too
                modifiers = _extract_modifiers(node, source)

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_java_signature(signature)
                stable_id = make_typed_stable_id(
                    "constructor", norm_sig, visibility_from_modifiers(modifiers),
                ) if norm_sig else None

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "constructor"),
                    name=full_name,
                    kind="constructor",
                    language="java",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    stable_id=stable_id,
                    signature=signature,
                    modifiers=modifiers,
                    shape_id=_java_analyzer.compute_shape_id(node),
                )
                symbols.append(symbol)

    return symbols


def _get_enclosing_method(
    node: "tree_sitter.Node",
    source: bytes,
    global_symbols: dict[str, Symbol],
    file_path: Path | None = None,
    symbol_by_position: dict[tuple[str, int, int], Symbol] | None = None,
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing method/constructor.

    Returns the Symbol for the enclosing method, or None if not inside a method.

    Uses symbol_by_position (keyed by file path + start position) when available,
    which correctly handles monorepos where multiple files define methods with
    the same name. Falls back to global_symbols when symbol_by_position is unavailable.
    """
    file_path_str = str(file_path) if file_path else None
    current = node.parent
    while current is not None:
        if current.type in ("method_declaration", "constructor_declaration"):
            # Position-based lookup handles duplicate names across files
            if symbol_by_position and file_path_str:
                pos_key = (file_path_str, current.start_point[0] + 1, current.start_point[1])
                sym = symbol_by_position.get(pos_key)
                if sym:
                    return sym
            # Fallback to name-based lookup
            name = _get_method_name(current, source)
            if name:
                # Get class context
                ancestors = _get_class_ancestors(current, source)
                if ancestors:
                    full_name = f"{'.'.join(ancestors)}.{name}"
                    if full_name in global_symbols:
                        return global_symbols[full_name]
            return None  # pragma: no cover  # Found method but couldn't resolve it
        current = current.parent
    return None


def _is_import_class_mismatch(
    type_name: str,
    resolved_sym: "Symbol",
    imports: dict[str, str],
    caller_file: str = "",
) -> bool:
    """Check if a resolved class symbol conflicts with the file's imports.

    When NameResolver suffix-matches ``Logger`` to ``Config.Logger`` but the
    file actually imports ``org.slf4j.Logger``, the resolved symbol is wrong —
    the developer intended the external library class. This prevents false
    edges that inflate centrality of inner/nested classes (INV-finak).

    Also rejects nested class resolution from bare names when the caller is
    outside the outer class's file. In Java, ``new Properties()`` cannot refer
    to ``Log4jConfiguration.Properties`` from outside Log4jConfiguration —
    you'd need the qualified name ``new Log4jConfiguration.Properties()``.
    This prevents false edges from JDK class name collisions (WI-bunul).

    Returns True when the edge should be **skipped** (import points elsewhere).
    """
    # Check 1: Nested class guard — if the resolved symbol is a nested class
    # (name contains "." AND kind is class/interface/enum) but the lookup used
    # a bare name, and the caller is not in the same file as the nested class,
    # it's almost certainly wrong. In Java, bare inner class names are only
    # valid inside the outer class.
    resolved_name = resolved_sym.name
    if (
        "." in resolved_name
        and "." not in type_name
        and resolved_sym.kind in ("class", "interface", "enum")
    ):
        resolved_path = resolved_sym.path or ""
        if caller_file != resolved_path:
            return True  # Bare name can't refer to nested class from outside

    # Check 2: Explicit import mismatch (original INV-finak check)
    if type_name not in imports:
        return False  # No import → can't disprove; allow the edge

    import_fqn = imports[type_name]
    # Convert FQN to path segments: "org.slf4j.Logger" → "org/slf4j/Logger"
    fqn_as_path = import_fqn.replace(".", "/")
    resolved_path = resolved_sym.path or ""
    resolved_no_ext = resolved_path.rsplit(".java", 1)[0]

    # If resolved class path ends with the import FQN path, they match
    if resolved_no_ext.endswith(fqn_as_path):
        return False  # Import matches resolved symbol → allow the edge

    # Try shorter match: last 2 segments (package + class)
    fqn_parts = import_fqn.split(".")
    if len(fqn_parts) >= 2:
        short_path = "/".join(fqn_parts[-2:])
        if resolved_no_ext.endswith(short_path):
            return False  # Short match → allow the edge

    # Import FQN doesn't match resolved symbol → skip the edge
    return True


def _resolve_base_class_java(
    base_name: str,
    child_sym: Symbol,
    class_by_name: dict[str, list[Symbol]],
    sym_file_imports: dict[str, dict[str, str]],
) -> Symbol | None:
    """Resolve a base class/interface name to a specific Symbol, disambiguating collisions.

    When multiple classes share the same name (e.g., test stubs named 'Model'),
    uses a priority cascade:

    1. Same-file match: base class defined in the same file as the child
    2. Import match: child's file has ``import com.example.models.Model`` and a
       candidate's fully qualified name or file path matches
    3. First by ID: deterministic fallback (sorted by symbol ID)

    Args:
        base_name: The base class/interface name to resolve (e.g., 'Model')
        child_sym: The child class symbol (for file context)
        class_by_name: Multi-value lookup: name -> list of Symbol candidates
        sym_file_imports: Maps symbol ID -> file-level imports dict
            (simple name -> fully qualified name)

    Returns:
        The resolved base class/interface Symbol, or None if no match found.
    """
    candidates = class_by_name.get(base_name)
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
        # e.g., "com.example.models.Model" -> "com/example/models/Model"
        fqn_as_path = fqn.replace(".", "/")
        for cand in candidates:
            cand_path = cand.path or ""
            cand_no_ext = cand_path.rsplit(".java", 1)[0]
            if cand_no_ext.endswith(fqn_as_path):
                return cand
        # Try matching just the last package segment + class name
        # e.g., "com.example.models.Model" -> "models/Model"
        fqn_parts = fqn.split(".")
        if len(fqn_parts) >= 2:
            short_path = "/".join(fqn_parts[-2:])
            for cand in candidates:
                cand_path = cand.path or ""
                cand_no_ext = cand_path.rsplit(".java", 1)[0]
                if cand_no_ext.endswith(short_path):
                    return cand

    # 3. Deterministic fallback: first by symbol ID (sorted for stability)
    candidates_sorted = sorted(candidates, key=lambda c: c.id)
    return candidates_sorted[0]


def _extract_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    run: AnalysisRun,
    global_symbols: dict[str, Symbol],
    class_symbols: dict[str, Symbol],
    imports: dict[str, str] | None = None,
    resolver: NameResolver | None = None,
    class_resolver: NameResolver | None = None,
    symbol_by_position: dict[tuple[str, int, int], Symbol] | None = None,
    class_by_name: dict[str, list[Symbol]] | None = None,
    sym_file_imports: dict[str, dict[str, str]] | None = None,
    method_resolver: ListNameResolver | None = None,
    class_parents: dict[str, str] | None = None,
    class_fields: dict[str, dict[str, str]] | None = None,
) -> list[Edge]:
    """Extract edges from a parsed Java tree (pass 2).

    Uses global symbol registry to resolve cross-file references.
    Uses iterative traversal to avoid RecursionError on deeply nested code.
    Optionally uses NameResolver for suffix-based matching and confidence tracking.

    Handles:
    - Direct method calls: method(), this.method()
    - Qualified method calls: ClassName.method()
    - Variable method calls: variable.method() (with type inference)
    - Object instantiation: new ClassName()
    - Inherited method calls: walks extends chain when direct lookup fails

    Type inference tracks types from:
    - Constructor calls: stub = new Client() -> stub has type Client
    - Method/constructor parameters: void process(Client client) -> client has type Client
    """
    if imports is None:
        imports = {}
    if resolver is None:
        resolver = NameResolver(global_symbols)
    if class_resolver is None:
        class_resolver = NameResolver(class_symbols)
    edges: list[Edge] = []
    _caller_path = str(file_path)
    # Track variable types for type inference: var_name -> class_name
    var_types: dict[str, str] = {}
    # Track class inheritance: child_class_name -> parent_class_name
    # Uses global map if provided (for cross-file inheritance), plus
    # augmented with per-file extends relationships discovered during traversal.
    if class_parents is None:
        class_parents = {}
    else:
        # Copy to avoid mutating the shared dict
        class_parents = dict(class_parents)

    for node in iter_tree(tree.root_node):
        # Check for extends (superclass) in class declarations
        if node.type == "class_declaration":
            name = _get_class_name(node, source)
            if name:
                ancestors = _get_class_ancestors(node, source)
                current_class = ".".join(ancestors + [name]) if ancestors else name

                # Check for extends (superclass)
                for child in node.children:
                    if child.type == "superclass":
                        # superclass contains "extends" keyword and type_identifier
                        for subchild in child.children:
                            if subchild.type == "type_identifier":
                                parent_name = _node_text(subchild, source)
                                if current_class in class_symbols:
                                    src_sym = class_symbols[current_class]
                                    # Use multi-value resolver when available
                                    dst_sym = None
                                    if class_by_name is not None and sym_file_imports is not None:
                                        dst_sym = _resolve_base_class_java(
                                            parent_name, src_sym,
                                            class_by_name, sym_file_imports,
                                        )
                                    elif parent_name in class_symbols:
                                        dst_sym = class_symbols[parent_name]
                                    if dst_sym is not None and dst_sym.id != src_sym.id:
                                        edge = Edge.create(
                                            src=src_sym.id,
                                            dst=dst_sym.id,
                                            edge_type="extends",
                                            line=child.start_point[0] + 1,
                                            confidence=0.95,
                                            origin=PASS_ID,
                                            origin_run_id=run.execution_id,
                                            evidence_type="ast_extends",
                                        )
                                        edges.append(edge)
                                        # Record parent for inherited method resolution
                                        class_parents[current_class] = dst_sym.name

                    # Check for implements (interfaces)
                    if child.type == "super_interfaces":
                        # super_interfaces contains "implements" and type_list
                        for subchild in child.children:
                            if subchild.type == "type_list":
                                for type_node in subchild.children:
                                    if type_node.type == "type_identifier":
                                        iface_name = _node_text(type_node, source)
                                        if current_class in class_symbols:
                                            src_sym = class_symbols[current_class]
                                            # Use multi-value resolver when available
                                            dst_sym = None
                                            if class_by_name is not None and sym_file_imports is not None:
                                                dst_sym = _resolve_base_class_java(
                                                    iface_name, src_sym,
                                                    class_by_name, sym_file_imports,
                                                )
                                            elif iface_name in class_symbols:
                                                dst_sym = class_symbols[iface_name]
                                            if dst_sym is not None and dst_sym.id != src_sym.id:
                                                edge = Edge.create(
                                                    src=src_sym.id,
                                                    dst=dst_sym.id,
                                                    edge_type="implements",
                                                    line=type_node.start_point[0] + 1,
                                                    confidence=0.95,
                                                    origin=PASS_ID,
                                                    origin_run_id=run.execution_id,
                                                    evidence_type="ast_implements",
                                                )
                                                edges.append(edge)

        # Method/constructor declarations - extract parameter types for type inference
        elif node.type in ("method_declaration", "constructor_declaration"):
            param_types = _extract_param_types(node, source)
            # Add parameter types to var_types for method call resolution
            # Note: This is file-scoped, not method-scoped, but variable name collisions
            # across methods are rare in practice
            for param_name, param_type in param_types.items():
                var_types[param_name] = param_type

        # Class field declarations — track field types for method call resolution
        # Java: private Repository repo; → var_types["repo"] = "Repository"
        elif node.type == "field_declaration":
            type_node = None
            for child in node.children:
                if child.type == "type_identifier":
                    type_node = child
                elif child.type == "variable_declarator" and type_node is not None:
                    for vc in child.children:
                        if vc.type == "identifier":
                            var_types[_node_text(vc, source)] = _node_text(
                                type_node, source
                            )
                            break

        # Method invocations — use tree-sitter field names for reliable extraction
        elif node.type == "method_invocation":
            current_method = _get_enclosing_method(node, source, global_symbols, file_path, symbol_by_position)
            if current_method:
                # Use tree-sitter named fields: "name" is the method, "object"
                # is the receiver.  This correctly handles field_access receivers
                # like `this.svc.process()` where the object child is a
                # field_access node rather than a simple identifier.
                name_node = node.child_by_field_name("name")
                object_node = node.child_by_field_name("object")

                method_name = _node_text(name_node, source) if name_node else None
                receiver_name = None

                if object_node is not None:
                    if object_node.type == "identifier":
                        receiver_name = _node_text(object_node, source)
                    elif object_node.type == "field_access":
                        # e.g. this.svc → extract "svc" (rightmost identifier)
                        # and use it for type-inference lookup
                        fa_field = object_node.child_by_field_name("field")
                        fa_obj = object_node.child_by_field_name("object")
                        if fa_field:
                            receiver_name = _node_text(fa_field, source)
                        # If the object is "this", treat the field as the
                        # receiver for type inference (this.repo → "repo")
                        if (
                            fa_obj
                            and fa_obj.type == "this"
                            and fa_field
                        ):
                            receiver_name = _node_text(fa_field, source)

                if method_name:
                    # Get class context
                    ancestors = _get_class_ancestors(node, source)
                    current_class = ".".join(ancestors) if ancestors else None
                    edge_added = False
                    resolved_sym: Symbol | None = None

                    # Case 1: this.method() or method() - resolve in current class
                    if receiver_name is None or receiver_name == "this":
                        if current_class:
                            candidate = f"{current_class}.{method_name}"
                            lookup_result = resolver.lookup(candidate, caller_path=_caller_path)
                            if lookup_result.found:
                                # Scale confidence by resolver's confidence multiplier
                                edge_confidence = 0.95 * lookup_result.confidence
                                edge = Edge.create(
                                    src=current_method.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    confidence=edge_confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_call_direct",
                                )
                                edges.append(edge)
                                edge_added = True
                                resolved_sym = lookup_result.symbol
                            else:
                                # Walk extends chain to find inherited method
                                parent = class_parents.get(current_class)
                                depth = 0
                                while parent and depth < 10:
                                    candidate = f"{parent}.{method_name}"
                                    lookup_result = resolver.lookup(candidate, caller_path=_caller_path)
                                    if lookup_result.found:
                                        edge_confidence = (
                                            0.90 * lookup_result.confidence
                                        )
                                        edge = Edge.create(
                                            src=current_method.id,
                                            dst=lookup_result.symbol.id,
                                            edge_type="calls",
                                            line=node.start_point[0] + 1,
                                            confidence=edge_confidence,
                                            origin=PASS_ID,
                                            origin_run_id=run.execution_id,
                                            evidence_type="ast_call_inherited",
                                        )
                                        edges.append(edge)
                                        edge_added = True
                                        resolved_sym = lookup_result.symbol
                                        break
                                    parent = class_parents.get(parent)
                                    depth += 1

                    # Case 2: ClassName.method() - static call
                    elif receiver_name and receiver_name in class_symbols:
                        candidate = f"{receiver_name}.{method_name}"
                        lookup_result = resolver.lookup(candidate, caller_path=_caller_path)
                        # WI-fuhaj: consult file imports. Without this guard,
                        # a static call like ``Logger.info(...)`` would resolve
                        # to a local class named ``Logger`` even when the file
                        # actually imports ``org.slf4j.Logger`` — the same
                        # short-name collision class that INV-finak fixed for
                        # instantiations and Case 3 type-inferred calls.
                        if lookup_result.found and not _is_import_class_mismatch(
                            receiver_name, lookup_result.symbol, imports,
                            caller_file=str(file_path),
                        ):
                            edge_confidence = 0.95 * lookup_result.confidence
                            edge = Edge.create(
                                src=current_method.id,
                                dst=lookup_result.symbol.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                confidence=edge_confidence,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_call_static",
                            )
                            edges.append(edge)
                            edge_added = True
                            resolved_sym = lookup_result.symbol

                    # Case 3: variable.method() - use type inference
                    elif receiver_name and receiver_name in var_types:
                        type_class_name = var_types[receiver_name]
                        candidate = f"{type_class_name}.{method_name}"
                        lookup_result = resolver.lookup(candidate, caller_path=_caller_path)
                        if lookup_result.found and not _is_import_class_mismatch(
                            type_class_name, lookup_result.symbol, imports,
                            caller_file=str(file_path),
                        ):
                            edge_confidence = 0.85 * lookup_result.confidence
                            edge = Edge.create(
                                src=current_method.id,
                                dst=lookup_result.symbol.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                confidence=edge_confidence,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="ast_call_type_inferred",
                            )
                            edges.append(edge)
                            edge_added = True
                            resolved_sym = lookup_result.symbol
                        else:
                            # Fallback: method not found on type (likely
                            # inherited from a framework base class, e.g.
                            # JpaRepository.save). Link to the type's
                            # class/interface symbol instead — but only
                            # if the class symbol matches the file's
                            # imports. WI-fuhaj: without this guard, a
                            # local Jackson POJO named ``Logger`` absorbs
                            # every ``log.trace/error/warn`` call in any
                            # file that imports ``org.slf4j.Logger``,
                            # because the slf4j Logger isn't in the
                            # codebase symbols so the suffix-match
                            # resolver reaches the local POJO. On Kafka
                            # this produced 2057+ bogus edges to a
                            # 50-line config POJO.
                            type_sym = class_symbols.get(type_class_name)
                            if type_sym is not None and not _is_import_class_mismatch(
                                type_class_name, type_sym, imports,
                                caller_file=str(file_path),
                            ):
                                edge = Edge.create(
                                    src=current_method.id,
                                    dst=type_sym.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    confidence=0.70,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_call_inherited_method",
                                )
                                edges.append(edge)
                                edge_added = True

                    # Case 3.5: inherited field.method() — receiver is a
                    # field declared in a parent class, not in this file's
                    # var_types.  Walk the extends chain to find the field
                    # type, then resolve the method on that type.
                    if (
                        not edge_added
                        and receiver_name
                        and receiver_name not in var_types
                        and receiver_name not in class_symbols
                        and class_fields
                        and current_class
                    ):
                        parent = class_parents.get(current_class)
                        depth = 0
                        while parent and depth < 10:
                            parent_fields = class_fields.get(parent)
                            if parent_fields and receiver_name in parent_fields:
                                field_type = parent_fields[receiver_name]
                                candidate = f"{field_type}.{method_name}"
                                lookup_result = resolver.lookup(candidate, caller_path=_caller_path)
                                if (
                                    lookup_result.found
                                    and not _is_import_class_mismatch(
                                        field_type, lookup_result.symbol,
                                        imports,
                                        caller_file=str(file_path),
                                    )
                                ):
                                    edge = Edge.create(
                                        src=current_method.id,
                                        dst=lookup_result.symbol.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        confidence=0.80 * lookup_result.confidence,
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                        evidence_type="ast_call_inherited_field",
                                    )
                                    edges.append(edge)
                                    edge_added = True
                                    resolved_sym = lookup_result.symbol
                                break
                            parent = class_parents.get(parent)
                            depth += 1

                    # Return type inference: if the resolved method has
                    # a return type and the call is in a variable assignment,
                    # track the variable's type from that return type.
                    if resolved_sym and resolved_sym.kind == "method":
                        ret_name = _extract_java_return_type_name(
                            resolved_sym.signature
                        )
                        if ret_name and ret_name in class_symbols:
                            parent_node = node.parent
                            if parent_node and parent_node.type == "variable_declarator":
                                for pc in parent_node.children:
                                    if pc.type == "identifier":
                                        var_types[_node_text(pc, source)] = ret_name
                                        break

                    # Case 4: Fallback - try imported class or just the receiver name
                    # This handles edge cases where the receiver isn't recognized as a
                    # class or variable but might still match a symbol via imports.
                    # Also reachable when Case 2's import guard rejects a mismatched
                    # static call — WI-fuhaj — so the guard must be repeated here.
                    if not edge_added and receiver_name and resolver:
                        candidates = [f"{receiver_name}.{method_name}"]
                        # Try imported class name
                        if receiver_name in imports:
                            full_class = imports[receiver_name].split(".")[-1]
                            candidates.insert(0, f"{full_class}.{method_name}")
                        for candidate in candidates:
                            lookup_result = resolver.lookup(candidate, caller_path=_caller_path)
                            if (
                                lookup_result.found
                                and lookup_result.symbol is not None
                                and not _is_import_class_mismatch(
                                    receiver_name, lookup_result.symbol, imports,
                                    caller_file=str(file_path),
                                )
                            ):
                                edge = Edge.create(  # pragma: no cover
                                    src=current_method.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    confidence=0.80 * lookup_result.confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_call_direct",
                                )
                                edges.append(edge)  # pragma: no cover
                                edge_added = True  # pragma: no cover
                                break  # pragma: no cover

                    # Emit unresolved external edge when no resolution strategy succeeded
                    if not edge_added:
                        # Use qualified name when receiver is known
                        unresolved_name = (
                            f"{receiver_name}.{method_name}"
                            if receiver_name and receiver_name != "this"
                            else method_name
                        )
                        # Use import path as module hint when available
                        module = imports.get(receiver_name, "external") if receiver_name else "external"
                        edges.append(make_unresolved_edge(
                            "java", current_method.id, unresolved_name,
                            node.start_point[0] + 1, PASS_ID, run.execution_id,
                            module_hint=module,
                        ))

        # Object creation: new ClassName()
        elif node.type == "object_creation_expression":
            current_method = _get_enclosing_method(node, source, global_symbols, file_path, symbol_by_position)
            type_name = None

            # Find the type being instantiated
            for child in node.children:
                if child.type == "type_identifier":
                    type_name = _node_text(child, source)
                    if current_method:
                        lookup_result = class_resolver.lookup(type_name, caller_path=_caller_path)
                        if lookup_result.found and lookup_result.symbol is not None:
                            # INV-finak: skip edge if file imports an external
                            # class with the same simple name (e.g., Logger)
                            if _is_import_class_mismatch(
                                type_name, lookup_result.symbol, imports,
                                caller_file=str(file_path),
                            ):
                                pass  # Import points elsewhere; skip false edge
                            else:
                                edge = Edge.create(
                                    src=current_method.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="instantiates",
                                    line=node.start_point[0] + 1,
                                    confidence=0.95 * lookup_result.confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_new",
                                )
                                edges.append(edge)
                    break

            # Track variable type for type inference
            # Check if this new expression is part of a variable assignment
            if type_name and node.parent:
                parent = node.parent
                # Java variable declarations: Type varName = new Type();
                if parent.type == "variable_declarator":
                    # Find variable name
                    for pc in parent.children:
                        if pc.type == "identifier":
                            var_name = _node_text(pc, source)
                            var_types[var_name] = type_name
                            break

        # Method references: App::transform, this::process, Class::new
        # Creates a "references" edge from the enclosing method to the
        # referenced method.  Constructor references (::new) create
        # "instantiates" edges instead.
        elif node.type == "method_reference":
            current_method = _get_enclosing_method(
                node, source, global_symbols, file_path, symbol_by_position,
            )
            if current_method:
                children = [c for c in node.children if c.is_named]
                if len(children) >= 2:
                    class_node = children[0]
                    method_node = children[1]
                    class_name = _node_text(class_node, source)
                    method_name = _node_text(method_node, source)
                elif len(children) == 1:
                    # Only class part, method is anonymous (::new)
                    class_name = _node_text(children[0], source)
                    method_name = "new"
                else:  # pragma: no cover
                    class_name = None
                    method_name = None

                if class_name and method_name == "new":
                    # Constructor reference: try to resolve the class
                    lookup_result = class_resolver.lookup(class_name, caller_path=_caller_path)
                    if (
                        lookup_result.found
                        and lookup_result.symbol is not None
                        # INV-finak: skip if import points elsewhere
                        and not _is_import_class_mismatch(
                            class_name, lookup_result.symbol, imports,
                            caller_file=str(file_path),
                        )
                    ):
                        edge = Edge.create(
                            src=current_method.id,
                            dst=lookup_result.symbol.id,
                            edge_type="instantiates",
                            line=node.start_point[0] + 1,
                            confidence=0.80 * lookup_result.confidence,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="constructor_reference",
                        )
                        edges.append(edge)

                elif class_name and method_name:
                    # Method reference: resolve ClassName.methodName
                    # Try this::method → look up method in current class
                    target_name = None
                    if class_name == "this":
                        # Resolve in current class scope
                        target_name = method_name
                    else:
                        target_name = f"{class_name}.{method_name}"

                    lookup_result = resolver.lookup(target_name, caller_path=_caller_path)
                    if lookup_result.found and lookup_result.symbol is not None:
                        edge = Edge.create(
                            src=current_method.id,
                            dst=lookup_result.symbol.id,
                            edge_type="references",
                            line=node.start_point[0] + 1,
                            confidence=0.80 * lookup_result.confidence,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="method_reference",
                        )
                        edges.append(edge)

    return edges


# Standard Java/JDK annotations that carry no architectural information.
# Suppressed from unresolved decorated_by edges to avoid creating dangling
# edges to nonexistent nodes (INV-miniz / WI-divob).
_STANDARD_JAVA_ANNOTATIONS = frozenset({
    # java.lang
    "Override", "Deprecated", "SuppressWarnings", "SafeVarargs",
    "FunctionalInterface",
    # java.lang.annotation
    "Retention", "Target", "Documented", "Inherited", "Repeatable",
    # javax.annotation / jakarta.annotation
    "Nullable", "Nonnull", "NonNull", "CheckForNull",
    "Generated", "PostConstruct", "PreDestroy", "Resource",
    # Common testing annotations (never architectural)
    "Test", "BeforeEach", "AfterEach", "BeforeAll", "AfterAll",
    "ParameterizedTest", "RepeatedTest", "DisplayName", "Disabled",
    "Nested", "Tag", "ExtendWith", "RunWith",
    # Common quality/nullability annotations
    "NotNull", "NotEmpty", "NotBlank", "Valid",
    "VisibleForTesting", "Beta", "Internal",
    # Lombok (code generation, not architecture)
    "Getter", "Setter", "Data", "Builder", "NoArgsConstructor",
    "AllArgsConstructor", "RequiredArgsConstructor", "Value",
    "ToString", "EqualsAndHashCode", "Slf4j", "Log", "Log4j",
    "Log4j2", "CommonsLog",
})


def _extract_annotation_edges(
    symbols: list[Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract decorated_by edges from annotation metadata.

    For each symbol (class, interface, method) with decorators metadata,
    creates decorated_by edges to annotation types that exist in the
    analyzed codebase. This enables visibility of annotation patterns
    like Spring's @Service, @Controller, @GetMapping, etc.

    Args:
        symbols: All extracted symbols
        global_symbols: Map of name -> Symbol for annotation lookup
        run: Current analysis run for provenance

    Returns:
        List of decorated_by edges for annotation relationships
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

            # Try to resolve the annotation to a symbol
            annotation_sym = global_symbols.get(dec_name)

            line = sym.span.start_line if sym.span else 0

            if annotation_sym:
                edge = Edge.create(
                    src=sym.id,
                    dst=annotation_sym.id,
                    edge_type="decorated_by",
                    line=line,
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_annotation",
                )
                edges.append(edge)
            elif dec_name not in _STANDARD_JAVA_ANNOTATIONS:
                # Only emit unresolved edges for non-standard annotations
                # (likely project or framework annotations like @Service).
                # Standard annotations (@Override, @Deprecated, etc.) carry
                # no architectural information and would create dangling edges
                # to nonexistent nodes.
                dst_id = f"java:unresolved:0-0:{dec_name}:unresolved"
                edge = Edge.create(
                    src=sym.id,
                    dst=dst_id,
                    edge_type="decorated_by",
                    line=line,
                    confidence=0.50,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_annotation_unresolved",
                )
                edges.append(edge)

    return edges


def _analyze_java_file(
    file_path: Path,
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge], bool]:
    """Analyze a single Java file (legacy single-pass, used for testing).

    Returns (symbols, edges, success).
    """
    parser = _get_java_parser()
    if parser is None:
        return [], [], False

    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):
        return [], [], False

    symbols = _extract_symbols(tree, source, file_path, run)
    populate_docstrings_from_tree(tree.root_node, source, symbols)

    # Build symbol registries for edge extraction
    global_symbols: dict[str, Symbol] = {}
    class_symbols: dict[str, Symbol] = {}

    for sym in symbols:
        global_symbols[sym.name] = sym
        if sym.kind in ("class", "interface", "enum"):
            class_symbols[sym.name] = sym

    edges = _extract_edges(tree, source, file_path, run, global_symbols, class_symbols)
    return symbols, edges, True


@register_analyzer("java", capture_symbols_as="java")
def analyze_java(repo_root: Path) -> JavaAnalysisResult:
    """Analyze all Java files in a repository.

    Uses a two-pass approach:
    1. Parse all files and extract symbols into global registry
    2. Detect calls/inheritance and resolve against global symbol registry

    Returns a JavaAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-java is not available, returns empty result (silently skipped).
    """
    return _java_analyzer.analyze(repo_root)


def _analyze_java_impl(repo_root: Path) -> JavaAnalysisResult:
    """Internal implementation of Java analysis.

    Called by JavaTreeSitterAnalyzer.analyze() after grammar availability
    has been checked by the base class.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Check for tree-sitter-java availability
    if not _java_analyzer._check_grammar_available():
        skip_reason = "java analysis skipped: grammar not available. " \
                      "Install the required tree-sitter grammar package."
        warnings.warn(skip_reason, UserWarning, stacklevel=3)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return JavaAnalysisResult(
            run=run,
            skipped=True,
            skip_reason="java tree-sitter grammar not available",
        )

    parser = _get_java_parser()
    if parser is None:
        skip_reason = "java analysis skipped: grammar not available. " \
                      "Install the required tree-sitter grammar package."
        warnings.warn(skip_reason, UserWarning, stacklevel=3)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return JavaAnalysisResult(
            run=run,
            skipped=True,
            skip_reason="java tree-sitter grammar not available",
        )

    # Pass 1: Parse all files and extract symbols
    parsed_files: list[_ParsedFile] = []
    all_symbols: list[Symbol] = []
    files_analyzed = 0
    files_skipped = 0

    for file_path in find_java_files(repo_root):
        try:
            source = file_path.read_bytes()
            tree = parser.parse(source)
            file_imports = _extract_imports(tree, source)
            parsed_files.append(_ParsedFile(
                path=file_path, tree=tree, source=source, imports=file_imports
            ))
            symbols = _extract_symbols(tree, source, file_path, run)
            populate_docstrings_from_tree(tree.root_node, source, symbols)
            all_symbols.extend(symbols)
            files_analyzed += 1
        except (OSError, IOError):
            files_skipped += 1

    # Build global symbol registries
    global_symbols: dict[str, Symbol] = {}
    class_symbols: dict[str, Symbol] = {}
    # Multi-value lookup for extends/implements disambiguation (INV-015)
    class_by_name: dict[str, list[Symbol]] = {}
    # Multi-value method lookup for AMB-METHOD ambiguity guard
    global_methods: dict[str, list[Symbol]] = {}

    # Position-based lookup for enclosing method resolution in monorepos
    symbol_by_position: dict[tuple[str, int, int], Symbol] = {}

    for sym in all_symbols:
        global_symbols[sym.name] = sym
        symbol_by_position[(sym.path, sym.span.start_line, sym.span.start_col)] = sym
        if sym.kind in ("class", "interface", "enum"):
            class_symbols[sym.name] = sym
            if sym.name not in class_by_name:
                class_by_name[sym.name] = []
            class_by_name[sym.name].append(sym)
        elif sym.kind == "method":
            short = sym.name.split(".")[-1] if "." in sym.name else sym.name
            global_methods.setdefault(short, []).append(sym)

    # Build per-symbol file imports for extends/implements disambiguation
    # Maps symbol ID -> file-level imports (simple_name -> fqn)
    #
    # Uses a path-to-symbol-IDs index for O(n+m) instead of O(n*m).
    # The old nested loop iterated all 76K symbols for each of 5.6K files
    # (434M iterations, 400s on kafka).  This version: two linear passes.
    sym_file_imports: dict[str, dict[str, str]] = {}
    syms_by_path: dict[str, list[str]] = {}
    for sym in all_symbols:
        syms_by_path.setdefault(sym.path, []).append(sym.id)
    for pf in parsed_files:
        file_imports = pf.imports or {}
        if file_imports:
            for sym_id in syms_by_path.get(str(pf.path), []):
                sym_file_imports[sym_id] = file_imports

    # Build global class inheritance map for inherited method resolution.
    # Maps child class name -> parent class name (resolved to symbol name).
    global_class_parents: dict[str, str] = {}
    # Build global class field types for inherited field resolution.
    # Maps class_name -> {field_name: type_name}
    global_class_fields: dict[str, dict[str, str]] = {}
    for pf in parsed_files:
        for node in iter_tree(pf.tree.root_node):
            if node.type == "class_declaration":
                name = _get_class_name(node, pf.source)
                if not name:  # pragma: no cover
                    continue
                ancestors = _get_class_ancestors(node, pf.source)
                current = ".".join(ancestors + [name]) if ancestors else name
                for child in node.children:
                    if child.type == "superclass":
                        for subchild in child.children:
                            if subchild.type == "type_identifier":
                                parent_name = _node_text(subchild, pf.source)
                                # Resolve parent to its symbol name
                                dst_sym = None
                                src_sym = class_symbols.get(current)
                                if (
                                    src_sym is not None
                                    and class_by_name is not None
                                    and sym_file_imports is not None
                                ):
                                    dst_sym = _resolve_base_class_java(
                                        parent_name, src_sym,
                                        class_by_name, sym_file_imports,
                                    )
                                if dst_sym is None and parent_name in class_symbols:  # pragma: no cover
                                    dst_sym = class_symbols[parent_name]
                                if dst_sym is not None:
                                    global_class_parents[current] = dst_sym.name
                                break
            # Collect field declarations per class
            elif node.type == "field_declaration":
                cls_ancestors = _get_class_ancestors(node, pf.source)
                if cls_ancestors:
                    cls_name = ".".join(cls_ancestors)
                    type_node = None
                    for child in node.children:
                        if child.type == "type_identifier":
                            type_node = child
                        elif (
                            child.type == "variable_declarator"
                            and type_node is not None
                        ):
                            for vc in child.children:
                                if vc.type == "identifier":
                                    field_name = _node_text(vc, pf.source)
                                    type_name = _node_text(type_node, pf.source)
                                    if cls_name not in global_class_fields:
                                        global_class_fields[cls_name] = {}
                                    global_class_fields[cls_name][field_name] = type_name
                                    break

    # WI-kuroj: build method return-type registry from Pass 1 symbols.
    # Java already chains return types inline during edge extraction
    # (lines 1475-1488 in _extract_edges), so this registry is primarily
    # for cross-language consistency and downstream linker consumption.
    # The inline chaining handles the active use case.
    method_return_type_registry: dict[str, str] = {}
    for sym in all_symbols:
        if sym.kind == "method" and sym.meta:
            ret = sym.meta.get("return_type")
            if ret and ret != "Object":
                method_return_type_registry.setdefault(sym.name, ret)

    # Pass 2: Extract edges using global symbol registry
    # Build resolvers ONCE and share across all files — the registry is frozen
    # after Pass 1, so the suffix index only needs to be built once.
    resolver = NameResolver(global_symbols)
    class_resolver = NameResolver(class_symbols)
    method_resolver = ListNameResolver(
        global_methods, ambiguity_threshold=3,
    )
    all_edges: list[Edge] = []
    for pf in parsed_files:
        edges = _extract_edges(
            pf.tree, pf.source, pf.path, run,
            global_symbols, class_symbols, pf.imports or {},
            resolver=resolver,
            class_resolver=class_resolver,
            symbol_by_position=symbol_by_position,
            class_by_name=class_by_name,
            sym_file_imports=sym_file_imports,
            method_resolver=method_resolver,
            class_parents=global_class_parents,
            class_fields=global_class_fields,
        )
        # WI-lozug: emit module_attr_ref edges for field_access nodes
        # whose base resolves to an imported class or to ``System``
        # (an implicit java.lang import).  Pairs with the
        # ``attributes:`` entries in io_primitives/java.yaml
        # (System.out, System.err under ipc_send; System.in under
        # ipc_recv).
        #
        # Deliberate deviation from Go/JS: the callee-skip logic is
        # disabled by passing node kinds that do not occur in Java.
        # The io_boundary pipeline tags callee names against the
        # primitives catalog, and ``println`` / ``print`` are not in
        # the catalog.  If ``System.out`` were skipped when it is the
        # receiver of a method_invocation (the dominant usage
        # pattern), the ``attributes: [out]`` entry would never match.
        # A ``calls`` edge for ``println`` and a ``module_attr_ref``
        # edge for ``System.out`` target different catalog lookups,
        # so no double-count occurs downstream.
        java_imports = {
            **(pf.imports or {}),
            "System": "System",
        }
        file_caller = Symbol(
            id=make_file_id("java", str(pf.path)),
            name=pf.path.name,
            kind="module",
            language="java",
            path=str(pf.path),
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
        )
        emit_module_attribute_refs(
            pf.tree.root_node,
            pf.source,
            java_imports,
            file_caller,
            "java",
            edges,
            node_kinds=("field_access",),
            object_field_names=("object",),
            property_field_names=("field",),
            call_node_kinds=("__never_match_java__",),
            call_function_field_names=("__unused__",),
        )
        # ADR-0015 Tier 1: annotate edges with dataflow access modes
        _java_df = _get_dataflow_config("java")
        if _java_df is not None:
            edges = _annotate_dataflow(edges, pf.tree, pf.source, _java_df)
        all_edges.extend(edges)

    # Extract annotation edges (INV-012: decorators metadata -> decorated_by edges)
    annotation_edges = _extract_annotation_edges(all_symbols, global_symbols, run)
    all_edges.extend(annotation_edges)

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    # Parse Gradle/Maven deps for tier classification of boundary nodes (WI-duhom)
    from hypergumbo_lang_mainstream.jvm_deps import parse_jvm_dependencies
    jvm_manifest = parse_jvm_dependencies(repo_root)

    return JavaAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
        dependency_manifest=jvm_manifest if jvm_manifest.entries else None,
    )
