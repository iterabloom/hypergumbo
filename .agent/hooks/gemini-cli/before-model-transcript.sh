#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Gemini CLI BeforeModel hook adapter for transcript feedback.
#
# BeforeModel fires before EVERY LLM API call (including retries and
# multi-step tool-use chains within a single turn). This gives us the
# tightest possible feedback loop — if the filtered transcript has new
# content, we can inject it as an additional message in the LLM request
# so the model sees it before generating its next response.
#
# The hook modifies the outgoing request by appending a user message
# to the messages array via hookSpecificOutput.llm_request.messages.
#
# Configured in .gemini/settings.json under BeforeModel.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_SCRIPT="$SCRIPT_DIR/../_shared/poll-transcript-change.sh"

# Capture stdin and extract session_id so the poller can find this
# session's per-session transcript file.
STDIN_JSON=$(cat)
source "$SCRIPT_DIR/../_shared/session_id_helpers.sh"
SESSION_ID=$(extract_gemini_session_id "$STDIN_JSON")

if [[ -z "$SESSION_ID" ]]; then
    echo '{"decision":"allow"}'
    exit 0
fi

# WI-sipov: touch per-session heartbeat for supervisor telemetry.
source "$SCRIPT_DIR/../_shared/touch_heartbeat.sh"
touch_heartbeat "$SESSION_ID"

HOOK_OUTPUT=$("$POLL_SCRIPT" "$SESSION_ID" 2>/dev/null) || {
    # No new content — allow request to proceed unmodified
    echo '{"decision":"allow"}'
    exit 0
}

if [[ -z "$HOOK_OUTPUT" ]]; then
    echo '{"decision":"allow"}'
    exit 0
fi

# Inject as an additional user message in the outgoing LLM request.
# This is appended to the messages array, so the model sees it as
# the most recent context before generating its response.
ESCAPED=$(echo "$HOOK_OUTPUT" | jq -Rs .)
cat <<ENDJSON
{
  "decision": "allow",
  "hookSpecificOutput": {
    "llm_request": {
      "messages": [
        {
          "role": "user",
          "content": $ESCAPED
        }
      ]
    }
  }
}
ENDJSON
