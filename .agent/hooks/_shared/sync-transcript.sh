#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# sync-transcript.sh — Long-running watcher that mirrors a session transcript
# to a per-session, vendor-agnostic location inside the repo.
#
# Launched in background by session-start hooks; killed by session-end hooks.
# Uses inotifywait to watch the source JSONL for close_write events and
# incrementally filters new lines into the per-session destination. The
# downstream hook (on_transcript_change.sh / poll-transcript-change.sh)
# is fired by the AI tool's own PostToolUse / FileChanged hook against
# the per-session destination.
#
# Per-session isolation (Option 2 of ADR-0018 amendment):
#   - DEST is .agent/.current_session_transcript.<SESSION_ID>.jsonl
#   - PID file is .agent/.transcript-sync.<SESSION_ID>.pid
#   - Filter state is .agent/.transcript-sync-state.<SESSION_ID>.json
# Each concurrent session in the same repo writes to its own files, so
# sibling sessions never race on shared state. Rotation into the global
# .last_*/.second_to_last_* slots happens at session END (not start), via
# rotate-on-session-end.sh — this script is purely append-only.
#
# Usage: sync-transcript.sh <source-jsonl> <dest-jsonl> <session-id>

set -euo pipefail

SRC="$1"
DEST="$2"
SESSION_ID="$3"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")"/../../.. && pwd)"
PID_FILE="$REPO_ROOT/.agent/.transcript-sync.${SESSION_ID}.pid"
FILTER_SCRIPT="$REPO_ROOT/.agent/hooks/_shared/filter-transcript.py"
STATE_FILE="$REPO_ROOT/.agent/.transcript-sync-state.${SESSION_ID}.json"

mkdir -p "$REPO_ROOT/.agent"

# Write the per-session PID file. Because the PID file path encodes
# SESSION_ID, sibling sessions never collide here.
echo $$ > "$PID_FILE"

# Conditional cleanup: only remove the PID file if it still belongs to us.
# A user manually clobbering our PID file is rare, but the guard keeps the
# trap from removing a file that has been re-claimed by another process.
cleanup() {
    if [[ -f "$PID_FILE" ]] && [[ "$(cat "$PID_FILE" 2>/dev/null)" == "$$" ]]; then
        rm -f "$PID_FILE"
    fi
}
trap cleanup EXIT

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

# Initial sync. The `|| true` is required because do_sync's last command
# is a `[[ new_size -gt prev_size ]]` test that doubles as the function's
# return value (callers can use it to detect "did the dest grow?"). With
# `set -euo pipefail` in effect, a false return at function exit otherwise
# propagates out and kills the watcher — silently. This bites whenever the
# filter drops every new line as noise (bash_progress dedup, snapshots),
# which is common in real Claude Code sessions.
do_sync || true

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
    do_sync || true
done
