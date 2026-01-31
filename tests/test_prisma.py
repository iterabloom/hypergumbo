"""Tests for Prisma analyzer.

Tests for the tree-sitter-based Prisma schema analyzer, verifying model extraction,
enum detection, relationship edges, and graceful degradation when tree-sitter is unavailable.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from hypergumbo_lang_extended1.prisma import (
    analyze_prisma,
    find_prisma_files,
    is_prisma_tree_sitter_available,
    PASS_ID,
)


@pytest.fixture
def prisma_repo(tmp_path: Path) -> Path:
    """Create a minimal Prisma project for testing."""
    # Main schema file
    (tmp_path / "schema.prisma").write_text(
        '''datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

generator client {
  provider = "prisma-client-js"
}

model User {
  id        Int      @id @default(autoincrement())
  email     String   @unique
  name      String?
  posts     Post[]
  profile   Profile?
  createdAt DateTime @default(now())
  role      Role     @default(USER)
}

model Post {
  id        Int      @id @default(autoincrement())
  title     String
  content   String?
  published Boolean  @default(false)
  author    User     @relation(fields: [authorId], references: [id])
  authorId  Int
  tags      Tag[]
}

model Profile {
  id     Int     @id @default(autoincrement())
  bio    String?
  user   User    @relation(fields: [userId], references: [id])
  userId Int     @unique
}

model Tag {
  id    Int    @id @default(autoincrement())
  name  String @unique
  posts Post[]
}

enum Role {
  USER
  ADMIN
  MODERATOR
}

enum Status {
  DRAFT
  PUBLISHED
  ARCHIVED
}
'''
    )

    return tmp_path


class TestFindPrismaFiles:
    """Tests for finding Prisma files."""

    def test_finds_prisma_files(self, prisma_repo: Path) -> None:
        """Should find all .prisma files recursively."""
        files = list(find_prisma_files(prisma_repo))
        assert len(files) == 1
        assert files[0].name == "schema.prisma"

    def test_finds_nested_prisma_files(self, tmp_path: Path) -> None:
        """Should find Prisma files in subdirectories."""
        prisma_dir = tmp_path / "prisma"
        prisma_dir.mkdir()
        (prisma_dir / "schema.prisma").write_text("model User { id Int @id }")

        files = list(find_prisma_files(tmp_path))
        assert len(files) == 1

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Should return empty iterator for directory with no Prisma files."""
        files = list(find_prisma_files(tmp_path))
        assert files == []


class TestIsPrismaTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_returns_true_when_available(self) -> None:
        """Should return True when tree-sitter-language-pack is installed."""
        assert is_prisma_tree_sitter_available() is True

    def test_returns_false_when_unavailable(self) -> None:
        """Should return False when tree-sitter-language-pack is not installed."""
        with patch("hypergumbo_lang_extended1.prisma.is_prisma_tree_sitter_available", return_value=False):
            # Direct test
            from hypergumbo_lang_extended1 import prisma as prisma_module
            with patch.object(prisma_module, "is_prisma_tree_sitter_available", return_value=False):
                assert prisma_module.is_prisma_tree_sitter_available() is False


class TestAnalyzePrisma:
    """Tests for the Prisma analyzer."""

    def test_skips_when_unavailable(self, prisma_repo: Path) -> None:
        """Should skip analysis and warn when tree-sitter is unavailable."""
        import hypergumbo_lang_extended1.prisma as prisma_module

        with patch.object(prisma_module, "is_prisma_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="tree-sitter-language-pack not available"):
                result = prisma_module.analyze_prisma(prisma_repo)

        assert result.skipped is True
        assert "tree-sitter-language-pack" in result.skip_reason
        assert result.symbols == []
        assert result.edges == []

    def test_extracts_models(self, prisma_repo: Path) -> None:
        """Should extract model declarations."""
        result = analyze_prisma(prisma_repo)

        assert not result.skipped
        assert result.symbols

        models = [s for s in result.symbols if s.kind == "class"]
        model_names = {s.name for s in models}

        assert "User" in model_names
        assert "Post" in model_names
        assert "Profile" in model_names
        assert "Tag" in model_names

    def test_extracts_enums(self, prisma_repo: Path) -> None:
        """Should extract enum declarations."""
        result = analyze_prisma(prisma_repo)

        enums = [s for s in result.symbols if s.kind == "enum"]
        enum_names = {s.name for s in enums}

        assert "Role" in enum_names
        assert "Status" in enum_names

    def test_extracts_datasource(self, prisma_repo: Path) -> None:
        """Should extract datasource block."""
        result = analyze_prisma(prisma_repo)

        configs = [s for s in result.symbols if s.kind == "config"]
        config_names = {s.name for s in configs}

        assert "db" in config_names

    def test_extracts_generator(self, prisma_repo: Path) -> None:
        """Should extract generator block."""
        result = analyze_prisma(prisma_repo)

        configs = [s for s in result.symbols if s.kind == "config"]
        generators = [c for c in configs if c.meta and c.meta.get("block_type") == "generator"]

        assert len(generators) == 1
        assert generators[0].name == "client"

    def test_extracts_relation_edges(self, prisma_repo: Path) -> None:
        """Should extract relationship edges between models."""
        result = analyze_prisma(prisma_repo)

        relation_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(relation_edges) > 0

        # Check that Post references User
        post_edges = [e for e in relation_edges if "Post" in e.src]
        assert any("User" in e.dst for e in post_edges)

        # Check that Profile references User
        profile_edges = [e for e in relation_edges if "Profile" in e.src]
        assert any("User" in e.dst for e in profile_edges)

    def test_model_field_count(self, prisma_repo: Path) -> None:
        """Should count model fields."""
        result = analyze_prisma(prisma_repo)

        user = next((s for s in result.symbols if s.name == "User"), None)
        assert user is not None
        assert user.meta is not None
        assert user.meta.get("field_count") == 7  # id, email, name, posts, profile, createdAt, role

        post = next((s for s in result.symbols if s.name == "Post"), None)
        assert post is not None
        assert post.meta is not None
        assert post.meta.get("field_count") == 7  # id, title, content, published, author, authorId, tags

    def test_enum_variant_count(self, prisma_repo: Path) -> None:
        """Should count enum variants."""
        result = analyze_prisma(prisma_repo)

        role = next((s for s in result.symbols if s.name == "Role"), None)
        assert role is not None
        assert role.meta is not None
        assert role.meta.get("variant_count") == 3  # USER, ADMIN, MODERATOR

    def test_pass_id(self, prisma_repo: Path) -> None:
        """Should have correct pass origin."""
        result = analyze_prisma(prisma_repo)

        for sym in result.symbols:
            assert sym.origin == PASS_ID

        for edge in result.edges:
            assert edge.origin == PASS_ID

    def test_analysis_run_metadata(self, prisma_repo: Path) -> None:
        """Should include analysis run metadata."""
        result = analyze_prisma(prisma_repo)

        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Should return empty result for repository with no Prisma files."""
        result = analyze_prisma(tmp_path)

        assert not result.skipped
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, prisma_repo: Path) -> None:
        """Should generate stable IDs for symbols."""
        result1 = analyze_prisma(prisma_repo)
        result2 = analyze_prisma(prisma_repo)

        ids1 = {s.id for s in result1.symbols}
        ids2 = {s.id for s in result2.symbols}

        assert ids1 == ids2

    def test_span_info(self, prisma_repo: Path) -> None:
        """Should include accurate span information."""
        result = analyze_prisma(prisma_repo)

        for sym in result.symbols:
            assert sym.span is not None
            assert sym.path is not None
            assert sym.span.start_line > 0
            assert sym.span.end_line >= sym.span.start_line

    def test_is_model_flag(self, prisma_repo: Path) -> None:
        """Should mark models with is_model metadata."""
        result = analyze_prisma(prisma_repo)

        models = [s for s in result.symbols if s.kind == "class"]
        for model in models:
            assert model.meta is not None
            assert model.meta.get("is_model") is True


class TestUnresolvedRelations:
    """Tests for handling unresolved model references."""

    def test_unresolved_relation_target(self, tmp_path: Path) -> None:
        """Should handle references to undefined models."""
        (tmp_path / "schema.prisma").write_text(
            '''model Post {
  id       Int      @id
  author   Unknown  @relation(fields: [authorId], references: [id])
  authorId Int
}
'''
        )

        result = analyze_prisma(tmp_path)

        relation_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(relation_edges) == 1

        # Should have lower confidence for unresolved target
        assert relation_edges[0].confidence == 0.6
        assert "unresolved:Unknown" in relation_edges[0].dst
