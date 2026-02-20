"""Dart/Flutter analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse Dart files and extract:
- Class declarations (including abstract classes)
- Function declarations (top-level and methods)
- Constructor declarations (named and unnamed)
- Getter and setter declarations
- Enum declarations
- Mixin declarations
- Extension declarations
- Import and export statements
- Function call relationships

If tree-sitter with Dart support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract classes, functions, methods, constructors, enums, mixins
2. Pass 2: Detect calls, imports, and instantiations using NameResolver

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Dart-specific
extraction logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for Dart grammar
- Two-pass allows cross-file call resolution
- Same pattern as other tree-sitter analyzers for consistency

Dart-Specific Considerations
----------------------------
- Dart has classes, mixins, extensions, and enums
- Methods are always inside class/mixin/extension bodies
- Constructors can be named (User.guest) or unnamed (User)
- Import/export statements have various forms:
  - import 'dart:io';
  - import 'package:flutter/material.dart';
  - import 'local.dart';
  - export 'src/models.dart';
  - import 'package:foo/foo.dart' show Bar;

AST Structure Notes
-------------------
- method_signature contains function_signature (for regular methods)
  or getter_signature/setter_signature (for accessors)
- function_signature and function_body are siblings at the same level
- Imports: import_or_export > library_import > import_specification >
  configurable_uri > uri > string_literal
- Function calls: expression_statement containing identifier + selector
  with argument_part > arguments
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

from hypergumbo_core.symbol_resolution import ListNameResolver

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("dart")


def find_dart_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Dart files in the repository."""
    yield from find_files(repo_root, ["*.dart"])


def _find_next_sibling_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find next sibling of given type."""
    current = node.next_sibling
    while current:
        if current.type == type_name:
            return current
        current = current.next_sibling
    return None


def _get_combined_span(
    start_node: "tree_sitter.Node",
    end_node: Optional["tree_sitter.Node"],
) -> tuple[int, int, int, int]:
    """Get span covering from start_node to end_node (or just start_node if no end)."""
    start_line = start_node.start_point[0] + 1
    start_col = start_node.start_point[1]
    if end_node:
        end_line = end_node.end_point[0] + 1
        end_col = end_node.end_point[1]
    else:
        end_line = start_node.end_point[0] + 1
        end_col = start_node.end_point[1]
    return start_line, end_line, start_col, end_col


def _extract_dart_signature(
    func_sig_node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract function signature from a Dart function_signature node.

    Dart function signatures look like:
        int add(int x, int y)
        String greet(String name, {bool loud = false})
        void main()

    Returns signature like "(int x, int y) int" or "(String name, {bool loud = ...}) String".
    """
    params: list[str] = []
    return_type: Optional[str] = None

    for child in func_sig_node.children:
        if child.type == "type_identifier":
            # Return type comes before function name
            if return_type is None:
                return_type = node_text(child, source).strip()
        elif child.type == "formal_parameter_list":
            # Extract parameters
            for param_child in child.children:
                if param_child.type == "formal_parameter":
                    param_text = node_text(param_child, source).strip()
                    if param_text:
                        params.append(param_text)
                elif param_child.type == "optional_formal_parameters":
                    # Handle optional/named parameters
                    opt_text = node_text(param_child, source).strip()
                    if opt_text:
                        # Replace default values with ...
                        import re
                        opt_text = re.sub(r'\s*=\s*[^,}\]]+', ' = ...', opt_text)
                        params.append(opt_text)

    sig = "(" + ", ".join(params) + ")"
    if return_type and return_type != "void":
        sig += f" {return_type}"
    return sig


def normalize_dart_signature(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Dart signature for typed stable_id (ADR-0014 §3)."""
    from hypergumbo_core.analyze.base import normalize_signature_types_first
    return normalize_signature_types_first(signature, type_params)


def _extract_dart_return_type_name(signature: str | None) -> str | None:
    """Extract the return type name from a Dart function signature.

    Dart signatures use the format ``(params) ReturnType`` where void is omitted.
    Returns the type name only if it is a simple uppercase identifier (filters
    out primitive types like ``int``, ``String`` and generics like ``List<T>``).

    Examples:
        ``"() ServiceClient"`` → ``"ServiceClient"``
        ``"(String name, int age) User"`` → ``"User"``
        ``"()"`` → ``None``
        ``"() int"`` → ``None`` (lowercase = primitive)
        ``"() List<String>"`` → ``None`` (generic)
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


def _find_enclosing_class(
    node: "tree_sitter.Node",
    source: bytes,
) -> Optional[str]:
    """Find the enclosing class/mixin/extension name for a given node."""
    current = node.parent
    while current:
        if current.type in ("class_definition", "mixin_declaration", "extension_declaration"):
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                return node_text(name_node, source)
        current = current.parent
    return None


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Dart file."""
    symbols: list[Symbol] = []

    def make_symbol(
        start_line: int,
        end_line: int,
        start_col: int,
        end_col: int,
        name: str,
        kind: str,
        prefix: Optional[str] = None,
        signature: Optional[str] = None,
    ) -> Symbol:
        """Create a Symbol with given span."""
        full_name = f"{prefix}.{name}" if prefix else name
        span = Span(
            start_line=start_line,
            end_line=end_line,
            start_col=start_col,
            end_col=end_col,
        )
        sym_id = make_symbol_id("dart", file_path, start_line, end_line, full_name, kind)
        # Dart visibility: underscore-prefixed names are library-private
        modifiers = ["private"] if name.startswith("_") else []
        return Symbol(
            id=sym_id,
            name=full_name,
            kind=kind,
            language="dart",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
            modifiers=modifiers,
        )

    for node in iter_tree(tree.root_node):
        # Class declaration
        if node.type == "class_definition":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source)
                start_line, end_line, start_col, end_col = _get_combined_span(node, None)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "class"))
            continue

        # Mixin declaration
        if node.type == "mixin_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source)
                start_line, end_line, start_col, end_col = _get_combined_span(node, None)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "mixin"))
            continue

        # Extension declaration
        if node.type == "extension_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source)
                start_line, end_line, start_col, end_col = _get_combined_span(node, None)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "extension"))
            continue

        # Enum declaration
        if node.type == "enum_declaration":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source)
                start_line, end_line, start_col, end_col = _get_combined_span(node, None)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "enum"))
            continue

        # Method signature (inside class body) - contains function_signature, getter, or setter
        if node.type == "method_signature":
            class_name = _find_enclosing_class(node, source)

            # Check for getter_signature
            getter_sig = find_child_by_type(node, "getter_signature")
            if getter_sig:
                name_node = find_child_by_type(getter_sig, "identifier")
                if name_node:
                    name = node_text(name_node, source)
                    # Find the function_body sibling
                    body = _find_next_sibling_by_type(node, "function_body")
                    start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                    symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "getter", class_name))
                continue

            # Check for setter_signature
            setter_sig = find_child_by_type(node, "setter_signature")
            if setter_sig:
                name_node = find_child_by_type(setter_sig, "identifier")
                if name_node:
                    name = node_text(name_node, source)
                    body = _find_next_sibling_by_type(node, "function_body")
                    start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                    symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "setter", class_name))
                continue

            # Check for constructor_signature (rare in method_signature)
            ctor_sig = find_child_by_type(node, "constructor_signature")
            if ctor_sig:  # pragma: no cover - constructor in method_signature
                name_parts = []
                for child in ctor_sig.children:
                    if child.type == "identifier":
                        name_parts.append(node_text(child, source))
                if name_parts:
                    name = ".".join(name_parts)
                    body = _find_next_sibling_by_type(node, "function_body")
                    start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                    symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "constructor", class_name))
                continue

            # Regular function_signature inside method_signature
            func_sig = find_child_by_type(node, "function_signature")
            if func_sig:
                name_node = find_child_by_type(func_sig, "identifier")
                if name_node:
                    name = node_text(name_node, source)
                    body = _find_next_sibling_by_type(node, "function_body")
                    start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                    sig = _extract_dart_signature(func_sig, source)
                    symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "method", class_name, signature=sig))
            continue

        # Top-level function_signature (not inside method_signature)
        if node.type == "function_signature" and (node.parent is None or node.parent.type != "method_signature"):
            class_name = _find_enclosing_class(node, source)
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source)
                # Find the function_body sibling
                body = _find_next_sibling_by_type(node, "function_body")
                start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                sig = _extract_dart_signature(node, source)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "function" if not class_name else "method", class_name, signature=sig))
            continue

        # Constructor signature at top level of class body (rare but possible)
        if node.type == "constructor_signature" and (node.parent is None or node.parent.type != "method_signature"):
            class_name = _find_enclosing_class(node, source)
            name_parts = []
            for child in node.children:
                if child.type == "identifier":
                    name_parts.append(node_text(child, source))
            if name_parts:
                name = ".".join(name_parts)
                body = _find_next_sibling_by_type(node, "function_body")
                start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "constructor", class_name))
            continue

    return symbols


def _find_enclosing_function(
    node: "tree_sitter.Node",
    function_scopes: dict[int, Symbol],
) -> Optional[Symbol]:
    """Find the function/method that contains this node."""
    current = node.parent
    while current:
        if current.type in ("function_signature", "function_body"):
            # Get the line number and look up
            line = current.start_point[0] + 1
            if line in function_scopes:
                return function_scopes[line]
            # Also check if this is part of a method_signature
            if current.parent and current.parent.type == "method_signature":  # pragma: no cover
                line = current.parent.start_point[0] + 1  # pragma: no cover
                if line in function_scopes:  # pragma: no cover
                    return function_scopes[line]  # pragma: no cover
        current = current.parent
    return None  # pragma: no cover - no enclosing function


def _extract_import_hints(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import statements for disambiguation.

    In Dart:
        import 'path' as prefix; -> prefix maps to path
        import 'path' show Name1, Name2; -> Name1 and Name2 map to path

    Returns a dict mapping short names/prefixes to full import paths.
    """
    hints: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import_specification":
            continue

        # Find the import path
        import_path: Optional[str] = None
        as_prefix: Optional[str] = None
        show_names: list[str] = []
        has_as = False

        for child in node.children:
            if child.type == "configurable_uri":
                # Extract path from uri > string_literal
                for sub in iter_tree(child):
                    if sub.type == "string_literal":
                        import_path = node_text(sub, source).strip("'\"")
                        break
            elif child.type == "as":
                has_as = True
            elif child.type == "identifier" and has_as:
                as_prefix = node_text(child, source)
            elif child.type == "combinator":
                # Look for show followed by identifiers
                is_show = False
                for sub in child.children:
                    if sub.type == "show":
                        is_show = True
                    elif sub.type == "identifier" and is_show:
                        show_names.append(node_text(sub, source))

        if import_path:
            if as_prefix:
                hints[as_prefix] = import_path
            for name in show_names:
                hints[name] = import_path

    return hints


def _extract_import_path(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract the import path from an import/export directive using iterative traversal."""
    for n in iter_tree(node):
        if n.type == "string_literal":
            text = node_text(n, source)
            # Remove quotes
            return text.strip("'\"")
    return None  # pragma: no cover - no string literal found


def _extract_param_types(
    node: "tree_sitter.Node", source: bytes
) -> dict[str, str]:
    """Extract parameter name -> type mapping from a Dart function signature.

    Dart function signatures look like:
        int add(int x, int y) { ... }
        void greet(String name) { ... }
        void process(Database db, {bool verbose = false}) { ... }

    Returns mapping like {"x": "int", "y": "int"} or {"db": "Database"}.
    """
    param_types: dict[str, str] = {}

    # Find the formal_parameter_list child
    params_node = find_child_by_type(node, "formal_parameter_list")
    if params_node is None:
        return param_types  # pragma: no cover - no params in function

    for child in params_node.children:
        if child.type == "formal_parameter":
            # formal_parameter contains type_identifier + identifier
            param_type: Optional[str] = None
            param_name: Optional[str] = None

            for subchild in child.children:
                if subchild.type == "type_identifier":
                    param_type = node_text(subchild, source)
                    # Strip generics: List<T> -> List (defensive - tree-sitter usually separates generics)
                    if "<" in param_type:  # pragma: no cover
                        param_type = param_type.split("<")[0]
                elif subchild.type == "identifier":
                    # First identifier after type is the parameter name
                    if param_type is not None and param_name is None:
                        param_name = node_text(subchild, source)

            if param_type and param_name:
                param_types[param_name] = param_type

        elif child.type == "optional_formal_parameters":
            # Handle optional/named parameters: {Database db, bool verbose}
            for opt_child in child.children:
                if opt_child.type == "formal_parameter":
                    param_type = None
                    param_name = None

                    for subchild in opt_child.children:
                        if subchild.type == "type_identifier":
                            param_type = node_text(subchild, source)
                            if "<" in param_type:  # pragma: no cover - defensive
                                param_type = param_type.split("<")[0]
                        elif subchild.type == "identifier":
                            if param_type is not None and param_name is None:
                                param_name = node_text(subchild, source)

                    if param_type and param_name:
                        param_types[param_name] = param_type

    return param_types


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: NameResolver,
    run_id: str,
    import_hints: dict[str, str] | None = None,
    method_resolver: ListNameResolver | None = None,
) -> list[Edge]:
    """Extract call, import, and instantiation edges from a parsed Dart file.

    Args:
        import_hints: Optional dict mapping short names to full import paths for disambiguation.
        method_resolver: Optional ListNameResolver for AMB-METHOD ambiguity guard.
    """
    if import_hints is None:  # pragma: no cover - defensive default
        import_hints = {}
    edges: list[Edge] = []
    file_id = make_file_id("dart", file_path)

    # Track functions by their enclosing scope
    function_scopes: dict[int, Symbol] = {}  # line -> symbol

    # Variable type tracking: variable_name -> class_name
    # Used for resolving method calls like db.save() to Database.save
    var_types: dict[str, str] = {}

    # First pass: map lines to their symbols
    for sym in file_symbols:
        if sym.kind in ("function", "method"):
            function_scopes[sym.span.start_line] = sym

    for node in iter_tree(tree.root_node):
        # Track parameter types from function declarations
        if node.type == "function_signature":
            param_types = _extract_param_types(node, source)
            var_types.update(param_types)

        # Track return types from function/method calls in variable declarations
        # Pattern: var client = getClient() or var client = factory.create()
        # AST: initialized_variable_definition > [inferred_type] identifier = identifier [selector(.method)] selector(args)
        if node.type == "initialized_variable_definition":
            children = list(node.children)
            var_name = None
            call_target = None
            call_method = None
            has_args = False
            after_eq = False

            for child in children:
                if child.type == "identifier":
                    if not after_eq:
                        var_name = node_text(child, source)
                    elif call_target is None:
                        call_target = node_text(child, source)
                elif child.type == "=":
                    after_eq = True
                elif child.type == "selector" and after_eq:
                    for sel_child in child.children:
                        if sel_child.type == "unconditional_assignable_selector":
                            for sub in sel_child.children:
                                if sub.type == "identifier":
                                    call_method = node_text(sub, source)
                        if sel_child.type in ("argument_part", "arguments"):
                            has_args = True
                    if any(
                        c.type in ("argument_part", "arguments")
                        for c in child.children
                    ):
                        has_args = True

            if var_name and call_target and has_args:
                resolved_sym = None
                if call_method and call_target in var_types:
                    # receiver.method() pattern
                    class_name = var_types[call_target]
                    qualified_name = f"{class_name}.{call_method}"
                    path_hint = import_hints.get(class_name)
                    lookup_result = resolver.lookup(
                        qualified_name, path_hint=path_hint
                    )
                    if lookup_result.found and lookup_result.symbol:
                        resolved_sym = lookup_result.symbol
                elif not call_method:
                    # Simple function call pattern
                    path_hint = import_hints.get(call_target)
                    lookup_result = resolver.lookup(
                        call_target, path_hint=path_hint
                    )
                    if lookup_result.found and lookup_result.symbol:
                        resolved_sym = lookup_result.symbol

                if resolved_sym and resolved_sym.kind in ("function", "method"):
                    ret_name = _extract_dart_return_type_name(
                        resolved_sym.signature
                    )
                    if ret_name:
                        # Verify return type class exists
                        class_result = resolver.lookup(ret_name)
                        if class_result.found:
                            var_types[var_name] = ret_name

        # Import/export directive
        if node.type == "import_or_export":
            import_path = _extract_import_path(node, source)
            if import_path:
                module_id = f"dart:{import_path}:0-0:module:module"
                edge = Edge.create(
                    src=file_id,
                    dst=module_id,
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="import",
                    confidence=0.95,
                )
                edges.append(edge)

        # Expression statement with function call pattern:
        # expression_statement > identifier + selector(s)
        # The AST can have multiple selectors: one for method name, one for arguments
        # Example: db.save('test') -> identifier('db') + selector('.save') + selector("('test')")
        if node.type == "expression_statement":
            children = list(node.children)
            first_ident = None
            method_name = None
            has_args = False

            for child in children:
                if child.type == "identifier" and first_ident is None:
                    first_ident = node_text(child, source)
                elif child.type == "selector":
                    # Check for method name in unconditional_assignable_selector
                    for sel_child in child.children:
                        if sel_child.type == "unconditional_assignable_selector":
                            for sub in sel_child.children:
                                if sub.type == "identifier":
                                    method_name = node_text(sub, source)
                                    break
                        # Check for arguments in this or any selector
                        if sel_child.type in ("argument_part", "arguments"):
                            has_args = True
                    # Also check for argument_part directly in selector children
                    if any(c.type == "argument_part" or c.type == "arguments" for c in child.children):
                        has_args = True

            if first_ident and has_args:
                caller = _find_enclosing_function(node, function_scopes)
                if caller:
                    if method_name and first_ident in var_types:
                        # Type-inferred method call: receiver.method()
                        class_name = var_types[first_ident]
                        qualified_name = f"{class_name}.{method_name}"
                        path_hint = import_hints.get(class_name)
                        lookup_result = resolver.lookup(qualified_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            callee = lookup_result.symbol
                            confidence = 0.85 * lookup_result.confidence
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="method_call_type_inferred",
                                confidence=confidence,
                            )
                            edges.append(edge)
                    elif not method_name:
                        # Simple function call: func()
                        # AMB-METHOD guard: when 3+ classes define the same
                        # method name, suppress the edge to avoid false positives.
                        path_hint = import_hints.get(first_ident)
                        if method_resolver is not None:
                            amb_check = method_resolver.lookup(
                                first_ident, path_hint=path_hint,
                            )
                            if not amb_check.found and amb_check.candidates:
                                continue  # 3+ method candidates, suppress
                        lookup_result = resolver.lookup(first_ident, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            callee = lookup_result.symbol
                            confidence = 0.85 * lookup_result.confidence
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="function_call",
                                confidence=confidence,
                            )
                            edges.append(edge)

        # Method call in selector (obj.method()) - complex AST pattern
        if node.type == "selector":  # pragma: no cover - method call detection
            for child in node.children:
                if child.type == "unconditional_assignable_selector":
                    method_name = None
                    for sub in child.children:
                        if sub.type == "identifier":
                            method_name = node_text(sub, source)
                    # Check if followed by argument_part
                    if method_name:
                        has_args = any(c.type == "argument_part" for c in node.children)
                        if has_args:
                            caller = _find_enclosing_function(node, function_scopes)
                            if caller:
                                path_hint = import_hints.get(method_name)
                                lookup_result = resolver.lookup(method_name, path_hint=path_hint)
                                if lookup_result.found and lookup_result.symbol:
                                    callee = lookup_result.symbol
                                    confidence = 0.80 * lookup_result.confidence
                                    edge = Edge.create(
                                        src=caller.id,
                                        dst=callee.id,
                                        edge_type="calls",
                                        line=node.start_point[0] + 1,
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                        evidence_type="method_call",
                                        confidence=confidence,
                                    )
                                    edges.append(edge)

        # Constructor invocation (ClassName() or new ClassName())
        if node.type in ("new_expression", "const_object_expression"):
            # Look for the type/class name
            for child in node.children:
                if child.type in ("type_identifier", "identifier"):
                    class_name = node_text(child, source)
                    caller = _find_enclosing_function(node, function_scopes)
                    if caller:
                        path_hint = import_hints.get(class_name)
                        lookup_result = resolver.lookup(class_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            callee = lookup_result.symbol
                            confidence = 0.90 * lookup_result.confidence
                            edge = Edge.create(
                                src=caller.id,
                                dst=callee.id,
                                edge_type="instantiates",
                                line=node.start_point[0] + 1,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                                evidence_type="constructor_call",
                                confidence=confidence,
                            )
                            edges.append(edge)

                    # Track variable type from constructor assignment
                    # Look for patterns like: var x = new ClassName() or final x = ClassName()
                    # AST: initialized_variable_definition > identifier + new_expression
                    parent = node.parent
                    if parent and parent.type == "initialized_variable_definition":
                        # Pattern: var x = new ClassName() or final x = new ClassName()
                        var_name_node = find_child_by_type(parent, "identifier")
                        if var_name_node:
                            var_name = node_text(var_name_node, source)
                            var_types[var_name] = class_name
                    break

        # Also detect ClassName() pattern (without new keyword) - look for type + arguments
        # This is a complex AST pattern that's hard to trigger reliably in tests
        if node.type == "primary":  # pragma: no cover - implicit constructor call
            children = list(node.children)
            for i, child in enumerate(children):
                if child.type == "type_identifier":
                    class_name = node_text(child, source)
                    # Check for following arguments
                    if i + 1 < len(children) and children[i + 1].type == "arguments":
                        caller = _find_enclosing_function(node, function_scopes)
                        if caller:
                            path_hint = import_hints.get(class_name)
                            lookup_result = resolver.lookup(class_name, path_hint=path_hint)
                            if lookup_result.found and lookup_result.symbol:
                                callee = lookup_result.symbol
                                confidence = 0.90 * lookup_result.confidence
                                edge = Edge.create(
                                    src=caller.id,
                                    dst=callee.id,
                                    edge_type="instantiates",
                                    line=node.start_point[0] + 1,
                                    origin=PASS_ID,
                                    origin_run_id=run_id,
                                    evidence_type="constructor_call",
                                    confidence=confidence,
                                )
                                edges.append(edge)
                    break

    return edges


class DartAnalyzer(TreeSitterAnalyzer):
    """Dart language analyzer using tree-sitter-language-pack."""

    lang = "dart"
    file_patterns: ClassVar[list[str]] = ["*.dart"]
    language_pack_name = "dart"
    create_file_symbols = True

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a Dart file."""
        analysis = FileAnalysis()
        symbols = _extract_symbols_from_file(tree, source, rel_path, run.execution_id)
        analysis.symbols = symbols
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        return analysis

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract import hints for disambiguation."""
        return _extract_import_hints(tree, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call, import, and instantiation edges from a Dart file."""
        # AMB-METHOD: build method resolver for ambiguity guard.
        # Dart's base register_symbol stores each symbol under its qualified
        # name only (no short-name duplicates), so no dedup needed.
        global_methods: dict[str, list[Symbol]] = {}
        for sym in global_symbols.values():
            if sym.kind == "method":
                short = sym.name.split(".")[-1] if "." in sym.name else sym.name
                global_methods.setdefault(short, []).append(sym)
        method_resolver = ListNameResolver(global_methods, ambiguity_threshold=3)

        file_symbols = list(local_symbols.values())
        return _extract_edges_from_file(
            tree, source, rel_path,
            file_symbols, resolver,
            run.execution_id, import_hints=import_aliases,
            method_resolver=method_resolver,
        )


_analyzer = DartAnalyzer()


def is_dart_tree_sitter_available() -> bool:
    """Check if tree-sitter with Dart grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("dart")
def analyze_dart(repo_root: Path) -> AnalysisResult:
    """Analyze Dart files in a repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-language-pack is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
