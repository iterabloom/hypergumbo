#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Failover-aware git status shim.
#
# During CI failover, `git status` reports "ahead of origin/dev" which is
# misleading — origin is stale and the authoritative remote is selfh.
# This shim installs a shell function wrapper that prints a warning banner
# before git status output when CI_FAILOVER_ACTIVE is present.
#
# Usage (from other scripts):
#   source scripts/lib/failover-git-shim.sh
#   failover_shim_install   # writes ~/.bash_aliases entry
#   failover_shim_uninstall # removes it

SHIM_MARKER_BEGIN="# --- failover-git-shim BEGIN ---"
SHIM_MARKER_END="# --- failover-git-shim END ---"

SHIM_BODY='# --- failover-git-shim BEGIN ---
# Installed by ci-failover engage; removed by ci-failover disengage-cleanup.
git() {
    if [ "$1" = "status" ] && [ -f "$(command git rev-parse --git-dir 2>/dev/null)/CI_FAILOVER_ACTIVE" ]; then
        echo "** FAILOVER ACTIVE -- '\''ahead/behind origin'\'' is stale. Authoritative remote: selfh **"
        echo ""
    fi
    command git "$@"
}
# --- failover-git-shim END ---'

failover_shim_install() {
	local target="${1:-$HOME/.bash_aliases}"

	# Ensure the file exists
	touch "$target"

	# Remove any existing shim first (idempotent)
	failover_shim_uninstall "$target"

	# Append the shim
	printf '\n%s\n' "$SHIM_BODY" >> "$target"
	echo "   Installed failover git-status shim in $target"
}

failover_shim_uninstall() {
	local target="${1:-$HOME/.bash_aliases}"

	[[ -f "$target" ]] || return 0

	# Remove the shim block (everything between markers, inclusive)
	if grep -qF "$SHIM_MARKER_BEGIN" "$target" 2>/dev/null; then
		local tmp
		tmp=$(mktemp)
		awk -v begin="$SHIM_MARKER_BEGIN" -v end="$SHIM_MARKER_END" '
			$0 == begin { skip=1; next }
			$0 == end   { skip=0; next }
			!skip
		' "$target" > "$tmp"
		# Remove trailing blank lines left by removal
		sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$tmp" 2>/dev/null || true
		mv "$tmp" "$target"
		echo "   Removed failover git-status shim from $target"
	fi
}
