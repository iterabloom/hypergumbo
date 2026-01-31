"""Tests for file taxonomy classification (ADR-0004).

Tests the two-dimensional classification system:
- Tier (provenance): Where does the file come from?
- Role (purpose): What is the file for?

See docs/adr/0004-file-taxonomy.md for the design rationale.
"""

from pathlib import Path

import pytest

from hypergumbo_core.taxonomy import (
    FileRole,
    LanguageSpec,
    LANGUAGES,
    get_language,
    get_role,
    is_analyzable,
    is_code,
    CODE_ROLES,
)


class TestFileRole:
    """Tests for the FileRole enum."""

    def test_roles_are_flags(self) -> None:
        """FileRole supports bitwise operations for ambiguous types."""
        combined = FileRole.CONFIG | FileRole.DATA
        assert FileRole.CONFIG in combined
        assert FileRole.DATA in combined
        assert FileRole.ANALYZABLE not in combined

    def test_code_roles_excludes_data(self) -> None:
        """CODE_ROLES includes analyzable, config, docs but not data."""
        assert FileRole.ANALYZABLE in CODE_ROLES
        assert FileRole.CONFIG in CODE_ROLES
        assert FileRole.DOCUMENTATION in CODE_ROLES
        assert FileRole.DATA not in CODE_ROLES


class TestLanguageSpec:
    """Tests for the LanguageSpec dataclass."""

    def test_basic_language(self) -> None:
        """Basic language spec with single role."""
        spec = LanguageSpec(
            name="python",
            extensions=["*.py", "*.pyi"],
            roles=FileRole.ANALYZABLE,
        )
        assert spec.name == "python"
        assert "*.py" in spec.extensions
        assert spec.roles == FileRole.ANALYZABLE

    def test_ambiguous_language(self) -> None:
        """Language spec with multiple roles and disambiguation rules."""
        spec = LanguageSpec(
            name="json",
            extensions=["*.json"],
            roles=FileRole.CONFIG | FileRole.DATA,
            config_files=["package.json", "tsconfig.json"],
            data_patterns=["*_data.json", "**/fixtures/*.json"],
        )
        assert FileRole.CONFIG in spec.roles
        assert FileRole.DATA in spec.roles
        assert "package.json" in spec.config_files
        assert "*_data.json" in spec.data_patterns


class TestLanguagesRegistry:
    """Tests for the LANGUAGES registry."""

    def test_python_is_analyzable(self) -> None:
        """Python is registered as analyzable."""
        assert "python" in LANGUAGES
        assert LANGUAGES["python"].roles == FileRole.ANALYZABLE
        assert "*.py" in LANGUAGES["python"].extensions

    def test_markdown_is_documentation(self) -> None:
        """Markdown is registered as documentation."""
        assert "markdown" in LANGUAGES
        assert LANGUAGES["markdown"].roles == FileRole.DOCUMENTATION

    def test_json_is_ambiguous(self) -> None:
        """JSON is registered with both CONFIG and DATA roles."""
        assert "json" in LANGUAGES
        spec = LANGUAGES["json"]
        assert FileRole.CONFIG in spec.roles
        assert FileRole.DATA in spec.roles
        assert spec.config_files is not None
        assert "package.json" in spec.config_files

    def test_yaml_is_config(self) -> None:
        """YAML is registered as config."""
        assert "yaml" in LANGUAGES
        assert LANGUAGES["yaml"].roles == FileRole.CONFIG


class TestGetLanguage:
    """Tests for language detection from file path."""

    def test_python_file(self, tmp_path: Path) -> None:
        """Detects Python from .py extension."""
        f = tmp_path / "main.py"
        f.touch()
        assert get_language(f) == "python"

    def test_python_stub(self, tmp_path: Path) -> None:
        """Detects Python from .pyi extension."""
        f = tmp_path / "types.pyi"
        f.touch()
        assert get_language(f) == "python"

    def test_markdown_file(self, tmp_path: Path) -> None:
        """Detects Markdown from .md extension."""
        f = tmp_path / "README.md"
        f.touch()
        assert get_language(f) == "markdown"

    def test_json_file(self, tmp_path: Path) -> None:
        """Detects JSON from .json extension."""
        f = tmp_path / "config.json"
        f.touch()
        assert get_language(f) == "json"

    def test_unknown_extension(self, tmp_path: Path) -> None:
        """Returns None for unknown extensions."""
        f = tmp_path / "mystery.xyz"
        f.touch()
        assert get_language(f) is None

    def test_dockerfile_exact_match(self, tmp_path: Path) -> None:
        """Detects Dockerfile by exact filename match."""
        f = tmp_path / "Dockerfile"
        f.touch()
        assert get_language(f) == "dockerfile"

    def test_makefile_exact_match(self, tmp_path: Path) -> None:
        """Detects Makefile by exact filename match."""
        f = tmp_path / "Makefile"
        f.touch()
        assert get_language(f) == "makefile"


class TestGetRole:
    """Tests for role classification."""

    def test_python_is_analyzable(self, tmp_path: Path) -> None:
        """Python files are ANALYZABLE."""
        f = tmp_path / "main.py"
        f.touch()
        assert get_role(f) == FileRole.ANALYZABLE

    def test_markdown_is_documentation(self, tmp_path: Path) -> None:
        """Markdown files are DOCUMENTATION."""
        f = tmp_path / "README.md"
        f.touch()
        assert get_role(f) == FileRole.DOCUMENTATION

    def test_yaml_is_config(self, tmp_path: Path) -> None:
        """YAML files are CONFIG."""
        f = tmp_path / "config.yaml"
        f.touch()
        assert get_role(f) == FileRole.CONFIG

    def test_package_json_is_config(self, tmp_path: Path) -> None:
        """package.json is CONFIG (filename override)."""
        f = tmp_path / "package.json"
        f.write_text("{}")
        assert get_role(f) == FileRole.CONFIG

    def test_tsconfig_is_config(self, tmp_path: Path) -> None:
        """tsconfig.json is CONFIG (filename override)."""
        f = tmp_path / "tsconfig.json"
        f.write_text("{}")
        assert get_role(f) == FileRole.CONFIG

    def test_data_json_is_data(self, tmp_path: Path) -> None:
        """Files matching data patterns are DATA."""
        f = tmp_path / "prices_data.json"
        f.write_text("{}")
        assert get_role(f) == FileRole.DATA

    def test_fixtures_json_is_data(self, tmp_path: Path) -> None:
        """JSON in fixtures directory is DATA (path pattern match)."""
        fixtures = tmp_path / "fixtures"
        fixtures.mkdir()
        f = fixtures / "users.json"
        f.write_text("{}")
        assert get_role(f) == FileRole.DATA

    def test_nested_fixtures_json_is_data(self, tmp_path: Path) -> None:
        """JSON in nested fixtures directory is DATA (path pattern match)."""
        nested = tmp_path / "tests" / "fixtures"
        nested.mkdir(parents=True)
        f = nested / "mock_response.json"
        f.write_text("{}")
        assert get_role(f) == FileRole.DATA

    def test_large_json_is_data(self, tmp_path: Path) -> None:
        """Large JSON files (>100KB) default to DATA."""
        f = tmp_path / "huge.json"
        # Write >100KB of content
        f.write_text('{"data": "' + "x" * 110000 + '"}')
        assert get_role(f) == FileRole.DATA

    def test_small_generic_json_is_config(self, tmp_path: Path) -> None:
        """Small generic JSON files default to CONFIG."""
        f = tmp_path / "settings.json"
        f.write_text('{"key": "value"}')
        assert get_role(f) == FileRole.CONFIG

    def test_unknown_file_returns_none(self, tmp_path: Path) -> None:
        """Unknown file types return None role."""
        f = tmp_path / "mystery.xyz"
        f.touch()
        assert get_role(f) is None


class TestIsAnalyzable:
    """Tests for is_analyzable helper."""

    def test_python_is_analyzable(self, tmp_path: Path) -> None:
        """Python files are analyzable."""
        f = tmp_path / "main.py"
        f.touch()
        assert is_analyzable(f) is True

    def test_markdown_not_analyzable(self, tmp_path: Path) -> None:
        """Markdown files are not analyzable."""
        f = tmp_path / "README.md"
        f.touch()
        assert is_analyzable(f) is False

    def test_json_not_analyzable(self, tmp_path: Path) -> None:
        """JSON files are not analyzable (no symbols to extract)."""
        f = tmp_path / "config.json"
        f.touch()
        assert is_analyzable(f) is False


class TestIsCode:
    """Tests for is_code helper (what counts for LOC)."""

    def test_python_is_code(self, tmp_path: Path) -> None:
        """Python counts as code."""
        f = tmp_path / "main.py"
        f.touch()
        assert is_code(f) is True

    def test_markdown_is_code(self, tmp_path: Path) -> None:
        """Markdown counts as code (documentation is code)."""
        f = tmp_path / "README.md"
        f.touch()
        assert is_code(f) is True

    def test_config_json_is_code(self, tmp_path: Path) -> None:
        """Config JSON counts as code."""
        f = tmp_path / "package.json"
        f.write_text("{}")
        assert is_code(f) is True

    def test_data_json_not_code(self, tmp_path: Path) -> None:
        """Data JSON does not count as code."""
        f = tmp_path / "prices_data.json"
        f.write_text("{}")
        assert is_code(f) is False

    def test_unknown_not_code(self, tmp_path: Path) -> None:
        """Unknown files don't count as code."""
        f = tmp_path / "mystery.xyz"
        f.touch()
        assert is_code(f) is False


class TestLanguageExtensions:
    """Tests for LANGUAGE_EXTENSIONS derivation (Phase 2 ADR-0004)."""

    def test_language_extensions_derived_from_languages(self) -> None:
        """LANGUAGE_EXTENSIONS is derived from LANGUAGES registry."""
        from hypergumbo_core.taxonomy import LANGUAGE_EXTENSIONS, LANGUAGES

        # Check that all LANGUAGES entries are in LANGUAGE_EXTENSIONS
        for name in LANGUAGES:
            assert name in LANGUAGE_EXTENSIONS
            assert LANGUAGE_EXTENSIONS[name] == list(LANGUAGES[name].extensions)

    def test_shell_alias_exists(self) -> None:
        """Shell alias maps to bash extensions for backward compatibility."""
        from hypergumbo_core.taxonomy import LANGUAGE_EXTENSIONS, LANGUAGES

        assert "shell" in LANGUAGE_EXTENSIONS
        assert "bash" in LANGUAGES
        assert LANGUAGE_EXTENSIONS["shell"] == list(LANGUAGES["bash"].extensions)

    def test_language_extensions_has_expected_count(self) -> None:
        """LANGUAGE_EXTENSIONS has all languages plus aliases."""
        from hypergumbo_core.taxonomy import LANGUAGE_EXTENSIONS, LANGUAGES, _LANGUAGE_ALIASES

        expected_count = len(LANGUAGES) + len(_LANGUAGE_ALIASES)
        assert len(LANGUAGE_EXTENSIONS) == expected_count

    def test_get_language_extensions_returns_copy(self) -> None:
        """get_language_extensions returns a new dict each call."""
        from hypergumbo_core.taxonomy import get_language_extensions

        result1 = get_language_extensions()
        result2 = get_language_extensions()
        assert result1 is not result2
        assert result1 == result2


class TestSourceExtensions:
    """Tests for SOURCE_EXTENSIONS derivation (Phase 3 ADR-0004)."""

    def test_source_extensions_only_analyzable(self) -> None:
        """SOURCE_EXTENSIONS only includes ANALYZABLE languages."""
        from hypergumbo_core.taxonomy import SOURCE_EXTENSIONS, LANGUAGES, FileRole

        for name in SOURCE_EXTENSIONS:
            # Skip aliases
            if name in LANGUAGES:
                assert LANGUAGES[name].roles == FileRole.ANALYZABLE

    def test_source_extensions_includes_python(self) -> None:
        """SOURCE_EXTENSIONS includes Python (an analyzable language)."""
        from hypergumbo_core.taxonomy import SOURCE_EXTENSIONS

        assert "python" in SOURCE_EXTENSIONS
        assert "*.py" in SOURCE_EXTENSIONS["python"]

    def test_source_extensions_excludes_markdown(self) -> None:
        """SOURCE_EXTENSIONS excludes Markdown (documentation, not analyzable)."""
        from hypergumbo_core.taxonomy import SOURCE_EXTENSIONS

        assert "markdown" not in SOURCE_EXTENSIONS

    def test_source_extensions_excludes_json(self) -> None:
        """SOURCE_EXTENSIONS excludes JSON (config/data, not analyzable)."""
        from hypergumbo_core.taxonomy import SOURCE_EXTENSIONS

        assert "json" not in SOURCE_EXTENSIONS

    def test_source_extensions_has_shell_alias(self) -> None:
        """Shell alias is included for analyzable bash."""
        from hypergumbo_core.taxonomy import SOURCE_EXTENSIONS

        assert "shell" in SOURCE_EXTENSIONS
        assert "*.sh" in SOURCE_EXTENSIONS["shell"]

    def test_get_analyzable_extensions_returns_copy(self) -> None:
        """get_analyzable_extensions returns a new dict each call."""
        from hypergumbo_core.taxonomy import get_analyzable_extensions

        result1 = get_analyzable_extensions()
        result2 = get_analyzable_extensions()
        assert result1 is not result2
        assert result1 == result2


class TestIsAdditionalFileCandidate:
    """Tests for is_additional_file_candidate (Phase 4 ADR-0004)."""

    def test_config_yaml_is_candidate(self, tmp_path: Path) -> None:
        """YAML config files are candidates for Additional Files."""
        from hypergumbo_core.taxonomy import is_additional_file_candidate

        f = tmp_path / "config.yaml"
        f.touch()
        assert is_additional_file_candidate(f) is True

    def test_markdown_is_candidate(self, tmp_path: Path) -> None:
        """Markdown documentation files are candidates."""
        from hypergumbo_core.taxonomy import is_additional_file_candidate

        f = tmp_path / "README.md"
        f.touch()
        assert is_additional_file_candidate(f) is True

    def test_python_is_not_candidate(self, tmp_path: Path) -> None:
        """Python source files are ANALYZABLE, not candidates."""
        from hypergumbo_core.taxonomy import is_additional_file_candidate

        f = tmp_path / "main.py"
        f.touch()
        assert is_additional_file_candidate(f) is False

    def test_data_json_is_not_candidate(self, tmp_path: Path) -> None:
        """DATA JSON files are not candidates."""
        from hypergumbo_core.taxonomy import is_additional_file_candidate

        f = tmp_path / "prices_data.json"
        f.write_text("{}")
        assert is_additional_file_candidate(f) is False

    def test_unknown_file_is_not_candidate(self, tmp_path: Path) -> None:
        """Unknown file types (binary, etc.) are not candidates."""
        from hypergumbo_core.taxonomy import is_additional_file_candidate

        f = tmp_path / "image.png"
        f.touch()
        assert is_additional_file_candidate(f) is False

    def test_config_json_is_candidate(self, tmp_path: Path) -> None:
        """CONFIG JSON files are candidates."""
        from hypergumbo_core.taxonomy import is_additional_file_candidate

        f = tmp_path / "package.json"
        f.write_text("{}")
        assert is_additional_file_candidate(f) is True
