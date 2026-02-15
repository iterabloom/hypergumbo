# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.cli.

Covers all CLI subcommands, both text and JSON output modes, error handling,
the textconv driver, and edge cases.
"""

from __future__ import annotations

import argparse
import json
import os
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hypergumbo_tracker.cli import (
    EXIT_INTERNAL_ERROR,
    EXIT_SUCCESS,
    EXIT_USER_ERROR,
    _build_parser,
    _find_tracker_root,
    _format_item_full,
    _format_item_short,
    _item_to_dict,
    main,
    textconv_main,
)
from hypergumbo_tracker.models import (
    CompiledItem,
    DiscussionEntry,
    KindConfig,
    Tier,
    TrackerConfig,
    load_config,
)
from hypergumbo_tracker.trackerset import TrackerSet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> TrackerConfig:
    return TrackerConfig(
        kinds={
            "invariant": KindConfig(prefix="INV", description="Test invariant"),
            "work_item": KindConfig(prefix="WI", description="Work item"),
        },
        statuses=["todo_hard", "todo_soft", "in_progress", "done", "deferred", "wont_do"],
        blocking_statuses=["todo_hard", "todo_soft"],
        resolved_statuses=["done", "deferred", "wont_do"],
        agent_usernames=["*_agent"],
        lamport_branches=["dev", "main"],
    )


def _setup_tracker(tmp_path: Path) -> Path:
    """Create tracker directory structure and return tracker_root."""
    tracker_root = tmp_path / ".agent"
    (tracker_root / "tracker" / ".ops").mkdir(parents=True)
    (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
    (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)
    return tracker_root


def _add_item(ops_dir: Path, item_id: str, kind: str = "work_item",
              status: str = "todo_hard", title: str | None = None) -> None:
    title = title or f"Item {item_id}"
    ops_content = textwrap.dedent(f"""\
        - op: create
          at: "2026-01-01T00:00:00Z"
          by: agent
          actor: test_agent
          clock: 1
          nonce: a1b2
          data:
            kind: {kind}
            title: "{title}"
            status: {status}
            priority: 2
    """)
    (ops_dir / f".{item_id}.ops").write_text(ops_content)


# ---------------------------------------------------------------------------
# Formatting tests
# ---------------------------------------------------------------------------


class TestFormatting:
    def test_format_item_short(self) -> None:
        item = CompiledItem(
            id="WI-test", kind="work_item", title="Test",
            status="todo_hard", tier=Tier.WORKSPACE,
        )
        result = _format_item_short(item, idx=0)
        assert "WI-test" in result
        assert "Test" in result
        assert "todo_hard" in result

    def test_format_item_short_no_idx(self) -> None:
        item = CompiledItem(
            id="WI-test", kind="work_item", title="Test",
            status="todo_hard", tier=Tier.WORKSPACE,
        )
        result = _format_item_short(item)
        assert "WI-test" in result

    def test_format_item_short_no_tier(self) -> None:
        item = CompiledItem(
            id="WI-test", kind="work_item", title="Test", status="todo_hard",
        )
        result = _format_item_short(item)
        assert "WI-test" in result

    def test_format_item_full(self) -> None:
        item = CompiledItem(
            id="WI-test", kind="work_item", title="Test Item",
            status="todo_hard", priority=1, tags=["a", "b"],
            description="A description", justification="Because reasons",
            fields={"key": "val"}, locked_fields={"priority"},
            duplicate_of=["WI-other"], not_duplicate_of=["WI-another"],
            discussion=[DiscussionEntry(by="agent", actor="a", at="t", message="m")],
            tier=Tier.CANONICAL, created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:01:00Z", cross_tier_conflict=True,
        )
        result = _format_item_full(item)
        assert "WI-test" in result
        assert "Test Item" in result
        assert "A description" in result
        assert "Because reasons" in result
        assert "fields.key: val" in result
        assert "locked" in result
        assert "duplicate_of" in result
        assert "not_duplicate_of" in result
        assert "CROSS-TIER CONFLICT" in result

    def test_item_to_dict(self) -> None:
        item = CompiledItem(
            id="WI-test", kind="work_item", title="Test",
            status="todo_hard", tier=Tier.WORKSPACE,
        )
        d = _item_to_dict(item)
        assert d["id"] == "WI-test"
        assert d["tier"] == "workspace"
        assert isinstance(d["locked_fields"], list)

    def test_item_to_dict_no_tier(self) -> None:
        item = CompiledItem(
            id="WI-test", kind="work_item", title="Test", status="todo_hard",
        )
        d = _item_to_dict(item)
        assert d["tier"] is None


# ---------------------------------------------------------------------------
# Tracker root discovery
# ---------------------------------------------------------------------------


class TestFindTrackerRoot:
    def test_finds_agent_dir(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        result = _find_tracker_root(tmp_path)
        assert result == agent_dir

    def test_finds_in_parent(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        child = tmp_path / "sub" / "dir"
        child.mkdir(parents=True)
        result = _find_tracker_root(child)
        assert result == agent_dir

    def test_raises_when_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            _find_tracker_root(tmp_path / "nowhere")
        assert exc.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParser:
    def test_build_parser(self) -> None:
        parser = _build_parser()
        assert parser is not None

    def test_no_command_shows_help(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# Init command
# ---------------------------------------------------------------------------


class TestInitCommand:
    def test_init(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        root = tmp_path / ".agent"
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "init"])
        assert exc.value.code == EXIT_SUCCESS
        assert (root / "tracker" / ".ops").is_dir()
        assert (root / "tracker-workspace" / ".ops").is_dir()
        assert (root / "tracker-workspace" / "stealth").is_dir()
        assert (root / "tracker" / ".ops" / ".gitattributes").exists()
        assert (root / "tracker-workspace" / "stealth" / ".gitignore").exists()

    def test_init_json(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        root = tmp_path / ".agent"
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "--json", "init"])
        assert exc.value.code == EXIT_SUCCESS
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "root" in data

    def test_init_idempotent(self, tmp_path: Path) -> None:
        root = tmp_path / ".agent"
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(root), "init"])
        # Run again — should not fail
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "init"])
        assert exc.value.code == EXIT_SUCCESS


# ---------------------------------------------------------------------------
# Stub commands
# ---------------------------------------------------------------------------


class TestStubCommands:
    def test_migrate(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        ledger = tmp_path / "ledger.md"
        ledger.write_text("")
        wi = tmp_path / "wi.md"
        wi.write_text("")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "migrate",
                "--ledger", str(ledger),
                "--work-items", str(wi),
            ])
        assert exc.value.code == EXIT_SUCCESS
        assert "created 0 items" in capsys.readouterr().out

    def test_tui_needs_tracker_root(self, capsys: pytest.CaptureFixture) -> None:
        """TUI requires a tracker root (.agent/ directory) to be found."""
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", "/nonexistent/path", "tui"])
        assert exc.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# Read commands
# ---------------------------------------------------------------------------


class TestReadCommands:
    def test_list_empty(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                        mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "list"])
        assert exc.value.code == EXIT_SUCCESS
        assert "(no items)" in capsys.readouterr().out

    def test_list_with_items(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                             mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-test", title="My Item")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "list"])
        assert exc.value.code == EXIT_SUCCESS
        assert "My Item" in capsys.readouterr().out

    def test_list_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                       mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "list"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, list)

    def test_list_with_filters(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                               mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a", status="todo_hard")
        _add_item(tracker_root / "tracker" / ".ops", "WI-b", status="done")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "list", "--status", "todo_hard"])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "WI-a" in out
        assert "WI-b" not in out

    def test_list_with_limit(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                             mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a")
        _add_item(tracker_root / "tracker" / ".ops", "WI-b")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "list", "--limit", "1"])
        assert exc.value.code == EXIT_SUCCESS

    def test_list_with_tier_filter(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                   mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-c")
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-w")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "list", "--tier", "canonical"])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "WI-c" in out

    def test_ready_empty(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                         mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "ready"])
        assert exc.value.code == EXIT_SUCCESS
        assert "(no ready items)" in capsys.readouterr().out

    def test_ready_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                        mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "ready"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, list)

    def test_ready_with_limit(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                              mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a")
        _add_item(tracker_root / "tracker" / ".ops", "WI-b")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "ready", "--limit", "1"])
        assert exc.value.code == EXIT_SUCCESS

    def test_show(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                  mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-test", title="My Item")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "show", "WI-test"])
        assert exc.value.code == EXIT_SUCCESS
        assert "My Item" in capsys.readouterr().out

    def test_show_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                       mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "show", "WI-test"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["id"] == "WI-test"

    def test_show_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                            mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "show", "WI-nonexistent"])
        assert exc.value.code == EXIT_USER_ERROR

    def test_log(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                 mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "log", "WI-test"])
        assert exc.value.code == EXIT_SUCCESS
        assert "create" in capsys.readouterr().out

    def test_log_file_missing(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                               mock_agent_uid: None) -> None:
        """_cmd_log returns error when ops file path doesn't exist on disk."""
        from hypergumbo_tracker.cli import EXIT_USER_ERROR as EU, _cmd_log
        from hypergumbo_tracker.store import Store

        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-test")
        config = load_config(tracker_root / "tracker")
        ts = TrackerSet(tracker_root, config=config)

        # Get the real resolve result first, then mock _resolve_id to return it
        full_id, store, tier = ts._resolve_id("WI-test")
        # Delete the file on disk
        store.item_path(full_id).unlink()
        # Mock _resolve_id to return the now-stale reference
        with patch.object(ts, "_resolve_id", return_value=(full_id, store, tier)):
            args = argparse.Namespace(item_id="WI-test", json=False)
            code = _cmd_log(args, ts)
            assert code == EU


# ---------------------------------------------------------------------------
# Write commands
# ---------------------------------------------------------------------------


class TestWriteCommands:
    def test_add(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                 mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "New Item",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out.strip()
        assert out.startswith("WI-")

    def test_add_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                      mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "add", "--kind", "work_item", "--title", "New Item",
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert "id" in data

    def test_add_with_options(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                              mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "Test",
                "--status", "todo_soft", "--priority", "1",
                "--tag", "a", "--tag", "b",
                "--description", "desc",
                "--tier", "canonical",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_add_with_fields(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                             mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "Test",
                "--field", "key=value",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_add_field_bad_format(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                  mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "Test",
                "--field", "no-equals-sign",
            ])
        assert exc.value.code == EXIT_USER_ERROR

    def test_add_with_before_and_more(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                      mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "Test",
                "--before", "WI-xxx",
                "--pr-ref", "#123",
                "--justification", "needed",
                "--parent", "WI-parent",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_update(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                    mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--status", "done",
            ])
        assert exc.value.code == EXIT_SUCCESS
        assert "updated" in capsys.readouterr().out

    def test_update_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                         mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "update", "WI-test", "--status", "done",
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True

    def test_update_with_tags(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                              mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--add-tag", "new_tag",
                "--remove-before", "WI-old",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_update_with_field(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                               mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--field", "key=value",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_update_field_bad_format(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                     mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--field", "bad",
            ])
        assert exc.value.code == EXIT_USER_ERROR

    def test_update_with_all_options(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                     mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--title", "New Title",
                "--priority", "0",
                "--parent", "WI-parent",
                "--pr-ref", "#456",
                "--justification", "reason",
                "--description", "new desc",
                "--add-before", "WI-before",
                "--remove-tag", "old",
                "--duplicate-of", "WI-dup",
                "--not-duplicate-of", "WI-nodup",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_discuss(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                     mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "discuss", "WI-test", "Hello discussion",
            ])
        assert exc.value.code == EXIT_SUCCESS
        assert "discussed" in capsys.readouterr().out

    def test_discuss_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                          mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "discuss", "WI-test", "Hello",
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True

    def test_discuss_summarize(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                               mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "discuss", "WI-test", "Summary text", "--summarize",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_discuss_clear(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                           mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "discuss", "WI-test", "--clear",
            ])
        assert exc.value.code == EXIT_SUCCESS


# ---------------------------------------------------------------------------
# Lock/Unlock commands
# ---------------------------------------------------------------------------


class TestLockUnlock:
    def test_lock(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                  mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "lock", "WI-test", "priority", "status",
            ])
        assert exc.value.code == EXIT_SUCCESS
        assert "locked" in capsys.readouterr().out

    def test_lock_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                       mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "lock", "WI-test", "priority",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_unlock(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                    mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "unlock", "WI-test", "priority",
            ])
        assert exc.value.code == EXIT_SUCCESS
        assert "unlocked" in capsys.readouterr().out

    def test_lock_agent_denied(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                               mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "lock", "WI-test", "priority",
            ])
        assert exc.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# Tier movement commands
# ---------------------------------------------------------------------------


class TestTierMovement:
    def test_promote(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                     mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "promote", "WI-test"])
        assert exc.value.code == EXIT_SUCCESS
        assert "promoted" in capsys.readouterr().out

    def test_promote_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                          mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "promote", "WI-test"])
        assert exc.value.code == EXIT_SUCCESS

    def test_demote(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                    mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "demote", "WI-test"])
        assert exc.value.code == EXIT_SUCCESS
        assert "demoted" in capsys.readouterr().out

    def test_demote_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                         mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "demote", "WI-test"])
        assert exc.value.code == EXIT_SUCCESS

    def test_stealth(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                     mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "stealth", "WI-test"])
        assert exc.value.code == EXIT_SUCCESS
        assert "stealthed" in capsys.readouterr().out

    def test_stealth_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                          mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "stealth", "WI-test"])
        assert exc.value.code == EXIT_SUCCESS

    def test_unstealth(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                       mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / "stealth", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "unstealth", "WI-test"])
        assert exc.value.code == EXIT_SUCCESS
        assert "unstealthed" in capsys.readouterr().out

    def test_unstealth_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                            mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / "stealth", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "unstealth", "WI-test"])
        assert exc.value.code == EXIT_SUCCESS

    def test_promote_wrong_tier(self, tmp_path: Path, mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "promote", "WI-test"])
        assert exc.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# Governance commands
# ---------------------------------------------------------------------------


class TestGovernanceCommands:
    def test_count_todos(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                         mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a")
        _add_item(tracker_root / "tracker" / ".ops", "WI-b")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "count-todos"])
        assert exc.value.code == EXIT_SUCCESS
        assert "2" in capsys.readouterr().out

    def test_count_todos_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                              mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "count-todos"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["count"] == 1

    def test_count_todos_hard(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                              mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a", status="todo_hard")
        _add_item(tracker_root / "tracker" / ".ops", "WI-b", status="todo_soft")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "count-todos", "--hard"])
        assert exc.value.code == EXIT_SUCCESS
        assert "1" in capsys.readouterr().out

    def test_count_todos_soft(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                              mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a", status="todo_hard")
        _add_item(tracker_root / "tracker" / ".ops", "WI-b", status="todo_soft")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "count-todos", "--soft"])
        assert exc.value.code == EXIT_SUCCESS
        assert "1" in capsys.readouterr().out

    def test_hash_todos(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                        mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "hash-todos"])
        assert exc.value.code == EXIT_SUCCESS
        h = capsys.readouterr().out.strip()
        assert len(h) == 64

    def test_hash_todos_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                             mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "hash-todos"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert "hash" in data

    def test_validate(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                      mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "validate"])
        assert exc.value.code == EXIT_SUCCESS
        assert "validation passed" in capsys.readouterr().out

    def test_validate_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                           mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "validate"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert "errors" in data

    def test_validate_with_file(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        ops_file = tracker_root / "tracker" / ".ops" / ".WI-test.ops"
        ops_file.write_text(textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
        """))
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "validate", str(ops_file)])
        assert exc.value.code == EXIT_SUCCESS

    def test_validate_missing_file(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                   mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "validate", "/nonexistent/file.ops"])
        assert exc.value.code == EXIT_USER_ERROR

    def test_validate_strict(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                             mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "validate", "--strict"])
        assert exc.value.code == EXIT_SUCCESS

    def test_validate_similar(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                              mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "validate", "--similar"])
        assert exc.value.code == EXIT_SUCCESS

    def test_validate_check_locks(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                  mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "validate", "--check-locks"])
        assert exc.value.code == EXIT_SUCCESS


# ---------------------------------------------------------------------------
# Utility commands
# ---------------------------------------------------------------------------


class TestUtilityCommands:
    def test_cache_rebuild(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                           mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "cache-rebuild"])
        assert exc.value.code == EXIT_SUCCESS
        assert "cache rebuilt" in capsys.readouterr().out

    def test_cache_rebuild_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "cache-rebuild"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True

    def test_reconcile_reset(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                             mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "reconcile-reset", "WI-test"])
        assert exc.value.code == EXIT_SUCCESS

    def test_reconcile_reset_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                  mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "reconcile-reset", "WI-test"])
        assert exc.value.code == EXIT_SUCCESS

    def test_fork_setup(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                        mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "fork-setup"])
        assert exc.value.code == EXIT_SUCCESS
        assert "workspace" in capsys.readouterr().out

    def test_fork_setup_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                             mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "fork-setup"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["scope"] == "workspace"

    def test_fork_setup_agent_denied(self, tmp_path: Path, mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "fork-setup"])
        assert exc.value.code == EXIT_USER_ERROR

    def test_fork_setup_existing_config(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                        mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        import yaml
        config_path = tracker_root / "tracker" / "config.yaml"
        config_data = {
            "kinds": {"work_item": {"prefix": "WI"}},
            "statuses": ["todo_hard", "done"],
            "stop_hook": {"blocking_statuses": ["todo_hard"], "resolved_statuses": ["done"]},
            "existing": "data",
        }
        config_path.write_text(yaml.dump(config_data))
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "fork-setup"])
        assert exc.value.code == EXIT_SUCCESS


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_tracker_root_not_dir(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tmp_path / "nonexistent"), "list"])
        assert exc.value.code == EXIT_USER_ERROR

    def test_auto_discover_tracker_root(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                        mock_agent_uid: None, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that main auto-discovers .agent/ from cwd."""
        tracker_root = _setup_tracker(tmp_path)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["list"])
        assert exc.value.code == EXIT_SUCCESS

    def test_corrupt_data_error(self, tmp_path: Path, mock_agent_uid: None) -> None:
        """CorruptFileError is caught and exits with INTERNAL_ERROR."""
        from hypergumbo_tracker.store import CorruptFileError

        tracker_root = _setup_tracker(tmp_path)
        with patch(
            "hypergumbo_tracker.cli._cmd_show",
            side_effect=CorruptFileError("bad data"),
        ):
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "show", "WI-x"])
            assert exc.value.code == EXIT_INTERNAL_ERROR

    def test_generic_exception_handler(self, tmp_path: Path, mock_agent_uid: None) -> None:
        """Generic exceptions are caught and exit with INTERNAL_ERROR."""
        tracker_root = _setup_tracker(tmp_path)
        with patch("hypergumbo_tracker.cli._cmd_list", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "list"])
            assert exc.value.code == EXIT_INTERNAL_ERROR

    def test_tracker_init_failure(self, tmp_path: Path, mock_agent_uid: None) -> None:
        """TrackerSet initialization failure exits with INTERNAL_ERROR."""
        tracker_root = _setup_tracker(tmp_path)
        # Write an invalid config that will fail during load_config
        (tracker_root / "tracker" / "config.yaml").write_text("kinds: null\nstatuses: null\n")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "list"])
        assert exc.value.code == EXIT_INTERNAL_ERROR

    def test_unlock_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                         mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "unlock", "WI-test", "priority",
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True

    def test_validate_warnings_non_json(self, tmp_path: Path,
                                        capsys: pytest.CaptureFixture,
                                        mock_agent_uid: None) -> None:
        """Test validation output with warnings shown in text mode."""
        tracker_root = _setup_tracker(tmp_path)
        # Create an item with an unknown field (produces warning)
        ops_content = textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Test"
                status: todo_hard
                priority: 2
                fields:
                  statement: "test"
                  root_cause: "test"
                  unknown_fld: "extra"
        """)
        import yaml
        # We need the invariant kind with fields_schema for warnings
        config_data = {
            "kinds": {
                "invariant": {
                    "prefix": "INV",
                    "fields_schema": {
                        "statement": {"type": "text", "required": True},
                        "root_cause": {"type": "text", "required": True},
                    },
                },
                "work_item": {"prefix": "WI"},
            },
            "statuses": ["todo_hard", "todo_soft", "in_progress", "done", "deferred", "wont_do"],
            "stop_hook": {
                "blocking_statuses": ["todo_hard", "todo_soft"],
                "resolved_statuses": ["done", "deferred", "wont_do"],
            },
        }
        (tracker_root / "tracker" / "config.yaml").write_text(yaml.dump(config_data))
        (tracker_root / "tracker" / ".ops" / ".INV-test.ops").write_text(ops_content)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "validate"])
        # Warnings don't make it fail unless --strict
        captured = capsys.readouterr()
        assert "WARNING" in captured.out

    def test_count_todos_exception(self, tmp_path: Path, mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with patch("hypergumbo_tracker.stop_hook.count_todos", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "count-todos"])
            assert exc.value.code == EXIT_USER_ERROR

    def test_hash_todos_exception(self, tmp_path: Path, mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with patch("hypergumbo_tracker.stop_hook.hash_todos", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "hash-todos"])
            assert exc.value.code == EXIT_USER_ERROR

    def test_guidance(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                      mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a")
        guidance_dir = tmp_path / "guidance"
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "guidance", "--guidance-dir", str(guidance_dir),
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out.strip()
        assert out.endswith(".md")

    def test_guidance_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                           mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        guidance_dir = tmp_path / "guidance"
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "guidance", "--guidance-dir", str(guidance_dir),
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert "path" in data

    def test_guidance_exception(self, tmp_path: Path, mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with patch("hypergumbo_tracker.stop_hook.generate_guidance", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "guidance"])
            assert exc.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# Textconv
# ---------------------------------------------------------------------------


class TestTextconv:
    def test_textconv_basic(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        ops_file = tmp_path / ".WI-test.ops"
        ops_file.write_text(textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "My Test Item"
                status: todo_hard
                priority: 2
                tags: [a, b]
                fields:
                  key: value
        """))
        with pytest.raises(SystemExit) as exc:
            textconv_main([str(ops_file)])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "WI-test" in out
        assert "My Test Item" in out
        assert "fields.key: value" in out

    def test_textconv_not_found(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            textconv_main(["/nonexistent/file.ops"])
        assert exc.value.code == 1

    def test_textconv_corrupt(self, tmp_path: Path) -> None:
        ops_file = tmp_path / ".WI-bad.ops"
        ops_file.write_text("{{{{bad yaml")
        with pytest.raises(SystemExit) as exc:
            textconv_main([str(ops_file)])
        assert exc.value.code == 1

    def test_textconv_non_ops_filename(self, tmp_path: Path,
                                       capsys: pytest.CaptureFixture) -> None:
        ops_file = tmp_path / "test.yaml"
        ops_file.write_text(textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
        """))
        with pytest.raises(SystemExit) as exc:
            textconv_main([str(ops_file)])
        assert exc.value.code == 0

    def test_textconv_with_locked_fields(self, tmp_path: Path,
                                         capsys: pytest.CaptureFixture) -> None:
        ops_file = tmp_path / ".WI-test.ops"
        ops_file.write_text(textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: work_item
                title: "Test"
                status: todo_hard
                priority: 2
            - op: lock
              at: "2026-01-01T00:01:00Z"
              by: human
              actor: jgstern
              clock: 2
              nonce: b2c3
              lock: [priority]
        """))
        with pytest.raises(SystemExit) as exc:
            textconv_main([str(ops_file)])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "locked" in out
