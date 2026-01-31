"""reStructuredText analyzer using tree-sitter.

reStructuredText (RST) is the standard documentation format for Python projects,
used by Sphinx to generate documentation. It's also used in docstrings and
standalone documentation files.

How It Works
------------
1. Uses tree-sitter-rst grammar from tree-sitter-language-pack to parse files
2. Extracts document structure (sections, titles)
3. Extracts directives (function, class, module definitions)
4. Extracts cross-references and toctree relationships

Symbols Extracted
-----------------
- **Sections**: Document sections with titles at various levels
- **Directives**: RST directives (function, class, module, note, warning, etc.)
- **Targets**: Reference targets for cross-linking

Edges Extracted
---------------
- **references**: Cross-document references (:ref:, :doc:, etc.)
- **includes**: toctree and include relationships

Why This Design
---------------
- RST is the primary documentation format for Python ecosystem
- Understanding document structure helps navigate large docs
- Cross-references map documentation dependencies
- Directive extraction captures API documentation
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


PASS_ID = "rst.tree_sitter"
PASS_VERSION = "0.1.0"


class RSTAnalysisResult:
    """Result of RST analysis."""

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


def is_rst_tree_sitter_available() -> bool:
    """Check if tree-sitter-rst is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("rst")
        return True
    except Exception:  # pragma: no cover
        return False


def find_rst_files(repo_root: Path) -> list[Path]:
    """Find all RST files in the repository."""
    return sorted(repo_root.glob("**/*.rst"))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str) -> str:
    """Create a stable symbol ID."""
    return f"rst:{path}:{kind}:{name}"


# Documentation directives that define API elements
API_DIRECTIVES = frozenset({
    "function", "class", "method", "module", "attribute", "data",
    "exception", "decorator", "property",
    # Autodoc directives
    "autofunction", "autoclass", "automethod", "automodule", "autodata",
    "autoexception", "autodecorator",
    # C domain
    "c:function", "c:type", "c:struct", "c:macro",
    # Other domains
    "py:function", "py:class", "py:method", "py:module",
    "js:function", "js:class", "js:method",
})

# Admonition directives (notes, warnings, etc.)
ADMONITION_DIRECTIVES = frozenset({
    "note", "warning", "tip", "important", "caution", "danger",
    "attention", "error", "hint", "seealso", "todo",
    "admonition", "versionadded", "versionchanged", "deprecated",
})


class RSTAnalyzer:
    """Analyzer for RST files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0
        self._section_counter = 0
        self._directive_counter = 0

    def analyze(self) -> RSTAnalysisResult:
        """Run the RST analysis."""
        start_time = time.time()

        files = find_rst_files(self.repo_root)
        if not files:
            return RSTAnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("rst")

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._section_counter = 0
                self._directive_counter = 0
                self._extract_symbols(tree.root_node, path, 0)
                self._files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        duration_ms = int((time.time() - start_time) * 1000)

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._execution_id,
            version=PASS_VERSION,
            toolchain={"name": "rst", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return RSTAnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(
        self, node: "tree_sitter.Node", path: Path, section_level: int
    ) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "section":
            self._extract_section(node, path, section_level)
        elif node.type == "directive":
            self._extract_directive(node, path)
        elif node.type == "target":
            self._extract_target(node, path)
        elif node.type == "interpreted_text":
            self._extract_reference(node, path)
        else:
            for child in node.children:
                self._extract_symbols(child, path, section_level)

    def _extract_section(
        self, node: "tree_sitter.Node", path: Path, parent_level: int
    ) -> None:
        """Extract a section with its title."""
        title_text = ""
        level = parent_level + 1

        for child in node.children:
            if child.type == "title":
                title_text = " ".join(
                    _get_node_text(c) for c in child.children if c.type == "text"
                ).strip()
                break

        if not title_text:  # pragma: no cover
            # Process children without creating a symbol
            for child in node.children:
                self._extract_symbols(child, path, level)
            return

        self._section_counter += 1
        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, f"section_{self._section_counter}", "section")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=title_text,
            kind="section",
            language="rst",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"{'#' * level} {title_text}",
            meta={"level": level},
        )
        self._symbols.append(symbol)

        # Process nested content
        for child in node.children:
            self._extract_symbols(child, path, level)

    def _extract_directive(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a directive."""
        directive_type = ""
        arguments = ""

        for child in node.children:
            if child.type == "type":
                directive_type = _get_node_text(child)
            elif child.type == "body":
                for body_child in child.children:
                    if body_child.type == "arguments":
                        # Use node text directly to preserve paths with slashes
                        arguments = _get_node_text(body_child).strip()
                        break

        if not directive_type:
            return  # pragma: no cover

        self._directive_counter += 1
        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(
            rel_path, f"directive_{self._directive_counter}", "directive"
        )

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Determine directive category
        is_api = directive_type in API_DIRECTIVES
        is_admonition = directive_type in ADMONITION_DIRECTIVES

        # Build signature
        if arguments:
            sig = f".. {directive_type}:: {arguments[:50]}{'...' if len(arguments) > 50 else ''}"
        else:
            sig = f".. {directive_type}::"

        # Use arguments as name if it's an API directive, otherwise use type
        name = arguments if (arguments and is_api) else directive_type

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=name,
            kind="directive",
            language="rst",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=sig,
            meta={
                "directive_type": directive_type,
                "arguments": arguments,
                "is_api": is_api,
                "is_admonition": is_admonition,
            },
        )
        self._symbols.append(symbol)

        # Extract toctree entries as edges
        if directive_type == "toctree":
            self._extract_toctree_entries(node, path, symbol_id)

        # Extract include directive as edge
        if directive_type == "include" and arguments:
            edge = Edge.create(
                src=symbol_id,
                dst=f"rst:file:{arguments}",
                edge_type="includes",
                line=node.start_point[0] + 1,
                origin=PASS_ID,
                origin_run_id=self._execution_id,
                evidence_type="static",
                confidence=1.0,
                evidence_lang="rst",
            )
            self._edges.append(edge)

    def _extract_toctree_entries(
        self, node: "tree_sitter.Node", path: Path, source_id: str
    ) -> None:
        """Extract entries from a toctree directive."""
        for child in node.children:
            if child.type == "body":
                for body_child in child.children:
                    if body_child.type == "content":
                        for content_child in body_child.children:
                            if content_child.type == "text":
                                entry = _get_node_text(content_child).strip()
                                if entry and not entry.startswith(":"):
                                    edge = Edge.create(
                                        src=source_id,
                                        dst=f"rst:doc:{entry}",
                                        edge_type="includes",
                                        line=content_child.start_point[0] + 1,
                                        origin=PASS_ID,
                                        origin_run_id=self._execution_id,
                                        evidence_type="static",
                                        confidence=1.0,
                                        evidence_lang="rst",
                                    )
                                    self._edges.append(edge)

    def _extract_target(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a reference target."""
        target_name = _get_node_text(node).strip()
        # Remove leading .. _ and trailing :
        if target_name.startswith(".. _"):
            target_name = target_name[4:]
        if target_name.endswith(":"):
            target_name = target_name[:-1]

        if not target_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, target_name, "target")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=target_name,
            kind="target",
            language="rst",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f".. _{target_name}:",
            meta={},
        )
        self._symbols.append(symbol)

    def _extract_reference(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a cross-reference."""
        role = ""
        target = ""

        for child in node.children:
            if child.type == "role":
                role = _get_node_text(child).strip(":")
            elif child.type == "interpreted_text":
                target = _get_node_text(child).strip("`")

        if not role or not target:  # pragma: no cover
            return

        # Only track cross-document references
        if role not in ("ref", "doc", "any", "mod", "func", "class", "meth", "attr"):
            return

        rel_path = path.relative_to(self.repo_root)
        src_id = f"rst:{rel_path}"

        edge = Edge.create(
            src=src_id,
            dst=f"rst:{role}:{target}",
            edge_type="references",
            line=node.start_point[0] + 1,
            origin=PASS_ID,
            origin_run_id=self._execution_id,
            evidence_type="static",
            confidence=1.0,
            evidence_lang="rst",
        )
        self._edges.append(edge)


def analyze_rst(repo_root: Path) -> RSTAnalysisResult:
    """Analyze RST files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        RSTAnalysisResult containing extracted symbols and edges
    """
    if not is_rst_tree_sitter_available():
        warnings.warn(
            "RST analysis skipped: tree-sitter-rst not available",
            UserWarning,
            stacklevel=2,
        )
        return RSTAnalysisResult(
            symbols=[],
            edges=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "rst", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-rst not available",
        )

    analyzer = RSTAnalyzer(repo_root)
    return analyzer.analyze()
