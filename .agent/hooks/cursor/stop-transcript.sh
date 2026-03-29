#!/bin/bash
# Cursor stop hook adapter for transcript feedback.
#
# Cursor lacks a FileChanged hook, and most context injection mechanisms
# are broken (additional_context, agentMessage, userMessage). The only
# reliable injection is the stop hook's followup_message, which auto-submits
# as the next user message and can trigger another agent turn.
#
# Configured in .cursor/hooks.json under stop.

set -euo pipefail

# Drain stdin (Cursor passes stop event JSON)
cat > /dev/null

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_SCRIPT="$SCRIPT_DIR/../_shared/poll-transcript-change.sh"

HOOK_OUTPUT=$("$POLL_SCRIPT" 2>/dev/null) || exit 0

# No output from hook — nothing to inject
if [[ -z "$HOOK_OUTPUT" ]]; then
    exit 0
fi

# Wrap as a followup_message (auto-submitted as next user prompt)
ESCAPED=$(echo "$HOOK_OUTPUT" | jq -Rs .)
echo "{\"followup_message\":$ESCAPED}"
