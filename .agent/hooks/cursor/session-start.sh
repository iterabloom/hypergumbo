#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Cursor sessionStart hook adapter
# See ADR-0008 for governance protocol
#
# Detects stale/OFF autonomous mode and injects a prompt asking the
# agent to query the user for mode selection.
#
# Cursor: stdout is injected as context. JSON with additionalContext
# field is also supported.
#
# Cursor hooks are configured in .cursor/hooks.json (project-level)
# or ~/.cursor/hooks.json (user-level). Hooks receive JSON on stdin
# with conversation_id, generation_id, workspace_roots, hook_event_name.
#
# NOTE: sessionStart hooks are beta and have known bugs in some Cursor
# versions (rejected as "unknown hook type" in some builds). The
# transcript sync uses the state.vscdb SQLite database as the source.
#
# SINGLE-SESSION-PER-REPO ENFORCEMENT (ADR-0018 amendment / Option 2):
# Cursor's transcript backing store is a global SQLite database shared by
# all Cursor windows in all workspaces, so per-session isolation requires
# a SQLite extractor that fans out to per-conversation files. That work
# is deferred (tracker WI-rijoj). Until then, Cursor is restricted to a
# single concurrent session per repo: this script detects a live sibling
# Cursor watcher (cursor-singleton) at session start and aborts the
# transcript sync wiring with a clear stderr message. The Cursor session
# itself still launches normally — only the transcript sync side is gated.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Capture stdin (Cursor passes JSON input)
STDIN_JSON=$(cat)

# --- Shared logic (sets SESSION_START_MESSAGE, SESSION_START_NEEDS_PROMPT) ---
source "$SCRIPT_DIR/../_shared/session_start_logic.sh"
source "$SCRIPT_DIR/../_shared/session_id_helpers.sh"

SESSION_ID=$(extract_cursor_session_id "$STDIN_JSON")

# --- Sibling-session check ---
# If another Cursor session is already running with the cursor-singleton
# session id, refuse to launch a second watcher. The user can still use
# their second Cursor window — they just won't get transcript-sync
# injection in it until the first Cursor session ends.
SIBLING_PID_FILE="$REPO_ROOT/.agent/.transcript-sync.${SESSION_ID}.pid"
SIBLING_LIVE=false
if [[ -f "$SIBLING_PID_FILE" ]]; then
    SIBLING_PID=$(cat "$SIBLING_PID_FILE" 2>/dev/null || true)
    if [[ -n "$SIBLING_PID" ]] && kill -0 "$SIBLING_PID" 2>/dev/null; then
        SIBLING_LIVE=true
    fi
fi

if [[ "$SIBLING_LIVE" == "true" ]]; then
    cat >&2 <<MSG
[hypergumbo] Cursor sibling-session check: another Cursor session in this
repo already owns the transcript sync watcher (PID $SIBLING_PID). Cursor
is enforced single-session-per-repo because its transcript backing store
is a global SQLite database — see tracker WI-rijoj for the deferred
per-conversation extractor work. Skipping transcript sync launch for this
session. You can still use this Cursor window normally; it just won't get
playbook-injection feedback until the other Cursor session ends.
MSG
else
    # --- Transcript sync: find the Cursor state database ---
    # Cursor stores conversations in SQLite (state.vscdb). The watcher
    # monitors the database file for writes; the downstream hook
    # (on_transcript_change.sh) is responsible for extracting conversation
    # data from the SQLite format (e.g., via sqlite3 queries on the
    # cursorDiskKV table for composerData/bubbleId keys).
    #
    # Locations by platform:
    #   Linux:  ~/.config/Cursor/User/globalStorage/state.vscdb
    #   macOS:  ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb
    CURSOR_DB=""
    if [[ -f "$HOME/.config/Cursor/User/globalStorage/state.vscdb" ]]; then
        CURSOR_DB="$HOME/.config/Cursor/User/globalStorage/state.vscdb"
    elif [[ -f "$HOME/Library/Application Support/Cursor/User/globalStorage/state.vscdb" ]]; then
        CURSOR_DB="$HOME/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
    fi

    if [[ -n "$CURSOR_DB" && -n "$SESSION_ID" ]]; then
        REPO_ROOT="$REPO_ROOT" "$SCRIPT_DIR/../_shared/launch-transcript-sync.sh" "$CURSOR_DB" "$SESSION_ID"
    fi
fi

if [[ "$SESSION_START_NEEDS_PROMPT" == "true" && -n "$SESSION_START_MESSAGE" ]]; then
    echo "$SESSION_START_MESSAGE"
fi
