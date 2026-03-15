# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Blade template analyzer."""
from pathlib import Path

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
        (tmp_path / "page.blade.php").write_text("@extends('layouts.app')")
        result = analyze_blade(tmp_path)
        names = [s.name for s in result.symbols]
        assert "layouts.app" in names
        assert any(s.kind == "extends" for s in result.symbols)

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
        assert len(result.symbols) == 3

    def test_double_quoted_names(self, tmp_path: Path) -> None:
        (tmp_path / "page.blade.php").write_text('@section("content")\n@endsection')
        result = analyze_blade(tmp_path)
        assert any(s.name == "content" for s in result.symbols)
