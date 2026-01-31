"""KDL (KDL Document Language) configuration analyzer using tree-sitter.

KDL is a modern document language designed for configuration files. It offers
a cleaner syntax than JSON or XML while being more structured than YAML.

How It Works
------------
1. Uses tree-sitter-kdl grammar from tree-sitter-language-pack
2. Extracts nodes with their arguments and properties
3. Identifies top-level configuration sections and nested structures

Symbols Extracted
-----------------
- **Nodes**: KDL nodes representing configuration entries
- **Sections**: Top-level nodes that contain children (configuration sections)

Why This Design
---------------
- KDL is gaining adoption as a configuration format
- Node hierarchy reveals configuration structure
- Properties and arguments capture configuration values
- Understanding nesting helps with configuration inheritance
"""

from __future__ import annotations

import time
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "kdl.tree_sitter"
PASS_VERSION = "0.1.0"


class KdlAnalysisResult:
    """Result of KDL configuration analysis."""

    def __init__(
        self,
        symbols: list[Symbol],
        run: AnalysisRun | None = None,
        skipped: bool = False,
        skip_reason: str = "",
    ) -> None:
        self.symbols = symbols
        self.edges: list = []  # KDL files typically don't have cross-file edges
        self.run = run
        self.skipped = skipped
        self.skip_reason = skip_reason


def is_kdl_tree_sitter_available() -> bool:
    """Check if tree-sitter-kdl is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("kdl")
        return True
    except Exception:  # pragma: no cover
        return False


def find_kdl_files(repo_root: Path) -> list[Path]:
    """Find all KDL configuration files in the repository."""
    files: list[Path] = []
    files.extend(repo_root.glob("**/*.kdl"))
    return sorted(set(files))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"kdl:{path}:{kind}:{line}:{name}"


class KdlAnalyzer:
    """Analyzer for KDL configuration files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0

    def analyze(self) -> KdlAnalysisResult:
        """Run the KDL analysis."""
        start_time = time.time()

        files = find_kdl_files(self.repo_root)
        if not files:
            return KdlAnalysisResult(
                symbols=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("kdl")

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path, depth=0)
                self._files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        duration_ms = int((time.time() - start_time) * 1000)

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._execution_id,
            version=PASS_VERSION,
            toolchain={"name": "kdl", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return KdlAnalysisResult(
            symbols=self._symbols,
            run=run,
        )

    def _extract_symbols(
        self, node: "tree_sitter.Node", path: Path, depth: int
    ) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "node":
            self._extract_node(node, path, depth)
        elif node.type == "document":
            # Process children of document
            for child in node.children:
                self._extract_symbols(child, path, depth)

    def _extract_node(
        self, node: "tree_sitter.Node", path: Path, depth: int
    ) -> None:
        """Extract a KDL node."""
        node_name = ""
        arguments: list[str] = []
        properties: dict[str, str] = {}
        has_children = False

        for child in node.children:
            if child.type == "identifier":
                node_name = _get_node_text(child)
            elif child.type == "node_field":
                self._extract_node_field(child, arguments, properties)
            elif child.type == "node_children":
                has_children = True
                # Recursively process children
                for nested in child.children:
                    if nested.type == "node":
                        self._extract_symbols(nested, path, depth + 1)

        if not node_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        # Determine kind based on structure
        kind = "section" if has_children else "node"

        symbol_id = _make_symbol_id(rel_path, node_name, kind, line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Build signature
        sig_parts = [node_name]
        if arguments:
            sig_parts.extend(f'"{arg}"' for arg in arguments[:3])
            if len(arguments) > 3:
                sig_parts.append("...")
        if properties:
            prop_strs = [f"{k}={v}" for k, v in list(properties.items())[:3]]
            sig_parts.extend(prop_strs)
            if len(properties) > 3:
                sig_parts.append("...")
        if has_children:
            sig_parts.append("{ ... }")

        signature = " ".join(sig_parts)

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=node_name,
            kind=kind,
            language="kdl",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=signature,
            meta={
                "depth": depth,
                "arg_count": len(arguments),
                "prop_count": len(properties),
                "has_children": has_children,
                "arguments": arguments[:5],  # Limit for brevity
                "properties": dict(list(properties.items())[:5]),
            },
        )
        self._symbols.append(symbol)

    def _extract_node_field(
        self,
        node: "tree_sitter.Node",
        arguments: list[str],
        properties: dict[str, str],
    ) -> None:
        """Extract arguments and properties from a node_field."""
        for child in node.children:
            if child.type == "value":
                # Anonymous argument
                value = self._extract_value(child)
                if value:
                    arguments.append(value)
            elif child.type == "prop":
                # Named property
                prop_name = ""
                prop_value = ""
                for prop_child in child.children:
                    if prop_child.type == "identifier":
                        prop_name = _get_node_text(prop_child)
                    elif prop_child.type == "value":
                        prop_value = self._extract_value(prop_child)
                if prop_name:
                    properties[prop_name] = prop_value

    def _extract_value(self, node: "tree_sitter.Node") -> str:
        """Extract a value from a value node."""
        for child in node.children:
            if child.type == "string":
                # Look for string_fragment
                for string_child in child.children:
                    if string_child.type == "string_fragment":
                        return _get_node_text(string_child)
                # Fallback to full string text without quotes  # pragma: no cover
                text = _get_node_text(child)  # pragma: no cover
                if text.startswith('"') and text.endswith('"'):  # pragma: no cover
                    return text[1:-1]  # pragma: no cover
            elif child.type == "keyword":
                # true, false, null
                return _get_node_text(child)
            elif child.type == "number":
                return _get_node_text(child)
        return ""  # pragma: no cover


def analyze_kdl(repo_root: Path) -> KdlAnalysisResult:
    """Analyze KDL configuration files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        KdlAnalysisResult containing extracted symbols
    """
    if not is_kdl_tree_sitter_available():
        warnings.warn(
            "KDL analysis skipped: tree-sitter-kdl not available",
            UserWarning,
            stacklevel=2,
        )
        return KdlAnalysisResult(
            symbols=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "kdl", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-kdl not available",
        )

    analyzer = KdlAnalyzer(repo_root)
    return analyzer.analyze()
