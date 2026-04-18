#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
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
# Format: "MODE" or "MODE pid=12345" (parallel session support)
_RAW_MODE=$(head -1 "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null || true)
MODE=$(echo "$_RAW_MODE" | sed 's/ *pid=[0-9]*//' | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
if [[ -z "$MODE" || "$MODE" == "OFF" || "$MODE" == "FALSE" ]]; then
  echo '{"decision": "approve", "reason": "Autonomous mode disabled"}'
  exit 0
fi

# Check if loop sentinel exists
if [[ ! -f "$REPO_ROOT/.agent/LOOP" ]]; then
  echo '{"decision": "approve", "reason": "Loop sentinel removed"}'
  exit 0
fi

# --- Shared logic (sets TOTAL_HARD, TOTAL_SOFT, TOTAL_TODOS, CIRCUIT_BREAKER_TRIPPED,
#     SESSION_IS_AUTONOMOUS, etc.) ---
source "$SCRIPT_DIR/../_shared/stop_logic.sh"

# --- PID-based session check (computed by stop_logic.sh) ---
if [[ "$SESSION_IS_AUTONOMOUS" == "false" ]]; then
  echo '{"decision": "approve", "reason": "Interactive session (PID does not match autonomous agent)"}'
  exit 0
fi

# --- Path 1: TODOs exist (both flavors block, subject to circuit breaker) ---
if [[ "$TOTAL_TODOS" -gt 0 && "$CIRCUIT_BREAKER_TRIPPED" == "false" ]]; then
  # WI-ripuz: when reply debt exists, the AUTONOMOUS MODE TODO count is
  # intentionally hidden — the reason text steers the agent to the
  # REPLY-FIRST CYCLE guidance file instead of forward-march TODO work.
  if [[ "${UNREAD_COUNT:-0}" -gt 0 ]]; then
    REASON=$(printf 'REPLY-FIRST CYCLE: %d unread human message(s). Do not pick from tracker ready, do not start new code, do not run bakeoffs. Read %s and clear reply debt first.' "$UNREAD_COUNT" "$GUIDANCE_FILE" | jq -Rs .)
  else
    REASON=$(printf 'AUTONOMOUS MODE: %d TODO(s) block stopping (%d hard, %d soft). Read %s for details.' "$TOTAL_TODOS" "$TOTAL_HARD" "$TOTAL_SOFT" "$GUIDANCE_FILE" | jq -Rs .)
  fi
  echo "{\"decision\":\"block\",\"reason\":$REASON}"
  exit 0
fi
if [[ "$TOTAL_TODOS" -gt 0 && "$CIRCUIT_BREAKER_TRIPPED" == "true" ]]; then
  # Mechanically deactivate autonomous mode — no point leaving it on
  echo "⚡ CIRCUIT BREAKER TRIPPED: No progress on $TOTAL_TODOS TODO(s) across $HASH_THRESHOLD stop events." >&2
  echo "Deactivating autonomous mode since the circuit breaker is tripped anyhow!" >&2
  # WI-razub intent/mode split: circuit-breaker only flips the current
  # session's mode, NEVER the project-level autonomous_intent.txt. The
  # supervisor daemon consults intent, so flipping it here would suppress
  # the respawn that the split was designed to enable.
  TOGGLE_OUTPUT=$("$REPO_ROOT/scripts/loop-toggle" --set-session-mode off 2>&1) || true
  echo "$TOGGLE_OUTPUT" >&2
  REASON=$(printf 'CIRCUIT BREAKER: No progress on %d TODO(s) across %d stop events. Autonomous mode deactivated. Persist stalled items via scripts/agent-notes --set. Read %s for details.' "$TOTAL_TODOS" "$HASH_THRESHOLD" "$GUIDANCE_FILE" | jq -Rs .)
  echo "{\"decision\":\"approve\",\"reason\":$REASON}"
  exit 0
fi

# --- Path 2: Cooldown (reflection completed within last 30 minutes) ---
if [[ "$ELAPSED_MIN" -lt 30 ]]; then
  REASON=$(printf 'Cooldown active (reflection completed %d min ago). Read %s for next actions.' "$ELAPSED_MIN" "$GUIDANCE_FILE_COOLDOWN" | jq -Rs .)
  echo "{\"decision\":\"block\",\"reason\":$REASON}"
  exit 0
fi

# --- Path 3: Full reflection checklist (stale or no prior reflection) ---
REASON=$(printf 'Stale reflection (last: %d min ago). Read %s to complete the stop reflection protocol.' "$ELAPSED_MIN" "$GUIDANCE_FILE_REFLECTION" | jq -Rs .)
echo "{\"decision\":\"block\",\"reason\":$REASON}"