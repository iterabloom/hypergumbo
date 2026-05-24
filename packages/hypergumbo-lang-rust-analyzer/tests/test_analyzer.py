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
from hypergumbo_lang_rust_analyzer.analyzer import (
    _disk_source_reader,
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

    def test_gate_true_but_backend_returns_none_empty_result(
        self, tmp_path: Path,
    ) -> None:
        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.analyzer.try_analyze_with_rust_analyzer",
            return_value=None,
        ):
            result = analyze_rust_with_scip(tmp_path)
        assert result.symbols == []
        assert result.edges == []

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
            return_value=([sample_sym], [sample_edge]),
        ):
            result = analyze_rust_with_scip(tmp_path)
        assert result.symbols == [sample_sym]
        assert result.edges == [sample_edge]

    def test_passes_repo_root_and_disk_reader_to_backend(
        self, tmp_path: Path,
    ) -> None:
        captured: dict[str, object] = {}

        def _fake_try(workspace, source_reader, *, log=None):
            captured["workspace"] = workspace
            captured["reader"] = source_reader
            return ([], [])

        with patch(
            "hypergumbo_lang_rust_analyzer.analyzer.should_use_rust_analyzer_backend",
            return_value=True,
        ), patch(
            "hypergumbo_lang_rust_analyzer.analyzer.try_analyze_with_rust_analyzer",
            side_effect=_fake_try,
        ):
            analyze_rust_with_scip(tmp_path)
        assert captured["workspace"] == tmp_path
        assert captured["reader"] is _disk_source_reader


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
            return_value=([], []),
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
            return_value=([], [scip_edge]),
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
            return_value=([], []),
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
            return_value=None,
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
            return_value=([], []),
        ), pytest.warns(UserWarning, match="produced no SCIP"):
            analyze_rust_with_scip(tmp_path)

    def test_warning_wired_to_graceful_degrade_log_callable(
        self, tmp_path: Path,
    ) -> None:
        """analyzer.py wires a real log callable into try_analyze_with_rust_analyzer
        so graceful-degrade's diagnostics surface as warnings to the user."""
        captured_log: list[object] = []

        def _fake_try(workspace, source_reader, *, log=None):
            captured_log.append(log)
            return None

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
