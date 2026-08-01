# SPDX-License-Identifier: AGPL-3.0-or-later
"""C++ analysis pass using tree-sitter-cpp.

This analyzer uses tree-sitter to parse C++ files and extract:
- Class declarations
- Struct declarations
- Enum declarations
- Function definitions (standalone and class methods)
- Namespace aliases (used as resolution path hints, ADR-0007)
- Function call relationships
- Include directives
- Object instantiation (new expressions, stack construction, and compound literals)
- Dispatch table edges (function pointers in static array initializers)
- Function-pointer references (address-of expressions like `&func` or `&Class::method`)
- Module attribute references for iostream IO (`std::cout`/`std::cerr`/`std::cin` and namespace-alias attribute reads; feeds io-boundaries)

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
5. Header dedup: ``.h`` files are only included when C++ source files
   (``.cpp``/``.cc``/``.cxx``) exist.  In pure C repos, ``.h`` files
   belong to the C analyzer.

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-cpp package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as other language analyzers for consistency
- Header dedup avoids phantom C++ symbols in pure C repos (e.g., git, Linux kernel)
- Uses iterative traversal to avoid RecursionError on deeply nested code
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional, TypeAlias

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import (
    AnalysisRun, Edge, ExternalRef, Span, Symbol, make_pass_id,
)
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    defer_bare_method_call,
    emit_module_attribute_refs,
    find_child_by_type as _find_child_by_type,
    iter_tree,
    make_file_id as _base_make_file_id,
    make_symbol_id as _base_make_symbol_id,
    make_unresolved_edge,
    node_text as _node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_lang_mainstream.symbol_introspection import (
    compute_cyclomatic_complexity,
)

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("cpp")

# Common C++ STL container/iterator method names.  When a call like
# ``v.clear()`` is parsed, tree-sitter yields a ``field_expression``
# wrapping the method name.  If field-chain resolution fails (the
# receiver type is unknown), we must NOT fall through to global symbol
# resolution — otherwise a project-level ``clear()`` function would
# erroneously become the callee.  This set enumerates names that are
# almost certainly STL member calls when invoked via ``.``.
_CPP_STL_METHODS: frozenset[str] = frozenset({
    # Container modifiers
    "clear", "push_back", "push_front", "pop_back", "pop_front",
    "emplace_back", "emplace_front", "emplace", "insert", "erase",
    "resize", "reserve", "shrink_to_fit", "swap", "assign",
    # Element access
    "at", "front", "back", "data", "top",
    # Capacity / observers
    "size", "empty", "capacity", "max_size", "length",
    # Iterators
    "begin", "end", "cbegin", "cend", "rbegin", "rend",
    # Lookup (associative containers)
    "find", "count", "contains", "lower_bound", "upper_bound",
    "equal_range",
    # String operations
    "substr", "append", "replace", "c_str", "str",
    # Stream
    "flush", "close", "open", "read", "write", "seekg", "seekp",
    "tellg", "tellp",
    # Smart pointers (excluding "get" — too commonly user-defined)
    "reset", "release", "use_count",
})

# Backwards compatibility alias
CppAnalysisResult: TypeAlias = AnalysisResult


def _has_cpp_source_files(repo_root: Path) -> bool:
    """Check if the repository contains C++ source files.

    Checks for unambiguous C++ source extensions (.cpp, .cc, .cxx).
    When none exist, .h files are presumed to be C headers and should
    be processed only by the C analyzer to avoid phantom C++ symbols.
    """
    return any(find_files(repo_root, ["*.cpp", "*.cc", "*.cxx"]))


def find_cpp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all C++ files in the repository.

    Headers (.hpp, .hxx) are always included (unambiguously C++).
    Plain .h headers are only included when C++ source files (.cpp, .cc, .cxx)
    exist — otherwise they belong to the C analyzer.

    Headers are yielded before source files so that definitions can replace
    declarations when building the symbol registry.
    """
    if _has_cpp_source_files(repo_root):
        # Full C++ repo: include .h headers
        yield from find_files(repo_root, ["*.h", "*.hpp", "*.hxx", "*.cpp", "*.cc", "*.cxx"])
    else:
        # No C++ source files: only unambiguously C++ files
        yield from find_files(repo_root, ["*.hpp", "*.hxx"])


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return _base_make_symbol_id("cpp", path, start_line, end_line, name, kind)


def _make_file_id(path: str) -> str:
    """Generate ID for a C++ file node (used as include edge source)."""
    return _base_make_file_id("cpp", path)


def _cpp_pure_virtual_name(
    field_decl: "tree_sitter.Node", source: bytes,
) -> "str | None":
    """The method name if *field_decl* is a PURE virtual, else None.

    A pure virtual is ``virtual T f() = 0;`` — the grammar gives it a
    ``virtual`` child, a ``function_declarator``, and an ``=`` followed by a
    ``number_literal`` of ``0``. Scoped to pure virtuals on purpose: a
    non-pure declaration has its definition in another translation unit and
    hypergumbo has no decl/def merging for cpp, so emitting those would yield
    two symbols for one method. A pure virtual has no definition anywhere by
    construction (audit-findings 0018).
    """
    if not any(c.type == "virtual" for c in field_decl.children):
        return None
    saw_equals = False
    for child in field_decl.children:
        if child.type == "=":
            saw_equals = True
        elif saw_equals and child.type == "number_literal":
            if _node_text(child, source).strip() != "0":
                return None  # pragma: no cover - `virtual T f() = 1;` is not
                # valid C++; tree-sitter parses permissively, so the guard is
                # kept for malformed or macro-expanded input.
            declarator = _find_child_by_type(field_decl, "function_declarator")
            if declarator is None:
                return None  # pragma: no cover - a `virtual` data member with
                # an initializer (`virtual int x = 0;`) is not valid C++, so no
                # well-formed input reaches this branch.
            ident = _find_child_by_type(declarator, "field_identifier") or \
                _find_child_by_type(declarator, "identifier")
            return _node_text(ident, source) if ident is not None else None
    return None


def _cpp_has_pure_virtual(type_node: "tree_sitter.Node", source: bytes) -> bool:
    """Whether a class/struct body declares any pure virtual — i.e. is abstract."""
    body = _find_child_by_type(type_node, "field_declaration_list")
    if body is None:
        return False
    return any(
        child.type == "field_declaration"
        and _cpp_pure_virtual_name(child, source) is not None
        for child in body.children
    )


def _emit_cpp_pure_virtual(
    field_decl: "tree_sitter.Node",
    method_name: str,
    owner_name: str,
    visibility: str,
    file_path: "Path",
    analysis: "AnalysisResult",
    run: AnalysisRun,
) -> None:
    """Emit a ``kind="method"`` Symbol for one pure virtual declaration.

    Named ``Owner::method`` to match every other cpp member, so the shared
    member-name splitter recovers the owner. Carries the ``abstract``
    modifier, which is where ``is_abstract_type`` reads abstractness.
    """
    start_line = field_decl.start_point[0] + 1
    end_line = field_decl.end_point[0] + 1
    full_name = f"{owner_name}::{method_name}"
    analysis.symbols.append(Symbol(
        id=_make_symbol_id(str(file_path), start_line, end_line, full_name, "method"),
        name=full_name,
        kind="method",
        language="cpp",
        path=str(file_path),
        span=Span(
            start_line=start_line,
            end_line=end_line,
            start_col=field_decl.start_point[1],
            end_col=field_decl.end_point[1],
        ),
        origin=PASS_ID,
        origin_run_id=run.execution_id,
        modifiers=[visibility, "virtual", "abstract"],
        line_span=end_line - start_line + 1,
    ))


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


def _extract_field_types_cpp(
    class_node: "tree_sitter.Node",
    source: bytes,
) -> dict[str, str]:
    """Extract field name → type name mappings from a class/struct body.

    Parses ``field_declaration`` nodes inside a ``field_declaration_list``.
    For pointer/reference types (``Repo* _repo``), extracts the base type
    (``Repo``).  For template types (``unique_ptr<Db>``), extracts the
    template argument type (``Db``) since that's what chained member access
    resolves through.  Skips primitive types (``int``, ``float``, etc.)
    since they don't have member methods.
    """
    fields: dict[str, str] = {}
    body = _find_child_by_type(class_node, "field_declaration_list")
    if body is None:
        return fields

    for field_decl in body.children:
        if field_decl.type != "field_declaration":
            continue

        # Extract type name — prefer type_identifier, fall back to
        # qualified_identifier.  Skip primitive types.
        type_name: str | None = None
        type_node = _find_child_by_type(field_decl, "type_identifier")
        if type_node:
            type_name = _node_text(type_node, source)
        else:
            qual_node = _find_child_by_type(field_decl, "qualified_identifier")
            if qual_node:
                # For qualified types like std::unique_ptr<Db>, extract the
                # template argument if present; otherwise use the last
                # type_identifier in the qualified name.
                tmpl = _find_child_by_type(qual_node, "template_type")
                if tmpl:
                    # Template: extract first type argument (e.g. Db from
                    # unique_ptr<Db>).
                    arg_list = _find_child_by_type(tmpl, "template_argument_list")
                    if arg_list:
                        desc = _find_child_by_type(arg_list, "type_descriptor")
                        if desc:
                            inner = _find_child_by_type(desc, "type_identifier")
                            if inner:
                                type_name = _node_text(inner, source)
                if type_name is None:
                    # No template — use last type_identifier
                    for c in reversed(qual_node.children):
                        if c.type == "type_identifier":
                            type_name = _node_text(c, source)
                            break

        if type_name is None:
            continue

        # Extract field name — either direct field_identifier or inside
        # pointer_declarator / reference_declarator.
        field_name: str | None = None
        for child in field_decl.children:
            if child.type == "field_identifier":
                field_name = _node_text(child, source)
                break
            if child.type in ("pointer_declarator", "reference_declarator"):
                fid = _find_child_by_type(child, "field_identifier")
                if fid:
                    field_name = _node_text(fid, source)
                    break

        if field_name:
            fields[field_name] = type_name

    return fields


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


# WI-jusus: node-type sets for field/variable (data-member) emission.
# A declarator chain wraps the declared name; a function_declarator inside one
# marks the declaration as a FUNCTION (member fn / prototype), never a data
# member. A nested type specifier marks a nested type declaration, not a field.
_CPP_DECLARATOR_TYPES = frozenset({
    "identifier", "field_identifier", "init_declarator", "pointer_declarator",
    "reference_declarator", "array_declarator", "parenthesized_declarator",
    "function_declarator",
})
_CPP_NESTED_TYPE_NODES = frozenset({
    "struct_specifier", "class_specifier", "enum_specifier", "union_specifier",
})


def _cpp_declarator_info(
    node: "tree_sitter.Node", source: bytes,
) -> tuple[str | None, bool]:
    """Resolve a C++ declarator chain to (innermost data name, is_function).

    Descends through init_/pointer_/reference_/array_/parenthesized_declarator
    to the leaf identifier. Returns ``(None, True)`` when the chain is a
    ``function_declarator`` (a member-function declaration or a prototype, not a
    data member), and ``(name, False)`` for a real data declarator.
    """
    t = node.type
    if t in ("identifier", "field_identifier"):
        return _node_text(node, source), False
    if t == "function_declarator":
        return None, True
    if t in ("init_declarator", "pointer_declarator", "reference_declarator",
             "array_declarator", "parenthesized_declarator"):
        for child in node.children:
            if child.type in _CPP_DECLARATOR_TYPES:
                return _cpp_declarator_info(child, source)
    return None, False  # pragma: no cover - defensive: a declarator group always wraps an inner declarator


def _cpp_data_declarators(
    decl_node: "tree_sitter.Node", source: bytes,
) -> tuple[list[str], bool, bool]:
    """Collect data-member names from a ``field_declaration`` / ``declaration``.

    Returns ``(names, is_function, has_nested_type)``: ``names`` is one entry per
    declarator (so ``int a, b;`` yields both, ``int arr[4]`` yields ``arr``,
    ``char* p`` yields ``p``); ``is_function`` is True if any declarator is a
    function declarator (member fn / prototype); ``has_nested_type`` is True if a
    nested struct/class/enum/union specifier is present (a nested type decl).
    """
    names: list[str] = []
    is_function = False
    has_nested_type = False
    for child in decl_node.children:
        if child.type in _CPP_NESTED_TYPE_NODES:
            has_nested_type = True
        elif child.type in _CPP_DECLARATOR_TYPES:
            name, is_func = _cpp_declarator_info(child, source)
            if is_func:
                is_function = True
            elif name is not None:
                names.append(name)
    return names, is_function, has_nested_type


def _cpp_leading_type(decl_node: "tree_sitter.Node", source: bytes) -> str:
    """Declared type text (qualifiers + type node) preceding the declarators.

    Storage-class specifiers (``static``, ``extern``) are modifiers, not type,
    and are excluded; iteration stops at the first declarator.
    """
    parts: list[str] = []
    for child in decl_node.children:
        if child.type == "storage_class_specifier":
            continue
        if child.type in _CPP_DECLARATOR_TYPES or child.type in _CPP_NESTED_TYPE_NODES:
            break
        text = _node_text(child, source).strip()
        if text and text != ";":
            parts.append(text)
    return " ".join(parts).strip()


def _cpp_is_static(decl_node: "tree_sitter.Node", source: bytes) -> bool:
    """True if the declaration carries a ``static`` storage-class specifier."""
    return any(
        child.type == "storage_class_specifier"
        and _node_text(child, source).strip() == "static"
        for child in decl_node.children
    )


def _emit_cpp_field_symbols(
    analysis: "FileAnalysis",
    type_node: "tree_sitter.Node",
    owner_name: str,
    default_visibility: str,
    file_path: Path,
    file_stable_id: str,
    source: bytes,
    run: AnalysisRun,
) -> None:
    """Emit kind="field" Symbols for a class/struct body's data members (WI-jusus).

    ``field_declaration`` is structurally distinct (it never appears in a
    function body) so NO scope walk is needed. Member-function declarations
    (``void move();``) and nested type declarations (``struct Inner {};``) also
    parse as ``field_declaration`` and are filtered out. Visibility follows the
    most recent ``access_specifier`` (C++ default: private for a class body,
    public for a struct body). Field symbols are appended to ``analysis.symbols``
    (they still reach output/search/centrality) but deliberately NOT to
    ``analysis.symbol_by_name`` — a data member is never a call target, and the
    ``register_symbol`` chokepoint likewise keeps it out of the global registry.
    """
    body = _find_child_by_type(type_node, "field_declaration_list")
    if body is None:
        return
    current_visibility = default_visibility
    for child in body.children:
        if child.type == "access_specifier":
            current_visibility = _node_text(child, source).strip()
            continue
        if child.type != "field_declaration":
            continue
        pure_virtual = _cpp_pure_virtual_name(child, source)
        if pure_virtual is not None:
            _emit_cpp_pure_virtual(
                child, pure_virtual, owner_name, current_visibility,
                file_path, analysis, run,
            )
            continue
        names, is_function, has_nested_type = _cpp_data_declarators(child, source)
        if is_function or has_nested_type or not names:
            continue
        field_type = _cpp_leading_type(child, source)
        modifiers = [current_visibility]
        if _cpp_is_static(child, source):
            modifiers.append("static")
        start_line = child.start_point[0] + 1
        end_line = child.end_point[0] + 1
        for field_name in names:
            full_name = f"{owner_name}::{field_name}"
            symbol = Symbol(
                id=_make_symbol_id(
                    str(file_path), start_line, end_line, full_name, "field",
                ),
                name=full_name,
                kind="field",
                language="cpp",
                path=str(file_path),
                span=Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=child.start_point[1],
                    end_col=child.end_point[1],
                ),
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                signature=field_type or None,
                modifiers=modifiers,
                is_exported=(current_visibility == "public"),
                line_span=end_line - start_line + 1,
                stable_id=_analyzer.compute_stable_id(
                    child, kind="field", name=full_name,
                    file_stable_id=file_stable_id,
                ),
            )
            analysis.symbols.append(symbol)
            analysis.node_for_symbol[symbol.id] = child


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    rel_path: str,
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single C++ file."""
    analysis = FileAnalysis()

    # WI-bokab (v7): file-identity anchor for this file's symbols. ``rel_path`` is
    # the repo-relative path (the base class computes it via
    # ``source_file.relative_to(repo_root)`` and the extract override threads it
    # here). Folded into compute_stable_id's containing slot so same-name
    # classes/structs/enums/functions/methods in different files hash distinctly.
    # Reuses the base helper so the value byte-matches the file Symbol's own
    # stable_id (both use make_file_stable_id("cpp", normalize_path(rel_path))).
    file_stable_id = _analyzer._file_anchor(rel_path)

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

                # A cpp class declaring a pure virtual IS abstract. Recorded in
                # modifiers, where the shared type-family predicate reads it —
                # audit 0018 measured cpp emitting an empty modifier list on
                # every symbol, so abstract bases read as concrete.
                class_modifiers = (
                    ["abstract"] if _cpp_has_pure_virtual(node, source) else []
                )
                symbol = Symbol(
                    id=_make_symbol_id(str(file_path), start_line, end_line, name, "class"),
                    name=name,
                    kind="class",
                    modifiers=class_modifiers,
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
                    stable_id=_analyzer.compute_stable_id(
                        node, kind="class", name=name,
                        file_stable_id=file_stable_id,
                    ),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[name] = symbol

                # Extract field types for chained member resolution
                field_types = _extract_field_types_cpp(node, source)
                if field_types:
                    analysis.class_field_types[name] = field_types

                # WI-jusus: emit kind="field" member data symbols (class body
                # default visibility is private).
                _emit_cpp_field_symbols(
                    analysis, node, name, "private",
                    file_path, file_stable_id, source, run,
                )

        # Struct definition (with body only — skip references and
        # forward declarations like ``struct Foo;``)
        elif node.type == "struct_specifier":
            has_body = any(c.type == "field_declaration_list" for c in node.children)
            name_node = _find_child_by_type(node, "type_identifier") if has_body else None
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
                    stable_id=_analyzer.compute_stable_id(
                        node, kind="struct", name=name,
                        file_stable_id=file_stable_id,
                    ),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[name] = symbol

                # Extract field types for chained member resolution
                field_types = _extract_field_types_cpp(node, source)
                if field_types:
                    analysis.class_field_types[name] = field_types

                # WI-jusus: emit kind="field" member data symbols (struct body
                # default visibility is public).
                _emit_cpp_field_symbols(
                    analysis, node, name, "public",
                    file_path, file_stable_id, source, run,
                )

        # Enum definition (with body only — skip references and
        # forward declarations)
        elif node.type == "enum_specifier":
            has_body = any(c.type == "enumerator_list" for c in node.children)
            name_node = _find_child_by_type(node, "type_identifier") if has_body else None
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
                    stable_id=_analyzer.compute_stable_id(
                        node, kind="enum", name=name,
                        file_stable_id=file_stable_id,
                    ),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[name] = symbol

                # WI-dorop: one kind="field" per enumerator, named
                # `Color::Red` — the `::` separator cpp already uses for its
                # fields (`f"{owner_name}::{field_name}"`) and methods, and one
                # the containment linker splits on. Without these the enum is a
                # container with nothing in it and a reverse slice from it
                # returns the container alone.
                #
                # Deliberately INSIDE the `if name_node:` guard. An anonymous
                # enum (`typedef enum { P, Q } Anon;`) has no `type_identifier`
                # and emits no container, so emitting its members here would
                # produce symbols whose dotted owner does not exist — orphans by
                # construction, which is worse than the recall miss. Scoped
                # (`enum class`) and unscoped enums are the same
                # `enum_specifier` node; the scope keyword is just a token, so
                # both are covered. A forward declaration has no
                # `enumerator_list` and is already excluded by `has_body`.
                member_list = _find_child_by_type(node, "enumerator_list")
                for member in member_list.children if member_list else ():
                    if member.type != "enumerator":
                        continue
                    m_name_node = _find_child_by_type(member, "identifier")
                    if m_name_node is None:  # pragma: no cover - always names
                        continue
                    m_name = _node_text(m_name_node, source)
                    m_full = f"{name}::{m_name}"
                    m_start = member.start_point[0] + 1
                    m_end = member.end_point[0] + 1
                    m_sym = Symbol(
                        id=_make_symbol_id(
                            str(file_path), m_start, m_end, m_full, "field",
                        ),
                        name=m_full,
                        kind="field",
                        language="cpp",
                        path=str(file_path),
                        span=Span(
                            start_line=m_start,
                            end_line=m_end,
                            start_col=member.start_point[1],
                            end_col=member.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        # An enumerator is as reachable as its enum; C++ has no
                        # per-enumerator access specifier.
                        modifiers=["public"],
                        is_exported=True,
                        stable_id=_analyzer.compute_stable_id(
                            member, kind="field", name=m_full,
                            file_stable_id=file_stable_id,
                        ),
                        line_span=m_end - m_start + 1,
                    )
                    analysis.symbols.append(m_sym)
                    analysis.node_for_symbol[m_sym.id] = member

        # Function definition
        elif node.type == "function_definition":
            result = _extract_function_name(node, source)
            if result:
                name, kind = result
                # Qualify inline method names with the enclosing class.
                # Without this, Parser::Initialize and Packager::Initialize
                # both register as just "Initialize", colliding in the registry.
                if kind == "method" and "::" not in name:
                    enclosing_cls = _get_enclosing_class(node, source)
                    if enclosing_cls:
                        name = f"{enclosing_cls}::{name}"
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
                    # INV-loguk: analytical fields for C++ functions/methods.
                    line_span=end_line - start_line + 1,
                    cyclomatic_complexity=compute_cyclomatic_complexity(
                        node, "cpp",
                    ),
                    stable_id=_analyzer.compute_stable_id(
                        node, kind=kind, name=name,
                        file_stable_id=file_stable_id,
                    ),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                # Store by both full name and short name
                analysis.symbol_by_name[name] = symbol
                short_name = name.split("::")[-1] if "::" in name else name
                if short_name != name:
                    analysis.symbol_by_name[short_name] = symbol

        # WI-jusus: top-level / namespace variable (kind="variable"). A
        # ``declaration`` node is shared between module-scope globals and
        # function-body locals, so only module-scope ones are variables
        # (INV-sidab). Function prototypes (a function_declarator) and forward
        # type declarations must not leak. Like fields, variables go to
        # analysis.symbols only, never symbol_by_name.
        elif node.type == "declaration":
            parent = node.parent
            is_module_scope = parent is not None and (
                parent.type == "translation_unit"
                or (
                    parent.type == "declaration_list"
                    and parent.parent is not None
                    and parent.parent.type == "namespace_definition"
                )
            )
            if is_module_scope:
                names, is_function, has_nested_type = _cpp_data_declarators(
                    node, source,
                )
                if names and not is_function and not has_nested_type:
                    var_type = _cpp_leading_type(node, source)
                    modifiers = (
                        ["static"] if _cpp_is_static(node, source) else []
                    )
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    for var_name in names:
                        symbol = Symbol(
                            id=_make_symbol_id(
                                str(file_path), start_line, end_line,
                                var_name, "variable",
                            ),
                            name=var_name,
                            kind="variable",
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
                            signature=var_type or None,
                            modifiers=modifiers,
                            is_exported="static" not in modifiers,
                            line_span=end_line - start_line + 1,
                            stable_id=_analyzer.compute_stable_id(
                                node, kind="variable", name=var_name,
                                file_stable_id=file_stable_id,
                            ),
                        )
                        analysis.symbols.append(symbol)
                        analysis.node_for_symbol[symbol.id] = node

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


def _get_enclosing_class(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Walk up the AST to find the enclosing class/struct name."""
    parent = node.parent
    while parent is not None:
        if parent.type in ("class_specifier", "struct_specifier"):
            name_node = _find_child_by_type(parent, "type_identifier")
            if name_node:
                return _node_text(name_node, source)
        parent = parent.parent
    return None


def _resolve_cpp_field_chain(
    receiver_node: "tree_sitter.Node",
    source: bytes,
    registry: dict[str, dict[str, str]],
    call_node: "tree_sitter.Node",
) -> str | None:
    """Walk a ``this->field->field`` chain and resolve to the final type.

    Decomposes nested ``field_expression`` nodes leaf-to-root, validates
    the root is ``this``, then walks root-to-leaf through the registry.
    The enclosing class is found by walking up the AST from the call node.
    """
    # Determine enclosing class from AST
    enclosing_type = _get_enclosing_class(call_node, source)
    if enclosing_type is None:
        return None

    # Decompose: walk field_expression chain leaf-to-root
    segments: list[str] = []
    node = receiver_node
    while node.type == "field_expression":
        field_node = node.child_by_field_name("field")
        if field_node is None:  # pragma: no cover — tree-sitter guarantees field
            return None
        segments.append(_node_text(field_node, source))
        node = node.child_by_field_name("argument")
        if node is None:  # pragma: no cover — tree-sitter guarantees argument
            return None

    # Root must be "this"
    if node.type != "this":
        return None

    # Reverse to walk root-to-leaf
    segments.reverse()

    # Walk through registry
    current_type = enclosing_type
    for field_name in segments:
        fields = registry.get(current_type)
        if fields is None:
            return None
        next_type = fields.get(field_name)
        if next_type is None:
            return None
        current_type = next_type

    return current_type


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
    field_type_registry: dict[str, dict[str, str]] | None = None,
) -> list[Edge]:
    """Extract include, call, and instantiation edges from a parsed tree.

    Uses iterative traversal to avoid RecursionError on deeply nested code.

    Args:
        namespace_aliases: Mapping of alias -> qualified_namespace for path_hint (ADR-0007)
        field_type_registry: Class field types for chained ``this->`` resolution.
    """
    if namespace_aliases is None:
        namespace_aliases = {}  # pragma: no cover - always passed by caller
    edges: list[Edge] = []
    _caller_path = str(file_path)
    file_id = _make_file_id(str(file_path))

    # WI-rupik / WI-mafik: pre-collect system #include headers so the
    # unresolved-call emit path can attribute calls to the file's
    # include set. Without this, ``std::printf(...)`` after
    # ``#include <cstdio>`` produces a dst with no header context.
    system_includes: list[str] = []
    for _n in iter_tree(tree.root_node):
        if _n.type == "preproc_include":
            _sys_lib = _find_child_by_type(_n, "system_lib_string")
            if _sys_lib:
                # Strip surrounding angle brackets: ``<cstdio>`` → ``cstdio``.
                _hdr = _node_text(_sys_lib, source).strip("<>")
                if _hdr:
                    system_includes.append(_hdr)

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
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))

        # Function call
        elif node.type == "call_expression":
            if current_function is not None:
                callee_name = get_callee_name(node)
                if callee_name:
                    # Try field chain resolution (this->field->method())
                    chain_resolved = False
                    # Detect member calls (obj.method() / ptr->method())
                    top_field = _find_child_by_type(node, "field_expression")
                    is_member_call = top_field is not None
                    if field_type_registry and top_field:
                        # Walk inner field_expression chain to find receiver
                        receiver = top_field.child_by_field_name("argument")
                        if receiver and receiver.type == "field_expression":
                            resolved_type = _resolve_cpp_field_chain(
                                receiver, source, field_type_registry,
                                node,
                            )
                            if resolved_type:
                                lookup = resolver.lookup(callee_name, path_hint=resolved_type, caller_path=_caller_path)
                                if lookup.found and lookup.symbol is not None:
                                    edges.append(Edge.create(
                                        src=current_function.id,
                                        dst=lookup.symbol.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        evidence_type="ast_call",
                                        origin=PASS_ID,
                                        origin_run_id=run.execution_id,
                                        meta={"call_construct": "method", "receiver": "field_chain"},
                                    ))
                                    chain_resolved = True

                    if chain_resolved:
                        pass  # Skip normal resolution
                    else:
                        # Try to resolve: look for short name first
                        short_name = callee_name.split("::")[-1] if "::" in callee_name else callee_name

                        # STL method guard: when the call is a member call
                        # (obj.method()) and the method name is a common STL
                        # method, do NOT fall through to local/global resolution.
                        # Without this, v.clear() would resolve to a project-level
                        # clear() function.
                        if is_member_call and short_name in _CPP_STL_METHODS:
                            chain_resolved = True  # suppress fallback

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
                        elif current_function.kind == "method":
                            # Same-class preference: bare call like Initialize()
                            # inside a class method — use the enclosing class
                            # as path_hint so the resolver prefers same-class
                            # symbols (e.g., Parser::Initialize over Packager::Initialize).
                            enclosing_cls = _get_enclosing_class(node, source)
                            if enclosing_cls:
                                path_hint = enclosing_cls

                    # Check local symbols first (skip if chain already resolved).
                    # Same-class preference: try qualified name (Class::method)
                    # before short name to avoid collisions when multiple classes
                    # define the same method name.
                    _local_callee = None
                    if not chain_resolved:
                        if path_hint and f"{path_hint}::{short_name}" in local_symbols:
                            _local_callee = local_symbols[f"{path_hint}::{short_name}"]
                        elif short_name in local_symbols:
                            _local_callee = local_symbols[short_name]
                    if _local_callee is not None:
                        callee = _local_callee
                        edges.append(Edge.create(
                            src=current_function.id,
                            dst=callee.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            evidence_type="ast_call",
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            meta={"call_construct": "function"},
                        ))
                    # Check global symbols via resolver
                    elif not chain_resolved:
                        lookup_result = resolver.lookup(short_name, path_hint=path_hint, caller_path=_caller_path)
                        # INV-fahub: a BARE call (bare short name, implicit-this,
                        # or a chained receiver whose token was dropped) that only
                        # weak-suffix-matches a DIFFERENT class's method is the
                        # cross-class magnet (dozens of call sites -> one arbitrary
                        # def, e.g. purge() -> ActivityPubClient::purge). C++
                        # method symbol names are ``Owner::method`` (separator
                        # "::"), so parse the owner with that separator. Same-class
                        # implicit-this (owner == enclosing) and free
                        # functions/objects still bind directly; a cross-class
                        # magnet defers to the inherited_calls Site-1 walker via an
                        # unresolved edge stamped with ``enclosing_class`` (INV-nogof
                        # withhold-not-pick-first + INV-nilud linker-owns-resolution).
                        _enclosing_type = _get_enclosing_class(node, source)
                        _sym = lookup_result.symbol
                        _defer = _sym is not None and defer_bare_method_call(
                            _sym.kind, _sym.name,
                            lookup_result.match_type, _enclosing_type,
                            separator="::",
                        )
                        if lookup_result.found and _sym is not None and not _defer:
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=_sym.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                evidence_type="ast_call",
                                confidence=0.80 * lookup_result.confidence,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                meta={"call_construct": "function"},
                            ))
                        else:
                            # WI-rupik / WI-mafik: associate the unresolved
                            # call with the file's #include set so cross-
                            # language linkers and io-boundaries see the
                            # header context. The semantics is "this call
                            # could be from any of the included headers";
                            # downstream consumers may split the module_hint
                            # on commas if they need per-header resolution.
                            if system_includes:
                                module_hint = ",".join(system_includes)
                                ext_ref = ExternalRef(
                                    lang="cpp",
                                    module_path=module_hint,
                                    name=short_name,
                                )
                                edges.append(make_unresolved_edge(
                                    "cpp", current_function.id, short_name,
                                    node.start_point[0] + 1, PASS_ID,
                                    run.execution_id,
                                    module_hint=module_hint,
                                    dst_ref=ext_ref,
                                    enclosing_class=_enclosing_type,
                                ))
                            else:
                                edges.append(make_unresolved_edge(
                                    "cpp", current_function.id, short_name,
                                    node.start_point[0] + 1, PASS_ID,
                                    run.execution_id,
                                    enclosing_class=_enclosing_type,
                                ))

                    # Callback argument detection: bare identifiers in the
                    # argument list that resolve to known functions are likely
                    # function pointer callbacks.
                    arg_list = node.child_by_field_name("arguments")
                    if arg_list:
                        for arg in arg_list.children:
                            if arg.type != "identifier":
                                continue
                            arg_name = _node_text(arg, source)
                            cb_lookup = resolver.lookup(arg_name, caller_path=_caller_path)
                            if (
                                cb_lookup.found
                                and cb_lookup.symbol is not None
                                and cb_lookup.symbol.kind == "function"
                                and cb_lookup.symbol.id != current_function.id
                            ):
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=cb_lookup.symbol.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    confidence=0.80 * cb_lookup.confidence,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                    evidence_type="function_pointer_arg",
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
                    # WI-dagih: emit the REGISTERED evidence_type 'ast_new' (the
                    # value js_ts/java already use for the identical heap-`new`
                    # instantiation), not the raw tree-sitter node-type string
                    # 'new_expression' (absent from the evidence-type catalog —
                    # an axis_conformance leak). Stack-construction paths keep
                    # their own registered 'stack_construction' pathway.
                    _try_instantiation_edge(
                        type_name, current_function, node, "ast_new",
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
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

        # Explicit function pointer: &process or &Class::method
        elif node.type == "pointer_expression":
            if current_function is not None:
                children = node.children
                # Must start with '&' (address-of, not dereference '*')
                if children and _node_text(children[0], source) == "&":
                    ref_name = None
                    ident = _find_child_by_type(node, "identifier")
                    if ident:
                        ref_name = _node_text(ident, source)
                    else:
                        qual = _find_child_by_type(node, "qualified_identifier")
                        if qual:
                            inner = _find_child_by_type(qual, "identifier")
                            if inner:
                                ref_name = _node_text(inner, source)
                    if ref_name:
                        target = local_symbols.get(ref_name)
                        if target is None:
                            lk = resolver.lookup(ref_name, caller_path=_caller_path)
                            if lk.found and lk.symbol is not None:
                                target = lk.symbol
                        if (
                            target is not None
                            and target.kind in ("function", "method")
                            and target.id != current_function.id
                        ):
                            edges.append(Edge.create(
                                src=current_function.id,
                                dst=target.id,
                                edge_type="references",
                                line=node.start_point[0] + 1,
                                evidence_type="function_pointer",
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))

        # Add children to stack with updated context
        for child in reversed(node.children):
            stack.append((child, new_function))

    # Dispatch table detection: function pointers in static array initializers.
    # Pattern: static struct Foo table[] = { { "name", func_ptr }, ... };
    # Identifiers in initializer lists that resolve to known functions
    # become dispatches_to edges. Same pattern as C — C++ codebases use
    # identical dispatch tables (command dispatch, plugin registries, etc.).
    #
    # After detecting dispatch tables, scan function bodies for references
    # to the dispatch table variable, creating uses_dispatch_table edges.
    dispatch_tables: dict[str, str] = {}  # variable name -> symbol ID
    for dt_node in iter_tree(tree.root_node):
        if dt_node.type != "init_declarator":
            continue
        # Must be at file scope (inside a declaration, not inside a function body)
        if not dt_node.parent or dt_node.parent.type != "declaration":
            continue  # pragma: no cover - defensive
        array_decl = None
        init_list = None
        for child in dt_node.children:
            if child.type == "array_declarator":
                array_decl = child
            elif child.type == "initializer_list":
                init_list = child
        if array_decl is None or init_list is None:
            continue

        # Get the array variable name
        array_name = None
        for child in array_decl.children:
            if child.type == "identifier":
                array_name = _node_text(child, source)
                break
        if not array_name:
            continue  # pragma: no cover - defensive

        # Build a stable src ID for the dispatch table (array variable)
        array_line = dt_node.start_point[0] + 1
        array_src_id = _base_make_symbol_id(
            "cpp", str(file_path), array_line, array_line, array_name, "variable",
        )

        # Scan nested initializer lists for identifiers matching functions
        seen_funcs: set[str] = set()
        for inner_node in iter_tree(init_list):
            if inner_node.type != "identifier":
                continue
            ident_name = _node_text(inner_node, source)
            if ident_name in seen_funcs:
                continue
            # Only link to known function symbols
            lookup_result = resolver.lookup(ident_name, caller_path=_caller_path)
            if not lookup_result.found or lookup_result.symbol is None:
                continue
            if lookup_result.symbol.kind != "function":
                continue  # pragma: no cover - type names parse as type_identifier
            seen_funcs.add(ident_name)
            edges.append(Edge.create(
                src=array_src_id,
                dst=lookup_result.symbol.id,
                edge_type="dispatches_to",
                line=inner_node.start_point[0] + 1,
                confidence=0.80 * lookup_result.confidence,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="dispatch_table_initializer",
            ))

        # Only record tables that actually have function pointer entries
        if seen_funcs:
            dispatch_tables[array_name] = array_src_id

    # Scan function bodies for references to discovered dispatch table
    # variables, creating uses_dispatch_table edges.
    if dispatch_tables:
        str_path = str(file_path)
        for dt_node in iter_tree(tree.root_node):
            if dt_node.type != "function_definition":
                continue
            fn_result = _extract_function_name(dt_node, source)
            if fn_result is None:
                continue  # pragma: no cover - defensive
            fn_name, _ = fn_result
            short_fn = fn_name.split("::")[-1] if "::" in fn_name else fn_name
            # Resolve the function symbol from local or global tables
            func_sym = None
            if local_symbols and short_fn in local_symbols:
                func_sym = local_symbols[short_fn]
            elif short_fn in global_symbols:  # pragma: no cover - fallback
                gs = global_symbols[short_fn]
                if gs.path == str_path:
                    func_sym = gs
            if func_sym is None:
                continue  # pragma: no cover - defensive
            body = dt_node.child_by_field_name("body")
            if body is None:
                continue  # pragma: no cover - defensive
            seen_tables: set[str] = set()
            for inner in iter_tree(body):
                if inner.type != "identifier":
                    continue
                name = _node_text(inner, source)
                if name in dispatch_tables and name not in seen_tables:
                    seen_tables.add(name)
                    # ADR-0023 §6 Phase 3 / audit-findings 0001 (WI-vasik-jofiv):
                    # Same as c.py — function references a dispatch-
                    # table data symbol. Canonical 'references' +
                    # meta['ref_construct']='dispatch_table'.
                    edges.append(Edge.create(
                        src=func_sym.id,
                        dst=dispatch_tables[name],
                        edge_type="references",
                        line=inner.start_point[0] + 1,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="dispatch_table_reference",
                        meta={"ref_construct": "dispatch_table"},
                    ))

    # WI-zojid: emit module_attr_ref edges for scoped attribute reads on
    # ``std::``-prefixed iostream symbols (``std::cout`` / ``std::cerr`` /
    # ``std::cin``) and any other namespace alias the analyser tracks.
    # Pairs with ``attributes:`` entries in io_primitives/cpp.yaml; without
    # this wire-up the iostream IO would never reach
    # ``hypergumbo io-boundaries`` on tree-sitter parses.  Cross-language
    # helper added by WI-vipur (originally for Rust's ``scoped_identifier``);
    # C++'s ``qualified_identifier`` has the same left-recursive ``scope``
    # / ``name`` shape so the same helper applies with ``scoped_path=True``.
    # ``std`` is injected as an implicit import — the C++ standard library
    # is in scope without an explicit ``using`` declaration.  Namespace
    # aliases declared with ``namespace fs = std::filesystem;`` are merged
    # in so attribute reads through the alias also resolve.
    attr_imports = dict(namespace_aliases)
    attr_imports.setdefault("std", "std")
    file_pseudo_symbol = Symbol(
        id=file_id,
        name=Path(_caller_path).name,
        kind="module",
        language="cpp",
        path=_caller_path,
        span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
        origin=PASS_ID,
        origin_run_id=run.execution_id,
    )
    emit_module_attribute_refs(
        tree.root_node,
        source,
        attr_imports,
        file_pseudo_symbol,
        "cpp",
        edges,
        node_kinds=("qualified_identifier",),
        object_field_names=("scope",),
        property_field_names=("name",),
        pass_id=PASS_ID,
        run_id=run.execution_id,
        call_node_kinds=("call_expression",),
        call_function_field_names=("function",),
        scoped_path=True,
    )

    return edges


class CppAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based C++ analyzer.

    Uses tree-sitter-cpp to parse C++ files and extract classes, structs,
    enums, functions, methods, include directives, call edges, and
    instantiation edges. Overrides ``register_symbol`` to prefer
    definitions (.cpp/.cc/.cxx) over declarations (.h/.hpp/.hxx).
    """

    lang = "cpp"
    file_patterns: ClassVar[list[str]] = ["*.cpp", "*.cc", "*.cxx", "*.h", "*.hpp", "*.hxx"]
    grammar_module = "tree_sitter_cpp"
    self_keywords: ClassVar[frozenset[str]] = frozenset({"this"})

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
        """Register symbol by qualified name, preferring source over headers.

        Does NOT register short names — the ``NameResolver`` suffix index
        handles ``"compute"`` → ``"MyClass::compute"`` lookups. Registering
        short names caused false exact matches when multiple types share a
        method name.

        WI-jusus chokepoint: field/variable data anchors are kept OUT of the
        call-resolution registry. A member field / global variable is never a
        call target, so registering it under its (possibly bare) name would
        clobber a same-named function's flat key or become a suffix-matched
        spurious call target. They remain in ``analysis.symbols`` (search /
        centrality / io-boundaries) since the output set is built independently
        of this registry.
        """
        if symbol.kind in ("field", "variable"):
            return
        existing = global_symbols.get(symbol.name)
        if existing is None:
            global_symbols[symbol.name] = symbol
        else:
            sym_is_source = any(
                symbol.path.endswith(ext) for ext in ('.cpp', '.cc', '.cxx')
            )
            existing_is_source = any(
                existing.path.endswith(ext) for ext in ('.cpp', '.cc', '.cxx')
            )
            if sym_is_source and not existing_is_source:
                global_symbols[symbol.name] = symbol  # pragma: no cover

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
            field_type_registry=getattr(self, "_field_type_registry", None),
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
