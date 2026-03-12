# SPDX-License-Identifier: AGPL-3.0-or-later
"""Groovy analysis pass using tree-sitter-groovy.

This analyzer uses tree-sitter to parse Groovy files and extract:
- Class declarations
- Interface declarations
- Enum declarations
- Method declarations (inside classes)
- Top-level function definitions (def keyword)
- Function call relationships
- Import statements

Note: Trait declarations are not currently supported by tree-sitter-groovy v0.1.2
(the grammar parses 'trait X' as a function call). Support will be added when
the grammar is updated.

It also handles Gradle build files (.gradle) which use Groovy DSL.

If tree-sitter with Groovy support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses the TreeSitterAnalyzer base class for two-pass orchestration:
1. extract_symbols_from_file: extracts classes, interfaces, enums, methods, functions
2. get_import_aliases: extracts 'import X as Y' aliases for path_hint disambiguation
3. register_symbol: registers both short and full names for cross-file resolution
4. extract_edges_from_file: resolves import statements and function/method calls

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-groovy package for grammar
- Two-pass allows cross-file call resolution
- Same pattern as Kotlin/Java/Scala analyzers for consistency
- Gradle build files are analyzed as Groovy code
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, make_pass_id
from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
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

PASS_ID = make_pass_id("groovy")


def find_groovy_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Groovy files in the repository.

    Includes both .groovy files and .gradle build files.
    """
    yield from find_files(repo_root, ["*.groovy", "*.gradle"])


def is_groovy_tree_sitter_available() -> bool:
    """Check if tree-sitter with Groovy grammar is available."""
    return _analyzer._check_grammar_available()


# Groovy modifiers that can appear on declarations.
# tree-sitter-groovy places keyword nodes directly inside a ``modifiers``
# container (same pattern as Java).
GROOVY_MODIFIERS = {
    "public", "private", "protected",
    "static", "abstract", "final",
    "synchronized", "native", "transient",
    "volatile", "strictfp", "default",
}


def _extract_modifiers_groovy(node: "tree_sitter.Node") -> list[str]:
    """Extract all modifiers from a Groovy declaration node.

    Groovy tree-sitter uses a ``modifiers`` container whose children are
    keyword tokens (e.g. ``public``, ``static``), same pattern as Java.

    Returns a list of modifier strings like ``["public", "static"]``.
    """
    modifiers: list[str] = []
    for child in node.children:
        if child.type == "modifiers":
            for kw in child.children:
                if kw.type in GROOVY_MODIFIERS:
                    modifiers.append(kw.type)
    return modifiers


def _extract_base_classes_groovy(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract base class and interface names from class declaration.

    Handles:
    - class Foo extends Bar
    - class Foo implements IBar
    - class Foo extends Bar implements IBaz, IQux
    - Generic types: List<String> -> List

    Args:
        node: class_declaration node
        source: Source code bytes

    Returns:
        List of base class/interface names (without generic params)
    """
    base_classes: list[str] = []

    for child in node.children:
        # superclass clause: class Foo extends Bar
        if child.type == "superclass":
            for sub in child.children:
                if sub.type == "type_identifier":
                    base_classes.append(node_text(sub, source))
                elif sub.type == "generic_type":
                    # Generic type: List<String> -> extract just the type name
                    type_id = find_child_by_type(sub, "type_identifier")
                    if type_id:
                        base_classes.append(node_text(type_id, source))
        # super_interfaces clause: class Foo implements IBar, IBaz
        elif child.type == "super_interfaces":
            type_list = find_child_by_type(child, "type_list")
            if type_list:
                for sub in type_list.children:
                    if sub.type == "type_identifier":
                        base_classes.append(node_text(sub, source))
                    elif sub.type == "generic_type":
                        type_id = find_child_by_type(sub, "type_identifier")
                        if type_id:
                            base_classes.append(node_text(type_id, source))

    return base_classes


def _extract_annotations_groovy(
    node: "tree_sitter.Node", source: bytes,
) -> list[dict[str, object]]:
    """Extract annotations from a Groovy declaration node.

    Groovy wraps annotations in a ``modifiers`` node, using
    ``marker_annotation`` for no-arg annotations and ``annotation``
    for parameterized ones.
    """
    decorators: list[dict[str, object]] = []
    for child in node.children:
        if child.type == "modifiers":
            for mod_child in child.children:
                if mod_child.type in ("marker_annotation", "annotation"):
                    name = ""
                    args: list[object] = []
                    kwargs: dict[str, object] = {}
                    for part in mod_child.children:
                        if part.type == "identifier":
                            name = node_text(part, source)
                        elif part.type == "annotation_argument_list":
                            for arg in part.children:
                                if arg.type == "string_literal":
                                    # Groovy string_literal uses
                                    # string_fragment (not string_content)
                                    frag = find_child_by_type(
                                        arg, "string_fragment",
                                    )
                                    if frag:
                                        args.append(node_text(frag, source))
                                    else:  # pragma: no cover — defensive
                                        args.append(node_text(arg, source))
                                elif arg.type == "element_value_pair":
                                    parts = [
                                        c for c in arg.children if c.type != "="
                                    ]
                                    if len(parts) >= 2:
                                        key = node_text(parts[0], source)
                                        val_node = parts[1]
                                        if val_node.type == "string_literal":
                                            frag = find_child_by_type(
                                                val_node, "string_fragment",
                                            )
                                            val = (
                                                node_text(frag, source)
                                                if frag
                                                else node_text(val_node, source)
                                            )
                                        else:
                                            val = node_text(val_node, source)
                                        kwargs[key] = val
                    if name:
                        decorators.append(
                            {"name": name, "args": args, "kwargs": kwargs}
                        )
    return decorators


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:  # pragma: no cover
    """Find child by field name."""
    return node.child_by_field_name(field_name)


def _get_enclosing_class(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up the tree to find the enclosing class/interface/trait name."""
    current = node.parent
    while current is not None:
        if current.type in ("class_declaration", "interface_declaration", "trait_declaration"):
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                return node_text(name_node, source)
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_enclosing_function_groovy(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up the tree to find the enclosing method/function."""
    current = node.parent
    while current is not None:
        if current.type == "method_declaration":
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                method_name = node_text(name_node, source)
                if method_name in local_symbols:
                    return local_symbols[method_name]
        elif current.type == "function_definition":
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                func_name = node_text(name_node, source)
                if func_name in local_symbols:
                    return local_symbols[func_name]
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_groovy_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract method signature from a method_declaration or function_definition node.

    Returns signature in format: (Type param, Type param2): ReturnType
    Omits void return types.
    """
    params: list[str] = []
    return_type: Optional[str] = None

    # Look for formal_parameters node
    for child in node.children:
        if child.type == "formal_parameters":
            for param in child.children:
                if param.type == "formal_parameter":
                    param_type = None
                    param_name = None
                    for pc in param.children:
                        if pc.type in ("type_identifier", "primitive_type",
                                       "array_type", "generic_type"):
                            param_type = node_text(pc, source)
                        elif pc.type == "identifier":
                            param_name = node_text(pc, source)
                    if param_type and param_name:
                        params.append(f"{param_type} {param_name}")
                    elif param_name:  # pragma: no cover - dynamic typing
                        params.append(param_name)
        elif child.type in ("type_identifier", "primitive_type", "void_type",
                            "array_type", "generic_type"):
            # Return type appears before the method name
            return_type = node_text(child, source)

    params_str = ", ".join(params)
    signature = f"({params_str})"

    if return_type and return_type != "void":
        signature += f": {return_type}"

    return signature


def normalize_groovy_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Groovy signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_types_first
    return normalize_signature_types_first(
        signature, type_params, skip_void_return=False, return_sep=":",
    )


def _extract_import_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import aliases for disambiguation.

    In Groovy:
        import java.util.List as JList -> JList maps to java.util.List

    Returns a dict mapping alias names to fully qualified module paths.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import_declaration":
            continue

        # Check if this import has an alias (has 'as' keyword)
        has_as = False
        module_path = None
        alias_name = None

        for child in node.children:
            if child.type == "scoped_identifier":
                module_path = node_text(child, source)
            elif child.type == "as":
                has_as = True
            elif child.type == "identifier" and has_as:
                # This identifier comes after 'as', so it's the alias
                alias_name = node_text(child, source)

        if has_as and module_path and alias_name:
            aliases[alias_name] = module_path

    return aliases


class GroovyAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based Groovy analyzer.

    Uses tree-sitter-groovy to parse .groovy and .gradle files.
    Extracts classes, interfaces, enums, methods, top-level functions,
    import statements, and call edges.

    Overrides ``register_symbol`` to register both short and fully-qualified
    names for cross-file resolution (e.g., both "doSomething" and
    "Utils.doSomething").

    Overrides ``get_import_aliases`` for Groovy's 'import X as Y' syntax.
    """

    lang = "groovy"
    file_patterns: ClassVar[list[str]] = ["*.groovy", "*.gradle"]
    grammar_module = "tree_sitter_groovy"

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract symbols from a single Groovy file.

        Detects class, interface, trait, and enum declarations, method declarations
        inside classes, and top-level function definitions (def keyword).
        """
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            # Class declaration
            if node.type == "class_declaration":
                name_node = find_child_by_type(node, "identifier")

                if name_node:
                    class_name = node_text(name_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    # Extract base classes and interfaces
                    base_classes = _extract_base_classes_groovy(node, source)
                    annotations = _extract_annotations_groovy(node, source)
                    meta: dict[str, object] | None = {}
                    if base_classes:
                        meta["base_classes"] = base_classes
                    if annotations:
                        meta["decorators"] = annotations
                    if not meta:
                        meta = None

                    symbol = Symbol(
                        id=make_symbol_id("groovy", str(file_path), start_line, end_line, class_name, "class"),
                        name=class_name,
                        kind="class",
                        language="groovy",
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
                        modifiers=_extract_modifiers_groovy(node),
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[class_name] = symbol

            # Interface declaration
            elif node.type == "interface_declaration":
                name_node = find_child_by_type(node, "identifier")

                if name_node:
                    iface_name = node_text(name_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    symbol = Symbol(
                        id=make_symbol_id("groovy", str(file_path), start_line, end_line, iface_name, "interface"),
                        name=iface_name,
                        kind="interface",
                        language="groovy",
                        path=str(file_path),
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        modifiers=_extract_modifiers_groovy(node),
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[iface_name] = symbol

            # Trait declaration (Groovy-specific)
            # NOTE: tree-sitter-groovy v0.1.2 doesn't produce trait_declaration nodes
            # This code is kept for future grammar updates
            elif node.type == "trait_declaration":  # pragma: no cover - grammar limitation
                name_node = find_child_by_type(node, "identifier")

                if name_node:
                    trait_name = node_text(name_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    symbol = Symbol(
                        id=make_symbol_id("groovy", str(file_path), start_line, end_line, trait_name, "trait"),
                        name=trait_name,
                        kind="trait",
                        language="groovy",
                        path=str(file_path),
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        modifiers=_extract_modifiers_groovy(node),
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[trait_name] = symbol

            # Enum declaration
            elif node.type == "enum_declaration":
                name_node = find_child_by_type(node, "identifier")

                if name_node:
                    enum_name = node_text(name_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    symbol = Symbol(
                        id=make_symbol_id("groovy", str(file_path), start_line, end_line, enum_name, "enum"),
                        name=enum_name,
                        kind="enum",
                        language="groovy",
                        path=str(file_path),
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        modifiers=_extract_modifiers_groovy(node),
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[enum_name] = symbol

            # Method declaration (inside class/trait)
            elif node.type == "method_declaration":
                name_node = find_child_by_type(node, "identifier")

                if name_node:
                    method_name = node_text(name_node, source)
                    current_class = _get_enclosing_class(node, source)
                    if current_class:
                        full_name = f"{current_class}.{method_name}"
                    else:  # pragma: no cover - defensive: methods always in classes
                        full_name = method_name

                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    signature = _extract_groovy_signature(node, source)
                    modifiers = _extract_modifiers_groovy(node)
                    annotations = _extract_annotations_groovy(node, source)
                    method_meta = (
                        {"decorators": annotations} if annotations else None
                    )

                    # Typed stable_id (ADR-0014 §3)
                    norm_sig = normalize_groovy_signature(signature)
                    stable_id = make_typed_stable_id(
                        "method", norm_sig, visibility_from_modifiers(modifiers),
                    ) if norm_sig else None

                    symbol = Symbol(
                        id=make_symbol_id("groovy", str(file_path), start_line, end_line, full_name, "method"),
                        name=full_name,
                        kind="method",
                        language="groovy",
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
                        meta=method_meta,
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[method_name] = symbol
                    analysis.symbol_by_name[full_name] = symbol

            # Function definition (def keyword at top level)
            elif node.type == "function_definition":
                name_node = find_child_by_type(node, "identifier")
                current_class = _get_enclosing_class(node, source)

                if name_node and current_class is None:
                    func_name = node_text(name_node, source)
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    signature = _extract_groovy_signature(node, source)
                    modifiers = _extract_modifiers_groovy(node)

                    # Typed stable_id (ADR-0014 §3)
                    norm_sig = normalize_groovy_signature(signature)
                    stable_id = make_typed_stable_id(
                        "function", norm_sig, visibility_from_modifiers(modifiers),
                    ) if norm_sig else None

                    symbol = Symbol(
                        id=make_symbol_id("groovy", str(file_path), start_line, end_line, func_name, "function"),
                        name=func_name,
                        kind="function",
                        language="groovy",
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

        return analysis

    def get_import_aliases(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
    ) -> dict[str, str]:
        """Extract import alias to module path mappings.

        Handles Groovy's 'import X as Y' syntax for call disambiguation.
        """
        return _extract_import_aliases(tree, source)

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict,
    ) -> None:
        """Register symbol by qualified name only.

        The ``NameResolver`` suffix index handles short-name lookups.
        """
        global_symbols[symbol.name] = symbol

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
        """Extract call and import edges from a file.

        Detects import statements and function/method calls (both
        method_invocation and juxt_function_call forms).
        Uses import aliases as path_hint for resolver disambiguation.
        """
        # AMB-METHOD: build method resolver for ambiguity guard
        global_methods: dict[str, list[Symbol]] = {}
        seen_ids: set[str] = set()
        for sym in global_symbols.values():
            if sym.id in seen_ids:  # pragma: no cover
                continue  # pragma: no cover
            seen_ids.add(sym.id)
            if sym.kind == "method":
                short = sym.name.split(".")[-1] if "." in sym.name else sym.name
                global_methods.setdefault(short, []).append(sym)
        method_resolver = ListNameResolver(global_methods, ambiguity_threshold=3)

        edges: list[Edge] = []
        _caller_path = str(file_path)
        file_id = make_file_id("groovy", str(file_path))
        var_types: dict[str, str] = {}

        for node in iter_tree(tree.root_node):
            # Track param types: formal_parameter → type_identifier + identifier
            if node.type == "formal_parameter":
                type_node = find_child_by_type(node, "type_identifier")
                name_node = find_child_by_type(node, "identifier")
                if type_node and name_node:
                    ptype = node_text(type_node, source)
                    pname = node_text(name_node, source)
                    if ptype != "def":  # 'def' is parsed as type_identifier
                        var_types[pname] = ptype

            # Track constructor assignments: def repo = new UserRepository()
            elif node.type == "variable_declarator":
                var_node = find_child_by_type(node, "identifier")
                ctor_node = find_child_by_type(node, "object_creation_expression")
                if var_node and ctor_node:
                    type_node = find_child_by_type(ctor_node, "type_identifier")
                    if type_node:
                        var_types[node_text(var_node, source)] = node_text(
                            type_node, source,
                        )

            # Detect import statements
            elif node.type == "import_declaration":
                # Get the scoped identifier being imported
                id_node = find_child_by_type(node, "scoped_identifier")
                if not id_node:  # pragma: no cover - grammar fallback
                    id_node = find_child_by_type(node, "identifier")
                if id_node:
                    import_path = node_text(id_node, source)
                    edges.append(Edge.create(
                        src=file_id,
                        dst=f"groovy:{import_path}:0-0:package:package",
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        evidence_type="import_statement",
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))

            # Detect function calls (various forms in Groovy)
            # method_invocation: helper() inside a method, or Receiver.method()
            # juxt_function_call: println "hello" (Groovy's operator-less call syntax)
            elif node.type in ("method_invocation", "juxt_function_call"):
                current_function = _get_enclosing_function_groovy(node, source, local_symbols)
                if current_function is not None:
                    # Extract receiver and method name from method_invocation
                    # Pattern: identifier.identifier(args) or just identifier(args)
                    receiver = None
                    callee_name = None

                    if node.type == "method_invocation":
                        # Check structure: receiver.method(args)
                        # Identifiers appear in order: receiver (optional), then method
                        identifiers = [c for c in node.children if c.type == "identifier"]
                        has_dot = any(c.type == "." for c in node.children)

                        if has_dot and len(identifiers) >= 2:
                            # Qualified call: Receiver.method()
                            receiver = node_text(identifiers[0], source)
                            callee_name = node_text(identifiers[1], source)
                        elif len(identifiers) >= 1:
                            # Simple call: method()
                            callee_name = node_text(identifiers[0], source)
                    else:
                        # juxt_function_call: println "hello"
                        callee_node = find_child_by_type(node, "identifier")
                        if callee_node:
                            callee_name = node_text(callee_node, source)

                    if callee_name:
                        # Get path hint from import aliases if receiver is aliased
                        path_hint: Optional[str] = None
                        if receiver and receiver in import_aliases:
                            path_hint = import_aliases[receiver]

                        # Type-qualified resolution: receiver.method() → Type.method
                        edge_added = False
                        if receiver and receiver in var_types:
                            type_name = var_types[receiver]
                            qualified = f"{type_name}.{callee_name}"
                            target = local_symbols.get(qualified)
                            if target is None:
                                lookup = resolver.lookup(
                                    qualified,
                                    path_hint=import_aliases.get(type_name),
                                    caller_path=_caller_path,
                                )
                                if lookup.found and lookup.symbol is not None:
                                    target = lookup.symbol
                            if target is not None:
                                edges.append(Edge.create(
                                    src=current_function.id,
                                    dst=target.id,
                                    edge_type="calls",
                                    line=node.start_point[0] + 1,
                                    evidence_type="function_call",
                                    confidence=0.85,
                                    origin=PASS_ID,
                                    origin_run_id=run.execution_id,
                                ))
                                edge_added = True

                        if not edge_added:
                            # AMB-METHOD guard: when 3+ classes define the same
                            # method name, suppress the edge to avoid false positives.
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
                            # Check global symbols via resolver
                            else:
                                lookup_result = resolver.lookup(callee_name, path_hint=path_hint, caller_path=_caller_path)
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

        return edges


_analyzer = GroovyAnalyzer()


@register_analyzer("groovy")
def analyze_groovy(repo_root: Path) -> AnalysisResult:
    """Analyze all Groovy files in a repository.

    Returns a AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-groovy is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
