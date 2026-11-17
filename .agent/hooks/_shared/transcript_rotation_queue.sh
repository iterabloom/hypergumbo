#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# transcript_rotation_queue.sh — the ONE home for "do the slow transcript
# work OUTSIDE the hook": the rotation queue, the shared lock, and the
# detach primitive.
#
# WHY THIS EXISTS
#   A vendor hook runs under a short budget (Claude Code: `"timeout": 5` in
#   .claude/settings.json) and is killed when it overruns. Rotation and the
#   start-time sweeps compress, validate and secret-scrub whole transcripts,
#   and a transcript reaches hundreds of megabytes. Measured: a 395 MB
#   .second_to_last slot was cut off at 5.0 s with 77 MB of a .partial
#   written, at two consecutive session ends, so the slots stopped rotating;
#   the next session START then tried to archive the un-rotated 453 MB file
#   BEFORE launching its watcher, was cut off too, and ran with no watcher.
#   The data survived only because archive_scrubbed never removes a source
#   before its archive validates.
#
# THE CONTRACT
#   A hook does only O(1) work: it records an intent (a queue entry), kills
#   or starts a watcher, and hands the rest to a detached worker. Every
#   worker does its slow work under ONE lock (.agent/.rotation.lock), so the
#   in-place scrub of a global slot can never race the rename that moves
#   that slot.
#
# THE QUEUE
#   .agent/.rotation-queue/<UTC stamp>.<session id> — an empty file per
#   ended session, created by the session-end hook BEFORE the watcher is
#   killed. Entries drain in name order (end order). The entry, not the
#   worker, is the durable record: workers are interchangeable, any of them
#   drains every entry, and an entry whose worker died (reboot, OOM kill) is
#   drained by the next session start or end. That is also why a busy lock
#   is answered by leaving the entry queued rather than by running
#   unsynchronized.
#
#   The entry exists before the PID file disappears, so a sweep running at
#   that moment always sees one of the two and never mistakes an ending
#   session for a stray.
#
# Sourced by end-session-transcript.sh, rotate-on-session-end.sh,
# sweep-transcript-orphans.sh and launch-transcript-sync.sh. Every function
# takes the .agent directory as its first argument; nothing here reads a
# caller's globals.

# shellcheck shell=bash

# Seconds a worker waits for the lock before leaving its work queued.
TRANSCRIPT_ROTATION_LOCK_WAIT="${TRANSCRIPT_ROTATION_LOCK_WAIT:-600}"

rotation_queue_dir() {
    echo "$1/.rotation-queue"
}

rotation_lock_file() {
    echo "$1/.rotation.lock"
}

rotation_log_file() {
    echo "$1/.transcript-rotation.log"
}

# rotation_is_queued <agent_dir> <session_id>
rotation_is_queued() {
    compgen -G "$1/.rotation-queue/*.$2" >/dev/null 2>&1
}

# rotation_enqueue <agent_dir> <session_id>
# Idempotent per session: a second end event for a queued session adds
# nothing. Returns non-zero only if the entry could not be written.
rotation_enqueue() {
    local qdir="$1/.rotation-queue"
    mkdir -p "$qdir" 2>/dev/null || return 1
    chmod 700 "$qdir" 2>/dev/null || true
    if rotation_is_queued "$1" "$2"; then
        return 0
    fi
    : > "$qdir/$(date -u +%Y%m%dT%H%M%SZ).$2" 2>/dev/null
}

# rotation_dequeue <agent_dir> <session_id>
rotation_dequeue() {
    local entry
    while IFS= read -r entry; do
        [[ -n "$entry" ]] && rm -f "$entry"
    done < <(compgen -G "$1/.rotation-queue/*.$2" 2>/dev/null || true)
    return 0
}

# rotation_session_is_live <agent_dir> <session_id>
# True when the session has a PID file naming a live watcher: it was resumed
# after its end event, so its files belong to a running session again.
rotation_session_is_live() {
    local pid_file="$1/.transcript-sync.$2.pid" pid
    [[ -f "$pid_file" ]] || return 1
    pid=$(cat "$pid_file" 2>/dev/null || true)
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

# transcript_detach <agent_dir> <command> [args...]
# Run a worker that outlives the hook. Its own session (setsid) keeps it out
# of the process group a harness kills on timeout. stdout and stderr go to
# the rotation log, never to /dev/null: the scrubber's "configured secret
# skipped" line and every "could not archive" warning are diagnostics that
# used to reach the hook's stderr.
transcript_detach() {
    local log
    log="$(rotation_log_file "$1")"
    shift
    ( umask 077; : >> "$log" ) 2>/dev/null || log=/dev/null
    if command -v setsid >/dev/null 2>&1; then
        setsid nohup "$@" </dev/null >>"$log" 2>&1 &
    else
        nohup "$@" </dev/null >>"$log" 2>&1 &
    fi
    disown 2>/dev/null || true
    return 0
}

# rotation_log_trim <agent_dir>
# Keep the log bounded: past 256 KiB, keep the last 64 KiB. Called by
# workers under the lock.
rotation_log_trim() {
    local log size
    log="$(rotation_log_file "$1")"
    [[ -f "$log" ]] || return 0
    size=$(wc -c < "$log" 2>/dev/null || echo 0)
    if [[ "$size" -gt 262144 ]]; then
        tail -c 65536 "$log" > "$log.trim.$$" 2>/dev/null \
            && mv -f "$log.trim.$$" "$log" 2>/dev/null
        rm -f "$log.trim.$$" 2>/dev/null
    fi
    return 0
}
