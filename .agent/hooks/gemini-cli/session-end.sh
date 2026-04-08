#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Gemini CLI session-end hook adapter
# See ADR-0008 for governance protocol
#
# Disables autonomous mode when the user ends their session, since the
# stop hook's PID binding becomes stale once the session exits.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# --- Capture stdin so session_id helper can parse it ---
STDIN_JSON=$(cat)

# --- Shared logic (sets SESSION_END_MODE, SESSION_END_ACTED) ---
source "$SCRIPT_DIR/../_shared/session_end_logic.sh"

if [[ "$SESSION_END_ACTED" == "true" ]]; then
    echo "Disabling autonomous mode since you are ending the session; run \`./scripts/loop-toggle ${SESSION_END_MODE}\` if you want autonomous mode to be active the next time you start a session in this shell." >&2
fi

# --- Kill this session's transcript sync watcher and rotate its files ---
source "$SCRIPT_DIR/../_shared/session_id_helpers.sh"
SESSION_ID=$(extract_gemini_session_id "$STDIN_JSON")

if [[ -n "$SESSION_ID" ]]; then
    "$SCRIPT_DIR/../_shared/kill-transcript-sync.sh" "$REPO_ROOT" "$SESSION_ID"
    "$SCRIPT_DIR/../_shared/rotate-on-session-end.sh" "$REPO_ROOT" "$SESSION_ID"
fi
