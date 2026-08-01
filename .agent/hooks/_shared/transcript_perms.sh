#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# transcript_perms.sh — the ONE home for the transcript permission contract
# (INV-todig): a secret's copy is never more readable than its origin.
#
# .env is mode 0600 and transcripts republish command output verbatim
# (a plain `git remote -v` banks a credential-bearing URL on disk), so every
# transcript-content file is 0600 and every archive directory 0700. The
# pre-fix pipeline created 664 files and a 2775 setgid archive dir purely by
# inheriting the process umask (002 here) — nothing ever decided that.
#
# Sourced by every shell writer in the pipeline: sync-transcript.sh,
# rotate-on-session-end.sh, launch-transcript-sync.sh, archive_scrubbed.sh.
# The wiring is parity-tested by tests/test_transcript_permissions.py, so a
# writer that drops the source line goes red. Python write sites
# (filter-transcript.py, on_transcript_change.py) enforce the same contract
# with os.chmod — self-healing on every write, because months of 664 files
# pre-date this contract and mv/scrub faithfully preserve whatever mode they
# find.
#
# All helpers are no-fail: they run under `set -euo pipefail` callers where
# a chmod on a vanished file must never abort a session end.

# shellcheck shell=bash

# Owner-only default for every file this PROCESS creates from here on.
# Deliberately a umask rather than per-site chmod: a write site added later
# is covered by default instead of by somebody remembering.
harden_transcript_umask() {
    umask 077
}

# Idempotent tightening for files that may PRE-EXIST at looser modes.
harden_transcript_file() {
    local p
    for p in "$@"; do
        if [[ -f "$p" ]]; then
            chmod 600 "$p" 2>/dev/null || true
        fi
    done
    return 0
}

# Ditto for directories. The explicit g-s matters: GNU chmod PRESERVES a
# directory's setgid bit under numeric modes, so a plain `chmod 700` would
# leave the legacy 2775 .archived-transcripts/ still propagating its group
# onto every new subdir (measured live, 2026-08-01).
harden_transcript_dir() {
    local p
    for p in "$@"; do
        if [[ -d "$p" ]]; then
            chmod 700 "$p" 2>/dev/null || true
            chmod g-s "$p" 2>/dev/null || true
        fi
    done
    return 0
}
