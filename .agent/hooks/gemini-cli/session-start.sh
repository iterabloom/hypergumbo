#!/bin/bash
# Gemini CLI SessionStart hook adapter
# See ADR-0008 for governance protocol
#
# Detects stale/OFF autonomous mode and injects a prompt asking the
# agent to query the user for mode selection.
#
# Gemini CLI: MUST output JSON only. Plain text causes failures.
# Uses decision: "allow" with reason field to inject context.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Read stdin (Gemini CLI passes JSON input)
cat > /dev/null

# --- Shared logic (sets SESSION_START_MESSAGE, SESSION_START_NEEDS_PROMPT) ---
source "$SCRIPT_DIR/../_shared/session_start_logic.sh"

if [[ "$SESSION_START_NEEDS_PROMPT" == "true" && -n "$SESSION_START_MESSAGE" ]]; then
    REASON=$(echo "$SESSION_START_MESSAGE" | jq -Rs .)
    echo "{\"decision\":\"allow\",\"reason\":$REASON}"
else
    echo '{"decision":"allow"}'
fi
