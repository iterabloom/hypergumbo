#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# rotate-on-session-end.sh — Promote a just-ended session's per-session
# current files into the global .last_*/.second_to_last_* slots.
#
# Called by each tool's session-end hook AFTER kill-transcript-sync.sh.
#
# Usage: rotate-on-session-end.sh <REPO_ROOT> <SESSION_ID>
#
# Semantics under per-session isolation (ADR-0018 amendment / Option 2):
#   - Each session writes to its own .current_session_transcript.<sid>.jsonl
#     and .current_injection_history.<sid>.jsonl during its life.
#   - When a session ENDS, this script atomically:
#       1. Acquires .agent/.rotation.lock (flock) so two simultaneous
#          session-ends serialize cleanly.
#       2. Archives the existing .second_to_last_* pair (if any) to a
#          timestamped subdir under .agent/.archived-transcripts/.
#       3. Demotes .last_* → .second_to_last_*.
#       4. Promotes this session's .current_*.<sid>.* → .last_*.
#       5. Cleans up this session's other per-session state files.
#       6. Releases the lock.
#
# The global ".last_session_transcript.jsonl" therefore means "most
# recently ENDED session in this repo." With concurrent sessions, two
# end events linearize via flock — last writer wins the .last slot.
# Crashed sessions (no session-end fired) DO NOT pass through here;
# their orphaned current files are archived directly by the next
# session-start's launch-transcript-sync.sh orphan sweep.

set -euo pipefail

REPO_ROOT="${1:-${REPO_ROOT:?REPO_ROOT must be set}}"
SESSION_ID="${2:?SESSION_ID is required}"
AGENT_DIR="$REPO_ROOT/.agent"
LOCK_FILE="$AGENT_DIR/.rotation.lock"

CURRENT_TR="$AGENT_DIR/.current_session_transcript.${SESSION_ID}.jsonl"
CURRENT_INJ="$AGENT_DIR/.current_injection_history.${SESSION_ID}.jsonl"
LAST_TR="$AGENT_DIR/.last_session_transcript.jsonl"
LAST_INJ="$AGENT_DIR/.last_injection_history.jsonl"
SECOND_TR="$AGENT_DIR/.second_to_last_transcript.jsonl"
SECOND_INJ="$AGENT_DIR/.second_to_last_injection_history.jsonl"
ARCHIVE_DIR="$AGENT_DIR/.archived-transcripts"

mkdir -p "$AGENT_DIR"

_do_rotation() {
    # Step 1: archive the about-to-be-clobbered .second_to_last_* pair.
    # Per ADR-0018, we keep cross-session history for retrospective analysis
    # without sacrificing the global .last_*/.second_to_last_* slots.
    # gzip + touch -r preserves the original session-end mtime so `ls -la`
    # shows when the prior session actually ended. The archive is best
    # effort: a gzip failure logs to stderr but does not abort rotation.
    if [[ -f "$SECOND_TR" || -f "$SECOND_INJ" ]]; then
        STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
        DEST_DIR="$ARCHIVE_DIR/$STAMP"
        mkdir -p "$DEST_DIR" || echo "warn: could not mkdir $DEST_DIR" >&2
        if [[ -f "$SECOND_TR" ]]; then
            if gzip -c "$SECOND_TR" > "$DEST_DIR/transcript.jsonl.gz" 2>/dev/null; then
                touch -r "$SECOND_TR" "$DEST_DIR/transcript.jsonl.gz" 2>/dev/null || true
            else
                echo "warn: failed to archive $SECOND_TR" >&2
            fi
        fi
        if [[ -f "$SECOND_INJ" ]]; then
            if gzip -c "$SECOND_INJ" > "$DEST_DIR/injection_history.jsonl.gz" 2>/dev/null; then
                touch -r "$SECOND_INJ" "$DEST_DIR/injection_history.jsonl.gz" 2>/dev/null || true
            else
                echo "warn: failed to archive $SECOND_INJ" >&2
            fi
        fi
    fi

    # Step 2: demote .last_* to .second_to_last_*.
    if [[ -f "$LAST_TR" ]]; then
        mv -f "$LAST_TR" "$SECOND_TR"
    else
        rm -f "$SECOND_TR"
    fi
    if [[ -f "$LAST_INJ" ]]; then
        mv -f "$LAST_INJ" "$SECOND_INJ"
    else
        rm -f "$SECOND_INJ"
    fi

    # Step 3: promote this session's per-session current files to .last_*.
    # Empty current files are removed (no content to promote).
    if [[ -s "$CURRENT_TR" ]]; then
        mv -f "$CURRENT_TR" "$LAST_TR"
    else
        rm -f "$CURRENT_TR"
    fi
    if [[ -s "$CURRENT_INJ" ]]; then
        mv -f "$CURRENT_INJ" "$LAST_INJ"
    else
        rm -f "$CURRENT_INJ"
    fi

    # Step 4: clean up this session's other per-session state files.
    rm -f "$AGENT_DIR/.transcript-sync.${SESSION_ID}.pid"
    rm -f "$AGENT_DIR/.transcript-sync-state.${SESSION_ID}.json"
    rm -f "$AGENT_DIR/.transcript-poll-state.${SESSION_ID}"
    rm -f "$AGENT_DIR/.transcript-injection-state.${SESSION_ID}.json"
}

# Acquire the rotation lock. flock with -x (exclusive) and a 30s timeout
# serializes concurrent end events. If flock is unavailable or fails, fall
# back to running rotation directly — the only loss is concurrent-end
# atomicity, which is acceptable in degraded environments.
if command -v flock >/dev/null 2>&1; then
    (
        flock -x -w 30 200 || {
            echo "warn: rotation lock timeout; running unsynchronized" >&2
        }
        _do_rotation
    ) 200>"$LOCK_FILE"
else
    _do_rotation
fi
