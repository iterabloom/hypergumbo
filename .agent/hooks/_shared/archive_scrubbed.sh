#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# archive_scrubbed.sh — the ONE fail-safe way to write a transcript archive.
#
# Sourced by rotate-on-session-end.sh (2 call sites) and
# launch-transcript-sync.sh (2 call sites). One implementation because this is
# the exact shape that failed review: four hand-rolled `gzip -c src > dest`
# pipelines, each of which had to independently get the failure handling right,
# and none of which did.
#
# WHAT WENT WRONG BEFORE (all demonstrated, twice, by independent reviewers):
#
#   if "$SCRUB" "$src" | gzip -c > "$dest"; then touch -r ...; fi
#   rm -f "$src"          # or: mv -f "$LAST" "$SECOND"
#
# `gzip -c <readable file>` essentially cannot fail, so the unconditional
# `rm`/`mv` after it was safe for years. Inserting a fallible process in front
# of gzip broke that invariant: when the scrubber failed, gzip had ALREADY
# written a valid gzip stream of empty input to the destination, the `if` guard
# could not un-write it, and the source was then deleted or clobbered. Result:
# a 20-byte archive that passes `gzip -t`, zero surviving copies of the
# transcript, exit 0, and (with `2>/dev/null` on the scrubber) no diagnostics.
# A scrubber that died MID-stream was worse still: a gzip-valid, plausible,
# silently TRUNCATED archive that nothing distinguishes from a good one.
#
# THE CONTRACT HERE
#   1. Write to a temp file, never straight to the destination.
#   2. Validate the temp: non-empty AND `gzip -t` clean AND -- the check that
#      catches truncation -- the same uncompressed line count as the source.
#   3. Only then move it into place.
#   4. Return 0 only if the destination is good. The CALLER must not delete or
#      clobber the source on a non-zero return.
#   5. If scrubbing fails, fall back to archiving UNSCRUBBED rather than
#      failing: losing a transcript is worse than retaining a secret in a
#      gitignored file, and the scrubber's stdout mode is itself fail-safe, so
#      this is belt-and-braces.
#
# Scrubber diagnostics go to stderr deliberately UNSUPPRESSED. The previous
# wiring sent them to /dev/null, which discarded the one line that reports a
# configured secret was skipped -- while the surrounding comment claimed
# failures were "reported loudly".

# shellcheck shell=bash

# Permission contract (INV-todig): archives hold transcript content, so the
# destination must be owner-only even when the caller's umask is permissive.
# shellcheck source=/dev/null
. "$(dirname -- "${BASH_SOURCE[0]}")/transcript_perms.sh"

# archive_scrubbed <src> <dest.gz> <repo_root>
# Returns 0 iff <dest.gz> is a validated archive of <src>.
archive_scrubbed() {
    local src="$1" dest="$2" repo_root="$3"
    local scrub_py tmp src_lines dest_lines

    scrub_py="$(dirname -- "${BASH_SOURCE[0]}")/scrub_secrets.py"
    tmp="${dest}.partial.$$"

    if [[ ! -s "$src" ]]; then
        echo "archive_scrubbed: source empty or missing: $src" >&2
        return 1
    fi

    # Invoke via python3 rather than relying on the executable bit: a lost +x
    # (fresh clone, restrictive umask, a copy that drops the mode) was one of
    # the four demonstrated ways the old wiring destroyed a transcript.
    if [[ -f "$scrub_py" ]] && command -v python3 >/dev/null 2>&1; then
        python3 "$scrub_py" --repo-root "$repo_root" "$src" | gzip -c > "$tmp"
    else
        echo "archive_scrubbed: scrubber unavailable; archiving UNSCRUBBED" >&2
        gzip -c "$src" > "$tmp"
    fi

    # Validate before letting this near the destination.
    if [[ ! -s "$tmp" ]]; then
        echo "archive_scrubbed: refusing empty archive for $src" >&2
        rm -f "$tmp"
        return 1
    fi
    if ! gzip -t "$tmp" 2>/dev/null; then
        echo "archive_scrubbed: refusing corrupt archive for $src" >&2
        rm -f "$tmp"
        return 1
    fi
    # Line-count equality is what catches a mid-stream death: a truncated
    # archive is still valid gzip, so `gzip -t` alone cannot see it.
    src_lines=$(wc -l < "$src")
    dest_lines=$(gzip -cd "$tmp" | wc -l)
    if [[ "$src_lines" != "$dest_lines" ]]; then
        echo "archive_scrubbed: refusing truncated archive for $src" \
             "($src_lines lines in, $dest_lines out)" >&2
        rm -f "$tmp"
        return 1
    fi

    mv -f "$tmp" "$dest" || { rm -f "$tmp"; return 1; }
    harden_transcript_file "$dest"
    # Preserve the source mtime so `ls -la` still shows when the session ended.
    touch -r "$src" "$dest" 2>/dev/null || true
    return 0
}
