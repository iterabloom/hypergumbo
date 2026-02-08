#!/bin/bash
# Claude Code Stop hook adapter
# See ADR-0008 for governance protocol
#
# Decision logic:
# 1. Pending TODOs (hard/soft, from ledger + work_items.md) → block (subject to circuit breaker)
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

# --- Shared logic (sets TOTAL_HARD, TOTAL_SOFT, TOTAL_TODOS, CIRCUIT_BREAKER_TRIPPED, etc.) ---
source "$SCRIPT_DIR/../_shared/stop_logic.sh"

# --- Path 1: TODOs exist (both flavors block, subject to circuit breaker) ---
if [[ "$TOTAL_TODOS" -gt 0 && "$CIRCUIT_BREAKER_TRIPPED" == "false" ]]; then
  REASON=$(printf 'AUTONOMOUS MODE: %d TODO(s) block stopping (%d hard, %d soft). Read %s for details.' "$TOTAL_TODOS" "$TOTAL_HARD" "$TOTAL_SOFT" "$GUIDANCE_FILE" | jq -Rs .)
  echo "{\"decision\":\"block\",\"reason\":$REASON}"
  exit 0
fi
if [[ "$TOTAL_TODOS" -gt 0 && "$CIRCUIT_BREAKER_TRIPPED" == "true" ]]; then
  REASON=$(printf 'CIRCUIT BREAKER: No progress on %d TODO(s) across %d stop events. Stopping approved. Persist stalled items to last_stop_check.json. Read %s for details.' "$TOTAL_TODOS" "$HASH_THRESHOLD" "$GUIDANCE_FILE" | jq -Rs .)
  echo "{\"decision\":\"approve\",\"reason\":$REASON}"
  exit 0
fi

# --- Path 2: Cooldown (reflection completed within last 30 minutes) ---
if [[ "$ELAPSED_MIN" -lt 30 ]]; then
  COOLDOWN_PROMPT=$(printf '%s%s%s' "$COOLDOWN_CONTENT" "$COOLDOWN_NOTES_SECTION" "$BAKEOFF_SUFFIX" | jq -Rs .)
  echo "{\"decision\":\"block\",\"reason\":$COOLDOWN_PROMPT}"
  exit 0
fi

# --- Path 3: Full reflection checklist (stale or no prior reflection) ---
REFLECTION_PROMPT=$(printf '%s%s' "$REFLECTION_CONTENT" "$BAKEOFF_SUFFIX" | jq -Rs .)
echo "{\"decision\":\"block\",\"reason\":$REFLECTION_PROMPT}"
