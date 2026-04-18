#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Shared session-end logic — sourced by all vendor hooks.
# See ADR-0008 for governance protocol.
#
# When an interactive session ends, autonomous mode becomes stale because
# the stop hook's PID binding refers to a now-dead process. This logic
# disables autonomous mode and tells the user how to re-enable it.
#
# Expects REPO_ROOT to be set by the caller.
# Exports: SESSION_END_MODE (the mode that was active, lowercase),
#          SESSION_END_ACTED (true if mode was disabled, false if already off)

SESSION_END_MODE=""
SESSION_END_ACTED=false

# Read current mode (strip pid= suffix, normalize)
_RAW_MODE=$(head -1 "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null || true)
_MODE=$(echo "$_RAW_MODE" | sed 's/ *pid=[0-9]*//' | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')

if [[ -z "$_MODE" || "$_MODE" == "OFF" || "$_MODE" == "FALSE" ]]; then
    SESSION_END_MODE="off"
    SESSION_END_ACTED=false
    return 0 2>/dev/null || exit 0
fi

# Remember what mode was active (lowercase for display)
SESSION_END_MODE=$(echo "$_MODE" | tr '[:upper:]' '[:lower:]')

# Disable autonomous mode for the current session only.
# WI-razub intent/mode split: session-end reflects "this CLI is ending" —
# the project-level intent in autonomous_intent.txt stays as the human set
# it so the next supervisor-driven respawn can pick up the correct mode.
"$REPO_ROOT/scripts/loop-toggle" --set-session-mode off >/dev/null 2>&1
SESSION_END_ACTED=true
