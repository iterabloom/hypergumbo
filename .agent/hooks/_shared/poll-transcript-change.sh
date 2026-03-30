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

# If the file shrank, a new session started — reset so we process the new file.
# (Mirrors filter-transcript.py's offset-reset logic for truncated files.)
if [[ "$CURRENT_SIZE" -lt "$LAST_SIZE" ]]; then
    LAST_SIZE=0
fi

# No new content
if [[ "$CURRENT_SIZE" -le "$LAST_SIZE" ]]; then
    exit 1
fi

# Call the hook if it exists and is executable.
# Record the new size AFTER the hook succeeds — if the hook fails or the
# process is killed, the next poll will re-process this chunk rather than
# silently skipping it. The hook is idempotent (on_transcript_change.py
# has its own dedup), so double-firing is harmless.
if [[ -x "$HOOK_SCRIPT" ]]; then
    "$HOOK_SCRIPT" "$TRANSCRIPT"
    echo "$CURRENT_SIZE" > "$POLL_STATE"
    exit 0
fi

exit 1
