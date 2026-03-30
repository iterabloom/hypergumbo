#!/bin/bash
# Claude Code PostToolUse hook adapter for transcript feedback.
#
# Claude Code lacks a working FileChanged hook (broken as of v2.1.87),
# so we poll the filtered transcript after each tool call. If new content
# exists, on_transcript_change.sh is called and its output is wrapped in
# Claude Code's additionalContext JSON format for injection.
#
# Plain stdout from PostToolUse hooks is NOT injected into conversation.
# Must use: {"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"..."}}
#
# The poll script exits 1 when there's nothing new. We swallow that to
# avoid "hook error" noise in Claude Code (non-zero exit = error).
#
# Configured in .claude/settings.json under PostToolUse.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_SCRIPT="$SCRIPT_DIR/../_shared/poll-transcript-change.sh"

HOOK_OUTPUT=$("$POLL_SCRIPT" 2>/dev/null) || exit 0

# No output from hook — nothing to inject
if [[ -z "$HOOK_OUTPUT" ]]; then
    exit 0
fi

# Wrap in Claude Code's expected JSON format
ESCAPED=$(echo "$HOOK_OUTPUT" | jq -Rs .)
cat <<ENDJSON
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": $ESCAPED
  }
}
ENDJSON
