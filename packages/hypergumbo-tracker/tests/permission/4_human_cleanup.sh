#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Permission test script 4/4: Human cleanup and final verification.
#
# Run as: jgstern
# Unlocks the invariant, verifies count-todos, prints combined results,
# and cleans up.
set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS=0; FAIL=0; TOTAL=0
TRACKER_CMD="${TRACKER_CMD:-hypergumbo-tracker}"

_log() { echo "[4_human_cleanup] $*"; }

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

if [ "${1:-}" = "--help" ] || [ $# -eq 0 ]; then
    echo "Usage: $0 <path/to/state.json>"
    echo "  Run as jgstern. Final verification and cleanup."
    echo "  The state.json path is printed by 1_agent_setup.sh."
    [ "${1:-}" = "--help" ] && exit 0 || exit 1
fi

# ---------------------------------------------------------------------------
# Load state
# ---------------------------------------------------------------------------

STATE_FILE="$1"

if [ ! -f "$STATE_FILE" ]; then
    _log "ERROR: state.json not found at: $STATE_FILE"
    _log "  Run scripts 1-3 first."
    exit 1
fi

TMPDIR="$(dirname "$STATE_FILE")"
REPO=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['repo'])")
TRACKER_ROOT=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['tracker_root'])")
INV_ID=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['inv_id'])")

_log "Repo: $REPO"
_log "Invariant: $INV_ID"

# Ensure safe.directory
git config --global --add safe.directory "$REPO" 2>/dev/null || true

cd "$REPO"
COMMON="--tracker-root $TRACKER_ROOT --no-auto-sync --json"

# ---------------------------------------------------------------------------
# Tests: Final human actions
# ---------------------------------------------------------------------------

_log "--- Final human actions ---"

# 1. Human unlocks invariant status
assert_exit "human unlock invariant status" 0 \
    $TRACKER_CMD $COMMON unlock "$INV_ID" status

# 2. Human updates invariant to done
assert_exit "human update invariant done" 0 \
    $TRACKER_CMD $COMMON update "$INV_ID" --status done

# 3. Verify count-todos = 0
set +e
count_output=$($TRACKER_CMD $COMMON count-todos 2>/dev/null)
count=$(echo "$count_output" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])")
set -e
TOTAL=$((TOTAL + 1))
if [ "$count" -eq 0 ]; then
    PASS=$((PASS + 1))
    _log "PASS: count-todos = 0"
else
    FAIL=$((FAIL + 1))
    _log "FAIL: count-todos = $count (expected 0)"
fi

# ---------------------------------------------------------------------------
# Combined results
# ---------------------------------------------------------------------------

# Load all script results
S1_PASS=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('script1_pass', 0))")
S1_FAIL=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('script1_fail', 0))")
S1_TOTAL=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('script1_total', 0))")
S2_PASS=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('script2_pass', 0))")
S2_FAIL=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('script2_fail', 0))")
S2_TOTAL=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('script2_total', 0))")
S3_PASS=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('script3_pass', 0))")
S3_FAIL=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('script3_fail', 0))")
S3_TOTAL=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('script3_total', 0))")

ALL_PASS=$((S1_PASS + S2_PASS + S3_PASS + PASS))
ALL_FAIL=$((S1_FAIL + S2_FAIL + S3_FAIL + FAIL))
ALL_TOTAL=$((S1_TOTAL + S2_TOTAL + S3_TOTAL + TOTAL))

echo ""
echo "=========================================="
echo "  Two-User Permission Test Suite Results"
echo "=========================================="
echo "  Script 1 (agent setup):      $S1_PASS/$S1_TOTAL passed, $S1_FAIL failed"
echo "  Script 2 (human governance): $S2_PASS/$S2_TOTAL passed, $S2_FAIL failed"
echo "  Script 3 (agent constrained):$S3_PASS/$S3_TOTAL passed, $S3_FAIL failed"
echo "  Script 4 (human cleanup):    $PASS/$TOTAL passed, $FAIL failed"
echo "------------------------------------------"
echo "  TOTAL:                       $ALL_PASS/$ALL_TOTAL passed, $ALL_FAIL failed"
echo "=========================================="

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

_log "Cleaning up temp directory: $TMPDIR"
rm -rf "$TMPDIR"

# Remove safe.directory entry
git config --global --unset-all safe.directory "$REPO" 2>/dev/null || true

if [ "$ALL_FAIL" -gt 0 ]; then
    echo "RESULT: FAIL"
    exit 1
else
    echo "RESULT: PASS"
    exit 0
fi
