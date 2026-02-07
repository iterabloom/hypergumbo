#!/usr/bin/env bash
# ------------------------------------------------------------------
# forgejo-api.sh: Shared library for Forgejo/Gitea API interactions
#
# Sourceable by auto-pr, ci-debug, contribute, and merge-pr.
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

	while true; do
		elapsed=$(( $(date +%s) - start_time ))
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
			local failed_contexts
			failed_contexts=$(echo "$API_RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    failed = [s.get('context', 'unknown') for s in data.get('statuses', [])
              if s.get('state') in ('failure', 'error')]
    print(', '.join(failed) if failed else 'unknown')
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")
			echo ""
			echo "❌ CI Failed. Contexts: $failed_contexts"
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
				echo "⚠️  ci-complete is sole pending job (Scenario A detection started)"
			fi
			local holdout_elapsed=$(( $(date +%s) - ci_complete_sole_holdout_since ))
			if [[ $holdout_elapsed -ge 300 ]]; then
				echo ""
				echo "✅ Scenario A: ci-complete hung for ${holdout_elapsed}s but all other jobs passed. Declaring success."
				return 0
			fi
		else
			# Reset if the situation changes
			ci_complete_sole_holdout_since=0
		fi

		printf "."
		sleep 10
	done
}

# ------------------------------------------------------------------
# do_merge PR_NUM TITLE DESC ORIG_SHA [--squash]
#   Merge helper: tries fast-forward, falls back to squash if requested.
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

	# Default: try fast-forward merge
	echo "🚀 Attempting fast-forward merge (preserves commit bodies)..."

	local merge_payload='{"do": "fast-forward-only", "delete_branch_after_merge": true}'

	if api_post "$API_BASE/pulls/$pr_num/merge" "$merge_payload"; then
		echo "✅ Fast-forward merged! (commit bodies preserved)"
		rm -f "$tmp_file"
		return 0
	fi

	# Check if it's a divergence error
	if echo "$API_RESPONSE" | grep -qi "not fast-forward\|cannot fast-forward\|branch has diverged"; then
		echo ""
		echo "❌ Fast-forward not possible: branch has diverged"
		echo ""
		echo "Please rebase and retry:"
		echo "  git fetch origin dev"
		echo "  git rebase origin/dev"
		echo "  ./scripts/auto-pr"
		echo ""
		echo "Or, if you must squash (loses commit body details):"
		echo "  ./scripts/auto-pr --squash"
		rm -f "$tmp_file"
		return 1
	fi

	# Check if PR was actually merged despite error code
	if _check_pr_merged "$pr_num"; then
		echo "✅ Verified: PR was successfully merged!"
		rm -f "$tmp_file"
		return 0
	fi

	echo "❌ Merge failed (HTTP $API_HTTP_CODE)"
	echo "Response: $API_RESPONSE"
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
