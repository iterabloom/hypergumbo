# SPDX-License-Identifier: AGPL-3.0-or-later
"""TLA+ analysis pass using tree-sitter-tlaplus.

This analyzer uses tree-sitter to parse TLA+ files and extract:
- Module declarations
- Operator definitions (including LOCAL and RECURSIVE)
- Constant declarations
- Variable declarations
- Theorems (named only — unnamed are skipped)
- Assumptions (named only — unnamed are skipped)
- EXTENDS import edges
- INSTANCE import edges
- Body reference edges (operator→operator, theorem→symbol, etc.)

TLA+ is a formal specification language for modeling concurrent and
distributed systems, used alongside implementation code in repos like
AWS services, CockroachDB, and Cosmos/Tendermint.  Unlike typical
programming languages, "calls" are less meaningful than "references"
(dependencies between operators and theorems).  We model operator
dependencies as "references" edges rather than "calls".

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all symbols into global registry
2. Pass 2: Detect imports and references

The base class handles grammar checking, parser creation, file discovery,
and result assembly.  This module provides only the TLA+-specific
extraction logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- Optional dependency keeps base install lightweight
- Uses tree-sitter-tlaplus package for grammar
- Two-pass allows cross-file resolution
- References model fits formal specification languages better than calls

TLA+-Specific Considerations
----------------------------
- The grammar distinguishes ``identifier`` (declarations) from
  ``identifier_ref`` (references in expressions).  Pass 1 reads
  ``identifier`` nodes; Pass 2 reads ``identifier_ref`` nodes.
- Primed variables (``x'``) parse as sibling ``identifier_ref`` +
  ``prime`` nodes — no stripping required.
- LOCAL wraps operator_definition inside a ``local_definition`` node.
- RECURSIVE declares operator names in ``recursive_declaration`` before
  the actual ``operator_definition``.
- Parameterized operators have ``identifier`` children between ``(``
  and ``)`` tokens before ``def_eq``.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional

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

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("tlaplus")


def _make_module_id(module_name: str) -> str:
    """Generate ID for a TLA+ module (used as import edge target)."""
    return f"tlaplus:{module_name}:0-0:module:module"


def _extract_operator_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract parameter signature from a parameterized operator definition.

    Parameterized operators have identifier children between ( and )
    tokens before def_eq.  For ``Add(a, b) == a + b`` returns ``"(a, b)"``.
    For zero-parameter operators returns None.
    """
    params: list[str] = []
    in_params = False
    for child in node.children:
        if child.type == "(" and not in_params:
            in_params = True
            continue
        if child.type == "def_eq":
            break
        if child.type == ")" and in_params:
            break
        if in_params and child.type == "identifier":
            params.append(node_text(child, source).strip())
    if params:
        return "(" + ", ".join(params) + ")"
    return None


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed TLA+ file.

    Detects:
    - module: Module declarations (---- MODULE Foo ----)
    - operator: Operator definitions (Op == expr, Op(a,b) == expr)
    - constant: CONSTANT declarations
    - variable: VARIABLE declarations
    - theorem: Named THEOREM declarations
    - assumption: Named ASSUME declarations
    """
    symbols: list[Symbol] = []
    recursive_names: set[str] = set()

    def add_symbol(
        node: "tree_sitter.Node",
        name: str,
        kind: str,
        meta: dict | None = None,
        signature: Optional[str] = None,
    ) -> None:
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        span = Span(
            start_line=start_line,
            end_line=end_line,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        )
        sym_id = make_symbol_id("tlaplus", file_path, start_line, end_line, name, kind)
        sym = Symbol(
            id=sym_id,
            name=name,
            kind=kind,
            language="tlaplus",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
        )
        if meta:
            sym.meta = meta
        symbols.append(sym)

    # First pass: collect RECURSIVE names
    for node in iter_tree(tree.root_node):
        if node.type == "recursive_declaration":
            op_decl = find_child_by_type(node, "operator_declaration")
            if op_decl:
                name_node = find_child_by_type(op_decl, "identifier")
                if name_node:
                    recursive_names.add(node_text(name_node, source).strip())

    # Main pass: extract symbols
    for node in iter_tree(tree.root_node):
        if node.type == "module":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source).strip()
                add_symbol(node, name, "module")

        elif node.type == "operator_definition":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source).strip()
                meta: dict = {}
                # Check if inside local_definition
                if node.parent and node.parent.type == "local_definition":
                    meta["is_local"] = True
                # Check if RECURSIVE
                if name in recursive_names:
                    meta["is_recursive"] = True
                sig = _extract_operator_signature(node, source)
                add_symbol(node, name, "operator", meta or None, signature=sig)

        elif node.type == "constant_declaration":
            for child in node.children:
                if child.type == "identifier":
                    name = node_text(child, source).strip()
                    add_symbol(child, name, "constant")

        elif node.type == "variable_declaration":
            for child in node.children:
                if child.type == "identifier":
                    name = node_text(child, source).strip()
                    add_symbol(child, name, "variable")

        elif node.type == "theorem":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source).strip()
                add_symbol(node, name, "theorem")

        elif node.type == "assumption":
            name_node = find_child_by_type(node, "identifier")
            if name_node:
                name = node_text(name_node, source).strip()
                add_symbol(node, name, "assumption")

    return symbols


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_symbols: list[Symbol],
    resolver: "NameResolver",
    run_id: str,
) -> list[Edge]:
    """Extract import and reference edges from a parsed TLA+ file.

    Detects:
    - imports: EXTENDS statements (confidence 0.95)
    - imports: INSTANCE statements (confidence 0.90)
    - references: Body references in operators/theorems/assumptions (confidence 0.80)
    """
    edges: list[Edge] = []
    file_id = make_file_id("tlaplus", file_path)

    # --- Import edges ---
    for node in iter_tree(tree.root_node):
        if node.type == "extends":
            for child in node.children:
                if child.type == "identifier_ref":
                    module_name = node_text(child, source).strip()
                    edges.append(Edge.create(
                        src=file_id,
                        dst=_make_module_id(module_name),
                        edge_type="imports",
                        line=child.start_point[0] + 1,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        evidence_type="extends",
                        confidence=0.95,
                    ))

        elif node.type == "instance":
            # Top-level INSTANCE (not inside an operator)
            ref_node = find_child_by_type(node, "identifier_ref")
            if ref_node:
                module_name = node_text(ref_node, source).strip()
                edges.append(Edge.create(
                    src=file_id,
                    dst=_make_module_id(module_name),
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="instance",
                    confidence=0.90,
                ))

    # --- Reference edges ---
    # Build quick lookup: symbol name → symbol (from this file's symbols)
    seen_ref_pairs: set[tuple[str, str]] = set()

    # Walk operator_definition, theorem, assumption nodes for body references
    for node in iter_tree(tree.root_node):
        if node.type not in ("operator_definition", "theorem", "assumption"):
            continue

        name_node = find_child_by_type(node, "identifier")
        if not name_node:
            continue  # pragma: no cover — unnamed theorems already skip
        enclosing_name = node_text(name_node, source).strip()

        enclosing_result = resolver.lookup(enclosing_name)
        if not enclosing_result.symbol:
            continue  # pragma: no cover

        # Walk all identifier_ref descendants of this node
        for desc in iter_tree(node):
            if desc.type != "identifier_ref":
                continue
            ref_name = node_text(desc, source).strip()
            if not ref_name or ref_name == enclosing_name:
                continue

            ref_result = resolver.lookup(ref_name)
            if ref_result.symbol:
                pair = (enclosing_result.symbol.id, ref_result.symbol.id)
                if pair not in seen_ref_pairs:
                    seen_ref_pairs.add(pair)
                    edges.append(Edge.create(
                        src=enclosing_result.symbol.id,
                        dst=ref_result.symbol.id,
                        edge_type="references",
                        line=desc.start_point[0] + 1,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        confidence=0.80,
                    ))

    return edges


class TLAPlusAnalyzer(TreeSitterAnalyzer):
    """TLA+ language analyzer using tree-sitter-tlaplus."""

    lang = "tlaplus"
    file_patterns: ClassVar[list[str]] = ["*.tla"]
    grammar_module = "tree_sitter_tlaplus"
    create_file_symbols = True

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract module, operator, constant, variable, theorem, and assumption symbols."""
        analysis = FileAnalysis()
        symbols = _extract_symbols_from_file(tree, source, rel_path, run.execution_id)
        analysis.symbols = symbols
        for sym in symbols:
            analysis.symbol_by_name[sym.name] = sym
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import and reference edges from a TLA+ file."""
        return _extract_edges_from_file(
            tree, source, rel_path, [], resolver, run.execution_id,
        )


_analyzer = TLAPlusAnalyzer()


def is_tlaplus_tree_sitter_available() -> bool:
    """Check if tree-sitter with TLA+ grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("tlaplus")
def analyze_tlaplus(repo_root: Path) -> AnalysisResult:
    """Analyze TLA+ files in a repository."""
    return _analyzer.analyze(repo_root)
