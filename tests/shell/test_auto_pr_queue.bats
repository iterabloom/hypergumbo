#!/usr/bin/env bats
# Tests for auto-pr vPR queue functionality
#
# These tests use AUTO_PR_SIMULATE_OUTAGE=1 to test offline behavior
# without actually contacting Codeberg.

setup() {
  # Create a temporary git repo for testing
  export TEST_DIR="$(mktemp -d)"
  export ORIGINAL_DIR="$(pwd)"
  export AUTO_PR_SCRIPT="$ORIGINAL_DIR/scripts/auto-pr"

  cd "$TEST_DIR"
  git init --initial-branch=dev
  git config user.email "test@example.com"
  git config user.name "Test User"

  # Create initial commit
  echo "initial" > README.md
  git add README.md
  git commit -m "initial commit"

  # Set up fake remote (just for git remote get-url to work)
  git remote add origin https://codeberg.org/test/repo.git

  # Set required env vars
  export FORGEJO_USER="testuser"
  export FORGEJO_TOKEN="testtoken"
  export AUTO_PR_SIMULATE_OUTAGE=1
}

teardown() {
  cd "$ORIGINAL_DIR"
  rm -rf "$TEST_DIR"
}

# ----------------------------------------------------------------------
# Queue empty state tests
# ----------------------------------------------------------------------

@test "list shows empty queue message when no vPRs queued" {
  run "$AUTO_PR_SCRIPT" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"No vPRs queued"* ]]
}

@test "status shows empty queue state" {
  run "$AUTO_PR_SCRIPT" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"Queue empty"* ]]
  [[ "$output" == *"remote workflow active"* ]]
}

@test "flush with empty queue exits cleanly" {
  run "$AUTO_PR_SCRIPT" flush
  [ "$status" -eq 0 ]
  [[ "$output" == *"No vPRs to flush"* ]]
}

# ----------------------------------------------------------------------
# Queueing with simulated outage
# ----------------------------------------------------------------------

@test "simulated outage queues as vPR" {
  git checkout -b test/feature-1
  echo "feature 1" > feature.txt
  git add feature.txt
  git commit -m "feat: add feature 1"

  run "$AUTO_PR_SCRIPT" "feat: add feature 1"
  [ "$status" -eq 0 ]
  [[ "$output" == *"[SIMULATED OUTAGE]"* ]]
  [[ "$output" == *"Queued as vPR #1"* ]]

  # Verify queue file exists
  [ -f ".git/PR_QUEUE" ]

  # Verify queue contains correct data
  run cat .git/PR_QUEUE
  [[ "$output" == *"test/feature-1"* ]]
  [[ "$output" == *"feat: add feature 1"* ]]
}

@test "second vPR gets queued with correct number" {
  # Create and queue first vPR
  git checkout -b test/feature-1
  echo "feature 1" > feature1.txt
  git add feature1.txt
  git commit -m "feat: feature 1"
  "$AUTO_PR_SCRIPT" "feat: feature 1"

  # Create second vPR branching from first
  git checkout -b test/feature-2
  echo "feature 2" > feature2.txt
  git add feature2.txt
  git commit -m "feat: feature 2"

  run "$AUTO_PR_SCRIPT" "feat: feature 2"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Queue already has 1 vPR"* ]]
  [[ "$output" == *"Queued as vPR #2"* ]]

  # Verify queue has 2 entries
  run wc -l < .git/PR_QUEUE
  [ "${output//[[:space:]]/}" -eq 2 ]
}

# ----------------------------------------------------------------------
# Queue listing
# ----------------------------------------------------------------------

@test "list shows all queued vPRs" {
  # Queue two vPRs
  git checkout -b test/feat-a
  git commit --allow-empty -m "feat A"
  "$AUTO_PR_SCRIPT" "feat A"

  git checkout -b test/feat-b
  git commit --allow-empty -m "feat B"
  "$AUTO_PR_SCRIPT" "feat B"

  run "$AUTO_PR_SCRIPT" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"Queued vPRs (2 total)"* ]]
  [[ "$output" == *"vPR #1: feat A"* ]]
  [[ "$output" == *"vPR #2: feat B"* ]]
  [[ "$output" == *"Queue tip: test/feat-b"* ]]
}

@test "status shows queue tip when non-empty" {
  git checkout -b test/my-feature
  git commit --allow-empty -m "my feature"
  "$AUTO_PR_SCRIPT" "my feature"

  run "$AUTO_PR_SCRIPT" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"Queue has 1 vPR"* ]]
  [[ "$output" == *"offline workflow active"* ]]
  [[ "$output" == *"Queue tip: test/my-feature"* ]]
}

# ----------------------------------------------------------------------
# Flush behavior
# ----------------------------------------------------------------------

@test "flush with simulated outage keeps queue intact" {
  git checkout -b test/feature
  git commit --allow-empty -m "my feature"
  "$AUTO_PR_SCRIPT" "my feature"

  # Try to flush (should fail due to simulated outage)
  run "$AUTO_PR_SCRIPT" flush
  [ "$status" -eq 1 ]
  [[ "$output" == *"[SIMULATED OUTAGE]"* ]]
  [[ "$output" == *"vPRs remain queued"* ]]

  # Queue should still have the entry
  [ -f ".git/PR_QUEUE" ]
  run wc -l < .git/PR_QUEUE
  [ "${output//[[:space:]]/}" -eq 1 ]
}

@test "flush with multiple vPRs shows correct count" {
  # Queue 3 vPRs
  git checkout -b test/a
  git commit --allow-empty -m "A"
  "$AUTO_PR_SCRIPT" "A"

  git checkout -b test/b
  git commit --allow-empty -m "B"
  "$AUTO_PR_SCRIPT" "B"

  git checkout -b test/c
  git commit --allow-empty -m "C"
  "$AUTO_PR_SCRIPT" "C"

  run "$AUTO_PR_SCRIPT" flush
  [ "$status" -eq 1 ]
  [[ "$output" == *"Flushing 3 vPR(s)"* ]]
  [[ "$output" == *"flush: 3 vPRs from offline queue"* ]]
}

# ----------------------------------------------------------------------
# Queue-aware branching
# ----------------------------------------------------------------------

@test "warns when branch not based on queue tip" {
  # Queue a vPR
  git checkout -b test/feature-1
  git commit --allow-empty -m "feature 1"
  "$AUTO_PR_SCRIPT" "feature 1"

  # Create a branch from dev (not from queue tip)
  git checkout dev
  git checkout -b test/feature-2
  git commit --allow-empty -m "feature 2"

  # This should warn about not being based on queue tip
  # We need to answer 'n' to the prompt
  run bash -c 'echo "n" | '"$AUTO_PR_SCRIPT"' "feature 2"'
  [ "$status" -eq 1 ]
  [[ "$output" == *"not based on queue tip"* ]]
}

# ----------------------------------------------------------------------
# Error handling
# ----------------------------------------------------------------------

@test "refuses to run on dev branch" {
  run "$AUTO_PR_SCRIPT" "test"
  [ "$status" -eq 1 ]
  [[ "$output" == *"You are on 'dev'"* ]]
}

@test "refuses to run on main branch" {
  git checkout -b main
  run "$AUTO_PR_SCRIPT" "test"
  [ "$status" -eq 1 ]
  [[ "$output" == *"You are on 'main'"* ]]
}

# ----------------------------------------------------------------------
# Help
# ----------------------------------------------------------------------

@test "help shows vPR documentation" {
  run "$AUTO_PR_SCRIPT" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"vPR (Virtual PR)"* ]]
  [[ "$output" == *"AUTO_PR_SIMULATE_OUTAGE"* ]]
}
