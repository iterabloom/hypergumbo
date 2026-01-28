#!/bin/bash
# Cursor stop hook adapter
# See ADR-0008 for governance protocol
#
# Cursor stop hooks receive JSON input and should return JSON output.
# To continue the agent loop, return {"followup_message": "..."}
# Ref: https://cursor.com/docs/agent/hooks

set -euo pipefail

# Find repository root (where AUTONOMOUS_MODE.txt lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Read input from stdin (Cursor passes JSON via stdin)
INPUT=$(cat)

# Check autonomous mode - if disabled, allow stop (empty output = no followup)
if [[ "$(cat "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null)" != "TRUE" ]]; then
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

# Escape the reflection prompt for JSON and return as followup_message
REFLECTION_PROMPT=$(cat "$REPO_ROOT/.agent/stop_reflect.md" | jq -Rs .)

cat <<EOF
{
  "followup_message": $REFLECTION_PROMPT
}
EOF
