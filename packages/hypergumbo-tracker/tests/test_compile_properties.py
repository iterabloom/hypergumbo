# SPDX-License-Identifier: MPL-2.0
"""Property-based tests for compile() using hypothesis.

Tests invariants that must hold for any valid op sequence:
- Idempotency: compile(ops) produces same result regardless of call count
- Permutation invariance: compile(shuffle(ops)) == compile(ops)
- Terminal status consistency
- Duplicate-create resilience
- Additive-op commutativity for set-valued fields
"""

from __future__ import annotations

import copy
import random
from typing import Any

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from hypergumbo_tracker.store import compile_ops


# ---------------------------------------------------------------------------
# Strategies for generating random op sequences
# ---------------------------------------------------------------------------


def _make_ts(day: int, hour: int, minute: int) -> str:
    """Generate a deterministic ISO 8601 timestamp."""
    return f"2026-02-{day:02d}T{hour:02d}:{minute:02d}:00Z"


@st.composite
def create_op(draw: st.DrawFn) -> dict[str, Any]:
    """Generate a valid create op dict."""
    day = draw(st.integers(min_value=1, max_value=15))
    hour = draw(st.integers(min_value=0, max_value=23))
    minute = draw(st.integers(min_value=0, max_value=59))
    return {
        "op": "create",
        "at": _make_ts(day, hour, minute),
        "by": draw(st.sampled_from(["agent", "human"])),
        "actor": draw(st.sampled_from(["test_agent", "jgstern"])),
        "clock": draw(st.integers(min_value=1, max_value=5)),
        "nonce": draw(st.from_regex(r"[0-9a-f]{4}", fullmatch=True)),
        "data": {
            "kind": "invariant",
            "title": draw(st.text(min_size=1, max_size=50)),
            "status": draw(st.sampled_from(["todo_hard", "todo_soft", "in_progress", "done"])),
            "priority": draw(st.integers(min_value=0, max_value=4)),
            "parent": None,
            "tags": draw(st.lists(st.sampled_from(["tag_a", "tag_b", "tag_c"]), max_size=3)),
            "before": [],
            "duplicate_of": [],
            "not_duplicate_of": [],
            "pr_ref": None,
            "description": draw(st.text(max_size=100)),
            "fields": {
                "statement": draw(st.text(max_size=50)),
            },
        },
    }


@st.composite
def update_op(draw: st.DrawFn, min_clock: int = 2) -> dict[str, Any]:
    """Generate a valid update op dict."""
    day = draw(st.integers(min_value=1, max_value=15))
    hour = draw(st.integers(min_value=0, max_value=23))
    minute = draw(st.integers(min_value=0, max_value=59))
    op: dict[str, Any] = {
        "op": "update",
        "at": _make_ts(day, hour, minute),
        "by": draw(st.sampled_from(["agent", "human"])),
        "actor": draw(st.sampled_from(["test_agent", "jgstern"])),
        "clock": draw(st.integers(min_value=min_clock, max_value=100)),
        "nonce": draw(st.from_regex(r"[0-9a-f]{4}", fullmatch=True)),
        "set": {},
    }
    # Optionally set scalar fields
    if draw(st.booleans()):
        op["set"]["status"] = draw(st.sampled_from(["todo_hard", "done", "in_progress"]))
    if draw(st.booleans()):
        op["set"]["priority"] = draw(st.integers(min_value=0, max_value=4))
    # Optionally add/remove tags
    if draw(st.booleans()):
        op["add"] = {"tags": draw(st.lists(st.sampled_from(["tag_a", "tag_b", "tag_c"]), min_size=1, max_size=2))}
    if draw(st.booleans()):
        op["remove"] = {"tags": draw(st.lists(st.sampled_from(["tag_a", "tag_b", "tag_c"]), min_size=1, max_size=2))}
    return op


@st.composite
def discuss_op(draw: st.DrawFn, min_clock: int = 2) -> dict[str, Any]:
    """Generate a valid discuss op dict."""
    day = draw(st.integers(min_value=1, max_value=15))
    hour = draw(st.integers(min_value=0, max_value=23))
    minute = draw(st.integers(min_value=0, max_value=59))
    return {
        "op": "discuss",
        "at": _make_ts(day, hour, minute),
        "by": draw(st.sampled_from(["agent", "human"])),
        "actor": draw(st.sampled_from(["test_agent", "jgstern"])),
        "clock": draw(st.integers(min_value=min_clock, max_value=100)),
        "nonce": draw(st.from_regex(r"[0-9a-f]{4}", fullmatch=True)),
        "message": draw(st.text(max_size=100)),
    }


@st.composite
def lock_op(draw: st.DrawFn, min_clock: int = 2) -> dict[str, Any]:
    """Generate a valid lock op dict."""
    day = draw(st.integers(min_value=1, max_value=15))
    hour = draw(st.integers(min_value=0, max_value=23))
    minute = draw(st.integers(min_value=0, max_value=59))
    return {
        "op": "lock",
        "at": _make_ts(day, hour, minute),
        "by": "human",
        "actor": "jgstern",
        "clock": draw(st.integers(min_value=min_clock, max_value=100)),
        "nonce": draw(st.from_regex(r"[0-9a-f]{4}", fullmatch=True)),
        "lock": draw(st.lists(st.sampled_from(["priority", "status", "tags"]), min_size=1, max_size=3)),
    }


@st.composite
def op_sequence(draw: st.DrawFn) -> list[dict[str, Any]]:
    """Generate a valid op sequence: one create + random updates/discusses/locks.

    Each op gets a unique nonce to ensure the sort key is deterministic
    regardless of input order (matching production behaviour where nonces
    are random and extremely unlikely to collide).
    """
    create = draw(create_op())
    n_subsequent = draw(st.integers(min_value=0, max_value=10))
    subsequent = draw(
        st.lists(
            st.one_of(update_op(), discuss_op(), lock_op()),
            min_size=n_subsequent,
            max_size=n_subsequent,
        )
    )
    all_ops = [create] + subsequent
    # Assign unique nonces so sort order is fully deterministic
    for i, op in enumerate(all_ops):
        op["nonce"] = f"{i:04x}"
    return all_ops


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestCompileProperties:
    @given(ops=op_sequence())
    @settings(max_examples=50)
    def test_idempotency(self, ops: list[dict[str, Any]]) -> None:
        """compile(ops) produces the same result on repeated calls."""
        result1 = compile_ops(ops, "INV-test")
        result2 = compile_ops(ops, "INV-test")

        assert result1.title == result2.title
        assert result1.status == result2.status
        assert result1.priority == result2.priority
        assert result1.tags == result2.tags
        assert result1.fields == result2.fields
        assert result1.locked_fields == result2.locked_fields
        assert len(result1.discussion) == len(result2.discussion)

    @given(ops=op_sequence())
    @settings(max_examples=50)
    def test_permutation_invariance(self, ops: list[dict[str, Any]]) -> None:
        """compile(shuffle(ops)) == compile(ops).

        Because ops are sorted by Lamport clock, the result should be
        deterministic regardless of input order.
        """
        assume(len(ops) >= 2)

        result1 = compile_ops(ops, "INV-test")

        # Shuffle the ops
        shuffled = list(ops)
        random.shuffle(shuffled)
        result2 = compile_ops(shuffled, "INV-test")

        assert result1.title == result2.title
        assert result1.status == result2.status
        assert result1.priority == result2.priority
        assert sorted(result1.tags) == sorted(result2.tags)
        assert result1.fields == result2.fields
        assert result1.locked_fields == result2.locked_fields

    @given(ops=op_sequence())
    @settings(max_examples=50)
    def test_created_at_always_set(self, ops: list[dict[str, Any]]) -> None:
        """compiled item always has created_at from the create op."""
        result = compile_ops(ops, "INV-test")
        assert result.created_at != ""

    @given(ops=op_sequence())
    @settings(max_examples=50)
    def test_updated_at_gte_created_at(self, ops: list[dict[str, Any]]) -> None:
        """updated_at is always >= created_at (lexicographic for ISO 8601)."""
        result = compile_ops(ops, "INV-test")
        # Both are ISO 8601 strings, so lexicographic comparison works
        assert result.updated_at >= result.created_at

    def test_duplicate_create_resilience(self) -> None:
        """Two create ops with identical data (cross-branch merge scenario)."""
        data = {
            "kind": "invariant", "title": "Same",
            "status": "todo_hard", "priority": 2,
            "description": "", "fields": {},
        }
        ops = [
            {"op": "create", "at": "2026-02-11T19:00:00Z", "by": "agent",
             "actor": "agent_b", "clock": 3, "nonce": "bbbb", "data": dict(data)},
            {"op": "create", "at": "2026-02-11T18:00:00Z", "by": "agent",
             "actor": "agent_a", "clock": 1, "nonce": "aaaa", "data": dict(data)},
            {"op": "update", "at": "2026-02-11T20:00:00Z", "by": "agent",
             "actor": "agent_a", "clock": 4, "nonce": "cccc",
             "set": {"status": "done"}},
        ]
        result = compile_ops(ops, "INV-test")
        # Uses earliest create timestamp
        assert result.created_at == "2026-02-11T18:00:00Z"
        # Update from either branch is applied
        assert result.status == "done"

    def test_additive_op_commutativity(self) -> None:
        """add/remove ops on tags commute (order doesn't matter for same clock values)."""
        base_create = {
            "op": "create", "at": "T1", "by": "agent", "actor": "a",
            "clock": 1, "nonce": "n1",
            "data": {"kind": "invariant", "title": "t", "status": "todo_hard",
                     "priority": 2, "description": "", "tags": ["initial"], "fields": {}},
        }
        add_op = {
            "op": "update", "at": "T2", "by": "agent", "actor": "a",
            "clock": 2, "nonce": "n2",
            "set": {}, "add": {"tags": ["new_tag"]},
        }
        remove_op = {
            "op": "update", "at": "T3", "by": "agent", "actor": "a",
            "clock": 3, "nonce": "n3",
            "set": {}, "remove": {"tags": ["initial"]},
        }

        # Order 1: add then remove
        result1 = compile_ops([base_create, add_op, remove_op], "INV-test")
        # Order 2: remove then add (different file order, same clock order)
        result2 = compile_ops([base_create, remove_op, add_op], "INV-test")

        # Both should produce the same final tag set
        assert sorted(result1.tags) == sorted(result2.tags)
