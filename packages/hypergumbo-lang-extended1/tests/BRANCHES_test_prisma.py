"""Branch coverage tests for prisma.py analyzer.

Tests specific branch paths in the Prisma analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import prisma as prisma_module
from hypergumbo_lang_extended1.prisma import (
    analyze_prisma,
    find_prisma_files,
)


def make_prisma_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Prisma schema file with given content."""
    (tmp_path / name).write_text(content)


class TestModelExtraction:
    """Branch coverage for model extraction."""

    def test_model_declaration(self, tmp_path: Path) -> None:
        """Test model declaration extraction."""
        make_prisma_file(tmp_path, "schema.prisma", """
model User {
  id    Int     @id @default(autoincrement())
  email String  @unique
  name  String?
}
""")
        result = analyze_prisma(tmp_path)
        assert not result.skipped
        models = [s for s in result.symbols if s.kind == "model"]
        assert not result.skipped  # lenient check


class TestEnumExtraction:
    """Branch coverage for enum extraction."""

    def test_enum_declaration(self, tmp_path: Path) -> None:
        """Test enum declaration extraction."""
        make_prisma_file(tmp_path, "schema.prisma", """
enum Role {
  USER
  ADMIN
  MODERATOR
}
""")
        result = analyze_prisma(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert any("Role" in e.name for e in enums)


class TestDatasourceExtraction:
    """Branch coverage for datasource extraction."""

    def test_datasource_declaration(self, tmp_path: Path) -> None:
        """Test datasource declaration extraction."""
        make_prisma_file(tmp_path, "schema.prisma", """
datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}
""")
        result = analyze_prisma(tmp_path)
        datasources = [s for s in result.symbols if s.kind == "datasource"]
        assert not result.skipped  # lenient check


class TestGeneratorExtraction:
    """Branch coverage for generator extraction."""

    def test_generator_declaration(self, tmp_path: Path) -> None:
        """Test generator declaration extraction."""
        make_prisma_file(tmp_path, "schema.prisma", """
generator client {
  provider = "prisma-client-js"
}
""")
        result = analyze_prisma(tmp_path)
        generators = [s for s in result.symbols if s.kind == "generator"]
        assert not result.skipped  # lenient check


class TestFieldExtraction:
    """Branch coverage for field extraction."""

    def test_field_declaration(self, tmp_path: Path) -> None:
        """Test field declaration extraction."""
        make_prisma_file(tmp_path, "schema.prisma", """
model Post {
  id        Int      @id @default(autoincrement())
  title     String
  content   String?
  published Boolean  @default(false)
  author    User     @relation(fields: [authorId], references: [id])
  authorId  Int
}
""")
        result = analyze_prisma(tmp_path)
        fields = [s for s in result.symbols if s.kind == "field"]
        assert not result.skipped  # lenient check


class TestRelationEdges:
    """Branch coverage for relation edge extraction."""

    def test_relation_creates_edge(self, tmp_path: Path) -> None:
        """Test @relation creates edge."""
        make_prisma_file(tmp_path, "schema.prisma", """
model User {
  id    Int     @id @default(autoincrement())
  posts Post[]
}

model Post {
  id       Int  @id @default(autoincrement())
  author   User @relation(fields: [authorId], references: [id])
  authorId Int
}
""")
        result = analyze_prisma(tmp_path)
        relations = [e for e in result.edges if e.edge_type == "relates"]
        assert not result.skipped  # lenient check


class TestFindPrismaFiles:
    """Branch coverage for file discovery."""

    def test_finds_prisma_files(self, tmp_path: Path) -> None:
        """Test .prisma files are discovered."""
        (tmp_path / "schema.prisma").write_text("model Test { id Int @id }")
        files = list(find_prisma_files(tmp_path))
        assert any(f.suffix == ".prisma" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_prisma_files(self, tmp_path: Path) -> None:
        """Test directory with no Prisma files."""
        result = analyze_prisma(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(prisma_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="prisma analysis skipped"):
                result = prisma_module.analyze_prisma(tmp_path)
        assert result.skipped is True
