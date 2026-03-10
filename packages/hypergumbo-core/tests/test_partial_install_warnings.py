"""Tests for partial installation warnings (ADR-0010 Item 8)."""

from __future__ import annotations

import warnings

import pytest

from hypergumbo_core.partial_install_warnings import (
    LANGUAGE_PACKAGES,
    PartialInstallWarning,
    check_partial_install_warnings,
    check_partial_linker_requirements,
    check_unanalyzed_files,
)
from hypergumbo_core.profile import LanguageStats, RepoProfile


def _make_jni_diagnostics(
    java_native_count: int = 0,
    c_jni_count: int = 0,
) -> list:
    """Create mock JNI linker diagnostics for testing.

    This creates diagnostics that look like what check_linker_requirements returns
    for the JNI linker, without depending on the actual linker registry state.
    """
    from unittest.mock import MagicMock

    mock_diag = MagicMock()
    mock_diag.linker_name = "jni"
    mock_diag.linker_description = "JNI linker"

    java_req = MagicMock()
    java_req.name = "java_native_methods"
    java_req.description = "Java native method declarations"
    java_req.count = java_native_count
    java_req.met = java_native_count > 0

    c_req = MagicMock()
    c_req.name = "c_cpp_jni_functions"
    c_req.description = "C/C++ JNI implementation functions"
    c_req.count = c_jni_count
    c_req.met = c_jni_count > 0

    mock_diag.requirements = [java_req, c_req]

    return [mock_diag]


class TestCheckUnanalyzedFiles:
    """Tests for unanalyzed files detection."""

    def test_no_warnings_when_all_analyzers_registered(self) -> None:
        """No warnings when all detected languages have analyzers."""
        profile = RepoProfile(
            languages={
                "python": LanguageStats(files=10, loc=500),
                "javascript": LanguageStats(files=5, loc=200),
            }
        )
        # Simulate that python and javascript analyzers are registered
        registered = {"python", "javascript"}

        warnings_list = check_unanalyzed_files(profile, registered_languages=registered)

        assert warnings_list == []

    def test_warning_for_unanalyzed_language(self) -> None:
        """Warning emitted when language detected but no analyzer registered."""
        profile = RepoProfile(
            languages={
                "python": LanguageStats(files=10, loc=500),
                "zig": LanguageStats(files=15, loc=800),  # No analyzer
            }
        )
        # Only python is registered
        registered = {"python"}

        warnings_list = check_unanalyzed_files(profile, registered_languages=registered)

        assert len(warnings_list) == 1
        warning = warnings_list[0]
        assert warning.category == "unanalyzed_files"
        assert warning.language == "zig"
        assert "15" in warning.message  # File count
        assert "hypergumbo-lang-extended1" in warning.message
        assert "pip install" in warning.message

    def test_no_warning_for_config_only_languages(self) -> None:
        """No warnings for config-only languages like JSON, YAML."""
        profile = RepoProfile(
            languages={
                "python": LanguageStats(files=10, loc=500),
                "json": LanguageStats(files=50, loc=1000),  # Config only
                "yaml": LanguageStats(files=20, loc=400),  # Config only
            }
        )
        registered = {"python"}

        warnings_list = check_unanalyzed_files(profile, registered_languages=registered)

        assert warnings_list == []

    def test_warning_includes_correct_package(self) -> None:
        """Warning suggests the correct package for each language."""
        # Test mainstream language
        profile_mainstream = RepoProfile(
            languages={"java": LanguageStats(files=5, loc=100)}
        )
        warnings_java = check_unanalyzed_files(
            profile_mainstream, registered_languages=set()
        )
        assert len(warnings_java) == 1
        assert "hypergumbo-lang-mainstream" in warnings_java[0].message

        # Test common language
        profile_common = RepoProfile(
            languages={"scala": LanguageStats(files=5, loc=100)}
        )
        warnings_scala = check_unanalyzed_files(
            profile_common, registered_languages=set()
        )
        assert len(warnings_scala) == 1
        assert "hypergumbo-lang-common" in warnings_scala[0].message

        # Test extended language
        profile_extended = RepoProfile(
            languages={"verilog": LanguageStats(files=5, loc=100)}
        )
        warnings_verilog = check_unanalyzed_files(
            profile_extended, registered_languages=set()
        )
        assert len(warnings_verilog) == 1
        assert "hypergumbo-lang-extended1" in warnings_verilog[0].message

    def test_no_warning_for_aliased_language(self) -> None:
        """No warning when profile uses alias (e.g., 'shell') and canonical
        name ('bash') has a registered analyzer."""
        profile = RepoProfile(
            languages={
                "shell": LanguageStats(files=61, loc=3000),
            }
        )
        # 'bash' is registered (canonical name)
        registered = {"bash"}

        warnings_list = check_unanalyzed_files(profile, registered_languages=registered)

        assert warnings_list == []

    def test_aliased_language_gets_package_suggestion(self) -> None:
        """When alias is used and no analyzer registered, suggest canonical package."""
        profile = RepoProfile(
            languages={
                "shell": LanguageStats(files=10, loc=200),
            }
        )
        registered: set[str] = set()

        warnings_list = check_unanalyzed_files(profile, registered_languages=registered)

        assert len(warnings_list) == 1
        warning = warnings_list[0]
        assert warning.language == "shell"
        # Should find the package via canonical name "bash"
        assert "hypergumbo-lang-common" in warning.message

    def test_warning_for_unknown_language(self) -> None:
        """Warning for language not in our package mapping."""
        profile = RepoProfile(
            languages={"unknown_lang": LanguageStats(files=3, loc=50)}
        )
        registered: set[str] = set()

        warnings_list = check_unanalyzed_files(profile, registered_languages=registered)

        assert len(warnings_list) == 1
        warning = warnings_list[0]
        assert "no analyzer is available" in warning.message
        assert warning.suggested_package is None


class TestCheckPartialLinkerRequirements:
    """Tests for partial linker requirement detection."""

    def test_no_warnings_when_all_requirements_met(self) -> None:
        """No warning when all linker requirements are satisfied."""
        from unittest.mock import patch

        from hypergumbo_core.linkers.registry import LinkerContext

        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
        )

        # Mock check_linker_requirements to return all requirements met
        with patch(
            "hypergumbo_core.linkers.registry.check_linker_requirements",
            return_value=_make_jni_diagnostics(java_native_count=2, c_jni_count=3),
        ):
            warnings_list = check_partial_linker_requirements(ctx)

        # No partial warnings when all requirements met
        jni_warnings = [w for w in warnings_list if w.linker == "jni"]
        assert len(jni_warnings) == 0

    def test_no_warnings_when_no_requirements_met(self) -> None:
        """No warning when no linker requirements are met (nothing to link)."""
        from unittest.mock import patch

        from hypergumbo_core.linkers.registry import LinkerContext

        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
        )

        # Mock check_linker_requirements to return no requirements met
        with patch(
            "hypergumbo_core.linkers.registry.check_linker_requirements",
            return_value=_make_jni_diagnostics(java_native_count=0, c_jni_count=0),
        ):
            warnings_list = check_partial_linker_requirements(ctx)

        # No warnings for completely unmet requirements
        assert all(w.category != "partial_linker" for w in warnings_list)

    def test_warning_for_partial_jni_requirements(self) -> None:
        """Warning when JNI linker has partial requirements (Java native but no C)."""
        from unittest.mock import patch

        from hypergumbo_core.linkers.registry import LinkerContext

        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
        )

        # Mock check_linker_requirements to return partial requirements
        # (Java native methods found, but no C JNI functions)
        with patch(
            "hypergumbo_core.linkers.registry.check_linker_requirements",
            return_value=_make_jni_diagnostics(java_native_count=1, c_jni_count=0),
        ):
            warnings_list = check_partial_linker_requirements(ctx)

        # Should have a partial warning for JNI linker
        jni_warnings = [w for w in warnings_list if w.linker == "jni"]
        assert len(jni_warnings) == 1
        warning = jni_warnings[0]
        assert warning.category == "partial_linker"
        assert "JNI" in warning.message
        assert "Java native method declarations" in warning.message
        assert "C/C++ JNI implementation functions" in warning.message

    def test_partial_linker_suggests_correct_package(self) -> None:
        """Partial linker warning suggests correct package for missing language."""
        from unittest.mock import patch

        from hypergumbo_core.linkers.registry import LinkerContext

        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
        )

        # Mock check_linker_requirements to return partial requirements
        with patch(
            "hypergumbo_core.linkers.registry.check_linker_requirements",
            return_value=_make_jni_diagnostics(java_native_count=1, c_jni_count=0),
        ):
            warnings_list = check_partial_linker_requirements(ctx)

        jni_warnings = [w for w in warnings_list if w.linker == "jni"]
        assert len(jni_warnings) == 1
        warning = jni_warnings[0]
        # JNI needs C, which is in hypergumbo-lang-mainstream
        assert warning.suggested_package == "hypergumbo-lang-mainstream"

    def test_partial_linker_without_package_suggestion(self) -> None:
        """Partial linker warning with no package suggestion (unknown mapping)."""
        from unittest.mock import MagicMock, patch

        from hypergumbo_core.linkers.registry import LinkerContext

        # Create a mock diagnostics result with partial requirements
        # but the linker is not in LINKER_LANGUAGE_REQUIREMENTS
        mock_diag = MagicMock()
        mock_diag.linker_name = "unknown_linker"
        mock_diag.linker_description = "Test linker"

        met_req = MagicMock()
        met_req.name = "met_req"
        met_req.description = "Met requirement"
        met_req.count = 5
        met_req.met = True

        unmet_req = MagicMock()
        unmet_req.name = "unmet_req"
        unmet_req.description = "Unmet requirement"
        unmet_req.count = 0
        unmet_req.met = False

        mock_diag.requirements = [met_req, unmet_req]

        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
        )

        with patch(
            "hypergumbo_core.linkers.registry.check_linker_requirements",
            return_value=[mock_diag],
        ):
            warnings_list = check_partial_linker_requirements(ctx)

        assert len(warnings_list) == 1
        warning = warnings_list[0]
        assert warning.linker == "unknown_linker"
        assert warning.suggested_package is None
        assert "5 Met requirement" in warning.message
        assert "Unmet requirement" in warning.message


class TestCheckPartialInstallWarnings:
    """Tests for the combined check function."""

    def test_emits_python_warnings_when_requested(self) -> None:
        """Warnings are emitted via Python's warnings module."""
        from unittest.mock import patch
        from hypergumbo_core.linkers.registry import LinkerContext

        profile = RepoProfile(
            languages={
                "zig": LanguageStats(files=5, loc=100),
            }
        )
        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
        )

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # Mock to return empty set (no analyzers), forcing warning for zig
            # Also mock linker requirements to avoid registry dependency
            with patch(
                "hypergumbo_core.partial_install_warnings._get_registered_analyzer_languages",
                return_value=set(),
            ), patch(
                "hypergumbo_core.linkers.registry.check_linker_requirements",
                return_value=[],
            ):
                result = check_partial_install_warnings(
                    profile, ctx, emit_warnings=True
                )

        # Check that warnings were emitted
        assert len(w) > 0
        assert any("zig" in str(warning.message).lower() for warning in w)
        assert len(result) > 0

    def test_no_python_warnings_when_disabled(self) -> None:
        """No Python warnings when emit_warnings=False."""
        from unittest.mock import patch
        from hypergumbo_core.linkers.registry import LinkerContext

        profile = RepoProfile(
            languages={
                "zig": LanguageStats(files=5, loc=100),
            }
        )
        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
        )

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # Mock to return empty set (no analyzers), forcing warning condition
            # Also mock linker requirements to avoid registry dependency
            with patch(
                "hypergumbo_core.partial_install_warnings._get_registered_analyzer_languages",
                return_value=set(),
            ), patch(
                "hypergumbo_core.linkers.registry.check_linker_requirements",
                return_value=[],
            ):
                result = check_partial_install_warnings(
                    profile, ctx, emit_warnings=False
                )

        # No Python warnings emitted
        assert len(w) == 0
        # But we still get the result
        assert len(result) > 0


class TestGetRegisteredAnalyzerLanguages:
    """Tests for analyzer language discovery."""

    def test_discovers_registered_analyzers(self) -> None:
        """_get_registered_analyzer_languages discovers installed analyzers."""
        from hypergumbo_core.partial_install_warnings import (
            _get_registered_analyzer_languages,
        )

        # In the monorepo, many analyzers are registered
        registered = _get_registered_analyzer_languages()

        # Should discover at least some common languages
        # (exact set depends on what's installed)
        assert isinstance(registered, set)
        # In CI/development, core languages should be available
        # This is a sanity check that the function works

    def test_handles_javascript_ts_pattern(self) -> None:
        """Analyzer names like javascript_ts map to both JS and TS."""
        from unittest.mock import patch, MagicMock

        from hypergumbo_core.partial_install_warnings import (
            _get_registered_analyzer_languages,
        )

        # Mock get_analyzers to return an analyzer with js_ts pattern
        mock_spec = MagicMock()
        mock_spec.name = "javascript_ts"

        with patch(
            "hypergumbo_core.analyze.all_analyzers.get_analyzers",
            return_value=[mock_spec],
        ):
            registered = _get_registered_analyzer_languages()

        assert "javascript" in registered
        assert "typescript" in registered

    def test_javascript_analyzer_covers_typescript(self) -> None:
        """Analyzer registered as 'javascript' implies TypeScript support too.

        The JS/TS analyzer is registered as 'javascript' via
        @register_analyzer('javascript') but handles both .js and .ts files.
        Without this mapping, repos with TypeScript files produce a spurious
        'Typescript analyzer not installed' warning.
        """
        from unittest.mock import MagicMock, patch

        from hypergumbo_core.partial_install_warnings import (
            _get_registered_analyzer_languages,
        )

        mock_spec = MagicMock()
        mock_spec.name = "javascript"

        with patch(
            "hypergumbo_core.analyze.all_analyzers.get_analyzers",
            return_value=[mock_spec],
        ):
            registered = _get_registered_analyzer_languages()

        assert "javascript" in registered
        assert "typescript" in registered

    def test_handles_underscore_names(self) -> None:
        """Analyzer names with underscores map parts to languages."""
        from unittest.mock import patch, MagicMock

        from hypergumbo_core.partial_install_warnings import (
            _get_registered_analyzer_languages,
        )

        # Mock get_analyzers to return an analyzer with underscore in name
        mock_spec = MagicMock()
        mock_spec.name = "swift_objc"

        with patch(
            "hypergumbo_core.analyze.all_analyzers.get_analyzers",
            return_value=[mock_spec],
        ):
            registered = _get_registered_analyzer_languages()

        # swift is in LANGUAGE_PACKAGES, objc is in LANGUAGE_PACKAGES
        assert "swift" in registered
        assert "objc" in registered

    def test_handles_simple_names(self) -> None:
        """Simple analyzer names are added directly."""
        from unittest.mock import patch, MagicMock

        from hypergumbo_core.partial_install_warnings import (
            _get_registered_analyzer_languages,
        )

        # Mock get_analyzers to return simple analyzer names
        mock_spec1 = MagicMock()
        mock_spec1.name = "python"
        mock_spec2 = MagicMock()
        mock_spec2.name = "go"

        with patch(
            "hypergumbo_core.analyze.all_analyzers.get_analyzers",
            return_value=[mock_spec1, mock_spec2],
        ):
            registered = _get_registered_analyzer_languages()

        assert "python" in registered
        assert "go" in registered

    def test_handles_cpp_pattern(self) -> None:
        """Analyzer names like c_cpp or cpp map correctly."""
        from unittest.mock import patch, MagicMock

        from hypergumbo_core.partial_install_warnings import (
            _get_registered_analyzer_languages,
        )

        mock_spec = MagicMock()
        mock_spec.name = "cpp"

        with patch(
            "hypergumbo_core.analyze.all_analyzers.get_analyzers",
            return_value=[mock_spec],
        ):
            registered = _get_registered_analyzer_languages()

        assert "cpp" in registered

    def test_handles_objc_pattern(self) -> None:
        """Analyzer named objc adds objc."""
        from unittest.mock import patch, MagicMock

        from hypergumbo_core.partial_install_warnings import (
            _get_registered_analyzer_languages,
        )

        mock_spec = MagicMock()
        mock_spec.name = "objc"

        with patch(
            "hypergumbo_core.analyze.all_analyzers.get_analyzers",
            return_value=[mock_spec],
        ):
            registered = _get_registered_analyzer_languages()

        assert "objc" in registered


class TestLanguagePackageMapping:
    """Tests for the LANGUAGE_PACKAGES mapping."""

    def test_mainstream_languages_mapped_correctly(self) -> None:
        """Mainstream languages map to hypergumbo-lang-mainstream."""
        mainstream = [
            "python", "javascript", "typescript", "java", "c", "cpp",
            "csharp", "go", "rust", "ruby", "php", "swift", "kotlin"
        ]
        for lang in mainstream:
            assert LANGUAGE_PACKAGES.get(lang) == "hypergumbo-lang-mainstream", (
                f"{lang} should be in hypergumbo-lang-mainstream"
            )

    def test_common_languages_mapped_correctly(self) -> None:
        """Common languages map to hypergumbo-lang-common."""
        common = [
            "scala", "bash", "sql", "lua", "perl", "haskell", "ocaml",
            "elixir", "erlang", "clojure", "fsharp"
        ]
        for lang in common:
            assert LANGUAGE_PACKAGES.get(lang) == "hypergumbo-lang-common", (
                f"{lang} should be in hypergumbo-lang-common"
            )

    def test_extended_languages_mapped_correctly(self) -> None:
        """Extended languages map to hypergumbo-lang-extended1."""
        extended = [
            "zig", "nim", "agda", "lean", "cobol", "solidity",
            "verilog", "vhdl", "fortran"
        ]
        for lang in extended:
            assert LANGUAGE_PACKAGES.get(lang) == "hypergumbo-lang-extended1", (
                f"{lang} should be in hypergumbo-lang-extended1"
            )
