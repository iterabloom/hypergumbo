# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Blade template analyzer."""
from pathlib import Path

from hypergumbo_core.overload_arity import scoped_stable_id
from hypergumbo_lang_extended1.blade import analyze_blade


class TestAnalyzeBlade:
    """Tests for Blade template analysis."""

    def test_detects_section(self, tmp_path: Path) -> None:
        (tmp_path / "page.blade.php").write_text("@section('content')\n<h1>Hello</h1>\n@endsection")
        result = analyze_blade(tmp_path)
        names = [s.name for s in result.symbols]
        assert "content" in names
        assert any(s.kind == "section" for s in result.symbols)

    def test_detects_yield(self, tmp_path: Path) -> None:
        (tmp_path / "layout.blade.php").write_text("<body>\n@yield('content')\n</body>")
        result = analyze_blade(tmp_path)
        names = [s.name for s in result.symbols]
        assert "content" in names
        assert any(s.kind == "yield" for s in result.symbols)

    def test_detects_extends(self, tmp_path: Path) -> None:
        # ADR-0027 Cluster E sub-case (b) shape 3 (audit-findings 0010,
        # WI-kunag): @extends now emits a companion extends_template Edge,
        # not a Symbol. The relationship is still represented in the graph.
        (tmp_path / "page.blade.php").write_text("@extends('layouts.app')")
        result = analyze_blade(tmp_path)
        assert any(
            e.edge_type == "extends" and (e.meta or {}).get("ref_construct") == "template"
            and (e.meta or {}).get("template") == "layouts.app"
            for e in result.edges
        )
        # No `extends`-kind Symbol after the fold.
        assert not any(s.kind == "extends" for s in result.symbols)

    def test_detects_component(self, tmp_path: Path) -> None:
        (tmp_path / "page.blade.php").write_text("@component('alert')\nDanger!\n@endcomponent")
        result = analyze_blade(tmp_path)
        names = [s.name for s in result.symbols]
        assert "alert" in names
        assert any(s.kind == "component" for s in result.symbols)

    def test_symbols_have_blade_language(self, tmp_path: Path) -> None:
        (tmp_path / "x.blade.php").write_text("@section('nav')\n@endsection")
        result = analyze_blade(tmp_path)
        assert all(s.language == "blade" for s in result.symbols)

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        result = analyze_blade(tmp_path)
        assert result.symbols == []

    def test_multiple_directives(self, tmp_path: Path) -> None:
        content = "@extends('layouts.app')\n@section('title')\nHello\n@endsection\n@yield('footer')"
        (tmp_path / "page.blade.php").write_text(content)
        result = analyze_blade(tmp_path)
        # 2 Symbols (section, yield) + 1 Edge (extends_template — see WI-kunag, origin="test", origin_run_id="test")
        assert len(result.symbols) == 2
        assert any(e.edge_type == "extends" and (e.meta or {}).get("ref_construct") == "template" for e in result.edges)

    def test_double_quoted_names(self, tmp_path: Path) -> None:
        (tmp_path / "page.blade.php").write_text('@section("content")\n@endsection')
        result = analyze_blade(tmp_path)
        assert any(s.name == "content" for s in result.symbols)


def _sid(kind: str, name: str, scope: tuple[str, ...], path: str = "t.blade.php") -> str:
    """The stable id a symbol nested in ``scope`` must carry."""
    return scoped_stable_id(kind=kind, lang="blade", path=path, name=name, scope=list(scope))


def _at(result, line: int, kind: str, name: str):
    """The one symbol of ``kind`` and ``name`` on ``line``."""
    found = [s for s in result.symbols
             if s.span.start_line == line and s.kind == kind and s.name == name]
    assert len(found) == 1, (line, kind, name, [(s.span.start_line, s.kind, s.name)
                                                for s in result.symbols])
    return found[0]


def _extends(result) -> list[tuple[int, str]]:
    return [(e.line, (e.meta or {}).get("template")) for e in result.edges
            if e.edge_type == "extends"]


class TestDirectiveArguments:
    """A directive with more than its name still counts (WI-pihor)."""

    def test_second_argument_keeps_the_directive(self, tmp_path: Path) -> None:
        (tmp_path / "t.blade.php").write_text(
            "@extends('layouts.app', ['title' => 'Home'])\n"
            "<title>@yield('title', 'Default')</title>\n"
            "@section('content')\n"
            "  @component('outer')\n"
            "    @component('alert', ['type' => 'danger'])\n"
            "      @yield('inside')\n"
            "    @endcomponent\n"
            "    @yield('after')\n"
            "  @endcomponent\n"
            "@endsection\n"
            "@yield('tail')\n")
        result = analyze_blade(tmp_path)
        assert _extends(result) == [(1, "layouts.app")]
        assert _at(result, 2, "yield", "title").stable_id == _sid("yield", "title", ())
        assert _at(result, 5, "component", "alert").stable_id == _sid(
            "component", "alert", ("content", "outer"))
        assert _at(result, 6, "yield", "inside").stable_id == _sid(
            "yield", "inside", ("content", "outer", "alert"))
        # the inner @endcomponent closes the inner component, not the outer one
        assert _at(result, 8, "yield", "after").stable_id == _sid(
            "yield", "after", ("content", "outer"))
        assert _at(result, 11, "yield", "tail").stable_id == _sid("yield", "tail", ())

    def test_arguments_across_lines(self, tmp_path: Path) -> None:
        (tmp_path / "t.blade.php").write_text(
            "@component('outer')\n"
            "  @component('alert', [\n"
            "      'msg' => fn($a) => strtoupper(\"(\" . $a),\n"
            "  ])\n"
            "    @yield('inside')\n"
            "  @endcomponent\n"
            "  @yield('after')\n"
            "@endcomponent\n"
            "@extends(\n  'layouts.multi'\n)\n")
        result = analyze_blade(tmp_path)
        assert _at(result, 5, "yield", "inside").stable_id == _sid(
            "yield", "inside", ("outer", "alert"))
        assert _at(result, 7, "yield", "after").stable_id == _sid("yield", "after", ("outer",))
        assert _extends(result) == [(9, "layouts.multi")]

    def test_inline_section_is_not_a_block(self, tmp_path: Path) -> None:
        (tmp_path / "t.blade.php").write_text(
            "@section('main')\n"
            "  @section('title', 'Home')\n"
            "  @section($name, 'v')\n"
            "  @yield('in_main')\n"
            "@endsection\n")
        result = analyze_blade(tmp_path)
        assert not any(s.name == "title" for s in result.symbols)
        assert _at(result, 4, "yield", "in_main").stable_id == _sid(
            "yield", "in_main", ("main",))

    def test_dynamic_block_closer_pops_its_own_block(self, tmp_path: Path) -> None:
        (tmp_path / "t.blade.php").write_text(
            "@section('main')\n"
            "  @component($componentName)\n"
            "    @yield('inside_dynamic')\n"
            "  @endcomponent\n"
            "  @yield('after_dynamic')\n"
            "  @section($dynamicSection)\n"
            "    @yield('in_dyn_section')\n"
            "  @endsection\n"
            "  @yield('after_dyn_section')\n"
            "@endsection\n")
        result = analyze_blade(tmp_path)
        for line, name in ((3, "inside_dynamic"), (5, "after_dynamic"),
                           (7, "in_dyn_section"), (9, "after_dyn_section")):
            assert _at(result, line, "yield", name).stable_id == _sid("yield", name, ("main",))
        assert [s.name for s in result.symbols] == [
            "main", "inside_dynamic", "after_dynamic", "in_dyn_section", "after_dyn_section"]


class TestNonDirectives:
    """Comments, escapes, @verbatim and PHP blocks hold no directives."""

    def test_text_that_blade_does_not_compile(self, tmp_path: Path) -> None:
        (tmp_path / "t.blade.php").write_text(
            "{{-- @section('commented') --}}\n"
            "@yield('first')\n"
            "@@yield('escaped')\n"
            "<a href=\"mailto:x@yield('email')\">m</a>\n"
            "@verbatim\n  @section('verb')\n@endverbatim\n"
            "@php\n  $s = \"@section('php')\";\n@endphp\n"
            "<?php echo '@section(\"raw\")'; ?>\n"
            "@php($x = '@yield(\"inline_php\")')\n"
            "@yield('last')\n")
        result = analyze_blade(tmp_path)
        assert [(s.span.start_line, s.name) for s in result.symbols] == [
            (2, "first"), (13, "last")]
        assert _at(result, 13, "yield", "last").stable_id == _sid("yield", "last", ())

    def test_unterminated_regions_hold_nothing_back(self, tmp_path: Path) -> None:
        (tmp_path / "t.blade.php").write_text(
            "{{-- note\n@yield('a')\n@verbatim\n@yield('b')\n@php\n@yield('c')\n<?php\n")
        result = analyze_blade(tmp_path)
        assert [s.name for s in result.symbols] == ["a", "b", "c"]


class TestMalformedBlade:
    """Malformed templates still yield their symbols."""

    def test_stray_closers_and_unclosed_section(self, tmp_path: Path) -> None:
        (tmp_path / "t.blade.php").write_text(
            "@section('a')\n"
            "  @yield('in_a')\n"
            "@endcomponent\n"
            "  @yield('still_in_a')\n"
            "@endsection\n"
            "@endsection\n"
            "@section('unclosed')\n"
            "  @yield('to_eof')\n"
            "@component('open'\n"
            "@yield('after_bad_args')\n")
        result = analyze_blade(tmp_path)
        assert _at(result, 4, "yield", "still_in_a").stable_id == _sid(
            "yield", "still_in_a", ("a",))
        assert _at(result, 8, "yield", "to_eof").stable_id == _sid(
            "yield", "to_eof", ("unclosed",))
        # an argument list that never closes is no directive
        assert _at(result, 10, "yield", "after_bad_args").stable_id == _sid(
            "yield", "after_bad_args", ("unclosed",))


class TestIdParsesBack:
    """A quoted view name may hold ':' (Laravel's ``package::view``); the id's
    name slot folds it to '.' so the id parses back into its five slots
    (ADR-0036 Ruling 1). Symbol.name and the edge's meta keep the literal."""

    def test_package_view_name_in_a_symbol(self, tmp_path: Path) -> None:
        from hypergumbo_core.analyze.base import make_symbol_id
        from hypergumbo_core.spec_validator import _check_id_format

        page = tmp_path / "t.blade.php"
        page.write_text("@component('mail::button')\n@endcomponent\n")
        result = analyze_blade(tmp_path)
        (sym,) = result.symbols
        assert sym.name == "mail::button"
        assert sym.id == make_symbol_id(
            "blade", str(page), 1, 1, "mail::button", "component",
        )
        assert sym.id.endswith(":1-1:mail..button:component")
        assert _check_id_format(result.symbols) == []

    def test_package_view_name_in_an_extends_target(self, tmp_path: Path) -> None:
        from hypergumbo_core.ir import symbol_name_slot

        (tmp_path / "t.blade.php").write_text("@extends('pkg::layouts.app')\n")
        result = analyze_blade(tmp_path)
        (edge,) = result.edges
        assert edge.meta["template"] == "pkg::layouts.app"
        assert edge.dst.split(":")[-3:] == ["0-0", "pkg..layouts.app", "template"]
        assert symbol_name_slot(edge.dst) == "pkg..layouts.app"
