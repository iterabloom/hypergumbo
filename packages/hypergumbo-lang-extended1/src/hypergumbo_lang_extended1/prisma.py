"""Prisma schema analyzer using tree-sitter.

This module provides static analysis for Prisma schema files (.prisma), extracting
models, enums, fields, and relationships for database schema visualization.

Prisma is an ORM for Node.js and TypeScript that uses a declarative schema file
to define database models and their relationships.

Implementation approach:
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

import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "prisma.tree_sitter"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class PrismaAnalysisResult:
    """Result of analyzing Prisma files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: Optional[AnalysisRun] = None
    skipped: bool = False
    skip_reason: str = ""


def is_prisma_tree_sitter_available() -> bool:
    """Check if tree-sitter-language-pack with Prisma support is available."""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("prisma")
        return True
    except (ImportError, Exception):  # pragma: no cover
        return False  # pragma: no cover


def find_prisma_files(root: Path) -> Iterator[Path]:
    """Find all Prisma schema files in the given directory."""
    for path in root.rglob("*.prisma"):
        if path.is_file():
            yield path


def _make_stable_id(path: Path, repo_root: Path, name: str, kind: str) -> str:
    """Create a stable ID for a Prisma symbol."""
    rel_path = path.relative_to(repo_root)
    return f"prisma:{rel_path}:{name}:{kind}"


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
    # The relation target comes from the field type, not the attribute
    for child in node.children:
        if child.type == "field_type":
            type_text = _get_node_text(child).strip()
            # Remove array [] and nullable ?
            type_text = type_text.rstrip("[]?")
            # Check if it's a model reference (starts with uppercase)
            if type_text and type_text[0].isupper() and type_text not in {
                "String", "Int", "Float", "Boolean", "DateTime", "Json",
                "Bytes", "BigInt", "Decimal"
            }:
                return type_text
    return None  # pragma: no cover


class PrismaAnalyzer:
    """Analyzer for Prisma schema files."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self._model_registry: dict[str, str] = {}  # model_name -> symbol_id
        self._run_id: str = ""

    def analyze(self) -> PrismaAnalysisResult:
        """Analyze all Prisma files in the repository."""
        if not is_prisma_tree_sitter_available():
            warnings.warn(
                "Prisma analysis skipped: tree-sitter-language-pack not available",
                UserWarning,
                stacklevel=2,
            )
            return PrismaAnalysisResult(
                skipped=True,
                skip_reason="tree-sitter-language-pack not available",
            )

        import uuid as uuid_module
        from tree_sitter_language_pack import get_parser

        start_time = time.time()
        self._run_id = f"uuid:{uuid_module.uuid4()}"

        parser = get_parser("prisma")
        prisma_files = list(find_prisma_files(self.repo_root))

        if not prisma_files:
            return PrismaAnalysisResult()

        # Pass 1: Collect all models and enums
        for path in prisma_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_symbols(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        # Build model registry
        for sym in self.symbols:
            if sym.kind in ("class", "enum"):
                self._model_registry[sym.name] = sym.id

        # Pass 2: Extract relationships
        for path in prisma_files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._extract_edges(tree.root_node, path)
            except Exception:  # pragma: no cover
                pass

        elapsed = time.time() - start_time

        run = AnalysisRun(
            execution_id=self._run_id,
            run_signature="",
            pass_id=PASS_ID,
            version=PASS_VERSION,
            toolchain={"name": "prisma", "version": "unknown"},
            duration_ms=int(elapsed * 1000),
        )

        return PrismaAnalysisResult(
            symbols=self.symbols,
            edges=self.edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "model_block":
            name = _get_identifier(node)
            if name:
                # Count fields
                field_count = sum(1 for c in node.children if c.type == "model_field")
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "model"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "model"),
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
                    meta={"field_count": field_count, "is_model": True},
                )
                self.symbols.append(sym)

        elif node.type == "enum_block":
            name = _get_identifier(node)
            if name:
                # Count variants (they are identifier nodes after the enum name)
                # Skip the first identifier (enum name) and braces
                identifiers = [c for c in node.children if c.type == "identifier"]
                variant_count = len(identifiers) - 1  # Subtract 1 for the enum name
                rel_path = str(path.relative_to(self.repo_root))
                sym = Symbol(
                    id=_make_stable_id(path, self.repo_root, name, "enum"),
                    stable_id=_make_stable_id(path, self.repo_root, name, "enum"),
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
                    meta={"variant_count": variant_count},
                )
                self.symbols.append(sym)

        elif node.type == "key_value_block":
            # Datasource or generator block
            block_type = None
            for child in node.children:
                if child.type in ("datasource", "generator"):
                    block_type = child.type
                    break

            if block_type:
                name = _get_identifier(node)
                if name:
                    rel_path = str(path.relative_to(self.repo_root))
                    sym = Symbol(
                        id=_make_stable_id(path, self.repo_root, name, block_type),
                        stable_id=_make_stable_id(path, self.repo_root, name, block_type),
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
                        meta={"block_type": block_type},
                    )
                    self.symbols.append(sym)

        # Recursively process children
        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_edges(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract relationship edges from a syntax tree node."""
        if node.type == "model_block":
            model_name = _get_identifier(node)
            if model_name:
                model_id = self._model_registry.get(model_name)
                if model_id:
                    # Look for relation fields
                    for child in node.children:
                        if child.type == "model_field":
                            self._process_field_relation(child, model_id, path)

        # Recursively process children
        for child in node.children:
            self._extract_edges(child, path)

    def _process_field_relation(
        self, field_node: "tree_sitter.Node", source_model_id: str, path: Path
    ) -> None:
        """Process a model field for potential relations."""
        # Check if this field has a @relation attribute
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
                target_id = self._model_registry.get(target_model)
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
                    origin_run_id=self._run_id,
                    evidence_type="schema_relation",
                    confidence=confidence,
                    evidence_lang="prisma",
                )
                self.edges.append(edge)


def analyze_prisma(repo_root: Path) -> PrismaAnalysisResult:
    """Analyze Prisma schema files in a repository.

    Args:
        repo_root: Root directory of the repository to analyze

    Returns:
        PrismaAnalysisResult containing symbols, edges, and analysis metadata
    """
    analyzer = PrismaAnalyzer(repo_root)
    return analyzer.analyze()
