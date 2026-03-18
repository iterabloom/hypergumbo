#!/bin/bash
# Gemini CLI AfterAgent hook adapter
# See ADR-0008 for governance protocol
#
# Gemini CLI AfterAgent hooks receive JSON input and MUST return JSON output.
# Plain text output is explicitly forbidden and will cause failures.
# To inject a continuation prompt, use decision: "deny" with reason field.
# Ref: https://geminicli.com/docs/hooks/reference/
#
# Decision logic:
# 1. Pending TODOs (hard/soft, from ledger + work_items.md) → deny (subject to circuit breaker)
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
# Format: "MODE" or "MODE pid=12345" (parallel session support)
_RAW_MODE=$(head -1 "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null || true)
MODE=$(echo "$_RAW_MODE" | sed 's/ *pid=[0-9]*//' | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
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

# --- Shared logic (sets TOTAL_HARD, TOTAL_SOFT, TOTAL_TODOS, CIRCUIT_BREAKER_TRIPPED,
#     SESSION_IS_AUTONOMOUS, etc.) ---
source "$SCRIPT_DIR/../_shared/stop_logic.sh"

# --- PID-based session check (computed by stop_logic.sh) ---
if [[ "$SESSION_IS_AUTONOMOUS" == "false" ]]; then
  echo '{"decision": "allow"}'
  exit 0
fi

# --- Path 1: TODOs exist (both flavors block, subject to circuit breaker) ---
if [[ "$TOTAL_TODOS" -gt 0 && "$CIRCUIT_BREAKER_TRIPPED" == "false" ]]; then
  REASON=$(printf 'AUTONOMOUS MODE: %d TODO(s) block stopping (%d hard, %d soft). Read %s for details.' "$TOTAL_TODOS" "$TOTAL_HARD" "$TOTAL_SOFT" "$GUIDANCE_FILE" | jq -Rs .)
  echo "{\"decision\":\"deny\",\"reason\":$REASON}"
  exit 0
fi
if [[ "$TOTAL_TODOS" -gt 0 && "$CIRCUIT_BREAKER_TRIPPED" == "true" ]]; then
  REASON=$(printf 'CIRCUIT BREAKER: No progress on %d TODO(s) across %d stop events. Stopping approved.' "$TOTAL_TODOS" "$HASH_THRESHOLD" | jq -Rs .)
  echo "{\"decision\":\"allow\",\"reason\":$REASON}"
  exit 0
fi

# --- Path 2: Cooldown (reflection completed within last 30 minutes) ---
if [[ "$ELAPSED_MIN" -lt 30 ]]; then
  REASON=$(printf 'Cooldown active (reflection completed %d min ago). Read %s for next actions.' "$ELAPSED_MIN" "$GUIDANCE_FILE_COOLDOWN" | jq -Rs .)
  echo "{\"decision\":\"deny\",\"reason\":$REASON}"
  exit 0
fi

# --- Path 3: Full reflection checklist (stale or no prior reflection) ---
REASON=$(printf 'Stale reflection (last: %d min ago). Read %s to complete the stop reflection protocol.' "$ELAPSED_MIN" "$GUIDANCE_FILE_REFLECTION" | jq -Rs .)
echo "{\"decision\":\"deny\",\"reason\":$REASON}"