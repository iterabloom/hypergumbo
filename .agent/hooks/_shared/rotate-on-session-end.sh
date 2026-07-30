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

# CDPATH= and -P: a poisoned CDPATH makes `cd` echo the resolved path to stdout,
# which would capture two lines into the variable; -P resolves symlinks so a
# symlinked hook still finds its siblings. Both were demonstrated routes to the
# data loss this helper exists to prevent.
_HOOK_SHARED_DIR="$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$_HOOK_SHARED_DIR/archive_scrubbed.sh"

mkdir -p "$AGENT_DIR"

_do_rotation() {
    # Step 1: archive the about-to-be-clobbered .second_to_last_* pair.
    # Per ADR-0018, we keep cross-session history for retrospective analysis
    # without sacrificing the global .last_*/.second_to_last_* slots.
    # Writes go through archive_scrubbed (secrets redacted, then validated:
    # non-empty + gzip -t + equal line count) which preserves the source mtime
    # so `ls -la` still shows when the prior session ended. Best effort: a
    # failure logs to stderr and leaves NO archive rather than a bad one, and
    # never aborts rotation. The demote below is what would destroy the source,
    # so it is now conditional on the archive having validated.
    if [[ -f "$SECOND_TR" || -f "$SECOND_INJ" ]]; then
        STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
        DEST_DIR="$ARCHIVE_DIR/$STAMP"
        mkdir -p "$DEST_DIR" || echo "warn: could not mkdir $DEST_DIR" >&2
        # If the archive does not validate, RESCUE the source instead of
        # leaving it to be clobbered by the demote below. `mv` within .agent/ is
        # same-filesystem and atomic, so it cannot truncate: the bytes always
        # reach safety before anything overwrites their old path. This is the
        # invariant the previous wiring broke -- there, a failed archive was
        # followed by an unconditional `mv -f "$LAST_TR" "$SECOND_TR"` that
        # destroyed the only remaining copy.
        if [[ -f "$SECOND_TR" ]]; then
            if ! archive_scrubbed "$SECOND_TR" "$DEST_DIR/transcript.jsonl.gz" \
                    "$REPO_ROOT"; then
                echo "warn: could not archive $SECOND_TR; rescuing it" \
                     "UNSCRUBBED to $DEST_DIR/transcript.jsonl.rescued" >&2
                mv -f "$SECOND_TR" "$DEST_DIR/transcript.jsonl.rescued" || \
                    echo "warn: rescue FAILED for $SECOND_TR" >&2
            fi
        fi
        if [[ -f "$SECOND_INJ" ]]; then
            if ! archive_scrubbed "$SECOND_INJ" \
                    "$DEST_DIR/injection_history.jsonl.gz" "$REPO_ROOT"; then
                echo "warn: could not archive $SECOND_INJ; rescuing it" \
                     "UNSCRUBBED to $DEST_DIR/injection_history.jsonl.rescued" >&2
                mv -f "$SECOND_INJ" \
                    "$DEST_DIR/injection_history.jsonl.rescued" || \
                    echo "warn: rescue FAILED for $SECOND_INJ" >&2
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

    # Step 3b: scrub the two GLOBAL slots in place.
    #
    # Steps 1-3 move bytes into .last_*/.second_to_last_* without inspecting
    # them, and those are the files the retrospective workflow reads -- so
    # scrubbing only the archive would leave a credential readable for two more
    # session-ends. In-place scrubbing is safe where archiving was not: it
    # writes a temp and atomically replaces, so a failure leaves the original
    # byte-identical (covered by tests/test_scrub_secrets.py).
    #
    # Best effort, and NOT quiet: stderr is unsuppressed so a skipped secret or
    # a failed verify is visible. A non-zero exit here must never abort a
    # session end.
    if [[ -f "$_HOOK_SHARED_DIR/scrub_secrets.py" ]] && \
            command -v python3 >/dev/null 2>&1; then
        for _slot in "$LAST_TR" "$LAST_INJ" "$SECOND_TR" "$SECOND_INJ"; do
            if [[ -s "$_slot" ]]; then
                python3 "$_HOOK_SHARED_DIR/scrub_secrets.py" \
                    --in-place --repo-root "$REPO_ROOT" "$_slot" || \
                    echo "warn: in-place scrub failed for $_slot;" \
                         "it is UNCHANGED (original preserved)" >&2
            fi
        done
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
