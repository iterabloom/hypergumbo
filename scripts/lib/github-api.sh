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

# ------------------------------------------------------------------
# Woodpecker pipeline readers (INV-bozid, the MASKING half).
#
# A Woodpecker workflow publishes ONE commit status for all of its steps, so
# `ci/woodpecker/cron/full-suite: failure` names no step, and one
# chronically-red step makes every sibling's verdict unreadable for as long
# as it stays red. The pipeline object behind a status's target_url carries
# each step's own state (`workflows[].children[].state`), and the log fetcher
# below was already reading it to pick the failed step -- so the per-gate
# verdict the aggregate hides was one call away the whole time. These helpers
# make that call once and render it, for `ci-debug status` and `ci-debug
# cron-status` alike.
#
# The alternative -- one workflow FILE per gate, so GitHub sees one context
# per gate -- was verified viable and deliberately not taken: every split
# file needs its own clone and its own grammar build, a single-agent runner
# would serialize them, and the first validation of any cron-file change is
# the next firing up to twelve hours away. Reading the verdict that already
# exists costs none of that. The split stays available if a gate ever needs
# its own GitHub check context (a required check, say); nothing here
# forecloses it.
#
# Credentials come from .env (WOODPECKER_SERVER / WOODPECKER_TOKEN /
# CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET -- see the tail of
# _github_fetch_job_log for what each HTTP code means). The GitHub token is
# never sent to the Woodpecker host.
# ------------------------------------------------------------------
_wp_have_credentials() {
	[[ -n "${WOODPECKER_SERVER:-}" && -n "${WOODPECKER_TOKEN:-}" \
	   && -n "${CF_ACCESS_CLIENT_ID:-}" && -n "${CF_ACCESS_CLIENT_SECRET:-}" ]]
}

# _wp_curl ARGS...
#   curl against the Woodpecker host with the Access + API headers attached.
#   The ONE place those headers are spelled.
_wp_curl() {
	curl -sS --max-time 60 \
		-H "Authorization: Bearer $WOODPECKER_TOKEN" \
		-H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
		-H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
		"$@"
}

# _wp_pipeline_coords TARGET_URL
#   Prints "REPO PIPELINE [WORKFLOW_INDEX]" parsed off a status target_url
#   (/repos/<repo>/pipeline/<number>[/<index>]); returns 1 for any other URL.
#   Only the PATH is matched -- the host comes from .env, never from the URL,
#   so a target_url can never redirect the credentials elsewhere (WI-solob).
#   The trailing number is the WORKFLOW index within the pipeline, which is
#   what a matrix leg's context (`.../nightly/2`) points at.
_wp_pipeline_coords() {
	local url="${1:-}"
	if [[ "$url" =~ /repos/([0-9]+)/pipeline/([0-9]+)(/([0-9]+))? ]]; then
		echo "${BASH_REMATCH[1]} ${BASH_REMATCH[2]} ${BASH_REMATCH[4]:-}"
		return 0
	fi
	return 1
}

# _wp_pipeline_json REPO PIPELINE OUTFILE
#   GET /api/repos/{repo}/pipelines/{n}; the body lands in OUTFILE and the
#   HTTP code in WP_HTTP_CODE, so a refusal can be named rather than hidden.
#   Returns 0 on 200. The body goes to a FILE and not to stdout on purpose:
#   a caller capturing stdout with `$(...)` runs this in a subshell, where
#   WP_HTTP_CODE is set and then thrown away -- the first cut reported every
#   refusal as "HTTP 000" for exactly that reason.
_wp_pipeline_json() {
	local repo="$1" pipeline="$2" out="$3" host="${WOODPECKER_SERVER%/}"
	WP_HTTP_CODE=$(_wp_curl -o "$out" -w '%{http_code}' \
		"$host/api/repos/$repo/pipelines/$pipeline" 2>/dev/null) || WP_HTTP_CODE="000"
	[[ "$WP_HTTP_CODE" == "200" ]]
}

# _wp_render_steps TARGET_URL [INDENT]
#   One line per step of the workflow a status points at:
#     OK   self-claims-gate
#     FAIL test-all-packages (exit 1)
#     ERR  build-grammars          <- the pipeline died here; NOT a pass
#     SKIP test-agent-infra        <- never ran; NOT a pass
#   `error` and `skipped` get their own marks because INV-bobor's third
#   delivery failure was exactly a died-inside-the-gate pipeline reading like
#   a passed gate. Prints nothing for a non-Woodpecker URL. When the steps
#   CANNOT be read -- no credentials, a refused fetch, an index the pipeline
#   does not have -- it says so and names why (once per run for the
#   credential case): an empty that does not name what it searched is an
#   absence manufactured by the instrument.
_wp_render_steps() {
	local url="${1:-}" indent="${2:-}"
	local coords repo pipeline wf
	coords=$(_wp_pipeline_coords "$url") || return 0
	read -r repo pipeline wf <<<"$coords"
	if ! _wp_have_credentials; then
		if [[ -z "${_WP_CREDS_NOTED:-}" ]]; then
			_WP_CREDS_NOTED=1
			echo "${indent}(per-step verdicts not read: WOODPECKER_SERVER, WOODPECKER_TOKEN,"
			echo "${indent} CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET must all be set in .env)"
		fi
		return 0
	fi
	local body
	body=$(mktemp)
	if ! _wp_pipeline_json "$repo" "$pipeline" "$body"; then
		rm -f "$body"
		echo "${indent}(per-step verdicts not read: pipeline $pipeline returned HTTP ${WP_HTTP_CODE:-000})"
		return 0
	fi
	# The payload travels by FILE: the heredoc owns stdin here.
	WP_BODY_FILE="$body" WP_WF="$wf" WP_INDENT="$indent" WP_PIPELINE="$pipeline" \
		python3 - <<'PY'
import json, os
indent = os.environ.get("WP_INDENT", "")
want = os.environ.get("WP_WF") or ""
pipeline = os.environ.get("WP_PIPELINE", "?")
try:
    with open(os.environ["WP_BODY_FILE"]) as fh:
        data = json.load(fh)
except (OSError, json.JSONDecodeError):
    data = {}
workflows = (data.get("workflows") or []) if isinstance(data, dict) else []
if want:
    workflows = [w for w in workflows if str(w.get("pid")) == want]
    if not workflows:
        print(f"{indent}(per-step verdicts not read: pipeline {pipeline} has no workflow #{want})")
        raise SystemExit(0)
MARK = {"success": "OK  ", "failure": "FAIL", "error": "ERR ",
        "skipped": "SKIP", "killed": "KILL"}
shown = 0
for wf in workflows:
    for step in (wf.get("children") or []):
        state = str(step.get("state") or "pending")
        mark = MARK.get(state, "... ")
        suffix = ""
        if state == "failure" and step.get("exit_code"):
            suffix = f" (exit {step['exit_code']})"
        elif state not in MARK:
            suffix = f" ({state})"
        print(f"{indent}{mark} {step.get('name', '?')}{suffix}")
        shown += 1
if not shown:
    print(f"{indent}(per-step verdicts not read: pipeline {pipeline} lists no steps)")
PY
	rm -f "$body"
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

	# WI-ratam: a STEP name matches no context, so the selector above has
	# already fallen through to a gate chosen by rule 2 -- before any step was
	# looked at. On dev 8955d9a2 (push/woodpecker beside cron/full-suite, both
	# green) `logs self-claims-gate` therefore declared "no step by that name"
	# and printed the push transcript, while `status` had just LISTED that
	# step under the cron gate: the per-step reader and this fetcher
	# disagreed about what exists. So, when the name matched no gate, search
	# every gate's pipeline for a step carrying it (failed gates first,
	# mirroring rule 2) and let the first hit choose the pipeline. The honest
	# substitution warning below stays for the genuinely-absent case, and it
	# is honest only because every pipeline was read before it fires.
	if [[ -n "$job_name" && "${how:-}" != "matched" ]] && _wp_have_credentials; then
		local _cands _cand_url _cand_ctx _cand_coords _cand_repo _cand_pipe _cand_json
		_cands=$(echo "$API_RESPONSE" | python3 -c "
import sys, json
try:
    statuses = [s for s in json.load(sys.stdin).get('statuses', [])
                if s.get('target_url')]
except Exception:
    sys.exit(0)
failed = [s for s in statuses
          if str(s.get('state', '')).lower() in ('failure', 'error')]
for s in failed + [s for s in statuses if s not in failed]:
    print('\t'.join((s['target_url'], str(s.get('context', '')))))
" 2>/dev/null || echo "")
		_cand_json=$(mktemp)
		# `while ... done <<<` runs in THIS shell, so the assignments inside
		# survive it -- a `| while` pipeline would lose them (the WP_HTTP_CODE
		# lesson, one function over).
		while IFS=$'\t' read -r _cand_url _cand_ctx; do
			[[ -n "$_cand_url" ]] || continue
			_cand_coords=$(_wp_pipeline_coords "$_cand_url") || continue
			read -r _cand_repo _cand_pipe _ <<<"$_cand_coords"
			_wp_pipeline_json "$_cand_repo" "$_cand_pipe" "$_cand_json" || continue
			if WP_JOB="$job_name" python3 -c "
import sys, json, os
want = (os.environ.get('WP_JOB') or '').strip().lower()
try:
    workflows = json.load(sys.stdin).get('workflows') or []
except Exception:
    sys.exit(1)
sys.exit(0 if any(str(s.get('name', '')).lower() == want
                  for wf in workflows for s in (wf.get('children') or []))
         else 1)
" <"$_cand_json" 2>/dev/null; then
				target_url="$_cand_url"
				how="matched-step"
				ctx="$_cand_ctx"
				break
			fi
		done <<<"$_cands"
		rm -f "$_cand_json"
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
	local wp_host wp_repo wp_pipeline _wp_coords
	wp_host="${WOODPECKER_SERVER:-}"
	wp_host="${wp_host%/}"
	if _wp_coords=$(_wp_pipeline_coords "$target_url"); then
		read -r wp_repo wp_pipeline _ <<<"$_wp_coords"
	fi

	if [[ -n "${wp_repo:-}" && -n "${wp_pipeline:-}" ]] && _wp_have_credentials; then
		local pipeline_json step_id
		pipeline_json=$(mktemp)
		_wp_pipeline_json "$wp_repo" "$wp_pipeline" "$pipeline_json" || true
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
" <"$pipeline_json" 2>/dev/null || echo "")
		rm -f "$pipeline_json"
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
			body=$(_wp_curl -w $'\n%{http_code}' \
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
