"""Prisma schema analyzer using tree-sitter.

This module provides static analysis for Prisma schema files (.prisma), extracting
models, enums, fields, and relationships for database schema visualization.

Prisma is an ORM for Node.js and TypeScript that uses a declarative schema file
to define database models and their relationships.

Implementation approach:
- Uses TreeSitterAnalyzer base class for two-pass orchestration
- Uses tree-sitter-language-pack for Prisma grammar
- Extracts models, enums, datasources, and generators
- Detects field relationships (@relation) for inter-model edges
- Tracks field types for schema understanding

Key constructs extracted:
- model_block: Database models (tables)
- enum_block: Enumerations
- key_value_block: Datasource and generator configs
- model_field with @relation: Foreign key relationships
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    make_symbol_id,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("prisma")


def find_prisma_files(root: Path) -> Iterator[Path]:
    """Find all Prisma schema files in the given directory."""
    for path in find_files(root, ["*.prisma"]):
        if path.is_file():
            yield path


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8")


def _get_identifier(node: "tree_sitter.Node") -> Optional[str]:
    """Get the identifier name from a node's children."""
    for child in node.children:
        if child.type == "identifier":
            return _get_node_text(child)
    return None  # pragma: no cover


def _get_relation_target(node: "tree_sitter.Node") -> Optional[str]:
    """Extract relation target model from @relation attribute.

    Looks for patterns like:
    - @relation(fields: [authorId], references: [id])
    - The target model is inferred from the field type
    """
    for child in node.children:
        if child.type == "field_type":
            type_text = _get_node_text(child).strip()
            type_text = type_text.rstrip("[]?")
            if type_text and type_text[0].isupper() and type_text not in {
                "String", "Int", "Float", "Boolean", "DateTime", "Json",
                "Bytes", "BigInt", "Decimal"
            }:
                return type_text
    return None  # pragma: no cover


class PrismaAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Prisma schema files using TreeSitterAnalyzer base class."""

    lang = "prisma"
    file_patterns: ClassVar[list[str]] = ["*.prisma"]
    language_pack_name = "prisma"

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract model, enum, and config symbols from a Prisma file."""
        analysis = FileAnalysis()
        rel_parts = Path(rel_path).parts
        repo_root = file_path
        for _ in rel_parts:
            repo_root = repo_root.parent

        self._extract_symbols_recursive(
            tree.root_node, file_path, repo_root, rel_path, run, analysis
        )
        return analysis

    def _extract_symbols_recursive(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        rel_path: str, run: "AnalysisRun", analysis: FileAnalysis,
    ) -> None:
        """Extract symbols from a syntax tree node recursively."""
        if node.type == "model_block":
            name = _get_identifier(node)
            if name:
                field_count = sum(1 for c in node.children if c.type == "model_field")
                sym = Symbol(
                    id=make_symbol_id("prisma", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, "model"),
                    stable_id=self.compute_stable_id(node, kind="model"),
                    name=name,
                    kind="class",
                    language="prisma",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"field_count": field_count, "is_model": True},
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node
                analysis.symbol_by_name[name] = sym

        elif node.type == "enum_block":
            name = _get_identifier(node)
            if name:
                identifiers = [c for c in node.children if c.type == "identifier"]
                variant_count = len(identifiers) - 1  # Subtract 1 for the enum name
                sym = Symbol(
                    id=make_symbol_id("prisma", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, "enum"),
                    stable_id=self.compute_stable_id(node, kind="enum"),
                    name=name,
                    kind="enum",
                    language="prisma",
                    path=rel_path,
                    span=Span(
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"variant_count": variant_count},
                )
                analysis.symbols.append(sym)
                analysis.node_for_symbol[sym.id] = node
                analysis.symbol_by_name[name] = sym

        elif node.type == "key_value_block":
            block_type = None
            for child in node.children:
                if child.type in ("datasource", "generator"):
                    block_type = child.type
                    break

            if block_type:
                name = _get_identifier(node)
                if name:
                    sym = Symbol(
                        id=make_symbol_id("prisma", rel_path, node.start_point[0] + 1, node.end_point[0] + 1, name, block_type),
                        stable_id=self.compute_stable_id(node, kind=block_type),
                        name=name,
                        kind="config",
                        language="prisma",
                        path=rel_path,
                        span=Span(
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        meta={"block_type": block_type},
                    )
                    analysis.symbols.append(sym)
                    analysis.node_for_symbol[sym.id] = node

        # Recursively process children
        for child in node.children:
            self._extract_symbols_recursive(
                child, path, repo_root, rel_path, run, analysis
            )

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract relationship edges from a Prisma file."""
        edges: list[Edge] = []
        rel_parts = Path(rel_path).parts
        repo_root = file_path
        for _ in rel_parts:
            repo_root = repo_root.parent

        self._extract_edges_recursive(
            tree.root_node, file_path, repo_root, global_symbols, run, edges,
        )
        return edges

    def _extract_edges_recursive(
        self, node: "tree_sitter.Node", path: Path, repo_root: Path,
        global_symbols: dict, run: "AnalysisRun", edges: list[Edge],
    ) -> None:
        """Extract relationship edges from a syntax tree node recursively."""
        if node.type == "model_block":
            model_name = _get_identifier(node)
            if model_name:
                model_sym = global_symbols.get(model_name)
                model_id = model_sym.id if model_sym else None
                if model_id:
                    for child in node.children:
                        if child.type == "model_field":
                            self._process_field_relation(
                                child, model_id, path, repo_root,
                                global_symbols, run, edges,
                            )

        # Recursively process children
        for child in node.children:
            self._extract_edges_recursive(
                child, path, repo_root, global_symbols, run, edges,
            )

    def _process_field_relation(
        self, field_node: "tree_sitter.Node", source_model_id: str,
        path: Path, repo_root: Path,
        global_symbols: dict, run: "AnalysisRun", edges: list[Edge],
    ) -> None:
        """Process a model field for potential relations."""
        has_relation = False
        for child in field_node.children:
            if child.type == "model_single_attribute":
                attr_text = _get_node_text(child)
                if "@relation" in attr_text:
                    has_relation = True
                    break

        if has_relation:
            target_model = _get_relation_target(field_node)
            if target_model:
                target_sym = global_symbols.get(target_model)
                target_id = target_sym.id if target_sym else None
                confidence = 1.0 if target_id else 0.6
                if target_id is None:
                    target_id = f"prisma:unresolved:{target_model}"

                line = field_node.start_point[0] + 1
                edge = Edge.create(
                    src=source_model_id,
                    dst=target_id,
                    edge_type="references",
                    line=line,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="schema_relation",
                    confidence=confidence,
                    evidence_lang="prisma",
                )
                edges.append(edge)


_analyzer = PrismaAnalyzer()


def is_prisma_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Prisma support is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("prisma")
def analyze_prisma(repo_root: Path) -> AnalysisResult:
    """Analyze Prisma schema files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        AnalysisResult containing symbols, edges, and analysis metadata
    """
    return _analyzer.analyze(repo_root)
