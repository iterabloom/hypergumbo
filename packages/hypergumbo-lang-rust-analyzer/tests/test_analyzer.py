# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for :mod:`hypergumbo_lang_rust_analyzer.analyzer` (WI-duzul Slice C-final).

Validates the registry-level analyzer function that chains the gate +
graceful-degrade + translate primitives into a single
``AnalysisResult``-returning entry point the hypergumbo-core analyzer
registry can dispatch to.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_lang_rust_analyzer.graceful_degrade import ScipAttempt
from hypergumbo_lang_rust_analyzer.analyzer import (
    _disk_source_reader,
    _repo_anchored_reader,
    analyze_rust_with_scip,
)


@pytest.fixture(autouse=True)
def _reset_graceful_degrade_log_dedup():
    """Graceful-degrade caches per-workspace log markers globally; reset."""
    from hypergumbo_lang_rust_analyzer.graceful_degrade import (
        _reset_logged_fallback_for_tests,
    )
    _reset_logged_fallback_for_tests()
    yield
    _reset_logged_fallback_for_tests()


class TestDiskSourceReader:
    def test_reads_existing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "lib.rs"
        f.write_bytes(b"fn main() {}\n")
        assert _disk_source_reader(str(f)) == b"fn main() {}\n"

    def test_repo_anchored_reader_resolves_against_repo_root_not_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """WI-kilih: SCIP's repo-relative ``doc.relative_path`` must resolve against
        ``repo_root``, not the process CWD — otherwise the stable_id parity
        reassignment silently no-ops when the survey runs from anywhere but
        ``repo_root``, and the SCIP symbol diverges from the tree-sitter anchor."""
        repo = tmp_path / "myrepo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "lib.rs").write_bytes(b"fn main() {}")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)  # cwd != repo_root — the divergence scenario

        reader = _repo_anchored_reader(repo)
        # a repo-relative path resolves against repo_root, not cwd
        assert reader("src/lib.rs") == b"fn main() {}"
        # a genuinely-missing file still degrades to None (skip reassignment, no crash)
        assert reader("src/missing.rs") is None


class TestAnalyzeRustWithScip:
    def test_gate_false_returns_empty_result(self, tmp_path: Path) -> None:
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=False,
        ):
            result = analyze_rust_with_scip(tmp_path)
        assert isinstance(result, AnalysisResult)
        assert result.symbols == []
        assert result.edges == []
        # WI-didil: self-declare the skip so the orchestrator records an honest
        # skipped_passes entry (not the generic "no files matched", which would
        # be wrong — the repo may contain .rs files; the backend just stayed off).
        assert result.skipped is True
        assert result.skip_reason == "rust-analyzer backend not enabled"

    def test_gate_false_does_not_call_try_analyze(
        self, tmp_path: Path,
    ) -> None:
        """Short-circuit: gate off → backend never invoked."""
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=False,
        ), patch(
            "hypergumbo_lang_rust_analyzer.analyzer.try_analyze_with_rust_analyzer",
            side_effect=AssertionError("should not be called"),
        ) as fake_try:
            analyze_rust_with_scip(tmp_path)
            fake_try.assert_not_called()

    def test_gate_true_but_backend_failed_is_a_reasoned_skip(
        self, tmp_path: Path,
    ) -> None:
        """Re-pointed, not deleted (WI-luvud).

        This asserted the literal prose ``"rust-analyzer backend produced no
        output"`` — the single string all FOUR failure states used to share,
        which is the defect WI-luvud names. The behaviour it was protecting
        (a failure self-declares a skip rather than vanishing) still holds and
        is still asserted; only the conflated string is gone.
        """
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.analyzer.try_analyze_with_rust_analyzer",
            return_value=ScipAttempt.failure(
                "pass_crashed", "rust-analyzer scip exited 101",
            ),
        ):
            result = analyze_rust_with_scip(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        # WI-didil: backend on but SCIP produced nothing (WI-nohah fall-through)
        # → self-declare a reasoned skip rather than vanishing silently.
        assert result.skipped is True
        assert result.skip_reason_code == "pass_crashed"
        assert "exited 101" in result.skip_reason, (
            "the skip prose must carry the state-specific detail, not a "
            "single string shared by every failure mode"
        )

    def test_gate_true_and_backend_succeeds_returns_symbols_and_edges(
        self, tmp_path: Path,
    ) -> None:
        sample_sym = Symbol(
            id="rust:src/lib.rs:1-2:foo:function",
            name="foo", kind="function", language="rust",
            path="src/lib.rs", span=Span(1, 2, 0, 0),
            origin="scip",
        )
        sample_edge = Edge.create(
            src=sample_sym.id, dst=sample_sym.id, edge_type="calls",
            line=1, confidence=0.9, origin="test", origin_run_id="rx",
        )
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.analyzer.try_analyze_with_rust_analyzer",
            return_value=ScipAttempt.success([sample_sym], [sample_edge]),
        ):
            result = analyze_rust_with_scip(tmp_path)
        assert result.symbols == [sample_sym]
        assert result.edges == [sample_edge]

    @pytest.mark.filterwarnings("ignore::UserWarning")  # mocked backend → no-SCIP-edges diag
    def test_passes_repo_root_and_anchored_reader_to_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_bytes(b"fn f() {}")
        captured: dict[str, object] = {}

        def _fake_try(workspace, source_reader, *, log=None, run_id=""):
            captured["workspace"] = workspace
            captured["reader"] = source_reader
            return ScipAttempt.success([], [])

        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.analyzer.try_analyze_with_rust_analyzer",
            side_effect=_fake_try,
        ):
            monkeypatch.chdir(tmp_path.parent)  # cwd != repo_root
            analyze_rust_with_scip(tmp_path)
        assert captured["workspace"] == tmp_path
        # WI-kilih: the reader handed to the backend is anchored at repo_root, so a
        # repo-relative SCIP path resolves against tmp_path, not the CWD.
        reader = captured["reader"]
        assert reader("src/lib.rs") == b"fn f() {}"


class TestAnalyzerRegistration:
    def test_registered_with_expected_name_and_priority(self) -> None:
        """The @register_analyzer decorator must announce the analyzer."""
        # Importing the module triggers the decorator; assert the
        # registry entry is present with the expected name and priority.
        import hypergumbo_lang_rust_analyzer.analyzer
        _ = hypergumbo_lang_rust_analyzer.analyzer  # keep import alive
        from hypergumbo_core.analyze.registry import (
            _ANALYZER_REGISTRY,
        )
        assert "rust_analyzer" in _ANALYZER_REGISTRY
        entry = _ANALYZER_REGISTRY["rust_analyzer"]
        assert entry.priority == 45
        assert entry.name == "rust_analyzer"

    def test_declares_itself_as_the_scip_backend_for_rust(self) -> None:
        """WI-juzig / ADR-0057 §10: the registry could not tell ``rust`` and
        ``rust_analyzer`` were two backends for one language — ``languages``
        defaulted to ``[name]``, making ``rust_analyzer`` its own (phantom)
        language, and ``backend`` was set on 0 of 118 registrations."""
        import hypergumbo_lang_rust_analyzer.analyzer as _ra
        _ = _ra  # the decorator fired at import
        from hypergumbo_core.analyze.registry import _ANALYZER_REGISTRY
        entry = _ANALYZER_REGISTRY["rust_analyzer"]
        assert entry.languages == ["rust"]
        assert entry.backend == "scip"


class TestEngagementCheck:
    """WI-todon: warn if the SCIP backend ran but produced no SCIP-origin edges
    on a repo that contains `.rs` files.

    This catches silent fall-through after invoke returned exit 0 but the
    SCIP→IR translation yielded nothing useful — exactly the wasmtime / OOM
    scenario where rust-analyzer "completes" without engaging.
    """

    @staticmethod
    def _make_scip_edge() -> Edge:
        sym = Symbol(
            id="rust:src/lib.rs:1-2:foo:function",
            name="foo", kind="function", language="rust",
            path="src/lib.rs", span=Span(1, 2, 0, 0), origin="scip",
        )
        return Edge.create(
            src=sym.id, dst=sym.id, edge_type="calls",
            line=1, confidence=0.9, origin="scip", origin_run_id="rx",
        )

    @staticmethod
    def _make_non_scip_edge() -> Edge:
        sym = Symbol(
            id="rust:src/lib.rs:1-2:foo:function",
            name="foo", kind="function", language="rust",
            path="src/lib.rs", span=Span(1, 2, 0, 0), origin="rust-v1",
        )
        return Edge.create(
            src=sym.id, dst=sym.id, edge_type="calls",
            line=1, confidence=0.5, origin="rust-v1", origin_run_id="rx",
        )

    def test_warns_when_zero_scip_edges_and_rs_files_present(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "lib.rs").write_bytes(b"fn main() {}\n")
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.analyzer.try_analyze_with_rust_analyzer",
            return_value=ScipAttempt.success([], []),
        ), pytest.warns(UserWarning, match="produced no SCIP"):
            analyze_rust_with_scip(tmp_path)

    def test_silent_when_at_least_one_scip_edge_present(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "lib.rs").write_bytes(b"fn main() {}\n")
        scip_edge = self._make_scip_edge()
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.analyzer.try_analyze_with_rust_analyzer",
            return_value=ScipAttempt.success([], [scip_edge]),
        ):
            import warnings
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                analyze_rust_with_scip(tmp_path)
            engagement = [w for w in caught if "produced no SCIP" in str(w.message)]
            assert engagement == []

    def test_silent_when_no_rs_files_present(self, tmp_path: Path) -> None:
        """No .rs files → no expectation that SCIP would emit anything; no warn."""
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.analyzer.try_analyze_with_rust_analyzer",
            return_value=ScipAttempt.success([], []),
        ):
            import warnings
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                analyze_rust_with_scip(tmp_path)
            engagement = [w for w in caught if "produced no SCIP" in str(w.message)]
            assert engagement == []

    def test_silent_when_result_is_none(self, tmp_path: Path) -> None:
        """graceful-degrade already logged on its way to None; don't double-warn."""
        (tmp_path / "lib.rs").write_bytes(b"fn main() {}\n")
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.analyzer.try_analyze_with_rust_analyzer",
            return_value=ScipAttempt.failure(
                "pass_crashed", "rust-analyzer scip exited 101",
            ),
        ):
            import warnings
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                analyze_rust_with_scip(tmp_path)
            engagement = [w for w in caught if "produced no SCIP" in str(w.message)]
            assert engagement == []

    def test_silent_when_gate_false(self, tmp_path: Path) -> None:
        """Gate False means the user did not opt in — no warning regardless."""
        (tmp_path / "lib.rs").write_bytes(b"fn main() {}\n")
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=False,
        ):
            import warnings
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                analyze_rust_with_scip(tmp_path)
            engagement = [w for w in caught if "produced no SCIP" in str(w.message)]
            assert engagement == []

    def test_detects_rs_files_in_subdirectories(self, tmp_path: Path) -> None:
        """Engagement check uses recursive scan — workspace crates live in subdirs."""
        sub = tmp_path / "crates" / "foo" / "src"
        sub.mkdir(parents=True)
        (sub / "lib.rs").write_bytes(b"fn main() {}\n")
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.analyzer.try_analyze_with_rust_analyzer",
            return_value=ScipAttempt.success([], []),
        ), pytest.warns(UserWarning, match="produced no SCIP"):
            analyze_rust_with_scip(tmp_path)

    def test_warning_wired_to_graceful_degrade_log_callable(
        self, tmp_path: Path,
    ) -> None:
        """analyzer.py wires a real log callable into try_analyze_with_rust_analyzer
        so graceful-degrade's diagnostics surface as warnings to the user."""
        captured_log: list[object] = []

        def _fake_try(workspace, source_reader, *, log=None, run_id=""):
            captured_log.append(log)
            return ScipAttempt.failure("pass_crashed", "exited 101")

        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.analyzer.try_analyze_with_rust_analyzer",
            side_effect=_fake_try,
        ):
            analyze_rust_with_scip(tmp_path)

        assert len(captured_log) == 1
        log_fn = captured_log[0]
        assert callable(log_fn)
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            log_fn("test message from graceful-degrade")
        assert len(caught) == 1
        assert "test message from graceful-degrade" in str(caught[0].message)


class TestProvenanceJoin:
    """WI-didag / WI-zabus: the success path must emit a real ``AnalysisRun``.

    Measured on ``aardvark-dns`` before this landed: 669 nodes and 637 edges
    carrying ``origin=['scip']`` entered the artifact from a pass that
    appeared in NEITHER ``analysis_runs`` NOR ``limits.skipped_passes`` — the
    only one of 118 registered analyzers for which that was true — and all
    1306 records had provenance resolving to nothing (1308 cross_field
    validation violations). The cause was one missing ``run=`` argument.
    """

    def _scip_blob(self) -> bytes:
        from hypergumbo_core.scip._generated import scip_pb2
        sym = "rust-analyzer cargo my_crate 0.1.0 math/add()."
        return scip_pb2.Index(documents=[scip_pb2.Document(
            language="Rust", relative_path="src/lib.rs",
            symbols=[scip_pb2.SymbolInformation(symbol=sym)],
            occurrences=[scip_pb2.Occurrence(
                symbol=sym, symbol_roles=0x01, range=[0, 0, 2, 0],
            )],
        )]).SerializeToString()

    def test_success_path_returns_an_analysis_run(self, tmp_path: Path) -> None:
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.graceful_degrade.run_rust_analyzer_scip",
            return_value=self._scip_blob(),
        ):
            result = analyze_rust_with_scip(tmp_path)
        assert result.run is not None, "a pass that ran must carry an AnalysisRun"
        assert result.run.pass_id == "rust_analyzer"

    def test_every_emitted_record_joins_to_the_returned_run(
        self, tmp_path: Path,
    ) -> None:
        """The end-to-end join, through the REAL translate and degrade path."""
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.graceful_degrade.run_rust_analyzer_scip",
            return_value=self._scip_blob(),
        ):
            result = analyze_rust_with_scip(tmp_path)
        assert result.run is not None
        assert result.symbols, "fixture must produce symbols to be a control"
        run_id = result.run.execution_id
        dangling_syms = [s for s in result.symbols if s.origin_run_id != run_id]
        dangling_edges = [e for e in result.edges if e.origin_run_id != run_id]
        assert dangling_syms == [], "symbols must join to the emitted run"
        assert dangling_edges == [], "edges must join to the emitted run"


class TestSilenceClassification:
    """WI-luvud: 'backend produced no output' was filed as ONE skip.

    Four distinct states shared the reason string "rust-analyzer backend
    produced no output" and the code ``unreported``, and the row's own title
    objected that the pass RAN. Both halves are addressed here: the states are
    told apart, and the one where the backend genuinely ran to completion now
    lands in the RAN population with an ``AnalysisRun``.
    """

    def _run_with(self, tmp_path, attempt):
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.analyzer.try_analyze_with_rust_analyzer",
            return_value=attempt,
        ):
            return analyze_rust_with_scip(tmp_path)

    def test_missing_binary_is_reported_as_dependency_unavailable(
        self, tmp_path: Path,
    ) -> None:
        from hypergumbo_lang_rust_analyzer.graceful_degrade import ScipAttempt
        result = self._run_with(tmp_path, ScipAttempt.failure(
            "dependency_unavailable", "rust-analyzer binary not resolvable",
        ))
        assert result.skipped is True
        assert result.skip_reason_code == "dependency_unavailable"

    def test_a_crash_is_reported_as_pass_crashed(self, tmp_path: Path) -> None:
        from hypergumbo_lang_rust_analyzer.graceful_degrade import ScipAttempt
        result = self._run_with(tmp_path, ScipAttempt.failure(
            "pass_crashed", "rust-analyzer scip exited 101",
        ))
        assert result.skipped is True
        assert result.skip_reason_code == "pass_crashed"

    def test_the_four_states_no_longer_share_one_reason_string(
        self, tmp_path: Path,
    ) -> None:
        """The conflation itself, pinned: distinct states, distinct prose."""
        from hypergumbo_lang_rust_analyzer.graceful_degrade import ScipAttempt
        reasons = {
            self._run_with(tmp_path, ScipAttempt.failure(code, detail)).skip_reason
            for code, detail in (
                ("dependency_unavailable", "binary not resolvable"),
                ("pass_crashed", "exited 101"),
                ("unreported", "exited 0 without writing an index"),
            )
        }
        assert len(reasons) == 3, f"states still share prose: {reasons}"

    def test_a_successful_empty_index_RAN_and_says_so(
        self, tmp_path: Path,
    ) -> None:
        """The state the row is named for. The backend ran to completion and
        found nothing: that belongs in analysis_runs, not skipped_passes, and
        must NOT claim no_candidate_files — the repo may be full of .rs."""
        from hypergumbo_lang_rust_analyzer.graceful_degrade import ScipAttempt
        result = self._run_with(tmp_path, ScipAttempt.success([], []))
        assert result.skipped is False, "the backend ran; this is not a skip"
        assert result.run is not None, "a pass that ran carries an AnalysisRun"
        assert result.run.silence_reason == "no_candidate_construct"
