#!/bin/bash
# on_transcript_change.sh — Thin wrapper that calls the Python implementation.
#
# Called by the AI tool's hook system when the filtered transcript changes.
# stdout is injected back into the agent's conversation as context.
#
# Configuration (environment variables):
#   OPENROUTER_API_KEY     — required
#   TRANSCRIPT_MODEL       — model (default: mistralai/mistral-nemo)
#   TRANSCRIPT_MAX_TOKENS  — token budget for window (default: 8000)
#   TRANSCRIPT_THRESHOLD   — min confidence to include playbook (default: 7)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Load project .env (same pattern as scripts/lib/forgejo-api.sh)
if [[ -f "$REPO_ROOT/.env" ]]; then
    set -o allexport
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +o allexport
fi

echo "[on_transcript_change.sh] FIRED at $(date '+%H:%M:%S') pid=$$ args=$*" >&2
exec python3 "$SCRIPT_DIR/on_transcript_change.py" "$@"
