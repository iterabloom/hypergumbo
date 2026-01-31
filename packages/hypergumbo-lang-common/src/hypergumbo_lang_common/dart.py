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
1. Check if tree-sitter with Dart grammar is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all symbols into global registry
   - Pass 2: Detect calls and resolve against global symbol registry
4. Detect function calls and import statements

Why This Design
---------------
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

PASS_ID = "dart-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_dart_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Dart files in the repository."""
    yield from find_files(repo_root, ["*.dart"])


def is_dart_tree_sitter_available() -> bool:
    """Check if tree-sitter with Dart grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language
        get_language("dart")
        return True
    except Exception:  # pragma: no cover - dart grammar not available
        return False


@dataclass
class DartAnalysisResult:
    """Result of analyzing Dart files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file.

    Stored during pass 1 and processed in pass 2 for cross-file resolution.
    """

    path: str
    source: bytes
    tree: object  # tree_sitter.Tree
    symbols: list[Symbol]
    import_hints: dict[str, str] = field(default_factory=dict)


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"dart:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Dart file node (used as import edge source)."""
    return f"dart:{path}:1-1:file:file"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


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
                return_type = _node_text(child, source).strip()
        elif child.type == "formal_parameter_list":
            # Extract parameters
            for param_child in child.children:
                if param_child.type == "formal_parameter":
                    param_text = _node_text(param_child, source).strip()
                    if param_text:
                        params.append(param_text)
                elif param_child.type == "optional_formal_parameters":
                    # Handle optional/named parameters
                    opt_text = _node_text(param_child, source).strip()
                    if opt_text:
                        # Replace default values with ...
                        import re
                        opt_text = re.sub(r'\s*=\s*[^,}\]]+', ' = ...', opt_text)
                        params.append(opt_text)

    sig = "(" + ", ".join(params) + ")"
    if return_type and return_type != "void":
        sig += f" {return_type}"
    return sig


def _find_enclosing_class(
    node: "tree_sitter.Node",
    source: bytes,
) -> Optional[str]:
    """Find the enclosing class/mixin/extension name for a given node."""
    current = node.parent
    while current:
        if current.type in ("class_definition", "mixin_declaration", "extension_declaration"):
            name_node = _find_child_by_type(current, "identifier")
            if name_node:
                return _node_text(name_node, source)
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
        sym_id = _make_symbol_id(file_path, start_line, end_line, full_name, kind)
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
        )

    for node in iter_tree(tree.root_node):
        # Class declaration
        if node.type == "class_definition":
            name_node = _find_child_by_type(node, "identifier")
            if name_node:
                name = _node_text(name_node, source)
                start_line, end_line, start_col, end_col = _get_combined_span(node, None)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "class"))
            continue

        # Mixin declaration
        if node.type == "mixin_declaration":
            name_node = _find_child_by_type(node, "identifier")
            if name_node:
                name = _node_text(name_node, source)
                start_line, end_line, start_col, end_col = _get_combined_span(node, None)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "mixin"))
            continue

        # Extension declaration
        if node.type == "extension_declaration":
            name_node = _find_child_by_type(node, "identifier")
            if name_node:
                name = _node_text(name_node, source)
                start_line, end_line, start_col, end_col = _get_combined_span(node, None)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "extension"))
            continue

        # Enum declaration
        if node.type == "enum_declaration":
            name_node = _find_child_by_type(node, "identifier")
            if name_node:
                name = _node_text(name_node, source)
                start_line, end_line, start_col, end_col = _get_combined_span(node, None)
                symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "enum"))
            continue

        # Method signature (inside class body) - contains function_signature, getter, or setter
        if node.type == "method_signature":
            class_name = _find_enclosing_class(node, source)

            # Check for getter_signature
            getter_sig = _find_child_by_type(node, "getter_signature")
            if getter_sig:
                name_node = _find_child_by_type(getter_sig, "identifier")
                if name_node:
                    name = _node_text(name_node, source)
                    # Find the function_body sibling
                    body = _find_next_sibling_by_type(node, "function_body")
                    start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                    symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "getter", class_name))
                continue

            # Check for setter_signature
            setter_sig = _find_child_by_type(node, "setter_signature")
            if setter_sig:
                name_node = _find_child_by_type(setter_sig, "identifier")
                if name_node:
                    name = _node_text(name_node, source)
                    body = _find_next_sibling_by_type(node, "function_body")
                    start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                    symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "setter", class_name))
                continue

            # Check for constructor_signature (rare in method_signature)
            ctor_sig = _find_child_by_type(node, "constructor_signature")
            if ctor_sig:  # pragma: no cover - constructor in method_signature
                name_parts = []
                for child in ctor_sig.children:
                    if child.type == "identifier":
                        name_parts.append(_node_text(child, source))
                if name_parts:
                    name = ".".join(name_parts)
                    body = _find_next_sibling_by_type(node, "function_body")
                    start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                    symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "constructor", class_name))
                continue

            # Regular function_signature inside method_signature
            func_sig = _find_child_by_type(node, "function_signature")
            if func_sig:
                name_node = _find_child_by_type(func_sig, "identifier")
                if name_node:
                    name = _node_text(name_node, source)
                    body = _find_next_sibling_by_type(node, "function_body")
                    start_line, end_line, start_col, end_col = _get_combined_span(node, body)
                    sig = _extract_dart_signature(func_sig, source)
                    symbols.append(make_symbol(start_line, end_line, start_col, end_col, name, "method", class_name, signature=sig))
            continue

        # Top-level function_signature (not inside method_signature)
        if node.type == "function_signature" and (node.parent is None or node.parent.type != "method_signature"):
            class_name = _find_enclosing_class(node, source)
            name_node = _find_child_by_type(node, "identifier")
            if name_node:
                name = _node_text(name_node, source)
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
                    name_parts.append(_node_text(child, source))
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
                        import_path = _node_text(sub, source).strip("'\"")
                        break
            elif child.type == "as":
                has_as = True
            elif child.type == "identifier" and has_as:
                as_prefix = _node_text(child, source)
            elif child.type == "combinator":
                # Look for show followed by identifiers
                is_show = False
                for sub in child.children:
                    if sub.type == "show":
                        is_show = True
                    elif sub.type == "identifier" and is_show:
                        show_names.append(_node_text(sub, source))

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
            text = _node_text(n, source)
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
    params_node = _find_child_by_type(node, "formal_parameter_list")
    if params_node is None:
        return param_types  # pragma: no cover - no params in function

    for child in params_node.children:
        if child.type == "formal_parameter":
            # formal_parameter contains type_identifier + identifier
            param_type: Optional[str] = None
            param_name: Optional[str] = None

            for subchild in child.children:
                if subchild.type == "type_identifier":
                    param_type = _node_text(subchild, source)
                    # Strip generics: List<T> -> List (defensive - tree-sitter usually separates generics)
                    if "<" in param_type:  # pragma: no cover
                        param_type = param_type.split("<")[0]
                elif subchild.type == "identifier":
                    # First identifier after type is the parameter name
                    if param_type is not None and param_name is None:
                        param_name = _node_text(subchild, source)

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
                            param_type = _node_text(subchild, source)
                            if "<" in param_type:  # pragma: no cover - defensive
                                param_type = param_type.split("<")[0]
                        elif subchild.type == "identifier":
                            if param_type is not None and param_name is None:
                                param_name = _node_text(subchild, source)

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
) -> list[Edge]:
    """Extract call, import, and instantiation edges from a parsed Dart file.

    Args:
        import_hints: Optional dict mapping short names to full import paths for disambiguation.
    """
    if import_hints is None:  # pragma: no cover - defensive default
        import_hints = {}
    edges: list[Edge] = []
    file_id = _make_file_id(file_path)

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
                    first_ident = _node_text(child, source)
                elif child.type == "selector":
                    # Check for method name in unconditional_assignable_selector
                    for sel_child in child.children:
                        if sel_child.type == "unconditional_assignable_selector":
                            for sub in sel_child.children:
                                if sub.type == "identifier":
                                    method_name = _node_text(sub, source)
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
                        path_hint = import_hints.get(first_ident)
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
                            method_name = _node_text(sub, source)
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
                    class_name = _node_text(child, source)
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
                        var_name_node = _find_child_by_type(parent, "identifier")
                        if var_name_node:
                            var_name = _node_text(var_name_node, source)
                            var_types[var_name] = class_name
                    break

        # Also detect ClassName() pattern (without new keyword) - look for type + arguments
        # This is a complex AST pattern that's hard to trigger reliably in tests
        if node.type == "primary":  # pragma: no cover - implicit constructor call
            children = list(node.children)
            for i, child in enumerate(children):
                if child.type == "type_identifier":
                    class_name = _node_text(child, source)
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


def analyze_dart(repo_root: Path) -> DartAnalysisResult:
    """Analyze Dart files in a repository.

    Returns a DartAnalysisResult with symbols, edges, and provenance.
    If tree-sitter for Dart is not available, returns a skipped result.
    """
    start_time = time.time()

    # Create analysis run for provenance
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    if not is_dart_tree_sitter_available():
        skip_reason = (
            "Dart analysis skipped: requires tree-sitter-language-pack "
            "(pip install tree-sitter-language-pack)"
        )
        warnings.warn(skip_reason)
        run.duration_ms = int((time.time() - start_time) * 1000)
        return DartAnalysisResult(
            run=run,
            skipped=True,
            skip_reason=skip_reason,
        )

    import tree_sitter
    from tree_sitter_language_pack import get_language

    DART_LANGUAGE = get_language("dart")
    parser = tree_sitter.Parser(DART_LANGUAGE)
    run_id = run.execution_id

    # Pass 1: Parse all files and extract symbols
    file_analyses: list[FileAnalysis] = []
    all_symbols: list[Symbol] = []
    global_symbol_registry: dict[str, Symbol] = {}
    files_analyzed = 0

    for dart_file in find_dart_files(repo_root):
        try:
            source = dart_file.read_bytes()
        except OSError:  # pragma: no cover
            continue

        tree = parser.parse(source)
        if tree.root_node is None:  # pragma: no cover - parser always returns root
            continue

        rel_path = str(dart_file.relative_to(repo_root))

        # Create file symbol
        file_symbol = Symbol(
            id=_make_file_id(rel_path),
            name="file",
            kind="file",
            language="dart",
            path=rel_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run_id,
        )
        all_symbols.append(file_symbol)

        # Extract symbols
        file_symbols = _extract_symbols_from_file(tree, source, rel_path, run_id)
        all_symbols.extend(file_symbols)

        # Extract import hints for disambiguation
        import_hints = _extract_import_hints(tree, source)

        # Register symbols globally (for cross-file resolution)
        for sym in file_symbols:
            global_symbol_registry[sym.name] = sym

        file_analyses.append(FileAnalysis(
            path=rel_path,
            source=source,
            tree=tree,
            symbols=file_symbols,
            import_hints=import_hints,
        ))
        files_analyzed += 1

    # Pass 2: Extract edges with cross-file resolution
    all_edges: list[Edge] = []
    resolver = NameResolver(global_symbol_registry)

    for fa in file_analyses:
        edges = _extract_edges_from_file(
            fa.tree,  # type: ignore
            fa.source,
            fa.path,
            fa.symbols,
            resolver,
            run_id,
            import_hints=fa.import_hints,
        )
        all_edges.extend(edges)

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return DartAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
