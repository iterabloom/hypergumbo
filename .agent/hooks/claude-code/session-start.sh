#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Claude Code SessionStart hook adapter
# See ADR-0008 for governance protocol
#
# Detects stale/OFF autonomous mode and injects a prompt asking the
# agent to query the user for mode selection. When the agent runs
# ./scripts/loop-toggle, the PID is correct because it runs inside
# the agent CLI process.
#
# Claude Code: stdout is injected into the conversation as context.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# --- Capture stdin (hook input JSON) before anything else reads it ---
STDIN_JSON=$(cat)

# --- Shared logic (sets SESSION_START_MESSAGE, SESSION_START_NEEDS_PROMPT) ---
source "$SCRIPT_DIR/../_shared/session_start_logic.sh"

# --- Transcript sync: discover source path, launch background watcher ---
source "$SCRIPT_DIR/../_shared/session_id_helpers.sh"
SESSION_ID=$(extract_claude_code_session_id "$STDIN_JSON")

if [[ -n "$SESSION_ID" ]]; then
    MANGLED_PATH=$(echo "$REPO_ROOT" | tr '/_' '-')
    TRANSCRIPT_SRC="$HOME/.claude/projects/${MANGLED_PATH}/${SESSION_ID}.jsonl"
    REPO_ROOT="$REPO_ROOT" "$SCRIPT_DIR/../_shared/launch-transcript-sync.sh" "$TRANSCRIPT_SRC" "$SESSION_ID"
fi

if [[ "$SESSION_START_NEEDS_PROMPT" == "true" && -n "$SESSION_START_MESSAGE" ]]; then
    echo "$SESSION_START_MESSAGE"
fi
