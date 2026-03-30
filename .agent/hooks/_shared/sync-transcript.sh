#!/bin/bash
# sync-transcript.sh — Long-running watcher that mirrors a session transcript
# to a vendor-agnostic location inside the repo.
#
# Launched in background by session-start hooks; killed by session-end hooks.
# Uses inotifywait to watch the source JSONL for close_write events and
# incrementally filters new lines into the destination. Fires
# on_transcript_change.sh as a downstream hook on each update.
#
# The filter drops redundant noise (repeated bash_progress heartbeats,
# empty progress lines, file-history snapshots) so downstream consumers
# only see meaningful events.
#
# Usage: sync-transcript.sh <source-jsonl> <dest-jsonl>

set -euo pipefail

SRC="$1"
DEST="$2"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")"/../../.. && pwd)"
PID_FILE="$REPO_ROOT/.agent/.transcript-sync.pid"
FILTER_SCRIPT="$REPO_ROOT/.agent/hooks/_shared/filter-transcript.py"
STATE_FILE="$REPO_ROOT/.agent/.transcript-sync-state.json"

echo $$ > "$PID_FILE"

cleanup() {
    rm -f "$PID_FILE"
}
trap cleanup EXIT

# Reset ALL per-session state for a fresh session.
# Convention: any file in .agent/ matching .transcript-* is per-session
# transient state and gets blown away here.  New state files that follow
# this naming convention are automatically covered — no registration needed.
rm -f "$REPO_ROOT/.agent/.transcript-"* "$DEST"

# Write a session token so consumers can detect stale state even if
# this reset was somehow skipped (e.g., watcher not launched).
echo "$(date +%s)-$$" > "$REPO_ROOT/.agent/.transcript-session-token"

# Phase 1: Wait for the source file to exist (may not be created until
# the first message is sent in the session).
SRC_DIR="$(dirname "$SRC")"
while [[ ! -f "$SRC" ]]; do
    inotifywait -qq -t 5 -e create "$SRC_DIR" 2>/dev/null || true
done

# Sync function: filter new lines from source into dest.
# Returns 0 if dest was modified, 1 if nothing new was written.
do_sync() {
    local prev_size=0
    if [[ -f "$DEST" ]]; then
        prev_size=$(stat -c%s "$DEST" 2>/dev/null || echo 0)
    fi

    if [[ -x "$FILTER_SCRIPT" ]] || command -v python3 &>/dev/null; then
        python3 "$FILTER_SCRIPT" "$SRC" "$DEST" "$STATE_FILE"
    else
        # Fallback: raw copy if python3 is unavailable
        cp -- "$SRC" "$DEST"
    fi

    local new_size=0
    if [[ -f "$DEST" ]]; then
        new_size=$(stat -c%s "$DEST" 2>/dev/null || echo 0)
    fi

    [[ "$new_size" -gt "$prev_size" ]]
}

# Initial sync
do_sync

# Phase 2: Watch for changes and filter on each write.
# The downstream hook (on_transcript_change.sh) is NOT called from here.
# Instead, the AI tool's own FileChanged hook watches the destination file
# and calls the hook — this lets the hook's stdout be injected back into
# the agent's conversation as context.
while true; do
    inotifywait -qq -e close_write "$SRC" 2>/dev/null || {
        # Source file may have been deleted or recreated
        if [[ ! -f "$SRC" ]]; then
            break
        fi
        continue
    }
    do_sync
done
