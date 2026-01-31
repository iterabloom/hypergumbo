"""Puppet manifest analyzer using tree-sitter.

Puppet is a configuration management tool that uses a declarative language
to describe system configuration. Understanding Puppet manifests helps with
infrastructure-as-code analysis and system configuration auditing.

How It Works
------------
1. Uses tree-sitter-puppet grammar from tree-sitter-language-pack
2. Extracts classes, defined types, resources, and nodes
3. Identifies include statements and resource relationships

Symbols Extracted
-----------------
- **Classes**: Puppet class definitions
- **Defined types**: Custom resource types (define)
- **Resources**: Resource declarations (package, service, file, etc.)
- **Nodes**: Node definitions
- **Includes**: Include statements

Edges Extracted
---------------
- **includes_class**: Links include statements to class definitions
- **requires_resource**: Links resource dependencies

Why This Design
---------------
- Puppet is widely used for infrastructure management
- Class/define patterns show reusable configuration components
- Resource relationships reveal deployment dependencies
- Node definitions map infrastructure topology
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


PASS_ID = "puppet.tree_sitter"
PASS_VERSION = "0.1.0"


class PuppetAnalysisResult:
    """Result of Puppet manifest analysis."""

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


def is_puppet_tree_sitter_available() -> bool:
    """Check if tree-sitter-puppet is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("puppet")
        return True
    except Exception:  # pragma: no cover
        return False


def find_puppet_files(repo_root: Path) -> list[Path]:
    """Find all Puppet manifest files in the repository."""
    files: list[Path] = []
    files.extend(repo_root.glob("**/*.pp"))
    return sorted(set(files))


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str, line: int) -> str:
    """Create a stable symbol ID."""
    return f"puppet:{path}:{kind}:{line}:{name}"


class PuppetAnalyzer:
    """Analyzer for Puppet manifest files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0
        self._class_registry: dict[str, str] = {}  # class name -> symbol id

    def analyze(self) -> PuppetAnalysisResult:
        """Run the Puppet analysis."""
        start_time = time.time()

        files = find_puppet_files(self.repo_root)
        if not files:
            return PuppetAnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("puppet")

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
            toolchain={"name": "puppet", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return PuppetAnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "class_definition":
            self._extract_class(node, path)
        elif node.type == "defined_resource_type":
            self._extract_defined_type(node, path)
        elif node.type == "resource_declaration":
            self._extract_resource(node, path)
        elif node.type == "node_definition":
            self._extract_node(node, path)
        elif node.type == "include_statement":
            self._extract_include(node, path)

        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_class(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a class definition."""
        class_name = ""
        params: list[str] = []

        for child in node.children:
            if child.type == "identifier":
                class_name = _get_node_text(child)
            elif child.type == "parameter_list":
                params = self._extract_params(child)

        if not class_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        symbol_id = _make_symbol_id(rel_path, class_name, "class", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Register class for edge creation
        self._class_registry[class_name] = symbol_id

        param_str = ", ".join(params) if params else ""
        signature = f"class {class_name}({param_str})" if params else f"class {class_name}"

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=class_name,
            kind="class",
            language="puppet",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=signature,
            meta={
                "params": params,
                "param_count": len(params),
            },
        )
        self._symbols.append(symbol)

    def _extract_defined_type(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a defined resource type."""
        type_name = ""
        params: list[str] = []

        for child in node.children:
            if child.type == "class_identifier":
                type_name = _get_node_text(child)
            elif child.type == "identifier" and not type_name:  # pragma: no cover
                type_name = _get_node_text(child)
            elif child.type == "parameter_list":
                params = self._extract_params(child)

        if not type_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        symbol_id = _make_symbol_id(rel_path, type_name, "defined_type", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        param_str = ", ".join(params) if params else ""
        signature = f"define {type_name}({param_str})" if params else f"define {type_name}"

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=type_name,
            kind="defined_type",
            language="puppet",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=signature,
            meta={
                "params": params,
                "param_count": len(params),
            },
        )
        self._symbols.append(symbol)

    def _extract_params(self, node: "tree_sitter.Node") -> list[str]:
        """Extract parameter names from a parameter list."""
        params: list[str] = []
        for child in node.children:
            if child.type == "parameter":
                for param_child in child.children:
                    if param_child.type == "variable":
                        var_name = _get_node_text(param_child)
                        # Remove $ prefix for cleaner display
                        if var_name.startswith("$"):
                            var_name = var_name[1:]
                        params.append(var_name)
                        break
        return params

    def _extract_resource(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a resource declaration."""
        resource_type = ""
        resource_title = ""
        attributes: dict[str, str] = {}

        for child in node.children:
            if child.type == "identifier":
                resource_type = _get_node_text(child)
            elif child.type == "class_identifier":
                resource_type = _get_node_text(child)
            elif child.type == "string":
                if not resource_title:
                    resource_title = _get_node_text(child).strip("'\"")
            elif child.type == "attribute":
                attr_text = _get_node_text(child)
                if "=>" in attr_text:
                    parts = attr_text.split("=>", 1)
                    key = parts[0].strip()
                    value = parts[1].strip() if len(parts) > 1 else ""
                    attributes[key] = value

        if not resource_type:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        name = f"{resource_type}[{resource_title}]" if resource_title else resource_type
        symbol_id = _make_symbol_id(rel_path, name, "resource", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=name,
            kind="resource",
            language="puppet",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"{resource_type} {{ '{resource_title}': ... }}",
            meta={
                "resource_type": resource_type,
                "title": resource_title,
                "ensure": attributes.get("ensure", ""),
            },
        )
        self._symbols.append(symbol)

        # Create requires_resource edges
        if "require" in attributes:
            self._create_require_edge(symbol_id, attributes["require"], line)
        if "notify" in attributes:
            self._create_notify_edge(symbol_id, attributes["notify"], line)

    def _create_require_edge(self, src_id: str, require_value: str, line: int) -> None:
        """Create a requires_resource edge."""
        # Parse require value like "Package['nginx']"
        edge = Edge.create(
            src=src_id,
            dst=f"puppet:resource:{require_value}",
            edge_type="requires_resource",
            line=line,
            origin=PASS_ID,
            origin_run_id=self._execution_id,
            evidence_type="require",
            confidence=0.9,
        )
        self._edges.append(edge)

    def _create_notify_edge(self, src_id: str, notify_value: str, line: int) -> None:
        """Create a notifies_resource edge."""
        edge = Edge.create(
            src=src_id,
            dst=f"puppet:resource:{notify_value}",
            edge_type="notifies_resource",
            line=line,
            origin=PASS_ID,
            origin_run_id=self._execution_id,
            evidence_type="notify",
            confidence=0.9,
        )
        self._edges.append(edge)

    def _extract_node(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a node definition."""
        node_name = ""

        for child in node.children:
            if child.type == "node_name":
                # Get the string content
                for name_child in child.children:
                    if name_child.type == "string":
                        node_name = _get_node_text(name_child).strip("'\"")
                        break
                if not node_name:  # pragma: no cover
                    node_name = _get_node_text(child).strip("'\"")

        if not node_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        symbol_id = _make_symbol_id(rel_path, node_name, "node", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=node_name,
            kind="node",
            language="puppet",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"node '{node_name}'",
            meta={},
        )
        self._symbols.append(symbol)

    def _extract_include(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract an include statement."""
        class_name = ""

        for child in node.children:
            if child.type == "identifier":
                class_name = _get_node_text(child)
            elif child.type == "class_identifier":  # pragma: no cover
                class_name = _get_node_text(child)

        if not class_name:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        line = node.start_point[0] + 1

        symbol_id = _make_symbol_id(rel_path, f"include {class_name}", "include", line)
        span = Span(
            start_line=line,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
            id=symbol_id,
            stable_id=symbol_id,
            name=f"include {class_name}",
            kind="include",
            language="puppet",
            path=str(rel_path),
            span=span,
            origin=PASS_ID,
            signature=f"include {class_name}",
            meta={"class_name": class_name},
        )
        self._symbols.append(symbol)

        # Create edge to class if defined
        if class_name in self._class_registry:
            edge = Edge.create(
                src=symbol_id,
                dst=self._class_registry[class_name],
                edge_type="includes_class",
                line=line,
                origin=PASS_ID,
                origin_run_id=self._execution_id,
                evidence_type="include",
                confidence=0.95,
            )
            self._edges.append(edge)


def analyze_puppet(repo_root: Path) -> PuppetAnalysisResult:
    """Analyze Puppet manifest files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        PuppetAnalysisResult containing extracted symbols and edges
    """
    if not is_puppet_tree_sitter_available():
        warnings.warn(
            "Puppet analysis skipped: tree-sitter-puppet not available",
            UserWarning,
            stacklevel=2,
        )
        return PuppetAnalysisResult(
            symbols=[],
            edges=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "puppet", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-puppet not available",
        )

    analyzer = PuppetAnalyzer(repo_root)
    return analyzer.analyze()
