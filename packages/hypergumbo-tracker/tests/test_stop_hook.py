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
    _call_openrouter,
    _extract_preface,
    _filter_blocking_statuses,
    _precedence_gate,
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


def _add_item(
    ops_dir: Path,
    item_id: str,
    status: str = "todo_hard",
    kind: str = "work_item",
    priority: int = 2,
) -> None:
    """Write a simple ops file."""
    fields_block = ""
    if kind == "invariant":
        fields_block = (
            '    fields:\n'
            '      statement: "test invariant statement"\n'
            '      root_cause: "test root cause"\n'
        )
    ops_content = textwrap.dedent(f"""\
        - op: create
          at: "2026-01-01T00:00:00Z"
          by: agent
          actor: test_agent
          clock: 1
          nonce: a1b2
          data:
            kind: {kind}
            title: "Item {item_id}"
            status: {status}
            priority: {priority}
    """)
    if fields_block:
        ops_content = ops_content.rstrip("\n") + "\n" + fields_block
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


    def test_violated_invariants_in_own_section(self, tmp_path: Path) -> None:
        """Violated invariants appear in '## Invariant Violations', not soft."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "INV-bad", "violated", kind="invariant")
        _add_item(canonical_ops, "WI-soft", "todo_soft")
        _add_item(canonical_ops, "WI-hard", "todo_hard")

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        content = Path(result).read_text()

        # Invariant violation gets its own section
        assert "## Invariant Violations" in content
        assert "## Hard TODO Items" in content
        assert "## Soft TODO Items" in content

        # Status summary counts violated items separately
        assert "Invariant Violations: 1" in content

        # INV item appears in violations section, not soft
        inv_section_pos = content.index("## Invariant Violations")
        soft_section_pos = content.index("## Soft TODO Items")
        inv_item_pos = content.index("INV-bad")
        assert inv_section_pos < inv_item_pos < soft_section_pos

        # Guidance mentions invariant violations
        assert "Invariant violations: these are blocking" in content

        # No redundant status or tier in item lines
        assert "status:" not in content
        assert "tier:" not in content

    def test_no_violated_skips_section(self, tmp_path: Path) -> None:
        """When no violated items exist, the section is omitted."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        content = Path(result).read_text()
        assert "## Invariant Violations" not in content
        assert "Invariant violations: these are blocking" not in content

    def test_guidance_drops_status_and_tier(self, tmp_path: Path) -> None:
        """Item lines should not include redundant status: or tier: metadata."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")
        _add_item(canonical_ops, "WI-b", "todo_soft")

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        content = Path(result).read_text()
        assert "status:" not in content
        assert "tier:" not in content
        # Items still appear with ID, priority, and title
        assert "[WI-a] P2 Item WI-a" in content
        assert "[WI-b] P2 Item WI-b" in content


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
    """REPLY-FIRST CYCLE branch (WI-ripuz).

    When ANY unread human messages exist (blocking or non-blocking), the
    guidance document changes shape entirely: TODO/violated/soft sections
    are suppressed, and the document instructs the agent to clear reply
    debt before resuming autonomous work.
    """

    def test_blocking_item_with_unread_triggers_reply_first(
        self, tmp_path: Path,
    ) -> None:
        """Blocking item with trailing human message triggers REPLY-FIRST."""
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
        assert "REPLY-FIRST CYCLE" in content
        assert "1 unread human message(s)" in content
        assert "WI-ur" in content
        assert "Blocking items with unread messages" in content
        assert "Take a breather" in content
        assert "do not start new code tasks" in content.lower()
        assert "tracker discuss" in content
        # TODO/violated/soft listings must be suppressed
        assert "## Hard TODO Items" not in content
        assert "## Invariant Violations" not in content
        assert "## Soft TODO Items" not in content

    def test_violated_item_with_unread_triggers_reply_first(
        self, tmp_path: Path,
    ) -> None:
        """Violated invariant with unread message triggers REPLY-FIRST."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        ops_content = textwrap.dedent("""\
            - op: create
              at: "2026-01-01T00:00:00Z"
              by: agent
              actor: test_agent
              clock: 1
              nonce: a1b2
              data:
                kind: invariant
                title: "Bad invariant"
                status: violated
                priority: 2
                fields:
                  statement: "X must be true"
                  root_cause: "X is false"
            - op: discuss
              at: "2026-01-15T10:00:00Z"
              by: human
              actor: jgstern
              clock: 10
              nonce: d001
              message: "please investigate"
        """)
        (canonical_ops / ".INV-viol.ops").write_text(ops_content)

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        content = Path(result).read_text()
        assert "REPLY-FIRST CYCLE" in content
        assert "INV-viol" in content
        assert "## Invariant Violations" not in content

    def test_non_blocking_item_with_unread_triggers_reply_first(
        self, tmp_path: Path,
    ) -> None:
        """Non-blocking item with trailing human message triggers REPLY-FIRST."""
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
        assert "REPLY-FIRST CYCLE" in content
        assert "Non-blocking items with unread messages" in content
        assert "WI-done" in content
        assert "1 unread)" in content

    def test_no_unread_messages_uses_default_guidance(
        self, tmp_path: Path,
    ) -> None:
        """No unread messages: default guidance shape (TODO listings)."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item(canonical_ops, "WI-a", "todo_hard")

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        content = Path(result).read_text()
        assert "REPLY-FIRST CYCLE" not in content
        assert "## Hard TODO Items" in content
        assert "WI-a" in content

    def test_reply_first_lists_all_unread_by_priority(
        self, tmp_path: Path,
    ) -> None:
        """REPLY-FIRST lists blocking + non-blocking items sorted by priority."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item_with_discussion(
            canonical_ops, "WI-hard", "todo_hard", priority=1,
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "check this"},
            ],
        )
        _add_item_with_discussion(
            canonical_ops, "WI-soft", "todo_soft", priority=3,
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
        assert "REPLY-FIRST CYCLE" in content
        assert "2 unread human message(s)" in content
        assert "WI-hard" in content
        assert "WI-soft" in content
        # Hard item (P1) should appear before soft item (P3) in the blocking
        # section — both are blocking since todo_soft is in default blocking
        # statuses.
        idx_hard = content.index("WI-hard")
        idx_soft = content.index("WI-soft")
        assert idx_hard < idx_soft

    def test_reply_first_suppresses_guidance_paragraph(
        self, tmp_path: Path,
    ) -> None:
        """REPLY-FIRST branch omits the default 'Guidance' paragraph entirely."""
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
        assert "## Guidance" not in content
        assert "assume the item is" not in content
        assert (
            "The TODO listing is intentionally suppressed in this cycle."
            in content
        )

    def test_reply_first_blocking_only_no_non_blocking_section(
        self, tmp_path: Path,
    ) -> None:
        """With only blocking unreads, non-blocking section is absent."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item_with_discussion(
            canonical_ops, "WI-b", "todo_hard",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "hi"},
            ],
        )
        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        result = generate_guidance(
            tracker_root, guidance_dir=guidance_dir, config=config,
        )
        content = Path(result).read_text()
        assert "## Blocking items with unread messages" in content
        assert "## Non-blocking items with unread messages" not in content


# ---------------------------------------------------------------------------
# Phase 2 (WI-mofaz): [[preface]] detector
# ---------------------------------------------------------------------------


class TestExtractPreface:
    """Preface detector: anchored at char 0, no whitespace, must close."""

    def test_accepts_important(self) -> None:
        assert _extract_preface("[[IMPORTANT]] please check") == "IMPORTANT"

    def test_accepts_hyphenated(self) -> None:
        assert (
            _extract_preface("[[do-right-fucking-now]] ship it")
            == "do-right-fucking-now"
        )

    def test_accepts_opaque_tag(self) -> None:
        """Ambiguous tags still activate the path — the gate decides meaning."""
        assert _extract_preface("[[Mclovin]] body") == "Mclovin"

    def test_rejects_internal_space(self) -> None:
        assert _extract_preface("[[has space]] body") is None

    def test_rejects_leading_text(self) -> None:
        assert _extract_preface("leading text [[IMPORTANT]] body") is None

    def test_rejects_leading_whitespace_inside(self) -> None:
        assert _extract_preface("[[ space-before-close]] body") is None

    def test_rejects_trailing_whitespace_inside(self) -> None:
        assert _extract_preface("[[space-after-close ]] body") is None

    def test_rejects_unclosed(self) -> None:
        assert _extract_preface("[[IMPORTANT body") is None

    def test_rejects_single_brackets(self) -> None:
        assert _extract_preface("[IMPORTANT] body") is None

    def test_rejects_empty_brackets(self) -> None:
        assert _extract_preface("[[]] body") is None

    def test_rejects_empty_message(self) -> None:
        assert _extract_preface("") is None

    def test_rejects_nested_bracket(self) -> None:
        assert _extract_preface("[[fo[o]]] body") is None


# ---------------------------------------------------------------------------
# Phase 2: LLM precedence gate
# ---------------------------------------------------------------------------


class TestPrecedenceGate:
    def test_no_other_unreads_returns_false(self) -> None:
        """Gate is meaningless when there are no other unreads to elevate over."""
        calls: list[str] = []

        def fake(prompt: str) -> str:
            calls.append(prompt)
            return "YES"

        assert _precedence_gate(
            "IMPORTANT", "WI-x", "Title", [], [], api_caller=fake,
        ) is False
        assert calls == []

    def test_yes_response_elevates(self) -> None:
        def fake(prompt: str) -> str:
            return "YES"

        assert _precedence_gate(
            "IMPORTANT", "WI-x", "Title",
            ["WI-other"], ["Other title"],
            api_caller=fake,
        ) is True

    def test_no_response_declines(self) -> None:
        def fake(prompt: str) -> str:
            return "NO, unclear meaning."

        assert _precedence_gate(
            "Mclovin", "WI-x", "Title",
            ["WI-other"], ["Other title"],
            api_caller=fake,
        ) is False

    def test_empty_response_declines(self) -> None:
        def fake(prompt: str) -> str:
            return ""

        assert _precedence_gate(
            "IMPORTANT", "WI-x", "Title",
            ["WI-other"], ["Other title"],
            api_caller=fake,
        ) is False

    def test_exception_declines(self) -> None:
        def fake(prompt: str) -> str:
            raise RuntimeError("network down")

        assert _precedence_gate(
            "IMPORTANT", "WI-x", "Title",
            ["WI-other"], ["Other title"],
            api_caller=fake,
        ) is False

    def test_prompt_contains_preface_and_others(self) -> None:
        """The gate prompt must name the preface tag and every other unread."""
        captured: list[str] = []

        def fake(prompt: str) -> str:
            captured.append(prompt)
            return "NO"

        _precedence_gate(
            "Mclovin", "WI-this", "Primary title",
            ["WI-a", "WI-b"],
            ["Title A", "Title B"],
            api_caller=fake,
        )
        assert len(captured) == 1
        prompt = captured[0]
        assert "[[Mclovin]]" in prompt
        assert "WI-this" in prompt
        assert "Primary title" in prompt
        assert "WI-a" in prompt
        assert "Title A" in prompt
        assert "WI-b" in prompt
        assert "Title B" in prompt
        # The count of other unreads is stated
        assert "2" in prompt

    def test_yes_case_insensitive(self) -> None:
        def fake(prompt: str) -> str:
            return "yes, it does"

        assert _precedence_gate(
            "IMPORTANT", "WI-x", "Title",
            ["WI-other"], ["Other title"],
            api_caller=fake,
        ) is True


class TestCallOpenrouter:
    def test_no_api_key_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert _call_openrouter("any prompt") == ""

    def test_success_returns_message(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import io
        import json as _json

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return _json.dumps({
                    "choices": [{"message": {"content": "YES"}}]
                }).encode()

        def fake_urlopen(req, timeout=0):
            # Verify the request was well-formed
            assert req.headers["Authorization"] == "Bearer sk-test"
            assert req.headers["Content-type"] == "application/json"
            payload = _json.loads(req.data.decode())
            assert payload["messages"][0]["content"] == "prompt body"
            return FakeResp()

        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen,
        )
        assert _call_openrouter("prompt body") == "YES"

    def test_network_error_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import urllib.error as ue

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

        def fake_urlopen(req, timeout=0):
            raise ue.URLError("boom")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        assert _call_openrouter("prompt") == ""

    def test_malformed_response_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"not json at all"

        def fake_urlopen(req, timeout=0):
            return FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        assert _call_openrouter("prompt") == ""


# ---------------------------------------------------------------------------
# Phase 2: REPLY-FIRST guidance with preface escalation
# ---------------------------------------------------------------------------


class TestReplyFirstPrefaceEscalation:
    """Preface detector + LLM gate integration with _build_reply_first_guidance."""

    def test_prefaced_message_with_other_unreads_calls_gate(
        self, tmp_path: Path,
    ) -> None:
        """Preface fires gate when other unreads exist; YES elevates the item."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item_with_discussion(
            canonical_ops, "WI-urgent", "todo_hard", priority=2,
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "[[IMPORTANT]] drop everything"},
            ],
        )
        _add_item_with_discussion(
            canonical_ops, "WI-normal", "todo_hard", priority=2,
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "ordinary message"},
            ],
        )

        captured: list[str] = []

        def fake(prompt: str) -> str:
            captured.append(prompt)
            return "YES"

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        with patch(
            "hypergumbo_tracker.stop_hook._call_openrouter",
            side_effect=fake,
        ):
            result = generate_guidance(
                tracker_root, guidance_dir=guidance_dir, config=config,
            )
        content = Path(result).read_text()

        # Gate was consulted for the prefaced item
        assert len(captured) == 1
        assert "[[IMPORTANT]]" in captured[0]
        assert "WI-normal" in captured[0]

        # Escalated section appears with the elevated item
        assert "## Escalated" in content
        assert "WI-urgent" in content
        assert "[[IMPORTANT]]" in content
        # Escalated section appears before the regular blocking section
        esc_pos = content.index("## Escalated")
        block_pos = content.index("## Blocking items with unread messages")
        assert esc_pos < block_pos
        # The elevated item does not also appear in the plain blocking section
        regular_section = content[block_pos:]
        assert "WI-urgent" not in regular_section

    def test_prefaced_message_gate_declines_falls_back(
        self, tmp_path: Path,
    ) -> None:
        """NO from gate → no escalation section; default equal-priority listing."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item_with_discussion(
            canonical_ops, "WI-prefaced", "todo_hard",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "[[Mclovin]] hmm"},
            ],
        )
        _add_item_with_discussion(
            canonical_ops, "WI-other", "todo_hard",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "other"},
            ],
        )

        def fake(prompt: str) -> str:
            return "NO"

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        with patch(
            "hypergumbo_tracker.stop_hook._call_openrouter",
            side_effect=fake,
        ):
            result = generate_guidance(
                tracker_root, guidance_dir=guidance_dir, config=config,
            )
        content = Path(result).read_text()

        assert "## Escalated" not in content
        assert "WI-prefaced" in content
        assert "WI-other" in content

    def test_prefaced_message_without_other_unreads_skips_gate(
        self, tmp_path: Path,
    ) -> None:
        """Lone prefaced unread: gate is not called (nothing to elevate over)."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item_with_discussion(
            canonical_ops, "WI-lone", "todo_hard",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "[[IMPORTANT]] only one unread"},
            ],
        )

        captured: list[str] = []

        def fake(prompt: str) -> str:
            captured.append(prompt)
            return "YES"

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        with patch(
            "hypergumbo_tracker.stop_hook._call_openrouter",
            side_effect=fake,
        ):
            result = generate_guidance(
                tracker_root, guidance_dir=guidance_dir, config=config,
            )
        content = Path(result).read_text()

        assert captured == []
        assert "## Escalated" not in content
        assert "WI-lone" in content

    def test_non_prefaced_messages_never_call_gate(
        self, tmp_path: Path,
    ) -> None:
        """When no message has a preface, the gate is not invoked."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item_with_discussion(
            canonical_ops, "WI-a", "todo_hard",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "plain message"},
            ],
        )
        _add_item_with_discussion(
            canonical_ops, "WI-b", "todo_hard",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "another plain message"},
            ],
        )

        captured: list[str] = []

        def fake(prompt: str) -> str:
            captured.append(prompt)
            return "YES"

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        with patch(
            "hypergumbo_tracker.stop_hook._call_openrouter",
            side_effect=fake,
        ):
            generate_guidance(
                tracker_root, guidance_dir=guidance_dir, config=config,
            )
        assert captured == []

    def test_ambiguous_preface_still_invokes_gate(
        self, tmp_path: Path,
    ) -> None:
        """[[Mclovin]] is still a valid preface — the gate decides meaning."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item_with_discussion(
            canonical_ops, "WI-m", "todo_hard",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "[[Mclovin]] strange tag"},
            ],
        )
        _add_item_with_discussion(
            canonical_ops, "WI-o", "todo_hard",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "other"},
            ],
        )

        captured: list[str] = []

        def fake(prompt: str) -> str:
            captured.append(prompt)
            return "NO"

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        with patch(
            "hypergumbo_tracker.stop_hook._call_openrouter",
            side_effect=fake,
        ):
            generate_guidance(
                tracker_root, guidance_dir=guidance_dir, config=config,
            )
        assert len(captured) == 1
        # The gate receives a well-formed prompt naming the preface and others
        assert "[[Mclovin]]" in captured[0]
        assert "WI-o" in captured[0]

    def test_non_blocking_prefaced_item_can_be_elevated(
        self, tmp_path: Path,
    ) -> None:
        """A non-blocking (done) item with a preface can still be escalated."""
        tracker_root = _setup_tracker(tmp_path)
        canonical_ops = tracker_root / "tracker" / ".ops"
        _add_item_with_discussion(
            canonical_ops, "WI-done-urgent", "done",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "[[IMPORTANT]] revisit"},
            ],
        )
        _add_item_with_discussion(
            canonical_ops, "WI-block", "todo_hard",
            discussion_ops=[
                {"at": "2026-01-15T10:00:00Z", "by": "human", "actor": "jgstern",
                 "message": "other"},
            ],
        )

        def fake(prompt: str) -> str:
            return "YES"

        config = _make_config()
        guidance_dir = tmp_path / "guidance"
        with patch(
            "hypergumbo_tracker.stop_hook._call_openrouter",
            side_effect=fake,
        ):
            result = generate_guidance(
                tracker_root, guidance_dir=guidance_dir, config=config,
            )
        content = Path(result).read_text()
        assert "## Escalated" in content
        assert "WI-done-urgent" in content
        # It should not also appear in the non-blocking section below
        esc_pos = content.index("## Escalated")
        non_block_pos = content.find("## Non-blocking items with unread messages")
        if non_block_pos != -1:
            non_block_section = content[non_block_pos:]
            assert "WI-done-urgent" not in non_block_section
        assert esc_pos < content.index("## Blocking items with unread messages")
