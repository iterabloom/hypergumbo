# SPDX-License-Identifier: MPL-2.0
"""Tests for WI-zonur edit-mode ops at the compile_ops() layer.

Covers:
- edit-mode-on / edit-mode-off window tracking (TTL auto-expiry, overlap-replaces,
  by:human gate)
- delete-msg / undelete-msg / edit-msg-text op effects on DiscussionEntry
- Window-bounded authorization: ops outside any valid window are filtered
- Cap enforcement: at most cap_max delete-msg+edit-msg-text per window;
  undelete-msg uncounted
- Half-open interval [start, start+ttl)
- Edit chain preservation: latest text wins; prior texts stored in edit_history
- Tombstone semantics and unknown-target handling

These tests drive the data layer only — CLI / OS-perm verification live in
other test modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hypergumbo_tracker.store import (
    EditModeNotActiveError,
    HumanAuthorityError,
    ItemNotFoundError,
    Store,
    _make_nonce,
    compile_ops,
)


# ---------------------------------------------------------------------------
# Op-construction helpers
# ---------------------------------------------------------------------------


def _create_op(*, at: str = "2026-06-03T00:00:00Z", clock: int = 1) -> dict[str, Any]:
    return {
        "op": "create",
        "at": at,
        "by": "agent",
        "actor": "test_agent",
        "clock": clock,
        "nonce": "0001",
        "data": {
            "kind": "invariant",
            "title": "t",
            "status": "todo_hard",
            "priority": 2,
            "description": "",
            "fields": {},
        },
    }


def _discuss_op(
    *,
    at: str,
    clock: int,
    nonce: str,
    message: str = "hello",
    by: str = "agent",
    actor: str = "test_agent",
) -> dict[str, Any]:
    return {
        "op": "discuss",
        "at": at,
        "by": by,
        "actor": actor,
        "clock": clock,
        "nonce": nonce,
        "message": message,
    }


def _edit_mode_on_op(
    *,
    at: str,
    clock: int,
    nonce: str,
    ttl_seconds: int = 1800,
    cap_max: int = 500,
    by: str = "human",
    actor: str = "jgstern",
) -> dict[str, Any]:
    return {
        "op": "edit-mode-on",
        "at": at,
        "by": by,
        "actor": actor,
        "clock": clock,
        "nonce": nonce,
        "ttl_seconds": ttl_seconds,
        "cap_max": cap_max,
    }


def _edit_mode_off_op(
    *,
    at: str,
    clock: int,
    nonce: str,
    by: str = "human",
    actor: str = "jgstern",
) -> dict[str, Any]:
    return {
        "op": "edit-mode-off",
        "at": at,
        "by": by,
        "actor": actor,
        "clock": clock,
        "nonce": nonce,
    }


def _delete_msg_op(
    *,
    at: str,
    clock: int,
    nonce: str,
    target_nonce: str,
    reason: str = "extracted",
    by: str = "agent",
    actor: str = "test_agent",
) -> dict[str, Any]:
    return {
        "op": "delete-msg",
        "at": at,
        "by": by,
        "actor": actor,
        "clock": clock,
        "nonce": nonce,
        "target_nonce": target_nonce,
        "reason": reason,
    }


def _undelete_msg_op(
    *,
    at: str,
    clock: int,
    nonce: str,
    target_nonce: str,
    reason: str = "reverting",
    by: str = "agent",
    actor: str = "test_agent",
) -> dict[str, Any]:
    return {
        "op": "undelete-msg",
        "at": at,
        "by": by,
        "actor": actor,
        "clock": clock,
        "nonce": nonce,
        "target_nonce": target_nonce,
        "reason": reason,
    }


def _edit_msg_text_op(
    *,
    at: str,
    clock: int,
    nonce: str,
    target_nonce: str,
    new_text: str,
    reason: str = "wording fix",
    by: str = "agent",
    actor: str = "test_agent",
) -> dict[str, Any]:
    return {
        "op": "edit-msg-text",
        "at": at,
        "by": by,
        "actor": actor,
        "clock": clock,
        "nonce": nonce,
        "target_nonce": target_nonce,
        "new_text": new_text,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Window tracking
# ---------------------------------------------------------------------------


class TestEditModeWindows:
    def test_no_edit_mode_op_filters_all_message_ops(self) -> None:
        """delete-msg without any edit-mode-on window is filtered (no-op)."""
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
            _delete_msg_op(
                at="2026-06-03T00:00:20Z", clock=3, nonce="bbbb",
                target_nonce="aaaa",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        assert len(item.discussion) == 1
        assert item.discussion[0].is_tombstoned is False

    def test_open_window_authorizes_delete_msg(self) -> None:
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
            _edit_mode_on_op(at="2026-06-03T00:30:00Z", clock=3, nonce="onnn"),
            _delete_msg_op(
                at="2026-06-03T00:30:05Z", clock=4, nonce="bbbb",
                target_nonce="aaaa",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        assert len(item.discussion) == 1
        assert item.discussion[0].is_tombstoned is True

    def test_window_auto_expires_at_ttl(self) -> None:
        """A window with ttl=60s closes at start+60s; ops after expiry filtered."""
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
            _edit_mode_on_op(
                at="2026-06-03T00:30:00Z", clock=3, nonce="onnn",
                ttl_seconds=60,
            ),
            # 61 seconds later — outside the window
            _delete_msg_op(
                at="2026-06-03T00:31:01Z", clock=4, nonce="bbbb",
                target_nonce="aaaa",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        assert item.discussion[0].is_tombstoned is False

    def test_edit_mode_off_terminates_window_early(self) -> None:
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
            _edit_mode_on_op(
                at="2026-06-03T00:30:00Z", clock=3, nonce="onnn",
                ttl_seconds=1800,
            ),
            _edit_mode_off_op(at="2026-06-03T00:30:10Z", clock=4, nonce="offf"),
            # 15s after window opened, 5s after it closed
            _delete_msg_op(
                at="2026-06-03T00:30:15Z", clock=5, nonce="bbbb",
                target_nonce="aaaa",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        assert item.discussion[0].is_tombstoned is False

    def test_new_edit_mode_on_replaces_prior_window(self) -> None:
        """Per design decision #13: overlapping windows replace, don't stack."""
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
            _discuss_op(at="2026-06-03T00:00:20Z", clock=3, nonce="bbbb"),
            # Open with ttl=3600s → would normally close at 01:30:00
            _edit_mode_on_op(
                at="2026-06-03T00:30:00Z", clock=4, nonce="ox01",
                ttl_seconds=3600,
            ),
            # Replace with new window at clock=5
            _edit_mode_on_op(
                at="2026-06-03T00:35:00Z", clock=5, nonce="ox02",
                ttl_seconds=60,  # tighter window: closes at 00:36:00
            ),
            # Inside the new window
            _delete_msg_op(
                at="2026-06-03T00:35:30Z", clock=6, nonce="d001",
                target_nonce="aaaa",
            ),
            # AFTER new window closes (60s ttl), but BEFORE old window would have
            _delete_msg_op(
                at="2026-06-03T00:40:00Z", clock=7, nonce="d002",
                target_nonce="bbbb",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        # aaaa should be tombstoned (inside new window), bbbb should NOT be
        # (outside replacement window — original window was preempted)
        a = next(d for d in item.discussion if d.nonce == "aaaa")
        b = next(d for d in item.discussion if d.nonce == "bbbb")
        assert a.is_tombstoned is True
        assert b.is_tombstoned is False

    def test_edit_mode_on_requires_by_human(self) -> None:
        """An agent-issued edit-mode-on op MUST be ignored at compile."""
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
            _edit_mode_on_op(
                at="2026-06-03T00:30:00Z", clock=3, nonce="onnn",
                by="agent", actor="test_agent",
            ),
            _delete_msg_op(
                at="2026-06-03T00:30:05Z", clock=4, nonce="d001",
                target_nonce="aaaa",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        # Without a valid window, the delete-msg must be filtered.
        assert item.discussion[0].is_tombstoned is False

    def test_compile_ignores_agent_issued_edit_mode_off(self) -> None:
        """An agent-issued edit-mode-off op MUST be ignored at compile."""
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
            _edit_mode_on_op(
                at="2026-06-03T00:30:00Z", clock=3, nonce="onnn",
            ),
            # Forged agent-issued off op — must not close the window
            _edit_mode_off_op(
                at="2026-06-03T00:30:05Z", clock=4, nonce="offa",
                by="agent", actor="test_agent",
            ),
            # delete-msg AFTER the forged off op — should still take effect
            # because the off was ignored.
            _delete_msg_op(
                at="2026-06-03T00:30:10Z", clock=5, nonce="d001",
                target_nonce="aaaa",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        assert item.discussion[0].is_tombstoned is True

    def test_window_is_half_open_start_inclusive_end_exclusive(self) -> None:
        """Window is [start, start+ttl): op at exactly start is in; at end is out."""
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
            _discuss_op(at="2026-06-03T00:00:20Z", clock=3, nonce="bbbb"),
            _edit_mode_on_op(
                at="2026-06-03T00:30:00Z", clock=4, nonce="onnn",
                ttl_seconds=60,
            ),
            # at == start: inside
            _delete_msg_op(
                at="2026-06-03T00:30:00Z", clock=5, nonce="d001",
                target_nonce="aaaa",
            ),
            # at == start+ttl: outside (half-open)
            _delete_msg_op(
                at="2026-06-03T00:31:00Z", clock=6, nonce="d002",
                target_nonce="bbbb",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        a = next(d for d in item.discussion if d.nonce == "aaaa")
        b = next(d for d in item.discussion if d.nonce == "bbbb")
        assert a.is_tombstoned is True
        assert b.is_tombstoned is False


# ---------------------------------------------------------------------------
# delete-msg / undelete-msg semantics
# ---------------------------------------------------------------------------


class TestDeleteUndeleteMsg:
    def test_undelete_after_delete_restores(self) -> None:
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
            _edit_mode_on_op(at="2026-06-03T00:30:00Z", clock=3, nonce="onnn"),
            _delete_msg_op(
                at="2026-06-03T00:30:05Z", clock=4, nonce="d001",
                target_nonce="aaaa",
            ),
            _undelete_msg_op(
                at="2026-06-03T00:30:10Z", clock=5, nonce="u001",
                target_nonce="aaaa",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        assert item.discussion[0].is_tombstoned is False

    def test_delete_after_undelete_re_tombstones(self) -> None:
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
            _edit_mode_on_op(at="2026-06-03T00:30:00Z", clock=3, nonce="onnn"),
            _delete_msg_op(
                at="2026-06-03T00:30:05Z", clock=4, nonce="d001",
                target_nonce="aaaa",
            ),
            _undelete_msg_op(
                at="2026-06-03T00:30:10Z", clock=5, nonce="u001",
                target_nonce="aaaa",
            ),
            _delete_msg_op(
                at="2026-06-03T00:30:15Z", clock=6, nonce="d002",
                target_nonce="aaaa",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        assert item.discussion[0].is_tombstoned is True

    def test_delete_unknown_target_nonce_ignored(self) -> None:
        """delete-msg whose target_nonce doesn't match any discuss op is a no-op."""
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
            _edit_mode_on_op(at="2026-06-03T00:30:00Z", clock=3, nonce="onnn"),
            _delete_msg_op(
                at="2026-06-03T00:30:05Z", clock=4, nonce="d001",
                target_nonce="ffff",  # No such message
            ),
        ]
        item = compile_ops(ops, "INV-test")
        assert len(item.discussion) == 1
        assert item.discussion[0].is_tombstoned is False


# ---------------------------------------------------------------------------
# edit-msg-text semantics
# ---------------------------------------------------------------------------


class TestEditMsgText:
    def test_edit_msg_text_replaces_message_text(self) -> None:
        ops = [
            _create_op(clock=1),
            _discuss_op(
                at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa",
                message="original text",
            ),
            _edit_mode_on_op(at="2026-06-03T00:30:00Z", clock=3, nonce="onnn"),
            _edit_msg_text_op(
                at="2026-06-03T00:30:05Z", clock=4, nonce="e001",
                target_nonce="aaaa",
                new_text="edited text",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        assert item.discussion[0].message == "edited text"
        assert item.discussion[0].nonce == "aaaa"  # preserved

    def test_edit_chain_latest_wins(self) -> None:
        ops = [
            _create_op(clock=1),
            _discuss_op(
                at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa",
                message="v0",
            ),
            _edit_mode_on_op(at="2026-06-03T00:30:00Z", clock=3, nonce="onnn"),
            _edit_msg_text_op(
                at="2026-06-03T00:30:05Z", clock=4, nonce="e001",
                target_nonce="aaaa", new_text="v1",
            ),
            _edit_msg_text_op(
                at="2026-06-03T00:30:10Z", clock=5, nonce="e002",
                target_nonce="aaaa", new_text="v2",
            ),
            _edit_msg_text_op(
                at="2026-06-03T00:30:15Z", clock=6, nonce="e003",
                target_nonce="aaaa", new_text="v3",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        entry = item.discussion[0]
        assert entry.message == "v3"
        # History is oldest first; current message NOT in history
        assert entry.edit_history == ["v0", "v1", "v2"]

    def test_edit_outside_window_ignored(self) -> None:
        ops = [
            _create_op(clock=1),
            _discuss_op(
                at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa",
                message="original",
            ),
            _edit_msg_text_op(
                at="2026-06-03T00:30:05Z", clock=3, nonce="e001",
                target_nonce="aaaa", new_text="hijacked",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        assert item.discussion[0].message == "original"
        assert item.discussion[0].edit_history == []

    def test_edit_on_tombstoned_msg_is_rejected_at_compile(self) -> None:
        """Per design decision #11: edit on tombstoned must be rejected.

        At the compile layer this means: a same-window edit AFTER a delete on
        the same target is dropped (the write-time reject lives in store.py;
        the compile-time enforcement is the safety net).
        """
        ops = [
            _create_op(clock=1),
            _discuss_op(
                at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa",
                message="original",
            ),
            _edit_mode_on_op(at="2026-06-03T00:30:00Z", clock=3, nonce="onnn"),
            _delete_msg_op(
                at="2026-06-03T00:30:05Z", clock=4, nonce="d001",
                target_nonce="aaaa",
            ),
            _edit_msg_text_op(
                at="2026-06-03T00:30:10Z", clock=5, nonce="e001",
                target_nonce="aaaa", new_text="should be dropped",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        assert item.discussion[0].message == "original"
        assert item.discussion[0].is_tombstoned is True


# ---------------------------------------------------------------------------
# Cap enforcement
# ---------------------------------------------------------------------------


class TestCapEnforcement:
    def test_cap_blocks_extra_delete_ops(self) -> None:
        """cap_max=2: only first 2 delete-msg ops in window take effect."""
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:01Z", clock=2, nonce="aaaa"),
            _discuss_op(at="2026-06-03T00:00:02Z", clock=3, nonce="bbbb"),
            _discuss_op(at="2026-06-03T00:00:03Z", clock=4, nonce="cccc"),
            _edit_mode_on_op(
                at="2026-06-03T00:30:00Z", clock=5, nonce="onnn",
                cap_max=2,
            ),
            _delete_msg_op(
                at="2026-06-03T00:30:01Z", clock=6, nonce="d001",
                target_nonce="aaaa",
            ),
            _delete_msg_op(
                at="2026-06-03T00:30:02Z", clock=7, nonce="d002",
                target_nonce="bbbb",
            ),
            _delete_msg_op(
                at="2026-06-03T00:30:03Z", clock=8, nonce="d003",
                target_nonce="cccc",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        by_nonce = {d.nonce: d for d in item.discussion}
        assert by_nonce["aaaa"].is_tombstoned is True
        assert by_nonce["bbbb"].is_tombstoned is True
        # Third op blocked by cap
        assert by_nonce["cccc"].is_tombstoned is False

    def test_undelete_does_not_count_toward_cap(self) -> None:
        """cap_max=2 with 2 successful deletes plus interleaved undeletes:
        all 2 deletes land because undeletes do not consume cap.

        If undeletes counted, the second delete would be blocked.
        """
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:01Z", clock=2, nonce="aaaa"),
            _discuss_op(at="2026-06-03T00:00:02Z", clock=3, nonce="bbbb"),
            _edit_mode_on_op(
                at="2026-06-03T00:30:00Z", clock=4, nonce="onnn",
                cap_max=2,
            ),
            _delete_msg_op(
                at="2026-06-03T00:30:01Z", clock=5, nonce="d001",
                target_nonce="aaaa",
            ),
            # Five undelete ops on a non-existent target — would burn cap
            # if they were counted.
            _undelete_msg_op(
                at="2026-06-03T00:30:02Z", clock=6, nonce="u001",
                target_nonce="aaaa",
            ),
            _undelete_msg_op(
                at="2026-06-03T00:30:03Z", clock=7, nonce="u002",
                target_nonce="aaaa",
            ),
            # Re-delete aaaa
            _delete_msg_op(
                at="2026-06-03T00:30:04Z", clock=8, nonce="d002",
                target_nonce="aaaa",
            ),
            # Cap=2 should be reached here (d001 + d002 only — undeletes are free)
            _delete_msg_op(
                at="2026-06-03T00:30:05Z", clock=9, nonce="d003",
                target_nonce="bbbb",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        by_nonce = {d.nonce: d for d in item.discussion}
        # d001 + d002 took the 2 cap slots; bbbb's delete was blocked.
        assert by_nonce["aaaa"].is_tombstoned is True
        assert by_nonce["bbbb"].is_tombstoned is False

    def test_cap_blocks_second_edit_after_first(self) -> None:
        """cap_max=1: first edit lands; second edit (different target) blocked."""
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:01Z", clock=2, nonce="aaaa",
                        message="a-orig"),
            _discuss_op(at="2026-06-03T00:00:02Z", clock=3, nonce="bbbb",
                        message="b-orig"),
            _edit_mode_on_op(
                at="2026-06-03T00:30:00Z", clock=4, nonce="onnn",
                cap_max=1,
            ),
            _edit_msg_text_op(
                at="2026-06-03T00:30:01Z", clock=5, nonce="e001",
                target_nonce="aaaa", new_text="a-edit",
            ),
            _edit_msg_text_op(
                at="2026-06-03T00:30:02Z", clock=6, nonce="e002",
                target_nonce="bbbb", new_text="b-edit",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        by_nonce = {d.nonce: d for d in item.discussion}
        assert by_nonce["aaaa"].message == "a-edit"
        # Second edit blocked by cap
        assert by_nonce["bbbb"].message == "b-orig"

    def test_edit_counts_toward_cap(self) -> None:
        """edit-msg-text also counts toward cap (per design decision #6)."""
        ops = [
            _create_op(clock=1),
            _discuss_op(
                at="2026-06-03T00:00:01Z", clock=2, nonce="aaaa",
                message="orig",
            ),
            _discuss_op(at="2026-06-03T00:00:02Z", clock=3, nonce="bbbb"),
            _edit_mode_on_op(
                at="2026-06-03T00:30:00Z", clock=4, nonce="onnn",
                cap_max=1,
            ),
            _edit_msg_text_op(
                at="2026-06-03T00:30:01Z", clock=5, nonce="e001",
                target_nonce="aaaa", new_text="edited",
            ),
            # Cap eaten by the edit; this delete is filtered.
            _delete_msg_op(
                at="2026-06-03T00:30:02Z", clock=6, nonce="d001",
                target_nonce="bbbb",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        by_nonce = {d.nonce: d for d in item.discussion}
        assert by_nonce["aaaa"].message == "edited"
        assert by_nonce["bbbb"].is_tombstoned is False

    def test_cap_resets_each_window(self) -> None:
        """Each window has its own cap counter."""
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:01Z", clock=2, nonce="aaaa"),
            _discuss_op(at="2026-06-03T00:00:02Z", clock=3, nonce="bbbb"),
            # Window 1 (cap=1)
            _edit_mode_on_op(
                at="2026-06-03T00:30:00Z", clock=4, nonce="on01",
                cap_max=1, ttl_seconds=60,
            ),
            _delete_msg_op(
                at="2026-06-03T00:30:01Z", clock=5, nonce="d001",
                target_nonce="aaaa",
            ),
            # Window 2, fresh cap=1
            _edit_mode_on_op(
                at="2026-06-03T01:00:00Z", clock=6, nonce="on02",
                cap_max=1, ttl_seconds=60,
            ),
            _delete_msg_op(
                at="2026-06-03T01:00:01Z", clock=7, nonce="d002",
                target_nonce="bbbb",
            ),
        ]
        item = compile_ops(ops, "INV-test")
        by_nonce = {d.nonce: d for d in item.discussion}
        assert by_nonce["aaaa"].is_tombstoned is True
        assert by_nonce["bbbb"].is_tombstoned is True


# ---------------------------------------------------------------------------
# DiscussionEntry shape: nonce propagation, edit_history default
# ---------------------------------------------------------------------------


class TestOsPermGate:
    """WI-zonur phase 2: OS-permission gate around the edit-mode log.

    The agent's CLI calls run as a process whose OS uid maps to a username
    matching the configured agent patterns. The phase-1 `by:human` field
    check is forgeable by an agent that edits the YAML file directly.
    Phase 2's defense-in-depth: the log file's OWNER UID is stat()'d, mapped
    to a username, and rejected if that username matches an agent pattern.
    Files owned by agents are filtered (whole-file) at read time, never
    removed from disk.
    """

    def test_log_lives_in_subdir(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        """Log path moved into `.edit-mode/edit-mode.ops` subdir."""
        store = _make_store(tmp_path)
        store.edit_mode_on(ttl_seconds=60)
        p = store.edit_mode_log_path()
        assert p.parent.name == ".edit-mode"
        assert p.name == "edit-mode.ops"
        assert p.exists()
        assert p.parent.is_dir()

    def test_agent_owned_log_is_filtered_at_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        mock_human_uid: None,
    ) -> None:
        """A log file whose owner uid maps to an agent username is dropped."""
        import pwd

        store = _make_store(tmp_path)
        store.edit_mode_on(ttl_seconds=60)
        log_path = store.edit_mode_log_path()
        assert log_path.exists()

        owning_uid = log_path.stat().st_uid

        class _FakeAgentEntry:
            pw_name = "spoofed_agent"
            pw_uid = owning_uid
            pw_gid = owning_uid
            pw_gecos = ""
            pw_dir = "/tmp"
            pw_shell = "/bin/false"

        original_getpwuid = pwd.getpwuid

        def fake_getpwuid(uid: int) -> Any:
            if uid == owning_uid:
                return _FakeAgentEntry()
            return original_getpwuid(uid)

        monkeypatch.setattr(pwd, "getpwuid", fake_getpwuid)

        ops = store._read_edit_mode_ops()
        assert ops == []

    def test_human_owned_log_returns_ops(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        store.edit_mode_on(ttl_seconds=60)
        ops = store._read_edit_mode_ops()
        assert len(ops) == 1
        assert ops[0]["op"] == "edit-mode-on"

    def test_subdir_mode_is_0755_or_0775(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        store.edit_mode_on(ttl_seconds=60)
        mode = store.edit_mode_log_path().parent.stat().st_mode & 0o777
        # 0o775 is acceptable in shared-group setups (the global
        # `_ensure_dir_group_writable` helper sets g+w).
        assert mode in (0o755, 0o775), f"unexpected dir mode {oct(mode)}"

    def test_missing_subdir_returns_empty_ops(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        ops = store._read_edit_mode_ops()
        assert ops == []

    def test_agent_owned_log_disables_delete_msg(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        mock_human_uid: None,
    ) -> None:
        import pwd

        store = _make_store(tmp_path)
        iid, mn = _add_item_with_discussion(store, "test item")
        store.edit_mode_on(ttl_seconds=60)

        owning_uid = store.edit_mode_log_path().stat().st_uid

        class _FakeAgentEntry:
            pw_name = "spoofed_agent"
            pw_uid = owning_uid
            pw_gid = owning_uid
            pw_gecos = ""
            pw_dir = "/tmp"
            pw_shell = "/bin/false"

        original_getpwuid = pwd.getpwuid

        def fake_getpwuid(uid: int) -> Any:
            if uid == owning_uid:
                return _FakeAgentEntry()
            return original_getpwuid(uid)

        monkeypatch.setattr(pwd, "getpwuid", fake_getpwuid)

        with pytest.raises(EditModeNotActiveError):
            store.delete_msg(iid, mn, "test")


class TestCompileAllCachedEditModeFallback:
    """Coverage for the cache-bypass branch when edit-mode log is present."""

    def test_cached_compile_bypasses_cache_when_edit_mode_log_exists(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        # Add an item with discussion
        iid, mn = _add_item_with_discussion(store, "test item")
        # Open + use edit-mode (creates the log)
        store.edit_mode_on(ttl_seconds=60)
        store.delete_msg(iid, mn, "extracted")

        # Build a minimal cache mock that should NEVER be consulted because
        # the edit-mode log exists. (If the code went down the cached branch,
        # this mock would have raised.)
        class _CacheMustNotBeUsed:
            def get_compiled(self, item_id: str) -> None:  # pragma: no cover
                raise AssertionError(
                    "cache.get_compiled called despite edit-mode log present"
                )

            def upsert(self, *a: Any, **k: Any) -> None:  # pragma: no cover
                raise AssertionError(
                    "cache.upsert called despite edit-mode log present"
                )

        items = store._compile_all_cached(_CacheMustNotBeUsed())
        # tombstone-state must be present despite cache being bypassed
        assert any(
            d.is_tombstoned
            for it in items for d in it.discussion if d.nonce == mn
        )


class TestFoldAuditMaterializationSmoke:
    """WI-zonur step 6 acceptance: one fold-audit-shaped extraction end-to-end.

    Mirrors the fold-correction workflow that motivated the WI: a parent row
    has a long discussion entry that bundles several reconstructed-row
    proposals; the agent (1) opens edit-mode via the human, (2) creates new
    discrete tracker items for each proposal, (3) deletes the original
    bundled-text message with `extracted-to <new-id>` reasons. After the
    window closes, `tracker show` on the parent should suppress the
    extracted text and the new items should be retrievable independently.
    """

    def test_extract_then_delete_message_e2e(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)

        # Parent invariant with one long fold-bundled discussion entry.
        parent_id = store.add(
            kind="invariant",
            title="Parent invariant carrying a fold-bundled proposal",
            description="root",
            fields={"statement": "x", "root_cause": "y"},
        )
        store.discuss(
            parent_id,
            "Pass-40 F40.A1+A2+A3 batch: three proposals bundled — "
            "(1) tighten X, (2) repair Y, (3) re-derive Z.",
        )
        parent_before = store.get(parent_id)
        assert len(parent_before.discussion) == 1
        bundled_nonce = parent_before.discussion[0].nonce

        # Step 1: human opens edit-mode.
        store.edit_mode_on(ttl_seconds=60, cap_max=10)

        # Step 2: agent creates the three new tracker items mirroring the
        # proposals (the materialization moves goes through normal add()).
        new_ids = [
            store.add(
                kind="invariant",
                title=f"Materialized proposal {n}",
                description=f"extracted from {parent_id} pass-40 F40.A{n}",
                fields={
                    "statement": f"proposal {n}",
                    "root_cause": f"root {n}",
                },
            )
            for n in (1, 2, 3)
        ]

        # Step 3: agent tombstones the parent's bundled-text message with
        # an "extracted-to" reason.
        reason = (
            f"extracted to {new_ids[0][:14]} + 2 more (F40.A1/A2/A3)"
        )[:100]
        store.delete_msg(parent_id, bundled_nonce, reason)

        # Verify: parent's only message is tombstoned, and the three new
        # items exist and are individually retrievable.
        parent_after = store.get(parent_id)
        assert parent_after.discussion[0].is_tombstoned is True
        assert parent_after.discussion[0].message.startswith(
            "Pass-40 F40.A1+A2+A3",
        )
        for new_id in new_ids:
            it = store.get(new_id)
            assert it.title.startswith("Materialized proposal")

        # And: rendered show output suppresses the tombstoned entry but
        # surfaces the footer.
        from hypergumbo_tracker.cli import _format_item_full

        rendered = _format_item_full(parent_after)
        assert "Pass-40 F40" not in rendered
        assert "(not shown: 1 deleted messages)" in rendered

        # With --include-deleted, the tombstone re-appears with marker.
        rendered_all = _format_item_full(parent_after, include_deleted=True)
        assert "[DELETED]" in rendered_all
        assert "Pass-40 F40" in rendered_all

        # Close the window; subsequent delete-msg attempts must fail.
        store.edit_mode_off()
        with pytest.raises(EditModeNotActiveError):
            store.delete_msg(parent_id, bundled_nonce, "second attempt")


class TestParseIsoTimestamp:
    """Coverage for the shared timestamp helper used by edit-mode windows."""

    def test_empty_string_returns_none(self) -> None:
        from hypergumbo_tracker.store import _parse_iso_timestamp

        assert _parse_iso_timestamp("") is None


class TestDiscussionEntryShape:
    def test_discuss_op_populates_nonce(self) -> None:
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
        ]
        item = compile_ops(ops, "INV-test")
        assert item.discussion[0].nonce == "aaaa"

    def test_discuss_op_default_not_tombstoned_empty_history(self) -> None:
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
        ]
        item = compile_ops(ops, "INV-test")
        entry = item.discussion[0]
        assert entry.is_tombstoned is False
        assert entry.edit_history == []

    def test_summarize_op_populates_nonce(self) -> None:
        ops = [
            _create_op(clock=1),
            _discuss_op(at="2026-06-03T00:00:10Z", clock=2, nonce="aaaa"),
            {
                "op": "discuss_summarize",
                "at": "2026-06-03T00:00:20Z",
                "by": "human", "actor": "jgstern",
                "clock": 3, "nonce": "summ",
                "message": "summarized",
            },
        ]
        item = compile_ops(ops, "INV-test")
        assert len(item.discussion) == 1
        assert item.discussion[0].is_summary is True
        assert item.discussion[0].nonce == "summ"


# ---------------------------------------------------------------------------
# Store API: edit_mode_on / edit_mode_off / delete_msg / undelete_msg /
# edit_msg_text — exercised through the on-disk file path.
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> Store:
    """Build a Store on a fresh temporary tracker root."""
    from helpers import make_test_config

    ops_dir = tmp_path / ".ops"
    ops_dir.mkdir()
    return Store(ops_dir, make_test_config())


def _add_item_with_discussion(
    store: Store, title: str, message: str = "first msg",
) -> tuple[str, str]:
    """Add an item with one discussion entry; return (item_id, msg_nonce)."""
    iid = store.add(
        kind="work_item",
        title=title,
        description="",
    )
    store.discuss(iid, message)
    item = store.get(iid)
    return iid, item.discussion[0].nonce


class TestStoreEditMode:
    def test_edit_mode_on_human_only(
        self, tmp_path: Path, mock_agent_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(HumanAuthorityError):
            store.edit_mode_on()

    def test_edit_mode_off_human_only(
        self, tmp_path: Path, mock_agent_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(HumanAuthorityError):
            store.edit_mode_off()

    def test_edit_mode_on_validates_ttl_and_cap(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(ValueError):
            store.edit_mode_on(ttl_seconds=0)
        with pytest.raises(ValueError):
            store.edit_mode_on(ttl_seconds=3601)
        with pytest.raises(ValueError):
            store.edit_mode_on(cap_max=0)
        with pytest.raises(ValueError):
            store.edit_mode_on(cap_max=501)

    def test_edit_mode_status_off_by_default(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        status = store.edit_mode_status()
        assert status["on"] is False
        assert status["remaining_s"] == 0
        assert status["ops_used"] == 0

    def test_edit_mode_status_on_after_open(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        store.edit_mode_on(ttl_seconds=60, cap_max=10)
        status = store.edit_mode_status()
        assert status["on"] is True
        assert status["ttl_seconds"] == 60
        assert status["cap_max"] == 10
        assert 0 <= status["remaining_s"] <= 60
        assert status["ops_used"] == 0

    def test_delete_msg_requires_edit_mode_active(
        self, tmp_path: Path, mock_agent_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        iid, mn = _add_item_with_discussion(store, "test item")
        with pytest.raises(EditModeNotActiveError):
            store.delete_msg(iid, mn, "test")

    def test_delete_msg_end_to_end(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        iid, mn = _add_item_with_discussion(store, "test item")
        store.edit_mode_on(ttl_seconds=60)
        store.delete_msg(iid, mn, "extracted to WI-foo")
        item = store.get(iid)
        assert item.discussion[0].is_tombstoned is True

    def test_undelete_msg_end_to_end(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        iid, mn = _add_item_with_discussion(store, "test item")
        store.edit_mode_on(ttl_seconds=60)
        store.delete_msg(iid, mn, "extracted")
        store.undelete_msg(iid, mn, "reverting")
        item = store.get(iid)
        assert item.discussion[0].is_tombstoned is False

    def test_edit_msg_text_end_to_end(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        iid, mn = _add_item_with_discussion(store, "test item", "original")
        store.edit_mode_on(ttl_seconds=60)
        store.edit_msg_text(iid, mn, "edited!", "fix wording")
        item = store.get(iid)
        assert item.discussion[0].message == "edited!"
        assert item.discussion[0].edit_history == ["original"]

    def test_edit_msg_text_rejected_on_tombstoned(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        iid, mn = _add_item_with_discussion(store, "test item", "original")
        store.edit_mode_on(ttl_seconds=60)
        store.delete_msg(iid, mn, "extracted")
        with pytest.raises(ValueError, match="tombstoned"):
            store.edit_msg_text(iid, mn, "edit", "fix wording")

    def test_msg_op_validates_reason(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        iid, mn = _add_item_with_discussion(store, "test item")
        store.edit_mode_on(ttl_seconds=60)
        with pytest.raises(ValueError, match="reason"):
            store.delete_msg(iid, mn, "")
        with pytest.raises(ValueError, match="reason"):
            store.delete_msg(iid, mn, "x" * 101)

    def test_msg_op_validates_target_nonce_format(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        iid, _ = _add_item_with_discussion(store, "test item")
        store.edit_mode_on(ttl_seconds=60)
        with pytest.raises(ValueError, match="target_nonce"):
            store.delete_msg(iid, "zzz", "test")
        with pytest.raises(ValueError, match="target_nonce"):
            store.delete_msg(iid, "ZZZZ", "test")  # uppercase rejected
        with pytest.raises(ValueError, match="target_nonce"):
            store.delete_msg(iid, "12345", "test")  # too long

    def test_msg_op_rejects_unknown_target(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        iid, _ = _add_item_with_discussion(store, "test item")
        store.edit_mode_on(ttl_seconds=60)
        with pytest.raises(ValueError, match="No discussion message"):
            store.delete_msg(iid, "ffff", "test")

    def test_msg_op_rejects_unknown_item(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        store.edit_mode_on(ttl_seconds=60)
        with pytest.raises(ItemNotFoundError):
            store.delete_msg("WI-doesnotexist", "ffff", "test")

    def test_edit_mode_status_counts_ops_used(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        iid, mn = _add_item_with_discussion(store, "test item")
        store.edit_mode_on(ttl_seconds=60, cap_max=10)
        store.delete_msg(iid, mn, "extracted")
        status = store.edit_mode_status()
        assert status["on"] is True
        assert status["ops_used"] == 1

    def test_edit_mode_off_then_op_fails(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        store = _make_store(tmp_path)
        iid, mn = _add_item_with_discussion(store, "test item")
        store.edit_mode_on(ttl_seconds=60)
        store.edit_mode_off()
        with pytest.raises(EditModeNotActiveError):
            store.delete_msg(iid, mn, "test")

    def test_edit_mode_status_ignores_agent_issued_off(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        """Agent-issued edit-mode-off ops must be ignored (defense-in-depth)."""
        from hypergumbo_tracker.store import _serialize_op

        store = _make_store(tmp_path)
        store.edit_mode_on(ttl_seconds=600)
        # Forge an agent-issued edit-mode-off op directly into the log.
        log = store.edit_mode_log_path()
        op = {
            "op": "edit-mode-off",
            "at": "2026-12-31T23:59:59.000000Z",
            "by": "agent",
            "actor": "test_agent",
            "clock": 0,
            "nonce": "0000",
        }
        log.write_text(log.read_text() + _serialize_op(op) + "\n")
        status = store.edit_mode_status()
        # On must still be True — agent's off-op was ignored
        assert status["on"] is True

    def test_edit_mode_status_auto_expires_after_ttl(
        self, tmp_path: Path, mock_human_uid: None,
    ) -> None:
        """Window past its TTL auto-expires; status reads as OFF."""
        from hypergumbo_tracker.store import _serialize_op

        store = _make_store(tmp_path)
        # Hand-craft an edit-mode-on op that opened deep in the past with a
        # short TTL — `now` is way past `at + ttl` so the auto-expire branch
        # fires.
        store._ensure_edit_mode_dir()
        log = store.edit_mode_log_path()
        op = {
            "op": "edit-mode-on",
            "at": "2020-01-01T00:00:00.000000Z",
            "by": "human",
            "actor": "jgstern",
            "clock": 0,
            "nonce": "0001",
            "ttl_seconds": 60,
            "cap_max": 10,
        }
        log.write_text(_serialize_op(op) + "\n")
        status = store.edit_mode_status()
        assert status["on"] is False
        assert status["remaining_s"] == 0
