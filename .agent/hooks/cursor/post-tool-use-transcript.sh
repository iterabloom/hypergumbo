#!/bin/bash
# Cursor postToolUse hook adapter for transcript feedback.
#
# Fires after each tool call. Polls the filtered transcript for new
# content and injects it as additional_context.
#
# NOTE (March 2026): additional_context on postToolUse is documented
# but currently broken (accepted and logged but never surfaced to the
# model, confirmed by Cursor team). This hook is written to the
# documented spec and will work once Cursor fixes the bug.
#
# Configured in .cursor/hooks.json under postToolUse.

set -euo pipefail

# Drain stdin (Cursor passes tool result JSON)
cat > /dev/null

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_SCRIPT="$SCRIPT_DIR/../_shared/poll-transcript-change.sh"

HOOK_OUTPUT=$("$POLL_SCRIPT" 2>/dev/null) || exit 0

if [[ -z "$HOOK_OUTPUT" ]]; then
    exit 0
fi

ESCAPED=$(echo "$HOOK_OUTPUT" | jq -Rs .)
echo "{\"additional_context\":$ESCAPED}"
