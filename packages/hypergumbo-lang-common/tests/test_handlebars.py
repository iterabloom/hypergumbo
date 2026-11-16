# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Handlebars template analyzer."""
from pathlib import Path

from hypergumbo_core.overload_arity import scoped_stable_id
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


class TestTildePartial:
    """A partial call with whitespace control is still a partial (WI-zisar)."""

    def test_tilde_partials_emit_symbols(self, tmp_path: Path) -> None:
        (tmp_path / "t.hbs").write_text(
            "{{~> lead}}\n{{> plain}}\n{{~> both~}}\n{{#if a}}{{~> inner}}{{/if}}\n")
        result = analyze_handlebars(tmp_path)
        partials = {(s.span.start_line, s.name): s for s in result.symbols
                    if s.kind == "partial"}
        assert set(partials) == {(1, "lead"), (2, "plain"), (3, "both"), (4, "inner")}
        assert partials[(4, "inner")].stable_id == scoped_stable_id(
            kind="partial", lang="handlebars", path="t.hbs", name="inner", scope=("if a",))
        assert partials[(1, "lead")].span.start_col == 0


def _sid(kind: str, name: str, scope: tuple[str, ...], path: str = "t.hbs") -> str:
    """The stable id a symbol nested in ``scope`` must carry."""
    return scoped_stable_id(kind=kind, lang="handlebars", path=path, name=name, scope=scope)


def _at(result, line: int, kind: str, name: str):
    """The one symbol of ``kind`` and ``name`` on ``line``."""
    found = [s for s in result.symbols
             if s.span.start_line == line and s.kind == kind and s.name == name]
    assert len(found) == 1, (line, kind, name, [(s.span.start_line, s.kind, s.name)
                                                for s in result.symbols])
    return found[0]


class TestBlockForms:
    """Every block opener form pushes a scope its closer pops (WI-dolad)."""

    def test_tilde_opener_is_a_block(self, tmp_path: Path) -> None:
        (tmp_path / "t.hbs").write_text(
            "{{#each outer}}\n  {{~#each inner}}\n    {{> row}}\n  {{~/each}}\n"
            "  {{> after}}\n{{/each}}\n")
        result = analyze_handlebars(tmp_path)
        inner = ("each outer", "each inner")
        assert _at(result, 2, "block", "each").stable_id == _sid("block", "each", inner)
        assert _at(result, 3, "partial", "row").stable_id == _sid("partial", "row", inner)
        # {{~/each}} closes the inner block, not the outer one
        assert _at(result, 5, "partial", "after").stable_id == _sid(
            "partial", "after", ("each outer",))

    def test_trailing_tilde_stays_out_of_the_label(self, tmp_path: Path) -> None:
        (tmp_path / "t.hbs").write_text(
            "{{~#each items~}}\n  {{> row}}\n{{~/each~}}\n{{> after}}\n")
        result = analyze_handlebars(tmp_path)
        assert _at(result, 2, "partial", "row").stable_id == _sid(
            "partial", "row", ("each items",))
        assert _at(result, 4, "partial", "after").stable_id == _sid("partial", "after", ())

    def test_split_line_opener(self, tmp_path: Path) -> None:
        (tmp_path / "t.hbs").write_text(
            "{{#each outer}}\n  {{#each\n     inner}}\n    {{> row}}\n  {{/each}}\n"
            "  {{> after}}\n{{/each}}\n")
        result = analyze_handlebars(tmp_path)
        inner = ("each outer", "each inner")
        assert _at(result, 2, "block", "each").stable_id == _sid("block", "each", inner)
        assert _at(result, 4, "partial", "row").stable_id == _sid("partial", "row", inner)
        assert _at(result, 6, "partial", "after").stable_id == _sid(
            "partial", "after", ("each outer",))

    def test_partial_block_and_inline_partial(self, tmp_path: Path) -> None:
        (tmp_path / "t.hbs").write_text(
            '{{#if user}}\n  {{#> layout title="t"}}\n    {{> row}}\n  {{/layout}}\n'
            '  {{#*inline "sidebar"}}\n    {{> side}}\n  {{/inline}}\n'
            "  {{> after}}\n{{/if}}\n")
        result = analyze_handlebars(tmp_path)
        layout = ("if user", '> layout title="t"')
        inline = ("if user", '*inline "sidebar"')
        assert _at(result, 2, "partial", "layout").stable_id == _sid("partial", "layout", layout)
        assert _at(result, 3, "partial", "row").stable_id == _sid("partial", "row", layout)
        assert _at(result, 5, "block", "inline").stable_id == _sid("block", "inline", inline)
        assert _at(result, 6, "partial", "side").stable_id == _sid("partial", "side", inline)
        assert _at(result, 8, "partial", "after").stable_id == _sid(
            "partial", "after", ("if user",))

    def test_partial_block_closer_does_not_pop_a_same_named_helper(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "t.hbs").write_text(
            "{{#layout}}\n  {{#> layout}}\n    {{> row}}\n  {{/layout}}\n"
            "  {{> after}}\n{{/layout}}\n")
        result = analyze_handlebars(tmp_path)
        assert _at(result, 3, "partial", "row").stable_id == _sid(
            "partial", "row", ("layout", "> layout"))
        assert _at(result, 5, "partial", "after").stable_id == _sid(
            "partial", "after", ("layout",))

    def test_inverse_section_and_spaced_opener(self, tmp_path: Path) -> None:
        (tmp_path / "t.hbs").write_text(
            "{{#items}}\n  {{> full}}\n{{/items}}\n{{^items}}\n  {{> empty}}\n{{/items}}\n"
            "{{# if spaced}}\n  {{> sp}}\n{{/if}}\n{{^}}\n")
        result = analyze_handlebars(tmp_path)
        assert _at(result, 2, "partial", "full").stable_id == _sid("partial", "full", ("items",))
        assert _at(result, 4, "helper", "items").stable_id == _sid("helper", "items", ("^items",))
        assert _at(result, 5, "partial", "empty").stable_id == _sid(
            "partial", "empty", ("^items",))
        assert _at(result, 7, "block", "if").stable_id == _sid("block", "if", ("if spaced",))
        assert _at(result, 8, "partial", "sp").stable_id == _sid("partial", "sp", ("if spaced",))

    def test_else_arms_share_the_block_scope(self, tmp_path: Path) -> None:
        (tmp_path / "t.hbs").write_text(
            "{{#if a}}{{> x}}{{else if b}}{{> y}}{{else}}{{> z}}{{/if}}{{> after}}\n")
        result = analyze_handlebars(tmp_path)
        for name in ("x", "y", "z"):
            assert _at(result, 1, "partial", name).stable_id == _sid("partial", name, ("if a",))
        assert _at(result, 1, "partial", "after").stable_id == _sid("partial", "after", ())


class TestNonConstructs:
    """Comments, raw blocks and escaped mustaches hold no constructs."""

    def test_comment_raw_and_escape(self, tmp_path: Path) -> None:
        (tmp_path / "t.hbs").write_text(
            "{{!-- {{#if hidden}} {{> ghost}} --}}\n{{! {{> ghost2}} }}\n"
            "\\{{> escaped}}\n{{{{raw}}}}\n  {{> rawp}} {{#if r}}\n{{{{/raw}}}}\n"
            "\\\\{{> real}}\n{{> after}}\n")
        result = analyze_handlebars(tmp_path)
        names = {s.name for s in result.symbols}
        assert not names & {"ghost", "ghost2", "escaped", "rawp", "if"}
        assert _at(result, 4, "helper", "raw").stable_id == _sid("helper", "raw", ("raw",))
        assert _at(result, 7, "partial", "real").stable_id == _sid("partial", "real", ())
        assert _at(result, 8, "partial", "after").stable_id == _sid("partial", "after", ())

    def test_unterminated_comment_and_raw_block(self, tmp_path: Path) -> None:
        # an unclosed {{!-- ends at the next }}; an unclosed raw block holds nothing back
        (tmp_path / "t.hbs").write_text(
            "{{!-- note }}\n{{> one}}\n{{{{raw}}}}\n{{> two}}\n")
        result = analyze_handlebars(tmp_path)
        assert _at(result, 2, "partial", "one").stable_id == _sid("partial", "one", ())
        assert _at(result, 4, "partial", "two").stable_id == _sid("partial", "two", ())


class TestMalformed:
    """Malformed templates still yield their symbols."""

    def test_mismatched_closer_is_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "t.hbs").write_text(
            "{{#each outer}}\n  {{/if}}\n  {{> b}}\n{{/each}}\n")
        result = analyze_handlebars(tmp_path)
        assert _at(result, 3, "partial", "b").stable_id == _sid("partial", "b", ("each outer",))

    def test_unclosed_block_scopes_to_end_of_file(self, tmp_path: Path) -> None:
        (tmp_path / "t.hbs").write_text("{{#with ctx}}\n  {{> c}}\n")
        result = analyze_handlebars(tmp_path)
        assert _at(result, 2, "partial", "c").stable_id == _sid("partial", "c", ("with ctx",))

    def test_unterminated_tag_ends_at_the_next_tag(self, tmp_path: Path) -> None:
        (tmp_path / "t.hbs").write_text("{{#each items\n{{> c}}\n{{#if")
        result = analyze_handlebars(tmp_path)
        # the opener without }} still yields its symbol but opens no block
        assert _at(result, 1, "block", "each").stable_id == _sid(
            "block", "each", ("each items",))
        assert _at(result, 2, "partial", "c").stable_id == _sid("partial", "c", ())
        assert _at(result, 3, "block", "if").stable_id == _sid("block", "if", ("if",))
