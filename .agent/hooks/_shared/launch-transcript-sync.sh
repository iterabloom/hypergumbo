#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# launch-transcript-sync.sh — Start the background transcript sync watcher
# for a specific session.
#
# Called by each tool's session-start hook.
#
# Usage: launch-transcript-sync.sh <source-transcript-path> <session-id>
# Requires: REPO_ROOT environment variable (or computed from script location).
#
# Per-session isolation (ADR-0018 amendment / Option 2):
#   - Each session has its own DEST: .agent/.current_session_transcript.<sid>.jsonl
#   - Each session has its own PID file: .agent/.transcript-sync.<sid>.pid
#   - The orphan sweep below walks per-session PID files, archives any
#     crashed-session current files, and removes their stale PID files.
#     A live sibling session has a live PID and is left alone.
#
# This script does NOT call kill-transcript-sync.sh against the new session,
# because the new session is being launched here for the first time. Cleanup
# of stale PID files from CRASHED prior sessions is handled inline.

set -euo pipefail

SRC="$1"
SESSION_ID="$2"
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")"/../../.. && pwd)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$REPO_ROOT/.agent"
DEST="$AGENT_DIR/.current_session_transcript.${SESSION_ID}.jsonl"

mkdir -p "$AGENT_DIR"

# ---------------------------------------------------------------------------
# One-time legacy cleanup (upgrade migration from pre-per-session pipeline).
# Pre-PR watchers used a single global `.transcript-sync.pid` and a
# 2-positional-arg signature (no session_id). After this PR merges, the
# first launch in any repo finds and kills any straggling legacy watcher
# so the new per-session pipeline can take over cleanly.
# ---------------------------------------------------------------------------
LEGACY_PID_FILE="$AGENT_DIR/.transcript-sync.pid"
if [[ -f "$LEGACY_PID_FILE" ]]; then
    legacy_pid=$(cat "$LEGACY_PID_FILE" 2>/dev/null || true)
    if [[ -n "$legacy_pid" ]] && kill -0 "$legacy_pid" 2>/dev/null; then
        kill "$legacy_pid" 2>/dev/null || true
    fi
    rm -f "$LEGACY_PID_FILE"
fi

# pgrep for any 2-arg legacy watcher whose DEST matches the legacy
# global path. The DEST is at offset +2 from the script-name field
# (vs. SESSION_ID at +3 in the new per-session signature).
LEGACY_DEST="$AGENT_DIR/.current_session_transcript.jsonl"
while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    pid="${line%% *}"
    [[ "$pid" == "$$" ]] && continue
    legacy_dest_arg=$(awk '{
        for (i=2; i<=NF; i++) {
            if ($i ~ /sync-transcript\.sh$/) {
                print $(i+2)
                exit
            }
        }
    }' <<< "$line")
    if [[ -n "$legacy_dest_arg" && "$legacy_dest_arg" == "$LEGACY_DEST" ]]; then
        kill "$pid" 2>/dev/null || true
    fi
done < <(pgrep -af 'sync-transcript\.sh' 2>/dev/null || true)

# Clean up legacy global state files. These won't be caught by the
# per-session orphan sweep because they don't have a session_id suffix.
rm -f "$AGENT_DIR/.transcript-sync-state.json"
rm -f "$AGENT_DIR/.transcript-poll-state"
rm -f "$AGENT_DIR/.transcript-injection-state.json"
rm -f "$AGENT_DIR/.transcript-session-token"
rm -f "$LEGACY_DEST"

# ---------------------------------------------------------------------------
# Orphan sweep: walk per-session PID files, archive any crashed-session
# current files, and remove their stale PID files. A live sibling session
# has a live PID — leave it alone.
# ---------------------------------------------------------------------------
shopt -s nullglob
for orphan_pid_file in "$AGENT_DIR"/.transcript-sync.*.pid; do
    orphan_sid="${orphan_pid_file##*/.transcript-sync.}"
    orphan_sid="${orphan_sid%.pid}"

    # Don't touch our own PID file (shouldn't exist yet, but defensive).
    if [[ "$orphan_sid" == "$SESSION_ID" ]]; then
        continue
    fi

    orphan_pid=$(cat "$orphan_pid_file" 2>/dev/null || true)
    if [[ -z "$orphan_pid" ]]; then
        rm -f "$orphan_pid_file"
        continue
    fi

    # If the recorded PID is alive, this is a live sibling session — leave
    # it alone. Per-session DEST means our launch cannot interfere with it.
    if kill -0 "$orphan_pid" 2>/dev/null; then
        continue
    fi

    # Stale: the watcher process is dead but its files were never cleaned up.
    # Archive the orphaned current file directly (do NOT promote to .last_*
    # because a crashed session shouldn't claim "most recently ended").
    orphan_dest="$AGENT_DIR/.current_session_transcript.${orphan_sid}.jsonl"
    orphan_inj="$AGENT_DIR/.current_injection_history.${orphan_sid}.jsonl"
    if [[ -s "$orphan_dest" || -s "$orphan_inj" ]]; then
        STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
        ARCHIVE_SUBDIR="$AGENT_DIR/.archived-transcripts/crashed-${STAMP}-${orphan_sid}"
        mkdir -p "$ARCHIVE_SUBDIR" || true
        if [[ -s "$orphan_dest" ]]; then
            if gzip -c "$orphan_dest" > "$ARCHIVE_SUBDIR/transcript.jsonl.gz" 2>/dev/null; then
                touch -r "$orphan_dest" "$ARCHIVE_SUBDIR/transcript.jsonl.gz" 2>/dev/null || true
            fi
            rm -f "$orphan_dest"
        fi
        if [[ -s "$orphan_inj" ]]; then
            if gzip -c "$orphan_inj" > "$ARCHIVE_SUBDIR/injection_history.jsonl.gz" 2>/dev/null; then
                touch -r "$orphan_inj" "$ARCHIVE_SUBDIR/injection_history.jsonl.gz" 2>/dev/null || true
            fi
            rm -f "$orphan_inj"
        fi
    else
        # No content to archive; just clean up empty files if any.
        rm -f "$orphan_dest" "$orphan_inj"
    fi

    # Remove stale per-session state files for this orphan.
    rm -f "$orphan_pid_file"
    rm -f "$AGENT_DIR/.transcript-sync-state.${orphan_sid}.json"
    rm -f "$AGENT_DIR/.transcript-poll-state.${orphan_sid}"
    rm -f "$AGENT_DIR/.transcript-injection-state.${orphan_sid}.json"
done
shopt -u nullglob

# ---------------------------------------------------------------------------
# Launch the watcher in the background (survives hook exit).
# ---------------------------------------------------------------------------
nohup "$SCRIPT_DIR/sync-transcript.sh" "$SRC" "$DEST" "$SESSION_ID" \
    </dev/null >/dev/null 2>&1 &
disown
