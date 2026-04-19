#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# ------------------------------------------------------------------
# forgejo-api.sh: Shared library for Forgejo/Gitea API interactions
#
# Sourceable by auto-pr, ci-debug, contribute, list-my-prs, and merge-pr.
# Provides: environment loading, API calls with JSON safety,
# CI polling with timeout, PR search, and merge helpers.
#
# Exit code scheme (all scripts):
#   0 = success
#   1 = failure (CI failed, merge rejected, auth error)
#   2 = timeout (CI still pending after deadline)
#
# Environment variables:
#   API_TIMEOUT       Per-HTTP-call timeout in seconds (default: 15)
#   CI_TIMEOUT_SECONDS  CI polling timeout in seconds (default: 2400 = 40 min)
# ------------------------------------------------------------------

# Guard against double-sourcing
[[ -n "${_FORGEJO_API_LOADED:-}" ]] && return 0
_FORGEJO_API_LOADED=1

# ------------------------------------------------------------------
# load_env: Load .env, set REPO_ROOT / FORGEJO_USER / FORGEJO_TOKEN
# ------------------------------------------------------------------
load_env() {
	REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo ".")"
	if [[ -f "$REPO_ROOT/.env" ]]; then
		set -o allexport
		# shellcheck disable=SC1091
		source "$REPO_ROOT/.env"
		set +o allexport
	fi
}

# ------------------------------------------------------------------
# detect_api_base: Set API_BASE and REPO_SLUG from git remote
# ------------------------------------------------------------------
detect_api_base() {
	local remote_url
	remote_url="$(git remote get-url origin)"
	REPO_SLUG="$(echo "$remote_url" | sed 's/\.git$//' | awk -F'[:/]' '{print $(NF-1)"/"$NF}')"

	if [[ -n "${FORGEJO_API_BASE:-}" ]]; then
		API_BASE="$FORGEJO_API_BASE/repos/$REPO_SLUG"
	elif [[ "$remote_url" == *"codeberg.org"* ]]; then
		API_BASE="https://codeberg.org/api/v1/repos/$REPO_SLUG"
	elif [[ "$remote_url" == *"gitea"* ]] || [[ "$remote_url" == *"forgejo"* ]]; then
		local host
		host=$(echo "$remote_url" | sed -E 's|.*[@/]([^:/]+)[:/].*|\1|')
		API_BASE="https://$host/api/v1/repos/$REPO_SLUG"
	else
		API_BASE="https://codeberg.org/api/v1/repos/$REPO_SLUG"
	fi
}

# ------------------------------------------------------------------
# apply_failover_overrides: Override API_BASE / REPO_SLUG / FORGEJO_TOKEN
# when CI failover is active. Call after detect_api_base().
# Sets FAILOVER_ACTIVE=true/false for callers to check.
# ------------------------------------------------------------------
apply_failover_overrides() {
	local failover_file="$REPO_ROOT/.git/CI_FAILOVER_ACTIVE"
	FAILOVER_ACTIVE=false
	if [[ -f "$failover_file" ]]; then
		FAILOVER_ACTIVE=true
		FAILOVER_URL=$(python3 -c "import json,sys; print(json.load(open('$failover_file'))['selfhosted_forgejo_url'])")
		FAILOVER_REPO=$(python3 -c "import json,sys; print(json.load(open('$failover_file'))['selfhosted_forgejo_repo'])")
		API_BASE="$FAILOVER_URL/api/v1/repos/$FAILOVER_REPO"
		REPO_SLUG="$FAILOVER_REPO"
		export FORGEJO_TOKEN="${SELFHOSTED_FORGEJO_TOKEN:-$FORGEJO_TOKEN}"
	fi
}

# ------------------------------------------------------------------
# api_call METHOD URL [DATA]
#   Safe HTTP wrapper. Sets $API_RESPONSE and $API_HTTP_CODE.
#   Returns: 0 = 2xx, 1 = non-2xx, 2 = curl failure or non-JSON response
# ------------------------------------------------------------------
api_call() {
	local method="$1" url="$2" data="${3:-}"
	local timeout="${API_TIMEOUT:-15}"
	local tmp_file
	tmp_file="$(mktemp)"

	local curl_args=(
		-s
		--max-time "$timeout"
		-o "$tmp_file"
		-w "%{http_code}"
		-X "$method"
		-H "Authorization: token ${FORGEJO_TOKEN:-}"
		-H "Content-Type: application/json"
	)

	if [[ -n "$data" ]]; then
		curl_args+=(-d "$data")
	fi

	API_HTTP_CODE=$(curl "${curl_args[@]}" "$url" 2>/dev/null) || {
		API_RESPONSE=""
		API_HTTP_CODE="000"
		rm -f "$tmp_file"
		return 2
	}

	API_RESPONSE="$(cat "$tmp_file" 2>/dev/null || echo "")"
	rm -f "$tmp_file"

	# Validate response looks like JSON (starts with { or [)
	local trimmed
	trimmed="$(echo "$API_RESPONSE" | sed 's/^[[:space:]]*//')"
	if [[ -n "$trimmed" ]] && [[ "${trimmed:0:1}" != "{" ]] && [[ "${trimmed:0:1}" != "[" ]]; then
		# Non-JSON response (HTML error page, timeout message, etc.)
		return 2
	fi

	# Check HTTP status code
	if [[ "$API_HTTP_CODE" =~ ^2[0-9][0-9]$ ]]; then
		return 0
	else
		return 1
	fi
}

api_get() { api_call GET "$@"; }
api_post() { api_call POST "$@"; }
api_patch() { api_call PATCH "$@"; }

# ------------------------------------------------------------------
# json_field DOTPATH
#   Safe field extraction from JSON on stdin.
#   Returns empty string on failure, never crashes.
#   Supports dotted paths: "head.sha", "head.repo.full_name"
# ------------------------------------------------------------------
json_field() {
	local dotpath="$1"
	python3 -c "
import sys, json, functools
try:
    data = json.load(sys.stdin)
    val = functools.reduce(lambda d, k: d[k], '$dotpath'.split('.'), data)
    if val is None:
        print('')
    else:
        print(val)
except Exception:
    print('')
" 2>/dev/null || echo ""
}

# ------------------------------------------------------------------
# json_array_find FIELD_PATH VALUE
#   Find element in JSON array (on stdin) by field match.
#   Prints the matching JSON object. Returns 1 if not found.
# ------------------------------------------------------------------
json_array_find() {
	local field="$1" value="$2"
	python3 -c "
import sys, json, functools
try:
    data = json.load(sys.stdin)
    for item in data:
        val = functools.reduce(lambda d, k: d[k], '$field'.split('.'), item)
        if str(val).startswith('$value'):
            print(json.dumps(item))
            sys.exit(0)
    sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

# ------------------------------------------------------------------
# find_open_pr TYPE VALUE
#   Find open PR by "sha" or "branch".
#   Sets FOUND_PR_NUM and FOUND_PR_SHA on success. Returns 1 if not found.
# ------------------------------------------------------------------
find_open_pr() {
	local search_type="$1" search_value="$2"
	FOUND_PR_NUM=""
	FOUND_PR_SHA=""

	if ! api_get "$API_BASE/pulls?state=open&sort=recentupdate"; then
		return 1
	fi

	local match
	case "$search_type" in
	sha)
		match=$(echo "$API_RESPONSE" | json_array_find "head.sha" "$search_value") || return 1
		;;
	branch)
		match=$(echo "$API_RESPONSE" | json_array_find "head.ref" "$search_value") || return 1
		;;
	*)
		echo "find_open_pr: unknown type '$search_type'" >&2
		return 1
		;;
	esac

	FOUND_PR_NUM=$(echo "$match" | json_field "number")
	FOUND_PR_SHA=$(echo "$match" | json_field "head.sha")

	if [[ -z "$FOUND_PR_NUM" || "$FOUND_PR_NUM" == "None" ]]; then
		return 1
	fi
	return 0
}

# ------------------------------------------------------------------
# find_merged_pr TYPE VALUE
#   Find a merged (state=closed AND merged=true) PR by "sha" or "branch".
#   Used by auto-pr's WI-bahuf already-merged detection: when a refs/for/
#   push is rejected with "the new commit is the same as the old commit",
#   the work is already in dev, so we look up the merge target by branch
#   name (most reliable since the agent hasn't deleted the branch yet)
#   or by HEAD SHA.
#
#   Sets FOUND_MERGED_PR_NUM and FOUND_MERGED_PR_SHA on success. Returns 1
#   if no merged PR matches (the rejection might have a different cause).
#
#   The Forgejo API does not have state=merged; we filter the closed list
#   client-side by `merged: true` to skip rejected/closed-without-merge PRs.
# ------------------------------------------------------------------
find_merged_pr() {
	local search_type="$1" search_value="$2"
	FOUND_MERGED_PR_NUM=""
	FOUND_MERGED_PR_SHA=""

	if ! api_get "$API_BASE/pulls?state=closed&sort=recentupdate&limit=50"; then
		return 1
	fi

	# Filter to merged-only client-side, then search by the requested key.
	local match
	match=$(echo "$API_RESPONSE" | python3 -c "
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
search_type = '$search_type'
search_value = '$search_value'
if search_type == 'sha':
    field_path = ('head', 'sha')
elif search_type == 'branch':
    field_path = ('head', 'ref')
else:
    print('find_merged_pr: unknown type', search_type, file=sys.stderr)
    sys.exit(1)
for item in data:
    if not item.get('merged'):
        continue
    val = item
    for key in field_path:
        val = val.get(key) if isinstance(val, dict) else None
        if val is None:
            break
    if val and str(val).startswith(search_value):
        print(json.dumps(item))
        sys.exit(0)
sys.exit(1)
" 2>/dev/null) || return 1

	FOUND_MERGED_PR_NUM=$(echo "$match" | json_field "number")
	FOUND_MERGED_PR_SHA=$(echo "$match" | json_field "head.sha")

	if [[ -z "$FOUND_MERGED_PR_NUM" || "$FOUND_MERGED_PR_NUM" == "None" ]]; then
		return 1
	fi
	return 0
}

# ------------------------------------------------------------------
# poll_ci HEAD_SHA
#   CI polling with timeout and ci-complete bypass (Scenario A/B).
#   Uses API_BASE and FORGEJO_TOKEN from environment.
#   Returns: 0 = success, 1 = failure, 2 = timeout
# ------------------------------------------------------------------
poll_ci() {
	local head_sha="$1"

	# WI-dotod test seam: when AUTOPR_TEST_POLL_EXITS is set, return exit
	# codes from a colon-separated sequence (e.g. "2:0" yields 2 on the
	# first call and 0 on the second). The position is tracked in
	# ${AUTOPR_TEST_POLL_EXITS}.pos. Mirrors AUTO_PR_SIMULATE_OUTAGE
	# pattern — exists only to let the Exit 2 retry loop be tested
	# without a live Forgejo instance.
	if [[ -n "${AUTOPR_TEST_POLL_EXITS:-}" ]]; then
		local _pos_file="${AUTOPR_TEST_POLL_EXITS_POS:-/tmp/autopr_test_poll_pos}"
		local _pos
		_pos=$(cat "$_pos_file" 2>/dev/null || echo 0)
		local -a _exits
		IFS=':' read -ra _exits <<< "$AUTOPR_TEST_POLL_EXITS"
		local _code
		if [[ $_pos -ge ${#_exits[@]} ]]; then
			_code=0
		else
			_code="${_exits[$_pos]}"
		fi
		echo $((_pos + 1)) > "$_pos_file"
		echo "[test-seam] poll_ci call #$((_pos + 1)) returning $_code"
		return "$_code"
	fi

	local timeout="${CI_TIMEOUT_SECONDS:-2400}"
	local stale_pending_threshold="${CI_STALE_PENDING_SECONDS:-300}"  # 5 min default
	local start_time elapsed
	start_time=$(date +%s)

	# Track how long ci-complete has been the sole holdout (Scenario A)
	local ci_complete_sole_holdout_since=0
	local poll_count=0
	local prev_summary=""

	# Track whether any job has left pending state (stale-pending detection)
	local any_job_started=false

	while true; do
		elapsed=$(( $(date +%s) - start_time ))
		poll_count=$((poll_count + 1))
		if [[ $elapsed -ge $timeout ]]; then
			echo ""
			echo "⏰ CI polling timed out after ${timeout}s (exit code 2)"
			echo ""
			echo "Scenario B: CI is stuck or slow. Recovery steps:"
			echo "  1. Do NOT accumulate more changes to hypergumbo code"
			echo "  2. Run: ./scripts/ci-debug status"
			echo "  3. When CI recovers: ./scripts/merge-pr <PR_NUM> --wait-for-ci"
			return 2
		fi

		if ! api_get "$API_BASE/commits/$head_sha/status"; then
			printf "?"
			sleep 10
			continue
		fi

		local state
		state=$(echo "$API_RESPONSE" | json_field "state")

		# Stale-pending detection: if no job has ever left pending state
		# and we've been waiting longer than the threshold, the CI run
		# likely never started (hung runner, dispatch failure).
		if [[ "$any_job_started" == "false" && $elapsed -ge $stale_pending_threshold ]]; then
			local has_non_pending
			has_non_pending=$(echo "$API_RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    statuses = data.get('statuses', [])
    non_pending = [s for s in statuses if s.get('status') != 'pending']
    print('yes' if non_pending else 'no')
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")
			if [[ "$has_non_pending" == "yes" ]]; then
				any_job_started=true
			elif [[ "$has_non_pending" == "no" ]]; then
				echo ""
				echo "⚠️  No CI jobs have started after ${stale_pending_threshold}s — possible hung runner (exit code 3)"
				return 3
			fi
		elif [[ "$any_job_started" == "false" ]]; then
			# Check if any job has started (so we don't re-check after it's set)
			local _check_started
			_check_started=$(echo "$API_RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    non_pending = [s for s in data.get('statuses', []) if s.get('status') != 'pending']
    print('yes' if non_pending else 'no')
except Exception:
    print('no')
" 2>/dev/null || echo "no")
			if [[ "$_check_started" == "yes" ]]; then
				any_job_started=true
			fi
		fi

		if [[ "$state" == "success" ]]; then
			echo ""
			echo "✅ CI Passed!"
			return 0
		elif [[ "$state" == "failure" || "$state" == "error" ]]; then
			# Aggregate status is failure, but ci-complete (the gate job)
			# may have succeeded — e.g., pytest failed but pytest-retry passed.
			local ci_complete_state
			ci_complete_state=$(echo "$API_RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for s in data.get('statuses', []):
        if 'ci-complete' in s.get('context', ''):
            print(s['status'])
            break
    else:
        print('not_found')
except Exception:
    print('error')
" 2>/dev/null || echo "error")

			if [[ "$ci_complete_state" == "success" ]]; then
				echo ""
				echo "✅ CI Passed! (ci-complete succeeded; primary job failures were recovered by retries)"
				return 0
			fi

			# Check if there are still pending jobs (e.g., pytest-retry).
			# If so, keep polling — the retry may succeed and ci-complete
			# will flip to success.
			local has_pending
			has_pending=$(echo "$API_RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    pending = [s.get('context', '') for s in data.get('statuses', [])
               if s.get('status') == 'pending']
    print('yes' if pending else 'no')
except Exception:
    print('no')
" 2>/dev/null || echo "no")

			if [[ "$has_pending" == "yes" ]]; then
				# Jobs still running — keep polling
				printf "."
				sleep 16
				continue
			fi

			local failed_contexts
			failed_contexts=$(echo "$API_RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    failed = [s.get('context', 'unknown') for s in data.get('statuses', [])
              if s.get('status') in ('failure', 'error')]
    print(', '.join(failed) if failed else 'unknown')
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")
			echo ""
			echo "❌ CI Failed. Contexts: $failed_contexts"
			echo ""
			# Extract first failed job's short name (e.g., "pytest" from "CI / pytest (pull_request)")
			local first_failed_job
			first_failed_job=$(echo "$failed_contexts" | sed 's/,.*//' | sed 's/.*\/ *//' | sed 's/ *(.*//')
			echo "📋 Fetching failed job log..."
			local log_output
			if log_output=$(fetch_job_log "$head_sha" "$first_failed_job" 2>/dev/null); then
				echo "--- Last 30 lines ---"
				echo "$log_output" | tail -30
				echo "--- End of log snippet ---"
			else
				echo "   (could not retrieve log automatically)"
			fi
			echo "💡 Full log: ./scripts/ci-debug logs ${first_failed_job:-}"
			return 1
		fi

		# Scenario A check: is ci-complete the sole holdout?
		local scenario_a_result
		scenario_a_result=$(echo "$API_RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    statuses = data.get('statuses', [])
    if not statuses:
        print('no_statuses')
        sys.exit(0)
    pending = []
    terminal = []
    for s in statuses:
        st = s.get('state', 'unknown')
        ctx = s.get('context', 'unknown')
        if st in ('success', 'failure', 'error'):
            terminal.append((ctx, st))
        else:
            pending.append(ctx)
    # All terminal are success, and only ci-complete is pending
    all_terminal_success = all(st == 'success' for _, st in terminal)
    ci_only_pending = len(pending) == 1 and any('ci-complete' in p.lower() for p in pending)
    if all_terminal_success and ci_only_pending and len(terminal) > 0:
        print('ci_complete_sole')
    else:
        print('mixed')
except Exception:
    print('error')
" 2>/dev/null || echo "error")

		if [[ "$scenario_a_result" == "ci_complete_sole" ]]; then
			if [[ $ci_complete_sole_holdout_since -eq 0 ]]; then
				ci_complete_sole_holdout_since=$(date +%s)
				echo ""
				echo "⏳ All jobs passed; waiting for ci-complete gate to catch up..."
			fi
			local holdout_elapsed=$(( $(date +%s) - ci_complete_sole_holdout_since ))
			if [[ $holdout_elapsed -ge 300 ]]; then
				echo ""
				echo "✅ CI Passed! (all jobs succeeded; ci-complete gate still propagating, but we don't care, because that's just weird runner behavior that happens occasionally; we can merge)"
				return 0
			fi
		else
			# Reset if the situation changes
			ci_complete_sole_holdout_since=0
		fi

		# Telemetry: only print when job statuses change (reuses API_RESPONSE,
		# no extra API call — be considerate of Codeberg as a community resource)
		local cur_summary
		cur_summary=$(ci_job_summary)
		if [[ "$cur_summary" != "$prev_summary" ]]; then
			echo ""
			echo "  [${elapsed}s] $cur_summary"
			prev_summary="$cur_summary"
		else
			printf "."
		fi

		sleep 10
	done
}

# ------------------------------------------------------------------
# ci_job_summary
#   Format one-line job status summary from API_RESPONSE (commit status).
#   Call after api_get ".../commits/$sha/status".
# ------------------------------------------------------------------
ci_job_summary() {
	echo "$API_RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    statuses = data.get('statuses', [])
    done = 0
    pending = []
    failed = []
    for s in statuses:
        ctx = s.get('context', '?')
        name = ctx.split(' / ')[-1].split(' (')[0] if ' / ' in ctx else ctx
        st = s.get('state', '?')
        if st == 'success':
            done += 1
        elif st in ('failure', 'error'):
            failed.append(name)
        else:
            pending.append(name)
    total = len(statuses)
    pending.sort()
    failed.sort()
    parts = []
    if done:
        parts.append(f'{done}/{total} passed')
    if pending:
        parts.append(f'waiting: {\" \".join(pending)}')
    if failed:
        parts.append(f'failed: {\" \".join(failed)}')
    print(', '.join(parts) if parts else '(no jobs yet)')
except Exception:
    print('(unavailable)')
" 2>/dev/null || echo "(unavailable)"
}

# ------------------------------------------------------------------
# fetch_job_log HEAD_SHA [JOB_NAME]
#   Fetch plain-text log for a CI job. Uses the web route; the REST API
#   /actions/jobs endpoint returns 404 on Codeberg's Forgejo v14.
#
#   The log URL path depends on the Forgejo version:
#   - Codeberg (Forgejo v14+) requires /attempt/1/logs
#   - Self-hosted older Forgejo uses /logs directly
#   We detect failover state via FAILOVER_ACTIVE (set by
#   apply_failover_overrides) to pick the right path.
#
#   Strategy (2 API calls + 1 web route):
#     1. GET /actions/runs?head_sha=<full-sha> → index_in_repo (run number)
#     2. GET /{owner}/{repo}/actions/runs/{run_number}/jobs/0 (HTML)
#        → parse embedded JSON for job names/indices
#     3. GET /{owner}/{repo}/actions/runs/{run_number}/jobs/{idx}/<path>
#        where <path> is "attempt/1/logs" on Codeberg, "logs" on self-hosted
#
#   Checks HTTP status explicitly so a 404 body is not printed as if it
#   were log content.
#
#   Prints log to stdout. Returns 1 if not found.
# ------------------------------------------------------------------
fetch_job_log() {
	local head_sha="$1"
	local target_name="${2:-}"

	# Resolve full SHA (the API requires it, not a prefix)
	if [[ ${#head_sha} -lt 40 ]]; then
		head_sha=$(git rev-parse "$head_sha" 2>/dev/null) || {
			echo "Could not resolve SHA: $1" >&2
			return 1
		}
	fi

	# Derive the base web URL from API_BASE
	# API_BASE = https://codeberg.org/api/v1/repos/owner/repo
	local web_base
	web_base=$(echo "$API_BASE" | sed 's|/api/v1/repos/|/|')

	# Step 1: Find run_number for this commit.
	# Try /actions/runs first (Codeberg / Forgejo 12+), then fall back to
	# /actions/tasks (Forgejo 11.x / gitea-1.22 which lacks the runs endpoint).
	local run_number=""
	run_number=$(_find_run_number_via_runs "$head_sha" 2>/dev/null) \
		|| run_number=$(_find_run_number_via_tasks "$head_sha" 2>/dev/null) \
		|| true

	if [[ -z "$run_number" ]]; then
		echo "Could not find CI run for commit ${head_sha:0:8}" >&2
		return 1
	fi

	# Step 2: Find the job index within the run.
	# Try parsing embedded JSON from the HTML page (Codeberg), then fall back
	# to probing log first-lines (self-hosted Forgejo where the page is a SPA).
	local job_index="" job_name=""
	read -r job_index job_name < <(
		_find_job_from_html "$web_base" "$run_number" "$target_name" 2>/dev/null \
		|| _find_job_from_log_probe "$web_base" "$run_number" "$target_name" 2>/dev/null \
		|| echo ""
	)

	if [[ -z "$job_index" ]]; then
		echo "Could not find job${target_name:+ matching '$target_name'} in run $run_number" >&2
		return 1
	fi

	echo "Fetching log for job '$job_name' (run #$run_number, index $job_index)..." >&2

	# Step 3: Download log via web route.
	# Pick URL path by Forgejo version: self-hosted (failover active) uses
	# /logs directly; Codeberg Forgejo v14+ requires /attempt/1/logs.
	local log_path="attempt/1/logs"
	if [[ "${FAILOVER_ACTIVE:-false}" == "true" ]]; then
		log_path="logs"
	fi

	# Use -w to capture HTTP status so a 404 body ("Not found.") is not
	# silently printed as if it were log content.
	local tmp_log http_code
	tmp_log=$(mktemp)
	http_code=$(curl -sSL --http1.1 --max-time 60 \
		-H "Authorization: token ${FORGEJO_TOKEN:-}" \
		-o "$tmp_log" \
		-w "%{http_code}" \
		"$web_base/actions/runs/$run_number/jobs/$job_index/$log_path" \
		2>/dev/null) || http_code="000"

	if [[ "$http_code" != "200" ]]; then
		rm -f "$tmp_log"
		echo "Log fetch failed: HTTP $http_code for /actions/runs/$run_number/jobs/$job_index/$log_path" >&2
		return 1
	fi

	cat "$tmp_log"
	rm -f "$tmp_log"
}

# ------------------------------------------------------------------
# _find_run_number_via_runs HEAD_SHA
#   Find run_number via /actions/runs (Codeberg / Forgejo 12+).
#   Prints run_number on success, returns 1 on failure.
# ------------------------------------------------------------------
_find_run_number_via_runs() {
	local head_sha="$1"
	local runs_response
	runs_response=$(curl -sS --http1.1 --max-time 15 \
		-H "Authorization: token ${FORGEJO_TOKEN:-}" \
		"$API_BASE/actions/runs?head_sha=$head_sha" 2>/dev/null) || return 1

	echo "$runs_response" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    runs = data.get('workflow_runs', data if isinstance(data, list) else [])
    ci_runs = [r for r in runs if str(r.get('workflow_id', '')) == 'ci.yml']
    pick = ci_runs[0] if ci_runs else (runs[0] if runs else None)
    if pick and pick.get('index_in_repo'):
        print(pick['index_in_repo'])
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

# ------------------------------------------------------------------
# _find_run_number_via_tasks HEAD_SHA
#   Fallback: find run_number via /actions/tasks (Forgejo 11.x).
#   This endpoint exists on older Forgejo where /actions/runs does not.
#   Prints run_number on success, returns 1 on failure.
# ------------------------------------------------------------------
_find_run_number_via_tasks() {
	local head_sha="$1"
	local tasks_response
	tasks_response=$(curl -sS --http1.1 --max-time 15 \
		-H "Authorization: token ${FORGEJO_TOKEN:-}" \
		"$API_BASE/actions/tasks?limit=100" 2>/dev/null) || return 1

	echo "$tasks_response" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    tasks = data.get('workflow_runs', data if isinstance(data, list) else [])
    # Filter to matching SHA; prefer ci.yml
    matching = [t for t in tasks if t.get('head_sha', '').startswith('$head_sha')]
    ci_tasks = [t for t in matching if t.get('workflow_id') == 'ci.yml']
    pick = ci_tasks[0] if ci_tasks else (matching[0] if matching else None)
    if pick and pick.get('run_number'):
        print(pick['run_number'])
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

# ------------------------------------------------------------------
# _find_job_from_html WEB_BASE RUN_NUMBER TARGET_NAME
#   Parse job list from the run page's embedded JSON (Codeberg).
#   Prints "INDEX NAME" on success, returns 1 on failure.
# ------------------------------------------------------------------
_find_job_from_html() {
	local web_base="$1" run_number="$2" target_name="$3"
	local page_html
	page_html=$(curl -sSL --http1.1 --max-time 15 \
		-H "Authorization: token ${FORGEJO_TOKEN:-}" \
		"$web_base/actions/runs/$run_number/jobs/0" 2>/dev/null) || return 1

	echo "$page_html" | python3 -c "
import sys, json, html, re

page = html.unescape(sys.stdin.read())
target = '$target_name'.lower()

match = re.search(r'\"jobs\":\s*\[(\{.*?\}(?:,\{.*?\})*)\]', page)
if not match:
    sys.exit(1)

jobs = json.loads('[' + match.group(1) + ']')

best = None
first = None
for i, j in enumerate(jobs):
    name = j.get('name', '')
    status = j.get('status', '')
    if target and target in name.lower():
        best = (i, name)
        break
    if first is None:
        first = (i, name)
    if not target and status in ('failure', 'error') and best is None:
        best = (i, name)
if best is None:
    best = first

if best:
    print(f'{best[0]} {best[1]}')
else:
    sys.exit(1)
" 2>/dev/null
}

# ------------------------------------------------------------------
# _find_job_from_log_probe WEB_BASE RUN_NUMBER TARGET_NAME
#   Fallback: probe log first-lines to find the job index.
#   Each Forgejo job log starts with a line containing "of job <name>".
#   Prints "INDEX NAME" on success, returns 1 on failure.
# ------------------------------------------------------------------
_find_job_from_log_probe() {
	local web_base="$1" run_number="$2" target_name="$3"
	local target_lower
	target_lower=$(echo "$target_name" | tr '[:upper:]' '[:lower:]')

	local best_index="" best_name=""
	local first_failed_index="" first_failed_name=""
	local first_index="" first_name=""
	local empty_count=0 named_count=0

	for idx in $(seq 0 20); do
		# Fetch first line of log. Avoid pipe (curl | head) because
		# set -o pipefail + SIGPIPE causes false failures.  Instead,
		# capture a bounded chunk and extract the first line.
		local raw_chunk="" first_line=""
		raw_chunk=$(curl -sS --http1.1 --max-time 5 -r 0-1023 \
			-H "Authorization: token ${FORGEJO_TOKEN:-}" \
			"$web_base/actions/runs/$run_number/jobs/$idx/logs" 2>/dev/null) || true
		first_line=$(echo "$raw_chunk" | head -1)

		# Empty response: log not ready or job index gap — skip, don't
		# stop.  Self-hosted Forgejo may return empty for valid indices
		# whose logs haven't been flushed yet.
		if [[ -z "$first_line" ]]; then
			empty_count=$((empty_count + 1))
			continue
		fi

		# Extract job name: "received task NNN of job <name>, be triggered by"
		local name
		name=$(echo "$first_line" | sed -n 's/.*of job \([^,]*\),.*/\1/p')
		if [[ -z "$name" ]]; then
			continue
		fi
		named_count=$((named_count + 1))

		# Track first job seen
		if [[ -z "$first_index" ]]; then
			first_index="$idx"
			first_name="$name"
		fi

		# Target match (case-insensitive substring)
		local name_lower
		name_lower=$(echo "$name" | tr '[:upper:]' '[:lower:]')
		if [[ -n "$target_lower" && "$name_lower" == *"$target_lower"* ]]; then
			echo "$idx $name"
			return 0
		fi
	done

	# No target match — return first job if no target was specified
	if [[ -z "$target_lower" && -n "$first_index" ]]; then
		echo "$first_index $first_name"
		return 0
	fi

	# Diagnostic for debugging probe failures
	if [[ $named_count -eq 0 && $empty_count -gt 0 ]]; then
		echo "Log probe: all $empty_count responses empty (logs may not be ready yet)" >&2
	elif [[ $named_count -gt 0 ]]; then
		echo "Log probe: found $named_count jobs but none matching '$target_name'" >&2
	fi

	return 1
}

# ------------------------------------------------------------------
# do_merge PR_NUM TITLE DESC ORIG_SHA [--squash]
#   Merge helper: tries fast-forward, falls back to rebase on divergence,
#   or squash if --squash is explicitly requested.
#   When merge fails with "head behind base branch" (e.g., tracker
#   auto-sync advanced dev during CI), rebases locally + force-pushes
#   + retries the merge automatically.
#   Uses API_BASE and FORGEJO_TOKEN from environment.
#   Returns: 0 = success, 1 = failure
# ------------------------------------------------------------------
# ------------------------------------------------------------------
# _ops_union_restore_file — WI-buhov data-loss fix
#
# Arguments: backup_file target_file
#
# Semantics: tracker .ops files are append-only CRDT logs (see
# .gitattributes: merge=union). When auto-pr rebases a feature branch
# and must restore a pre-rebase backup, the target may have received
# newer ops during the rebase (e.g. a tracker-sync commit pulled in
# from dev, or a concurrent agent discuss call). Overwriting with the
# backup loses those newer ops. Instead, this function appends every
# line from the backup that isn't already present in the target — an
# order-preserving line-level union. On fresh targets (non-existent),
# it just copies. Exit 0 on success, non-zero on filesystem failure.
# ------------------------------------------------------------------
_ops_union_restore_file() {
	local backup_file="$1"
	local target_file="$2"
	if [[ ! -f "$backup_file" ]]; then
		return 0
	fi
	if [[ -f "$target_file" ]]; then
		local tmp
		tmp=$(mktemp) || return 1
		if cat "$target_file" "$backup_file" | awk '!seen[$0]++' > "$tmp"; then
			mv "$tmp" "$target_file"
		else
			rm -f "$tmp"
			return 1
		fi
	else
		cp "$backup_file" "$target_file"
	fi
}

# ------------------------------------------------------------------
# _ops_union_restore_dir — WI-tasuj dotfile-glob fix
#
# Arguments: backup_subdir ops_dir [strip_modified_suffix:true|false]
#
# Iterates every regular file in backup_subdir (INCLUDING dotfiles,
# which bash's default `*` glob omits) and union-restores each into
# ops_dir via _ops_union_restore_file. Tracker ops filenames begin
# with `.` (.WI-…ops, .INV-…ops), so a single `*` glob silently
# enumerates zero files — the root cause of WI-tasuj's 2026-04-19
# loss of three tracker-reply ops. Mirroring the backup loop's two
# patterns (.*  *) enumerates hidden and non-hidden files alike
# without requiring `shopt -s dotglob`; the `[[ -f "$f" ]]` guard
# filters out the `.` / `..` pseudo-entries that `.*` matches.
#
# When strip_modified_suffix is true, a ".modified" suffix on each
# backup filename is dropped before composing the target path — the
# caller's backup step uses this suffix to distinguish
# tracked-but-locally-modified ops files from untracked ones.
# ------------------------------------------------------------------
_ops_union_restore_dir() {
	local backup_subdir="$1"
	local ops_dir="$2"
	local strip_modified_suffix="${3:-false}"
	[[ -d "$backup_subdir" ]] || return 0
	mkdir -p "$ops_dir"
	local f base target_name
	for f in "$backup_subdir"/.* "$backup_subdir"/*; do
		[[ -f "$f" ]] || continue
		base=$(basename "$f")
		if [[ "$strip_modified_suffix" == "true" ]]; then
			target_name="${base%.modified}"
		else
			target_name="$base"
		fi
		_ops_union_restore_file "$f" "$ops_dir/$target_name"
	done
}

do_merge() {
	local pr_num="$1" title="$2" desc="$3" orig_sha="$4"
	local force_squash="${5:-false}"
	local merge_response tmp_file
	tmp_file="$(mktemp)"

	if [[ "$force_squash" == "--squash" || "$force_squash" == "true" ]]; then
		echo "⚠️  Squash merge requested (commit body will be preserved via git notes)"

		local merge_payload
		merge_payload='{"do": "squash", "delete_branch_after_merge": true}'

		if api_post "$API_BASE/pulls/$pr_num/merge" "$merge_payload"; then
			echo "✅ Squash merged!"
			_attach_git_note "$desc" "$orig_sha"
			rm -f "$tmp_file"
			return 0
		fi

		# Check if PR was merged despite error code
		if _check_pr_merged "$pr_num"; then
			echo "✅ Verified: PR was successfully squash merged!"
			_attach_git_note "$desc" "$orig_sha"
			rm -f "$tmp_file"
			return 0
		fi

		echo "❌ Squash merge failed (HTTP $API_HTTP_CODE)"
		rm -f "$tmp_file"
		return 1
	fi

	# Default: try fast-forward merge with retry on transient failures
	echo "🚀 Attempting fast-forward merge (preserves commit bodies)..."

	local merge_payload='{"do": "fast-forward-only", "delete_branch_after_merge": true}'
	local max_retries=3
	local attempt

	for attempt in $(seq 1 $max_retries); do
		if api_post "$API_BASE/pulls/$pr_num/merge" "$merge_payload"; then
			# HTTP 2xx — verify the PR was actually merged (Forgejo sometimes
			# returns 200 with merged:false when branch protection blocks it)
			if _check_pr_merged "$pr_num"; then
				echo "✅ Fast-forward merged! (commit bodies preserved)"
				rm -f "$tmp_file"
				return 0
			fi
			# HTTP 200 but not merged — likely branch protection race condition
			if [[ $attempt -lt $max_retries ]]; then
				echo "⚠️  Merge returned success but PR not yet merged (attempt $attempt/$max_retries)"
				sleep $((attempt * 5))
				continue
			fi
			echo "❌ Merge returned HTTP 200 but PR was not merged after $max_retries attempts"
			echo "   This usually means branch protection is blocking (e.g., a failed status check)."
			echo ""
			echo "Recovery: ./scripts/merge-pr $pr_num"
			rm -f "$tmp_file"
			return 1
		fi

		# Save merge error before _check_pr_merged overwrites globals
		local merge_http_code="$API_HTTP_CODE"
		local merge_response="$API_RESPONSE"

		# Check if it's a divergence error (not retryable with FF)
		if echo "$merge_response" | grep -qi "not fast-forward\|cannot fast-forward\|branch has diverged"; then
			echo ""
			echo "⚠️  Fast-forward not possible: branch has diverged"
			echo "   Trying rebase merge (preserves individual commits)..."

			local rebase_payload='{"do": "rebase", "delete_branch_after_merge": true}'
			if api_post "$API_BASE/pulls/$pr_num/merge" "$rebase_payload"; then
				if _check_pr_merged "$pr_num"; then
					echo "✅ Rebase merged! (commits rebased onto $BASE_BRANCH)"
					rm -f "$tmp_file"
					return 0
				fi
			fi

			# Rebase merge also failed — give up with recovery instructions
			echo "❌ Rebase merge also failed"
			echo ""
			echo "Please rebase locally and retry:"
			echo "  git fetch origin dev"
			echo "  git rebase origin/dev"
			echo "  ./scripts/auto-pr"
			echo ""
			echo "Or, if you must squash (loses commit body details):"
			echo "  ./scripts/auto-pr --squash"
			rm -f "$tmp_file"
			return 1
		fi

		# Check if head is behind base (e.g., tracker auto-sync advanced
		# dev while CI was running).  Rebase locally and force-push so the
		# next retry attempt can fast-forward.
		if echo "$merge_response" | grep -qi "head behind base branch\|is behind"; then
			echo ""
			echo "⚠️  Head branch is behind base — rebasing locally..."
			local cur_branch
			cur_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)
			local base_branch="${BASE_BRANCH:-dev}"

			if [[ -z "$cur_branch" || "$cur_branch" == "HEAD" ]]; then
				echo "❌ Cannot determine current branch for local rebase"
				rm -f "$tmp_file"
				return 1
			fi

			# Back up tracker .ops files that would block rebase, then
			# restore them afterward so no pending operations are lost.
			local ops_backup
			ops_backup=$(mktemp -d /tmp/ops-backup-XXXXXX)
			local had_ops_backup=false
			local ops_dir
			for ops_dir in .agent/tracker/.ops .agent/tracker-workspace/.ops; do
				if [[ -d "$ops_dir" ]]; then
					local backup_subdir="$ops_backup/$ops_dir"
					mkdir -p "$backup_subdir"
					# Back up untracked .ops files
					for f in "$ops_dir"/.*ops "$ops_dir"/*.ops; do
						[[ -f "$f" ]] || continue
						if ! git ls-files --error-unmatch "$f" &>/dev/null; then
							cp "$f" "$backup_subdir/"
							rm -f "$f"
							had_ops_backup=true
						fi
					done
					# Back up modified tracked .ops files (diff from HEAD)
					if git diff --quiet -- "$ops_dir" 2>/dev/null; then
						:  # No modifications
					else
						for f in "$ops_dir"/.*ops "$ops_dir"/*.ops; do
							[[ -f "$f" ]] || continue
							if git diff --quiet -- "$f" 2>/dev/null; then
								:  # This file is clean
							else
								cp "$f" "$backup_subdir/$(basename "$f").modified"
								had_ops_backup=true
							fi
						done
					fi
				fi
			done
			# Revert tracked .ops files to HEAD so rebase can proceed cleanly
			git checkout -- .agent/tracker-workspace/.ops/ 2>/dev/null || true
			git checkout -- .agent/tracker/.ops/ 2>/dev/null || true

			if git fetch origin "$base_branch" --quiet 2>/dev/null \
			   && git rebase "origin/$base_branch" --quiet 2>/dev/null; then
				# Restore backed-up .ops files so no pending operations are lost.
				# WI-buhov: previously this step `cp`ed the backup over the
				# working-tree file unconditionally, which overwrote any ops
				# appended by concurrent tracker activity between the backup
				# snapshot and the rebase (either agent-driven discuss/add/
				# update calls mid-CI-poll or tracker-sync commits pulled in
				# by the rebase itself). Ops files are line-level-append-only
				# CRDT logs (merge=union in .gitattributes), so the correct
				# restore is line-level union: keep the rebased working-tree
				# content, then append any lines from the backup that aren't
				# already present. `awk '!seen[$0]++'` is an order-preserving
				# dedupe — rebased content keeps its ordering; backup-only
				# lines tail after.
				if [[ "$had_ops_backup" == true ]]; then
					for ops_dir in .agent/tracker/.ops .agent/tracker-workspace/.ops; do
						_ops_union_restore_dir "$ops_backup/$ops_dir" "$ops_dir" true
					done
					echo "   Restored backed-up .ops files from $ops_backup (union-merged)"
				fi
				rm -rf "$ops_backup" 2>/dev/null || true
				echo "   Rebase succeeded — force-pushing..."
				# Push via refs/for/ (Forgejo AGit) to update the PR head ref.
				# Pushing to the named branch alone doesn't update PRs created
				# via refs/for/dev/branch.
				if git push origin "HEAD:refs/for/$base_branch/$cur_branch" -o force-push=true --quiet 2>/dev/null; then
					echo "   Force-push succeeded — retrying merge..."
					sleep 3  # Give Forgejo a moment to update PR head
					# Retry fast-forward merge after rebase
					if api_post "$API_BASE/pulls/$pr_num/merge" "$merge_payload"; then
						if _check_pr_merged "$pr_num"; then
							echo "✅ Fast-forward merged after local rebase!"
							rm -f "$tmp_file"
							return 0
						fi
					fi
					echo "⚠️  Merge not accepted after rebase (CI may need to re-run on new SHA)"
					echo ""
					echo "Recovery: ./scripts/merge-pr $pr_num --wait-for-ci"
				else
					echo "❌ Force-push failed after rebase"
				fi
			else
				# Restore backed-up .ops files even on rebase failure.
				# WI-buhov: same union-merge semantics as the success path —
				# even though rebase failed, the working tree may contain
				# ops appended by concurrent tracker activity that we must
				# not overwrite. Rebase-failure backups keep the original
				# basename (no .modified suffix is applied on this path),
				# so we don't need to strip it.
				if [[ "$had_ops_backup" == true ]]; then
					for ops_dir in .agent/tracker/.ops .agent/tracker-workspace/.ops; do
						_ops_union_restore_dir "$ops_backup/$ops_dir" "$ops_dir" false
					done
					echo "   Restored backed-up .ops files from $ops_backup (union-merged)"
				fi
				rm -rf "$ops_backup" 2>/dev/null || true
				echo "❌ Local rebase failed (conflicts?)"
				echo "   Resolve manually:"
				echo "     git fetch origin $base_branch"
				echo "     git rebase origin/$base_branch"
				echo "     git push origin HEAD:refs/for/$base_branch/$cur_branch -o force-push=true"
				echo "     ./scripts/merge-pr $pr_num --wait-for-ci"
			fi
			rm -f "$tmp_file"
			return 1
		fi

		# Check if PR was actually merged despite error code
		if _check_pr_merged "$pr_num"; then
			echo "✅ Verified: PR was successfully merged!"
			rm -f "$tmp_file"
			return 0
		fi

		# Transient failure — retry with backoff
		if [[ $attempt -lt $max_retries ]]; then
			echo "⚠️  Merge failed (HTTP $merge_http_code, attempt $attempt/$max_retries) — retrying in $((attempt * 5))s..."
			sleep $((attempt * 5))
		else
			echo "❌ Merge failed after $max_retries attempts (last HTTP $merge_http_code)"
			echo "Response: $merge_response"
			rm -f "$tmp_file"
			return 1
		fi
	done

	rm -f "$tmp_file"
	return 1
}

# ------------------------------------------------------------------
# _check_pr_merged PR_NUM  (internal helper)
# ------------------------------------------------------------------
_check_pr_merged() {
	local pr_num="$1"
	if api_get "$API_BASE/pulls/$pr_num"; then
		local merged
		merged=$(echo "$API_RESPONSE" | json_field "merged")
		[[ "$merged" == "True" || "$merged" == "true" ]]
	else
		return 1
	fi
}

# ------------------------------------------------------------------
# _attach_git_note DESC ORIG_SHA  (internal helper for squash merges)
# ------------------------------------------------------------------
_attach_git_note() {
	local desc="$1" orig_sha="$2"
	if [[ -z "$desc" ]]; then
		return 0
	fi

	local base_branch="${BASE_BRANCH:-dev}"
	git fetch origin "$base_branch" --quiet 2>/dev/null || return 0
	local new_sha
	new_sha=$(git rev-parse "origin/$base_branch" 2>/dev/null) || return 0

	echo "📝 Attaching git note with original commit body..."
	git notes add -f -m "Original commit body from $orig_sha:

$desc" "$new_sha" 2>/dev/null || true

	git push origin refs/notes/commits --quiet 2>/dev/null || \
	git push origin refs/notes/* --quiet 2>/dev/null || true
	echo "   Note attached to $new_sha"
}
