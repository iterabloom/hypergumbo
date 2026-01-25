#!/bin/bash
# Gemini CLI AfterAgent hook adapter
# See ADR-0008 for governance protocol

set -euo pipefail

# Find repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Check autonomous mode
if [[ "$(cat "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null)" != "TRUE" ]]; then
  exit 0
fi

# Check if loop sentinel exists
if [[ ! -f "$REPO_ROOT/.agent/LOOP" ]]; then
  exit 0
fi

# Gemini CLI reads stdout as continuation prompt
cat "$REPO_ROOT/.agent/stop_reflect.md"
