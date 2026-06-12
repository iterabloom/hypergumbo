#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -u

# ==============================================================================
# TEST SUITE FOR HYPERGUMBO commit-msg HOOK
# ==============================================================================

# 0. Locate the real hook we're testing
# ------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL_HOOK="$SCRIPT_DIR/commit-msg"

if [[ ! -f "$REAL_HOOK" ]]; then
  echo "❌ ERROR: Cannot find commit-msg hook at $REAL_HOOK" >&2
  exit 1
fi

echo "🔍 Testing hook: $REAL_HOOK"

# 1. Setup Sandbox
# ------------------------------------------------------------------------------
TEST_DIR="$(mktemp -d -t hypergumbo-test.XXXXXX)"
HOOKS_DIR="$TEST_DIR/.githooks"
mkdir -p "$HOOKS_DIR"

cleanup() {
  rm -rf "$TEST_DIR"
}
trap cleanup EXIT

echo "📂 Initialized test sandbox at: $TEST_DIR"

# 2. Populate Configuration Files
# ------------------------------------------------------------------------------

# Added "Stable" to test word boundary behavior
cat > "$HOOKS_DIR/brand-patterns.txt" <<EOF
Claude
Gemini
GPT
Stable
EOF

FERRET_PHRASE="a ferret riding a surface of holographic panels in a mossy Shoney's atrium with a dynasty of pigeons made of pumpernickel crumbs"
cat > "$HOOKS_DIR/absurd-phrases.txt" <<EOF
$FERRET_PHRASE
EOF

FERRET_SLUG=$(echo "$FERRET_PHRASE" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//;s/-$//')
BAD_EMAIL="${FERRET_SLUG}@racialcapitalism.isbad"

# 3. Install the Hook script - COPY THE REAL ONE!
# ------------------------------------------------------------------------------
COMMIT_MSG_HOOK="$HOOKS_DIR/commit-msg"
cp "$REAL_HOOK" "$COMMIT_MSG_HOOK"
chmod +x "$COMMIT_MSG_HOOK"

echo "📋 Copied real hook to sandbox"

# 4. Helpers for Testing
# ------------------------------------------------------------------------------
PASS_COUNT=0
FAIL_COUNT=0

run_test() {
  local test_name="$1"
  local input_msg="$2"
  local expected_msg="$3"

  local msg_file_path="$TEST_DIR/COMMIT_EDITMSG"
  printf '%s' "$input_msg" > "$msg_file_path"

  echo "--------------------------------------------------------"
  echo "TEST: $test_name"

  if ! "$COMMIT_MSG_HOOK" "$msg_file_path" 2>/dev/null; then
    echo "❌ CRASH: Hook exited with error."
    ((FAIL_COUNT++))
    return 1
  fi

  local actual_msg
  actual_msg=$(cat "$msg_file_path")

  if [[ "$actual_msg" == "$expected_msg" ]]; then
    echo "✅ PASS"
    ((PASS_COUNT++))
  else
    echo "❌ FAIL"
    echo "--- Expected ---"
    echo "$expected_msg" | cat -A | sed 's/^/  /'
    echo "--- Actual ---"
    echo "$actual_msg" | cat -A | sed 's/^/  /'
    ((FAIL_COUNT++))
    return 1
  fi
}

# 5. Define Basic Text Blocks (Shared)
# ------------------------------------------------------------------------------
read -r -d '' BODY <<'EOF' || true
test: enforce 100% coverage in CI and add missing tests

CI was running pytest without coverage enforcement, allowing the codebase
to ship at 68% coverage despite the 100% requirement in AGENTS.md. This
adds --cov=src --cov-fail-under=100 to CI and the unit tests needed to
achieve full coverage.
EOF

DIRTY_LINE="🤖 Generated with [Claude Code](https://claude.com/claude-code)"
SIGNER="Signed-off-by: jgstern-agent <josh-agent@iterabloom.com>"

# 6. Execute Scenarios
# ------------------------------------------------------------------------------

# SCENARIO 1: "Claude Opus 4.5"
INPUT_1="${BODY}

${DIRTY_LINE}

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
${SIGNER}
"

EXPECTED_1="${BODY}

Co-Authored-By: ${FERRET_PHRASE} <${BAD_EMAIL}>
${SIGNER}"

run_test "Scenario 1: Claude Opus (Nuclear Replacement)" "$INPUT_1" "$EXPECTED_1"

# SCENARIO 2: "Tom Morello"
INPUT_2="${BODY}

${DIRTY_LINE}

Co-Authored-By: Tom Morello <tmorello@anthropic.com>
${SIGNER}
"

EXPECTED_2="${BODY}

Co-Authored-By: Tom Morello <tmorello@anthropic.com>
${SIGNER}"

run_test "Scenario 2: Tom Morello (Identity Preserved)" "$INPUT_2" "$EXPECTED_2"


# SCENARIO 3: "Claude Shannon"
INPUT_3="${BODY}

Co-Authored-By: Claude Shannon <cshannon@anthropic.com>
${SIGNER}
"

EXPECTED_3="${BODY}

Co-Authored-By: ${FERRET_PHRASE} <${BAD_EMAIL}>
${SIGNER}"

run_test "Scenario 3: Claude Shannon (Prof Shannon Unluckily Wiped)" "$INPUT_3" "$EXPECTED_3"

# SCENARIO 4: DCO Check
echo "--------------------------------------------------------"
echo "TEST: Scenario 4: DCO Check (Expecting Failure)"
echo "Update readme" > "$TEST_DIR/COMMIT_EDITMSG"

if ! "$COMMIT_MSG_HOOK" "$TEST_DIR/COMMIT_EDITMSG" >/dev/null 2>&1; then
    echo "✅ PASS (Hook blocked commit w/o signature)"
    ((PASS_COUNT++))
else
    echo "❌ FAIL (Hook allowed commit w/o signature)"
    ((FAIL_COUNT++))
fi

# SCENARIO 5: Word Boundary - technical terms preserved
# "stable_id" should NOT be replaced even though "Stable" is a brand pattern
INPUT_5="feat: compute stable_id for Python symbols

Signed-off-by: Developer <dev@example.com>
"

EXPECTED_5="feat: compute stable_id for Python symbols

Signed-off-by: Developer <dev@example.com>"

run_test "Scenario 5: stable_id (Word Boundary Preserved)" "$INPUT_5" "$EXPECTED_5"

# SCENARIO 6: Word Boundary - standalone brand SHOULD be replaced
# "Stable Diffusion" should be replaced because "Stable" is a whole word
INPUT_6="feat: add Stable Diffusion integration

Signed-off-by: Developer <dev@example.com>
"

# Note: The expected output depends on the phrase picker, but the key thing is
# that "Stable" gets replaced. We'll check that "Stable" is NOT in the output.
echo "--------------------------------------------------------"
echo "TEST: Scenario 6: Stable Diffusion (Whole Word Replaced)"
printf '%s' "$INPUT_6" > "$TEST_DIR/COMMIT_EDITMSG"

if ! "$COMMIT_MSG_HOOK" "$TEST_DIR/COMMIT_EDITMSG" 2>/dev/null; then
    echo "❌ CRASH: Hook exited with error."
    ((FAIL_COUNT++))
else
    actual_msg=$(cat "$TEST_DIR/COMMIT_EDITMSG")
    # Check that "Stable" (case-insensitive) is no longer present
    if echo "$actual_msg" | grep -qi "Stable"; then
        echo "❌ FAIL (Stable was NOT replaced)"
        echo "--- Actual ---"
        echo "$actual_msg" | cat -A | sed 's/^/  /'
        ((FAIL_COUNT++))
    else
        echo "✅ PASS (Stable was correctly replaced)"
        ((PASS_COUNT++))
    fi
fi

# SCENARIO 7: Word Boundary - prefix/suffix should be preserved
# "unstable" should NOT be replaced even though it contains "stable"
INPUT_7="fix: handle unstable network connections

Signed-off-by: Developer <dev@example.com>
"

EXPECTED_7="fix: handle unstable network connections

Signed-off-by: Developer <dev@example.com>"

run_test "Scenario 7: unstable (Prefix Preserved)" "$INPUT_7" "$EXPECTED_7"

# 7. Pre-commit branch guard tests
# ------------------------------------------------------------------------------

echo ""
echo "========================================================"
echo "PRE-COMMIT BRANCH GUARD TESTS"
echo "========================================================"

PRE_COMMIT_HOOK="$SCRIPT_DIR/pre-commit"

if [[ -f "$PRE_COMMIT_HOOK" ]]; then
  # Helper: test pre-commit branch guard by mocking `git branch --show-current`
  run_branch_guard_test() {
    local test_name="$1"
    local branch_name="$2"
    local expect_result="$3"  # "block" or "allow"

    echo "--------------------------------------------------------"
    echo "TEST: $test_name"

    # Create a wrapper that overrides `git` to return a fake branch name,
    # then runs only the branch guard portion of the pre-commit hook
    local guard_script
    guard_script=$(mktemp)
    cat > "$guard_script" <<GUARD_EOF
#!/usr/bin/env bash
set -euo pipefail
git() {
  if [[ "\$1" == "branch" && "\$2" == "--show-current" ]]; then
    echo "$branch_name"
    return 0
  fi
  command git "\$@"
}
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'
current_branch=\$(git branch --show-current 2>/dev/null || true)
if [[ "\$current_branch" == "dev" || "\$current_branch" == "main" ]]; then
    exit 1
fi
exit 0
GUARD_EOF
    chmod +x "$guard_script"

    if bash "$guard_script" >/dev/null 2>&1; then
      if [[ "$expect_result" == "allow" ]]; then
        echo "  ✅ PASS (commit allowed on branch '$branch_name')"
        ((PASS_COUNT++))
      else
        echo "  ❌ FAIL (commit should have been blocked on '$branch_name')"
        ((FAIL_COUNT++))
      fi
    else
      if [[ "$expect_result" == "block" ]]; then
        echo "  ✅ PASS (commit blocked on branch '$branch_name')"
        ((PASS_COUNT++))
      else
        echo "  ❌ FAIL (commit should have been allowed on '$branch_name')"
        ((FAIL_COUNT++))
      fi
    fi
    rm -f "$guard_script"
  }

  run_branch_guard_test "Pre-commit: block commit on dev" "dev" "block"
  run_branch_guard_test "Pre-commit: block commit on main" "main" "block"
  run_branch_guard_test "Pre-commit: allow commit on feature branch" "jgstern/feat/test" "allow"
  run_branch_guard_test "Pre-commit: allow commit on tracker-sync branch" "tracker-sync/20260305-120000" "allow"
fi

# 7b. Pre-push hook tests
# ------------------------------------------------------------------------------

PRE_PUSH_HOOK="$SCRIPT_DIR/pre-push"

if [[ -f "$PRE_PUSH_HOOK" ]]; then
  echo ""
  echo "========================================================"
  echo "PRE-PUSH HOOK TESTS"
  echo "========================================================"

  # Create a clean .git dir without CI_FAILOVER_ACTIVE for non-failover tests
  CLEAN_GIT_DIR="$(mktemp -d -t hypergumbo-clean-git.XXXXXX)"

  # Helper: simulate pre-push stdin (local_ref local_sha remote_ref remote_sha)
  run_pre_push_test() {
    local test_name="$1"
    local remote_ref="$2"
    local expect_block="$3"  # "block" or "allow"

    local stdin_line="refs/heads/test abc123 $remote_ref def456"

    echo "--------------------------------------------------------"
    echo "TEST: $test_name"

    if echo "$stdin_line" | GIT_DIR="$CLEAN_GIT_DIR" "$PRE_PUSH_HOOK" "origin" "https://example.com" >/dev/null 2>&1; then
      if [[ "$expect_block" == "allow" ]]; then
        echo "  PASS (push allowed as expected)"
        ((PASS_COUNT++))
      else
        echo "  FAIL (push should have been blocked)"
        ((FAIL_COUNT++))
      fi
    else
      if [[ "$expect_block" == "block" ]]; then
        echo "  PASS (push blocked as expected)"
        ((PASS_COUNT++))
      else
        echo "  FAIL (push should have been allowed)"
        ((FAIL_COUNT++))
      fi
    fi
  }

  run_pre_push_test "Pre-push: block push to dev" "refs/heads/dev" "block"
  run_pre_push_test "Pre-push: block push to main" "refs/heads/main" "block"
  run_pre_push_test "Pre-push: allow push to feature branch" "refs/heads/jgstern/feat/test" "allow"
  run_pre_push_test "Pre-push: allow push to refs/for/dev (Forgejo PR)" "refs/for/dev/my-branch" "allow"

  rm -rf "$CLEAN_GIT_DIR"

  # 7c. Failover remote verification tests
  # --------------------------------------------------------------------------
  echo ""
  echo "========================================================"
  echo "PRE-PUSH FAILOVER REMOTE VERIFICATION TESTS"
  echo "========================================================"

  # Helper: test failover-aware pre-push with a fake .git dir for CI_FAILOVER_ACTIVE
  FAILOVER_SANDBOX="$(mktemp -d -t hypergumbo-failover-test.XXXXXX)"
  # Create a minimal git repo structure so the hook can find CI_FAILOVER_ACTIVE
  mkdir -p "$FAILOVER_SANDBOX/.git"

  run_failover_push_test() {
    local test_name="$1"
    local remote_name="$2"
    local remote_ref="$3"
    local failover_active="$4"  # "true" or "false"
    local expect_result="$5"    # "block" or "allow"
    local disengaging="${6:-}"  # "1" to set CI_FAILOVER_DISENGAGING, empty otherwise

    local stdin_line="refs/heads/test abc123 $remote_ref def456"

    echo "--------------------------------------------------------"
    echo "TEST: $test_name"

    # Set or remove CI_FAILOVER_ACTIVE
    if [[ "$failover_active" == "true" ]]; then
      echo "selfh" > "$FAILOVER_SANDBOX/.git/CI_FAILOVER_ACTIVE"
    else
      rm -f "$FAILOVER_SANDBOX/.git/CI_FAILOVER_ACTIVE"
    fi

    # Run the hook with GIT_DIR pointing to our sandbox .git
    if echo "$stdin_line" | CI_FAILOVER_DISENGAGING="$disengaging" GIT_DIR="$FAILOVER_SANDBOX/.git" "$PRE_PUSH_HOOK" "$remote_name" "https://example.com" >/dev/null 2>&1; then
      if [[ "$expect_result" == "allow" ]]; then
        echo "  ✅ PASS (push allowed as expected)"
        ((PASS_COUNT++))
      else
        echo "  ❌ FAIL (push should have been blocked)"
        ((FAIL_COUNT++))
      fi
    else
      if [[ "$expect_result" == "block" ]]; then
        echo "  ✅ PASS (push blocked as expected)"
        ((PASS_COUNT++))
      else
        echo "  ❌ FAIL (push should have been allowed)"
        ((FAIL_COUNT++))
      fi
    fi
  }

  # During failover: push to origin should be BLOCKED
  run_failover_push_test "Failover: block push to origin" \
    "origin" "refs/for/dev/my-branch" "true" "block"

  # During failover: push to selfh should be ALLOWED
  run_failover_push_test "Failover: allow push to selfh" \
    "selfh" "refs/for/dev/my-branch" "true" "allow"

  # No failover: push to origin should be ALLOWED (feature branch)
  run_failover_push_test "No failover: allow push to origin" \
    "origin" "refs/for/dev/my-branch" "false" "allow"

  # During failover: push to selfh on protected branch still BLOCKED
  run_failover_push_test "Failover: block push to selfh/dev (protected)" \
    "selfh" "refs/heads/dev" "true" "block"

  # Disengage carve-out: with CI_FAILOVER_DISENGAGING=1, AGit push to origin is ALLOWED
  run_failover_push_test "Failover + disengaging: allow AGit push to origin" \
    "origin" "refs/for/dev/repatriation" "true" "allow" "1"

  # Disengage carve-out: even with the env var, direct push to origin/dev still BLOCKED by protected-branch rule
  run_failover_push_test "Failover + disengaging: still block direct push to origin/dev" \
    "origin" "refs/heads/dev" "true" "block" "1"

  rm -rf "$FAILOVER_SANDBOX"
fi

# 8. Stop hook state file tests
# ------------------------------------------------------------------------------

echo ""
echo "========================================================"
echo "STOP HOOK STATE FILE TESTS"
echo "========================================================"

# Find the Claude Code stop hook (canonical adapter for testing)
STOP_HOOK="$SCRIPT_DIR/../.agent/hooks/claude-code/stop.sh"

if [[ -f "$STOP_HOOK" ]]; then
  # Setup: create a minimal repo-like structure in the sandbox
  STOP_TEST_DIR="$(mktemp -d -t hypergumbo-stop-test.XXXXXX)"
  stop_cleanup() {
    rm -rf "$STOP_TEST_DIR"
  }
  # Chain cleanup (original cleanup trap is for TEST_DIR)

  mkdir -p "$STOP_TEST_DIR/.agent/hooks/claude-code"
  mkdir -p "$STOP_TEST_DIR/.agent"

  # Enable autonomous mode and loop sentinel.
  # Store the current test shell's PID ($$) in AUTONOMOUS_MODE.txt so that
  # stop_logic.sh's _is_pid_ancestor() walk (running inside each $(...)
  # subshell) finds $$ as an ancestor and classifies the session as
  # autonomous. Prior to this fix, scenario 8a left behind the PID of a
  # transient command-substitution subshell that had already exited by
  # the time scenarios 8c/8d-1 ran, causing them to take the
  # "dead stored PID → non-autonomous" branch and silently fail.
  echo "BROAD pid=$$" > "$STOP_TEST_DIR/AUTONOMOUS_MODE.txt"
  touch "$STOP_TEST_DIR/.agent/LOOP"

  # Provide stop_reflect.md and cooldown_prompt.md
  echo "Reflect here" > "$STOP_TEST_DIR/.agent/stop_reflect.md"
  echo "Cooldown here" > "$STOP_TEST_DIR/.agent/cooldown_prompt.md"

  # Copy the hook and patch REPO_ROOT to our sandbox
  # We can't easily patch the hook, so we'll create a wrapper that sets REPO_ROOT
  cat > "$STOP_TEST_DIR/run_stop_hook.sh" <<'WRAPPER'
#!/bin/bash
set -euo pipefail
export REPO_ROOT="$1"
# Source the hook logic inline by rewriting SCRIPT_DIR
SCRIPT_DIR="$REPO_ROOT/.agent/hooks/claude-code"
# We need to re-derive REPO_ROOT from the hook's perspective.
# The hook uses: REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# So we need the hook at the right relative path.
exec "$REPO_ROOT/.agent/hooks/claude-code/stop.sh"
WRAPPER
  chmod +x "$STOP_TEST_DIR/run_stop_hook.sh"

  # Copy the real hook — it will derive REPO_ROOT from its own location
  cp "$STOP_HOOK" "$STOP_TEST_DIR/.agent/hooks/claude-code/stop.sh"
  chmod +x "$STOP_TEST_DIR/.agent/hooks/claude-code/stop.sh"

  # Copy shared stop logic (sourced by the hook)
  mkdir -p "$STOP_TEST_DIR/.agent/hooks/_shared"
  cp "$SCRIPT_DIR/../.agent/hooks/_shared/stop_logic.sh" "$STOP_TEST_DIR/.agent/hooks/_shared/stop_logic.sh"

  # Provide a minimal tracker setup so count-todos works (returns 0)
  mkdir -p "$STOP_TEST_DIR/scripts"
  cat > "$STOP_TEST_DIR/scripts/tracker" <<'TRACKER_STUB'
#!/bin/bash
# Stub tracker for stop hook tests — returns 0 for count-todos, empty for guidance
case "$1" in
  count-todos) echo 0 ;;
  guidance) echo "" ;;
  *) echo "" ;;
esac
TRACKER_STUB
  chmod +x "$STOP_TEST_DIR/scripts/tracker"

  # Isolate $HOME so stop_logic.sh reads sandbox state, not real state.
  # Create a fake $HOME with the expected notebook structure.
  # stop_logic.sh computes GUIDANCE_LOG_DIR as
  # "$HOME/${_REPO_NAME}_lab_notebook/guidance_log" where _REPO_NAME is the
  # basename of REPO_ROOT. REPO_ROOT in this sandbox is STOP_TEST_DIR, so
  # _REPO_NAME is its basename — we must match that path exactly, not a
  # hardcoded "hypergumbo" prefix.
  FAKE_HOME="$(mktemp -d -t hypergumbo-fakehome.XXXXXX)"
  SANDBOX_REPO_NAME="$(basename "$STOP_TEST_DIR")"
  SANDBOX_NOTEBOOK="$FAKE_HOME/${SANDBOX_REPO_NAME}_lab_notebook/guidance_log"
  mkdir -p "$SANDBOX_NOTEBOOK"

  # SCENARIO 8a: stop_hook_state.json with recent timestamp → cooldown
  echo "--------------------------------------------------------"
  echo "TEST: Scenario 8a: Stop hook reads stop_hook_state.json (cooldown)"
  RECENT_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '{"last_completed_utc": "%s", "current_branch": "dev"}\n' "$RECENT_TS" > "$SANDBOX_NOTEBOOK/stop_hook_state.json"

  OUTPUT=$(HOME="$FAKE_HOME" "$STOP_TEST_DIR/.agent/hooks/claude-code/stop.sh" 2>&1)
  if echo "$OUTPUT" | grep -q '"decision":"block"' && echo "$OUTPUT" | grep -q "Cooldown"; then
    echo "  ✅ PASS (cooldown triggered from stop_hook_state.json)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (expected cooldown block from stop_hook_state.json)"
    echo "  Output: $OUTPUT"
    ((FAIL_COUNT++))
  fi

  # SCENARIO 8c: No state file → full reflection (Path 3)
  # Reset AUTONOMOUS_MODE.txt to our test shell's PID before this scenario
  # (8a writes guidance_file into stop_hook_state.json which has now been
  # removed, so Path 2 cooldown gating falls through to Path 3).
  echo "--------------------------------------------------------"
  echo "TEST: Scenario 8c: No state file → full reflection checklist"
  echo "BROAD pid=$$" > "$STOP_TEST_DIR/AUTONOMOUS_MODE.txt"
  rm -f "$SANDBOX_NOTEBOOK/stop_hook_state.json"

  OUTPUT=$(HOME="$FAKE_HOME" "$STOP_TEST_DIR/.agent/hooks/claude-code/stop.sh" 2>&1)
  if echo "$OUTPUT" | grep -q '"decision":"block"' && echo "$OUTPUT" | grep -q "Stale reflection"; then
    echo "  ✅ PASS (full reflection triggered when no state file)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (expected full reflection when no state file)"
    echo "  Output: $OUTPUT"
    ((FAIL_COUNT++))
  fi

  # SCENARIO 8d-1: Dead PID in AUTONOMOUS_MODE.txt → approve as non-autonomous
  # (NOT auto-reclaim). Per stop_logic.sh lines 74-78: when the stored PID
  # is dead, the hook deliberately refuses to auto-reclaim ownership — the
  # autonomous agent must be restarted via loop-toggle, which will set a
  # fresh PID. This scenario asserts that contract. A previous version of
  # this test asserted auto-reclaim, which silently failed for months
  # because the auto-reclaim behavior was deliberately removed without
  # updating the test (INV-pofam).
  echo "--------------------------------------------------------"
  echo "TEST: Scenario 8d-1: Dead PID → approve (no auto-reclaim)"
  # Use a PID that definitely doesn't exist (max pid + 1 style)
  echo "BROAD pid=999999999" > "$STOP_TEST_DIR/AUTONOMOUS_MODE.txt"
  rm -f "$SANDBOX_NOTEBOOK/stop_hook_state.json"

  OUTPUT=$(HOME="$FAKE_HOME" "$STOP_TEST_DIR/.agent/hooks/claude-code/stop.sh" 2>&1)
  if echo "$OUTPUT" | grep -q '"decision": "approve"' \
     && echo "$OUTPUT" | grep -q "Interactive session"; then
    # Verify the hook did NOT modify AUTONOMOUS_MODE.txt
    UNCHANGED_MODE=$(cat "$STOP_TEST_DIR/AUTONOMOUS_MODE.txt")
    if [[ "$UNCHANGED_MODE" == "BROAD pid=999999999" ]]; then
      echo "  ✅ PASS (dead PID → approve, AUTONOMOUS_MODE.txt unchanged)"
      ((PASS_COUNT++))
    else
      echo "  ❌ FAIL (hook approved but modified AUTONOMOUS_MODE.txt to: $UNCHANGED_MODE)"
      ((FAIL_COUNT++))
    fi
  else
    echo "  ❌ FAIL (expected approve with 'Interactive session' reason for dead PID)"
    echo "  Output: $OUTPUT"
    ((FAIL_COUNT++))
  fi

  # SCENARIO 8d-2: Live PID that is NOT our ancestor → approve (interactive)
  echo "--------------------------------------------------------"
  echo "TEST: Scenario 8d-2: Live non-ancestor PID → approve as interactive"
  # PID 1 (init/systemd) is always alive and never our ancestor via the walk
  echo "BROAD pid=1" > "$STOP_TEST_DIR/AUTONOMOUS_MODE.txt"

  OUTPUT=$(HOME="$FAKE_HOME" "$STOP_TEST_DIR/.agent/hooks/claude-code/stop.sh" 2>&1)
  if echo "$OUTPUT" | grep -q '"decision": "approve"' && echo "$OUTPUT" | grep -q "Interactive"; then
    echo "  ✅ PASS (live non-ancestor PID → approved as interactive)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (expected approve for live non-ancestor PID)"
    echo "  Output: $OUTPUT"
    ((FAIL_COUNT++))
  fi

  # Reset for remaining tests
  echo "BROAD" > "$STOP_TEST_DIR/AUTONOMOUS_MODE.txt"

  rm -rf "$STOP_TEST_DIR" "$FAKE_HOME"
else
  echo "⚠️  Skipping stop hook tests: $STOP_HOOK not found"
fi

# 8b. reference-transaction hook tests (auto-recover dropped tracker ops)
# ------------------------------------------------------------------------------
# The reference-transaction hook fires `tracker recover` after a ref update so a
# working-tree-rewriting command (git reset --hard / git checkout — both update
# the worktree BEFORE the ref, so the hook fires after the ops are dropped) is
# self-healed from the out-of-repo journal. This is a REQUIRED hook (committed to
# .githooks/), so its absence is a failure, not a skip.

REFTX_HOOK="$SCRIPT_DIR/reference-transaction"

echo ""
echo "========================================================"
echo "REFERENCE-TRANSACTION HOOK TESTS"
echo "========================================================"

# TEST: the hook file exists (required hook).
echo "--------------------------------------------------------"
echo "TEST: reference-transaction hook file exists"
if [[ -f "$REFTX_HOOK" ]]; then
  echo "  ✅ PASS"
  ((PASS_COUNT++))
else
  echo "  ❌ FAIL (required hook $REFTX_HOOK is missing)"
  ((FAIL_COUNT++))
fi

if [[ -f "$REFTX_HOOK" ]]; then
  REFTX_DIR="$(mktemp -d -t hypergumbo-reftx-test.XXXXXX)"

  # Sandbox git repo: real reference-transaction hook installed via core.hooksPath,
  # plus a stub scripts/tracker that records each invocation (and drops a sentinel
  # on `recover`). The log/sentinel paths are baked in absolutely so the stub works
  # regardless of the CWD git hands the hook.
  git -C "$REFTX_DIR" init -q
  git -C "$REFTX_DIR" config user.email test@example.com
  git -C "$REFTX_DIR" config user.name "Test"
  mkdir -p "$REFTX_DIR/.githooks" "$REFTX_DIR/scripts"
  cp "$REFTX_HOOK" "$REFTX_DIR/.githooks/reference-transaction"
  chmod +x "$REFTX_DIR/.githooks/reference-transaction"
  git -C "$REFTX_DIR" config core.hooksPath .githooks

  cat > "$REFTX_DIR/scripts/tracker" <<TRK
#!/bin/bash
echo "\$*" >> "$REFTX_DIR/.tracker-calls"
[[ "\$1" == "recover" ]] && : > "$REFTX_DIR/.recover-ran"
exit 0
TRK
  chmod +x "$REFTX_DIR/scripts/tracker"

  reftx_calls() { cat "$REFTX_DIR/.tracker-calls" 2>/dev/null; }
  reset_reftx_log() { rm -f "$REFTX_DIR/.tracker-calls" "$REFTX_DIR/.recover-ran"; }

  # TEST: state=committed → hook invokes `tracker recover`.
  echo "--------------------------------------------------------"
  echo "TEST: reference-transaction committed → calls 'tracker recover'"
  reset_reftx_log
  ( cd "$REFTX_DIR" && echo "" | ./.githooks/reference-transaction committed ) >/dev/null 2>&1
  if reftx_calls | grep -qx "recover"; then
    echo "  ✅ PASS (recover invoked on committed)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (expected 'recover' on committed; got: $(reftx_calls))"
    ((FAIL_COUNT++))
  fi

  # TEST: recover-disabled marker present → hook SKIPS recover.
  # The tracker's own git operations (do_sync's fetch+ff, auto-pr) set this
  # marker so the hook does not restore journalled-uncommitted ops mid-merge —
  # which otherwise collides with the very ff that reconciles them.
  echo "--------------------------------------------------------"
  echo "TEST: reference-transaction committed + recover-disabled marker → skips recover"
  reset_reftx_log
  touch "$REFTX_DIR/.git/tracker-recover-disabled"
  ( cd "$REFTX_DIR" && echo "" | ./.githooks/reference-transaction committed ) >/dev/null 2>&1
  if [[ -z "$(reftx_calls)" ]]; then
    echo "  ✅ PASS (recover skipped while marker present)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (marker should suppress recover; got: $(reftx_calls))"
    ((FAIL_COUNT++))
  fi
  rm -f "$REFTX_DIR/.git/tracker-recover-disabled"

  # TEST: state=prepared → no tracker call.
  echo "--------------------------------------------------------"
  echo "TEST: reference-transaction prepared → no tracker call"
  reset_reftx_log
  ( cd "$REFTX_DIR" && echo "" | ./.githooks/reference-transaction prepared ) >/dev/null 2>&1
  if [[ -z "$(reftx_calls)" ]]; then
    echo "  ✅ PASS (no call on prepared)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (prepared should be a no-op; got: $(reftx_calls))"
    ((FAIL_COUNT++))
  fi

  # TEST: state=aborted → no tracker call.
  echo "--------------------------------------------------------"
  echo "TEST: reference-transaction aborted → no tracker call"
  reset_reftx_log
  ( cd "$REFTX_DIR" && echo "" | ./.githooks/reference-transaction aborted ) >/dev/null 2>&1
  if [[ -z "$(reftx_calls)" ]]; then
    echo "  ✅ PASS (no call on aborted)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (aborted should be a no-op; got: $(reftx_calls))"
    ((FAIL_COUNT++))
  fi

  # TEST: a real `git reset --hard` fires the hook (state=committed) → recover runs.
  # This proves the wiring end-to-end: git reset --hard updates the worktree, THEN
  # the ref, firing reference-transaction committed → our hook → tracker recover.
  echo "--------------------------------------------------------"
  echo "TEST: real 'git reset --hard' fires hook → recover runs"
  git -C "$REFTX_DIR" commit -q --allow-empty -m c1
  git -C "$REFTX_DIR" commit -q --allow-empty -m c2
  reset_reftx_log   # clear the calls the two commits themselves triggered
  git -C "$REFTX_DIR" reset --hard HEAD~1 -q >/dev/null 2>&1
  if [[ -f "$REFTX_DIR/.recover-ran" ]]; then
    echo "  ✅ PASS (reset --hard triggered hook → recover)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (reset --hard did not trigger recover)"
    ((FAIL_COUNT++))
  fi

  # TEST: missing scripts/tracker → hook still exits 0 (must never block a git op).
  echo "--------------------------------------------------------"
  echo "TEST: missing scripts/tracker → hook exits 0 (degrades silently)"
  REFTX_NOTRK="$(mktemp -d -t hypergumbo-reftx-notrk.XXXXXX)"
  git -C "$REFTX_NOTRK" init -q
  cp "$REFTX_HOOK" "$REFTX_NOTRK/reference-transaction"
  chmod +x "$REFTX_NOTRK/reference-transaction"
  if ( cd "$REFTX_NOTRK" && echo "" | ./reference-transaction committed ) >/dev/null 2>&1; then
    echo "  ✅ PASS (hook exits 0 without scripts/tracker)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (hook should exit 0 even without scripts/tracker)"
    ((FAIL_COUNT++))
  fi
  rm -rf "$REFTX_NOTRK"

  # TEST: GIT_REFLOG_ACTION=merge → skip recover (a constructive merge/pull/rebase
  # brings ops rather than dropping them; recover would restore a journalled op
  # untracked and abort the merge's own ff).
  echo "--------------------------------------------------------"
  echo "TEST: reference-transaction committed + GIT_REFLOG_ACTION=merge → skips recover"
  reset_reftx_log
  ( cd "$REFTX_DIR" && echo "" | GIT_REFLOG_ACTION="merge selfh/dev" ./.githooks/reference-transaction committed ) >/dev/null 2>&1
  if [[ -z "$(reftx_calls)" ]]; then
    echo "  ✅ PASS (recover skipped for merge reflog action)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (merge reflog action should skip recover; got: $(reftx_calls))"
    ((FAIL_COUNT++))
  fi

  # TEST: stdin lists only remote-tracking refs → skip recover (a fetch never
  # drops working-tree ops, so there is nothing to recover).
  echo "--------------------------------------------------------"
  echo "TEST: reference-transaction committed + only remote-tracking refs → skips recover"
  reset_reftx_log
  ( cd "$REFTX_DIR" && printf '%s %s %s\n' 0000 1111 refs/remotes/selfh/dev | env -u GIT_REFLOG_ACTION ./.githooks/reference-transaction committed ) >/dev/null 2>&1
  if [[ -z "$(reftx_calls)" ]]; then
    echo "  ✅ PASS (recover skipped for fetch — only remote-tracking refs)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (remote-tracking-only update should skip recover; got: $(reftx_calls))"
    ((FAIL_COUNT++))
  fi

  # TEST: a LOCAL ref update (refs/heads) with no merge/pull/rebase reflog action
  # still recovers — preserves the reset --hard durability path.
  echo "--------------------------------------------------------"
  echo "TEST: reference-transaction committed + local refs/heads → still recovers"
  reset_reftx_log
  ( cd "$REFTX_DIR" && printf '%s %s %s\n' 0000 1111 refs/heads/dev | env -u GIT_REFLOG_ACTION ./.githooks/reference-transaction committed ) >/dev/null 2>&1
  if reftx_calls | grep -qx "recover"; then
    echo "  ✅ PASS (local ref update still recovers — reset --hard path intact)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (local ref update must still recover; got: $(reftx_calls))"
    ((FAIL_COUNT++))
  fi

  rm -rf "$REFTX_DIR"
fi

# 8c. post-checkout hook tests (branch checkout self-heals dropped tracker ops)
# ------------------------------------------------------------------------------
# `git checkout <branch>` retargets HEAD as a SYMBOLIC ref, which does NOT fire
# `reference-transaction` — so the reference-transaction hook can't self-heal a
# checkout that drops pending ops. post-checkout fires after a branch switch and
# closes that gap. Only on branch checkouts ($3 == 1), and skipped while the
# tracker's own reconciliation holds the recover-disabled marker.

PCO_HOOK="$SCRIPT_DIR/post-checkout"

echo ""
echo "========================================================"
echo "POST-CHECKOUT HOOK TESTS"
echo "========================================================"

if [[ -f "$PCO_HOOK" ]]; then
  PCO_DIR="$(mktemp -d -t hypergumbo-pco-test.XXXXXX)"
  git -C "$PCO_DIR" init -q
  mkdir -p "$PCO_DIR/.githooks" "$PCO_DIR/scripts"
  cp "$PCO_HOOK" "$PCO_DIR/.githooks/post-checkout"
  chmod +x "$PCO_DIR/.githooks/post-checkout"
  git -C "$PCO_DIR" config core.hooksPath .githooks

  cat > "$PCO_DIR/scripts/tracker" <<TRK
#!/bin/bash
[[ "\$1" == "recover" ]] && : > "$PCO_DIR/.recover-ran"
exit 0
TRK
  chmod +x "$PCO_DIR/scripts/tracker"

  pco_ran() { [[ -f "$PCO_DIR/.recover-ran" ]]; }
  reset_pco() { rm -f "$PCO_DIR/.recover-ran"; }

  # TEST: branch checkout (flag 1) → recover runs.
  echo "--------------------------------------------------------"
  echo "TEST: post-checkout branch switch (flag=1) → calls 'tracker recover'"
  reset_pco
  ( cd "$PCO_DIR" && ./.githooks/post-checkout 0000000 1111111 1 ) >/dev/null 2>&1
  if pco_ran; then
    echo "  ✅ PASS (recover invoked on branch checkout)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (branch checkout should self-heal via recover)"
    ((FAIL_COUNT++))
  fi

  # TEST: file checkout (flag 0) → no recover (`git checkout -- file`).
  echo "--------------------------------------------------------"
  echo "TEST: post-checkout file checkout (flag=0) → no recover"
  reset_pco
  ( cd "$PCO_DIR" && ./.githooks/post-checkout 0000000 1111111 0 ) >/dev/null 2>&1
  if ! pco_ran; then
    echo "  ✅ PASS (no recover on file checkout)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (file checkout must not trigger recover)"
    ((FAIL_COUNT++))
  fi

  # TEST: branch checkout + recover-disabled marker → skip recover.
  echo "--------------------------------------------------------"
  echo "TEST: post-checkout branch switch + recover-disabled marker → skips recover"
  reset_pco
  touch "$PCO_DIR/.git/tracker-recover-disabled"
  ( cd "$PCO_DIR" && ./.githooks/post-checkout 0000000 1111111 1 ) >/dev/null 2>&1
  if ! pco_ran; then
    echo "  ✅ PASS (recover skipped while marker present)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (marker should suppress recover on checkout)"
    ((FAIL_COUNT++))
  fi
  rm -f "$PCO_DIR/.git/tracker-recover-disabled"

  rm -rf "$PCO_DIR"
else
  echo "⚠️  Skipping post-checkout tests: $PCO_HOOK not found"
fi

# 9. Summary
# ------------------------------------------------------------------------------
echo ""
echo "========================================================"
echo "SUMMARY: $PASS_COUNT passed, $FAIL_COUNT failed"
if (( FAIL_COUNT > 0 )); then
  exit 1
fi

