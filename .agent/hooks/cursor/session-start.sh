#!/bin/bash
# Cursor sessionStart hook adapter
# See ADR-0008 for governance protocol
#
# Detects stale/OFF autonomous mode and injects a prompt asking the
# agent to query the user for mode selection.
#
# Cursor: stdout is injected as context. JSON with additionalContext
# field is also supported.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Read stdin (Cursor passes JSON input)
cat > /dev/null

# --- Shared logic (sets SESSION_START_MESSAGE, SESSION_START_NEEDS_PROMPT) ---
source "$SCRIPT_DIR/../_shared/session_start_logic.sh"

if [[ "$SESSION_START_NEEDS_PROMPT" == "true" && -n "$SESSION_START_MESSAGE" ]]; then
    echo "$SESSION_START_MESSAGE"
fi
