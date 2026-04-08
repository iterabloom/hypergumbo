#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
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

# Rotate prior session transcripts before clearing state.
# .last_session_transcript.jsonl = previous session (the one to retrospect on)
# .second_to_last_transcript.jsonl = two sessions ago (for comparison)
LAST="$REPO_ROOT/.agent/.last_session_transcript.jsonl"
SECOND="$REPO_ROOT/.agent/.second_to_last_transcript.jsonl"

# Injection-history sidecar (ADR-0018 B1): parallel to the transcript pair.
# Captures which playbooks were selected/injected/skipped per LLM poll, so
# the agentic-session-retrospective playbook can answer "were the right
# playbooks injected at the right times?" — a question that is structurally
# unanswerable from the transcript JSONL alone (Claude Code's
# additionalContext mechanism injects into the API request but not the
# session log).
INJ_CURRENT="$REPO_ROOT/.agent/.current_injection_history.jsonl"
INJ_LAST="$REPO_ROOT/.agent/.last_injection_history.jsonl"
INJ_SECOND="$REPO_ROOT/.agent/.second_to_last_injection_history.jsonl"
ARCHIVE_DIR="$REPO_ROOT/.agent/.archived-transcripts"

# Step 1: archive the about-to-be-clobbered .second_to_last_* pair into a
# timestamped subdir.  Per ADR-0018, we keep cross-session history for
# retrospective analysis without sacrificing the per-session reset semantics
# of .current/.last/.second_to_last.  gzip + touch -r preserves the original
# session-end mtime so `ls -la` shows when the session actually ended.  The
# archive is best-effort: a gzip failure logs to stderr but does NOT abort
# session start.
if [[ -f "$SECOND" || -f "$INJ_SECOND" ]]; then
    STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    DEST_DIR="$ARCHIVE_DIR/$STAMP"
    mkdir -p "$DEST_DIR" || echo "warn: could not mkdir $DEST_DIR" >&2
    if [[ -f "$SECOND" ]]; then
        if gzip -c "$SECOND" > "$DEST_DIR/transcript.jsonl.gz" 2>/dev/null; then
            touch -r "$SECOND" "$DEST_DIR/transcript.jsonl.gz" 2>/dev/null || true
        else
            echo "warn: failed to archive $SECOND" >&2
        fi
    fi
    if [[ -f "$INJ_SECOND" ]]; then
        if gzip -c "$INJ_SECOND" > "$DEST_DIR/injection_history.jsonl.gz" 2>/dev/null; then
            touch -r "$INJ_SECOND" "$DEST_DIR/injection_history.jsonl.gz" 2>/dev/null || true
        else
            echo "warn: failed to archive $INJ_SECOND" >&2
        fi
    fi
fi

# Step 2: rotate the transcript pair (.last → .second, .current → .last)
if [[ -f "$LAST" ]]; then
    mv -f "$LAST" "$SECOND"
fi
if [[ -f "$DEST" && -s "$DEST" ]]; then
    mv -f "$DEST" "$LAST"
fi

# Step 3: rotate the injection-history sidecar in parallel.  These files
# DO NOT match the .transcript-* glob below (they use a different prefix),
# so they survive the per-session reset and persist across sessions exactly
# the way the transcript pair does.
if [[ -f "$INJ_LAST" ]]; then
    mv -f "$INJ_LAST" "$INJ_SECOND"
fi
if [[ -f "$INJ_CURRENT" && -s "$INJ_CURRENT" ]]; then
    mv -f "$INJ_CURRENT" "$INJ_LAST"
fi

# Reset ALL per-session state for a fresh session.
# Convention: any file in .agent/ matching .transcript-* is per-session
# transient state and gets blown away here.  New state files that follow
# this naming convention are automatically covered — no registration needed.
# NOTE: .current_injection_history.jsonl and the .archived-transcripts/ dir
# deliberately use a different prefix so they are NOT caught by this glob.
rm -f "$REPO_ROOT/.agent/.transcript-"* "$DEST"

# Write the PID file AFTER the per-session reset (otherwise the rm -f
# above silently nukes it, which made kill-transcript-sync.sh blind to
# the live watcher — the root cause of the 13-orphan leak observed
# Apr 5–7 2026, alongside the EXIT trap race fixed by the conditional
# cleanup below).
echo $$ > "$PID_FILE"

# Write a session token so consumers can detect stale state even if
# this reset was somehow skipped (e.g., watcher not launched).
echo "$(date +%s)-$$" > "$REPO_ROOT/.agent/.transcript-session-token"

# Conditional cleanup: only remove the PID file if it still belongs to us.
# A racing session start may have overwritten the PID file with its own
# PID; in that case the new owner manages the file's lifecycle, not us.
# Without this guard, an exiting watcher unconditionally clobbered the new
# watcher's PID file.
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
