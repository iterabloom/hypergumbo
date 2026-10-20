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

``create_file_symbols = True`` makes the base class emit a ``file`` symbol per
``.tla`` file, so the EXTENDS/INSTANCE import edges (``src`` = the file id)
start from a real node. Reference edges are deduplicated per
(enclosing, referenced) pair, at confidence 0.80.

The enclosing operator/theorem/assumption of a reference is the symbol its
definition node declares, found by POSITION in this file. The referenced name
binds, in order, a LET-local operator in scope, a definition of THIS spec by
bare name, and only then the repo-wide resolver (INV-razus, WI-kosar): before,
``Bar == Init`` in spec B could be aimed at spec A's ``Init``.

Identity (WI-tusad): operators record their parameters in
``meta["parameters"]`` (an identifier or a higher-order ``G(_, _)``
declaration each count as one) and mint the ADR-0035 producer stable_id
through ``overload_arity.declared_arity_stable_id``. A LET-local operator keeps
its bare name and carries the operators it is defined in as its scope chain
(``A/f``), so ``LET f == ...`` inside ``A`` and inside ``B`` hash apart; before,
both shared the backstop floor key and were left null. Constants, theorems and
assumptions mint the same formula with no arity.

The base class handles grammar checking, parser creation, file discovery,
and result assembly.  This module provides only the TLA+-specific
extraction logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- Optional dependency keeps base install lightweight
- Uses tree-sitter-tlaplus package for grammar
- Two-pass allows cross-file resolution (by bare name; see the known gap above)
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
from typing import Any, TYPE_CHECKING, ClassVar, Optional

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
    symbol_declared_by,
    symbols_at,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, declared_arity, declared_arity_stable_id, parameter_entry,
)

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


#: Kinds minted here with the producer formula; ``module`` and ``variable``
#: take their table formulas downstream.
_TLA_PRODUCER_KINDS = frozenset({"operator", "constant", "theorem", "assumption"})


def _operator_parameters(
    node: "tree_sitter.Node", source: bytes,
) -> list[dict[str, object]]:
    """``meta["parameters"]`` of an ``operator_definition``.

    Each ``identifier`` between ``(`` and ``)`` is a parameter, and so is each
    ``operator_declaration`` (a higher-order ``G(_, _)``), named by its
    identifier.
    """
    entries: list[dict[str, object]] = []
    in_params = False
    for child in node.children:
        if child.type == "(" and not in_params:
            in_params = True
        elif child.type in (")", "def_eq"):
            break
        elif in_params and child.type == "identifier":
            entries.append(parameter_entry(node_text(child, source).strip()))
        elif in_params and child.type == "operator_declaration":
            name_node = find_child_by_type(child, "identifier")
            text = node_text(name_node if name_node else child, source).strip()
            entries.append(parameter_entry(text))
    return entries


def _operator_scope_chain(
    node: "tree_sitter.Node", source: bytes, name: str,
) -> str:
    """``A/f`` for an operator ``f`` defined in a LET inside operator ``A``."""
    chain = [name]
    current = node.parent
    while current is not None:
        if current.type == "operator_definition":
            outer = find_child_by_type(current, "identifier")
            if outer is not None:
                chain.append(node_text(outer, source).strip())
        current = current.parent
    return "/".join(reversed(chain))


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
        meta: dict[str, Any] | None = None,
        signature: Optional[str] = None,
    ) -> None:
        stable_id: Optional[str] = None
        if kind in _TLA_PRODUCER_KINDS:
            stable_id = declared_arity_stable_id(
                kind=kind, lang="tlaplus", path=file_path, name=name,
                qualified_name=_operator_scope_chain(node, source, name),
                arity=declared_arity(meta),
            )
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
            stable_id=stable_id,
            name=name,
            kind=kind,
            language="tlaplus",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
            meta=meta or None,
        )
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
                meta: dict[str, Any] = {
                    PARAMETERS_KEY: _operator_parameters(node, source),
                }
                # Check if inside local_definition
                if node.parent and node.parent.type == "local_definition":
                    meta["is_local"] = True
                # Check if RECURSIVE
                if name in recursive_names:
                    meta["is_recursive"] = True
                sig = _extract_operator_signature(node, source)
                add_symbol(node, name, "operator", meta, signature=sig)

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


def _local_operator(
    local_ops: dict[str, Symbol], node: "tree_sitter.Node", source: bytes,
    enclosing_name: str, ref_name: str,
) -> Optional[Symbol]:
    """The LET-local operator ``ref_name`` visible from definition ``node``.

    Tries ``<chain>/ref``, then each shorter prefix of the definition's chain:
    a LET binds its operators for the definition it sits in and everything
    nested inside it.
    """
    parts = _operator_scope_chain(node, source, enclosing_name).split("/")
    while parts:
        found = local_ops.get("/".join([*parts, ref_name]))
        if found is not None:
            return found
        parts.pop()
    return None


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
                ))

    # --- Reference edges ---
    # Build quick lookup: symbol name → symbol (from this file's symbols)
    seen_ref_pairs: set[tuple[str, str]] = set()
    decl_index = symbols_at(file_symbols)
    # LET-local operators by scope chain, so a reference to ``f`` inside ``A``
    # reaches ``A/f`` before the repo-wide bare-name lookup.
    local_ops: dict[str, Symbol] = {}
    for node in iter_tree(tree.root_node):
        if node.type == "operator_definition" and node.parent is not None \
                and node.parent.type == "let_in":
            sym = symbol_declared_by(node, decl_index)
            if sym is not None:
                local_ops[_operator_scope_chain(node, source, sym.name)] = sym
    # This spec's own definitions by bare name, LET-locals excluded (they are
    # reached through ``local_ops`` and are invisible outside their LET), so a
    # reference binds THIS spec's ``Init`` before another spec's.
    local_ids = {s.id for s in local_ops.values()}
    module_ops: dict[str, Symbol] = {}
    for declared in file_symbols:
        if declared.kind != "file" and declared.id not in local_ids:
            module_ops.setdefault(declared.name, declared)

    # Walk operator_definition, theorem, assumption nodes for body references
    for node in iter_tree(tree.root_node):
        if node.type not in ("operator_definition", "theorem", "assumption"):
            continue

        name_node = find_child_by_type(node, "identifier")
        if not name_node:
            continue  # pragma: no cover — unnamed theorems already skip
        enclosing_name = node_text(name_node, source).strip()

        # The definition's own symbol, by position: two LET-local ``f``s (or a
        # same-named operator in another spec) share the bare name.
        enclosing = symbol_declared_by(node, decl_index)
        if enclosing is None:
            continue  # pragma: no cover - every named definition is emitted

        # Walk all identifier_ref descendants of this node
        for desc in iter_tree(node):
            if desc.type != "identifier_ref":
                continue
            ref_name = node_text(desc, source).strip()
            if not ref_name or ref_name == enclosing_name:
                continue

            target = _local_operator(local_ops, node, source, enclosing_name, ref_name)
            if target is None:
                target = module_ops.get(ref_name)
            if target is None:
                target = resolver.lookup(ref_name).symbol
            if target is not None:
                pair = (enclosing.id, target.id)
                if pair not in seen_ref_pairs:
                    seen_ref_pairs.add(pair)
                    edges.append(Edge.create(
                        src=enclosing.id,
                        dst=target.id,
                        edge_type="references",
                        evidence_type="reference",
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
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import and reference edges from a TLA+ file."""
        return _extract_edges_from_file(
            tree, source, rel_path, self.file_symbols(local_symbols),
            resolver, run.execution_id,
        )


_analyzer = TLAPlusAnalyzer()


def is_tlaplus_tree_sitter_available() -> bool:
    """Check if tree-sitter with TLA+ grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("tlaplus")
def analyze_tlaplus(repo_root: Path) -> AnalysisResult:
    """Analyze TLA+ files in a repository."""
    return _analyzer.analyze(repo_root)
