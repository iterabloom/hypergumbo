#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# end-session-transcript.sh — the ONE transcript entry point for a vendor's
# session-end hook. Returns in well under a second whatever the transcript
# sizes are.
#
# Usage: end-session-transcript.sh <REPO_ROOT> <SESSION_ID>
#
# Three steps, in an order that matters:
#   1. Queue the session for rotation (an empty file; see
#      transcript_rotation_queue.sh). FIRST, so that from here on a
#      concurrent sweep sees either the watcher's PID file or the queue
#      entry and never takes this session's transcript for a stray.
#   2. Kill this session's watcher (kill-transcript-sync.sh).
#   3. Hand the rotation to a detached worker
#      (rotate-on-session-end.sh), which archives, renames and scrubs under
#      the rotation lock.
#
# A session-end hook must NOT call rotate-on-session-end.sh itself: that
# script compresses and scrubs whole transcripts and outlasts any hook
# budget once they are large. tests/test_transcript_hook_budget.py holds
# every vendor hook to this entry point.
#
# Every step is best effort: a failure here must never fail a session end.

set -uo pipefail

REPO_ROOT="${1:?REPO_ROOT is required}"
SESSION_ID="${2:?SESSION_ID is required}"
AGENT_DIR="$REPO_ROOT/.agent"
SCRIPT_DIR="$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=/dev/null
. "$SCRIPT_DIR/transcript_perms.sh"
harden_transcript_umask
# shellcheck source=/dev/null
. "$SCRIPT_DIR/transcript_rotation_queue.sh"

mkdir -p "$AGENT_DIR" 2>/dev/null || true

# A failed enqueue is survivable: the worker below is handed the session id
# and rotates it without an entry.
rotation_enqueue "$AGENT_DIR" "$SESSION_ID" || \
    echo "warn: could not queue $SESSION_ID for rotation" >&2

bash "$SCRIPT_DIR/kill-transcript-sync.sh" "$REPO_ROOT" "$SESSION_ID" || \
    echo "warn: kill-transcript-sync.sh failed for $SESSION_ID" >&2

transcript_detach "$AGENT_DIR" \
    bash "$SCRIPT_DIR/rotate-on-session-end.sh" "$REPO_ROOT" "$SESSION_ID"

exit 0
