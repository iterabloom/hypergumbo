# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pony language analyzer using tree-sitter.

Pony is an actor-model programming language with capabilities-secure type system,
reference capabilities for safe concurrency, and memory safety without a garbage
collector pause. It runs on the LLVM backend.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract actors, classes, interfaces, traits, primitives, methods,
   behaviours, constructors
2. Pass 2: Extract call edges with registry lookup for resolution. A call's
   source is the constructor / method / behaviour whose declaration contains
   it, else its type, found by POSITION in the file (INV-midag): every Pony
   program declares ``actor Main`` with ``new create``, so a name lookup in the
   repository-wide registry credited one program's calls to another's. A bare
   callee resolves to this file's declaration before the registry's.

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
- **Behaviours**: ``be`` declarations, an actor's asynchronous message
  handlers, as ``kind="method"`` with ``meta["is_behaviour"] = True`` and a
  ``be name(params)`` signature (WI-rokus; the registry has no behaviour kind)
- **Fields**: var/let field definitions whose name does not start with ``_``

Edges Extracted
---------------
- **calls**: Method and constructor invocations

Why This Design
---------------
- Pony's actor model makes message passing important: a behaviour is a
  symbol, and the calls in its body are its own. A message SEND
  (``worker.ping()``) is a call edge like any other; it resolves only when the
  receiver's spelling names the target (``Worker.ping``), since receivers are
  not typed
- Reference capabilities (iso, trn, ref, val, box, tag) are captured only for
  method (``fun``) receivers, as meta ``capability``
- Interface/trait relationships (``is`` clauses) are NOT emitted as edges;
  interfaces and traits appear only as symbols
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING, ClassVar, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    SymbolsAt,
    TreeSitterAnalyzer,
    enclosing_declared_symbol,
    make_doc_symbol_ids,
    make_unresolved_edge,
    symbols_at,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.analyze.base import node_own_text as _get_node_text

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

    span = Span(
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # node.id and stable_id are minted together by make_doc_symbol_ids;
    # node.id now carries start_line (INV-dulah).
    symbol_id, stable_id = make_doc_symbol_ids(
        "pony", str(rel_path), "constructor", full_name,
        span.start_line, span.end_line,
    )

    signature = f"new {name}({', '.join(params)})"

    return Symbol(
        id=symbol_id,
        stable_id=stable_id,
        name=full_name,
        kind="constructor",
        language="pony",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=signature,
        meta={"params": params, "parent_type": current_type},
        cyclomatic_complexity=compute_cyclomatic_complexity(node, "pony"),
        line_span=node.end_point[0] - node.start_point[0] + 1,
    )


def _extract_method(
    node: "tree_sitter.Node", rel_path: str, current_type: Optional[str],
) -> Optional[Symbol]:
    """Extract a method (``fun``) or a behaviour (``be``, a ``behavior`` node).

    A behaviour is the method an actor runs asynchronously when it receives
    the message of that name. The registry has no behaviour kind, so it is a
    ``method`` with ``meta["is_behaviour"] = True`` and a ``be`` signature
    (WI-rokus). It takes no receiver capability: a behaviour's is always
    ``tag``, implicitly.
    """
    is_behaviour = node.type == "behavior"
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

    span = Span(
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # node.id and stable_id are minted together by make_doc_symbol_ids;
    # node.id now carries start_line (INV-dulah).
    symbol_id, stable_id = make_doc_symbol_ids(
        "pony", str(rel_path), "method", full_name,
        span.start_line, span.end_line,
    )

    cap_str = f" {capability}" if capability else ""
    keyword = "be" if is_behaviour else "fun"
    signature = f"{keyword}{cap_str} {name}({', '.join(params)})"

    meta: dict[str, Any] = {"params": params, "parent_type": current_type}
    if capability:
        meta["capability"] = capability
    if is_behaviour:
        meta["is_behaviour"] = True

    return Symbol(
        id=symbol_id,
        stable_id=stable_id,
        name=full_name,
        kind="method",
        language="pony",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=signature,
        meta=meta,
        cyclomatic_complexity=compute_cyclomatic_complexity(node, "pony"),
        line_span=node.end_point[0] - node.start_point[0] + 1,
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

    span = Span(
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # node.id and stable_id are minted together by make_doc_symbol_ids;
    # node.id now carries start_line (INV-dulah).
    symbol_id, stable_id = make_doc_symbol_ids(
        "pony", str(rel_path), "field", full_name,
        span.start_line, span.end_line,
    )

    return Symbol(
        id=symbol_id,
        stable_id=stable_id,
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
    """Extract members (constructors, methods, behaviours, fields) from a type."""
    for child in node.children:
        if child.type == "constructor":
            sym = _extract_constructor(child, rel_path, current_type)
            if sym:
                analysis.symbols.append(sym)
                symbol_registry[sym.name] = sym.id
                analysis.symbol_by_name[sym.name] = sym
        elif child.type in ("method", "behavior"):
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

    span = Span(
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # node.id and stable_id are minted together by make_doc_symbol_ids;
    # node.id now carries start_line (INV-dulah).
    symbol_id, stable_id = make_doc_symbol_ids(
        "pony", str(rel_path), type_kind, name,
        span.start_line, span.end_line,
    )

    # Count members
    method_count = 0
    behaviour_count = 0
    constructor_count = 0
    field_count = 0
    for child in node.children:
        if child.type == "members":
            for member in child.children:
                if member.type == "method":
                    method_count += 1
                elif member.type == "behavior":
                    behaviour_count += 1
                elif member.type == "constructor":
                    constructor_count += 1
                elif member.type == "field":
                    field_count += 1

    meta = {
        "method_count": method_count,
        "behaviour_count": behaviour_count,
        "constructor_count": constructor_count,
        "field_count": field_count,
    }

    symbol = Symbol(
        id=symbol_id,
        stable_id=stable_id,
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


#: The type declarations: a call in one of them but in no member falls back to it.
_PONY_TYPE_NODES: frozenset[str] = frozenset({
    "actor_definition", "class_definition", "interface_definition",
    "trait_definition", "primitive_definition",
})

#: The members whose body is a call's enclosing callable (WI-rokus adds ``be``).
_PONY_CALLABLE_NODES: frozenset[str] = frozenset({"constructor", "method", "behavior"})


def _resolve_callee(
    name: str, file_table: dict[str, Symbol], symbol_registry: dict[str, str],
) -> Optional[str]:
    """This file's declaration of ``name`` first, then the repository's.

    The registry keeps ONE symbol per name, and every Pony program declares
    ``actor Main`` with ``new create``, so a bare ``helper()`` in one program
    resolved into another's ``Main.helper`` when the registry kept that one.
    """
    local = file_table.get(name)
    if local is not None:
        return local.id
    return symbol_registry.get(name)


def _extract_call_edge(
    node: "tree_sitter.Node", rel_path: str, run_id: str,
    symbol_registry: dict[str, str],
    file_table: dict[str, Symbol],
    decl_index: SymbolsAt,
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

    # The source: the member whose declaration CONTAINS the call, else its type,
    # found by POSITION in this file (INV-midag), never by name.
    enclosing = (
        enclosing_declared_symbol(node, decl_index, _PONY_CALLABLE_NODES)
        or enclosing_declared_symbol(node, decl_index, _PONY_TYPE_NODES)
    )
    src = enclosing.id if enclosing is not None else f"pony:{rel_path}"

    # Try to resolve the target
    resolved_id = _resolve_callee(callee_name, file_table, symbol_registry)

    # Also try type.method format if we have a type
    if not resolved_id and len(parts) == 2:
        pass  # Already in type.method format
    elif not resolved_id and current_type:
        # A member of the enclosing type. A Pony type is declared in ONE file,
        # so this file's table is the only place its members can be: the
        # registry's same-named entry is ANOTHER program's type (every program
        # declares ``actor Main``), never this one's.
        own = file_table.get(f"{current_type}.{callee_name}")
        resolved_id = own.id if own is not None else None

    if resolved_id:
        return Edge.create(
            src=src,
            dst=resolved_id,
            edge_type="calls",
            line=node.start_point[0] + 1,
            origin=PASS_ID,
            origin_run_id=run_id,
            evidence_type="static",
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
    file_table: dict[str, Symbol],
    decl_index: SymbolsAt,
    current_type: Optional[str],
    edges_out: list[Edge],
) -> None:
    """Extract call edges from the syntax tree.

    ``current_type`` (the enclosing type's name) only qualifies a bare callee
    (``helper()`` -> ``Main.helper``); the edge's source is found by position.
    """
    if node.type in _PONY_TYPE_NODES:
        current_type = None
        for child in node.children:
            if child.type == "identifier":
                current_type = _get_node_text(child)
                break

    if node.type == "call_expression":
        edge = _extract_call_edge(
            node, rel_path, run_id, symbol_registry, file_table,
            decl_index, current_type,
        )
        if edge:
            edges_out.append(edge)

    for child in node.children:
        _extract_pony_edges(
            child, rel_path, run_id, symbol_registry, file_table,
            decl_index, current_type, edges_out,
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

    def register_symbol(self, symbol: Symbol, global_symbols: dict[str, Symbol]) -> None:
        """Register symbol by name for cross-file resolution."""
        global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
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

        # This file's declarations by name: EVERY symbol (fields included, as
        # the registry holds them), where ``local_symbols`` keeps only the
        # callables and types.
        file_syms = self.file_symbols(local_symbols)
        file_table = {s.name: s for s in file_syms}
        _extract_pony_edges(
            tree.root_node, rel_path, run.execution_id,
            symbol_registry, file_table, symbols_at(file_syms), None, edges,
        )
        return edges


_analyzer = PonyAnalyzer()


@register_analyzer("pony", language_state="no_taxonomy_spec")  # WI-futin
def analyze_pony(repo_root: Path) -> AnalysisResult:
    """Analyze Pony files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
