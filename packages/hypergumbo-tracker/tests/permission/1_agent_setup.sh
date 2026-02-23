#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Permission test script 1/4: Agent setup and capability/restriction tests.
#
# Run as: jgstern_agent
# This script creates the temp repo, initializes the tracker, and exercises
# agent-allowed and agent-denied operations.
set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS=0; FAIL=0; TOTAL=0
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TRACKER_CMD="${TRACKER_CMD:-hypergumbo-tracker}"

_log() { echo "[1_agent_setup] $*"; }

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

assert_stderr_contains() {
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

_check_path_accessible() {
    # Verify every directory component has other-execute or project-dev
    # group-execute, so the human user can traverse the path.
    local target="$1"
    local current=""
    local old_IFS="$IFS"
    IFS="/"
    for part in $target; do
        IFS="$old_IFS"
        [ -z "$part" ] && continue
        current="$current/$part"
        [ ! -d "$current" ] && return 0

        local perms
        perms=$(stat -c '%A' "$current" 2>/dev/null) || return 0
        local other_x="${perms:9:1}"
        # 'x' or 't' (sticky+execute) count as traversable
        if [ "$other_x" = "x" ] || [ "$other_x" = "t" ]; then
            continue
        fi
        local group_x="${perms:6:1}"
        local grp
        grp=$(stat -c '%G' "$current" 2>/dev/null)
        # 'x' or 's' (setgid+execute) count as group-traversable
        if [ "$grp" = "project-dev" ] && { [ "$group_x" = "x" ] || [ "$group_x" = "s" ]; }; then
            continue
        fi
        _log "ERROR: '$current' is not traversable by the human user."
        _log "  Every parent directory must allow execute for 'other' (o+x) or"
        _log "  the project-dev group (g+x) so jgstern can reach the test files."
        _log "  This is also required for the real two-user tracker model."
        _log "  Fix:  sudo chmod o+x '$current'"
        IFS="$old_IFS"
        return 1
    done
    IFS="$old_IFS"
    return 0
}

WORKDIR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --help)
            echo "Usage: $0 [--workdir DIR]"
            echo "  Run as jgstern_agent. Creates temp repo and tests agent capabilities."
            echo ""
            echo "Options:"
            echo "  --workdir DIR  Parent directory for test files (default: /tmp)."
            echo "                 Use ~/tracker-permission-test to test on the same"
            echo "                 filesystem as real tracker data (/tmp is often tmpfs)."
            echo "                 Requires that the home directory is traversable by"
            echo "                 the human user (chmod o+x)."
            exit 0
            ;;
        --workdir)
            WORKDIR="$2"; shift 2
            ;;
        *)
            _log "ERROR: unknown argument: $1"
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

if ! command -v "$TRACKER_CMD" >/dev/null 2>&1; then
    _log "ERROR: $TRACKER_CMD not found on PATH"
    exit 1
fi

if ! getent group project-dev >/dev/null 2>&1; then
    _log "ERROR: 'project-dev' group does not exist."
    _log "  Create it: sudo groupadd project-dev"
    _log "  Add users: sudo usermod -aG project-dev jgstern && sudo usermod -aG project-dev jgstern_agent"
    exit 1
fi

# ---------------------------------------------------------------------------
# Create temp directory with correct group
# ---------------------------------------------------------------------------

if [ -n "$WORKDIR" ]; then
    # Check that the workdir path is traversable BEFORE creating anything
    _check_path_accessible "$WORKDIR" || exit 1
    mkdir -p "$WORKDIR"
    chgrp project-dev "$WORKDIR" 2>/dev/null || true
    chmod 2775 "$WORKDIR" 2>/dev/null || true
    TMPDIR=$(mktemp -d "$WORKDIR/tracker-permission-test-XXXX")
else
    TMPDIR=$(mktemp -d /tmp/tracker-permission-test-XXXX)
fi

# Make temp dir group-accessible so both users can reach state.json and repo
chgrp project-dev "$TMPDIR" 2>/dev/null || true
chmod 2775 "$TMPDIR" 2>/dev/null || true
_log "Temp dir: $TMPDIR"

REPO="$TMPDIR/repo"
mkdir -p "$REPO"
chgrp project-dev "$REPO" 2>/dev/null || true
chmod 2775 "$REPO" 2>/dev/null || true
cd "$REPO"

# Init git repo
git init -q
git checkout -b dev

# Init tracker structure
TRACKER_ROOT="$REPO/.agent"
mkdir -p "$TRACKER_ROOT/tracker/.ops"
mkdir -p "$TRACKER_ROOT/tracker-workspace/.ops"
mkdir -p "$TRACKER_ROOT/tracker-workspace/stealth"

# Set group ownership on all tracker dirs so both users can read/write
chgrp -R project-dev "$TRACKER_ROOT" 2>/dev/null || true
chmod 2775 "$TRACKER_ROOT" \
    "$TRACKER_ROOT/tracker" \
    "$TRACKER_ROOT/tracker-workspace" 2>/dev/null || true

# Set group and setgid on .ops dirs
chmod g+ws "$TRACKER_ROOT/tracker/.ops" \
    "$TRACKER_ROOT/tracker-workspace/.ops" \
    "$TRACKER_ROOT/tracker-workspace/stealth" 2>/dev/null || true

# Write config
cat > "$TRACKER_ROOT/tracker/config.yaml" <<'YAML'
kinds:
  invariant:
    prefix: INV
    description: "Test invariant"
    fields_schema:
      statement:
        type: text
        required: true
      root_cause:
        type: text
        required: true
  work_item:
    prefix: WI
    description: "Work item"
statuses:
  - todo_hard
  - todo_soft
  - in_progress
  - needs_human_review
  - done
  - wont_do
  - deleted
stop_hook:
  blocking_statuses:
    - todo_hard
    - todo_soft
  resolved_statuses:
    - done
    - wont_do
    - deleted
  human_only_statuses:
    - deleted
actor_resolution:
  agent_usernames:
    - "*_agent"
lamport_branches:
  - dev
YAML

# Initial commit so git works
git add -A
git commit -q -m "init" --no-gpg-sign

# Make .git group-accessible for cross-user git operations
chgrp -R project-dev "$REPO/.git" 2>/dev/null || true
chmod -R g+rwX "$REPO/.git" 2>/dev/null || true

COMMON="--tracker-root $TRACKER_ROOT --no-auto-sync --json"

# ---------------------------------------------------------------------------
# Tests: Agent-allowed operations
# ---------------------------------------------------------------------------

_log "--- Agent-allowed operations ---"

# 1. Agent creates work_item (succeeds)
assert_exit "agent add work_item" 0 \
    $TRACKER_CMD $COMMON add --kind work_item --title "Agent work item"
WI_ID=$(
    $TRACKER_CMD $COMMON list --kind work_item 2>/dev/null \
    | python3 -c "import sys,json; items=json.load(sys.stdin); print(items[0]['id'])"
)
_log "  work_item ID: $WI_ID"

# 2. Agent creates invariant with required fields (succeeds)
assert_exit "agent add invariant" 0 \
    $TRACKER_CMD $COMMON add --kind invariant --title "Agent invariant" \
    --field statement="Test statement" --field root_cause="Test cause"
INV_ID=$(
    $TRACKER_CMD $COMMON list --kind invariant 2>/dev/null \
    | python3 -c "import sys,json; items=json.load(sys.stdin); print(items[0]['id'])"
)
_log "  invariant ID: $INV_ID"

# 3. Agent discusses item (succeeds)
assert_exit "agent discuss" 0 \
    $TRACKER_CMD $COMMON discuss "$WI_ID" "Agent comment"

# ---------------------------------------------------------------------------
# Tests: Agent-denied operations
# ---------------------------------------------------------------------------

_log "--- Agent-denied operations ---"

# 4. Agent tries lock (fails)
assert_exit "agent lock denied" 1 \
    $TRACKER_CMD $COMMON lock "$WI_ID" status

# 5. Agent tries unlock (fails)
assert_exit "agent unlock denied" 1 \
    $TRACKER_CMD $COMMON unlock "$WI_ID" status

# 6. Agent tries discuss --clear (fails)
assert_exit "agent discuss --clear denied" 1 \
    $TRACKER_CMD $COMMON discuss "$WI_ID" --clear

# 7. Agent tries stealth (fails)
assert_exit "agent stealth denied" 1 \
    $TRACKER_CMD $COMMON stealth "$WI_ID"

# 8. Agent tries unstealth (fails — item not stealthed, but error is authority)
assert_stderr_contains "agent unstealth denied" "human authority\|cannot move" \
    $TRACKER_CMD $COMMON unstealth "$WI_ID"

# 9. Agent tries delete (fails — human_only_statuses)
assert_exit "agent delete denied" 1 \
    $TRACKER_CMD $COMMON delete "$WI_ID"

# ---------------------------------------------------------------------------
# Save state for next script
# ---------------------------------------------------------------------------

cat > "$TMPDIR/state.json" <<JSON
{
    "repo": "$REPO",
    "tracker_root": "$TRACKER_ROOT",
    "wi_id": "$WI_ID",
    "inv_id": "$INV_ID",
    "script1_pass": $PASS,
    "script1_fail": $FAIL,
    "script1_total": $TOTAL
}
JSON
# Both users write to state.json; default umask (022) makes it owner-writable only
chmod g+w "$TMPDIR/state.json"

_log "Results: $PASS/$TOTAL passed, $FAIL failed"
_log ""
_log "State file (pass to scripts 2-4):"
_log "  $TMPDIR/state.json"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
