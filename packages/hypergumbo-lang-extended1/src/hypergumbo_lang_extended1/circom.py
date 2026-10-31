# SPDX-License-Identifier: AGPL-3.0-or-later
"""Circom analysis pass using tree-sitter-circom.

This analyzer uses tree-sitter to parse Circom (.circom) files and extract:
- Template definitions (the primary abstraction in Circom, mapped to 'class' kind)
- Function definitions (pure computations, no signals)
- Main component definitions (circuit entry point)
- Signal declarations (input/output/internal)
- Template instantiation edges (component declarations → template calls)
- Function call edges
- Include directive edges (imports)
- Signal flow constraint edges (<==, ==>, === → references edges with evidence_type=signal_constraint)

Circom is a domain-specific language for writing zero-knowledge circuits.
Templates define reusable circuit components with typed I/O signals.
Component instantiation is the primary composition mechanism — analogous
to class instantiation in OOP languages.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract templates, functions, signals, main;
   record each signal's owning template and each file's include texts
2. Pass 2: Detect include directives, template instantiations, function calls, and signal flow constraints (references edges)

Names resolve by circom's scoping (``_CircomScope``), never by bare name
repo-wide (WI-loviz). A signal reference inside template T resolves to T's
own signal of that name and to nothing else, so a parameter, ``var`` or loop
index named like another template's signal references nothing. Only a
component access ``c.in`` (``component c = B();`` or ``c[i] = B();``)
reaches another template's signal, and then only B's. tree-sitter-circom
binds ``<==`` tighter than ``.`` and ``[...]`` (``y <== c.out`` parses as
``(y <== c).out``), so a constraint is read from the leaves of its whole
expression. A template or function name resolves to the referring file's
definition, then the nearest one its includes reach, then the only one in
the repository; with several and none visible, the last registered, at the
``NameResolver`` ambiguity confidence. An include resolves next to the
including file, else by path suffix, and its edge targets that file's node.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate (grammar check, file discovery)
- Built from source since tree-sitter-circom is not on PyPI
- Two-pass allows cross-file template resolution (include + instantiate)
- Templates as 'class' kind captures their role as reusable abstractions
"""
from __future__ import annotations

import math
import posixpath
import warnings
from collections import deque
from dataclasses import dataclass, field
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
from hypergumbo_core.overload_arity import declared_arity_stable_id
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity
from hypergumbo_core.pass_silence import DEPENDENCY_UNAVAILABLE
from hypergumbo_core.symbol_resolution import NameResolver

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun

PASS_ID = make_pass_id("circom")


def find_circom_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Circom files in the repository."""
    yield from find_files(repo_root, ["*.circom"])


def is_circom_tree_sitter_available() -> bool:
    """Check if the tree-sitter-circom grammar is available."""
    try:
        import tree_sitter_circom

        return bool(tree_sitter_circom.language())
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


_CONSTRAINT_OPS = frozenset({"<==", "==>", "==="})
# What a name can resolve to: a component or ``main`` instantiates a template;
# a plain call may be a function or (``c[i] = B()``) a template.
_TEMPLATE_KINDS = frozenset({"class"})
_CALLABLE_KINDS = frozenset({"class", "function"})


def _definition_symbol_id(
    definition: "tree_sitter.Node", source: bytes, file_path: str,
) -> Optional[str]:
    """The symbol id pass 1 gave a template or function definition node."""
    name_node = find_child_by_type(definition, "identifier")
    if name_node is None:  # pragma: no cover - the grammar requires a name
        return None
    kind = "class" if definition.type == "template_definition" else "function"
    return make_symbol_id(
        "circom", file_path, definition.start_point[0] + 1,
        definition.end_point[0] + 1, node_text(name_node, source).strip(), kind,
    )


def _enclosing_definition(node: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """The template or function definition a node sits in, if any."""
    parent = node.parent
    while parent is not None:
        if parent.type in ("template_definition", "function_definition"):
            return parent
        parent = parent.parent
    return None


def _include_texts(root: "tree_sitter.Node", source: bytes) -> list[str]:
    """The path text of every include directive (top level only, as in circom)."""
    texts: list[str] = []
    for node in root.children:
        if node.type == "include_directive":
            string_node = find_child_by_type(node, "string")
            if string_node is not None:
                texts.append(_include_text(string_node, source))
    return texts


def _include_text(string_node: "tree_sitter.Node", source: bytes) -> str:
    return node_text(string_node, source).strip().strip('"').strip("'")


def _extract_symbols_from_file(
    analyzer: TreeSitterAnalyzer,
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
    signal_owners: Optional[dict[str, str]] = None,
) -> list[Symbol]:
    """Extract all symbols from a parsed Circom file.

    Detects:
    - class: template definitions (Circom's primary abstraction)
    - function: function definitions (pure computations)
    - variable: the main component and signal declarations, one symbol per
      declared name (``signal input a, b;`` declares two). Component
      declarations are not symbols; they become ``calls`` edges in pass 2.

    A function's ``stable_id`` is ``analyzer.compute_stable_id`` (WI-tusad):
    its ``parameter_list`` is a direct child, so the ADR-0035 untyped tier
    reads the parameter count from it. Circom functions are file-level and
    their names are unique, so the chain is the name. A template is
    ``compute_stable_id`` over its parameter count too; a signal and the main
    component ``declared_arity_stable_id`` with no arity, a signal scoped by
    its template (``T/in``), so the same signal name in two templates of one
    file holds two ids.

    ``signal_owners``, when given, receives ``signal id -> template id`` for
    every signal: the lexical scope pass 2 resolves signal references in.
    """
    symbols: list[Symbol] = []

    def variable_id(name: str, owner: Optional[str]) -> str:
        return declared_arity_stable_id(
            kind="variable", lang="circom", path=file_path, name=name,
            qualified_name=f"{owner}/{name}" if owner else name, arity=None,
        )

    def template_of(node: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
        current = node.parent
        while current is not None:
            if current.type == "template_definition":
                return current
            current = current.parent
        return None  # pragma: no cover - signals are declared in templates

    for node in iter_tree(tree.root_node):
        if node.type == "template_definition":
            name_node = find_child_by_type(node, "identifier")
            if not name_node:
                continue  # pragma: no cover
            name = node_text(name_node, source).strip()
            span = Span(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            )
            # Extract parameter signature
            param_node = find_child_by_type(node, "parameter_list")
            sig = None
            if param_node:
                sig_text = node_text(param_node, source).strip()
                if sig_text and sig_text != "()":
                    sig = sig_text

            sym = Symbol(
                id=make_symbol_id("circom", file_path, span.start_line, span.end_line, name, "class"),
                stable_id=analyzer.compute_stable_id(
                    node, "class", name=name, qualified_name=name,
                    file_stable_id=analyzer._file_anchor(file_path),
                ),
                name=name,
                kind="class",
                language="circom",
                path=file_path,
                span=span,
                origin=PASS_ID,
                origin_run_id=run_id,
                signature=sig,
            )
            symbols.append(sym)

        elif node.type == "function_definition":
            name_node = find_child_by_type(node, "identifier")
            if not name_node:
                continue  # pragma: no cover
            name = node_text(name_node, source).strip()
            span = Span(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            )
            param_node = find_child_by_type(node, "parameter_list")
            sig = None
            if param_node:
                sig_text = node_text(param_node, source).strip()
                if sig_text and sig_text != "()":
                    sig = sig_text

            sym = Symbol(
                id=make_symbol_id("circom", file_path, span.start_line, span.end_line, name, "function"),
                stable_id=analyzer.compute_stable_id(
                    node, "function", name=name, qualified_name=name,
                    file_stable_id=analyzer._file_anchor(file_path),
                ),
                name=name,
                kind="function",
                language="circom",
                path=file_path,
                span=span,
                origin=PASS_ID,
                origin_run_id=run_id,
                signature=sig,
                cyclomatic_complexity=compute_cyclomatic_complexity(node, "circom"),
                line_span=span.end_line - span.start_line + 1,
            )
            symbols.append(sym)

        elif node.type == "main_component_definition":
            span = Span(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            )
            sym = Symbol(
                id=make_symbol_id("circom", file_path, span.start_line, span.end_line, "main", "variable"),
                stable_id=variable_id("main", None),
                name="main",
                kind="variable",
                language="circom",
                path=file_path,
                span=span,
                origin=PASS_ID,
                origin_run_id=run_id,
            )
            symbols.append(sym)

        elif node.type == "signal_declaration_statement":
            span = Span(
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                start_col=node.start_point[1],
                end_col=node.end_point[1],
            )
            # Extract signal visibility (input/output)
            vis_node = find_child_by_type(node, "signal_visibility")
            modifiers: list[str] = []
            if vis_node:
                vis_text = node_text(vis_node, source).strip()
                if vis_text:
                    modifiers.append(vis_text)
            owner_node = template_of(node)
            owner_name = None
            owner_id = None
            if owner_node is not None:
                owner_id = _definition_symbol_id(owner_node, source, file_path)
                owner_ident = find_child_by_type(owner_node, "identifier")
                if owner_ident is not None:
                    owner_name = node_text(owner_ident, source).strip()

            # ``signal input a, b;`` names two signals: one symbol each.
            for name_node in node.children_by_field_name("name"):
                name = node_text(name_node, source).strip()
                if not name:
                    continue  # pragma: no cover
                sym = Symbol(
                    id=make_symbol_id("circom", file_path, span.start_line, span.end_line, name, "variable"),
                    stable_id=variable_id(name, owner_name),
                    name=name,
                    kind="variable",
                    language="circom",
                    path=file_path,
                    span=span,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    modifiers=list(modifiers),
                )
                symbols.append(sym)
                if signal_owners is not None and owner_id is not None:
                    signal_owners[sym.id] = owner_id

    return symbols


@dataclass
class _Pass1Record:
    """What pass 1 saw, kept for building the pass-2 scopes."""

    symbols_by_path: dict[str, list[Symbol]] = field(default_factory=dict)
    signal_owners: dict[str, str] = field(default_factory=dict)
    includes: dict[str, list[str]] = field(default_factory=dict)


class _CircomScope:
    """Name resolution by circom's own scoping, not by bare name repo-wide.

    - A signal is visible only inside the template that declares it
      (``signals[template id][name]``). A reference to another template's
      signal goes through a component (``c.in``), whose template is resolved
      first.
    - A template or function name resolves to the definition in the
      referring file, else to the nearest one its includes reach (breadth
      first), else to the only definition in the repository. Several
      definitions none of which is visible keep the old last-registered
      pick, with the ``NameResolver`` ambiguity confidence: circom's
      namespace is that of the whole compilation unit, so a file can use a
      template its *includer* brought in, and dropping the edge would be the
      silent direction.
    - An include resolves as the C closure does (``include_closure.py`` in
      hypergumbo-lang-mainstream): next to the including file first, else
      every repository file whose path ends with the include text at a path
      component boundary (``-l`` library paths are not known).
    """

    def __init__(self, record: _Pass1Record) -> None:
        self.callables: dict[str, list[Symbol]] = {}
        self.signals: dict[str, dict[str, list[Symbol]]] = {}
        self._paths = set(record.symbols_by_path)
        self._includes = record.includes
        self._closures: dict[str, list[str]] = {}
        self._by_suffix: dict[str, list[str]] = {}
        for path in sorted(self._paths):
            parts = path.split("/")
            for i in range(len(parts)):
                self._by_suffix.setdefault("/".join(parts[i:]), []).append(path)
        for symbols in record.symbols_by_path.values():
            for sym in symbols:
                if sym.kind in _CALLABLE_KINDS:
                    self.callables.setdefault(sym.name, []).append(sym)
                owner = record.signal_owners.get(sym.id)
                if owner is not None:
                    self.signals.setdefault(owner, {}).setdefault(
                        sym.name, [],
                    ).append(sym)

    def resolve_include(self, from_path: str, text: str) -> list[str]:
        """Repository paths an include directive in ``from_path`` names."""
        beside = posixpath.normpath(
            posixpath.join(posixpath.dirname(from_path), text),
        )
        if beside in self._paths:
            return [beside]
        tail = posixpath.normpath(text)
        while tail.startswith("../"):
            tail = tail[3:]
        return list(self._by_suffix.get(tail.lstrip("/"), ()))

    def closure(self, path: str) -> list[str]:
        """Files ``path`` includes, transitively, nearest first."""
        cached = self._closures.get(path)
        if cached is not None:
            return cached
        seen = {path}
        order: list[str] = []
        queue = deque([path])
        while queue:
            current = queue.popleft()
            for text in self._includes.get(current, ()):
                for target in self.resolve_include(current, text):
                    if target not in seen:
                        seen.add(target)
                        order.append(target)
                        queue.append(target)
        self._closures[path] = order
        return order

    def resolve_callable(
        self, name: str, path: str, kinds: frozenset[str],
    ) -> Optional[tuple[Symbol, float]]:
        """The definition ``name`` names from ``path``, and the match quality."""
        candidates = [s for s in self.callables.get(name, ()) if s.kind in kinds]
        if not candidates:
            return None
        first_by_path: dict[str, Symbol] = {}
        for sym in candidates:
            first_by_path.setdefault(sym.path, sym)
        if path in first_by_path:
            return first_by_path[path], NameResolver.CONFIDENCE_EXACT
        for included in self.closure(path):
            if included in first_by_path:
                return first_by_path[included], NameResolver.CONFIDENCE_EXACT
        if len(candidates) == 1:
            return candidates[0], NameResolver.CONFIDENCE_EXACT
        return candidates[-1], (
            NameResolver.CONFIDENCE_AMBIGUOUS / math.sqrt(len(candidates))
        )


def _callee_name(call: "tree_sitter.Node", source: bytes) -> Optional[str]:
    callee = find_child_by_type(call, "identifier")
    return node_text(callee, source).strip() if callee is not None else None


def _component_templates(
    definition: "tree_sitter.Node", source: bytes,
) -> dict[str, set[str]]:
    """Each component a template declares -> the template names it is bound to.

    ``component c = B();`` binds at the declaration; ``component c[n];``
    binds later, per element, with ``c[i] = B();``.
    """
    components: dict[str, set[str]] = {}
    assignments: list["tree_sitter.Node"] = []
    for node in iter_tree(definition):
        if node.type == "component_declaration_statement":
            call = find_child_by_type(node, "call_expression")
            callee = _callee_name(call, source) if call is not None else None
            for name_node in node.children_by_field_name("name"):
                bound = components.setdefault(node_text(name_node, source).strip(), set())
                if callee:
                    bound.add(callee)
        elif node.type == "assignment_expression":
            assignments.append(node)
    for node in assignments:
        operands = node.named_children
        is_plain = any(not c.is_named and c.type == "=" for c in node.children)
        if not is_plain or len(operands) != 2 or operands[1].type != "call_expression":
            continue
        base: Optional["tree_sitter.Node"] = operands[0]
        while base is not None and base.type == "array_access_expression":
            base = base.child_by_field_name("base")
        name = node_text(base, source).strip() if base is not None else ""
        callee = _callee_name(operands[1], source)
        if name in components and callee:
            components[name].add(callee)
    return components


def _has_constraint_op(node: "tree_sitter.Node") -> bool:
    return any(not c.is_named and c.type in _CONSTRAINT_OPS for c in node.children)


def _constraint_anchor(assignment: "tree_sitter.Node") -> "tree_sitter.Node":
    """The whole expression a constraint assignment belongs to.

    tree-sitter-circom binds ``<==`` / ``===`` tighter than ``.`` and
    ``[...]``, so ``y <== c.out;`` parses as ``(y <== c).out`` and
    ``a === c[1].out;`` as ``((a === c)[1]).out``: the assignment node stops
    before the member access it constrains. The anchor is the outermost
    expression above it, below the enclosing statement.
    """
    anchor = assignment
    while (
        anchor.parent is not None
        and not anchor.parent.type.endswith("_statement")
        and anchor.parent.type not in ("template_body", "function_body", "source_file")
    ):
        anchor = anchor.parent
    return anchor


def _access_base_index(leaves: list["tree_sitter.Node"], j: int) -> Optional[int]:
    """Index of the identifier a ``.`` access applies to, walking back over
    balanced ``[...]`` from leaf ``j`` (``c[i].out`` -> ``c``)."""
    depth = 0
    while j >= 0:
        kind = leaves[j].type
        if kind == "]":
            depth += 1
        elif kind == "[":
            depth -= 1
        elif depth == 0:
            return j if kind == "identifier" else None
        j -= 1
    return None  # pragma: no cover - an access always follows an operand


def _constraint_targets(
    anchor: "tree_sitter.Node",
    source: bytes,
    file_path: str,
    own: dict[str, list[Symbol]],
    components: dict[str, set[str]],
    scope: _CircomScope,
) -> list[Symbol]:
    """Signals a constraint statement references, in source order.

    Read from the leaves, not the tree, because of the precedence quirk in
    :func:`_constraint_anchor`: ``base [..]* . property`` is a component
    access whenever ``base`` is a component of the enclosing template, and
    resolves to ``property`` among the bound template's signals. Every other
    identifier (a callee excepted) resolves among the enclosing template's
    own signals; a parameter, ``var`` or loop index matches none.
    """
    leaves = [n for n in iter_tree(anchor) if n.child_count == 0]
    bases: set[int] = set()
    accessed: dict[int, list[Symbol]] = {}
    for i, leaf in enumerate(leaves):
        if leaf.type != "property_identifier" or i < 2 or leaves[i - 1].type != ".":
            continue
        j = _access_base_index(leaves, i - 2)
        if j is None:
            continue
        bound = components.get(node_text(leaves[j], source).strip())
        if bound is None:
            continue
        bases.add(j)
        prop = node_text(leaf, source).strip()
        accessed[i] = []
        for template_name in sorted(bound):
            resolved = scope.resolve_callable(template_name, file_path, _TEMPLATE_KINDS)
            if resolved is not None:
                accessed[i].extend(scope.signals.get(resolved[0].id, {}).get(prop, ()))

    targets: list[Symbol] = []
    for i, leaf in enumerate(leaves):
        if i in accessed:
            targets.extend(accessed[i])
        elif (
            leaf.type == "identifier"
            and i not in bases
            and leaf.parent is not None
            and leaf.parent.type != "call_expression"
        ):
            targets.extend(own.get(node_text(leaf, source).strip(), ()))
    return targets


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
    scope: _CircomScope,
    file_symbol: Optional[Symbol],
) -> list[Edge]:
    """Extract edges from a parsed Circom file.

    Detects:
    - imports: include directives (to the included file's node when the
      repository has it, else to an unresolved ``circom:<text>:0-0`` id)
    - calls: template instantiations (component declarations, main component)
    - calls: function calls
    - references: signal flow constraints (<==, ==>, ===, and a signal
      declaration's ``<==`` initializer), resolved by ``scope``
    """
    edges: list[Edge] = []
    components_of: dict[tuple[int, int], dict[str, set[str]]] = {}
    anchored: set[tuple[int, int]] = set()

    def callable_edge(
        call: "tree_sitter.Node", src_id: str, line: int, kinds: frozenset[str],
    ) -> None:
        callee = _callee_name(call, source)
        resolved = scope.resolve_callable(callee, file_path, kinds) if callee else None
        if resolved is not None:
            edges.append(Edge.create(
                src=src_id,
                dst=resolved[0].id,
                edge_type="calls",
                evidence_type="ast_call",
                line=line,
                resolution_score=resolved[1],
                origin=PASS_ID,
                origin_run_id=run_id,
            ))

    for node in iter_tree(tree.root_node):
        # Include directives → import edges
        if node.type == "include_directive":
            string_node = find_child_by_type(node, "string")
            if string_node and file_symbol:
                raw = _include_text(string_node, source)
                targets = [
                    make_file_id("circom", path)
                    for path in scope.resolve_include(file_path, raw)
                ] or [f"circom:{raw}:0-0:file:file"]
                for target_id in targets:
                    edges.append(Edge.create(
                        src=file_symbol.id,
                        dst=target_id,
                        edge_type="imports",
                        evidence_type="include",
                        line=node.start_point[0] + 1,
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))

        # Call expressions → calls edges (template instantiation or function call)
        elif node.type == "call_expression":
            callee_name = _callee_name(node, source)
            if not callee_name:
                continue  # pragma: no cover
            parent_type = node.parent.type if node.parent is not None else ""
            instantiates = (
                parent_type in ("component_declaration_statement", "main_component_definition")
                or find_child_by_type(node, "anonymous_inputs") is not None
            )
            resolved = scope.resolve_callable(
                callee_name, file_path,
                _TEMPLATE_KINDS if instantiates else _CALLABLE_KINDS,
            )
            if resolved is not None:
                definition = _enclosing_definition(node)
                src_id = (
                    _definition_symbol_id(definition, source, file_path)
                    if definition is not None else None
                )
                if src_id:
                    edge = Edge.create(
                        src=src_id,
                        dst=resolved[0].id,
                        edge_type="calls",
                        evidence_type="ast_call",
                        line=node.start_point[0] + 1,
                        confidence=0.85,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                    edges.append(edge)

        # Main component definition → calls edge to the instantiated template
        elif node.type == "main_component_definition":
            call_node = find_child_by_type(node, "call_expression")
            if call_node:
                main_id = make_symbol_id(
                    "circom",
                    file_path,
                    node.start_point[0] + 1,
                    node.end_point[0] + 1,
                    "main",
                    "variable",
                )
                callable_edge(call_node, main_id, node.start_point[0] + 1, _TEMPLATE_KINDS)

        # Component declarations inside templates → calls edges
        elif node.type == "component_declaration_statement":
            call_node = find_child_by_type(node, "call_expression")
            definition = _enclosing_definition(node)
            if call_node and definition is not None:
                src_id = _definition_symbol_id(definition, source, file_path)
                if src_id:
                    callable_edge(call_node, src_id, node.start_point[0] + 1, _TEMPLATE_KINDS)

        # Signal flow constraints (<==, ==>, ===) → references edges (WI-zijos),
        # each name resolved in the template's own scope (WI-loviz).
        elif (
            node.type in ("assignment_expression", "signal_declaration_statement")
            and _has_constraint_op(node)
        ):
            anchor = (
                node if node.type == "signal_declaration_statement"
                else _constraint_anchor(node)
            )
            key = (anchor.start_byte, anchor.end_byte)
            if key in anchored:
                continue  # pragma: no cover - one constraint per statement
            anchored.add(key)
            definition = _enclosing_definition(anchor)
            if definition is None:  # pragma: no cover
                continue
            src_id = _definition_symbol_id(definition, source, file_path)
            if not src_id:  # pragma: no cover
                continue
            def_key = (definition.start_byte, definition.end_byte)
            if def_key not in components_of:
                components_of[def_key] = _component_templates(definition, source)
            seen: set[str] = set()
            for target in _constraint_targets(
                anchor, source, file_path, scope.signals.get(src_id, {}),
                components_of[def_key], scope,
            ):
                if target.id in seen:
                    continue
                seen.add(target.id)
                edges.append(Edge.create(
                    src=src_id,
                    dst=target.id,
                    edge_type="references",
                    line=anchor.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    evidence_type="signal_constraint",
                ))

    return edges


class CircomTreeSitterAnalyzer(TreeSitterAnalyzer):
    """Circom analyzer using tree-sitter-circom grammar.

    Pass 1 records each file's symbols, signal owners and include texts in a
    per-run :class:`_Pass1Record`; pass 2 builds one :class:`_CircomScope`
    from it and resolves every name through that. ``analyze`` resets both
    at the start and end of each run, so no state leaks between runs.
    """

    lang = "circom"
    file_patterns: ClassVar[list[str]] = ["*.circom"]
    grammar_module = "tree_sitter_circom"
    create_file_symbols = True

    _record: Optional[_Pass1Record] = None
    _scope: Optional[_CircomScope] = None

    def analyze(self, repo_root: Path, max_files: Optional[int] = None) -> AnalysisResult:
        """Run both passes with a fresh per-run scope record."""
        self._record = _Pass1Record()
        self._scope = None
        try:
            return super().analyze(repo_root, max_files)
        finally:
            self._record = None
            self._scope = None

    def _pass1_record(self) -> _Pass1Record:
        if self._record is None:  # pragma: no cover - analyze() sets it
            self._record = _Pass1Record()
        return self._record

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        source_file: Path,
        rel_path: str,
        run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a single Circom file."""
        record = self._pass1_record()
        symbols = _extract_symbols_from_file(
            self, tree, source, rel_path, run.execution_id, record.signal_owners,
        )
        record.symbols_by_path[rel_path] = symbols
        record.includes[rel_path] = _include_texts(tree.root_node, source)
        return FileAnalysis(symbols=symbols)

    def extract_edges_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        local_symbols: dict,
        global_symbols: dict[str, Symbol],
        run: "AnalysisRun",
        import_aliases: dict,
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract edges from a single Circom file (pass 2)."""
        if self._scope is None:
            self._scope = _CircomScope(self._pass1_record())
        # Find file symbol for import edges (registered by name = rel_path)
        file_sym = global_symbols.get(rel_path)
        return _extract_edges_from_file(
            tree, source, rel_path, run.execution_id, self._scope, file_sym,
        )


_analyzer = CircomTreeSitterAnalyzer()


@register_analyzer("circom")
def analyze_circom(repo_root: Path) -> AnalysisResult:
    """Analyze Circom files in a repository.

    Returns an AnalysisResult with symbols and edges extracted from
    .circom files. If tree-sitter-circom is not available, returns
    a skipped result with a UserWarning — but only when the tree
    actually contains .circom files (WI-ruman: don't pester users
    whose Go / Java / Rust repos happen to share an analyzer
    registry with the Circom backend).
    """
    if not is_circom_tree_sitter_available():
        if any(find_circom_files(repo_root)):
            warnings.warn(
                "Circom analysis skipped: tree-sitter-circom grammar "
                "not available. Run `hypergumbo build-grammars` to "
                "build it.",
                UserWarning,
                stacklevel=2,
            )
        from hypergumbo_core.ir import PASS_VERSION, AnalysisRun, _get_python_toolchain

        run = AnalysisRun(
            pass_id=PASS_ID,
            # WI-luliv: toolchain is Dict[str, str], not a bare str — a string
            # value crashes finalize's run_signature recompute (.get on a str).
            toolchain=_get_python_toolchain(),
            execution_id=f"skip-{PASS_ID}",
            version=PASS_VERSION,
        )
        return AnalysisResult(
            symbols=[],
            edges=[],
            usage_contexts=[],
            run=run,
            skipped=True,
            skip_reason="tree-sitter-circom grammar not available",
            skip_reason_code=DEPENDENCY_UNAVAILABLE,
        )
    return _analyzer.analyze(repo_root)
