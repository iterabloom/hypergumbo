"""Twig template analyzer using tree-sitter.

Twig is a modern template engine for PHP, used extensively in Symfony
and other PHP frameworks. Understanding Twig structure helps with
template architecture and component relationships.

How It Works
------------
Uses TreeSitterAnalyzer base class for single-pass orchestration:
1. Pass 1: Extract blocks, extends, includes, macros, and control structures
2. Identifies template inheritance and composition patterns

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Twig-specific extraction
logic. Edges (extends_template, includes_template) are created during Pass 1
alongside symbols, since they don't require cross-file resolution.

Symbols Extracted
-----------------
- **Blocks**: Block definitions ({% block name %})
- **Extends**: Template inheritance ({% extends "base.twig" %})
- **Includes**: Template includes ({% include "partial.twig" %})
- **Macros**: Reusable template functions ({% macro name() %})
- **For loops**: Iteration structures ({% for item in items %})
- **Conditionals**: If statements ({% if condition %})

Edges Extracted
---------------
- **extends_template**: Links child template to parent template
- **includes_template**: Links include statements to templates

Why This Design
---------------
- Twig is the dominant PHP template engine
- Block/extends patterns reveal template hierarchy
- Include patterns show template composition
- Macros indicate reusable template logic
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver


PASS_ID = make_pass_id("twig")


def find_twig_files(repo_root: Path) -> list[Path]:
    """Find all Twig template files in the repository."""
    files: list[Path] = []
    files.extend(find_files(repo_root, ["*.twig"]))
    files.extend(find_files(repo_root, ["*.html.twig"]))
    return sorted(set(files))


def is_twig_tree_sitter_available() -> bool:
    """Check if tree-sitter-twig is available."""
    return _analyzer._check_grammar_available()


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def _make_symbol_id(path: str, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"twig:{path}:{kind}:{line}:{name}"


def _create_extends_symbol(
    rel_path: str, node: "tree_sitter.Node",
    template_name: str, run_id: str,
) -> tuple[Symbol, list[Edge]]:
    """Create a symbol for extends statement."""
    line = node.start_point[0] + 1
    edges: list[Edge] = []

    symbol_id = _make_symbol_id(rel_path, template_name, "extends", line)
    span = Span(
        start_line=line,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    symbol = Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=f"extends {template_name}",
        kind="extends",
        language="twig",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f'{{% extends "{template_name}" %}}',
        meta={"template": template_name},
    )

    # Create edge to parent template
    if template_name:
        edge = Edge.create(
            src=symbol_id,
            dst=f"twig:template:{template_name}",
            edge_type="extends_template",
            line=line,
            origin=PASS_ID,
            origin_run_id=run_id,
            evidence_type="extends",
            confidence=0.95,
        )
        edges.append(edge)

    return symbol, edges


def _create_block_symbol(
    rel_path: str, node: "tree_sitter.Node", block_name: str,
) -> Symbol:
    """Create a symbol for block definition."""
    line = node.start_point[0] + 1

    symbol_id = _make_symbol_id(rel_path, block_name, "block", line)
    span = Span(
        start_line=line,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    return Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=block_name,
        kind="block",
        language="twig",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{{% block {block_name} %}}",
        meta={},
    )


def _create_include_symbol(
    rel_path: str, node: "tree_sitter.Node",
    template_name: str, run_id: str,
) -> tuple[Symbol, list[Edge]]:
    """Create a symbol for include statement."""
    line = node.start_point[0] + 1
    edges: list[Edge] = []

    symbol_id = _make_symbol_id(rel_path, template_name, "include", line)
    span = Span(
        start_line=line,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    symbol = Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=f"include {template_name}",
        kind="include",
        language="twig",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f'{{% include "{template_name}" %}}',
        meta={"template": template_name},
    )

    # Create edge to included template
    if template_name:
        edge = Edge.create(
            src=symbol_id,
            dst=f"twig:template:{template_name}",
            edge_type="includes_template",
            line=line,
            origin=PASS_ID,
            origin_run_id=run_id,
            evidence_type="include",
            confidence=0.95,
        )
        edges.append(edge)

    return symbol, edges


def _create_macro_symbol(
    rel_path: str, node: "tree_sitter.Node", macro_name: str,
) -> Symbol:
    """Create a symbol for macro definition."""
    line = node.start_point[0] + 1

    symbol_id = _make_symbol_id(rel_path, macro_name, "macro", line)
    span = Span(
        start_line=line,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    return Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=macro_name,
        kind="macro",
        language="twig",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{{% macro {macro_name}() %}}",
        meta={},
    )


def _create_include_function_symbol(
    rel_path: str, node: "tree_sitter.Node",
    template_name: str, run_id: str,
) -> tuple[Symbol, list[Edge]]:
    """Create a symbol for include() function call."""
    line = node.start_point[0] + 1
    edges: list[Edge] = []

    symbol_id = _make_symbol_id(rel_path, template_name, "include_func", line)
    span = Span(
        start_line=line,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    symbol = Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=f"include({template_name})",
        kind="include",
        language="twig",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{{{{ include('{template_name}') }}}}",
        meta={"template": template_name},
    )

    # Create edge to included template
    edge = Edge.create(
        src=symbol_id,
        dst=f"twig:template:{template_name}",
        edge_type="includes_template",
        line=line,
        origin=PASS_ID,
        origin_run_id=run_id,
        evidence_type="include",
        confidence=0.95,
    )
    edges.append(edge)

    return symbol, edges


def _extract_tag_statement(
    node: "tree_sitter.Node", parent_node: "tree_sitter.Node",
    rel_path: str, run_id: str,
    symbols_out: list[Symbol], edges_out: list[Edge],
) -> None:
    """Extract tag statements like extends, block, include, macro."""
    tag_name = ""
    arg_value = ""

    for child in node.children:
        if child.type == "tag":
            tag_name = _get_node_text(child)
        elif child.type == "variable":
            arg_value = _get_node_text(child)
        elif child.type in ("interpolated_string", "string"):
            text = _get_node_text(child)
            if text.startswith('"') and text.endswith('"'):
                arg_value = text[1:-1]
            elif text.startswith("'") and text.endswith("'"):
                arg_value = text[1:-1]
            else:  # pragma: no cover
                arg_value = text

    if tag_name == "extends":
        sym, edges = _create_extends_symbol(rel_path, parent_node, arg_value, run_id)
        symbols_out.append(sym)
        edges_out.extend(edges)
    elif tag_name == "block" and arg_value:
        sym = _create_block_symbol(rel_path, parent_node, arg_value)
        symbols_out.append(sym)
    elif tag_name == "include":
        sym, edges = _create_include_symbol(rel_path, parent_node, arg_value, run_id)
        symbols_out.append(sym)
        edges_out.extend(edges)


def _extract_macro_statement(
    node: "tree_sitter.Node", parent_node: "tree_sitter.Node",
    rel_path: str, symbols_out: list[Symbol],
) -> None:
    """Extract macro definition from macro_statement node."""
    macro_name = ""

    for child in node.children:
        if child.type == "method":
            macro_name = _get_node_text(child)

    if macro_name:
        sym = _create_macro_symbol(rel_path, parent_node, macro_name)
        symbols_out.append(sym)


def _extract_for_statement(
    node: "tree_sitter.Node", parent_node: "tree_sitter.Node",
    rel_path: str, symbols_out: list[Symbol],
) -> None:
    """Extract for loop statement."""
    loop_var = ""
    iterable = ""

    for child in node.children:
        if child.type == "variable":
            if not loop_var:
                loop_var = _get_node_text(child)
            else:
                iterable = _get_node_text(child)

    line = parent_node.start_point[0] + 1

    name = f"for {loop_var} in {iterable}" if iterable else f"for {loop_var}"
    symbol_id = _make_symbol_id(rel_path, name, "for_loop", line)
    span = Span(
        start_line=line,
        start_col=parent_node.start_point[1],
        end_line=parent_node.end_point[0] + 1,
        end_col=parent_node.end_point[1],
    )

    symbol = Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=name,
        kind="for_loop",
        language="twig",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{{% {name} %}}",
        meta={"loop_variable": loop_var, "iterable": iterable},
    )
    symbols_out.append(symbol)


def _extract_if_statement(
    node: "tree_sitter.Node", parent_node: "tree_sitter.Node",
    rel_path: str, symbols_out: list[Symbol],
) -> None:
    """Extract if conditional statement."""
    condition = ""

    for child in node.children:
        if child.type == "variable":
            condition = _get_node_text(child)
            break

    line = parent_node.start_point[0] + 1

    name = f"if {condition}"
    symbol_id = _make_symbol_id(rel_path, name, "conditional", line)
    span = Span(
        start_line=line,
        start_col=parent_node.start_point[1],
        end_line=parent_node.end_point[0] + 1,
        end_col=parent_node.end_point[1],
    )

    symbol = Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=name,
        kind="conditional",
        language="twig",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{{% {name} %}}",
        meta={"condition": condition},
    )
    symbols_out.append(symbol)


def _extract_statement(
    node: "tree_sitter.Node", rel_path: str, run_id: str,
    symbols_out: list[Symbol], edges_out: list[Edge],
) -> None:
    """Extract statement directives ({% ... %})."""
    for child in node.children:
        if child.type == "tag_statement":
            _extract_tag_statement(child, node, rel_path, run_id, symbols_out, edges_out)
        elif child.type == "macro_statement":
            _extract_macro_statement(child, node, rel_path, symbols_out)
        elif child.type == "for_statement":
            _extract_for_statement(child, node, rel_path, symbols_out)
        elif child.type == "if_statement":
            _extract_if_statement(child, node, rel_path, symbols_out)


def _extract_function_call(
    node: "tree_sitter.Node", parent_node: "tree_sitter.Node",
    rel_path: str, run_id: str,
    symbols_out: list[Symbol], edges_out: list[Edge],
) -> None:
    """Extract function calls in output directives."""
    func_name = ""
    args: list[str] = []

    for child in node.children:
        if child.type == "function_identifier":
            func_name = _get_node_text(child)
        elif child.type == "arguments":
            for arg_child in child.children:
                if arg_child.type == "argument":
                    args.append(_get_node_text(arg_child))

    if not func_name:
        return  # pragma: no cover

    line = parent_node.start_point[0] + 1

    # Handle include() function calls
    if func_name == "include" and args:
        template_name = args[0].strip("'\"")
        sym, edges = _create_include_function_symbol(
            rel_path, parent_node, template_name, run_id,
        )
        symbols_out.append(sym)
        edges_out.extend(edges)
        return

    symbol_id = _make_symbol_id(rel_path, func_name, "function_call", line)
    span = Span(
        start_line=line,
        start_col=parent_node.start_point[1],
        end_line=parent_node.end_point[0] + 1,
        end_col=parent_node.end_point[1],
    )

    symbol = Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=func_name,
        kind="function_call",
        language="twig",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"{{{{ {func_name}() }}}}",
        meta={"arg_count": len(args)},
    )
    symbols_out.append(symbol)


def _extract_output(
    node: "tree_sitter.Node", rel_path: str, run_id: str,
    symbols_out: list[Symbol], edges_out: list[Edge],
) -> None:
    """Extract output directives ({{ ... }}) - specifically function calls."""
    for child in node.children:
        if child.type == "function_call":
            _extract_function_call(child, node, rel_path, run_id, symbols_out, edges_out)


def _extract_twig_symbols(
    node: "tree_sitter.Node", rel_path: str, run_id: str,
    symbols_out: list[Symbol], edges_out: list[Edge],
) -> None:
    """Extract symbols from a syntax tree node."""
    if node.type == "statement_directive":
        _extract_statement(node, rel_path, run_id, symbols_out, edges_out)
    elif node.type == "output_directive":
        _extract_output(node, rel_path, run_id, symbols_out, edges_out)

    for child in node.children:
        _extract_twig_symbols(child, rel_path, run_id, symbols_out, edges_out)


class TwigAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Twig template files using TreeSitterAnalyzer base class."""

    lang = "twig"
    file_patterns: ClassVar[list[str]] = ["*.twig", "*.html.twig"]
    language_pack_name = "twig"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract Twig template symbols and edges from a Twig file.

        Note: Twig edges (extends_template, includes_template) are extracted
        during Pass 1 since they don't require cross-file symbol resolution.
        We store them via import_aliases with a special key for later retrieval.
        """
        analysis = FileAnalysis()
        # Collect edges during symbol extraction; will be added in post_process
        edges: list[Edge] = []
        _extract_twig_symbols(
            tree.root_node, rel_path, run.execution_id,
            analysis.symbols, edges,
        )
        # Encode edge count so we can retrieve edges later
        # Store edges in import_aliases using JSON-serializable representation
        for i, edge in enumerate(edges):
            analysis.import_aliases[f"__edge_{i}__"] = (
                f"{edge.src}|{edge.dst}|{edge.edge_type}|{edge.line}|"
                f"{edge.origin}|{edge.origin_run_id}|{edge.evidence_type}|{edge.confidence}"
            )
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Re-extract edges from Twig file during Pass 2.

        Since Twig edges don't need cross-file resolution, we simply
        re-extract them from the AST.
        """
        edges: list[Edge] = []
        symbols: list[Symbol] = []  # discarded
        _extract_twig_symbols(
            tree.root_node, rel_path, run.execution_id,
            symbols, edges,
        )
        return edges


_analyzer = TwigAnalyzer()


@register_analyzer("twig")
def analyze_twig(repo_root: Path) -> AnalysisResult:
    """Analyze Twig template files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
