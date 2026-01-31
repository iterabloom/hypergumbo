"""Smithy API definition language analyzer.

This module analyzes Smithy files (.smithy) using tree-sitter. Smithy is AWS's
interface definition language (IDL) for defining web services. It's used by
AWS to define their service APIs and is available as an open specification.

How It Works
------------
- Pass 1: Collect symbols (services, operations, structures, resources, etc.)
- Pass 2: Extract edges (operation references, type references)

Symbol Types
------------
- service: Service definitions
- operation: Operation (RPC endpoint) definitions
- structure: Structure (data type) definitions
- resource: Resource definitions
- simple_type: Simple type aliases (string, integer, etc.)
- namespace: Namespace declarations

Edge Types
----------
- references: Type references (input/output types, member types)
- contains: Service-to-operation relationships
"""

from __future__ import annotations

import time
import uuid as uuid_module
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "smithy.tree_sitter"
PASS_VERSION = "0.1.0"


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable identifier for a symbol."""
    rel_path = str(path.relative_to(repo_root))
    return f"smithy:{rel_path}:{kind}:{name}"


def find_smithy_files(repo_root: Path) -> list[Path]:
    """Find all Smithy files in the repository."""
    return sorted(repo_root.glob("**/*.smithy"))


def is_smithy_tree_sitter_available() -> bool:
    """Check if tree-sitter-smithy is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("smithy")
        return True
    except Exception:  # pragma: no cover
        return False


class SmithyAnalysisResult:
    """Result of Smithy analysis."""

    def __init__(
        self,
        symbols: list[Symbol] | None = None,
        edges: list[Edge] | None = None,
        run: AnalysisRun | None = None,
        skipped: bool = False,
        skip_reason: str = "",
    ):
        self.symbols = symbols or []
        self.edges = edges or []
        self.run = run
        self.skipped = skipped
        self.skip_reason = skip_reason


class SmithyAnalyzer:
    """Analyzer for Smithy API definition files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._symbol_registry: dict[str, str] = {}  # name -> id
        self._run_id: str = ""
        self._current_namespace: Optional[str] = None

    def analyze(self) -> SmithyAnalysisResult:
        """Analyze all Smithy files in the repository."""
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("smithy")
        smithy_files = find_smithy_files(self.repo_root)

        if not smithy_files:
            return SmithyAnalysisResult()

        # Pass 1: Collect all symbols
        for path in smithy_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_namespace = None
                self._extract_symbols(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        # Build symbol registry
        for sym in self.symbols:
            self._symbol_registry[sym.name] = sym.id
            # Also register short name (without namespace)
            if "#" in sym.name:
                short_name = sym.name.split("#")[-1]
                if short_name not in self._symbol_registry:
                    self._symbol_registry[short_name] = sym.id

        # Pass 2: Extract edges
        for path in smithy_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_namespace = None
                self._extract_edges(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._run_id,
            version=PASS_VERSION,
            toolchain={"name": "smithy", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return SmithyAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _get_qualified_name(self, name: str) -> str:
        """Get the fully qualified name including namespace."""
        if self._current_namespace:
            return f"{self._current_namespace}#{name}"
        return name  # pragma: no cover - Smithy files typically always have namespaces

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "namespace_statement":
            # Extract namespace
            for child in node.children:
                if child.type == "namespace" and child.text:
                    text = _get_node_text(child)
                    if text != "namespace":  # Skip the keyword
                        self._current_namespace = text
                        rel_path = str(path.relative_to(self.repo_root))
                        sym = Symbol(
                            id=_make_stable_id(path, self.repo_root, text, "namespace"),
                            stable_id=_make_stable_id(path, self.repo_root, text, "namespace"),
                            name=text,
                            kind="namespace",
                            language="smithy",
                            path=rel_path,
                            span=Span(
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                                start_col=node.start_point[1],
                                end_col=node.end_point[1],
                            ),
                            origin=PASS_ID,
                        )
                        self.symbols.append(sym)
                        break

        elif node.type == "service_statement":
            self._extract_shape(node, path, "service")

        elif node.type == "operation_statement":
            self._extract_shape(node, path, "operation")

        elif node.type == "structure_statement":
            self._extract_shape(node, path, "structure")

        elif node.type == "resource_statement":
            self._extract_shape(node, path, "resource")

        elif node.type == "simple_shape_statement":
            self._extract_simple_shape(node, path)

        elif node.type == "union_statement":
            self._extract_shape(node, path, "union")

        elif node.type == "enum_statement":
            self._extract_shape(node, path, "enum")

        elif node.type == "list_statement":
            self._extract_shape(node, path, "list")

        elif node.type == "map_statement":
            self._extract_shape(node, path, "map")

        # Recurse into children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_shape(
        self, node: "tree_sitter.Node", path: Path, kind: str
    ) -> None:
        """Extract a shape definition."""
        name = None
        traits: list[str] = []

        for child in node.children:
            if child.type == "identifier":
                name = _get_node_text(child)

        # Look for traits in parent shape_statement
        parent = node.parent
        if parent and parent.type == "shape_body":
            grandparent = parent.parent
            if grandparent and grandparent.type == "shape_statement":
                for sibling in grandparent.children:
                    if sibling.type == "trait_statement":
                        trait_name = self._get_trait_name(sibling)
                        if trait_name:
                            traits.append(trait_name)

        if name:
            qualified_name = self._get_qualified_name(name)
            rel_path = str(path.relative_to(self.repo_root))

            sym = Symbol(
                id=_make_stable_id(path, self.repo_root, qualified_name, kind),
                stable_id=_make_stable_id(path, self.repo_root, qualified_name, kind),
                name=qualified_name,
                kind=kind,
                language="smithy",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                meta={"traits": traits} if traits else {},
            )
            self.symbols.append(sym)

    def _extract_simple_shape(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a simple shape definition (e.g., string CityId)."""
        shape_type = None
        name = None

        for child in node.children:
            # Note: Tree-sitter may represent these as different node types
            if child.type in ("string", "integer", "long", "short", "byte",
                              "float", "double", "boolean", "bigInteger",
                              "bigDecimal", "timestamp", "blob", "document"):
                shape_type = _get_node_text(child)  # pragma: no cover
            elif child.type == "identifier":
                name = _get_node_text(child)

        if name:
            qualified_name = self._get_qualified_name(name)
            rel_path = str(path.relative_to(self.repo_root))

            sym = Symbol(
                id=_make_stable_id(path, self.repo_root, qualified_name, "simple_type"),
                stable_id=_make_stable_id(path, self.repo_root, qualified_name, "simple_type"),
                name=qualified_name,
                kind="simple_type",
                language="smithy",
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                meta={"base_type": shape_type} if shape_type else {},
            )
            self.symbols.append(sym)

    def _get_trait_name(self, node: "tree_sitter.Node") -> Optional[str]:
        """Extract trait name from a trait_statement node."""
        for child in node.children:
            if child.type == "shape_id":
                return _get_node_text(child)
        return None  # pragma: no cover

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract edges from a syntax tree node."""
        if node.type == "namespace_statement":
            # Update current namespace
            for child in node.children:
                if child.type == "namespace" and child.text:
                    text = _get_node_text(child)
                    if text != "namespace":
                        self._current_namespace = text
                        break

        elif node.type == "service_statement":
            # Extract service-to-operation relationships
            service_name = None
            for child in node.children:
                if child.type == "identifier":
                    service_name = self._get_qualified_name(_get_node_text(child))
                    break

            if service_name:
                self._extract_service_operations(node, path, service_name)

        elif node.type == "operation_statement":
            # Extract input/output type references
            op_name = None
            for child in node.children:
                if child.type == "identifier":
                    op_name = self._get_qualified_name(_get_node_text(child))
                    break

            if op_name:
                self._extract_operation_types(node, path, op_name)

        elif node.type == "structure_statement":
            # Extract member type references
            struct_name = None
            for child in node.children:
                if child.type == "identifier":
                    struct_name = self._get_qualified_name(_get_node_text(child))
                    break

            if struct_name:
                self._extract_member_types(node, path, struct_name)

        # Recurse into children
        for child in node.children:
            self._extract_edges(child, path)

    def _extract_service_operations(
        self, node: "tree_sitter.Node", path: Path, service_name: str
    ) -> None:
        """Extract operation references from a service definition."""
        for child in node.children:
            if child.type == "node_object":
                self._extract_service_object_refs(child, path, service_name)
            self._extract_service_operations(child, path, service_name)

    def _extract_service_object_refs(
        self, node: "tree_sitter.Node", path: Path, service_name: str
    ) -> None:
        """Extract references from a service's node_object."""
        for child in node.children:
            if child.type == "node_object_kvp":
                key = None
                for subchild in child.children:
                    if subchild.type == "node_object_key":
                        key = _get_node_text(subchild)
                    elif subchild.type == "node_value" and key == "operations":
                        self._extract_array_refs(subchild, path, service_name, "contains")

    def _extract_array_refs(
        self, node: "tree_sitter.Node", path: Path, src_name: str, edge_type: str
    ) -> None:
        """Extract references from an array value."""
        for child in node.children:
            if child.type == "node_array":
                for subchild in child.children:
                    if subchild.type == "node_value":
                        for item in subchild.children:
                            if item.type == "shape_id":
                                ref_name = _get_node_text(item)
                                self._add_reference_edge(path, src_name, ref_name, edge_type)

    def _extract_operation_types(
        self, node: "tree_sitter.Node", path: Path, op_name: str
    ) -> None:
        """Extract input/output type references from an operation."""
        for child in node.children:
            if child.type == "operation_body":
                for subchild in child.children:
                    if subchild.type == "operation_member":
                        self._extract_operation_member(subchild, path, op_name)

    def _extract_operation_member(
        self, node: "tree_sitter.Node", path: Path, op_name: str
    ) -> None:
        """Extract a reference from an operation member (input/output/errors)."""
        for child in node.children:
            if child.type == "shape_id":
                ref_name = _get_node_text(child)
                self._add_reference_edge(path, op_name, ref_name, "references")
            elif child.type == "operation_errors":
                self._extract_error_refs(child, path, op_name)

    def _extract_error_refs(
        self, node: "tree_sitter.Node", path: Path, src_name: str
    ) -> None:
        """Extract error type references from an operation."""
        for child in node.children:
            if child.type == "operation_error":
                ref_name = _get_node_text(child)
                self._add_reference_edge(path, src_name, ref_name, "references")

    def _extract_member_types(
        self, node: "tree_sitter.Node", path: Path, struct_name: str
    ) -> None:
        """Extract member type references from a structure."""
        for child in node.children:
            if child.type == "shape_members":
                for subchild in child.children:
                    if subchild.type == "shape_member":
                        self._extract_shape_member_type(subchild, path, struct_name)

    def _extract_shape_member_type(
        self, node: "tree_sitter.Node", path: Path, struct_name: str
    ) -> None:
        """Extract the type reference from a shape member."""
        for child in node.children:
            if child.type == "shape_id":
                ref_name = _get_node_text(child)
                # Filter out primitive types
                if ref_name not in ("String", "Integer", "Long", "Short",
                                    "Byte", "Float", "Double", "Boolean",
                                    "BigInteger", "BigDecimal", "Timestamp",
                                    "Blob", "Document"):
                    self._add_reference_edge(path, struct_name, ref_name, "references")

    def _add_reference_edge(
        self, path: Path, src_name: str, ref_name: str, edge_type: str
    ) -> None:
        """Add a reference edge between shapes."""
        # Try to resolve the reference
        qualified_ref = ref_name
        if "#" not in ref_name and self._current_namespace:
            qualified_ref = f"{self._current_namespace}#{ref_name}"

        dst_id = self._symbol_registry.get(qualified_ref)
        if not dst_id:
            dst_id = self._symbol_registry.get(ref_name)

        if dst_id:
            confidence = 1.0
            dst = dst_id
        else:
            confidence = 0.6
            dst = f"unresolved:{ref_name}"

        src_id = self._symbol_registry.get(src_name, f"smithy:{path.relative_to(self.repo_root)}:file")

        edge = Edge.create(
            src=src_id,
            dst=dst,
            edge_type=edge_type,
            line=1,  # Smithy doesn't have good line tracking in grammar
            origin=PASS_ID,
            origin_run_id=self._run_id,
            evidence_type="tree_sitter",
            confidence=confidence,
            evidence_lang="smithy",
        )
        self.edges.append(edge)


def analyze_smithy(repo_root: Path) -> SmithyAnalysisResult:
    """Analyze Smithy files in the repository.

    Args:
        repo_root: Root path of the repository to analyze

    Returns:
        SmithyAnalysisResult containing symbols and edges
    """
    if not is_smithy_tree_sitter_available():
        warnings.warn(
            "Smithy analysis skipped: tree-sitter-smithy not available",
            UserWarning,
            stacklevel=2,
        )
        return SmithyAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-smithy not available",
        )

    analyzer = SmithyAnalyzer(repo_root)
    return analyzer.analyze()
