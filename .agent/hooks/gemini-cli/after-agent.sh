#!/bin/bash
# Gemini CLI AfterAgent hook adapter
# See ADR-0008 for governance protocol
#
# Gemini CLI AfterAgent hooks receive JSON input and MUST return JSON output.
# Plain text output is explicitly forbidden and will cause failures.
# To inject a continuation prompt, use decision: "deny" with reason field.
# Ref: https://geminicli.com/docs/hooks/reference/

set -euo pipefail

# Find repository root (where AUTONOMOUS_MODE.txt lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Read input from stdin (Gemini CLI passes JSON via stdin)
INPUT=$(cat)

# Check autonomous mode - if disabled, allow completion
if [[ "$(cat "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null)" != "TRUE" ]]; then
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

# Escape the reflection prompt for JSON
# Use decision: "deny" with reason to inject as new prompt to the agent
REFLECTION_PROMPT=$(cat "$REPO_ROOT/.agent/stop_reflect.md" | jq -Rs .)

cat <<EOF
{
  "decision": "deny",
  "reason": $REFLECTION_PROMPT
}
EOF
