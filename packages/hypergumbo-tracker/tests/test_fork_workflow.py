# SPDX-License-Identifier: MPL-2.0
"""End-to-end test for the fork-based tracker workflow.

Simulates the complete fork contributor workflow:
1. Fork-setup sets scope to workspace
2. Agent creates workspace items (not canonical)
3. Contribute would exclude workspace files
4. Promote moves workspace items to canonical
5. Promoted items appear in canonical tier

Uses the tracker CLI and TrackerSet directly with temp directories
and mocked actor resolution (human/agent authority).
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_tracker.cli import main, EXIT_SUCCESS, EXIT_USER_ERROR
from hypergumbo_tracker.models import KindConfig, TrackerConfig
from hypergumbo_tracker.trackerset import TrackerSet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> TrackerConfig:
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


def _setup_tracker_root(tmp_path: Path) -> Path:
    """Create tracker directory structure and config file."""
    import yaml

    tracker_root = tmp_path / ".agent"
    (tracker_root / "tracker" / ".ops").mkdir(parents=True)
    (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
    (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)

    config_path = tracker_root / "tracker" / "config.yaml"
    config_path.write_text(yaml.dump({
        "kinds": {
            "invariant": {"prefix": "INV", "description": "Test invariant"},
            "work_item": {"prefix": "WI", "description": "Work item"},
        },
        "statuses": ["todo_hard", "todo_soft", "in_progress", "done", "deferred", "wont_do"],
        "stop_hook": {
            "blocking_statuses": ["todo_hard", "todo_soft"],
            "resolved_statuses": ["done", "deferred", "wont_do"],
        },
        "actor_resolution": {"agent_usernames": ["*_agent"]},
        "lamport_branches": ["dev", "main"],
    }))
    return tracker_root


def _add_canonical_item(ops_dir: Path, item_id: str,
                        status: str = "todo_hard") -> None:
    """Add a pre-existing canonical item (simulates upstream content)."""
    content = textwrap.dedent(f"""\
        - op: create
          at: "2026-01-01T00:00:00Z"
          by: agent
          actor: upstream_agent
          clock: 1
          nonce: a1b2
          data:
            kind: work_item
            title: "Upstream item {item_id}"
            status: {status}
            priority: 2
    """)
    (ops_dir / f".{item_id}.ops").write_text(content)


# ---------------------------------------------------------------------------
# E2E test
# ---------------------------------------------------------------------------


class TestForkWorkflow:
    """End-to-end fork workflow: setup -> workspace writes -> promote."""

    def test_full_fork_lifecycle(self, tmp_path: Path,
                                 mock_human_uid: None) -> None:
        """Complete fork workflow: setup, workspace add, promote."""
        tracker_root = _setup_tracker_root(tmp_path)

        # 1. Simulate upstream canonical item
        _add_canonical_item(tracker_root / "tracker" / ".ops", "WI-upstream")

        # 2. Fork-setup: sets scope to workspace
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "fork-setup"])
        assert exc.value.code == EXIT_SUCCESS

        # Verify config was updated with scope=workspace
        import yaml
        config_path = tracker_root / "tracker" / "config.yaml"
        config_data = yaml.safe_load(config_path.read_text())
        assert config_data["stop_hook"]["scope"] == "workspace"

        # 3. Create a TrackerSet and add a workspace item
        config = _make_config()
        ts = TrackerSet(tracker_root, config=config)

        # Agent adds to workspace tier
        with patch("hypergumbo_tracker.store.resolve_actor",
                   return_value=("agent", "fork_agent")):
            item_id = ts.workspace.add(
                kind="work_item",
                title="Fork-local investigation",
                status="todo_hard",
                priority=1,
            )

        # 4. Verify workspace item exists in workspace tier only
        ws_ops = list((tracker_root / "tracker-workspace" / ".ops").glob("*.ops"))
        canon_ops = list((tracker_root / "tracker" / ".ops").glob("*.ops"))
        assert len(ws_ops) == 1, "Workspace should have 1 item"
        assert len(canon_ops) == 1, "Canonical should still have only upstream item"

        # 5. Verify contribute would detect workspace files
        # (check that workspace dir has files that contribute would exclude)
        workspace_files = list(
            (tracker_root / "tracker-workspace").rglob("*.ops")
        )
        assert len(workspace_files) > 0, "Workspace has files to exclude"

        # 6. Promote workspace item to canonical
        ts.promote(item_id)

        # 7. Verify promoted item is now in canonical
        canon_ops_after = list((tracker_root / "tracker" / ".ops").glob("*.ops"))
        assert len(canon_ops_after) == 2, "Canonical should now have 2 items"

        # Verify item is no longer in workspace
        ws_ops_after = list(
            (tracker_root / "tracker-workspace" / ".ops").glob("*.ops")
        )
        assert len(ws_ops_after) == 0, "Workspace should be empty after promote"

    def test_fork_setup_requires_human(self, tmp_path: Path,
                                       mock_agent_uid: None) -> None:
        """Fork-setup must reject agent authority."""
        tracker_root = _setup_tracker_root(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "fork-setup"])
        assert exc.value.code == EXIT_USER_ERROR

    def test_workspace_scope_excludes_canonical_from_count(
        self, tmp_path: Path, mock_human_uid: None
    ) -> None:
        """With scope=workspace, count-todos only counts workspace items."""
        tracker_root = _setup_tracker_root(tmp_path)

        # Add canonical blocking item
        _add_canonical_item(tracker_root / "tracker" / ".ops", "WI-canon",
                            status="todo_hard")

        # Fork-setup
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(tracker_root), "fork-setup"])

        # Add workspace blocking item
        config = _make_config()
        ts = TrackerSet(tracker_root, config=config)
        with patch("hypergumbo_tracker.store.resolve_actor",
                   return_value=("agent", "fork_agent")):
            ts.workspace.add(
                kind="work_item",
                title="Workspace blocker",
                status="todo_hard",
                priority=1,
            )

        # Count todos with scope=workspace should only count workspace item
        from hypergumbo_tracker.stop_hook import count_todos
        hard_count = count_todos(tracker_root, hard=True)
        # With workspace scope: canonical items are excluded
        assert hard_count == 1, "Only workspace blocking item should count"

    def test_workspace_item_stays_in_workspace_tier(
        self, tmp_path: Path
    ) -> None:
        """Items added to workspace store stay in workspace directory."""
        tracker_root = _setup_tracker_root(tmp_path)
        config = _make_config()
        ts = TrackerSet(tracker_root, config=config)

        with patch("hypergumbo_tracker.store.resolve_actor",
                   return_value=("agent", "test_agent")):
            item_id = ts.workspace.add(
                kind="invariant",
                title="Local invariant",
                status="todo_soft",
            )

        # Confirm it's in workspace
        ws_files = list((tracker_root / "tracker-workspace" / ".ops").glob(
            "*.ops"
        ))
        assert any(item_id in f.name for f in ws_files)

        # Confirm it's NOT in canonical
        canon_files = list((tracker_root / "tracker" / ".ops").glob("*.ops"))
        assert not any(item_id in f.name for f in canon_files)

    def test_promote_then_list_shows_canonical(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
        mock_human_uid: None
    ) -> None:
        """After promote, item appears in canonical tier in CLI list."""
        tracker_root = _setup_tracker_root(tmp_path)
        config = _make_config()
        ts = TrackerSet(tracker_root, config=config)

        with patch("hypergumbo_tracker.store.resolve_actor",
                   return_value=("agent", "test_agent")):
            item_id = ts.workspace.add(
                kind="work_item",
                title="Promotable item",
                status="in_progress",
            )

        # Promote
        ts.promote(item_id)

        # List via CLI and check tier marker
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(tracker_root), "list"])
        assert exc.value.code == EXIT_SUCCESS
        output = capsys.readouterr().out
        assert "Promotable item" in output
        # After promotion, should show as canonical
        assert "[canonical]" in output or "[C]" in output
