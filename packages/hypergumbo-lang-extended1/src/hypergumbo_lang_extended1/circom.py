"""Circom analysis pass using tree-sitter-circom.

This analyzer uses tree-sitter to parse Circom (.circom) files and extract:
- Template definitions (the primary abstraction in Circom, mapped to 'class' kind)
- Function definitions (pure computations, no signals)
- Main component definitions (circuit entry point)
- Signal declarations (input/output/internal)
- Template instantiation edges (component declarations → template calls)
- Function call edges
- Include directive edges (imports)

Circom is a domain-specific language for writing zero-knowledge circuits.
Templates define reusable circuit components with typed I/O signals.
Component instantiation is the primary composition mechanism — analogous
to class instantiation in OOP languages.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract templates, functions, signals, main
2. Pass 2: Detect include directives, template instantiations, function calls

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate (grammar check, file discovery)
- Built from source since tree-sitter-circom is not on PyPI
- Two-pass allows cross-file template resolution (include + instantiate)
- Templates as 'class' kind captures their role as reusable abstractions
"""
from __future__ import annotations

import warnings
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

PASS_ID = make_pass_id("circom")


def find_circom_files(repo_root: Path) -> Iterator[Path]:  # pragma: no cover
    """Yield all Circom files in the repository."""
    yield from find_files(repo_root, ["*.circom"])


def is_circom_tree_sitter_available() -> bool:
    """Check if the tree-sitter-circom grammar is available."""
    try:
        import tree_sitter_circom

        return bool(tree_sitter_circom.language())
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> list[Symbol]:
    """Extract all symbols from a parsed Circom file.

    Detects:
    - class: template definitions (Circom's primary abstraction)
    - function: function definitions (pure computations)
    - variable: main component, signal declarations, component declarations
    """
    symbols: list[Symbol] = []

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
                name=name,
                kind="function",
                language="circom",
                path=file_path,
                span=span,
                origin=PASS_ID,
                origin_run_id=run_id,
                signature=sig,
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
            # Extract signal visibility (input/output)
            vis_node = find_child_by_type(node, "signal_visibility")
            modifiers: list[str] = []
            if vis_node:
                vis_text = node_text(vis_node, source).strip()
                if vis_text:
                    modifiers.append(vis_text)

            sym = Symbol(
                id=make_symbol_id("circom", file_path, span.start_line, span.end_line, name, "variable"),
                name=name,
                kind="variable",
                language="circom",
                path=file_path,
                span=span,
                origin=PASS_ID,
                origin_run_id=run_id,
                modifiers=modifiers if modifiers else None,
            )
            symbols.append(sym)

    return symbols


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
    resolver: "NameResolver",
    file_symbol: Optional[Symbol],
) -> list[Edge]:
    """Extract edges from a parsed Circom file.

    Detects:
    - imports: include directives
    - calls: template instantiations (component declarations, main component)
    - calls: function calls
    """
    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        # Include directives → import edges
        if node.type == "include_directive":
            string_node = find_child_by_type(node, "string")
            if string_node and file_symbol:
                raw = node_text(string_node, source).strip().strip('"').strip("'")
                target_id = f"circom:{raw}:0-0:file:file"
                edge = Edge.create(
                    src=file_symbol.id,
                    dst=target_id,
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                    confidence=0.95,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                edges.append(edge)

        # Call expressions → calls edges (template instantiation or function call)
        elif node.type == "call_expression":
            callee_node = find_child_by_type(node, "identifier")
            if not callee_node:
                continue  # pragma: no cover
            callee_name = node_text(callee_node, source).strip()
            # Resolve callee to a symbol
            result = resolver.lookup(callee_name)
            if result.symbol:
                target = result.symbol
                # Determine src: find the enclosing template or function
                src_id = _find_enclosing_symbol_id(
                    node, source, file_path, run_id, resolver,
                )
                if src_id:
                    edge = Edge.create(
                        src=src_id,
                        dst=target.id,
                        edge_type="calls",
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
                callee_node = find_child_by_type(call_node, "identifier")
                if callee_node:
                    callee_name = node_text(callee_node, source).strip()
                    result = resolver.lookup(callee_name)
                    if result.symbol:
                        # Find main symbol
                        main_id = make_symbol_id(
                            "circom",
                            file_path,
                            node.start_point[0] + 1,
                            node.end_point[0] + 1,
                            "main",
                            "variable",
                        )
                        edge = Edge.create(
                            src=main_id,
                            dst=result.symbol.id,
                            edge_type="calls",
                            line=node.start_point[0] + 1,
                            confidence=0.95,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        )
                        edges.append(edge)

        # Component declarations inside templates → calls edges
        elif node.type == "component_declaration_statement":
            call_node = find_child_by_type(node, "call_expression")
            if call_node:
                callee_node = find_child_by_type(call_node, "identifier")
                if callee_node:
                    callee_name = node_text(callee_node, source).strip()
                    result = resolver.lookup(callee_name)
                    if result.symbol:
                        src_id = _find_enclosing_symbol_id(
                            node, source, file_path, run_id, resolver,
                        )
                        if src_id:
                            edge = Edge.create(
                                src=src_id,
                                dst=result.symbol.id,
                                edge_type="calls",
                                line=node.start_point[0] + 1,
                                confidence=0.90,
                                origin=PASS_ID,
                                origin_run_id=run_id,
                            )
                            edges.append(edge)

    return edges


def _find_enclosing_symbol_id(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: str,
    run_id: str,
    resolver: "NameResolver",
) -> Optional[str]:
    """Walk up the AST to find the enclosing template/function."""
    parent = node.parent
    while parent is not None:
        if parent.type in ("template_definition", "function_definition"):
            name_node = find_child_by_type(parent, "identifier")
            if name_node:
                name = node_text(name_node, source).strip()
                kind = "class" if parent.type == "template_definition" else "function"
                span = Span(
                    start_line=parent.start_point[0] + 1,
                    end_line=parent.end_point[0] + 1,
                    start_col=parent.start_point[1],
                    end_col=parent.end_point[1],
                )
                return make_symbol_id("circom", file_path, span.start_line, span.end_line, name, kind)
        parent = parent.parent
    return None  # pragma: no cover


class CircomTreeSitterAnalyzer(TreeSitterAnalyzer):
    """Circom analyzer using tree-sitter-circom grammar."""

    lang = "circom"
    file_patterns: ClassVar[list[str]] = ["*.circom"]
    grammar_module = "tree_sitter_circom"
    create_file_symbols = True

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        source_file: Path,
        rel_path: str,
        run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a single Circom file."""
        symbols = _extract_symbols_from_file(tree, source, rel_path, run.execution_id)
        return FileAnalysis(symbols=symbols)

    def extract_edges_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        local_symbols: dict,
        global_symbols: dict,
        run: "AnalysisRun",
        import_aliases: dict,
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract edges from a single Circom file (pass 2)."""
        # Find file symbol for import edges (registered by name = rel_path)
        file_sym = global_symbols.get(rel_path)
        return _extract_edges_from_file(
            tree, source, rel_path, run.execution_id, resolver, file_sym,
        )


_analyzer = CircomTreeSitterAnalyzer()


@register_analyzer("circom")
def analyze_circom(repo_root: Path) -> AnalysisResult:
    """Analyze Circom files in a repository.

    Returns an AnalysisResult with symbols and edges extracted from
    .circom files. If tree-sitter-circom is not available, returns
    a skipped result with a UserWarning.
    """
    if not is_circom_tree_sitter_available():
        warnings.warn(
            "Circom analysis skipped: tree-sitter-circom grammar not available. "
            "Run `hypergumbo build-grammars` to build it.",
            UserWarning,
            stacklevel=2,
        )
        from hypergumbo_core.ir import PASS_VERSION, AnalysisRun

        run = AnalysisRun(
            pass_id=PASS_ID,
            toolchain="tree-sitter-circom",
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
        )
    return _analyzer.analyze(repo_root)
