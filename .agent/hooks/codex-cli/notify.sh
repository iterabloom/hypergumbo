#!/bin/bash
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
MODE=$(cat "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
if [[ -z "$MODE" || "$MODE" == "OFF" || "$MODE" == "FALSE" ]]; then
  exit 0
fi
if [[ ! -f "$REPO_ROOT/.agent/LOOP" ]]; then
  exit 0
fi

# --- Shared logic (sets TOTAL_HARD, TOTAL_SOFT, TOTAL_TODOS, CIRCUIT_BREAKER_TRIPPED, etc.) ---
source "$SCRIPT_DIR/../_shared/stop_logic.sh"

# --- Path 1: Surface pending TODO items ---
if [[ "$TOTAL_TODOS" -gt 0 && "$CIRCUIT_BREAKER_TRIPPED" == "false" ]]; then
  cat >&2 <<BANNER
================================================================
  AUTONOMOUS MODE: $TOTAL_TODOS PENDING TODO(s) ($TOTAL_HARD hard, $TOTAL_SOFT soft)
  Read $GUIDANCE_FILE for details.
  (If Codex CLI does not auto-continue, review and manually proceed)
================================================================

BANNER
  exit 0
fi
if [[ "$TOTAL_TODOS" -gt 0 && "$CIRCUIT_BREAKER_TRIPPED" == "true" ]]; then
  cat >&2 <<BANNER
================================================================
  CIRCUIT BREAKER: No progress on $TOTAL_TODOS TODO(s) across $HASH_THRESHOLD stop events.
  Stopping approved. Read $GUIDANCE_FILE for details.
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
