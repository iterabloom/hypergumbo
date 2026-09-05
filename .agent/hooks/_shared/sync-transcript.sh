#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# sync-transcript.sh — Long-running watcher that mirrors a session transcript
# to a per-session, vendor-agnostic location inside the repo.
#
# Launched in background by session-start hooks; killed by session-end hooks,
# and exits on its own when the OWNER process named in TRANSCRIPT_OWNER_PID is
# gone (see "Lifetime contract" below).
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

# Permission contract (INV-todig): everything this watcher creates — the
# per-session DEST via the filter, the state file, the PID file — is
# owner-only, because transcripts republish command output verbatim.
# shellcheck source=/dev/null
. "$REPO_ROOT/.agent/hooks/_shared/transcript_perms.sh"
harden_transcript_umask

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

# ---------------------------------------------------------------------------
# Lifetime contract (2026-09-05): a watcher must not outlive the session that
# launched it, and no child of the watcher may outlive the watcher.
#
# Two orphans were found live after a session crash, each holding one of the
# 128 per-user inotify instances: an `inotifywait -e close_write` with PPID 1
# and no `-t`, 50 hours into a wait on a transcript nobody would write again
# (its bash parent took a SIGTERM it did not trap, died at once, and left the
# foreground child behind); and a whole watcher 26 hours into Phase 1 for a
# source that never appeared, because the crashed session never ran its
# session-end hook. Three mechanisms close both:
#   1. every inotifywait carries -t, so a child orphaned by ANY parent death
#      returns within TRANSCRIPT_WATCH_TIMEOUT seconds instead of never;
#   2. the watch runs in the background under `wait`, so a trapped
#      TERM/INT/HUP interrupts it and the handler kills the child before
#      exiting (a foreground child defers the trap until it returns — which,
#      for an unbounded watch, was never);
#   3. the launcher names the OWNER process (the harness) in
#      TRANSCRIPT_OWNER_PID and both phases check it on every iteration — by
#      PID and, when /proc is readable, by start time, so a recycled PID does
#      not keep a dead session's watcher alive.
# An empty TRANSCRIPT_OWNER_PID (a vendor hook that cannot name one) keeps the
# pre-contract behaviour: the session-end hook is then the only terminator.
# ---------------------------------------------------------------------------
OWNER_PID="${TRANSCRIPT_OWNER_PID:-}"
WATCH_TIMEOUT="${TRANSCRIPT_WATCH_TIMEOUT:-60}"

# Start time (clock ticks since boot) of a PID, or "" when /proc is not
# readable. Field 22 of /proc/PID/stat; the comm field before it may contain
# spaces, so strip through the closing paren first.
proc_start() {
    if [[ ! -r "/proc/$1/stat" ]]; then
        echo ""
        return 0
    fi
    sed 's/.*) //' "/proc/$1/stat" 2>/dev/null | awk '{print $20}'
}
OWNER_START=""
if [[ -n "$OWNER_PID" ]]; then
    OWNER_START="$(proc_start "$OWNER_PID")"
fi

owner_alive() {
    if [[ -z "$OWNER_PID" ]]; then
        return 0
    fi
    kill -0 "$OWNER_PID" 2>/dev/null || return 1
    if [[ -z "$OWNER_START" ]]; then
        return 0
    fi
    [[ "$(proc_start "$OWNER_PID")" == "$OWNER_START" ]]
}

WATCH_CHILD=""
on_signal() {
    if [[ -n "$WATCH_CHILD" ]]; then
        kill "$WATCH_CHILD" 2>/dev/null || true
    fi
    exit 0
}
trap on_signal TERM INT HUP

# Run inotifywait in the background and wait on it (mechanism 2). Returns the
# child's exit code: 0 event, 2 timeout, anything else failure.
watch() {
    local rc=0
    inotifywait -qq "$@" 2>/dev/null &
    WATCH_CHILD=$!
    wait "$WATCH_CHILD" || rc=$?
    WATCH_CHILD=""
    return "$rc"
}

owner_alive || exit 0

# Backoff bounds for the degraded path below. A failure path with no delay
# is what turned a resource shortage into a CPU meltdown (see Phase 2).
WATCH_BACKOFF_MIN=1
WATCH_BACKOFF_MAX=30
backoff=$WATCH_BACKOFF_MIN

# A source that exists but is not a REGULAR file can never accumulate
# transcript lines — /dev/null is passed by sandboxed harnesses. Phase 1's
# `-f` test can never be satisfied by it, so without this guard the watcher
# waits forever for a file that will never appear. One such process ran for
# 7 days and burned 13.9 hours of CPU.
if [[ -e "$SRC" && ! -f "$SRC" ]]; then
    exit 0
fi

# Phase 1: Wait for the source file to exist (may not be created until
# the first message is sent in the session).
SRC_DIR="$(dirname "$SRC")"
while [[ ! -f "$SRC" ]]; do
    owner_alive || exit 0
    rc=0
    watch -t 5 -e create "$SRC_DIR" || rc=$?
    # rc 2 is the -t timeout firing, which IS the pacing we asked for.
    # rc 1 means inotify itself is unavailable and returns instantly — the
    # `|| true` this replaces swallowed that into a tight spin.
    if [[ $rc -eq 0 || $rc -eq 2 ]]; then
        backoff=$WATCH_BACKOFF_MIN
    else
        sleep "$backoff"
        backoff=$(( backoff * 2 ))
        if (( backoff > WATCH_BACKOFF_MAX )); then
            backoff=$WATCH_BACKOFF_MAX
        fi
    fi
done
backoff=$WATCH_BACKOFF_MIN

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

# Heal a DEST/state pair a pre-contract session left at 664 (mv and the
# scrubber both preserve modes, so nothing else ever tightens them).
harden_transcript_file "$DEST" "$STATE_FILE"

# Phase 2: Watch for changes and filter on each write.
# The downstream hook (on_transcript_change.sh) is NOT called from here.
# Instead, the AI tool's own FileChanged hook watches the destination file
# and calls the hook — this lets the hook's stdout be injected back into
# the agent's conversation as context.
#
# DEGRADED MODE, and why it is not just a sleep. `inotifywait` fails
# instantly when fs.inotify.max_user_instances is exhausted — observed in
# production at 128/128, held by unrelated processes. The previous error
# path ran `continue` with no delay, which had two consequences, both bad:
# the watcher spun (1988 invocations in 3 seconds, measured; 114 million
# context switches on one long-lived process), AND it never called do_sync
# on that path, so the mirror silently stopped updating. It burned a core
# and published nothing. So on failure we sleep, sync ANYWAY — polling is
# a worse watch, not no watch — and widen the interval while it keeps
# failing, resetting the moment a real watch succeeds.
while true; do
    owner_alive || exit 0
    rc=0
    watch -t "$WATCH_TIMEOUT" -e close_write "$SRC" || rc=$?
    if [[ $rc -eq 0 ]]; then
        backoff=$WATCH_BACKOFF_MIN
        do_sync || true
        continue
    fi
    # rc 2 is -t expiring with no event. Not a failure: reset the backoff,
    # sync anyway (closes the microsecond re-arm gap between watches), and
    # let the loop re-check the owner. The timeout is what bounds a child
    # orphaned by a parent death the trap never saw (SIGKILL, OOM).
    if [[ $rc -eq 2 ]]; then
        backoff=$WATCH_BACKOFF_MIN
        do_sync || true
        continue
    fi
    # Source file may have been deleted or recreated
    if [[ ! -f "$SRC" ]]; then
        break
    fi
    sleep "$backoff"
    do_sync || true
    backoff=$(( backoff * 2 ))
    if (( backoff > WATCH_BACKOFF_MAX )); then
        backoff=$WATCH_BACKOFF_MAX
    fi
done
