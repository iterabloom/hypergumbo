# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/all_analyzers.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/registry.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/ranking.py
"""The order passes' results are collected in is a function of the registry.

Analyzers run in one thread pool and the linkers of one priority in another.
Their results were gathered with ``as_completed``, so the symbol and edge
lists were extended in the order the threads happened to finish: two runs of
one tree gave two list orders, and every consumer that keeps "the first" or
"the last" holder of a name answered differently between them (a Django route
bound a Python view in one run and a same-named JavaScript class in the next).

The rule these tests pin: results are collected in the order the passes were
submitted, and that order is the registry's, which is total (priority, then
name) so it does not depend on the order packages were discovered in either.

Each ordering test makes the FIRST pass in registry order finish LAST (it
waits for the others), and asserts that this really happened before it reads
the collected order: a test in which the passes finish in registry order
anyway would pass with the old loop too.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from hypergumbo_core.analyze import all_analyzers as _all_analyzers
from hypergumbo_core.analyze import registry as _analyzer_registry_mod
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import get_all_analyzers, register_analyzer
from hypergumbo_core.ir import PASS_VERSION, AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.linkers import registry as _linker_registry_mod
from hypergumbo_core.linkers.registry import (
    LinkerContext,
    LinkerResult,
    get_all_linkers,
    register_linker,
    run_all_linkers,
)


@pytest.fixture(autouse=True)
def _isolate_registries(monkeypatch):
    """Each test sees only the passes it registers; the originals come back.

    The pools are sized by ``os.cpu_count()``: on a one-core machine the
    passes would run one after another in submission order and no test here
    could tell the two loops apart, so the count is pinned.
    """
    saved_analyzers = dict(_analyzer_registry_mod._ANALYZER_REGISTRY)
    saved_discovered = _analyzer_registry_mod._discovered
    saved_linkers = dict(_linker_registry_mod._LINKER_REGISTRY)
    _analyzer_registry_mod._ANALYZER_REGISTRY.clear()
    _analyzer_registry_mod._discovered = True
    _linker_registry_mod._LINKER_REGISTRY.clear()
    monkeypatch.setattr(_all_analyzers.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(_linker_registry_mod.os, "cpu_count", lambda: 4)
    yield
    _analyzer_registry_mod._ANALYZER_REGISTRY.clear()
    _analyzer_registry_mod._ANALYZER_REGISTRY.update(saved_analyzers)
    _analyzer_registry_mod._discovered = saved_discovered
    _linker_registry_mod._LINKER_REGISTRY.clear()
    _linker_registry_mod._LINKER_REGISTRY.update(saved_linkers)


def _symbol(name: str) -> Symbol:
    return Symbol(
        id=f"python:{name}.py:1-2:{name}:function",
        name=name,
        kind="function",
        language="python",
        path=f"{name}.py",
        span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
        origin="test",
        origin_run_id="test-run",
    )


def _analysis_result(name: str) -> AnalysisResult:
    run = AnalysisRun.create(pass_id=f"{name}-v1", version=PASS_VERSION)
    return AnalysisResult(
        run=run, symbols=[_symbol(name)], edges=[], usage_contexts=[],
        skipped=False,
    )


class _FinishLast:
    """Lets the first pass in registry order return only after the others.

    ``others_done`` is set by the last of the other passes; ``waited`` records
    that the first pass saw it set, i.e. that it really did finish last.
    """

    def __init__(self, others: int) -> None:
        self._remaining = others
        self._lock = threading.Lock()
        self.others_done = threading.Event()
        self.finished_last = False

    def other_finished(self) -> None:
        with self._lock:
            self._remaining -= 1
            if self._remaining == 0:
                self.others_done.set()

    def wait_for_others(self) -> None:
        self.finished_last = self.others_done.wait(timeout=30)


class TestAnalyzerCollectionOrder:
    def test_symbols_are_collected_in_registry_order_not_completion_order(
        self, tmp_path: Path,
    ) -> None:
        gate = _FinishLast(others=2)

        @register_analyzer("a-first", priority=10, language_state="no_language")
        def _first(root, **kwargs):
            gate.wait_for_others()
            return _analysis_result("first")

        @register_analyzer("b-second", priority=10, language_state="no_language")
        def _second(root, **kwargs):
            try:
                return _analysis_result("second")
            finally:
                gate.other_finished()

        @register_analyzer("c-third", priority=20, language_state="no_language")
        def _third(root, **kwargs):
            try:
                return _analysis_result("third")
            finally:
                gate.other_finished()

        runs, symbols, _edges, _ucs, _limits, _cap, _man = (
            _all_analyzers.run_all_analyzers(tmp_path)
        )

        assert gate.finished_last, "the first analyzer did not finish last"
        names = [s.name for s in symbols if s.kind == "function"]
        assert names == ["first", "second", "third"]
        passes = [r["pass"] for r in runs if r["pass"].endswith("-v1")]
        assert passes == ["first-v1", "second-v1", "third-v1"]

    def test_registry_order_is_priority_then_name(self) -> None:
        """Equal priorities are ordered by name, not by registration order
        (which is the order packages were discovered in)."""

        @register_analyzer("zeta", priority=10, language_state="no_language")
        def _zeta(root, **kwargs):  # pragma: no cover - never dispatched
            return None

        @register_analyzer("alpha", priority=10, language_state="no_language")
        def _alpha(root, **kwargs):  # pragma: no cover - never dispatched
            return None

        @register_analyzer("omega", priority=5, language_state="no_language")
        def _omega(root, **kwargs):  # pragma: no cover - never dispatched
            return None

        assert [a.name for a in get_all_analyzers()] == ["omega", "alpha", "zeta"]


class TestLinkerCollectionOrder:
    @staticmethod
    def _result(name: str) -> LinkerResult:
        run = AnalysisRun.create(pass_id=f"{name}-v1", version=PASS_VERSION)
        symbol = _symbol(name)
        edge = Edge.create(
            src=symbol.id, dst=f"python:{name}.py:9-9:callee:function",
            edge_type="calls", line=1, origin="test",
            origin_run_id=run.execution_id,
        )
        return LinkerResult(symbols=[symbol], edges=[edge], run=run)

    def test_one_priority_group_is_collected_in_registry_order(self) -> None:
        gate = _FinishLast(others=2)
        seen_by_later: dict[str, list[str]] = {}

        @register_linker("a-first", priority=30)
        def _first(ctx: LinkerContext) -> LinkerResult:
            gate.wait_for_others()
            return self._result("first")

        @register_linker("b-second", priority=30)
        def _second(ctx: LinkerContext) -> LinkerResult:
            try:
                return self._result("second")
            finally:
                gate.other_finished()

        @register_linker("c-third", priority=30)
        def _third(ctx: LinkerContext) -> LinkerResult:
            try:
                return self._result("third")
            finally:
                gate.other_finished()

        @register_linker("later", priority=40)
        def _later(ctx: LinkerContext) -> LinkerResult:
            seen_by_later["symbols"] = [s.name for s in ctx.symbols]
            seen_by_later["edges"] = [e.src for e in ctx.edges]
            return LinkerResult()

        results = run_all_linkers(LinkerContext(repo_root=Path("/test")))

        assert gate.finished_last, "the first linker did not finish last"
        assert [name for name, _ in results][:3] == [
            "a-first", "b-second", "c-third",
        ]
        # What the next priority group is handed: the same order.
        assert seen_by_later["symbols"] == ["first", "second", "third"]
        assert [src.split(":")[3] for src in seen_by_later["edges"]] == [
            "first", "second", "third",
        ]

    def test_registry_order_is_priority_then_name(self) -> None:
        @register_linker("zeta", priority=30)
        def _zeta(ctx: LinkerContext) -> LinkerResult:  # pragma: no cover
            return LinkerResult()

        @register_linker("alpha", priority=30)
        def _alpha(ctx: LinkerContext) -> LinkerResult:  # pragma: no cover
            return LinkerResult()

        @register_linker("omega", priority=5)
        def _omega(ctx: LinkerContext) -> LinkerResult:  # pragma: no cover
            return LinkerResult()

        assert [lnk.name for lnk in get_all_linkers()] == ["omega", "alpha", "zeta"]


def test_file_centrality_scores_are_keyed_in_input_order(
    tmp_path: Path, monkeypatch,
) -> None:
    """The per-file centrality dict is written into the behavior map
    (``sketch_precomputed.additional_file_centrality_scores``), so its key
    order is output. It follows the files handed in, whichever file's scan
    finishes first."""
    import concurrent.futures

    from hypergumbo_core.ranking import _compute_centrality_with_python

    files = []
    for i in range(6):
        f = tmp_path / f"f{i}.txt"
        f.write_text(f"alpha beta {i}\n")
        files.append(f)

    real_as_completed = concurrent.futures.as_completed

    def _reversed_as_completed(futures, *args, **kwargs):
        """Completion order at its least convenient: the reverse of
        submission. A loop that does not ask for completion order never
        calls this."""
        done = list(real_as_completed(futures, *args, **kwargs))
        return iter(sorted(done, key=list(futures).index, reverse=True))

    monkeypatch.setattr(concurrent.futures, "as_completed", _reversed_as_completed)

    result = _compute_centrality_with_python(
        files, {"alpha": 3, "beta": 2}, max_file_size=1024, progress_callback=None,
    )

    assert list(result.normalized_scores) == files
    assert list(result.symbols_per_file) == files
    assert all(score > 0 for score in result.normalized_scores.values())
