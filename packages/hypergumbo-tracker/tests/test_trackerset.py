# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.trackerset.

Covers TrackerSet construction, merged reads, write routing, tier movement,
scope-aware count_todos, self-healing reconciliation, and cache integration
hooks.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from hypergumbo_tracker.models import (
    CompiledItem,
    Tier,
    TrackerConfig,
    load_config,
)
from hypergumbo_tracker.store import (
    AmbiguousPrefixError,
    HumanAuthorityError,
    ItemNotFoundError,
    Store,
    _parse_ops_file,
    _serialize_op,
    compile_ops,
)
from hypergumbo_tracker.trackerset import (
    ReconciliationError,
    TierMovementError,
    TrackerSet,
    _MAX_RECONCILE_ATTEMPTS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tracker_root(tmp_path: Path, config_yaml: Path) -> Path:
    """Create a 3-tier tracker directory structure."""
    root = tmp_path / ".agent"
    canonical_dir = root / "tracker"
    canonical_ops = canonical_dir / ".ops"
    workspace_dir = root / "tracker-workspace"
    workspace_ops = workspace_dir / ".ops"
    stealth = workspace_dir / "stealth"
    canonical_ops.mkdir(parents=True)
    workspace_ops.mkdir(parents=True)
    stealth.mkdir(parents=True)
    # Copy config to tracker dirs
    shutil.copy(config_yaml, canonical_dir / "config.yaml")
    shutil.copy(config_yaml, workspace_dir / "config.yaml")
    return root


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tracker_root(tmp_path: Path, config_yaml: Path) -> Path:
    """Create a 3-tier tracker directory structure."""
    return _make_tracker_root(tmp_path, config_yaml)


@pytest.fixture()
def tracker_set(tracker_root: Path) -> TrackerSet:
    """TrackerSet with no cache (pure Store-backed)."""
    return TrackerSet(tracker_root)


# ---------------------------------------------------------------------------
# TrackerSet constructor
# ---------------------------------------------------------------------------


class TestTrackerSetConstructor:
    def test_creates_three_stores(
        self, tracker_root: Path, mock_agent_uid: None
    ) -> None:
        ts = TrackerSet(tracker_root)
        assert ts.canonical is not None
        assert ts.workspace is not None
        assert ts.stealth is not None

    def test_creates_dirs_if_missing(
        self, tmp_path: Path, config_yaml: Path, mock_agent_uid: None
    ) -> None:
        root = tmp_path / ".agent"
        # Only create tracker dir with config, let TrackerSet create the rest
        canonical_dir = root / "tracker"
        canonical_dir.mkdir(parents=True)
        shutil.copy(config_yaml, canonical_dir / "config.yaml")

        ts = TrackerSet(root)
        assert (root / "tracker" / ".ops").is_dir()
        assert (root / "tracker-workspace" / ".ops").is_dir()
        assert (root / "tracker-workspace" / "stealth").is_dir()

    def test_uses_provided_config(
        self, tracker_root: Path, mock_agent_uid: None
    ) -> None:
        config = load_config(tracker_root / "tracker")
        ts = TrackerSet(tracker_root, config=config)
        assert ts.config is config

    def test_loads_config_from_canonical(
        self, tracker_root: Path, mock_agent_uid: None
    ) -> None:
        ts = TrackerSet(tracker_root)
        assert ts.config is not None
        assert "invariant" in ts.config.kinds


# ---------------------------------------------------------------------------
# Merged reads: list_items
# ---------------------------------------------------------------------------


class TestListItems:
    def test_merged_list_from_all_tiers(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        id1 = tracker_set.add("invariant", "Canonical Item", tier=Tier.CANONICAL)
        id2 = tracker_set.add("work_item", "Workspace Item", tier=Tier.WORKSPACE,
                              not_duplicate_of=[id1])
        items = tracker_set.list_items()
        ids = {i.id for i in items}
        assert id1 in ids
        assert id2 in ids

    def test_items_annotated_with_tier(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        id1 = tracker_set.add("invariant", "Can", tier=Tier.CANONICAL)
        id2 = tracker_set.add("work_item", "Work", tier=Tier.WORKSPACE,
                              not_duplicate_of=[id1])
        items = tracker_set.list_items()
        item_map = {i.id: i for i in items}
        assert item_map[id1].tier == Tier.CANONICAL
        assert item_map[id2].tier == Tier.WORKSPACE

    def test_filter_by_tier(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        id1 = tracker_set.add("invariant", "Can", tier=Tier.CANONICAL)
        tracker_set.add("work_item", "Work", tier=Tier.WORKSPACE,
                        not_duplicate_of=[id1])
        items = tracker_set.list_items(tier=Tier.CANONICAL)
        assert len(items) == 1
        assert items[0].id == id1

    def test_filter_by_status(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        id1 = tracker_set.add("invariant", "Hard", tier=Tier.WORKSPACE,
                              status="todo_hard")
        id2 = tracker_set.add("work_item", "Soft", tier=Tier.WORKSPACE,
                              status="todo_soft", not_duplicate_of=[id1])
        items = tracker_set.list_items(status="todo_hard")
        assert len(items) == 1
        assert items[0].id == id1

    def test_filter_by_kind(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        id1 = tracker_set.add("invariant", "Inv", tier=Tier.WORKSPACE)
        tracker_set.add("work_item", "WI", tier=Tier.WORKSPACE,
                        not_duplicate_of=[id1])
        items = tracker_set.list_items(kind="invariant")
        assert len(items) == 1
        assert items[0].id == id1

    def test_sorted_by_priority(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        id1 = tracker_set.add("invariant", "Low Priority", tier=Tier.WORKSPACE,
                              priority=3)
        id2 = tracker_set.add("work_item", "High Priority", tier=Tier.CANONICAL,
                              priority=1, not_duplicate_of=[id1])
        items = tracker_set.list_items()
        assert items[0].id == id2
        assert items[1].id == id1

    def test_empty_result(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        items = tracker_set.list_items()
        assert items == []


# ---------------------------------------------------------------------------
# Merged reads: ready()
# ---------------------------------------------------------------------------


class TestReady:
    def test_ready_across_tiers(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        id1 = tracker_set.add("invariant", "Ready Can", tier=Tier.CANONICAL)
        id2 = tracker_set.add("work_item", "Ready WS", tier=Tier.WORKSPACE,
                              not_duplicate_of=[id1])
        ready = tracker_set.ready()
        ids = {i.id for i in ready}
        assert id1 in ids
        assert id2 in ids

    def test_cross_tier_before_blocks(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """Item in canonical with before:[Y] blocks Y in workspace."""
        id_y = tracker_set.add("work_item", "Target Y", tier=Tier.WORKSPACE)
        # Item X has before: [id_y] — meaning X blocks Y
        id_x = tracker_set.add("invariant", "Blocker X", tier=Tier.CANONICAL,
                               before=[id_y], not_duplicate_of=[id_y])
        ready = tracker_set.ready()
        ready_ids = {i.id for i in ready}
        assert id_x in ready_ids  # X is ready (not blocked)
        assert id_y not in ready_ids  # Y is blocked by X

    def test_resolved_blocker_unblocks(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """Once blocker is resolved, target becomes ready."""
        id_y = tracker_set.add("work_item", "Target", tier=Tier.WORKSPACE)
        id_x = tracker_set.add("invariant", "Blocker", tier=Tier.CANONICAL,
                               before=[id_y], not_duplicate_of=[id_y])
        # Resolve the blocker
        tracker_set.update(id_x, set_fields={"status": "done"})
        ready = tracker_set.ready()
        ready_ids = {i.id for i in ready}
        assert id_y in ready_ids


# ---------------------------------------------------------------------------
# Cross-tier get() and prefix resolution
# ---------------------------------------------------------------------------


class TestGet:
    def test_get_from_any_tier(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        id1 = tracker_set.add("invariant", "In Canonical", tier=Tier.CANONICAL)
        item = tracker_set.get(id1)
        assert item.id == id1
        assert item.tier == Tier.CANONICAL

    def test_get_not_found(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        with pytest.raises(ItemNotFoundError):
            tracker_set.get("INV-nonexistent")


# ---------------------------------------------------------------------------
# Cross-tier children() and ancestors()
# ---------------------------------------------------------------------------


class TestTreeTraversal:
    def test_children_span_tiers(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        parent_id = tracker_set.add("invariant", "Parent", tier=Tier.CANONICAL)
        child_id = tracker_set.add("work_item", "Child", tier=Tier.WORKSPACE,
                                   parent=parent_id, not_duplicate_of=[parent_id])
        children = tracker_set.children(parent_id)
        assert len(children) == 1
        assert children[0].id == child_id
        assert children[0].tier == Tier.WORKSPACE

    def test_ancestors_span_tiers(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        parent_id = tracker_set.add("invariant", "Grandparent", tier=Tier.CANONICAL)
        child_id = tracker_set.add("work_item", "Child", tier=Tier.WORKSPACE,
                                   parent=parent_id, not_duplicate_of=[parent_id])
        ancestors = tracker_set.ancestors(child_id)
        assert len(ancestors) == 1
        assert ancestors[0].id == parent_id
        assert ancestors[0].tier == Tier.CANONICAL


# ---------------------------------------------------------------------------
# Write routing
# ---------------------------------------------------------------------------


class TestWriteRouting:
    def test_add_default_workspace(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Default Tier")
        item = tracker_set.get(item_id)
        assert item.tier == Tier.WORKSPACE

    def test_add_to_canonical(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Canonical", tier=Tier.CANONICAL)
        item = tracker_set.get(item_id)
        assert item.tier == Tier.CANONICAL

    def test_add_to_stealth(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Stealth Item", tier=Tier.STEALTH)
        item = tracker_set.get(item_id)
        assert item.tier == Tier.STEALTH

    def test_update_routes_to_correct_tier(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Update Me", tier=Tier.CANONICAL)
        tracker_set.update(item_id, set_fields={"status": "in_progress"})
        item = tracker_set.get(item_id)
        assert item.status == "in_progress"

    def test_discuss_routes_to_correct_tier(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Discuss Me", tier=Tier.WORKSPACE)
        tracker_set.discuss(item_id, message="test discussion")
        item = tracker_set.get(item_id)
        assert len(item.discussion) == 1

    def test_lock_routes_to_correct_tier(
        self, tracker_set: TrackerSet, mock_human_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Lock Me", tier=Tier.WORKSPACE)
        tracker_set.lock(item_id, ["status"])
        item = tracker_set.get(item_id)
        assert "status" in item.locked_fields

    def test_unlock_routes_to_correct_tier(
        self, tracker_set: TrackerSet, mock_human_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Unlock Me", tier=Tier.WORKSPACE)
        tracker_set.lock(item_id, ["status"])
        tracker_set.unlock(item_id, ["status"])
        item = tracker_set.get(item_id)
        assert "status" not in item.locked_fields


# ---------------------------------------------------------------------------
# count_todos (scope-aware)
# ---------------------------------------------------------------------------


class TestCountTodos:
    def test_scope_all_counts_everything(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        id1 = tracker_set.add("invariant", "Can Todo", tier=Tier.CANONICAL)
        id2 = tracker_set.add("work_item", "WS Todo", tier=Tier.WORKSPACE,
                              not_duplicate_of=[id1])
        assert tracker_set.count_todos() >= 2

    def test_scope_workspace_excludes_canonical(
        self, tracker_root: Path, mock_agent_uid: None
    ) -> None:
        config = load_config(tracker_root / "tracker")
        # Create a config with workspace scope
        from dataclasses import replace
        ws_config = replace(config, scope="workspace")
        ts = TrackerSet(tracker_root, config=ws_config)

        id1 = ts.add("invariant", "Can Todo", tier=Tier.CANONICAL)
        ts.add("work_item", "WS Todo", tier=Tier.WORKSPACE,
               not_duplicate_of=[id1])
        assert ts.count_todos() == 1  # Only workspace counted

    def test_stealth_always_counted(
        self, tracker_root: Path, mock_agent_uid: None
    ) -> None:
        config = load_config(tracker_root / "tracker")
        from dataclasses import replace
        ws_config = replace(config, scope="workspace")
        ts = TrackerSet(tracker_root, config=ws_config)

        id1 = ts.add("invariant", "Stealth Todo", tier=Tier.STEALTH)
        assert ts.count_todos() == 1

    def test_resolved_not_counted(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Done Item", tier=Tier.WORKSPACE,
                                  status="done")
        assert tracker_set.count_todos() == 0


# ---------------------------------------------------------------------------
# Tier movement: promote / demote
# ---------------------------------------------------------------------------


class TestPromoteDemote:
    def test_promote_workspace_to_canonical(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Promote Me", tier=Tier.WORKSPACE)
        tracker_set.promote(item_id)
        item = tracker_set.get(item_id)
        assert item.tier == Tier.CANONICAL

    def test_promote_from_wrong_tier_raises(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Already Can", tier=Tier.CANONICAL)
        with pytest.raises(TierMovementError, match="workspace"):
            tracker_set.promote(item_id)

    def test_demote_canonical_to_workspace(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Demote Me", tier=Tier.CANONICAL)
        tracker_set.demote(item_id)
        item = tracker_set.get(item_id)
        assert item.tier == Tier.WORKSPACE

    def test_demote_from_wrong_tier_raises(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "In WS", tier=Tier.WORKSPACE)
        with pytest.raises(TierMovementError, match="canonical"):
            tracker_set.demote(item_id)

    def test_promote_then_demote_roundtrip(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Roundtrip", tier=Tier.WORKSPACE)
        tracker_set.promote(item_id)
        assert tracker_set.get(item_id).tier == Tier.CANONICAL
        tracker_set.demote(item_id)
        assert tracker_set.get(item_id).tier == Tier.WORKSPACE

    def test_promote_preserves_ops(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Ops Preserved", tier=Tier.WORKSPACE)
        tracker_set.update(item_id, set_fields={"status": "in_progress"})
        tracker_set.promote(item_id)
        item = tracker_set.get(item_id)
        assert item.status == "in_progress"
        assert item.tier == Tier.CANONICAL


# ---------------------------------------------------------------------------
# Tier movement: stealth / unstealth
# ---------------------------------------------------------------------------


class TestStealthUnstealth:
    def test_stealth_as_human(
        self, tracker_root: Path, mock_human_uid: None
    ) -> None:
        ts = TrackerSet(tracker_root)
        item_id = ts.add("invariant", "Stealth Me", tier=Tier.WORKSPACE)
        ts.stealth_item(item_id)
        item = ts.get(item_id)
        assert item.tier == Tier.STEALTH

    def test_stealth_as_agent_raises(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "No Stealth", tier=Tier.WORKSPACE)
        with pytest.raises(HumanAuthorityError, match="stealth"):
            tracker_set.stealth_item(item_id)

    def test_stealth_from_wrong_tier_raises(
        self, tracker_root: Path, mock_human_uid: None
    ) -> None:
        ts = TrackerSet(tracker_root)
        item_id = ts.add("invariant", "In Can", tier=Tier.CANONICAL)
        with pytest.raises(TierMovementError, match="workspace"):
            ts.stealth_item(item_id)

    def test_unstealth_as_human(
        self, tracker_root: Path, mock_human_uid: None
    ) -> None:
        ts = TrackerSet(tracker_root)
        item_id = ts.add("invariant", "Unstealth Me", tier=Tier.WORKSPACE)
        ts.stealth_item(item_id)
        ts.unstealth_item(item_id)
        item = ts.get(item_id)
        assert item.tier == Tier.WORKSPACE

    def test_unstealth_as_agent_raises(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        # Manually create a stealth item
        item_id = tracker_set.add("invariant", "Stealthed", tier=Tier.STEALTH)
        with pytest.raises(HumanAuthorityError, match="unstealth"):
            tracker_set.unstealth_item(item_id)

    def test_unstealth_from_wrong_tier_raises(
        self, tracker_root: Path, mock_human_uid: None
    ) -> None:
        ts = TrackerSet(tracker_root)
        item_id = ts.add("invariant", "In WS", tier=Tier.WORKSPACE)
        with pytest.raises(TierMovementError, match="stealth"):
            ts.unstealth_item(item_id)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


class TestReconciliation:
    def _create_duplicate(
        self, tracker_set: TrackerSet, item_id: str, src_tier: Tier, dst_tier: Tier
    ) -> None:
        """Manually copy an item's ops file to another tier to simulate conflict."""
        src_store = tracker_set._tier_stores[src_tier]
        dst_store = tracker_set._tier_stores[dst_tier]
        src_path = src_store.item_path(item_id)
        dst_path = dst_store.item_path(item_id)
        shutil.copy(str(src_path), str(dst_path))

    def test_reconcile_with_promote_history(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """Item with promote op history → auto-merge to canonical."""
        item_id = tracker_set.add("invariant", "Reconcile Promote", tier=Tier.WORKSPACE)
        tracker_set.promote(item_id)
        # Simulate conflict: copy canonical item back to workspace
        self._create_duplicate(tracker_set, item_id, Tier.CANONICAL, Tier.WORKSPACE)
        reconciled = tracker_set.reconcile()
        assert item_id in reconciled
        # Should be in canonical only
        assert tracker_set.get(item_id).tier == Tier.CANONICAL
        assert not tracker_set.workspace.item_path(item_id).exists()

    def test_reconcile_no_movement_ops_flags_conflict(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """No tier-movement ops → flag cross_tier_conflict via reconcile op."""
        item_id = tracker_set.add("invariant", "Conflict", tier=Tier.WORKSPACE)
        # Copy to canonical to create duplicate without movement ops
        self._create_duplicate(tracker_set, item_id, Tier.WORKSPACE, Tier.CANONICAL)
        reconciled = tracker_set.reconcile()
        assert item_id in reconciled
        # Both should have reconcile ops now
        can_ops = _parse_ops_file(tracker_set.canonical.item_path(item_id))
        ws_ops = _parse_ops_file(tracker_set.workspace.item_path(item_id))
        assert any(o.get("op") == "reconcile" for o in can_ops)
        assert any(o.get("op") == "reconcile" for o in ws_ops)

    def test_reconcile_max_attempts_stops(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """After MAX_RECONCILE_ATTEMPTS, stop trying."""
        item_id = tracker_set.add("invariant", "Max Attempts", tier=Tier.WORKSPACE)
        self._create_duplicate(tracker_set, item_id, Tier.WORKSPACE, Tier.CANONICAL)

        # Run reconcile multiple times until max attempts
        for _ in range(_MAX_RECONCILE_ATTEMPTS):
            tracker_set.reconcile()

        # Next reconcile should skip this item
        # Add another reconcile op manually to reach the limit
        reconciled = tracker_set.reconcile()
        assert item_id not in reconciled

    def test_reconcile_appends_op(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """Reconciliation appends a reconcile op, never deletes existing ops."""
        item_id = tracker_set.add("invariant", "Append Only", tier=Tier.WORKSPACE)
        # Get initial op count
        initial_ops = _parse_ops_file(tracker_set.workspace.item_path(item_id))
        initial_count = len(initial_ops)

        self._create_duplicate(tracker_set, item_id, Tier.WORKSPACE, Tier.CANONICAL)
        tracker_set.reconcile()

        # The item should still have its create op (plus reconcile)
        # Check whichever tier it ended up in
        for _t, store in tracker_set._tier_stores.items():
            path = store.item_path(item_id)
            if path.exists():
                ops = _parse_ops_file(path)
                # Should have at least the original ops
                assert any(o.get("op") == "create" for o in ops)
                break

    def test_reconcile_empty_store(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """Reconcile on empty store returns empty list."""
        assert tracker_set.reconcile() == []

    def test_reconcile_no_duplicates(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """Reconcile skips items only in one tier."""
        tracker_set.add("invariant", "Solo Item", tier=Tier.WORKSPACE)
        reconciled = tracker_set.reconcile()
        assert reconciled == []


class TestReconcileReset:
    def test_reconcile_reset_as_human(
        self, tracker_root: Path, mock_human_uid: None
    ) -> None:
        ts = TrackerSet(tracker_root)
        item_id = ts.add("invariant", "Reset Me", tier=Tier.WORKSPACE)
        # Create duplicate in canonical
        shutil.copy(
            str(ts.workspace.item_path(item_id)),
            str(ts.canonical.item_path(item_id)),
        )
        ts.reconcile_reset(item_id)
        # Should be in canonical only
        assert ts.canonical.item_path(item_id).exists()
        assert not ts.workspace.item_path(item_id).exists()

    def test_reconcile_reset_as_agent_raises(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "No Reset", tier=Tier.WORKSPACE)
        with pytest.raises(HumanAuthorityError, match="reconcile_reset"):
            tracker_set.reconcile_reset(item_id)

    def test_reconcile_reset_not_found(
        self, tracker_root: Path, mock_human_uid: None
    ) -> None:
        ts = TrackerSet(tracker_root)
        with pytest.raises(ItemNotFoundError):
            ts.reconcile_reset("INV-nonexistent")


# ---------------------------------------------------------------------------
# _resolve_id cross-tier
# ---------------------------------------------------------------------------


class TestResolveId:
    def test_resolve_full_id(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "Full ID", tier=Tier.CANONICAL)
        full, store, tier = tracker_set._resolve_id(item_id)
        assert full == item_id
        assert tier == Tier.CANONICAL

    def test_resolve_not_found_raises(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        with pytest.raises(ItemNotFoundError):
            tracker_set._resolve_id("INV-nonexistent")

    def test_resolve_ambiguous_different_ids(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """Different items with same prefix across tiers raises ambiguous."""
        # This is hard to trigger naturally since content-hash IDs differ,
        # but we can create items with similar prefixes across tiers
        id1 = tracker_set.add("invariant", "First", tier=Tier.CANONICAL)
        id2 = tracker_set.add("invariant", "Second", tier=Tier.WORKSPACE,
                              not_duplicate_of=[id1])
        # Both are INV- prefixed, so "INV-" should be ambiguous across tiers
        with pytest.raises(AmbiguousPrefixError):
            tracker_set._resolve_id("INV-")

    def test_resolve_same_id_multiple_tiers_returns_canonical(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """Same ID in multiple tiers → returns canonical."""
        item_id = tracker_set.add("invariant", "Dup Item", tier=Tier.WORKSPACE)
        # Copy to canonical
        shutil.copy(
            str(tracker_set.workspace.item_path(item_id)),
            str(tracker_set.canonical.item_path(item_id)),
        )
        _, _, tier = tracker_set._resolve_id(item_id)
        assert tier == Tier.CANONICAL


# ---------------------------------------------------------------------------
# _find_item_tier
# ---------------------------------------------------------------------------


class TestFindItemTier:
    def test_find_in_canonical(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        item_id = tracker_set.add("invariant", "In Can", tier=Tier.CANONICAL)
        store, tier = tracker_set._find_item_tier(item_id)
        assert tier == Tier.CANONICAL

    def test_find_not_found_raises(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        with pytest.raises(ItemNotFoundError):
            tracker_set._find_item_tier("INV-does-not-exist")


# ---------------------------------------------------------------------------
# Cache integration hooks
# ---------------------------------------------------------------------------


class TestCacheHooks:
    def test_no_cache_noop(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """Operations work without cache (no-op cache hooks)."""
        item_id = tracker_set.add("invariant", "No Cache")
        tracker_set.update(item_id, set_fields={"status": "in_progress"})
        assert tracker_set.get(item_id).status == "in_progress"

    def test_set_caches_wired(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """set_caches stores cache instances."""
        mock_cache = MagicMock()
        tracker_set.set_caches({Tier.WORKSPACE: mock_cache})
        item_id = tracker_set.add("invariant", "With Cache")
        # Cache upsert should have been called
        mock_cache.upsert.assert_called()

    def test_cache_upsert_on_update(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """Update triggers cache upsert."""
        item_id = tracker_set.add("invariant", "Cache Update")
        mock_cache = MagicMock()
        tracker_set.set_caches({Tier.WORKSPACE: mock_cache})
        tracker_set.update(item_id, set_fields={"status": "done"})
        assert mock_cache.upsert.call_count >= 1

    def test_cache_upsert_on_discuss(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """Discuss triggers cache upsert."""
        item_id = tracker_set.add("invariant", "Cache Discuss")
        mock_cache = MagicMock()
        tracker_set.set_caches({Tier.WORKSPACE: mock_cache})
        tracker_set.discuss(item_id, message="test")
        assert mock_cache.upsert.call_count >= 1

    def test_cache_delete_on_promote(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """Promote deletes from workspace cache, upserts to canonical cache."""
        item_id = tracker_set.add("invariant", "Cache Promote")
        ws_cache = MagicMock()
        can_cache = MagicMock()
        tracker_set.set_caches({
            Tier.WORKSPACE: ws_cache,
            Tier.CANONICAL: can_cache,
        })
        tracker_set.promote(item_id)
        ws_cache.delete.assert_called_with(item_id)
        can_cache.upsert.assert_called()

    def test_cache_delete_item_no_cache(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """_cache_delete_item is no-op when no cache set."""
        # Should not raise
        tracker_set._cache_delete_item(Tier.WORKSPACE, "fake-id")

    def test_cache_upsert_item_no_cache(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """_cache_upsert_item is no-op when no cache set."""
        tracker_set._cache_upsert_item(
            Tier.WORKSPACE, tracker_set.workspace, "fake-id"
        )

    def test_cache_upsert_item_missing_tier(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """_cache_upsert_item is no-op when tier not in caches dict."""
        tracker_set.set_caches({})  # Empty caches dict
        tracker_set._cache_upsert_item(
            Tier.WORKSPACE, tracker_set.workspace, "fake-id"
        )

    def test_cache_delete_item_missing_tier(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """_cache_delete_item is no-op when tier not in caches dict."""
        tracker_set.set_caches({})
        tracker_set._cache_delete_item(Tier.WORKSPACE, "fake-id")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_determine_reconcile_winner_demote(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """Item with demote ops → workspace wins."""
        item_id = tracker_set.add("invariant", "Demote Winner", tier=Tier.CANONICAL)
        tracker_set.demote(item_id)
        # Now in workspace with demote op history
        # Create conflict by copying to canonical
        shutil.copy(
            str(tracker_set.workspace.item_path(item_id)),
            str(tracker_set.canonical.item_path(item_id)),
        )
        winner = tracker_set._determine_reconcile_winner(
            item_id, [Tier.CANONICAL, Tier.WORKSPACE]
        )
        assert winner == Tier.WORKSPACE

    def test_determine_reconcile_winner_none(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """No movement ops → returns None."""
        item_id = tracker_set.add("invariant", "No Movement", tier=Tier.WORKSPACE)
        shutil.copy(
            str(tracker_set.workspace.item_path(item_id)),
            str(tracker_set.canonical.item_path(item_id)),
        )
        winner = tracker_set._determine_reconcile_winner(
            item_id, [Tier.CANONICAL, Tier.WORKSPACE]
        )
        assert winner is None

    def test_lock_on_canonical_item(
        self, tracker_root: Path, mock_human_uid: None
    ) -> None:
        """Lock works on canonical items."""
        ts = TrackerSet(tracker_root)
        item_id = ts.add("invariant", "Lock Can", tier=Tier.CANONICAL)
        ts.lock(item_id, ["status"])
        item = ts.get(item_id)
        assert "status" in item.locked_fields

    def test_unlock_on_stealth_item(
        self, tracker_root: Path, mock_human_uid: None
    ) -> None:
        """Unlock works on stealth items."""
        ts = TrackerSet(tracker_root)
        item_id = ts.add("invariant", "Lock Stealth", tier=Tier.STEALTH)
        ts.lock(item_id, ["title"])
        ts.unlock(item_id, ["title"])
        item = ts.get(item_id)
        assert "title" not in item.locked_fields

    def test_ready_excludes_duplicates(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """ready() excludes items with duplicate_of set."""
        id1 = tracker_set.add("invariant", "Original", tier=Tier.WORKSPACE)
        id2 = tracker_set.add("work_item", "Dup", tier=Tier.CANONICAL,
                              duplicate_of=[id1], not_duplicate_of=[])
        ready = tracker_set.ready()
        ready_ids = {i.id for i in ready}
        assert id1 in ready_ids
        assert id2 not in ready_ids

    def test_ready_excludes_cross_tier_conflict(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """ready() excludes items flagged with cross_tier_conflict."""
        from unittest.mock import patch

        item_id = tracker_set.add("invariant", "Conflicted", tier=Tier.WORKSPACE)

        # Patch _compile_all on the workspace store to return an item
        # with cross_tier_conflict=True
        original_compile = tracker_set.workspace._compile_all

        def patched_compile() -> list[CompiledItem]:
            items = original_compile()
            for item in items:
                item.cross_tier_conflict = True
            return items

        with patch.object(tracker_set.workspace, "_compile_all", patched_compile):
            ready = tracker_set.ready()
            assert item_id not in {i.id for i in ready}

    def test_ancestors_missing_parent(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """ancestors() when parent references a non-existent item."""
        item_id = tracker_set.add("invariant", "Orphan", tier=Tier.WORKSPACE,
                                  parent="INV-does-not-exist")
        ancestors = tracker_set.ancestors(item_id)
        assert ancestors == []

    def test_resolve_id_ambiguous_within_tier(
        self, tracker_set: TrackerSet, mock_agent_uid: None
    ) -> None:
        """AmbiguousPrefixError within a single tier re-raised."""
        id1 = tracker_set.add("invariant", "First Ambiguous", tier=Tier.WORKSPACE)
        id2 = tracker_set.add("invariant", "Second Ambiguous", tier=Tier.WORKSPACE,
                              not_duplicate_of=[id1])
        # Both are INV- prefixed within workspace → ambiguous
        with pytest.raises(AmbiguousPrefixError):
            tracker_set._resolve_id("INV-")


# ---------------------------------------------------------------------------
# Integration: TrackerSet + real Cache instances
# ---------------------------------------------------------------------------


class TestTrackerSetCacheIntegration:
    def _make_cached_tracker_set(
        self, tracker_root: Path, cache_dir: Path
    ) -> TrackerSet:
        """Create a TrackerSet wired with real Cache instances."""
        from hypergumbo_tracker.cache import Cache

        ts = TrackerSet(tracker_root)
        caches = {}
        for tier_val, store in [
            (Tier.CANONICAL, ts.canonical),
            (Tier.WORKSPACE, ts.workspace),
            (Tier.STEALTH, ts.stealth),
        ]:
            db_path = cache_dir / f"{tier_val.value}.cache.db"
            caches[tier_val] = Cache(store, db_path, tier_val)
        ts.set_caches(caches)
        return ts

    def test_add_populates_cache(
        self, tracker_root: Path, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        ts = self._make_cached_tracker_set(tracker_root, cache_dir)
        item_id = ts.add("invariant", "Cached Add", tier=Tier.WORKSPACE)
        # Check that cache has the item
        cached = ts._caches[Tier.WORKSPACE].get_all()
        assert any(i.id == item_id for i in cached)

    def test_update_updates_cache(
        self, tracker_root: Path, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        ts = self._make_cached_tracker_set(tracker_root, cache_dir)
        item_id = ts.add("invariant", "Cache Update", tier=Tier.WORKSPACE)
        ts.update(item_id, set_fields={"status": "done"})
        cached = ts._caches[Tier.WORKSPACE].get_all()
        item = next(i for i in cached if i.id == item_id)
        assert item.status == "done"

    def test_promote_moves_cache_entry(
        self, tracker_root: Path, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        ts = self._make_cached_tracker_set(tracker_root, cache_dir)
        item_id = ts.add("invariant", "Cache Promote", tier=Tier.WORKSPACE)
        ts.promote(item_id)

        # Should be in canonical cache, not workspace
        ws_items = ts._caches[Tier.WORKSPACE].get_all()
        can_items = ts._caches[Tier.CANONICAL].get_all()
        assert not any(i.id == item_id for i in ws_items)
        assert any(i.id == item_id for i in can_items)

    def test_demote_moves_cache_entry(
        self, tracker_root: Path, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        ts = self._make_cached_tracker_set(tracker_root, cache_dir)
        item_id = ts.add("invariant", "Cache Demote", tier=Tier.CANONICAL)
        ts.demote(item_id)

        can_items = ts._caches[Tier.CANONICAL].get_all()
        ws_items = ts._caches[Tier.WORKSPACE].get_all()
        assert not any(i.id == item_id for i in can_items)
        assert any(i.id == item_id for i in ws_items)

    def test_discuss_updates_cache(
        self, tracker_root: Path, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        ts = self._make_cached_tracker_set(tracker_root, cache_dir)
        item_id = ts.add("invariant", "Cache Discuss", tier=Tier.WORKSPACE)
        ts.discuss(item_id, message="hello from cache test")
        cached = ts._caches[Tier.WORKSPACE].get_all()
        item = next(i for i in cached if i.id == item_id)
        assert len(item.discussion) == 1

    def test_reconcile_updates_caches(
        self, tracker_root: Path, tmp_path: Path, mock_agent_uid: None
    ) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        ts = self._make_cached_tracker_set(tracker_root, cache_dir)
        item_id = ts.add("invariant", "Cache Reconcile", tier=Tier.WORKSPACE)
        ts.promote(item_id)

        # Create conflict
        shutil.copy(
            str(ts.canonical.item_path(item_id)),
            str(ts.workspace.item_path(item_id)),
        )
        ts.reconcile()

        # After reconcile, item should be in canonical cache only
        can_items = ts._caches[Tier.CANONICAL].get_all()
        assert any(i.id == item_id for i in can_items)
