"""HCL/Terraform analyzer using tree-sitter.

This analyzer extracts resources, data sources, modules, variables, outputs,
providers, and locals from Terraform and HCL files. It uses tree-sitter-hcl
for parsing when available, falling back gracefully when the grammar is not
installed.

Node types handled:
- block: Terraform blocks (resource, data, module, variable, output, locals, provider)
  - First identifier is block type
  - string_lit nodes are labels (resource type, resource name)
- attribute: Key-value pairs within blocks
- variable_expr with get_attr: References (var.x, aws_instance.web.id)

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract all symbols (resources, data, modules, etc.)
2. Pass 2: Extract reference edges between symbols

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the HCL-specific extraction
logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("hcl")


def find_hcl_files(root: Path) -> list[Path]:
    """Find all HCL/Terraform files in a directory tree, excluding vendor dirs.

    Identifies files by extensions:
    - .tf: Terraform configuration
    - .hcl: Generic HCL (Packer, Consul, etc.)
    """
    return list(find_files(root, ["*.tf", "*.hcl"]))


def _find_all_children_by_type(
    node: "tree_sitter.Node", type_name: str
) -> list["tree_sitter.Node"]:
    """Find all direct children with given type."""
    return [child for child in node.children if child.type == type_name]


def _extract_string_value(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract string value from string_lit node."""
    template_lit = find_child_by_type(node, "template_literal")
    if template_lit:
        return node_text(template_lit, source)
    return None  # pragma: no cover


def _extract_block_info(
    node: "tree_sitter.Node", source: bytes
) -> tuple[str | None, list[str]]:
    """Extract block type and labels from a block node.

    Returns (block_type, [label1, label2, ...])
    For 'resource "aws_instance" "web"' returns ("resource", ["aws_instance", "web"])
    """
    children = list(node.children)
    block_type: str | None = None
    labels: list[str] = []

    for child in children:
        if child.type == "identifier" and block_type is None:
            block_type = node_text(child, source)
        elif child.type == "string_lit":
            val = _extract_string_value(child, source)
            if val:
                labels.append(val)
        elif child.type == "block_start":
            break  # Stop before body

    return block_type, labels


def _extract_local_names(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract local value names from a locals block body."""
    names: list[str] = []
    body = find_child_by_type(node, "body")
    if body:
        for child in body.children:
            if child.type == "attribute":
                ident = find_child_by_type(child, "identifier")
                if ident:
                    names.append(node_text(ident, source))
    return names


def _extract_symbols_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    rel_path: str,
    run_id: str,
    symbols: list[Symbol],
    symbol_by_name: dict[str, Symbol],
) -> None:
    """Extract symbols from a parsed HCL tree.

    Args:
        tree: Parsed tree-sitter tree
        source: Source file bytes
        rel_path: Relative path to file
        run_id: The execution ID for provenance
        symbols: List to append symbols to
        symbol_by_name: Dict to register symbols by name
    """
    for node in iter_tree(tree.root_node):
        if node.type == "block":
            block_type, labels = _extract_block_info(node, source)
            if not block_type:
                continue  # pragma: no cover

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1

            if block_type == "resource" and len(labels) >= 2:
                # resource "type" "name" -> type.name
                name = f"{labels[0]}.{labels[1]}"
                symbol_id = make_symbol_id("hcl", rel_path, start_line, end_line, name, "resource")
                symbol = Symbol(
                    id=symbol_id,
                    name=name,
                    kind="resource",
                    language="hcl",
                    path=rel_path,
                    span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                symbols.append(symbol)
                symbol_by_name[name] = symbol

            elif block_type == "data" and len(labels) >= 2:
                # data "type" "name" -> data.type.name
                name = f"data.{labels[0]}.{labels[1]}"
                symbol_id = make_symbol_id("hcl", rel_path, start_line, end_line, name, "data")
                symbol = Symbol(
                    id=symbol_id,
                    name=name,
                    kind="data",
                    language="hcl",
                    path=rel_path,
                    span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                symbols.append(symbol)
                symbol_by_name[name] = symbol

            elif block_type == "variable" and len(labels) >= 1:
                # variable "name" -> var.name
                name = f"var.{labels[0]}"
                symbol_id = make_symbol_id("hcl", rel_path, start_line, end_line, name, "variable")
                symbol = Symbol(
                    id=symbol_id,
                    name=name,
                    kind="variable",
                    language="hcl",
                    path=rel_path,
                    span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                symbols.append(symbol)
                symbol_by_name[name] = symbol

            elif block_type == "output" and len(labels) >= 1:
                # output "name" -> output.name
                name = f"output.{labels[0]}"
                symbol_id = make_symbol_id("hcl", rel_path, start_line, end_line, name, "output")
                symbol = Symbol(
                    id=symbol_id,
                    name=name,
                    kind="output",
                    language="hcl",
                    path=rel_path,
                    span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                symbols.append(symbol)
                symbol_by_name[name] = symbol

            elif block_type == "module" and len(labels) >= 1:
                # module "name" -> module.name
                name = f"module.{labels[0]}"
                symbol_id = make_symbol_id("hcl", rel_path, start_line, end_line, name, "module")
                symbol = Symbol(
                    id=symbol_id,
                    name=name,
                    kind="module",
                    language="hcl",
                    path=rel_path,
                    span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                symbols.append(symbol)
                symbol_by_name[name] = symbol

            elif block_type == "provider" and len(labels) >= 1:
                # provider "name" -> provider.name
                name = f"provider.{labels[0]}"
                symbol_id = make_symbol_id("hcl", rel_path, start_line, end_line, name, "provider")
                symbol = Symbol(
                    id=symbol_id,
                    name=name,
                    kind="provider",
                    language="hcl",
                    path=rel_path,
                    span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                symbols.append(symbol)
                symbol_by_name[name] = symbol

            elif block_type == "locals":
                # Extract individual local values
                local_names = _extract_local_names(node, source)
                for local_name in local_names:
                    name = f"local.{local_name}"
                    symbol_id = make_symbol_id("hcl", rel_path, start_line, end_line, name, "local")
                    symbol = Symbol(
                        id=symbol_id,
                        name=name,
                        kind="local",
                        language="hcl",
                        path=rel_path,
                        span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                    symbols.append(symbol)
                    symbol_by_name[name] = symbol


def _extract_reference_chain(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract reference chain from variable_expr with get_attr nodes.

    Parses: aws_instance.web.id -> "aws_instance.web"
    Parses: var.instance_type -> "var.instance_type"
    """
    if node.type != "variable_expr":
        return None  # pragma: no cover - type guard

    parts: list[str] = []
    ident = find_child_by_type(node, "identifier")
    if ident:
        parts.append(node_text(ident, source))

    # Walk siblings for get_attr chains
    for sibling in node.parent.children if node.parent else []:
        if sibling.type == "get_attr":
            get_ident = find_child_by_type(sibling, "identifier")
            if get_ident:
                parts.append(node_text(get_ident, source))

    if len(parts) >= 2:
        # Return first two parts as the referenced symbol
        return ".".join(parts[:2])
    return None


def _find_references_in_expression(
    node: "tree_sitter.Node", source: bytes
) -> list[tuple[str, int]]:
    """Find all references in an expression node.

    Returns list of (reference_name, line_number) tuples.
    """
    refs: list[tuple[str, int]] = []

    for n in iter_tree(node):
        if n.type == "variable_expr":
            ref = _extract_reference_chain(n, source)
            if ref:
                refs.append((ref, n.start_point[0] + 1))

    return refs


def _extract_module_source(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract source path from a module block."""
    body = find_child_by_type(node, "body")
    if not body:
        return None  # pragma: no cover

    for attr in _find_all_children_by_type(body, "attribute"):
        ident = find_child_by_type(attr, "identifier")
        if ident and node_text(ident, source) == "source":
            expr = find_child_by_type(attr, "expression")
            if expr:
                lit_val = find_child_by_type(expr, "literal_value")
                if lit_val:
                    str_lit = find_child_by_type(lit_val, "string_lit")
                    if str_lit:
                        return _extract_string_value(str_lit, source)
    return None  # pragma: no cover - no source attribute


def _find_enclosing_block_symbol(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Symbol | None:
    """Find the enclosing block's symbol by walking up parent nodes."""
    current = node.parent
    while current is not None:
        if current.type == "block":
            block_type, labels = _extract_block_info(current, source)

            sym_name: str | None = None
            if block_type == "resource" and len(labels) >= 2:
                sym_name = f"{labels[0]}.{labels[1]}"
            elif block_type == "data" and len(labels) >= 2:
                sym_name = f"data.{labels[0]}.{labels[1]}"
            elif block_type == "module" and len(labels) >= 1:
                sym_name = f"module.{labels[0]}"
            elif block_type == "output" and len(labels) >= 1:
                sym_name = f"output.{labels[0]}"

            if sym_name and sym_name in local_symbols:
                return local_symbols[sym_name]
        current = current.parent
    return None


def _extract_edges_from_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    rel_path: str,
    local_symbols: dict[str, Symbol],
    resolver: "NameResolver",
    run_id: str,
) -> list[Edge]:
    """Extract edges from a parsed HCL tree using global symbol knowledge.

    Args:
        tree: Parsed tree-sitter tree
        source: Source file bytes
        rel_path: Relative path to file
        local_symbols: Symbol-by-name dict for this file
        resolver: NameResolver for cross-file symbol lookup
        run_id: The execution ID for provenance

    Returns:
        List of Edge instances.
    """
    edges: list[Edge] = []
    file_id = make_file_id("hcl", rel_path)

    for node in iter_tree(tree.root_node):
        if node.type == "block":
            block_type, labels = _extract_block_info(node, source)

            # Handle module source
            if block_type == "module" and len(labels) >= 1:
                mod_source = _extract_module_source(node, source)
                if mod_source and mod_source.startswith("./"):
                    line = node.start_point[0] + 1
                    edges.append(Edge.create(
                        src=file_id,
                        dst=mod_source,
                        edge_type="imports",
                        line=line,
                        evidence_type="module_source",
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    ))

        # Find references in expressions
        elif node.type == "expression":
            current_symbol = _find_enclosing_block_symbol(node, source, local_symbols)
            if current_symbol:
                refs = _find_references_in_expression(node, source)
                for ref_name, ref_line in refs:
                    # Try to match reference to a known symbol
                    target: Symbol | None = None
                    confidence = 0.85

                    if ref_name in local_symbols:
                        target = local_symbols[ref_name]
                        confidence = 0.95
                    else:
                        # Check global symbols via resolver
                        lookup_result = resolver.lookup(ref_name)
                        if lookup_result.found and lookup_result.symbol is not None:  # pragma: no cover - suffix fallback
                            target = lookup_result.symbol
                            confidence = 0.85 * lookup_result.confidence

                    if target and target.id != current_symbol.id:
                        edges.append(Edge.create(
                            src=current_symbol.id,
                            dst=target.id,
                            edge_type="depends_on",
                            line=ref_line,
                            evidence_type="reference",
                            confidence=confidence,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))

    return edges


class HclAnalyzer(TreeSitterAnalyzer):
    """HCL/Terraform language analyzer using tree-sitter-hcl."""

    lang = "hcl"
    file_patterns: ClassVar[list[str]] = ["*.tf", "*.hcl"]
    grammar_module = "tree_sitter_hcl"
    create_file_symbols = False

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract resources, data, variables, outputs, modules, providers, locals."""
        analysis = FileAnalysis()

        _extract_symbols_from_tree(
            tree, source, rel_path, run.execution_id,
            analysis.symbols, analysis.symbol_by_name,
        )

        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract dependency and import edges from an HCL file."""
        return _extract_edges_from_tree(
            tree, source, rel_path,
            local_symbols, resolver, run.execution_id,
        )


_analyzer = HclAnalyzer()


def is_hcl_tree_sitter_available() -> bool:
    """Check if tree-sitter and hcl grammar are available."""
    return _analyzer._check_grammar_available()


@register_analyzer("hcl")
def analyze_hcl(root: Path) -> AnalysisResult:
    """Analyze HCL/Terraform files in a directory.

    Uses tree-sitter-hcl for parsing. Falls back gracefully if not available.
    """
    return _analyzer.analyze(root)
