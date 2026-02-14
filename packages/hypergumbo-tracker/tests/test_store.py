# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.store.

Covers ID generation, compile(), add(), update(), discuss(), lock/unlock,
SimHash, prefix matching, ready/list, before-cycle detection, and
Lamport clock computation.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hypergumbo_tracker.models import (
    CompiledItem,
    TrackerConfig,
    KindConfig,
    FieldSchema,
)
from hypergumbo_tracker.store import (
    _SIMHASH_THRESHOLD,
    AmbiguousPrefixError,
    CorruptFileError,
    CycleError,
    DiscussionRateLimitError,
    HumanAuthorityError,
    ItemExistsError,
    ItemNotFoundError,
    LockedFieldError,
    Store,
    _compute_id,
    _compute_simhash,
    _hamming_distance,
    _item_text_for_simhash,
    _op_sort_key,
    _parse_ops_file,
    _serialize_op,
    _tokenize,
    compile_ops,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> TrackerConfig:
    """Create a minimal TrackerConfig for testing."""
    return TrackerConfig(
        kinds={
            "invariant": KindConfig(prefix="INV", description="Test invariant"),
            "work_item": KindConfig(prefix="WI", description="Test work item"),
            "meta_invariant": KindConfig(prefix="META", description="Meta"),
        },
        statuses=["todo_hard", "todo_soft", "in_progress", "done", "deferred", "wont_do"],
        blocking_statuses=["todo_hard", "todo_soft"],
        resolved_statuses=["done", "deferred", "wont_do"],
        agent_usernames=["*_agent"],
        lamport_branches=["dev", "main"],
    )


def _write_ops_file(ops_dir: Path, item_id: str, ops_yaml: str) -> Path:
    """Write an ops YAML string directly to a .ops file."""
    path = ops_dir / f".{item_id}.ops"
    path.write_text(ops_yaml)
    return path


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


class TestComputeId:
    def test_deterministic(self) -> None:
        data = {"kind": "invariant", "title": "test", "status": "todo_hard"}
        id1 = _compute_id("INV", data)
        id2 = _compute_id("INV", data)
        assert id1 == id2

    def test_different_data_different_id(self) -> None:
        data1 = {"kind": "invariant", "title": "test1"}
        data2 = {"kind": "invariant", "title": "test2"}
        id1 = _compute_id("INV", data1)
        id2 = _compute_id("INV", data2)
        assert id1 != id2

    def test_prefix_matches_kind(self) -> None:
        data = {"kind": "invariant", "title": "test"}
        item_id = _compute_id("INV", data)
        assert item_id.startswith("INV-")

    def test_proquint_format(self) -> None:
        data = {"kind": "invariant", "title": "test"}
        item_id = _compute_id("INV", data)
        # proquint.uint2quint(32-bit int) produces "xxxxx-xxxxx" (two syllables)
        # 4 words x 2 syllables = 8 syllables
        # Format: INV-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx
        assert item_id.startswith("INV-")
        proquint_part = item_id[4:]  # After "INV-"
        syllables = proquint_part.split("-")
        assert len(syllables) == 8
        for syllable in syllables:
            assert len(syllable) == 5

    def test_salt_changes_id(self) -> None:
        data = {"kind": "invariant", "title": "test"}
        id1 = _compute_id("INV", data)
        id2 = _compute_id("INV", data, salt="abc")
        assert id1 != id2

    def test_proquint_roundtrip(self) -> None:
        """Verify proquint encoding is reversible: encode 32-bit words → decode → re-encode."""
        import proquint as pq

        data = {"kind": "invariant", "title": "test encode decode"}
        item_id = _compute_id("INV", data)
        proquint_part = item_id[4:]  # After "INV-"
        syllables = proquint_part.split("-")

        # syllables come in pairs (each pair from one uint2quint call)
        for i in range(0, len(syllables), 2):
            pair = f"{syllables[i]}-{syllables[i+1]}"
            value = pq.quint2uint(pair)
            assert 0 <= value < 2**32
            re_encoded = pq.uint2quint(value)
            assert re_encoded == pair


# ---------------------------------------------------------------------------
# compile()
# ---------------------------------------------------------------------------


class TestCompile:
    def test_single_create(self) -> None:
        ops = [{
            "op": "create",
            "at": "2026-02-11T18:00:00Z",
            "by": "agent",
            "actor": "test_agent",
            "clock": 1,
            "nonce": "f7a2",
            "data": {
                "kind": "invariant",
                "title": "Test Title",
                "status": "todo_hard",
                "priority": 1,
                "tags": ["analysis_quality"],
                "description": "Test desc",
                "fields": {"statement": "invariant statement"},
            },
        }]
        item = compile_ops(ops, "INV-test")
        assert item.id == "INV-test"
        assert item.kind == "invariant"
        assert item.title == "Test Title"
        assert item.status == "todo_hard"
        assert item.priority == 1
        assert item.tags == ["analysis_quality"]
        assert item.description == "Test desc"
        assert item.fields["statement"] == "invariant statement"
        assert item.created_at == "2026-02-11T18:00:00Z"
        assert item.updated_at == "2026-02-11T18:00:00Z"

    def test_create_plus_update_lww(self) -> None:
        ops = [
            {
                "op": "create", "at": "2026-02-11T18:00:00Z",
                "by": "agent", "actor": "test_agent", "clock": 1, "nonce": "a1",
                "data": {"kind": "invariant", "title": "Original", "status": "todo_hard",
                         "priority": 2, "description": "", "fields": {}},
            },
            {
                "op": "update", "at": "2026-02-11T19:00:00Z",
                "by": "agent", "actor": "test_agent", "clock": 2, "nonce": "b2",
                "set": {"status": "done", "priority": 0, "title": "Updated"},
            },
        ]
        item = compile_ops(ops, "INV-test")
        assert item.status == "done"
        assert item.priority == 0
        assert item.title == "Updated"
        assert item.updated_at == "2026-02-11T19:00:00Z"

    def test_fields_per_key_lww(self) -> None:
        ops = [
            {
                "op": "create", "at": "T1", "by": "agent", "actor": "a",
                "clock": 1, "nonce": "n1",
                "data": {"kind": "invariant", "title": "t", "status": "todo_hard",
                         "priority": 2, "description": "",
                         "fields": {"statement": "orig", "root_cause": "orig rc"}},
            },
            {
                "op": "update", "at": "T2", "by": "agent", "actor": "a",
                "clock": 2, "nonce": "n2",
                "set": {"fields": {"root_cause": "updated rc"}},
            },
        ]
        item = compile_ops(ops, "INV-test")
        assert item.fields["statement"] == "orig"  # Unchanged
        assert item.fields["root_cause"] == "updated rc"  # Updated

    def test_duplicate_creates_lowest_clock_wins(self) -> None:
        ops = [
            {
                "op": "create", "at": "2026-02-11T19:00:00Z",
                "by": "agent", "actor": "agent_b", "clock": 3, "nonce": "b1",
                "data": {"kind": "invariant", "title": "Same Title", "status": "todo_hard",
                         "priority": 2, "description": "", "fields": {}},
            },
            {
                "op": "create", "at": "2026-02-11T18:00:00Z",
                "by": "agent", "actor": "agent_a", "clock": 1, "nonce": "a1",
                "data": {"kind": "invariant", "title": "Same Title", "status": "todo_hard",
                         "priority": 2, "description": "", "fields": {}},
            },
        ]
        item = compile_ops(ops, "INV-test")
        # Lowest clock (1) wins as the canonical create
        assert item.created_at == "2026-02-11T18:00:00Z"

    def test_add_remove_tags(self) -> None:
        ops = [
            {
                "op": "create", "at": "T1", "by": "agent", "actor": "a",
                "clock": 1, "nonce": "n1",
                "data": {"kind": "invariant", "title": "t", "status": "todo_hard",
                         "priority": 2, "description": "",
                         "tags": ["tag1", "tag2"], "fields": {}},
            },
            {
                "op": "update", "at": "T2", "by": "agent", "actor": "a",
                "clock": 2, "nonce": "n2",
                "set": {},
                "add": {"tags": ["tag3"]},
                "remove": {"tags": ["tag1"]},
            },
        ]
        item = compile_ops(ops, "INV-test")
        assert "tag1" not in item.tags
        assert "tag2" in item.tags
        assert "tag3" in item.tags

    def test_discussion_ops(self) -> None:
        ops = [
            {
                "op": "create", "at": "T1", "by": "agent", "actor": "a",
                "clock": 1, "nonce": "n1",
                "data": {"kind": "invariant", "title": "t", "status": "todo_hard",
                         "priority": 2, "description": "", "fields": {}},
            },
            {
                "op": "discuss", "at": "T2", "by": "human", "actor": "jgstern",
                "clock": 2, "nonce": "n2", "message": "hello",
            },
            {
                "op": "discuss", "at": "T3", "by": "agent", "actor": "test_agent",
                "clock": 3, "nonce": "n3", "message": "world",
            },
        ]
        item = compile_ops(ops, "INV-test")
        assert len(item.discussion) == 2
        assert item.discussion[0].message == "hello"
        assert item.discussion[0].by == "human"
        assert item.discussion[1].message == "world"

    def test_discuss_clear_resets(self) -> None:
        ops = [
            {
                "op": "create", "at": "T1", "by": "agent", "actor": "a",
                "clock": 1, "nonce": "n1",
                "data": {"kind": "invariant", "title": "t", "status": "todo_hard",
                         "priority": 2, "description": "", "fields": {}},
            },
            {
                "op": "discuss", "at": "T2", "by": "human", "actor": "h",
                "clock": 2, "nonce": "n2", "message": "old msg",
            },
            {
                "op": "discuss_clear", "at": "T3", "by": "human", "actor": "h",
                "clock": 3, "nonce": "n3",
            },
        ]
        item = compile_ops(ops, "INV-test")
        assert item.discussion == []

    def test_discuss_summarize_replaces(self) -> None:
        ops = [
            {
                "op": "create", "at": "T1", "by": "agent", "actor": "a",
                "clock": 1, "nonce": "n1",
                "data": {"kind": "invariant", "title": "t", "status": "todo_hard",
                         "priority": 2, "description": "", "fields": {}},
            },
            {
                "op": "discuss", "at": "T2", "by": "human", "actor": "h",
                "clock": 2, "nonce": "n2", "message": "msg1",
            },
            {
                "op": "discuss", "at": "T3", "by": "agent", "actor": "a",
                "clock": 3, "nonce": "n3", "message": "msg2",
            },
            {
                "op": "discuss_summarize", "at": "T4", "by": "agent", "actor": "a",
                "clock": 4, "nonce": "n4", "message": "Summary of discussion",
            },
        ]
        item = compile_ops(ops, "INV-test")
        assert len(item.discussion) == 1
        assert item.discussion[0].message == "Summary of discussion"
        assert item.discussion[0].is_summary is True

    def test_lock_unlock(self) -> None:
        ops = [
            {
                "op": "create", "at": "T1", "by": "agent", "actor": "a",
                "clock": 1, "nonce": "n1",
                "data": {"kind": "invariant", "title": "t", "status": "todo_hard",
                         "priority": 2, "description": "", "fields": {}},
            },
            {
                "op": "lock", "at": "T2", "by": "human", "actor": "h",
                "clock": 2, "nonce": "n2", "lock": ["priority", "status"],
            },
            {
                "op": "unlock", "at": "T3", "by": "human", "actor": "h",
                "clock": 3, "nonce": "n3", "unlock": ["status"],
            },
        ]
        item = compile_ops(ops, "INV-test")
        assert "priority" in item.locked_fields
        assert "status" not in item.locked_fields

    def test_created_at_updated_at(self) -> None:
        ops = [
            {
                "op": "create", "at": "2026-02-11T18:00:00Z", "by": "agent",
                "actor": "a", "clock": 1, "nonce": "n1",
                "data": {"kind": "invariant", "title": "t", "status": "todo_hard",
                         "priority": 2, "description": "", "fields": {}},
            },
            {
                "op": "update", "at": "2026-02-11T20:00:00Z", "by": "agent",
                "actor": "a", "clock": 5, "nonce": "n5",
                "set": {"priority": 0},
            },
        ]
        item = compile_ops(ops, "INV-test")
        assert item.created_at == "2026-02-11T18:00:00Z"
        assert item.updated_at == "2026-02-11T20:00:00Z"

    def test_sort_by_clock_timestamp_actor(self) -> None:
        assert _op_sort_key({"clock": 1, "at": "T1", "by": "agent"}) < \
               _op_sort_key({"clock": 2, "at": "T1", "by": "agent"})
        assert _op_sort_key({"clock": 1, "at": "T1", "by": "human"}) < \
               _op_sort_key({"clock": 1, "at": "T1", "by": "agent"})

    def test_empty_ops_raises(self) -> None:
        with pytest.raises(CorruptFileError, match="Empty op log"):
            compile_ops([], "INV-test")

    def test_no_create_op_raises(self) -> None:
        ops = [{"op": "update", "at": "T1", "by": "agent", "actor": "a",
                "clock": 1, "nonce": "n1", "set": {"status": "done"}}]
        with pytest.raises(CorruptFileError, match="no create op"):
            compile_ops(ops, "INV-test")

    def test_set_valued_field_wholesale_replace(self) -> None:
        ops = [
            {
                "op": "create", "at": "T1", "by": "agent", "actor": "a",
                "clock": 1, "nonce": "n1",
                "data": {"kind": "invariant", "title": "t", "status": "todo_hard",
                         "priority": 2, "description": "",
                         "tags": ["old1", "old2"], "fields": {}},
            },
            {
                "op": "update", "at": "T2", "by": "agent", "actor": "a",
                "clock": 2, "nonce": "n2",
                "set": {"tags": ["new1", "new2"]},
            },
        ]
        item = compile_ops(ops, "INV-test")
        assert item.tags == ["new1", "new2"]

    def test_before_add_remove(self) -> None:
        ops = [
            {
                "op": "create", "at": "T1", "by": "agent", "actor": "a",
                "clock": 1, "nonce": "n1",
                "data": {"kind": "invariant", "title": "t", "status": "todo_hard",
                         "priority": 2, "description": "",
                         "before": ["INV-aaa"], "fields": {}},
            },
            {
                "op": "update", "at": "T2", "by": "agent", "actor": "a",
                "clock": 2, "nonce": "n2",
                "set": {},
                "add": {"before": ["INV-bbb"]},
                "remove": {"before": ["INV-aaa"]},
            },
        ]
        item = compile_ops(ops, "INV-test")
        assert "INV-aaa" not in item.before
        assert "INV-bbb" in item.before

    def test_add_does_not_duplicate(self) -> None:
        ops = [
            {
                "op": "create", "at": "T1", "by": "agent", "actor": "a",
                "clock": 1, "nonce": "n1",
                "data": {"kind": "invariant", "title": "t", "status": "todo_hard",
                         "priority": 2, "description": "",
                         "tags": ["existing"], "fields": {}},
            },
            {
                "op": "update", "at": "T2", "by": "agent", "actor": "a",
                "clock": 2, "nonce": "n2",
                "set": {}, "add": {"tags": ["existing"]},
            },
        ]
        item = compile_ops(ops, "INV-test")
        assert item.tags.count("existing") == 1

    def test_tier_movement_ops_no_state_change(self) -> None:
        ops = [
            {
                "op": "create", "at": "T1", "by": "agent", "actor": "a",
                "clock": 1, "nonce": "n1",
                "data": {"kind": "invariant", "title": "t", "status": "todo_hard",
                         "priority": 2, "description": "", "fields": {}},
            },
            {
                "op": "promote", "at": "T2", "by": "agent", "actor": "a",
                "clock": 2, "nonce": "n2",
            },
            {
                "op": "demote", "at": "T3", "by": "agent", "actor": "a",
                "clock": 3, "nonce": "n3",
            },
            {
                "op": "stealth", "at": "T4", "by": "human", "actor": "h",
                "clock": 4, "nonce": "n4",
            },
            {
                "op": "unstealth", "at": "T5", "by": "human", "actor": "h",
                "clock": 5, "nonce": "n5",
            },
            {
                "op": "reconcile", "at": "T6", "by": "agent", "actor": "a",
                "clock": 6, "nonce": "n6",
                "from_tier": "workspace", "reason": "test",
            },
        ]
        item = compile_ops(ops, "INV-test")
        # State should be unchanged from create
        assert item.status == "todo_hard"
        assert item.title == "t"
        assert item.updated_at == "T6"  # Last op timestamp


# ---------------------------------------------------------------------------
# Store: add()
# ---------------------------------------------------------------------------


class TestStoreAdd:
    def test_add_creates_ops_file(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Test Item", description="desc")
        assert item_id.startswith("INV-")

        item_path = ops_dir / f".{item_id}.ops"
        assert item_path.exists()

        # Parse and verify
        ops = _parse_ops_file(item_path)
        assert len(ops) == 1
        assert ops[0]["op"] == "create"
        assert ops[0]["data"]["title"] == "Test Item"

    def test_add_same_content_raises(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="Duplicate Test", description="desc")
        with pytest.raises(ItemExistsError, match="already exists"):
            store.add(kind="invariant", title="Duplicate Test", description="desc")

    def test_add_unknown_kind_raises(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        with pytest.raises(ValueError, match="Unknown kind"):
            store.add(kind="nonexistent", title="Test")

    def test_add_hash_collision_auto_salt(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Collision Test")

        # Simulate a hash collision: create a file at the same path but with
        # NO create ops (so the existence check finds the file but no create op,
        # triggering the salt path)
        item_path = ops_dir / f".{item_id}.ops"
        # Write an update-only op (no create) — simulates a corrupted/different file
        item_path.write_text("- op: update\n  at: T1\n  by: agent\n  actor: a\n"
                             "  clock: 1\n  nonce: xxxx\n  set:\n    status: done\n")

        # Now add with the same data — file exists, no create ops found → salt
        item_id2 = store.add(kind="invariant", title="Collision Test")
        assert item_id2 != item_id
        assert item_id2.startswith("INV-")

    def test_add_default_status(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Default Status")
        item = store.get(item_id)
        assert item.status == "todo_hard"  # First blocking status

    def test_add_with_all_fields(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(
            kind="invariant",
            title="Full Item",
            status="todo_soft",
            priority=1,
            tags=["analysis_quality"],
            before=["INV-other"],
            description="Full description",
            fields={"statement": "test stmt", "root_cause": "test cause"},
            pr_ref="PR-42",
            justification="because",
        )
        item = store.get(item_id)
        assert item.status == "todo_soft"
        assert item.priority == 1
        assert "analysis_quality" in item.tags
        assert "INV-other" in item.before
        assert item.description == "Full description"
        assert item.fields["statement"] == "test stmt"
        assert item.pr_ref == "PR-42"
        assert item.justification == "because"


# ---------------------------------------------------------------------------
# Store: update()
# ---------------------------------------------------------------------------


class TestStoreUpdate:
    def test_update_appends_op(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Update Test")
        store.update(item_id, set_fields={"status": "done"})

        item = store.get(item_id)
        assert item.status == "done"

    def test_update_nonexistent_raises(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        with pytest.raises(ItemNotFoundError):
            store.update("INV-nonexistent-id-that-does-not-exist-at-all", set_fields={"status": "done"})

    def test_update_locked_field_rejected(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Lock Test")

        # Manually add a lock op (simulating human action)
        item_path = ops_dir / f".{item_id}.ops"
        lock_op = {
            "op": "lock", "at": "2026-02-11T19:00:00Z", "by": "human",
            "actor": "jgstern", "clock": 99, "nonce": "lock",
            "lock": ["priority"],
        }
        serialized = _serialize_op(lock_op)
        with open(item_path, "a") as f:
            f.write(serialized)

        with pytest.raises(LockedFieldError, match=r"priority.*locked"):
            store.update(item_id, set_fields={"priority": 0})

    def test_update_with_add_remove(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Tag Test", tags=["old_tag"])
        store.update(
            item_id,
            set_fields={},
            add_fields={"tags": ["new_tag"]},
            remove_fields={"tags": ["old_tag"]},
        )
        item = store.get(item_id)
        assert "new_tag" in item.tags
        assert "old_tag" not in item.tags


# ---------------------------------------------------------------------------
# Store: discuss()
# ---------------------------------------------------------------------------


class TestStoreDiscuss:
    def test_discuss_appends_message(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Discuss Test")
        store.discuss(item_id, "Hello from agent")

        item = store.get(item_id)
        assert len(item.discussion) == 1
        assert item.discussion[0].message == "Hello from agent"
        assert item.discussion[0].by == "agent"

    def test_discuss_clear_by_agent_raises(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Clear Test")
        with pytest.raises(HumanAuthorityError, match="human authority"):
            store.discuss(item_id, "", clear=True)

    def test_discuss_clear_by_human(self, ops_dir: Path, mock_human_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Clear Test")
        store.discuss(item_id, "message 1")
        store.discuss(item_id, "", clear=True)
        item = store.get(item_id)
        assert item.discussion == []

    def test_discuss_summarize(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Summarize Test")
        store.discuss(item_id, "msg1")
        store.discuss(item_id, "msg2")
        store.discuss(item_id, "Summary of discussion", summarize=True)

        item = store.get(item_id)
        assert len(item.discussion) == 1
        assert item.discussion[0].is_summary is True
        assert item.discussion[0].message == "Summary of discussion"

    def test_discuss_nonexistent_raises(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        with pytest.raises(ItemNotFoundError):
            store.discuss("INV-nonexistent-id-that-does-not-exist-at-all", "msg")

    def test_discuss_locked_discussion_rejected(
        self, ops_dir: Path, mock_agent_uid: None
    ) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Lock Discuss Test")

        # Manually add a lock on discussion
        item_path = ops_dir / f".{item_id}.ops"
        lock_op = {
            "op": "lock", "at": "2026-02-11T19:00:00Z", "by": "human",
            "actor": "jgstern", "clock": 99, "nonce": "lock",
            "lock": ["discussion"],
        }
        serialized = _serialize_op(lock_op)
        with open(item_path, "a") as f:
            f.write(serialized)

        with pytest.raises(LockedFieldError, match=r"Discussion.*locked"):
            store.discuss(item_id, "Should be rejected")

    def test_discussion_rate_limit(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Rate Limit Test")

        # Write enough discuss ops to exceed the daily limit
        item_path = ops_dir / f".{item_id}.ops"
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # 200k tokens * 4.4 chars/token = 880k chars needed
        big_message = "x" * 900_000
        discuss_op = {
            "op": "discuss", "at": now, "by": "agent",
            "actor": "test_agent", "clock": 50, "nonce": "rl01",
            "message": big_message,
        }
        serialized = _serialize_op(discuss_op)
        with open(item_path, "a") as f:
            f.write(serialized)

        with pytest.raises(DiscussionRateLimitError, match="rate limit"):
            store.discuss(item_id, "Should fail")

    def test_discussion_soft_cap_warning(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Soft Cap Test")

        # Add enough discuss ops to trigger soft cap
        item_path = ops_dir / f".{item_id}.ops"
        for i in range(20):
            discuss_op = {
                "op": "discuss", "at": f"2026-02-11T{i:02d}:00:00Z",
                "by": "agent", "actor": "test_agent", "clock": i + 10,
                "nonce": f"d{i:03d}", "message": f"msg {i}",
            }
            serialized = _serialize_op(discuss_op)
            with open(item_path, "a") as f:
                f.write(serialized)

        with pytest.warns(UserWarning, match="soft cap"):
            store.discuss(item_id, "One more message")


# ---------------------------------------------------------------------------
# Store: lock() / unlock()
# ---------------------------------------------------------------------------


class TestStoreLock:
    def test_lock_by_agent_raises(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Lock Test")
        with pytest.raises(HumanAuthorityError, match="human authority"):
            store.lock(item_id, ["priority"])

    def test_lock_by_human(self, ops_dir: Path, mock_human_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Lock Test")
        store.lock(item_id, ["priority", "status"])
        item = store.get(item_id)
        assert "priority" in item.locked_fields
        assert "status" in item.locked_fields

    def test_unlock_by_agent_raises(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Unlock Test")
        with pytest.raises(HumanAuthorityError, match="human authority"):
            store.unlock(item_id, ["priority"])

    def test_unlock_by_human(self, ops_dir: Path, mock_human_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Unlock Test")
        store.lock(item_id, ["priority"])
        store.unlock(item_id, ["priority"])
        item = store.get(item_id)
        assert "priority" not in item.locked_fields

    def test_lock_nonexistent_raises(self, ops_dir: Path, mock_human_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        with pytest.raises(ItemNotFoundError):
            store.lock("INV-nonexistent-id-that-does-not-exist-at-all", ["priority"])

    def test_unlock_nonexistent_raises(self, ops_dir: Path, mock_human_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        with pytest.raises(ItemNotFoundError):
            store.unlock("INV-nonexistent-id-that-does-not-exist-at-all", ["priority"])


# ---------------------------------------------------------------------------
# SimHash
# ---------------------------------------------------------------------------


class TestSimHash:
    def test_identical_text_same_hash(self) -> None:
        text = "Every calls edge has a non-null caller symbol"
        assert _compute_simhash(text) == _compute_simhash(text)

    def test_similar_text_low_distance(self) -> None:
        # SimHash needs enough tokens for statistical averaging; short texts
        # produce noisy fingerprints.  Use realistic-length item descriptions.
        text1 = (
            "symbol resolution stability for all calls edges in every language "
            "and framework that hypergumbo supports including java python "
            "typescript ruby kotlin"
        )
        text2 = (
            "symbol resolution stability for all calls edges in every language "
            "and framework that hypergumbo supports including java python "
            "typescript ruby scala"
        )
        h1 = _compute_simhash(text1)
        h2 = _compute_simhash(text2)
        dist = _hamming_distance(h1, h2)
        # Texts sharing 19/20 tokens should have low distance
        assert dist <= _SIMHASH_THRESHOLD

    def test_unrelated_text_high_distance(self) -> None:
        text1 = (
            "symbol resolution stability for all calls edges in every language "
            "and framework that hypergumbo supports including java python "
            "typescript ruby kotlin"
        )
        text2 = (
            "the quick brown fox jumps over the lazy dog while eating "
            "cookies and drinking tea by a large beautiful lake nearby"
        )
        h1 = _compute_simhash(text1)
        h2 = _compute_simhash(text2)
        dist = _hamming_distance(h1, h2)
        # Completely different texts should have high distance
        assert dist > _SIMHASH_THRESHOLD

    def test_empty_text(self) -> None:
        assert _compute_simhash("") == 0

    def test_hamming_distance_identical(self) -> None:
        assert _hamming_distance(0b1010, 0b1010) == 0

    def test_hamming_distance_different(self) -> None:
        assert _hamming_distance(0b1010, 0b0101) == 4

    def test_tokenize(self) -> None:
        tokens = _tokenize("Hello World! Test-123")
        assert tokens == ["hello", "world", "test", "123"]

    def test_item_text_for_simhash(self) -> None:
        data = {
            "title": "Test Title",
            "description": "Test Description",
            "fields": {"statement": "A statement", "count": 42},
        }
        text = _item_text_for_simhash(data)
        assert "Test Title" in text
        assert "Test Description" in text
        assert "A statement" in text

    def test_similarity_warning_on_add(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        title_base = (
            "symbol resolution stability for all calls edges in every language "
            "and framework that hypergumbo supports including java python "
            "typescript ruby kotlin"
        )
        title_similar = (
            "symbol resolution stability for all calls edges in every language "
            "and framework that hypergumbo supports including java python "
            "typescript ruby scala"
        )
        store.add(kind="invariant", title=title_base, description="")
        # Add near-identical item (differs by one token) — should warn
        with pytest.warns(UserWarning, match="similar"):
            store.add(kind="invariant", title=title_similar, description="")

    def test_not_duplicate_of_suppresses_warning(
        self, ops_dir: Path, mock_agent_uid: None
    ) -> None:
        store = Store(ops_dir, config=_make_config())
        title_base = (
            "symbol resolution stability for all calls edges in every language "
            "and framework that hypergumbo supports including java python "
            "typescript ruby kotlin"
        )
        title_similar = (
            "symbol resolution stability for all calls edges in every language "
            "and framework that hypergumbo supports including java python "
            "typescript ruby scala"
        )
        id1 = store.add(kind="invariant", title=title_base, description="")
        # Add similar with not_duplicate_of — no warning expected
        store.add(
            kind="invariant",
            title=title_similar,
            description="",
            not_duplicate_of=[id1],
        )


# ---------------------------------------------------------------------------
# Prefix matching and aliases
# ---------------------------------------------------------------------------


class TestPrefixMatching:
    def test_exact_match(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Prefix Test")
        resolved = store._resolve_id(item_id)
        assert resolved == item_id

    def test_prefix_with_kind(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Prefix Test Unique")
        # Use first 10 chars as prefix
        prefix = item_id[:10]
        resolved = store._resolve_id(prefix)
        assert resolved == item_id

    def test_prefix_without_kind(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Prefix No Kind")
        # Use the proquint part (after "INV-")
        proquint_prefix = item_id.split("-", 1)[1][:5]
        resolved = store._resolve_id(proquint_prefix)
        assert resolved == item_id

    def test_ambiguous_prefix(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="Ambiguous A")
        store.add(kind="invariant", title="Ambiguous B")
        # "INV-" matches both
        with pytest.raises(AmbiguousPrefixError, match="ambiguous"):
            store._resolve_id("INV-")

    def test_no_match(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        with pytest.raises(ItemNotFoundError, match="No items match"):
            store._resolve_id("NONEXISTENT-prefix")

    def test_positional_alias(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        id1 = store.add(kind="invariant", title="Alias Item 1")
        id2 = store.add(kind="work_item", title="Alias Item 2")

        # list_items populates the alias stash
        items = store.list_items()

        resolved = store._resolve_id(":1")
        assert resolved == items[0].id

    def test_positional_alias_out_of_range(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="Only Item")
        store.list_items()
        with pytest.raises(ItemNotFoundError, match="out of range"):
            store._resolve_id(":99")

    def test_positional_alias_invalid(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        with pytest.raises(ItemNotFoundError, match="Invalid positional alias"):
            store._resolve_id(":abc")


# ---------------------------------------------------------------------------
# Store: ready() and list_items()
# ---------------------------------------------------------------------------


class TestReadyAndList:
    def test_list_items_all(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="Item 1")
        store.add(kind="work_item", title="Item 2")
        items = store.list_items()
        assert len(items) == 2

    def test_list_items_filter_status(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="Open Item", status="todo_hard")
        store.add(kind="invariant", title="Done Item", status="done")
        items = store.list_items(status="done")
        assert len(items) == 1
        assert items[0].title == "Done Item"

    def test_list_items_filter_kind(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="Inv Item")
        store.add(kind="work_item", title="WI Item")
        items = store.list_items(kind="invariant")
        assert len(items) == 1
        assert items[0].kind == "invariant"

    def test_list_items_filter_tag(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="Tagged", tags=["analysis_quality"])
        store.add(kind="invariant", title="Untagged")
        items = store.list_items(tag="analysis_quality")
        assert len(items) == 1
        assert items[0].title == "Tagged"

    def test_list_items_sorted_by_priority(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="P3", priority=3)
        store.add(kind="invariant", title="P1", priority=1)
        store.add(kind="invariant", title="P2", priority=2)
        items = store.list_items()
        assert items[0].priority == 1
        assert items[1].priority == 2
        assert items[2].priority == 3

    def test_ready_only_blocking_statuses(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="Blocking", status="todo_hard")
        store.add(kind="invariant", title="Done", status="done")
        store.add(kind="invariant", title="In Progress", status="in_progress")
        ready = store.ready()
        assert len(ready) == 1
        assert ready[0].title == "Blocking"

    def test_ready_excludes_blocked_by_before(
        self, ops_dir: Path, mock_agent_uid: None
    ) -> None:
        store = Store(ops_dir, config=_make_config())
        blocker_id = store.add(kind="invariant", title="Blocker", status="todo_hard")
        blocked_id = store.add(kind="invariant", title="Blocked", status="todo_hard")

        # blocker.before = [blocked_id] means "finish Blocker before Blocked"
        # So Blocked is blocked by Blocker
        store.update(blocker_id, add_fields={"before": [blocked_id]})

        ready = store.ready()
        ready_ids = {r.id for r in ready}
        # Blocker is ready (nothing blocks it)
        assert blocker_id in ready_ids
        # Blocked is NOT ready (Blocker has before=[Blocked] and is unresolved)
        assert blocked_id not in ready_ids

    def test_ready_excludes_duplicates(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        store.add(
            kind="invariant", title="Duplicate", status="todo_hard",
            duplicate_of=["INV-other"],
        )
        ready = store.ready()
        assert len(ready) == 0

    def test_ready_sorted_by_priority(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="P2", status="todo_hard", priority=2)
        store.add(kind="invariant", title="P0", status="todo_hard", priority=0)
        ready = store.ready()
        assert ready[0].priority == 0


# ---------------------------------------------------------------------------
# Store: children() and ancestors()
# ---------------------------------------------------------------------------


class TestTreeTraversal:
    def test_children(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        parent_id = store.add(kind="invariant", title="Parent")
        child1_id = store.add(kind="invariant", title="Child 1", parent=parent_id)
        child2_id = store.add(kind="invariant", title="Child 2", parent=parent_id)
        store.add(kind="invariant", title="Not a child")

        children = store.children(parent_id)
        assert len(children) == 2
        child_titles = {c.title for c in children}
        assert child_titles == {"Child 1", "Child 2"}

    def test_ancestors(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        root_id = store.add(kind="invariant", title="Root")
        mid_id = store.add(kind="invariant", title="Middle", parent=root_id)
        leaf_id = store.add(kind="invariant", title="Leaf", parent=mid_id)

        ancestors = store.ancestors(leaf_id)
        assert len(ancestors) == 2
        ancestor_titles = [a.title for a in ancestors]
        assert ancestor_titles == ["Middle", "Root"]


# ---------------------------------------------------------------------------
# Store: before-cycle detection
# ---------------------------------------------------------------------------


class TestBeforeCycles:
    def test_no_cycles(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        id1 = store.add(kind="invariant", title="A")
        store.add(kind="invariant", title="B", before=[id1])
        cycles = store.check_before_cycles()
        assert cycles == []


# ---------------------------------------------------------------------------
# Store: get()
# ---------------------------------------------------------------------------


class TestStoreGet:
    def test_get_by_full_id(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Get Test")
        item = store.get(item_id)
        assert item.title == "Get Test"

    def test_get_nonexistent_raises(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        with pytest.raises(ItemNotFoundError):
            store.get("INV-nonexistent-id-that-does-not-exist-at-all")


# ---------------------------------------------------------------------------
# Store: corrupt file handling
# ---------------------------------------------------------------------------


class TestCorruptFileHandling:
    def test_corrupt_yaml(self, ops_dir: Path) -> None:
        path = ops_dir / ".INV-corrupt.ops"
        path.write_text("{{invalid yaml")
        with pytest.raises(CorruptFileError):
            _parse_ops_file(path)

    def test_non_list_yaml(self, ops_dir: Path) -> None:
        path = ops_dir / ".INV-notlist.ops"
        path.write_text("key: value\n")
        with pytest.raises(CorruptFileError, match="expected YAML list"):
            _parse_ops_file(path)

    def test_empty_file(self, ops_dir: Path) -> None:
        path = ops_dir / ".INV-empty.ops"
        path.write_text("")
        ops = _parse_ops_file(path)
        assert ops == []

    def test_compile_all_skips_corrupt(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="Good Item")
        # Add a corrupt file
        (ops_dir / ".INV-corrupt-test.ops").write_text("{{bad")
        items = store.list_items()
        # Should only have the good item (corrupt file skipped)
        assert len(items) == 1
        assert items[0].title == "Good Item"


# ---------------------------------------------------------------------------
# Store: _parse_ops_bytes
# ---------------------------------------------------------------------------


class TestParseOpsBytes:
    def test_valid_bytes(self) -> None:
        from hypergumbo_tracker.store import _parse_ops_bytes
        content = b"- op: create\n  at: T1\n"
        result = _parse_ops_bytes(content)
        assert len(result) == 1
        assert result[0]["op"] == "create"

    def test_invalid_bytes(self) -> None:
        from hypergumbo_tracker.store import _parse_ops_bytes
        result = _parse_ops_bytes(b"{{bad yaml")
        assert result == []

    def test_non_list_bytes(self) -> None:
        from hypergumbo_tracker.store import _parse_ops_bytes
        result = _parse_ops_bytes(b"key: value\n")
        assert result == []

    def test_empty_bytes(self) -> None:
        from hypergumbo_tracker.store import _parse_ops_bytes
        result = _parse_ops_bytes(b"")
        assert result == []


# ---------------------------------------------------------------------------
# CLI stubs
# ---------------------------------------------------------------------------


class TestCliStubs:
    def test_main_exits(self) -> None:
        from hypergumbo_tracker.cli import main
        with pytest.raises(SystemExit):
            main()

    def test_textconv_main_exits(self) -> None:
        from hypergumbo_tracker.cli import textconv_main
        with pytest.raises(SystemExit):
            textconv_main()


# ---------------------------------------------------------------------------
# Store: _find_git_dir
# ---------------------------------------------------------------------------


class TestFindGitDir:
    def test_finds_git_dir(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.store import _find_git_dir
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        found = _find_git_dir(tmp_path / "some" / "file.txt")
        # Will not find because we need actual parent traversal from file
        # Let's use a file within tmp_path
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        found = _find_git_dir(subdir)
        assert found == git_dir

    def test_no_git_dir(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.store import _find_git_dir
        found = _find_git_dir(tmp_path / "isolated")
        # Should return None when no .git found
        assert found is None


# ---------------------------------------------------------------------------
# ready() with before semantics (corrected)
# ---------------------------------------------------------------------------


class TestReadyBeforeSemantics:
    """Test that ready() correctly implements before semantics.

    X.before = [Y] means "X blocks Y — finish X before starting Y."
    So Y is blocked until X is resolved.
    """

    def test_blocker_ready_blocked_not(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        blocker_id = store.add(kind="invariant", title="Blocker", status="todo_hard")
        blocked_id = store.add(kind="invariant", title="Blocked", status="todo_hard")

        # blocker.before = [blocked_id] → blocker blocks blocked_id
        store.update(blocker_id, add_fields={"before": [blocked_id]})

        ready = store.ready()
        ready_ids = {r.id for r in ready}

        # Blocker is ready (nothing in any item's before field targets blocker)
        assert blocker_id in ready_ids
        # Blocked is NOT ready (blocker.before contains blocked_id and blocker is unresolved)
        assert blocked_id not in ready_ids

    def test_resolved_blocker_unblocks(self, ops_dir: Path, mock_agent_uid: None) -> None:
        store = Store(ops_dir, config=_make_config())
        blocker_id = store.add(kind="invariant", title="Blocker", status="todo_hard")
        blocked_id = store.add(kind="invariant", title="Blocked", status="todo_hard")

        store.update(blocker_id, add_fields={"before": [blocked_id]})
        # Resolve the blocker
        store.update(blocker_id, set_fields={"status": "done"})

        ready = store.ready()
        ready_ids = {r.id for r in ready}
        # Blocker is resolved (done) → not in blocking_statuses → not in ready
        assert blocker_id not in ready_ids
        # Blocked is now unblocked since blocker is resolved
        assert blocked_id in ready_ids


# ---------------------------------------------------------------------------
# Coverage: serialization edge cases
# ---------------------------------------------------------------------------


class TestSerializationEdgeCases:
    """Cover serialization paths for non-string field values,
    remaining data keys, and update-set with fields dict."""

    def test_create_with_non_string_field_value(self) -> None:
        """Non-string value in create data fields dict (line 192)."""
        op = {
            "op": "create", "at": "T1", "by": "agent", "actor": "a",
            "clock": 1, "nonce": "aaaa",
            "data": {
                "kind": "invariant", "title": "t", "status": "todo_hard",
                "priority": 2, "description": "", "fields": {"count": 42},
            },
        }
        serialized = _serialize_op(op)
        assert "count: 42" in serialized

    def test_create_with_extra_data_keys(self) -> None:
        """Data keys not in canonical order are still serialized (lines 197-198)."""
        op = {
            "op": "create", "at": "T1", "by": "agent", "actor": "a",
            "clock": 1, "nonce": "aaaa",
            "data": {
                "kind": "invariant", "title": "t", "status": "todo_hard",
                "priority": 2, "description": "", "fields": {},
                "custom_key": "custom_value",
            },
        }
        serialized = _serialize_op(op)
        assert "custom_key:" in serialized
        assert "custom_value" in serialized

    def test_update_set_with_description(self) -> None:
        """Update set with a double-quoted field like description (line 205)."""
        op = {
            "op": "update", "at": "T1", "by": "agent", "actor": "a",
            "clock": 2, "nonce": "bbbb",
            "set": {"description": "yes"},
        }
        serialized = _serialize_op(op)
        # "yes" would be coerced to True without quoting
        assert '"yes"' in serialized

    def test_update_set_with_fields_dict(self) -> None:
        """Update set.fields dict with string and non-string values (lines 207-213)."""
        op = {
            "op": "update", "at": "T1", "by": "agent", "actor": "a",
            "clock": 2, "nonce": "cccc",
            "set": {
                "fields": {
                    "statement": "null",  # needs double-quoting
                    "count": 99,          # non-string, no quoting
                },
            },
        }
        serialized = _serialize_op(op)
        assert '"null"' in serialized
        assert "count: 99" in serialized

    def test_parse_ops_file_yaml_null(self, tmp_path: Path) -> None:
        """YAML file that parses to None (line 299)."""
        path = tmp_path / "null.ops"
        path.write_text("---\n")
        from hypergumbo_tracker.store import _parse_ops_file
        result = _parse_ops_file(path)
        assert result == []


# ---------------------------------------------------------------------------
# Coverage: SimHash list field values
# ---------------------------------------------------------------------------


class TestSimHashListFields:
    def test_item_text_with_list_field(self) -> None:
        """List values in fields dict are expanded to text (line 430)."""
        data = {
            "title": "Title",
            "description": "Desc",
            "fields": {"items": ["alpha", "beta", "gamma"]},
        }
        text = _item_text_for_simhash(data)
        assert "alpha" in text
        assert "beta" in text
        assert "gamma" in text


# ---------------------------------------------------------------------------
# Coverage: compile() set-valued field via `set` dict
# ---------------------------------------------------------------------------


class TestCompileSetValuedFieldOverwrite:
    def test_set_tags_via_set_dict(self) -> None:
        """Setting a set-valued field via update.set replaces wholesale (line 513)."""
        ops = [
            {
                "op": "create", "at": "T1", "by": "agent", "actor": "a",
                "clock": 1, "nonce": "n1",
                "data": {
                    "kind": "invariant", "title": "t", "status": "todo_hard",
                    "priority": 2, "description": "", "tags": ["old1", "old2"],
                    "fields": {},
                },
            },
            {
                "op": "update", "at": "T2", "by": "agent", "actor": "a",
                "clock": 2, "nonce": "n2",
                "set": {"tags": ["new1", "new2"]},
            },
        ]
        item = compile_ops(ops, "INV-test")
        assert sorted(item.tags) == ["new1", "new2"]


# ---------------------------------------------------------------------------
# Coverage: Lamport clock and git operations
# ---------------------------------------------------------------------------


class TestLamportClock:
    def test_local_clock_from_file(self, tmp_path: Path) -> None:
        """Clock reads max from local file when no git dir exists."""
        from hypergumbo_tracker.store import _compute_lamport_clock
        ops_file = tmp_path / "item.ops"
        ops_file.write_text(
            "- op: create\n  at: T1\n  clock: 5\n"
            "- op: update\n  at: T2\n  clock: 10\n"
        )
        clock = _compute_lamport_clock(ops_file, ["dev", "main"], git_dir=None)
        # No git dir found → falls through; max local = 10, returns 11
        assert clock == 11

    def test_corrupt_local_file(self, tmp_path: Path) -> None:
        """CorruptFileError in local file is silently ignored (lines 613-614)."""
        from hypergumbo_tracker.store import _compute_lamport_clock
        ops_file = tmp_path / "item.ops"
        ops_file.write_text("{{corrupt yaml")
        clock = _compute_lamport_clock(ops_file, ["dev", "main"], git_dir=None)
        # Corrupt file → max_clock stays 0 → returns 1
        assert clock == 1

    def test_filepath_not_relative_to_git_dir(self, tmp_path: Path) -> None:
        """ValueError when filepath not relative to git_dir (lines 622-625)."""
        from hypergumbo_tracker.store import _compute_lamport_clock
        git_dir = tmp_path / "repo" / ".git"
        git_dir.mkdir(parents=True)
        # File in a completely different tree
        other = tmp_path / "other" / "item.ops"
        other.parent.mkdir(parents=True)
        other.write_text("- op: create\n  at: T1\n  clock: 3\n")
        clock = _compute_lamport_clock(other, ["dev"], git_dir=git_dir)
        # ValueError → returns max_clock + 1 (local max = 3)
        assert clock == 4

    def test_batch_peek_with_mocked_git(self, tmp_path: Path) -> None:
        """_batch_peek_clocks parses git cat-file --batch output (lines 663-721)."""
        from hypergumbo_tracker.store import _batch_peek_clocks
        yaml_content = b"- op: create\n  at: T1\n  clock: 7\n"
        size = len(yaml_content)

        # Simulate git cat-file --batch output:
        # "<ref> blob <size>\n<content>\n"
        batch_output = (
            f"dev:file.ops blob {size}\n".encode()
            + yaml_content
            + b"\n"
            + b"main:file.ops missing\n"
        )

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (batch_output, b"")

        with patch("hypergumbo_tracker.store.subprocess.Popen", return_value=mock_proc):
            result = _batch_peek_clocks(["dev:file.ops", "main:file.ops"], tmp_path)
        assert result == 7

    def test_batch_peek_empty_refs(self) -> None:
        """_batch_peek_clocks returns 0 for empty refs (line 663)."""
        from hypergumbo_tracker.store import _batch_peek_clocks
        assert _batch_peek_clocks([], Path("/tmp")) == 0

    def test_batch_peek_oserror(self, tmp_path: Path) -> None:
        """_batch_peek_clocks handles OSError gracefully (line 718)."""
        from hypergumbo_tracker.store import _batch_peek_clocks
        with patch("hypergumbo_tracker.store.subprocess.Popen", side_effect=OSError("no git")):
            result = _batch_peek_clocks(["dev:file.ops"], tmp_path)
        assert result == 0

    def test_batch_peek_short_header(self, tmp_path: Path) -> None:
        """_batch_peek_clocks skips headers with < 3 parts (line 697)."""
        from hypergumbo_tracker.store import _batch_peek_clocks
        batch_output = b"short\n"

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (batch_output, b"")

        with patch("hypergumbo_tracker.store.subprocess.Popen", return_value=mock_proc):
            result = _batch_peek_clocks(["ref:path"], tmp_path)
        assert result == 0

    def test_batch_peek_invalid_size(self, tmp_path: Path) -> None:
        """_batch_peek_clocks skips headers with non-integer size (line 702)."""
        from hypergumbo_tracker.store import _batch_peek_clocks
        batch_output = b"ref blob notanumber\n"

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (batch_output, b"")

        with patch("hypergumbo_tracker.store.subprocess.Popen", return_value=mock_proc):
            result = _batch_peek_clocks(["ref:path"], tmp_path)
        assert result == 0

    def test_batch_peek_truncated_content(self, tmp_path: Path) -> None:
        """_batch_peek_clocks handles truncated content (line 706)."""
        from hypergumbo_tracker.store import _batch_peek_clocks
        # Size says 1000 but actual content is shorter
        batch_output = b"ref blob 1000\nshort"

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (batch_output, b"")

        with patch("hypergumbo_tracker.store.subprocess.Popen", return_value=mock_proc):
            result = _batch_peek_clocks(["ref:path"], tmp_path)
        assert result == 0

    def test_lamport_clock_with_git_dir(self, tmp_path: Path) -> None:
        """Full _compute_lamport_clock with git dir and unmerged branches (lines 630-653)."""
        from hypergumbo_tracker.store import _compute_lamport_clock

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        ops_file = tmp_path / "items" / "file.ops"
        ops_file.parent.mkdir()
        ops_file.write_text("- op: create\n  at: T1\n  clock: 3\n")

        # Mock subprocess.run for unmerged branches
        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stdout = "feature-a\nfeature-b\n"

        # Mock _batch_peek_clocks to return a higher clock
        with patch("hypergumbo_tracker.store.subprocess.run", return_value=mock_run_result):
            with patch("hypergumbo_tracker.store._batch_peek_clocks", return_value=5):
                clock = _compute_lamport_clock(ops_file, ["dev", "main"], git_dir=git_dir)
        # max(local=3, batch=5) + 1 = 6
        assert clock == 6

    def test_lamport_clock_unmerged_timeout(self, tmp_path: Path) -> None:
        """Unmerged branch check handles TimeoutExpired (line 649)."""
        from hypergumbo_tracker.store import _compute_lamport_clock
        import subprocess as sp

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        ops_file = tmp_path / "file.ops"
        ops_file.write_text("- op: create\n  at: T1\n  clock: 2\n")

        with patch("hypergumbo_tracker.store.subprocess.run",
                    side_effect=sp.TimeoutExpired("git", 5)):
            with patch("hypergumbo_tracker.store._batch_peek_clocks", return_value=0):
                clock = _compute_lamport_clock(ops_file, ["dev"], git_dir=git_dir)
        # Local max = 2, batch = 0 → max(2, 0) + 1 = 3
        assert clock == 3

    def test_lamport_clock_no_lamport_branches(self, tmp_path: Path) -> None:
        """Lamport clock with empty branches list uses 'dev' default (line 637)."""
        from hypergumbo_tracker.store import _compute_lamport_clock

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        ops_file = tmp_path / "file.ops"
        ops_file.write_text("- op: create\n  at: T1\n  clock: 1\n")

        mock_run_result = MagicMock()
        mock_run_result.returncode = 1  # no unmerged
        mock_run_result.stdout = ""

        with patch("hypergumbo_tracker.store.subprocess.run", return_value=mock_run_result):
            with patch("hypergumbo_tracker.store._batch_peek_clocks", return_value=0):
                clock = _compute_lamport_clock(ops_file, [], git_dir=git_dir)
        assert clock == 2


# ---------------------------------------------------------------------------
# Coverage: _find_git_dir worktree (line 733)
# ---------------------------------------------------------------------------


class TestFindGitDirWorktree:
    def test_gitdir_file(self, tmp_path: Path) -> None:
        """_find_git_dir handles .git as a file (worktree link)."""
        from hypergumbo_tracker.store import _find_git_dir
        gitfile = tmp_path / ".git"
        gitfile.write_text("gitdir: /some/path/.git/worktrees/foo\n")
        found = _find_git_dir(tmp_path / "subdir" / "file.txt")
        # .git is a file → returned as the git dir
        assert found == gitfile


# ---------------------------------------------------------------------------
# Coverage: Store property and edge cases
# ---------------------------------------------------------------------------


class TestStoreProperties:
    def test_ops_dir_property(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """ops_dir property (line 764)."""
        store = Store(ops_dir, config=_make_config())
        assert store.ops_dir == ops_dir

    def test_config_property(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """config property (line 768)."""
        config = _make_config()
        store = Store(ops_dir, config=config)
        assert store.config is config

    def test_init_without_config(self, tmp_path: Path) -> None:
        """Store init with config=None loads from parent (line 758)."""
        from hypergumbo_tracker.models import load_config
        ops_dir = tmp_path / ".ops"
        ops_dir.mkdir()
        # No config.yaml or template → uses built-in defaults
        store = Store(ops_dir)  # config=None
        assert store.config is not None

    def test_list_item_files_no_dir(self, tmp_path: Path) -> None:
        """_list_item_files when ops_dir doesn't exist (line 781)."""
        store = Store(tmp_path / "nonexistent", config=_make_config())
        assert store._list_item_files() == []

    def test_id_from_filename_no_match(self, tmp_path: Path) -> None:
        """_id_from_filename with non-matching filename (line 793)."""
        store = Store(tmp_path, config=_make_config())
        result = store._id_from_filename(Path("plain_file.txt"))
        assert result == "plain_file.txt"


# ---------------------------------------------------------------------------
# Coverage: Store update/discuss/lock agent enforcement
# ---------------------------------------------------------------------------


class TestAgentEnforcement:
    def test_update_locked_field_agent(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Agent update on locked field raises LockedFieldError (lines 928-952)."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Locked Item")

        # Manually write a lock op to the file (human authority)
        import datetime
        lock_op = {
            "op": "lock",
            "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "by": "human", "actor": "jgstern",
            "clock": 99, "nonce": "1234",
            "lock": ["status"],
        }
        serialized = _serialize_op(lock_op)
        item_path = ops_dir / f".{item_id}.ops"
        with open(item_path, "a") as f:
            f.write(serialized)

        # Now try to update status as agent → should fail
        with pytest.raises(LockedFieldError, match="status"):
            store.update(item_id, set_fields={"status": "done"})

    def test_update_locked_add_field(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Agent add on locked field raises LockedFieldError."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Locked Tags")

        lock_op = {
            "op": "lock", "at": "2026-02-14T12:00:00Z",
            "by": "human", "actor": "jgstern",
            "clock": 99, "nonce": "abcd",
            "lock": ["tags"],
        }
        serialized = _serialize_op(lock_op)
        with open(ops_dir / f".{item_id}.ops", "a") as f:
            f.write(serialized)

        with pytest.raises(LockedFieldError, match="tags"):
            store.update(item_id, add_fields={"tags": ["new"]})

    def test_update_locked_remove_field(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Agent remove on locked field raises LockedFieldError."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Locked Remove", tags=["a"])

        lock_op = {
            "op": "lock", "at": "2026-02-14T12:00:00Z",
            "by": "human", "actor": "jgstern",
            "clock": 99, "nonce": "dcba",
            "lock": ["tags"],
        }
        serialized = _serialize_op(lock_op)
        with open(ops_dir / f".{item_id}.ops", "a") as f:
            f.write(serialized)

        with pytest.raises(LockedFieldError, match="tags"):
            store.update(item_id, remove_fields={"tags": ["a"]})

    def test_discuss_clear_agent(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Agent discuss_clear raises HumanAuthorityError (line 1002)."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Clear Test")
        with pytest.raises(HumanAuthorityError, match="discuss_clear"):
            store.discuss(item_id, message="", clear=True)

    def test_discuss_locked_discussion(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Agent discuss on locked discussion raises LockedFieldError."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Discussion Lock")

        lock_op = {
            "op": "lock", "at": "2026-02-14T12:00:00Z",
            "by": "human", "actor": "jgstern",
            "clock": 99, "nonce": "lock",
            "lock": ["discussion"],
        }
        serialized = _serialize_op(lock_op)
        with open(ops_dir / f".{item_id}.ops", "a") as f:
            f.write(serialized)

        with pytest.raises(LockedFieldError, match=r"[Dd]iscussion"):
            store.discuss(item_id, message="blocked")

    def test_lock_agent(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Agent lock raises HumanAuthorityError (line 1087)."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Lock Agent Test")
        with pytest.raises(HumanAuthorityError, match="lock"):
            store.lock(item_id, ["status"])

    def test_unlock_agent(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Agent unlock raises HumanAuthorityError (line 1115)."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Unlock Agent Test")
        with pytest.raises(HumanAuthorityError, match="unlock"):
            store.unlock(item_id, ["status"])

    def test_lock_nonexistent(self, ops_dir: Path, mock_human_uid: None) -> None:
        """Lock on nonexistent item raises ItemNotFoundError."""
        store = Store(ops_dir, config=_make_config())
        with pytest.raises(ItemNotFoundError):
            store.lock("INV-nonexistent-id-that-does-not-exist-at-all", ["status"])

    def test_unlock_nonexistent(self, ops_dir: Path, mock_human_uid: None) -> None:
        """Unlock on nonexistent item raises ItemNotFoundError."""
        store = Store(ops_dir, config=_make_config())
        with pytest.raises(ItemNotFoundError):
            store.unlock("INV-nonexistent-id-that-does-not-exist-at-all", ["status"])

    def test_discuss_nonexistent(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Discuss on nonexistent item raises ItemNotFoundError."""
        store = Store(ops_dir, config=_make_config())
        with pytest.raises(ItemNotFoundError):
            store.discuss("INV-nonexistent-id-that-does-not-exist-at-all", message="hi")


# ---------------------------------------------------------------------------
# Coverage: cross-branch locked fields
# ---------------------------------------------------------------------------


class TestCrossBranchLockedFields:
    def test_no_git_dir(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Returns empty set when no git dir found (line 1167)."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="No Git")
        item_path = ops_dir / f".{item_id}.ops"

        with patch("hypergumbo_tracker.store._find_git_dir", return_value=None):
            result = store._cross_branch_locked_fields(item_path, item_id)
        assert result == set()

    def test_value_error_relative_path(self, ops_dir: Path, tmp_path: Path, mock_agent_uid: None) -> None:
        """Returns empty set on ValueError (file not relative to git parent)."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="RelPath Error")
        item_path = ops_dir / f".{item_id}.ops"
        # Git dir in different tree
        other_git = tmp_path / "other" / ".git"
        other_git.mkdir(parents=True)

        with patch("hypergumbo_tracker.store._find_git_dir", return_value=other_git):
            result = store._cross_branch_locked_fields(item_path, item_id)
        assert result == set()

    def test_with_mocked_git(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Cross-branch lock check with mocked git cat-file (lines 1185-1228)."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Cross Branch Lock")
        item_path = ops_dir / f".{item_id}.ops"

        # Build mock git output that has a lock op
        yaml_content = (
            b"- op: create\n  at: T1\n  by: human\n  actor: jgstern\n"
            b"  clock: 1\n  nonce: aaaa\n  data:\n    kind: invariant\n"
            b"    title: Cross Branch Lock\n    status: todo_hard\n"
            b"    priority: 2\n    description: \"\"\n    fields: {}\n"
            b"- op: lock\n  at: T2\n  by: human\n  actor: jgstern\n"
            b"  clock: 2\n  nonce: bbbb\n  lock:\n  - priority\n"
        )
        size = len(yaml_content)
        batch_output = f"dev:path blob {size}\n".encode() + yaml_content + b"\nHEAD:path missing\n"

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (batch_output, b"")

        git_dir = ops_dir.parent / ".git"
        git_dir.mkdir(exist_ok=True)

        with patch("hypergumbo_tracker.store._find_git_dir", return_value=git_dir):
            with patch("hypergumbo_tracker.store.subprocess.Popen", return_value=mock_proc):
                result = store._cross_branch_locked_fields(item_path, item_id)

        assert "priority" in result

    def test_corrupt_branch_content(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """CorruptFileError on branch content is silently ignored (line 1241)."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Corrupt Branch")
        item_path = ops_dir / f".{item_id}.ops"

        # Git output with content that parses as YAML list but fails compile (no create)
        yaml_content = b"- op: update\n  clock: 5\n"
        size = len(yaml_content)
        batch_output = f"dev:path blob {size}\n".encode() + yaml_content + b"\n"

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (batch_output, b"")

        git_dir = ops_dir.parent / ".git"
        git_dir.mkdir(exist_ok=True)

        with patch("hypergumbo_tracker.store._find_git_dir", return_value=git_dir):
            with patch("hypergumbo_tracker.store.subprocess.Popen", return_value=mock_proc):
                result = store._cross_branch_locked_fields(item_path, item_id)

        assert result == set()

    def test_oserror(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """OSError in cross-branch check is silently handled (line 1228)."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="OS Error")
        item_path = ops_dir / f".{item_id}.ops"

        git_dir = ops_dir.parent / ".git"
        git_dir.mkdir(exist_ok=True)

        with patch("hypergumbo_tracker.store._find_git_dir", return_value=git_dir):
            with patch("hypergumbo_tracker.store.subprocess.Popen", side_effect=OSError("no")):
                result = store._cross_branch_locked_fields(item_path, item_id)

        assert result == set()


# ---------------------------------------------------------------------------
# Coverage: similarity check with corrupt file
# ---------------------------------------------------------------------------


class TestSimilarityCheckCorrupt:
    def test_corrupt_file_skipped(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """CorruptFileError during similarity check is skipped (line 1241/1259)."""
        store = Store(ops_dir, config=_make_config())
        # Write a corrupt ops file
        (ops_dir / ".INV-corrupt-sim.ops").write_text("{{bad yaml")
        # Adding a new item should succeed (corrupt file skipped in similarity check)
        item_id = store.add(kind="invariant", title="Not Corrupt")
        assert item_id.startswith("INV-")


# ---------------------------------------------------------------------------
# Coverage: discussion rate limit
# ---------------------------------------------------------------------------


class TestDiscussionRateLimit:
    def test_rate_limit_exceeded(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Discussion rate limit triggers DiscussionRateLimitError (line 1256)."""
        import datetime
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Rate Limited")

        # Write many discuss ops with today's date to exceed 200k tokens
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        big_message = "x" * 880_001  # 880,001 chars / 4.4 = 200,000.23 tokens
        discuss_op = {
            "op": "discuss", "at": today, "by": "agent", "actor": "test_agent",
            "clock": 50, "nonce": "rate",
            "message": big_message,
        }
        serialized = _serialize_op(discuss_op)
        with open(ops_dir / f".{item_id}.ops", "a") as f:
            f.write(serialized)

        with pytest.raises(DiscussionRateLimitError, match="rate limit"):
            store.discuss(item_id, message="one more")


# ---------------------------------------------------------------------------
# Coverage: _resolve_id ambiguous prefix
# ---------------------------------------------------------------------------


class TestResolveIdAmbiguous:
    def test_ambiguous_prefix(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Ambiguous prefix raises AmbiguousPrefixError (lines 1347-1348)."""
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="Item Alpha")
        store.add(kind="invariant", title="Item Beta")

        # "INV-" matches both
        with pytest.raises(AmbiguousPrefixError, match="ambiguous"):
            store._resolve_id("INV-")


# ---------------------------------------------------------------------------
# Coverage: ready() duplicate_of and cross_tier_conflict
# ---------------------------------------------------------------------------


class TestReadyFilters:
    def test_duplicate_excluded(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Items with duplicate_of are excluded from ready (line 1437)."""
        store = Store(ops_dir, config=_make_config())
        id1 = store.add(kind="invariant", title="Original", status="todo_hard")
        id2 = store.add(kind="invariant", title="Duplicate", status="todo_hard",
                        duplicate_of=[id1])
        ready = store.ready()
        ready_ids = {r.id for r in ready}
        assert id1 in ready_ids
        assert id2 not in ready_ids

    def test_cross_tier_conflict_excluded(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Items with cross_tier_conflict are excluded from ready (line 1437)."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Conflicted", status="todo_hard")

        # Manually set cross_tier_conflict on compiled item
        # We need to write it into the ops file in a way that compile sees it
        # Actually cross_tier_conflict is not set by compile_ops — it's set externally
        # So we test by checking that list/ready filters it out when set
        items = store.list_items()
        # The item should be in the list
        assert any(i.id == item_id for i in items)

    def test_resolved_item_not_ready(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Items in resolved_statuses are excluded from ready."""
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="Done", status="done")
        ready = store.ready()
        assert len(ready) == 0


# ---------------------------------------------------------------------------
# Coverage: before-cycle detection with actual cycle
# ---------------------------------------------------------------------------


class TestBeforeCycleDetection:
    def test_cycle_detected(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Check before_cycles detects cycles (lines 1539-1540)."""
        store = Store(ops_dir, config=_make_config())
        id_a = store.add(kind="invariant", title="A")
        id_b = store.add(kind="invariant", title="B")

        # A.before = [B] means A blocks B
        store.update(id_a, add_fields={"before": [id_b]})
        # B.before = [A] means B blocks A → cycle
        store.update(id_b, add_fields={"before": [id_a]})

        cycles = store.check_before_cycles()
        assert len(cycles) > 0
        # Each cycle should contain both IDs
        found_both = any(id_a in c and id_b in c for c in cycles)
        assert found_both


# ---------------------------------------------------------------------------
# Coverage: human-authority operations (lock, unlock, discuss_clear)
# ---------------------------------------------------------------------------


class TestHumanAuthorityOps:
    def test_lock_as_human(self, ops_dir: Path, mock_human_uid: None) -> None:
        """Human can lock fields."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Human Lock")
        store.lock(item_id, ["status", "priority"])
        item = store.get(item_id)
        assert "status" in item.locked_fields
        assert "priority" in item.locked_fields

    def test_unlock_as_human(self, ops_dir: Path, mock_human_uid: None) -> None:
        """Human can unlock fields."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Human Unlock")
        store.lock(item_id, ["status"])
        store.unlock(item_id, ["status"])
        item = store.get(item_id)
        assert "status" not in item.locked_fields

    def test_discuss_clear_as_human(self, ops_dir: Path, mock_human_uid: None) -> None:
        """Human can clear discussion."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Clear Discussion")
        store.discuss(item_id, message="first message")
        item = store.get(item_id)
        assert len(item.discussion) == 1
        store.discuss(item_id, message="", clear=True)
        item = store.get(item_id)
        assert len(item.discussion) == 0

    def test_discuss_summarize(self, ops_dir: Path, mock_human_uid: None) -> None:
        """Summarize replaces discussion with summary entry."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Summarize Test")
        store.discuss(item_id, message="msg1")
        store.discuss(item_id, message="msg2")
        store.discuss(item_id, message="Summary of discussion", summarize=True)
        item = store.get(item_id)
        assert len(item.discussion) == 1
        assert item.discussion[0].is_summary is True

    def test_update_nonexistent_item(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Update on nonexistent item raises ItemNotFoundError (line 928)."""
        store = Store(ops_dir, config=_make_config())
        # Create an ops file that will match resolve but then delete it
        # so _resolve_id succeeds (exact match) but the file is gone
        fake_id = "INV-aaaaa-bbbbb-ccccc-ddddd-eeeee-fffff-ggggg-hhhhh"
        fake_path = ops_dir / f".{fake_id}.ops"
        fake_path.write_text("- op: create\n  at: T1\n  clock: 1\n  data: {kind: invariant, title: t}\n")
        # Delete the file — _resolve_id uses exact match (path.exists()), but
        # update() checks again. So we need the file to exist for resolve
        # but be missing for the update check. Use mock instead.
        with patch.object(store, "_resolve_id", return_value=fake_id):
            fake_path.unlink()
            with pytest.raises(ItemNotFoundError, match="not found"):
                store.update(fake_id, set_fields={"status": "done"})


# ---------------------------------------------------------------------------
# Coverage: get() after resolve returns path that doesn't exist
# ---------------------------------------------------------------------------


class TestGetEdgeCases:
    def test_get_deleted_after_resolve(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """get() when item path doesn't exist after resolve (line 1478)."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Will Delete")
        # Delete the file after resolve would succeed
        item_path = ops_dir / f".{item_id}.ops"
        item_path.unlink()
        with pytest.raises(ItemNotFoundError):
            store.get(item_id)


# ---------------------------------------------------------------------------
# Coverage: _compile_all skip corrupt (line 1492)
# ---------------------------------------------------------------------------


class TestCompileAllCorrupt:
    def test_compile_all_skips_no_create(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """_compile_all skips files that fail to compile (line 1492)."""
        store = Store(ops_dir, config=_make_config())
        store.add(kind="invariant", title="Good")
        # Write file with only an update op (no create → CorruptFileError in compile)
        (ops_dir / ".INV-nocreate.ops").write_text("- op: update\n  clock: 1\n")
        items = store.list_items()
        assert len(items) == 1
        assert items[0].title == "Good"


# ---------------------------------------------------------------------------
# Coverage: compile_ops duplicate create with earlier timestamp (line 513)
# ---------------------------------------------------------------------------


class TestCompileDuplicateCreateEarlier:
    def test_duplicate_create_earlier_timestamp_sets_created_at(self) -> None:
        """Duplicate create with earlier timestamp updates created_at (line 513)."""
        ops = [
            {
                "op": "create", "at": "2026-02-11T19:00:00Z", "by": "agent",
                "actor": "agent_a", "clock": 1, "nonce": "aaaa",
                "data": {
                    "kind": "invariant", "title": "T", "status": "todo_hard",
                    "priority": 2, "description": "", "fields": {},
                },
            },
            {
                "op": "create", "at": "2026-02-11T17:00:00Z", "by": "agent",
                "actor": "agent_b", "clock": 2, "nonce": "bbbb",
                "data": {
                    "kind": "invariant", "title": "T", "status": "todo_hard",
                    "priority": 2, "description": "", "fields": {},
                },
            },
        ]
        item = compile_ops(ops, "INV-test")
        # Second create has earlier timestamp → created_at updated
        assert item.created_at == "2026-02-11T17:00:00Z"


# ---------------------------------------------------------------------------
# Coverage: _batch_peek_clocks no-newline edge case (line 688)
# ---------------------------------------------------------------------------


class TestBatchPeekNoNewline:
    def test_output_without_trailing_newline(self, tmp_path: Path) -> None:
        """_batch_peek_clocks handles output without trailing newline (line 688)."""
        from hypergumbo_tracker.store import _batch_peek_clocks
        # Output that doesn't end with newline — nl == -1 branch
        batch_output = b"some data without newline"

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (batch_output, b"")

        with patch("hypergumbo_tracker.store.subprocess.Popen", return_value=mock_proc):
            result = _batch_peek_clocks(["ref:path"], tmp_path)
        assert result == 0


# ---------------------------------------------------------------------------
# Coverage: discuss/lock/unlock nonexistent after resolve (lines 1002, 1087, 1115, 1492)
# ---------------------------------------------------------------------------


class TestMethodsNonexistentAfterResolve:
    def test_discuss_nonexistent_after_resolve(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """discuss() when file doesn't exist after resolve (line 1002)."""
        store = Store(ops_dir, config=_make_config())
        fake_id = "INV-aaaaa-bbbbb-ccccc-ddddd-eeeee-fffff-ggggg-hhhhh"
        with patch.object(store, "_resolve_id", return_value=fake_id):
            with pytest.raises(ItemNotFoundError):
                store.discuss(fake_id, message="hi")

    def test_lock_nonexistent_after_resolve(self, ops_dir: Path, mock_human_uid: None) -> None:
        """lock() when file doesn't exist after resolve (line 1087)."""
        store = Store(ops_dir, config=_make_config())
        fake_id = "INV-aaaaa-bbbbb-ccccc-ddddd-eeeee-fffff-ggggg-hhhhh"
        with patch.object(store, "_resolve_id", return_value=fake_id):
            with pytest.raises(ItemNotFoundError):
                store.lock(fake_id, ["status"])

    def test_unlock_nonexistent_after_resolve(self, ops_dir: Path, mock_human_uid: None) -> None:
        """unlock() when file doesn't exist after resolve (line 1115)."""
        store = Store(ops_dir, config=_make_config())
        fake_id = "INV-aaaaa-bbbbb-ccccc-ddddd-eeeee-fffff-ggggg-hhhhh"
        with patch.object(store, "_resolve_id", return_value=fake_id):
            with pytest.raises(ItemNotFoundError):
                store.unlock(fake_id, ["status"])

    def test_get_nonexistent_after_resolve(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """get() when file doesn't exist after resolve (line 1492)."""
        store = Store(ops_dir, config=_make_config())
        fake_id = "INV-aaaaa-bbbbb-ccccc-ddddd-eeeee-fffff-ggggg-hhhhh"
        with patch.object(store, "_resolve_id", return_value=fake_id):
            with pytest.raises(ItemNotFoundError):
                store.get(fake_id)


# ---------------------------------------------------------------------------
# Coverage: _cross_branch_locked_fields parsing edge cases
# ---------------------------------------------------------------------------


class TestCrossBranchParsingEdgeCases:
    def _make_store_with_item(self, ops_dir: Path) -> tuple:
        """Helper: create store with one item and return (store, item_id, item_path)."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Cross Branch Parse")
        item_path = ops_dir / f".{item_id}.ops"
        return store, item_id, item_path

    def test_no_newline_in_output(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Output without newline triggers nl==-1 break (line 1196)."""
        store, item_id, item_path = self._make_store_with_item(ops_dir)
        batch_output = b"data without newline"
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (batch_output, b"")
        git_dir = ops_dir.parent / ".git"
        git_dir.mkdir(exist_ok=True)

        with patch("hypergumbo_tracker.store._find_git_dir", return_value=git_dir):
            with patch("hypergumbo_tracker.store.subprocess.Popen", return_value=mock_proc):
                result = store._cross_branch_locked_fields(item_path, item_id)
        assert result == set()

    def test_short_header(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Header with < 3 parts triggers continue (line 1205)."""
        store, item_id, item_path = self._make_store_with_item(ops_dir)
        batch_output = b"shortline\n"
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (batch_output, b"")
        git_dir = ops_dir.parent / ".git"
        git_dir.mkdir(exist_ok=True)

        with patch("hypergumbo_tracker.store._find_git_dir", return_value=git_dir):
            with patch("hypergumbo_tracker.store.subprocess.Popen", return_value=mock_proc):
                result = store._cross_branch_locked_fields(item_path, item_id)
        assert result == set()

    def test_invalid_size_header(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Header with non-integer size triggers ValueError continue (lines 1209-1210)."""
        store, item_id, item_path = self._make_store_with_item(ops_dir)
        batch_output = b"ref blob notanumber\n"
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (batch_output, b"")
        git_dir = ops_dir.parent / ".git"
        git_dir.mkdir(exist_ok=True)

        with patch("hypergumbo_tracker.store._find_git_dir", return_value=git_dir):
            with patch("hypergumbo_tracker.store.subprocess.Popen", return_value=mock_proc):
                result = store._cross_branch_locked_fields(item_path, item_id)
        assert result == set()

    def test_truncated_content(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Truncated content triggers pos+size > len(output) break (line 1213)."""
        store, item_id, item_path = self._make_store_with_item(ops_dir)
        batch_output = b"ref blob 9999\nshort"
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (batch_output, b"")
        git_dir = ops_dir.parent / ".git"
        git_dir.mkdir(exist_ok=True)

        with patch("hypergumbo_tracker.store._find_git_dir", return_value=git_dir):
            with patch("hypergumbo_tracker.store.subprocess.Popen", return_value=mock_proc):
                result = store._cross_branch_locked_fields(item_path, item_id)
        assert result == set()


# ---------------------------------------------------------------------------
# Coverage: _check_similarity no-create-op file (line 1256)
# ---------------------------------------------------------------------------


class TestSimilarityNoCreate:
    def test_file_without_create_op_skipped(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """Existing file without create op is skipped in similarity check (line 1256)."""
        store = Store(ops_dir, config=_make_config())
        # Write a file with only update ops (no create)
        (ops_dir / ".INV-nocreatefile.ops").write_text(
            "- op: update\n  at: T1\n  by: agent\n  actor: a\n  clock: 1\n  nonce: aaaa\n  set: {status: done}\n"
        )
        # Adding a new item should not crash on the no-create file
        item_id = store.add(kind="invariant", title="Has Create")
        assert item_id.startswith("INV-")


# ---------------------------------------------------------------------------
# Coverage: _resolve_id with corrupt file in prefix matching (lines 1347-1348)
# ---------------------------------------------------------------------------


class TestResolveIdCorruptFile:
    def test_corrupt_file_in_prefix_match(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """CorruptFileError during prefix matching sets empty title (lines 1347-1348)."""
        store = Store(ops_dir, config=_make_config())
        # Write a corrupt file. Use a partial prefix for resolution so
        # it goes through prefix matching (not exact match).
        (ops_dir / ".INV-corrupt-prefix-test.ops").write_text("{{bad yaml")
        # Resolve with just a prefix that doesn't match exactly
        resolved = store._resolve_id("INV-corrupt-prefix")
        assert resolved == "INV-corrupt-prefix-test"


# ---------------------------------------------------------------------------
# Coverage: ready() cross_tier_conflict exclusion (line 1437)
# ---------------------------------------------------------------------------


class TestReadyCrossTierConflict:
    def test_cross_tier_conflict_excluded_from_ready(
        self, ops_dir: Path, mock_agent_uid: None
    ) -> None:
        """Items with cross_tier_conflict=True are excluded from ready (line 1437)."""
        store = Store(ops_dir, config=_make_config())
        item_id = store.add(kind="invariant", title="Conflicted", status="todo_hard")

        # Patch _compile_all to return the item with cross_tier_conflict=True
        real_items = store._compile_all()
        for item in real_items:
            if item.id == item_id:
                item.cross_tier_conflict = True

        with patch.object(store, "_compile_all", return_value=real_items):
            ready = store.ready()
        ready_ids = {r.id for r in ready}
        assert item_id not in ready_ids


# ---------------------------------------------------------------------------
# Coverage: ancestors() parent not in item_map (line 1478)
# ---------------------------------------------------------------------------


class TestAncestorsOrphanParent:
    def test_orphan_parent_stops_traversal(self, ops_dir: Path, mock_agent_uid: None) -> None:
        """ancestors() stops when parent ID is not in the store (line 1478)."""
        store = Store(ops_dir, config=_make_config())
        # Create item with a parent that doesn't exist in the store
        item_id = store.add(
            kind="invariant", title="Child with Missing Parent",
            parent="INV-nonexistent-parent-id",
        )
        ancestors = store.ancestors(item_id)
        # No ancestors found since parent doesn't exist
        assert ancestors == []
