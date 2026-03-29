#!/bin/bash
# poll-transcript-change.sh — Check if the filtered transcript has new content
# since the last poll. If so, call on_transcript_change.sh and output its result.
#
# Used by tools that lack a FileChanged hook (Codex, Gemini, Cursor).
# These tools poll from their own lifecycle hooks (PostToolUse, AfterAgent, stop).
#
# Usage: poll-transcript-change.sh
# Outputs: whatever on_transcript_change.sh outputs (if there's new content),
#          or nothing (if the transcript hasn't changed).
# Exit code: 0 if new content was found and hook ran, 1 if no changes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
TRANSCRIPT="$REPO_ROOT/.agent/.current_session_transcript.jsonl"
POLL_STATE="$REPO_ROOT/.agent/.transcript-poll-state"
HOOK_SCRIPT="$SCRIPT_DIR/on_transcript_change.sh"

# No transcript file yet — nothing to do
if [[ ! -f "$TRANSCRIPT" ]]; then
    exit 1
fi

CURRENT_SIZE=$(stat -c%s "$TRANSCRIPT" 2>/dev/null || echo 0)
LAST_SIZE=0
if [[ -f "$POLL_STATE" ]]; then
    LAST_SIZE=$(cat "$POLL_STATE" 2>/dev/null || echo 0)
fi

# No new content
if [[ "$CURRENT_SIZE" -le "$LAST_SIZE" ]]; then
    exit 1
fi

# Record new size before calling hook (so concurrent calls don't double-fire)
echo "$CURRENT_SIZE" > "$POLL_STATE"

# Call the hook if it exists and is executable
if [[ -x "$HOOK_SCRIPT" ]]; then
    exec "$HOOK_SCRIPT" "$TRANSCRIPT"
fi

exit 1
