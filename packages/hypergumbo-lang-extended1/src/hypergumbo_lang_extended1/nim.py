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

import uuid
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
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

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


def _make_symbol(
    analyzer: "NimAnalyzer", rel_path: str, run_id: str, node: "tree_sitter.Node",
    name: str, kind: str, source: bytes,
    signature: Optional[str] = None, meta: Optional[dict] = None,
) -> Symbol:
    """Create a Symbol with consistent formatting."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
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
        canonical_name=name,
        kind=kind,
        language="nim",
        path=rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        stable_id=analyzer.compute_stable_id(node, kind=kind),
        signature=signature,
        meta=meta,
    )


def _process_proc_declaration(
    analyzer: "NimAnalyzer", source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
) -> Optional[Symbol]:
    """Process a proc declaration."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    proc_name = node_text(name_node, source)
    params = find_child_by_type(node, "parameter_declaration_list")
    signature = node_text(params, source) if params else "()"

    return _make_symbol(analyzer, rel_path, run_id, node, proc_name, "function", source, signature=signature)


def _process_func_declaration(
    analyzer: "NimAnalyzer", source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
) -> Optional[Symbol]:
    """Process a func declaration (pure function)."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    func_name = node_text(name_node, source)
    params = find_child_by_type(node, "parameter_declaration_list")
    signature = node_text(params, source) if params else "()"

    return _make_symbol(analyzer, rel_path, run_id, node, func_name, "function", source, signature=signature)


def _process_method_declaration(
    analyzer: "NimAnalyzer", source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
) -> Optional[Symbol]:
    """Process a method declaration."""
    name_node = find_child_by_type(node, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    method_name = node_text(name_node, source)
    params = find_child_by_type(node, "parameter_declaration_list")
    signature = node_text(params, source) if params else "()"

    return _make_symbol(analyzer, rel_path, run_id, node, method_name, "method", source, signature=signature)


def _process_type_declaration(
    analyzer: "NimAnalyzer", source: bytes, rel_path: str, run_id: str, node: "tree_sitter.Node",
) -> Optional[Symbol]:
    """Process a type declaration."""
    type_sym = find_child_by_type(node, "type_symbol_declaration")
    if not type_sym:
        return None  # pragma: no cover - defensive

    name_node = find_child_by_type(type_sym, "identifier")
    if not name_node:
        return None  # pragma: no cover - defensive

    type_name = node_text(name_node, source)
    return _make_symbol(analyzer, rel_path, run_id, node, type_name, "type", source)


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
                    Edge(
                        id=f"edge:nim:{uuid.uuid4().hex[:12]}",
                        src=file_stable_id,
                        dst=f"nim:?:{import_name}:module",
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
                            Edge(
                                id=f"edge:nim:{uuid.uuid4().hex[:12]}",
                                src=file_stable_id,
                                dst=f"nim:?:{import_name}:module",
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
                            dst_id = lookup_result.symbol.id
                            confidence = 0.85 * lookup_result.confidence
                        else:
                            dst_id = f"nim:external:{target_name}:function"
                            confidence = 0.70

                        edges.append(Edge(
                            id=f"edge:nim:{uuid.uuid4().hex[:12]}",
                            src=caller.id,
                            dst=dst_id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=confidence,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

        return edges


_analyzer = NimAnalyzer()


@register_analyzer("nim")
def analyze_nim(repo_root: Path) -> AnalysisResult:
    """Analyze Nim files in a repository."""
    return _analyzer.analyze(repo_root)
