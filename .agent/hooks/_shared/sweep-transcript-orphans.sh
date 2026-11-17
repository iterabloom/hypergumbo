#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# sweep-transcript-orphans.sh — Archive the transcripts no session-end will
# ever rotate, and clear what a killed archiver left behind.
#
# Usage: sweep-transcript-orphans.sh <REPO_ROOT> <SESSION_ID>
#   <SESSION_ID> is the session whose start launched this sweep; its own
#   files are never swept.
#
# THIS SCRIPT IS SLOW AND MUST NOT RUN INSIDE A HOOK. Each archive
# compresses, validates and secret-scrubs a whole transcript. It used to be
# the middle of launch-transcript-sync.sh, AHEAD of the watcher launch: with
# one un-rotated 453 MB transcript on disk, the session-start hook was cut
# off inside the sweep and the session ran with no watcher. The launcher now
# starts its watcher first and runs this script DETACHED (see
# transcript_rotation_queue.sh).
#
# What it does, in order:
#   0. Drains the rotation queue (rotate-on-session-end.sh --drain). A
#      session whose own rotation worker died is rotated into .last_* here
#      rather than archived as a stray below.
#   Then, under the rotation lock:
#   1. Removes *.partial.<pid> files whose writer is gone, and the archive
#      subdir that leaves empty. archive_scrubbed writes to that temp and
#      only renames a validated archive, so a partial is never the only copy
#      of anything.
#   2. Orphan sweep: a per-session PID file naming a DEAD watcher marks a
#      crashed session; its current files go to
#      .archived-transcripts/crashed-<stamp>-<sid>/.
#   3. Stray sweep: a per-session transcript with NO PID file.
#   Both sweeps skip a session that is queued for rotation: it has ended,
#   and rotate-on-session-end.sh owns its files. An entry can appear while
#   this sweep runs; the session-end hook writes it before the PID file
#   disappears, so one of the two is always visible.
#
# A busy lock (TRANSCRIPT_ROTATION_LOCK_WAIT seconds) skips the sweep; the
# next session start runs it again.

set -uo pipefail

REPO_ROOT="${1:?REPO_ROOT is required}"
SESSION_ID="${2:?SESSION_ID is required}"
AGENT_DIR="$REPO_ROOT/.agent"
SCRIPT_DIR="$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$SCRIPT_DIR/archive_scrubbed.sh"
# Permission contract (INV-todig): the sweeps create archive subdirs —
# owner-only.
# shellcheck source=/dev/null
. "$SCRIPT_DIR/transcript_perms.sh"
harden_transcript_umask
# shellcheck source=/dev/null
. "$SCRIPT_DIR/transcript_rotation_queue.sh"

mkdir -p "$AGENT_DIR"
LOCK_FILE="$(rotation_lock_file "$AGENT_DIR")"

# Step 0, under its own acquisition of the lock.
bash "$SCRIPT_DIR/rotate-on-session-end.sh" --drain "$REPO_ROOT" || \
    echo "warn: queue drain failed" >&2

_sweep_dead_partials() {
    local partial pid dir
    [[ -d "$AGENT_DIR/.archived-transcripts" ]] || return 0
    while IFS= read -r partial; do
        [[ -n "$partial" ]] || continue
        pid="${partial##*.partial.}"
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        # A live writer (an environment without flock) keeps its temp.
        if kill -0 "$pid" 2>/dev/null; then
            continue
        fi
        rm -f "$partial"
        dir="$(dirname -- "$partial")"
        rmdir "$dir" 2>/dev/null || true
    done < <(find "$AGENT_DIR/.archived-transcripts" -mindepth 2 -maxdepth 2 \
                 -type f -name '*.partial.*' 2>/dev/null)
    return 0
}

_sweep() {
_sweep_dead_partials

# ---------------------------------------------------------------------------
# Orphan sweep: walk per-session PID files, archive any crashed-session
# current files, and remove their stale PID files. A live sibling session
# has a live PID — leave it alone.
# ---------------------------------------------------------------------------
shopt -s nullglob
for orphan_pid_file in "$AGENT_DIR"/.transcript-sync.*.pid; do
    orphan_sid="${orphan_pid_file##*/.transcript-sync.}"
    orphan_sid="${orphan_sid%.pid}"

    # Not our own: this session's watcher is already running.
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

    # Ended and queued for rotation: not ours.
    if rotation_is_queued "$AGENT_DIR" "$orphan_sid"; then
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
        harden_transcript_dir "$AGENT_DIR/.archived-transcripts" "$ARCHIVE_SUBDIR"
        # The `rm` is now CONDITIONAL on a validated archive. Previously it sat
        # outside the `if`, so a scrubber failure produced a 20-byte empty gzip
        # and deleted the only copy -- silently, since this path had no `else`
        # warn and `2>/dev/null` ate the traceback.
        if [[ -s "$orphan_dest" ]]; then
            if archive_scrubbed "$orphan_dest" \
                    "$ARCHIVE_SUBDIR/transcript.jsonl.gz" "$REPO_ROOT"; then
                rm -f "$orphan_dest"
            else
                echo "warn: could not archive $orphan_dest; LEAVING IT IN" \
                     "PLACE (next sweep retries)" >&2
            fi
        fi
        if [[ -s "$orphan_inj" ]]; then
            if archive_scrubbed "$orphan_inj" \
                    "$ARCHIVE_SUBDIR/injection_history.jsonl.gz" "$REPO_ROOT"; then
                rm -f "$orphan_inj"
            else
                echo "warn: could not archive $orphan_inj; LEAVING IT IN" \
                     "PLACE (next sweep retries)" >&2
            fi
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

# ---------------------------------------------------------------------------
# Second sweep: transcripts whose PID FILE IS GONE.
#
# The loop above derives the transcript name FROM the PID filename, so a
# .current_session_transcript.<sid>.jsonl with no matching .transcript-sync
# .<sid>.pid is invisible to it -- permanently, since rotation for that SID will
# never fire again either. One such file was found live: 16.8MB from 2026-07-12
# holding 8 occurrences of a live credential, unreachable by any code path.
# Globbing transcripts instead of PID files is what closes that class.
shopt -s nullglob
for stray in "$AGENT_DIR"/.current_session_transcript.*.jsonl; do
    stray_sid="${stray##*/.current_session_transcript.}"
    stray_sid="${stray_sid%.jsonl}"
    [[ "$stray_sid" == "$SESSION_ID" ]] && continue
    # Ended and queued for rotation: not ours. Checked BEFORE the PID file:
    # the entry is written before the PID file is removed, so this order
    # cannot see "no entry yet" followed by "PID file already gone".
    rotation_is_queued "$AGENT_DIR" "$stray_sid" && continue
    # A live sibling still has its PID file; leave it alone.
    [[ -f "$AGENT_DIR/.transcript-sync.${stray_sid}.pid" ]] && continue
    [[ -s "$stray" ]] || { rm -f "$stray"; continue; }
    STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    STRAY_DIR="$AGENT_DIR/.archived-transcripts/stray-${STAMP}-${stray_sid}"
    mkdir -p "$STRAY_DIR" || true
    harden_transcript_dir "$AGENT_DIR/.archived-transcripts" "$STRAY_DIR"
    if archive_scrubbed "$stray" "$STRAY_DIR/transcript.jsonl.gz" "$REPO_ROOT"; then
        rm -f "$stray"
    else
        echo "warn: could not archive stray $stray; LEAVING IT IN PLACE" >&2
    fi
    stray_inj="$AGENT_DIR/.current_injection_history.${stray_sid}.jsonl"
    if [[ -s "$stray_inj" ]]; then
        if archive_scrubbed "$stray_inj" \
                "$STRAY_DIR/injection_history.jsonl.gz" "$REPO_ROOT"; then
            rm -f "$stray_inj"
        else
            echo "warn: could not archive stray $stray_inj; LEAVING IT" >&2
        fi
    fi
done

shopt -u nullglob
rotation_log_trim "$AGENT_DIR"
}

if command -v flock >/dev/null 2>&1; then
    (
        if ! flock -x -w "$TRANSCRIPT_ROTATION_LOCK_WAIT" 200; then
            echo "warn: rotation lock busy after" \
                 "${TRANSCRIPT_ROTATION_LOCK_WAIT}s; sweep skipped" >&2
            exit 0
        fi
        _sweep
    ) 200>"$LOCK_FILE"
else
    _sweep
fi

# The line tests and a human reading the log wait for.
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) sweep done session=$SESSION_ID"
