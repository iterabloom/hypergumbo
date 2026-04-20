# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the ``awaits_bakeoff_validation`` stop-hook nudge (WI-dolil slice 3).

The nudge appends a short markdown section to the stop hook guidance file
when (a) the count of work items carrying the ``awaits_bakeoff_validation``
tag and a blocking status reaches a threshold, AND (b) the most recent
DEEP bakeoff cycle completed longer than a configured window ago (or
has never run).

These tests exercise the pure-Python helper at
``.agent/hooks/_shared/awaits_bakeoff_nudge.py``. The helper is invoked
by ``stop_logic.sh`` and its stdout is appended to the guidance file.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import time
from pathlib import Path
from types import ModuleType

import pytest


REPO_ROOT = Path(__file__).parent.parent
NUDGE_SCRIPT = REPO_ROOT / ".agent" / "hooks" / "_shared" / "awaits_bakeoff_nudge.py"


def _import_nudge() -> ModuleType:
    spec = importlib.util.spec_from_file_location("awaits_bakeoff_nudge", str(NUDGE_SCRIPT))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- compute_nudge pure function ---


def test_compute_nudge_below_threshold_returns_empty() -> None:
    mod = _import_nudge()
    out = mod.compute_nudge(
        count=4,
        last_deep_mtime=time.time() - (100 * 3600),  # well past the window
        now_epoch=time.time(),
        threshold=5,
        stale_hours=72,
    )
    assert out == ""


def test_compute_nudge_recent_cycle_returns_empty() -> None:
    mod = _import_nudge()
    now = time.time()
    out = mod.compute_nudge(
        count=10,
        last_deep_mtime=now - (5 * 3600),  # 5h ago, well under 72h window
        now_epoch=now,
        threshold=5,
        stale_hours=72,
    )
    assert out == ""


def test_compute_nudge_stale_cycle_fires() -> None:
    mod = _import_nudge()
    now = time.time()
    age_hours = 100
    out = mod.compute_nudge(
        count=9,
        last_deep_mtime=now - (age_hours * 3600),
        now_epoch=now,
        threshold=5,
        stale_hours=72,
    )
    assert out
    assert "AWAITS_BAKEOFF_VALIDATION BACKLOG" in out
    assert "9" in out
    assert "100" in out  # age shown
    assert "72" in out   # window shown
    assert "threshold: 5" in out
    assert "./scripts/bakeoff-deep cycle" in out


def test_compute_nudge_no_cycle_ever_fires() -> None:
    mod = _import_nudge()
    out = mod.compute_nudge(
        count=6,
        last_deep_mtime=None,
        now_epoch=time.time(),
        threshold=5,
        stale_hours=72,
    )
    assert out
    assert "never" in out.lower()
    assert "AWAITS_BAKEOFF_VALIDATION BACKLOG" in out


def test_compute_nudge_exact_threshold_fires() -> None:
    mod = _import_nudge()
    now = time.time()
    out = mod.compute_nudge(
        count=5,
        last_deep_mtime=now - (73 * 3600),
        now_epoch=now,
        threshold=5,
        stale_hours=72,
    )
    assert out


def test_compute_nudge_exact_window_edge_is_not_stale() -> None:
    """An age of exactly stale_hours is NOT stale yet (strict >)."""
    mod = _import_nudge()
    now = time.time()
    out = mod.compute_nudge(
        count=10,
        last_deep_mtime=now - (72 * 3600),
        now_epoch=now,
        threshold=5,
        stale_hours=72,
    )
    assert out == ""


# --- load_nudge_config ---


def test_load_nudge_config_defaults_when_file_missing(tmp_path: Path) -> None:
    mod = _import_nudge()
    cfg = mod.load_nudge_config(tmp_path / "missing.yaml")
    assert cfg["threshold"] == mod.DEFAULT_THRESHOLD
    assert cfg["stale_cycle_hours"] == mod.DEFAULT_STALE_HOURS
    assert set(cfg["blocking_statuses"]) == set(mod.DEFAULT_BLOCKING_STATUSES)


def test_load_nudge_config_defaults_when_keys_absent(tmp_path: Path) -> None:
    mod = _import_nudge()
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("stop_hook:\n  blocking_statuses:\n    - todo_hard\n")
    cfg = mod.load_nudge_config(cfg_path)
    assert cfg["threshold"] == mod.DEFAULT_THRESHOLD
    assert cfg["stale_cycle_hours"] == mod.DEFAULT_STALE_HOURS
    # blocking_statuses should be taken from config, not default.
    assert cfg["blocking_statuses"] == ["todo_hard"]


def test_load_nudge_config_reads_overrides(tmp_path: Path) -> None:
    mod = _import_nudge()
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "stop_hook:\n"
        "  blocking_statuses:\n"
        "    - todo_hard\n"
        "    - todo_soft\n"
        "  awaits_bakeoff_validation_nudge:\n"
        "    threshold: 3\n"
        "    stale_cycle_hours: 48\n"
    )
    cfg = mod.load_nudge_config(cfg_path)
    assert cfg["threshold"] == 3
    assert cfg["stale_cycle_hours"] == 48
    assert cfg["blocking_statuses"] == ["todo_hard", "todo_soft"]


def test_load_nudge_config_malformed_yaml_returns_defaults(tmp_path: Path) -> None:
    mod = _import_nudge()
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(": : not valid yaml : :\n  -\n")
    cfg = mod.load_nudge_config(cfg_path)
    assert cfg["threshold"] == mod.DEFAULT_THRESHOLD


# --- find_latest_deep_cycle_mtime ---


def test_find_latest_deep_cycle_mtime_returns_none_when_dir_missing(tmp_path: Path) -> None:
    mod = _import_nudge()
    assert mod.find_latest_deep_cycle_mtime(tmp_path / "nope") is None


def test_find_latest_deep_cycle_mtime_returns_none_when_empty(tmp_path: Path) -> None:
    mod = _import_nudge()
    assert mod.find_latest_deep_cycle_mtime(tmp_path) is None


def test_find_latest_deep_cycle_mtime_ignores_broad_sessions(tmp_path: Path) -> None:
    mod = _import_nudge()
    broad_state = tmp_path / "broad-20260101-000000" / "state.json"
    broad_state.parent.mkdir(parents=True)
    broad_state.write_text("{}")
    assert mod.find_latest_deep_cycle_mtime(tmp_path) is None


def test_find_latest_deep_cycle_mtime_picks_newest_deep(tmp_path: Path) -> None:
    mod = _import_nudge()
    older = tmp_path / "deep-20260101-000000" / "state.json"
    newer = tmp_path / "deep-20260201-000000" / "state.json"
    for p in (older, newer):
        p.parent.mkdir(parents=True)
        p.write_text("{}")
    import os
    old_ts = time.time() - 7 * 24 * 3600
    new_ts = time.time() - 1 * 3600
    os.utime(older, (old_ts, old_ts))
    os.utime(newer, (new_ts, new_ts))
    got = mod.find_latest_deep_cycle_mtime(tmp_path)
    assert got is not None
    assert abs(got - new_ts) < 2


def test_find_latest_deep_cycle_mtime_skips_dirs_without_state(tmp_path: Path) -> None:
    mod = _import_nudge()
    (tmp_path / "deep-no-state").mkdir()  # directory but no state.json
    with_state = tmp_path / "deep-has-state" / "state.json"
    with_state.parent.mkdir(parents=True)
    with_state.write_text("{}")
    got = mod.find_latest_deep_cycle_mtime(tmp_path)
    assert got is not None


# --- count_tagged_items ---


def test_count_tagged_items_zero_when_tracker_missing(tmp_path: Path) -> None:
    mod = _import_nudge()
    fake = tmp_path / "nonexistent-tracker"
    assert mod.count_tagged_items(fake, list(mod.DEFAULT_BLOCKING_STATUSES)) == 0


def test_count_tagged_items_filters_to_blocking_status(tmp_path: Path) -> None:
    mod = _import_nudge()
    fake_tracker = tmp_path / "fake-tracker"
    payload = json.dumps([
        {"id": "WI-a", "status": "todo_soft"},
        {"id": "WI-b", "status": "todo_soft"},
        {"id": "WI-c", "status": "done"},      # not blocking → excluded
        {"id": "WI-d", "status": "in_progress"},
        {"id": "WI-e", "status": "wont_do"},   # not blocking → excluded
    ])
    fake_tracker.write_text(f"#!/bin/bash\ncat <<'EOF'\n{payload}\nEOF\n")
    fake_tracker.chmod(0o755)
    n = mod.count_tagged_items(fake_tracker, ["todo_soft", "in_progress"])
    assert n == 3


def test_count_tagged_items_handles_nonzero_exit(tmp_path: Path) -> None:
    mod = _import_nudge()
    fake = tmp_path / "fake-tracker"
    fake.write_text("#!/bin/bash\nexit 2\n")
    fake.chmod(0o755)
    assert mod.count_tagged_items(fake, ["todo_soft"]) == 0


def test_count_tagged_items_handles_non_json_output(tmp_path: Path) -> None:
    mod = _import_nudge()
    fake = tmp_path / "fake-tracker"
    fake.write_text("#!/bin/bash\necho 'not json'\n")
    fake.chmod(0o755)
    assert mod.count_tagged_items(fake, ["todo_soft"]) == 0


def test_count_tagged_items_handles_non_list_json(tmp_path: Path) -> None:
    mod = _import_nudge()
    fake = tmp_path / "fake-tracker"
    fake.write_text('#!/bin/bash\necho \'{"not": "a list"}\'\n')
    fake.chmod(0o755)
    assert mod.count_tagged_items(fake, ["todo_soft"]) == 0


# --- End-to-end CLI ---


def _write_fake_tracker(dst: Path, items: list[dict]) -> None:
    payload = json.dumps(items)
    dst.write_text(f"#!/bin/bash\ncat <<'EOF'\n{payload}\nEOF\n")
    dst.chmod(0o755)


def test_cli_no_tracker_prints_nothing(tmp_path: Path) -> None:
    (tmp_path / ".agent" / "tracker").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    result = subprocess.run(
        ["python3", str(NUDGE_SCRIPT), str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_cli_emits_nudge_when_conditions_met(tmp_path: Path) -> None:
    # Build a fake repo skeleton + fake tracker script + fake old bakeoff dir.
    (tmp_path / ".agent" / "tracker").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    _write_fake_tracker(
        tmp_path / "scripts" / "tracker",
        [{"id": f"WI-{i}", "status": "todo_soft"} for i in range(7)],
    )
    # Bakeoff artifacts dir with one old deep session.
    bakeoff_root = tmp_path / "bakeoff_artifacts"
    old_state = bakeoff_root / "deep-20251001-000000" / "state.json"
    old_state.parent.mkdir(parents=True)
    old_state.write_text("{}")
    import os
    old_ts = time.time() - (100 * 3600)
    os.utime(old_state, (old_ts, old_ts))

    result = subprocess.run(
        [
            "python3", str(NUDGE_SCRIPT), str(tmp_path),
            "--bakeoff-root", str(bakeoff_root),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "AWAITS_BAKEOFF_VALIDATION BACKLOG" in result.stdout
    assert "7" in result.stdout


def test_cli_silent_when_recent_cycle(tmp_path: Path) -> None:
    (tmp_path / ".agent" / "tracker").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    _write_fake_tracker(
        tmp_path / "scripts" / "tracker",
        [{"id": f"WI-{i}", "status": "todo_soft"} for i in range(20)],
    )
    bakeoff_root = tmp_path / "bakeoff_artifacts"
    recent_state = bakeoff_root / "deep-recent" / "state.json"
    recent_state.parent.mkdir(parents=True)
    recent_state.write_text("{}")  # mtime = now

    result = subprocess.run(
        [
            "python3", str(NUDGE_SCRIPT), str(tmp_path),
            "--bakeoff-root", str(bakeoff_root),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert result.stdout == ""


# --- Integration guards ---


def test_stop_logic_sh_invokes_the_nudge_script() -> None:
    """``stop_logic.sh`` must invoke ``awaits_bakeoff_nudge.py`` so its
    output gets appended to the guidance file. If someone removes the
    invocation, this guard fires before the feature silently regresses."""
    content = (REPO_ROOT / ".agent" / "hooks" / "_shared" / "stop_logic.sh").read_text()
    assert "awaits_bakeoff_nudge.py" in content


def test_agents_md_documents_the_nudge() -> None:
    """AGENTS.md's Bakeoff Validation Discipline section must mention the
    stop-hook nudge so humans and agents know the surfacing mechanism
    exists (WI-dolil slice 3 acceptance)."""
    content = (REPO_ROOT / "AGENTS.md").read_text()
    # Loose check — the phrase "stop hook" near the nudge keywords.
    assert "awaits_bakeoff_validation" in content
    assert "stop hook" in content.lower() or "stop-hook" in content.lower()
