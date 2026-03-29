#!/bin/bash
# kill-transcript-sync.sh — Stop the background transcript sync watcher.
# Called by each tool's session-end hook. Idempotent.
#
# Usage: source this script (needs REPO_ROOT set), or run directly with $1=REPO_ROOT

set -euo pipefail

REPO_ROOT="${1:-${REPO_ROOT:?REPO_ROOT must be set}}"
SYNC_PID_FILE="$REPO_ROOT/.agent/.transcript-sync.pid"

if [[ -f "$SYNC_PID_FILE" ]]; then
    SYNC_PID=$(cat "$SYNC_PID_FILE" 2>/dev/null || true)
    if [[ -n "$SYNC_PID" ]] && kill -0 "$SYNC_PID" 2>/dev/null; then
        kill "$SYNC_PID" 2>/dev/null || true
    fi
    rm -f "$SYNC_PID_FILE"
fi
