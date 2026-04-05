#!/bin/bash
# Cursor afterAgentResponse hook adapter for transcript feedback.
#
# Fires after each assistant response. Polls the filtered transcript
# for new content and injects it as additional_context.
#
# NOTE (March 2026): additional_context on afterAgentResponse is
# documented but currently broken (agent_message/additional_context
# silently dropped since v2.0.64). This hook is written to the
# documented spec and will work once Cursor fixes the regression.
#
# Configured in .cursor/hooks.json under afterAgentResponse.

set -euo pipefail

# Drain stdin (Cursor passes response JSON)
cat > /dev/null

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_SCRIPT="$SCRIPT_DIR/../_shared/poll-transcript-change.sh"

HOOK_OUTPUT=$("$POLL_SCRIPT" 2>/dev/null) || exit 0

if [[ -z "$HOOK_OUTPUT" ]]; then
    exit 0
fi

ESCAPED=$(echo "$HOOK_OUTPUT" | jq -Rs .)
echo "{\"additional_context\":$ESCAPED}"
