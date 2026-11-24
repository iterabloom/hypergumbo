#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# secret-args.sh — hand a credential to git or curl without putting it on a
# command line.
#
# WHY. A process's argument list is public on the machine: `ps`, `pgrep -a`
# and /proc/<pid>/cmdline show it to every local user for as long as the
# process lives, and anything that samples the process list records it. Both
# ways these scripts used to pass a token put it there:
#
#   git -c credential.helper="!f() { echo username=$USER; echo password=$TOKEN; }; f" push ...
#   curl -H "Authorization: token $TOKEN" ...
#
# (WI-jizos: seen when a process listing taken during a tracker auto-sync
# printed the push command, token included, into an agent transcript.)
#
# HOW. A process's ENVIRONMENT and its standard input are readable only by its
# owner (and root), so the credential travels there instead:
#
#   git_with_token  the helper git is given reads two environment variables
#                   set for that one git process; the command line holds the
#                   variables' NAMES. git runs the helper through `sh -c`,
#                   whose argument is the same text, names only.
#   curl_with_secret_headers
#                   the secret headers are written to curl's standard input
#                   and read with `-H @-` (curl 7.55.0 and later). Bash writes
#                   a here-string itself; no process is started for it.
#
# Sourced by forgejo-api.sh (so by auto-pr, merge-pr, contribute and ci-debug)
# and by scripts/release. `tests/test_token_not_on_command_line.py` runs both
# functions against stand-ins that record their own arguments, and scans the
# scripts for the two old spellings.

# shellcheck shell=bash

# git_with_token USER TOKEN GIT-ARGS...
#   Run `git GIT-ARGS...` with a credential helper that answers USER / TOKEN.
#   Exit status and output are git's.
git_with_token() {
	local _gwt_user="$1" _gwt_token="$2"
	shift 2
	# Single quotes on purpose: the variables are expanded by the shell git
	# starts for the helper, not here.
	# shellcheck disable=SC2016
	HG_GIT_CRED_USER="$_gwt_user" HG_GIT_CRED_TOKEN="$_gwt_token" \
		git -c credential.helper='!f() { echo "username=$HG_GIT_CRED_USER"; echo "password=$HG_GIT_CRED_TOKEN"; }; f' "$@"
}

# curl_with_secret_headers 'Name: value'... -- CURL-ARGS...
#   Run `curl CURL-ARGS...` with the headers before `--` sent on standard
#   input. Exit status and output are curl's. curl's own standard input is
#   therefore taken: a caller that feeds a request body with `-d @-` cannot
#   use this.
curl_with_secret_headers() {
	local _cwsh_headers="" _cwsh_header
	while [[ $# -gt 0 && "$1" != "--" ]]; do
		_cwsh_header="$1"
		_cwsh_headers+="${_cwsh_header}"$'\n'
		shift
	done
	shift  # the --
	curl -H @- "$@" <<<"${_cwsh_headers%$'\n'}"
}
