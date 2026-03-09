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
# Format: "MODE" or "MODE pid=12345" (parallel session support)
_RAW_MODE=$(head -1 "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null || true)
MODE=$(echo "$_RAW_MODE" | sed 's/ *pid=[0-9]*//' | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
if [[ -z "$MODE" || "$MODE" == "OFF" || "$MODE" == "FALSE" ]]; then
  echo '{"decision": "approve", "reason": "Autonomous mode disabled"}'
  exit 0
fi

# --- PID-based parallel session detection ---
# If a PID is stored, only the agent whose ancestor matches that PID is
# treated as autonomous. Other sessions (interactive) get approved immediately.
# If no PID is stored, the first agent to hit this hook claims ownership.
_STORED_PID=""
if [[ "$_RAW_MODE" =~ pid=([0-9]+) ]]; then
  _STORED_PID="${BASH_REMATCH[1]}"
fi

_is_pid_ancestor() {
  # Walk /proc ancestor chain from current process to check if target PID
  # is an ancestor. Returns 0 if found, 1 if not.
  local target=$1
  local pid=$$
  while [[ $pid -gt 1 ]]; do
    [[ "$pid" == "$target" ]] && return 0
    pid=$(awk '/^PPid:/ {print $2}' "/proc/$pid/status" 2>/dev/null) || return 1
  done
  return 1
}

if [[ -n "$_STORED_PID" ]]; then
  if _is_pid_ancestor "$_STORED_PID"; then
    : # This is the autonomous agent — proceed to blocking logic
  elif [[ -d "/proc/$_STORED_PID" ]]; then
    # Stored PID is alive but not our ancestor — we're a different session
    echo '{"decision": "approve", "reason": "Interactive session (PID does not match autonomous agent)"}'
    exit 0
  else
    # Stored PID is dead — don't auto-reclaim. Approve this session.
    # The autonomous agent must be restarted via loop-toggle, which
    # will set a fresh PID. Auto-reclaim caused interactive sessions
    # to inherit autonomous blocking after the agent crashed.
    echo '{"decision": "approve", "reason": "Autonomous agent PID is dead; use loop-toggle to restart"}'
    exit 0
  fi
else
  # No PID stored: claim ownership using $PPID (the agent process)
  # Rewrite the mode file with our PID appended
  echo "$MODE pid=$PPID" > "$REPO_ROOT/AUTONOMOUS_MODE.txt"
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
  REASON=$(printf 'Cooldown active (reflection completed %d min ago). Read %s for next actions.' "$ELAPSED_MIN" "$GUIDANCE_FILE_COOLDOWN" | jq -Rs .)
  echo "{\"decision\":\"block\",\"reason\":$REASON}"
  exit 0
fi

# --- Path 3: Full reflection checklist (stale or no prior reflection) ---
REASON=$(printf 'Stale reflection (last: %d min ago). Read %s to complete the stop reflection protocol.' "$ELAPSED_MIN" "$GUIDANCE_FILE_REFLECTION" | jq -Rs .)
echo "{\"decision\":\"block\",\"reason\":$REASON}"
