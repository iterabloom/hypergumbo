#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Codex CLI SessionStart hook adapter
# See ADR-0008 for governance protocol
#
# Codex CLI supports SessionStart hooks natively (configured in
# ~/.codex/hooks.json or .codex/hooks.json). Receives JSON on stdin
# with session_id, transcript_path, cwd, model, permission_mode, source.
#
# Transcript sync: uses transcript_path from stdin JSON to launch a
# background watcher that mirrors it to a per-session current file
# (.agent/.current_session_transcript.<session_id>.jsonl).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# --- Capture stdin (Codex CLI passes JSON input) ---
STDIN_JSON=$(cat)

# --- Shared logic (sets SESSION_START_MESSAGE, SESSION_START_NEEDS_PROMPT) ---
source "$SCRIPT_DIR/../_shared/session_start_logic.sh"

# --- Transcript sync: extract transcript_path from stdin JSON ---
# Codex CLI provides transcript_path directly in the hook input.
# Fallback: find the newest JSONL in ~/.codex/sessions/ if not provided.
TRANSCRIPT_SRC=""
if command -v jq &>/dev/null; then
    TRANSCRIPT_SRC=$(echo "$STDIN_JSON" | jq -r '.transcript_path // empty' 2>/dev/null || true)
else
    TRANSCRIPT_SRC=$(echo "$STDIN_JSON" | grep -oP '"transcript_path"\s*:\s*"\K[^"]+' 2>/dev/null || true)
fi

# Fallback: discover from filesystem
if [[ -z "$TRANSCRIPT_SRC" || "$TRANSCRIPT_SRC" == "null" ]]; then
    CODEX_SESSIONS="$HOME/.codex/sessions"
    if [[ -d "$CODEX_SESSIONS" ]]; then
        TRANSCRIPT_SRC=$(find "$CODEX_SESSIONS" -name '*.jsonl' -type f \
            -printf '%T@ %p\n' 2>/dev/null \
            | sort -rn | head -1 | cut -d' ' -f2- || true)
    fi
fi

source "$SCRIPT_DIR/../_shared/session_id_helpers.sh"
SESSION_ID=$(extract_codex_session_id "$STDIN_JSON")

if [[ -n "$TRANSCRIPT_SRC" && "$TRANSCRIPT_SRC" != "null" && -n "$SESSION_ID" ]]; then
    REPO_ROOT="$REPO_ROOT" "$SCRIPT_DIR/../_shared/launch-transcript-sync.sh" "$TRANSCRIPT_SRC" "$SESSION_ID"
fi

if [[ "$SESSION_START_NEEDS_PROMPT" == "true" && -n "$SESSION_START_MESSAGE" ]]; then
    echo "$SESSION_START_MESSAGE"
fi
