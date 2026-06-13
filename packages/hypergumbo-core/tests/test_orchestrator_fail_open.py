# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail-open containment at the analyzer + linker orchestrators (§17 / WI-madal L3).

§17 of the spec guarantees that ``hypergumbo run`` emits valid (if partial)
JSON even when analysis is incomplete: a single failing pass must never abort
the whole run. WI-madal's L1/L2 fixes covered per-file read errors and the
fingerprint pre-pass; the subjects here are **L3** — the two orchestrator
``future.result()`` sites that previously let ANY exception escaping a pass
kill the entire run:

* ``analyze/all_analyzers.run_all_analyzers`` — the threaded analyzer
  dispatcher (the production path used by ``cli.run_behavior_map``), and
* ``linkers/registry.run_all_linkers`` — the linker dispatcher, in BOTH its
  serial (single-linker priority group) and parallel (multi-linker group)
  branches.

A crashed pass is recorded *pass-level* (it is not tied to one file) via
``Limits.record_crashed_pass`` -> ``skipped_passes`` with a ``crashed:``
reason, distinct from the deliberate "no files matched" / "grammar not
available" skips, and ``partial_results_reason`` is set so the top-level
honesty signal fires. No new output-schema field is introduced.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hypergumbo_core.analyze import all_analyzers as _all_analyzers
from hypergumbo_core.analyze import registry as _analyzer_registry_mod
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.ir import AnalysisRun
from hypergumbo_core.limits import Limits
from hypergumbo_core.linkers import registry as _linker_registry_mod
from hypergumbo_core.linkers.registry import (
    LinkerContext,
    LinkerResult,
    register_linker,
    run_all_linkers,
)


@pytest.fixture(autouse=True)
def _isolate_registries():
    """Isolate both the analyzer and linker registries for each test.

    Saves and clears each registry (and the analyzer discovery flag) so a test
    sees only the fakes it registers, then restores the originals afterward —
    other test files on the same xdist worker rely on the import-time
    registrations.
    """
    saved_analyzers = dict(_analyzer_registry_mod._ANALYZER_REGISTRY)
    saved_discovered = _analyzer_registry_mod._discovered
    saved_linkers = dict(_linker_registry_mod._LINKER_REGISTRY)
    _analyzer_registry_mod._ANALYZER_REGISTRY.clear()
    _analyzer_registry_mod._discovered = False
    _linker_registry_mod._LINKER_REGISTRY.clear()
    yield
    _analyzer_registry_mod._ANALYZER_REGISTRY.clear()
    _analyzer_registry_mod._ANALYZER_REGISTRY.update(saved_analyzers)
    _analyzer_registry_mod._discovered = saved_discovered
    _linker_registry_mod._LINKER_REGISTRY.clear()
    _linker_registry_mod._LINKER_REGISTRY.update(saved_linkers)


def _healthy_analysis_result(pass_id: str) -> AnalysisResult:
    """A minimal non-skipped analyzer result that survives full collection."""
    run = MagicMock(spec=AnalysisRun)
    run.to_dict.return_value = {"pass": pass_id}
    run.pass_id = pass_id
    run.failed_files = []
    return AnalysisResult(
        run=run, symbols=[], edges=[], usage_contexts=[], skipped=False
    )


class TestLimitsRecordCrashedPass:
    """Limits.record_crashed_pass — the shared pass-level crash channel."""

    def test_records_pass_and_reason(self) -> None:
        """A crash lands in skipped_passes with a 'crashed:' reason + sets partial flag."""
        limits = Limits()
        limits.record_crashed_pass("python", RuntimeError("boom"))
        assert limits.skipped_passes == [
            {"pass": "python", "reason": "crashed: RuntimeError: boom"}
        ]
        assert limits.partial_results_reason  # top-level honesty signal fires
        # And it surfaces in the serialized limits block consumers read.
        assert {"pass": "python", "reason": "crashed: RuntimeError: boom"} in (
            limits.to_dict()["skipped_passes"]
        )

    def test_does_not_clobber_existing_partial_reason(self) -> None:
        """An already-set partial_results_reason is preserved, not overwritten."""
        limits = Limits()
        limits.partial_results_reason = "preset reason"
        limits.record_crashed_pass("go", ValueError("nope"))
        assert limits.partial_results_reason == "preset reason"
        assert limits.skipped_passes[0]["pass"] == "go"


class TestAnalyzerOrchestratorFailOpen:
    """analyze/all_analyzers.run_all_analyzers — threaded production dispatcher."""

    def test_crashing_analyzer_does_not_abort_run(self, tmp_path: Path) -> None:
        """A crashing analyzer is contained; sibling results + partial output survive."""

        @register_analyzer("healthy-an", priority=10)
        def _healthy(root, **kwargs):
            return _healthy_analysis_result("healthy-an")

        @register_analyzer("crash-an", priority=20)
        def _crash(root, **kwargs):
            raise RuntimeError("analyzer boom")

        # Pin discovery so ensure_discovered() does not repopulate real analyzers.
        _analyzer_registry_mod._discovered = True

        (analysis_runs, _syms, _edges, _ucs, limits, _cap, _man) = (
            _all_analyzers.run_all_analyzers(tmp_path)
        )

        # The healthy analyzer's run survived the sibling crash (loop continued).
        assert any(r.get("pass") == "healthy-an" for r in analysis_runs)
        # The crash was contained and recorded pass-level.
        crashed = [s for s in limits.skipped_passes if s["pass"] == "crash-an"]
        assert len(crashed) == 1
        assert crashed[0]["reason"].startswith("crashed: RuntimeError")
        assert "analyzer boom" in crashed[0]["reason"]
        assert limits.partial_results_reason


class TestLinkerOrchestratorFailOpen:
    """linkers/registry.run_all_linkers — serial + parallel dispatch paths."""

    @staticmethod
    def _ctx() -> LinkerContext:
        return LinkerContext(repo_root=Path("/test"))

    def test_serial_crashing_linker_does_not_abort(self) -> None:
        """Distinct priorities -> serial path; a crash is contained without a limits sink."""

        @register_linker("healthy-ser", priority=10)
        def _healthy(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        @register_linker("crash-ser", priority=20)
        def _crash(ctx: LinkerContext) -> LinkerResult:
            raise RuntimeError("linker serial boom")

        # No limits passed: containment must still hold (limits-is-None branch).
        results = run_all_linkers(self._ctx())
        names = {name for name, _ in results}
        assert "healthy-ser" in names
        assert "crash-ser" not in names

    def test_parallel_crashing_linker_does_not_abort(self) -> None:
        """Same priority -> parallel ThreadPoolExecutor path; a crash is contained."""

        @register_linker("healthy-par", priority=30)
        def _healthy(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        @register_linker("crash-par", priority=30)
        def _crash(ctx: LinkerContext) -> LinkerResult:
            raise RuntimeError("linker parallel boom")

        results = run_all_linkers(self._ctx())
        names = {name for name, _ in results}
        assert "healthy-par" in names
        assert "crash-par" not in names

    def test_crash_recorded_into_limits_when_supplied(self) -> None:
        """When a Limits sink is supplied, the crashed linker is recorded pass-level."""

        @register_linker("crash-rec", priority=15)
        def _crash(ctx: LinkerContext) -> LinkerResult:
            raise ValueError("recorded boom")

        limits = Limits()
        run_all_linkers(self._ctx(), limits=limits)
        crashed = [s for s in limits.skipped_passes if s["pass"] == "crash-rec"]
        assert len(crashed) == 1
        assert crashed[0]["reason"].startswith("crashed: ValueError")
        assert "recorded boom" in crashed[0]["reason"]

    def test_enclosure_post_pass_crash_is_contained(self, monkeypatch) -> None:
        """The enclosure post-pass is itself a pass; a crash there must not abort.

        ``_connect_synthetic_to_enclosing`` runs after the priority-group loop
        and mints its own AnalysisRun, so it is a §17 pass. A crash in it is
        contained: the priority-group linker results gathered so far survive,
        and the crash is recorded as the 'enclosure' pass.
        """

        @register_linker("healthy-enc", priority=40)
        def _healthy(ctx: LinkerContext) -> LinkerResult:
            return LinkerResult()

        def _boom(*args, **kwargs):
            raise RuntimeError("enclosure boom")

        monkeypatch.setattr(
            _linker_registry_mod, "_connect_synthetic_to_enclosing", _boom
        )

        limits = Limits()
        results = run_all_linkers(self._ctx(), limits=limits)
        names = {name for name, _ in results}
        assert "healthy-enc" in names  # priority-group linker survived
        assert "enclosure" not in names  # crashed post-pass not appended
        crashed = [s for s in limits.skipped_passes if s["pass"] == "enclosure"]
        assert len(crashed) == 1
        assert crashed[0]["reason"].startswith("crashed: RuntimeError")
        assert "enclosure boom" in crashed[0]["reason"]
