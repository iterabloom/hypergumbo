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
#   - Crashed sessions' files are archived by sweep-transcript-orphans.sh,
#     which this script runs DETACHED and only AFTER the watcher is up. The
#     sweep compresses and scrubs whole transcripts; run inline and ahead of
#     the launch (as it was), one un-rotated 453 MB transcript cut the
#     session-start hook off at its 5 s budget and the session ran with no
#     watcher. This script does O(1) work.
#
# Same-SID idempotence guard:
#   Claude Code (and other vendors) fire SessionStart on multiple lifecycle
#   events with the SAME session_id — startup, resume, /clear, and /compact
#   all re-fire the hook. Without the guard below, each re-fire would launch
#   a second watcher on top of the live one, both tailing the same vendor
#   source and racing on the unlocked filter-transcript.py state file. The
#   result was uniform 2x duplication of every event in the per-session DEST
#   (observed in archived session fb7b3494 and 3 other archived sessions
#   on April 21–26 2026, ~3% of all sessions). The guard here mirrors the
#   two-phase cleanup in kill-transcript-sync.sh.
#
# This script does NOT call kill-transcript-sync.sh against SIBLING sessions,
# because their per-session DEST is isolated. Cleanup of stale PID files from
# CRASHED prior sessions is handled by the detached sweep.

set -euo pipefail

SRC="$1"
SESSION_ID="$2"
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")"/../../.. && pwd)}"
SCRIPT_DIR="$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Permission contract (INV-todig): the watcher and the sweep launched at the
# bottom inherit this process's umask — owner-only for both.
# shellcheck source=/dev/null
. "$SCRIPT_DIR/transcript_perms.sh"
harden_transcript_umask
# shellcheck source=/dev/null
. "$SCRIPT_DIR/transcript_rotation_queue.sh"
AGENT_DIR="$REPO_ROOT/.agent"
DEST="$AGENT_DIR/.current_session_transcript.${SESSION_ID}.jsonl"

mkdir -p "$AGENT_DIR"

# This session is starting, so a rotation still queued under its id is void:
# the session was resumed before its end was processed. Dropped before the
# watcher exists, so no drain can rotate a file the watcher is writing.
rotation_dequeue "$AGENT_DIR" "$SESSION_ID"

# ---------------------------------------------------------------------------
# Same-SID idempotence guard (two-phase, mirrors kill-transcript-sync.sh).
# ---------------------------------------------------------------------------
SELF_PID_FILE="$AGENT_DIR/.transcript-sync.${SESSION_ID}.pid"

# Phase 1: PID-file path.
if [[ -f "$SELF_PID_FILE" ]]; then
    self_pid=$(cat "$SELF_PID_FILE" 2>/dev/null || true)
    if [[ -n "$self_pid" ]] && kill -0 "$self_pid" 2>/dev/null; then
        kill "$self_pid" 2>/dev/null || true
        # Brief wait for the watcher's EXIT trap to fire so its inotifywait
        # is gone and we don't have two writers racing during the new launch.
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$self_pid" 2>/dev/null || break
            sleep 0.1
        done
    fi
    rm -f "$SELF_PID_FILE"
fi

# Phase 2: pgrep fallback. Catches a prior watcher whose PID file was lost
# (disk-full, manual rm, crash mid-write) but whose process is still alive.
# Match by SESSION_ID at offset +3 from the script-name field, identically
# to kill-transcript-sync.sh's Phase 2 — that's the structural shape of a
# real watcher's command line.
while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    pid="${line%% *}"
    [[ "$pid" == "$$" ]] && continue
    proc_sid=$(awk '{
        for (i=2; i<=NF; i++) {
            if ($i ~ /sync-transcript\.sh$/) {
                print $(i+3)
                exit
            }
        }
    }' <<< "$line")
    if [[ -n "$proc_sid" && "$proc_sid" == "$SESSION_ID" ]]; then
        kill "$pid" 2>/dev/null || true
    fi
done < <(pgrep -af 'sync-transcript\.sh' 2>/dev/null || true)

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
# per-session sweeps because they don't have a session_id suffix.
rm -f "$AGENT_DIR/.transcript-sync-state.json"
rm -f "$AGENT_DIR/.transcript-poll-state"
rm -f "$AGENT_DIR/.transcript-injection-state.json"
rm -f "$AGENT_DIR/.transcript-session-token"
rm -f "$LEGACY_DEST"

# ---------------------------------------------------------------------------
# Name the OWNER of this watcher — the harness process whose session it
# mirrors — so the watcher can exit on its own when that process is gone
# (sync-transcript.sh, "Lifetime contract"). Precedence: an explicit
# TRANSCRIPT_OWNER_PID; the PID Claude Code exports to its hooks as
# CLAUDE_PID; else the first ancestor of this script that is not a shell,
# which for every vendor wrapper in .agent/hooks/<vendor>/ is the CLI process
# itself. Empty when nothing can be determined — the watcher then keeps the
# pre-contract behaviour and relies on the session-end hook alone.
# ---------------------------------------------------------------------------
resolve_owner_pid() {
    if [[ -n "${TRANSCRIPT_OWNER_PID:-}" ]]; then
        echo "$TRANSCRIPT_OWNER_PID"
        return 0
    fi
    if [[ -n "${CLAUDE_PID:-}" ]]; then
        echo "$CLAUDE_PID"
        return 0
    fi
    local p="$PPID" comm
    while [[ -n "$p" && "$p" -gt 1 ]]; do
        comm="$(ps -o comm= -p "$p" 2>/dev/null | tr -d ' ')"
        case "$comm" in
            bash|sh|dash|zsh|ksh|nohup|setsid|timeout)
                p="$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')" ;;
            "")
                echo ""
                return 0 ;;
            *)
                echo "$p"
                return 0 ;;
        esac
    done
    echo ""
}
OWNER_PID="$(resolve_owner_pid)"

# ---------------------------------------------------------------------------
# Launch the watcher in the background (survives hook exit).
# ---------------------------------------------------------------------------
TRANSCRIPT_OWNER_PID="$OWNER_PID" \
    nohup "$SCRIPT_DIR/sync-transcript.sh" "$SRC" "$DEST" "$SESSION_ID" \
    </dev/null >/dev/null 2>&1 &
disown

# ---------------------------------------------------------------------------
# Only now the slow part, and not in this process: archive what crashed or
# un-rotated sessions left behind.
# ---------------------------------------------------------------------------
transcript_detach "$AGENT_DIR" \
    bash "$SCRIPT_DIR/sweep-transcript-orphans.sh" "$REPO_ROOT" "$SESSION_ID"
