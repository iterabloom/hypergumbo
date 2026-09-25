#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# brand-scrub.sh — the vendor-attribution scrub, as a sourceable library.
#
# WHY THIS FILE EXISTS. The scrub used to live entirely inside the `commit-msg`
# hook, which meant it protected exactly one channel: a git commit message. A PR
# title, a PR body and a PR comment are none of those things, so every one of
# them reached the forge unscrubbed. That was not a matching failure -- the
# shipped pattern list matches a vendor URL correctly, and the observed leak sat
# on a line the existing scan window already covers. It was a CHANNEL GAP: the
# rule was enforced at one of six egress points because the rule lived inside
# the hook that happened to discover it first. (WI-sidot.)
#
# So the matching, the phrase selection and the line sweep move here, and every
# caller -- the hook and the forge scripts alike -- sources this one file and
# reads `.githooks/brand-patterns.txt`, the one pattern list. A second
# implementation is how two channels drift apart; there is now only one.
#
# WHY IT LIVES IN .githooks/ RATHER THAN scripts/lib/. It is colocated with the
# data it cannot run without (`brand-patterns.txt`, `absurd-phrases.txt`), and
# `commit-msg` must be able to find it by `$hook_dir` alone -- the hook is
# copied into a bare sandbox by `test_hooks.sh`, with no repo around it.
#
# THE POLICY, AND WHY IT IS NOT "REFUSE". A body hit DELETES THE WHOLE LINE and
# says so only in a count. Refusing instead -- rejecting the push and making a
# human fix the text -- was proposed and declined: PR bodies here are fulsome
# and the code is well-commented, so losing a few words costs less than handing
# a human a blocked PR to unblock. That decision is load-bearing for the pattern
# list, which is full of ordinary English (Nova, Titan, Granite, Arctic, Falcon,
# Phi). `falcon` is a web framework this project ships support for. Under a
# refuse policy every PR about the falcon analyzer would be blocked; under this
# one it silently loses a line, which is the priced cost.
#
# THE WINDOW. Only the first and last `BS_BODY_SCAN_WINDOW` lines of a body are
# swept (default 10). Vendor self-attribution clusters at the two ends; the
# middle of a long body is ordinary technical prose, where an ordinary-English
# false positive would delete something real and say nothing useful about it. A
# body of <= 2*window lines is fully covered, so this changes nothing for short
# text. Keeping the window for forge text too is a deliberate parity decision:
# the unscanned middle is a known, accepted cost, not an oversight.
#
# CASE SENSITIVITY IS LOAD-BEARING. Every matcher here runs case-insensitively,
# and each function sets `nocasematch` itself rather than trusting the caller's
# shell state -- `commit-msg` turns it on globally for its own `case` statement,
# but a forge script has no reason to, and a matcher whose correctness depends
# on an option someone else set is a matcher that fails silently.

# --- internals -------------------------------------------------------------
# Prefixed `bs_` throughout: this file is SOURCED into scripts that have their
# own helpers, and an unprefixed `have()` or `slugify()` would collide.

bs_have() { command -v "$1" >/dev/null 2>&1; }

bs_sha256_hex() {
	if bs_have sha256sum; then sha256sum | awk '{print $1}'
	elif bs_have shasum; then shasum -a 256 | awk '{print $1}'
	elif bs_have openssl; then openssl dgst -sha256 | awk '{print $NF}'
	elif bs_have python3; then
		python3 -c 'import sys,hashlib; sys.stdout.write(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
	else
		echo "brand-scrub: need sha256sum, shasum, openssl, or python3" >&2
		return 1
	fi
}

bs_gen_secret() {
	if bs_have openssl; then openssl rand -base64 32
	elif bs_have base64; then head -c 32 /dev/urandom | base64
	elif bs_have python3; then
		python3 -c 'import os,base64,sys; sys.stdout.write(base64.b64encode(os.urandom(32)).decode()+"\n")'
	else
		echo "brand-scrub: need openssl, base64, or python3 to generate secret" >&2
		return 1
	fi
}

bs_slugify() {
	local s
	s="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"
	s="${s#-}"; s="${s%-}"
	printf '%s' "$s"
}

bs_escape_regex_basic() {
	if bs_have sed; then
		printf '%s' "$1" | sed -e 's/[][(){}.^$*+?|\\]/\\&/g'
	elif bs_have perl; then
		perl -e 'my $s = join("", <STDIN>); chomp($s); print quotemeta($s);' <<<"$1"
	elif bs_have python3; then
		printf '%s' "$1" | python3 -c 'import re,sys; sys.stdout.write(re.escape(sys.stdin.read()))'
	else
		echo "brand-scrub: need sed, perl, or python3 to escape patterns" >&2
		return 1
	fi
}

# --- public API ------------------------------------------------------------

# bs_init [patterns_file] [phrases_file]
# Loads the pattern list into BS_BRAND_RE and the phrase list into BS_PHRASES,
# and ensures the keyed secret exists. Callers may re-invoke to re-point at a
# different list (the test suite does exactly this).
bs_init() {
	local here
	here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	local patterns="${1:-${HOOK_BRANDS_FILE:-$here/brand-patterns.txt}}"
	local phrases="${2:-${HOOK_PHRASES_FILE:-$here/absurd-phrases.txt}}"

	# --- patterns ---
	local pats=() line p
	if [[ -f "$patterns" ]]; then
		while IFS= read -r line || [[ -n "$line" ]]; do
			[[ -z "$line" ]] && continue
			[[ "$line" =~ ^[[:space:]]*# ]] && continue
			line="${line#"${line%%[![:space:]]*}"}"
			line="${line%"${line##*[![:space:]]}"}"
			[[ -z "$line" ]] && continue
			if [[ "$line" == re:* ]]; then
				# Raw regex: the author owns its boundaries.
				p="${line#re:}"
			else
				# Literal: \b-wrapped so "Stable" matches "Stable Diffusion"
				# but NOT "stable_id". This boundary rule is the reason the
				# shipped list can contain ordinary words at all.
				p="\\b$(bs_escape_regex_basic "$line")\\b"
			fi
			[[ -z "$p" ]] && continue
			pats+=( "$p" )
		done < "$patterns"
	fi
	if (( ${#pats[@]} == 0 )); then
		pats+=( "\\b$(bs_escape_regex_basic "FanDuel")\\b" )
		pats+=( "\\b$(bs_escape_regex_basic "DraftKings")\\b" )
		pats+=( "\\b$(bs_escape_regex_basic "BetMGM")\\b" )
	fi
	local joined="" first=1 s
	for s in "${pats[@]}"; do
		if (( first )); then joined="$s"; first=0; else joined="${joined}|${s}"; fi
	done
	BS_BRAND_RE="($joined)"

	# --- phrases ---
	BS_PHRASES=()
	local pline
	if [[ -f "$phrases" ]]; then
		while IFS= read -r pline || [[ -n "$pline" ]]; do
			[[ -z "$pline" ]] && continue
			[[ "$pline" =~ ^[[:space:]]*# ]] && continue
			pline="${pline#"${pline%%[![:space:]]*}"}"
			pline="${pline%"${pline##*[![:space:]]}"}"
			[[ -z "$pline" ]] && continue
			BS_PHRASES+=( "$pline" )
		done < "$phrases"
	fi
	if (( ${#BS_PHRASES[@]} == 0 )); then
		BS_PHRASES=(
			"a walrus conducting an orchestra of penguins"
			"an octopus painting a self-portrait with all eight arms"
			"a giraffe playing the violin on a tightrope"
			"a parrot steering a pirate ship made of spaghetti"
			"a raccoon juggling watermelons while surfing"
		)
	fi

	# The keyed secret is resolved LAZILY, by _bs_ensure_secret below. bs_init
	# must not touch it: only phrase selection needs a key, a body sweep never
	# does, and bs_init runs at SOURCE time in every script that talks to the
	# forge. Reading $HOME here made sourcing the library fail outright in any
	# environment without one -- `set -u` turns an unset HOME into an unbound
	# variable, which kills the source, which exits the caller 1 with no output.
	# That is not hypothetical: the auto-pr test suite deliberately runs with
	# `env={"PATH": "/usr/bin:/bin"}` to reproduce the caller's real shell, and
	# it caught exactly this.
	_BS_SECRET=""
}

# _bs_ensure_secret — locate (or create) the keyed secret, on first use only.
#
# The key and its path are underscore-private. They were BS_SECRET /
# BS_SECRET_FILE, sharing the public API's prefix, and a caller that reused
# `BS_SECRET` for a PATH got the library's VALUE interpolated back -- which
# wrote a freshly generated key into the repository root, under a filename
# that WAS the key. An internal should not look like part of the interface.
#
# Every expansion is `:-` guarded: this runs under `set -u` in every caller,
# and the whole point of the lazy split is that a missing HOME must degrade,
# never abort. Resolution order ends in a per-uid temp path so that a shell
# with neither HOME nor XDG_CONFIG_HOME still gets a key that is STABLE across
# runs -- `git commit --amend` re-runs the hook over an already-scrubbed
# message and must pick the same phrase, or the scrub stops being idempotent.
_bs_ensure_secret() {
	[[ -n "$_BS_SECRET" ]] && return 0
	local candidate=""
	if [[ -n "${BRAND_SCRUB_SECRET_FILE:-}" ]]; then
		candidate="$BRAND_SCRUB_SECRET_FILE"
	elif [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
		candidate="$XDG_CONFIG_HOME/brand-scrub.key"
	elif [[ -n "${HOME:-}" ]]; then
		candidate="$HOME/.config/brand-scrub.key"
	else
		candidate="${TMPDIR:-/tmp}/brand-scrub-$(id -u 2>/dev/null || echo 0).key"
	fi
	_BS_SECRET_FILE="$candidate"
	if [[ ! -f "$_BS_SECRET_FILE" ]]; then
		mkdir -p "$(dirname "$_BS_SECRET_FILE")" 2>/dev/null || true
		( umask 077; bs_gen_secret > "$_BS_SECRET_FILE" ) 2>/dev/null || true
		chmod 600 "$_BS_SECRET_FILE" 2>/dev/null || true
	fi
	# A key we could not persist still has to work: fall back to an in-process
	# one rather than letting a read-only filesystem abort a commit.
	_BS_SECRET="$(cat "$_BS_SECRET_FILE" 2>/dev/null || true)"
	[[ -n "$_BS_SECRET" ]] || _BS_SECRET="$(bs_gen_secret 2>/dev/null || echo fallback-key)"
	return 0
}

# bs_pick_phrase <seed> — deterministic per (secret, seed).
bs_pick_phrase() {
	_bs_ensure_secret
	local seed="$1" h idx
	h="$(printf '%s|%s' "$_BS_SECRET" "$seed" | bs_sha256_hex)"
	idx=$(( 16#${h:0:8} % ${#BS_PHRASES[@]} ))
	printf '%s' "${BS_PHRASES[$idx]}"
}

# bs_replace_ci <replacement> <text> — global, case-insensitive.
bs_replace_ci() {
	local repl="$1" in="$2"
	if bs_have perl; then
		REPL="$repl" BRAND_RE="$BS_BRAND_RE" perl -pe 's{$ENV{BRAND_RE}}{$ENV{REPL}}ig' <<<"$in"
	elif bs_have python3; then
		REPL="$repl" BRAND_RE="$BS_BRAND_RE" python3 -c '
import os, re, sys
sys.stdout.write(re.sub(os.environ["BRAND_RE"], os.environ["REPL"], sys.stdin.read(), flags=re.I))' <<<"$in"
	else
		echo "brand-scrub: need perl or python3 for replacement" >&2
		return 1
	fi
}

# bs_matches <text> — does this text carry a brand? Exit status only.
bs_matches() {
	local restore=0
	shopt -q nocasematch || restore=1
	shopt -s nocasematch
	local rc=1
	[[ "$1" =~ $BS_BRAND_RE ]] && rc=0
	(( restore )) && shopt -u nocasematch
	return $rc
}

# bs_scrub_body <text>
# Deletes whole lines carrying a brand, but only within the first and last
# BS_BODY_SCAN_WINDOW lines. Sets BS_SCRUBBED_COUNT to the number deleted.
# Always succeeds: a false positive costs a line, never a run.
bs_scrub_body() {
	local window="${BS_BODY_SCAN_WINDOW:-${HOOK_BODY_SCAN_WINDOW:-10}}"
	local lines=() line
	while IFS= read -r line || [[ -n "$line" ]]; do
		lines+=( "$line" )
	done <<< "$1"

	local restore=0
	shopt -q nocasematch || restore=1
	shopt -s nocasematch

	local kept=() n=${#lines[@]} i
	BS_SCRUBBED_COUNT=0
	for (( i = 0; i < n; i++ )); do
		if (( i < window || i >= n - window )) && [[ "${lines[$i]}" =~ $BS_BRAND_RE ]]; then
			BS_SCRUBBED_COUNT=$(( BS_SCRUBBED_COUNT + 1 ))
			continue
		fi
		kept+=( "${lines[$i]}" )
	done

	(( restore )) && shopt -u nocasematch

	# `set -u` guard: printf over an empty array is an unbound expansion, and
	# an all-brand body is exactly the case that produces one.
	if (( ${#kept[@]} > 0 )); then
		printf '%s\n' "${kept[@]}"
	fi
	return 0
}

# bs_scrub_title <seed> <text>
# A title is SUBSTITUTED, never deleted -- a PR with no title is not a PR, the
# same reason the commit subject line has always been substituted rather than
# swept. Returns the text unchanged when it carries no brand.
bs_scrub_title() {
	local seed="$1" text="$2"
	if bs_matches "$text"; then
		bs_replace_ci "$(bs_pick_phrase "$seed")" "$text"
	else
		printf '%s\n' "$text"
	fi
}

# bs_report_scrub <before> [what]
# One line to stderr when the scrub actually removed something.
#
# TAKES THE *BEFORE* TEXT AND RE-SWEEPS IT, rather than diffing before against
# after. Two earlier shapes were both wrong:
#
#   - Reading BS_SCRUBBED_COUNT back from the caller is broken by construction,
#     because every call site spells it `X="$(bs_scrub_body "$X")"` and command
#     substitution is a SUBSHELL -- the count is set in the child and the parent
#     sees an unbound variable under `set -u`.
#   - Diffing the line COUNTS of before and after over-reports, because `$( )`
#     strips trailing newlines: sweeping the last line of a body that ends
#     "prose\n\nURL" leaves "prose\n\n", which comes back as one line, and the
#     honest answer "removed 1" prints as "removed 2".
#
# Calling bs_scrub_body directly -- a plain call with a redirect, NOT a
# substitution -- runs it in this very shell, so BS_SCRUBBED_COUNT is exactly
# the number of lines the real sweep removed. A project that argues this hard
# about what its numbers mean should not print one it cannot defend.
bs_report_scrub() {
	local before="$1" what="${2:-text}"
	bs_scrub_body "$before" >/dev/null
	if (( ${BS_SCRUBBED_COUNT:-0} > 0 )); then
		echo "🧽 brand scrub: removed $BS_SCRUBBED_COUNT line(s) from $what" >&2
	fi
	return 0
}
