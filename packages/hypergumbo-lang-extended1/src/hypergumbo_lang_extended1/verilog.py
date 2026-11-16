# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verilog/SystemVerilog analysis pass using tree-sitter-verilog.

This analyzer uses tree-sitter to parse Verilog/SystemVerilog files and extract:
- Module definitions
- Interface definitions (SystemVerilog)
- Task and function declarations (``kind="task"`` / ``"function"``), wherever
  they are declared: a module, interface, package, program, class, checker,
  generate block or the compilation unit
- Net declarations (``kind="signal"``), one symbol per declared name
- Module and interface instantiations, as ``instantiates`` edges (not symbols)
- Calls to tasks and functions, as ``calls`` edges, for the call forms the
  grammar parses

If tree-sitter-verilog is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all definitions
2. Pass 2: Place tasks and nets under their design unit, resolve
   instantiations and calls, and create edges
3. Post-process: mark the top-level modules as entrypoints

Instantiations
--------------
An instantiation's source is the innermost enclosing module or interface,
found by position; one outside both (in a module the grammar could not parse)
is dropped. The target type is looked up case-insensitively, preferring
``kind="module"`` (``"interface"`` when the source is an interface, which can
instantiate nothing else): a unique match gets confidence 0.90, several
candidates 0.50 with ``disambiguation_fallback``, and no match
``verilog:external:<type>:module`` at 0.70.

Only modules and interfaces are registered for that lookup
(``register_symbol``): a net or a task named like an undeclared module must
not capture its instantiations.

tree-sitter-verilog decides the node type from the port list, not from what
is instantiated (``_INSTANTIATIONS``):

- ``module_instantiation`` (``interface_instantiation`` in an interface
  body): two or more named ports, a ``#(...)`` parameter list, or several
  instances in one statement.
- ``checker_instantiation``: one instance with no port, one named or
  positional port, ``.*`` or an implicit ``.x``, with or without an array
  range.
- ``udp_instantiation``: one instance with two or more positional ports.

The last two are also what a real checker or UDP instance parses as, so the
type name decides. A name the repository declares as a module or interface
is an instantiation of it. Otherwise a name it declares as a checker, a
program or a UDP (``primitive``) in any file is not a module instantiation
and emits nothing (pass 1 records those names in ``_other_units``; they are
not symbols). A name declared nowhere is an external module, exactly as for
``module_instantiation``: the port list must not decide whether an edge
exists. Three shapes of ``checker_instantiation`` are never a module
(``_instantiated_type``): one with no instance name (how a task enable
``initial t2(w, z);`` parses), one in procedural code, and one whose type is
package-scoped. Gate instances (``and g1(...)``) are ``gate_instantiation``
and emit nothing.

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

Calls
-----
Without ``calls`` edges every function this module emits reads as dead code,
so pass 2 resolves the call forms tree-sitter-verilog parses:

- ``f(a)`` (``tf_call``) and ``f()`` (a ``let_expression``): the name is
  looked up in each enclosing scope of the file, innermost first (generate
  block, module / interface / program / class / package, compilation unit).
  At each scope the scope's own declaration wins, then a name it imports
  explicitly (``import pkg::f;``), then one a wildcard import supplies
  (``import pkg::*;``); an import in a module header belongs to the module.
  Scopes are keyed by position, so an ``ifdef`` pair of ``module dup`` is two.
  The walk stops at a class that ``extends`` another: the name may be
  inherited.
- ``pkg::f(...)`` / ``C::m(...)`` (a ``let_expression`` with a
  ``package_scope``): the one ``f`` a package, else a class, of that name
  declares, in any file (pass 1 records those in ``_RunRecord``).
- ``this.m(...)`` (``method_call``): the enclosing class's own ``m``.

The edge's source is the innermost task or function around the call, else
the module or interface, else the file node (a package or class body, or a
module the grammar lost). A callee that resolves to no single declaration
emits no edge: a system task or function (``$display``, ``$clog2``; a
different node type), a name declared nowhere in reach (a function pasted in
by an ``include``), two declarations of one name in one scope, a method call
on an instance, an object or an interface port. Not parsed as calls at all,
so never an edge: a task enable (``t2(w, z);`` is a syntax error, a bare
``run;`` a declaration), a call in a parameter initializer, ``void'(f(x))``,
``super.m(x)``, a function called without parentheses.

A task or function is ``is_exported`` by where it is declared
(``_EXPORTED_BY_SCOPE``): a package's are importable by every design unit, so
exported; a module's, program's or checker's are local, so not; for an
interface, a class and the compilation unit the field stays ``None``.

A module no ``instantiates`` edge reaches is a top-level module (IEEE 1800
§23.3.1): it is elaborated without being instantiated, so its body runs.
``post_process`` gives it the ``entrypoint`` concept, which makes it a seed of
reachability walks; an instantiated module is reached through its
``instantiates`` edge. This over-approximates where an instantiation resolved
to another same-named module or sits in a module the grammar lost.

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

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Iterator, Optional

from hypergumbo_core.ir import Edge, Span, Symbol, UsageContext, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    ancestor_labels,
    enclosing_declared_symbol,
    iter_tree,
    node_text,
    find_child_by_type,
    make_file_id,
    make_symbol_id,
    symbol_declared_by,
    symbols_at,
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

#: The declaration nodes of those kinds.
_UNIT_DECLARATIONS = frozenset({"module_declaration", "interface_declaration"})

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


#: Every node that declares a task or function symbol.
_CALLABLE_NODE_TYPES = frozenset({*_CALLABLE_DECLARATIONS, "class_constructor_declaration"})

#: The kinds those nodes declare: what a call can reach.
_CALLABLE_KINDS = frozenset({"task", "function"})

#: Nodes whose symbol a call inside them is anchored on, innermost first.
_CALL_SOURCE_NODE_TYPES = _CALLABLE_NODE_TYPES | _UNIT_DECLARATIONS

#: The call forms the grammar parses (see "Calls" in the module docstring).
_CALL_NODE_TYPES = frozenset({"tf_call", "let_expression", "method_call"})

#: Nodes that open a lexical scope. Unlike the identity scope chain
#: (``_scope_label``), an unlabelled generate block counts: name lookup needs
#: where a declaration is, not what it is called.
_SCOPE_NODE_TYPES = frozenset({
    "module_declaration", "interface_declaration", "program_declaration",
    "package_declaration", "class_declaration", "checker_declaration",
    "generate_block",
})

#: Scopes a ``name::`` qualifier can name, and how the run record tags them.
_QUALIFIER_SCOPES = {"package_declaration": "package", "class_declaration": "class"}

#: ``is_exported`` of a task or function by the design unit declaring it. A
#: package's items are importable by every design unit; a module's, program's
#: or checker's are local to it. An interface, a class and the compilation
#: unit are absent: no rule, so ``None``.
_EXPORTED_BY_SCOPE = {
    "package_declaration": True,
    "module_declaration": False,
    "program_declaration": False,
    "checker_declaration": False,
}

#: The concept a top-level module carries; ``entrypoints`` reads it.
_TOP_MODULE_CONCEPT = {"concept": "entrypoint", "framework": "verilog"}


@dataclass
class _RunRecord:
    """What pass 1 saw across the repository that no symbol carries.

    ``other_units``: lowercased names declared as a checker, program or UDP.
    ``scoped_callables``: the tasks and functions declared directly in a
    package or a class, by ``(scope tag, scope name, callable name)``: the
    targets of ``pkg::f``, ``C::m`` and an ``import``.
    """

    other_units: set[str] = field(default_factory=set)
    scoped_callables: dict[tuple[str, str, str], list[Symbol]] = field(default_factory=dict)


def _scope_path(node: "tree_sitter.Node") -> list["tree_sitter.Node"]:
    """The scope-opening ancestors of ``node``, outermost first."""
    path = []
    current = node.parent
    while current is not None:
        if current.type in _SCOPE_NODE_TYPES:
            path.append(current)
        current = current.parent
    path.reverse()
    return path


def _scope_key(path: list["tree_sitter.Node"]) -> tuple[int, ...]:
    """A scope's identity within its file: where each enclosing scope starts.

    By position, not by name: an ``ifdef`` pair of ``module dup`` is two
    scopes (INV-mozas).
    """
    return tuple(scope.start_byte for scope in path)


def _callable_exported(path: list["tree_sitter.Node"]) -> Optional[bool]:
    """``is_exported`` of a task or function declared at ``path``: the rule of
    the innermost design unit around it (a generate block is not one)."""
    for scope in reversed(path):
        if scope.type != "generate_block":
            return _EXPORTED_BY_SCOPE.get(scope.type)
    return None


def _call_target(
    node: "tree_sitter.Node", source: bytes,
) -> Optional[tuple[Optional[str], str, bool]]:
    """``(qualifier, name, on_this)`` of a ``_CALL_NODE_TYPES`` node, or
    ``None`` for a call whose callee the source does not name: a method call
    on anything but ``this`` (an instance, an interface port, an object)."""
    if node.type == "method_call":
        body = find_child_by_type(node, "method_call_body")
        ident = find_child_by_type(body, "method_identifier") if body else None
        if ident is None or node_text(node.children[0], source) != "this":
            return None
        return None, node_text(ident, source), True
    ident = find_child_by_type(node, "simple_identifier")
    if ident is None:
        return None  # pragma: no cover - both call nodes lead with the name
    scope = find_child_by_type(node, "package_scope")
    package = find_child_by_type(scope, "package_identifier") if scope else None
    return node_text(package, source) if package else None, node_text(ident, source), False


def _only(candidates: Optional[list[Symbol]]) -> Optional[Symbol]:
    """The one candidate, or ``None`` for none or several: two declarations
    of one name in one scope (an ``ifdef`` pair) are not chosen between."""
    return candidates[0] if candidates and len(candidates) == 1 else None


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


#: An instantiation node's ``(type-name node type, node type holding
#: name_of_instance)``; ``None`` when the instantiation holds the name itself.
_INSTANTIATIONS: dict[str, tuple[str, Optional[str]]] = {
    "module_instantiation": ("simple_identifier", "hierarchical_instance"),
    "interface_instantiation": ("interface_identifier", "hierarchical_instance"),
    "checker_instantiation": ("checker_identifier", None),
    "udp_instantiation": ("simple_identifier", "udp_instance"),
}

#: The node types the grammar also gives a real checker or UDP instance, so
#: the type name decides (see "Instantiations" in the module docstring).
_AMBIGUOUS_INSTANTIATIONS = frozenset({"checker_instantiation", "udp_instantiation"})

#: Declarations an ambiguous instantiation may name instead of a module.
_OTHER_UNIT_DECLARATIONS = frozenset({
    "checker_declaration", "program_declaration", "udp_declaration",
})


def _other_unit_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """The name a checker, program or UDP (``primitive``) declaration declares."""
    if node.type == "udp_declaration":
        header = node.children[0]
        ident = find_child_by_type(header, "simple_identifier")
        return node_text(ident, source) if ident else None
    return _scope_label(node, source)


def _instantiated_type(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """The type name of a named instance, from an ``_INSTANTIATIONS`` node.

    ``None`` when the node is no instantiation of a design unit:

    - it names no instance (an unnamed UDP instance; a task enable such as
      ``initial t2(w, z);``, which the grammar reads as a checker
      instantiation with a missing instance name);
    - it is a checker instantiation in procedural code (a statement), where
      no module or interface can be instantiated;
    - its type name is package-scoped (``pkg::c u(...)``): a package holds
      checkers, never modules or interfaces.
    """
    type_node_type, holder_type = _INSTANTIATIONS[node.type]
    type_node = find_child_by_type(node, type_node_type)
    if type_node is None:
        return None  # pragma: no cover - an instantiation always names a type
    if holder_type is None:
        parent_type = node.parent.type if node.parent is not None else ""
        if parent_type == "statement_item" or find_child_by_type(node, "package_scope"):
            return None
        holders = [node]
    else:
        holders = [c for c in node.children if c.type == holder_type]
    for holder in holders:
        name_node = find_child_by_type(holder, "name_of_instance")
        if name_node is not None and node_text(name_node, source):
            return node_text(type_node, source) or None
    return None


def _find_containing_unit(
    node: "tree_sitter.Node", unit_by_pos: dict[tuple[int, int], Symbol]
) -> Optional[Symbol]:
    """Walk up parents to the innermost module or interface holding ``node``."""
    current = node.parent
    while current is not None:
        unit = unit_by_pos.get((current.start_point[0] + 1, current.start_point[1]))
        if unit is not None and current.type in _UNIT_DECLARATIONS:
            return unit
        current = current.parent
    return None


class VerilogAnalyzer(TreeSitterAnalyzer):
    """Verilog/SystemVerilog language analyzer using tree-sitter-verilog."""

    lang = "verilog"
    file_patterns: ClassVar[list[str]] = ["*.v", "*.sv", "*.vh", "*.svh"]
    grammar_module = "tree_sitter_verilog"

    #: Filled by pass 1, read by pass 2, reset around each ``analyze`` run.
    _record: Optional[_RunRecord] = None

    def analyze(self, repo_root: Path, max_files: Optional[int] = None) -> AnalysisResult:
        """Run both passes with a fresh run record."""
        self._record = _RunRecord()
        try:
            return super().analyze(repo_root, max_files)
        finally:
            self._record = None

    def _run_record(self) -> _RunRecord:
        if self._record is None:  # pragma: no cover - analyze() sets it
            self._record = _RunRecord()
        return self._record

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract module, interface, task, function and net symbols from a Verilog file."""
        analysis = FileAnalysis()
        record = self._run_record()

        def add(
            node: "tree_sitter.Node", name: str, kind: str, *,
            member_of: Optional[list[str]] = None, is_exported: Optional[bool] = None,
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
                is_exported=is_exported,
            )
            analysis.symbols.append(sym)
            return sym

        def scope_of(node: "tree_sitter.Node") -> list[str]:
            return ancestor_labels(node, lambda n: _scope_label(n, source))

        def add_callable(node: "tree_sitter.Node", name: str, kind: str) -> Symbol:
            """Append a task or function, exported by its design unit's rule,
            and record one a ``name::`` qualifier or an ``import`` can reach."""
            path = _scope_path(node)
            sym = add(
                node, name, kind, member_of=scope_of(node),
                is_exported=_callable_exported(path),
            )
            tag = _QUALIFIER_SCOPES.get(path[-1].type) if path else None
            owner = _scope_label(path[-1], source) if tag else None
            if tag and owner:
                record.scoped_callables.setdefault((tag, owner, name), []).append(sym)
            return sym

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

            elif node.type in _OTHER_UNIT_DECLARATIONS:
                other_name = _other_unit_name(node, source)
                if other_name:
                    record.other_units.add(other_name.lower())

            elif node.type in _CALLABLE_DECLARATIONS:
                body_type, ident_type, kind = _CALLABLE_DECLARATIONS[node.type]
                callable_name = _callable_name(node, source, body_type, ident_type)
                if callable_name:
                    sym = add_callable(node, callable_name, kind)
                    analysis.node_for_symbol[sym.id] = node

            elif node.type == "class_constructor_declaration":
                add_callable(node, "new", "function")

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
        # same-named interface coexists.
        sym_index = SymbolByName(case_insensitive=True)
        for candidates in global_symbols.values():
            for sym in candidates:
                sym_index.add(sym)

        # A unit's symbol by where its declaration starts (INV-mozas: an
        # enclosing symbol is found by position, never by name).
        unit_by_pos = {_span_bounds(unit)[0]: unit for unit in units}
        other_units = self._run_record().other_units

        for node in iter_tree(tree.root_node):
            if node.type not in _INSTANTIATIONS:
                continue
            source_unit = _find_containing_unit(node, unit_by_pos)
            module_type = _instantiated_type(node, source)
            if not module_type or source_unit is None:
                continue

            # An interface can instantiate only interfaces.
            resolved = sym_index.lookup_one(
                module_type,
                prefer_kind="interface" if source_unit.kind == "interface" else "module",
            )
            edge_meta: Optional[dict[str, Any]] = None
            if resolved is not None:
                dst_sym, is_fallback = resolved
                dst_id: str = dst_sym.id
                # INV-zuhub fallback contract: when multiple
                # candidates matched, mark the edge as a
                # disambiguation fallback with confidence ≤ 0.5
                # so downstream consumers can demote it.
                if is_fallback:
                    confidence = 0.50
                    edge_meta = {"disambiguation_fallback": True}
                else:
                    confidence = 0.90
            elif node.type in _AMBIGUOUS_INSTANTIATIONS and module_type.lower() in other_units:
                continue  # a real checker, UDP or program instance
            else:
                dst_id = f"verilog:external:{module_type}:module"
                confidence = 0.70

            edges.append(Edge.create(
                src=source_unit.id,
                dst=dst_id,
                edge_type="instantiates",
                line=node.start_point[0] + 1,
                confidence=confidence,
                origin=PASS_ID,
                evidence_type="verilog_instantiation",
                meta=edge_meta,
                origin_run_id=run.execution_id,
            ))

        edges.extend(self._call_edges(tree, source, rel_path, file_symbols, run))
        return edges

    def _call_edges(
        self, tree: "tree_sitter.Tree", source: bytes, rel_path: str,
        file_symbols: list[Symbol], run: "AnalysisRun",
    ) -> list[Edge]:
        """``calls`` edges for the call forms the grammar parses (see "Calls"
        in the module docstring). A callee that resolves to no single task or
        function emits nothing."""
        index = symbols_at(file_symbols)
        declared: dict[tuple[int, ...], dict[str, list[Symbol]]] = {}
        imports: dict[tuple[int, ...], list[tuple[str, Optional[str]]]] = {}
        call_nodes = []
        for node in iter_tree(tree.root_node):
            if node.type in _CALLABLE_NODE_TYPES:
                sym = symbol_declared_by(node, index)
                if sym is not None and sym.kind in _CALLABLE_KINDS:
                    declared.setdefault(_scope_key(_scope_path(node)), {}).setdefault(
                        sym.name, [],
                    ).append(sym)
            elif node.type == "package_import_item":
                package = find_child_by_type(node, "package_identifier")
                member = find_child_by_type(node, "simple_identifier")
                if package:
                    imports.setdefault(_scope_key(_scope_path(node)), []).append(
                        (node_text(package, source), node_text(member, source) if member else None)
                    )
            elif node.type in _CALL_NODE_TYPES:
                call_nodes.append(node)

        edges: list[Edge] = []
        for node in call_nodes:
            target = _call_target(node, source)
            callee = self._resolve_call(target, _scope_path(node), declared, imports) if target else None
            if callee is None:
                continue
            caller = enclosing_declared_symbol(node, index, _CALL_SOURCE_NODE_TYPES)
            edges.append(Edge.create(
                src=caller.id if caller else make_file_id("verilog", rel_path),
                dst=callee.id,
                edge_type="calls",
                evidence_type="ast_call",
                line=node.start_point[0] + 1,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
            ))
        return edges

    def _resolve_call(
        self,
        target: tuple[Optional[str], str, bool],
        path: list["tree_sitter.Node"],
        declared: dict[tuple[int, ...], dict[str, list[Symbol]]],
        imports: dict[tuple[int, ...], list[tuple[str, Optional[str]]]],
    ) -> Optional[Symbol]:
        """The task or function a call names, or ``None``.

        ``pkg::f`` / ``C::m``: the one ``f`` a package (else a class) of that
        name declares, in any file. ``this.m``: the ``m`` the innermost scope
        (the class) itself declares. A bare name: each enclosing scope of this file, innermost
        first, and at each one the scope's own declaration, then a name it
        imports explicitly, then one a wildcard import supplies. The walk
        stops at a class that ``extends`` another, whose inherited methods
        this analyzer does not know.
        """
        qualifier, name, on_this = target
        scoped = self._run_record().scoped_callables
        if qualifier is not None:
            return _only(
                scoped.get(("package", qualifier, name)) or scoped.get(("class", qualifier, name))
            )
        if on_this:
            return _only(declared.get(_scope_key(path), {}).get(name))
        for depth in range(len(path), -1, -1):
            key = _scope_key(path[:depth])
            found = declared.get(key, {}).get(name)
            if not found:
                items = imports.get(key, ())
                found = [
                    sym for package, member in items if member == name
                    for sym in scoped.get(("package", package, name), ())
                ] or [
                    sym for package, member in items if member is None
                    for sym in scoped.get(("package", package, name), ())
                ]
            if found:
                return _only(found)
            if depth and any(child.type == "extends" for child in path[depth - 1].children):
                return None
        return None

    def post_process(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        usage_contexts: list[UsageContext],
        run: "AnalysisRun",
    ) -> tuple[list[Symbol], list[Edge], list[UsageContext]]:
        """Mark the top-level modules: those no instantiation names."""
        instantiated = {edge.dst for edge in edges if edge.edge_type == "instantiates"}
        for sym in symbols:
            if sym.kind == "module" and sym.id not in instantiated:
                sym.meta = {**(sym.meta or {}), "concepts": [dict(_TOP_MODULE_CONCEPT)]}
        return symbols, edges, usage_contexts


_analyzer = VerilogAnalyzer()


def is_verilog_tree_sitter_available() -> bool:
    """Check if tree-sitter with Verilog grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("verilog", find_files=find_verilog_files)
def analyze_verilog_files(repo_root: Path) -> AnalysisResult:
    """Analyze Verilog/SystemVerilog files in the repository."""
    return _analyzer.analyze(repo_root)
