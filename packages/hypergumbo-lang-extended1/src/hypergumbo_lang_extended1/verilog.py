# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verilog/SystemVerilog analysis pass using tree-sitter-verilog.

This analyzer uses tree-sitter to parse Verilog/SystemVerilog files and extract:
- Module definitions
- Interface definitions (SystemVerilog)
- Task and function declarations (``kind="task"`` / ``"function"``), wherever
  they are declared: a module, interface, package, program, class, checker,
  generate block or the compilation unit
- Net declarations (``kind="signal"``), one symbol per declared name
- Module instantiations, as ``instantiates`` edges (not symbols)

If tree-sitter-verilog is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all definitions
2. Pass 2: Place tasks and nets under their design unit, resolve module
   instantiations, and create edges

An instantiation's source is the enclosing ``module_declaration``, found by
position; one outside any module (e.g. inside an interface) is dropped. The
target type is looked up case-insensitively, preferring ``kind="module"``: a
unique match gets confidence 0.90, several candidates 0.50 with
``disambiguation_fallback``, and no match ``verilog:external:<type>:module`` at
0.70.

Only modules and interfaces are registered for that lookup
(``register_symbol``): a net or a task named like an undeclared module must
not capture its instantiations.

Members and their identity
--------------------------
A task, function or net is a member of what encloses it, and two modules of
one file routinely declare the same member name (``clk``, ``run``). So each
member mints ``overload_arity.scoped_stable_id`` over its scope chain
(ADR-0035 §1): the names of the enclosing modules, interfaces, packages,
programs, classes, checkers and LABELLED generate blocks, outermost first
(``_scope_label``). Deleting ``module a`` then leaves the ids of ``module
b``'s members where they were. Two same-name members under one chain (the
branches of an unlabelled generate ``if``, an ``ifdef`` pair) tie and are
ordered by the per-file split pass. No parameter count is hashed: Verilog has
no overloading, and a Verilog-1995 function declares its ports in its body.
Modules and interfaces keep the backstop's table formula, which this module
does not mint.

A net's symbol spans its own declarator, so ``wire w, z;`` is two symbols with
two spans. ``function new`` is a function named ``new``.

The containment linker gives a function its ``contains`` edge (from the
tightest enclosing module or interface, else the file), but ``task`` and
``signal`` are not in its containable set. Pass 2 emits theirs here with the
same rule, by span: from the innermost enclosing module or interface, else
from the file node (``evidence_type="enclosing_scope"``). A package, program,
class or checker is no symbol, so its members root at the file.

What the grammar decides
------------------------
tree-sitter-verilog reads some valid source as a syntax error and recovers
with nodes that look like declarations; a symbol is emitted only where the
node says what it is:

- A net needs a built-in net type keyword (``wire``, ``tri``, ``supply0``
  ...) or ``interconnect``. ``state_t st;`` (a variable of a user-defined
  type) and ``mynet n;`` (a net of a user-defined ``nettype``) parse as the
  same keyword-less ``net_declaration``, and so does recovery debris such as
  ``end generate``; none is emitted.
- A task or function whose name is followed by an error node is skipped: for
  an out-of-block ``task C::et`` the grammar gives the class ``C`` as the name.
- A prototype (an ``extern`` method, a modport ``import``) is no definition
  and emits nothing; a DPI ``import`` does not parse.
- ``reg`` / ``logic`` / ``integer`` variables are ``data_declaration`` nodes,
  not nets, and are not emitted (a bare task enable ``run;`` parses as one).

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Verilog-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- Optional dependency keeps base install lightweight
- Uses tree-sitter-verilog package for grammar
- Two-pass allows cross-file module resolution
- Hardware-specific: modules, interfaces, instantiations are first-class
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Iterator, Optional

from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    ancestor_labels,
    iter_tree,
    node_text,
    find_child_by_type,
    make_file_id,
    make_symbol_id,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import scoped_stable_id
from hypergumbo_core.symbol_indexes import SymbolByName

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("verilog")


def find_verilog_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Verilog/SystemVerilog files in the repository.

    The analyzer's own discovery (``_find_source_files``), so the two cannot drift.
    """
    yield from _analyzer._find_source_files(repo_root)


def _extract_module_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract module name from module_declaration node."""
    # Look for module_header -> simple_identifier
    header = find_child_by_type(node, "module_header")
    if header:
        for child in header.children:
            if child.type == "simple_identifier":
                return node_text(child, source)
    return None  # pragma: no cover


def _extract_interface_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract interface name from interface_declaration node."""
    # Look for interface_ansi_header -> interface_identifier -> simple_identifier
    for child in node.children:
        if child.type in ("interface_ansi_header", "interface_nonansi_header"):
            for subchild in child.children:
                if subchild.type == "interface_identifier":
                    ident = find_child_by_type(subchild, "simple_identifier")
                    if ident:
                        return node_text(ident, source)
                elif subchild.type == "simple_identifier":  # pragma: no cover
                    return node_text(subchild, source)  # pragma: no cover
        elif child.type == "interface_identifier":  # pragma: no cover
            ident = find_child_by_type(child, "simple_identifier")  # pragma: no cover
            if ident:  # pragma: no cover
                return node_text(ident, source)  # pragma: no cover
        elif child.type == "simple_identifier":  # pragma: no cover
            return node_text(child, source)  # pragma: no cover
    return None  # pragma: no cover


#: Kinds an instantiation can name; the only ones ``register_symbol`` indexes.
_DESIGN_UNIT_KINDS = frozenset({"module", "interface"})

#: Member kinds the containment linker does not place (its containable set
#: holds ``function`` and ``variable``, not ``task`` or ``signal``), so this
#: module emits their ``contains`` edge itself.
_SELF_PLACED_KINDS = frozenset({"task", "signal"})

#: Scope-opening nodes whose name is a direct identifier child, by node type.
_SCOPE_IDENTIFIER_TYPES = {
    "package_declaration": "package_identifier",
    "class_declaration": "class_identifier",
    "checker_declaration": "checker_identifier",
    "generate_block": "generate_block_identifier",
}

#: A callable declaration's ``(body node type, name node type, symbol kind)``.
_CALLABLE_DECLARATIONS = {
    "task_declaration": ("task_body_declaration", "task_identifier", "task"),
    "function_declaration": ("function_body_declaration", "function_identifier", "function"),
}


def _scope_label(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """The name ``node`` contributes to a member's scope chain, or ``None``.

    The ``label_of`` of :func:`ancestor_labels`: a module, interface, package,
    program, class, checker or labelled generate block names a scope; an
    unlabelled generate block and every other node do not.
    """
    if node.type == "module_declaration":
        return _extract_module_name(node, source)
    if node.type == "interface_declaration":
        return _extract_interface_name(node, source)
    if node.type == "program_declaration":
        for header in node.children:
            ident = find_child_by_type(header, "program_identifier")
            if ident:
                return node_text(ident, source)
        return None  # pragma: no cover - a program always has a header
    ident_type = _SCOPE_IDENTIFIER_TYPES.get(node.type)
    if ident_type is None:
        return None
    ident = find_child_by_type(node, ident_type)
    return node_text(ident, source) if ident else None


def _callable_name(
    node: "tree_sitter.Node", source: bytes, body_type: str, ident_type: str,
) -> Optional[str]:
    """The name a ``task_declaration`` / ``function_declaration`` declares.

    ``None`` when the name is followed by an error node: the grammar does not
    parse a scoped name, and for ``task C::et`` gives the class ``C`` as the
    task's name.
    """
    body = find_child_by_type(node, body_type)
    ident = find_child_by_type(body, ident_type) if body else None
    if ident is None:
        return None  # pragma: no cover - a declaration always names itself
    following = ident.next_sibling
    if following is not None and following.type == "ERROR":
        return None
    return node_text(ident, source) or None


def _net_declarators(node: "tree_sitter.Node") -> list["tree_sitter.Node"]:
    """The node of each name a ``net_declaration`` declares.

    Only a declaration led by a built-in net type keyword or ``interconnect``
    counts (see the module docstring). The declarator is what the symbol
    spans; its first ``simple_identifier`` (or itself, for ``interconnect``)
    is the name.
    """
    lead = node.children[0].type
    if lead == "net_type":
        decls = find_child_by_type(node, "list_of_net_decl_assignments")
        return [c for c in decls.children if c.type == "net_decl_assignment"] if decls else []
    if lead == "interconnect":
        return [c for c in node.children if c.type == "simple_identifier"]
    return []


def _span_bounds(symbol: Symbol) -> tuple[tuple[int, int], tuple[int, int]]:
    """``((start_line, start_col), (end_line, end_col))`` of a symbol's span."""
    assert symbol.span is not None  # every symbol of this analyzer has a span
    span = symbol.span
    return (span.start_line, span.start_col), (span.end_line, span.end_col)


def _enclosing_unit(member: Symbol, units: list[Symbol]) -> Optional[Symbol]:
    """The innermost of ``units`` (one file's modules and interfaces) whose
    span holds ``member``'s, or ``None`` when no unit does."""
    start, end = _span_bounds(member)
    best: Optional[Symbol] = None
    for unit in units:
        unit_start, unit_end = _span_bounds(unit)
        if unit_start <= start and end <= unit_end and (
            best is None or unit_start > _span_bounds(best)[0]
        ):
            best = unit
    return best


def _extract_instantiation_info(node: "tree_sitter.Node", source: bytes) -> Optional[tuple[str, str]]:
    """Extract module type and instance name from module_instantiation.

    Returns:
        Tuple of (module_type, instance_name) or None if not found
    """
    module_type = None
    instance_name = None

    for child in node.children:
        if child.type == "simple_identifier" and module_type is None:
            module_type = node_text(child, source)
        elif child.type == "hierarchical_instance":
            # Look for name_of_instance -> instance_identifier -> simple_identifier
            name_of_inst = find_child_by_type(child, "name_of_instance")
            if name_of_inst:
                inst_ident = find_child_by_type(name_of_inst, "instance_identifier")
                if inst_ident:
                    ident = find_child_by_type(inst_ident, "simple_identifier")
                    if ident:
                        instance_name = node_text(ident, source)

    if module_type and instance_name:
        return (module_type, instance_name)
    return None  # pragma: no cover


def _find_containing_module(
    node: "tree_sitter.Node", module_by_pos: dict[tuple[int, int], str]
) -> Optional[str]:
    """Walk up parents to find the containing module's symbol ID."""
    current = node.parent
    while current is not None:
        pos_key = (current.start_byte, current.end_byte)
        if pos_key in module_by_pos:
            return module_by_pos[pos_key]
        current = current.parent
    return None  # pragma: no cover - defensive


class VerilogAnalyzer(TreeSitterAnalyzer):
    """Verilog/SystemVerilog language analyzer using tree-sitter-verilog."""

    lang = "verilog"
    file_patterns: ClassVar[list[str]] = ["*.v", "*.sv", "*.vh", "*.svh"]
    grammar_module = "tree_sitter_verilog"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract module, interface, task, function and net symbols from a Verilog file."""
        analysis = FileAnalysis()

        def add(
            node: "tree_sitter.Node", name: str, kind: str, *, member_of: Optional[list[str]] = None,
        ) -> Symbol:
            """Append the symbol ``node`` declares. ``member_of`` is a member's
            scope chain; a design unit passes none and takes the backstop's id."""
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            sym = Symbol(
                id=make_symbol_id("verilog", rel_path, start_line, end_line, name, kind),
                stable_id=None if member_of is None else scoped_stable_id(
                    kind=kind, lang="verilog", path=rel_path, name=name, scope=member_of,
                ),
                shape_id=None,
                fingerprint=None,  # WI-vudul: central stamp_symbol_fingerprints owns this (was dead bare-hex)
                kind=kind,
                name=name,
                path=rel_path,
                language="verilog",
                span=Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
            )
            analysis.symbols.append(sym)
            return sym

        def scope_of(node: "tree_sitter.Node") -> list[str]:
            return ancestor_labels(node, lambda n: _scope_label(n, source))

        for node in iter_tree(tree.root_node):
            if node.type == "module_declaration":
                module_name = _extract_module_name(node, source)
                if module_name:
                    analysis.symbol_by_name[module_name] = add(node, module_name, "module")

            elif node.type == "interface_declaration":
                interface_name = _extract_interface_name(node, source)
                if interface_name:
                    analysis.symbol_by_name[interface_name] = add(
                        node, interface_name, "interface",
                    )

            elif node.type in _CALLABLE_DECLARATIONS:
                body_type, ident_type, kind = _CALLABLE_DECLARATIONS[node.type]
                callable_name = _callable_name(node, source, body_type, ident_type)
                if callable_name:
                    sym = add(node, callable_name, kind, member_of=scope_of(node))
                    analysis.node_for_symbol[sym.id] = node

            elif node.type == "class_constructor_declaration":
                add(node, "new", "function", member_of=scope_of(node))

            elif node.type == "net_declaration":
                for declarator in _net_declarators(node):
                    ident = (
                        declarator if declarator.type == "simple_identifier"
                        else find_child_by_type(declarator, "simple_identifier")
                    )
                    net_name = node_text(ident, source) if ident else ""
                    if net_name:
                        add(declarator, net_name, "signal", member_of=scope_of(declarator))

        return analysis

    def register_symbol(self, symbol: Symbol, global_symbols: dict[str, Any]) -> None:
        """Register Verilog symbols multi-value, case-insensitive (WI-sofaf).

        Pre-fix (INV-paroh): a single-value ``dict[str, Symbol]`` keyed on
        the lowercased symbol name overwrote on every collision. A
        ``module Foo`` followed by an ``interface Foo`` would land on the
        interface (last-write-wins); the inverse insert order would land
        on the module. ``extract_edges_from_file``'s instantiation
        resolution then routed to whichever survived, which is wrong in
        the interface-wins case.

        Post-fix: store every candidate per lowercase key as a list. The
        downstream lookup builds a :class:`SymbolByName` (case-insensitive)
        and applies ``prefer_kind="module"`` to land on the module
        deterministically regardless of insert order.

        Only design units are registered: the registry's one reader is that
        instantiation lookup, and a net, task or function named like an
        undeclared module must not become its target.
        """
        if symbol.kind in _DESIGN_UNIT_KINDS:
            global_symbols.setdefault(symbol.name.lower(), []).append(symbol)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Any],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract member ``contains`` and module instantiation edges from a Verilog file."""
        edges: list[Edge] = []

        file_symbols = self.file_symbols(local_symbols)
        units = [s for s in file_symbols if s.kind in _DESIGN_UNIT_KINDS]
        for member in file_symbols:
            if member.kind in _SELF_PLACED_KINDS:
                owner = _enclosing_unit(member, units)
                edges.append(Edge.create(
                    src=owner.id if owner else make_file_id("verilog", rel_path),
                    dst=member.id,
                    edge_type="contains",
                    evidence_type="enclosing_scope",
                    line=_span_bounds(member)[0][0],
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                ))

        # WI-sofaf / INV-paroh: build a kind-aware index so module
        # instantiations resolve to ``module`` candidates even when a
        # same-named interface / package / primitive coexists.
        sym_index = SymbolByName(case_insensitive=True)
        for candidates in global_symbols.values():
            for sym in candidates:
                sym_index.add(sym)

        # Build module_by_pos for parent walking
        module_by_pos: dict[tuple[int, int], str] = {}
        for node in iter_tree(tree.root_node):
            if node.type == "module_declaration":
                module_name = _extract_module_name(node, source)
                if module_name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    symbol_id = make_symbol_id("verilog", rel_path, start_line, end_line, module_name, "module")
                    module_by_pos[(node.start_byte, node.end_byte)] = symbol_id

        for node in iter_tree(tree.root_node):
            if node.type == "module_instantiation":
                current_module_id = _find_containing_module(node, module_by_pos)
                inst_info = _extract_instantiation_info(node, source)
                if inst_info and current_module_id:
                    module_type, _instance_name = inst_info
                    start_line = node.start_point[0] + 1

                    resolved = sym_index.lookup_one(module_type, prefer_kind="module")
                    edge_meta: Optional[dict] = None
                    if resolved is not None:
                        dst_sym, is_fallback = resolved
                        dst_id = dst_sym.id
                        # INV-zuhub fallback contract: when multiple
                        # candidates matched, mark the edge as a
                        # disambiguation fallback with confidence ≤ 0.5
                        # so downstream consumers can demote it.
                        if is_fallback:
                            confidence = 0.50
                            edge_meta = {"disambiguation_fallback": True}
                        else:
                            confidence = 0.90
                    else:
                        dst_id = f"verilog:external:{module_type}:module"
                        confidence = 0.70

                    edge = Edge.create(
                        src=current_module_id,
                        dst=dst_id,
                        edge_type="instantiates",
                        line=start_line,
                        confidence=confidence,
                        origin=PASS_ID,
                        evidence_type="verilog_instantiation",
                        meta=edge_meta,
                        origin_run_id=run.execution_id,
                    )
                    edges.append(edge)

        return edges


_analyzer = VerilogAnalyzer()


def is_verilog_tree_sitter_available() -> bool:
    """Check if tree-sitter with Verilog grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("verilog", find_files=find_verilog_files)
def analyze_verilog_files(repo_root: Path) -> AnalysisResult:
    """Analyze Verilog/SystemVerilog files in the repository."""
    return _analyzer.analyze(repo_root)
