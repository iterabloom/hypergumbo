#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Shared session-start logic — sourced by all vendor hooks.
# See ADR-0008 for governance protocol.
#
# Detects when autonomous mode is OFF or has a stale PID, and emits a
# message asking the agent to prompt the user to choose a mode.  When
# the agent runs `./scripts/loop-toggle <mode>`, the PID is set from
# inside the agent CLI process, giving us the correct PID automatically.
#
# Expects REPO_ROOT to be set by the caller.
# Exports: SESSION_START_MESSAGE (non-empty if the agent should prompt),
#          SESSION_START_NEEDS_PROMPT (true/false)

SESSION_START_MESSAGE=""
SESSION_START_NEEDS_PROMPT=false

# Read current mode
_RAW_MODE=$(head -1 "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null || true)
_MODE=$(echo "$_RAW_MODE" | sed 's/ *pid=[0-9]*//' | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
_STORED_PID=$(echo "$_RAW_MODE" | grep -oP 'pid=\K[0-9]+' 2>/dev/null || true)

# Case 1: Autonomous mode is OFF or unset
if [[ -z "$_MODE" || "$_MODE" == "off" || "$_MODE" == "false" ]]; then
    SESSION_START_NEEDS_PROMPT=true
    SESSION_START_MESSAGE="Autonomous mode is OFF. Before starting work, ask the user which mode to use: BROAD, DEEP, or OFF. Then run: ./scripts/loop-toggle <choice>"
    return 0 2>/dev/null || true
fi

# Case 2: Autonomous mode is set but PID is stale (dead process)
if [[ -n "$_STORED_PID" && ! -d "/proc/$_STORED_PID" ]]; then
    SESSION_START_NEEDS_PROMPT=true
    SESSION_START_MESSAGE="Autonomous mode was ${_MODE^^} but the previous session (pid=$_STORED_PID) has ended. Before starting work, ask the user which mode to use: BROAD, DEEP, or OFF. Then run: ./scripts/loop-toggle <choice>"
    return 0 2>/dev/null || true
fi

# Case 3: Autonomous mode is set with a live PID that isn't ours.
# This means another session owns the autonomous lock.  Don't prompt —
# the other session is still running.
if [[ -n "$_STORED_PID" ]]; then
    # Check if the stored PID is an ancestor of this process
    _check_pid=$$
    _is_ancestor=false
    while [[ $_check_pid -gt 1 ]]; do
        if [[ "$_check_pid" == "$_STORED_PID" ]]; then
            _is_ancestor=true
            break
        fi
        _check_pid=$(awk '/^PPid:/ {print $2}' "/proc/$_check_pid/status" 2>/dev/null) || break
    done
    if [[ "$_is_ancestor" == "false" && -d "/proc/$_STORED_PID" ]]; then
        # Another live session owns autonomous mode — no prompt needed
        SESSION_START_NEEDS_PROMPT=false
        SESSION_START_MESSAGE=""
        return 0 2>/dev/null || true
    fi
fi

# Case 4: Mode is active, PID matches or was just set — all good
SESSION_START_NEEDS_PROMPT=false
SESSION_START_MESSAGE=""
