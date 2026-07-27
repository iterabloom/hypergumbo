# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nim language analysis pass using tree-sitter.

Detects:
- Import statements
- Type definitions (objects, enums, tuples)
- Proc definitions (procedures)
- Func definitions (pure functions)
- Method definitions

Nim is a compiled systems programming language with Python-like syntax,
combining low-level control with high-level expressiveness.
The tree-sitter-nim parser handles .nim, .nims, and .nimble files.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract proc/func/method/type definitions with signatures
2. Pass 2: Extract import edges and call edges using NameResolver

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Nim-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates ~150 lines of boilerplate
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for Nim grammar
- Nim is growing in systems programming communities
- Supports source, script, and package files
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
    make_symbol_id,
    make_unresolved_edge,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("nim")


def find_nim_files(repo_root: Path) -> Iterator[Path]:
    """Find all Nim files in the repository."""
    yield from find_files(repo_root, ["*.nim", "*.nims", "*.nimble"])


def is_nim_tree_sitter_available() -> bool:
    """Check if tree-sitter-nim is available."""
    return _analyzer._check_grammar_available()


# ---------------------------------------------------------------------------
# Symbol extraction helpers
# ---------------------------------------------------------------------------


def _declared_name_node(
    parent: "tree_sitter.Node",
) -> tuple[Optional["tree_sitter.Node"], bool]:
    """Return ``(name_identifier, is_exported)`` for a Nim declaration.

    A plain declaration exposes its name as a direct ``identifier`` child. An
    EXPORTED one — Nim's ``*`` postfix, ``proc greet*`` / ``type Person*`` —
    wraps that identifier in an ``exported_symbol`` node (``identifier`` +
    ``*``), so a bare ``find_child_by_type(parent, "identifier")`` finds
    nothing and the whole declaration was silently dropped (INV-bisom, 0/246
    on the filed corpus). Resolve the inner identifier in either shape, and
    report whether the ``*`` export marker was present so the caller can set
    ``Symbol.is_exported`` (mirroring go.py's lexical-case export rule).
    """
    ident = find_child_by_type(parent, "identifier")
    if ident is not None:
        return ident, False
    exported = find_child_by_type(parent, "exported_symbol")
    if exported is not None:
        return find_child_by_type(exported, "identifier"), True
    return None, False  # pragma: no cover - defensive: a declaration always names


def _make_symbol(
    analyzer: "NimAnalyzer", rel_path: str, run_id: str, node: "tree_sitter.Node",
    name: str, kind: str, source: bytes,
    signature: Optional[str] = None, meta: Optional[dict] = None,
    is_exported: bool = False,
) -> Symbol:
    """Create a Symbol with consistent formatting."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    # INV-loguk: CC only for callables; the "type" kind funnels through this
    # same helper and would otherwise aggregate nested proc branches.
    _is_callable = kind in ("function", "method")
    sym_id = make_symbol_id("nim", rel_path, start_line, end_line, name, kind)
    span = Span(
        start_line=start_line,
        start_col=node.start_point[1],
        end_line=end_line,
        end_col=node.end_point[1],
    )
    return Symbol(
        id=sym_id,
        name=name,
        kind=kind,
        language="nim",
        path=rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        # WI-bokab (v7): fold the declaring file's identity into the
        # containing slot via the base helper. ``rel_path`` is the
        # repo-relative path threaded down from
        # ``extract_symbols_from_file(... rel_path ...)``, so the anchor is
        # location-independent — two same-(kind, name) procs in different
        # files now hash distinctly.
        stable_id=analyzer.compute_stable_id(
            node, kind=kind, name=name,
            file_stable_id=analyzer._file_anchor(rel_path),
        ),
        signature=signature,
        meta=meta,
        is_exported=is_exported,
        cyclomatic_complexity=(
            compute_cyclomatic_complexity(node, "nim") if _is_callable else None
        ),
        line_span=(end_line - start_line + 1) if _is_callable else None,
    )


def _process_proc_declaration(
    analyzer: "NimAnalyzer", source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
) -> Optional[Symbol]:
    """Process a proc declaration."""
    name_node, is_exported = _declared_name_node(node)
    if not name_node:
        return None  # pragma: no cover - defensive

    proc_name = node_text(name_node, source)
    params = find_child_by_type(node, "parameter_declaration_list")
    signature = node_text(params, source) if params else "()"

    return _make_symbol(analyzer, rel_path, run_id, node, proc_name, "function", source, signature=signature, is_exported=is_exported)


def _process_func_declaration(
    analyzer: "NimAnalyzer", source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
) -> Optional[Symbol]:
    """Process a func declaration (pure function)."""
    name_node, is_exported = _declared_name_node(node)
    if not name_node:
        return None  # pragma: no cover - defensive

    func_name = node_text(name_node, source)
    params = find_child_by_type(node, "parameter_declaration_list")
    signature = node_text(params, source) if params else "()"

    return _make_symbol(analyzer, rel_path, run_id, node, func_name, "function", source, signature=signature, is_exported=is_exported)


def _process_method_declaration(
    analyzer: "NimAnalyzer", source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
) -> Optional[Symbol]:
    """Process a method declaration."""
    name_node, is_exported = _declared_name_node(node)
    if not name_node:
        return None  # pragma: no cover - defensive

    method_name = node_text(name_node, source)
    params = find_child_by_type(node, "parameter_declaration_list")
    signature = node_text(params, source) if params else "()"

    return _make_symbol(analyzer, rel_path, run_id, node, method_name, "method", source, signature=signature, is_exported=is_exported)


def _process_type_declaration(
    analyzer: "NimAnalyzer", source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
) -> Optional[Symbol]:
    """Process a type declaration."""
    type_sym = find_child_by_type(node, "type_symbol_declaration")
    if not type_sym:
        return None  # pragma: no cover - defensive

    name_node, is_exported = _declared_name_node(type_sym)
    if not name_node:
        return None  # pragma: no cover - defensive

    type_name = node_text(name_node, source)
    return _make_symbol(analyzer, rel_path, run_id, node, type_name, "type", source, is_exported=is_exported)


# ---------------------------------------------------------------------------
# Field / variable extraction helpers (WI-jusus)
# ---------------------------------------------------------------------------


def _enclosing_type_name(
    node: "tree_sitter.Node", source: bytes,
) -> Optional[str]:
    """Return the name of the ``type_declaration`` directly owning ``node``.

    An object ``field_declaration`` / ``enum_field_declaration`` lives inside a
    ``type_declaration`` whose name (``type_symbol_declaration``) becomes the
    field's owner. ``_declared_name_node`` resolves the plain-or-exported name
    shape, so ``Person*`` yields owner ``Person``.

    An **intervening** ``field_declaration`` on the way up means ``node`` is a
    member of an anonymous tuple/object embedded in *another* field's type
    (``coord: tuple[x, y: int]``) — those inner members belong to the tuple, not
    to the enclosing named object, so attributing them would mint a phantom,
    wrong-owner ``Outer.x``. Abort the walk (skip the symbol — a fails-safe
    miss) in that case. A standalone named tuple (``type Coord = tuple[x, y]``)
    and object-variant branch fields cross no intervening ``field_declaration``,
    so their members stay correctly owned; a tuple in a proc return / free
    ``var`` type reaches the root with no ``type_declaration`` and is skipped.
    """
    current = node.parent
    while current is not None:
        if current.type == "field_declaration":
            return None
        if current.type == "type_declaration":
            type_sym = find_child_by_type(current, "type_symbol_declaration")
            if type_sym is not None:
                name_node, _ = _declared_name_node(type_sym)
                if name_node is not None:
                    return node_text(name_node, source)
            return None  # pragma: no cover - defensive: a type always names
        current = current.parent
    return None  # a field_declaration outside any named type (proc-return / free tuple)


def _iter_named_declarations(
    sdl: "tree_sitter.Node", source: bytes,
) -> Iterator[tuple["tree_sitter.Node", str, bool]]:
    """Yield ``(symbol_declaration_node, name, is_exported)`` per declared name.

    A ``symbol_declaration_list`` holds one ``symbol_declaration`` per name, so a
    multi-name declaration (``age, weight: int`` / ``var a, b = 0``) yields each
    name separately. ``_declared_name_node`` resolves the plain-or-``*``-exported
    identifier shape. Shared by the field and module-variable paths.
    """
    for child in sdl.children:
        if child.type != "symbol_declaration":
            continue
        name_node, is_exported = _declared_name_node(child)
        if name_node is None:
            continue  # pragma: no cover - defensive: a declaration always names
        yield child, node_text(name_node, source), is_exported


def _process_field_declaration(
    analyzer: "NimAnalyzer", source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
) -> list[tuple[Symbol, "tree_sitter.Node"]]:
    """Process object ``field_declaration`` members into ``kind="field"`` anchors.

    ``field_declaration > symbol_declaration_list > symbol_declaration+`` — each
    name is emitted as ``Owner.member`` keyed off its own node so co-declared
    fields get distinct spans / shape_ids.
    """
    type_name = _enclosing_type_name(node, source)
    if type_name is None:
        return []  # pragma: no cover - defensive
    sdl = find_child_by_type(node, "symbol_declaration_list")
    if sdl is None:
        return []  # pragma: no cover - defensive
    pairs: list[tuple[Symbol, "tree_sitter.Node"]] = []
    for child, member, is_exported in _iter_named_declarations(sdl, source):
        sym = _make_symbol(
            analyzer, rel_path, run_id, child, f"{type_name}.{member}",
            "field", source, is_exported=is_exported,
        )
        pairs.append((sym, child))
    return pairs


def _process_enum_field_declaration(
    analyzer: "NimAnalyzer", source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
) -> Optional[Symbol]:
    """Process an ``enum_field_declaration`` member into a ``kind="field"`` anchor.

    Enum variants are type members accessed via ``.`` (``Color.red``), emitted
    as fields per the dart/zig enum-body-value precedent. Structure is
    ``enum_field_declaration > symbol_declaration [= value]``.
    """
    type_name = _enclosing_type_name(node, source)
    if type_name is None:
        return None  # pragma: no cover - defensive
    sd = find_child_by_type(node, "symbol_declaration")
    if sd is None:
        return None  # pragma: no cover - defensive
    name_node, is_exported = _declared_name_node(sd)
    if name_node is None:
        return None  # pragma: no cover - defensive
    member = node_text(name_node, source)
    return _make_symbol(
        analyzer, rel_path, run_id, node, f"{type_name}.{member}",
        "field", source, is_exported=is_exported,
    )


def _process_module_variable(
    analyzer: "NimAnalyzer", source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
) -> list[tuple[Symbol, "tree_sitter.Node"]]:
    """Process a module-level ``variable_declaration`` into ``kind="variable"`` anchors.

    A ``const``/``var``/``let`` at module scope is a ``variable_declaration``
    whose section (``const_section``/``var_section``/``let_section``) sits
    DIRECTLY under ``source_file``. A proc-body local reuses the SAME node but
    its section lives under a ``statement_list``, so the source_file-parented
    gate keeps locals out (the swift/go INV-lanaz/INV-sidab local-leak class).
    Multi-name (``var a, b = 0``) lists one ``symbol_declaration`` per name.
    """
    section = node.parent
    if section is None or section.parent is None:
        return []  # pragma: no cover - defensive
    if section.parent.type != "source_file":
        return []  # a proc-body local (or nested block) — not a module variable
    sdl = find_child_by_type(node, "symbol_declaration_list")
    if sdl is None:
        return []  # pragma: no cover - defensive
    pairs: list[tuple[Symbol, "tree_sitter.Node"]] = []
    for child, var_name, is_exported in _iter_named_declarations(sdl, source):
        sym = _make_symbol(
            analyzer, rel_path, run_id, child, var_name,
            "variable", source, is_exported=is_exported,
        )
        pairs.append((sym, child))
    return pairs


# ---------------------------------------------------------------------------
# Import alias and edge extraction helpers
# ---------------------------------------------------------------------------


def _extract_import_aliases(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract import aliases for disambiguation.

    In Nim:
        import strutils as su -> su maps to strutils

    Returns a dict mapping alias names to module names.
    """
    aliases: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "import_statement":
            continue

        expr_list = find_child_by_type(node, "expression_list")
        if not expr_list:  # pragma: no cover - defensive for malformed import
            continue

        for child in expr_list.children:
            if child.type == "infix_expression":
                module_name = None
                alias_name = None
                found_as = False

                for subchild in child.children:
                    if subchild.type == "identifier":
                        if not found_as:
                            module_name = node_text(subchild, source)
                        else:
                            alias_name = node_text(subchild, source)
                    elif subchild.type == "as":
                        found_as = True

                if module_name and alias_name:
                    aliases[alias_name] = module_name

    return aliases


def _extract_import_edges(
    source: bytes, file_stable_id: str, run_id: str, node: "tree_sitter.Node",
) -> list[Edge]:
    """Extract import edges from an import statement."""
    edges: list[Edge] = []
    expr_list = find_child_by_type(node, "expression_list")
    if expr_list:
        for child in expr_list.children:
            if child.type == "identifier":
                import_name = node_text(child, source)
                edges.append(
                    Edge.create(
                        src=file_stable_id,
                        dst=f"nim:{import_name}:0-0:module:module",
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                        confidence=0.9,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                )
            elif child.type == "infix_expression":
                for subchild in child.children:
                    if subchild.type == "identifier":
                        import_name = node_text(subchild, source)
                        edges.append(
                            Edge.create(
                                src=file_stable_id,
                                dst=f"nim:{import_name}:0-0:module:module",
                                edge_type="imports",
                                line=node.start_point[0] + 1,
                                confidence=0.9,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            )
                        )
                        break  # Only take the first identifier (module name)
    return edges


def _find_enclosing_proc_nim(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the enclosing proc/func/method Symbol by walking up parents."""
    current = node.parent
    while current is not None:
        if current.type in ("proc_declaration", "func_declaration", "method_declaration"):
            name_node = find_child_by_type(current, "identifier")
            if name_node:
                name = node_text(name_node, source)
                sym = local_symbols.get(name)
                if sym:
                    return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _get_call_target_name_nim(
    node: "tree_sitter.Node", source: bytes
) -> tuple[Optional[str], Optional[str]]:
    """Extract the target name and receiver from a call node.

    Returns (target_name, receiver) where receiver is the module prefix
    for qualified calls like su.strip().
    """
    for child in node.children:
        if child.type == "identifier":
            return (node_text(child, source), None)
        elif child.type == "dot_expression":
            parts = []
            for subchild in child.children:
                if subchild.type == "identifier":
                    parts.append(node_text(subchild, source))
            if len(parts) >= 2:
                return (parts[-1], parts[0])
            elif len(parts) == 1:  # pragma: no cover - defensive
                return (parts[0], None)
    return (None, None)  # pragma: no cover - defensive


# ---------------------------------------------------------------------------
# NimAnalyzer: TreeSitterAnalyzer subclass
# ---------------------------------------------------------------------------


class NimAnalyzer(TreeSitterAnalyzer):
    """Nim language analyzer using tree-sitter-language-pack."""

    lang = "nim"
    file_patterns: ClassVar[list[str]] = ["*.nim", "*.nims", "*.nimble"]
    language_pack_name = "nim"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract proc, func, method, type, field, and variable symbols.

        WI-jusus: ``field`` (object/enum members) and ``variable`` (module-level
        ``const``/``var``/``let``) are DATA anchors — emitted to
        ``analysis.symbols`` for search/centrality but kept out of the call
        graph. They are never added to ``symbol_by_name`` here, and
        ``register_symbol`` skips them from the global resolution registry, so a
        bare-named ``variable`` can never clobber a same-named ``proc``.
        """
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            pairs: list[tuple[Symbol, "tree_sitter.Node"]] = []
            if node.type == "proc_declaration":
                sym = _process_proc_declaration(self, source, rel_path, run.execution_id, node)
                if sym:
                    pairs.append((sym, node))
            elif node.type == "func_declaration":
                sym = _process_func_declaration(self, source, rel_path, run.execution_id, node)
                if sym:
                    pairs.append((sym, node))
            elif node.type == "method_declaration":
                sym = _process_method_declaration(self, source, rel_path, run.execution_id, node)
                if sym:
                    pairs.append((sym, node))
            elif node.type == "type_declaration":
                sym = _process_type_declaration(self, source, rel_path, run.execution_id, node)
                if sym:
                    pairs.append((sym, node))
            elif node.type == "field_declaration":
                pairs.extend(_process_field_declaration(self, source, rel_path, run.execution_id, node))
            elif node.type == "enum_field_declaration":
                sym = _process_enum_field_declaration(self, source, rel_path, run.execution_id, node)
                if sym:
                    pairs.append((sym, node))
            elif node.type == "variable_declaration":
                pairs.extend(_process_module_variable(self, source, rel_path, run.execution_id, node))

            for sym, sym_node in pairs:
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = sym_node
                if sym.kind in ("function", "method"):
                    analysis.symbol_by_name[sym.name] = sym

        return analysis

    def register_symbol(self, symbol: Symbol, global_symbols: dict) -> None:
        """Keep field/variable symbols OUT of the call-resolution registry (WI-jusus).

        A ``field``/``variable`` is a data anchor, never a call or instantiation
        target. Registering them would let a bare-named module ``variable``
        clobber a same-named ``proc``'s flat registry key (a false-negative the
        edge site cannot recover) and let a field shadow a real method — so both
        integrity vectors are closed at this one chokepoint rather than by gating
        every edge site. They remain in ``analysis.symbols`` (search / centrality
        / io-boundaries) because the output symbol set is built independently of
        this registry.
        """
        if symbol.kind in ("field", "variable"):
            return
        super().register_symbol(symbol, global_symbols)

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract Nim import aliases (import X as Y)."""
        return _extract_import_aliases(tree, source)

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract import and call edges from a Nim file."""
        edges: list[Edge] = []
        file_stable_id = f"nim:{rel_path}:file:"

        for node in iter_tree(tree.root_node):
            if node.type == "import_statement":
                edges.extend(_extract_import_edges(
                    source, file_stable_id, run.execution_id, node,
                ))

            elif node.type == "call":
                target_name, receiver = _get_call_target_name_nim(node, source)
                if target_name:
                    caller = _find_enclosing_proc_nim(node, source, local_symbols)
                    if caller:
                        path_hint: Optional[str] = None
                        if receiver:
                            path_hint = import_aliases.get(receiver)

                        lookup_result = resolver.lookup(target_name, path_hint=path_hint)
                        if lookup_result.found and lookup_result.symbol:
                            edges.append(Edge.create(
                                src=caller.id,
                                dst=lookup_result.symbol.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                confidence=0.85 * lookup_result.confidence,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))
                        else:
                            edges.append(make_unresolved_edge(
                                "nim", caller.id, target_name,
                                node.start_point[0] + 1,
                                PASS_ID, run.execution_id,
                            ))

        return edges


_analyzer = NimAnalyzer()


@register_analyzer("nim")
def analyze_nim(repo_root: Path) -> AnalysisResult:
    """Analyze Nim files in a repository."""
    return _analyzer.analyze(repo_root)
