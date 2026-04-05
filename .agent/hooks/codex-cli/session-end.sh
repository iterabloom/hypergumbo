#!/bin/bash
# Codex CLI session-end hook adapter
# See ADR-0008 for governance protocol
#
# Disables autonomous mode when the user ends their session, since the
# stop hook's PID binding becomes stale once the session exits.
#
# LIMITATION: Codex CLI may not support session-end hooks natively.
# This script exists for forward-compatibility and can be wired up
# manually (e.g., via shell trap or .bashrc EXIT hook).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# --- Shared logic (sets SESSION_END_MODE, SESSION_END_ACTED) ---
source "$SCRIPT_DIR/../_shared/session_end_logic.sh"

if [[ "$SESSION_END_ACTED" == "true" ]]; then
    cat >&2 <<BANNER
================================================================
  Autonomous mode disabled (was: ${SESSION_END_MODE}).
  Run ./scripts/loop-toggle ${SESSION_END_MODE} to re-enable.
================================================================
BANNER
fi

# --- Kill transcript sync watcher ---
"$SCRIPT_DIR/../_shared/kill-transcript-sync.sh" "$REPO_ROOT"
