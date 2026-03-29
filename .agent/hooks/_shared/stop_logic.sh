#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Shared stop hook logic — sourced by all vendor hooks.
# See ADR-0008 for governance protocol; ADR-0013 for structured tracker.
#
# Uses the structured tracker CLI (scripts/tracker) for TODO counting,
# circuit breaker hashing, and guidance generation. Fail-closed: if the
# tracker CLI is present but fails, the hook blocks (exit 1). Circuit
# breaker uses file-change hashing on sentinel directories (not tracker
# state). Bakeoff convergence is surfaced in guidance files when bakeoff
# artifacts are present.
#
# Expects REPO_ROOT to be set by the caller.
# Exports: TOTAL_HARD, TOTAL_SOFT, TOTAL_TODOS, CIRCUIT_BREAKER_TRIPPED,
#          SESSION_IS_AUTONOMOUS, GUIDANCE_FILE, GUIDANCE_FILE_COOLDOWN,
#          GUIDANCE_FILE_REFLECTION, STATE_FILE, ELAPSED_MIN, HASH_THRESHOLD
#
# Optional env vars for dry-run (set by scripts/stop-hook-preview):
#   STOP_HOOK_DRY_RUN       - When non-empty, writes go to a temp dir instead
#                             of permanent locations. No side effects on disk.
#   STOP_HOOK_BAKEOFF_FILTER - Glob pattern for bakeoff session dirs (e.g.
#                             "broad-*"). Default: "*" (all sessions).

# --- Setup ---
# Guidance log and state file live outside the repo to avoid polluting git.
# Derive a project-specific directory from the repo directory name.
_REPO_NAME="${REPO_ROOT##*/}"
GUIDANCE_LOG_DIR="$HOME/${_REPO_NAME}_lab_notebook/guidance_log"
mkdir -p "$GUIDANCE_LOG_DIR"
HASH_FILE="/tmp/${_REPO_NAME}_stop_hashes"
HASH_THRESHOLD=5

# --- PID-based parallel session detection ---
# When multiple agent sessions share a repo (e.g., one autonomous, one
# interactive), only the session whose ancestor PID matches the stored PID
# should be treated as autonomous.  Other sessions get SESSION_IS_AUTONOMOUS=false
# and the vendor hook should approve/allow the stop immediately.
#
# The PID is stored in AUTONOMOUS_MODE.txt as "MODE pid=12345".
# If no PID is stored, the first session to reach this code claims ownership.
#
# Expects _RAW_MODE to be set by the vendor hook (the raw first line of
# AUTONOMOUS_MODE.txt, before stripping the pid= suffix).  If _RAW_MODE is
# unset, we read it ourselves for backward compatibility.
SESSION_IS_AUTONOMOUS=true

if [[ -z "${_RAW_MODE:-}" ]]; then
  _RAW_MODE=$(head -1 "$REPO_ROOT/AUTONOMOUS_MODE.txt" 2>/dev/null || true)
fi

_STORED_PID=""
if [[ "$_RAW_MODE" =~ pid=([0-9]+) ]]; then
  _STORED_PID="${BASH_REMATCH[1]}"
fi

_is_pid_ancestor() {
  # Walk /proc ancestor chain from current process to check if target PID
  # is an ancestor. Returns 0 if found, 1 if not.
  local target=$1
  local pid=$$
  while [[ $pid -gt 1 ]]; do
    [[ "$pid" == "$target" ]] && return 0
    pid=$(awk '/^PPid:/ {print $2}' "/proc/$pid/status" 2>/dev/null) || return 1
  done
  return 1
}

if [[ -n "$_STORED_PID" ]]; then
  if _is_pid_ancestor "$_STORED_PID"; then
    SESSION_IS_AUTONOMOUS=true
  elif [[ -d "/proc/$_STORED_PID" ]]; then
    # Stored PID is alive but not our ancestor — we're a different session
    SESSION_IS_AUTONOMOUS=false
  else
    # Stored PID is dead — don't auto-reclaim. The autonomous agent must
    # be restarted via loop-toggle, which will set a fresh PID.
    SESSION_IS_AUTONOMOUS=false
  fi
else
  # No PID stored: claim ownership using $PPID (the agent process)
  SESSION_IS_AUTONOMOUS=true
  _MODE_CLEAN=$(echo "$_RAW_MODE" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
  echo "${_MODE_CLEAN} pid=$PPID" > "$REPO_ROOT/AUTONOMOUS_MODE.txt"
fi

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

  # Check prior history BEFORE appending current hash.  This avoids an
  # off-by-one (the current stop shouldn't count toward the threshold —
  # it should only trip after HASH_THRESHOLD *prior* identical stops) and
  # a TOCTOU race (two separate `tail` reads could see different data if
  # another hook fires between them).
  if [[ -f "$HASH_FILE" ]]; then
    LAST_N=$(tail -n "$HASH_THRESHOLD" "$HASH_FILE")
    TAIL_COUNT=$(printf '%s\n' "$LAST_N" | wc -l)
    UNIQUE_HASHES=$(printf '%s\n' "$LAST_N" | sort -u)
    UNIQUE_COUNT=$(printf '%s\n' "$UNIQUE_HASHES" | wc -l)
    if [[ "$TAIL_COUNT" -ge "$HASH_THRESHOLD" && "$UNIQUE_COUNT" -eq 1 && "$UNIQUE_HASHES" == "$CURRENT_HASH" ]]; then
      CIRCUIT_BREAKER_TRIPPED=true
    fi
  fi

  # Record current hash AFTER the check
  if [[ -z "${STOP_HOOK_DRY_RUN:-}" ]]; then
    echo "$CURRENT_HASH" >> "$HASH_FILE"
  fi
fi

# --- Bakeoff convergence summary (shared across all vendors) ---
# Computed early so it can be appended to guidance files.
BAKEOFF_SUFFIX=""
BAKEOFF_CONVERGENCE_LINE=""
BAKEOFF_DIR="$HOME/${_REPO_NAME}_lab_notebook/bakeoff_artifacts"
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
        BAKEOFF_SUFFIX=$'\n\n---\n## IS THE BAKEOFF STATUS CONVERGED? WHAT TO DO IF NOT\nBakeoff Status: '"$BAKEOFF_SUMMARY"$'\nLatest BROAD bakeoff session is CONVERGED — no critical/high issues.\nNext steps:\n  1. Run LLM assessment (if not done): ./scripts/bakeoff-broad-reflect\n  2. Aggregate findings: ./scripts/bakeoff-broad-reflect aggregate\n  3. Select a new cohort: ./scripts/bakeoff-broad cohort --count 5\n  4. Or move to other work items (tracker ready)\nDuring idle time (CI pending, bakeoff running): aggregate prior sessions.'
      else
        BAKEOFF_SUFFIX=$'\n\n---\n## IS THE BAKEOFF STATUS CONVERGED? WHAT TO DO IF NOT\nBakeoff Status: '"$BAKEOFF_SUMMARY"$'\nLatest DEEP bakeoff session is CONVERGED — all repos GOOD.\nNext steps:\n  1. Run LLM assessment (if not done): ./scripts/bakeoff-deep-reflect\n  2. Aggregate findings: ./scripts/bakeoff-deep-reflect aggregate\n  3. Compare with prior sessions: ./scripts/bakeoff-deep compare <A> <B>\n  4. Select a new cohort: ./scripts/bakeoff-deep cohort --count 4\n  5. Or move to other work items (tracker ready)\nDuring idle time (CI pending, bakeoff running): aggregate prior sessions.'
      fi
    elif [[ "$BAKEOFF_SUMMARY" == NEEDS_WORK* ]]; then
      BAKEOFF_CONVERGENCE_LINE="$BAKEOFF_SUMMARY"
      if [[ "$_SESSION_TYPE" == "broad" ]]; then
        BAKEOFF_SUFFIX=$'\n\n---\n## IS THE BAKEOFF STATUS CONVERGED? WHAT TO DO IF NOT\nBakeoff Status: '"$BAKEOFF_SUMMARY"$'\nLatest BROAD bakeoff session has outstanding issues.\nInvestigate:\n  1. View issues: ./scripts/bakeoff-broad issues --format json\n  2. Run LLM assessment for deeper analysis: ./scripts/bakeoff-broad-reflect\n  3. Diagnose latest: ./scripts/bakeoff-broad diagnose\n  4. Fix issues, then re-run: ./scripts/bakeoff-broad cycle\nDuring idle time: ./scripts/bakeoff-broad-reflect aggregate'
      else
        BAKEOFF_SUFFIX=$'\n\n---\n## IS THE BAKEOFF STATUS CONVERGED? WHAT TO DO IF NOT\nBakeoff Status: '"$BAKEOFF_SUMMARY"$'\nLatest DEEP bakeoff session has outstanding issues.\nInvestigate:\n  1. Check status: ./scripts/bakeoff-deep status\n  2. Run LLM assessment for deeper analysis: ./scripts/bakeoff-deep-reflect\n  3. Diagnose repos: ./scripts/bakeoff-deep diagnose\n  4. Fix issues, then re-run: ./scripts/bakeoff-deep cycle\nDuring idle time: ./scripts/bakeoff-deep-reflect aggregate'
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

  # Update last_stop_check.json with guidance_file pointer + bakeoff convergence.
  # Seeds the file from scratch if it doesn't exist yet (closes bootstrap gap).
  if [[ -n "$GUIDANCE_FILE" && -z "${STOP_HOOK_DRY_RUN:-}" ]]; then
    STATE_FILE_FOR_GF="$GUIDANCE_LOG_DIR/last_stop_check.json"
    if command -v jq &>/dev/null; then
      TMP=$(mktemp)
      _EXISTING="{}"
      if [[ -f "$STATE_FILE_FOR_GF" ]]; then
        _EXISTING=$(cat "$STATE_FILE_FOR_GF")
      fi
      if printf '%s' "$_EXISTING" | jq --arg gf "$GUIDANCE_FILE" \
            --arg bc "${BAKEOFF_CONVERGENCE_LINE:-}" \
            --arg bs "${_SESSION_DIR:-}" \
            --arg bt "${_SESSION_TYPE:-}" \
            '. + {guidance_file: $gf} + (if $bc != "" then {bakeoff_convergence: $bc} else {} end) + (if $bs != "" then {bakeoff_session_path: $bs, bakeoff_session_type: $bt} else {} end)' \
            > "$TMP" 2>/dev/null; then
        mv "$TMP" "$STATE_FILE_FOR_GF"
      else
        rm -f "$TMP"
      fi
    fi
  fi
fi

# (Bakeoff convergence computed above, before guidance file write)

# --- Cooldown & reflection: compute elapsed time, write guidance files ---
STATE_FILE="$GUIDANCE_LOG_DIR/last_stop_check.json"

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

# --- Stale-PR audit: surface open PRs older than 6 hours ---
# Queries Forgejo for open PRs. Any PR created more than 6 hours ago is
# flagged in the active guidance file. This catches PRs orphaned by context
# compaction, remote timeouts, or failed CI that the agent forgot about.
# Non-fatal: if the API call fails, we silently skip the audit.
STALE_PR_SECTION=""
if [[ -z "${STOP_HOOK_DRY_RUN:-}" ]]; then
  _stale_pr_audit() {
    # Source the Forgejo API library
    local api_lib="$REPO_ROOT/scripts/lib/forgejo-api.sh"
    [[ -f "$api_lib" ]] || return 0
    # shellcheck disable=SC1090
    source "$api_lib"
    load_env 2>/dev/null || return 0
    detect_api_base 2>/dev/null || return 0

    # Fetch open PRs (silently fail if no connectivity)
    if ! api_get "$API_BASE/pulls?state=open&sort=recentupdate&limit=50" 2>/dev/null; then
      return 0
    fi

    local now_epoch
    now_epoch=$(date +%s)
    local threshold=$((6 * 3600))  # 6 hours in seconds

    # Parse PRs: filter to those older than 6 hours
    local stale_prs
    stale_prs=$(python3 -c "
import json, sys, datetime
now = $now_epoch
threshold = $threshold
prs = json.loads(sys.stdin.read())
if not isinstance(prs, list):
    sys.exit(0)
for pr in prs:
    created = pr.get('created_at', '')
    if not created:
        continue
    # Parse ISO 8601 timestamp
    try:
        dt = datetime.datetime.fromisoformat(created.replace('Z', '+00:00'))
        age_s = now - int(dt.timestamp())
    except (ValueError, TypeError):
        continue
    if age_s > threshold:
        num = pr.get('number', '?')
        title = pr.get('title', '?')[:60]
        age_h = age_s // 3600
        branch = pr.get('head', {}).get('ref', '?')
        mergeable = pr.get('mergeable', None)
        ci_note = ''
        if mergeable is False:
            ci_note = ' [NOT MERGEABLE]'
        elif mergeable is True:
            ci_note = ' [mergeable]'
        print(f'- PR #{num} ({age_h}h old){ci_note}: {title}')
        print(f'  Branch: {branch}')
" <<< "$API_RESPONSE" 2>/dev/null) || return 0

    if [[ -n "$stale_prs" ]]; then
      STALE_PR_SECTION=$(printf '\n\n## STALE PULL REQUESTS\nThe following open PRs are older than 6 hours. Consider: merge (if CI green),\nrebase + re-push (if out of date), fix (if CI failed), or close (if superseded).\n\n%s\n' "$stale_prs")
    fi
  }
  _stale_pr_audit
fi

# Append stale-PR section to the active guidance file (whichever was generated)
if [[ -n "$STALE_PR_SECTION" ]]; then
  for _gf in "$GUIDANCE_FILE" "$GUIDANCE_FILE_COOLDOWN" "$GUIDANCE_FILE_REFLECTION"; do
    if [[ -n "$_gf" && -f "$_gf" ]]; then
      printf '%s' "$STALE_PR_SECTION" >> "$_gf"
    fi
  done
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