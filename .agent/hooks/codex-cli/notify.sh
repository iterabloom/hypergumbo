#!/bin/bash
# Codex CLI notification hook adapter
# Limited: can only notify, cannot block or inject prompt
# See ADR-0008 for governance protocol

set -euo pipefail

# Find repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Check autonomous mode and loop sentinel
if [[ "$(cat "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null)" == "TRUE" ]]; then
  if [[ -f "$REPO_ROOT/.agent/LOOP" ]]; then
    echo "WARNING: Autonomous mode active. Review .agent/stop_reflect.md before stopping."
  fi
fi
