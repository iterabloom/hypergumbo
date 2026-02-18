# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.stop_hook.

Covers count_todos with hard/soft filtering, hash_todos fingerprinting,
scope awareness, and fail-closed wrappers.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hypergumbo_tracker.models import TrackerConfig
from hypergumbo_tracker.stop_hook import (
    _filter_blocking_statuses,
    count_todos,
    count_todos_safe,
    generate_guidance,
    generate_guidance_safe,
    hash_todos,
    hash_todos_safe,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> TrackerConfig:
    """Create a minimal TrackerConfig for testing."""
    from helpers import make_test_config

    return make_test_config(**overrides)


def _setup_tracker(tmp_path: Path) -> Path:
    """Set up a tracker root with directories."""
    tracker_root = tmp_path / ".agent"
    (tracker_root / "tracker" / ".ops").mkdir(parents=True)
    (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
    (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)
    return tracker_root


def _add_item(ops_dir: Path, item_id: str, status: str = "todo_hard") -> None:
    """Write a simple ops file."""
    ops_content = textwrap.dedent(f"""\
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
    """)
    (ops_dir / f".{item_id}.ops").write_text(ops_content)


# ---------------------------------------------------------------------------
# _filter_blocking_statuses
# ---------------------------------------------------------------------------


class TestFilterBlockingStatuses:
    def test_no_filter(self) -> None:
        result = _filter_blocking_statuses(["todo_hard", "todo_soft"])
        assert result == {"todo_hard", "todo_soft"}

    def test_hard_filter(self) -> None:
        result = _filter_blocking_statuses(["todo_hard", "todo_soft"], hard=True)
        assert result == {"todo_hard"}

    def test_soft_filter(self) -> None:
        result = _filter_blocking_statuses(["todo_hard", "todo_soft"], soft=True)
        assert result == {"todo_soft"}


# ---------------------------------------------------------------------------
# count_todos
# ---------------------------------------------------------------------------


class TestCountTodos:
    def test_counts_blocking_items(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")
        _add_item(canonical_ops, "WI-b", "todo_soft")
        _add_item(canonical_ops, "WI-c", "done")

        config = _make_config()
        assert count_todos(tracker_root, config=config) == 2

    def test_hard_only(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")
        _add_item(canonical_ops, "WI-b", "todo_soft")

        config = _make_config()
        assert count_todos(tracker_root, hard=True, config=config) == 1

    def test_soft_only(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")
        _add_item(canonical_ops, "WI-b", "todo_soft")

        config = _make_config()
        assert count_todos(tracker_root, soft=True, config=config) == 1

    def test_workspace_scope(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        workspace_ops = tracker_root / "tracker-workspace" / ".ops"

        _add_item(canonical_ops, "WI-a", "todo_hard")  # Canonical — should be excluded
        _add_item(workspace_ops, "WI-b", "todo_soft")

        config = _make_config(scope="workspace")
        assert count_todos(tracker_root, config=config) == 1

    def test_stealth_always_counted(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        stealth_ops = tracker_root / "tracker-workspace" / "stealth"
        _add_item(stealth_ops, "WI-s", "todo_hard")

        config = _make_config(scope="workspace")
        assert count_todos(tracker_root, config=config) == 1

    def test_empty_tracker(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        config = _make_config()
        assert count_todos(tracker_root, config=config) == 0

    def test_loads_config_when_none(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        import yaml
        config_data = {
            "kinds": {"work_item": {"prefix": "WI"}},
            "statuses": ["todo_hard", "done"],
            "stop_hook": {"blocking_statuses": ["todo_hard"], "resolved_statuses": ["done"]},
        }
        (tracker_root / "tracker" / "config.yaml").write_text(yaml.dump(config_data))
        _add_item(tracker_root / "tracker" / ".ops", "WI-a", "todo_hard")

        assert count_todos(tracker_root) == 1


# ---------------------------------------------------------------------------
# hash_todos
# ---------------------------------------------------------------------------


class TestHashTodos:
    def test_returns_hex_string(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")

        config = _make_config()
        result = hash_todos(tracker_root, config=config)
        assert len(result) == 64  # SHA256 hex
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")

        config = _make_config()
        h1 = hash_todos(tracker_root, config=config)
        h2 = hash_todos(tracker_root, config=config)
        assert h1 == h2

    def test_changes_when_items_change(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")

        config = _make_config()
        h1 = hash_todos(tracker_root, config=config)

        _add_item(canonical_ops, "WI-b", "todo_soft")
        h2 = hash_todos(tracker_root, config=config)
        assert h1 != h2

    def test_ignores_done_items(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "done")

        config = _make_config()
        h1 = hash_todos(tracker_root, config=config)

        # Empty tracker should give same hash (no blocking items)
        tracker_root2 = _setup_tracker(Path(str(tmp_path) + "2"))
        h2 = hash_todos(tracker_root2, config=config)
        assert h1 == h2

    def test_workspace_scope(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        workspace_ops = tracker_root / "tracker-workspace" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")
        _add_item(workspace_ops, "WI-b", "todo_soft")

        config_all = _make_config(scope="all")
        config_ws = _make_config(scope="workspace")

        h_all = hash_todos(tracker_root, config=config_all)
        h_ws = hash_todos(tracker_root, config=config_ws)
        assert h_all != h_ws

    def test_loads_config_when_none(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        import yaml
        config_data = {
            "kinds": {"work_item": {"prefix": "WI"}},
            "statuses": ["todo_hard", "done"],
            "stop_hook": {"blocking_statuses": ["todo_hard"], "resolved_statuses": ["done"]},
        }
        (tracker_root / "tracker" / "config.yaml").write_text(yaml.dump(config_data))
        _add_item(tracker_root / "tracker" / ".ops", "WI-a", "todo_hard")

        result = hash_todos(tracker_root)
        assert len(result) == 64


# ---------------------------------------------------------------------------
# TrackerSet.hash_todos
# ---------------------------------------------------------------------------


class TestTrackerSetHashTodos:
    def test_hash_todos_on_trackerset(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.trackerset import TrackerSet
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")

        config = _make_config()
        ts = TrackerSet(tracker_root, config=config)
        result = ts.hash_todos()
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_todos_deterministic(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.trackerset import TrackerSet
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")

        config = _make_config()
        ts = TrackerSet(tracker_root, config=config)
        assert ts.hash_todos() == ts.hash_todos()

    def test_hash_todos_workspace_scope(self, tmp_path: Path) -> None:
        """hash_todos with workspace scope only includes workspace+stealth items."""
        from hypergumbo_tracker.trackerset import TrackerSet
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        workspace_ops = tracker_root / "tracker-workspace" / ".ops"
        _add_item(canonical_ops, "WI-can", "todo_hard")
        _add_item(workspace_ops, "WI-ws", "todo_hard")

        config_all = _make_config()
        ts_all = TrackerSet(tracker_root, config=config_all)
        hash_all = ts_all.hash_todos()

        config_ws = _make_config(scope="workspace")
        ts_ws = TrackerSet(tracker_root, config=config_ws)
        hash_ws = ts_ws.hash_todos()

        # workspace scope excludes canonical, so hashes should differ
        assert hash_all != hash_ws


# ---------------------------------------------------------------------------
# Fail-closed wrappers
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_count_todos_safe_returns_count(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")

        result = count_todos_safe(tracker_root)
        assert result >= 0

    def test_count_todos_safe_on_error(self) -> None:
        # Use a path where directory creation will fail
        with patch("hypergumbo_tracker.stop_hook.count_todos", side_effect=RuntimeError("boom")):
            result = count_todos_safe(Path("/tmp/whatever"))
        assert result == -1

    def test_hash_todos_safe_returns_hash(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        result = hash_todos_safe(tracker_root)
        assert result is not None
        assert len(result) == 64

    def test_hash_todos_safe_on_error(self) -> None:
        with patch("hypergumbo_tracker.stop_hook.hash_todos", side_effect=RuntimeError("boom")):
            result = hash_todos_safe(Path("/tmp/whatever"))
        assert result is None


# ---------------------------------------------------------------------------
# generate_guidance
# ---------------------------------------------------------------------------


class TestGenerateGuidance:
    def test_generates_file_with_blocking_items(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")
        _add_item(canonical_ops, "WI-b", "todo_soft")
        _add_item(canonical_ops, "WI-c", "done")

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )

        assert Path(result).exists()
        content = Path(result).read_text()
        assert "WI-a" in content
        assert "WI-b" in content
        assert "WI-c" not in content  # done items excluded
        assert "# Stop Hook Guidance" in content

    def test_creates_guidance_dir(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")

        config = _make_config()
        guidance_dir = tmp_path / "new" / "nested" / "dir"
        assert not guidance_dir.exists()

        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        assert guidance_dir.exists()
        assert Path(result).exists()

    def test_scope_workspace_excludes_canonical(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        workspace_ops = tracker_root / "tracker-workspace" / ".ops"
        _add_item(canonical_ops, "WI-can", "todo_hard")
        _add_item(workspace_ops, "WI-ws", "todo_soft")

        config = _make_config(scope="workspace")
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        content = Path(result).read_text()
        assert "WI-can" not in content
        assert "WI-ws" in content

    def test_returns_absolute_path(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        assert Path(result).is_absolute()

    def test_empty_tracker_still_generates_file(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        assert Path(result).exists()
        content = Path(result).read_text()
        assert "# Stop Hook Guidance" in content
        assert "Hard TODO Items: 0" in content

    def test_default_guidance_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        tracker_root = _setup_tracker(tmp_path)
        config = _make_config()
        monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
        result = generate_guidance(tracker_root, config=config)
        expected_dir = tmp_path / "fakehome" / "hypergumbo_lab_notebook" / "guidance_log"
        assert Path(result).parent == expected_dir

    def test_loads_config_when_none(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        import yaml
        config_data = {
            "kinds": {"work_item": {"prefix": "WI"}},
            "statuses": ["todo_hard", "done"],
            "stop_hook": {"blocking_statuses": ["todo_hard"], "resolved_statuses": ["done"]},
        }
        (tracker_root / "tracker" / "config.yaml").write_text(yaml.dump(config_data))
        _add_item(tracker_root / "tracker" / ".ops", "WI-a", "todo_hard")

        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(tracker_root, guidance_dir=guidance_dir)
        assert Path(result).exists()

    def test_items_sorted_by_priority(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"

        # Create items with different priorities via custom ops
        for item_id, priority in [("WI-lo", 3), ("WI-hi", 0), ("WI-mid", 2)]:
            ops_content = textwrap.dedent(f"""\
                - op: create
                  at: "2026-01-01T00:00:00Z"
                  by: agent
                  actor: test_agent
                  clock: 1
                  nonce: a1b2
                  data:
                    kind: work_item
                    title: "Item {item_id}"
                    status: todo_hard
                    priority: {priority}
            """)
            (canonical_ops / f".{item_id}.ops").write_text(ops_content)

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        content = Path(result).read_text()
        # P0 should appear before P2, P2 before P3
        hi_pos = content.index("WI-hi")
        mid_pos = content.index("WI-mid")
        lo_pos = content.index("WI-lo")
        assert hi_pos < mid_pos < lo_pos


class TestGenerateGuidanceSafe:
    def test_safe_returns_path_on_success(self, tmp_path: Path) -> None:
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")

        guidance_dir = tmp_path / "guidance"
        result = generate_guidance_safe(tracker_root, guidance_dir=guidance_dir)
        assert result is not None
        assert Path(result).exists()

    def test_safe_returns_none_on_error(self) -> None:
        with patch(
            "hypergumbo_tracker.stop_hook.generate_guidance",
            side_effect=RuntimeError("boom"),
        ):
            result = generate_guidance_safe(Path("/tmp/whatever"))
        assert result is None


# ---------------------------------------------------------------------------
# generate_guidance: unread human message annotations
# ---------------------------------------------------------------------------


def _add_item_with_discussion(
    ops_dir: Path, item_id: str, status: str, discussion_ops: list[dict],
    priority: int = 2,
) -> None:
    """Write an ops file with create + discussion ops."""
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
            priority: {priority}
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


class TestGenerateGuidanceUnreadMessages:
    def test_blocking_item_with_unread_gets_tag(self, tmp_path: Path) -> None:
        """Blocking item with trailing human message gets [UNREAD] tag."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item_with_discussion(
            canonical_ops, "WI-ur", "todo_hard",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "please check"},
            ],
        )
        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        content = Path(result).read_text()
        assert "**[UNREAD]**" in content
        assert "WI-ur" in content
        assert "1 with unread human message(s)" in content
        assert "check-messages" in content

    def test_non_blocking_item_with_unread(self, tmp_path: Path) -> None:
        """Non-blocking item with trailing human message appears in separate section."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item_with_discussion(
            canonical_ops, "WI-done", "done",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "fyi"},
            ],
        )
        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        content = Path(result).read_text()
        assert "Unread Human Messages (non-blocking)" in content
        assert "WI-done" in content
        assert "1 unread)" in content

    def test_no_unread_messages_backward_compatible(self, tmp_path: Path) -> None:
        """No unread messages: no unread section, no check-messages hint."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        content = Path(result).read_text()
        assert "**[UNREAD]**" not in content
        assert "Unread Human Messages" not in content
        assert "check-messages" not in content

    def test_unread_count_in_status_section(self, tmp_path: Path) -> None:
        """Status section shows unread counts for hard and soft items."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item_with_discussion(
            canonical_ops, "WI-hard", "todo_hard",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "check this"},
            ],
        )
        _add_item_with_discussion(
            canonical_ops, "WI-soft", "todo_soft",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "also this"},
            ],
        )
        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        content = Path(result).read_text()
        # Both sections should have unread count annotations
        lines = content.split("\n")
        hard_count_lines = [l for l in lines if "Hard TODO Items:" in l]
        assert len(hard_count_lines) == 1
        # After the hard count line, there should be an unread count line
        hard_idx = lines.index(hard_count_lines[0])
        assert "1 with unread human message(s)" in lines[hard_idx + 1]

    def test_non_blocking_unread_count_in_status(self, tmp_path: Path) -> None:
        """Non-blocking unread count shows in status section."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item_with_discussion(
            canonical_ops, "WI-nr", "done",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "info"},
            ],
        )
        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        content = Path(result).read_text()
        assert "Non-blocking items with unread messages: 1" in content
