"""Apex language analyzer.

This module analyzes Salesforce Apex files (.cls, .trigger) using tree-sitter.
Apex is Salesforce's proprietary Java-like language for developing on the
Force.com platform.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
- Pass 1: Collect symbols (classes, interfaces, enums, methods, fields)
- Pass 2: Extract edges (method calls, static calls)

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Apex-specific extraction
logic.

Symbol Types
------------
- class: Class definitions (including abstract, virtual, with sharing)
- interface: Interface definitions
- enum: Enum definitions
- method: Method definitions
- constructor: Constructor definitions
- field: Field/property declarations

Edge Types
----------
- calls: Method calls from one symbol to another
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = "apex.tree_sitter"
PASS_VERSION = "0.1.0"

# Built-in Apex system classes and methods to filter out
APEX_BUILTINS = frozenset({
    "System", "Debug", "String", "Integer", "Boolean", "Decimal", "Double",
    "Long", "Date", "DateTime", "Time", "Blob", "Id", "Object", "List", "Set",
    "Map", "Database", "DML", "Test", "Assert", "Exception", "Math", "JSON",
    "Limits", "Schema", "Type", "UserInfo", "Crypto", "EncodingUtil", "URL",
    "PageReference", "ApexPages", "Messaging", "Email", "Trigger", "Approval",
    "Process", "Flow", "Batch", "Schedulable", "Queueable", "HttpRequest",
    "HttpResponse", "Http", "RestRequest", "RestResponse", "RestContext",
    # Common methods on builtins
    "debug", "assertEquals", "assertNotEquals", "assertTrue", "assertFalse",
    "format", "valueOf", "toString", "hashCode", "equals", "clone", "size",
    "get", "put", "add", "remove", "contains", "isEmpty", "clear", "keySet",
    "values", "entrySet", "addAll", "removeAll", "containsAll", "serialize",
    "deserialize", "deserializeStrict", "insert", "update", "delete", "upsert",
    "query", "countQuery", "getQueryLocator", "executeBatch", "schedule",
    "enqueue", "abortJob", "getErrorMessage",
})


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_stable_id(rel_path: str, name: str, kind: str) -> str:
    """Create a stable identifier for a symbol."""
    return f"apex:{rel_path}:{kind}:{name}"


def _extract_base_classes_apex(node: "tree_sitter.Node") -> list[str]:
    """Extract base classes/interfaces from Apex class declaration.

    Apex uses Java-like syntax:
        class Dog extends Animal implements Comparable { }

    The AST structure:
    - superclass node contains extends + type_identifier
    - interfaces node contains implements + type_list with type_identifiers
    """
    base_classes: list[str] = []

    for child in node.children:
        if child.type == "superclass":
            # Extract the base class
            for subchild in child.children:
                if subchild.type == "type_identifier":
                    base_classes.append(_get_node_text(subchild))
        elif child.type == "interfaces":
            # Extract implemented interfaces
            for subchild in child.children:
                if subchild.type == "type_list":
                    for type_node in subchild.children:
                        if type_node.type == "type_identifier":
                            base_classes.append(_get_node_text(type_node))

    return base_classes


def find_apex_files(repo_root: Path) -> list[Path]:
    """Find all Apex files in the repository."""
    cls_files = list(find_files(repo_root, ["*.cls"]))
    trigger_files = list(find_files(repo_root, ["*.trigger"]))
    return sorted(cls_files + trigger_files)


# ---------------------------------------------------------------------------
# Helper functions for symbol extraction
# ---------------------------------------------------------------------------


def _get_modifiers(node: "tree_sitter.Node") -> dict[str, bool]:
    """Extract modifiers from a modifiers node."""
    modifiers: dict[str, bool] = {}
    for child in node.children:
        if child.type == "modifiers":
            for mod in child.children:
                if mod.type == "modifier":
                    for m in mod.children:
                        mod_text = _get_node_text(m).lower()
                        if mod_text in ("public", "private", "protected", "global"):
                            modifiers["visibility"] = True
                            modifiers[mod_text] = True
                        elif mod_text == "static":
                            modifiers["static"] = True
                        elif mod_text == "abstract":
                            modifiers["abstract"] = True
                        elif mod_text == "virtual":
                            modifiers["virtual"] = True
                        elif mod_text == "override":
                            modifiers["override"] = True
                        elif mod_text in ("with", "without", "inherited"):  # pragma: no cover
                            # Sharing mode keywords (e.g., "with sharing")
                            pass
    return modifiers


def _extract_params(node: "tree_sitter.Node") -> list[str]:
    """Extract parameter names from a formal_parameters node."""
    params: list[str] = []
    for child in node.children:
        if child.type == "formal_parameter":
            param_type = None
            param_name = None
            for subchild in child.children:
                if subchild.type == "type_identifier":
                    param_type = _get_node_text(subchild)
                elif subchild.type == "generic_type":
                    param_type = _get_node_text(subchild)
                elif subchild.type == "identifier":
                    param_name = _get_node_text(subchild)
            if param_type and param_name:
                params.append(f"{param_type} {param_name}")
            elif param_name:  # pragma: no cover
                # Defensive: params usually have types in Apex
                params.append(param_name)
    return params


def _extract_method_symbol(
    node: "tree_sitter.Node", rel_path: str, current_class: Optional[str],
) -> Optional[Symbol]:
    """Extract a method symbol from a method_declaration node."""
    name = None
    return_type = None
    modifiers = _get_modifiers(node)
    params: list[str] = []

    for child in node.children:
        if child.type == "identifier":
            name = _get_node_text(child)
        elif child.type == "void_type":
            return_type = "void"
        elif child.type == "type_identifier":
            return_type = _get_node_text(child)
        elif child.type == "generic_type":
            return_type = _get_node_text(child)
        elif child.type == "formal_parameters":
            params = _extract_params(child)

    if not name:
        return None  # pragma: no cover

    qualified_name = f"{current_class}.{name}" if current_class else name

    meta: dict[str, object] = {}
    if return_type:
        meta["return_type"] = return_type
    if params:
        meta["params"] = params
    if modifiers.get("static"):
        meta["static"] = True
    if modifiers.get("public"):
        meta["visibility"] = "public"
    elif modifiers.get("private"):
        meta["visibility"] = "private"
    elif modifiers.get("protected"):
        meta["visibility"] = "protected"
    elif modifiers.get("global"):
        meta["visibility"] = "global"
    if modifiers.get("override"):
        meta["override"] = True
    if modifiers.get("virtual"):
        meta["virtual"] = True

    signature = f"{name}({', '.join(params)})"

    return Symbol(
        id=_make_stable_id(rel_path, qualified_name, "method"),
        stable_id=_make_stable_id(rel_path, qualified_name, "method"),
        name=qualified_name,
        kind="method",
        language="apex",
        path=rel_path,
        span=Span(
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        ),
        origin=PASS_ID,
        signature=signature,
        meta=meta if meta else {},
    )


def _extract_constructor_symbol(
    node: "tree_sitter.Node", rel_path: str, current_class: Optional[str],
) -> Optional[Symbol]:
    """Extract a constructor symbol from a constructor_declaration node."""
    name = None
    modifiers = _get_modifiers(node)
    params: list[str] = []

    for child in node.children:
        if child.type == "identifier":
            name = _get_node_text(child)
        elif child.type == "formal_parameters":
            params = _extract_params(child)

    if not name:
        return None  # pragma: no cover

    qualified_name = f"{current_class}.{name}" if current_class else name

    meta: dict[str, object] = {}
    if params:
        meta["params"] = params
    if modifiers.get("public"):
        meta["visibility"] = "public"
    elif modifiers.get("private"):
        meta["visibility"] = "private"
    elif modifiers.get("protected"):
        meta["visibility"] = "protected"
    elif modifiers.get("global"):
        meta["visibility"] = "global"

    signature = f"{name}({', '.join(params)})"

    return Symbol(
        id=_make_stable_id(rel_path, qualified_name, "constructor"),
        stable_id=_make_stable_id(rel_path, qualified_name, "constructor"),
        name=qualified_name,
        kind="constructor",
        language="apex",
        path=rel_path,
        span=Span(
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        ),
        origin=PASS_ID,
        signature=signature,
        meta=meta if meta else {},
    )


def _extract_field_symbols(
    node: "tree_sitter.Node", rel_path: str, current_class: Optional[str],
) -> list[Symbol]:
    """Extract field symbols from a field_declaration node."""
    symbols: list[Symbol] = []
    field_type = None
    modifiers = _get_modifiers(node)

    for child in node.children:
        if child.type == "type_identifier":
            field_type = _get_node_text(child)
        elif child.type == "generic_type":
            field_type = _get_node_text(child)
        elif child.type == "variable_declarator":
            name = None
            for subchild in child.children:
                if subchild.type == "identifier":
                    name = _get_node_text(subchild)
                    break

            if name:
                qualified_name = f"{current_class}.{name}" if current_class else name

                meta: dict[str, object] = {}
                if field_type:
                    meta["type"] = field_type
                if modifiers.get("static"):
                    meta["static"] = True
                if modifiers.get("public"):
                    meta["visibility"] = "public"
                elif modifiers.get("private"):
                    meta["visibility"] = "private"
                elif modifiers.get("protected"):
                    meta["visibility"] = "protected"
                elif modifiers.get("global"):
                    meta["visibility"] = "global"

                sym = Symbol(
                    id=_make_stable_id(rel_path, qualified_name, "field"),
                    stable_id=_make_stable_id(rel_path, qualified_name, "field"),
                    name=qualified_name,
                    kind="field",
                    language="apex",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta=meta if meta else {},
                )
                symbols.append(sym)
    return symbols


def _extract_class_body(
    body_node: "tree_sitter.Node", rel_path: str, current_class: str,
    analysis: FileAnalysis,
) -> None:
    """Extract members from a class body, recursing into inner classes."""
    for child in body_node.children:
        if child.type == "method_declaration":
            sym = _extract_method_symbol(child, rel_path, current_class)
            if sym:
                analysis.symbols.append(sym)
                analysis.symbol_by_name[sym.name] = sym
        elif child.type == "constructor_declaration":
            sym = _extract_constructor_symbol(child, rel_path, current_class)
            if sym:
                analysis.symbols.append(sym)
                analysis.symbol_by_name[sym.name] = sym
        elif child.type == "field_declaration":
            for sym in _extract_field_symbols(child, rel_path, current_class):
                analysis.symbols.append(sym)
                analysis.symbol_by_name[sym.name] = sym
        elif child.type == "class_declaration":
            _extract_class_node(child, rel_path, analysis)
        elif child.type == "interface_declaration":
            _extract_interface_node(child, rel_path, analysis)
        elif child.type == "enum_declaration":
            _extract_enum_node(child, rel_path, analysis)


def _extract_class_node(
    node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
) -> None:
    """Extract a class and its members."""
    name = None
    modifiers = _get_modifiers(node)

    for child in node.children:
        if child.type == "identifier":
            name = _get_node_text(child)
            break

    if name:
        meta: dict[str, object] = {}

        if modifiers.get("public"):
            meta["visibility"] = "public"
        elif modifiers.get("private"):
            meta["visibility"] = "private"
        elif modifiers.get("protected"):
            meta["visibility"] = "protected"
        elif modifiers.get("global"):
            meta["visibility"] = "global"

        if modifiers.get("abstract"):
            meta["abstract"] = True
        if modifiers.get("virtual"):
            meta["virtual"] = True

        base_classes = _extract_base_classes_apex(node)
        if base_classes:
            meta["base_classes"] = base_classes

        sym = Symbol(
            id=_make_stable_id(rel_path, name, "class"),
            stable_id=_make_stable_id(rel_path, name, "class"),
            name=name,
            kind="class",
            language="apex",
            path=rel_path,
            span=Span(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            ),
            origin=PASS_ID,
            meta=meta if meta else {},
        )
        analysis.symbols.append(sym)
        analysis.symbol_by_name[sym.name] = sym

        for child in node.children:
            if child.type == "class_body":
                _extract_class_body(child, rel_path, name, analysis)


def _extract_interface_node(
    node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
) -> None:
    """Extract an interface and its methods."""
    name = None
    modifiers = _get_modifiers(node)

    for child in node.children:
        if child.type == "identifier":
            name = _get_node_text(child)
            break

    if name:
        meta: dict[str, object] = {}

        if modifiers.get("public"):
            meta["visibility"] = "public"
        elif modifiers.get("private"):
            meta["visibility"] = "private"
        elif modifiers.get("global"):
            meta["visibility"] = "global"

        sym = Symbol(
            id=_make_stable_id(rel_path, name, "interface"),
            stable_id=_make_stable_id(rel_path, name, "interface"),
            name=name,
            kind="interface",
            language="apex",
            path=rel_path,
            span=Span(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            ),
            origin=PASS_ID,
            meta=meta if meta else {},
        )
        analysis.symbols.append(sym)
        analysis.symbol_by_name[sym.name] = sym

        for child in node.children:
            if child.type == "interface_body":
                for member in child.children:
                    if member.type == "method_declaration":
                        method_sym = _extract_method_symbol(member, rel_path, name)
                        if method_sym:
                            analysis.symbols.append(method_sym)
                            analysis.symbol_by_name[method_sym.name] = method_sym


def _extract_enum_node(
    node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
) -> None:
    """Extract an enum definition."""
    name = None
    modifiers = _get_modifiers(node)
    constants: list[str] = []

    for child in node.children:
        if child.type == "identifier":
            name = _get_node_text(child)
        elif child.type == "enum_body":
            for subchild in child.children:
                if subchild.type == "enum_constant":
                    for c in subchild.children:
                        if c.type == "identifier":
                            constants.append(_get_node_text(c))

    if name:
        meta: dict[str, object] = {"constants": constants} if constants else {}

        if modifiers.get("public"):
            meta["visibility"] = "public"
        elif modifiers.get("private"):
            meta["visibility"] = "private"
        elif modifiers.get("global"):
            meta["visibility"] = "global"

        sym = Symbol(
            id=_make_stable_id(rel_path, name, "enum"),
            stable_id=_make_stable_id(rel_path, name, "enum"),
            name=name,
            kind="enum",
            language="apex",
            path=rel_path,
            span=Span(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            ),
            origin=PASS_ID,
            meta=meta if meta else {},
        )
        analysis.symbols.append(sym)
        analysis.symbol_by_name[sym.name] = sym


def _extract_trigger_node(
    node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
) -> None:
    """Extract a trigger definition."""
    name = None
    sobject = None

    for child in node.children:
        if child.type == "identifier":
            if name is None:
                name = _get_node_text(child)
            else:
                sobject = _get_node_text(child)

    if name:
        meta: dict[str, object] = {}
        if sobject:
            meta["sobject"] = sobject

        sym = Symbol(
            id=_make_stable_id(rel_path, name, "trigger"),
            stable_id=_make_stable_id(rel_path, name, "trigger"),
            name=name,
            kind="trigger",
            language="apex",
            path=rel_path,
            span=Span(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            ),
            origin=PASS_ID,
            meta=meta if meta else {},
        )
        analysis.symbols.append(sym)
        analysis.symbol_by_name[sym.name] = sym


def _extract_symbols_recursive(
    node: "tree_sitter.Node", rel_path: str, analysis: FileAnalysis,
) -> None:
    """Recursively extract symbols from a syntax tree."""
    if node.type == "class_declaration":
        _extract_class_node(node, rel_path, analysis)
        return
    elif node.type == "interface_declaration":
        _extract_interface_node(node, rel_path, analysis)
        return
    elif node.type == "enum_declaration":
        _extract_enum_node(node, rel_path, analysis)
        return
    elif node.type == "trigger_declaration":
        _extract_trigger_node(node, rel_path, analysis)
        return

    for child in node.children:
        _extract_symbols_recursive(child, rel_path, analysis)


# ---------------------------------------------------------------------------
# Edge extraction helpers
# ---------------------------------------------------------------------------


def _extract_calls_from_block(
    node: "tree_sitter.Node", rel_path: str, src_name: str,
    current_class: Optional[str], symbol_registry: dict[str, str],
    run_id: str, edges: list[Edge],
) -> None:
    """Extract method calls from a code block."""
    if node.type == "method_invocation":
        _extract_method_call(
            node, rel_path, src_name, current_class,
            symbol_registry, run_id, edges,
        )
    elif node.type == "object_creation_expression":
        _extract_constructor_call(
            node, rel_path, src_name, symbol_registry, run_id, edges,
        )

    for child in node.children:
        _extract_calls_from_block(
            child, rel_path, src_name, current_class,
            symbol_registry, run_id, edges,
        )


def _extract_method_call(
    node: "tree_sitter.Node", rel_path: str, src_name: str,
    current_class: Optional[str], symbol_registry: dict[str, str],
    run_id: str, edges: list[Edge],
) -> None:
    """Extract a method call edge."""
    target = None
    method_name = None
    identifiers: list[str] = []

    for child in node.children:
        if child.type == "identifier":
            identifiers.append(_get_node_text(child))
        elif child.type == "this":
            target = "this"

    if len(identifiers) >= 2:
        target = identifiers[0]
        method_name = identifiers[1]
    elif len(identifiers) == 1:
        if target == "this":
            method_name = identifiers[0]
        else:
            method_name = identifiers[0]

    if method_name:
        if method_name in APEX_BUILTINS or (target and target in APEX_BUILTINS):
            return

        if target == "this" and current_class:
            call_name = f"{current_class}.{method_name}"
        elif target:
            call_name = f"{target}.{method_name}"
        else:
            if current_class:
                call_name = f"{current_class}.{method_name}"
            else:  # pragma: no cover
                call_name = method_name

        _add_call_edge(
            rel_path, src_name, call_name, node.start_point[0] + 1,
            symbol_registry, run_id, edges,
        )


def _extract_constructor_call(
    node: "tree_sitter.Node", rel_path: str, src_name: str,
    symbol_registry: dict[str, str], run_id: str, edges: list[Edge],
) -> None:
    """Extract a constructor call (new ClassName()) edge."""
    type_name = None

    for child in node.children:
        if child.type == "type_identifier":
            type_name = _get_node_text(child)
            break
        elif child.type == "generic_type":
            for subchild in child.children:
                if subchild.type == "type_identifier":
                    type_name = _get_node_text(subchild)
                    break
            break

    if type_name and type_name not in APEX_BUILTINS:
        call_name = f"{type_name}.{type_name}"
        _add_call_edge(
            rel_path, src_name, call_name, node.start_point[0] + 1,
            symbol_registry, run_id, edges,
        )


def _add_call_edge(
    rel_path: str, src_name: str, call_name: str, line: int,
    symbol_registry: dict[str, str], run_id: str, edges: list[Edge],
) -> None:
    """Add a call edge."""
    dst_id = symbol_registry.get(call_name)
    if not dst_id:
        short_name = call_name.split(".")[-1] if "." in call_name else call_name
        dst_id = symbol_registry.get(short_name)

    if dst_id:
        confidence = 1.0
        dst = dst_id
    else:
        confidence = 0.6
        dst = f"unresolved:{call_name}"

    src_id = symbol_registry.get(
        src_name, f"apex:{rel_path}:file"
    )

    edge = Edge.create(
        src=src_id,
        dst=dst,
        edge_type="calls",
        line=line,
        origin=PASS_ID,
        origin_run_id=run_id,
        evidence_type="tree_sitter",
        confidence=confidence,
        evidence_lang="apex",
    )
    edges.append(edge)


def _extract_edges_recursive(
    node: "tree_sitter.Node", rel_path: str,
    current_class: Optional[str], symbol_registry: dict[str, str],
    run_id: str, edges: list[Edge],
) -> None:
    """Recursively extract edges from a syntax tree."""
    if node.type == "class_declaration":
        for child in node.children:
            if child.type == "identifier":
                new_class = _get_node_text(child)
                for c in node.children:
                    _extract_edges_recursive(
                        c, rel_path, new_class, symbol_registry, run_id, edges,
                    )
                return

    elif node.type == "method_declaration":
        method_name = None
        for child in node.children:
            if child.type == "identifier":
                method_name = _get_node_text(child)
                break

        if method_name and current_class:
            src_name = f"{current_class}.{method_name}"
            for child in node.children:
                if child.type == "block":
                    _extract_calls_from_block(
                        child, rel_path, src_name, current_class,
                        symbol_registry, run_id, edges,
                    )

    elif node.type == "constructor_declaration":
        constructor_name = None
        for child in node.children:
            if child.type == "identifier":
                constructor_name = _get_node_text(child)
                break

        if constructor_name and current_class:
            src_name = f"{current_class}.{constructor_name}"
            for child in node.children:
                if child.type == "constructor_body":
                    _extract_calls_from_block(
                        child, rel_path, src_name, current_class,
                        symbol_registry, run_id, edges,
                    )

    for child in node.children:
        _extract_edges_recursive(
            child, rel_path, current_class, symbol_registry, run_id, edges,
        )


# ---------------------------------------------------------------------------
# ApexAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class ApexAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Salesforce Apex files using TreeSitterAnalyzer base class."""

    lang = "apex"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["*.cls", "*.trigger"]
    language_pack_name = "apex"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a single Apex file."""
        analysis = FileAnalysis()
        _extract_symbols_recursive(tree.root_node, rel_path, analysis)
        return analysis

    def register_symbol(
        self, symbol: Symbol, global_symbols: dict,
    ) -> None:
        """Register symbol by name and short name for cross-file resolution."""
        global_symbols[symbol.name] = symbol
        if "." in symbol.name:
            short_name = symbol.name.split(".")[-1]
            if short_name not in global_symbols:
                global_symbols[short_name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from a single Apex file."""
        edges: list[Edge] = []

        # Build symbol registry (name -> id) from global symbols
        symbol_registry: dict[str, str] = {}
        for sym_name, sym in global_symbols.items():
            symbol_registry[sym_name] = sym.id

        _extract_edges_recursive(
            tree.root_node, rel_path, None,
            symbol_registry, run.execution_id, edges,
        )
        return edges


_analyzer = ApexAnalyzer()


def is_apex_tree_sitter_available() -> bool:
    """Check if tree-sitter-apex is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("apex")
def analyze_apex(repo_root: Path) -> AnalysisResult:
    """Analyze Apex files in the repository.

    Args:
        repo_root: Root path of the repository to analyze

    Returns:
        AnalysisResult containing symbols and edges
    """
    return _analyzer.analyze(repo_root)
