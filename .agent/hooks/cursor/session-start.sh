#!/bin/bash
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

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Capture stdin (Cursor passes JSON input)
STDIN_JSON=$(cat)

# --- Shared logic (sets SESSION_START_MESSAGE, SESSION_START_NEEDS_PROMPT) ---
source "$SCRIPT_DIR/../_shared/session_start_logic.sh"

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

if [[ -n "$CURSOR_DB" ]]; then
    REPO_ROOT="$REPO_ROOT" "$SCRIPT_DIR/../_shared/launch-transcript-sync.sh" "$CURSOR_DB"
fi

if [[ "$SESSION_START_NEEDS_PROMPT" == "true" && -n "$SESSION_START_MESSAGE" ]]; then
    echo "$SESSION_START_MESSAGE"
fi
