#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Permission test script 2/4: Human governance operations.
#
# Run as: jgstern
# Tests human-authority ops: lock, unlock, discuss --clear, stealth,
# unstealth, delete.
set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS=0; FAIL=0; TOTAL=0
TRACKER_CMD="${TRACKER_CMD:-hypergumbo-tracker}"

_log() { echo "[2_human_governance] $*"; }

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

assert_output_contains() {
    local label="$1" pattern="$2"
    shift 2
    TOTAL=$((TOTAL + 1))
    set +e
    output=$("$@" 2>&1)
    set -e
    if echo "$output" | grep -qi "$pattern"; then
        PASS=$((PASS + 1))
        _log "PASS: $label (found '$pattern')"
    else
        FAIL=$((FAIL + 1))
        _log "FAIL: $label (pattern '$pattern' not found)"
        _log "  output: $output"
    fi
}

if [ "${1:-}" = "--help" ]; then
    echo "Usage: $0 <state.json>"
    echo "  Run as jgstern. Tests human governance operations."
    exit 0
fi

# ---------------------------------------------------------------------------
# Load state
# ---------------------------------------------------------------------------

STATE_FILE="${1:-/tmp/tracker-permission-test-*/state.json}"
# Expand glob
STATE_FILE=$(ls $STATE_FILE 2>/dev/null | head -1)

if [ ! -f "$STATE_FILE" ]; then
    _log "ERROR: state.json not found. Run 1_agent_setup.sh first."
    exit 1
fi

TMPDIR="$(dirname "$STATE_FILE")"
REPO=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['repo'])")
TRACKER_ROOT=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['tracker_root'])")
WI_ID=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['wi_id'])")
INV_ID=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['inv_id'])")

_log "Repo: $REPO"
_log "Work item: $WI_ID"
_log "Invariant: $INV_ID"

# Set safe.directory for cross-user access
git config --global --add safe.directory "$REPO" 2>/dev/null || true

COMMON="--tracker-root $TRACKER_ROOT --no-auto-sync --json"

# ---------------------------------------------------------------------------
# Tests: Human governance operations
# ---------------------------------------------------------------------------

_log "--- Human governance operations ---"

# 1. Human locks status on work_item
assert_exit "human lock status" 0 \
    $TRACKER_CMD $COMMON lock "$WI_ID" status

# 2. Human discusses item
assert_exit "human discuss" 0 \
    $TRACKER_CMD $COMMON discuss "$WI_ID" "Human comment"

# 3. Human clears discussion
assert_exit "human discuss --clear" 0 \
    $TRACKER_CMD $COMMON discuss "$WI_ID" --clear

# 4. Human locks discussion on work_item
assert_exit "human lock discussion" 0 \
    $TRACKER_CMD $COMMON lock "$WI_ID" discussion

# 5. Human unlocks discussion
assert_exit "human unlock discussion" 0 \
    $TRACKER_CMD $COMMON unlock "$WI_ID" discussion

# 6. Human stealths work_item (must be workspace tier)
assert_exit "human stealth" 0 \
    $TRACKER_CMD $COMMON stealth "$WI_ID"

# 7. Human unstealths work_item
assert_exit "human unstealth" 0 \
    $TRACKER_CMD $COMMON unstealth "$WI_ID"

# 8. Human deletes work_item (new feature!)
assert_exit "human delete" 0 \
    $TRACKER_CMD $COMMON delete "$WI_ID"

# 9. Verify deleted item not in ready output
set +e
ready_output=$($TRACKER_CMD $COMMON ready 2>&1)
set -e
TOTAL=$((TOTAL + 1))
if echo "$ready_output" | grep -q "$WI_ID"; then
    FAIL=$((FAIL + 1))
    _log "FAIL: deleted item still in ready"
else
    PASS=$((PASS + 1))
    _log "PASS: deleted item excluded from ready"
fi

# 10. Human locks status on invariant (for script 3 to test against)
assert_exit "human lock invariant status" 0 \
    $TRACKER_CMD $COMMON lock "$INV_ID" status

# 11. Fix config.yaml ownership: make it human-owned, not agent-writable
CONFIG_PATH="$TRACKER_ROOT/tracker/config.yaml"
if [ -f "$CONFIG_PATH" ]; then
    chown "$(whoami)" "$CONFIG_PATH" 2>/dev/null || true
    chmod 644 "$CONFIG_PATH" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Save updated state
# ---------------------------------------------------------------------------

python3 -c "
import json
with open('$STATE_FILE') as f:
    state = json.load(f)
state['script2_pass'] = $PASS
state['script2_fail'] = $FAIL
state['script2_total'] = $TOTAL
with open('$STATE_FILE', 'w') as f:
    json.dump(state, f, indent=2)
"

_log "Results: $PASS/$TOTAL passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
