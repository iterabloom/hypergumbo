#!/bin/bash
# Shared stop hook logic — sourced by all vendor hooks.
# See ADR-0008 for governance protocol.
#
# Expects REPO_ROOT to be set by the caller.
# Exports: TOTAL_HARD, TOTAL_SOFT, TOTAL_TODOS, CIRCUIT_BREAKER_TRIPPED,
#          GUIDANCE_FILE, BAKEOFF_SUFFIX, COOLDOWN_CONTENT, COOLDOWN_NOTES_SECTION,
#          REFLECTION_CONTENT, STATE_FILE, ELAPSED_MIN
#
# Callers handle: early exits (autonomous mode check, loop sentinel, vendor guards)
#                 and output formatting (JSON shape varies per vendor).

# --- Setup ---
GUIDANCE_LOG_DIR="$HOME/hypergumbo_lab_notebook/guidance_log"
mkdir -p "$GUIDANCE_LOG_DIR"
HASH_FILE="/tmp/hypergumbo_stop_hashes"
HASH_THRESHOLD=5
WORK_ITEMS_FILE="$GUIDANCE_LOG_DIR/work_items.md"
LEDGER_FILE="$REPO_ROOT/.agent/invariant-ledger.md"

# --- Count hard TODOs (TODO! — invariant/defect, investigate deeply) ---
# Note: grep -c exits 1 on no match but still outputs "0", so
# $(grep -c ... || echo 0) would capture "0\n0". Use || true and default.
HARD_TODO_COUNT=$(grep -c '^\s*- \*\*TODO!\*\*' "$LEDGER_FILE" 2>/dev/null) || HARD_TODO_COUNT=0
WORK_HARD_COUNT=0
if [[ -f "$WORK_ITEMS_FILE" ]]; then
  WORK_HARD_COUNT=$(grep -c '^\s*- \*\*TODO!\*\*' "$WORK_ITEMS_FILE" 2>/dev/null) || WORK_HARD_COUNT=0
fi
TOTAL_HARD=$((HARD_TODO_COUNT + WORK_HARD_COUNT))

# --- Count soft TODOs (TODO without ! — backlog, address or defer freely) ---
# Pattern: **TODO** followed by non-! character (avoids matching **TODO!**)
SOFT_TODO_COUNT=$(grep -c '^\s*- \*\*TODO\*\*[^!]' "$LEDGER_FILE" 2>/dev/null) || SOFT_TODO_COUNT=0
WORK_SOFT_COUNT=0
if [[ -f "$WORK_ITEMS_FILE" ]]; then
  WORK_SOFT_COUNT=$(grep -c '^\s*- \*\*TODO\*\*[^!]' "$WORK_ITEMS_FILE" 2>/dev/null) || WORK_SOFT_COUNT=0
fi
TOTAL_SOFT=$((SOFT_TODO_COUNT + WORK_SOFT_COUNT))

TOTAL_TODOS=$((TOTAL_HARD + TOTAL_SOFT))

# --- Circuit breaker (hash-based no-progress detection) ---
CIRCUIT_BREAKER_TRIPPED=false
if [[ "$TOTAL_TODOS" -gt 0 ]]; then
  TODO_CONTENT=$(grep '^\s*- \*\*TODO[!]\{0,1\}\*\*' "$LEDGER_FILE" 2>/dev/null || true)
  if [[ -f "$WORK_ITEMS_FILE" ]]; then
    WORK_CONTENT=$(grep '^\s*- \*\*TODO[!]\{0,1\}\*\*' "$WORK_ITEMS_FILE" 2>/dev/null || true)
    TODO_CONTENT="${TODO_CONTENT}${WORK_CONTENT}"
  fi
  CURRENT_HASH=$(printf '%s' "$TODO_CONTENT" | sha256sum | cut -d' ' -f1)
  echo "$CURRENT_HASH" >> "$HASH_FILE"
  TAIL_COUNT=$(tail -n "$HASH_THRESHOLD" "$HASH_FILE" | wc -l)
  UNIQUE_COUNT=$(tail -n "$HASH_THRESHOLD" "$HASH_FILE" | sort -u | wc -l)
  if [[ "$TAIL_COUNT" -ge "$HASH_THRESHOLD" && "$UNIQUE_COUNT" -eq 1 ]]; then
    CIRCUIT_BREAKER_TRIPPED=true
  fi
fi

# --- Write guidance file (if any TODOs exist) ---
GUIDANCE_FILE=""
if [[ "$TOTAL_TODOS" -gt 0 ]]; then
  TIMESTAMP=$(date +%m%d%Y_%H%M)
  GUIDANCE_FILE="$GUIDANCE_LOG_DIR/stop_guidance_${TIMESTAMP}.md"
  {
    echo "# Stop Hook Guidance — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo ""
    echo "## Status"
    echo "- Hard TODOs (blocking): $TOTAL_HARD (ledger: $HARD_TODO_COUNT, work_items: $WORK_HARD_COUNT)"
    echo "- Soft TODOs (informational): $TOTAL_SOFT (ledger: $SOFT_TODO_COUNT, work_items: $WORK_SOFT_COUNT)"
    echo "- Circuit breaker: $CIRCUIT_BREAKER_TRIPPED (threshold: $HASH_THRESHOLD)"
    echo ""
    if [[ "$TOTAL_HARD" -gt 0 ]]; then
      echo "## Hard TODO Items (TODO! — investigate deeply, assume structural)"
      grep '^\s*- \*\*TODO!\*\*' "$LEDGER_FILE" 2>/dev/null || true
      [[ -f "$WORK_ITEMS_FILE" ]] && grep '^\s*- \*\*TODO!\*\*' "$WORK_ITEMS_FILE" 2>/dev/null || true
      echo ""
    fi
    if [[ "$TOTAL_SOFT" -gt 0 ]]; then
      echo "## Soft TODO Items (TODO — address or defer freely)"
      grep '^\s*- \*\*TODO\*\*[^!]' "$LEDGER_FILE" 2>/dev/null || true
      [[ -f "$WORK_ITEMS_FILE" ]] && grep '^\s*- \*\*TODO\*\*[^!]' "$WORK_ITEMS_FILE" 2>/dev/null || true
      echo ""
    fi
    echo "## Guidance"
    echo "TODO! items: investigate deeply, assume structural. Fix or explicitly DEFER with justification."
    echo "TODO items: address or defer freely. OK to defer if higher-priority work exists."
  } > "$GUIDANCE_FILE"

  # Update last_stop_check.json with guidance_file pointer
  STATE_FILE_FOR_GF="$REPO_ROOT/.agent/last_stop_check.json"
  if command -v jq &>/dev/null && [[ -f "$STATE_FILE_FOR_GF" ]]; then
    TMP=$(mktemp)
    if jq --arg gf "$GUIDANCE_FILE" '. + {guidance_file: $gf}' "$STATE_FILE_FOR_GF" > "$TMP" 2>/dev/null; then
      mv "$TMP" "$STATE_FILE_FOR_GF"
    else
      rm -f "$TMP"
    fi
  fi
fi

# --- Bakeoff convergence summary (shared across all vendors) ---
BAKEOFF_SUFFIX=""
BAKEOFF_DIR="$HOME/hypergumbo_lab_notebook/bakeoff_artifacts"
if [[ -d "$BAKEOFF_DIR" ]]; then
  LATEST_STATE=$(find "$BAKEOFF_DIR" -maxdepth 2 -name state.json -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
  if [[ -n "$LATEST_STATE" ]]; then
    BAKEOFF_SUMMARY=$(python3 -c "
import json, sys
try:
    with open('$LATEST_STATE') as f:
        state = json.load(f)
    ch = state.get('convergence_history', [])
    if not ch:
        sys.exit(0)
    latest = ch[-1]
    crit = latest.get('critical', 0)
    high = latest.get('high', 0)
    new = latest.get('new_issues', 0)
    cohort_num = latest.get('cohort', '?')
    iteration = latest.get('iteration', '?')
    if crit == 0 and high == 0 and new == 0:
        print(f'CONVERGED cohort={cohort_num} iter={iteration}')
    else:
        print(f'NEEDS_WORK cohort={cohort_num} iter={iteration} critical={crit} high={high} new={new}')
except Exception:
    pass
" 2>/dev/null || true)

    if [[ "$BAKEOFF_SUMMARY" == CONVERGED* ]]; then
      BAKEOFF_SUFFIX=$'\n\n---\nBakeoff convergence: '"$BAKEOFF_SUMMARY"$'\nLatest bakeoff session is CONVERGED — no critical/high issues. Running another bakeoff on the same cohort would be redundant. Consider: selecting a new cohort, mining existing artifacts, or moving to other work items.'
    elif [[ "$BAKEOFF_SUMMARY" == NEEDS_WORK* ]]; then
      BAKEOFF_SUFFIX=$'\n\n---\nBakeoff convergence: '"$BAKEOFF_SUMMARY"$'\nLatest bakeoff session has outstanding issues. Consider investigating these before starting new work.'
    fi
  fi
fi

# --- Cooldown & reflection content (pre-computed for vendor wrappers) ---
STATE_FILE="$REPO_ROOT/.agent/last_stop_check.json"
# Backward compat: fall back to old filename if new one doesn't exist
if [[ ! -f "$STATE_FILE" && -f "$REPO_ROOT/.agent/stop_hook_state.json" ]]; then
  STATE_FILE="$REPO_ROOT/.agent/stop_hook_state.json"
fi

ELAPSED_MIN=9999  # Default: stale (will trigger Path 3)
COOLDOWN_CONTENT=""
COOLDOWN_NOTES_SECTION=""
if [[ -f "$STATE_FILE" ]]; then
  LAST_TS=$(jq -r '.last_completed_utc // "1970-01-01T00:00:00Z"' "$STATE_FILE" 2>/dev/null || echo "1970-01-01T00:00:00Z")
  LAST_EPOCH=$(date -d "$LAST_TS" +%s 2>/dev/null || echo 0)
  NOW_EPOCH=$(date +%s)
  ELAPSED_MIN=$(( (NOW_EPOCH - LAST_EPOCH) / 60 ))

  if [[ "$ELAPSED_MIN" -lt 30 ]]; then
    COOLDOWN_CONTENT=$(cat "$REPO_ROOT/.agent/cooldown_prompt.md")
    NOTES=$(jq -r '.notes // ""' "$STATE_FILE" 2>/dev/null || true)
    if [[ -n "$NOTES" ]]; then
      COOLDOWN_NOTES_SECTION=$(printf '\n\n---\n## LAST REFLECTION NOTES\n%s\n---' "$NOTES")
    fi
  fi
fi

REFLECTION_CONTENT=$(cat "$REPO_ROOT/.agent/stop_reflect.md")
