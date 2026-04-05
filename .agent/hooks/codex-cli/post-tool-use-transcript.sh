#!/bin/bash
# Codex CLI PostToolUse hook adapter for transcript feedback.
#
# Codex CLI lacks a FileChanged hook, so we poll the filtered transcript
# after each tool call. If new content exists, on_transcript_change.sh
# is called and its output is wrapped in Codex's additionalContext JSON
# format for injection into the conversation.
#
# Configured in .codex/hooks.json under PostToolUse.

set -euo pipefail

# Drain stdin (Codex passes tool result JSON)
cat > /dev/null

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_SCRIPT="$SCRIPT_DIR/../_shared/poll-transcript-change.sh"

HOOK_OUTPUT=$("$POLL_SCRIPT" 2>/dev/null) || exit 0

# No output from hook — nothing to inject
if [[ -z "$HOOK_OUTPUT" ]]; then
    exit 0
fi

# Wrap in Codex's expected JSON format
ESCAPED=$(echo "$HOOK_OUTPUT" | jq -Rs .)
echo "{\"additionalContext\":$ESCAPED}"
