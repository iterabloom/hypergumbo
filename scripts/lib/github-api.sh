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
# _wp_warn_substituted KIND REQUESTED HOW ACTUAL
#
# INV-vazuh. The log resolver picks a pipeline and then a step inside it, and
# BOTH selections degrade to a fallback when an explicit name matches nothing.
# That degradation is deliberate and useful; what made it dangerous is that it
# was invisible, so a substituted transcript was indistinguishable from the
# one that was asked for. One rule, one place: whenever a name was supplied
# and the selector did NOT match it, say so on stderr, naming both sides so
# the reader can tell which gate the numbers below actually came from.
#
# Deliberately silent when no name was requested — nothing was substituted
# then, and a warning on every ordinary call is noise that trains the reader
# to skip it.
_wp_warn_substituted() {
	local requested="${1:-}" how="${2:-}" actual="${3:-}"
	[[ -z "$requested" ]] && return 0
	[[ "$how" != fallback-* ]] && return 0
	{
		echo "⚠️  Nothing named '$requested' on this commit — no gate and no"
		echo "    step by that name."
		if [[ -n "$actual" ]]; then
			echo "    Showing '$actual' INSTEAD. The output below is NOT"
			echo "    '$requested' — a gate that never ran cannot pass."
		fi
		if [[ "$how" == "fallback-first" ]]; then
			echo "    (Nothing had failed either, so this is simply the first"
			echo "     gate reported. Run 'ci-debug status' to list them.)"
		fi
	} >&2
}

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
	# WI-zavut: pick the PIPELINE before picking the step inside it. Several
	# gates report on one commit (push/woodpecker beside cron/full-suite), and
	# each is a SEPARATE Woodpecker pipeline with its own target_url. This
	# resolver used to take the first status carrying one and break, applying
	# JOB_NAME only later to choose a step within a pipeline already chosen
	# wrongly — so every job name returned the push transcript, and the cron
	# gate's log had never been read by anyone while that gate reported
	# FAILURE. Two rules, in order:
	#
	#   1. an explicit job name selects its OWN gate (matched on the full
	#      context or its trailing segment, because operators type
	#      "full-suite", not "ci/woodpecker/cron/full-suite");
	#   2. with no name — or a name that matches nothing — prefer the gate
	#      that FAILED. Reaching for a log means something broke, so
	#      defaulting to the first status handed back the GREEN pipeline
	#      while a different gate was red. This mirrors the step-level rule
	#      already applied below.
	#
	# INV-vazuh: rule 2 is right and stays, but it must not be SILENT. On dev
	# ea0d6a83ab the only status is push/woodpecker (success) — the cron gate
	# never ran on that commit. `logs cron/full-suite ea0d6a83ab` matched no
	# status, fell past the failed-gate rule (nothing had failed) to
	# statuses[0], and printed the PUSH pipeline's GREEN transcript at rc=0.
	# Nothing in the output said "that is not the gate you asked for", so a
	# gate that never ran reads as a gate that passed. Substituting is still
	# the most useful answer; doing it without saying so is what manufactures
	# a false green. Both selectors below now report HOW they chose, and both
	# route the warning through _wp_warn_substituted.
	if api_get "$API_BASE/commits/$head_sha/status"; then
		local _sel how ctx
		_sel=$(echo "$API_RESPONSE" | WP_JOB="$job_name" python3 -c "
import sys, json, os
want = (os.environ.get('WP_JOB') or '').strip().lower()
try:
    statuses = [s for s in json.load(sys.stdin).get('statuses', [])
                if s.get('target_url')]
except Exception:
    sys.exit(0)

def emit(s, how):
    print('\t'.join((s['target_url'], how, str(s.get('context', '')))))
    sys.exit(0)

if want:
    for s in statuses:
        ctx = str(s.get('context', '')).lower()
        if want == ctx or want in ctx or ctx.rsplit('/', 1)[-1] == want:
            emit(s, 'matched')
for s in statuses:
    if str(s.get('state', '')).lower() in ('failure', 'error'):
        emit(s, 'fallback-failed')
if statuses:
    emit(statuses[0], 'fallback-first')
" 2>/dev/null || echo "")
		IFS=$'\t' read -r target_url how ctx <<<"$_sel"
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
		local _step_sel step_how step_name
		_step_sel=$(WP_JOB="$job_name" python3 -c "
import sys, json, os
want = (os.environ.get('WP_JOB') or '').strip().lower()
try:
    steps = [s for wf in (json.load(sys.stdin).get('workflows') or [])
             for s in (wf.get('children') or [])]
except Exception:
    sys.exit(0)

def emit(s, how):
    print('\t'.join((str(s.get('id')), how, str(s.get('name', '')))))
    sys.exit(0)

if want:
    for s in steps:
        if str(s.get('name','')).lower() == want:
            emit(s, 'matched')
for s in steps:
    if s.get('state') == 'failure' or s.get('exit_code'):
        emit(s, 'fallback-failed')
if steps:
    emit(steps[-1], 'fallback-first')
" <<<"$pipeline_json" 2>/dev/null || echo "")
		IFS=$'\t' read -r step_id step_how step_name <<<"$_step_sel"
		# ONE name is tried as a gate and then as a step, so a fallback at
		# either level alone is not a substitution:
		#   gate matched  -> the name was a gate name; no step will carry it,
		#                    and falling back to the failed STEP is the point.
		#   step matched  -> the name was a step name; the gate fallback that
		#                    got us into this pipeline did its job.
		# Only when NEITHER matched has the caller been handed something they
		# did not ask for, and that is the one case worth interrupting for.
		if [[ "${how:-}" != "matched" && "${step_how:-}" != "matched" ]]; then
			_wp_warn_substituted "$job_name" "${how:-}" "${ctx:-}"
		fi

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

	# Reached without credentials (or without pipeline coordinates), so no step
	# was ever consulted and the target_url printed below is whatever the gate
	# selection produced. If that was a fallback, the URL points at a DIFFERENT
	# gate than the one named — say so before printing it.
	_wp_warn_substituted "$job_name" "${how:-}" "${ctx:-}"

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
