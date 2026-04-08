#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# kill-transcript-sync.sh — Stop a single session's transcript sync watcher.
# Called by each tool's session-end hook. Idempotent.
#
# Usage: kill-transcript-sync.sh <REPO_ROOT> <SESSION_ID>
#
# Per-session isolation (ADR-0018 amendment / Option 2):
#   - This script kills ONLY the watcher belonging to <SESSION_ID>. A live
#     sibling session in the same repo with a different SESSION_ID is never
#     touched.
#   - Two-phase cleanup:
#       1. PID file path (the happy path) — kill the watcher whose PID is
#          stored in .agent/.transcript-sync.<SESSION_ID>.pid.
#       2. pgrep fallback (catches the rare case where the per-session PID
#          file went missing) — scan for sync-transcript.sh processes whose
#          third positional argument equals <SESSION_ID>, and kill them.
#
# Rotation of this session's per-session current files into the global
# .last_*/.second_to_last_* slots is handled by rotate-on-session-end.sh,
# which the session-end hook calls AFTER this script. Keeping kill and
# rotate as separate scripts means we never accidentally rotate orphans.

set -euo pipefail

REPO_ROOT="${1:-${REPO_ROOT:?REPO_ROOT must be set}}"
SESSION_ID="${2:?SESSION_ID is required}"
SYNC_PID_FILE="$REPO_ROOT/.agent/.transcript-sync.${SESSION_ID}.pid"

# Phase 1: PID-file path (fast happy path).
if [[ -f "$SYNC_PID_FILE" ]]; then
    SYNC_PID=$(cat "$SYNC_PID_FILE" 2>/dev/null || true)
    if [[ -n "$SYNC_PID" ]] && kill -0 "$SYNC_PID" 2>/dev/null; then
        kill "$SYNC_PID" 2>/dev/null || true
    fi
    rm -f "$SYNC_PID_FILE"
fi

# Phase 2: pgrep fallback. The PID-file path above misses any watcher whose
# PID file was lost (e.g., disk full during write, or manual `rm`). Scan
# every running sync-transcript.sh process and kill those whose third
# positional argument equals our SESSION_ID. Matching by SESSION_ID (not
# DEST) is the structural fix for the watcher-leak bug — the prior
# DEST-matching loop killed sibling sessions in the same repo because they
# all shared a single global DEST.
#
# Two-phase parsing of `pgrep -af` output:
#   1. PID is the first whitespace-delimited field
#   2. The SESSION_ID argument is the third positional arg AFTER the field
#      whose value ends in 'sync-transcript.sh' (i.e., the script path).
#      For a real watcher, the command line is:
#        bash /path/to/sync-transcript.sh <SRC> <DEST> <SESSION_ID>
#      so SESSION_ID is at offset +3 from the script-name field.
while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    pid="${line%% *}"
    # Defensive: never kill ourselves.
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
