"""C# analysis pass using tree-sitter-c-sharp.

This analyzer uses tree-sitter to parse C# files and extract:
- Class declarations
- Interface declarations
- Struct declarations
- Enum declarations
- Method declarations (inside classes/structs)
- Constructor declarations
- Property declarations
- Function call relationships
- Using directives (imports)
- Object instantiation

If tree-sitter with C# support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-c-sharp is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls, instantiations, and resolve against global symbol registry
4. Detect using directives and object creations

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-c-sharp package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other language analyzers for consistency
"""
from __future__ import annotations

import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "csharp-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_csharp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all C# files in the repository."""
    yield from find_files(repo_root, ["*.cs"])


def is_csharp_tree_sitter_available() -> bool:
    """Check if tree-sitter with C# grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec("tree_sitter_c_sharp") is None:
        return False
    return True


@dataclass
class CSharpAnalysisResult:
    """Result of analyzing C# files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"csharp:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a C# file node (used as import edge source)."""
    return f"csharp:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _extract_annotations(
    node: "tree_sitter.Node", source: bytes
) -> list[dict[str, object]]:
    """Extract all C# attributes from a node (class, interface, method, etc).

    C# attributes appear in attribute_list children. Each attribute may have:
    - name: The attribute name (e.g., "HttpGet", "Route", "ApiController")
    - args: Positional arguments from attribute_argument_list
    - kwargs: Named arguments (name = value pairs)

    Returns list of attribute info dicts: [{"name": str, "args": list, "kwargs": dict}]
    """
    annotations: list[dict[str, object]] = []

    for child in node.children:
        if child.type == "attribute_list":
            # attribute_list contains one or more attributes
            for attr in child.children:
                if attr.type == "attribute":
                    attr_info: dict[str, object] = {"name": "", "args": [], "kwargs": {}}

                    # Get the attribute name (may be qualified like System.Serializable)
                    for attr_child in attr.children:
                        if attr_child.type == "identifier":
                            attr_info["name"] = _node_text(attr_child, source)
                        elif attr_child.type == "qualified_name":
                            attr_info["name"] = _node_text(attr_child, source)

                    # Extract arguments from attribute_argument_list
                    arg_list = _find_child_by_type(attr, "attribute_argument_list")
                    if arg_list:
                        args: list[str] = []
                        kwargs: dict[str, str] = {}

                        for arg in arg_list.children:
                            if arg.type == "attribute_argument":
                                # Check if it's a named argument via assignment_expression
                                assign_expr = _find_child_by_type(
                                    arg, "assignment_expression"
                                )
                                if assign_expr:
                                    # Named argument: name = value
                                    name_node = _find_child_by_type(
                                        assign_expr, "identifier"
                                    )
                                    arg_name = (
                                        _node_text(name_node, source)
                                        if name_node
                                        else ""
                                    )

                                    # Value is the string_literal after =
                                    for assign_child in assign_expr.children:
                                        if assign_child.type == "string_literal":
                                            value = _node_text(assign_child, source)
                                            if value.startswith('"') and value.endswith(
                                                '"'
                                            ):
                                                value = value[1:-1]
                                            if arg_name:
                                                kwargs[arg_name] = value
                                            break
                                else:
                                    # Positional argument
                                    for arg_child in arg.children:
                                        if arg_child.type == "string_literal":
                                            value = _node_text(arg_child, source)
                                            # Strip quotes from string literals
                                            if value.startswith('"') and value.endswith(
                                                '"'
                                            ):
                                                value = value[1:-1]
                                            args.append(value)
                                            break
                                        else:
                                            # Non-string literal (e.g., number, bool)
                                            value = _node_text(arg_child, source)
                                            args.append(value)
                                            break

                        attr_info["args"] = args
                        attr_info["kwargs"] = kwargs

                    if attr_info["name"]:
                        annotations.append(attr_info)

    return annotations


def _find_children_by_type(node: "tree_sitter.Node", type_name: str) -> list["tree_sitter.Node"]:
    """Find all children of given type."""
    return [child for child in node.children if child.type == type_name]


def _extract_type_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract type text from a type node."""
    return _node_text(node, source)


def _extract_param_types(
    node: "tree_sitter.Node", source: bytes
) -> dict[str, str]:
    """Extract parameter name -> type mapping from a method/constructor declaration.

    This enables type inference for method calls on parameters, e.g.:
        void Process(Database db) {
            db.Save();  // resolves to Database.Save
        }

    Returns:
        Dict mapping parameter names to their type names (simple name only).
    """
    param_types: dict[str, str] = {}

    # Type node types in C#
    type_node_types = ("predefined_type", "generic_name", "array_type",
                       "nullable_type", "qualified_name", "ref_type", "pointer_type")

    # Find parameter_list node
    params_node = _find_child_by_type(node, "parameter_list")
    if params_node is None:
        return param_types  # pragma: no cover - no params in method

    # Extract parameter types
    for child in params_node.children:
        if child.type == "parameter":
            param_type = None
            param_name = None
            for subchild in child.children:
                # Type nodes
                if subchild.type in type_node_types:
                    param_type = _extract_type_text(subchild, source)
                    # Strip generic parameters: List<T> -> List
                    if "<" in param_type:
                        param_type = param_type.split("<")[0]
                    # Strip array brackets: int[] -> int
                    if "[" in param_type:
                        param_type = param_type.split("[")[0]
                    # Strip nullable: int? -> int
                    param_type = param_type.rstrip("?")
                # Custom types use identifier
                elif subchild.type == "identifier":
                    if param_type is None:
                        # First identifier is the type
                        param_type = _node_text(subchild, source)
                    else:
                        # Second identifier is the name
                        param_name = _node_text(subchild, source)
            if param_type and param_name:
                param_types[param_name] = param_type

    return param_types


def _extract_csharp_signature(
    node: "tree_sitter.Node", source: bytes, is_constructor: bool = False
) -> Optional[str]:
    """Extract function signature from a C# method or constructor declaration.

    Returns signature like:
    - "(int x, int y) int" for regular methods
    - "(string msg)" for void methods (no return type shown)
    - "(string name, int age)" for constructors (no return type)

    Args:
        node: The method_declaration or constructor_declaration node.
        source: The source code bytes.
        is_constructor: True if this is a constructor (no return type).

    Returns:
        The signature string, or None if extraction fails.
    """
    params_node = None
    return_type = None

    # Find parameter_list and return type
    # The return type is a type node (predefined_type, generic_name, etc.)
    # NOT a plain identifier (that's the method name)
    type_node_types = ("predefined_type", "generic_name", "array_type",
                       "nullable_type", "qualified_name", "ref_type", "pointer_type")

    for child in node.children:
        if child.type == "parameter_list":
            params_node = child
        # Return type is a type node, not identifier
        elif child.type in type_node_types:
            return_type = _extract_type_text(child, source)
        # Handle custom type as return (identifier that's a type, not method name)
        # The identifier for return type comes BEFORE parameter_list
        elif child.type == "identifier" and params_node is None and return_type is None:
            # Check if this is a type (followed by another identifier which is method name)
            # Actually, for custom return types we need special handling
            # Let's skip this for now - will use the type nodes above
            pass

    if params_node is None:
        return None  # pragma: no cover

    # Extract parameters
    params: list[str] = []
    for child in params_node.children:
        if child.type == "parameter":
            param_type = None
            param_name = None
            for subchild in child.children:
                # Type nodes (not plain identifier for type - those are param names)
                if subchild.type in type_node_types:
                    param_type = _extract_type_text(subchild, source)
                # For custom types, identifier IS the type if param_type not set
                elif subchild.type == "identifier":
                    if param_type is None:
                        param_type = _node_text(subchild, source)
                    else:
                        param_name = _node_text(subchild, source)
            if param_type and param_name:
                params.append(f"{param_type} {param_name}")

    params_str = ", ".join(params)
    signature = f"({params_str})"

    # Add return type for methods (not constructors), but omit void
    if not is_constructor and return_type and return_type != "void":
        signature += f" {return_type}"

    return signature


def _extract_method_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract the method name from a method declaration.

    In C#, method_declaration has: [modifiers] return_type method_name(params)
    The return_type might be an identifier (e.g., 'Product') or predefined_type (e.g., 'int').
    The method_name is always an identifier after the return type.
    """
    identifiers = _find_children_by_type(node, "identifier")
    # If return type is a predefined_type (int, void, etc.), first identifier is method name
    # If return type is an identifier (custom type), second identifier is method name
    has_predefined_type = _find_child_by_type(node, "predefined_type") is not None
    has_generic_name = _find_child_by_type(node, "generic_name") is not None

    if has_predefined_type or has_generic_name:
        # Return type is predefined (int, void, etc.) or generic (Task<T>)
        # First identifier is method name
        if identifiers:
            return _node_text(identifiers[0], source)
    else:
        # Return type is a custom type (an identifier)
        # Second identifier is method name
        if len(identifiers) >= 2:
            return _node_text(identifiers[1], source)
        elif identifiers:  # pragma: no cover - defensive fallback
            # Fallback: only one identifier means no custom return type detected
            return _node_text(identifiers[0], source)
    return None  # pragma: no cover - defensive


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)
    using_aliases: dict[str, str] = field(default_factory=dict)


def _get_enclosing_class(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing class/interface/struct name.

    Args:
        node: The current node.
        source: Source bytes for extracting text.

    Returns:
        The name of the enclosing type, or None if not inside a type.
    """
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "interface_declaration", "struct_declaration"):
            name_node = _find_child_by_type(current, "identifier")
            if name_node:
                return _node_text(name_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_using_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract using directive aliases from a parsed C# tree.

    Maps type names to their full namespace paths for disambiguation:
    - using System.Collections.Generic; -> Generic: System.Collections.Generic
    - using MyApp.Services; -> Services: MyApp.Services
    - using Svc = MyApp.Services; -> Svc: MyApp.Services

    Returns dict mapping local alias/name -> full namespace path.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "using_directive":
            continue

        # Check if this is an aliased using (has = child)
        # Structure: using_directive { identifier, =, qualified_name }
        has_equals = any(child.type == "=" for child in node.children)

        if has_equals:
            # Handle 'using Alias = Namespace.Type;' (aliased using)
            # First identifier is the alias, qualified_name is the path
            alias_node = _find_child_by_type(node, "identifier")
            path_node = _find_child_by_type(node, "qualified_name")
            if alias_node and path_node:
                alias = _node_text(alias_node, source)
                full_path = _node_text(path_node, source)
                if alias and full_path:
                    aliases[alias] = full_path
            continue

        # Handle regular 'using Namespace.Type;'
        name_node = _find_child_by_type(node, "qualified_name")
        if name_node:
            full_path = _node_text(name_node, source)
            if full_path and "." in full_path:
                # Last segment is the imported name
                name = full_path.rsplit(".", 1)[-1]
                if name:
                    aliases[name] = full_path
            continue

        # Handle simple 'using Namespace;'
        id_node = _find_child_by_type(node, "identifier")
        if id_node:
            name = _node_text(id_node, source)
            if name:
                aliases[name] = name

    return aliases


def _extract_base_list(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract base classes and interfaces from base_list (META-001).

    C# syntax: class User : BaseModel, IEntity, IDisposable { }
    AST structure:
        class_declaration
            identifier "User"
            base_list
                identifier "BaseModel"
                identifier "IEntity"
                identifier "IDisposable"

    Returns list of base type names.
    """
    base_classes: list[str] = []

    for child in node.children:
        if child.type == "base_list":
            for base_child in child.children:
                if base_child.type in ("identifier", "generic_name", "qualified_name"):
                    base_name = _node_text(base_child, source)
                    # Strip generic parameters: List<int> -> List
                    if "<" in base_name:
                        base_name = base_name.split("<")[0]
                    if base_name:
                        base_classes.append(base_name)
            break

    return base_classes


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single C# file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.
    """
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):  # pragma: no cover - IO errors hard to trigger in tests
        return FileAnalysis()

    # Extract using aliases for disambiguation
    using_aliases = _extract_using_aliases(tree, source)

    analysis = FileAnalysis(using_aliases=using_aliases)

    def extract_name_from_declaration(node: "tree_sitter.Node") -> Optional[str]:
        """Extract the identifier name from a declaration node."""
        name_node = _find_child_by_type(node, "identifier")
        if name_node:
            return _node_text(name_node, source)
        return None  # pragma: no cover - defensive

    for node in iter_tree(tree.root_node):
        # Class declaration
        if node.type == "class_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract annotations for FRAMEWORK_PATTERNS phase
                annotations = _extract_annotations(node, source)
                # Extract base classes/interfaces (META-001)
                base_classes = _extract_base_list(node, source)

                meta: dict[str, object] | None = None
                if annotations or base_classes:
                    meta = {}
                    if annotations:
                        meta["annotations"] = annotations
                    if base_classes:
                        meta["base_classes"] = base_classes

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "class"),
                    name=name,
                    kind="class",
                    language="csharp",
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
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

        # Interface declaration
        elif node.type == "interface_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "interface"),
                    name=name,
                    kind="interface",
                    language="csharp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

        # Struct declaration
        elif node.type == "struct_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "struct"),
                    name=name,
                    kind="struct",
                    language="csharp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

        # Enum declaration
        elif node.type == "enum_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "enum"),
                    name=name,
                    kind="enum",
                    language="csharp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

        # Method declaration
        elif node.type == "method_declaration":
            name = _extract_method_name(node, source)
            if name:
                current_class = _get_enclosing_class(node, source)
                if current_class:
                    full_name = f"{current_class}.{name}"
                else:
                    full_name = name  # pragma: no cover - should always be in class

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract all annotations for FRAMEWORK_PATTERNS phase
                annotations = _extract_annotations(node, source)

                # Build meta dict
                meta: dict[str, object] | None = None
                if annotations:
                    meta = {"annotations": annotations}

                # Extract signature
                signature = _extract_csharp_signature(node, source, is_constructor=False)

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="csharp",
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
                    signature=signature,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Constructor declaration
        elif node.type == "constructor_declaration":
            name = extract_name_from_declaration(node)
            if name:
                current_class = _get_enclosing_class(node, source)
                full_name = f"{current_class}.{name}" if current_class else name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract signature (constructors have no return type)
                signature = _extract_csharp_signature(node, source, is_constructor=True)

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "constructor"),
                    name=full_name,
                    kind="constructor",
                    language="csharp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    signature=signature,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol
                analysis.symbol_by_name[full_name] = symbol

        # Property declaration
        elif node.type == "property_declaration":
            name = extract_name_from_declaration(node)
            if name:
                current_class = _get_enclosing_class(node, source)
                full_name = f"{current_class}.{name}" if current_class else name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "property"),
                    name=full_name,
                    kind="property",
                    language="csharp",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol
                analysis.symbol_by_name[full_name] = symbol

    return analysis


def _get_enclosing_method(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing method or constructor.

    Args:
        node: The current node.
        source: Source bytes for extracting text.
        local_symbols: Map of method/constructor names to Symbol objects.

    Returns:
        The Symbol for the enclosing method/constructor, or None if not inside one.
    """
    current = node.parent
    while current is not None:
        if current.type == "method_declaration":
            method_name = _extract_method_name(current, source)
            if method_name and method_name in local_symbols:
                return local_symbols[method_name]
        elif current.type == "constructor_declaration":  # pragma: no cover
            name_node = _find_child_by_type(current, "identifier")
            if name_node:
                ctor_name = _node_text(name_node, source)
                if ctor_name in local_symbols:
                    return local_symbols[ctor_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
    resolver: NameResolver | None = None,
    using_aliases: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract call, import, and instantiation edges from a file.

    Uses iterative tree traversal to avoid RecursionError on deeply nested code.

    Type inference tracks types from:
    - Constructor calls: var db = new Database() -> db has type Database
    - Method/constructor parameters: void Process(Database db) -> db has type Database

    Args:
        using_aliases: Optional dict mapping type names to namespace paths for disambiguation.
    """
    if resolver is None:  # pragma: no cover - defensive
        resolver = NameResolver(global_symbols)
    if using_aliases is None:  # pragma: no cover - defensive default
        using_aliases = {}
    try:
        source = file_path.read_bytes()
        tree = parser.parse(source)
    except (OSError, IOError):  # pragma: no cover - IO errors hard to trigger in tests
        return []

    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))
    # Track variable types for type inference: var_name -> class_name
    var_types: dict[str, str] = {}

    def get_callee_name(node: "tree_sitter.Node") -> Optional[str]:
        """Extract the method name being called from an invocation expression."""
        # Find the expression being invoked (function part before argument_list)
        for child in node.children:
            if child.type == "member_access_expression":
                # e.g., Console.WriteLine or obj.Method
                # Get the last identifier (the method name)
                identifiers = _find_children_by_type(child, "identifier")
                if identifiers:
                    return _node_text(identifiers[-1], source)
            elif child.type == "identifier":
                # Direct function call
                return _node_text(child, source)
        return None  # pragma: no cover - defensive

    for node in iter_tree(tree.root_node):
        # Using directive
        if node.type == "using_directive":
            # Get the namespace being imported
            name_node = _find_child_by_type(node, "identifier")
            if not name_node:
                name_node = _find_child_by_type(node, "qualified_name")
            if name_node:
                import_path = _node_text(name_node, source)
                edges.append(Edge.create(
                    src=file_id,
                    dst=f"csharp:{import_path}:0-0:namespace:namespace",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    evidence_type="using_directive",
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                ))

        # Method/constructor declarations - extract parameter types for type inference
        elif node.type in ("method_declaration", "constructor_declaration"):
            param_types = _extract_param_types(node, source)
            # Add parameter types to var_types for method call resolution
            for param_name, param_type in param_types.items():
                var_types[param_name] = param_type

        # Invocation expression (method call)
        elif node.type == "invocation_expression":
            current_function = _get_enclosing_method(node, source, local_symbols)
            if current_function is not None:
                # Check for member_access_expression (receiver.method() pattern)
                member_access = _find_child_by_type(node, "member_access_expression")
                if member_access:
                    # Extract receiver and method name
                    identifiers = _find_children_by_type(member_access, "identifier")
                    if len(identifiers) >= 2:
                        receiver_name = _node_text(identifiers[0], source)
                        method_name = _node_text(identifiers[-1], source)

                        # Try type inference: receiver.method() -> ClassName.method
                        if receiver_name in var_types:
                            class_name = var_types[receiver_name]
                            qualified_name = f"{class_name}.{method_name}"
                            if qualified_name in local_symbols:
                                callee = local_symbols[qualified_name]
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=callee.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="method_call_type_inferred",
                                    confidence=0.85,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))
                                continue
                            else:
                                # Use type's import path for disambiguation
                                import_hint = using_aliases.get(class_name)
                                lookup_result = resolver.lookup(qualified_name, path_hint=import_hint)
                                if lookup_result.found and lookup_result.symbol is not None:
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=lookup_result.symbol.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="method_call_type_inferred",
                                        confidence=0.80 * lookup_result.confidence,
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                    ))
                                    continue

                # Fallback to original simple name resolution
                callee_name = get_callee_name(node)
                if callee_name:
                    # Check local symbols first
                    if callee_name in local_symbols:
                        callee = local_symbols[callee_name]
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="method_call",
                            confidence=0.85,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))
                    # Check global symbols via resolver
                    else:
                        # Use import path for disambiguation
                        import_hint = using_aliases.get(callee_name)
                        lookup_result = resolver.lookup(callee_name, path_hint=import_hint)
                        if lookup_result.found and lookup_result.symbol is not None:
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=lookup_result.symbol.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="method_call",
                                confidence=0.80 * lookup_result.confidence,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))

        # Object creation expression (new ClassName())
        elif node.type == "object_creation_expression":
            current_function = _get_enclosing_method(node, source, local_symbols)
            type_node = _find_child_by_type(node, "identifier")
            type_name = _node_text(type_node, source) if type_node else None

            if current_function is not None and type_name:
                # Check if it's a known class
                if type_name in local_symbols:
                    target = local_symbols[type_name]
                    edges.append(Edge.create(
                        src=current_function.id,
                        dst=target.id,
                        edge_type="instantiates",
                        line=node.start_point[0] + 1,
                        evidence_type="object_creation",
                        confidence=0.90,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))
                else:
                    # Use import path for disambiguation
                    import_hint = using_aliases.get(type_name)
                    lookup_result = resolver.lookup(type_name, path_hint=import_hint)
                    if lookup_result.found and lookup_result.symbol is not None:
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=lookup_result.symbol.id,
                            edge_type="instantiates",
                            line=node.start_point[0] + 1,
                            evidence_type="object_creation",
                            confidence=0.85 * lookup_result.confidence,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

            # Track variable type for type inference
            # C#: var db = new Database() or Database db = new Database()
            if type_name and node.parent:
                parent = node.parent
                # Check for variable_declarator (var x = new Class())
                if parent.type == "variable_declarator":
                    var_name_node = _find_child_by_type(parent, "identifier")
                    if var_name_node:
                        var_name = _node_text(var_name_node, source)
                        var_types[var_name] = type_name
                # Check for equals_value_clause in a variable_declaration
                # (alternative AST pattern - defensive code)
                elif parent.type == "equals_value_clause":  # pragma: no cover - alt AST pattern
                    grandparent = parent.parent
                    if grandparent and grandparent.type == "variable_declarator":
                        var_name_node = _find_child_by_type(grandparent, "identifier")
                        if var_name_node:
                            var_name = _node_text(var_name_node, source)
                            var_types[var_name] = type_name

    return edges


def analyze_csharp(repo_root: Path) -> CSharpAnalysisResult:
    """Analyze all C# files in a repository.

    Returns a CSharpAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-c-sharp is not available, returns a skipped result.
    """
    if not is_csharp_tree_sitter_available():
        warnings.warn(
            "tree-sitter-c-sharp not available. Install with: pip install hypergumbo[csharp]",
            stacklevel=2,
        )
        return CSharpAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-c-sharp not available",
        )

    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Import tree-sitter-c-sharp
    try:
        import tree_sitter_c_sharp
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_c_sharp.language())
        parser = tree_sitter.Parser(lang)
    except Exception as e:  # pragma: no cover - parser load failure hard to trigger
        run.duration_ms = int((time.time() - start_time) * 1000)
        return CSharpAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=f"Failed to load C# parser: {e}",
        )

    # Pass 1: Extract all symbols
    file_analyses: dict[Path, FileAnalysis] = {}
    files_skipped = 0

    for cs_file in find_csharp_files(repo_root):
        analysis = _extract_symbols_from_file(cs_file, parser, run)
        if analysis.symbols:
            file_analyses[cs_file] = analysis
        else:
            files_skipped += 1

    # Build global symbol registry
    global_symbols: dict[str, Symbol] = {}
    for analysis in file_analyses.values():
        for symbol in analysis.symbols:
            # Store by short name for cross-file resolution
            short_name = symbol.name.split(".")[-1] if "." in symbol.name else symbol.name
            global_symbols[short_name] = symbol
            global_symbols[symbol.name] = symbol

    # Pass 2: Extract edges
    resolver = NameResolver(global_symbols)
    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for cs_file, analysis in file_analyses.items():
        all_symbols.extend(analysis.symbols)

        edges = _extract_edges_from_file(
            cs_file, parser, analysis.symbol_by_name, global_symbols, run, resolver,
            using_aliases=analysis.using_aliases
        )
        all_edges.extend(edges)

    run.files_analyzed = len(file_analyses)
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return CSharpAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
