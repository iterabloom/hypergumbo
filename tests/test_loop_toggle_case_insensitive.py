# SPDX-License-Identifier: AGPL-3.0-or-later
"""Case-insensitivity test for ``scripts/loop-toggle``.

The session-start hook prompts the agent with the choices "BROAD", "DEEP",
or "OFF" (uppercase), but ``loop-toggle`` historically only accepted the
lowercase forms (``broad``, ``deep``, ``off``). Agents that copied the
hook prompt verbatim hit ``Unknown command: OFF`` and had to recover via
``--help``. The fix is to lowercase the argument before dispatching, so
the script accepts whichever casing the caller uses.

This test invokes the script as a subprocess against an isolated
``AUTONOMOUS_MODE.txt`` and ``.agent/`` so the host repo's autonomous
state is never touched.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT_REAL = Path(__file__).parent.parent
LOOP_TOGGLE = REPO_ROOT_REAL / "scripts" / "loop-toggle"


def _isolated_repo(tmp_path: Path) -> Path:
    """Build a tiny fake repo containing only what loop-toggle touches."""
    fake_root = tmp_path / "fake-repo"
    fake_root.mkdir()
    (fake_root / "scripts").mkdir()
    shutil.copy(LOOP_TOGGLE, fake_root / "scripts" / "loop-toggle")
    (fake_root / "scripts" / "loop-toggle").chmod(0o755)
    (fake_root / ".agent").mkdir()
    # Start in OFF state so toggle/dispatch decisions are deterministic.
    (fake_root / "AUTONOMOUS_MODE.txt").write_text("OFF\n")
    return fake_root


def _run(fake_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(fake_root / "scripts" / "loop-toggle"), *args],
        capture_output=True, text=True, cwd=str(fake_root),
    )


@pytest.mark.parametrize(
    "arg,expected_mode",
    [
        ("OFF", "OFF"),
        ("Off", "OFF"),
        ("off", "OFF"),
        ("BROAD", "BROAD"),
        ("Broad", "BROAD"),
        ("broad", "BROAD"),
        ("DEEP", "DEEP"),
        ("Deep", "DEEP"),
        ("deep", "DEEP"),
        ("DISABLE", "OFF"),
        ("ON", "BROAD"),
        ("ENABLE", "BROAD"),
    ],
)
def test_loop_toggle_accepts_any_case(
    tmp_path: Path, arg: str, expected_mode: str,
) -> None:
    """``loop-toggle <ARG>`` should succeed regardless of casing and write
    the corresponding mode to ``AUTONOMOUS_MODE.txt``."""
    fake_root = _isolated_repo(tmp_path)
    result = _run(fake_root, arg)
    assert result.returncode == 0, (
        f"loop-toggle {arg!r} failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    mode_file = fake_root / "AUTONOMOUS_MODE.txt"
    assert mode_file.exists()
    # First whitespace-delimited token on the first line is the mode;
    # the optional ``pid=<N>`` suffix may follow.
    first_line = mode_file.read_text().splitlines()[0]
    assert first_line.split()[0] == expected_mode, (
        f"AUTONOMOUS_MODE.txt should be {expected_mode!r}, got {first_line!r}"
    )


@pytest.mark.parametrize("arg", ["STATUS", "Status", "S"])
def test_loop_toggle_status_accepts_any_case(
    tmp_path: Path, arg: str,
) -> None:
    """The ``status`` subcommand should also accept any casing."""
    fake_root = _isolated_repo(tmp_path)
    result = _run(fake_root, arg)
    assert result.returncode == 0, (
        f"loop-toggle {arg!r} failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert "Autonomous Mode Status" in result.stdout


def test_loop_toggle_help_accepts_any_case(tmp_path: Path) -> None:
    """``HELP`` (uppercase) should print the help text, not error out."""
    fake_root = _isolated_repo(tmp_path)
    result = _run(fake_root, "HELP")
    assert result.returncode == 0, (
        f"loop-toggle HELP failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert "Usage:" in result.stdout


def test_loop_toggle_unknown_command_still_errors(tmp_path: Path) -> None:
    """Genuinely unknown commands should still produce a clear error."""
    fake_root = _isolated_repo(tmp_path)
    result = _run(fake_root, "frobnicate")
    assert result.returncode == 1
    assert "Unknown command" in result.stderr
