#!/bin/bash
# Cursor stop hook adapter
# See ADR-0008 for governance protocol
#
# Cursor stop hooks receive JSON input and should return JSON output.
# To continue the agent loop, return {"followup_message": "..."}
# Ref: https://cursor.com/docs/agent/hooks
#
# Three-way decision logic:
# 1. Pending TODOs in ledger → followup with pending items listing
# 2. Cooldown (reflection completed <30 min ago) → followup with cooldown prompt
# 3. Stale reflection → followup with full checklist

set -euo pipefail

# Find repository root (where AUTONOMOUS_MODE.txt lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Read input from stdin (Cursor passes JSON via stdin)
INPUT=$(cat)

# Check autonomous mode - if disabled, allow stop (empty output = no followup)
# TRUE, BROAD, and DEEP all enable autonomous behavior
# OFF and FALSE both mean disabled (see scripts/loop-toggle)
MODE=$(cat "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
if [[ -z "$MODE" || "$MODE" == "OFF" || "$MODE" == "FALSE" ]]; then
  echo '{}'
  exit 0
fi

# Check if loop sentinel exists - if removed, allow stop
if [[ ! -f "$REPO_ROOT/.agent/LOOP" ]]; then
  echo '{}'
  exit 0
fi

# Check loop_count to prevent infinite loops (Cursor default limit is 5)
LOOP_COUNT=$(echo "$INPUT" | jq -r '.loop_count // 0')
if [[ "$LOOP_COUNT" -ge 5 ]]; then
  echo '{}'
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
  "followup_message": $REASON
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
  "followup_message": $COOLDOWN_PROMPT
}
EOF
    exit 0
  fi
fi

# Path 3: Full reflection checklist (stale or no prior reflection)
REFLECTION_PROMPT=$(cat "$REPO_ROOT/.agent/stop_reflect.md" | jq -Rs .)
cat <<EOF
{
  "followup_message": $REFLECTION_PROMPT
}
EOF
