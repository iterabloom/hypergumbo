#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Gemini CLI SessionStart hook adapter
# See ADR-0008 for governance protocol
#
# Detects stale/OFF autonomous mode and injects a prompt asking the
# agent to query the user for mode selection.
#
# Gemini CLI: MUST output JSON only. Plain text causes failures.
# Uses decision: "allow" with reason field to inject context.
#
# Gemini CLI provides transcript_path in the stdin JSON, pointing to
# ~/.gemini/tmp/<project_slug>/chats/session-YYYY-MM-DDTHH-MM-<uuid>.json.
# Also sets env vars: GEMINI_SESSION_ID, GEMINI_PROJECT_DIR.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Capture stdin (Gemini CLI passes JSON input)
STDIN_JSON=$(cat)

# --- Shared logic (sets SESSION_START_MESSAGE, SESSION_START_NEEDS_PROMPT) ---
source "$SCRIPT_DIR/../_shared/session_start_logic.sh"

# --- Transcript sync: extract transcript_path from stdin JSON ---
# Gemini CLI provides transcript_path directly in the hook input.
# Fallback: find the newest session file in ~/.gemini/tmp/*/chats/.
TRANSCRIPT_SRC=""
if command -v jq &>/dev/null; then
    TRANSCRIPT_SRC=$(echo "$STDIN_JSON" | jq -r '.transcript_path // empty' 2>/dev/null || true)
else
    TRANSCRIPT_SRC=$(echo "$STDIN_JSON" | grep -oP '"transcript_path"\s*:\s*"\K[^"]+' 2>/dev/null || true)
fi

# Fallback: discover from filesystem
if [[ -z "$TRANSCRIPT_SRC" || "$TRANSCRIPT_SRC" == "null" ]]; then
    GEMINI_TMP="$HOME/.gemini/tmp"
    if [[ -d "$GEMINI_TMP" ]]; then
        TRANSCRIPT_SRC=$(find "$GEMINI_TMP" -path '*/chats/*' -type f \
            \( -name '*.json' -o -name '*.jsonl' \) \
            -printf '%T@ %p\n' 2>/dev/null \
            | sort -rn | head -1 | cut -d' ' -f2- || true)
    fi
fi

source "$SCRIPT_DIR/../_shared/session_id_helpers.sh"
SESSION_ID=$(extract_gemini_session_id "$STDIN_JSON")

if [[ -n "$TRANSCRIPT_SRC" && "$TRANSCRIPT_SRC" != "null" && -n "$SESSION_ID" ]]; then
    REPO_ROOT="$REPO_ROOT" "$SCRIPT_DIR/../_shared/launch-transcript-sync.sh" "$TRANSCRIPT_SRC" "$SESSION_ID"
fi

if [[ "$SESSION_START_NEEDS_PROMPT" == "true" && -n "$SESSION_START_MESSAGE" ]]; then
    REASON=$(echo "$SESSION_START_MESSAGE" | jq -Rs .)
    echo "{\"decision\":\"allow\",\"reason\":$REASON}"
else
    echo '{"decision":"allow"}'
fi
