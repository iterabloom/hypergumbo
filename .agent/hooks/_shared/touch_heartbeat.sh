#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# touch_heartbeat.sh — shared per-session heartbeat helper (WI-sipov).
#
# Touches ~/hypergumbo_lab_notebook/agent-supervisor/<session>.heartbeat.
# The future agent-supervisor daemon (WI-rofuv / WI-razub) reads this
# file's mtime for telemetry ONLY — the daemon's load-bearing "is this
# session working?" signal is the tmux pane-byte delta, not the
# heartbeat. So this helper's contract is:
#
#   1. Safe to call from any vendor hook, any long-running wrapper, at
#      any point in session lifecycle.
#   2. Never blocks, never errors out of its caller: if HOME is
#      unwritable, the directory can't be created, or the touch fails,
#      the helper silently returns 0 so the caller keeps going.
#   3. No-op when session_id is empty (session identity not yet
#      resolved by the caller — per-turn hooks bail out themselves in
#      that case, but the helper is robust if called without a sid).
#
# Usage:
#   source "$SCRIPT_DIR/../_shared/touch_heartbeat.sh"
#   touch_heartbeat "$SESSION_ID"
#
# Optional override for tests:
#   HEARTBEAT_DIR=<path> touch_heartbeat "$SESSION_ID"

touch_heartbeat() {
    local sid="${1:-}"
    if [[ -z "$sid" ]]; then
        return 0
    fi
    local dir="${HEARTBEAT_DIR:-$HOME/hypergumbo_lab_notebook/agent-supervisor}"
    # Redirect mkdir's errors so we don't polute the hook's stderr with
    # permission-denied noise when HOME is read-only (CI sandboxes, etc.).
    mkdir -p "$dir" 2>/dev/null || return 0
    # touch may still fail (race, filesystem full, quota) — swallow it.
    touch "$dir/${sid}.heartbeat" 2>/dev/null || return 0
    return 0
}
