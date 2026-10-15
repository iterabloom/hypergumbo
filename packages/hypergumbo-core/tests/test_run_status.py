# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for run_status: the phase line, the heartbeat and the run summary.

Stacks are exercised with duck-typed fake frames rather than real ones,
because ``abridged_stack`` keeps only frames whose module is a hypergumbo
package, and this test module is not one. A fake frame carries exactly
the four attributes the walk reads (``f_globals``, ``f_code``, ``f_lineno``,
``f_back``), so each test states the stack it means.
"""

from __future__ import annotations

import io
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from hypergumbo_core import run_status
from hypergumbo_core.run_status import (
    RunStatus, abridged_stack, format_duration,
)


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _frame(module: str, qualname: str, line: int, back: Optional[Any] = None) -> Any:
    return SimpleNamespace(
        f_globals={"__name__": module},
        f_code=SimpleNamespace(co_qualname=qualname, co_name=qualname.rsplit(".", 1)[-1]),
        f_lineno=line,
        f_back=back,
    )


def _stack(*specs: tuple[str, str, int]) -> Any:
    """Build a frame chain from outermost to innermost; return the innermost."""
    frame = None
    for module, qualname, line in specs:
        frame = _frame(module, qualname, line, frame)
    return frame


def _wait_for(predicate: Any, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HG_HEARTBEAT", raising=False)


@pytest.mark.parametrize("secs, text", [
    (0, "0s"), (59.9, "59s"), (60, "1m00s"), (192, "3m12s"),
    (3599, "59m59s"), (3720, "1h02m"),
])
def test_format_duration(secs: float, text: str) -> None:
    assert format_duration(secs) == text


def test_abridged_stack_keeps_hypergumbo_frames_outermost_first() -> None:
    frame = _stack(
        ("__main__", "main", 1),
        ("hypergumbo_core.cli", "run_survey", 100),
        ("concurrent.futures.thread", "_worker", 5),
        ("hypergumbo_core.linkers.registry", "run_all_linkers", 200),
        ("hypergumbo_lang_mainstream.py", "Analyzer.<locals>.visit", 300),
    )
    assert abridged_stack(frame, 4) == (
        "cli.run_survey > registry.run_all_linkers > py.Analyzer.visit:300"
    )


def test_abridged_stack_drops_wrappers_and_its_own_module() -> None:
    frame = _stack(
        ("hypergumbo_core.cli", "_releases_file_index.<locals>.wrapper", 1),
        ("hypergumbo_core.run_status", "reports_run.<locals>.wrapper", 2),
        ("hypergumbo_core.cli", "run_survey", 3),
    )
    assert abridged_stack(frame, 4) == "cli.run_survey:3"


def test_abridged_stack_collapses_recursion_and_truncates() -> None:
    frame = _stack(
        ("hypergumbo_core.a", "f1", 1),
        ("hypergumbo_core.a", "f2", 2),
        ("hypergumbo_core.a", "walk", 3),
        ("hypergumbo_core.a", "walk", 4),
        ("hypergumbo_core.a", "walk", 5),
        ("hypergumbo_core.a", "leaf", 6),
    )
    assert abridged_stack(frame, 2) == "… > a.walk (recursion x3) > a.leaf:6"
    assert abridged_stack(frame, 4) == "a.f1 > a.f2 > a.walk (recursion x3) > a.leaf:6"
    assert abridged_stack(frame, 1, elide=False) == "a.leaf:6"


def test_abridged_stack_innermost_recursion_keeps_line_number_first() -> None:
    frame = _stack(
        ("hypergumbo_lang_mainstream.py", "_format_annotation", 1170),
        ("hypergumbo_lang_mainstream.py", "_format_annotation", 1166),
    )
    assert abridged_stack(frame, 1, elide=False) == (
        "py._format_annotation:1166 (recursion x2)"
    )


def test_abridged_stack_without_qualname_falls_back_to_name() -> None:
    frame = SimpleNamespace(
        f_globals={"__name__": "hypergumbo_core.x"},
        f_code=SimpleNamespace(co_name="g"), f_lineno=9, f_back=None,
    )
    assert abridged_stack(frame, 4) == "x.g:9"


def test_abridged_stack_empty_for_idle_worker() -> None:
    frame = _stack(("threading", "run", 1), ("queue", "get", 2))
    assert abridged_stack(frame, 4) == ""


@pytest.mark.parametrize("raw, expected", [
    (None, (30.0, False)), ("5", (5.0, True)), ("0", (0.0, True)),
    ("-3", (0.0, True)), ("soon", (30.0, False)),
])
def test_heartbeat_interval(monkeypatch: pytest.MonkeyPatch, raw: Optional[str],
                            expected: tuple[float, bool]) -> None:
    if raw is not None:
        monkeypatch.setenv("HG_HEARTBEAT", raw)
    assert run_status._heartbeat_interval() == expected


def test_no_run_without_progress_or_without_a_tty() -> None:
    status = RunStatus(_Tty())
    assert status.begin(False) is False
    plain = RunStatus(io.StringIO())
    assert plain.begin(True) is False
    plain.phase("x")
    plain.item("y", 1, 2)
    plain.end(False)
    assert plain._stream_override.getvalue() == ""


def test_live_run_draws_phases_counts_and_one_complete_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HG_HEARTBEAT", "0")
    out = _Tty()
    status = RunStatus(out)
    token = status.begin(True)
    status.phase("Running linkers")
    status.phase("Running linkers")  # repeat: no new phase
    status.item("Embedding files", 41, 53)
    inner = status.begin(False)  # nested: joins regardless of progress
    status.phase("Writing output")
    status.end(inner)
    assert "Complete" not in out.getvalue()
    status.end(token)
    text = out.getvalue()
    assert "\r[    0s] Running linkers (0s)" in text
    assert "· Embedding files 41/53" in text
    assert text.count("Complete in 0s") == 1
    assert "Slowest phases" not in text  # a short run gets no summary
    assert "%" not in text and "ETA" not in text
    assert [label for label, _ in status._phases] == ["Running linkers", "Writing output"]


def test_slowest_phases_totals_repeated_labels() -> None:
    status = RunStatus(_Tty())
    status._phases = [("A", 0.0), ("B", 10.0), ("A", 100.0)]
    assert status.slowest_phases(400.0) == "A 5m10s, B 1m30s"


def test_summary_printed_for_a_long_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HG_HEARTBEAT", "0")
    out = _Tty()
    status = RunStatus(out)
    token = status.begin(True)
    status.phase("Running analyzers")
    status._start -= 120
    status._phases = [("Running analyzers", status._start)]
    status.end(token)
    assert "Complete in 2m00s" in out.getvalue()
    assert "Slowest phases: Running analyzers 2m00s" in out.getvalue()


def test_heartbeat_on_non_tty_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HG_HEARTBEAT", "0.01")
    out = io.StringIO()
    status = RunStatus(out)
    token = status.begin(True)
    assert token is True
    status.phase("Running analyzers")
    assert _wait_for(lambda: "Running analyzers" in out.getvalue())
    status.end(token)
    lines = out.getvalue().splitlines()
    assert "\r" not in out.getvalue()  # no redraws on a captured stream
    assert lines[0].startswith("[") and "Running analyzers" in lines[0]
    assert lines[-1] == "Complete in 0s"
    assert status._thread is None


def test_heartbeat_line_reports_owner_stack_and_one_line_per_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = RunStatus(_Tty())
    status._start = time.monotonic()
    status._phases = [("Running analyzers", status._start)]
    status._owner_ident = 1
    me = threading.get_ident()
    analyzer = _stack(
        ("concurrent.futures.thread", "_worker", 1),
        ("hypergumbo_core.analyze.all_analyzers", "_run_costed", 2),
        ("hypergumbo_core.analyze.base", "TreeSitterAnalyzer._analyze_body", 4570),
    )
    frames = {
        1: _stack(("hypergumbo_core.analyze.all_analyzers", "run_all_analyzers", 442)),
        2: analyzer,
        3: _stack(("hypergumbo_core.linkers.cgo", "link", 7)),
        4: _stack(("hypergumbo_core.ranking", "score", 9)),  # unnamed thread
        5: _stack(("threading", "wait", 1)),  # idle worker: omitted
        me: _stack(("hypergumbo_core.x", "should_not_appear", 1)),
    }
    monkeypatch.setattr(sys, "_current_frames", lambda: frames)
    monkeypatch.setattr(run_status, "_WORKER_PASSES", {2: "javascript", 3: "cgo-linker"})
    lines = status.heartbeat_line(time.monotonic()).split("\n")
    assert lines == [
        "[    0s] Running analyzers (0s) | main: all_analyzers.run_all_analyzers:442",
        "           cgo-linker  cgo.link:7",
        "           javascript  base.TreeSitterAnalyzer._analyze_body:4570",
        "           ranking     ranking.score:9",
    ]


def test_heartbeat_names_the_main_pass_and_caps_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = RunStatus(_Tty())
    status._start = time.monotonic()
    status._owner_ident = 1
    frames = {1: _stack(("hypergumbo_core.linkers.websocket", "link", 3))}
    for ident in range(10, 20):
        frames[ident] = _stack(("hypergumbo_core.p", f"f{ident}", ident))
    monkeypatch.setattr(sys, "_current_frames", lambda: frames)
    monkeypatch.setattr(run_status, "_WORKER_PASSES", {1: "websocket-linker"})
    lines = status.heartbeat_line(time.monotonic()).split("\n")
    assert lines[0] == "[    0s] | main (websocket-linker): websocket.link:3"
    assert len(lines) == 1 + 8 + 1
    assert lines[-1] == "           +2 more"


def test_worker_pass_names_the_thread_and_restores_on_exit() -> None:
    ident = threading.get_ident()
    with run_status.worker_pass("outer"):
        assert run_status._WORKER_PASSES[ident] == "outer"
        with run_status.worker_pass("inner"):
            assert run_status._WORKER_PASSES[ident] == "inner"
        assert run_status._WORKER_PASSES[ident] == "outer"
    assert ident not in run_status._WORKER_PASSES


def test_heartbeat_line_when_owner_has_no_hypergumbo_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = RunStatus(_Tty())
    status._start = time.monotonic()
    status._owner_ident = 1
    monkeypatch.setattr(sys, "_current_frames", lambda: {1: _stack(("threading", "x", 1))})
    assert status.heartbeat_line(time.monotonic()) == "[    0s]"
    monkeypatch.setattr(sys, "_current_frames", lambda: {})
    assert status.heartbeat_line(time.monotonic()) == "[    0s]"


def test_live_heartbeat_redraws_the_phase_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HG_HEARTBEAT", "0.01")
    out = _Tty()
    status = RunStatus(out)
    token = status.begin(True)
    status.phase("Running linkers with a long label")
    assert _wait_for(lambda: out.getvalue().count("\n") >= 1)
    status.end(token)
    beat = out.getvalue().split("\n")[0]
    # The beat overwrote the phase line in place, padded to its full width.
    assert beat.startswith("\r[    0s] Running linkers with a long label")


def test_live_multi_line_heartbeat_pads_only_its_first_line() -> None:
    out = _Tty()
    status = RunStatus(out)
    status._live = True
    status._stream = out
    status._drawn = 30
    status._write_line("short\n           worker  a.b:1")
    assert out.getvalue() == "\rshort" + " " * 25 + "\n           worker  a.b:1\n"


def test_other_stderr_writes_clear_the_phase_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HG_HEARTBEAT", "0")
    out = _Tty()
    monkeypatch.setattr(sys, "stderr", out)
    status = RunStatus()
    token = status.begin(True)
    assert sys.stderr is not out
    assert sys.stderr.isatty() is True  # delegated to the real stream
    status.phase("Running linkers")
    drawn = len(out.getvalue()) - 1
    print("UserWarning: something", file=sys.stderr)
    status.end(token)
    assert sys.stderr is out
    text = out.getvalue()
    assert "\r" + " " * drawn + "\rUserWarning: something\n" in text
    print("after", file=sys.stderr)
    assert out.getvalue().endswith("after\n")


def test_reports_run_ends_the_run_when_the_call_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HG_HEARTBEAT", "0")
    status = RunStatus(_Tty())
    monkeypatch.setattr(run_status, "STATUS", status)

    @run_status.reports_run
    def pipeline(repo: str, progress: bool = True) -> str:
        run_status.phase("Indexing files")
        run_status.item("files", 1, 1)
        if repo == "bad":
            raise RuntimeError("boom")
        return repo

    with pytest.raises(RuntimeError):
        pipeline("bad")
    assert status._depth == 0
    assert pipeline("ok", False) == "ok"
    assert pipeline("ok", progress=True) == "ok"
    assert status._depth == 0
    assert status._stream_override.getvalue().count("Complete in") == 2


def test_sketch_progress_reports_into_run_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from hypergumbo_core.sketch import SketchProgress

    monkeypatch.setenv("HG_HEARTBEAT", "0")
    status = RunStatus(_Tty())
    monkeypatch.setattr(run_status, "STATUS", status)
    token = status.begin(True)
    prog = SketchProgress()
    prog.start_phase("config")
    prog.start_phase("analysis", cached=True)
    prog.start_phase("structure", cached=True)  # no cached text: plain name
    prog.update_item_progress("Embedding files", 2, 5)
    prog.complete_phase("structure")
    prog.finish()
    prog.disable()
    prog.start_phase("format")
    prog.update_item_progress("ignored", 1, 1)
    status.end(token)
    assert [label for label, _ in status._phases] == [
        "Sketch: Extracting config",
        "Sketch: Loading cached analysis",
        "Sketch: Building structure",
    ]
    text = status._stream_override.getvalue()
    assert "· Embedding files 2/5" in text
    assert "ignored" not in text
