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

# Helper: append the Fundamental Concept Audit cadence reminder to
# SESSION_START_MESSAGE when the audit-cadence script emits one. The
# cadence check is a soft nudge — running it through this helper means
# every code path below picks it up regardless of which mode-state
# branch fires. Skipped when another session already owns autonomous
# mode (Case 3) so we don't double-prompt while another session is alive.
_append_concept_audit_cadence() {
    local _cadence_script="$REPO_ROOT/.agent/hooks/_shared/check_audit_cadence.py"
    if [[ ! -x "$_cadence_script" ]]; then
        return 0
    fi
    local _cadence_msg
    _cadence_msg=$("$_cadence_script" 2>/dev/null || true)
    if [[ -z "$_cadence_msg" ]]; then
        return 0
    fi
    if [[ -n "$SESSION_START_MESSAGE" ]]; then
        SESSION_START_MESSAGE="${SESSION_START_MESSAGE}

${_cadence_msg}"
    else
        SESSION_START_MESSAGE="$_cadence_msg"
        SESSION_START_NEEDS_PROMPT=true
    fi
}

# Case 0: respawn from the agent-supervisor daemon (WI-sakod / WI-razub).
# When HYPERGUMBO_RESPAWN=1, the supervisor just spawned this CLI because
# the prior session was stuck or dead. Instead of pausing to ask the human
# for a mode (the prior session was doing just that, and stagnated), we:
#   (a) auto-enable autonomous mode for THIS session per autonomous_intent.txt
#       (the project-level intent, split out in WI-pobon), and
#   (b) emit the generic seed prompt so the agent begins work immediately.
# Intent=OFF defends against misconfiguration: if the supervisor spawned
# us but the human flipped intent OFF in between, we fall through to the
# normal OFF prompt rather than forcing autonomous mode on.
if [[ "${HYPERGUMBO_RESPAWN:-}" == "1" ]]; then
    _INTENT_FILE="$REPO_ROOT/autonomous_intent.txt"
    _INTENT="OFF"
    if [[ -f "$_INTENT_FILE" ]]; then
        _INTENT_RAW=$(head -1 "$_INTENT_FILE" 2>/dev/null | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
        case "$_INTENT_RAW" in
            OFF|FALSE)    _INTENT="OFF" ;;
            BROAD|TRUE)   _INTENT="BROAD" ;;
            DEEP)         _INTENT="DEEP" ;;
            *)            _INTENT="OFF" ;;
        esac
    fi
    if [[ "$_INTENT" != "OFF" ]]; then
        # Auto-enable autonomous mode for THIS session. Narrow write (does
        # NOT touch autonomous_intent.txt, which the supervisor owns).
        "$REPO_ROOT/scripts/loop-toggle" --set-session-mode "$_INTENT" >/dev/null 2>&1 || true
        SESSION_START_NEEDS_PROMPT=true
        SESSION_START_MESSAGE="Please familiarize yourself with this repo. Once you have done so, please set autonomous mode to DEEP."
        _append_concept_audit_cadence
        return 0 2>/dev/null || true
    fi
    # Intent=OFF while supervisor-spawned: fall through to the normal
    # OFF-mode human-prompt path below. The supervisor shouldn't have
    # spawned in that case, but this preserves graceful degradation.
fi

# Read current mode
_RAW_MODE=$(head -1 "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null || true)
_MODE=$(echo "$_RAW_MODE" | sed 's/ *pid=[0-9]*//' | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
_STORED_PID=$(echo "$_RAW_MODE" | grep -oP 'pid=\K[0-9]+' 2>/dev/null || true)

# Case 1: Autonomous mode is OFF or unset
if [[ -z "$_MODE" || "$_MODE" == "off" || "$_MODE" == "false" ]]; then
    SESSION_START_NEEDS_PROMPT=true
    SESSION_START_MESSAGE="Autonomous mode is OFF. Before starting work, ask the user which mode to use: BROAD, DEEP, or OFF. Then run: ./scripts/loop-toggle <choice>"
    _append_concept_audit_cadence
    return 0 2>/dev/null || true
fi

# Case 2: Autonomous mode is set but PID is stale (dead process)
if [[ -n "$_STORED_PID" && ! -d "/proc/$_STORED_PID" ]]; then
    SESSION_START_NEEDS_PROMPT=true
    SESSION_START_MESSAGE="Autonomous mode was ${_MODE^^} but the previous session (pid=$_STORED_PID) has ended. Before starting work, ask the user which mode to use: BROAD, DEEP, or OFF. Then run: ./scripts/loop-toggle <choice>"
    _append_concept_audit_cadence
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
_append_concept_audit_cadence
