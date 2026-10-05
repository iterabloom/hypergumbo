# SPDX-License-Identifier: MPL-2.0
"""Tests for `hypergumbo_tracker.priority_trend` and the `tracker priority-trend` subcommand.

The replay is checked on hand-built op logs whose day-by-day answer is worked out
in the test body; the SVG by parsing it as XML and reading the points back; the
subcommand end to end on a temporary tracker.
"""
from __future__ import annotations

import datetime
import json
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_tracker.cli import EXIT_SUCCESS, main
from hypergumbo_tracker.priority_trend import (
    TrendPoint,
    _y_ticks,
    item_timeline,
    priority_trend,
    render_svg,
)
from hypergumbo_tracker.store import Store

from helpers import make_test_config

RESOLVED = frozenset({"done", "wont_do", "deleted", "satisfied", "holding"})
SVG_NS = "{http://www.w3.org/2000/svg}"
D = datetime.date


def _create(at: str, status: str = "todo_hard", priority: int = 2, clock: int = 1) -> dict[str, Any]:
    return {"op": "create", "at": at, "by": "agent", "clock": clock, "nonce": f"c{clock}",
            "data": {"kind": "work_item", "title": "t", "status": status, "priority": priority}}


def _update(at: str, clock: int, **set_: Any) -> dict[str, Any]:
    return {"op": "update", "at": at, "by": "agent", "clock": clock, "nonce": f"u{clock}", "set": set_}


def _day(n: int) -> str:
    return f"2026-03-{n:02d}T12:00:00Z"


# ---------------------------------------------------------------------------
# one item's history
# ---------------------------------------------------------------------------


class TestItemTimeline:
    def test_change_points_are_the_end_of_day_state(self) -> None:
        ops = [
            _create(_day(1), priority=2),
            _update(_day(3), 2, priority=4),
            _update("2026-03-03T23:59:59Z", 3, priority=1),   # same day: the last op wins
            _update(_day(5), 4, status="done"),
        ]
        assert item_timeline(ops, "WI-a", RESOLVED) == [
            (D(2026, 3, 1), 2), (D(2026, 3, 3), 1), (D(2026, 3, 5), None),
        ]

    def test_a_day_that_changes_nothing_is_not_a_change_point(self) -> None:
        ops = [_create(_day(1)), {"op": "discuss", "at": _day(2), "by": "agent", "clock": 2,
                                  "nonce": "d", "message": "hi"}]
        assert item_timeline(ops, "WI-a", RESOLVED) == [(D(2026, 3, 1), 2)]

    def test_reopening_counts_again(self) -> None:
        ops = [_create(_day(1), status="done", priority=3), _update(_day(2), 2, status="todo_soft")]
        assert item_timeline(ops, "WI-a", RESOLVED) == [(D(2026, 3, 2), 3)]   # born resolved: not open yet

    def test_ops_before_the_create_are_not_an_item_yet(self) -> None:
        """Clock order and wall-clock order can disagree across branches: an update whose
        timestamp precedes every create op compiles to nothing on that day. Once the create
        is in, the update applies exactly as ``compile_ops`` (and so ``show``) applies it."""
        ops = [_update(_day(1), 1, priority=0), _create(_day(2), priority=3, clock=2)]
        assert item_timeline(ops, "WI-a", RESOLVED) == [(D(2026, 3, 2), 0)]

    def test_an_op_without_a_readable_time_applies_from_the_start(self) -> None:
        ops = [_create(_day(2), priority=2), _update("not-a-time", 1, priority=4)]
        assert item_timeline(ops, "WI-a", RESOLVED) == [(D(2026, 3, 2), 4)]

    def test_an_item_with_no_readable_time_has_no_history(self) -> None:
        assert item_timeline([{**_create(_day(1)), "at": ""}], "WI-a", RESOLVED) == []
        assert item_timeline([], "WI-a", RESOLVED) == []


# ---------------------------------------------------------------------------
# the series
# ---------------------------------------------------------------------------


class TestPriorityTrend:
    def _two_items(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "WI-a": [_create(_day(1), priority=2), _update(_day(4), 2, status="done")],
            "WI-b": [_create(_day(2), priority=4), _update(_day(3), 2, priority=1)],
        }

    def test_one_point_per_calendar_day_with_the_mean_of_the_open_items(self) -> None:
        assert priority_trend(self._two_items(), RESOLVED) == [
            TrendPoint(D(2026, 3, 1), 1, 2.0),
            TrendPoint(D(2026, 3, 2), 2, 3.0),
            TrendPoint(D(2026, 3, 3), 2, 1.5),
            TrendPoint(D(2026, 3, 4), 1, 1.0),
        ]

    def test_quiet_days_carry_the_state_forward(self) -> None:
        series = priority_trend({"WI-a": [_create(_day(1), priority=3), _update(_day(4), 2, priority=1)]}, RESOLVED)
        assert [(p.day.day, p.mean_priority) for p in series] == [(1, 3.0), (2, 3.0), (3, 3.0), (4, 1.0)]

    def test_a_day_with_nothing_open_has_no_mean(self) -> None:
        ops = {"WI-a": [_create(_day(1)), _update(_day(2), 2, status="wont_do"), _update(_day(3), 3, status="todo_hard")]}
        assert priority_trend(ops, RESOLVED)[1] == TrendPoint(D(2026, 3, 2), 0, None)

    def test_since_and_until_window_the_series_without_losing_earlier_state(self) -> None:
        series = priority_trend(self._two_items(), RESOLVED, since=D(2026, 3, 3), until=D(2026, 3, 6))
        assert series == [
            TrendPoint(D(2026, 3, 3), 2, 1.5),
            TrendPoint(D(2026, 3, 4), 1, 1.0),
            TrendPoint(D(2026, 3, 5), 1, 1.0),
            TrendPoint(D(2026, 3, 6), 1, 1.0),
        ]
        assert priority_trend(self._two_items(), RESOLVED, since=D(2026, 3, 5), until=D(2026, 3, 4)) == []

    def test_no_history_is_no_series(self) -> None:
        assert priority_trend({}, RESOLVED) == []
        assert priority_trend({"WI-a": []}, RESOLVED) == []

    def test_the_resolved_set_is_the_callers(self) -> None:
        ops = {"WI-a": [_create(_day(1), status="holding", priority=3)]}
        assert priority_trend(ops, RESOLVED) == []          # born resolved: never open
        assert priority_trend(ops, {"done"})[0] == TrendPoint(D(2026, 3, 1), 1, 3.0)


# ---------------------------------------------------------------------------
# the plot
# ---------------------------------------------------------------------------


def _svg_root(svg: str) -> ET.Element:
    return ET.fromstring(svg)  # noqa: S314 -- parses this module's own output, not untrusted input


def _circles(svg: str) -> list[ET.Element]:
    return list(_svg_root(svg).iter(f"{SVG_NS}circle"))


class TestRenderSvg:
    def test_one_circle_per_day_that_has_a_mean_with_its_numbers_on_hover(self) -> None:
        points = [TrendPoint(D(2026, 3, 1), 4, 2.5), TrendPoint(D(2026, 3, 2), 0, None),
                  TrendPoint(D(2026, 3, 3), 3, 3.0)]
        circles = _circles(render_svg(points, title="Mean priority"))
        assert len(circles) == 2
        tips = [c.find(f"{SVG_NS}title").text for c in circles]  # type: ignore[union-attr]
        assert tips == ["2026-03-01: mean 2.50 over 4 open", "2026-03-03: mean 3.00 over 3 open"]

    def test_time_runs_left_to_right_and_a_higher_mean_is_drawn_higher(self) -> None:
        points = [TrendPoint(D(2026, 3, 1), 1, 1.0), TrendPoint(D(2026, 3, 9), 1, 3.0)]
        a, b = _circles(render_svg(points, title="t"))
        assert float(a.get("cx", "")) < float(b.get("cx", ""))
        assert float(a.get("cy", "")) > float(b.get("cy", ""))   # SVG y grows downward

    def test_a_single_point_and_a_flat_series_still_draw(self) -> None:
        one = _circles(render_svg([TrendPoint(D(2026, 3, 1), 1, 2.0)], title="t"))
        assert len(one) == 1
        flat = [TrendPoint(D(2026, 3, d), 2, 4.0) for d in (1, 2, 3)]
        cys = {c.get("cy") for c in _circles(render_svg(flat, title="t"))}
        assert len(cys) == 1

    def test_the_title_is_escaped_and_the_axes_are_labelled(self) -> None:
        svg = render_svg([TrendPoint(D(2026, 3, 1), 1, 2.0)], title="a < b & c")
        texts = [t.text or "" for t in _svg_root(svg).iter(f"{SVG_NS}text")]
        assert "a < b & c" in texts
        assert any("0 = most urgent" in t for t in texts)
        assert "2026-03-01" in texts

    def test_gridlines_sit_on_round_values(self) -> None:
        assert _y_ticks(2.0, 3.25) == [2.0, 2.25, 2.5, 2.75, 3.0, 3.25]
        assert _y_ticks(1.25, 4.0) == [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
        assert _y_ticks(0.0, 4.0) == [0.0, 1.0, 2.0, 3.0, 4.0]

    def test_no_points_says_so(self) -> None:
        for points in ([], [TrendPoint(D(2026, 3, 1), 0, None)]):
            svg = render_svg(points, title="t")
            assert _circles(svg) == []
            assert "no open items" in svg


# ---------------------------------------------------------------------------
# Store.item_ops
# ---------------------------------------------------------------------------


class TestStoreItemOps:
    def test_returns_each_items_raw_log_and_skips_unreadable_files(
        self, ops_dir: Path, tmp_path: Path, mock_agent_uid: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hypergumbo_tracker import race_log, store as store_mod

        store = Store(ops_dir, config=make_test_config())
        item_id = store.add(kind="work_item", title="Good item", status="todo_hard", priority=1)
        (ops_dir / ".WI-corrupt.ops").write_text("{{bad")
        got = dict(store.item_ops())
        assert list(got) == [item_id]
        assert got[item_id][0]["op"] == "create"

        log_path = tmp_path / "race_log.jsonl"
        race_log.configure_race_log(log_path)
        try:
            def boom(path: Path) -> list[dict[str, Any]]:
                raise OSError(5, "synthetic EIO")

            monkeypatch.setattr(store_mod, "_parse_ops_file", boom)
            assert store.item_ops() == []
        finally:
            race_log.configure_race_log(None)
        records = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert records and all(r["event"] == "compile_suppressed" for r in records)


# ---------------------------------------------------------------------------
# the subcommand
# ---------------------------------------------------------------------------


def _setup_tracker(tmp_path: Path) -> Path:
    tracker_root = tmp_path / ".agent"
    (tracker_root / "tracker" / ".ops").mkdir(parents=True)
    (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
    (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)
    return tracker_root


def _write_item(ops_dir: Path, item_id: str, at: str, priority: int, extra: str = "") -> None:
    (ops_dir / f".{item_id}.ops").write_text(textwrap.dedent(f"""\
        - op: create
          at: "{at}"
          by: agent
          actor: test_agent
          clock: 1
          nonce: a1b2
          data:
            kind: work_item
            title: "item {item_id}"
            status: todo_hard
            priority: {priority}
    """) + extra)


def _closed_on(at: str) -> str:
    return textwrap.dedent(f"""\
        - op: update
          at: "{at}"
          by: agent
          actor: test_agent
          clock: 2
          nonce: c3d4
          set:
            status: done
    """)


def _populate(root: Path) -> None:
    ops = root / "tracker" / ".ops"
    _write_item(ops, "WI-aaa", _day(1), 2, _closed_on(_day(3)))
    _write_item(ops, "WI-bbb", _day(2), 4)
    _write_item(root / "tracker-workspace" / ".ops", "WI-ccc", _day(2), 0)


class TestPriorityTrendCommand:
    def test_writes_the_svg_and_summarises(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None,
    ) -> None:
        root = _setup_tracker(tmp_path)
        _populate(root)
        out = tmp_path / "trend.svg"
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "priority-trend", "--out", str(out)])
        assert exc.value.code == EXIT_SUCCESS
        tips = [c.find(f"{SVG_NS}title").text for c in _circles(out.read_text())]  # type: ignore[union-attr]
        assert tips == ["2026-03-01: mean 2.00 over 1 open", "2026-03-02: mean 2.00 over 3 open",
                        "2026-03-03: mean 2.00 over 2 open"]
        text = capsys.readouterr().out
        assert str(out) in text
        assert "3 day(s), 2026-03-01 to 2026-03-03" in text
        assert "2.00 (1 open) on 2026-03-01" in text and "2.00 (2 open) on 2026-03-03" in text

    def test_json_prints_the_series_and_writes_svg_only_when_asked(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = _setup_tracker(tmp_path)
        _populate(root)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "--json", "priority-trend", "--since", "2026-03-02"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["svg"] is None
        assert data["points"] == [
            {"day": "2026-03-02", "open": 3, "mean_priority": 2.0},
            {"day": "2026-03-03", "open": 2, "mean_priority": 2.0},
        ]
        assert set(data["resolved_statuses"]) == {"done", "wont_do", "deleted"}   # the default config's
        assert list(tmp_path.glob("*.svg")) == []
        out = tmp_path / "x.svg"
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(root), "--json", "priority-trend", "--out", str(out)])
        assert json.loads(capsys.readouterr().out)["svg"] == str(out)
        assert len(_circles(out.read_text())) == 3

    def test_default_out_is_a_file_in_the_working_directory(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = _setup_tracker(tmp_path)
        _populate(root)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(root), "priority-trend"])
        assert (tmp_path / "tracker-priority-trend.svg").exists()

    def test_an_empty_window_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None,
    ) -> None:
        root = _setup_tracker(tmp_path)
        _populate(root)
        out = tmp_path / "trend.svg"
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "priority-trend", "--since", "2027-01-01", "--out", str(out)])
        assert exc.value.code == EXIT_SUCCESS
        assert "no days with history" in capsys.readouterr().out
        assert "no open items" in out.read_text()

    def test_a_window_where_nothing_is_open_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None,
    ) -> None:
        root = _setup_tracker(tmp_path)
        _write_item(root / "tracker" / ".ops", "WI-aaa", _day(1), 2, _closed_on(_day(3)))
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(root), "priority-trend", "--since", "2026-03-03",
                  "--out", str(tmp_path / "t.svg")])
        assert "1 day(s), 2026-03-03 to 2026-03-03; no open items on any day" in capsys.readouterr().out

    def test_a_malformed_date_is_a_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None,
    ) -> None:
        root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "priority-trend", "--since", "March"])
        assert exc.value.code == 2
        assert "YYYY-MM-DD" in capsys.readouterr().err

    def test_workspace_scope_ignores_canonical_items(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None,
    ) -> None:
        import yaml

        root = _setup_tracker(tmp_path)
        _populate(root)
        (root / "tracker" / "config.yaml").write_text(yaml.dump({
            "kinds": {"work_item": {"prefix": "WI"}},
            "statuses": ["todo_hard", "todo_soft", "done", "wont_do"],
            "stop_hook": {"blocking_statuses": ["todo_hard", "todo_soft"], "resolved_statuses": ["done", "wont_do"],
                          "scope": "workspace"},
        }))
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(root), "--json", "priority-trend"])
        data = json.loads(capsys.readouterr().out)
        assert data["points"] == [{"day": "2026-03-02", "open": 1, "mean_priority": 0.0}]
