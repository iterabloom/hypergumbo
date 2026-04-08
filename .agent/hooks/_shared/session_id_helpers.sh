#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# session_id_helpers.sh — Vendor-specific session_id extraction helpers
# for the per-session transcript pipeline (ADR-0018 amendment / Option 2).
#
# Each vendor exposes its session identity differently in hook input. This
# file centralizes the extraction logic so vendor hooks (session-start,
# session-end, post-tool-use, before-model, etc.) can call a single helper
# without each one re-implementing JSON parsing.
#
# Vendor identity sources:
#   - Claude Code: stdin JSON `.session_id` (UUID, stable for session lifetime)
#   - Codex CLI:   stdin JSON `.transcript_path` (basename = session id)
#   - Gemini CLI:  stdin JSON `.transcript_path` OR env GEMINI_SESSION_ID
#   - Cursor:      hardcoded `cursor-singleton` (Cursor's backing store is a
#                  global SQLite DB shared by all Cursor windows; per-session
#                  fan-out is deferred — see tracker WI-rijoj. Cursor is
#                  enforced single-session-per-repo by the sibling check
#                  in cursor/session-start.sh.)
#
# All extractors run their result through `sanitize_session_id` so the
# value is safe to use as a filename component. The sanitized session_id
# matches `[A-Za-z0-9_-]+`.

# Sanitize a session id so it is safe as a filename component.
# Replaces any char outside [A-Za-z0-9_-] with -, collapses runs of -, and
# strips leading/trailing -. Empty input yields empty output.
sanitize_session_id() {
    local raw="$1"
    if [[ -z "$raw" || "$raw" == "null" ]]; then
        echo ""
        return
    fi
    echo "$raw" | tr -c 'A-Za-z0-9_-' '-' | sed 's/--*/-/g; s/^-//; s/-$//'
}

# Extract session_id from Claude Code hook stdin JSON.
extract_claude_code_session_id() {
    local stdin_json="$1"
    local sid=""
    if command -v jq >/dev/null 2>&1; then
        sid=$(echo "$stdin_json" | jq -r '.session_id // empty' 2>/dev/null || true)
    else
        sid=$(echo "$stdin_json" | grep -oP '"session_id"\s*:\s*"\K[^"]+' 2>/dev/null || true)
    fi
    sanitize_session_id "$sid"
}

# Extract session_id from Codex CLI hook stdin JSON. Falls back to the
# basename of `transcript_path` since Codex passes the path, not the id.
extract_codex_session_id() {
    local stdin_json="$1"
    local path=""
    if command -v jq >/dev/null 2>&1; then
        path=$(echo "$stdin_json" | jq -r '.transcript_path // empty' 2>/dev/null || true)
    else
        path=$(echo "$stdin_json" | grep -oP '"transcript_path"\s*:\s*"\K[^"]+' 2>/dev/null || true)
    fi
    if [[ -z "$path" || "$path" == "null" ]]; then
        echo ""
        return
    fi
    local base="${path##*/}"
    base="${base%.jsonl}"
    base="${base%.json}"
    sanitize_session_id "$base"
}

# Extract session_id from Gemini CLI hook input. Prefers the
# GEMINI_SESSION_ID env var if set; otherwise derives from transcript_path.
extract_gemini_session_id() {
    local stdin_json="$1"
    if [[ -n "${GEMINI_SESSION_ID:-}" ]]; then
        sanitize_session_id "$GEMINI_SESSION_ID"
        return
    fi
    local path=""
    if command -v jq >/dev/null 2>&1; then
        path=$(echo "$stdin_json" | jq -r '.transcript_path // empty' 2>/dev/null || true)
    else
        path=$(echo "$stdin_json" | grep -oP '"transcript_path"\s*:\s*"\K[^"]+' 2>/dev/null || true)
    fi
    if [[ -z "$path" || "$path" == "null" ]]; then
        echo ""
        return
    fi
    local base="${path##*/}"
    base="${base%.json}"
    base="${base%.jsonl}"
    sanitize_session_id "$base"
}

# Cursor: always returns the constant cursor-singleton.
# Cursor's transcript backing store is the global SQLite database
# state.vscdb, which is shared across ALL Cursor windows in ALL workspaces.
# Per-session fan-out is deferred until tracker WI-rijoj is implemented.
# Until then, Cursor is enforced single-session-per-repo via the sibling
# check in cursor/session-start.sh.
extract_cursor_session_id() {
    echo "cursor-singleton"
}
