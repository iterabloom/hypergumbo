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

Two-pass analysis:
- Pass 1: Extract all symbols (resources, data, modules, etc.)
- Pass 2: Extract reference edges between symbols
"""

from __future__ import annotations

import importlib.util
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .base import iter_tree
from ..ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "hcl-v1"
PASS_VERSION = "1.0.0"


def is_hcl_tree_sitter_available() -> bool:
    """Check if tree-sitter and hcl grammar are available."""
    ts_spec = importlib.util.find_spec("tree_sitter")
    if ts_spec is None:
        return False
    hcl_spec = importlib.util.find_spec("tree_sitter_hcl")
    return hcl_spec is not None


def find_hcl_files(root: Path) -> list[Path]:
    """Find all HCL/Terraform files in a directory tree.

    Identifies files by extensions:
    - .tf: Terraform configuration
    - .hcl: Generic HCL (Packer, Consul, etc.)
    """
    hcl_files: list[Path] = []
    hcl_extensions = (".tf", ".hcl")

    for path in root.rglob("*"):
        if not path.is_file():  # pragma: no cover - directories skipped
            continue

        # Skip Terraform cache and lock files
        if any(
            part.startswith(".") or part == "terraform.tfstate.d"
            for part in path.parts
        ):  # pragma: no cover - test dirs don't have these
            continue

        if path.suffix in hcl_extensions:
            hcl_files.append(path)

    return hcl_files


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first direct child with given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_all_children_by_type(
    node: "tree_sitter.Node", type_name: str
) -> list["tree_sitter.Node"]:
    """Find all direct children with given type."""
    return [child for child in node.children if child.type == type_name]


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Get text content of a node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _extract_string_value(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract string value from string_lit node."""
    template_lit = _find_child_by_type(node, "template_literal")
    if template_lit:
        return _node_text(template_lit, source)
    return None  # pragma: no cover


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"hcl:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for an HCL file node (used as import edge source)."""
    return f"hcl:{path}:1-1:file:file"


@dataclass
class HCLAnalysisResult:
    """Result of analyzing HCL files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)


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
            block_type = _node_text(child, source)
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
    body = _find_child_by_type(node, "body")
    if body:
        for child in body.children:
            if child.type == "attribute":
                ident = _find_child_by_type(child, "identifier")
                if ident:
                    names.append(_node_text(ident, source))
    return names


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> FileAnalysis:
    """Extract symbols from a single HCL file."""
    analysis = FileAnalysis()
    rel_path = str(file_path)

    try:
        source = file_path.read_bytes()
    except (OSError, IOError):  # pragma: no cover
        return analysis

    tree = parser.parse(source)

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
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "resource")
                symbol = Symbol(
                    id=symbol_id,
                    name=name,
                    kind="resource",
                    language="hcl",
                    path=rel_path,
                    span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

            elif block_type == "data" and len(labels) >= 2:
                # data "type" "name" -> data.type.name
                name = f"data.{labels[0]}.{labels[1]}"
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "data")
                symbol = Symbol(
                    id=symbol_id,
                    name=name,
                    kind="data",
                    language="hcl",
                    path=rel_path,
                    span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

            elif block_type == "variable" and len(labels) >= 1:
                # variable "name" -> var.name
                name = f"var.{labels[0]}"
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "variable")
                symbol = Symbol(
                    id=symbol_id,
                    name=name,
                    kind="variable",
                    language="hcl",
                    path=rel_path,
                    span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

            elif block_type == "output" and len(labels) >= 1:
                # output "name" -> output.name
                name = f"output.{labels[0]}"
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "output")
                symbol = Symbol(
                    id=symbol_id,
                    name=name,
                    kind="output",
                    language="hcl",
                    path=rel_path,
                    span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

            elif block_type == "module" and len(labels) >= 1:
                # module "name" -> module.name
                name = f"module.{labels[0]}"
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "module")
                symbol = Symbol(
                    id=symbol_id,
                    name=name,
                    kind="module",
                    language="hcl",
                    path=rel_path,
                    span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

            elif block_type == "provider" and len(labels) >= 1:
                # provider "name" -> provider.name
                name = f"provider.{labels[0]}"
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "provider")
                symbol = Symbol(
                    id=symbol_id,
                    name=name,
                    kind="provider",
                    language="hcl",
                    path=rel_path,
                    span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.append(symbol)
                analysis.symbol_by_name[name] = symbol

            elif block_type == "locals":
                # Extract individual local values
                local_names = _extract_local_names(node, source)
                for local_name in local_names:
                    name = f"local.{local_name}"
                    symbol_id = _make_symbol_id(rel_path, start_line, end_line, name, "local")
                    symbol = Symbol(
                        id=symbol_id,
                        name=name,
                        kind="local",
                        language="hcl",
                        path=rel_path,
                        span=Span(start_line, end_line, node.start_point[1], node.end_point[1]),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    )
                    analysis.symbols.append(symbol)
                    analysis.symbol_by_name[name] = symbol

    return analysis


def _extract_reference_chain(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract reference chain from variable_expr with get_attr nodes.

    Parses: aws_instance.web.id -> "aws_instance.web"
    Parses: var.instance_type -> "var.instance_type"
    """
    if node.type != "variable_expr":
        return None  # pragma: no cover - type guard

    parts: list[str] = []
    ident = _find_child_by_type(node, "identifier")
    if ident:
        parts.append(_node_text(ident, source))

    # Walk siblings for get_attr chains
    for sibling in node.parent.children if node.parent else []:
        if sibling.type == "get_attr":
            get_ident = _find_child_by_type(sibling, "identifier")
            if get_ident:
                parts.append(_node_text(get_ident, source))

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
    body = _find_child_by_type(node, "body")
    if not body:
        return None  # pragma: no cover

    for attr in _find_all_children_by_type(body, "attribute"):
        ident = _find_child_by_type(attr, "identifier")
        if ident and _node_text(ident, source) == "source":
            expr = _find_child_by_type(attr, "expression")
            if expr:
                lit_val = _find_child_by_type(expr, "literal_value")
                if lit_val:
                    str_lit = _find_child_by_type(lit_val, "string_lit")
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


def _extract_edges_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Extract edges from a file using global symbol knowledge."""
    edges: list[Edge] = []
    rel_path = str(file_path)
    file_id = _make_file_id(rel_path)

    try:
        source = file_path.read_bytes()
    except (OSError, IOError):  # pragma: no cover
        return edges

    tree = parser.parse(source)

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
                        origin_run_id=run.execution_id,
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
                    elif ref_name in global_symbols:  # pragma: no cover - cross-file
                        target = global_symbols[ref_name]
                        confidence = 0.85

                    if target and target.id != current_symbol.id:
                        edges.append(Edge.create(
                            src=current_symbol.id,
                            dst=target.id,
                            edge_type="depends_on",
                            line=ref_line,
                            evidence_type="reference",
                            confidence=confidence,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

    return edges


def analyze_hcl(root: Path) -> HCLAnalysisResult:
    """Analyze HCL/Terraform files in a directory.

    Uses tree-sitter-hcl for parsing. Falls back gracefully if not available.
    """
    if not is_hcl_tree_sitter_available():
        warnings.warn(
            "tree-sitter-hcl not available. Install with: pip install tree-sitter-hcl"
        )
        return HCLAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-hcl not available",
        )

    try:
        import tree_sitter
        import tree_sitter_hcl

        language = tree_sitter.Language(tree_sitter_hcl.language())
        parser = tree_sitter.Parser(language)
    except Exception as e:
        return HCLAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to load HCL parser: {e}",
        )

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_files = find_hcl_files(root)
    if not all_files:  # pragma: no cover - no HCL files in test
        return HCLAnalysisResult(run=run)

    # Pass 1: Extract symbols from all files
    all_symbols: list[Symbol] = []
    file_analyses: dict[Path, FileAnalysis] = {}
    global_symbols: dict[str, Symbol] = {}

    for hcl_file in all_files:
        analysis = _extract_symbols_from_file(hcl_file, parser, run)
        file_analyses[hcl_file] = analysis
        all_symbols.extend(analysis.symbols)

        # Collect symbols globally for cross-file resolution
        for name, sym in analysis.symbol_by_name.items():
            global_symbols[name] = sym

    # Pass 2: Extract edges using global symbol knowledge
    all_edges: list[Edge] = []

    for hcl_file, analysis in file_analyses.items():
        edges = _extract_edges_from_file(
            hcl_file, parser, analysis.symbol_by_name, global_symbols, run
        )
        all_edges.extend(edges)

    return HCLAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
