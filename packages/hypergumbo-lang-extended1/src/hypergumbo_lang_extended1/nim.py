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
        """Extract proc, func, method, and type symbols from a Nim file."""
        analysis = FileAnalysis()

        for node in iter_tree(tree.root_node):
            sym: Optional[Symbol] = None
            if node.type == "proc_declaration":
                sym = _process_proc_declaration(self, source, rel_path, run.execution_id, node)
            elif node.type == "func_declaration":
                sym = _process_func_declaration(self, source, rel_path, run.execution_id, node)
            elif node.type == "method_declaration":
                sym = _process_method_declaration(self, source, rel_path, run.execution_id, node)
            elif node.type == "type_declaration":
                sym = _process_type_declaration(self, source, rel_path, run.execution_id, node)

            if sym:
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node
                if sym.kind in ("function", "method"):
                    analysis.symbol_by_name[sym.name] = sym

        return analysis

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
