# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Just (justfile) analyzer."""
from pathlib import Path

from hypergumbo_lang_common.just import analyze_just


class TestAnalyzeJust:
    """Tests for justfile analysis."""

    def test_detects_recipe(self, tmp_path: Path) -> None:
        (tmp_path / "justfile").write_text("build:\n    cargo build\n")
        result = analyze_just(tmp_path)
        names = [s.name for s in result.symbols]
        assert "build" in names
        assert any(s.kind == "recipe" for s in result.symbols)

    def test_detects_variable(self, tmp_path: Path) -> None:
        (tmp_path / "justfile").write_text("version := '1.0'\n")
        result = analyze_just(tmp_path)
        names = [s.name for s in result.symbols]
        assert "version" in names
        assert any(s.kind == "variable" for s in result.symbols)

    def test_detects_alias(self, tmp_path: Path) -> None:
        (tmp_path / "justfile").write_text("build:\n    echo build\nalias b := build\n")
        result = analyze_just(tmp_path)
        names = [s.name for s in result.symbols]
        assert "b" in names
        assert any(s.kind == "alias" for s in result.symbols)

    def test_detects_dependency_edge(self, tmp_path: Path) -> None:
        content = "build:\n    cargo build\ntest: build\n    cargo test\n"
        (tmp_path / "justfile").write_text(content)
        result = analyze_just(tmp_path)
        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "depends_on"

    def test_skips_recipe_body(self, tmp_path: Path) -> None:
        content = "build:\n    echo hello\n    @echo silent\n"
        (tmp_path / "justfile").write_text(content)
        result = analyze_just(tmp_path)
        # Only the recipe, not body lines
        assert len(result.symbols) == 1

    def test_skips_keywords(self, tmp_path: Path) -> None:
        (tmp_path / "justfile").write_text("set shell := ['bash']\nexport FOO := 'bar'\n")
        result = analyze_just(tmp_path)
        # set and export are keywords, not recipes/variables
        assert len(result.symbols) == 0

    def test_symbols_have_just_language(self, tmp_path: Path) -> None:
        (tmp_path / "justfile").write_text("test:\n    echo test\n")
        result = analyze_just(tmp_path)
        assert all(s.language == "just" for s in result.symbols)

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        result = analyze_just(tmp_path)
        assert result.symbols == []

    def test_just_extension(self, tmp_path: Path) -> None:
        (tmp_path / "tasks.just").write_text("deploy:\n    ./deploy.sh\n")
        result = analyze_just(tmp_path)
        assert len(result.symbols) == 1

    def test_multiple_recipes(self, tmp_path: Path) -> None:
        content = "clean:\n    rm -rf build\nbuild:\n    make\ntest: build\n    pytest\n"
        (tmp_path / "justfile").write_text(content)
        result = analyze_just(tmp_path)
        names = [s.name for s in result.symbols if s.kind == "recipe"]
        assert "clean" in names
        assert "build" in names
        assert "test" in names
