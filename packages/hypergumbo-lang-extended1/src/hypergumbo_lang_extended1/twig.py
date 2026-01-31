"""Twig template analyzer using tree-sitter.

Twig is a modern template engine for PHP, used extensively in Symfony
and other PHP frameworks. Understanding Twig structure helps with
template architecture and component relationships.

How It Works
------------
1. Uses tree-sitter-twig grammar from tree-sitter-language-pack
2. Extracts blocks, extends, includes, macros, and control structures
3. Identifies template inheritance and composition patterns

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

import time
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "twig.tree_sitter"
PASS_VERSION = "0.1.0"


class TwigAnalysisResult:
    """Result of Twig template analysis."""

    def __init__(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        run: AnalysisRun | None = None,
        skipped: bool = False,
        skip_reason: str = "",
    ) -> None:
        self.symbols = symbols
        self.edges = edges
        self.run = run
        self.skipped = skipped
        self.skip_reason = skip_reason


def is_twig_tree_sitter_available() -> bool:
    """Check if tree-sitter-twig is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("twig")
        return True
    except Exception:  # pragma: no cover
        return False


def find_twig_files(repo_root: Path) -> list[Path]:
    """Find all Twig template files in the repository."""
    files: list[Path] = []
    files.extend(repo_root.glob("**/*.twig"))
    files.extend(repo_root.glob("**/*.html.twig"))
    return sorted(set(files))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"twig:{path}:{kind}:{line}:{name}"


class TwigAnalyzer:
    """Analyzer for Twig template files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0
        self._template_registry: dict[str, str] = {}  # template name -> symbol id

    def analyze(self) -> TwigAnalysisResult:
        """Run the Twig analysis."""
        start_time = time.time()

        files = find_twig_files(self.repo_root)
        if not files:
            return TwigAnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("twig")

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path)
                self._files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        duration_ms = int((time.time() - start_time) * 1000)

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._execution_id,
            version=PASS_VERSION,
            toolchain={"name": "twig", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return TwigAnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "statement_directive":
            self._extract_statement(node, path)
        elif node.type == "output_directive":
            self._extract_output(node, path)

        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_statement(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract statement directives ({% ... %})."""
        # Find the tag_statement or specific statement type
        for child in node.children:
            if child.type == "tag_statement":
                self._extract_tag_statement(child, path, node)
            elif child.type == "macro_statement":
                self._extract_macro_statement(child, path, node)
            elif child.type == "for_statement":
                self._extract_for_statement(child, path, node)
            elif child.type == "if_statement":
                self._extract_if_statement(child, path, node)

    def _extract_tag_statement(
        self, node: "tree_sitter.Node", path: Path, parent_node: "tree_sitter.Node"
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
                # Get the string content without quotes
                text = _get_node_text(child)
                if text.startswith('"') and text.endswith('"'):
                    arg_value = text[1:-1]
                elif text.startswith("'") and text.endswith("'"):
                    arg_value = text[1:-1]
                else:  # pragma: no cover
                    arg_value = text

        if tag_name == "extends":
            self._create_extends_symbol(path, parent_node, arg_value)
        elif tag_name == "block" and arg_value:
            self._create_block_symbol(path, parent_node, arg_value)
        elif tag_name == "include":
            self._create_include_symbol(path, parent_node, arg_value)

    def _extract_macro_statement(
        self, node: "tree_sitter.Node", path: Path, parent_node: "tree_sitter.Node"
    ) -> None:
        """Extract macro definition from macro_statement node."""
        macro_name = ""

        for child in node.children:
            if child.type == "method":
                macro_name = _get_node_text(child)

        if macro_name:
            self._create_macro_symbol(path, parent_node, macro_name)

    def _create_extends_symbol(
        self, path: Path, node: "tree_sitter.Node", template_name: str
    ) -> None:
        """Create a symbol for extends statement."""
        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

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
        self._symbols.append(symbol)

        # Create edge to parent template
        if template_name:
            edge = Edge.create(
                src=symbol_id,
                dst=f"twig:template:{template_name}",
                edge_type="extends_template",
                line=line,
                origin=PASS_ID,
                origin_run_id=self._execution_id,
                evidence_type="extends",
                confidence=0.95,
            )
            self._edges.append(edge)

    def _create_block_symbol(
        self, path: Path, node: "tree_sitter.Node", block_name: str
    ) -> None:
        """Create a symbol for block definition."""
        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        symbol_id = _make_symbol_id(rel_path, block_name, "block", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
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
        self._symbols.append(symbol)

    def _create_include_symbol(
        self, path: Path, node: "tree_sitter.Node", template_name: str
    ) -> None:
        """Create a symbol for include statement."""
        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

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
        self._symbols.append(symbol)

        # Create edge to included template
        if template_name:
            edge = Edge.create(
                src=symbol_id,
                dst=f"twig:template:{template_name}",
                edge_type="includes_template",
                line=line,
                origin=PASS_ID,
                origin_run_id=self._execution_id,
                evidence_type="include",
                confidence=0.95,
            )
            self._edges.append(edge)

    def _create_macro_symbol(
        self, path: Path, node: "tree_sitter.Node", macro_name: str
    ) -> None:
        """Create a symbol for macro definition."""
        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        symbol_id = _make_symbol_id(rel_path, macro_name, "macro", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
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
        self._symbols.append(symbol)

    def _extract_for_statement(
        self, node: "tree_sitter.Node", path: Path, parent_node: "tree_sitter.Node"
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

        rel_path = path.relative_to(self.repo_root)
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
        self._symbols.append(symbol)

    def _extract_if_statement(
        self, node: "tree_sitter.Node", path: Path, parent_node: "tree_sitter.Node"
    ) -> None:
        """Extract if conditional statement."""
        condition = ""

        for child in node.children:
            if child.type == "variable":
                condition = _get_node_text(child)
                break

        rel_path = path.relative_to(self.repo_root)
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
        self._symbols.append(symbol)

    def _extract_output(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract output directives ({{ ... }}) - specifically function calls."""
        for child in node.children:
            if child.type == "function_call":
                self._extract_function_call(child, path, node)

    def _extract_function_call(
        self, node: "tree_sitter.Node", path: Path, parent_node: "tree_sitter.Node"
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

        rel_path = path.relative_to(self.repo_root)
        line = parent_node.start_point[0] + 1

        # Handle include() function calls
        if func_name == "include" and args:
            template_name = args[0].strip("'\"")
            self._create_include_function_symbol(path, parent_node, template_name)
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
        self._symbols.append(symbol)

    def _create_include_function_symbol(
        self, path: Path, node: "tree_sitter.Node", template_name: str
    ) -> None:
        """Create a symbol for include() function call."""
        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

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
        self._symbols.append(symbol)

        # Create edge to included template
        edge = Edge.create(
            src=symbol_id,
            dst=f"twig:template:{template_name}",
            edge_type="includes_template",
            line=line,
            origin=PASS_ID,
            origin_run_id=self._execution_id,
            evidence_type="include",
            confidence=0.95,
        )
        self._edges.append(edge)


def analyze_twig(repo_root: Path) -> TwigAnalysisResult:
    """Analyze Twig template files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        TwigAnalysisResult containing extracted symbols and edges
    """
    if not is_twig_tree_sitter_available():
        warnings.warn(
            "Twig analysis skipped: tree-sitter-twig not available",
            UserWarning,
            stacklevel=2,
        )
        return TwigAnalysisResult(
            symbols=[],
            edges=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "twig", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-twig not available",
        )

    analyzer = TwigAnalyzer(repo_root)
    return analyzer.analyze()
