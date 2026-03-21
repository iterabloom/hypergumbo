#!/bin/bash
# Cursor stop hook adapter
# See ADR-0008 for governance protocol
#
# Cursor stop hooks receive JSON input and should return JSON output.
# To continue the agent loop, return {"followup_message": "..."}
# Ref: https://cursor.com/docs/agent/hooks
#
# Decision logic:
# 1. Pending TODOs (hard/soft, from ledger + work_items.md) → followup (subject to circuit breaker)
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
# Format: "MODE" or "MODE pid=12345" (parallel session support)
_RAW_MODE=$(head -1 "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null || true)
MODE=$(echo "$_RAW_MODE" | sed 's/ *pid=[0-9]*//' | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
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

# --- Shared logic (sets TOTAL_HARD, TOTAL_SOFT, TOTAL_TODOS, CIRCUIT_BREAKER_TRIPPED,
#     SESSION_IS_AUTONOMOUS, etc.) ---
source "$SCRIPT_DIR/../_shared/stop_logic.sh"

# --- PID-based session check (computed by stop_logic.sh) ---
if [[ "$SESSION_IS_AUTONOMOUS" == "false" ]]; then
  echo '{}'
  exit 0
fi

# --- Path 1: TODOs exist (both flavors block, subject to circuit breaker) ---
if [[ "$TOTAL_TODOS" -gt 0 && "$CIRCUIT_BREAKER_TRIPPED" == "false" ]]; then
  REASON=$(printf 'AUTONOMOUS MODE: %d TODO(s) block stopping (%d hard, %d soft). Read %s for details.' "$TOTAL_TODOS" "$TOTAL_HARD" "$TOTAL_SOFT" "$GUIDANCE_FILE" | jq -Rs .)
  echo "{\"followup_message\":$REASON}"
  exit 0
fi
if [[ "$TOTAL_TODOS" -gt 0 && "$CIRCUIT_BREAKER_TRIPPED" == "true" ]]; then
  # Circuit breaker tripped — allow stop
  echo '{}'
  exit 0
fi

# --- Path 2: Cooldown (reflection completed within last 30 minutes) ---
if [[ "$ELAPSED_MIN" -lt 30 ]]; then
  REASON=$(printf 'Cooldown active (reflection completed %d min ago). Read %s for next actions.' "$ELAPSED_MIN" "$GUIDANCE_FILE_COOLDOWN" | jq -Rs .)
  echo "{\"followup_message\":$REASON}"
  exit 0
fi

# --- Path 3: Full reflection checklist (stale or no prior reflection) ---
REASON=$(printf 'Stale reflection (last: %d min ago). Read %s to complete the stop reflection protocol.' "$ELAPSED_MIN" "$GUIDANCE_FILE_REFLECTION" | jq -Rs .)
echo "{\"followup_message\":$REASON}"