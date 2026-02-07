#!/bin/bash
# Claude Code Stop hook adapter
# See ADR-0008 for governance protocol
#
# Decision logic:
# 1. Pending TODOs in ledger → block with pending items listing
# 1.5. Bakeoff convergence summary → informational (appended to prompt)
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

# Path 1.5: Bakeoff convergence summary (informational, appended to prompts)
BAKEOFF_SUFFIX=""
BAKEOFF_DIR="$HOME/hypergumbo_lab_notebook/bakeoff_artifacts"
if [[ -d "$BAKEOFF_DIR" ]]; then
  # Find most recent session's state.json (broad-* or deep-*)
  LATEST_STATE=$(find "$BAKEOFF_DIR" -maxdepth 2 -name state.json -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
  if [[ -n "$LATEST_STATE" ]]; then
    BAKEOFF_SUMMARY=$(python3 -c "
import json, sys
try:
    with open('$LATEST_STATE') as f:
        state = json.load(f)
    ch = state.get('convergence_history', [])
    if not ch:
        sys.exit(0)
    latest = ch[-1]
    crit = latest.get('critical', 0)
    high = latest.get('high', 0)
    new = latest.get('new_issues', 0)
    cohort_num = latest.get('cohort', '?')
    iteration = latest.get('iteration', '?')
    if crit == 0 and high == 0 and new == 0:
        print(f'CONVERGED cohort={cohort_num} iter={iteration}')
    else:
        print(f'NEEDS_WORK cohort={cohort_num} iter={iteration} critical={crit} high={high} new={new}')
except Exception:
    pass
" 2>/dev/null || true)

    if [[ "$BAKEOFF_SUMMARY" == CONVERGED* ]]; then
      BAKEOFF_SUFFIX=$'\n\n---\nBakeoff convergence: '"$BAKEOFF_SUMMARY"$'\nLatest bakeoff session is CONVERGED — no critical/high issues. Running another bakeoff on the same cohort would be redundant. Consider: selecting a new cohort, mining existing artifacts, or moving to other work items.'
    elif [[ "$BAKEOFF_SUMMARY" == NEEDS_WORK* ]]; then
      BAKEOFF_SUFFIX=$'\n\n---\nBakeoff convergence: '"$BAKEOFF_SUMMARY"$'\nLatest bakeoff session has outstanding issues. Consider investigating these before starting new work.'
    fi
  fi
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
    COOLDOWN_CONTENT=$(cat "$REPO_ROOT/.agent/cooldown_prompt.md")
    COOLDOWN_PROMPT=$(printf '%s%s' "$COOLDOWN_CONTENT" "$BAKEOFF_SUFFIX" | jq -Rs .)
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
REFLECTION_CONTENT=$(cat "$REPO_ROOT/.agent/stop_reflect.md")
REFLECTION_PROMPT=$(printf '%s%s' "$REFLECTION_CONTENT" "$BAKEOFF_SUFFIX" | jq -Rs .)
cat <<EOF
{
  "decision": "block",
  "reason": $REFLECTION_PROMPT
}
EOF
