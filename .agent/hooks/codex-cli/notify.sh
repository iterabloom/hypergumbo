#!/bin/bash
# Codex CLI notification hook adapter
# See ADR-0008 for governance protocol
#
# LIMITATION: Codex CLI notify hooks can only send notifications.
# They CANNOT block execution or inject continuation prompts.
# This is a fundamental API limitation - see:
# https://github.com/openai/codex/discussions/2150
#
# The notify hook receives a JSON argument with event details.
# Currently only "agent-turn-complete" events are supported.
# Ref: https://developers.openai.com/codex/config-advanced/

set -euo pipefail

# Find repository root (where AUTONOMOUS_MODE.txt lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Codex passes JSON as a command-line argument (not stdin)
# Note: must quote the default to avoid bash brace expansion issues
JSON_ARG="${1:-"{}"}"

# Parse event type (currently only "agent-turn-complete" is supported)
EVENT_TYPE=$(echo "$JSON_ARG" | jq -r '.type // "unknown"')

# Only act on agent-turn-complete events
if [[ "$EVENT_TYPE" != "agent-turn-complete" ]]; then
  exit 0
fi

# Check autonomous mode and loop sentinel
# TRUE, BROAD, and DEEP all enable autonomous behavior
# OFF and FALSE both mean disabled (see scripts/loop-toggle)
MODE=$(cat "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
if [[ -n "$MODE" && "$MODE" != "OFF" && "$MODE" != "FALSE" ]]; then
  if [[ -f "$REPO_ROOT/.agent/LOOP" ]]; then
    # --- Three-way notification logic ---

    # Path 1: Surface pending TODO items
    TODO_COUNT=$(grep -c '^\s*- \*\*TODO\*\*' "$REPO_ROOT/.agent/invariant-ledger.md" 2>/dev/null || echo 0)
    if [[ "$TODO_COUNT" -gt 0 ]]; then
      cat >&2 <<BANNER
════════════════════════════════════════════════════════════════════
  AUTONOMOUS MODE: $TODO_COUNT PENDING SCOPE EXPANSION TODO(s)
  (If Codex CLI does not auto-continue, review and manually proceed)
════════════════════════════════════════════════════════════════════

BANNER
      grep '^\s*- \*\*TODO\*\*' "$REPO_ROOT/.agent/invariant-ledger.md" 2>/dev/null >&2
      exit 0
    fi

    # Path 2: Cooldown notification
    STATE_FILE="$REPO_ROOT/.agent/stop_hook_state.json"
    if [[ -f "$STATE_FILE" ]]; then
      LAST_TS=$(jq -r '.last_completed_utc // "1970-01-01T00:00:00Z"' "$STATE_FILE" 2>/dev/null || echo "1970-01-01T00:00:00Z")
      LAST_EPOCH=$(date -d "$LAST_TS" +%s 2>/dev/null || echo 0)
      NOW_EPOCH=$(date +%s)
      ELAPSED_MIN=$(( (NOW_EPOCH - LAST_EPOCH) / 60 ))

      if [[ "$ELAPSED_MIN" -lt 30 ]]; then
        cat >&2 <<BANNER
════════════════════════════════════════════════════════════════════
  AUTONOMOUS MODE ACTIVE - COOLDOWN (reflection completed ${ELAPSED_MIN}m ago)
  (If Codex CLI does not auto-continue, review and manually proceed)
════════════════════════════════════════════════════════════════════

BANNER
        cat "$REPO_ROOT/.agent/cooldown_prompt.md" >&2
        exit 0
      fi
    fi

    # Path 3: Full reflection prompt
    cat >&2 <<'BANNER'
════════════════════════════════════════════════════════════════════
  AUTONOMOUS MODE ACTIVE - REFLECTION REQUIRED BEFORE STOPPING
  (If Codex CLI does not auto-continue, review and manually proceed)
════════════════════════════════════════════════════════════════════

BANNER
    cat "$REPO_ROOT/.agent/stop_reflect.md" >&2
  fi
fi
