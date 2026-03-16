# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Handlebars template analyzer."""
from pathlib import Path

from hypergumbo_lang_common.handlebars import analyze_handlebars


class TestAnalyzeHandlebars:
    """Tests for Handlebars template analysis."""

    def test_detects_partial(self, tmp_path: Path) -> None:
        (tmp_path / "page.hbs").write_text("{{> header}}\n<h1>Hello</h1>")
        result = analyze_handlebars(tmp_path)
        names = [s.name for s in result.symbols]
        assert "header" in names
        assert any(s.kind == "partial" for s in result.symbols)

    def test_detects_quoted_partial(self, tmp_path: Path) -> None:
        (tmp_path / "page.hbs").write_text('{{> "shared/footer"}}')
        result = analyze_handlebars(tmp_path)
        assert any(s.name == "shared/footer" for s in result.symbols)

    def test_detects_builtin_block(self, tmp_path: Path) -> None:
        (tmp_path / "list.hbs").write_text("{{#each items}}\n<li>{{this}}</li>\n{{/each}}")
        result = analyze_handlebars(tmp_path)
        assert any(s.name == "each" and s.kind == "block" for s in result.symbols)

    def test_detects_custom_helper_block(self, tmp_path: Path) -> None:
        (tmp_path / "card.hbs").write_text("{{#markdown}}\n# Hello\n{{/markdown}}")
        result = analyze_handlebars(tmp_path)
        assert any(s.name == "markdown" and s.kind == "helper" for s in result.symbols)

    def test_if_is_builtin_block(self, tmp_path: Path) -> None:
        (tmp_path / "cond.hbs").write_text("{{#if show}}\nvisible\n{{/if}}")
        result = analyze_handlebars(tmp_path)
        assert any(s.name == "if" and s.kind == "block" for s in result.symbols)

    def test_handlebars_extension(self, tmp_path: Path) -> None:
        (tmp_path / "page.handlebars").write_text("{{> nav}}")
        result = analyze_handlebars(tmp_path)
        assert len(result.symbols) == 1

    def test_symbols_have_handlebars_language(self, tmp_path: Path) -> None:
        (tmp_path / "x.hbs").write_text("{{> header}}")
        result = analyze_handlebars(tmp_path)
        assert all(s.language == "handlebars" for s in result.symbols)

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        result = analyze_handlebars(tmp_path)
        assert result.symbols == []

    def test_multiple_partials(self, tmp_path: Path) -> None:
        (tmp_path / "page.hbs").write_text("{{> header}}\n{{> content}}\n{{> footer}}")
        result = analyze_handlebars(tmp_path)
        names = [s.name for s in result.symbols]
        assert "header" in names
        assert "content" in names
        assert "footer" in names
