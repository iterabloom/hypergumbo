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
if [[ -n "$MODE" && "$MODE" != "OFF" && "$MODE" != "FALSE" ]]; then
  if [[ -f "$REPO_ROOT/.agent/LOOP" ]]; then
    # Output the full reflection prompt to stderr
    # Even though Codex can't auto-continue, this gets the words into context
    cat >&2 <<'EOF'
════════════════════════════════════════════════════════════════════
  AUTONOMOUS MODE ACTIVE - REFLECTION REQUIRED BEFORE STOPPING
  (If Codex CLI does not auto-continue, review and manually proceed)
════════════════════════════════════════════════════════════════════

EOF
    cat "$REPO_ROOT/.agent/stop_reflect.md" >&2
  fi
fi
