#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Permission test script 3/4: Agent blocked by human-set constraints.
#
# Run as: jgstern_agent
# Verifies that agent attempts to modify locked fields fail with errors,
# and checks filesystem permission expectations (.ops group, setgid, config
# read-only).
set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS=0; FAIL=0; TOTAL=0
TRACKER_CMD="${TRACKER_CMD:-hypergumbo-tracker}"

_log() { echo "[3_agent_constrained] $*"; }

assert_exit() {
    local label="$1" expected="$2"
    shift 2
    TOTAL=$((TOTAL + 1))
    set +e
    output=$("$@" 2>&1)
    actual=$?
    set -e
    if [ "$actual" -eq "$expected" ]; then
        PASS=$((PASS + 1))
        _log "PASS: $label (exit=$actual)"
    else
        FAIL=$((FAIL + 1))
        _log "FAIL: $label (expected exit=$expected, got exit=$actual)"
        _log "  output: $output"
    fi
}

if [ "${1:-}" = "--help" ]; then
    echo "Usage: $0 <state.json>"
    echo "  Run as jgstern_agent. Tests that agent respects human constraints."
    exit 0
fi

# ---------------------------------------------------------------------------
# Load state
# ---------------------------------------------------------------------------

STATE_FILE="${1:-/tmp/tracker-permission-test-*/state.json}"
STATE_FILE=$(ls $STATE_FILE 2>/dev/null | head -1)

if [ ! -f "$STATE_FILE" ]; then
    _log "ERROR: state.json not found. Run scripts 1 and 2 first."
    exit 1
fi

TMPDIR="$(dirname "$STATE_FILE")"
REPO=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['repo'])")
TRACKER_ROOT=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['tracker_root'])")
INV_ID=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['inv_id'])")

_log "Repo: $REPO"
_log "Invariant: $INV_ID"

cd "$REPO"
COMMON="--tracker-root $TRACKER_ROOT --no-auto-sync --json"

# ---------------------------------------------------------------------------
# Tests: Agent constrained by human locks
# ---------------------------------------------------------------------------

_log "--- Agent constrained by locks ---"

# 1. Agent tries to update locked status on invariant (fails: LockedFieldError)
assert_exit "agent update locked status" 1 \
    $TRACKER_CMD $COMMON update "$INV_ID" --status done

# 2. Agent updates non-locked field on invariant (succeeds — priority not locked)
assert_exit "agent update unlocked field" 0 \
    $TRACKER_CMD $COMMON update "$INV_ID" --priority 1

# 3. Agent discusses invariant (succeeds — discussion not locked)
assert_exit "agent discuss unlocked" 0 \
    $TRACKER_CMD $COMMON discuss "$INV_ID" "Agent follow-up"

# ---------------------------------------------------------------------------
# Tests: Filesystem permission checks
# ---------------------------------------------------------------------------

_log "--- Filesystem permissions ---"

# 4. Config.yaml is not writable by agent
CONFIG_PATH="$TRACKER_ROOT/tracker/config.yaml"
TOTAL=$((TOTAL + 1))
if [ ! -w "$CONFIG_PATH" ]; then
    PASS=$((PASS + 1))
    _log "PASS: config.yaml not writable by agent"
else
    FAIL=$((FAIL + 1))
    _log "FAIL: config.yaml IS writable by agent (expected read-only)"
fi

# 5. .ops dirs have correct group (project-dev)
_check_group() {
    local dir="$1" label="$2"
    TOTAL=$((TOTAL + 1))
    local group
    group=$(stat -c '%G' "$dir" 2>/dev/null || stat -f '%Sg' "$dir" 2>/dev/null)
    if [ "$group" = "project-dev" ]; then
        PASS=$((PASS + 1))
        _log "PASS: $label group is project-dev"
    else
        FAIL=$((FAIL + 1))
        _log "FAIL: $label group is '$group' (expected project-dev)"
    fi
}

_check_group "$TRACKER_ROOT/tracker/.ops" "tracker/.ops"
_check_group "$TRACKER_ROOT/tracker-workspace/.ops" "tracker-workspace/.ops"
_check_group "$TRACKER_ROOT/tracker-workspace/stealth" "stealth"

# 6. Verify setgid bit on .ops dirs
_check_setgid() {
    local dir="$1" label="$2"
    TOTAL=$((TOTAL + 1))
    local perms
    perms=$(stat -c '%a' "$dir" 2>/dev/null || stat -f '%p' "$dir" 2>/dev/null)
    # setgid shows as 2xxx in octal
    if echo "$perms" | grep -q '^2'; then
        PASS=$((PASS + 1))
        _log "PASS: $label has setgid"
    else
        FAIL=$((FAIL + 1))
        _log "FAIL: $label setgid not set (perms=$perms)"
    fi
}

_check_setgid "$TRACKER_ROOT/tracker/.ops" "tracker/.ops"
_check_setgid "$TRACKER_ROOT/tracker-workspace/.ops" "tracker-workspace/.ops"
_check_setgid "$TRACKER_ROOT/tracker-workspace/stealth" "stealth"

# ---------------------------------------------------------------------------
# Save updated state
# ---------------------------------------------------------------------------

python3 -c "
import json
with open('$STATE_FILE') as f:
    state = json.load(f)
state['script3_pass'] = $PASS
state['script3_fail'] = $FAIL
state['script3_total'] = $TOTAL
with open('$STATE_FILE', 'w') as f:
    json.dump(state, f, indent=2)
"

_log "Results: $PASS/$TOTAL passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
