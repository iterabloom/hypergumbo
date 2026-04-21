# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the WI-sipov heartbeat helper ``touch_heartbeat.sh``.

The helper is sourced by every vendor's per-turn hook and (eventually) by
the shared stop logic + long-running wrappers. Its contract is:

1. Touch ``<HEARTBEAT_DIR>/<session_id>.heartbeat`` when called with a
   non-empty session_id.
2. No-op + return 0 when session_id is empty.
3. No-op + return 0 when the directory cannot be created (read-only
   ``$HOME``, permission denied, etc.) — hooks must never fail because
   the supervisor's bookkeeping file cannot be written.
4. Create the directory if missing.

The supervisor daemon (WI-rofuv) reads heartbeat mtimes for telemetry
ONLY — pane-byte delta is the load-bearing signal. So this helper must
be fast and silent, never interrupting a running hook.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest


REPO_ROOT_REAL = Path(__file__).parent.parent
TOUCH_HEARTBEAT = REPO_ROOT_REAL / ".agent" / "hooks" / "_shared" / "touch_heartbeat.sh"


def _source_and_call(
    heartbeat_dir: Path | None,
    session_id: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Source the helper in a fresh bash, call ``touch_heartbeat``, return
    the CompletedProcess."""
    env = os.environ.copy()
    if heartbeat_dir is not None:
        env["HEARTBEAT_DIR"] = str(heartbeat_dir)
    if extra_env:
        env.update(extra_env)
    cmd = (
        f'source "{TOUCH_HEARTBEAT}" && touch_heartbeat "{session_id}"; '
        f'echo "exit=$?"'
    )
    return subprocess.run(
        ["bash", "-c", cmd], capture_output=True, text=True, env=env,
    )


# --- Happy path ---


def test_creates_heartbeat_file_with_sanitized_session_id(tmp_path: Path) -> None:
    """``touch_heartbeat SID`` creates ``<dir>/SID.heartbeat`` and exits 0."""
    hb_dir = tmp_path / "agent-supervisor"
    result = _source_and_call(hb_dir, "claude-abc-123")
    assert result.returncode == 0
    assert "exit=0" in result.stdout
    target = hb_dir / "claude-abc-123.heartbeat"
    assert target.exists()


def test_updates_mtime_on_repeated_call(tmp_path: Path) -> None:
    """Calling ``touch_heartbeat`` twice must update the file's mtime —
    that's the whole point; the supervisor reads mtime to detect
    activity."""
    hb_dir = tmp_path / "agent-supervisor"
    _source_and_call(hb_dir, "sess-42")
    target = hb_dir / "sess-42.heartbeat"
    first_mtime = target.stat().st_mtime
    # Backdate the file so the second touch has room to change mtime,
    # even on filesystems with coarse mtime resolution.
    old_time = first_mtime - 5
    os.utime(target, (old_time, old_time))
    time.sleep(0.1)  # Cheap insurance for any remaining coarseness.
    _source_and_call(hb_dir, "sess-42")
    second_mtime = target.stat().st_mtime
    assert second_mtime > old_time


def test_creates_heartbeat_dir_if_missing(tmp_path: Path) -> None:
    """Missing heartbeat directory must be created on first call."""
    hb_dir = tmp_path / "nested" / "agent-supervisor"
    assert not hb_dir.exists()
    result = _source_and_call(hb_dir, "sess-abc")
    assert result.returncode == 0
    assert hb_dir.is_dir()
    assert (hb_dir / "sess-abc.heartbeat").exists()


# --- Graceful no-op paths ---


def test_empty_session_id_is_noop(tmp_path: Path) -> None:
    """Empty session_id → return 0 and do nothing (no stray files)."""
    hb_dir = tmp_path / "agent-supervisor"
    result = _source_and_call(hb_dir, "")
    assert result.returncode == 0
    assert "exit=0" in result.stdout
    # No files should have been created.
    assert not hb_dir.exists() or not any(hb_dir.iterdir())


def test_unwritable_heartbeat_dir_is_silent(tmp_path: Path) -> None:
    """When the heartbeat dir can't be created, the helper must return 0
    and emit nothing to stderr — hooks must never fail because of
    heartbeat bookkeeping.

    Uses an ENOTDIR precondition (parent is a regular file, not a
    directory) rather than a chmod-based one, because CI runs as root
    and root bypasses DAC write permissions — `chmod 0o555` on a
    directory does NOT prevent root from writing to it, so the
    permission-denied failure mode couldn't be reproduced in CI.
    `mkdir -p <regular_file>/subdir` fails with ENOTDIR regardless of
    uid, so the test exercises the intended swallow-the-error branch
    on every runner.
    """
    parent_file = tmp_path / "not-a-dir"
    parent_file.write_text("")  # Regular file, not a directory.
    hb_dir = parent_file / "agent-supervisor"  # mkdir -p → ENOTDIR.
    result = _source_and_call(hb_dir, "sess-notdir")
    assert result.returncode == 0, (
        f"helper must not error: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    # No stderr noise that would pollute the host hook's stderr.
    assert result.stderr == "", (
        f"helper must not emit stderr noise; got {result.stderr!r}"
    )
    assert not hb_dir.exists()


def test_default_heartbeat_dir_when_env_unset(tmp_path: Path) -> None:
    """When ``HEARTBEAT_DIR`` is unset, the helper uses
    ``$HOME/hypergumbo_lab_notebook/agent-supervisor/``. We point HOME
    at tmp_path to avoid polluting the real home during tests."""
    cmd = (
        f'unset HEARTBEAT_DIR; '
        f'source "{TOUCH_HEARTBEAT}" && touch_heartbeat "sess-home"; '
        f'echo "exit=$?"'
    )
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env.pop("HEARTBEAT_DIR", None)
    result = subprocess.run(
        ["bash", "-c", cmd], capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0
    expected = tmp_path / "hypergumbo_lab_notebook" / "agent-supervisor" / "sess-home.heartbeat"
    assert expected.exists()


# --- Integration with vendor per-turn hooks ---


VENDOR_PER_TURN_HOOKS = [
    ".agent/hooks/claude-code/post-tool-use-transcript.sh",
    ".agent/hooks/codex-cli/post-tool-use-transcript.sh",
    ".agent/hooks/cursor/post-tool-use-transcript.sh",
    ".agent/hooks/gemini-cli/before-model-transcript.sh",
]


@pytest.mark.parametrize("hook_path", VENDOR_PER_TURN_HOOKS)
def test_each_vendor_per_turn_hook_sources_heartbeat_helper(hook_path: str) -> None:
    """Regression guard: every per-turn hook must source the heartbeat
    helper and call it after resolving SESSION_ID. If someone factors
    that out without replacing it, this test fires."""
    content = (REPO_ROOT_REAL / hook_path).read_text()
    assert "touch_heartbeat.sh" in content, (
        f"{hook_path} no longer sources touch_heartbeat.sh"
    )
    assert 'touch_heartbeat "$SESSION_ID"' in content, (
        f"{hook_path} no longer calls touch_heartbeat"
    )


@pytest.mark.parametrize("hook_path", VENDOR_PER_TURN_HOOKS)
def test_heartbeat_call_fires_after_session_id_resolved(hook_path: str) -> None:
    """The heartbeat call must come AFTER the SESSION_ID extraction and
    empty-SID guard — calling it with an empty SID is safe (per
    ``test_empty_session_id_is_noop``), but it's wasteful work that
    belongs after the guard. Cheap structural check: the call site must
    appear after the `if [[ -z "$SESSION_ID" ]]` block."""
    content = (REPO_ROOT_REAL / hook_path).read_text()
    guard_idx = content.find('if [[ -z "$SESSION_ID" ]]')
    heartbeat_idx = content.find('touch_heartbeat "$SESSION_ID"')
    assert guard_idx >= 0, f"{hook_path} missing SESSION_ID empty guard"
    assert heartbeat_idx >= 0, f"{hook_path} missing touch_heartbeat call"
    assert heartbeat_idx > guard_idx, (
        f"{hook_path}: touch_heartbeat must be called AFTER the "
        f"empty-SESSION_ID guard; got heartbeat at {heartbeat_idx}, "
        f"guard at {guard_idx}"
    )
