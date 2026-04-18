# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for WI-sakod: respawn-aware session-start hook logic.

When the agent-supervisor daemon (WI-razub / WI-rofuv) spawns a fresh CLI
to replace a stuck session, it sets ``HYPERGUMBO_RESPAWN=1`` on the new
tmux session. The vendor session-start hooks (via the shared
``session_start_logic.sh``) must branch on that env var to:

* auto-enable autonomous mode for this session per ``autonomous_intent.txt``
  (narrow-write, so the project-level intent stays untouched), and
* emit the generic seed prompt that kicks the agent into work —
  preserving forward-march momentum across the respawn boundary.

Guards against the opposite direction too: ``HYPERGUMBO_RESPAWN=1`` with
intent=OFF must NOT force autonomous mode on; we fall through to the
existing OFF human-prompt path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT_REAL = Path(__file__).parent.parent
SESSION_START_LOGIC = REPO_ROOT_REAL / ".agent" / "hooks" / "_shared" / "session_start_logic.sh"
LOOP_TOGGLE = REPO_ROOT_REAL / "scripts" / "loop-toggle"


def _isolated_repo(tmp_path: Path, initial_mode: str = "OFF") -> Path:
    """Build a minimal fake repo with just enough on disk for the shared
    session_start_logic.sh to run — it needs AUTONOMOUS_MODE.txt, the
    loop-toggle script, and (for the respawn branch) autonomous_intent.txt."""
    root = tmp_path / "fake-repo"
    root.mkdir()
    # Scripts directory with loop-toggle.
    (root / "scripts").mkdir()
    shutil.copy(LOOP_TOGGLE, root / "scripts" / "loop-toggle")
    (root / "scripts" / "loop-toggle").chmod(0o755)
    # Hook shared directory with session_start_logic.sh.
    shared = root / ".agent" / "hooks" / "_shared"
    shared.mkdir(parents=True)
    shutil.copy(SESSION_START_LOGIC, shared / "session_start_logic.sh")
    # Seed mode + loop sentinel dirs so loop-toggle's writes work.
    (root / "AUTONOMOUS_MODE.txt").write_text(f"{initial_mode}\n")
    return root


def _source_logic(
    repo_root: Path, *, respawn: bool = False,
) -> tuple[str, str]:
    """Source session_start_logic.sh in a fresh bash with REPO_ROOT set
    and (optionally) HYPERGUMBO_RESPAWN=1, then echo the two exported
    vars so the test can assert on them.

    Returns (needs_prompt, message).
    """
    env = os.environ.copy()
    env["REPO_ROOT"] = str(repo_root)
    if respawn:
        env["HYPERGUMBO_RESPAWN"] = "1"
    else:
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
    # Parts split on ASCII Record Separator (0x1E) so SESSION_START_MESSAGE
    # can itself contain colons, spaces, and newlines without ambiguity.
    parts = result.stdout.split("\x1e", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _read_mode(repo_root: Path) -> str:
    """Return the first whitespace-delimited token of line 1."""
    lines = (repo_root / "AUTONOMOUS_MODE.txt").read_text().splitlines()
    return lines[0].split()[0] if lines else ""


def _read_intent(repo_root: Path) -> str:
    intent_path = repo_root / "autonomous_intent.txt"
    return intent_path.read_text().strip() if intent_path.exists() else ""


# --- Respawn + intent=DEEP: auto-enable + seed prompt ---


def test_respawn_with_deep_intent_auto_enables(tmp_path: Path) -> None:
    """HYPERGUMBO_RESPAWN=1 + intent=DEEP → session mode flipped to DEEP,
    seed prompt emitted."""
    repo = _isolated_repo(tmp_path, initial_mode="OFF")
    (repo / "autonomous_intent.txt").write_text("DEEP\n")
    needs, msg = _source_logic(repo, respawn=True)
    assert needs == "true"
    assert "familiarize yourself with this repo" in msg
    assert "set autonomous mode to DEEP" in msg
    # Session mode was flipped to DEEP.
    assert _read_mode(repo) == "DEEP"
    # Intent untouched.
    assert _read_intent(repo) == "DEEP"


def test_respawn_with_broad_intent_auto_enables(tmp_path: Path) -> None:
    """HYPERGUMBO_RESPAWN=1 + intent=BROAD → session mode flipped to BROAD,
    seed prompt still mentions DEEP (generic by design)."""
    repo = _isolated_repo(tmp_path, initial_mode="OFF")
    (repo / "autonomous_intent.txt").write_text("BROAD\n")
    needs, msg = _source_logic(repo, respawn=True)
    assert needs == "true"
    assert "familiarize yourself with this repo" in msg
    assert _read_mode(repo) == "BROAD"
    assert _read_intent(repo) == "BROAD"


def test_respawn_is_case_insensitive_for_intent_value(tmp_path: Path) -> None:
    """Intent file may be lower/mixed case (loop-toggle normalizes on
    write, but users may hand-edit). The respawn branch must handle it."""
    repo = _isolated_repo(tmp_path, initial_mode="OFF")
    (repo / "autonomous_intent.txt").write_text("deep")
    needs, msg = _source_logic(repo, respawn=True)
    assert needs == "true"
    assert _read_mode(repo) == "DEEP"


# --- Respawn + intent=OFF: fall through to human prompt ---


def test_respawn_with_off_intent_falls_through_to_off_prompt(tmp_path: Path) -> None:
    """HYPERGUMBO_RESPAWN=1 + intent=OFF must NOT force autonomous on;
    the existing OFF human-prompt path takes over."""
    repo = _isolated_repo(tmp_path, initial_mode="OFF")
    (repo / "autonomous_intent.txt").write_text("OFF\n")
    needs, msg = _source_logic(repo, respawn=True)
    assert needs == "true"
    # Generic "ask the user" message — NOT the respawn seed prompt.
    assert "ask the user which mode" in msg
    assert "familiarize yourself" not in msg
    # Session mode unchanged.
    assert _read_mode(repo) == "OFF"


def test_respawn_without_intent_file_falls_through(tmp_path: Path) -> None:
    """No autonomous_intent.txt at all → treat as OFF; fall through to
    the normal OFF prompt rather than silently auto-enabling."""
    repo = _isolated_repo(tmp_path, initial_mode="OFF")
    assert not (repo / "autonomous_intent.txt").exists()
    needs, msg = _source_logic(repo, respawn=True)
    assert needs == "true"
    assert "ask the user which mode" in msg


def test_respawn_with_garbage_intent_falls_through(tmp_path: Path) -> None:
    """Malformed intent value normalizes to OFF; fall through."""
    repo = _isolated_repo(tmp_path, initial_mode="OFF")
    (repo / "autonomous_intent.txt").write_text("unicorn")
    needs, msg = _source_logic(repo, respawn=True)
    assert needs == "true"
    assert "ask the user which mode" in msg
    assert _read_mode(repo) == "OFF"  # Unchanged.


# --- No respawn env var: existing behavior ---


def test_no_respawn_env_uses_existing_off_prompt(tmp_path: Path) -> None:
    """Without HYPERGUMBO_RESPAWN, existing OFF-mode behavior is
    preserved — human is prompted for mode selection."""
    repo = _isolated_repo(tmp_path, initial_mode="OFF")
    (repo / "autonomous_intent.txt").write_text("DEEP\n")
    needs, msg = _source_logic(repo, respawn=False)
    assert needs == "true"
    # NOT the respawn seed prompt — intent is not consulted when
    # HYPERGUMBO_RESPAWN is unset.
    assert "familiarize yourself" not in msg
    assert "ask the user which mode" in msg
    # Session mode unchanged — we did NOT call loop-toggle.
    assert _read_mode(repo) == "OFF"


def test_no_respawn_env_with_broad_mode_does_not_prompt(tmp_path: Path) -> None:
    """Pre-existing BROAD mode + no respawn → no prompt (existing
    behavior; the human already set it)."""
    repo = _isolated_repo(tmp_path, initial_mode="BROAD")
    needs, msg = _source_logic(repo, respawn=False)
    # No prompt because autonomous mode is already active.
    assert needs == "false"
    assert msg == ""


# --- Regression guards ---


def test_respawn_never_writes_intent_file(tmp_path: Path) -> None:
    """The respawn branch must NEVER touch autonomous_intent.txt — that
    file is owned by the human + supervisor, and the session-start hook's
    job is strictly to mirror intent into the session mode."""
    repo = _isolated_repo(tmp_path, initial_mode="OFF")
    (repo / "autonomous_intent.txt").write_text("BROAD\n")
    intent_mtime_before = (repo / "autonomous_intent.txt").stat().st_mtime
    _source_logic(repo, respawn=True)
    intent_mtime_after = (repo / "autonomous_intent.txt").stat().st_mtime
    # File content unchanged.
    assert (repo / "autonomous_intent.txt").read_text().strip() == "BROAD"
    # And mtime unchanged (belt + suspenders).
    assert intent_mtime_before == intent_mtime_after


def test_respawn_env_wrong_value_is_ignored(tmp_path: Path) -> None:
    """HYPERGUMBO_RESPAWN values other than exactly '1' must NOT trigger
    the respawn branch. Prevents accidental activation from env var
    leakage (e.g., somebody writes HYPERGUMBO_RESPAWN=true)."""
    repo = _isolated_repo(tmp_path, initial_mode="OFF")
    (repo / "autonomous_intent.txt").write_text("DEEP\n")
    env = os.environ.copy()
    env["REPO_ROOT"] = str(repo)
    env["HYPERGUMBO_RESPAWN"] = "true"  # Wrong value.
    cmd = (
        f'source "{repo}/.agent/hooks/_shared/session_start_logic.sh"; '
        'printf "%s" "$SESSION_START_MESSAGE"'
    )
    result = subprocess.run(
        ["bash", "-c", cmd], capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0
    # Falls through to the regular OFF-mode path (since AUTONOMOUS_MODE.txt
    # starts as OFF). Not the respawn seed prompt.
    assert "familiarize yourself" not in result.stdout
    # Session mode unchanged.
    assert _read_mode(repo) == "OFF"


# --- Every vendor session-start hook surfaces the message ---


VENDOR_SESSION_START_HOOKS = [
    ".agent/hooks/claude-code/session-start.sh",
    ".agent/hooks/codex-cli/session-start.sh",
    ".agent/hooks/cursor/session-start.sh",
    ".agent/hooks/gemini-cli/session-start.sh",
]


@pytest.mark.parametrize("hook_path", VENDOR_SESSION_START_HOOKS)
def test_vendor_hook_sources_shared_session_start_logic(hook_path: str) -> None:
    """Every vendor session-start hook must source the shared logic so
    the respawn branch takes effect across all four CLIs."""
    content = (REPO_ROOT_REAL / hook_path).read_text()
    assert "session_start_logic.sh" in content, (
        f"{hook_path} must source _shared/session_start_logic.sh so the "
        f"respawn branch applies to this vendor."
    )


@pytest.mark.parametrize("hook_path", VENDOR_SESSION_START_HOOKS)
def test_vendor_hook_surfaces_session_start_message(hook_path: str) -> None:
    """Every vendor hook must still emit SESSION_START_MESSAGE when the
    respawn branch set it — guards against a vendor hook bypassing the
    shared output path."""
    content = (REPO_ROOT_REAL / hook_path).read_text()
    assert "SESSION_START_MESSAGE" in content, (
        f"{hook_path} must reference SESSION_START_MESSAGE to emit the "
        f"respawn seed prompt (or the existing human-prompt text)."
    )
