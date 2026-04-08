#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# launch-transcript-sync.sh — Start the background transcript sync watcher.
# Called by each tool's session-start hook.
#
# Usage: launch-transcript-sync.sh <source-transcript-path> [REPO_ROOT]
# Requires: REPO_ROOT environment variable (or pass as $2).
#
# Kills any stale watcher (via the kill script's pgrep fallback) before
# launching a new one. The kill script is the single source of truth for
# how to find and terminate watchers; this script just delegates.

set -euo pipefail

SRC="$1"
REPO_ROOT="${2:-${REPO_ROOT:?REPO_ROOT must be set}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$REPO_ROOT/.agent/.current_session_transcript.jsonl"

# Kill any stale watcher for THIS repo. The kill script's two-phase logic
# (PID file + pgrep fallback scoped by DEST) catches both the happy path
# and the leak case where the PID file is missing or stale. This replaces
# the prior PID-file-only guard, which was blind to the 13 orphans that
# accumulated Apr 5–7 2026.
"$SCRIPT_DIR/kill-transcript-sync.sh" "$REPO_ROOT"

# Launch watcher in background (survives hook exit)
nohup "$SCRIPT_DIR/sync-transcript.sh" "$SRC" "$DEST" \
    </dev/null >/dev/null 2>&1 &
disown
