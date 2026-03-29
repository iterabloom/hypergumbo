#!/bin/bash
# launch-transcript-sync.sh — Start the background transcript sync watcher.
# Called by each tool's session-start hook.
#
# Usage: launch-transcript-sync.sh <source-transcript-path>
# Requires: REPO_ROOT environment variable (or pass as $2).
#
# Kills any stale watcher before launching a new one.

set -euo pipefail

SRC="$1"
REPO_ROOT="${2:-${REPO_ROOT:?REPO_ROOT must be set}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$REPO_ROOT/.agent/.current_session_transcript.jsonl"
SYNC_PID_FILE="$REPO_ROOT/.agent/.transcript-sync.pid"

# Kill any stale watcher from a prior session
if [[ -f "$SYNC_PID_FILE" ]]; then
    OLD_PID=$(cat "$SYNC_PID_FILE" 2>/dev/null || true)
    if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID" 2>/dev/null || true
    fi
    rm -f "$SYNC_PID_FILE"
fi

# Launch watcher in background (survives hook exit)
nohup "$SCRIPT_DIR/sync-transcript.sh" "$SRC" "$DEST" \
    </dev/null >/dev/null 2>&1 &
disown
