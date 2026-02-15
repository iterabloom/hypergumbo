#!/bin/bash
# Shared stop hook logic — sourced by all vendor hooks.
# See ADR-0008 for governance protocol; ADR-0013 for structured tracker.
#
# Uses the structured tracker CLI (scripts/tracker) for TODO counting,
# circuit breaker hashing, and guidance generation. Fail-closed: if the
# tracker CLI is present but fails, the hook blocks (exit 1).
#
# Expects REPO_ROOT to be set by the caller.
# Exports: TOTAL_HARD, TOTAL_SOFT, TOTAL_TODOS, CIRCUIT_BREAKER_TRIPPED,
#          GUIDANCE_FILE, GUIDANCE_FILE_COOLDOWN, GUIDANCE_FILE_REFLECTION,
#          STATE_FILE, ELAPSED_MIN

# --- Setup ---
GUIDANCE_LOG_DIR="$HOME/hypergumbo_lab_notebook/guidance_log"
mkdir -p "$GUIDANCE_LOG_DIR"
HASH_FILE="/tmp/hypergumbo_stop_hashes"
HASH_THRESHOLD=5

# --- Structured tracker (fail-closed) ---
TOTAL_HARD=0
TOTAL_SOFT=0
if [[ -x "$REPO_ROOT/scripts/tracker" ]] && [[ -d "$REPO_ROOT/.agent/tracker" ]]; then
  TOTAL_HARD=$("$REPO_ROOT/scripts/tracker" count-todos --hard 2>/dev/null) || \
    { echo "ERROR: tracker count-todos --hard failed" >&2; exit 1; }
  TOTAL_SOFT=$("$REPO_ROOT/scripts/tracker" count-todos --soft 2>/dev/null) || \
    { echo "ERROR: tracker count-todos --soft failed" >&2; exit 1; }
fi
TOTAL_TODOS=$((TOTAL_HARD + TOTAL_SOFT))

# --- Circuit breaker (hash-based no-progress detection) ---
CIRCUIT_BREAKER_TRIPPED=false
if [[ "$TOTAL_TODOS" -gt 0 ]]; then
  CURRENT_HASH=$("$REPO_ROOT/scripts/tracker" hash-todos 2>/dev/null) || CURRENT_HASH="fallback-$$"
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
  GUIDANCE_FILE=$("$REPO_ROOT/scripts/tracker" guidance --guidance-dir "$GUIDANCE_LOG_DIR" 2>/dev/null) || true
  if [[ -z "$GUIDANCE_FILE" ]] || [[ ! -f "$GUIDANCE_FILE" ]]; then
    echo "WARNING: tracker guidance generation failed, continuing without guidance file" >&2
    GUIDANCE_FILE=""
  fi

  # Update last_stop_check.json with guidance_file pointer
  if [[ -n "$GUIDANCE_FILE" ]]; then
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

# --- Cooldown & reflection: compute elapsed time, write guidance files ---
STATE_FILE="$REPO_ROOT/.agent/last_stop_check.json"
# Backward compat: fall back to old filename if new one doesn't exist
if [[ ! -f "$STATE_FILE" && -f "$REPO_ROOT/.agent/stop_hook_state.json" ]]; then
  STATE_FILE="$REPO_ROOT/.agent/stop_hook_state.json"
fi

ELAPSED_MIN=9999  # Default: stale (will trigger Path 3)
if [[ -f "$STATE_FILE" ]]; then
  LAST_TS=$(jq -r '.last_completed_utc // "1970-01-01T00:00:00Z"' "$STATE_FILE" 2>/dev/null || echo "1970-01-01T00:00:00Z")
  LAST_EPOCH=$(date -d "$LAST_TS" +%s 2>/dev/null || echo 0)
  NOW_EPOCH=$(date +%s)
  ELAPSED_MIN=$(( (NOW_EPOCH - LAST_EPOCH) / 60 ))
fi

# --- Write guidance file for Path 2: Cooldown ---
# Combines cooldown_prompt.md + last reflection notes + bakeoff suffix.
GUIDANCE_FILE_COOLDOWN=""
if [[ "$ELAPSED_MIN" -lt 30 ]]; then
  TIMESTAMP=$(date +%m%d%Y_%H%M)
  GUIDANCE_FILE_COOLDOWN="$GUIDANCE_LOG_DIR/stop_guidance_cooldown_${TIMESTAMP}.md"
  {
    cat "$REPO_ROOT/.agent/cooldown_prompt.md"
    # Append last reflection notes if present
    if [[ -f "$STATE_FILE" ]]; then
      NOTES=$(jq -r '.notes // ""' "$STATE_FILE" 2>/dev/null || true)
      if [[ -n "$NOTES" ]]; then
        printf '\n\n---\n## LAST REFLECTION NOTES\n%s\n---' "$NOTES"
      fi
    fi
    # Append bakeoff convergence if present
    if [[ -n "$BAKEOFF_SUFFIX" ]]; then
      printf '%s' "$BAKEOFF_SUFFIX"
    fi
  } > "$GUIDANCE_FILE_COOLDOWN"
fi

# --- Write guidance file for Path 3: Full reflection ---
# Combines stop_reflect.md + bakeoff suffix.
GUIDANCE_FILE_REFLECTION=""
if [[ "$ELAPSED_MIN" -ge 30 ]]; then
  TIMESTAMP=$(date +%m%d%Y_%H%M)
  GUIDANCE_FILE_REFLECTION="$GUIDANCE_LOG_DIR/stop_guidance_reflect_${TIMESTAMP}.md"
  {
    cat "$REPO_ROOT/.agent/stop_reflect.md"
    if [[ -n "$BAKEOFF_SUFFIX" ]]; then
      printf '%s' "$BAKEOFF_SUFFIX"
    fi
  } > "$GUIDANCE_FILE_REFLECTION"
fi
