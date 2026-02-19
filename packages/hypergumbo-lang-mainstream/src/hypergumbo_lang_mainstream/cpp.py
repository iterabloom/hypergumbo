"""C++ analysis pass using tree-sitter-cpp.

This analyzer uses tree-sitter to parse C++ files and extract:
- Class declarations
- Struct declarations
- Enum declarations
- Function definitions (standalone and class methods)
- Namespace declarations
- Function call relationships
- Include directives
- Object instantiation (new expressions)

If tree-sitter with C++ support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-cpp is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls, instantiations, and resolve against global symbol registry
4. Detect include directives and new expressions

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-cpp package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other language analyzers for consistency
- Uses iterative traversal to avoid RecursionError on deeply nested code
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type as _find_child_by_type,
    iter_tree,
    make_file_id as _base_make_file_id,
    make_symbol_id as _base_make_symbol_id,
    node_text as _node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "cpp-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# Backwards compatibility alias
CppAnalysisResult = AnalysisResult


def find_cpp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all C++ files in the repository.

    Headers (.h, .hpp, .hxx) are yielded before source files (.cpp, .cc, .cxx)
    so that definitions can replace declarations when building the symbol registry.
    """
    yield from find_files(repo_root, ["*.h", "*.hpp", "*.hxx", "*.cpp", "*.cc", "*.cxx"])


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return _base_make_symbol_id("cpp", path, start_line, end_line, name, kind)


def _make_file_id(path: str) -> str:
    """Generate ID for a C++ file node (used as include edge source)."""
    return _base_make_file_id("cpp", path)


def _extract_base_classes_cpp(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract base classes from C++ class/struct declaration.

    C++ uses single and multiple inheritance with optional access specifiers:
        class Dog : public Animal { }
        class Cat : Animal, public Printable { }
        struct Vector : public BaseType { }

    The AST has a `base_class_clause` containing `type_identifier` or
    `qualified_identifier` nodes for each base class.
    """
    base_classes: list[str] = []

    base_clause = _find_child_by_type(node, "base_class_clause")
    if base_clause is None:
        return base_classes

    for child in base_clause.children:
        if child.type == "type_identifier":
            # Simple base class name
            base_classes.append(_node_text(child, source))
        elif child.type == "qualified_identifier":
            # Qualified name like std::runtime_error
            base_classes.append(_node_text(child, source))

    return base_classes


def _extract_cpp_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a C++ function definition or declaration.

    Returns signature like "(int x, std::string& name) int" or "(void)".
    """
    if node.type not in ("function_definition", "declaration"):
        return None  # pragma: no cover

    # Find function_declarator (may be wrapped in pointer_declarator or reference_declarator)
    declarator = _find_child_by_type(node, "function_declarator")
    if not declarator:
        ptr_decl = _find_child_by_type(node, "pointer_declarator")
        if ptr_decl:
            declarator = _find_child_by_type(ptr_decl, "function_declarator")
    if not declarator:
        ref_decl = _find_child_by_type(node, "reference_declarator")
        if ref_decl:
            declarator = _find_child_by_type(ref_decl, "function_declarator")
    if not declarator:
        return None  # pragma: no cover

    # Find parameter_list
    param_list = _find_child_by_type(declarator, "parameter_list")
    if not param_list:
        return None  # pragma: no cover

    # Extract parameters
    param_strs: list[str] = []
    for child in param_list.children:
        if child.type == "parameter_declaration":
            param_text = _node_text(child, source).strip()
            param_strs.append(param_text)

    # Build signature with parameters
    sig = "(" + ", ".join(param_strs) + ")"

    # Extract return type (collect nodes before function_declarator)
    return_type_parts: list[str] = []
    for child in node.children:
        if child.type == "function_declarator":
            break
        if child.type in (
            "primitive_type", "type_identifier", "qualified_identifier",
            "sized_type_specifier", "template_type", "auto",
            "storage_class_specifier", "type_qualifier",
        ):
            return_type_parts.append(_node_text(child, source))

    if return_type_parts:
        return_type = " ".join(return_type_parts)
        if return_type and return_type != "void":
            sig += f" {return_type}"

    return sig


def _extract_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[tuple[str, str]]:
    """Extract function name and kind from function_definition or field_declaration.

    Returns (name, kind) tuple where kind is 'function' or 'method'.
    Handles pointer return types where function_declarator is wrapped
    inside pointer_declarator (e.g., ``T* create()``).
    """
    declarator = _find_child_by_type(node, "function_declarator")
    if not declarator:
        # Pointer return types: function_definition -> pointer_declarator -> function_declarator
        ptr_decl = _find_child_by_type(node, "pointer_declarator")
        if ptr_decl:
            declarator = _find_child_by_type(ptr_decl, "function_declarator")
    if not declarator:
        # Reference return types: function_definition -> reference_declarator -> function_declarator
        ref_decl = _find_child_by_type(node, "reference_declarator")
        if ref_decl:
            declarator = _find_child_by_type(ref_decl, "function_declarator")
    if not declarator:
        return None  # pragma: no cover - defensive

    # Check for qualified name (Class::method)
    qualified = _find_child_by_type(declarator, "qualified_identifier")
    if qualified:
        # It's a class method implementation
        # Format: namespace::class::method or class::method
        full_name = _node_text(qualified, source)
        return (full_name, "method")

    # Check for simple identifier (standalone function)
    ident = _find_child_by_type(declarator, "identifier")
    if ident:
        name = _node_text(ident, source)
        return (name, "function")

    # Check for field_identifier (method declaration in class)
    field_ident = _find_child_by_type(declarator, "field_identifier")
    if field_ident:
        name = _node_text(field_ident, source)
        return (name, "method")

    return None  # pragma: no cover - defensive


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    rel_path: str,
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single C++ file."""
    analysis = FileAnalysis()

    # Extract namespace aliases for ADR-0007
    analysis.import_aliases = _extract_namespace_aliases(tree.root_node, source)

    # Use iterative traversal to avoid RecursionError on deeply nested code
    for node in iter_tree(tree.root_node):
        # Class declaration
        if node.type == "class_specifier":
            name_node = _find_child_by_type(node, "type_identifier")
            if name_node:
                name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract base classes for inheritance linker
                base_classes = _extract_base_classes_cpp(node, source)
                meta = {"base_classes": base_classes} if base_classes else None

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "class"),
                    name=name,
                    kind="class",
                    language="cpp",
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

        # Struct declaration
        elif node.type == "struct_specifier":
            name_node = _find_child_by_type(node, "type_identifier")
            if name_node:
                name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract base classes for inheritance linker
                base_classes = _extract_base_classes_cpp(node, source)
                meta = {"base_classes": base_classes} if base_classes else None

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "struct"),
                    name=name,
                    kind="struct",
                    language="cpp",
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

        # Enum declaration
        elif node.type == "enum_specifier":
            name_node = _find_child_by_type(node, "type_identifier")
            if name_node:
                name = _node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "enum"),
                    name=name,
                    kind="enum",
                    language="cpp",
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

        # Function definition
        elif node.type == "function_definition":
            result = _extract_function_name(node, source)
            if result:
                name, kind = result
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                signature = _extract_cpp_signature(node, source)

                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, kind),
                    name=name,
                    kind=kind,
                    language="cpp",
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
                # Store by both full name and short name
                analysis.symbol_by_name[name] = symbol
                short_name = name.split("::")[-1] if "::" in name else name
                if short_name != name:
                    analysis.symbol_by_name[short_name] = symbol

    return analysis


def _extract_namespace_aliases(
    root_node: "tree_sitter.Node",
    source: bytes,
) -> dict[str, str]:
    """Extract namespace aliases from C++ source (ADR-0007).

    Namespace alias syntax:
        namespace fs = std::filesystem;

    Returns dict mapping alias -> qualified_namespace (e.g., "fs" -> "std::filesystem").
    """
    aliases: dict[str, str] = {}
    for node in iter_tree(root_node):
        if node.type == "namespace_alias_definition":
            alias_name = None
            target_namespace = None
            for child in node.children:
                if child.type == "namespace_identifier" and alias_name is None:
                    alias_name = _node_text(child, source)
                elif child.type == "nested_namespace_specifier":
                    target_namespace = _node_text(child, source)
            if alias_name and target_namespace:
                aliases[alias_name] = target_namespace
    return aliases


def _find_class_or_struct(
    type_name: str,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    resolver: NameResolver,
) -> Symbol | None:
    """Find a class or struct symbol by name.

    In C++, constructors share the class name (``Config::Config()``) and may
    overwrite the class in the ``symbol_by_name`` dict.  This helper checks
    local symbols first, then global, and finally the resolver -- returning
    the first match whose ``kind`` is ``class`` or ``struct``.
    """
    for pool in (local_symbols, global_symbols):
        candidate = pool.get(type_name)
        if candidate and candidate.kind in ("class", "struct"):
            return candidate
    # Resolver fallback for cross-file types not in the simple dicts.
    # In practice, global_symbols already includes all class/struct names,
    # so the resolver is only needed for edge cases (e.g., suffix matching).
    lookup_result = resolver.lookup(type_name)  # pragma: no cover
    if (  # pragma: no cover
        lookup_result.found
        and lookup_result.symbol is not None
        and lookup_result.symbol.kind in ("class", "struct")
    ):
        return lookup_result.symbol  # pragma: no cover
    return None  # pragma: no cover


def _try_instantiation_edge(
    type_name: str,
    current_function: Symbol,
    node: "tree_sitter.Node",
    evidence_type: str,
    base_confidence: float,
    local_symbols: dict[str, Symbol],
    resolver: NameResolver,
    edges: list[Edge],
    run: AnalysisRun,
) -> None:
    """Emit an instantiates edge if type_name resolves to a known symbol.

    Checks local symbols first (higher confidence), then falls back to
    the NameResolver for cross-file resolution.
    """
    if type_name in local_symbols:
        target = local_symbols[type_name]
        edges.append(Edge.create(
            src=current_function.id,
            dst=target.id,
            edge_type="instantiates",
            line=node.start_point[0] + 1,
            evidence_type=evidence_type,
            confidence=base_confidence,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
        ))
    else:
        lookup_result = resolver.lookup(type_name)
        if lookup_result.found and lookup_result.symbol is not None:
            edges.append(Edge.create(
                src=current_function.id,
                dst=lookup_result.symbol.id,
                edge_type="instantiates",
                line=node.start_point[0] + 1,
                evidence_type=evidence_type,
                confidence=(base_confidence - 0.05) * lookup_result.confidence,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
            ))


def _extract_edges_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    rel_path: str,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
    resolver: NameResolver,
    namespace_aliases: dict[str, str] | None = None,
) -> list[Edge]:
    """Extract include, call, and instantiation edges from a parsed tree.

    Uses iterative traversal to avoid RecursionError on deeply nested code.

    Args:
        namespace_aliases: Mapping of alias -> qualified_namespace for path_hint (ADR-0007)
    """
    if namespace_aliases is None:
        namespace_aliases = {}  # pragma: no cover - always passed by caller
    edges: list[Edge] = []
    file_id = _make_file_id(str(file_path))

    def get_callee_name(node: "tree_sitter.Node") -> Optional[str]:
        """Extract the function name being called from a call_expression.

        Handles plain calls, qualified calls, field (method) calls,
        and template instantiation variants of each:
          - process<int>(42)       -> template_function -> identifier
          - obj.get<int>()         -> field_expression -> template_method -> field_identifier
          - NS::make<T>()          -> qualified_identifier (contains template_function)
        """
        # Check for field_expression (obj.method() or obj.method<T>())
        field_expr = _find_child_by_type(node, "field_expression")
        if field_expr:
            # Plain method: field_identifier is a direct child
            field_ident = _find_child_by_type(field_expr, "field_identifier")
            if field_ident:
                return _node_text(field_ident, source)
            # Template method: field_expression -> template_method -> field_identifier
            tmpl_method = _find_child_by_type(field_expr, "template_method")
            if tmpl_method:
                field_ident = _find_child_by_type(tmpl_method, "field_identifier")
                if field_ident:
                    return _node_text(field_ident, source)

        # Check for qualified_identifier (Class::method() or NS::func<T>())
        qualified = _find_child_by_type(node, "qualified_identifier")
        if qualified:
            # If the qualified name contains a template_function,
            # reconstruct without template args: NS::func<T> -> NS::func
            tmpl_in_qual = _find_child_by_type(qualified, "template_function")
            if tmpl_in_qual:
                ident = _find_child_by_type(tmpl_in_qual, "identifier")
                if ident:
                    ns_node = _find_child_by_type(qualified, "namespace_identifier")
                    if ns_node:
                        return _node_text(ns_node, source) + "::" + _node_text(ident, source)
                    return _node_text(ident, source)  # pragma: no cover - defensive
            return _node_text(qualified, source)

        # Check for template_function (process<int>(42))
        tmpl_func = _find_child_by_type(node, "template_function")
        if tmpl_func:
            ident = _find_child_by_type(tmpl_func, "identifier")
            if ident:
                return _node_text(ident, source)

        # Check for simple identifier (function())
        ident = _find_child_by_type(node, "identifier")
        if ident:
            return _node_text(ident, source)

        return None  # pragma: no cover - defensive

    # Stack entries: (node, current_function_context)
    stack: list[tuple["tree_sitter.Node", Optional[Symbol]]] = [
        (tree.root_node, None)
    ]

    while stack:
        node, current_function = stack.pop()

        new_function = current_function

        # Track current function for call edges
        if node.type == "function_definition":
            result = _extract_function_name(node, source)
            if result:
                name, _ = result
                short_name = name.split("::")[-1] if "::" in name else name
                if short_name in local_symbols:
                    new_function = local_symbols[short_name]

        # Include directive
        elif node.type == "preproc_include":
            # Get the included file
            path_node = _find_child_by_type(node, "string_literal")
            if path_node:
                # Local include: #include "header.h"
                content = _find_child_by_type(path_node, "string_content")
                if content:
                    include_path = _node_text(content, source)
                    edges.append(Edge.create(
                        src=file_id,
                        dst=f"cpp:{include_path}:0-0:header:header",
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        evidence_type="include_directive",
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))
            else:
                # System include: #include <header>
                sys_lib = _find_child_by_type(node, "system_lib_string")
                if sys_lib:
                    include_path = _node_text(sys_lib, source)
                    edges.append(Edge.create(
                        src=file_id,
                        dst=f"cpp:{include_path}:0-0:header:header",
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        evidence_type="include_directive",
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))

        # Function call
        elif node.type == "call_expression":
            if current_function is not None:
                callee_name = get_callee_name(node)
                if callee_name:
                    # Try to resolve: look for short name first
                    short_name = callee_name.split("::")[-1] if "::" in callee_name else callee_name

                    # Extract namespace prefix for path_hint (ADR-0007)
                    path_hint = None
                    if "::" in callee_name:
                        ns_prefix = callee_name.split("::")[0]
                        # Check if namespace prefix is an alias
                        if ns_prefix in namespace_aliases:
                            # Resolve alias: fs::func -> std::filesystem as path_hint
                            path_hint = namespace_aliases[ns_prefix]
                        else:
                            # Use explicit namespace as path_hint
                            path_hint = ns_prefix

                    # Check local symbols first
                    if short_name in local_symbols:
                        callee = local_symbols[short_name]
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
                    # Check global symbols via resolver
                    else:
                        lookup_result = resolver.lookup(short_name, path_hint=path_hint)
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

        # new expression
        elif node.type == "new_expression":
            if current_function is not None:
                type_name = None
                type_node = _find_child_by_type(node, "type_identifier")
                if type_node:
                    type_name = _node_text(type_node, source)
                else:
                    # Check for qualified_identifier (new Namespace::Class())
                    qualified = _find_child_by_type(node, "qualified_identifier")
                    if qualified:
                        # Get the type_identifier from within the qualified name
                        inner_type = _find_child_by_type(qualified, "type_identifier")
                        if inner_type:
                            type_name = _node_text(inner_type, source)
                if type_name:
                    _try_instantiation_edge(
                        type_name, current_function, node, "new_expression",
                        0.90, local_symbols, resolver, edges, run,
                    )

        # Stack object construction: Widget w; / Widget w(args); / Widget w{};
        elif node.type == "declaration":
            if current_function is not None:
                type_name = None
                type_node = _find_child_by_type(node, "type_identifier")
                if type_node:
                    type_name = _node_text(type_node, source)
                else:
                    # Namespace-qualified type: ui::Button btn;
                    qual_node = _find_child_by_type(node, "qualified_identifier")
                    if qual_node:
                        inner_type = _find_child_by_type(qual_node, "type_identifier")
                        if inner_type:
                            type_name = _node_text(inner_type, source)
                if type_name:
                    # Only emit for known class/struct types, not primitives.
                    # symbol_by_name may map to the constructor instead of the
                    # class, so check kind of both local and global candidates.
                    target = _find_class_or_struct(
                        type_name, local_symbols, global_symbols, resolver,
                    )
                    if target is not None:
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=target.id,
                            edge_type="instantiates",
                            line=node.start_point[0] + 1,
                            evidence_type="stack_construction",
                            confidence=0.85,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

        # Compound literal expression: Widget{42} as expression
        elif node.type == "compound_literal_expression":
            if current_function is not None:
                type_node = _find_child_by_type(node, "type_identifier")
                if type_node:
                    type_name = _node_text(type_node, source)
                    target = _find_class_or_struct(
                        type_name, local_symbols, global_symbols, resolver,
                    )
                    if target is not None:
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=target.id,
                            edge_type="instantiates",
                            line=node.start_point[0] + 1,
                            evidence_type="stack_construction",
                            confidence=0.85,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

        # Add children to stack with updated context
        for child in reversed(node.children):
            stack.append((child, new_function))

    return edges


class CppAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based C++ analyzer.

    Uses tree-sitter-cpp to parse C++ files and extract classes, structs,
    enums, functions, methods, include directives, call edges, and
    instantiation edges. Overrides ``register_symbol`` to prefer
    definitions (.cpp/.cc/.cxx) over declarations (.h/.hpp/.hxx).
    """

    lang = "cpp"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["*.cpp", "*.cc", "*.cxx", "*.h", "*.hpp", "*.hxx"]
    grammar_module = "tree_sitter_cpp"

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Yield C++ files (headers before sources for declaration ordering)."""
        yield from find_cpp_files(repo_root)

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        return _extract_symbols_from_tree(tree, source, file_path, rel_path, run)

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict,
    ) -> None:
        """Register symbol, preferring source files over headers for definitions."""
        short_name = symbol.name.split("::")[-1] if "::" in symbol.name else symbol.name
        sym_is_source = any(symbol.path.endswith(ext) for ext in ('.cpp', '.cc', '.cxx'))
        for name in (short_name, symbol.name):
            existing = global_symbols.get(name)
            if existing is None:
                global_symbols[name] = symbol
            else:
                existing_is_source = any(existing.path.endswith(ext) for ext in ('.cpp', '.cc', '.cxx'))
                if sym_is_source and not existing_is_source:
                    global_symbols[name] = symbol  # pragma: no cover

    def extract_edges_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        local_symbols: dict[str, Symbol],
        global_symbols: dict,
        run: AnalysisRun,
        import_aliases: dict[str, str],
        resolver: NameResolver,
    ) -> list[Edge]:
        return _extract_edges_from_tree(
            tree, source, file_path, rel_path,
            local_symbols, global_symbols, run, resolver,
            namespace_aliases=import_aliases,
        )

    def get_import_aliases(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
    ) -> dict[str, str]:
        """Extract namespace aliases from C++ source (ADR-0007)."""
        return _extract_namespace_aliases(tree.root_node, source)


_analyzer = CppAnalyzer()


def is_cpp_tree_sitter_available() -> bool:
    """Check if tree-sitter with C++ grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("cpp")
def analyze_cpp(repo_root: Path) -> CppAnalysisResult:
    """Analyze all C++ files in a repository.

    Returns a CppAnalysisResult with symbols, edges, and provenance.
    If tree-sitter-cpp is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
