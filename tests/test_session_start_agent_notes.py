# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the session-start ``_append_agent_notes_status`` helper.

The helper is the read-side counterpart to the session-end agent-notes
refresh discipline (recover-state-playbook §"Session-end agent-notes
refresh"). When a fresh session starts, if a non-empty
``agent_notes.json`` exists at
``$HOME/<repo_name>_lab_notebook/guidance_log/agent_notes.json``, the
helper appends a one-line prompt to ``SESSION_START_MESSAGE`` naming
both the notes-file age and the last-session age (mtime of
``.agent/.last_session_transcript.jsonl``) and asking the agent to
prompt the user about loading the notes via
``./scripts/agent-notes --show``.

These tests source the shared bash logic in an isolated tmp HOME so the
helper finds (or doesn't find) a synthetic notes file. They cover:

* Notes file with non-empty ``notes`` → prompt appears alongside the
  mode prompt; both ages render in coarse "X ago" form.
* Notes file present but ``notes`` empty / whitespace → no prompt
  (avoids confusing handoff cues from a cleared notes file).
* Notes file absent → no prompt.
* No ``.last_session_transcript.jsonl`` → notes prompt still fires but
  omits the last-session clause (graceful degradation).
* The prompt text contains the exact ``./scripts/agent-notes --show``
  command so a fresh agent can copy-paste it without inferring it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path


REPO_ROOT_REAL = Path(__file__).parent.parent
SESSION_START_LOGIC = REPO_ROOT_REAL / ".agent" / "hooks" / "_shared" / "session_start_logic.sh"
LOOP_TOGGLE = REPO_ROOT_REAL / "scripts" / "loop-toggle"


def _isolated_repo(tmp_path: Path, *, initial_mode: str = "OFF") -> Path:
    """Build a fake repo root with the minimum on disk for
    session_start_logic.sh to source cleanly."""
    root = tmp_path / "fake-repo"
    root.mkdir()
    (root / "scripts").mkdir()
    shutil.copy(LOOP_TOGGLE, root / "scripts" / "loop-toggle")
    (root / "scripts" / "loop-toggle").chmod(0o755)
    shared = root / ".agent" / "hooks" / "_shared"
    shared.mkdir(parents=True)
    shutil.copy(SESSION_START_LOGIC, shared / "session_start_logic.sh")
    (root / "AUTONOMOUS_MODE.txt").write_text(f"{initial_mode}\n")
    return root


def _write_notes(home: Path, repo_name: str, notes_text: str | None) -> Path:
    """Create the notes file under a fake HOME so the helper finds it.

    ``notes_text=None`` means write the file with a missing ``notes``
    key (regression: the jq filter must default to empty); otherwise
    the value is written verbatim under ``"notes"``.
    """
    notes_dir = home / f"{repo_name}_lab_notebook" / "guidance_log"
    notes_dir.mkdir(parents=True, exist_ok=True)
    notes_path = notes_dir / "agent_notes.json"
    if notes_text is None:
        notes_path.write_text("{}\n")
    else:
        notes_path.write_text(json.dumps({"notes": notes_text}) + "\n")
    return notes_path


def _write_last_transcript(repo_root: Path, *, age_seconds: int = 0) -> Path:
    """Create ``.agent/.last_session_transcript.jsonl`` with mtime
    backdated by ``age_seconds`` so the age clause renders predictably."""
    path = repo_root / ".agent" / ".last_session_transcript.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic transcript\n")
    if age_seconds > 0:
        ts = time.time() - age_seconds
        os.utime(path, (ts, ts))
    return path


def _backdate(path: Path, *, age_seconds: int) -> None:
    """Backdate a file's mtime by ``age_seconds`` so the helper's
    ``_format_time_ago`` produces a deterministic bucket label."""
    ts = time.time() - age_seconds
    os.utime(path, (ts, ts))


def _source_logic(repo_root: Path, fake_home: Path) -> tuple[str, str]:
    """Source session_start_logic.sh in a fresh bash with REPO_ROOT and
    HOME overridden, then dump the two exported vars separated by ASCII
    Record Separator (0x1E) so newlines in the message survive."""
    env = os.environ.copy()
    env["REPO_ROOT"] = str(repo_root)
    env["HOME"] = str(fake_home)
    env.pop("HYPERGUMBO_RESPAWN", None)
    cmd = (
        f'source "{repo_root}/.agent/hooks/_shared/session_start_logic.sh"; '
        'printf "%s\\x1e%s" "$SESSION_START_NEEDS_PROMPT" "$SESSION_START_MESSAGE"'
    )
    result = subprocess.run(
        ["bash", "-c", cmd], capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, (
        f"sourcing failed:\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    parts = result.stdout.split("\x1e", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


# --- Notes present + last-transcript present: full prompt ---


def test_notes_prompt_fires_with_both_ages(tmp_path: Path) -> None:
    """Non-empty notes + recent .last_session_transcript.jsonl → prompt
    names both ages and the agent-notes --show command."""
    repo = _isolated_repo(tmp_path)
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    notes_path = _write_notes(fake_home, repo.name, "Real handoff text.")
    transcript_path = _write_last_transcript(repo, age_seconds=7200)  # 2h
    _backdate(notes_path, age_seconds=1560)  # 26m

    needs, msg = _source_logic(repo, fake_home)

    assert needs == "true"
    assert "agent_notes.json was last updated" in msg
    assert "26m ago" in msg
    assert "last session ended 2h ago" in msg
    assert "./scripts/agent-notes --show" in msg
    # Mode prompt still fires — the helper appends, doesn't replace.
    assert "Autonomous mode is OFF" in msg
    # When the notes prompt is appended onto an existing message, the
    # "ALSO REQUIRED" prefix marks it as a separate item so the agent
    # doesn't treat answering the mode prompt as resolving both.
    assert (
        "ALSO REQUIRED (separate item — do not treat as resolved by handling the prompt above):"
        in msg
    )


def test_notes_prompt_uses_seconds_under_one_minute(tmp_path: Path) -> None:
    """Sub-minute ages render as ``Xs ago`` (boundary case for the
    bucketing helper)."""
    repo = _isolated_repo(tmp_path)
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    _write_notes(fake_home, repo.name, "Fresh.")
    _write_last_transcript(repo, age_seconds=0)

    needs, msg = _source_logic(repo, fake_home)

    assert needs == "true"
    # mtime is "now" (within a second or two of test execution).
    assert "agent_notes.json was last updated" in msg
    # Either ``Xs ago`` or ``just now`` is acceptable for a freshly
    # written file (clock-skew defence in the helper).
    assert ("s ago" in msg or "just now" in msg)


def test_notes_prompt_uses_days_when_old(tmp_path: Path) -> None:
    """Multi-day ages render as ``Xd ago`` (top of the bucketing
    pyramid). Confirms the day branch isn't dead code."""
    repo = _isolated_repo(tmp_path)
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    notes_path = _write_notes(fake_home, repo.name, "Stale handoff.")
    _write_last_transcript(repo, age_seconds=4 * 86400)
    _backdate(notes_path, age_seconds=3 * 86400)

    needs, msg = _source_logic(repo, fake_home)

    assert needs == "true"
    assert "3d ago" in msg
    assert "4d ago" in msg


# --- Notes file present but empty: no prompt ---


def test_notes_prompt_skipped_when_notes_field_empty_string(tmp_path: Path) -> None:
    """Notes file with ``notes=""`` (e.g., after --clear) does NOT fire
    the prompt — there's nothing to load."""
    repo = _isolated_repo(tmp_path)
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    _write_notes(fake_home, repo.name, "")
    _write_last_transcript(repo, age_seconds=3600)

    _, msg = _source_logic(repo, fake_home)

    assert "agent_notes.json was last updated" not in msg
    # Mode prompt still fires (Case 1 OFF).
    assert "Autonomous mode is OFF" in msg


def test_notes_prompt_skipped_when_notes_field_whitespace(tmp_path: Path) -> None:
    """Whitespace-only notes are treated as empty (defends against an
    --append-with-empty-string corner case)."""
    repo = _isolated_repo(tmp_path)
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    _write_notes(fake_home, repo.name, "   \n\t  ")
    _write_last_transcript(repo, age_seconds=3600)

    _, msg = _source_logic(repo, fake_home)

    assert "agent_notes.json was last updated" not in msg


def test_notes_prompt_skipped_when_notes_key_missing(tmp_path: Path) -> None:
    """Notes file written without the ``notes`` key (e.g., legacy
    schema) is treated as empty via the jq filter's empty-string
    default."""
    repo = _isolated_repo(tmp_path)
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    _write_notes(fake_home, repo.name, None)  # writes ``{}``
    _write_last_transcript(repo, age_seconds=3600)

    _, msg = _source_logic(repo, fake_home)

    assert "agent_notes.json was last updated" not in msg


# --- Notes file absent ---


def test_notes_prompt_skipped_when_file_absent(tmp_path: Path) -> None:
    """No notes file at all → no prompt. Default state on a brand-new
    repo where no agent has ever run."""
    repo = _isolated_repo(tmp_path)
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    # Deliberately do NOT call _write_notes.
    _write_last_transcript(repo, age_seconds=3600)

    _, msg = _source_logic(repo, fake_home)

    assert "agent_notes.json was last updated" not in msg


# --- Last-transcript absent: graceful degradation ---


def test_notes_prompt_omits_last_session_clause_when_transcript_absent(
    tmp_path: Path,
) -> None:
    """Notes file present but no .last_session_transcript.jsonl → the
    prompt still fires, but the ", last session ended ..." clause is
    omitted (no synthetic age for a missing signal)."""
    repo = _isolated_repo(tmp_path)
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    _write_notes(fake_home, repo.name, "Notes without transcript.")
    # Deliberately do NOT write the last_transcript.

    _, msg = _source_logic(repo, fake_home)

    assert "agent_notes.json was last updated" in msg
    assert "last session ended" not in msg


# --- Case 4 (mode active) still appends notes prompt ---


def test_notes_prompt_fires_in_case_4_mode_active(tmp_path: Path) -> None:
    """When autonomous mode is already set with the current PID owning
    the lock (Case 4), the notes prompt still fires — fresh sessions
    that resume autonomous mode benefit from prior-session context."""
    repo = _isolated_repo(tmp_path, initial_mode="DEEP")
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    notes_path = _write_notes(fake_home, repo.name, "Real handoff text.")
    _backdate(notes_path, age_seconds=900)

    # No PID in AUTONOMOUS_MODE.txt — _STORED_PID will be empty so the
    # ancestor check in Case 3 doesn't fire. Falls through to Case 4.
    needs, msg = _source_logic(repo, fake_home)

    assert needs == "true"  # The notes prompt itself flips this on.
    assert "agent_notes.json was last updated" in msg
    # Mode prompt is NOT in the message (mode is DEEP, not OFF).
    assert "Autonomous mode is OFF" not in msg
    # Case 4 starts with an empty SESSION_START_MESSAGE before the notes
    # helper runs, so the "ALSO REQUIRED" prefix is not added — the notes
    # prompt is the sole item, not an appendage to a prior one.
    assert "ALSO REQUIRED" not in msg
