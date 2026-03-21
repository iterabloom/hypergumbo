# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pony language analyzer using tree-sitter.

Pony is an actor-model programming language with capabilities-secure type system,
reference capabilities for safe concurrency, and memory safety without a garbage
collector pause. It runs on the LLVM backend.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract actors, classes, interfaces, traits, primitives, methods, constructors
2. Pass 2: Extract call edges with registry lookup for resolution

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Pony-specific extraction
logic.

Symbols Extracted
-----------------
- **Actors**: Pony's concurrent entities (actor definitions)
- **Classes**: Regular class definitions
- **Interfaces**: Interface definitions (structural typing)
- **Traits**: Trait definitions (nominal typing)
- **Primitives**: Singleton value types
- **Constructors**: new constructors within types
- **Methods**: fun methods within types
- **Fields**: var/let field definitions

Edges Extracted
---------------
- **calls**: Method and constructor invocations

Why This Design
---------------
- Pony's actor model makes understanding message passing important
- Reference capabilities (iso, trn, ref, val, box, tag) are captured
- Interface/trait relationships help understand type hierarchies
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    make_unresolved_edge,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver


PASS_ID = make_pass_id("pony")

# Built-in Pony types and functions to filter
PONY_BUILTINS = frozenset({
    # Primitives
    "None", "Bool", "I8", "I16", "I32", "I64", "I128", "ILong", "ISize",
    "U8", "U16", "U32", "U64", "U128", "ULong", "USize", "F32", "F64",
    # Collections
    "Array", "String", "Map", "Set", "List", "Range",
    # System
    "Env", "StdStream", "FileAuth", "NetAuth", "DNSAuth",
    # Common functions
    "print", "println", "err", "out", "apply", "create", "size", "push",
    "pop", "values", "keys", "pairs", "string", "hash", "eq", "ne",
    "lt", "le", "gt", "ge", "add", "sub", "mul", "div", "rem", "neg",
    "op_and", "op_or", "op_xor", "op_not", "shl", "shr",
})


def find_pony_files(repo_root: Path) -> list[Path]:
    """Find all Pony files in the repository."""
    return sorted(find_files(repo_root, ["*.pony"]))


def is_pony_tree_sitter_available() -> bool:
    """Check if tree-sitter-pony is available."""
    return _analyzer._check_grammar_available()


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def _make_symbol_id(path: str, name: str, kind: str) -> str:
    """Create a stable symbol ID."""
    return f"pony:{path}:{kind}:{name}"


def _extract_parameters(node: "tree_sitter.Node") -> list[str]:
    """Extract parameter names from a parameters node."""
    params: list[str] = []
    for child in node.children:
        if child.type == "parameter":
            for param_child in child.children:
                if param_child.type == "identifier":
                    params.append(_get_node_text(param_child))
                    break
    return params


def _extract_constructor(
    node: "tree_sitter.Node", rel_path: str, current_type: Optional[str],
) -> Optional[Symbol]:
    """Extract a constructor (new)."""
    name = None
    params: list[str] = []

    for child in node.children:
        if child.type == "identifier":
            name = _get_node_text(child)
        elif child.type == "parameters":
            params = _extract_parameters(child)

    if not name:
        return None  # pragma: no cover

    full_name = f"{current_type}.{name}" if current_type else name
    symbol_id = _make_symbol_id(rel_path, full_name, "constructor")

    span = Span(
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    signature = f"new {name}({', '.join(params)})"

    return Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=full_name,
        kind="constructor",
        language="pony",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=signature,
        meta={"params": params, "parent_type": current_type},
    )


def _extract_method(
    node: "tree_sitter.Node", rel_path: str, current_type: Optional[str],
) -> Optional[Symbol]:
    """Extract a method (fun)."""
    name = None
    params: list[str] = []
    capability = ""

    for child in node.children:
        if child.type == "identifier":
            name = _get_node_text(child)
        elif child.type == "parameters":
            params = _extract_parameters(child)
        elif child.type == "capability":
            for cap_child in child.children:
                if cap_child.type in ("ref", "val", "box", "iso", "trn", "tag"):
                    capability = cap_child.type
                    break

    if not name:
        return None  # pragma: no cover

    full_name = f"{current_type}.{name}" if current_type else name
    symbol_id = _make_symbol_id(rel_path, full_name, "method")

    span = Span(
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    cap_str = f" {capability}" if capability else ""
    signature = f"fun{cap_str} {name}({', '.join(params)})"

    meta: dict = {"params": params, "parent_type": current_type}
    if capability:
        meta["capability"] = capability

    return Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=full_name,
        kind="method",
        language="pony",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=signature,
        meta=meta,
    )


def _extract_field(
    node: "tree_sitter.Node", rel_path: str, current_type: Optional[str],
) -> Optional[Symbol]:
    """Extract a field (var or let)."""
    name = None
    field_type = "var"

    for child in node.children:
        if child.type == "identifier":
            name = _get_node_text(child)
        elif child.type in ("var", "let"):
            field_type = child.type

    if not name:
        return None  # pragma: no cover

    # Skip underscore-prefixed private fields for cleaner output
    if name.startswith("_"):
        return None

    full_name = f"{current_type}.{name}" if current_type else name
    symbol_id = _make_symbol_id(rel_path, full_name, "field")

    span = Span(
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    return Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=full_name,
        kind="field",
        language="pony",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{field_type} {name}",
        meta={"field_type": field_type, "parent_type": current_type},
    )


def _extract_members(
    node: "tree_sitter.Node", rel_path: str, current_type: str,
    analysis: FileAnalysis, symbol_registry: dict[str, str],
) -> None:
    """Extract members (constructors, methods, fields) from a type."""
    for child in node.children:
        if child.type == "constructor":
            sym = _extract_constructor(child, rel_path, current_type)
            if sym:
                analysis.symbols.append(sym)
                symbol_registry[sym.name] = sym.id
                analysis.symbol_by_name[sym.name] = sym
        elif child.type == "method":
            sym = _extract_method(child, rel_path, current_type)
            if sym:
                analysis.symbols.append(sym)
                symbol_registry[sym.name] = sym.id
                analysis.symbol_by_name[sym.name] = sym
        elif child.type == "field":
            sym = _extract_field(child, rel_path, current_type)
            if sym:
                analysis.symbols.append(sym)


def _extract_type_definition(
    node: "tree_sitter.Node", rel_path: str,
    analysis: FileAnalysis, symbol_registry: dict[str, str],
) -> None:
    """Extract a type definition (actor, class, interface, trait, primitive)."""
    type_kind = node.type.replace("_definition", "")
    name = None

    for child in node.children:
        if child.type == "identifier":
            name = _get_node_text(child)
            break

    if not name:
        return  # pragma: no cover

    symbol_id = _make_symbol_id(rel_path, name, type_kind)

    span = Span(
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # Count members
    method_count = 0
    constructor_count = 0
    field_count = 0
    for child in node.children:
        if child.type == "members":
            for member in child.children:
                if member.type == "method":
                    method_count += 1
                elif member.type == "constructor":
                    constructor_count += 1
                elif member.type == "field":
                    field_count += 1

    meta = {
        "method_count": method_count,
        "constructor_count": constructor_count,
        "field_count": field_count,
    }

    symbol = Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=name,
        kind=type_kind,
        language="pony",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{type_kind} {name}",
        meta=meta,
    )
    analysis.symbols.append(symbol)
    symbol_registry[name] = symbol_id
    analysis.symbol_by_name[name] = symbol

    # Extract members
    for child in node.children:
        if child.type == "members":
            _extract_members(child, rel_path, name, analysis, symbol_registry)


def _extract_pony_symbols(
    node: "tree_sitter.Node", rel_path: str,
    analysis: FileAnalysis, symbol_registry: dict[str, str],
) -> None:
    """Extract symbols from a syntax tree node."""
    if node.type in ("actor_definition", "class_definition", "interface_definition",
                     "trait_definition", "primitive_definition"):
        _extract_type_definition(node, rel_path, analysis, symbol_registry)
        return  # Don't recurse - _extract_type_definition handles members

    for child in node.children:
        _extract_pony_symbols(child, rel_path, analysis, symbol_registry)


def _get_member_expression_name(node: "tree_sitter.Node") -> str:
    """Get the full name from a member expression (e.g., Counter.create)."""
    parts: list[str] = []
    _collect_member_parts(node, parts)
    return ".".join(parts)


def _collect_member_parts(node: "tree_sitter.Node", parts: list[str]) -> None:
    """Collect parts of a member expression."""
    for child in node.children:
        if child.type == "identifier":
            parts.append(_get_node_text(child))
        elif child.type == "member_expression":
            _collect_member_parts(child, parts)


def _extract_call_edge(
    node: "tree_sitter.Node", rel_path: str, run_id: str,
    symbol_registry: dict[str, str],
    current_method_id: Optional[str],
    current_type_id: Optional[str],
    current_type: Optional[str],
) -> Optional[Edge]:
    """Extract a call edge from a call expression."""
    callee_name = None

    for child in node.children:
        if child.type == "member_expression":
            callee_name = _get_member_expression_name(child)
        elif child.type == "identifier":
            callee_name = _get_node_text(child)

    if not callee_name:
        return None  # pragma: no cover

    # Extract the method name for filtering
    parts = callee_name.split(".")
    method_name = parts[-1] if parts else callee_name
    type_name = parts[0] if len(parts) > 1 else ""

    # Skip builtins
    if method_name in PONY_BUILTINS or type_name in PONY_BUILTINS:
        return None

    # Determine source
    src = current_method_id or current_type_id or f"pony:{rel_path}"

    # Try to resolve the target
    resolved_id = symbol_registry.get(callee_name)

    # Also try type.method format if we have a type
    if not resolved_id and len(parts) == 2:
        pass  # Already in type.method format
    elif not resolved_id and current_type:
        local_name = f"{current_type}.{callee_name}"
        resolved_id = symbol_registry.get(local_name)

    if resolved_id:
        return Edge.create(
            src=src,
            dst=resolved_id,
            edge_type="calls",
            line=node.start_point[0] + 1,
            origin=PASS_ID,
            origin_run_id=run_id,
            evidence_type="static",
            confidence=1.0,
            evidence_lang="pony",
        )
    else:
        return make_unresolved_edge(
            "pony", src, callee_name,
            node.start_point[0] + 1,
            PASS_ID, run_id,
        )


def _extract_pony_edges(
    node: "tree_sitter.Node", rel_path: str, run_id: str,
    symbol_registry: dict[str, str],
    current_type: Optional[str],
    current_type_id: Optional[str],
    current_method: Optional[str],
    current_method_id: Optional[str],
    edges_out: list[Edge],
) -> None:
    """Extract call edges from the syntax tree."""
    # Track current context
    if node.type in ("actor_definition", "class_definition", "interface_definition",
                     "trait_definition", "primitive_definition"):
        name = None
        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
                break
        new_type = name
        new_type_id = symbol_registry.get(name) if name else None
        for child in node.children:
            _extract_pony_edges(
                child, rel_path, run_id, symbol_registry,
                new_type, new_type_id, None, None, edges_out,
            )
        return

    if node.type in ("constructor", "method"):
        name = None
        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)
                break
        full_name = f"{current_type}.{name}" if current_type and name else name
        new_method_id = symbol_registry.get(full_name) if full_name else None
        for child in node.children:
            _extract_pony_edges(
                child, rel_path, run_id, symbol_registry,
                current_type, current_type_id,
                full_name, new_method_id, edges_out,
            )
        return

    if node.type == "call_expression":
        edge = _extract_call_edge(
            node, rel_path, run_id, symbol_registry,
            current_method_id, current_type_id, current_type,
        )
        if edge:
            edges_out.append(edge)

    for child in node.children:
        _extract_pony_edges(
            child, rel_path, run_id, symbol_registry,
            current_type, current_type_id,
            current_method, current_method_id, edges_out,
        )


class PonyAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Pony files using TreeSitterAnalyzer base class."""

    lang = "pony"
    file_patterns: ClassVar[list[str]] = ["*.pony"]
    language_pack_name = "pony"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract Pony symbols (actors, classes, methods, etc.)."""
        analysis = FileAnalysis()
        # Store a per-file symbol registry for edge extraction
        symbol_registry: dict[str, str] = {}
        _extract_pony_symbols(
            tree.root_node, rel_path, analysis, symbol_registry,
        )
        # Store the registry in import_aliases for Pass 2 access
        # We encode it as a special key
        analysis.import_aliases["__symbol_registry__"] = "present"
        return analysis

    def register_symbol(self, symbol: Symbol, global_symbols: dict) -> None:
        """Register symbol by name for cross-file resolution."""
        global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from a Pony file."""
        edges: list[Edge] = []

        # Build symbol_registry from global_symbols
        symbol_registry: dict[str, str] = {}
        for name, sym in global_symbols.items():
            if isinstance(sym, Symbol):
                symbol_registry[name] = sym.id

        _extract_pony_edges(
            tree.root_node, rel_path, run.execution_id,
            symbol_registry, None, None, None, None, edges,
        )
        return edges


_analyzer = PonyAnalyzer()


@register_analyzer("pony")
def analyze_pony(repo_root: Path) -> AnalysisResult:
    """Analyze Pony files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
