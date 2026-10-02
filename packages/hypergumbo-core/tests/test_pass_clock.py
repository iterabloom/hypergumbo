# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-pass cost: ``AnalysisRun.cpu_ms`` beside the wall-clock ``duration_ms``.

WI-nuvam. Analyzers run in one ThreadPoolExecutor and linkers in one pool per
priority cohort, so a pass's ``duration_ms`` is the WALL time of its worker:
cards overlap (they do not sum to the survey's wall time) and a card inflates
with whatever runs alongside it (a thread waiting on the GIL is still on the
wall clock). The measured case on the row: the same 43 bash files billed 2.3 s
or 49.9 s depending only on what the python analyzer was doing.

``cpu_ms`` is the CPU time of the thread that ran the pass. Waiting on the
GIL, on I/O or on a sleep does not advance it, so it measures the pass rather
than the machine's load. These tests pin three things:

* the clock separates waiting from working (a sleeping pass: large wall, ~0
  CPU; a burning pass: CPU at least what it burned);
* the CPU is read on the WORKER thread at each pool (a CPU-burning pass would
  read ~0 if it were read on the orchestrating thread, which only waits), for
  analyzers and for linkers on both the serial and the parallel branch;
* every pass serialized into ``analysis_runs`` carries a measured ``cpu_ms``,
  and an unmeasured run serializes ``null``, never a 0 that reads as "free".
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from hypergumbo_core.analyze import all_analyzers as _all_analyzers
from hypergumbo_core.analyze import registry as _analyzer_registry_mod
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.ir import AnalysisRun
from hypergumbo_core.linkers import registry as _linker_registry_mod
from hypergumbo_core.linkers.registry import (
    LinkerContext,
    LinkerResult,
    register_linker,
    run_all_linkers,
)
from hypergumbo_core.pass_clock import PassClock, PassCost

# A burn target well above timer granularity, and a sleep well above the CPU
# the sleeping thread spends getting in and out of the sleep.
_BURN_S = 0.15
_SLEEP_S = 0.3


def _burn_cpu(seconds: float) -> None:
    """Spin until THIS thread has consumed ``seconds`` of CPU.

    Keyed on thread_time, not the wall clock, so the burn is the same CPU
    amount however loaded the machine is: the assertion on it is exact by
    construction rather than timing-sensitive.
    """
    deadline = time.thread_time() + seconds
    while time.thread_time() < deadline:
        pass


def _run(pass_id: str) -> AnalysisRun:
    return AnalysisRun.create(pass_id=pass_id, version="1.0.0")


@pytest.fixture
def _isolated_registries():
    saved_analyzers = dict(_analyzer_registry_mod._ANALYZER_REGISTRY)
    saved_discovered = _analyzer_registry_mod._discovered
    saved_linkers = dict(_linker_registry_mod._LINKER_REGISTRY)
    _analyzer_registry_mod._ANALYZER_REGISTRY.clear()
    # Pin discovery so ensure_discovered() does not repopulate real analyzers.
    _analyzer_registry_mod._discovered = True
    _linker_registry_mod._LINKER_REGISTRY.clear()
    yield
    _analyzer_registry_mod._ANALYZER_REGISTRY.clear()
    _analyzer_registry_mod._ANALYZER_REGISTRY.update(saved_analyzers)
    _analyzer_registry_mod._discovered = saved_discovered
    _linker_registry_mod._LINKER_REGISTRY.clear()
    _linker_registry_mod._LINKER_REGISTRY.update(saved_linkers)


class TestPassClock:
    def test_sleep_is_wall_not_cpu(self) -> None:
        clock = PassClock()
        time.sleep(_SLEEP_S)
        cost = clock.read()
        assert cost.wall_ms >= int(_SLEEP_S * 1000)
        assert cost.cpu_ms < cost.wall_ms / 2

    def test_burn_is_cpu(self) -> None:
        clock = PassClock()
        _burn_cpu(_BURN_S)
        cost = clock.read()
        assert cost.cpu_ms >= int(_BURN_S * 1000)

    def test_cpu_rounds_up_so_a_measured_pass_never_reads_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A measured pass is never 0 ms of CPU, even on a clock too coarse to
        see it (0 would read as "cost nothing"); sub-ms CPU rounds up."""
        from hypergumbo_core import pass_clock as _pc

        readings = iter([1_000, 1_000])  # start, read: the clock did not move
        monkeypatch.setattr(_pc.time, "thread_time_ns", lambda: next(readings))
        assert PassClock().read().cpu_ms == 1

        readings = iter([0, 2_000_001])  # 2.000001 ms -> 3, not 2
        monkeypatch.setattr(_pc.time, "thread_time_ns", lambda: next(readings))
        assert PassClock().read().cpu_ms == 3

    def test_read_on_another_thread_refuses(self) -> None:
        """thread_time is per-thread: reading it elsewhere would silently
        report a different thread's CPU, so the clock refuses instead."""
        clock = PassClock()
        caught: list[BaseException] = []

        def _read_elsewhere() -> None:
            try:
                clock.read()
            except RuntimeError as exc:
                caught.append(exc)

        worker = threading.Thread(target=_read_elsewhere)
        worker.start()
        worker.join()
        assert len(caught) == 1
        assert "thread" in str(caught[0])


class TestPassCostStamp:
    def test_fills_unset_duration_and_sets_cpu(self) -> None:
        run = _run("p")
        PassCost(wall_ms=40, cpu_ms=7).stamp(run)
        assert run.duration_ms == 40
        assert run.cpu_ms == 7

    def test_keeps_a_body_measured_duration(self) -> None:
        """A pass body that timed itself keeps its own wall figure."""
        run = _run("p")
        run.duration_ms = 999
        PassCost(wall_ms=40, cpu_ms=7).stamp(run)
        assert run.duration_ms == 999
        assert run.cpu_ms == 7


class TestAnalysisRunSerializesCpuMs:
    def test_unmeasured_run_serializes_null_not_zero(self) -> None:
        """ABSENT != EMPTY: a run nobody measured says so with null."""
        run = _run("p")
        assert run.cpu_ms is None
        data = run.to_dict()
        assert "cpu_ms" in data
        assert data["cpu_ms"] is None

    def test_measured_run_serializes_its_value(self) -> None:
        run = _run("p")
        run.cpu_ms = 12
        assert run.to_dict()["cpu_ms"] == 12


class TestAnalyzerPoolMeasuresOnTheWorkerThread:
    def test_cpu_is_the_pass_own_and_wall_includes_waiting(
        self, tmp_path: Path, _isolated_registries
    ) -> None:
        @register_analyzer("burner-an", priority=10, language_state="no_language")
        def _burner(root, **kwargs):
            _burn_cpu(_BURN_S)
            return AnalysisResult(run=_run("burner-an"))

        @register_analyzer("sleeper-an", priority=20, language_state="no_language")
        def _sleeper(root, **kwargs):
            time.sleep(_SLEEP_S)
            return AnalysisResult(run=_run("sleeper-an"))

        (analysis_runs, *_rest) = _all_analyzers.run_all_analyzers(tmp_path)
        by_pass = {r["pass"]: r for r in analysis_runs}
        assert set(by_pass) == {"burner-an", "sleeper-an"}  # reach first

        # Read on the orchestrating thread (which only waits in as_completed)
        # this would be ~0: the burn is attributable only on the worker.
        assert by_pass["burner-an"]["cpu_ms"] >= int(_BURN_S * 1000)
        # The sleeper spent its wall time waiting: wall is large, CPU is not.
        assert by_pass["sleeper-an"]["duration_ms"] >= int(_SLEEP_S * 1000)
        assert by_pass["sleeper-an"]["cpu_ms"] < by_pass["sleeper-an"]["duration_ms"] / 2

    def test_runless_productive_result_is_still_costed(
        self, tmp_path: Path, _isolated_registries
    ) -> None:
        """WI-didag: the orchestrator synthesizes a run for a producer that
        emitted output without one. The cost measured on the worker reaches
        that synthesized run too, through the same chokepoint."""
        from hypergumbo_core.ir import Span, Symbol

        sym = Symbol(
            id="python:a.py:1-1:f:function", name="f", kind="function",
            language="python", path="a.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=1),
        )

        @register_analyzer("runless-an", priority=10, language_state="no_language")
        def _runless(root, **kwargs):
            _burn_cpu(_BURN_S)
            return AnalysisResult(run=None, symbols=[sym])

        (analysis_runs, *_rest) = _all_analyzers.run_all_analyzers(tmp_path)
        runs = [r for r in analysis_runs if r["pass"] == "runless-an"]
        assert len(runs) == 1
        assert runs[0]["cpu_ms"] >= int(_BURN_S * 1000)


class TestLinkerDispatchMeasuresOnTheWorkerThread:
    @staticmethod
    def _ctx() -> LinkerContext:
        return LinkerContext(repo_root=Path("/test"))

    @pytest.mark.parametrize("sibling_priority", [10, 20], ids=["parallel", "serial"])
    def test_cpu_is_the_pass_own_on_both_branches(
        self, sibling_priority: int, _isolated_registries
    ) -> None:
        """Same priority -> the cohort pool; distinct -> the serial branch."""

        @register_linker("burner-lk", priority=10)
        def _burner(ctx: LinkerContext) -> LinkerResult:
            _burn_cpu(_BURN_S)
            return LinkerResult(run=_run("burner-lk"))

        @register_linker("sleeper-lk", priority=sibling_priority)
        def _sleeper(ctx: LinkerContext) -> LinkerResult:
            time.sleep(_SLEEP_S)
            return LinkerResult(run=_run("sleeper-lk"))

        results = dict(run_all_linkers(self._ctx()))
        assert {"burner-lk", "sleeper-lk"} <= set(results)  # reach first
        burner = results["burner-lk"].run
        sleeper = results["sleeper-lk"].run
        assert burner.cpu_ms >= int(_BURN_S * 1000)
        assert sleeper.duration_ms >= int(_SLEEP_S * 1000)
        assert sleeper.cpu_ms < sleeper.duration_ms / 2


def test_every_serialized_pass_carries_a_measured_cpu_ms(tmp_path: Path) -> None:
    """Property over the production pipeline: no pass reaches analysis_runs
    without its own CPU cost. A dangling call makes the boundary-synthesis
    pass record a run, so a synthesis pass is in the population, not only
    analyzers and linkers."""
    from hypergumbo_core.cli import run_behavior_map

    (tmp_path / "app.py").write_text(
        "import os\n\ndef main():\n    os.getcwd()\n    helper()\n\n"
        "def helper():\n    pass\n"
    )
    out_path = tmp_path / "out.json"
    run_behavior_map(
        repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False
    )
    runs = json.loads(out_path.read_text())["analysis_runs"]
    passes = {r["pass"] for r in runs}
    assert "boundary_external_symbol_synthesis" in passes  # reach first
    unmeasured = [r["pass"] for r in runs if not isinstance(r.get("cpu_ms"), int)]
    assert unmeasured == []
    assert all(r["cpu_ms"] >= 1 for r in runs)
