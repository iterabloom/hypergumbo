#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_SCRIPT="$SCRIPT_DIR/../_shared/poll-transcript-change.sh"

# Capture stdin and extract session_id (cursor-singleton constant under
# the per-session pipeline; see cursor/session-start.sh for rationale).
STDIN_JSON=$(cat)
source "$SCRIPT_DIR/../_shared/session_id_helpers.sh"
SESSION_ID=$(extract_cursor_session_id "$STDIN_JSON")

if [[ -z "$SESSION_ID" ]]; then
    exit 0
fi

HOOK_OUTPUT=$("$POLL_SCRIPT" "$SESSION_ID" 2>/dev/null) || exit 0

if [[ -z "$HOOK_OUTPUT" ]]; then
    exit 0
fi

ESCAPED=$(echo "$HOOK_OUTPUT" | jq -Rs .)
echo "{\"additional_context\":$ESCAPED}"
