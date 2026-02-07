#!/bin/bash
# Gemini CLI AfterAgent hook adapter
# See ADR-0008 for governance protocol
#
# Gemini CLI AfterAgent hooks receive JSON input and MUST return JSON output.
# Plain text output is explicitly forbidden and will cause failures.
# To inject a continuation prompt, use decision: "deny" with reason field.
# Ref: https://geminicli.com/docs/hooks/reference/
#
# Three-way decision logic:
# 1. Pending TODOs in ledger → deny with pending items listing
# 2. Cooldown (reflection completed <30 min ago) → deny with cooldown prompt
# 3. Stale reflection → deny with full checklist

set -euo pipefail

# Find repository root (where AUTONOMOUS_MODE.txt lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Read input from stdin (Gemini CLI passes JSON via stdin)
INPUT=$(cat)

# Check autonomous mode - if disabled, allow completion
# TRUE, BROAD, and DEEP all enable autonomous behavior
# OFF and FALSE both mean disabled (see scripts/loop-toggle)
MODE=$(cat "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
if [[ -z "$MODE" || "$MODE" == "OFF" || "$MODE" == "FALSE" ]]; then
  echo '{"decision": "allow"}'
  exit 0
fi

# Check if loop sentinel exists - if removed, allow completion
if [[ ! -f "$REPO_ROOT/.agent/LOOP" ]]; then
  echo '{"decision": "allow"}'
  exit 0
fi

# Check stop_hook_active to prevent infinite loops
STOP_HOOK_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
if [[ "$STOP_HOOK_ACTIVE" == "true" ]]; then
  echo '{"decision": "allow"}'
  exit 0
fi

# --- Three-way decision logic ---

# Path 1: Check for pending TODO items in invariant ledger
TODO_COUNT=$(grep -c '^\s*- \*\*TODO\*\*' "$REPO_ROOT/.agent/invariant-ledger.md" 2>/dev/null || echo 0)
if [[ "$TODO_COUNT" -gt 0 ]]; then
  TODO_ITEMS=$(grep '^\s*- \*\*TODO\*\*' "$REPO_ROOT/.agent/invariant-ledger.md" 2>/dev/null | head -20)
  REASON=$(printf 'AUTONOMOUS MODE: %d pending scope expansion TODO(s) in invariant ledger. Address these before stopping:\n\n%s\n\nThese are first-class work items from the Scope Expansion Commitment Protocol. Fix them, or explicitly DEFER with justification in the ledger.' "$TODO_COUNT" "$TODO_ITEMS" | jq -Rs .)
  cat <<EOF
{
  "decision": "deny",
  "reason": $REASON
}
EOF
  exit 0
fi

# Path 2: Check cooldown (reflection completed within last 30 minutes)
STATE_FILE="$REPO_ROOT/.agent/stop_hook_state.json"
if [[ -f "$STATE_FILE" ]]; then
  LAST_TS=$(jq -r '.last_completed_utc // "1970-01-01T00:00:00Z"' "$STATE_FILE" 2>/dev/null || echo "1970-01-01T00:00:00Z")
  LAST_EPOCH=$(date -d "$LAST_TS" +%s 2>/dev/null || echo 0)
  NOW_EPOCH=$(date +%s)
  ELAPSED_MIN=$(( (NOW_EPOCH - LAST_EPOCH) / 60 ))

  if [[ "$ELAPSED_MIN" -lt 30 ]]; then
    COOLDOWN_PROMPT=$(cat "$REPO_ROOT/.agent/cooldown_prompt.md" | jq -Rs .)
    cat <<EOF
{
  "decision": "deny",
  "reason": $COOLDOWN_PROMPT
}
EOF
    exit 0
  fi
fi

# Path 3: Full reflection checklist (stale or no prior reflection)
REFLECTION_PROMPT=$(cat "$REPO_ROOT/.agent/stop_reflect.md" | jq -Rs .)
cat <<EOF
{
  "decision": "deny",
  "reason": $REFLECTION_PROMPT
}
EOF
