#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Multi-vendor interjection normalization for the transcript sync pipeline.

Detects user interjections (messages sent while an AI agent is busy) across
multiple vendor transcript formats and emits a unified
``normalized_user_interjection`` event alongside the original vendor-specific
rows.

Supported vendors and their detection characteristics:

- **Claude Code** (confidence 1.0): Explicit ``queue-operation`` events with
  ``operation: enqueue``. These are first-class transcript events that Claude
  Code writes when the user queues a message during a tool call. Detection is
  exact — no inference needed.

- **Codex CLI** (confidence 0.9): ``turn_aborted`` with ``reason: interrupted``
  immediately followed by a ``user_message``. The Esc-interrupt + new message
  pattern is reliable but blind to quietly-queued messages (``pending_input``
  is never serialized to the rollout JSONL). Known blind spot documented in
  WI-nadud.

- **OpenHands** (confidence 0.8): ``MessageObservation`` with ``source: user``
  arriving before a prior ``CmdRunAction``'s corresponding
  ``CmdOutputObservation``. Event-sourced persistence makes structural
  inference practical, but the action/observation pairing heuristic can
  miss concurrent actions or misattribute timing in edge cases.

Per WI-nadud: this module is the implementation of the normalized event
schema and vendor adapters. The schema was designed from two independent
research surveys of 12+ AI coding platforms.

Usage:
    from normalize_interjections import normalize_rows, detect_vendor
    result_rows, new_state = normalize_rows(rows, vendor="claude-code")
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Normalized event schema
# ---------------------------------------------------------------------------

def _make_normalized(
    *,
    vendor: str,
    timestamp: str,
    content: str,
    runtime_state_at_accept: str,
    delivery_semantics: str,
    queue_scope: str = "unknown",
    approval_effect: str = "none",
    source_kind: str,
    confidence: float,
    raw_refs: list[dict] | None = None,
) -> dict:
    """Construct a normalized_user_interjection event dict.

    All fields are validated at test time against the schema contract in
    test_normalize_interjections.py. This function is the single point of
    construction — no other code path should assemble the event dict
    directly.
    """
    return {
        "type": "normalized_user_interjection",
        "vendor": vendor,
        "timestamp": timestamp,
        "content": content,
        "runtime_state_at_accept": runtime_state_at_accept,
        "delivery_semantics": delivery_semantics,
        "queue_scope": queue_scope,
        "approval_effect": approval_effect,
        "observability": {
            "source_kind": source_kind,
            "confidence": confidence,
        },
        "raw_refs": raw_refs or [],
    }


# ---------------------------------------------------------------------------
# Vendor auto-detection
# ---------------------------------------------------------------------------

def detect_vendor(rows: list[dict]) -> str | None:
    """Auto-detect vendor from row content.

    Inspects up to all rows looking for vendor-specific markers:
    - Claude Code: any row with ``type: queue-operation``, or ``type: progress``
      with ``data.type: bash_progress``, or ``type: user`` (Claude Code's
      standard message type).
    - Codex CLI: any row with ``type: turn_aborted``, ``type: function_call``,
      ``type: user_message``, or ``type: assistant_message``.
    - OpenHands: any row with ``event_type`` field (event-sourced format).

    Returns the vendor string or None if unrecognizable.
    """
    for row in rows:
        row_type = row.get("type", "")

        # Claude Code markers
        if row_type == "queue-operation":
            return "claude-code"
        if row_type == "progress":
            data = row.get("data")
            if isinstance(data, dict) and data.get("type") == "bash_progress":
                return "claude-code"

        # Codex CLI markers
        if row_type in ("turn_aborted", "function_call", "user_message", "assistant_message"):
            return "codex-cli"

        # OpenHands marker: event_type field
        if "event_type" in row:
            return "openhands"

    # Claude Code fallback: type=user/assistant/system without Codex/OH markers
    for row in rows:
        row_type = row.get("type", "")
        if row_type in ("user", "assistant", "system"):
            return "claude-code"

    return None


# ---------------------------------------------------------------------------
# Claude Code adapter
# ---------------------------------------------------------------------------

def _normalize_claude_code(
    rows: list[dict],
    state: dict,  # noqa: ARG001 — Claude Code needs no cross-batch state
) -> tuple[list[dict], dict]:
    """Normalize Claude Code queue-operation enqueue events.

    Emits a ``normalized_user_interjection`` immediately after each
    ``queue-operation`` row with ``operation: enqueue``. Other queue
    operations (dequeue, remove) are passed through without normalization.

    No cross-batch state needed — each enqueue is self-contained.
    """
    result: list[dict] = []
    for row in rows:
        result.append(row)

        if (
            row.get("type") == "queue-operation"
            and row.get("operation") == "enqueue"
        ):
            result.append(_make_normalized(
                vendor="claude-code",
                timestamp=row.get("timestamp", ""),
                content=row.get("content", ""),
                runtime_state_at_accept="busy",
                delivery_semantics="queued",
                queue_scope="after_current_turn",
                source_kind="explicit_event",
                confidence=1.0,
                raw_refs=[row],
            ))

    return result, state


# ---------------------------------------------------------------------------
# Codex CLI adapter
# ---------------------------------------------------------------------------

def _normalize_codex_cli(
    rows: list[dict],
    state: dict,
) -> tuple[list[dict], dict]:
    """Normalize Codex CLI interrupted-turn interjections.

    Detects the pattern: ``turn_aborted`` with ``reason: interrupted``
    immediately followed by ``user_message``. The turn_aborted may be at
    the end of a previous batch (carried in state).

    Known blind spot: quietly-queued messages (pending_input) are never
    serialized to the rollout JSONL, so this adapter cannot detect them.
    """
    # Cross-batch state: was the previous batch's last relevant event a
    # turn_aborted(interrupted)?
    prev_aborted = state.get("prev_interrupted_abort")
    result: list[dict] = []

    for row in rows:
        row_type = row.get("type", "")

        if row_type == "user_message" and prev_aborted is not None:
            # Pattern match: turn_aborted(interrupted) + user_message
            result.append(row)
            result.append(_make_normalized(
                vendor="codex-cli",
                timestamp=row.get("timestamp", ""),
                content=row.get("content", ""),
                runtime_state_at_accept="busy",
                delivery_semantics="interrupt",
                queue_scope="after_current_turn",
                source_kind="inferred_from_event_history",
                confidence=0.9,
                raw_refs=[prev_aborted, row],
            ))
            prev_aborted = None
        elif (
            row_type == "turn_aborted"
            and str(row.get("reason", "")).lower() == "interrupted"
        ):
            result.append(row)
            prev_aborted = row
        else:
            # Any non-user_message after a turn_aborted breaks the pattern
            if row_type != "turn_aborted":
                prev_aborted = None
            result.append(row)

    # Persist cross-batch state
    new_state = dict(state)
    new_state["prev_interrupted_abort"] = prev_aborted
    return result, new_state


# ---------------------------------------------------------------------------
# OpenHands adapter
# ---------------------------------------------------------------------------

def _normalize_openhands(
    rows: list[dict],
    state: dict,
) -> tuple[list[dict], dict]:
    """Normalize OpenHands interjections via event-sourced inference.

    Tracks unclosed actions (CmdRunAction without a corresponding
    CmdOutputObservation). A user ``MessageObservation`` arriving while
    any action is unclosed is classified as an interjection.

    Cross-batch state: the set of unclosed action IDs carries across
    batches.
    """
    # Unclosed action IDs (action_id -> action row)
    unclosed: dict[int, dict] = {}
    for aid_str, arow in state.get("unclosed_actions", {}).items():
        unclosed[int(aid_str)] = arow

    result: list[dict] = []

    for row in rows:
        event_type = row.get("event_type", "")

        # Track action open/close
        if event_type == "CmdRunAction":
            action_id = row.get("id")
            if action_id is not None:
                unclosed[action_id] = row

        elif event_type == "CmdOutputObservation":
            cause_id = row.get("cause")
            if cause_id is not None:
                unclosed.pop(cause_id, None)

        # Check for user interjection during open action
        if (
            event_type == "MessageObservation"
            and row.get("source") == "user"
            and unclosed
        ):
            # Pick the most recently opened action as the raw_ref
            latest_action = max(unclosed.values(), key=lambda a: a.get("id", 0))
            result.append(row)
            result.append(_make_normalized(
                vendor="openhands",
                timestamp=row.get("timestamp", ""),
                content=row.get("content", ""),
                runtime_state_at_accept="tool_running",
                delivery_semantics="unknown",
                queue_scope="unknown",
                source_kind="inferred_from_event_history",
                confidence=0.8,
                raw_refs=[latest_action, row],
            ))
        else:
            result.append(row)

    # Persist unclosed actions as serializable dict (str keys for JSON)
    new_state = dict(state)
    new_state["unclosed_actions"] = {str(k): v for k, v in unclosed.items()}
    return result, new_state


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_VENDOR_ADAPTERS = {
    "claude-code": _normalize_claude_code,
    "codex-cli": _normalize_codex_cli,
    "openhands": _normalize_openhands,
}


def normalize_rows(
    rows: list[dict],
    *,
    vendor: str | None = None,
    state: dict | None = None,
) -> tuple[list[dict], dict]:
    """Normalize interjection events in a batch of transcript rows.

    Args:
        rows: List of parsed JSON row dicts from the transcript.
        vendor: Vendor identifier (``"claude-code"``, ``"codex-cli"``,
            ``"openhands"``). If None, auto-detected from row content.
        state: Cross-batch normalization state from a previous call.
            Pass the state returned by the previous call to enable
            cross-batch pattern detection.

    Returns:
        Tuple of (result_rows, new_state). ``result_rows`` contains all
        original rows plus any synthetic ``normalized_user_interjection``
        events inserted immediately after the triggering row.
        ``new_state`` should be passed to the next call.
    """
    if state is None:
        state = {}

    if vendor is None:
        vendor = detect_vendor(rows)

    if vendor is None:
        # Unknown vendor — pass through unchanged
        return list(rows), state

    adapter = _VENDOR_ADAPTERS.get(vendor)
    if adapter is None:
        return list(rows), state

    return adapter(rows, state)
