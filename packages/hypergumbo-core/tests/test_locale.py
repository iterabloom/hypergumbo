"""Tests for locale-aware documentation directory handling.

Covers detection of translated documentation directories (GitLab-style
doc-locale/<lang>/ and FastAPI-style docs/<lang>/), exclusion of locale
dirs during file discovery, and --locale flag behavior including CLI
integration via _setup_locale_filtering.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.discovery import (
    _is_locale_dirname,
    detect_locale_dirs,
    find_files,
    get_locale_excludes,
    set_locale_excludes,
)


def _make_locale_tree_gitlab(root: Path) -> None:
    """Create GitLab-style locale directory structure.

    doc/           <- English (primary)
    doc-locale/
      ja-jp/       <- Japanese translations
      fr-fr/       <- French translations
    """
    (root / "doc").mkdir()
    (root / "doc" / "intro.md").write_text("# Introduction")
    (root / "doc" / "setup.md").write_text("# Setup")

    for lang in ("ja-jp", "fr-fr"):
        d = root / "doc-locale" / lang
        d.mkdir(parents=True)
        (d / "intro.md").write_text(f"# Introduction ({lang})")
        (d / "setup.md").write_text(f"# Setup ({lang})")


def _make_locale_tree_fastapi(root: Path) -> None:
    """Create FastAPI-style locale directory structure.

    docs/
      en/          <- English
      ja/           <- Japanese
      es/           <- Spanish
    """
    for lang in ("en", "ja", "es"):
        d = root / "docs" / lang
        d.mkdir(parents=True)
        (d / "index.md").write_text(f"# Docs ({lang})")
        (d / "tutorial.md").write_text(f"# Tutorial ({lang})")


class TestDetectLocaleDirs:
    """Tests for detect_locale_dirs()."""

    def test_gitlab_style(self, tmp_path: Path) -> None:
        """Detects doc-locale/<lang>/ directories."""
        _make_locale_tree_gitlab(tmp_path)
        result = detect_locale_dirs(tmp_path)
        assert result.style == "doc-locale"
        assert set(result.languages) == {"ja-jp", "fr-fr"}
        assert result.primary_dir == tmp_path / "doc"

    def test_fastapi_style(self, tmp_path: Path) -> None:
        """Detects docs/<lang>/ peer directories with en/ as primary."""
        _make_locale_tree_fastapi(tmp_path)
        result = detect_locale_dirs(tmp_path)
        assert result.style == "docs-peer"
        assert set(result.languages) == {"ja", "es"}
        assert result.primary_dir == tmp_path / "docs" / "en"

    def test_gitlab_style_docs_fallback(self, tmp_path: Path) -> None:
        """When doc-locale/ exists but doc/ doesn't, falls back to docs/ as primary."""
        # Create doc-locale but use docs/ instead of doc/
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "intro.md").write_text("# Introduction")
        for lang in ("ja-jp",):
            d = tmp_path / "doc-locale" / lang
            d.mkdir(parents=True)
            (d / "intro.md").write_text(f"# Introduction ({lang})")
        result = detect_locale_dirs(tmp_path)
        assert result is not None
        assert result.style == "doc-locale"
        assert result.primary_dir == tmp_path / "docs"

    def test_no_locales(self, tmp_path: Path) -> None:
        """Returns None when no locale directories are found."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hi')")
        result = detect_locale_dirs(tmp_path)
        assert result is None

    def test_single_doc_dir_no_locales(self, tmp_path: Path) -> None:
        """A plain docs/ directory without language subdirs is not a locale."""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "readme.md").write_text("# Docs")
        result = detect_locale_dirs(tmp_path)
        assert result is None

    def test_docs_with_non_language_subdirs(self, tmp_path: Path) -> None:
        """docs/api/ and docs/guide/ are not language codes."""
        for d in ("api", "guide", "images"):
            (tmp_path / "docs" / d).mkdir(parents=True)
            (tmp_path / "docs" / d / "index.md").write_text("# Content")
        result = detect_locale_dirs(tmp_path)
        assert result is None

    def test_fastapi_style_needs_en(self, tmp_path: Path) -> None:
        """FastAPI-style requires an en/ directory to be present."""
        # Only non-English locale dirs — no en/ — not a locale pattern
        for lang in ("ja", "es"):
            d = tmp_path / "docs" / lang
            d.mkdir(parents=True)
            (d / "index.md").write_text(f"# {lang}")
        result = detect_locale_dirs(tmp_path)
        assert result is None


class TestSetLocaleExcludes:
    """Tests for the global locale exclude mechanism."""

    def test_set_and_get(self) -> None:
        """Can set and retrieve locale exclude paths."""
        try:
            set_locale_excludes([Path("/repo/doc-locale/ja-jp")])
            excludes = get_locale_excludes()
            assert len(excludes) == 1
            assert excludes[0] == Path("/repo/doc-locale/ja-jp")
        finally:
            set_locale_excludes(None)

    def test_default_is_none(self) -> None:
        """Default locale excludes are None (no filtering)."""
        set_locale_excludes(None)
        assert get_locale_excludes() is None

    def test_find_files_respects_locale_excludes(self, tmp_path: Path) -> None:
        """find_files skips directories listed in locale excludes."""
        _make_locale_tree_gitlab(tmp_path)

        # Without locale excludes, all .md files are found
        all_files = list(find_files(tmp_path, ["*.md"]))
        all_names = {str(p.relative_to(tmp_path)) for p in all_files}
        assert "doc-locale/ja-jp/intro.md" in all_names
        assert "doc/intro.md" in all_names

        # With locale excludes, translated files are skipped
        try:
            set_locale_excludes([
                tmp_path / "doc-locale" / "ja-jp",
                tmp_path / "doc-locale" / "fr-fr",
            ])
            filtered = list(find_files(tmp_path, ["*.md"]))
            filtered_names = {str(p.relative_to(tmp_path)) for p in filtered}
            assert "doc/intro.md" in filtered_names
            assert "doc/setup.md" in filtered_names
            assert "doc-locale/ja-jp/intro.md" not in filtered_names
            assert "doc-locale/fr-fr/intro.md" not in filtered_names
        finally:
            set_locale_excludes(None)

    def test_find_files_locale_excludes_fastapi_style(self, tmp_path: Path) -> None:
        """find_files excludes non-English locale dirs in FastAPI-style repos."""
        _make_locale_tree_fastapi(tmp_path)

        try:
            set_locale_excludes([
                tmp_path / "docs" / "ja",
                tmp_path / "docs" / "es",
            ])
            filtered = list(find_files(tmp_path, ["*.md"]))
            filtered_names = {str(p.relative_to(tmp_path)) for p in filtered}
            assert "docs/en/index.md" in filtered_names
            assert "docs/ja/index.md" not in filtered_names
            assert "docs/es/index.md" not in filtered_names
        finally:
            set_locale_excludes(None)

    def test_locale_swap_to_specific_language(self, tmp_path: Path) -> None:
        """When --locale ja-jp is used, exclude primary and other locales, include ja-jp."""
        _make_locale_tree_gitlab(tmp_path)

        # Simulate --locale ja-jp: exclude English docs and fr-fr, keep ja-jp
        try:
            set_locale_excludes([
                tmp_path / "doc",
                tmp_path / "doc-locale" / "fr-fr",
            ])
            filtered = list(find_files(tmp_path, ["*.md"]))
            filtered_names = {str(p.relative_to(tmp_path)) for p in filtered}
            assert "doc-locale/ja-jp/intro.md" in filtered_names
            assert "doc/intro.md" not in filtered_names
            assert "doc-locale/fr-fr/intro.md" not in filtered_names
        finally:
            set_locale_excludes(None)


class TestLocaleExcludesForLocale:
    """Tests for computing locale excludes given a --locale flag value."""

    def test_default_excludes_translations(self, tmp_path: Path) -> None:
        """Default (no --locale): exclude all translation dirs, keep primary."""
        _make_locale_tree_gitlab(tmp_path)
        info = detect_locale_dirs(tmp_path)
        assert info is not None
        excludes = info.excludes_for_locale(None)
        assert tmp_path / "doc-locale" / "ja-jp" in excludes
        assert tmp_path / "doc-locale" / "fr-fr" in excludes
        assert tmp_path / "doc" not in excludes

    def test_specific_locale_excludes_others(self, tmp_path: Path) -> None:
        """--locale ja-jp: exclude primary and other locales, keep ja-jp."""
        _make_locale_tree_gitlab(tmp_path)
        info = detect_locale_dirs(tmp_path)
        assert info is not None
        excludes = info.excludes_for_locale("ja-jp")
        assert tmp_path / "doc" in excludes
        assert tmp_path / "doc-locale" / "fr-fr" in excludes
        assert tmp_path / "doc-locale" / "ja-jp" not in excludes

    def test_invalid_locale_raises(self, tmp_path: Path) -> None:
        """--locale de: error if locale directory doesn't exist."""
        _make_locale_tree_gitlab(tmp_path)
        info = detect_locale_dirs(tmp_path)
        assert info is not None
        with pytest.raises(ValueError, match="de"):
            info.excludes_for_locale("de")

    def test_fastapi_default(self, tmp_path: Path) -> None:
        """FastAPI-style default: exclude ja/ and es/, keep en/."""
        _make_locale_tree_fastapi(tmp_path)
        info = detect_locale_dirs(tmp_path)
        assert info is not None
        excludes = info.excludes_for_locale(None)
        assert tmp_path / "docs" / "ja" in excludes
        assert tmp_path / "docs" / "es" in excludes
        assert tmp_path / "docs" / "en" not in excludes

    def test_fastapi_specific_locale(self, tmp_path: Path) -> None:
        """FastAPI-style --locale ja: exclude en/ and es/, keep ja/."""
        _make_locale_tree_fastapi(tmp_path)
        info = detect_locale_dirs(tmp_path)
        assert info is not None
        excludes = info.excludes_for_locale("ja")
        assert tmp_path / "docs" / "en" in excludes
        assert tmp_path / "docs" / "es" in excludes
        assert tmp_path / "docs" / "ja" not in excludes


class TestIsLocaleDirname:
    """Tests for _is_locale_dirname heuristic."""

    def test_iso_639_codes(self) -> None:
        """Recognizes ISO 639-1 two-letter codes."""
        assert _is_locale_dirname("en") is True
        assert _is_locale_dirname("ja") is True
        assert _is_locale_dirname("fr") is True
        assert _is_locale_dirname("zh") is True

    def test_extended_locale_tags(self) -> None:
        """Recognizes extended locale tags like ja-jp, pt-br."""
        assert _is_locale_dirname("ja-jp") is True
        assert _is_locale_dirname("pt-br") is True
        assert _is_locale_dirname("zh-hans") is True

    def test_non_locale_names(self) -> None:
        """Rejects non-locale directory names."""
        assert _is_locale_dirname("api") is False
        assert _is_locale_dirname("guide") is False
        assert _is_locale_dirname("images") is False
        assert _is_locale_dirname("src") is False
        assert _is_locale_dirname("test") is False

    def test_case_insensitive(self) -> None:
        """Locale detection is case-insensitive."""
        assert _is_locale_dirname("EN") is True
        assert _is_locale_dirname("JA-JP") is True


class TestSetupLocaleFiltering:
    """Tests for the CLI-level _setup_locale_filtering helper."""

    def test_no_locales_no_flag(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """No locale dirs, no --locale flag: no output, no excludes."""
        from hypergumbo_core.cli import _setup_locale_filtering

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("pass")

        try:
            _setup_locale_filtering(tmp_path, None)
            assert get_locale_excludes() is None
        finally:
            set_locale_excludes(None)

    def test_no_locales_with_flag_warns(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """No locale dirs but --locale specified: prints warning."""
        from hypergumbo_core.cli import _setup_locale_filtering

        (tmp_path / "src").mkdir()

        try:
            _setup_locale_filtering(tmp_path, "ja")
            captured = capsys.readouterr()
            assert "WARNING" in captured.err
            assert "ja" in captured.err
        finally:
            set_locale_excludes(None)

    def test_locales_detected_default(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """Locale dirs detected, no --locale: logs detection and excludes translations."""
        from hypergumbo_core.cli import _setup_locale_filtering

        _make_locale_tree_gitlab(tmp_path)

        try:
            _setup_locale_filtering(tmp_path, None)
            captured = capsys.readouterr()
            assert "Locale docs detected" in captured.err
            assert "doc-locale" in captured.err
            assert "Excluding translated docs" in captured.err

            excludes = get_locale_excludes()
            assert excludes is not None
            assert len(excludes) == 2
        finally:
            set_locale_excludes(None)

    def test_locales_detected_specific(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """Locale dirs detected with --locale ja-jp: logs swap decision."""
        from hypergumbo_core.cli import _setup_locale_filtering

        _make_locale_tree_gitlab(tmp_path)

        try:
            _setup_locale_filtering(tmp_path, "ja-jp")
            captured = capsys.readouterr()
            assert "Locale docs detected" in captured.err
            assert "Using locale 'ja-jp'" in captured.err

            excludes = get_locale_excludes()
            assert excludes is not None
            # Excludes doc/ and doc-locale/fr-fr, but NOT doc-locale/ja-jp
            exclude_names = {d.name for d in excludes}
            assert "doc" in exclude_names
            assert "fr-fr" in exclude_names
        finally:
            set_locale_excludes(None)

    def test_invalid_locale_exits(self, tmp_path: Path) -> None:
        """Invalid --locale value causes sys.exit(1)."""
        from hypergumbo_core.cli import _setup_locale_filtering

        _make_locale_tree_gitlab(tmp_path)

        try:
            with pytest.raises(SystemExit) as exc_info:
                _setup_locale_filtering(tmp_path, "de")
            assert exc_info.value.code == 1
        finally:
            set_locale_excludes(None)


class TestCliLocaleArgParsing:
    """Tests that --locale is parsed correctly by the CLI."""

    def test_run_parser_accepts_locale(self) -> None:
        """The run subcommand accepts --locale."""
        from hypergumbo_core.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["run", ".", "--locale", "ja-jp"])
        assert args.locale == "ja-jp"

    def test_run_parser_locale_default_none(self) -> None:
        """The run subcommand defaults locale to None."""
        from hypergumbo_core.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["run", "."])
        assert args.locale is None

    def test_sketch_parser_accepts_locale(self) -> None:
        """The sketch subcommand accepts --locale."""
        from hypergumbo_core.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["sketch", ".", "--locale", "fr"])
        assert args.locale == "fr"

    def test_sketch_parser_locale_default_none(self) -> None:
        """The sketch subcommand defaults locale to None."""
        from hypergumbo_core.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["sketch", "."])
        assert args.locale is None
