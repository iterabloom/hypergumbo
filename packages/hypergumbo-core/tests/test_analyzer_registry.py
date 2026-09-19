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
    stamp_analyzer_config_fingerprint,
)
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, _default_config_fingerprint
from hypergumbo_core.limits import Limits
from hypergumbo_core.analyze import registry as _registry_mod
from hypergumbo_core.analyze.registry import (
    AUDIT_CITATION_PREFIX,
    LANGUAGE_STATE_NO_LANGUAGE,
    LANGUAGE_STATE_NO_SPEC,
    LANGUAGE_STATE_TAXONOMY,
    SPAN_ROLE_ITEM,
    SPAN_ROLE_TOKEN,
    LanguageDeclarationError,
    MergeAnchor,
    MergeDeclarationError,
    MergeDisjoint,
    RegisteredAnalyzer,
    UndeclaredProducerError,
    analyzers_for_language,
    as_emitted,
    backends_executing_analysed_code,
    clear_registry,
    ensure_discovered,
    get_all_analyzers,
    get_analyzer,
    last_segment,
    list_registered,
    merge_participants,
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
        assert ra.supports_max_files is False
        assert ra.capture_symbols_as is None
        # WI-hupaz: depends_on defaults to an empty list (per-instance, not shared).
        assert ra.depends_on == []

    def test_no_requires_symbols_field(self) -> None:
        """WI-burin: the dead requires_symbols field has been removed.

        It was a never-passed, never-consumed multi-pass-symbol-consumption
        stub superseded by ``depends_on`` (INV-hujog), which carries CNF
        pass-id dependencies that are actually validated.
        """
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(RegisteredAnalyzer)}
        assert "requires_symbols" not in field_names

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
            supports_max_files=True,
            capture_symbols_as="java",
        )
        assert ra.name == "java"
        assert ra.func is func
        assert ra.priority == 10
        assert ra.supports_max_files is True
        assert ra.capture_symbols_as == "java"


# ---------------------------------------------------------------------------
# stamp_analyzer_config_fingerprint — orchestrator chokepoint (INV-lidul/WI-mipul)
# ---------------------------------------------------------------------------


class TestStampAnalyzerConfigFingerprint:
    """The orchestrator-loop chokepoint that stamps a producer-identity
    config_fingerprint for the override-analyze / function-registered cohort
    that bypasses TreeSitterAnalyzer._analyze_body."""

    def _analyzer(self, name="zz"):
        return RegisteredAnalyzer(
            name=name, func=lambda root: AnalysisResult(),
            backend="tree-sitter", languages=[name],
        )

    def test_stamps_default_run(self):
        """A run carrying the default sentinel is stamped with a real
        producer-identity fingerprint."""
        run = AnalysisRun.create(pass_id="zz", version="1.0.0")
        assert run.config_fingerprint == _default_config_fingerprint()
        stamp_analyzer_config_fingerprint(AnalysisResult(run=run), self._analyzer())
        assert run.config_fingerprint != _default_config_fingerprint()
        assert run.config_fingerprint.startswith("sha256:")

    def test_skips_none_run(self):
        """No-op when the result has no run."""
        stamp_analyzer_config_fingerprint(AnalysisResult(run=None), self._analyzer())

    def test_preserves_self_stamped_run(self):
        """A run already carrying a non-default (self-stamped) fingerprint is
        left untouched — the ~30 _analyze_body analyzers keep their richer
        class+grammar+globs digest."""
        run = AnalysisRun.create(
            pass_id="zz", version="1.0.0", config_fingerprint="sha256:cafecafecafecafe"
        )
        stamp_analyzer_config_fingerprint(AnalysisResult(run=run), self._analyzer())
        assert run.config_fingerprint == "sha256:cafecafecafecafe"

    def test_distinct_analyzers_distinct_fingerprints(self):
        """Two differently-identified analyzers produce distinct fingerprints."""
        run_a = AnalysisRun.create(pass_id="a", version="1.0.0")
        run_b = AnalysisRun.create(pass_id="b", version="1.0.0")
        stamp_analyzer_config_fingerprint(AnalysisResult(run=run_a), self._analyzer("alpha"))
        stamp_analyzer_config_fingerprint(AnalysisResult(run=run_b), self._analyzer("beta"))
        assert run_a.config_fingerprint != run_b.config_fingerprint


class TestCollectAnalyzerResultStampsEmissionCounts:
    """INV-gizik / INV-pitab: collect_analyzer_result stamps nodes_emitted /
    edges_emitted on the run (before to_dict snapshots it) at the universal
    analyzer chokepoint."""

    def test_counts_stamped_into_appended_run(self):
        run = AnalysisRun.create(pass_id="python", version="1.0.0")
        sym = Symbol(
            id="python:m.py:1-1:f:function", name="f", kind="function",
            language="python", path="m.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=1),
        )
        edge = Edge(
            id="python:m.py:1-1:f:function->g", src="python:m.py:1-1:f:function",
            dst="g", edge_type="calls", line=1, origin=["python"],
            origin_run_id=run.execution_id,
        )
        result = AnalysisResult(symbols=[sym], edges=[edge], run=run)
        runs: list = []
        collect_analyzer_result(result, runs, [], [], [], Limits())
        assert len(runs) == 1
        assert runs[0]["nodes_emitted"] == 1
        assert runs[0]["edges_emitted"] == 1

    def test_skipped_result_records_no_run(self):
        """A skipped analyzer records a skipped_passes entry, not analysis_runs —
        no counts are stamped onto a run nobody serializes."""
        run = AnalysisRun.create(pass_id="rust", version="1.0.0")
        result = AnalysisResult(symbols=[], edges=[], run=run, skipped=True,
                                skip_reason="grammar unavailable")
        runs: list = []
        limits = Limits()
        collect_analyzer_result(result, runs, [], [], [], limits)
        assert runs == []
        assert any(s["pass"] == "rust" for s in limits.skipped_passes)


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
        )
        def analyze_js(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
            return AnalysisResult()

        registered = get_analyzer("javascript")
        assert registered is not None
        assert registered.name == "javascript"
        assert registered.priority == 20
        assert registered.supports_max_files is True
        assert registered.capture_symbols_as == "js"

    def test_depends_on_defaults_to_empty_list(self) -> None:
        """WI-dilab: depends_on defaults to [] (CNF: empty outer list)."""

        @register_analyzer("nodeps-analyzer", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_nodeps(repo_root: Path) -> AnalysisResult:
            return AnalysisResult()

        registered = get_analyzer("nodeps-analyzer")
        assert registered is not None
        assert registered.depends_on == []

    def test_with_depends_on_cnf(self) -> None:
        """WI-dilab: depends_on kwarg flows into RegisteredAnalyzer as CNF.

        Carries pass-id dependencies in Conjunctive Normal Form (outer-AND
        of inner-OR) surfaced via the catalog's ``Pass.depends_on`` field
        (INV-hujog).
        """

        @register_analyzer(
            "jni-bridge-test-analyzer",
            language_state=LANGUAGE_STATE_NO_LANGUAGE,
            depends_on=[["java"], ["c", "cpp"]],
        )
        def analyze_jni_bridge(repo_root: Path) -> AnalysisResult:
            return AnalysisResult()

        registered = get_analyzer("jni-bridge-test-analyzer")
        assert registered is not None
        assert registered.depends_on == [["java"], ["c", "cpp"]]


# ---------------------------------------------------------------------------
# get_analyzer
# ---------------------------------------------------------------------------


class TestLanguageDeclarationGate:
    """WI-juzig: an analyzer's languages are the taxonomy's vocabulary, or a declaration.

    ``register_analyzer`` used to default ``languages`` to ``[name]`` and check
    nothing, so a pass named ``make`` was language ``make`` while the taxonomy,
    the profile and every file-anchored ``Symbol.language`` said ``makefile``
    (INV-nidul: a FALSE ``limits.skipped_languages`` verdict), and two names
    that are not languages at all (``rust_analyzer``, ``manifest_targets``)
    became languages the spec validator would accept. It also conflated absent
    with empty: an explicit ``languages=[]`` became ``[name]``.

    The gate: every effective language is a taxonomy key (aliases
    canonicalised) unless the registration DECLARES otherwise, and the
    declaration is checked in both directions — a ``no_taxonomy_spec``
    declaration on a language the taxonomy DOES carry is stale and raises.
    """

    def test_undeclared_non_taxonomy_name_raises_naming_analyzer_and_value(self) -> None:
        with pytest.raises(LanguageDeclarationError) as exc:

            @register_analyzer("foo_bar")
            def analyze_foo(repo_root: Path) -> AnalysisResult:  # pragma: no cover
                return AnalysisResult()

        assert "foo_bar" in str(exc.value)
        assert "taxonomy" in str(exc.value)
        assert get_analyzer("foo_bar") is None

    def test_declared_language_outside_vocabulary_raises(self) -> None:
        with pytest.raises(LanguageDeclarationError) as exc:

            @register_analyzer("bash", languages=["klingon"])
            def analyze_k(repo_root: Path) -> AnalysisResult:  # pragma: no cover
                return AnalysisResult()

        assert "klingon" in str(exc.value)
        assert "bash" in str(exc.value)

    def test_taxonomy_name_default_is_gated_and_kept(self) -> None:
        @register_analyzer("go")
        def analyze_go(repo_root: Path) -> AnalysisResult:  # pragma: no cover
            return AnalysisResult()

        reg = get_analyzer("go")
        assert reg is not None
        assert reg.languages == ["go"]
        assert reg.language_state == LANGUAGE_STATE_TAXONOMY

    def test_explicit_empty_list_is_not_absent(self) -> None:
        """ABSENT != EMPTY: ``languages=[]`` used to be silently rewritten to
        ``[name]``. It is now an error — the caller meant something and the
        registry must not guess which."""
        with pytest.raises(LanguageDeclarationError) as exc:

            @register_analyzer("go", languages=[])
            def analyze_go(repo_root: Path) -> AnalysisResult:  # pragma: no cover
                return AnalysisResult()

        assert "languages=[]" in str(exc.value)
        assert LANGUAGE_STATE_NO_LANGUAGE in str(exc.value)

    def test_no_language_state_stores_an_empty_list(self) -> None:
        @register_analyzer("synth-pass", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_synth(repo_root: Path) -> AnalysisResult:  # pragma: no cover
            return AnalysisResult()

        reg = get_analyzer("synth-pass")
        assert reg is not None
        assert reg.languages == []
        assert reg.language_state == LANGUAGE_STATE_NO_LANGUAGE

    def test_no_language_state_with_languages_raises(self) -> None:
        with pytest.raises(LanguageDeclarationError) as exc:

            @register_analyzer(
                "synth-pass", languages=["go"], language_state=LANGUAGE_STATE_NO_LANGUAGE,
            )
            def analyze_synth(repo_root: Path) -> AnalysisResult:  # pragma: no cover
                return AnalysisResult()

        assert "synth-pass" in str(exc.value)

    def test_no_spec_state_accepts_a_language_the_taxonomy_lacks(self) -> None:
        @register_analyzer("gleam", language_state=LANGUAGE_STATE_NO_SPEC)
        def analyze_gleam(repo_root: Path) -> AnalysisResult:  # pragma: no cover
            return AnalysisResult()

        reg = get_analyzer("gleam")
        assert reg is not None
        assert reg.languages == ["gleam"]
        assert reg.language_state == LANGUAGE_STATE_NO_SPEC

    def test_no_spec_state_on_a_taxonomy_language_is_stale_and_raises(self) -> None:
        """The declaration is checked in BOTH directions: once a LanguageSpec
        lands for the language, the declaration must go, or the gate says so."""
        with pytest.raises(LanguageDeclarationError) as exc:

            @register_analyzer("rust", language_state=LANGUAGE_STATE_NO_SPEC)
            def analyze_rust(repo_root: Path) -> AnalysisResult:  # pragma: no cover
                return AnalysisResult()

        assert "rust" in str(exc.value)
        assert "stale" in str(exc.value)

    def test_alias_key_is_canonicalised_to_the_taxonomy_name(self) -> None:
        """``LANGUAGE_ALIASES`` keys are accepted and stored as the taxonomy
        name, so the registry speaks one vocabulary (shell -> bash)."""
        @register_analyzer("shell")
        def analyze_shell(repo_root: Path) -> AnalysisResult:  # pragma: no cover
            return AnalysisResult()

        reg = get_analyzer("shell")
        assert reg is not None
        assert reg.languages == ["bash"]

    def test_unknown_language_state_raises(self) -> None:
        with pytest.raises(LanguageDeclarationError) as exc:

            @register_analyzer("go", language_state="maybe")
            def analyze_go(repo_root: Path) -> AnalysisResult:  # pragma: no cover
                return AnalysisResult()

        assert "maybe" in str(exc.value)

    def test_registered_analyzer_dataclass_defaults_to_taxonomy_state(self) -> None:
        ra = RegisteredAnalyzer(name="x", func=lambda root: AnalysisResult())
        assert ra.language_state == LANGUAGE_STATE_TAXONOMY


class TestAnalyzersForLanguage:
    """WI-juzig: "who produces language L?" answered in the taxonomy vocabulary,
    returning EVERY producer — a language can have two backends."""

    def test_returns_every_producer_in_priority_order(self) -> None:
        @register_analyzer("rust_scip", priority=45, languages=["rust"], backend="scip")
        def analyze_scip(repo_root: Path) -> AnalysisResult:  # pragma: no cover
            return AnalysisResult()

        @register_analyzer("rust", priority=50, backend="tree-sitter")
        def analyze_rust(repo_root: Path) -> AnalysisResult:  # pragma: no cover
            return AnalysisResult()

        @register_analyzer("go")
        def analyze_go(repo_root: Path) -> AnalysisResult:  # pragma: no cover
            return AnalysisResult()

        assert [a.name for a in analyzers_for_language("rust")] == ["rust_scip", "rust"]
        assert [a.name for a in analyzers_for_language("go")] == ["go"]

    def test_finds_a_producer_whose_pass_name_differs_from_the_language(self) -> None:
        """``get_analyzer("makefile")`` is None — the pass is named ``make``.
        Looking up by declared language finds it."""
        @register_analyzer("make", languages=["makefile"])
        def analyze_make(repo_root: Path) -> AnalysisResult:  # pragma: no cover
            return AnalysisResult()

        assert get_analyzer("makefile") is None
        assert [a.name for a in analyzers_for_language("makefile")] == ["make"]

    def test_unproduced_language_is_an_empty_list(self) -> None:
        assert analyzers_for_language("cobol") == []


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

        @register_analyzer("a", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_a(root: Path) -> AnalysisResult:
            return AnalysisResult()

        @register_analyzer("b", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_b(root: Path) -> AnalysisResult:
            return AnalysisResult()

        result = list(get_all_analyzers())
        assert len(result) == 2
        names = {r.name for r in result}
        assert names == {"a", "b"}

    def test_sorted_by_priority(self) -> None:
        """Returns analyzers sorted by priority (ascending)."""

        @register_analyzer("high", priority=90, language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_high(root: Path) -> AnalysisResult:
            return AnalysisResult()

        @register_analyzer("low", priority=10, language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_low(root: Path) -> AnalysisResult:
            return AnalysisResult()

        @register_analyzer("mid", priority=50, language_state=LANGUAGE_STATE_NO_LANGUAGE)
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

        @register_analyzer("test_lang", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_test(repo_root: Path) -> AnalysisResult:
            return AnalysisResult(symbols=[MagicMock(name="sym1")])

        result = run_analyzer("test_lang", Path("/test"))
        assert len(result.symbols) == 1

    def test_passes_kwargs(self) -> None:
        """Passes extra kwargs to the analyzer function."""
        received_kwargs: dict = {}

        @register_analyzer("test_lang", language_state=LANGUAGE_STATE_NO_LANGUAGE)
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

        @register_analyzer("second", priority=50, language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_second(root: Path) -> AnalysisResult:
            call_order.append("second")
            return AnalysisResult()

        @register_analyzer("first", priority=10, language_state=LANGUAGE_STATE_NO_LANGUAGE)
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

        @register_analyzer("test", language_state=LANGUAGE_STATE_NO_LANGUAGE)
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

        @register_analyzer("a", language_state=LANGUAGE_STATE_NO_LANGUAGE)
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

    def test_language_declaration_error_is_not_swallowed_by_discovery(self) -> None:
        """WI-juzig: ``_import_module_list`` swallows import failures so a
        missing optional grammar cannot take the registry down. A
        ``LanguageDeclarationError`` is not that — it is a defect in the
        registration itself, and swallowing it would make the analyzer
        silently vanish from the catalogue (ABSENT != EMPTY)."""
        mock_ep = MagicMock()
        mock_ep.load.return_value = ["good_module", "misdeclared_module"]

        def side_effect(name):
            if name == "misdeclared_module":
                raise LanguageDeclarationError("analyzer 'x' declares language 'x'")

        with patch(
            "importlib.metadata.entry_points", return_value=[mock_ep]
        ), patch("importlib.import_module", side_effect=side_effect):
            with pytest.raises(LanguageDeclarationError):
                ensure_discovered()

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

        @register_analyzer("already_registered", language_state=LANGUAGE_STATE_NO_LANGUAGE)
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

        @register_analyzer("test_clear", language_state=LANGUAGE_STATE_NO_LANGUAGE)
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
        run.to_dict.return_value = {"pass": "test"}
        run.pass_id = "test"

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
        run.pass_id = "lean"

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
        assert limits.skipped_passes[0]["pass"] == "lean"
        assert "grammar not available" in limits.skipped_passes[0]["reason"]

    def test_drains_failed_files_into_limits(self) -> None:
        """Per-run failed_files drain into limits.failed_files, stamped with pass_id (INV-buhur)."""
        from hypergumbo_core.ir import AnalysisRun, Symbol, UsageContext
        from hypergumbo_core.limits import Limits

        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "python"}
        run.pass_id = "python"
        run.failed_files = [
            {"path": "broken.py", "reason": "SyntaxError: line 3"},
            {"path": "bad-utf8.py", "reason": "UnicodeDecodeError"},
        ]

        result = AnalysisResult(
            symbols=[],
            edges=[],
            usage_contexts=[],
            run=run,
            skipped=False,
        )

        analysis_runs: list[dict] = []
        all_symbols: list[Symbol] = []
        all_edges: list = []
        all_usage_contexts: list[UsageContext] = []
        limits = Limits()

        collect_analyzer_result(
            result, analysis_runs, all_symbols, all_edges, all_usage_contexts, limits
        )

        assert len(limits.failed_files) == 2
        assert limits.failed_files[0].path == "broken.py"
        assert limits.failed_files[0].reason == "SyntaxError: line 3"
        assert limits.failed_files[0].analyzer == "python"
        assert limits.failed_files[1].path == "bad-utf8.py"
        assert limits.failed_files[1].analyzer == "python"

    def test_backfills_empty_origin_run_id_from_run(self) -> None:
        """WI-mosil central backstop: a direct-constructor analyzer's Symbol with
        origin_run_id='' is stamped from the run's execution_id at collection so the
        node->AnalysisRun join resolves (toml/json/wgsl/sql producers don't thread it)."""
        from hypergumbo_core.ir import AnalysisRun, Span, Symbol, UsageContext
        from hypergumbo_core.limits import Limits

        sym = Symbol(
            id="toml:pyproject.toml:0-0:requests:dependency",
            name="requests",
            kind="dependency",
            language="toml",
            path="pyproject.toml",
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
            origin="toml-v1",
            origin_run_id="",
        )
        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "toml-v1"}
        run.pass_id = "toml-v1"
        run.execution_id = "uuid:EXEC-TOML"

        result = AnalysisResult(
            symbols=[sym], edges=[], usage_contexts=[], run=run, skipped=False,
        )
        all_symbols: list[Symbol] = []
        collect_analyzer_result(result, [], all_symbols, [], [], Limits())
        assert all_symbols[0].origin_run_id == "uuid:EXEC-TOML"

    def test_does_not_overwrite_present_origin_run_id(self) -> None:
        """The backstop is pure fill: a Symbol that already carries an origin_run_id
        (a multi-pass producer that threaded its own) is left untouched."""
        from hypergumbo_core.ir import AnalysisRun, Span, Symbol
        from hypergumbo_core.limits import Limits

        sym = Symbol(
            id="python:m.py:1-2:f:function",
            name="f",
            kind="function",
            language="python",
            path="m.py",
            span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
            origin="python",
            origin_run_id="uuid:ALREADY",
        )
        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "python"}
        run.pass_id = "python"
        run.execution_id = "uuid:DIFFERENT"

        result = AnalysisResult(
            symbols=[sym], edges=[], usage_contexts=[], run=run, skipped=False,
        )
        all_symbols: list[Symbol] = []
        collect_analyzer_result(result, [], all_symbols, [], [], Limits())
        assert all_symbols[0].origin_run_id == "uuid:ALREADY"

    def test_drains_failed_files_from_partially_skipped_result(self) -> None:
        """failed_files drain even when result.skipped=True — partial-skip analyzers may have already recorded entries before bailing."""
        from hypergumbo_core.ir import AnalysisRun, Symbol, UsageContext
        from hypergumbo_core.limits import Limits

        run = MagicMock(spec=AnalysisRun)
        run.pass_id = "ipc-linker"
        run.failed_files = [{"path": "main.ts", "reason": "OSError: permission denied"}]

        result = AnalysisResult(
            run=run,
            skipped=True,
            skip_reason="bailed mid-scan",
        )

        analysis_runs: list[dict] = []
        all_symbols: list[Symbol] = []
        all_edges: list = []
        all_usage_contexts: list[UsageContext] = []
        limits = Limits()

        collect_analyzer_result(
            result, analysis_runs, all_symbols, all_edges, all_usage_contexts, limits
        )

        assert len(limits.skipped_passes) == 1
        assert len(limits.failed_files) == 1
        assert limits.failed_files[0].path == "main.ts"
        assert limits.failed_files[0].analyzer == "ipc-linker"

    def test_run_none_records_no_files_matched_skip(self) -> None:
        """WI-didil: a dispatched analyzer that produced no run (``run=None``)
        must not silently vanish — it records a ``skipped_passes`` entry.

        This is the taxonomy-bypass cohort (matlab/meson/puppet/racket/robot/
        scheme/scss): their languages are absent from the taxonomy, so the
        file-presence pre-filter dispatches them; with no matching files each
        returns a bare ``AnalysisResult()`` (``run=None``). Before this fix the
        orchestrator dropped them, producing neither an AnalysisRun nor a skip
        record — violating the "every catalog pass → AR or skip" invariant."""
        from hypergumbo_core.ir import Symbol
        from hypergumbo_core.limits import Limits

        result = AnalysisResult()  # run=None, skipped=False, no symbols
        analysis_runs: list[dict] = []
        all_symbols: list[Symbol] = []
        limits = Limits()

        collect_analyzer_result(
            result, analysis_runs, all_symbols, [], [], limits, analyzer_name="matlab"
        )

        assert analysis_runs == []
        # WI-dukoh: a BARE run=None result (nothing self-declared) is a
        # no-input analyzer by construction, so the orchestrator may classify
        # it with certainty — this is the one branch where NO_CANDIDATE_FILES
        # is the orchestrator's own claim rather than a guess on a producer's
        # behalf.
        assert limits.skipped_passes == [
            {"pass": "matlab", "reason": "no files matched",
             "silence_reason": "no_candidate_files"}
        ]

    def test_run_none_uses_declared_skip_reason(self) -> None:
        """WI-didil: when a ``run=None`` result declares ``skipped=True`` with a
        ``skip_reason``, the recorded skip uses that reason verbatim — not the
        generic "no files matched". Covers rust_analyzer (opt-in SCIP backend
        disabled: it self-declares "rust-analyzer backend not enabled") and the
        latent grammar-unavailable path (a skipped result whose ``run`` is None
        was formerly dropped by the same silent early-return)."""
        from hypergumbo_core.ir import Symbol
        from hypergumbo_core.limits import Limits

        result = AnalysisResult(
            skipped=True, skip_reason="rust-analyzer backend not enabled"
        )
        all_symbols: list[Symbol] = []
        limits = Limits()

        collect_analyzer_result(
            result, [], all_symbols, [], [], limits, analyzer_name="rust_analyzer"
        )

        # WI-dukoh: this result self-declares PROSE but no code, so the code
        # is UNREPORTED -- "the producer did not classify itself". It must NOT
        # inherit no_candidate_files: the producer said something the axis
        # cannot read, which is not the same as saying the repo had no files,
        # and this analyzer's repo may be full of .rs files.
        assert limits.skipped_passes == [
            {"pass": "rust_analyzer",
             "reason": "rust-analyzer backend not enabled",
             "silence_reason": "unreported"}
        ]

    def test_run_none_uses_declared_skip_reason_code(self) -> None:
        """WI-dukoh: a producer that declares its CODE keeps it verbatim.

        The live rust_analyzer does declare one; this pins the plumbing that
        carries a self-declared code from AnalysisResult through to
        limits.skipped_passes, which is what makes the migration real rather
        than a field nothing fills.
        """
        from hypergumbo_core.ir import Symbol
        from hypergumbo_core.limits import Limits
        from hypergumbo_core.pass_silence import BACKEND_DISABLED

        result = AnalysisResult(
            skipped=True,
            skip_reason="rust-analyzer backend not enabled",
            skip_reason_code=BACKEND_DISABLED,
        )
        all_symbols: list[Symbol] = []
        limits = Limits()

        collect_analyzer_result(
            result, [], all_symbols, [], [], limits, analyzer_name="rust_analyzer"
        )

        assert limits.skipped_passes == [
            {"pass": "rust_analyzer",
             "reason": "rust-analyzer backend not enabled",
             "silence_reason": "backend_disabled"}
        ]

    def test_run_none_without_analyzer_name_records_nothing(self) -> None:
        """Defensive: a ``run=None`` result with no ``analyzer_name`` (legacy callers)
        extends symbols/edges but records no skip — a skip record with an empty
        pass id would be meaningless. Symbols are still drained fail-open."""
        from hypergumbo_core.ir import Span, Symbol
        from hypergumbo_core.limits import Limits

        sym = Symbol(
            id="x:f.py:1-2:g:function",
            name="g",
            kind="function",
            language="x",
            path="f.py",
            span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
            origin="x",
            origin_run_id="r",
        )
        result = AnalysisResult(symbols=[sym])  # run=None, no pass_name
        all_symbols: list[Symbol] = []
        limits = Limits()

        collect_analyzer_result(result, [], all_symbols, [], [], limits)

        assert all_symbols == [sym]
        assert limits.skipped_passes == []


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
            origin="java",
            origin_run_id="run1",
        )
        sym_relative = Symbol(
            id="rust:src/lib.rs:1-5:foo:function",
            name="foo",
            kind="function",
            language="rust",
            path="src/lib.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
            origin="rust",
            origin_run_id="run1",
        )

        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "test"}
        run.pass_id = "test"

        result = AnalysisResult(
            symbols=[sym_absolute, sym_relative],
            edges=[],
            usage_contexts=[],
            run=run,
            skipped=False,
        )

        @register_analyzer("test_abs_path", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_test(root: Path) -> AnalysisResult:
            return result

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, symbols, _, _, _, _, _ = facade_run_all(repo_root)

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
        run.to_dict.return_value = {"pass": "test"}
        run.pass_id = "test"

        result = AnalysisResult(
            symbols=[],
            edges=[],
            usage_contexts=[uc],
            run=run,
            skipped=False,
        )

        @register_analyzer("test_uc_path", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_test(root: Path) -> AnalysisResult:
            return result

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, _, _, usage_contexts, _, _, _ = facade_run_all(repo_root)

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
            origin="c",
            origin_run_id="run1",
        )

        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "test"}
        run.pass_id = "test"

        result = AnalysisResult(
            symbols=[sym],
            edges=[],
            usage_contexts=[],
            run=run,
            skipped=False,
        )

        @register_analyzer("test_outside_path", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_test(root: Path) -> AnalysisResult:
            return result

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, symbols, _, _, _, _, _ = facade_run_all(repo_root)

        # Path outside repo_root should be left as-is
        assert symbols[0].path == "/usr/include/stdlib.h"

    def test_absolute_failed_file_paths_are_relativized(self) -> None:
        """failed_files paths are normalized the same way symbol/uc paths are (INV-buhur).

        Helper functions that record failures (e.g. csharp/kotlin/go's
        _extract_symbols_from_file) may not have repo_root in scope and emit
        absolute paths; the orchestrator normalizes for consistency.
        """
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.ir import AnalysisRun

        repo_root = Path("/home/user/myrepo")

        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "test"}
        run.pass_id = "test"
        run.failed_files = [
            {"path": "/home/user/myrepo/src/Broken.cs", "reason": "OSError"},
            {"path": "already/relative.py", "reason": "SyntaxError"},
            {"path": "/elsewhere/outside.go", "reason": "OSError"},
        ]

        result = AnalysisResult(
            symbols=[],
            edges=[],
            usage_contexts=[],
            run=run,
            skipped=False,
        )

        @register_analyzer("test_failed_path", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_test(root: Path) -> AnalysisResult:
            return result

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, _, _, _, limits, _, _ = facade_run_all(repo_root)

        assert len(limits.failed_files) == 3
        # Absolute path under repo_root → relative.
        assert limits.failed_files[0].path == "src/Broken.cs"
        # Already-relative path → unchanged.
        assert limits.failed_files[1].path == "already/relative.py"
        # Absolute path outside repo_root → unchanged.
        assert limits.failed_files[2].path == "/elsewhere/outside.go"


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
        run.to_dict.return_value = {"pass": "test-trunc"}
        run.pass_id = "test-trunc"

        # Analyzer that calls find_files — the global callback should fire
        @register_analyzer("test_trunc", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_trunc(root: Path) -> AnalysisResult:
            list(find_files(root, ["*.py"]))
            return AnalysisResult(run=run, skipped=False)

        try:
            set_max_file_bytes(100)
            with patch(
                "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
            ):
                _, _, _, _, limits, _, _ = facade_run_all(tmp_path)

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


class TestRunAllAnalyzersDependencyManifest:
    """Tests that run_all_analyzers collects dependency manifests (WI-vovuk)."""

    def test_manifest_collected_from_analyzer(self) -> None:
        """Dependency manifests from analyzers are merged and returned."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.ir import AnalysisRun
        from hypergumbo_core.supply_chain import DependencyManifest

        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "test-manifest"}
        run.pass_id = "test-manifest"

        manifest = DependencyManifest(entries={
            "github.com/foo/bar": {"direct": True},
        })

        @register_analyzer("test_manifest", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_test(root: Path) -> AnalysisResult:
            return AnalysisResult(
                run=run,
                skipped=False,
                dependency_manifest=manifest,
            )

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, _, _, _, _, _, merged = facade_run_all(Path("/test"))

        assert merged is not None
        # ADR-0041 §1/§2 (supply:F5): the merged manifest's discriminating
        # output is the directness stamp (tier is uniformly external).
        assert merged.classify_directness("github.com/foo/bar") == "direct"

    def test_no_manifest_returns_none(self) -> None:
        """Returns None when no analyzers provide manifests."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.ir import AnalysisRun

        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "test-nomanifest"}
        run.pass_id = "test-nomanifest"

        @register_analyzer("test_nomanifest", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_test(root: Path) -> AnalysisResult:
            return AnalysisResult(run=run, skipped=False)

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, _, _, _, _, _, merged = facade_run_all(Path("/test"))

        assert merged is None


class TestRunAllAnalyzersFileSymbolSynthesis:
    """WI-ramuv: orchestrator synthesizes file Symbols for dangling make_file_id endpoints."""

    def test_synthesizes_missing_file_symbol_for_dangling_endpoint(self) -> None:
        """An import edge with make_file_id-shape src and no producer-side
        Symbol gets a real file Symbol synthesized at the orchestrator."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.analyze.base import make_file_id
        from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

        repo_root = Path("/test")
        caller = Symbol(
            id="python:src/main.py:10-20:foo:function",
            name="foo",
            kind="function",
            language="python",
            path="src/main.py",
            span=Span(start_line=10, end_line=20, start_col=0, end_col=0),
            origin="py",
            origin_run_id="run1",
        )
        dangling_file_id = make_file_id("python", "src/main.py")
        edge = Edge.create(
            src=dangling_file_id,
            dst=caller.id,
            edge_type="imports_module",
            line=1,
            origin="py",
            origin_run_id="run1",
        )

        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "py"}
        run.pass_id = "py"

        result = AnalysisResult(
            symbols=[caller], edges=[edge], usage_contexts=[],
            run=run, skipped=False,
        )

        @register_analyzer("test_file_synth", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_test(root: Path) -> AnalysisResult:
            return result

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, symbols, _, _, _, _, _ = facade_run_all(repo_root)

        synth = [s for s in symbols if s.id == dangling_file_id]
        assert len(synth) == 1
        assert synth[0].kind == "file"
        assert synth[0].language == "python"
        assert synth[0].path == "src/main.py"

    def test_does_not_duplicate_when_analyzer_already_emits_file_symbol(
        self,
    ) -> None:
        """Analyzers (Lua, HTML, yaml_ansible) that already emit a producer-
        side file Symbol must not gain a duplicate from the post-process."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.analyze.base import make_file_id
        from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

        repo_root = Path("/test")
        file_id = make_file_id("lua", "init.lua")
        existing_file_sym = Symbol(
            id=file_id,
            name="init.lua",
            kind="file",
            language="lua",
            path="init.lua",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="lua",
            origin_run_id="run1",
        )
        edge = Edge.create(
            src=file_id,
            dst="lua:init.lua:5-10:hello:function",
            edge_type="contains",
            line=5,
            origin="lua",
            origin_run_id="run1",
        )

        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "lua"}
        run.pass_id = "lua"

        result = AnalysisResult(
            symbols=[existing_file_sym], edges=[edge], usage_contexts=[],
            run=run, skipped=False,
        )

        @register_analyzer("test_file_no_dup", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_test(root: Path) -> AnalysisResult:
            return result

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, symbols, _, _, _, _, _ = facade_run_all(repo_root)

        file_syms = [s for s in symbols if s.id == file_id]
        assert len(file_syms) == 1
        assert file_syms[0].origin == ["lua"]  # producer's, not the synth's


class TestRunAllAnalyzersFilePresencePreFilter:
    """WI-jadig: skip analyzer dispatch when its languages have zero files.

    Lifecycle policy (INV-manov member): an analyzer is dispatched only
    when ``profile.languages[L].files > 0`` for at least one ``L`` in
    ``analyzer.languages``. Otherwise the dispatcher records a
    ``skipped_passes`` entry with reason ``"no files matched"`` and
    does not invoke the analyzer function — saving the wall-clock cost
    of opening a parser / walking the FileIndex / building a tree for
    a pass that has no input.
    """

    def test_skips_analyzer_when_profile_has_no_files_for_its_language(
        self,
    ) -> None:
        """Analyzer for a language absent from profile.languages is not dispatched."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )

        called: list[str] = []

        @register_analyzer("rust", languages=["rust"])
        def analyze_rust(root: Path) -> AnalysisResult:
            called.append("rust")  # pragma: no cover - should not run
            return AnalysisResult()

        profile = {"languages": {"python": {"files": 5}}}

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, _, _, _, limits, _, _ = facade_run_all(
                Path("/test"), profile=profile,
            )

        assert called == []
        skipped_names = {s["pass"] for s in limits.skipped_passes}
        assert "rust" in skipped_names
        rust_entry = next(s for s in limits.skipped_passes if s["pass"] == "rust")
        assert rust_entry["reason"] == "no files matched"

    def test_runs_analyzer_when_profile_has_files_for_its_language(
        self,
    ) -> None:
        """Analyzer for a language present in profile.languages with files>0 runs."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.ir import AnalysisRun

        called: list[str] = []
        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "python"}
        run.pass_id = "python"

        @register_analyzer("python", languages=["python"])
        def analyze_python(root: Path) -> AnalysisResult:
            called.append("python")
            return AnalysisResult(run=run, skipped=False)

        profile = {"languages": {"python": {"files": 5}}}

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, _, _, _, limits, _, _ = facade_run_all(
                Path("/test"), profile=profile,
            )

        assert called == ["python"]
        # The python pass must NOT appear in skipped_passes.
        skipped_names = {s["pass"] for s in limits.skipped_passes}
        assert "python" not in skipped_names

    def test_no_profile_runs_all_analyzers(self) -> None:
        """When profile is None, every analyzer is dispatched (backcompat)."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.ir import AnalysisRun

        called: list[str] = []

        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "rust"}
        run.pass_id = "rust"

        @register_analyzer("rust", languages=["rust"])
        def analyze_rust(root: Path) -> AnalysisResult:
            called.append("rust")
            return AnalysisResult(run=run, skipped=False)

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            facade_run_all(Path("/test"))  # no profile arg

        assert called == ["rust"]

    def test_zero_files_skips_analyzer(self) -> None:
        """A language entry with files==0 still triggers skip."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )

        called: list[str] = []

        @register_analyzer("go", languages=["go"])
        def analyze_go(root: Path) -> AnalysisResult:
            called.append("go")  # pragma: no cover - should not run
            return AnalysisResult()

        profile = {"languages": {"go": {"files": 0}, "python": {"files": 5}}}

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, _, _, _, limits, _, _ = facade_run_all(
                Path("/test"), profile=profile,
            )

        assert called == []
        skipped_names = {s["pass"] for s in limits.skipped_passes}
        assert "go" in skipped_names

    def test_multi_language_analyzer_runs_if_any_has_files(self) -> None:
        """Analyzer declaring multiple languages runs if ANY has files>0."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.ir import AnalysisRun

        called: list[str] = []
        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "ts_js"}
        run.pass_id = "ts_js"

        @register_analyzer("ts_js", languages=["typescript", "javascript"])
        def analyze_ts_js(root: Path) -> AnalysisResult:
            called.append("ts_js")
            return AnalysisResult(run=run, skipped=False)

        # Only javascript has files; typescript missing entirely.
        profile = {"languages": {"javascript": {"files": 3}}}

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            facade_run_all(Path("/test"), profile=profile)

        assert called == ["ts_js"]

    def test_uncertain_language_dispatches_defensively(self) -> None:
        """Analyzer declaring a language outside LANGUAGE_EXTENSIONS dispatches.

        ``gitignore`` / ``requirements`` / ``manifest_targets`` are not in
        the taxonomy's ``LANGUAGE_EXTENSIONS`` table, so the profile detector
        doesn't count files for them. The pre-filter has no signal to skip
        on, and must dispatch defensively — otherwise the .gitignore /
        requirements.txt / Makefile / etc. file types never get analyzed.
        """
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.ir import AnalysisRun

        called: list[str] = []
        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "gitignore"}
        run.pass_id = "gitignore"

        # WI-juzig: a language the taxonomy lacks a spec for is DECLARED as
        # such; the gate refuses an undeclared one.
        @register_analyzer(
            "gitignore", languages=["gitignore"], language_state=LANGUAGE_STATE_NO_SPEC,
        )
        def analyze_gitignore(root: Path) -> AnalysisResult:
            called.append("gitignore")
            return AnalysisResult(run=run, skipped=False)

        # Profile lists only python — but "gitignore" isn't a taxonomy
        # language, so the dispatcher should NOT use the absence as a
        # signal to skip.
        profile = {"languages": {"python": {"files": 5}}}

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, _, _, _, limits, _, _ = facade_run_all(
                Path("/test"), profile=profile,
            )

        assert called == ["gitignore"]
        skipped_names = {s["pass"] for s in limits.skipped_passes}
        assert "gitignore" not in skipped_names


    def test_no_language_analyzer_is_dispatched_without_profile_evidence(
        self,
    ) -> None:
        """WI-juzig: a pass declared ``no_language`` has ``languages == []``.
        The dispatcher used to re-inflate an empty list into ``{name}`` — a
        phantom the profile never counted, retained by accident. Now the
        empty declaration is retained on purpose: the profile has no opinion
        about a pass that reads no language's files."""
        from hypergumbo_core.analyze.all_analyzers import (
            run_all_analyzers as facade_run_all,
        )
        from hypergumbo_core.ir import AnalysisRun

        called: list[str] = []
        run = MagicMock(spec=AnalysisRun)
        run.to_dict.return_value = {"pass": "manifest-ish"}
        run.pass_id = "manifest-ish"

        @register_analyzer("manifest-ish", language_state=LANGUAGE_STATE_NO_LANGUAGE)
        def analyze_manifest(root: Path) -> AnalysisResult:
            called.append("manifest-ish")
            return AnalysisResult(run=run, skipped=False)

        profile = {"languages": {"python": {"files": 5}}}

        with patch(
            "hypergumbo_core.analyze.all_analyzers.ensure_discovered"
        ):
            _, _, _, _, limits, _, _ = facade_run_all(
                Path("/test"), profile=profile,
            )

        assert called == ["manifest-ish"]
        assert "manifest-ish" not in {s["pass"] for s in limits.skipped_passes}


class TestProductiveRunlessResultIsNotSilentlyDropped:
    """WI-didag: a producer that emits output without a run VANISHES.

    ``collect_analyzer_result``'s run-is-None branch records a
    ``skipped_passes`` entry only when the result produced nothing. A
    producer that emitted symbols/edges but no run is drained and then
    dropped from the accounting entirely — so it lands in NEITHER
    ``analysis_runs`` NOR ``limits.skipped_passes``, breaking the WI-didil
    completeness contract precisely when the pass SUCCEEDED.

    Measured with the rust-analyzer backend enabled on aardvark-dns: 669
    nodes + 637 edges from a pass appearing in neither bucket, the only one
    of 118 registered analyzers for which that was true.

    The output must still be kept — that part of the branch is correct and
    deliberate — so this pins the accounting, not the drain.
    """

    def _productive_runless(self):
        from hypergumbo_core.analyze.base import AnalysisResult
        from hypergumbo_core.ir import Edge, Span, Symbol
        sym = Symbol(
            id="rust:src/lib.rs:1-2:foo:function", name="foo", kind="function",
            language="rust", path="src/lib.rs", span=Span(1, 2, 0, 0),
            origin="scip",
        )
        edge = Edge.create(
            src=sym.id, dst=sym.id, edge_type="calls", line=1,
            confidence=0.9, origin="scip", origin_run_id="uuid:fabricated",
        )
        return AnalysisResult(symbols=[sym], edges=[edge]), sym, edge

    def test_a_runless_producer_appears_in_one_of_the_two_buckets(self) -> None:
        from hypergumbo_core.analyze.all_analyzers import collect_analyzer_result
        from hypergumbo_core.limits import Limits
        result, _sym, _edge = self._productive_runless()
        runs, syms, edges, ucs, limits = [], [], [], [], Limits()
        collect_analyzer_result(
            result, runs, syms, edges, ucs, limits, analyzer_name="rust_analyzer",
        )
        accounted = bool(runs) or any(
            e.get("pass") == "rust_analyzer" for e in limits.skipped_passes
        )
        assert accounted, (
            "a pass that produced 2 records is in neither analysis_runs nor "
            "limits.skipped_passes — WI-didil completeness violated"
        )

    def test_its_output_is_still_kept(self) -> None:
        """The drain is correct and must not regress while fixing accounting."""
        from hypergumbo_core.analyze.all_analyzers import collect_analyzer_result
        from hypergumbo_core.limits import Limits
        result, sym, edge = self._productive_runless()
        runs, syms, edges, ucs, limits = [], [], [], [], Limits()
        collect_analyzer_result(
            result, runs, syms, edges, ucs, limits, analyzer_name="rust_analyzer",
        )
        assert syms == [sym]
        assert edges == [edge]


# ---------------------------------------------------------------------------
# ADR-0057 §10 / WI-hohuh: the producer contract
# ---------------------------------------------------------------------------


def _noop(repo_root: Path) -> AnalysisResult:
    return AnalysisResult(symbols=[], edges=[], run=None)


class TestMergeDeclaration:
    """Each backend DECLARES how its records pair with another producer's.

    The declaration is a value the registry validates at decoration time:
    an anchor (name key + span role + measured authority) for a backend that
    emits records for the same declarations another producer emits, or a
    disjointness claim naming the producers it shares a language with but
    never a record. Unknown keys raise, as ``user_config`` does for unknown
    settings — a silently-ignored declaration would be the ABSENT != EMPTY
    defect at registration.
    """

    def test_a_registration_without_a_declaration_stores_none(self) -> None:
        register_analyzer("python")(_noop)
        entry = get_analyzer("python")
        assert entry is not None
        assert entry.merge is None
        assert entry.executes_analysed_code is False

    def test_an_anchor_is_stored_as_declared(self) -> None:
        anchor = MergeAnchor(name_key=last_segment("::"), span_role=SPAN_ROLE_ITEM)
        register_analyzer("rust", merge=anchor)(_noop)
        entry = get_analyzer("rust")
        assert entry is not None
        assert entry.merge is anchor
        assert entry.merge.authoritative_for == {}

    def test_a_span_role_outside_the_vocabulary_is_refused(self) -> None:
        with pytest.raises(MergeDeclarationError, match="span_role"):
            MergeAnchor(name_key=as_emitted, span_role="line")

    def test_a_name_key_must_be_callable(self) -> None:
        with pytest.raises(MergeDeclarationError, match="name_key"):
            MergeAnchor(name_key="last_segment", span_role=SPAN_ROLE_ITEM)  # type: ignore[arg-type]

    @pytest.mark.parametrize("citation", ["", "measured it myself", "docs/adr/0057.md"])
    def test_an_authority_claim_without_an_audit_citation_is_refused(
        self, citation: str,
    ) -> None:
        """ADR-0057 §5/§10: no ``authoritative_for`` entry except by citing a
        committed ``docs/audits/`` table produced by the agreement instrument."""
        with pytest.raises(MergeDeclarationError, match=AUDIT_CITATION_PREFIX):
            MergeAnchor(
                name_key=as_emitted,
                span_role=SPAN_ROLE_TOKEN,
                authoritative_for={"kind": citation},
            )

    def test_a_cited_authority_claim_is_stored(self) -> None:
        anchor = MergeAnchor(
            name_key=as_emitted,
            span_role=SPAN_ROLE_TOKEN,
            authoritative_for={"kind": "docs/audits/99-example.md"},
        )
        assert anchor.authoritative_for == {"kind": "docs/audits/99-example.md"}

    def test_an_unknown_declaration_key_raises(self) -> None:
        with pytest.raises(TypeError):
            MergeAnchor(name_key=as_emitted, span_role=SPAN_ROLE_TOKEN, authority="x")  # type: ignore[call-arg]

    def test_a_dict_is_not_a_declaration(self) -> None:
        """The decorator refuses anything but the two declaration types, naming
        the analyzer — a dict with the right keys would be accepted by duck
        typing and then silently ignore a misspelled one."""
        with pytest.raises(MergeDeclarationError, match="rust"):
            register_analyzer(
                "rust", merge={"name_key": as_emitted, "span_role": "token"},  # type: ignore[arg-type]
            )(_noop)

    def test_a_disjoint_declaration_needs_at_least_one_partner(self) -> None:
        with pytest.raises(MergeDeclarationError, match="partners"):
            MergeDisjoint(partners=())

    def test_name_key_helpers(self) -> None:
        assert as_emitted("process_message") == "process_message"
        assert last_segment("::")("CoreDns::process_message") == "process_message"
        assert last_segment("::")("free_fn") == "free_fn"
        assert last_segment(".")("Class.method") == "method"


class TestMergeParticipants:
    """``merge_participants(L)`` is what the merge pass (WI-kokiz) reads.

    It returns the anchored producers of ``L`` the pass pairs among, and it
    REFUSES — naming the analyzer — when a language has two producers and
    one of them has not declared how its records relate to the other's.
    Falling through would read as "nothing to merge".
    """

    _anchor = MergeAnchor(name_key=as_emitted, span_role=SPAN_ROLE_TOKEN)
    _item = MergeAnchor(name_key=last_segment("::"), span_role=SPAN_ROLE_ITEM)

    def test_a_sole_producer_has_nothing_to_merge_declared_or_not(self) -> None:
        register_analyzer("rust")(_noop)
        assert merge_participants("rust") == []
        register_analyzer("python", merge=self._anchor)(_noop)
        assert merge_participants("python") == []

    def test_two_anchored_producers_are_returned_in_priority_order(self) -> None:
        register_analyzer("rust", priority=50, merge=self._item)(_noop)
        register_analyzer("rust_scip", priority=45, languages=["rust"], merge=self._anchor)(_noop)
        assert [a.name for a in merge_participants("rust")] == ["rust_scip", "rust"]

    def test_an_undeclared_producer_is_refused_by_name(self) -> None:
        register_analyzer("rust", merge=self._item)(_noop)
        register_analyzer("rust_scip", languages=["rust"])(_noop)
        with pytest.raises(UndeclaredProducerError) as exc:
            merge_participants("rust")
        message = str(exc.value)
        assert "rust_scip" in message
        assert "'rust'" in message  # the language, so the reader knows which pair

    def test_mutually_disjoint_producers_have_nothing_to_merge(self) -> None:
        register_analyzer("javascript", languages=["javascript", "svelte"],
                          merge=MergeDisjoint(partners=("svelte",)))(_noop)
        register_analyzer("svelte", merge=MergeDisjoint(partners=("javascript",)))(_noop)
        assert merge_participants("svelte") == []

    def test_a_one_sided_disjoint_claim_covers_the_pair(self) -> None:
        """An anchored incumbent need not name every template analyzer that
        keeps out of its way; the analyzer that stays disjoint says so."""
        register_analyzer("javascript", languages=["javascript", "svelte"],
                          merge=self._anchor)(_noop)
        register_analyzer("svelte", merge=MergeDisjoint(partners=("javascript",)))(_noop)
        assert merge_participants("svelte") == []

    def test_a_disjoint_claim_that_names_neither_side_is_refused(self) -> None:
        register_analyzer("javascript", languages=["javascript", "svelte"],
                          merge=self._anchor)(_noop)
        register_analyzer("svelte", merge=MergeDisjoint(partners=("vue",)))(_noop)
        with pytest.raises(UndeclaredProducerError) as exc:
            merge_participants("svelte")
        message = str(exc.value)
        assert "svelte" in message and "javascript" in message

    def test_disjoint_producers_are_excluded_from_the_anchored_set(self) -> None:
        register_analyzer("rust", priority=50, merge=self._item)(_noop)
        register_analyzer("rust_scip", priority=45, languages=["rust"], merge=self._anchor)(_noop)
        register_analyzer("rust_docs", priority=60, languages=["rust"],
                          merge=MergeDisjoint(partners=("rust", "rust_scip")))(_noop)
        assert [a.name for a in merge_participants("rust")] == ["rust_scip", "rust"]


class TestExecutesAnalysedCode:
    """ADR-0045 §5: which store a backend's opt-in lands in follows from the
    bit the backend DECLARES, not from a list someone remembers to update."""

    def test_the_executing_set_is_derived_from_the_declarations(self) -> None:
        register_analyzer("rust")(_noop)
        register_analyzer("rust_scip", languages=["rust"], executes_analysed_code=True)(_noop)
        register_analyzer("python", executes_analysed_code=False)(_noop)
        assert backends_executing_analysed_code() == frozenset({"rust_scip"})

    def test_an_empty_registry_executes_nothing(self) -> None:
        assert backends_executing_analysed_code() == frozenset()
