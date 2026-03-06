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
#
# Optional env vars for dry-run (set by scripts/stop-hook-preview):
#   STOP_HOOK_DRY_RUN       - When non-empty, writes go to a temp dir instead
#                             of permanent locations. No side effects on disk.
#   STOP_HOOK_BAKEOFF_FILTER - Glob pattern for bakeoff session dirs (e.g.
#                             "broad-*"). Default: "*" (all sessions).

# --- Setup ---
GUIDANCE_LOG_DIR="$HOME/hypergumbo_lab_notebook/guidance_log"
mkdir -p "$GUIDANCE_LOG_DIR"
HASH_FILE="/tmp/hypergumbo_stop_hashes"
HASH_THRESHOLD=5

# --- Dry-run support ---
if [[ -n "${STOP_HOOK_DRY_RUN:-}" ]]; then
  _DRY_RUN_TMPDIR=$(mktemp -d)
fi

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

# --- Circuit breaker (file-change-based no-progress detection) ---
# Hashes file modification times in sentinel directories to detect whether
# real work product changed between stop events.  This measures whether the
# agent *did* make progress (files changed) rather than whether it *said* it
# made progress (tracker updated).  Dotfiles and invisible directories are
# excluded because tracker bookkeeping and git metadata shouldn't count as
# progress — source code and docs changes should.
CIRCUIT_BREAKER_TRIPPED=false
if [[ "$TOTAL_TODOS" -gt 0 ]]; then
  # Read sentinel dirs from tracker config, fall back to sensible defaults
  SENTINEL_DIRS=()
  if command -v python3 &>/dev/null; then
    while IFS= read -r dir; do
      [[ -n "$dir" ]] && SENTINEL_DIRS+=("$dir")
    done < <(python3 -c "
import yaml, os, sys
try:
    with open('$REPO_ROOT/.agent/tracker/config.yaml') as f:
        cfg = yaml.safe_load(f)
    dirs = cfg.get('stop_hook', {}).get('progress_sentinel_dirs', [])
    for d in dirs:
        d = os.path.expanduser(d)
        if not os.path.isabs(d):
            d = os.path.join('$REPO_ROOT', d)
        print(d)
except Exception:
    pass
" 2>/dev/null)
  fi
  # Fall back to defaults if config didn't provide any
  if [[ ${#SENTINEL_DIRS[@]} -eq 0 ]]; then
    SENTINEL_DIRS=("$REPO_ROOT/packages" "$REPO_ROOT/docs" "$REPO_ROOT/scripts")
  fi

  # Build file-change hash from sentinel directories
  FIND_ARGS=()
  for d in "${SENTINEL_DIRS[@]}"; do
    [[ -d "$d" ]] && FIND_ARGS+=("$d")
  done
  if [[ ${#FIND_ARGS[@]} -gt 0 ]]; then
    CURRENT_HASH=$(find "${FIND_ARGS[@]}" -not -path '*/.*' -not -path '*/guidance_log/*' -type f -printf '%p %T@\n' 2>/dev/null | sort | sha256sum | cut -d' ' -f1)
  else
    CURRENT_HASH="no-sentinel-dirs-$$"
  fi

  if [[ -z "${STOP_HOOK_DRY_RUN:-}" ]]; then
    echo "$CURRENT_HASH" >> "$HASH_FILE"
  fi
  TAIL_COUNT=$(tail -n "$HASH_THRESHOLD" "$HASH_FILE" | wc -l)
  UNIQUE_COUNT=$(tail -n "$HASH_THRESHOLD" "$HASH_FILE" | sort -u | wc -l)
  if [[ "$TAIL_COUNT" -ge "$HASH_THRESHOLD" && "$UNIQUE_COUNT" -eq 1 ]]; then
    CIRCUIT_BREAKER_TRIPPED=true
  fi
fi

# --- Bakeoff convergence summary (shared across all vendors) ---
# Computed early so it can be appended to guidance files.
BAKEOFF_SUFFIX=""
BAKEOFF_CONVERGENCE_LINE=""
BAKEOFF_DIR="$HOME/hypergumbo_lab_notebook/bakeoff_artifacts"
if [[ -d "$BAKEOFF_DIR" ]]; then
  BAKEOFF_GLOB="${STOP_HOOK_BAKEOFF_FILTER:-*}"
  LATEST_STATE=$(find "$BAKEOFF_DIR" -maxdepth 3 -path "*/${BAKEOFF_GLOB}/state.json" -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
  if [[ -n "$LATEST_STATE" ]]; then
    BAKEOFF_SUMMARY=$(python3 -c "
import json, sys
try:
    with open('$LATEST_STATE') as f:
        state = json.load(f)
    cohort_num = state.get('cohort_number', '?')
    iteration = state.get('iteration', '?')
    # BROAD schema: convergence_history with critical/high/new_issues
    ch = state.get('convergence_history') or []
    if ch:
        latest = ch[-1]
        crit = latest.get('critical', 0)
        high = latest.get('high', 0)
        new = latest.get('new_issues', 0)
        cohort_num = latest.get('cohort', cohort_num)
        iteration = latest.get('iteration', iteration)
        if crit == 0 and high == 0 and new == 0:
            print(f'CONVERGED cohort={cohort_num} iter={iteration}')
        else:
            print(f'NEEDS_WORK cohort={cohort_num} iter={iteration} critical={crit} high={high} new={new}')
        sys.exit(0)
    # DEEP schema: verdicts with per-repo verdict (GOOD/WARN/FAIL)
    verdicts = state.get('verdicts') or []
    if verdicts:
        good = sum(1 for v in verdicts if v.get('verdict') == 'GOOD')
        warn = sum(1 for v in verdicts if v.get('verdict') == 'WARN')
        fail = sum(1 for v in verdicts if v.get('verdict') == 'FAIL')
        # Collect worst repos (FAIL first, then WARN) with their top concern
        worst = []
        for v in verdicts:
            if v.get('verdict') in ('FAIL', 'WARN') and v.get('concerns'):
                worst.append(f\"{v['repo_name']}: {v['concerns'][0]}\")
        worst_str = ''
        if worst:
            worst_str = '\\n  Worst: ' + '; '.join(worst[:3])
        if fail == 0 and warn == 0:
            print(f'CONVERGED cohort={cohort_num} iter={iteration}')
        else:
            print(f'NEEDS_WORK cohort={cohort_num} iter={iteration} good={good} warn={warn} fail={fail}{worst_str}')
        sys.exit(0)
except Exception:
    pass
" 2>/dev/null || true)

    # Determine session type (broad vs deep) from directory name
    _SESSION_DIR=$(dirname "$LATEST_STATE")
    _SESSION_NAME=$(basename "$_SESSION_DIR")
    _SESSION_TYPE="broad"
    if [[ "$_SESSION_NAME" == deep-* ]]; then
      _SESSION_TYPE="deep"
    fi

    if [[ "$BAKEOFF_SUMMARY" == CONVERGED* ]]; then
      BAKEOFF_CONVERGENCE_LINE="$BAKEOFF_SUMMARY"
      if [[ "$_SESSION_TYPE" == "broad" ]]; then
        BAKEOFF_SUFFIX=$'\n\n---\nBakeoff convergence: '"$BAKEOFF_SUMMARY"$'\nLatest BROAD bakeoff session is CONVERGED — no critical/high issues.\nNext steps:\n  - Select a new cohort: ./scripts/bakeoff cohort --count 5\n  - Mine existing artifacts: ./scripts/bakeoff issues --format json\n  - Run LLM assessment: ./scripts/bakeoff-reflect\n  - Or move to other work items (tracker ready)'
      else
        BAKEOFF_SUFFIX=$'\n\n---\nBakeoff convergence: '"$BAKEOFF_SUMMARY"$'\nLatest DEEP bakeoff session is CONVERGED — all repos GOOD.\nNext steps:\n  - Select a new cohort: ./scripts/bakeoff-features cohort --count 4\n  - Compare sessions: ./scripts/bakeoff-features compare <A> <B>\n  - Run LLM assessment: ./scripts/bakeoff-features-reflect\n  - Or move to other work items (tracker ready)'
      fi
    elif [[ "$BAKEOFF_SUMMARY" == NEEDS_WORK* ]]; then
      BAKEOFF_CONVERGENCE_LINE="$BAKEOFF_SUMMARY"
      if [[ "$_SESSION_TYPE" == "broad" ]]; then
        BAKEOFF_SUFFIX=$'\n\n---\nBakeoff convergence: '"$BAKEOFF_SUMMARY"$'\nLatest BROAD bakeoff session has outstanding issues.\nInvestigate:\n  - View issues: ./scripts/bakeoff issues --format json\n  - Diagnose latest: ./scripts/bakeoff diagnose\n  - Check status: ./scripts/bakeoff status\n  - Re-run after fixes: ./scripts/bakeoff cycle'
      else
        BAKEOFF_SUFFIX=$'\n\n---\nBakeoff convergence: '"$BAKEOFF_SUMMARY"$'\nLatest DEEP bakeoff session has outstanding issues.\nInvestigate:\n  - Check status: ./scripts/bakeoff-features status\n  - Diagnose repos: ./scripts/bakeoff-features diagnose\n  - Re-run after fixes: ./scripts/bakeoff-features run\n  - View questions: ./scripts/bakeoff-features questions'
      fi
    fi
  fi
fi

# --- Write guidance file (if any TODOs exist) ---
GUIDANCE_FILE=""
if [[ "$TOTAL_TODOS" -gt 0 ]]; then
  if [[ -n "${STOP_HOOK_DRY_RUN:-}" ]]; then
    GUIDANCE_FILE=$("$REPO_ROOT/scripts/tracker" guidance --guidance-dir "$_DRY_RUN_TMPDIR" 2>/dev/null) || true
  else
    GUIDANCE_FILE=$("$REPO_ROOT/scripts/tracker" guidance --guidance-dir "$GUIDANCE_LOG_DIR" 2>/dev/null) || true
  fi
  if [[ -z "$GUIDANCE_FILE" ]] || [[ ! -f "$GUIDANCE_FILE" ]]; then
    echo "WARNING: tracker guidance generation failed, continuing without guidance file" >&2
    GUIDANCE_FILE=""
  fi

  # Phase 1a: append bakeoff convergence to guidance file
  if [[ -n "$GUIDANCE_FILE" && -n "$BAKEOFF_SUFFIX" ]]; then
    printf '%s' "$BAKEOFF_SUFFIX" >> "$GUIDANCE_FILE"
  fi

  # Update last_stop_check.json with guidance_file pointer + bakeoff convergence
  if [[ -n "$GUIDANCE_FILE" && -z "${STOP_HOOK_DRY_RUN:-}" ]]; then
    STATE_FILE_FOR_GF="$HOME/hypergumbo_lab_notebook/last_stop_check.json"
    if command -v jq &>/dev/null && [[ -f "$STATE_FILE_FOR_GF" ]]; then
      TMP=$(mktemp)
      if jq --arg gf "$GUIDANCE_FILE" \
            --arg bc "${BAKEOFF_CONVERGENCE_LINE:-}" \
            '. + {guidance_file: $gf} + (if $bc != "" then {bakeoff_convergence: $bc} else {} end)' \
            "$STATE_FILE_FOR_GF" > "$TMP" 2>/dev/null; then
        mv "$TMP" "$STATE_FILE_FOR_GF"
      else
        rm -f "$TMP"
      fi
    fi
  fi
fi

# (Bakeoff convergence computed above, before guidance file write)

# --- Cooldown & reflection: compute elapsed time, write guidance files ---
STATE_FILE="$HOME/hypergumbo_lab_notebook/last_stop_check.json"
# Backward compat: fall back to old locations if new one doesn't exist
if [[ ! -f "$STATE_FILE" ]]; then
  if [[ -f "$REPO_ROOT/.agent/last_stop_check.json" ]]; then
    STATE_FILE="$REPO_ROOT/.agent/last_stop_check.json"
  elif [[ -f "$REPO_ROOT/.agent/stop_hook_state.json" ]]; then
    STATE_FILE="$REPO_ROOT/.agent/stop_hook_state.json"
  fi
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
  if [[ -n "${STOP_HOOK_DRY_RUN:-}" ]]; then
    GUIDANCE_FILE_COOLDOWN="${_DRY_RUN_TMPDIR}/stop_guidance_cooldown_${TIMESTAMP}.md"
  else
    GUIDANCE_FILE_COOLDOWN="$GUIDANCE_LOG_DIR/stop_guidance_cooldown_${TIMESTAMP}.md"
  fi
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
  if [[ -n "${STOP_HOOK_DRY_RUN:-}" ]]; then
    GUIDANCE_FILE_REFLECTION="${_DRY_RUN_TMPDIR}/stop_guidance_reflect_${TIMESTAMP}.md"
  else
    GUIDANCE_FILE_REFLECTION="$GUIDANCE_LOG_DIR/stop_guidance_reflect_${TIMESTAMP}.md"
  fi
  {
    cat "$REPO_ROOT/.agent/stop_reflect.md"
    if [[ -n "$BAKEOFF_SUFFIX" ]]; then
      printf '%s' "$BAKEOFF_SUFFIX"
    fi
  } > "$GUIDANCE_FILE_REFLECTION"
fi

# --- Guidance file organization: move older files to subfolder ---
# Keep the 10 most recent guidance files in the main directory for quick
# access. Move everything else to older_guidance/ for archival. NEVER
# deletes guidance files — move-only policy.
if [[ -d "$GUIDANCE_LOG_DIR" && -z "${STOP_HOOK_DRY_RUN:-}" ]]; then
  OLDER_DIR="$GUIDANCE_LOG_DIR/older_guidance"
  # Count guidance files (stop_guidance_*.md pattern)
  GUIDANCE_COUNT=$(find "$GUIDANCE_LOG_DIR" -maxdepth 1 -name 'stop_guidance_*.md' -type f 2>/dev/null | wc -l)
  if [[ "$GUIDANCE_COUNT" -gt 10 ]]; then
    mkdir -p "$OLDER_DIR"
    # Move all but the 10 most recent (by modification time)
    find "$GUIDANCE_LOG_DIR" -maxdepth 1 -name 'stop_guidance_*.md' -type f -printf '%T@ %p\n' 2>/dev/null \
      | sort -rn | tail -n +"11" | cut -d' ' -f2- \
      | while IFS= read -r f; do
          mv "$f" "$OLDER_DIR/" 2>/dev/null || true
        done
  fi
fi
