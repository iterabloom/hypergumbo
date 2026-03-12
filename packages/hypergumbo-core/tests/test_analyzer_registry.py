# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the analyze.registry module.

Tests the decorator-based analyzer registration system, mirroring the
proven pattern from test_linker_registry.py. Covers registration,
discovery, metadata, priority ordering, and test isolation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hypergumbo_core.analyze.all_analyzers import (
    clear_analyzer_cache,
    collect_analyzer_result,
)
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze import registry as _registry_mod
from hypergumbo_core.analyze.registry import (
    RegisteredAnalyzer,
    clear_registry,
    ensure_discovered,
    get_all_analyzers,
    get_analyzer,
    list_registered,
    register_analyzer,
    run_all_analyzers,
    run_analyzer,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate registry state for each test.

    Saves the current registry and discovery flag, clears the registry for
    the test, then restores the original state afterward. This prevents
    cross-test pollution in pytest-xdist where other tests on the same worker
    depend on the populated registry (module re-imports are no-ops, so
    decorators don't re-fire after a clear).
    """
    saved_registry = dict(_registry_mod._ANALYZER_REGISTRY)
    saved_discovered = _registry_mod._discovered
    _registry_mod._ANALYZER_REGISTRY.clear()
    _registry_mod._discovered = False
    yield
    _registry_mod._ANALYZER_REGISTRY.clear()
    _registry_mod._ANALYZER_REGISTRY.update(saved_registry)
    _registry_mod._discovered = saved_discovered


# ---------------------------------------------------------------------------
# RegisteredAnalyzer dataclass
# ---------------------------------------------------------------------------


class TestRegisteredAnalyzer:
    """Tests for RegisteredAnalyzer dataclass."""

    def test_defaults(self) -> None:
        """Default values are set correctly."""
        ra = RegisteredAnalyzer(name="test", func=lambda root: AnalysisResult())
        assert ra.name == "test"
        assert ra.priority == 50
        assert ra.requires_symbols is None
        assert ra.supports_max_files is False
        assert ra.capture_symbols_as is None

    def test_get_func_without_module_path(self) -> None:
        """get_func() returns stored func when module_path/func_name are not set."""

        def func(root: Path) -> AnalysisResult:
            return AnalysisResult()

        ra = RegisteredAnalyzer(name="test", func=func)
        assert ra.get_func() is func

    def test_custom_values(self) -> None:
        """Custom values can be set."""

        def func(root: Path) -> AnalysisResult:
            return AnalysisResult()
        ra = RegisteredAnalyzer(
            name="java",
            func=func,
            priority=10,
            requires_symbols=["python"],
            supports_max_files=True,
            capture_symbols_as="java",
        )
        assert ra.name == "java"
        assert ra.func is func
        assert ra.priority == 10
        assert ra.requires_symbols == ["python"]
        assert ra.supports_max_files is True
        assert ra.capture_symbols_as == "java"


# ---------------------------------------------------------------------------
# register_analyzer decorator
# ---------------------------------------------------------------------------


class TestRegisterAnalyzer:
    """Tests for the @register_analyzer decorator."""

    def test_basic_registration(self) -> None:
        """Decorator registers an analyzer by name."""

        @register_analyzer("rust")
        def analyze_rust(repo_root: Path) -> AnalysisResult:
            return AnalysisResult()

        registered = get_analyzer("rust")
        assert registered is not None
        assert registered.name == "rust"
        assert registered.func is analyze_rust

    def test_with_priority(self) -> None:
        """Decorator accepts priority parameter."""

        @register_analyzer("python", priority=10)
        def analyze_python(repo_root: Path) -> AnalysisResult:
            return AnalysisResult()

        registered = get_analyzer("python")
        assert registered is not None
        assert registered.priority == 10

    def test_with_supports_max_files(self) -> None:
        """Decorator accepts supports_max_files parameter."""

        @register_analyzer("html", supports_max_files=True)
        def analyze_html(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
            return AnalysisResult()

        registered = get_analyzer("html")
        assert registered is not None
        assert registered.supports_max_files is True

    def test_with_capture_symbols_as(self) -> None:
        """Decorator accepts capture_symbols_as parameter."""

        @register_analyzer("java", capture_symbols_as="java")
        def analyze_java(repo_root: Path) -> AnalysisResult:
            return AnalysisResult()

        registered = get_analyzer("java")
        assert registered is not None
        assert registered.capture_symbols_as == "java"

    def test_with_requires_symbols(self) -> None:
        """Decorator accepts requires_symbols parameter."""

        @register_analyzer("jni_bridge", requires_symbols=["java", "c"])
        def analyze_jni(repo_root: Path) -> AnalysisResult:
            return AnalysisResult()

        registered = get_analyzer("jni_bridge")
        assert registered is not None
        assert registered.requires_symbols == ["java", "c"]

    def test_returns_original_function(self) -> None:
        """Decorator returns the function unchanged."""

        @register_analyzer("go")
        def analyze_go(repo_root: Path) -> AnalysisResult:
            return AnalysisResult()

        # The decorated function is the same object
        assert callable(analyze_go)
        result = analyze_go(Path("/test"))
        assert isinstance(result, AnalysisResult)

    def test_overwrite_registration(self) -> None:
        """Registering the same name twice overwrites the first."""

        @register_analyzer("python")
        def analyze_v1(repo_root: Path) -> AnalysisResult:
            return AnalysisResult()

        @register_analyzer("python")
        def analyze_v2(repo_root: Path) -> AnalysisResult:
            return AnalysisResult()

        registered = get_analyzer("python")
        assert registered is not None
        assert registered.func is analyze_v2

    def test_all_metadata_fields(self) -> None:
        """Decorator stores all metadata fields correctly."""

        @register_analyzer(
            "javascript",
            priority=20,
            supports_max_files=True,
            capture_symbols_as="js",
            requires_symbols=["html"],
        )
        def analyze_js(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
            return AnalysisResult()

        registered = get_analyzer("javascript")
        assert registered is not None
        assert registered.name == "javascript"
        assert registered.priority == 20
        assert registered.supports_max_files is True
        assert registered.capture_symbols_as == "js"
        assert registered.requires_symbols == ["html"]


# ---------------------------------------------------------------------------
# get_analyzer
# ---------------------------------------------------------------------------


class TestGetAnalyzer:
    """Tests for get_analyzer()."""

    def test_returns_registered(self) -> None:
        """Returns a RegisteredAnalyzer when found."""

        @register_analyzer("go")
        def analyze_go(repo_root: Path) -> AnalysisResult:
            return AnalysisResult()

        result = get_analyzer("go")
        assert result is not None
        assert isinstance(result, RegisteredAnalyzer)

    def test_returns_none_for_unknown(self) -> None:
        """Returns None for unregistered name."""
        result = get_analyzer("nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# get_all_analyzers
# ---------------------------------------------------------------------------


class TestGetAllAnalyzers:
    """Tests for get_all_analyzers()."""

    def test_empty_registry(self) -> None:
        """Returns empty iterator when no analyzers registered."""
        result = list(get_all_analyzers())
        assert result == []

    def test_returns_all(self) -> None:
        """Returns all registered analyzers."""

        @register_analyzer("a")
        def analyze_a(root: Path) -> AnalysisResult:
            return AnalysisResult()

        @register_analyzer("b")
        def analyze_b(root: Path) -> AnalysisResult:
            return AnalysisResult()

        result = list(get_all_analyzers())
        assert len(result) == 2
        names = {r.name for r in result}
        assert names == {"a", "b"}

    def test_sorted_by_priority(self) -> None:
        """Returns analyzers sorted by priority (ascending)."""

        @register_analyzer("high", priority=90)
        def analyze_high(root: Path) -> AnalysisResult:
            return AnalysisResult()

        @register_analyzer("low", priority=10)
        def analyze_low(root: Path) -> AnalysisResult:
            return AnalysisResult()

        @register_analyzer("mid", priority=50)
        def analyze_mid(root: Path) -> AnalysisResult:
            return AnalysisResult()

        result = list(get_all_analyzers())
        priorities = [r.priority for r in result]
        assert priorities == [10, 50, 90]
        names = [r.name for r in result]
        assert names == ["low", "mid", "high"]


# ---------------------------------------------------------------------------
# run_analyzer
# ---------------------------------------------------------------------------


class TestRunAnalyzer:
    """Tests for run_analyzer()."""

    def test_runs_named_analyzer(self) -> None:
        """Runs the analyzer function for the given name."""

        @register_analyzer("test_lang")
        def analyze_test(repo_root: Path) -> AnalysisResult:
            return AnalysisResult(symbols=[MagicMock(name="sym1")])

        result = run_analyzer("test_lang", Path("/test"))
        assert len(result.symbols) == 1

    def test_passes_kwargs(self) -> None:
        """Passes extra kwargs to the analyzer function."""
        received_kwargs: dict = {}

        @register_analyzer("test_lang")
        def analyze_test(repo_root: Path, **kwargs) -> AnalysisResult:
            received_kwargs.update(kwargs)
            return AnalysisResult()

        run_analyzer("test_lang", Path("/test"), max_files=10)
        assert received_kwargs == {"max_files": 10}

    def test_raises_for_unknown(self) -> None:
        """Raises KeyError for unregistered analyzer name."""
        with pytest.raises(KeyError, match=r"Unknown analyzer: 'missing'\. Available:"):
            run_analyzer("missing", Path("/test"))


# ---------------------------------------------------------------------------
# run_all_analyzers
# ---------------------------------------------------------------------------


class TestRunAllAnalyzers:
    """Tests for run_all_analyzers()."""

    def test_runs_all_in_priority_order(self) -> None:
        """Runs analyzers in priority order, returns (name, result) tuples."""
        call_order: list[str] = []

        @register_analyzer("second", priority=50)
        def analyze_second(root: Path) -> AnalysisResult:
            call_order.append("second")
            return AnalysisResult()

        @register_analyzer("first", priority=10)
        def analyze_first(root: Path) -> AnalysisResult:
            call_order.append("first")
            return AnalysisResult()

        results = run_all_analyzers(Path("/test"))
        assert call_order == ["first", "second"]
        assert len(results) == 2
        assert results[0][0] == "first"
        assert results[1][0] == "second"

    def test_empty_registry(self) -> None:
        """Returns empty list when no analyzers registered."""
        results = run_all_analyzers(Path("/test"))
        assert results == []

    def test_passes_kwargs(self) -> None:
        """Passes kwargs to each analyzer."""
        received: list[dict] = []

        @register_analyzer("test")
        def analyze_test(root: Path, **kwargs) -> AnalysisResult:
            received.append(kwargs)
            return AnalysisResult()

        run_all_analyzers(Path("/test"), extra="value")
        assert received == [{"extra": "value"}]


# ---------------------------------------------------------------------------
# clear_registry
# ---------------------------------------------------------------------------


class TestClearRegistry:
    """Tests for clear_registry()."""

    def test_clears_all(self) -> None:
        """Clears all registered analyzers."""

        @register_analyzer("a")
        def analyze_a(root: Path) -> AnalysisResult:
            return AnalysisResult()

        assert list(get_all_analyzers()) != []
        clear_registry()
        assert list(get_all_analyzers()) == []

    def test_resets_discovered_flag(self) -> None:
        """Clears the discovered flag so ensure_discovered() will re-run."""
        # After ensure_discovered(), the flag is set
        with patch(
            "hypergumbo_core.analyze.registry._load_entry_point_modules"
        ) as mock_load:
            mock_load.return_value = None
            ensure_discovered()
            # Second call should be no-op (already discovered)
            ensure_discovered()
            assert mock_load.call_count == 1

        # After clear, ensure_discovered() should run again
        clear_registry()
        with patch(
            "hypergumbo_core.analyze.registry._load_entry_point_modules"
        ) as mock_load:
            mock_load.return_value = None
            ensure_discovered()
            assert mock_load.call_count == 1


# ---------------------------------------------------------------------------
# list_registered
# ---------------------------------------------------------------------------


class TestListRegistered:
    """Tests for list_registered()."""

    def test_empty(self) -> None:
        """Returns empty list when no analyzers registered."""
        assert list_registered() == []

    def test_returns_names(self) -> None:
        """Returns list of registered analyzer names."""

        @register_analyzer("go")
        def analyze_go(root: Path) -> AnalysisResult:
            return AnalysisResult()

        @register_analyzer("rust")
        def analyze_rust(root: Path) -> AnalysisResult:
            return AnalysisResult()

        names = list_registered()
        assert set(names) == {"go", "rust"}


# ---------------------------------------------------------------------------
# ensure_discovered
# ---------------------------------------------------------------------------


class TestEnsureDiscovered:
    """Tests for ensure_discovered() entry-point loading."""

    def test_loads_entry_points_once(self) -> None:
        """Only loads entry-points on first call."""
        with patch(
            "hypergumbo_core.analyze.registry._load_entry_point_modules"
        ) as mock_load:
            mock_load.return_value = None
            ensure_discovered()
            ensure_discovered()
            ensure_discovered()
            assert mock_load.call_count == 1

    def test_imports_modules_from_entry_point(self) -> None:
        """Entry-point loading imports modules which trigger decorators."""
        # Simulate entry-point that returns a list of module paths
        mock_ep = MagicMock()
        mock_ep.load.return_value = ["fake_module_a", "fake_module_b"]

        with patch(
            "importlib.metadata.entry_points", return_value=[mock_ep]
        ), patch("importlib.import_module") as mock_import:
            ensure_discovered()

        # Should have imported both modules
        assert mock_import.call_count == 2
        mock_import.assert_any_call("fake_module_a")
        mock_import.assert_any_call("fake_module_b")

    def test_handles_import_error_gracefully(self) -> None:
        """Import errors for individual modules are caught and logged."""
        mock_ep = MagicMock()
        mock_ep.load.return_value = ["good_module", "bad_module"]

        def side_effect(name):
            if name == "bad_module":
                raise ImportError("No module named 'bad_module'")

        with patch(
            "importlib.metadata.entry_points", return_value=[mock_ep]
        ), patch("importlib.import_module", side_effect=side_effect):
            # Should not raise
            ensure_discovered()

    def test_handles_entry_point_load_error(self) -> None:
        """Errors loading entry-point itself are caught gracefully."""
        mock_ep = MagicMock()
        mock_ep.load.side_effect = Exception("broken entry point")

        with patch(
            "importlib.metadata.entry_points", return_value=[mock_ep]
        ):
            # Should not raise
            ensure_discovered()

    def test_handles_entry_points_unavailable(self) -> None:
        """Works gracefully when entry_points() raises."""
        with patch(
            "importlib.metadata.entry_points",
            side_effect=Exception("no metadata"),
        ):
            # Should not raise
            ensure_discovered()

    def test_non_list_entry_point_skipped(self) -> None:
        """Entry-points that don't return a list are skipped."""
        mock_ep = MagicMock()
        mock_ep.load.return_value = "not_a_list"

        with patch(
            "importlib.metadata.entry_points", return_value=[mock_ep]
        ), patch("importlib.import_module") as mock_import:
            ensure_discovered()

        # Should not have tried to import anything
        mock_import.assert_not_called()

    def test_empty_list_entry_point_skipped(self) -> None:
        """Entry-points that return an empty list are skipped."""
        mock_ep = MagicMock()
        mock_ep.load.return_value = []

        with patch(
            "importlib.metadata.entry_points", return_value=[mock_ep]
        ), patch("importlib.import_module") as mock_import:
            ensure_discovered()

        mock_import.assert_not_called()

    def test_unknown_format_skipped(self) -> None:
        """Entry-points with unrecognized element types are skipped."""
        mock_ep = MagicMock()
        mock_ep.load.return_value = [42, 43]  # Neither str nor AnalyzerSpec

        with patch(
            "importlib.metadata.entry_points", return_value=[mock_ep]
        ), patch("importlib.import_module") as mock_import:
            ensure_discovered()

        mock_import.assert_not_called()


# ---------------------------------------------------------------------------
# Transition: AnalyzerSpec-based entry-points
# ---------------------------------------------------------------------------


class TestAnalyzerSpecTransition:
    """Tests for the transition path from AnalyzerSpec to decorator registration."""

    def test_registers_from_analyzer_specs(self) -> None:
        """AnalyzerSpec entry-points are converted to RegisteredAnalyzer."""
        from types import SimpleNamespace

        # Simulate AnalyzerSpec NamedTuple (has module_path attribute)
        mock_spec = SimpleNamespace(
            name="test_lang",
            module_path="fake_module",
            func_name="analyze_test",
            supports_max_files=True,
            capture_symbols_as="test",
        )

        # Create a mock module with the function
        mock_module = MagicMock()
        mock_func = MagicMock(return_value=AnalysisResult())
        mock_module.analyze_test = mock_func

        mock_ep = MagicMock()
        mock_ep.load.return_value = [mock_spec]

        with patch(
            "importlib.metadata.entry_points", return_value=[mock_ep]
        ), patch("importlib.import_module", return_value=mock_module):
            ensure_discovered()

        registered = get_analyzer("test_lang")
        assert registered is not None
        assert registered.name == "test_lang"
        assert registered.func is mock_func
        assert registered.supports_max_files is True
        assert registered.capture_symbols_as == "test"

    def test_spec_does_not_overwrite_decorator(self) -> None:
        """Decorator-registered analyzers take priority over spec-based ones."""

        @register_analyzer("already_registered")
        def analyze_existing(root: Path) -> AnalysisResult:
            return AnalysisResult()

        from types import SimpleNamespace

        mock_spec = SimpleNamespace(
            name="already_registered",
            module_path="fake_module",
            func_name="analyze_other",
        )
        mock_ep = MagicMock()
        mock_ep.load.return_value = [mock_spec]

        with patch(
            "importlib.metadata.entry_points", return_value=[mock_ep]
        ):
            ensure_discovered()

        # Original decorator registration should be preserved
        registered = get_analyzer("already_registered")
        assert registered is not None
        assert registered.func is analyze_existing

    def test_spec_import_failure_skipped(self) -> None:
        """Failed AnalyzerSpec module imports are skipped gracefully."""
        from types import SimpleNamespace

        mock_spec = SimpleNamespace(
            name="bad_lang",
            module_path="nonexistent_module",
            func_name="analyze_bad",
            supports_max_files=False,
            capture_symbols_as=None,
        )
        mock_ep = MagicMock()
        mock_ep.load.return_value = [mock_spec]

        with patch(
            "importlib.metadata.entry_points", return_value=[mock_ep]
        ), patch(
            "importlib.import_module", side_effect=ImportError("no such module")
        ):
            ensure_discovered()

        assert get_analyzer("bad_lang") is None


# ---------------------------------------------------------------------------
# Facade: all_analyzers.py functions
# ---------------------------------------------------------------------------


class TestClearAnalyzerCache:
    """Tests for the clear_analyzer_cache() facade."""

    def test_clears_registry(self) -> None:
        """clear_analyzer_cache() delegates to clear_registry()."""

        @register_analyzer("test_clear")
        def analyze_test(root: Path) -> AnalysisResult:
            return AnalysisResult()

        assert get_analyzer("test_clear") is not None
        clear_analyzer_cache()
        assert get_analyzer("test_clear") is None


class TestCollectAnalyzerResult:
    """Tests for collect_analyzer_result()."""

    def test_collects_successful_result(self) -> None:
        """Successful results are collected into all lists."""
        from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, UsageContext
        from hypergumbo_core.limits import Limits

        sym = Symbol(
            id="test:f.py:1-2:foo:function",
            name="foo",
            kind="function",
            language="test",
            path="f.py",
            span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
            origin="test",
            origin_run_id="run1",
        )
        edge = Edge(
            id="e1",
            edge_type="calls",
            src="a",
            dst="b",
            line=1,
            origin="test",
            origin_run_id="run1",
        )
        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "test-v1"}
        run.pass_id = "test-v1"

        result = AnalysisResult(
            symbols=[sym],
            edges=[edge],
            usage_contexts=[],
            run=run,
            skipped=False,
        )

        analysis_runs: list[dict] = []
        all_symbols: list[Symbol] = []
        all_edges: list[Edge] = []
        all_usage_contexts: list[UsageContext] = []
        limits = Limits()

        collect_analyzer_result(
            result, analysis_runs, all_symbols, all_edges, all_usage_contexts, limits
        )

        assert len(analysis_runs) == 1
        assert len(all_symbols) == 1
        assert len(all_edges) == 1

    def test_collects_skipped_result(self) -> None:
        """Skipped results are recorded in limits.skipped_passes."""
        from hypergumbo_core.ir import AnalysisRun, Symbol, UsageContext
        from hypergumbo_core.limits import Limits

        run = MagicMock(spec=AnalysisRun)
        run.pass_id = "lean-ts-v1"

        result = AnalysisResult(
            run=run,
            skipped=True,
            skip_reason="tree-sitter-lean grammar not available",
        )

        analysis_runs: list[dict] = []
        all_symbols: list[Symbol] = []
        all_edges: list = []
        all_usage_contexts: list[UsageContext] = []
        limits = Limits()

        collect_analyzer_result(
            result, analysis_runs, all_symbols, all_edges, all_usage_contexts, limits
        )

        assert len(analysis_runs) == 0
        assert len(all_symbols) == 0
        assert len(limits.skipped_passes) == 1
        assert limits.skipped_passes[0]["pass"] == "lean-ts-v1"
        assert "grammar not available" in limits.skipped_passes[0]["reason"]


# ---------------------------------------------------------------------------
# Path normalization in run_all_analyzers (facade)
# ---------------------------------------------------------------------------


class TestRunAllAnalyzersPathNormalization:
    """Tests that run_all_analyzers normalizes absolute paths to relative."""

    def test_absolute_symbol_paths_are_relativized(self) -> None:
        """Symbol paths that are absolute and under repo_root become relative."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.ir import AnalysisRun, Span, Symbol

        repo_root = Path("/home/user/myrepo")

        sym_absolute = Symbol(
            id="java:/home/user/myrepo/src/Main.java:1-10:Main:class",
            name="Main",
            kind="class",
            language="java",
            path="/home/user/myrepo/src/Main.java",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="java-v1",
            origin_run_id="run1",
        )
        sym_relative = Symbol(
            id="rust:src/lib.rs:1-5:foo:function",
            name="foo",
            kind="function",
            language="rust",
            path="src/lib.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="rust-v1",
            origin_run_id="run1",
        )

        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "test-v1"}
        run.pass_id = "test-v1"

        result = AnalysisResult(
            symbols=[sym_absolute, sym_relative],
            edges=[],
            usage_contexts=[],
            run=run,
            skipped=False,
        )

        @register_analyzer("test_abs_path")
        def analyze_test(root: Path) -> AnalysisResult:
            return result

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, symbols, _, _, _, _ = facade_run_all(repo_root)

        # Absolute path should be relativized
        assert symbols[0].path == "src/Main.java"
        # Already-relative path should be unchanged
        assert symbols[1].path == "src/lib.rs"

    def test_absolute_usage_context_paths_are_relativized(self) -> None:
        """UsageContext paths that are absolute and under repo_root become relative."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.ir import AnalysisRun, Span, UsageContext

        repo_root = Path("/home/user/myrepo")

        uc = UsageContext(
            id="uc1",
            kind="call",
            context_name="register",
            symbol_ref=None,
            position="args[0]",
            metadata={},
            path="/home/user/myrepo/routes/web.py",
            span=Span(start_line=5, end_line=5, start_col=0, end_col=40),
        )

        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "test-v1"}
        run.pass_id = "test-v1"

        result = AnalysisResult(
            symbols=[],
            edges=[],
            usage_contexts=[uc],
            run=run,
            skipped=False,
        )

        @register_analyzer("test_uc_path")
        def analyze_test(root: Path) -> AnalysisResult:
            return result

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, _, _, usage_contexts, _, _ = facade_run_all(repo_root)

        assert usage_contexts[0].path == "routes/web.py"

    def test_path_outside_repo_root_unchanged(self) -> None:
        """Absolute paths NOT under repo_root are left unchanged."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.ir import AnalysisRun, Span, Symbol

        repo_root = Path("/home/user/myrepo")

        sym = Symbol(
            id="c:/usr/include/stdlib.h:1-5:malloc:function",
            name="malloc",
            kind="function",
            language="c",
            path="/usr/include/stdlib.h",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="c-v1",
            origin_run_id="run1",
        )

        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "test-v1"}
        run.pass_id = "test-v1"

        result = AnalysisResult(
            symbols=[sym],
            edges=[],
            usage_contexts=[],
            run=run,
            skipped=False,
        )

        @register_analyzer("test_outside_path")
        def analyze_test(root: Path) -> AnalysisResult:
            return result

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, symbols, _, _, _, _ = facade_run_all(repo_root)

        # Path outside repo_root should be left as-is
        assert symbols[0].path == "/usr/include/stdlib.h"


class TestRunAllAnalyzersTruncatedFiles:
    """Tests that run_all_analyzers wires file-skip tracking to limits."""

    def test_truncated_files_populated_via_global_callback(
        self, tmp_path: Path,
    ) -> None:
        """Oversized files discovered by find_files() are recorded in limits."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.discovery import find_files, set_max_file_bytes
        from hypergumbo_core.ir import AnalysisRun

        # Create a large file that exceeds the limit
        large = tmp_path / "big.py"
        large.write_text("x" * 5000)
        small = tmp_path / "ok.py"
        small.write_text("x = 1\n")

        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "test-trunc-v1"}
        run.pass_id = "test-trunc-v1"

        # Analyzer that calls find_files — the global callback should fire
        @register_analyzer("test_trunc")
        def analyze_trunc(root: Path) -> AnalysisResult:
            list(find_files(root, ["*.py"]))
            return AnalysisResult(run=run, skipped=False)

        try:
            set_max_file_bytes(100)
            with patch(
                "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
            ):
                _, _, _, _, limits, _ = facade_run_all(tmp_path)

            assert len(limits.truncated_files) == 1
            assert "big.py" in limits.truncated_files[0]["path"]
            assert limits.truncated_files[0]["size_bytes"] > 100
            assert "exceeds" in limits.truncated_files[0]["reason"]
        finally:
            set_max_file_bytes(None)

    def test_global_callback_cleared_after_run(self) -> None:
        """Global on_file_skipped callback is cleared after run_all_analyzers."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.discovery import _global_on_file_skipped

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            facade_run_all(Path("/test"))

        # Import the module-level variable directly
        import hypergumbo_core.discovery as disc_mod
        assert disc_mod._global_on_file_skipped is None
