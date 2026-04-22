# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the intent/mode split in ``scripts/loop-toggle`` (WI-pobon).

The split introduces two narrow flag forms so the circuit-breaker trip path
can reset the current session mode without flipping the project-level
``autonomous_intent.txt`` that the agent-supervisor daemon (WI-razub)
consults:

* ``--set-intent MODE``        — writes ``autonomous_intent.txt`` only.
* ``--set-session-mode MODE``  — writes ``AUTONOMOUS_MODE.txt`` only.

The existing bareword form (``./scripts/loop-toggle DEEP``) keeps its old
behavior of writing BOTH files, preserving today's human UX where a single
mode change updates both the project intent and the current session.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT_REAL = Path(__file__).parent.parent
LOOP_TOGGLE = REPO_ROOT_REAL / "scripts" / "loop-toggle"


def _isolated_repo(tmp_path: Path, initial_mode: str = "OFF") -> Path:
    """Build a tiny fake repo containing only what ``loop-toggle`` touches."""
    fake_root = tmp_path / "fake-repo"
    fake_root.mkdir()
    (fake_root / "scripts").mkdir()
    shutil.copy(LOOP_TOGGLE, fake_root / "scripts" / "loop-toggle")
    (fake_root / "scripts" / "loop-toggle").chmod(0o755)
    (fake_root / ".agent").mkdir()
    (fake_root / "AUTONOMOUS_MODE.txt").write_text(f"{initial_mode}\n")
    return fake_root


def _run(fake_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(fake_root / "scripts" / "loop-toggle"), *args],
        capture_output=True, text=True, cwd=str(fake_root),
    )


def _mode_file_first_token(fake_root: Path) -> str:
    """Return the first whitespace-delimited token on line 1 of AUTONOMOUS_MODE.txt."""
    return (fake_root / "AUTONOMOUS_MODE.txt").read_text().splitlines()[0].split()[0]


def _intent_file(fake_root: Path) -> str:
    """Return autonomous_intent.txt contents stripped, or '' if the file is absent."""
    intent_path = fake_root / "autonomous_intent.txt"
    if not intent_path.exists():
        return ""
    return intent_path.read_text().strip()


# --- --set-intent: writes intent only, does not touch session mode ---


@pytest.mark.parametrize("mode", ["OFF", "BROAD", "DEEP", "off", "broad", "deep"])
def test_set_intent_writes_only_intent_file(tmp_path: Path, mode: str) -> None:
    """``--set-intent MODE`` writes the normalized value to ``autonomous_intent.txt``
    and leaves ``AUTONOMOUS_MODE.txt`` alone."""
    fake_root = _isolated_repo(tmp_path, initial_mode="DEEP")
    result = _run(fake_root, "--set-intent", mode)
    assert result.returncode == 0, (
        f"--set-intent {mode!r} failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert _intent_file(fake_root) == mode.upper()
    # Session mode must NOT be touched — it should remain whatever it was
    # when we seeded the fake repo.
    assert _mode_file_first_token(fake_root) == "DEEP"


def test_set_intent_does_not_touch_loop_sentinel(tmp_path: Path) -> None:
    """``--set-intent`` must not create or remove the LOOP sentinel."""
    fake_root = _isolated_repo(tmp_path)
    # Start with no sentinel files.
    assert not (fake_root / ".agent" / "LOOP").exists()
    assert not (fake_root / ".agent" / "disabled.LOOP").exists()
    result = _run(fake_root, "--set-intent", "DEEP")
    assert result.returncode == 0
    # Still no sentinel files.
    assert not (fake_root / ".agent" / "LOOP").exists()
    assert not (fake_root / ".agent" / "disabled.LOOP").exists()


# --- --set-session-mode: writes mode only, does not touch intent ---


@pytest.mark.parametrize("mode", ["OFF", "BROAD", "DEEP"])
def test_set_session_mode_writes_only_mode_file(tmp_path: Path, mode: str) -> None:
    """``--set-session-mode MODE`` writes ``AUTONOMOUS_MODE.txt`` and does NOT
    create or modify ``autonomous_intent.txt``."""
    fake_root = _isolated_repo(tmp_path)
    # Seed a known intent value that should survive.
    (fake_root / "autonomous_intent.txt").write_text("BROAD\n")
    result = _run(fake_root, "--set-session-mode", mode)
    assert result.returncode == 0, (
        f"--set-session-mode {mode!r} failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert _mode_file_first_token(fake_root) == mode
    # Intent must be unchanged.
    assert _intent_file(fake_root) == "BROAD"


def test_set_session_mode_broad_toggles_loop_sentinel_on(tmp_path: Path) -> None:
    """``--set-session-mode BROAD`` should enable the LOOP sentinel like the
    bareword form does, because the existing session mode is being turned on."""
    fake_root = _isolated_repo(tmp_path)
    result = _run(fake_root, "--set-session-mode", "BROAD")
    assert result.returncode == 0
    assert (fake_root / ".agent" / "LOOP").exists()


def test_set_session_mode_off_disables_loop_sentinel(tmp_path: Path) -> None:
    """``--set-session-mode off`` must disable the LOOP sentinel, just like the
    circuit-breaker trip path expects."""
    fake_root = _isolated_repo(tmp_path)
    (fake_root / ".agent" / "LOOP").touch()
    result = _run(fake_root, "--set-session-mode", "off")
    assert result.returncode == 0
    # Must be gone (moved to disabled.LOOP by disable_loop_sentinel).
    assert not (fake_root / ".agent" / "LOOP").exists()


# --- Bareword form still writes BOTH files ---


@pytest.mark.parametrize("mode", ["OFF", "BROAD", "DEEP"])
def test_bareword_mode_writes_both_files(tmp_path: Path, mode: str) -> None:
    """The existing ``loop-toggle MODE`` bareword form must continue to write
    BOTH ``AUTONOMOUS_MODE.txt`` AND ``autonomous_intent.txt`` so human UX is
    preserved."""
    fake_root = _isolated_repo(tmp_path)
    result = _run(fake_root, mode)
    assert result.returncode == 0, (
        f"bareword {mode!r} failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert _mode_file_first_token(fake_root) == mode
    assert _intent_file(fake_root) == mode


def test_bareword_on_writes_intent_broad(tmp_path: Path) -> None:
    """The ``on`` alias (backward-compatible for ``broad``) must also update
    intent, not just the session mode."""
    fake_root = _isolated_repo(tmp_path)
    result = _run(fake_root, "on")
    assert result.returncode == 0
    assert _mode_file_first_token(fake_root) == "BROAD"
    assert _intent_file(fake_root) == "BROAD"


# --- status surfaces both mode and intent ---


def test_status_surfaces_intent_value(tmp_path: Path) -> None:
    """``status`` must show the intent value from ``autonomous_intent.txt``
    alongside the session mode."""
    fake_root = _isolated_repo(tmp_path, initial_mode="OFF")
    (fake_root / "autonomous_intent.txt").write_text("DEEP\n")
    result = _run(fake_root, "status")
    assert result.returncode == 0
    assert "Intent:" in result.stdout
    assert "DEEP" in result.stdout


def test_status_flags_when_intent_and_mode_differ(tmp_path: Path) -> None:
    """When intent != session mode, status must make that visible so the
    operator sees the divergence."""
    fake_root = _isolated_repo(tmp_path, initial_mode="OFF")
    (fake_root / "autonomous_intent.txt").write_text("DEEP\n")
    result = _run(fake_root, "status")
    assert result.returncode == 0
    assert "Intent and current session mode differ" in result.stdout


def test_status_when_intent_file_missing_defaults_to_off(tmp_path: Path) -> None:
    """When ``autonomous_intent.txt`` does not exist, status reports Intent: OFF
    rather than erroring."""
    fake_root = _isolated_repo(tmp_path, initial_mode="OFF")
    assert not (fake_root / "autonomous_intent.txt").exists()
    result = _run(fake_root, "status")
    assert result.returncode == 0
    # The line "Intent: OFF ..." must appear.
    assert "Intent:" in result.stdout
    assert "OFF" in result.stdout


# --- Validation errors ---


def test_set_intent_rejects_unknown_mode(tmp_path: Path) -> None:
    """``--set-intent FROBNICATE`` must exit non-zero with a clear message."""
    fake_root = _isolated_repo(tmp_path)
    result = _run(fake_root, "--set-intent", "FROBNICATE")
    assert result.returncode == 1
    assert "Unknown mode" in result.stderr


def test_set_intent_requires_mode_argument(tmp_path: Path) -> None:
    """``--set-intent`` with no following argument must exit non-zero."""
    fake_root = _isolated_repo(tmp_path)
    result = _run(fake_root, "--set-intent")
    assert result.returncode == 1
    assert "Missing mode argument" in result.stderr


def test_set_session_mode_rejects_unknown_mode(tmp_path: Path) -> None:
    """``--set-session-mode FROBNICATE`` must exit non-zero."""
    fake_root = _isolated_repo(tmp_path)
    result = _run(fake_root, "--set-session-mode", "FROBNICATE")
    assert result.returncode == 1
    assert "Unknown mode" in result.stderr


def test_set_session_mode_requires_mode_argument(tmp_path: Path) -> None:
    """``--set-session-mode`` with no following argument must exit non-zero."""
    fake_root = _isolated_repo(tmp_path)
    result = _run(fake_root, "--set-session-mode")
    assert result.returncode == 1
    assert "Missing mode argument" in result.stderr


# --- Help text documents the flags ---


def test_help_text_mentions_set_intent_and_set_session_mode(tmp_path: Path) -> None:
    """``--help`` must document the new flag forms so future agents / humans
    discover them without having to read the script."""
    fake_root = _isolated_repo(tmp_path)
    result = _run(fake_root, "--help")
    assert result.returncode == 0
    assert "--set-intent" in result.stdout
    assert "--set-session-mode" in result.stdout
