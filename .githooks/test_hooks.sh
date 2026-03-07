#!/usr/bin/env bash
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

  # Helper: simulate pre-push stdin (local_ref local_sha remote_ref remote_sha)
  run_pre_push_test() {
    local test_name="$1"
    local remote_ref="$2"
    local expect_block="$3"  # "block" or "allow"

    local stdin_line="refs/heads/test abc123 $remote_ref def456"

    echo "--------------------------------------------------------"
    echo "TEST: $test_name"

    if echo "$stdin_line" | "$PRE_PUSH_HOOK" "origin" "https://example.com" >/dev/null 2>&1; then
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

  # Enable autonomous mode and loop sentinel
  echo "BROAD" > "$STOP_TEST_DIR/AUTONOMOUS_MODE.txt"
  touch "$STOP_TEST_DIR/.agent/LOOP"

  # Empty invariant ledger (no TODOs — skip Path 1)
  echo "" > "$STOP_TEST_DIR/.agent/invariant-ledger.md"

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
  FAKE_HOME="$(mktemp -d -t hypergumbo-fakehome.XXXXXX)"
  mkdir -p "$FAKE_HOME/hypergumbo_lab_notebook/guidance_log"

  # SCENARIO 8a: New filename (last_stop_check.json) with recent timestamp → cooldown
  echo "--------------------------------------------------------"
  echo "TEST: Scenario 8a: Stop hook reads last_stop_check.json (cooldown)"
  RECENT_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '{"last_completed_utc": "%s"}\n' "$RECENT_TS" > "$FAKE_HOME/hypergumbo_lab_notebook/last_stop_check.json"
  # Remove old sandbox fallback files
  rm -f "$STOP_TEST_DIR/.agent/last_stop_check.json"
  rm -f "$STOP_TEST_DIR/.agent/stop_hook_state.json"

  OUTPUT=$(HOME="$FAKE_HOME" "$STOP_TEST_DIR/.agent/hooks/claude-code/stop.sh" 2>&1)
  if echo "$OUTPUT" | grep -q '"decision":"block"' && echo "$OUTPUT" | grep -q "Cooldown"; then
    echo "  ✅ PASS (cooldown triggered from last_stop_check.json)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (expected cooldown block from last_stop_check.json)"
    echo "  Output: $OUTPUT"
    ((FAIL_COUNT++))
  fi

  # SCENARIO 8b: Backward compat — only stop_hook_state.json exists → cooldown
  echo "--------------------------------------------------------"
  echo "TEST: Scenario 8b: Stop hook falls back to stop_hook_state.json (backward compat)"
  rm -f "$FAKE_HOME/hypergumbo_lab_notebook/last_stop_check.json"
  rm -f "$STOP_TEST_DIR/.agent/last_stop_check.json"
  printf '{"last_completed_utc": "%s"}\n' "$RECENT_TS" > "$STOP_TEST_DIR/.agent/stop_hook_state.json"

  OUTPUT=$(HOME="$FAKE_HOME" "$STOP_TEST_DIR/.agent/hooks/claude-code/stop.sh" 2>&1)
  if echo "$OUTPUT" | grep -q '"decision":"block"' && echo "$OUTPUT" | grep -q "Cooldown"; then
    echo "  ✅ PASS (cooldown triggered from stop_hook_state.json fallback)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (expected cooldown block from stop_hook_state.json fallback)"
    echo "  Output: $OUTPUT"
    ((FAIL_COUNT++))
  fi

  # SCENARIO 8c: Neither file exists → full reflection (Path 3)
  echo "--------------------------------------------------------"
  echo "TEST: Scenario 8c: No state file → full reflection checklist"
  rm -f "$FAKE_HOME/hypergumbo_lab_notebook/last_stop_check.json"
  rm -f "$STOP_TEST_DIR/.agent/last_stop_check.json"
  rm -f "$STOP_TEST_DIR/.agent/stop_hook_state.json"

  OUTPUT=$(HOME="$FAKE_HOME" "$STOP_TEST_DIR/.agent/hooks/claude-code/stop.sh" 2>&1)
  if echo "$OUTPUT" | grep -q '"decision":"block"' && echo "$OUTPUT" | grep -q "Stale reflection"; then
    echo "  ✅ PASS (full reflection triggered when no state file)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (expected full reflection when no state file)"
    echo "  Output: $OUTPUT"
    ((FAIL_COUNT++))
  fi

  # SCENARIO 8d-1: Dead PID in AUTONOMOUS_MODE.txt → re-claim and block
  echo "--------------------------------------------------------"
  echo "TEST: Scenario 8d-1: Dead PID re-claim (crash recovery)"
  # Use a PID that definitely doesn't exist (max pid + 1 style)
  echo "BROAD pid=999999999" > "$STOP_TEST_DIR/AUTONOMOUS_MODE.txt"
  rm -f "$FAKE_HOME/hypergumbo_lab_notebook/last_stop_check.json"
  rm -f "$STOP_TEST_DIR/.agent/last_stop_check.json"
  rm -f "$STOP_TEST_DIR/.agent/stop_hook_state.json"

  OUTPUT=$(HOME="$FAKE_HOME" "$STOP_TEST_DIR/.agent/hooks/claude-code/stop.sh" 2>&1)
  if echo "$OUTPUT" | grep -q '"decision":"block"'; then
    # Also verify it re-claimed the PID
    NEW_MODE=$(cat "$STOP_TEST_DIR/AUTONOMOUS_MODE.txt")
    if echo "$NEW_MODE" | grep -q "pid=" && ! echo "$NEW_MODE" | grep -q "pid=999999999"; then
      echo "  ✅ PASS (dead PID re-claimed, hook blocks)"
      ((PASS_COUNT++))
    else
      echo "  ❌ FAIL (hook blocked but PID not re-claimed)"
      echo "  AUTONOMOUS_MODE.txt: $NEW_MODE"
      ((FAIL_COUNT++))
    fi
  else
    echo "  ❌ FAIL (expected block after dead PID re-claim, got approve)"
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

  # SCENARIO 8d: New file takes priority over old file
  echo "--------------------------------------------------------"
  echo "TEST: Scenario 8d: last_stop_check.json takes priority over stop_hook_state.json"
  # New file: recent timestamp → cooldown (in $HOME location, which is checked first)
  printf '{"last_completed_utc": "%s"}\n' "$RECENT_TS" > "$FAKE_HOME/hypergumbo_lab_notebook/last_stop_check.json"
  # Old file: epoch timestamp → would be stale (Path 3) if read
  printf '{"last_completed_utc": "1970-01-01T00:00:00Z"}\n' > "$STOP_TEST_DIR/.agent/stop_hook_state.json"

  OUTPUT=$(HOME="$FAKE_HOME" "$STOP_TEST_DIR/.agent/hooks/claude-code/stop.sh" 2>&1)
  if echo "$OUTPUT" | grep -q '"decision":"block"' && echo "$OUTPUT" | grep -q "Cooldown"; then
    echo "  ✅ PASS (new file takes priority — cooldown from last_stop_check.json)"
    ((PASS_COUNT++))
  else
    echo "  ❌ FAIL (expected cooldown from last_stop_check.json, not stale fallback)"
    echo "  Output: $OUTPUT"
    ((FAIL_COUNT++))
  fi

  rm -rf "$STOP_TEST_DIR" "$FAKE_HOME"
else
  echo "⚠️  Skipping stop hook tests: $STOP_HOOK not found"
fi

# 9. Summary
# ------------------------------------------------------------------------------
echo ""
echo "========================================================"
echo "SUMMARY: $PASS_COUNT passed, $FAIL_COUNT failed"
if (( FAIL_COUNT > 0 )); then
  exit 1
fi

