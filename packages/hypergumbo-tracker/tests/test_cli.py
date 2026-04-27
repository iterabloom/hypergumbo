# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.cli.

Covers all CLI subcommands, both text and JSON output modes, error handling,
the textconv driver, and edge cases.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hypergumbo_tracker.cli import (
    EXIT_INTERNAL_ERROR,
    EXIT_SUCCESS,
    EXIT_USER_ERROR,
    _MUTATION_COMMANDS,
    _build_parser,
    _detect_screen_altscreen_off,
    _find_tracker_root,
    _format_item_full,
    _format_item_short,
    _item_to_dict,
    _maybe_auto_sync,
    _print_sync_reminder,
    _print_screen_warning,
    _changes_to_dict,
    _describe_changes,
    _short_id,
    _warn_unread_human_messages,
    main,
    textconv_main,
)
from hypergumbo_tracker.models import (
    CompiledItem,
    DiscussionEntry,
    Tier,
    TrackerConfig,
    load_config,
)
from hypergumbo_tracker.trackerset import TrackerSet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> TrackerConfig:
    from helpers import make_test_config

    return make_test_config(**overrides)


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
            description="A description",
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
        assert "fields.key: val" in result
        assert "locked" in result
        assert "duplicate_of" in result
        assert "not_duplicate_of" in result
        assert "CROSS-TIER CONFLICT" in result
        assert "(1 entries)" in result
        assert "a (agent): m" in result

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

    def test_init_creates_tracker_gitignore(self, tmp_path: Path) -> None:
        root = tmp_path / ".agent"
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "init"])
        assert exc.value.code == EXIT_SUCCESS
        gitignore = root / "tracker" / ".gitignore"
        assert gitignore.exists()
        assert "config.yaml" in gitignore.read_text()

    def test_init_copies_config_template(self, tmp_path: Path) -> None:
        root = tmp_path / ".agent"
        tracker_dir = root / "tracker"
        tracker_dir.mkdir(parents=True)
        template = tracker_dir / "config.yaml.template"
        template.write_text("kinds:\n  work_item:\n    prefix: WI\n")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "init"])
        assert exc.value.code == EXIT_SUCCESS
        config = tracker_dir / "config.yaml"
        assert config.exists()
        assert config.read_text() == template.read_text()

    def test_init_does_not_overwrite_existing_config(self, tmp_path: Path) -> None:
        root = tmp_path / ".agent"
        tracker_dir = root / "tracker"
        tracker_dir.mkdir(parents=True)
        template = tracker_dir / "config.yaml.template"
        template.write_text("kinds:\n  work_item:\n    prefix: WI\n")
        config = tracker_dir / "config.yaml"
        config.write_text("custom: data\n")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "init"])
        assert exc.value.code == EXIT_SUCCESS
        # Config should not be overwritten
        assert config.read_text() == "custom: data\n"

    def test_init_no_template_no_config_copy(self, tmp_path: Path) -> None:
        root = tmp_path / ".agent"
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "init"])
        assert exc.value.code == EXIT_SUCCESS
        # No template, so no config should be created
        assert not (root / "tracker" / "config.yaml").exists()


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

    def test_list_multi_status(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                               mock_agent_uid: None) -> None:
        """--status is repeatable: union of all specified statuses (WI-lukop)."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a", status="todo_hard")
        _add_item(tracker_root / "tracker" / ".ops", "WI-b", status="todo_soft")
        _add_item(tracker_root / "tracker" / ".ops", "WI-c", status="done")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "list",
                "--status", "todo_hard", "--status", "todo_soft",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "WI-a" in out
        assert "WI-b" in out
        assert "WI-c" not in out

    def test_list_open_filter(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                              mock_agent_uid: None) -> None:
        """--open shows items with open statuses (WI-lukop)."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a", status="todo_soft")
        _add_item(tracker_root / "tracker" / ".ops", "WI-b", status="done")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "list", "--open"])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "WI-a" in out
        assert "WI-b" not in out

    def test_list_open_plus_status(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                   mock_agent_uid: None) -> None:
        """--open combined with --status merges both sets (WI-lukop)."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a", status="done")
        _add_item(tracker_root / "tracker" / ".ops", "WI-b", status="todo_soft")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "list",
                "--open", "--status", "done",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        # Both should appear: --open includes todo_soft, --status adds done
        assert "WI-a" in out
        assert "WI-b" in out

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
        assert isinstance(data, dict)
        assert "ready" in data
        assert isinstance(data["ready"], list)

    def test_ready_with_limit(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                              mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a")
        _add_item(tracker_root / "tracker" / ".ops", "WI-b")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "ready", "--limit", "1"])
        assert exc.value.code == EXIT_SUCCESS

    def test_ready_shows_unread_human_messages(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """ready should show a notice for items with unread human messages."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker" / ".ops"
        # A ready item (no messages)
        _add_item(ops_dir, "WI-a", status="todo_hard")
        # A done item with unread human message (not ready, but has message)
        _add_item_with_discussion(
            ops_dir, "WI-b",
            discussion_ops=[
                {"at": "2026-01-02T00:00:00Z", "by": "human",
                 "actor": "alice", "message": "please review this"},
            ],
            status="done",
        )
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "ready"])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        # Should show the ready item
        assert "WI-a" in out
        # Should show unread message notice for WI-b
        assert "WI-b" in out
        assert "unread human message" in out.lower()
        # Should show usage hint
        assert "tracker show" in out
        assert "tracker discuss" in out

    def test_ready_no_unread_notice_when_none(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """ready should not show unread section when no items have messages."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "ready"])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "unread" not in out.lower()

    def test_ready_json_includes_unread_messages(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """ready --json should include unread_messages field."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker" / ".ops"
        _add_item(ops_dir, "WI-a", status="todo_hard")
        _add_item_with_discussion(
            ops_dir, "WI-b",
            discussion_ops=[
                {"at": "2026-01-02T00:00:00Z", "by": "human",
                 "actor": "alice", "message": "check this"},
            ],
            status="holding",
        )
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "ready"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        # JSON output should have items_with_unread_messages key
        assert "items_with_unread_messages" in data
        unread_items = data["items_with_unread_messages"]
        assert len(unread_items) == 1
        assert unread_items[0]["id"] == "WI-b"

    def test_ready_unread_workspace_scope(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Workspace scope should only show unread from workspace/stealth tiers."""
        import yaml
        tracker_root = _setup_tracker(tmp_path)
        # Human message in canonical tier (should be excluded)
        _add_item_with_discussion(
            tracker_root / "tracker" / ".ops", "WI-can",
            discussion_ops=[
                {"at": "2026-01-02T00:00:00Z", "by": "human",
                 "actor": "alice", "message": "canonical msg"},
            ],
            status="done",
        )
        # Human message in workspace tier (should be included)
        _add_item_with_discussion(
            tracker_root / "tracker-workspace" / ".ops", "WI-ws",
            discussion_ops=[
                {"at": "2026-01-02T00:00:00Z", "by": "human",
                 "actor": "alice", "message": "workspace msg"},
            ],
            status="done",
        )
        config_data = {
            "kinds": {"work_item": {"prefix": "WI"}},
            "statuses": [
                "todo_hard", "todo_soft", "in_progress",
                "needs_human_review", "done", "wont_do",
            ],
            "stop_hook": {
                "blocking_statuses": ["todo_hard", "todo_soft"],
                "resolved_statuses": ["done", "wont_do"],
                "scope": "workspace",
            },
        }
        (tracker_root / "tracker" / "config.yaml").write_text(
            yaml.dump(config_data))
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "ready"])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "WI-ws" in out
        assert "WI-can" not in out

    def test_ready_unread_excludes_agent_replied(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Items where the agent already replied should not show as unread."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker" / ".ops"
        _add_item_with_discussion(
            ops_dir, "WI-c",
            discussion_ops=[
                {"at": "2026-01-02T00:00:00Z", "by": "human",
                 "actor": "alice", "message": "question?"},
                {"at": "2026-01-02T01:00:00Z", "by": "agent",
                 "actor": "bot", "message": "answer."},
            ],
            status="done",
        )
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "ready"])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "unread" not in out.lower()

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

    def test_add_invariant_missing_required_fields(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Adding an invariant without required fields (statement, root_cause) must fail."""
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "invariant", "--title", "Missing fields",
            ])
        assert exc.value.code == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "statement" in err
        assert "root_cause" in err

    def test_add_invariant_partial_required_fields(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Adding an invariant with only one required field must fail."""
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "invariant", "--title", "Partial fields",
                "--field", "statement=Some statement",
            ])
        assert exc.value.code == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "root_cause" in err

    def test_add_invariant_all_required_fields_succeeds(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Adding an invariant with all required fields must succeed."""
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "invariant", "--title", "Valid invariant",
                "--field", "statement=X must always be true",
                "--field", "root_cause=Y is broken",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_add_with_before_and_more(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                      mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-before-target", title="Before target")
        _add_item(ops_dir, "WI-parent-target", title="Parent target")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "Test",
                "--isbefore", "WI-before-target",
                "--pr-ref", "#123",
                "--parent", "WI-parent-target",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_add_parent_resolves_prefix(self, tmp_path: Path,
                                        capsys: pytest.CaptureFixture,
                                        mock_agent_uid: None) -> None:
        """--parent with a short prefix resolves to the full proquint ID."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        full_id = "WI-abcde-fghij-klmno-pqrst-uvwxy-zabcd-efghi-jklmn"
        _add_item(ops_dir, full_id, title="Parent item")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "Child",
                "--parent", "WI-abcde",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_add_parent_nonexistent_fails(self, tmp_path: Path,
                                          capsys: pytest.CaptureFixture,
                                          mock_agent_uid: None) -> None:
        """--parent referencing a nonexistent item fails at CLI time."""
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "Child",
                "--parent", "WI-nonexistent",
            ])
        assert exc.value.code == EXIT_USER_ERROR

    def test_add_workspace_parent_to_stealth_rejected(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """WI-sohot: workspace item cannot reference a stealth-tier parent.

        Stealth is gitignored, so CI cannot resolve the reference. The CLI
        must refuse the add at write time rather than letting it through to
        fail later in CI.
        """
        tracker_root = _setup_tracker(tmp_path)
        stealth_dir = tracker_root / "tracker-workspace" / "stealth"
        _add_item(stealth_dir, "WI-stealth-target", title="Stealth")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "Child",
                "--tier", "workspace",
                "--parent", "WI-stealth-target",
            ])
        assert exc.value.code == EXIT_USER_ERROR
        captured = capsys.readouterr()
        assert "stealth" in captured.err.lower()
        assert "ci cannot resolve" in captured.err.lower()

    def test_add_canonical_isbefore_to_stealth_rejected(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """WI-sohot: canonical item cannot --isbefore a stealth target."""
        tracker_root = _setup_tracker(tmp_path)
        stealth_dir = tracker_root / "tracker-workspace" / "stealth"
        _add_item(stealth_dir, "WI-stealth-target", title="Stealth")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "Blocker",
                "--tier", "canonical",
                "--isbefore", "WI-stealth-target",
            ])
        assert exc.value.code == EXIT_USER_ERROR

    def test_add_stealth_parent_to_canonical_allowed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """WI-sohot: stealth items may reference any tier (full local scope)."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_dir = tracker_root / "tracker" / ".ops"
        _add_item(canonical_dir, "WI-canonical-target", title="Canonical")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "StealthChild",
                "--tier", "stealth",
                "--parent", "WI-canonical-target",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_add_workspace_parent_to_workspace_allowed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Sanity: workspace→workspace references are unaffected."""
        tracker_root = _setup_tracker(tmp_path)
        ws_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ws_dir, "WI-ws-target", title="Workspace target")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "WSChild",
                "--tier", "workspace",
                "--parent", "WI-ws-target",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_add_description_file(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                  mock_agent_uid: None) -> None:
        """--description-file reads description from a file (WI-pudan)."""
        tracker_root = _setup_tracker(tmp_path)
        desc_file = tmp_path / "desc.md"
        desc_file.write_text("Description with `backticks` and $VAR")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "FileDesc",
                "--description-file", str(desc_file),
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_add_description_stdin(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                   mock_agent_uid: None) -> None:
        """--description-stdin reads description from stdin (WI-pudan)."""
        from io import StringIO
        from unittest.mock import patch

        tracker_root = _setup_tracker(tmp_path)
        with patch("sys.stdin", StringIO("Stdin description\n")):
            with pytest.raises(SystemExit) as exc:
                main([
                    "--tracker-root", str(tracker_root),
                    "add", "--kind", "work_item", "--title", "StdinDesc",
                    "--description-stdin",
                ])
        assert exc.value.code == EXIT_SUCCESS

    def test_add_description_conflict(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                      mock_agent_uid: None) -> None:
        """--description and --description-file together is an error (WI-pudan)."""
        tracker_root = _setup_tracker(tmp_path)
        desc_file = tmp_path / "desc.md"
        desc_file.write_text("text")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "Conflict",
                "--description", "inline",
                "--description-file", str(desc_file),
            ])
        assert exc.value.code == EXIT_USER_ERROR

    def test_add_description_file_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                            mock_agent_uid: None) -> None:
        """--description-file with nonexistent file is an error (WI-pudan)."""
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "add", "--kind", "work_item", "--title", "Missing",
                "--description-file", str(tmp_path / "nonexistent.md"),
            ])
        assert exc.value.code == EXIT_USER_ERROR

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

    def test_update_description_file(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                     mock_agent_uid: None) -> None:
        """update --description-file reads from file (WI-pudan)."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        desc_file = tmp_path / "desc.md"
        desc_file.write_text("Updated via file")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--description-file", str(desc_file),
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_update_description_conflict(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                         mock_agent_uid: None) -> None:
        """update --description + --description-file is an error (WI-pudan)."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        desc_file = tmp_path / "desc.md"
        desc_file.write_text("text")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--description", "inline", "--description-file", str(desc_file),
            ])
        assert exc.value.code == EXIT_USER_ERROR

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
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-test")
        _add_item(ops_dir, "WI-old", title="Old item")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--add-tag", "new_tag",
                "--remove-isbefore", "WI-old",
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

    def test_update_add_field_preserves_existing(self, tmp_path: Path,
                                                 capsys: pytest.CaptureFixture,
                                                 mock_agent_uid: None) -> None:
        """--add-field merges into existing fields without erasing (WI-lorip)."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        # Set initial fields
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--field", "statement=original",
            ])
        capsys.readouterr()  # clear capture
        # Add a new field — should preserve 'statement'
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--add-field", "root_cause=new_cause",
            ])
        assert exc.value.code == EXIT_SUCCESS
        capsys.readouterr()  # clear capture
        # Verify both fields exist
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--json",
                "show", "WI-test",
            ])
        data = json.loads(capsys.readouterr().out)
        assert data["fields"]["statement"] == "original"
        assert data["fields"]["root_cause"] == "new_cause"

    def test_update_remove_field(self, tmp_path: Path,
                                 capsys: pytest.CaptureFixture,
                                 mock_agent_uid: None) -> None:
        """--remove-field deletes a key from fields (WI-lorip)."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--field", "a=1", "--field", "b=2",
            ])
        capsys.readouterr()  # clear capture
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--remove-field", "a",
            ])
        assert exc.value.code == EXIT_SUCCESS
        capsys.readouterr()  # clear capture
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--json",
                "show", "WI-test",
            ])
        data = json.loads(capsys.readouterr().out)
        assert "a" not in data["fields"]
        assert data["fields"]["b"] == "2"

    def test_update_field_and_add_field_conflict(self, tmp_path: Path,
                                                  capsys: pytest.CaptureFixture,
                                                  mock_agent_uid: None) -> None:
        """--field and --add-field together is an error (WI-lorip)."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--field", "a=1", "--add-field", "b=2",
            ])
        assert exc.value.code == EXIT_USER_ERROR

    def test_update_add_field_bad_format(self, tmp_path: Path,
                                         capsys: pytest.CaptureFixture,
                                         mock_agent_uid: None) -> None:
        """--add-field without = is an error (WI-lorip)."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--add-field", "bad",
            ])
        assert exc.value.code == EXIT_USER_ERROR

    def test_update_with_all_options(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                     mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-test")
        _add_item(ops_dir, "WI-parent-ref", title="Parent ref")
        _add_item(ops_dir, "WI-before-ref", title="Before ref")
        _add_item(ops_dir, "WI-dup-ref", title="Dup ref")
        _add_item(ops_dir, "WI-nodup-ref", title="Nodup ref")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--title", "New Title",
                "--priority", "0",
                "--parent", "WI-parent-ref",
                "--pr-ref", "#456",
                "--description", "new desc",
                "--add-isbefore", "WI-before-ref",
                "--remove-tag", "old",
                "--add-duplicate-of", "WI-dup-ref",
                "--add-not-duplicate-of", "WI-nodup-ref",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_update_with_note(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                               mock_agent_uid: None) -> None:
        """--note adds a discussion entry after the update."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--status", "done",
                "--note", "Resolved via PR #1457",
            ])
        assert exc.value.code == EXIT_SUCCESS
        assert "updated" in capsys.readouterr().out
        # Verify the discussion was actually added
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root), "--json",
                "show", "WI-test",
            ])
        show_data = json.loads(capsys.readouterr().out)
        discussion = show_data.get("discussion", [])
        assert any("Resolved via PR #1457" in str(d) for d in discussion)

    def test_update_with_note_json(self, tmp_path: Path,
                                    capsys: pytest.CaptureFixture,
                                    mock_agent_uid: None) -> None:
        """--note works in JSON output mode too."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "update", "WI-test",
                "--note", "Context note for tracking",
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True

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
                "discuss", "WI-test", "--summarize", "Summary text",
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

    def test_discuss_blocks_without_ack_thread(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Discuss exits non-zero when last message is from human and no --ack-thread."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item_with_discussion(ops_dir, "WI-test", [
            {"at": "2026-01-01T01:00:00Z", "by": "agent",
             "actor": "test_agent", "message": "Initial note"},
            {"at": "2026-01-02T01:00:00Z", "by": "human",
             "actor": "jgstern", "message": "Please investigate this"},
        ])
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "discuss", "WI-test", "Will do",
            ])
        assert exc.value.code == EXIT_USER_ERROR
        out = capsys.readouterr().out
        assert "unread message from the human" in out
        assert "Please investigate this" in out
        assert "--ack-thread" in out

    def test_discuss_proceeds_with_ack_thread(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Discuss proceeds when --ack-thread is provided with unread human message."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item_with_discussion(ops_dir, "WI-test", [
            {"at": "2026-01-01T01:00:00Z", "by": "agent",
             "actor": "test_agent", "message": "Initial note"},
            {"at": "2026-01-02T01:00:00Z", "by": "human",
             "actor": "jgstern", "message": "Please investigate this"},
        ])
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "discuss", "WI-test",
                "I've read the thread. Here's my reply.",
                "--ack-thread",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "discussed" in out

    def test_discuss_gate_message_shows_working_arg_order(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """WI-foril: the gate-message points at the working arg order
        (`message`, then `--ack-thread`), not the argparse-rejected order.

        Argparse subparsers with `nargs="?"` positional + a following
        optional flag refuse to accept `--ack-thread "<msg>"` (flag before
        positional) — the message gets parsed as extra args. The fix is
        cosmetic: the printed re-run hint must show the working order so
        users (and agents) don't follow the broken example.
        """
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item_with_discussion(ops_dir, "WI-test", [
            {"at": "2026-01-01T01:00:00Z", "by": "agent",
             "actor": "test_agent", "message": "Initial note"},
            {"at": "2026-01-02T01:00:00Z", "by": "human",
             "actor": "jgstern", "message": "Please investigate this"},
        ])
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "discuss", "WI-test", "Will do",
            ])
        assert exc.value.code == EXIT_USER_ERROR
        out = capsys.readouterr().out
        # Working order: message first, then --ack-thread
        assert "\"<your reply>\" --ack-thread" in out
        # Anti-regression: must NOT show the broken order
        assert "--ack-thread \"<your reply>\"" not in out

    # WI-foril: the sentinel test that asserted argparse rejects
    # `--ack-thread "<msg>"` (flag-before-positional) was removed when
    # Python 3.12's argparse refactor made the backtracking over
    # `nargs="?"` positionals more aggressive and the "broken" order
    # started parsing as `ack_thread=True, message="<msg>"`. Its own
    # docstring anticipated this: "If a future Python or argparse
    # change ever made the broken order work, this test would fail and
    # we'd revisit the gate-message text." The gate-message
    # (tests/test_cli.py::test_discuss_gate_message_shows_working_arg_order)
    # still recommends the universally-working order (`"<msg>"` then
    # `--ack-thread`), so no text change is needed.

    def test_discuss_no_warning_when_last_is_agent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """No unread warning when the last message is from an agent."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item_with_discussion(ops_dir, "WI-test", [
            {"at": "2026-01-01T01:00:00Z", "by": "agent",
             "actor": "test_agent", "message": "Status update"},
        ])
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "discuss", "WI-test", "Another update",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "unread message from the human" not in out

    def test_discuss_no_warning_on_clear(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_human_uid: None,
    ) -> None:
        """No unread warning when using --clear."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item_with_discussion(ops_dir, "WI-test", [
            {"at": "2026-01-01T01:00:00Z", "by": "human",
             "actor": "jgstern", "message": "Some human message"},
        ])
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "discuss", "WI-test", "--clear",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "unread message from the human" not in out

    def test_discuss_no_warning_on_summarize(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """No unread warning when using --summarize."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item_with_discussion(ops_dir, "WI-test", [
            {"at": "2026-01-01T01:00:00Z", "by": "human",
             "actor": "jgstern", "message": "Human says hello"},
        ])
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "discuss", "WI-test", "--summarize", "Summary text",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "unread message from the human" not in out

    def test_discuss_shows_prior_transcript(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Unread gate includes prior transcript and exits non-zero."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item_with_discussion(ops_dir, "WI-test", [
            {"at": "2026-01-01T01:00:00Z", "by": "agent",
             "actor": "test_agent", "message": "First note"},
            {"at": "2026-01-02T01:00:00Z", "by": "agent",
             "actor": "test_agent", "message": "Second note"},
            {"at": "2026-01-03T01:00:00Z", "by": "human",
             "actor": "jgstern", "message": "Human reply"},
        ])
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "discuss", "WI-test", "Agent responds",
            ])
        # Now blocks without --ack-thread (WI-zufuj)
        assert exc.value.code == EXIT_USER_ERROR
        out = capsys.readouterr().out
        assert "Human reply" in out
        assert "First note" in out
        assert "Second note" in out
        assert "--ack-thread" in out

    def test_discuss_view_shows_thread(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """discuss <id> with no message prints the discussion thread (WI-kidip)."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item_with_discussion(ops_dir, "WI-test", [
            {"at": "2026-01-01T01:00:00Z", "by": "agent",
             "actor": "test_agent", "message": "First note"},
            {"at": "2026-01-02T01:00:00Z", "by": "human",
             "actor": "jgstern", "message": "Human reply"},
        ])
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "discuss", "WI-test",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "First note" in out
        assert "Human reply" in out
        assert "2 entries" in out or "Discussion" in out

    def test_discuss_view_empty_thread(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """discuss <id> on item with no discussion prints '(no entries)'."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "discuss", "WI-test",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "no entries" in out.lower()

    def test_discuss_view_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """discuss <id> --json with no message returns discussion as JSON list."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item_with_discussion(ops_dir, "WI-test", [
            {"at": "2026-01-01T01:00:00Z", "by": "agent",
             "actor": "test_agent", "message": "A note"},
        ])
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "discuss", "WI-test",
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["message"] == "A note"

    def test_update_note_warns_unread_human_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """update --note also warns about unread human messages."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item_with_discussion(ops_dir, "WI-test", [
            {"at": "2026-01-01T01:00:00Z", "by": "human",
             "actor": "jgstern", "message": "Check this please"},
        ])
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--status", "done",
                "--note", "Fixed it",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "unread message from the human" in out
        assert "Check this please" in out

    def test_warn_unread_silently_handles_missing_item(
        self, tmp_path: Path, mock_agent_uid: None,
    ) -> None:
        """_warn_unread_human_messages returns silently for nonexistent items."""
        tracker_root = _setup_tracker(tmp_path)
        ts = TrackerSet(tracker_root)
        # Should not raise — the caller handles the error
        _warn_unread_human_messages("WI-nonexistent", ts)


# ---------------------------------------------------------------------------
# Verbose confirmation messages
# ---------------------------------------------------------------------------


class TestVerboseConfirmations:
    """Mutation commands print human-readable confirmation details."""

    def test_update_status_shows_change(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Status change prints old → new status."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test",
                  status="todo_hard")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--status", "done",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "updated" in out
        assert "status" in out
        assert "todo_hard" in out
        assert "done" in out

    def test_update_priority_shows_change(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Priority change prints old → new priority."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--priority", "1",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "updated" in out
        assert "priority" in out
        assert "P2" in out
        assert "P1" in out

    def test_update_add_isbefore_shows_relationship(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--add-isbefore prints the relationship with item titles."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-alpha", title="Alpha task")
        _add_item(ops_dir, "WI-beta", title="Beta task")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-alpha", "--add-isbefore", "WI-beta",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "updated" in out
        assert "blocks" in out.lower() or "isbefore" in out.lower()

    def test_update_workspace_add_isbefore_to_stealth_rejected(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """WI-sohot: update --add-isbefore from workspace to stealth fails."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        stealth_dir = tracker_root / "tracker-workspace" / "stealth"
        _add_item(ops_dir, "WI-alpha", title="Alpha task")
        _add_item(stealth_dir, "WI-stealth", title="Stealth target")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-alpha", "--add-isbefore", "WI-stealth",
            ])
        assert exc.value.code == EXIT_USER_ERROR
        assert "stealth" in capsys.readouterr().err.lower()

    def test_update_remove_isbefore_skips_tier_check(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Removal narrows refs and never creates a cross-tier link, so the
        tier guard does not apply to --remove-isbefore."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        stealth_dir = tracker_root / "tracker-workspace" / "stealth"
        _add_item(ops_dir, "WI-alpha", title="Alpha")
        _add_item(stealth_dir, "WI-stealth", title="Stealth")
        # remove-isbefore on a stealth target succeeds (it's a no-op since
        # alpha never had stealth in its isbefore, but the guard must not
        # synthesize a "cross-tier" rejection here).
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-alpha", "--remove-isbefore", "WI-stealth",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_update_add_blocked_by_canonical_target_to_stealth_blocker_rejected(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """WI-sohot: --add-blocked-by puts isbefore on the BLOCKER. If the
        target is stealth and the blocker is canonical/workspace, the blocker
        would gain a cross-tier isbefore reference — reject."""
        tracker_root = _setup_tracker(tmp_path)
        ws_dir = tracker_root / "tracker-workspace" / ".ops"
        stealth_dir = tracker_root / "tracker-workspace" / "stealth"
        _add_item(ws_dir, "WI-blocker", title="Workspace blocker")
        _add_item(stealth_dir, "WI-stealth-target", title="Stealth target")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-stealth-target",
                "--add-blocked-by", "WI-blocker",
            ])
        assert exc.value.code == EXIT_USER_ERROR
        assert "stealth" in capsys.readouterr().err.lower()

    def test_update_remove_isbefore_shows_relationship(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--remove-isbefore prints the unlinked relationship."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-alpha", title="Alpha task")
        _add_item(ops_dir, "WI-beta", title="Beta task")
        # First add the isbefore link
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-alpha", "--add-isbefore", "WI-beta",
            ])
        capsys.readouterr()  # clear
        # Now remove it
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-alpha", "--remove-isbefore", "WI-beta",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "updated" in out
        assert "no longer" in out.lower() or "removed" in out.lower()

    def test_update_parent_shows_relationship(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--parent prints the parent relationship."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-child", title="Child item")
        _add_item(ops_dir, "META-parent", kind="meta_invariant",
                  title="Parent meta")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-child", "--parent", "META-parent",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "updated" in out
        assert "parent" in out.lower()

    def test_update_tags_shows_change(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Tag mutations print which tags were added/removed."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--add-tag", "important",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "updated" in out
        assert "important" in out

    def test_update_json_includes_changes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """JSON mode includes structured change details."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test",
                  status="todo_hard")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "update", "WI-test", "--status", "done",
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert "changes" in data

    def test_discuss_shows_confirmation(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Discuss command prints confirmation with message count."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test",
                  title="Test item")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "discuss", "WI-test", "My discussion message",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "discussed" in out

    def test_update_multiple_changes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Multiple mutations in one update all appear in output."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test",
                  status="todo_hard")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--status", "done",
                "--priority", "1",
                "--add-tag", "resolved",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "status" in out
        assert "priority" in out
        assert "resolved" in out

    def test_update_remove_tag_shows_change(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Tag removal prints which tag was removed."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        # Add tag first
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--add-tag", "obsolete",
            ])
        capsys.readouterr()
        # Remove it
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--remove-tag", "obsolete",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "obsolete" in out

    def test_update_pr_ref_shows_change(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Setting pr_ref shows the new value."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--pr-ref", "#1234",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "pr_ref" in out
        assert "#1234" in out

    def test_update_field_change(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Custom field changes show the field name and value."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--field", "root_cause=race condition",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "root_cause" in out
        assert "race condition" in out


class TestDescribeChangesUnit:
    """Unit tests for _describe_changes and helpers."""

    def test_short_id_single_part(self) -> None:
        """IDs with fewer than 2 parts return unchanged."""
        assert _short_id("NOID") == "NOID"

    def test_short_id_normal(self) -> None:
        """Normal proquint IDs are shortened to prefix + first word."""
        assert _short_id("WI-mamuh-puduz-extra") == "WI-mamuh"

    def test_changes_to_dict_no_colon(self) -> None:
        """Lines without ': ' get field='unknown'."""
        result = _changes_to_dict(["something happened"])
        assert result[0]["field"] == "unknown"
        assert result[0]["detail"] == "something happened"

    def test_describe_parent_removed(self) -> None:
        """Parent removal is reported."""
        old = CompiledItem(
            id="WI-a", kind="work_item", title="A", status="todo_hard",
            parent="META-parent",
        )
        new = CompiledItem(
            id="WI-a", kind="work_item", title="A", status="todo_hard",
            parent=None,
        )

        class FakeTS:
            def get(self, item_id: str) -> CompiledItem:
                return CompiledItem(
                    id=item_id, kind="meta_invariant",
                    title="Parent", status="todo_hard",
                )

        changes = _describe_changes(old, new, FakeTS())  # type: ignore[arg-type]
        assert any("removed" in c for c in changes)

    def test_describe_pr_ref_removed(self) -> None:
        """PR ref removal is reported."""
        old = CompiledItem(
            id="WI-a", kind="work_item", title="A", status="todo_hard",
            pr_ref="#100",
        )
        new = CompiledItem(
            id="WI-a", kind="work_item", title="A", status="todo_hard",
            pr_ref=None,
        )

        class FakeTS:
            pass

        changes = _describe_changes(old, new, FakeTS())  # type: ignore[arg-type]
        assert any("pr_ref" in c and "removed" in c for c in changes)

    def test_describe_duplicate_of_removed(self) -> None:
        """Duplicate-of removal is reported."""
        old = CompiledItem(
            id="WI-a", kind="work_item", title="A", status="todo_hard",
            duplicate_of=["WI-other"],
        )
        new = CompiledItem(
            id="WI-a", kind="work_item", title="A", status="todo_hard",
            duplicate_of=[],
        )

        class FakeTS:
            def get(self, item_id: str) -> CompiledItem:
                return CompiledItem(
                    id=item_id, kind="work_item",
                    title="Other", status="todo_hard",
                )

        changes = _describe_changes(old, new, FakeTS())  # type: ignore[arg-type]
        assert any("duplicate_of" in c and "removed" in c for c in changes)

    def test_describe_not_duplicate_of_added_and_removed(self) -> None:
        """Not-duplicate-of add and removal are reported."""
        old = CompiledItem(
            id="WI-a", kind="work_item", title="A", status="todo_hard",
            not_duplicate_of=["WI-old"],
        )
        new = CompiledItem(
            id="WI-a", kind="work_item", title="A", status="todo_hard",
            not_duplicate_of=["WI-new"],
        )

        class FakeTS:
            def get(self, item_id: str) -> CompiledItem:
                return CompiledItem(
                    id=item_id, kind="work_item",
                    title="Item", status="todo_hard",
                )

        changes = _describe_changes(old, new, FakeTS())  # type: ignore[arg-type]
        assert any("not_duplicate_of" in c and "removed" in c for c in changes)
        assert any("not_duplicate_of" in c and "+" in c for c in changes)

    def test_describe_field_removed(self) -> None:
        """Custom field removal is reported."""
        old = CompiledItem(
            id="WI-a", kind="work_item", title="A", status="todo_hard",
            fields={"root_cause": "bug"},
        )
        new = CompiledItem(
            id="WI-a", kind="work_item", title="A", status="todo_hard",
            fields={},
        )

        class FakeTS:
            pass

        changes = _describe_changes(old, new, FakeTS())  # type: ignore[arg-type]
        assert any("root_cause" in c and "removed" in c for c in changes)


# ---------------------------------------------------------------------------
# Batch command
# ---------------------------------------------------------------------------


class TestDepsCommand:
    """Tests for the deps subcommand (dependency graph view)."""

    def test_deps_shows_item(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """deps shows the target item."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-target",
                  title="Target item")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "deps", "WI-target",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "Target item" in out

    def test_deps_shows_children(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """deps shows children of the target."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "META-parent", kind="meta_invariant",
                  title="Parent meta")
        _add_item(ops_dir, "WI-child1", title="Child 1")
        _add_item(ops_dir, "WI-child2", title="Child 2")
        # Set parents
        for child in ("WI-child1", "WI-child2"):
            with pytest.raises(SystemExit):
                main([
                    "--tracker-root", str(tracker_root),
                    "update", child, "--parent", "META-parent",
                ])
        capsys.readouterr()
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "deps", "META-parent",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "Child 1" in out
        assert "Child 2" in out

    def test_deps_shows_blocking(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """deps shows isbefore/blocked-by relationships."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-blocker", title="Blocker task")
        _add_item(ops_dir, "WI-blocked", title="Blocked task")
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-blocker", "--add-isbefore", "WI-blocked",
            ])
        capsys.readouterr()
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "deps", "WI-blocker",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "Blocked task" in out

    def test_deps_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """deps in JSON mode returns structured output."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-target",
                  title="Target")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "deps", "WI-target",
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["id"] == "WI-target"

    def test_deps_no_relationships(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """deps with no relationships shows the item alone."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-lonely",
                  title="Lonely item")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "deps", "WI-lonely",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "Lonely item" in out

    def test_deps_blocked_by(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """deps shows items that block the target (inverse isbefore)."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "INV-target", kind="invariant", title="Target inv")
        _add_item(ops_dir, "WI-blocker", title="Blocker task")
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-blocker", "--add-isbefore", "INV-target",
            ])
        capsys.readouterr()
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "deps", "INV-target",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "blocked by" in out
        assert "Blocker task" in out

    def test_deps_json_full(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """JSON mode includes parent, children, blocks, and blocked_by."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "META-top", kind="meta_invariant", title="Top")
        _add_item(ops_dir, "WI-child", title="Child")
        _add_item(ops_dir, "WI-target2", title="Target 2")
        _add_item(ops_dir, "WI-blocker2", title="Blocker 2")
        # child's parent = META-top
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-child", "--parent", "META-top",
            ])
        # child blocks WI-target2
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-child", "--add-isbefore", "WI-target2",
            ])
        # WI-blocker2 blocks WI-child
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-blocker2", "--add-isbefore", "WI-child",
            ])
        capsys.readouterr()
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "deps", "WI-child",
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["parent"]["title"] == "Top"
        assert data["blocks"][0]["title"] == "Target 2"
        assert data["blocked_by"][0]["title"] == "Blocker 2"

    def test_deps_json_children(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """JSON mode includes children array."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "META-top2", kind="meta_invariant", title="Top")
        _add_item(ops_dir, "WI-kid", title="Kid item")
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-kid", "--parent", "META-top2",
            ])
        capsys.readouterr()
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "deps", "META-top2",
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert "children" in data
        assert data["children"][0]["title"] == "Kid item"

    def test_deps_text_with_parent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Text mode shows parent relationship."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "META-parent2", kind="meta_invariant", title="Parent")
        _add_item(ops_dir, "WI-child2", title="Child item")
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-child2", "--parent", "META-parent2",
            ])
        capsys.readouterr()
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "deps", "WI-child2",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "parent:" in out
        assert "Parent" in out


class TestBulkRelationships:
    """Tests for --add-blocked-by and --remove-blocked-by inverse operations."""

    def test_add_blocked_by(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--add-blocked-by sets isbefore links on the referenced items."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "INV-target", kind="invariant", title="Target inv")
        _add_item(ops_dir, "WI-blocker1", title="Blocker 1")
        _add_item(ops_dir, "WI-blocker2", title="Blocker 2")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "INV-target",
                "--add-blocked-by", "WI-blocker1",
                "--add-blocked-by", "WI-blocker2",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        # Both blockers should now have isbefore: [INV-target]
        assert "blocks" in out.lower() or "blocked-by" in out.lower()

    def test_add_blocked_by_sets_before_on_other_items(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Verify the isbefore link is actually set on the blocker items."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "INV-target", kind="invariant", title="Target")
        _add_item(ops_dir, "WI-blocker", title="Blocker")
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "update", "INV-target",
                "--add-blocked-by", "WI-blocker",
            ])
        # Verify by showing the blocker item
        capsys.readouterr()
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "show", "WI-blocker",
            ])
        show_out = capsys.readouterr().out
        assert "INV-target" in show_out

    def test_remove_blocked_by(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--remove-blocked-by removes isbefore links from the referenced items."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "INV-target", kind="invariant", title="Target inv")
        _add_item(ops_dir, "WI-blocker", title="Blocker")
        # First add the link
        with pytest.raises(SystemExit):
            main([
                "--tracker-root", str(tracker_root),
                "update", "INV-target",
                "--add-blocked-by", "WI-blocker",
            ])
        capsys.readouterr()
        # Now remove it
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "INV-target",
                "--remove-blocked-by", "WI-blocker",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "no longer" in out.lower() or "removed" in out.lower()

    def test_blocked_by_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--add-blocked-by works in JSON mode."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "INV-target", kind="invariant", title="Target")
        _add_item(ops_dir, "WI-blocker", title="Blocker")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "update", "INV-target",
                "--add-blocked-by", "WI-blocker",
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True


class TestBatchCommand:
    """Tests for the batch subcommand."""

    def test_batch_from_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Batch reads commands from a .htrac file."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        batch_file = tmp_path / "ops.htrac"
        batch_file.write_text(
            "update WI-test --status done\n"
            "update WI-test --priority 1\n"
        )
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "batch", str(batch_file),
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "2/2" in out or "2 succeeded" in out

    def test_batch_from_stdin(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Batch reads commands from stdin with '-'."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        input_text = "update WI-test --status done\n"
        with pytest.raises(SystemExit) as exc:
            with patch("sys.stdin", new=__import__("io").StringIO(input_text)):
                main([
                    "--tracker-root", str(tracker_root), "--no-auto-sync",
                    "batch", "-",
                ])
        assert exc.value.code == EXIT_SUCCESS

    def test_batch_skips_comments_and_blanks(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Comments and blank lines are ignored."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        batch_file = tmp_path / "ops.htrac"
        batch_file.write_text(
            "# This is a comment\n"
            "\n"
            "update WI-test --status done\n"
            "  # Indented comment\n"
        )
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "batch", str(batch_file),
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "1/1" in out or "1 succeeded" in out

    def test_batch_partial_failure(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """If one operation fails, others still run and exit is non-zero."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        batch_file = tmp_path / "ops.htrac"
        batch_file.write_text(
            "update WI-test --status done\n"
            "update WI-nonexistent --status done\n"
            "update WI-test --priority 1\n"
        )
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "batch", str(batch_file),
            ])
        assert exc.value.code != EXIT_SUCCESS
        out = capsys.readouterr().out
        err = capsys.readouterr().err
        # Should report which operations failed
        assert "FAIL" in out or "failed" in out.lower() or "FAIL" in err or "failed" in err.lower()

    def test_batch_json_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """JSON mode returns structured results."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        batch_file = tmp_path / "ops.htrac"
        batch_file.write_text("update WI-test --status done\n")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "--json",
                "batch", str(batch_file),
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert "results" in data
        assert data["results"][0]["ok"] is True

    def test_batch_empty_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Empty batch file succeeds with 0 operations."""
        tracker_root = _setup_tracker(tmp_path)
        batch_file = tmp_path / "empty.htrac"
        batch_file.write_text("# Nothing to do\n")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "batch", str(batch_file),
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_batch_discuss_command(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Batch supports discuss commands."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        batch_file = tmp_path / "ops.htrac"
        batch_file.write_text(
            'update WI-test --status done\n'
            'discuss WI-test "Fixed in PR #100"\n'
        )
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "batch", str(batch_file),
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_batch_file_not_found(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Missing batch file gives user error."""
        tracker_root = _setup_tracker(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "batch", str(tmp_path / "nonexistent.htrac"),
            ])
        assert exc.value.code == EXIT_USER_ERROR

    def test_batch_disallowed_command(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Read-only commands are rejected inside batch."""
        tracker_root = _setup_tracker(tmp_path)
        batch_file = tmp_path / "ops.htrac"
        batch_file.write_text("show WI-test\n")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "batch", str(batch_file),
            ])
        assert exc.value.code == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "not allowed" in err

    def test_batch_invalid_args(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Invalid arguments in a batch line are reported."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        batch_file = tmp_path / "ops.htrac"
        batch_file.write_text(
            "update WI-test --status done\n"
            "update --invalid-flag\n"
        )
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "batch", str(batch_file),
            ])
        assert exc.value.code == EXIT_USER_ERROR

    def test_batch_empty_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Empty batch in JSON mode returns proper structure."""
        tracker_root = _setup_tracker(tmp_path)
        batch_file = tmp_path / "empty.htrac"
        batch_file.write_text("# All comments\n\n")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "--json",
                "batch", str(batch_file),
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["results"] == []

    def test_batch_item_not_found_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """ItemNotFoundError in batch is caught and reported per-op."""
        tracker_root = _setup_tracker(tmp_path)
        batch_file = tmp_path / "ops.htrac"
        batch_file.write_text("update WI-nonexistent --status done\n")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "batch", str(batch_file),
            ])
        assert exc.value.code == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "FAIL" in err

    def test_batch_handler_nonzero_exit(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Handler returning non-zero exit code is reported as failure."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        batch_file = tmp_path / "ops.htrac"
        # --field without = gives EXIT_USER_ERROR from _cmd_update
        batch_file.write_text("update WI-test --field badformat\n")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "batch", str(batch_file),
            ])
        assert exc.value.code == EXIT_USER_ERROR


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
# Freeze/unfreeze commands
# ---------------------------------------------------------------------------


class TestFreezeUnfreeze:
    def test_freeze(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                    mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "freeze", "WI-test",
            ])
        assert exc.value.code == EXIT_SUCCESS
        assert "frozen" in capsys.readouterr().out

    def test_freeze_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                         mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "freeze", "WI-test",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True

    def test_freeze_agent_denied(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                 mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "freeze", "WI-test",
            ])
        assert exc.value.code == EXIT_USER_ERROR

    def test_unfreeze(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                      mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-test")
        # Freeze first
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(tracker_root), "freeze", "WI-test"])
        capsys.readouterr()  # clear capture
        # Then unfreeze
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "unfreeze", "WI-test",
            ])
        assert exc.value.code == EXIT_SUCCESS
        assert "unfrozen" in capsys.readouterr().out

    def test_unfreeze_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                           mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-test")
        # Freeze first
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(tracker_root), "freeze", "WI-test"])
        capsys.readouterr()
        # Then unfreeze with JSON
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "unfreeze", "WI-test",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True

    def test_frozen_item_show(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                              mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        # Freeze
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(tracker_root), "freeze", "WI-test"])
        capsys.readouterr()
        # Show
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "show", "WI-test",
            ])
        assert exc.value.code == EXIT_SUCCESS
        assert "FROZEN" in capsys.readouterr().out

    def test_frozen_item_show_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                   mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        # Freeze
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(tracker_root), "freeze", "WI-test"])
        capsys.readouterr()
        # Show JSON
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "show", "WI-test",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = json.loads(capsys.readouterr().out)
        assert out["frozen"] is True

    def test_frozen_item_agent_update_blocked(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Agent update on frozen item → FrozenItemError → exit 1."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-test")
        # Manually create sentinel (agent can't call freeze)
        import shutil
        ops_file = ops_dir / ".WI-test.ops"
        frozen_file = ops_dir / ".WI-test.frozen"
        shutil.copy2(ops_file, frozen_file)
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test", "--priority", "0",
            ])
        assert exc.value.code == EXIT_USER_ERROR

    def test_repair_drift(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_human_uid: None,
    ) -> None:
        """Human repair-drift via CLI → exit 0."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-test")
        # Freeze
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(tracker_root), "freeze", "WI-test"])
        capsys.readouterr()
        # Tamper .ops directly
        ops_file = ops_dir / ".WI-test.ops"
        with open(ops_file, "a") as f:
            f.write("# tampered\n")
        # Repair
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "repair-drift", "WI-test",
            ])
        assert exc.value.code == EXIT_SUCCESS
        assert "drift repaired" in capsys.readouterr().out

    def test_repair_drift_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_human_uid: None,
    ) -> None:
        """JSON mode → {"ok": true}."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-test")
        # Freeze
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(tracker_root), "freeze", "WI-test"])
        capsys.readouterr()
        # Tamper .ops directly
        ops_file = ops_dir / ".WI-test.ops"
        with open(ops_file, "a") as f:
            f.write("# tampered\n")
        # Repair with JSON
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "repair-drift", "WI-test",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True


# ---------------------------------------------------------------------------
# Delete command
# ---------------------------------------------------------------------------


class TestDeleteCommand:
    def test_delete_as_human(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                             mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "delete", "WI-test",
            ])
        assert exc.value.code == EXIT_SUCCESS
        assert "deleted" in capsys.readouterr().out

    def test_delete_as_human_json(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                  mock_human_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json", "--no-auto-sync",
                "delete", "WI-test",
            ])
        assert exc.value.code == EXIT_SUCCESS
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True

    def test_delete_as_agent_denied(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                    mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--no-auto-sync",
                "delete", "WI-test",
            ])
        assert exc.value.code == EXIT_USER_ERROR

    def test_delete_in_mutation_commands(self) -> None:
        assert "delete" in _MUTATION_COMMANDS


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

    def test_validate_deep_similar(self, tmp_path: Path, capsys: pytest.CaptureFixture,
                                   mock_agent_uid: None) -> None:
        tracker_root = _setup_tracker(tmp_path)
        with patch("hypergumbo_tracker.validation._check_embedding_duplicates") as mock_check:
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "validate", "--deep-similar"])
            assert exc.value.code == EXIT_SUCCESS
            mock_check.assert_called_once()


# ---------------------------------------------------------------------------
# Utility commands
# ---------------------------------------------------------------------------


class TestUtilityCommands:
    def test_cache_rebuild(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cache-rebuild rebuilds the live XDG caches that every other
        read path uses. The test forces ``TRACKER_CACHE_DIR`` to a tmp
        path so ``_get_cache_dir`` returns a known location without
        needing a real git repo fingerprint.
        """
        tracker_root = _setup_tracker(tmp_path)
        cache_dir = tmp_path / "xdg_cache"
        monkeypatch.setenv("TRACKER_CACHE_DIR", str(cache_dir))
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "cache-rebuild"])
        assert exc.value.code == EXIT_SUCCESS
        assert "cache rebuilt" in capsys.readouterr().out
        # Verify the caches were actually rebuilt at the XDG-style path
        # the dispatcher wired, NOT at the legacy ``.agent/.cache-*.db``
        # path the old hardcoded subcommand used to write to.
        for tier_name in ("canonical", "workspace", "stealth"):
            assert (cache_dir / f"{tier_name}.cache.db").exists()
        assert not (tracker_root / ".cache-canonical.db").exists()

    def test_cache_rebuild_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        cache_dir = tmp_path / "xdg_cache"
        monkeypatch.setenv("TRACKER_CACHE_DIR", str(cache_dir))
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "cache-rebuild"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True

    def test_cache_rebuild_no_cache_dir(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``_get_cache_dir`` returns None (no git repo, no
        ``TRACKER_CACHE_DIR`` override) the dispatcher doesn't wire
        any Cache instances onto the TrackerSet, so ``ts._caches`` is
        None and ``cache-rebuild`` must exit with a user error and a
        clear message on stderr instead of crashing or silently
        succeeding.
        """
        tracker_root = _setup_tracker(tmp_path)
        monkeypatch.delenv("TRACKER_CACHE_DIR", raising=False)
        # Ensure _get_cache_dir returns None regardless of whether
        # pytest happens to run inside a git repo.
        import hypergumbo_tracker.cli as cli_mod
        monkeypatch.setattr(cli_mod, "_get_cache_dir", lambda _root: None)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "cache-rebuild"])
        assert exc.value.code == EXIT_USER_ERROR
        captured = capsys.readouterr()
        assert "no cache directory available" in captured.err

    def test_cache_rebuild_no_cache_dir_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """JSON variant of the no-cache-dir error path: emits the error
        object on stdout instead of a message on stderr, still returns
        EXIT_USER_ERROR.
        """
        tracker_root = _setup_tracker(tmp_path)
        monkeypatch.delenv("TRACKER_CACHE_DIR", raising=False)
        import hypergumbo_tracker.cli as cli_mod
        monkeypatch.setattr(cli_mod, "_get_cache_dir", lambda _root: None)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "cache-rebuild"])
        assert exc.value.code == EXIT_USER_ERROR
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is False
        assert "no cache directory available" in data["error"]

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
            "statuses": ["todo_hard", "todo_soft", "in_progress", "needs_human_review", "done", "wont_do"],
            "stop_hook": {
                "blocking_statuses": ["todo_hard", "todo_soft"],
                "resolved_statuses": ["done", "wont_do"],
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


# ---------------------------------------------------------------------------
# _get_cache_dir
# ---------------------------------------------------------------------------


class TestGetCacheDir:
    def test_returns_none_no_git(self, tmp_path: Path) -> None:
        """No git repo → returns None."""
        from hypergumbo_tracker.cli import _get_cache_dir
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        result = _get_cache_dir(tracker_root)
        assert result is None

    @staticmethod
    def _init_git_repo(repo: Path) -> None:
        """Set up a minimal git repo with one commit."""
        import subprocess
        def _git(*args: str) -> None:
            subprocess.run(
                ["git", *args],  # noqa: S607
                cwd=str(repo), capture_output=True, check=True,
            )
        _git("init", str(repo))
        _git("config", "user.email", "t@t.com")
        _git("config", "user.name", "T")
        (repo / "f.txt").write_text("x")
        _git("add", "f.txt")
        _git("commit", "-m", "i")

    def test_returns_path_with_git(self, tmp_path: Path) -> None:
        """Git repo → returns XDG-based cache path."""
        from hypergumbo_tracker.cli import _get_cache_dir

        self._init_git_repo(tmp_path)

        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        result = _get_cache_dir(tracker_root)
        assert result is not None
        assert "hypergumbo-tracker" in str(result)

    def test_returns_none_git_dir_but_no_git(self, tmp_path: Path) -> None:
        """Git dir exists as file but no real git → fingerprint 'no-git' → None."""
        from hypergumbo_tracker.cli import _get_cache_dir
        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        # Create a .git file (like a worktree) but no real git
        git_file = tmp_path / ".git"
        git_file.write_text("gitdir: /nonexistent/path\n")
        result = _get_cache_dir(tracker_root)
        assert result is None

    def test_xdg_cache_home_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """XDG_CACHE_HOME env var is respected."""
        from hypergumbo_tracker.cli import _get_cache_dir

        self._init_git_repo(tmp_path)

        custom_cache = tmp_path / "custom_cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(custom_cache))

        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        result = _get_cache_dir(tracker_root)
        assert result is not None
        assert str(custom_cache) in str(result)

    def test_tracker_cache_dir_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TRACKER_CACHE_DIR env var overrides default cache path."""
        from hypergumbo_tracker.cli import _get_cache_dir

        custom_dir = tmp_path / "my_cache"
        monkeypatch.setenv("TRACKER_CACHE_DIR", str(custom_dir))

        tracker_root = tmp_path / ".agent"
        tracker_root.mkdir()
        result = _get_cache_dir(tracker_root)
        assert result == custom_dir


# ---------------------------------------------------------------------------
# D6: Four separate duplicate flags
# ---------------------------------------------------------------------------


class TestDuplicateFlags:
    def test_remove_duplicate_of(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--remove-duplicate-of removes a duplicate-of link."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-test")
        _add_item(ops_dir, "WI-dup", title="Dup item")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--remove-duplicate-of", "WI-dup",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_remove_not_duplicate_of(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--remove-not-duplicate-of removes a not-duplicate-of link."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        _add_item(ops_dir, "WI-test")
        _add_item(ops_dir, "WI-nodup", title="Not-dup item")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--remove-not-duplicate-of", "WI-nodup",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_remove_duplicate_of_resolves_prefix(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--remove-duplicate-of with a short prefix resolves to the full ID."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        full_id = "WI-abcde-fghij-klmno-pqrst-uvwxy-zabcd-efghi-jklmn"
        _add_item(ops_dir, "WI-test")
        _add_item(ops_dir, full_id, title="Dup target")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--remove-duplicate-of", "WI-abcde",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_remove_duplicate_of_nonexistent_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--remove-duplicate-of with a nonexistent prefix fails."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--remove-duplicate-of", "WI-nonexistent",
            ])
        assert exc.value.code == EXIT_USER_ERROR

    def test_remove_not_duplicate_of_resolves_prefix(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--remove-not-duplicate-of with a short prefix resolves to the full ID."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        full_id = "WI-abcde-fghij-klmno-pqrst-uvwxy-zabcd-efghi-jklmn"
        _add_item(ops_dir, "WI-test")
        _add_item(ops_dir, full_id, title="Not-dup target")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--remove-not-duplicate-of", "WI-abcde",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_remove_not_duplicate_of_nonexistent_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--remove-not-duplicate-of with a nonexistent prefix fails."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--remove-not-duplicate-of", "WI-nonexistent",
            ])
        assert exc.value.code == EXIT_USER_ERROR


class TestRemoveIsbeforePrefixResolution:
    """--remove-isbefore must resolve short prefixes like --add-isbefore does."""

    def test_remove_isbefore_resolves_prefix(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--remove-isbefore with a short prefix resolves to the full ID."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        full_id = "WI-abcde-fghij-klmno-pqrst-uvwxy-zabcd-efghi-jklmn"
        _add_item(ops_dir, "WI-test")
        _add_item(ops_dir, full_id, title="Isbefore target")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--remove-isbefore", "WI-abcde",
            ])
        assert exc.value.code == EXIT_SUCCESS

    def test_remove_isbefore_nonexistent_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--remove-isbefore with a nonexistent prefix fails."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root),
                "update", "WI-test",
                "--remove-isbefore", "WI-nonexistent",
            ])
        assert exc.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# Cache wiring in main()
# ---------------------------------------------------------------------------


class TestCacheWiring:
    """Exercise the cache initialization path in main() (cli.py:912-920)."""

    def test_main_wires_caches_when_cache_dir_set(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """When _get_cache_dir returns a path, main() creates Cache instances."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        with patch(
            "hypergumbo_tracker.cli._get_cache_dir",
            return_value=cache_dir,
        ):
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "list"])
        assert exc.value.code == EXIT_SUCCESS

        # Verify cache DB files were created for each tier
        cache_files = list(cache_dir.glob("*.cache.db*"))
        assert len(cache_files) > 0


# ---------------------------------------------------------------------------
# GNU Screen altscreen detection
# ---------------------------------------------------------------------------


class TestDetectScreenAltscreenOff:
    """Tests for _detect_screen_altscreen_off helper."""

    def test_no_sty_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Outside GNU Screen (no STY env var) → False."""
        monkeypatch.delenv("STY", raising=False)
        assert _detect_screen_altscreen_off() is False

    def test_sty_set_altscreen_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Inside Screen, altscreen off → True."""
        monkeypatch.setenv("STY", "12345.pts-0.host")
        with patch("hypergumbo_tracker.cli.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "altscreen off\n"
            assert _detect_screen_altscreen_off() is True

    def test_sty_set_altscreen_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Inside Screen, altscreen on → False."""
        monkeypatch.setenv("STY", "12345.pts-0.host")
        with patch("hypergumbo_tracker.cli.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "altscreen on\n"
            assert _detect_screen_altscreen_off() is False

    def test_sty_set_file_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Inside Screen, screen binary not found → True (assume off)."""
        monkeypatch.setenv("STY", "12345.pts-0.host")
        with patch(
            "hypergumbo_tracker.cli.subprocess.run",
            side_effect=FileNotFoundError("screen"),
        ):
            assert _detect_screen_altscreen_off() is True

    def test_sty_set_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Inside Screen, query times out → True (assume off)."""
        import subprocess as sp
        monkeypatch.setenv("STY", "12345.pts-0.host")
        with patch(
            "hypergumbo_tracker.cli.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="screen", timeout=5),
        ):
            assert _detect_screen_altscreen_off() is True


class TestPrintScreenWarning:
    """Tests for _print_screen_warning helper."""

    def test_prints_to_stderr(self, capsys: pytest.CaptureFixture) -> None:
        _print_screen_warning()
        captured = capsys.readouterr()
        assert "altscreen" in captured.err
        assert "~/.screenrc" in captured.err


# ---------------------------------------------------------------------------
# TUI with GNU Screen integration
# ---------------------------------------------------------------------------


class TestTuiScreenIntegration:
    """Tests for _cmd_tui's Screen altscreen-off handling."""

    def test_tui_with_altscreen_off(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When altscreen is off: warn before, clear + warn after."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")

        with patch(
            "hypergumbo_tracker.cli._detect_screen_altscreen_off",
            return_value=True,
        ), patch(
            "hypergumbo_tracker.cli.time.sleep",
        ) as mock_sleep, patch(
            "hypergumbo_tracker.tui.TrackerApp.run",
        ):
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "tui"])
            assert exc.value.code == EXIT_SUCCESS

        mock_sleep.assert_called_once_with(3)

        captured = capsys.readouterr()
        # Warning printed to stderr twice (before and after)
        assert captured.err.count("altscreen") >= 2
        # Clear sequence written to stdout
        assert "\033[2J\033[H" in captured.out

    def test_tui_without_screen(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """When not in Screen: no warning, no clear."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")

        with patch(
            "hypergumbo_tracker.cli._detect_screen_altscreen_off",
            return_value=False,
        ), patch(
            "hypergumbo_tracker.tui.TrackerApp.run",
        ):
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "tui"])
            assert exc.value.code == EXIT_SUCCESS

        captured = capsys.readouterr()
        assert "altscreen" not in captured.err
        assert "\033[2J" not in captured.out


# ---------------------------------------------------------------------------
# _format_item_full discussion rendering
# ---------------------------------------------------------------------------


class TestFormatItemFullDiscussion:
    """Tests for discussion entry rendering in _format_item_full."""

    def test_no_discussion(self) -> None:
        item = CompiledItem(
            id="WI-test", kind="work_item", title="Test",
            status="todo_hard", discussion=[],
        )
        result = _format_item_full(item)
        assert "discussion: (none)" in result

    def test_with_discussion_entries(self) -> None:
        item = CompiledItem(
            id="WI-test", kind="work_item", title="Test",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="human", actor="jgstern",
                    at="2026-01-15T10:00:00Z", message="Please investigate",
                ),
                DiscussionEntry(
                    by="agent", actor="test_agent",
                    at="2026-01-15T10:05:00Z", message="On it",
                ),
            ],
        )
        result = _format_item_full(item)
        assert "(2 entries)" in result
        assert "jgstern (human): Please investigate" in result
        assert "test_agent (agent): On it" in result

    def test_summary_entry_prefix(self) -> None:
        item = CompiledItem(
            id="WI-test", kind="work_item", title="Test",
            status="todo_hard",
            discussion=[
                DiscussionEntry(
                    by="agent", actor="test_agent",
                    at="2026-01-15T10:00:00Z", message="Summary text",
                    is_summary=True,
                ),
            ],
        )
        result = _format_item_full(item)
        assert "[summary] Summary text" in result


# ---------------------------------------------------------------------------
# check-messages command
# ---------------------------------------------------------------------------


def _add_item_with_discussion(
    ops_dir: Path, item_id: str, discussion_ops: list[dict],
    status: str = "todo_hard",
) -> None:
    """Write an ops file with a create op followed by discussion ops."""
    lines = [textwrap.dedent(f"""\
        - op: create
          at: "2026-01-01T00:00:00Z"
          by: agent
          actor: test_agent
          clock: 1
          nonce: a1b2
          data:
            kind: work_item
            title: "Item {item_id}"
            status: {status}
            priority: 2
    """)]
    for i, disc in enumerate(discussion_ops):
        lines.append(textwrap.dedent(f"""\
        - op: discuss
          at: "{disc['at']}"
          by: {disc['by']}
          actor: {disc['actor']}
          clock: {10 + i}
          nonce: d{i:03d}
          message: "{disc['message']}"
        """))
    (ops_dir / f".{item_id}.ops").write_text("".join(lines))


class TestCheckMessages:
    def test_no_unread_messages(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker" / ".ops", "WI-a")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "check-messages"])
        assert exc.value.code == EXIT_SUCCESS
        assert "(no unread human messages)" in capsys.readouterr().out

    def test_item_with_trailing_human_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item_with_discussion(
            tracker_root / "tracker-workspace" / ".ops", "WI-msg",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "agent", "actor": "test_agent",
                 "message": "Working on it"},
                {"at": "2026-01-15T11:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "Please check edge case"},
            ],
        )
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "check-messages"])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "WI-msg" in out
        assert "Please check edge case" in out

    def test_json_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item_with_discussion(
            tracker_root / "tracker-workspace" / ".ops", "WI-j",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "Hello"},
            ],
        )
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "check-messages"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "WI-j"
        assert len(data[0]["unread_messages"]) == 1

    def test_autolimit(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        _add_item_with_discussion(
            tracker_root / "tracker-workspace" / ".ops", "WI-al",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "msg1"},
                {"at": "2026-01-15T10:01:00Z", "by": "human", "actor": "jgstern",
                 "message": "msg2"},
                {"at": "2026-01-15T10:02:00Z", "by": "human", "actor": "jgstern",
                 "message": "msg3"},
            ],
        )
        with pytest.raises(SystemExit) as exc:
            main([
                "--tracker-root", str(tracker_root), "--json",
                "check-messages", "--autolimit", "1",
            ])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert len(data[0]["unread_messages"]) == 1
        assert data[0]["unread_messages"][0]["message"] == "msg3"

    def test_multiple_items_text_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """Multiple items with unread messages separated by blank lines."""
        tracker_root = _setup_tracker(tmp_path)
        ws_ops = tracker_root / "tracker-workspace" / ".ops"
        _add_item_with_discussion(
            ws_ops, "WI-a",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "msg a"},
            ],
        )
        _add_item_with_discussion(
            ws_ops, "WI-b",
            discussion_ops=[
                {"at": "2026-01-15T11:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "msg b"},
            ],
        )
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "check-messages"])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "WI-a" in out
        assert "WI-b" in out
        assert "msg a" in out
        assert "msg b" in out

    def test_workspace_scope_excludes_canonical(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        tracker_root = _setup_tracker(tmp_path)
        # Human message in canonical tier
        _add_item_with_discussion(
            tracker_root / "tracker" / ".ops", "WI-can",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "canonical msg"},
            ],
        )
        # Human message in workspace tier
        _add_item_with_discussion(
            tracker_root / "tracker-workspace" / ".ops", "WI-ws",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "workspace msg"},
            ],
        )
        # Write a workspace-scoped config
        import yaml
        config_data = {
            "kinds": {"work_item": {"prefix": "WI"}},
            "statuses": ["todo_hard", "todo_soft", "in_progress", "needs_human_review", "done", "wont_do"],
            "stop_hook": {
                "blocking_statuses": ["todo_hard", "todo_soft"],
                "resolved_statuses": ["done", "wont_do"],
                "scope": "workspace",
            },
        }
        (tracker_root / "tracker" / "config.yaml").write_text(yaml.dump(config_data))
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "check-messages"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        ids = [item["id"] for item in data]
        assert "WI-ws" in ids
        assert "WI-can" not in ids


    def test_context_includes_description_and_prior(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """check-messages includes description, pr_ref, prior discussion (WI-radab)."""
        tracker_root = _setup_tracker(tmp_path)
        ops_dir = tracker_root / "tracker-workspace" / ".ops"
        # Long description (>500 chars) to test truncation
        long_desc = "A" * 600
        # Long message (>200 chars) to test preview truncation
        long_msg = "B" * 250
        ops_content = textwrap.dedent(f"""\
        - op: create
          at: "2026-01-01T00:00:00Z"
          by: agent
          actor: test_agent
          clock: 1
          nonce: a1b2
          data:
            kind: work_item
            title: "Contextual Item"
            status: todo_hard
            priority: 2
            description: "{long_desc}"
            pr_ref: "PR #999"
        - op: discuss
          at: "2026-01-10T10:00:00Z"
          by: agent
          actor: test_agent
          clock: 10
          nonce: d001
          message: "{long_msg}"
        - op: discuss
          at: "2026-01-11T10:00:00Z"
          by: human
          actor: jgstern
          clock: 11
          nonce: d002
          message: "Human question"
        """)
        (ops_dir / ".WI-ctx.ops").write_text(ops_content)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "check-messages"])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        # Description truncated with ...
        assert "..." in out
        assert "AAAA" in out
        # pr_ref shown
        assert "PR #999" in out
        # Prior discussion with long message truncated
        assert "BBBB" in out
        assert "Human question" in out

    def test_context_json_includes_description(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """check-messages --json includes description and prior_discussion (WI-radab)."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item_with_discussion(
            tracker_root / "tracker-workspace" / ".ops", "WI-jctx",
            discussion_ops=[
                {"at": "2026-01-10T10:00:00Z", "by": "agent", "actor": "test_agent",
                 "message": "Prior agent note"},
                {"at": "2026-01-11T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "Human asks question"},
            ],
        )
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "--json", "check-messages"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert len(data) == 1
        assert "description" in data[0]
        assert "prior_discussion" in data[0]
        assert len(data[0]["prior_discussion"]) == 1  # agent note before human msg
        assert data[0]["prior_discussion"][0]["message"] == "Prior agent note"


# ---------------------------------------------------------------------------
# Auto-sync
# ---------------------------------------------------------------------------


def _make_completed_process(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Build a CompletedProcess for mocking subprocess.run."""
    return subprocess.CompletedProcess(
        args=["git"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class TestAutoSync:
    """Tests for _maybe_auto_sync and its wiring into main()."""

    def test_mutation_command_triggers_check(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A mutation command (add) calls _maybe_auto_sync on success."""
        tracker_root = _setup_tracker(tmp_path)
        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "0")  # disabled
        with patch(
            "hypergumbo_tracker.cli._maybe_auto_sync",
        ) as mock_sync:
            with pytest.raises(SystemExit) as exc:
                main([
                    "--tracker-root", str(tracker_root),
                    "add", "--kind", "work_item", "--title", "T",
                ])
            assert exc.value.code == EXIT_SUCCESS
            mock_sync.assert_called_once_with(tracker_root)

    def test_read_command_skips_auto_sync(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """A read-only command (list) does not call _maybe_auto_sync."""
        tracker_root = _setup_tracker(tmp_path)
        with patch(
            "hypergumbo_tracker.cli._maybe_auto_sync",
        ) as mock_sync:
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "list"])
            assert exc.value.code == EXIT_SUCCESS
            mock_sync.assert_not_called()

    def test_no_auto_sync_flag(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None,
    ) -> None:
        """--no-auto-sync prevents the check even for mutation commands."""
        tracker_root = _setup_tracker(tmp_path)
        with patch(
            "hypergumbo_tracker.cli._maybe_auto_sync",
        ) as mock_sync:
            with pytest.raises(SystemExit) as exc:
                main([
                    "--tracker-root", str(tracker_root),
                    "--no-auto-sync",
                    "add", "--kind", "work_item", "--title", "T",
                ])
            assert exc.value.code == EXIT_SUCCESS
            mock_sync.assert_not_called()

    def test_failed_command_skips_auto_sync(
        self, tmp_path: Path, mock_agent_uid: None,
    ) -> None:
        """A failed mutation command (exit != 0) skips auto-sync."""
        tracker_root = _setup_tracker(tmp_path)
        with patch(
            "hypergumbo_tracker.cli._maybe_auto_sync",
        ) as mock_sync:
            with pytest.raises(SystemExit) as exc:
                main([
                    "--tracker-root", str(tracker_root),
                    "update", "WI-nonexistent", "--status", "done",
                ])
            assert exc.value.code != EXIT_SUCCESS
            mock_sync.assert_not_called()

    def test_threshold_zero_disables(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TRACKER_AUTO_SYNC_THRESHOLD=0 disables auto-sync."""
        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "0")
        # Should return immediately without calling pending_sync_lines
        with patch(
            "hypergumbo_tracker.sync.pending_sync_lines",
        ) as mock_psl:
            _maybe_auto_sync(tmp_path)
            mock_psl.assert_not_called()

    def test_below_threshold_no_sync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines below threshold: no sync triggered."""
        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "50")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=10,
            ) as mock_psl:
                with patch(
                    "hypergumbo_tracker.sync.preflight_check",
                ) as mock_pre:
                    _maybe_auto_sync(tmp_path)
                    mock_psl.assert_called_once()
                    mock_pre.assert_not_called()

    def test_above_threshold_triggers_sync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Lines above threshold: preflight + do_sync are called."""
        from hypergumbo_tracker.sync import PreflightResult, SyncResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "10")

        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=15,
            ):
                with patch(
                    "hypergumbo_tracker.sync.preflight_check",
                ) as mock_pre:
                    mock_pre.return_value = PreflightResult(
                        ok=True,
                        repo_root=tmp_path,
                        git_dir=git_dir,
                        original_branch="dev",
                        changed_files=[".agent/tracker/.ops/.WI-x.ops"],
                        api_base="https://codeberg.org/api/v1/repos/o/r",
                        forgejo_user="user",
                        forgejo_token="tok",
                    )
                    with patch(
                        "hypergumbo_tracker.sync.do_sync",
                    ) as mock_sync:
                        mock_sync.return_value = SyncResult(
                            success=True, pr_number=99,
                            files_synced=1, exit_code=0,
                        )
                        _maybe_auto_sync(tmp_path)
                        mock_sync.assert_called_once()

        captured = capsys.readouterr()
        assert "auto-sync:" in captured.err
        assert "15 lines" in captured.err
        assert "PR #99" in captured.err

    def test_preflight_failure_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Preflight failure prints warning but doesn't raise."""
        from hypergumbo_tracker.sync import PreflightResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ):
                with patch(
                    "hypergumbo_tracker.sync.preflight_check",
                ) as mock_pre:
                    mock_pre.return_value = PreflightResult(
                        ok=False, error="auto-pr in flight",
                    )
                    _maybe_auto_sync(tmp_path)

        captured = capsys.readouterr()
        assert "preflight failed" in captured.err
        assert "auto-pr in flight" in captured.err

    def test_sync_failure_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Sync failure prints warning but doesn't raise."""
        from hypergumbo_tracker.sync import PreflightResult, SyncResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ):
                with patch(
                    "hypergumbo_tracker.sync.preflight_check",
                ) as mock_pre:
                    mock_pre.return_value = PreflightResult(
                        ok=True,
                        repo_root=tmp_path,
                        git_dir=git_dir,
                        original_branch="dev",
                        changed_files=[".agent/tracker/.ops/.WI-x.ops"],
                        api_base="https://codeberg.org/api/v1/repos/o/r",
                        forgejo_user="u",
                        forgejo_token="t",
                    )
                    with patch(
                        "hypergumbo_tracker.sync.do_sync",
                    ) as mock_sync:
                        mock_sync.return_value = SyncResult(
                            success=False, error="CI failed", exit_code=1,
                        )
                        _maybe_auto_sync(tmp_path)

        captured = capsys.readouterr()
        assert "sync failed" in captured.err

    def test_exception_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Unexpected exceptions are caught and logged."""
        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")

        with patch("subprocess.run", side_effect=RuntimeError("boom")):
            _maybe_auto_sync(tmp_path)

        captured = capsys.readouterr()
        assert "unexpected error" in captured.err
        assert "boom" in captured.err

    def test_git_rev_parse_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If git rev-parse --show-toplevel fails, returns silently."""
        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(returncode=1)
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
            ) as mock_psl:
                _maybe_auto_sync(tmp_path)
                mock_psl.assert_not_called()

    def test_tracker_root_outside_repo_root_skips_sync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto-sync skips if tracker_root is not under the cwd-derived repo_root.

        Regression test for the hang observed during pytest execution when
        ``.git/PR_PENDING`` was set on the real repo but tests injected a
        fake ``tracker_root`` under ``tmp_path``. Previously, ``_maybe_auto_sync``
        would derive ``repo_root`` from cwd (the real repo), compute
        ``pending_sync_lines`` against the real repo, trigger sync because
        the real repo had accumulated ops above the threshold, and then
        wait up to 15 minutes for the real ``.git/PR_PENDING`` to clear
        — despite the test's ``tracker_root`` having nothing to do with the
        real repo at all.

        The fix: if ``tracker_root`` is not inside ``repo_root``, skip
        auto-sync entirely — we are in a confused state (tests, cross-repo
        CLI usage, stale cwd) and should not sync the wrong repo.
        """
        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")

        # Simulate the confused state: git rev-parse returns the REAL
        # hypergumbo repo (via cwd), but tracker_root is under tmp_path
        # which is NOT under the real repo.
        fake_other_repo = tmp_path / "fake_other_repo"
        fake_other_repo.mkdir()
        fake_tracker_root = tmp_path / ".agent"
        fake_tracker_root.mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(fake_other_repo),
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
            ) as mock_psl:
                _maybe_auto_sync(fake_tracker_root)
                # pending_sync_lines must NOT be called — the guard
                # short-circuits before the count lookup. Otherwise the
                # real repo's ops would be counted and sync would trigger.
                mock_psl.assert_not_called()

    def test_invalid_threshold_uses_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-integer threshold falls back to default."""
        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "not_a_number")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=10,
            ) as mock_psl:
                # Default is 50, 10 < 50, so no sync
                _maybe_auto_sync(tmp_path)
                mock_psl.assert_called_once()

    def test_preflight_ok_but_no_changed_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Preflight passes but no changed files — do_sync not called."""
        from hypergumbo_tracker.sync import PreflightResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ):
                with patch(
                    "hypergumbo_tracker.sync.preflight_check",
                ) as mock_pre:
                    mock_pre.return_value = PreflightResult(
                        ok=True,
                        repo_root=tmp_path,
                        git_dir=tmp_path / ".git",
                        original_branch="dev",
                        changed_files=[],
                    )
                    with patch(
                        "hypergumbo_tracker.sync.do_sync",
                    ) as mock_sync:
                        _maybe_auto_sync(tmp_path)
                        mock_sync.assert_not_called()

    def test_tui_no_sync_without_mutations(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TUI should NOT trigger auto-sync when no mutations occurred."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")

        with patch(
            "hypergumbo_tracker.cli._detect_screen_altscreen_off",
            return_value=False,
        ), patch(
            "hypergumbo_tracker.tui.TrackerApp.run",
        ), patch(
            "hypergumbo_tracker.cli._maybe_auto_sync",
        ) as mock_sync:
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "tui"])
            assert exc.value.code == EXIT_SUCCESS
            mock_sync.assert_not_called()

    def test_tui_syncs_when_mutations_occurred(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TUI should trigger auto-sync when ops lines increased during session."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")

        # Simulate pending_sync_lines returning 10 before TUI, 50 after
        # (i.e., the TUI session wrote 40 new ops lines).
        call_count = 0

        def fake_pending_lines(repo_root: Path) -> int:
            nonlocal call_count
            call_count += 1
            return 10 if call_count == 1 else 50

        with patch(
            "hypergumbo_tracker.cli._detect_screen_altscreen_off",
            return_value=False,
        ), patch(
            "hypergumbo_tracker.tui.TrackerApp.run",
        ), patch(
            "hypergumbo_tracker.sync.pending_sync_lines",
            side_effect=fake_pending_lines,
        ), patch(
            "hypergumbo_tracker.cli._maybe_auto_sync",
        ) as mock_sync:
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "tui"])
            assert exc.value.code == EXIT_SUCCESS
            mock_sync.assert_called_once_with(tracker_root)

    def test_tui_no_sync_when_git_unavailable(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_agent_uid: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TUI gracefully skips sync check when git rev-parse fails."""
        tracker_root = _setup_tracker(tmp_path)
        _add_item(tracker_root / "tracker-workspace" / ".ops", "WI-test")

        with patch(
            "hypergumbo_tracker.cli._detect_screen_altscreen_off",
            return_value=False,
        ), patch(
            "hypergumbo_tracker.tui.TrackerApp.run",
        ), patch(
            "subprocess.run",
            side_effect=FileNotFoundError("git not found"),
        ), patch(
            "hypergumbo_tracker.cli._maybe_auto_sync",
        ) as mock_sync:
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "tui"])
            assert exc.value.code == EXIT_SUCCESS
            mock_sync.assert_not_called()

    def test_mutation_commands_set(self) -> None:
        """_MUTATION_COMMANDS contains expected commands."""
        assert "add" in _MUTATION_COMMANDS
        assert "update" in _MUTATION_COMMANDS
        assert "discuss" in _MUTATION_COMMANDS
        # TUI handles auto-sync internally, not via _MUTATION_COMMANDS
        assert "tui" not in _MUTATION_COMMANDS
        # Read-only commands should NOT be in the set
        assert "list" not in _MUTATION_COMMANDS
        assert "show" not in _MUTATION_COMMANDS
        assert "ready" not in _MUTATION_COMMANDS
        assert "sync" not in _MUTATION_COMMANDS


class TestAutoSyncPrWaitGate:
    """Tests for the PR_PENDING wait gate in _maybe_auto_sync."""

    def test_waits_for_pr_pending_removal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """When PR_PENDING exists, auto-sync waits until it's removed."""
        from hypergumbo_tracker.sync import PreflightResult, SyncResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)
        pr_pending = git_dir / "PR_PENDING"
        pr_pending.write_text("42\n")

        call_count = 0
        original_sleep = None  # not needed, time.sleep is patched

        def fake_sleep(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            # Remove PR_PENDING after 2 sleep calls (simulating auto-pr finishing)
            if call_count >= 2:
                pr_pending.unlink(missing_ok=True)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ), patch(
                "hypergumbo_tracker.sync.preflight_check",
            ) as mock_pre, patch(
                "hypergumbo_tracker.sync.do_sync",
            ) as mock_sync, patch(
                "hypergumbo_tracker.cli.time",
            ) as mock_time:
                mock_time.time = __import__("time").time
                mock_time.monotonic = __import__("time").monotonic
                mock_time.sleep = fake_sleep
                mock_pre.return_value = PreflightResult(
                    ok=True, repo_root=tmp_path, git_dir=git_dir,
                    original_branch="dev",
                    changed_files=[".agent/tracker/.ops/.WI-x.ops"],
                    api_base="https://codeberg.org/api/v1/repos/o/r",
                    forgejo_user="u", forgejo_token="t",
                )
                mock_sync.return_value = SyncResult(
                    success=True, pr_number=99,
                    files_synced=1, exit_code=0,
                )
                _maybe_auto_sync(tmp_path)
                # Should have proceeded to sync after PR_PENDING was removed
                mock_sync.assert_called_once()

        captured = capsys.readouterr()
        assert "waiting for in-flight PR" in captured.err
        assert "in-flight PR finished" in captured.err

    def test_pr_pending_wait_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """When PR_PENDING never clears, auto-sync times out and skips."""
        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)
        pr_pending = git_dir / "PR_PENDING"
        pr_pending.write_text("42\n")

        # monotonic returns increasing values that quickly exceed deadline
        mono_values = iter([0, 1000, 2000])

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ), patch(
                "hypergumbo_tracker.sync.preflight_check",
            ) as mock_pre, patch(
                "hypergumbo_tracker.cli.time",
            ) as mock_time:
                mock_time.time = __import__("time").time
                mock_time.monotonic = lambda: next(mono_values)
                mock_time.sleep = MagicMock()
                _maybe_auto_sync(tmp_path)
                # Should NOT have called preflight (timed out)
                mock_pre.assert_not_called()

        captured = capsys.readouterr()
        assert "timed out waiting for in-flight PR" in captured.err

    def test_no_pr_pending_skips_wait(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """When PR_PENDING doesn't exist, auto-sync proceeds without waiting."""
        from hypergumbo_tracker.sync import PreflightResult, SyncResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)
        # No PR_PENDING file

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ), patch(
                "hypergumbo_tracker.sync.preflight_check",
            ) as mock_pre, patch(
                "hypergumbo_tracker.sync.do_sync",
            ) as mock_sync:
                mock_pre.return_value = PreflightResult(
                    ok=True, repo_root=tmp_path, git_dir=git_dir,
                    original_branch="dev",
                    changed_files=[".agent/tracker/.ops/.WI-x.ops"],
                    api_base="https://codeberg.org/api/v1/repos/o/r",
                    forgejo_user="u", forgejo_token="t",
                )
                mock_sync.return_value = SyncResult(
                    success=True, pr_number=99,
                    files_synced=1, exit_code=0,
                )
                _maybe_auto_sync(tmp_path)
                mock_sync.assert_called_once()

        captured = capsys.readouterr()
        # Should NOT contain "waiting for in-flight PR"
        assert "waiting for in-flight PR" not in captured.err


class TestAutoSyncEarlyGateCheck:
    """Tests for the early TRACKER_SYNC_PENDING check in _maybe_auto_sync.

    When a sync is already in progress (gate file exists), _maybe_auto_sync
    should bail out early without calling preflight or do_sync.  This
    prevents concurrent auto-sync calls from racing past preflight and
    creating duplicate PRs.
    """

    def test_skips_when_sync_pending_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """auto-sync bails early when sync gate is held by an OS-level flock.

        Post-WI-nutin: a bare TRACKER_SYNC_PENDING file with no flock holder
        is treated as stale and auto-cleaned. To verify the skip path we
        must hold the flock for the duration of the test.
        """
        import fcntl

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)
        gate_path = git_dir / "TRACKER_SYNC_PENDING"
        fh = open(gate_path, "a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write(f"pid={os.getpid()}\nstarted=1000000000\n")
        fh.flush()
        try:
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = _make_completed_process(
                    stdout=str(tmp_path)
                )
                with patch(
                    "hypergumbo_tracker.sync.pending_sync_lines",
                    return_value=100,
                ), patch(
                    "hypergumbo_tracker.sync.preflight_check",
                ) as mock_pre, patch(
                    "hypergumbo_tracker.sync.do_sync",
                ) as mock_sync:
                    _maybe_auto_sync(tmp_path)
                    # Neither preflight nor do_sync should have been called
                    mock_pre.assert_not_called()
                    mock_sync.assert_not_called()
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()

        captured = capsys.readouterr()
        assert "tracker sync gate locked" in captured.err
        assert f"PID {os.getpid()}" in captured.err


class TestAutoSyncCircuitBreaker:
    """Tests for the circuit breaker in _maybe_auto_sync."""

    def test_failure_creates_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """When sync fails, a TRACKER_SYNC_FAILED marker is created."""
        from hypergumbo_tracker.sync import PreflightResult, SyncResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ), patch(
                "hypergumbo_tracker.sync.preflight_check",
            ) as mock_pre, patch(
                "hypergumbo_tracker.sync.do_sync",
            ) as mock_sync:
                mock_pre.return_value = PreflightResult(
                    ok=True, repo_root=tmp_path, git_dir=git_dir,
                    original_branch="dev",
                    changed_files=[".agent/tracker/.ops/.WI-x.ops"],
                    api_base="https://codeberg.org/api/v1/repos/o/r",
                    forgejo_user="u", forgejo_token="t",
                )
                mock_sync.return_value = SyncResult(
                    success=False, error="merge failed", exit_code=1,
                )
                _maybe_auto_sync(tmp_path)

        marker = git_dir / "TRACKER_SYNC_FAILED"
        assert marker.exists()
        content = marker.read_text().strip()
        assert content.startswith("1:")  # count:timestamp format
        captured = capsys.readouterr()
        assert "sync failed (1/3)" in captured.err

    def test_marker_blocks_retry_during_cooldown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Circuit breaker prevents sync during cooldown period."""
        import time

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)
        (git_dir / "TRACKER_SYNC_FAILED").write_text(
            f"1:{time.time()}"
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ), patch(
                "hypergumbo_tracker.sync.preflight_check",
            ) as mock_pre:
                _maybe_auto_sync(tmp_path)
                mock_pre.assert_not_called()

        captured = capsys.readouterr()
        assert "circuit breaker open" in captured.err

    def test_expired_marker_allows_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Expired marker is removed and sync proceeds."""
        import time
        from hypergumbo_tracker.sync import PreflightResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)
        # Write marker from 2 hours ago (well past 30 min cooldown)
        (git_dir / "TRACKER_SYNC_FAILED").write_text(
            f"1:{time.time() - 7200}"
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ), patch(
                "hypergumbo_tracker.sync.preflight_check",
            ) as mock_pre:
                mock_pre.return_value = PreflightResult(
                    ok=False, error="test stop",
                )
                _maybe_auto_sync(tmp_path)
                mock_pre.assert_called_once()

        assert not (git_dir / "TRACKER_SYNC_FAILED").exists()

    def test_success_clears_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Successful sync removes any existing failure marker."""
        import time
        from hypergumbo_tracker.sync import PreflightResult, SyncResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)
        # Write an expired marker so it doesn't block
        (git_dir / "TRACKER_SYNC_FAILED").write_text(
            f"1:{time.time() - 7200}"
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ), patch(
                "hypergumbo_tracker.sync.preflight_check",
            ) as mock_pre, patch(
                "hypergumbo_tracker.sync.do_sync",
            ) as mock_sync:
                mock_pre.return_value = PreflightResult(
                    ok=True, repo_root=tmp_path, git_dir=git_dir,
                    original_branch="dev",
                    changed_files=[".agent/tracker/.ops/.WI-x.ops"],
                    api_base="https://codeberg.org/api/v1/repos/o/r",
                    forgejo_user="u", forgejo_token="t",
                )
                mock_sync.return_value = SyncResult(
                    success=True, pr_number=42,
                    files_synced=1, exit_code=0,
                )
                _maybe_auto_sync(tmp_path)

        assert not (git_dir / "TRACKER_SYNC_FAILED").exists()

    def test_corrupt_marker_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Corrupt marker file is removed and sync proceeds."""
        from hypergumbo_tracker.sync import PreflightResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)
        (git_dir / "TRACKER_SYNC_FAILED").write_text("not_a_number")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ), patch(
                "hypergumbo_tracker.sync.preflight_check",
            ) as mock_pre:
                mock_pre.return_value = PreflightResult(
                    ok=False, error="test stop",
                )
                _maybe_auto_sync(tmp_path)
                mock_pre.assert_called_once()

    def test_escalation_disables_autonomous_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """After max consecutive failures, autonomous mode is disabled."""
        import time
        from hypergumbo_tracker.sync import PreflightResult, SyncResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)
        # Write marker with count=2, expired cooldown so sync retries
        (git_dir / "TRACKER_SYNC_FAILED").write_text(
            f"2:{time.time() - 7200}"
        )
        # Write AUTONOMOUS_MODE.txt so escalation has something to disable
        (tmp_path / "AUTONOMOUS_MODE.txt").write_text("BROAD\n")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ), patch(
                "hypergumbo_tracker.sync.preflight_check",
            ) as mock_pre, patch(
                "hypergumbo_tracker.sync.do_sync",
            ) as mock_sync:
                mock_pre.return_value = PreflightResult(
                    ok=True, repo_root=tmp_path, git_dir=git_dir,
                    original_branch="dev",
                    changed_files=[".agent/tracker/.ops/.WI-x.ops"],
                    api_base="https://codeberg.org/api/v1/repos/o/r",
                    forgejo_user="u", forgejo_token="t",
                )
                mock_sync.return_value = SyncResult(
                    success=False, error="merge failed", exit_code=1,
                )
                _maybe_auto_sync(tmp_path)

        # Marker should show count=3
        marker = git_dir / "TRACKER_SYNC_FAILED"
        assert marker.exists()
        assert marker.read_text().strip().startswith("3:")
        # Autonomous mode should be OFF
        assert (tmp_path / "AUTONOMOUS_MODE.txt").read_text().strip() == "OFF"
        captured = capsys.readouterr()
        assert "ESCALATION" in captured.err
        assert "was BROAD" in captured.err

    def test_escalation_skips_when_already_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Escalation does nothing if autonomous mode is already OFF."""
        import time
        from hypergumbo_tracker.sync import PreflightResult, SyncResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)
        (git_dir / "TRACKER_SYNC_FAILED").write_text(
            f"2:{time.time() - 7200}"
        )
        (tmp_path / "AUTONOMOUS_MODE.txt").write_text("OFF\n")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ), patch(
                "hypergumbo_tracker.sync.preflight_check",
            ) as mock_pre, patch(
                "hypergumbo_tracker.sync.do_sync",
            ) as mock_sync:
                mock_pre.return_value = PreflightResult(
                    ok=True, repo_root=tmp_path, git_dir=git_dir,
                    original_branch="dev",
                    changed_files=[".agent/tracker/.ops/.WI-x.ops"],
                    api_base="https://codeberg.org/api/v1/repos/o/r",
                    forgejo_user="u", forgejo_token="t",
                )
                mock_sync.return_value = SyncResult(
                    success=False, error="merge failed", exit_code=1,
                )
                _maybe_auto_sync(tmp_path)

        captured = capsys.readouterr()
        # Should NOT see escalation message since already OFF
        assert "ESCALATION" not in captured.err
        assert (tmp_path / "AUTONOMOUS_MODE.txt").read_text().strip() == "OFF"

    def test_escalation_no_mode_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Escalation handles missing AUTONOMOUS_MODE.txt (default OFF)."""
        import time
        from hypergumbo_tracker.sync import PreflightResult, SyncResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)
        (git_dir / "TRACKER_SYNC_FAILED").write_text(
            f"2:{time.time() - 7200}"
        )
        # No AUTONOMOUS_MODE.txt — defaults to OFF, so no escalation

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ), patch(
                "hypergumbo_tracker.sync.preflight_check",
            ) as mock_pre, patch(
                "hypergumbo_tracker.sync.do_sync",
            ) as mock_sync:
                mock_pre.return_value = PreflightResult(
                    ok=True, repo_root=tmp_path, git_dir=git_dir,
                    original_branch="dev",
                    changed_files=[".agent/tracker/.ops/.WI-x.ops"],
                    api_base="https://codeberg.org/api/v1/repos/o/r",
                    forgejo_user="u", forgejo_token="t",
                )
                mock_sync.return_value = SyncResult(
                    success=False, error="merge failed", exit_code=1,
                )
                _maybe_auto_sync(tmp_path)

        captured = capsys.readouterr()
        assert "ESCALATION" not in captured.err

    def test_failure_count_increments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Consecutive failures increment the count in the marker file."""
        import time
        from hypergumbo_tracker.sync import PreflightResult, SyncResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "5")
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)
        # First failure — count goes from 1 to 2 after expired cooldown
        (git_dir / "TRACKER_SYNC_FAILED").write_text(
            f"1:{time.time() - 7200}"
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch(
                "hypergumbo_tracker.sync.pending_sync_lines",
                return_value=100,
            ), patch(
                "hypergumbo_tracker.sync.preflight_check",
            ) as mock_pre, patch(
                "hypergumbo_tracker.sync.do_sync",
            ) as mock_sync:
                mock_pre.return_value = PreflightResult(
                    ok=True, repo_root=tmp_path, git_dir=git_dir,
                    original_branch="dev",
                    changed_files=[".agent/tracker/.ops/.WI-x.ops"],
                    api_base="https://codeberg.org/api/v1/repos/o/r",
                    forgejo_user="u", forgejo_token="t",
                )
                mock_sync.return_value = SyncResult(
                    success=False, error="merge failed", exit_code=1,
                )
                _maybe_auto_sync(tmp_path)

        marker = git_dir / "TRACKER_SYNC_FAILED"
        assert marker.read_text().strip().startswith("2:")
        captured = capsys.readouterr()
        assert "sync failed (2/3)" in captured.err


class TestSyncReminder:
    """Tests for _print_sync_reminder."""

    def test_prints_reminder_on_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Reminder prints pending lines and threshold to stderr."""
        with patch(
            "hypergumbo_tracker.cli.subprocess.run",
        ) as mock_run, patch(
            "hypergumbo_tracker.sync.pending_sync_lines",
        ) as mock_pending:
            mock_run.return_value = MagicMock(returncode=0, stdout="/repo\n")
            mock_pending.return_value = 12
            _print_sync_reminder()
        captured = capsys.readouterr()
        assert "Auto-sync is AUTOMATIC" in captured.err
        assert "do NOT push or sync" in captured.err
        assert "12 pending line(s)" in captured.err
        assert "threshold=" in captured.err

    def test_invalid_threshold_env_falls_back(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-integer TRACKER_AUTO_SYNC_THRESHOLD falls back to default."""
        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "not_a_number")
        with patch(
            "hypergumbo_tracker.cli.subprocess.run",
        ) as mock_run, patch(
            "hypergumbo_tracker.sync.pending_sync_lines",
        ) as mock_pending:
            mock_run.return_value = MagicMock(returncode=0, stdout="/repo\n")
            mock_pending.return_value = 5
            _print_sync_reminder()
        captured = capsys.readouterr()
        assert "threshold=40" in captured.err

    def test_git_failure_silently_returns(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """If git rev-parse fails, no output, no error."""
        with patch(
            "hypergumbo_tracker.cli.subprocess.run",
        ) as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            _print_sync_reminder()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_exception_silently_caught(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Unexpected exceptions are swallowed."""
        with patch(
            "hypergumbo_tracker.cli.subprocess.run",
            side_effect=RuntimeError("boom"),
        ):
            _print_sync_reminder()
        captured = capsys.readouterr()
        assert captured.err == ""

