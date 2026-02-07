#!/bin/bash
# Claude Code Stop hook adapter
# See ADR-0008 for governance protocol
#
# Three-way decision logic:
# 1. Pending TODOs in ledger → block with pending items listing
# 2. Cooldown (reflection completed <30 min ago) → block with cooldown prompt
# 3. Stale reflection → block with full checklist

set -euo pipefail

# Find repository root (where AUTONOMOUS_MODE.txt lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Check autonomous mode (TRUE, BROAD, or DEEP all enable autonomous behavior)
# OFF and FALSE both mean disabled (see scripts/loop-toggle)
MODE=$(cat "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
if [[ -z "$MODE" || "$MODE" == "OFF" || "$MODE" == "FALSE" ]]; then
  echo '{"decision": "approve", "reason": "Autonomous mode disabled"}'
  exit 0
fi

# Check if loop sentinel exists
if [[ ! -f "$REPO_ROOT/.agent/LOOP" ]]; then
  echo '{"decision": "approve", "reason": "Loop sentinel removed"}'
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
  "decision": "block",
  "reason": $REASON
}
EOF
  exit 0
fi

# Path 2: Check cooldown (reflection completed within last 30 minutes)
STATE_FILE="$REPO_ROOT/.agent/last_stop_check.json"
# Backward compat: fall back to old filename if new one doesn't exist
if [[ ! -f "$STATE_FILE" && -f "$REPO_ROOT/.agent/stop_hook_state.json" ]]; then
  STATE_FILE="$REPO_ROOT/.agent/stop_hook_state.json"
fi
if [[ -f "$STATE_FILE" ]]; then
  LAST_TS=$(jq -r '.last_completed_utc // "1970-01-01T00:00:00Z"' "$STATE_FILE" 2>/dev/null || echo "1970-01-01T00:00:00Z")
  LAST_EPOCH=$(date -d "$LAST_TS" +%s 2>/dev/null || echo 0)
  NOW_EPOCH=$(date +%s)
  ELAPSED_MIN=$(( (NOW_EPOCH - LAST_EPOCH) / 60 ))

  if [[ "$ELAPSED_MIN" -lt 30 ]]; then
    COOLDOWN_PROMPT=$(cat "$REPO_ROOT/.agent/cooldown_prompt.md" | jq -Rs .)
    cat <<EOF
{
  "decision": "block",
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
  "decision": "block",
  "reason": $REFLECTION_PROMPT
}
EOF
