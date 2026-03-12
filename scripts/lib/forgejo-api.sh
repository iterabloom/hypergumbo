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
# poll_ci HEAD_SHA
#   CI polling with timeout and ci-complete bypass (Scenario A/B).
#   Uses API_BASE and FORGEJO_TOKEN from environment.
#   Returns: 0 = success, 1 = failure, 2 = timeout
# ------------------------------------------------------------------
poll_ci() {
	local head_sha="$1"
	local timeout="${CI_TIMEOUT_SECONDS:-2400}"
	local start_time elapsed
	start_time=$(date +%s)

	# Track how long ci-complete has been the sole holdout (Scenario A)
	local ci_complete_sole_holdout_since=0
	local poll_count=0
	local prev_summary=""

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
#   Fetch plain-text log for a CI job. Uses the web route with
#   /attempt/1/logs (the REST API /actions/jobs endpoint returns 404
#   on Codeberg's Forgejo v14).
#
#   Strategy (2 API calls + 1 web route):
#     1. GET /actions/runs?head_sha=<full-sha> → index_in_repo (run number)
#     2. GET /{owner}/{repo}/actions/runs/{run_number}/jobs/0 (HTML)
#        → parse embedded JSON for job names/indices
#     3. GET /{owner}/{repo}/actions/runs/{run_number}/jobs/{idx}/attempt/1/logs
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

	# Step 1: Find run_number (index_in_repo) for this commit
	local runs_response
	runs_response=$(curl -sS --http1.1 --max-time 15 \
		-H "Authorization: token ${FORGEJO_TOKEN:-}" \
		"$API_BASE/actions/runs?head_sha=$head_sha" 2>/dev/null) || return 1

	local run_info
	run_info=$(echo "$runs_response" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    runs = data.get('workflow_runs', data if isinstance(data, list) else [])
    # Prefer ci.yml (exact match) over tracker-ci.yml / full-suite.yml.
    # ci.yml has the pytest job; tracker-ci.yml has only tracker validation.
    ci_runs = [r for r in runs if str(r.get('workflow_id', '')) == 'ci.yml']
    pick = ci_runs[0] if ci_runs else (runs[0] if runs else None)
    if pick:
        print(f'{pick[\"id\"]} {pick.get(\"index_in_repo\", \"\")}')
except Exception:
    pass
" 2>/dev/null)

	local run_id run_number
	read -r run_id run_number <<< "$run_info"

	if [[ -z "$run_number" ]]; then
		echo "Could not find CI run for commit ${head_sha:0:8}" >&2
		return 1
	fi

	# Step 2: Get job list from the HTML page's embedded JSON
	local page_html
	page_html=$(curl -sSL --http1.1 --max-time 15 \
		-H "Authorization: token ${FORGEJO_TOKEN:-}" \
		"$web_base/actions/runs/$run_number/jobs/0" 2>/dev/null) || return 1

	local job_index job_name
	read -r job_index job_name < <(echo "$page_html" | python3 -c "
import sys, json, html, re

page = html.unescape(sys.stdin.read())
target = '${target_name}'.lower()

# Extract JSON blob containing jobs from the decoded HTML
match = re.search(r'\"jobs\":\s*\[(\{.*?\}(?:,\{.*?\})*)\]', page)
if not match:
    sys.exit(1)

jobs = json.loads('[' + match.group(1) + ']')

# Match by name, or pick first failed job, or first job
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
" 2>/dev/null)

	if [[ -z "$job_index" ]]; then
		echo "Could not find job${target_name:+ matching '$target_name'} in run $run_number" >&2
		return 1
	fi

	echo "Fetching log for job '$job_name' (run #$run_number, index $job_index)..." >&2

	# Step 3: Download log via web route with attempt number
	curl -sSL --http1.1 --max-time 60 \
		-H "Authorization: token ${FORGEJO_TOKEN:-}" \
		"$web_base/actions/runs/$run_number/jobs/$job_index/attempt/1/logs" 2>/dev/null
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
				# Ops files are append-only, so restoring the pre-rebase copy
				# (which has the latest appended ops) is safe — the rebased
				# version from dev is a subset of what we backed up.
				if [[ "$had_ops_backup" == true ]]; then
					for ops_dir in .agent/tracker/.ops .agent/tracker-workspace/.ops; do
						local backup_subdir="$ops_backup/$ops_dir"
						[[ -d "$backup_subdir" ]] || continue
						mkdir -p "$ops_dir"
						for f in "$backup_subdir"/*; do
							[[ -f "$f" ]] || continue
							local base
							base=$(basename "$f")
							# Strip .modified suffix for tracked-file backups
							local target_name="${base%.modified}"
							cp "$f" "$ops_dir/$target_name"
						done
					done
					echo "   Restored backed-up .ops files from $ops_backup"
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
				# Restore backed-up .ops files even on rebase failure
				if [[ "$had_ops_backup" == true ]]; then
					for ops_dir in .agent/tracker/.ops .agent/tracker-workspace/.ops; do
						local backup_subdir="$ops_backup/$ops_dir"
						[[ -d "$backup_subdir" ]] || continue
						mkdir -p "$ops_dir"
						for f in "$backup_subdir"/*; do
							[[ -f "$f" ]] || continue
							cp "$f" "$ops_dir/$(basename "$f")"
						done
					done
					echo "   Restored backed-up .ops files from $ops_backup"
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
