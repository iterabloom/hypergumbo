# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for partial installation warnings (ADR-0010 Item 8)."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from hypergumbo_core.partial_install_warnings import (
    LANGUAGE_PACKAGES,
    PartialInstallWarning,
    check_partial_install_warnings,
    check_partial_linker_requirements,
    check_rust_analyzer_disclosure,
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


def _make_file_presence_diagnostics(js_ts_file_count: int, pattern_count: int) -> list:
    """Mock diagnostics where the met requirement is a ``*_files`` presence check.

    Mirrors the structure of NAPI/TAURI_IPC/SOLIDITY_ABI/etc.: one requirement
    is a language file count (suffix ``_files``) and the other is the actual
    pattern match that would indicate intentional linker use.
    """
    from unittest.mock import MagicMock

    mock_diag = MagicMock()
    mock_diag.linker_name = "napi"
    mock_diag.linker_description = "NAPI linker"

    js_req = MagicMock()
    js_req.name = "js_ts_files"
    js_req.description = "JavaScript/TypeScript files (potential native addon callers)"
    js_req.count = js_ts_file_count
    js_req.met = js_ts_file_count > 0

    pattern_req = MagicMock()
    pattern_req.name = "c_cpp_napi_functions"
    pattern_req.description = "C/C++ N-API function implementations"
    pattern_req.count = pattern_count
    pattern_req.met = pattern_count > 0

    mock_diag.requirements = [js_req, pattern_req]
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

    def test_file_presence_only_met_suppresses_warning(self) -> None:
        """WI-vasir: cross-language linkers with only ``_files`` met are silent.

        NAPI / TAURI_IPC / SOLIDITY_ABI / WASM_BINDGEN / IPC / LUA_FFI /
        RUBY_FFI / PYFFI all define a ``<lang>_files`` requirement plus a
        concrete-pattern requirement. On any polyglot repo the file count
        triggers the partial-install warning even though the repo plainly
        doesn't use the linker's target integration. Suppress the warning
        when the met requirements are all file-presence checks.
        """
        from unittest.mock import patch

        from hypergumbo_core.linkers.registry import LinkerContext

        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
        )

        with patch(
            "hypergumbo_core.linkers.registry.check_linker_requirements",
            return_value=_make_file_presence_diagnostics(
                js_ts_file_count=19, pattern_count=0,
            ),
        ):
            warnings_list = check_partial_linker_requirements(ctx)

        assert all(w.linker != "napi" for w in warnings_list), (
            "Partial warning must be suppressed when the only met "
            "requirement is a language-file presence check."
        )

    def test_file_presence_plus_pattern_met_still_warns(self) -> None:
        """When a pattern-level requirement IS met, the partial warning still fires.

        Regression guard against the WI-vasir suppression widening past its
        intended scope: if a repo has both JS/TS files AND some NAPI
        patterns, a missing complementary requirement is real signal.
        """
        from unittest.mock import MagicMock, patch

        from hypergumbo_core.linkers.registry import LinkerContext

        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
        )

        # Three requirements: js_ts_files (met), napi_patterns (met),
        # third_addon_src (unmet). Met side includes a non-_files entry
        # so the suppression should NOT apply.
        mock_diag = MagicMock()
        mock_diag.linker_name = "napi"
        mock_diag.linker_description = "NAPI linker"
        js_req = MagicMock()
        js_req.name = "js_ts_files"
        js_req.description = "JavaScript/TypeScript files"
        js_req.count = 10
        js_req.met = True
        pat_req = MagicMock()
        pat_req.name = "napi_patterns"
        pat_req.description = "napi_create_function call sites"
        pat_req.count = 3
        pat_req.met = True
        src_req = MagicMock()
        src_req.name = "c_cpp_napi_functions"
        src_req.description = "C/C++ N-API function implementations"
        src_req.count = 0
        src_req.met = False
        mock_diag.requirements = [js_req, pat_req, src_req]

        with patch(
            "hypergumbo_core.linkers.registry.check_linker_requirements",
            return_value=[mock_diag],
        ):
            warnings_list = check_partial_linker_requirements(ctx)

        napi_warnings = [w for w in warnings_list if w.linker == "napi"]
        assert len(napi_warnings) == 1, (
            "Partial warning must still fire when a pattern-level "
            "requirement is met alongside the file presence check."
        )

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

    @staticmethod
    def _make_cgo_diag_and_spec():
        """Helper for the WI-zamoz gate tests.

        Returns ``(mock_diag, fake_spec)`` shaped like the real CGO linker:
        ``language_pairs=[("go", "c"), ("go", "cpp")]`` activation, one met
        ``c_cpp_functions`` requirement and one unmet ``go_cgo_calls``
        requirement (the candle-shaped partial diagnostic).
        """
        from unittest.mock import MagicMock

        from hypergumbo_core.linkers.registry import LinkerActivation

        mock_diag = MagicMock()
        mock_diag.linker_name = "cgo"
        mock_diag.linker_description = "CGO linker"
        go_req = MagicMock()
        go_req.name = "go_cgo_calls"
        go_req.description = "Go cgo calls (C.funcName() via import \"C\")"
        go_req.count = 0
        go_req.met = False
        c_req = MagicMock()
        c_req.name = "c_cpp_functions"
        c_req.description = "C/C++ function implementations"
        c_req.count = 151
        c_req.met = True
        mock_diag.requirements = [go_req, c_req]

        fake_spec = MagicMock()
        fake_spec.name = "cgo"
        fake_spec.activation = LinkerActivation(
            language_pairs=[("go", "c"), ("go", "cpp")]
        )
        return mock_diag, fake_spec

    def test_cgo_warning_suppressed_when_go_absent_in_tree(self) -> None:
        """WI-zamoz: suppress partial-install warning when the linker's
        activation conditions wouldn't trigger it on the detected tree.

        The CGO linker activates on
        ``language_pairs=[("go", "c"), ("go", "cpp")]``. A repo with
        C/C++ symbols but no Go (e.g. candle: Rust + Python + C/CUDA
        bindings) produces a CGO diagnostic with met=c_cpp_functions
        and unmet=go_cgo_calls. Without the gate, this fires
        ``CGO linker found N C/C++ implementations but 0 Go cgo
        calls`` — unactionable because the user has no Go in tree.
        """
        from unittest.mock import patch

        from hypergumbo_core.linkers.registry import LinkerContext

        mock_diag, fake_spec = self._make_cgo_diag_and_spec()

        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
            detected_languages={"rust", "python", "c"},
        )

        with patch(
            "hypergumbo_core.linkers.registry.check_linker_requirements",
            return_value=[mock_diag],
        ), patch(
            "hypergumbo_core.linkers.registry.get_all_linkers",
            return_value=iter([fake_spec]),
        ):
            warnings_list = check_partial_linker_requirements(ctx)

        assert all(w.linker != "cgo" for w in warnings_list), (
            "Partial warning must be suppressed when the linker's "
            "activation conditions wouldn't fire on the detected tree."
        )

    def test_cgo_warning_fires_when_go_present_in_tree(self) -> None:
        """Regression guard for the WI-zamoz gate.

        When the CGO linker activation conditions ARE met (Go + C in
        ``detected_languages``), a partial diagnostic must still emit
        a warning. Distinguishes "linker would have run, requirements
        partially met" (real signal — emit) from "linker wouldn't have
        run, requirements look partial as artefact" (noise — suppress).
        """
        from unittest.mock import patch

        from hypergumbo_core.linkers.registry import LinkerContext

        mock_diag, fake_spec = self._make_cgo_diag_and_spec()

        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
            detected_languages={"go", "c"},
        )

        with patch(
            "hypergumbo_core.linkers.registry.check_linker_requirements",
            return_value=[mock_diag],
        ), patch(
            "hypergumbo_core.linkers.registry.get_all_linkers",
            return_value=iter([fake_spec]),
        ):
            warnings_list = check_partial_linker_requirements(ctx)

        cgo_warnings = [w for w in warnings_list if w.linker == "cgo"]
        assert len(cgo_warnings) == 1, (
            "Partial warning must still fire when the linker would "
            "have activated on the detected tree."
        )

    def test_activation_gate_no_op_when_detection_info_empty(self) -> None:
        """Backward-compat: existing tests pass empty detection info.

        The WI-zamoz gate is a noise-reducer for real CLI runs (where
        the profile always populates ``detected_languages`` /
        ``detected_frameworks``). Test fixtures that skip detection —
        e.g. crafted-diagnostic mocks with the default empty sets —
        must keep their existing behavior.
        """
        from unittest.mock import MagicMock, patch

        from hypergumbo_core.linkers.registry import LinkerContext

        mock_diag = MagicMock()
        mock_diag.linker_name = "cgo"
        mock_diag.linker_description = "CGO linker"
        go_req = MagicMock()
        go_req.name = "go_cgo_calls"
        go_req.description = "Go cgo calls"
        go_req.count = 0
        go_req.met = False
        c_req = MagicMock()
        c_req.name = "c_cpp_functions"
        c_req.description = "C/C++ function implementations"
        c_req.count = 151
        c_req.met = True
        mock_diag.requirements = [go_req, c_req]

        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
            # detected_languages and detected_frameworks default to empty
        )

        with patch(
            "hypergumbo_core.linkers.registry.check_linker_requirements",
            return_value=[mock_diag],
        ):
            warnings_list = check_partial_linker_requirements(ctx)

        cgo_warnings = [w for w in warnings_list if w.linker == "cgo"]
        assert len(cgo_warnings) == 1

    @staticmethod
    def _make_dependency_diag_and_spec():
        """Helper for the WI-ruman gate tests.

        Returns ``(mock_diag, fake_spec)`` shaped like the real DEPENDENCY
        linker: ``always=True`` activation, one met ``import_edges``
        requirement and one unmet ``toml_dependencies`` requirement.
        """
        from unittest.mock import MagicMock

        from hypergumbo_core.linkers.registry import LinkerActivation

        mock_diag = MagicMock()
        mock_diag.linker_name = "dependency"
        mock_diag.linker_description = "Dependency linker"
        toml_req = MagicMock()
        toml_req.name = "toml_dependencies"
        toml_req.description = (
            "TOML dependency declarations (Cargo.toml, pyproject.toml)"
        )
        toml_req.count = 0
        toml_req.met = False
        import_req = MagicMock()
        import_req.name = "import_edges"
        import_req.description = "Import edges from code analyzers"
        import_req.count = 42
        import_req.met = True
        mock_diag.requirements = [toml_req, import_req]

        fake_spec = MagicMock()
        fake_spec.name = "dependency"
        fake_spec.activation = LinkerActivation(always=True)
        return mock_diag, fake_spec

    def test_dependency_toml_warning_suppressed_on_go_only_tree(self) -> None:
        """WI-ruman: suppress DEPENDENCY/toml partial-install warning when
        the tree contains no TOML-using languages.

        The DEPENDENCY linker has ``always=True`` activation, so the
        WI-zamoz gate doesn't fire. But on a pure-Go repo the unmet
        ``toml_dependencies`` requirement is structurally impossible to
        satisfy — Go uses ``go.mod``, not TOML manifests. The warning
        suggests installing ``hypergumbo-lang-mainstream`` to populate
        TOML deps, but on a Go-only tree there are no TOML manifests to
        parse regardless of which packages are installed. Suppress.
        """
        from unittest.mock import patch

        from hypergumbo_core.linkers.registry import LinkerContext

        mock_diag, fake_spec = self._make_dependency_diag_and_spec()

        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
            detected_languages={"go"},
        )

        with patch(
            "hypergumbo_core.linkers.registry.check_linker_requirements",
            return_value=[mock_diag],
        ), patch(
            "hypergumbo_core.linkers.registry.get_all_linkers",
            return_value=iter([fake_spec]),
        ):
            warnings_list = check_partial_linker_requirements(ctx)

        assert all(w.linker != "dependency" for w in warnings_list), (
            "DEPENDENCY/toml_dependencies partial warning must be "
            "suppressed when neither Rust nor Python is in the tree."
        )

    def test_dependency_toml_warning_fires_on_rust_tree(self) -> None:
        """Regression guard for the WI-ruman gate.

        When the tree DOES contain a TOML-using language (Rust here),
        the partial DEPENDENCY/toml diagnostic remains a real signal —
        the user has Rust code but no TOML deps were extracted, which
        is the legitimate ``is hypergumbo-lang-mainstream installed?``
        case. Distinguishes "no toml because tree doesn't use toml"
        (suppress) from "no toml because the parser is missing"
        (emit).
        """
        from unittest.mock import patch

        from hypergumbo_core.linkers.registry import LinkerContext

        mock_diag, fake_spec = self._make_dependency_diag_and_spec()

        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
            detected_languages={"rust", "c"},
        )

        with patch(
            "hypergumbo_core.linkers.registry.check_linker_requirements",
            return_value=[mock_diag],
        ), patch(
            "hypergumbo_core.linkers.registry.get_all_linkers",
            return_value=iter([fake_spec]),
        ):
            warnings_list = check_partial_linker_requirements(ctx)

        dependency_warnings = [
            w for w in warnings_list if w.linker == "dependency"
        ]
        assert len(dependency_warnings) == 1

    def test_dependency_toml_warning_fires_on_python_tree(self) -> None:
        """Regression guard for the WI-ruman gate.

        Python is the other TOML-using language. The partial warning
        must still fire when Python is in the tree (pyproject.toml is
        the expected manifest shape).
        """
        from unittest.mock import patch

        from hypergumbo_core.linkers.registry import LinkerContext

        mock_diag, fake_spec = self._make_dependency_diag_and_spec()

        ctx = LinkerContext(
            repo_root=None,  # type: ignore[arg-type]
            symbols=[],
            edges=[],
            detected_languages={"python", "javascript"},
        )

        with patch(
            "hypergumbo_core.linkers.registry.check_linker_requirements",
            return_value=[mock_diag],
        ), patch(
            "hypergumbo_core.linkers.registry.get_all_linkers",
            return_value=iter([fake_spec]),
        ):
            warnings_list = check_partial_linker_requirements(ctx)

        dependency_warnings = [
            w for w in warnings_list if w.linker == "dependency"
        ]
        assert len(dependency_warnings) == 1


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


class TestRustAnalyzerDisclosure:
    """The Rust backend is opt-in, and the reason must be stated.

    `rust-analyzer scip <workspace>` EXECUTES that workspace's build.rs and
    expands its proc macros, as the invoking user. Verified with a canary
    crate on three fresh, never-built projects: the bare invocation fires it,
    and so does `--config-path` with `cargo.buildScripts.enable=false` under
    both key spellings — the config is accepted, reports no errors, and the
    build script runs anyway. An earlier "safe mode works" reading was Cargo
    CACHING the first run's build-script output; on a clean crate it does not
    hold. So there is no known way to index a Cargo project without running
    its code.

    That makes the opt-in gate load-bearing for SAFETY, not only for the
    ~10x indexing cost its docstring cites. A user is entitled to know both
    that the better backend exists and what enabling it means, so the
    advisory states the trust implication rather than only the capability.
    """

    def test_advertises_the_backend_when_rust_files_are_present(self) -> None:
        profile = RepoProfile(languages={"rust": LanguageStats(files=12, loc=900)})

        warnings_list = check_rust_analyzer_disclosure(profile, available=True)

        assert len(warnings_list) == 1
        msg = warnings_list[0].message
        assert "rust-analyzer" in msg
        # The capability, so the user knows why they'd want it.
        assert "precise" in msg.lower() or "precision" in msg.lower()

    def test_the_message_states_the_trust_implication_not_just_capability(
        self,
    ) -> None:
        """The point of the disclosure. A message that only advertises the
        feature would have the user enable it on a repo they have not read."""
        profile = RepoProfile(languages={"rust": LanguageStats(files=3, loc=40)})

        msg = check_rust_analyzer_disclosure(profile, available=True)[0].message

        assert "build script" in msg.lower()
        assert "trust" in msg.lower()

    def test_mentions_installing_it_when_absent(self) -> None:
        """Absent is still worth advertising — with the same caveat attached,
        so the trust implication is known BEFORE the user installs it."""
        profile = RepoProfile(languages={"rust": LanguageStats(files=7, loc=300)})

        msg = check_rust_analyzer_disclosure(profile, available=False)[0].message

        assert "install" in msg.lower()
        assert "build script" in msg.lower()

    def test_silent_when_the_repo_has_no_rust(self) -> None:
        """No advisory noise for repos the backend cannot help."""
        profile = RepoProfile(languages={"python": LanguageStats(files=9, loc=400)})

        assert check_rust_analyzer_disclosure(profile, available=True) == []


def test_rust_disclosure_is_reachable_from_the_aggregator(
    tmp_path: Path, monkeypatch,
) -> None:
    """WIRING: the disclosure must fire through the entry point users hit.

    Without this the function is a WI-ratuv family member — correct, tested,
    and called by nobody, which is how a docstring's claim about behaviour
    drifts away from behaviour. `check_partial_install_warnings` is the entry
    point the CLI calls, so that is where it has to appear.
    """
    from hypergumbo_core import partial_install_warnings as piw
    from hypergumbo_core.linkers.registry import LinkerContext

    monkeypatch.setattr(
        piw, "is_rust_analyzer_available", lambda **_kw: True,
    )
    profile = RepoProfile(languages={"rust": LanguageStats(files=4, loc=120)})

    # Production's own LinkerContext, not a hand-rolled stub. The first
    # version of this test used a fake carrying only the attributes the
    # aggregator read WHEN NO LINKERS WERE REGISTERED; under the full suite
    # every package loads, the JNI linker registers, and it reads `symbols`,
    # which the fake lacked. A stub whose adequacy depends on global registry
    # state is a test-isolation trap.
    # A REAL empty repo root, not None. The neighbouring tests pass None and
    # survive only because they patch `check_linker_requirements`; this test
    # deliberately exercises the real path, where the gRPC linker's
    # requirement check globs the filesystem and None raises.
    ctx = LinkerContext(repo_root=tmp_path, symbols=[], edges=[])
    found = check_partial_install_warnings(
        profile, ctx, emit_warnings=False,
    )

    rust = [w for w in found if w.category == "rust_analyzer_optin"]
    assert len(rust) == 1
    assert "build script" in rust[0].message.lower()
