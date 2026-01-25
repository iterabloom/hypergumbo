#!/bin/bash
# Claude Code Stop hook adapter
# See ADR-0008 for governance protocol

set -euo pipefail

# Find repository root (where AUTONOMOUS_MODE.txt lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Check autonomous mode
if [[ "$(cat "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null)" != "TRUE" ]]; then
  echo '{"decision": "approve", "reason": "Autonomous mode disabled"}'
  exit 0
fi

# Check if loop sentinel exists
if [[ ! -f "$REPO_ROOT/.agent/LOOP" ]]; then
  echo '{"decision": "approve", "reason": "Loop sentinel removed"}'
  exit 0
fi

# Escape the reflection prompt for JSON
REFLECTION_PROMPT=$(cat "$REPO_ROOT/.agent/stop_reflect.md" | jq -Rs .)

# Inject reflection prompt - block the stop and continue with reflection
cat <<EOF
{
  "decision": "block",
  "reason": "Reflection required before stopping",
  "continue": $REFLECTION_PROMPT
}
EOF
