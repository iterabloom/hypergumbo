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

# 7. Summary
# ------------------------------------------------------------------------------
echo "========================================================"
echo "SUMMARY: $PASS_COUNT passed, $FAIL_COUNT failed"
if (( FAIL_COUNT > 0 )); then
  exit 1
fi

