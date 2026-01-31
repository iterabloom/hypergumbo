"""Java properties file analyzer using tree-sitter.

Java .properties files are key-value configuration files used extensively
in Java applications, Android development, and i18n/l10n bundles.

How It Works
------------
1. Uses tree-sitter-properties grammar from tree-sitter-language-pack
2. Extracts property key-value pairs
3. Groups properties by prefix/namespace

Symbols Extracted
-----------------
- **Properties**: Key-value configuration entries

Edges Extracted
---------------
- None (properties files are typically self-contained)

Why This Design
---------------
- .properties files are ubiquitous in Java ecosystem
- Configuration values reveal application behavior
- Namespace prefixes indicate logical groupings
- i18n bundles contain user-facing strings
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


PASS_ID = "properties.tree_sitter"
PASS_VERSION = "0.1.0"


class PropertiesAnalysisResult:
    """Result of properties file analysis."""

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


def is_properties_tree_sitter_available() -> bool:
    """Check if tree-sitter-properties is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("properties")
        return True
    except Exception:  # pragma: no cover
        return False


def find_properties_files(repo_root: Path) -> list[Path]:
    """Find all properties files in the repository."""
    return sorted(repo_root.glob("**/*.properties"))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str) -> str:
    """Create a stable symbol ID."""
    return f"properties:{path}:{kind}:{name}"


# Common property prefixes and their meanings
KNOWN_PREFIXES = {
    "database": "database",
    "db": "database",
    "spring": "framework",
    "server": "server",
    "logging": "logging",
    "log": "logging",
    "app": "application",
    "application": "application",
    "mail": "mail",
    "smtp": "mail",
    "security": "security",
    "auth": "security",
    "cache": "cache",
    "redis": "cache",
    "jpa": "persistence",
    "hibernate": "persistence",
    "kafka": "messaging",
    "rabbitmq": "messaging",
    "aws": "cloud",
    "azure": "cloud",
    "gcp": "cloud",
}


class PropertiesAnalyzer:
    """Analyzer for Java properties files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0

    def analyze(self) -> PropertiesAnalysisResult:
        """Run the properties analysis."""
        start_time = time.time()

        files = find_properties_files(self.repo_root)
        if not files:
            return PropertiesAnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("properties")

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
            toolchain={"name": "properties", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return PropertiesAnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "property":
            self._extract_property(node, path)

        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_property(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a property key-value pair."""
        key = ""
        value = ""

        for child in node.children:
            if child.type == "key":
                key = _get_node_text(child)
            elif child.type == "value":
                value = _get_node_text(child)

        if not key:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, key, "property")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Extract prefix/namespace from key
        prefix = key.split(".")[0].lower() if "." in key else ""
        category = KNOWN_PREFIXES.get(prefix, "")

        # Determine if this is a sensitive key
        is_sensitive = any(
            s in key.lower()
            for s in ("password", "secret", "token", "key", "credential")
        )

        # Truncate long values in signature
        sig_value = value[:30] + "..." if len(value) > 30 else value
        if is_sensitive and value:
            sig_value = "***"

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=key,
            kind="property",
            language="properties",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"{key}={sig_value}" if value else key,
            meta={
                "value": value if not is_sensitive else "***",
                "prefix": prefix,
                "category": category,
                "is_sensitive": is_sensitive,
            },
        )
        self._symbols.append(symbol)


def analyze_properties(repo_root: Path) -> PropertiesAnalysisResult:
    """Analyze Java properties files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        PropertiesAnalysisResult containing extracted symbols and edges
    """
    if not is_properties_tree_sitter_available():
        warnings.warn(
            "Properties analysis skipped: tree-sitter-properties not available",
            UserWarning,
            stacklevel=2,
        )
        return PropertiesAnalysisResult(
            symbols=[],
            edges=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "properties", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-properties not available",
        )

    analyzer = PropertiesAnalyzer(repo_root)
    return analyzer.analyze()
