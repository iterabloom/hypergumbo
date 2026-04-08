#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# kill-transcript-sync.sh — Stop background transcript sync watchers for this repo.
# Called by each tool's session-end hook. Idempotent.
#
# Usage: kill-transcript-sync.sh <REPO_ROOT>
#
# Two-phase cleanup:
#   1. PID file path (the happy path) — kill the watcher whose PID is stored
#      in .agent/.transcript-sync.pid.
#   2. pgrep fallback (catches leaked watchers when the PID file is missing,
#      stale, or was clobbered by an EXIT-trap race) — scan for
#      sync-transcript.sh processes whose DEST argument matches this repo's
#      expected destination, and kill them.
#
# Scoping by DEST naturally restricts to "watchers for THIS repo," so a
# concurrent Claude Code session in a DIFFERENT repo is never touched.

set -euo pipefail

REPO_ROOT="${1:-${REPO_ROOT:?REPO_ROOT must be set}}"
SYNC_PID_FILE="$REPO_ROOT/.agent/.transcript-sync.pid"
EXPECTED_DEST="$REPO_ROOT/.agent/.current_session_transcript.jsonl"

# Phase 1: PID-file path (fast happy path).
if [[ -f "$SYNC_PID_FILE" ]]; then
    SYNC_PID=$(cat "$SYNC_PID_FILE" 2>/dev/null || true)
    if [[ -n "$SYNC_PID" ]] && kill -0 "$SYNC_PID" 2>/dev/null; then
        kill "$SYNC_PID" 2>/dev/null || true
    fi
    rm -f "$SYNC_PID_FILE"
fi

# Phase 2: pgrep fallback. The PID-file path above misses any watcher whose
# PID file was lost (e.g., overwritten by a racing session start, or removed
# by an EXIT trap that has since been fixed but left orphans behind). Scan
# every running sync-transcript.sh process and kill those whose DEST argument
# matches this repo's expected destination.
#
# Two-phase parsing of `pgrep -af` output:
#   1. PID is the first whitespace-delimited field
#   2. The DEST argument is the second positional arg AFTER the field whose
#      value ends in 'sync-transcript.sh' (i.e., the script path itself).
#      For a real watcher, the command line is:
#        bash /path/to/sync-transcript.sh <SRC> <DEST>
#      so DEST is at offset +2 from the script-name field.
while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    pid="${line%% *}"
    # Defensive: never kill ourselves.
    [[ "$pid" == "$$" ]] && continue
    dest=$(awk '{
        for (i=2; i<=NF; i++) {
            if ($i ~ /sync-transcript\.sh$/) {
                print $(i+2)
                exit
            }
        }
    }' <<< "$line")
    if [[ -n "$dest" && "$dest" == "$EXPECTED_DEST" ]]; then
        kill "$pid" 2>/dev/null || true
    fi
done < <(pgrep -af 'sync-transcript\.sh' 2>/dev/null || true)
