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
from typing import TYPE_CHECKING, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    is_grammar_available,
    iter_tree,
    make_symbol_id as _base_make_symbol_id,
    node_text as _node_text,
)

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "java-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Backwards compatibility alias
JavaAnalysisResult = AnalysisResult


def find_java_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Java files in the repository."""
    yield from find_files(repo_root, ["*.java"])


def is_java_tree_sitter_available() -> bool:
    """Check if tree-sitter and Java grammar are available."""
    return is_grammar_available("tree_sitter_java")


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
    """Extract all modifiers from a method/constructor declaration.

    Returns a list of modifier strings like ["public", "static", "native"].

    Tree-sitter-java uses modifier keywords as node types directly (e.g., "public",
    "static", "native"), so we can match against the node type.
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

    Note on type inference: Variable method calls (e.g., stub.method()) are resolved
    using constructor-only type inference. This tracks types from direct constructor
    calls (stub = new Client()) but NOT from factory methods (stub = Client.create()).
    This covers ~90% of real-world cases with minimal complexity.
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
                if arg_child.type == "string_literal":
                    # Simple string argument: @Annotation("value")
                    args.append(_java_value_to_python(arg_child, source))
                elif arg_child.type == "element_value_pair":
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
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "enum"),
                    name=full_name,
                    kind="enum",
                    language="java",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
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
                    signature=signature,
                    modifiers=modifiers,
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

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), span.start_line, span.end_line, full_name, "constructor"),
                    name=full_name,
                    kind="constructor",
                    language="java",
                    path=str(file_path),
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=signature,
                    modifiers=modifiers,
                )
                symbols.append(symbol)

    return symbols


def _get_enclosing_method(
    node: "tree_sitter.Node",
    source: bytes,
    global_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing method/constructor.

    Returns the Symbol for the enclosing method, or None if not inside a method.
    """
    current = node.parent
    while current is not None:
        if current.type in ("method_declaration", "constructor_declaration"):
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
    # Track variable types for type inference: var_name -> class_name
    var_types: dict[str, str] = {}

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
                                    if parent_name in class_symbols:
                                        dst_sym = class_symbols[parent_name]
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
                                            if iface_name in class_symbols:
                                                dst_sym = class_symbols[iface_name]
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

        # Method invocations
        elif node.type == "method_invocation":
            current_method = _get_enclosing_method(node, source, global_symbols)
            if current_method:
                # Get the method name being called
                method_name = None
                receiver_name = None
                for child in node.children:
                    if child.type == "identifier":
                        # First identifier is receiver, second is method name
                        if receiver_name is None and method_name is None:
                            # This could be either receiver.method() or just method()
                            receiver_name = _node_text(child, source)
                        else:
                            # This is the method name in receiver.method()
                            method_name = _node_text(child, source)

                # If only one identifier found, it's the method name (no receiver)
                if method_name is None and receiver_name is not None:
                    method_name = receiver_name
                    receiver_name = None

                if method_name:
                    # Get class context
                    ancestors = _get_class_ancestors(node, source)
                    current_class = ".".join(ancestors) if ancestors else None
                    edge_added = False

                    # Case 1: this.method() or method() - resolve in current class
                    if receiver_name is None or receiver_name == "this":
                        if current_class:
                            candidate = f"{current_class}.{method_name}"
                            lookup_result = resolver.lookup(candidate)
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

                    # Case 2: ClassName.method() - static call
                    elif receiver_name and receiver_name in class_symbols:
                        candidate = f"{receiver_name}.{method_name}"
                        lookup_result = resolver.lookup(candidate)
                        if lookup_result.found:
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

                    # Case 3: variable.method() - use type inference
                    elif receiver_name and receiver_name in var_types:
                        type_class_name = var_types[receiver_name]
                        candidate = f"{type_class_name}.{method_name}"
                        lookup_result = resolver.lookup(candidate)
                        if lookup_result.found:
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

                    # Case 4: Fallback - try imported class or just the receiver name
                    # This handles edge cases where the receiver isn't recognized as a
                    # class or variable but might still match a symbol via imports.
                    # In practice, this is rarely hit since Case 2 handles most static
                    # calls and Case 3 handles most instance calls.
                    if not edge_added and receiver_name and resolver:  # pragma: no cover
                        candidates = [f"{receiver_name}.{method_name}"]
                        # Try imported class name
                        if receiver_name in imports:
                            full_class = imports[receiver_name].split(".")[-1]
                            candidates.insert(0, f"{full_class}.{method_name}")
                        for candidate in candidates:
                            lookup_result = resolver.lookup(candidate)
                            if lookup_result.found and lookup_result.symbol is not None:
                                edge = Edge.create(
                                    src=current_method.id,
                                    dst=lookup_result.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    confidence=0.80 * lookup_result.confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="ast_call_direct",
                                )
                                edges.append(edge)
                                break

        # Object creation: new ClassName()
        elif node.type == "object_creation_expression":
            current_method = _get_enclosing_method(node, source, global_symbols)
            type_name = None

            # Find the type being instantiated
            for child in node.children:
                if child.type == "type_identifier":
                    type_name = _node_text(child, source)
                    if current_method:
                        lookup_result = class_resolver.lookup(type_name)
                        if lookup_result.found and lookup_result.symbol is not None:
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

    # Build symbol registries for edge extraction
    global_symbols: dict[str, Symbol] = {}
    class_symbols: dict[str, Symbol] = {}

    for sym in symbols:
        global_symbols[sym.name] = sym
        if sym.kind in ("class", "interface", "enum"):
            class_symbols[sym.name] = sym

    edges = _extract_edges(tree, source, file_path, run, global_symbols, class_symbols)
    return symbols, edges, True


def analyze_java(repo_root: Path) -> JavaAnalysisResult:
    """Analyze all Java files in a repository.

    Uses a two-pass approach:
    1. Parse all files and extract symbols into global registry
    2. Detect calls/inheritance and resolve against global symbol registry

    Returns a JavaAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-java is not available, returns empty result (silently skipped).
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Check for tree-sitter-java availability
    if not is_java_tree_sitter_available():
        skip_reason = "Java analysis skipped: requires tree-sitter-java (pip install tree-sitter-java)"
        warnings.warn(skip_reason, stacklevel=2)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return JavaAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    parser = _get_java_parser()
    if parser is None:
        skip_reason = "Java analysis skipped: requires tree-sitter-java (pip install tree-sitter-java)"
        warnings.warn(skip_reason, stacklevel=2)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return JavaAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
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
            all_symbols.extend(symbols)
            files_analyzed += 1
        except (OSError, IOError):
            files_skipped += 1

    # Build global symbol registries
    global_symbols: dict[str, Symbol] = {}
    class_symbols: dict[str, Symbol] = {}

    for sym in all_symbols:
        global_symbols[sym.name] = sym
        if sym.kind in ("class", "interface", "enum"):
            class_symbols[sym.name] = sym

    # Pass 2: Extract edges using global symbol registry
    all_edges: list[Edge] = []
    for pf in parsed_files:
        edges = _extract_edges(
            pf.tree, pf.source, pf.path, run,
            global_symbols, class_symbols, pf.imports or {}
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return JavaAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
