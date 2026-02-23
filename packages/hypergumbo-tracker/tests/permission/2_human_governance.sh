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

if [ "${1:-}" = "--help" ] || [ $# -eq 0 ]; then
    echo "Usage: $0 <path/to/state.json>"
    echo "  Run as jgstern. Tests human governance operations."
    echo "  The state.json path is printed by 1_agent_setup.sh."
    [ "${1:-}" = "--help" ] && exit 0 || exit 1
fi

# ---------------------------------------------------------------------------
# Load state
# ---------------------------------------------------------------------------

STATE_FILE="$1"

if [ ! -f "$STATE_FILE" ]; then
    _log "ERROR: state.json not found at: $STATE_FILE"
    _log "  Run 1_agent_setup.sh first — it prints the path."
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

# 11. Lock title with mixed case (should normalize to lowercase)
assert_exit "human lock Title (case-insensitive)" 0 \
    $TRACKER_CMD $COMMON lock "$INV_ID" Title

# 12. Human tries to lock a field that doesn't exist (validation error)
assert_exit "human lock nonexistent field" 1 \
    $TRACKER_CMD $COMMON lock "$INV_ID" nonexistent_field

# 13. Fix config.yaml ownership: make it human-owned, not agent-writable.
#     Can't chown without sudo, so replace the file: copy (human-owned) →
#     delete original (dir is group-writable, no sticky bit) → rename.
CONFIG_PATH="$TRACKER_ROOT/tracker/config.yaml"
if [ -f "$CONFIG_PATH" ]; then
    cp "$CONFIG_PATH" "$CONFIG_PATH.tmp"
    rm -f "$CONFIG_PATH"
    mv "$CONFIG_PATH.tmp" "$CONFIG_PATH"
    chmod 644 "$CONFIG_PATH"
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
