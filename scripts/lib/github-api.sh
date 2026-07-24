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
	{
		echo "CI logs are hosted in Woodpecker (behind Cloudflare Access) and are"
		echo "not retrievable via the API. Open the run directly:"
		echo "  ${target_url:-<Woodpecker UI>}"
	} >&2
	return 1
}
