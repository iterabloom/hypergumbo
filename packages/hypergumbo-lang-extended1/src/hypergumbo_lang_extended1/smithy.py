# SPDX-License-Identifier: AGPL-3.0-or-later
"""Smithy API definition language analyzer.

This module analyzes Smithy files (.smithy) using tree-sitter. Smithy is AWS's
interface definition language (IDL) for defining web services. It's used by
AWS to define their service APIs and is available as an open specification.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
- Pass 1: Collect symbols (services, operations, structures, resources, etc.)
- Pass 2: Extract edges (operation references, type references)

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the Smithy-specific extraction
logic.

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

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    make_symbol_id,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("smithy")


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def find_smithy_files(repo_root: Path) -> list[Path]:
    """Find all Smithy files in the repository."""
    return sorted(find_files(repo_root, ["*.smithy"]))


def is_smithy_tree_sitter_available() -> bool:
    """Check if tree-sitter-smithy is available."""
    return _analyzer._check_grammar_available()


def _get_qualified_name(name: str, current_namespace: Optional[str]) -> str:
    """Get the fully qualified name including namespace."""
    if current_namespace:
        return f"{current_namespace}#{name}"
    return name  # pragma: no cover - Smithy files typically always have namespaces


def _get_trait_name(node: "tree_sitter.Node") -> Optional[str]:
    """Extract trait name from a trait_statement node."""
    for child in node.children:
        if child.type == "shape_id":
            return _get_node_text(child)
    return None  # pragma: no cover


def _extract_namespace(
    node: "tree_sitter.Node", rel_path: str,
    analyzer: "SmithyAnalyzer",
) -> tuple[Optional[Symbol], Optional[str]]:
    """Extract namespace declaration. Returns (symbol, namespace_name)."""
    for child in node.children:
        if child.type == "namespace" and child.text:
            text = _get_node_text(child)
            if text != "namespace":  # Skip the keyword
                sym = Symbol(
                    id=make_symbol_id("smithy", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, text, "namespace"),
                    stable_id=analyzer.compute_stable_id(node, kind="namespace"),
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
                return sym, text
    return None, None  # pragma: no cover


def _extract_shape(
    node: "tree_sitter.Node", rel_path: str, kind: str,
    current_namespace: Optional[str], analyzer: "SmithyAnalyzer",
) -> Optional[Symbol]:
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
                    trait_name = _get_trait_name(sibling)
                    if trait_name:
                        traits.append(trait_name)

    if name:
        qualified_name = _get_qualified_name(name, current_namespace)

        return Symbol(
            id=make_symbol_id("smithy", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, qualified_name, kind),
            stable_id=analyzer.compute_stable_id(node, kind=kind),
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
    return None  # pragma: no cover


def _extract_simple_shape(
    node: "tree_sitter.Node", rel_path: str,
    current_namespace: Optional[str], analyzer: "SmithyAnalyzer",
) -> Optional[Symbol]:
    """Extract a simple shape definition (e.g., string CityId)."""
    shape_type = None
    name = None

    for child in node.children:
        if child.type in ("string", "integer", "long", "short", "byte",
                          "float", "double", "boolean", "bigInteger",
                          "bigDecimal", "timestamp", "blob", "document"):
            shape_type = _get_node_text(child)  # pragma: no cover
        elif child.type == "identifier":
            name = _get_node_text(child)

    if name:
        qualified_name = _get_qualified_name(name, current_namespace)

        return Symbol(
            id=make_symbol_id("smithy", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, qualified_name, "simple_type"),
            stable_id=analyzer.compute_stable_id(node, kind="simple_type"),
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
    return None  # pragma: no cover


# ---------------------------------------------------------------------------
# Edge extraction helpers
# ---------------------------------------------------------------------------


def _extract_service_object_refs(
    node: "tree_sitter.Node", rel_path: str, service_name: str,
    symbol_registry: dict[str, str], current_namespace: Optional[str],
    run_id: str,
) -> list[Edge]:
    """Extract references from a service's node_object."""
    edges: list[Edge] = []
    for child in node.children:
        if child.type == "node_object_kvp":
            key = None
            for subchild in child.children:
                if subchild.type == "node_object_key":
                    key = _get_node_text(subchild)
                elif subchild.type == "node_value" and key == "operations":
                    edges.extend(_extract_array_refs(
                        subchild, rel_path, service_name, "contains",
                        symbol_registry, current_namespace, run_id,
                    ))
    return edges


def _extract_array_refs(
    node: "tree_sitter.Node", rel_path: str, src_name: str, edge_type: str,
    symbol_registry: dict[str, str], current_namespace: Optional[str],
    run_id: str,
) -> list[Edge]:
    """Extract references from an array value."""
    edges: list[Edge] = []
    for child in node.children:
        if child.type == "node_array":
            for subchild in child.children:
                if subchild.type == "node_value":
                    for item in subchild.children:
                        if item.type == "shape_id":
                            ref_name = _get_node_text(item)
                            edge = _make_reference_edge(
                                rel_path, src_name, ref_name, edge_type,
                                symbol_registry, current_namespace, run_id,
                            )
                            if edge:
                                edges.append(edge)
    return edges


def _extract_operation_types(
    node: "tree_sitter.Node", rel_path: str, op_name: str,
    symbol_registry: dict[str, str], current_namespace: Optional[str],
    run_id: str,
) -> list[Edge]:
    """Extract input/output type references from an operation."""
    edges: list[Edge] = []
    for child in node.children:
        if child.type == "operation_body":
            for subchild in child.children:
                if subchild.type == "operation_member":
                    edges.extend(_extract_operation_member(
                        subchild, rel_path, op_name,
                        symbol_registry, current_namespace, run_id,
                    ))
    return edges


def _extract_operation_member(
    node: "tree_sitter.Node", rel_path: str, op_name: str,
    symbol_registry: dict[str, str], current_namespace: Optional[str],
    run_id: str,
) -> list[Edge]:
    """Extract a reference from an operation member (input/output/errors)."""
    edges: list[Edge] = []
    for child in node.children:
        if child.type == "shape_id":
            ref_name = _get_node_text(child)
            edge = _make_reference_edge(
                rel_path, op_name, ref_name, "references",
                symbol_registry, current_namespace, run_id,
            )
            if edge:
                edges.append(edge)
        elif child.type == "operation_errors":
            edges.extend(_extract_error_refs(
                child, rel_path, op_name,
                symbol_registry, current_namespace, run_id,
            ))
    return edges


def _extract_error_refs(
    node: "tree_sitter.Node", rel_path: str, src_name: str,
    symbol_registry: dict[str, str], current_namespace: Optional[str],
    run_id: str,
) -> list[Edge]:
    """Extract error type references from an operation."""
    edges: list[Edge] = []
    for child in node.children:
        if child.type == "operation_error":
            ref_name = _get_node_text(child)
            edge = _make_reference_edge(
                rel_path, src_name, ref_name, "references",
                symbol_registry, current_namespace, run_id,
            )
            if edge:
                edges.append(edge)
    return edges


def _extract_member_types(
    node: "tree_sitter.Node", rel_path: str, struct_name: str,
    symbol_registry: dict[str, str], current_namespace: Optional[str],
    run_id: str,
) -> list[Edge]:
    """Extract member type references from a structure."""
    edges: list[Edge] = []
    for child in node.children:
        if child.type == "shape_members":
            for subchild in child.children:
                if subchild.type == "shape_member":
                    edges.extend(_extract_shape_member_type(
                        subchild, rel_path, struct_name,
                        symbol_registry, current_namespace, run_id,
                    ))
    return edges


def _extract_shape_member_type(
    node: "tree_sitter.Node", rel_path: str, struct_name: str,
    symbol_registry: dict[str, str], current_namespace: Optional[str],
    run_id: str,
) -> list[Edge]:
    """Extract the type reference from a shape member."""
    edges: list[Edge] = []
    for child in node.children:
        if child.type == "shape_id":
            ref_name = _get_node_text(child)
            # Filter out primitive types
            if ref_name not in ("String", "Integer", "Long", "Short",
                                "Byte", "Float", "Double", "Boolean",
                                "BigInteger", "BigDecimal", "Timestamp",
                                "Blob", "Document"):
                edge = _make_reference_edge(
                    rel_path, struct_name, ref_name, "references",
                    symbol_registry, current_namespace, run_id,
                )
                if edge:
                    edges.append(edge)
    return edges


def _make_reference_edge(
    rel_path: str, src_name: str, ref_name: str, edge_type: str,
    symbol_registry: dict[str, str], current_namespace: Optional[str],
    run_id: str,
) -> Optional[Edge]:
    """Add a reference edge between shapes."""
    # Try to resolve the reference
    qualified_ref = ref_name
    if "#" not in ref_name and current_namespace:
        qualified_ref = f"{current_namespace}#{ref_name}"

    dst_id = symbol_registry.get(qualified_ref)
    if not dst_id:
        dst_id = symbol_registry.get(ref_name)

    if dst_id:
        confidence = 1.0
        dst = dst_id
    else:
        confidence = 0.6
        dst = f"unresolved:{ref_name}"

    src_id = symbol_registry.get(src_name, f"smithy:{rel_path}:file")

    return Edge.create(
        src=src_id,
        dst=dst,
        edge_type=edge_type,
        line=1,  # Smithy doesn't have good line tracking in grammar
        origin=PASS_ID,
        origin_run_id=run_id,
        evidence_type="tree_sitter",
        confidence=confidence,
        evidence_lang="smithy",
    )


class SmithyAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Smithy API definition files using TreeSitterAnalyzer base class."""

    lang = "smithy"
    file_patterns: ClassVar[list[str]] = ["*.smithy"]
    language_pack_name = "smithy"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract Smithy symbols (services, operations, structures, etc.)."""
        analysis = FileAnalysis()
        current_namespace: Optional[str] = None

        for node in iter_tree(tree.root_node):
            if node.type == "namespace_statement":
                sym, ns_name = _extract_namespace(node, rel_path, self)
                if sym:
                    analysis.symbols.append(sym)
                    analysis.node_for_symbol[sym.id] = node
                    current_namespace = ns_name
                    # Store namespace for edge extraction
                    analysis.import_aliases["__namespace__"] = ns_name or ""

            elif node.type == "service_statement":
                sym = _extract_shape(node, rel_path, "service", current_namespace, self)
                if sym:
                    analysis.symbols.append(sym)
                    analysis.node_for_symbol[sym.id] = node
                    analysis.symbol_by_name[sym.name] = sym

            elif node.type == "operation_statement":
                sym = _extract_shape(node, rel_path, "operation", current_namespace, self)
                if sym:
                    analysis.symbols.append(sym)
                    analysis.node_for_symbol[sym.id] = node
                    analysis.symbol_by_name[sym.name] = sym

            elif node.type == "structure_statement":
                sym = _extract_shape(node, rel_path, "structure", current_namespace, self)
                if sym:
                    analysis.symbols.append(sym)
                    analysis.node_for_symbol[sym.id] = node
                    analysis.symbol_by_name[sym.name] = sym

            elif node.type == "resource_statement":
                sym = _extract_shape(node, rel_path, "resource", current_namespace, self)
                if sym:
                    analysis.symbols.append(sym)
                    analysis.node_for_symbol[sym.id] = node
                    analysis.symbol_by_name[sym.name] = sym

            elif node.type == "simple_shape_statement":
                sym = _extract_simple_shape(node, rel_path, current_namespace, self)
                if sym:
                    analysis.symbols.append(sym)
                    analysis.node_for_symbol[sym.id] = node
                    analysis.symbol_by_name[sym.name] = sym

            elif node.type == "union_statement":
                sym = _extract_shape(node, rel_path, "union", current_namespace, self)
                if sym:
                    analysis.symbols.append(sym)
                    analysis.node_for_symbol[sym.id] = node
                    analysis.symbol_by_name[sym.name] = sym

            elif node.type == "enum_statement":
                sym = _extract_shape(node, rel_path, "enum", current_namespace, self)
                if sym:
                    analysis.symbols.append(sym)
                    analysis.node_for_symbol[sym.id] = node
                    analysis.symbol_by_name[sym.name] = sym

            elif node.type == "list_statement":
                sym = _extract_shape(node, rel_path, "list", current_namespace, self)
                if sym:
                    analysis.symbols.append(sym)
                    analysis.node_for_symbol[sym.id] = node
                    analysis.symbol_by_name[sym.name] = sym

            elif node.type == "map_statement":
                sym = _extract_shape(node, rel_path, "map", current_namespace, self)
                if sym:
                    analysis.symbols.append(sym)
                    analysis.node_for_symbol[sym.id] = node
                    analysis.symbol_by_name[sym.name] = sym

        return analysis

    def register_symbol(self, symbol: Symbol, global_symbols: dict) -> None:
        """Register symbol with both full name and short name."""
        global_symbols[symbol.name] = symbol
        # Also register short name (without namespace)
        if "#" in symbol.name:
            short_name = symbol.name.split("#")[-1]
            if short_name not in global_symbols:
                global_symbols[short_name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract reference and containment edges from a Smithy file."""
        edges: list[Edge] = []
        current_namespace: Optional[str] = import_aliases.get("__namespace__")

        # Build a name->id registry from global_symbols
        symbol_registry: dict[str, str] = {}
        for name, sym in global_symbols.items():
            if isinstance(sym, Symbol):
                symbol_registry[name] = sym.id

        for node in iter_tree(tree.root_node):
            if node.type == "namespace_statement":
                # Update current namespace
                for child in node.children:
                    if child.type == "namespace" and child.text:
                        text = _get_node_text(child)
                        if text != "namespace":
                            current_namespace = text
                            break

            elif node.type == "service_statement":
                service_name = None
                for child in node.children:
                    if child.type == "identifier":
                        service_name = _get_qualified_name(
                            _get_node_text(child), current_namespace,
                        )
                        break

                if service_name:
                    # Extract service-to-operation relationships
                    for child in node.children:
                        if child.type == "node_object":
                            edges.extend(_extract_service_object_refs(
                                child, rel_path, service_name,
                                symbol_registry, current_namespace,
                                run.execution_id,
                            ))

            elif node.type == "operation_statement":
                op_name = None
                for child in node.children:
                    if child.type == "identifier":
                        op_name = _get_qualified_name(
                            _get_node_text(child), current_namespace,
                        )
                        break

                if op_name:
                    edges.extend(_extract_operation_types(
                        node, rel_path, op_name,
                        symbol_registry, current_namespace,
                        run.execution_id,
                    ))

            elif node.type == "structure_statement":
                struct_name = None
                for child in node.children:
                    if child.type == "identifier":
                        struct_name = _get_qualified_name(
                            _get_node_text(child), current_namespace,
                        )
                        break

                if struct_name:
                    edges.extend(_extract_member_types(
                        node, rel_path, struct_name,
                        symbol_registry, current_namespace,
                        run.execution_id,
                    ))

        return edges


_analyzer = SmithyAnalyzer()


@register_analyzer("smithy")
def analyze_smithy(repo_root: Path) -> AnalysisResult:
    """Analyze Smithy files in the repository.

    Args:
        repo_root: Root path of the repository to analyze

    Returns:
        AnalysisResult containing symbols and edges
    """
    return _analyzer.analyze(repo_root)
