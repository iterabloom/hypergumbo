#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# rotate-on-session-end.sh — Promote just-ended sessions' per-session
# current files into the global .last_*/.second_to_last_* slots.
#
# Usage: rotate-on-session-end.sh <REPO_ROOT> <SESSION_ID>
#        rotate-on-session-end.sh --drain <REPO_ROOT>
#
# THIS SCRIPT IS SLOW AND MUST NOT RUN INSIDE A HOOK. It compresses,
# validates and secret-scrubs whole transcripts, which reach hundreds of
# megabytes; a hook budget (Claude Code: 5 s) cuts it off mid-archive. A
# session-end hook calls end-session-transcript.sh, which queues the session
# and runs this script DETACHED. See transcript_rotation_queue.sh for the
# queue and the measured failure.
#
# Both forms drain the whole rotation queue; workers are interchangeable.
# The first form also queues <SESSION_ID> (idempotent), and rotates it even
# if the queue entry could not be written.
#
# Semantics under per-session isolation (ADR-0018 amendment / Option 2):
#   - Each session writes to its own .current_session_transcript.<sid>.jsonl
#     and .current_injection_history.<sid>.jsonl during its life.
#   - Under .agent/.rotation.lock (flock), for each queued session in end
#     order, this script:
#       1. Archives the existing .second_to_last_* pair (if any) to a
#          timestamped subdir under .agent/.archived-transcripts/.
#       2. Demotes .last_* → .second_to_last_*.
#       3. Promotes the session's .current_*.<sid>.* → .last_*.
#       4. Cleans up the session's other per-session state files.
#     and then, once per drain, heals modes and scrubs the global slots in
#     place. Queue entries are removed only after that scrub, so a worker
#     killed anywhere before it leaves entries for the next drain to finish.
#
# Two cases rotate nothing for an entry:
#   - No current file exists for the session. A repeated drain (the previous
#     worker died after the renames) would otherwise demote the transcript
#     it had just promoted and leave .last_* absent.
#   - The session is live again (a PID file naming a live watcher): it was
#     resumed before its entry drained, and its files belong to a running
#     session. Checked before the slow archive and again before the renames.
#
# A busy lock (TRANSCRIPT_ROTATION_LOCK_WAIT seconds, default 600) leaves
# the queue untouched for the next drain. It never runs unsynchronized: the
# holder may be scrubbing a slot in place, and its atomic replace would
# clobber a slot renamed underneath it.
#
# The global ".last_session_transcript.jsonl" therefore means "most
# recently ENDED session in this repo."
# Crashed sessions (no session-end fired) DO NOT pass through here;
# their orphaned current files are archived by sweep-transcript-orphans.sh,
# which the next session start runs detached.

set -euo pipefail

if [[ "${1:-}" == "--drain" ]]; then
    REPO_ROOT="${2:-${REPO_ROOT:?REPO_ROOT must be set}}"
    SESSION_ID=""
else
    REPO_ROOT="${1:-${REPO_ROOT:?REPO_ROOT must be set}}"
    SESSION_ID="${2:?SESSION_ID is required}"
fi
AGENT_DIR="$REPO_ROOT/.agent"

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
# Permission contract (INV-todig): rotation moves transcript bytes with mv,
# which preserves whatever mode the source had — so rotation must both
# create owner-only and explicitly heal the global slots it populates.
# shellcheck source=/dev/null
. "$_HOOK_SHARED_DIR/transcript_perms.sh"
harden_transcript_umask
# shellcheck source=/dev/null
. "$_HOOK_SHARED_DIR/transcript_rotation_queue.sh"

mkdir -p "$AGENT_DIR"
LOCK_FILE="$(rotation_lock_file "$AGENT_DIR")"
QUEUE_DIR="$(rotation_queue_dir "$AGENT_DIR")"

# _rotate_slots <session_id>: steps 1-3 for one ended session.
_rotate_slots() {
    local sid="$1"
    local CURRENT_TR="$AGENT_DIR/.current_session_transcript.${sid}.jsonl"
    local CURRENT_INJ="$AGENT_DIR/.current_injection_history.${sid}.jsonl"

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
        # 0700 on the root also strips the legacy setgid bit (2775) that
        # propagated group access onto every archive subdir.
        harden_transcript_dir "$ARCHIVE_DIR" "$DEST_DIR"
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
                harden_transcript_file "$DEST_DIR/transcript.jsonl.rescued"
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
                harden_transcript_file "$DEST_DIR/injection_history.jsonl.rescued"
            fi
        fi
    fi

    # The archive above can take a while. If the session was resumed
    # meanwhile, stop before the renames: nothing has moved yet.
    if rotation_session_is_live "$AGENT_DIR" "$sid"; then
        return 1
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

    return 0
}

# Steps 3c/3b, once per drain, over the two GLOBAL slots.
_heal_and_scrub_slots() {
    # Step 3c (ordered before the scrub, which preserves modes): heal the
    # global slots. mv carried over whatever mode the source files had, and
    # months of pre-contract sessions left 664 — every rotation tightens.
    harden_transcript_file "$LAST_TR" "$LAST_INJ" "$SECOND_TR" "$SECOND_INJ"

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
}

# Step 4: clean up one session's other per-session state files.
_cleanup_session_state() {
    local sid="$1"
    rm -f "$AGENT_DIR/.transcript-sync.${sid}.pid"
    rm -f "$AGENT_DIR/.transcript-sync-state.${sid}.json"
    rm -f "$AGENT_DIR/.transcript-poll-state.${sid}"
    rm -f "$AGENT_DIR/.transcript-injection-state.${sid}.json"
}

# _rotate_one <session_id>: one queue entry, start to finish.
_rotate_one() {
    local sid="$1"
    # Resumed before its entry drained: the files belong to a live session.
    if rotation_session_is_live "$AGENT_DIR" "$sid"; then
        return 0
    fi
    # Rotate only if the session left a current file (an empty one counts:
    # it is removed, and .last_* is still demoted). With none, this entry
    # was already rotated by a worker that died before removing it.
    if [[ -e "$AGENT_DIR/.current_session_transcript.${sid}.jsonl" || \
          -e "$AGENT_DIR/.current_injection_history.${sid}.jsonl" ]]; then
        _rotate_slots "$sid" || return 0
    fi
    _cleanup_session_state "$sid"
}

# Drain every queued session in end order, then <SESSION_ID> itself if it
# has no entry (the enqueue failed).
_drain() {
    local entry sid extra="$SESSION_ID" did=0
    local -a drained=()
    shopt -s nullglob
    for entry in "$QUEUE_DIR"/*; do
        sid="${entry##*/}"
        sid="${sid#*.}"
        _rotate_one "$sid"
        drained+=("$entry")
        did=1
        [[ "$sid" == "$extra" ]] && extra=""
    done
    shopt -u nullglob
    if [[ -n "$extra" ]]; then
        _rotate_one "$extra"
        did=1
    fi
    if [[ "$did" == 1 ]]; then
        _heal_and_scrub_slots
    fi
    # Entries go last: a worker killed before this point leaves them for
    # the next drain, which finds no current file and finishes the scrub.
    for entry in ${drained[@]+"${drained[@]}"}; do
        rm -f "$entry"
    done
    rotation_log_trim "$AGENT_DIR"
}

if [[ -n "$SESSION_ID" ]]; then
    rotation_enqueue "$AGENT_DIR" "$SESSION_ID" || \
        echo "warn: could not queue $SESSION_ID; rotating it directly" >&2
fi

# Acquire the rotation lock. A busy lock leaves the queue for the next
# drain (see the header). If flock is unavailable, fall back to draining
# directly — the only loss is concurrent-end atomicity, which is acceptable
# in degraded environments.
if command -v flock >/dev/null 2>&1; then
    (
        if ! flock -x -w "$TRANSCRIPT_ROTATION_LOCK_WAIT" 200; then
            echo "warn: rotation lock busy after" \
                 "${TRANSCRIPT_ROTATION_LOCK_WAIT}s; leaving the queue for" \
                 "the next drain" >&2
            exit 0
        fi
        _drain
    ) 200>"$LOCK_FILE"
else
    _drain
fi
