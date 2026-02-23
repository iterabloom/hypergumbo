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

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis as _BaseFileAnalysis,
    TreeSitterAnalyzer,
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

PASS_ID = make_pass_id("csharp")


def find_csharp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all C# files in the repository."""
    yield from find_files(repo_root, ["*.cs"])



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
                            attr_info["name"] = node_text(attr_child, source)
                        elif attr_child.type == "qualified_name":
                            attr_info["name"] = node_text(attr_child, source)

                    # Extract arguments from attribute_argument_list
                    arg_list = find_child_by_type(attr, "attribute_argument_list")
                    if arg_list:
                        args: list[str] = []
                        kwargs: dict[str, str] = {}

                        for arg in arg_list.children:
                            if arg.type == "attribute_argument":
                                # Check if it's a named argument via assignment_expression
                                assign_expr = find_child_by_type(
                                    arg, "assignment_expression"
                                )
                                if assign_expr:
                                    # Named argument: name = value
                                    name_node = find_child_by_type(
                                        assign_expr, "identifier"
                                    )
                                    arg_name = (
                                        node_text(name_node, source)
                                        if name_node
                                        else ""
                                    )

                                    # Value is the string_literal after =
                                    for assign_child in assign_expr.children:
                                        if assign_child.type == "string_literal":
                                            value = node_text(assign_child, source)
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
                                            value = node_text(arg_child, source)
                                            # Strip quotes from string literals
                                            if value.startswith('"') and value.endswith(
                                                '"'
                                            ):
                                                value = value[1:-1]
                                            args.append(value)
                                            break
                                        else:
                                            # Non-string literal (e.g., number, bool)
                                            value = node_text(arg_child, source)
                                            args.append(value)
                                            break

                        attr_info["args"] = args
                        attr_info["kwargs"] = kwargs

                    if attr_info["name"]:
                        annotations.append(attr_info)

    return annotations


# C# modifiers that can appear on declarations.
# tree-sitter-c-sharp wraps each keyword in a ``modifier`` node whose
# single child has the keyword as its node type.
CSHARP_MODIFIERS = {
    "public", "private", "protected", "internal",
    "static", "abstract", "virtual", "override",
    "sealed", "async", "readonly", "extern",
    "partial", "unsafe", "volatile", "new",
}


def _extract_modifiers(node: "tree_sitter.Node") -> list[str]:
    """Extract all modifiers from a C# declaration node.

    In tree-sitter-c-sharp, each modifier keyword is wrapped in a separate
    ``modifier`` child node.  For example ``public static void Foo()``
    produces two ``modifier`` children: one containing ``public`` and one
    containing ``static``.

    Returns a list of modifier strings like ``["public", "static"]``.
    """
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "modifier":
            for kw in child.children:
                if kw.type in CSHARP_MODIFIERS:
                    modifiers.append(kw.type)
    return modifiers


def _find_children_by_type(node: "tree_sitter.Node", type_name: str) -> list["tree_sitter.Node"]:
    """Find all children of given type."""
    return [child for child in node.children if child.type == type_name]


def _extract_type_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract type text from a type node."""
    return node_text(node, source)


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
    params_node = find_child_by_type(node, "parameter_list")
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
                        param_type = node_text(subchild, source)
                    else:
                        # Second identifier is the name
                        param_name = node_text(subchild, source)
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

    # Collect identifiers before parameter_list to distinguish return type
    # from method name.  In C# method_declaration children:
    #   [modifiers] return_type method_name parameter_list block
    # Custom return types (e.g. ServiceClient) are plain identifier nodes,
    # so if there are 2+ identifiers before the parameter_list, the first
    # is the return type.
    pre_params_identifiers: list[str] = []

    for child in node.children:
        if child.type == "parameter_list":
            params_node = child
        # Return type is a type node, not identifier
        elif child.type in type_node_types:
            return_type = _extract_type_text(child, source)
        # Track identifiers before parameter_list for custom return type detection
        elif child.type == "identifier" and params_node is None:
            pre_params_identifiers.append(node_text(child, source))

    # If no type_node return type was found but there are 2+ identifiers
    # before the parameter_list, the first identifier is the return type
    if return_type is None and len(pre_params_identifiers) >= 2:
        return_type = pre_params_identifiers[0]

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
                        param_type = node_text(subchild, source)
                    else:
                        param_name = node_text(subchild, source)
            if param_type and param_name:
                params.append(f"{param_type} {param_name}")

    params_str = ", ".join(params)
    signature = f"({params_str})"

    # Add return type for methods (not constructors), but omit void
    if not is_constructor and return_type and return_type != "void":
        signature += f" {return_type}"

    return signature


def normalize_csharp_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a C# signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_types_first
    return normalize_signature_types_first(signature, type_params)


def _extract_csharp_return_type_name(signature: str | None) -> str | None:
    """Extract the return type name from a C# method signature.

    C# signatures use the format ``(params) ReturnType`` where void is omitted.
    Returns the type name only if it is a simple uppercase identifier (filters
    out primitive types like ``int``, ``string`` and generics like ``List<T>``).

    Examples:
        ``"() ServiceClient"`` → ``"ServiceClient"``
        ``"(string name, int age) User"`` → ``"User"``
        ``"()"`` → ``None``
        ``"() string"`` → ``None`` (lowercase = primitive)
        ``"() List<string>"`` → ``None`` (generic)
    """
    if not signature:
        return None
    paren_idx = signature.rfind(")")
    if paren_idx < 0 or paren_idx == len(signature) - 1:
        return None
    ret_part = signature[paren_idx + 1:].strip()
    if ret_part and ret_part.isidentifier() and ret_part[0].isupper():
        return ret_part
    return None


def _track_csharp_return_type(
    resolved_sym: "Symbol",
    node: "tree_sitter.Node",
    source: bytes,
    var_types: dict[str, str],
    local_symbols: dict[str, "Symbol"],
) -> None:
    """Track variable type from a resolved method's return type annotation.

    When ``var client = factory.GetClient()`` resolves to ``Factory.GetClient``
    with signature ``() ServiceClient``, this sets ``var_types["client"] = "ServiceClient"``
    so that subsequent ``client.Fetch()`` resolves via type inference.

    The invocation_expression AST in C# has the parent chain:
    ``equals_value_clause > variable_declarator > identifier``.
    """
    if resolved_sym.kind != "method":
        return  # pragma: no cover - callers resolve to methods
    ret_name = _extract_csharp_return_type_name(resolved_sym.signature)
    if not ret_name or ret_name not in local_symbols:
        return
    parent = node.parent
    if not parent:
        return  # pragma: no cover - AST nodes always have parents
    # Direct child of variable_declarator
    if parent.type == "variable_declarator":
        name_node = find_child_by_type(parent, "identifier")
        if name_node:
            var_types[node_text(name_node, source)] = ret_name
    # Inside equals_value_clause -> variable_declarator
    elif parent.type == "equals_value_clause":  # pragma: no cover - alt AST pattern
        grandparent = parent.parent
        if grandparent and grandparent.type == "variable_declarator":
            name_node = find_child_by_type(grandparent, "identifier")
            if name_node:
                var_types[node_text(name_node, source)] = ret_name


def _extract_method_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract the method name from a method declaration.

    In C#, method_declaration has: [modifiers] return_type method_name(params)
    The return_type might be an identifier (e.g., 'Product') or predefined_type (e.g., 'int').
    The method_name is always an identifier after the return type.
    """
    identifiers = _find_children_by_type(node, "identifier")
    # If return type is a predefined_type (int, void, etc.), first identifier is method name
    # If return type is an identifier (custom type), second identifier is method name
    has_predefined_type = find_child_by_type(node, "predefined_type") is not None
    has_generic_name = find_child_by_type(node, "generic_name") is not None

    if has_predefined_type or has_generic_name:
        # Return type is predefined (int, void, etc.) or generic (Task<T>)
        # First identifier is method name
        if identifiers:
            return node_text(identifiers[0], source)
    else:
        # Return type is a custom type (an identifier)
        # Second identifier is method name
        if len(identifiers) >= 2:
            return node_text(identifiers[1], source)
        elif identifiers:  # pragma: no cover - defensive fallback
            # Fallback: only one identifier means no custom return type detected
            return node_text(identifiers[0], source)
    return None  # pragma: no cover - defensive


# Use the base FileAnalysis; using_aliases maps to import_aliases
FileAnalysis = _BaseFileAnalysis


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
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                return node_text(name_node, source)
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
            alias_node = find_child_by_type(node, "identifier")
            path_node = find_child_by_type(node, "qualified_name")
            if alias_node and path_node:
                alias = node_text(alias_node, source)
                full_path = node_text(path_node, source)
                if alias and full_path:
                    aliases[alias] = full_path
            continue

        # Handle regular 'using Namespace.Type;'
        name_node = find_child_by_type(node, "qualified_name")
        if name_node:
            full_path = node_text(name_node, source)
            if full_path and "." in full_path:
                # Last segment is the imported name
                name = full_path.rsplit(".", 1)[-1]
                if name:
                    aliases[name] = full_path
            continue

        # Handle simple 'using Namespace;'
        id_node = find_child_by_type(node, "identifier")
        if id_node:
            name = node_text(id_node, source)
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
                    base_name = node_text(base_child, source)
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

    analysis = FileAnalysis(import_aliases=using_aliases)

    def extract_name_from_declaration(node: "tree_sitter.Node") -> Optional[str]:
        """Extract the identifier name from a declaration node."""
        name_node = find_child_by_type(node, "identifier")
        if name_node:
            return node_text(name_node, source)
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
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, name, "class"),
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
                    modifiers=_extract_modifiers(node),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[name] = symbol

        # Interface declaration
        elif node.type == "interface_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, name, "interface"),
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
                    modifiers=_extract_modifiers(node),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[name] = symbol

        # Struct declaration
        elif node.type == "struct_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, name, "struct"),
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
                    modifiers=_extract_modifiers(node),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[name] = symbol

        # Enum declaration
        elif node.type == "enum_declaration":
            name = extract_name_from_declaration(node)
            if name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, name, "enum"),
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
                    modifiers=_extract_modifiers(node),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
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
                modifiers = _extract_modifiers(node)

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_csharp_signature(signature)
                stable_id = make_typed_stable_id(
                    "method", norm_sig, visibility_from_modifiers(modifiers),
                ) if norm_sig else None

                symbol = Symbol(
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, full_name, "method"),
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
                    stable_id=stable_id,
                    signature=signature,
                    modifiers=modifiers,
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
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
                modifiers = _extract_modifiers(node)

                # Typed stable_id (ADR-0014 §3)
                norm_sig = normalize_csharp_signature(signature)
                stable_id = make_typed_stable_id(
                    "constructor", norm_sig, visibility_from_modifiers(modifiers),
                ) if norm_sig else None

                symbol = Symbol(
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, full_name, "constructor"),
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
                    stable_id=stable_id,
                    signature=signature,
                    modifiers=modifiers,
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
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
                    id=make_symbol_id("csharp", str(file_path), start_line, end_line, full_name, "property"),
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
                    modifiers=_extract_modifiers(node),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
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
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                ctor_name = node_text(name_node, source)
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
    method_resolver: ListNameResolver | None = None,
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
    file_id = make_file_id("csharp", str(file_path))
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
                    return node_text(identifiers[-1], source)
            elif child.type == "identifier":
                # Direct function call
                return node_text(child, source)
        return None  # pragma: no cover - defensive

    for node in iter_tree(tree.root_node):
        # Using directive
        if node.type == "using_directive":
            # Get the namespace being imported
            name_node = find_child_by_type(node, "identifier")
            if not name_node:
                name_node = find_child_by_type(node, "qualified_name")
            if name_node:
                import_path = node_text(name_node, source)
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

        # Field declarations - track field types: private readonly IService _svc;
        elif node.type == "field_declaration":
            var_decl = find_child_by_type(node, "variable_declaration")
            if var_decl:
                # Type can be identifier (simple) or generic_name (generic)
                type_name = None
                type_id = find_child_by_type(var_decl, "identifier")
                if type_id:
                    type_name = node_text(type_id, source)
                else:
                    gen_name = find_child_by_type(var_decl, "generic_name")
                    if gen_name:
                        gen_id = find_child_by_type(gen_name, "identifier")
                        if gen_id:
                            type_name = node_text(gen_id, source)
                var_declarator = find_child_by_type(
                    var_decl, "variable_declarator"
                )
                if type_name and var_declarator:
                    name_node = find_child_by_type(
                        var_declarator, "identifier"
                    )
                    if name_node:
                        field_name = node_text(name_node, source)
                        var_types[field_name] = type_name

        # Invocation expression (method call)
        elif node.type == "invocation_expression":
            current_function = _get_enclosing_method(node, source, local_symbols)
            if current_function is not None:
                # Check for member_access_expression (receiver.method() pattern)
                member_access = find_child_by_type(node, "member_access_expression")
                if member_access:
                    # Extract receiver and method name
                    identifiers = _find_children_by_type(member_access, "identifier")
                    receiver_name = None
                    method_name = None
                    if len(identifiers) >= 2:
                        receiver_name = node_text(identifiers[0], source)
                        method_name = node_text(identifiers[-1], source)
                    elif len(identifiers) == 1:
                        # Nested: this._field.Method() — inner is
                        # member_access_expression(this, _field)
                        method_name = node_text(identifiers[0], source)
                        inner = find_child_by_type(
                            member_access, "member_access_expression"
                        )
                        if inner:
                            inner_ids = _find_children_by_type(
                                inner, "identifier"
                            )
                            this_node = find_child_by_type(inner, "this")
                            if this_node and inner_ids:
                                receiver_name = node_text(
                                    inner_ids[-1], source
                                )
                    if receiver_name and method_name:

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
                                _track_csharp_return_type(
                                    callee, node, source, var_types, local_symbols
                                )
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
                                    _track_csharp_return_type(
                                        lookup_result.symbol, node, source,
                                        var_types, local_symbols,
                                    )
                                    continue

                # Fallback to original simple name resolution
                callee_name = get_callee_name(node)
                if callee_name:
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
                            evidence_type="method_call",
                            confidence=0.85,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))
                        _track_csharp_return_type(
                            callee, node, source, var_types, local_symbols
                        )
                    # Check global symbols via resolver
                    else:
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
                            _track_csharp_return_type(
                                lookup_result.symbol, node, source,
                                var_types, local_symbols,
                            )

        # Object creation expression (new ClassName())
        elif node.type == "object_creation_expression":
            current_function = _get_enclosing_method(node, source, local_symbols)
            type_node = find_child_by_type(node, "identifier")
            type_name = node_text(type_node, source) if type_node else None

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
                    var_name_node = find_child_by_type(parent, "identifier")
                    if var_name_node:
                        var_name = node_text(var_name_node, source)
                        var_types[var_name] = type_name
                # Check for equals_value_clause in a variable_declaration
                # (alternative AST pattern - defensive code)
                elif parent.type == "equals_value_clause":  # pragma: no cover - alt AST pattern
                    grandparent = parent.parent
                    if grandparent and grandparent.type == "variable_declarator":
                        var_name_node = find_child_by_type(grandparent, "identifier")
                        if var_name_node:
                            var_name = node_text(var_name_node, source)
                            var_types[var_name] = type_name

        # Method group references: bare identifier in argument or assignment
        # that resolves to a function/method symbol.
        # Pattern 1: argument containing only an identifier — items.ForEach(Process)
        elif node.type == "argument":
            id_node = find_child_by_type(node, "identifier")
            if id_node and len(node.named_children) == 1:
                ref_name = node_text(id_node, source)
                target = local_symbols.get(ref_name)
                if target is None and resolver is not None:
                    lookup = resolver.lookup(ref_name)
                    if lookup.found and lookup.symbol is not None:
                        target = lookup.symbol
                if target is not None and target.kind in ("function", "method"):
                    current_function = _get_enclosing_method(
                        node, source, local_symbols,
                    )
                    if current_function is not None and target.id != current_function.id:
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=target.id,
                            edge_type="references",
                            line=node.start_point[0] + 1,
                            evidence_type="method_group",
                            confidence=0.80,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

        # Pattern 2: variable_declarator with RHS identifier — Action handler = Handle
        elif node.type == "variable_declarator":
            children = node.children
            eq_idx = next(
                (i for i, c in enumerate(children) if c.type == "="), -1,
            )
            if eq_idx >= 0 and eq_idx + 1 < len(children):
                rhs = children[eq_idx + 1]
                if rhs.type == "identifier":
                    ref_name = node_text(rhs, source)
                    target = local_symbols.get(ref_name)
                    if target is None and resolver is not None:
                        lookup = resolver.lookup(ref_name)
                        if lookup.found and lookup.symbol is not None:
                            target = lookup.symbol
                    if target is not None and target.kind in ("function", "method"):
                        current_function = _get_enclosing_method(
                            node, source, local_symbols,
                        )
                        if (
                            current_function is not None
                            and target.id != current_function.id
                        ):
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=target.id,
                                edge_type="references",
                                line=node.start_point[0] + 1,
                                evidence_type="method_group",
                                confidence=0.80,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))

    return edges


def _extract_attribute_edges(
    symbols: list[Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract decorated_by edges from C# attribute metadata.

    For each symbol (class, interface, method) with annotations metadata,
    creates decorated_by edges to attribute types that exist in the
    analyzed codebase. This enables visibility of attribute patterns
    like ASP.NET Core's [ApiController], [HttpGet], [Service], etc.

    Args:
        symbols: All extracted symbols
        global_symbols: Map of name -> Symbol for attribute lookup
        run: Current analysis run for provenance

    Returns:
        List of decorated_by edges for attribute relationships
    """
    edges: list[Edge] = []

    for sym in symbols:
        if sym.meta is None:
            continue

        # C# uses "annotations" instead of "decorators" for historical reasons
        annotations = sym.meta.get("annotations")
        if not annotations or not isinstance(annotations, list):
            continue

        for annotation in annotations:
            if not isinstance(annotation, dict):  # pragma: no cover
                continue

            attr_name = annotation.get("name")
            if not attr_name or not isinstance(attr_name, str):  # pragma: no cover
                continue

            # Try to resolve the attribute to a symbol
            # C# attributes may be referenced with or without "Attribute" suffix
            attr_sym = global_symbols.get(attr_name)
            if not attr_sym:
                attr_sym = global_symbols.get(f"{attr_name}Attribute")

            line = sym.span.start_line if sym.span else 0

            if attr_sym:
                edge = Edge.create(
                    src=sym.id,
                    dst=attr_sym.id,
                    edge_type="decorated_by",
                    line=line,
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_attribute",
                )
                edges.append(edge)
            else:
                # Emit unresolved edge for attributes we can't resolve
                # This helps track framework attributes like [ApiController]
                dst_id = f"csharp:unresolved:0-0:{attr_name}:unresolved"
                edge = Edge.create(
                    src=sym.id,
                    dst=dst_id,
                    edge_type="decorated_by",
                    line=line,
                    confidence=0.50,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_attribute_unresolved",
                )
                edges.append(edge)

    return edges


class CSharpAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based C# analyzer.

    Uses tree-sitter-c-sharp to parse C# files and extract classes, interfaces,
    structs, enums, methods, constructors, properties, call edges, import edges,
    instantiation edges, and attribute (decorated_by) edges.

    Overrides ``analyze`` for two reasons:
    1. ``_extract_symbols_from_file`` does its own file reading/parsing
    2. ``_extract_attribute_edges`` post-processing requires all symbols+global_symbols
    """

    lang = "csharp"
    file_patterns: ClassVar[list[str]] = ["*.cs"]
    grammar_module = "tree_sitter_c_sharp"

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict,
    ) -> None:
        """Register symbol with both short and full names for cross-file resolution."""
        short_name = symbol.name.split(".")[-1] if "." in symbol.name else symbol.name
        global_symbols[short_name] = symbol
        global_symbols[symbol.name] = symbol

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run C# analysis with attribute edge post-processing.

        The C# analyzer uses ``_extract_symbols_from_file`` which reads files
        internally, and adds an ``_extract_attribute_edges`` post-processing step.
        """
        import time as _time
        import warnings as _warnings

        start_time = _time.time()
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        if not self._check_grammar_available():
            _warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            run.duration_ms = int((_time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        parser = self._create_parser()

        # Pass 1: Extract all symbols
        file_analyses: dict[Path, FileAnalysis] = {}
        files_skipped = 0

        for cs_file in find_csharp_files(repo_root):
            if max_files is not None and len(file_analyses) >= max_files:
                break  # pragma: no cover

            analysis = _extract_symbols_from_file(cs_file, parser, run)
            if analysis.symbols:
                file_analyses[cs_file] = analysis
            else:
                files_skipped += 1

        # Build global symbol registry
        global_symbols: dict[str, Symbol] = {}
        # AMB-METHOD: multi-value method registry for ambiguity guard
        global_methods: dict[str, list[Symbol]] = {}
        for analysis in file_analyses.values():
            for symbol in analysis.symbols:
                self.register_symbol(symbol, global_symbols)
                if symbol.kind == "method":
                    short = symbol.name.split(".")[-1] if "." in symbol.name else symbol.name
                    global_methods.setdefault(short, []).append(symbol)

        # Pass 2: Extract edges
        resolver = NameResolver(global_symbols)
        method_resolver = ListNameResolver(global_methods, ambiguity_threshold=3)
        all_symbols: list[Symbol] = []
        all_edges: list[Edge] = []

        for cs_file, analysis in file_analyses.items():
            all_symbols.extend(analysis.symbols)

            edges = _extract_edges_from_file(
                cs_file, parser, analysis.symbol_by_name, global_symbols, run, resolver,
                using_aliases=analysis.import_aliases,
                method_resolver=method_resolver,
            )
            all_edges.extend(edges)

        # Extract attribute edges (INV-012: annotations metadata -> decorated_by edges)
        attribute_edges = _extract_attribute_edges(all_symbols, global_symbols, run)
        all_edges.extend(attribute_edges)

        run.files_analyzed = len(file_analyses)
        run.files_skipped = files_skipped
        run.duration_ms = int((_time.time() - start_time) * 1000)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            run=run,
        )


_analyzer = CSharpAnalyzer()


def is_csharp_tree_sitter_available() -> bool:
    """Check if tree-sitter with C# grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("csharp")
def analyze_csharp(repo_root: Path) -> AnalysisResult:
    """Analyze all C# files in a repository.

    Returns a AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-c-sharp is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
