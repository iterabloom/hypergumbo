#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
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
# Format: "MODE" or "MODE pid=12345" (parallel session support)
_RAW_MODE=$(head -1 "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null || true)
MODE=$(echo "$_RAW_MODE" | sed 's/ *pid=[0-9]*//' | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
if [[ -z "$MODE" || "$MODE" == "OFF" || "$MODE" == "FALSE" ]]; then
  exit 0
fi
if [[ ! -f "$REPO_ROOT/.agent/LOOP" ]]; then
  exit 0
fi

# --- Shared logic (sets TOTAL_HARD, TOTAL_SOFT, TOTAL_TODOS, CIRCUIT_BREAKER_TRIPPED,
#     SESSION_IS_AUTONOMOUS, etc.) ---
source "$SCRIPT_DIR/../_shared/stop_logic.sh"

# --- PID-based session check (computed by stop_logic.sh) ---
if [[ "$SESSION_IS_AUTONOMOUS" == "false" ]]; then
  exit 0
fi

# --- Path 1: Surface pending TODO items ---
if [[ "$TOTAL_TODOS" -gt 0 && "$CIRCUIT_BREAKER_TRIPPED" == "false" ]]; then
  # WI-ripuz: REPLY-FIRST CYCLE takes over the banner when reply debt exists.
  if [[ "${UNREAD_COUNT:-0}" -gt 0 ]]; then
    cat >&2 <<BANNER
================================================================
  REPLY-FIRST CYCLE: $UNREAD_COUNT UNREAD HUMAN MESSAGE(S)
  Do NOT pick from tracker ready, do NOT start new code, do NOT run bakeoffs.
  Read $GUIDANCE_FILE and clear reply debt first.
================================================================

BANNER
  else
    cat >&2 <<BANNER
================================================================
  AUTONOMOUS MODE: $TOTAL_TODOS PENDING TODO(s) ($TOTAL_HARD hard, $TOTAL_SOFT soft)
  Read $GUIDANCE_FILE for details.
  (If Codex CLI does not auto-continue, review and manually proceed)
================================================================

BANNER
  fi
  exit 0
fi
if [[ "$TOTAL_TODOS" -gt 0 && "$CIRCUIT_BREAKER_TRIPPED" == "true" ]]; then
  echo "⚡ CIRCUIT BREAKER TRIPPED: No progress on $TOTAL_TODOS TODO(s) across $HASH_THRESHOLD stop events." >&2
  echo "Deactivating autonomous mode since the circuit breaker is tripped anyhow!" >&2
  # WI-razub intent/mode split: circuit-breaker only touches the current
  # session mode. autonomous_intent.txt is the supervisor's signal and
  # must stay as the human left it.
  TOGGLE_OUTPUT=$("$REPO_ROOT/scripts/loop-toggle" --set-session-mode off 2>&1) || true
  echo "$TOGGLE_OUTPUT" >&2
  cat >&2 <<BANNER
================================================================
  Autonomous mode deactivated. Read $GUIDANCE_FILE for details.
================================================================

BANNER
  exit 0
fi

# --- Path 2: Cooldown notification ---
if [[ "$ELAPSED_MIN" -lt 30 ]]; then
  cat >&2 <<BANNER
================================================================
  AUTONOMOUS MODE ACTIVE - COOLDOWN (reflection completed ${ELAPSED_MIN}m ago)
  Read $GUIDANCE_FILE_COOLDOWN for next actions.
  (If Codex CLI does not auto-continue, review and manually proceed)
================================================================

BANNER
  exit 0
fi

# --- Path 3: Full reflection prompt ---
cat >&2 <<BANNER
================================================================
  AUTONOMOUS MODE ACTIVE - REFLECTION REQUIRED BEFORE STOPPING
  Read $GUIDANCE_FILE_REFLECTION to complete the stop reflection protocol.
  (If Codex CLI does not auto-continue, review and manually proceed)
================================================================

BANNER