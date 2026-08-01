#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# ------------------------------------------------------------------
# github-api.sh: GitHub-backend implementations for the forge tooling.
#
# Sourced by forgejo-api.sh (the dispatcher).  Holds ONLY the functions
# whose GitHub behavior genuinely diverges from Forgejo/Gitea — merge
# (PUT {merge_method} vs POST {do}) and CI-log fetch (Woodpecker logs are
# behind Cloudflare Access, unlike Forgejo's web log route).  Everything
# else in forgejo-api.sh is already shape-compatible (Gitea copied GitHub's
# REST API), so the dispatcher gates only these on ${FORGE_BACKEND:-forgejo}.
#
# DORMANT: nothing here runs unless FORGE_BACKEND=github (origin is
# github.com, or HYPERGUMBO_FORGE_BACKEND=github forces it).  While Codeberg
# is origin the whole file is inert.
#
# Retirement note (Phase 3b): when GitHub is declared permanent, the Forgejo
# bodies in forgejo-api.sh are deleted and this file's contents fold back in.
# ------------------------------------------------------------------

# Guard against double-sourcing.
[[ -n "${_GITHUB_API_LOADED:-}" ]] && return 0
_GITHUB_API_LOADED=1

# ------------------------------------------------------------------
# _github_delete_pr_head_branch PR_NUM
#   Delete a merged PR's head branch (parity with Forgejo's
#   delete_branch_after_merge, which GitHub's merge endpoint lacks).
#   Best-effort — never fatal.  Reads head.ref from the PR, then
#   DELETE /git/refs/heads/{ref} (204 on success, 422 if already gone).
# ------------------------------------------------------------------
_github_delete_pr_head_branch() {
	local pr_num="$1"
	if api_get "$API_BASE/pulls/$pr_num"; then
		local ref
		ref=$(echo "$API_RESPONSE" | json_field "head.ref")
		if [[ -n "$ref" && "$ref" != "None" ]]; then
			api_call DELETE "$API_BASE/git/refs/heads/$ref" >/dev/null 2>&1 || true
		fi
	fi
}

# ------------------------------------------------------------------
# _github_do_merge PR_NUM TITLE DESC ORIG_SHA [--squash|true]
#   GitHub merge: PUT /pulls/{n}/merge with {"merge_method": ...},
#   cascading rebase -> merge (or squash when forced).  Success is a
#   200 carrying {"merged": true}; verified via _check_pr_merged (which
#   also catches an already-merged idempotent retry).  GitHub has no AGit
#   proc-receive / DB-desync failure mode, so none of the Forgejo
#   desync-resync / local-rebase machinery in do_merge applies here.
#   Drop-in for do_merge: same args, same 0/1 return, attaches the git
#   note on success and deletes the merged head branch.
# ------------------------------------------------------------------
_github_do_merge() {
	local pr_num="$1" desc="$3" orig_sha="$4"
	local force_squash="${5:-false}"

	local -a methods
	if [[ "$force_squash" == "--squash" || "$force_squash" == "true" ]]; then
		methods=(squash)
		echo "⚠️  Squash merge requested (commit body preserved via git notes)"
	else
		methods=(rebase merge)
		echo "🚀 Attempting rebase merge (GitHub backend)..."
	fi

	local method payload last_code=""
	for method in "${methods[@]}"; do
		payload="{\"merge_method\": \"$method\"}"
		api_call PUT "$API_BASE/pulls/$pr_num/merge" "$payload" || true
		last_code="$API_HTTP_CODE"
		# GitHub returns 200 {merged:true} on success; a retry after a
		# successful merge (or a 405 "already merged") also verifies true.
		if _check_pr_merged "$pr_num"; then
			echo "✅ Merged via $method! (GitHub)"
			_github_delete_pr_head_branch "$pr_num"
			_attach_git_note "$desc" "$orig_sha"
			return 0
		fi
	done

	echo "❌ GitHub merge failed after trying: ${methods[*]} (last HTTP $last_code)"
	echo ""
	echo "Recovery: ./scripts/merge-pr $pr_num"
	return 1
}

# ------------------------------------------------------------------
# _github_fetch_job_log HEAD_SHA [JOB_NAME]
#   Capability gap: Woodpecker CI logs live behind Cloudflare Access and
#   are not retrievable via the GitHub API (GitHub Actions is disabled;
#   the only agent-readable CI signal is the Woodpecker commit-STATUS).
#   Degrade gracefully — point the caller at the status target_url and
#   return non-zero so callers fall back to their "could not retrieve
#   log" path.  Drop-in for fetch_job_log.
# ------------------------------------------------------------------
_github_fetch_job_log() {
	local head_sha="${1:-}"
	local job_name="${2:-}"
	local target_url=""
	if api_get "$API_BASE/commits/$head_sha/status"; then
		target_url=$(echo "$API_RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for s in data.get('statuses', []):
        u = s.get('target_url')
        if u:
            print(u)
            break
except Exception:
    pass
" 2>/dev/null || echo "")
	fi

	# WI-solob. Fetching a Woodpecker log takes TWO calls, not one, and the
	# reason is a trap worth stating: the trailing number in the UI url
	# (/repos/<repo>/pipeline/<number>/<n>) is the WORKFLOW index, not the step.
	# The logs endpoint keys on the step's GLOBAL id, so a path built from the
	# UI url 404s no matter how correct the credentials are. Resolve the step
	# from the pipeline first, then fetch.
	#
	# The SERVER comes from WOODPECKER_SERVER in .env, never from this file:
	# the repo is public, so the CI host is configuration, not source. Only the
	# pipeline COORDINATES are read off target_url, and only its path is
	# matched, so a target_url can never reintroduce a hard-coded host.
	local wp_host wp_repo wp_pipeline
	wp_host="${WOODPECKER_SERVER:-}"
	wp_host="${wp_host%/}"
	if [[ "$target_url" =~ /repos/([0-9]+)/pipeline/([0-9]+) ]]; then
		wp_repo="${BASH_REMATCH[1]}"
		wp_pipeline="${BASH_REMATCH[2]}"
	fi

	if [[ -n "$wp_host" && -n "${wp_repo:-}" && -n "${wp_pipeline:-}" \
	      && -n "${WOODPECKER_TOKEN:-}" && -n "${CF_ACCESS_CLIENT_ID:-}" \
	      && -n "${CF_ACCESS_CLIENT_SECRET:-}" ]]; then
		local -a _wp_hdr=(
			-H "Authorization: Bearer $WOODPECKER_TOKEN"
			-H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID"
			-H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
		)
		local pipeline_json step_id
		pipeline_json=$(curl -sS "${_wp_hdr[@]}" \
			"$wp_host/api/repos/$wp_repo/pipelines/$wp_pipeline" 2>/dev/null || true)
		# Prefer a step whose name matches JOB_NAME; otherwise the FAILED step,
		# so `ci-debug logs` with no job lands on the thing that actually broke.
		step_id=$(WP_JOB="$job_name" python3 -c "
import sys, json, os
want = (os.environ.get('WP_JOB') or '').strip().lower()
try:
    steps = [s for wf in (json.load(sys.stdin).get('workflows') or [])
             for s in (wf.get('children') or [])]
except Exception:
    sys.exit(0)
if want:
    for s in steps:
        if str(s.get('name','')).lower() == want:
            print(s.get('id')); sys.exit(0)
for s in steps:
    if s.get('state') == 'failure' or s.get('exit_code'):
        print(s.get('id')); sys.exit(0)
if steps:
    print(steps[-1].get('id'))
" <<<"$pipeline_json" 2>/dev/null || echo "")

		if [[ -n "$step_id" ]]; then
			local body http
			body=$(curl -sS -w $'\n%{http_code}' "${_wp_hdr[@]}" \
				"$wp_host/api/repos/$wp_repo/logs/$wp_pipeline/$step_id" 2>/dev/null || true)
			http="${body##*$'\n'}"
			body="${body%$'\n'*}"
			if [[ "$http" == "200" && -n "$body" ]]; then
				# Each entry is ONE line with a `line` ordinal, and `data` can be
				# null — both learned from a real response, and both silently
				# corrupt the output if assumed away.
				python3 -c "
import sys, json, base64
try:
    entries = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if not isinstance(entries, list):
    # WI-holik: an HTTP 200 whose body is JSON null (or an error-envelope
    # object) is an error response wearing a success code. sorted(None)
    # raised a bare TypeError here — a traceback at the exact moment the
    # operator needs the failed-CI log. Diagnose the shape instead.
    sys.stderr.write(
        'woodpecker log endpoint returned '
        + type(entries).__name__
        + ', not a log-entry list (an error response with a 200 code)\n'
    )
    sys.exit(1)
out = []
for e in sorted(entries, key=lambda x: x.get('line') or 0):
    d = e.get('data')
    if d is None:
        continue
    try:
        d = base64.b64decode(d).decode('utf-8', 'replace')
    except Exception:
        d = str(d)
    out.append(d.rstrip('\n'))
sys.stdout.write('\n'.join(out) + '\n')
" <<<"$body" && return 0
			fi
			echo "Woodpecker log fetch failed (HTTP ${http:-000})." >&2
		else
			echo "Could not resolve a step id from pipeline $wp_pipeline." >&2
		fi
	fi

	{
		echo "CI logs are hosted in Woodpecker behind Cloudflare Access and need"
		echo "credentials. Open the run directly:"
		echo "  ${target_url:-<Woodpecker UI>}"
		echo ""
		echo "To make this fetchable (WI-solob), set in .env:"
		echo "  WOODPECKER_SERVER        - https://<your woodpecker host>"
		echo "  WOODPECKER_TOKEN         - Woodpecker API token (app auth)"
		echo "  CF_ACCESS_CLIENT_ID      - Cloudflare Access service token id,"
		echo "                             the FULL value, ending in"
		echo "                             .access.<team>.cloudflareaccess.com"
		echo "  CF_ACCESS_CLIENT_SECRET  - Cloudflare Access service token secret"
		echo ""
		echo "Reaching the host is NOT enough: an unauthenticated GET is 302-"
		echo "redirected to the Access login and never reaches Woodpecker. The"
		echo "Access application also needs a policy whose ACTION is 'Service"
		echo "Auth' including that token. Diagnose from the code you get back:"
		echo "  302 -> not recognised as a service token (check the Client ID)"
		echo "  403 -> recognised, but no Service Auth policy admits it"
		echo "  401 -> past Access; the Woodpecker token is the problem"
		echo "  404 -> past both; wrong API path or step id"
	} >&2
	return 1
}
