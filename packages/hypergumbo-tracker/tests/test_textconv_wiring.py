# SPDX-License-Identifier: MPL-2.0
"""In-process unit coverage for the textconv wiring.

Companion to ``test_textconv_git_integration.py``. That module drives git as a
real subprocess, which proves the behaviour but contributes **no coverage** (per
the repo's coverage policy: subprocess tests don't count). These tests exercise
the same code paths in-process so the wiring is both *proven* and *covered*.

Split deliberately: the e2e file answers "does git succeed?", this one answers
"does each branch behave?". Neither substitutes for the other — the e2e file is
what catches an override or a stderr-writing driver, and this one is what catches
a branch that was never reached.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hypergumbo_tracker.cli import _cmd_textconv, main
from hypergumbo_tracker.setup import (
    _resolve_textconv_command,
    _verify_textconv_runs,
)

_OPS = """\
- op: create  # a1b2
  at: '2026-01-01T00:00:00Z'  # a1b2
  by: agent  # a1b2
  actor: tester  # a1b2
  clock: 1  # a1b2
  nonce: a1b2  # a1b2
  data:  # a1b2
    kind: work_item  # a1b2
    title: Wiring probe  # a1b2
    status: todo_soft  # a1b2
    priority: 2  # a1b2
"""

_ITEM = "WI-wiring-aaaaa-bbbbb-ccccc-ddddd-eeeee-fffff-ggggg"


@pytest.fixture()
def ops_file(tmp_path: Path) -> Path:
    p = tmp_path / f".{_ITEM}.ops"
    p.write_text(_OPS)
    return p


# --- the subcommand -------------------------------------------------------


def test_cmd_textconv_renders_and_returns_zero(
    ops_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The handler prints compiled state and reports success."""
    rc = _cmd_textconv(argparse.Namespace(file=str(ops_file)))
    assert rc == 0
    out = capsys.readouterr()
    assert _ITEM in out.out
    assert "Wiring probe" in out.out
    # Git aborts a diff whose driver writes to stderr — assert silence.
    assert out.err == ""


def test_main_dispatches_textconv_early(
    ops_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main(["textconv", FILE])`` reaches the handler and exits 0.

    Also pins the *early* dispatch: no ``--tracker-root`` is supplied, so if this
    ever started constructing a TrackerSet it would fail rather than pass.
    """
    with pytest.raises(SystemExit) as exc:
        main(["textconv", str(ops_file)])
    assert exc.value.code == 0
    assert _ITEM in capsys.readouterr().out


def test_both_entry_points_render_identically(
    ops_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Console script and subcommand share one body, so output must match.

    This is the anti-drift assertion: ADR-0013 documents both surfaces, and the
    original defect was that they were documented as one thing and built as
    another.
    """
    from hypergumbo_tracker.cli import textconv_main

    _cmd_textconv(argparse.Namespace(file=str(ops_file)))
    via_subcommand = capsys.readouterr().out

    with pytest.raises(SystemExit):
        textconv_main([str(ops_file)])
    via_console_script = capsys.readouterr().out

    assert via_subcommand == via_console_script


# --- command resolution ---------------------------------------------------


def test_resolve_prefers_the_in_repo_script(tmp_path: Path) -> None:
    """With scripts/tracker-textconv present, use it (handles install fallbacks)."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "tracker-textconv").write_text("#!/bin/sh\n")
    assert _resolve_textconv_command(tmp_path) == str(scripts / "tracker-textconv")


def test_resolve_falls_back_to_the_cli_subcommand(tmp_path: Path) -> None:
    """Standalone installs have no repo script — use the documented subcommand."""
    assert _resolve_textconv_command(tmp_path) == "hypergumbo-tracker textconv"


def test_resolve_handles_no_repo_root() -> None:
    """``repo_root=None`` must not raise."""
    assert _resolve_textconv_command(None) == "hypergumbo-tracker textconv"


# --- behavioural verification --------------------------------------------


def _cfg(value: str) -> MagicMock:
    m = MagicMock()
    m.stdout = value
    return m


def test_verify_reports_missing_configuration(ops_file: Path) -> None:
    with patch("hypergumbo_tracker.setup.subprocess.run", return_value=_cfg("")):
        assert _verify_textconv_runs(None, ops_file) == "no command configured"


def test_verify_reports_unexecutable_driver(ops_file: Path) -> None:
    calls = [_cfg("definitely-not-a-real-binary")]

    def run(cmd, **kw):
        if calls:
            return calls.pop(0)
        raise OSError("No such file or directory")

    with patch("hypergumbo_tracker.setup.subprocess.run", side_effect=run):
        reason = _verify_textconv_runs(None, ops_file)
    assert reason is not None and "could not be executed" in reason


def test_verify_reports_nonzero_exit_with_first_line(ops_file: Path) -> None:
    """The real defect's shape: non-zero exit plus a usage dump."""
    proc = MagicMock(returncode=2, stdout="", stderr="invalid choice: 'textconv'\nmore\n")
    with patch("hypergumbo_tracker.setup.subprocess.run",
               side_effect=[_cfg("drv"), proc]):
        reason = _verify_textconv_runs(None, ops_file)
    assert reason is not None
    assert "exited 2" in reason and "invalid choice" in reason


def test_verify_reports_nonzero_exit_with_no_output(ops_file: Path) -> None:
    proc = MagicMock(returncode=1, stdout="", stderr="")
    with patch("hypergumbo_tracker.setup.subprocess.run",
               side_effect=[_cfg("drv"), proc]):
        reason = _verify_textconv_runs(None, ops_file)
    assert reason is not None and "(no output)" in reason


def test_verify_rejects_a_driver_that_writes_to_stderr(ops_file: Path) -> None:
    """Exit 0 is not enough: git aborts the diff on any stderr output."""
    proc = MagicMock(returncode=0, stdout="fine\n", stderr="deprecation warning\n")
    with patch("hypergumbo_tracker.setup.subprocess.run",
               side_effect=[_cfg("drv"), proc]):
        reason = _verify_textconv_runs(None, ops_file)
    assert reason is not None and "wrote to stderr" in reason


def test_verify_rejects_a_silent_driver(ops_file: Path) -> None:
    """A driver emitting nothing would make diffs clean but empty."""
    proc = MagicMock(returncode=0, stdout="   \n", stderr="")
    with patch("hypergumbo_tracker.setup.subprocess.run",
               side_effect=[_cfg("drv"), proc]):
        assert _verify_textconv_runs(None, ops_file) == "driver produced no output"


def test_verify_accepts_a_healthy_driver(ops_file: Path) -> None:
    proc = MagicMock(returncode=0, stdout=f"{_ITEM}  Wiring probe\n", stderr="")
    with patch("hypergumbo_tracker.setup.subprocess.run",
               side_effect=[_cfg("drv"), proc]):
        assert _verify_textconv_runs(None, ops_file) is None


def test_verify_passes_repo_root_as_cwd(ops_file: Path, tmp_path: Path) -> None:
    """Config lookup must be scoped to the repo, not the process cwd."""
    proc = MagicMock(returncode=0, stdout="ok\n", stderr="")
    with patch("hypergumbo_tracker.setup.subprocess.run",
               side_effect=[_cfg("drv"), proc]) as m:
        _verify_textconv_runs(tmp_path, ops_file)
    assert m.call_args_list[0].kwargs["cwd"] == str(tmp_path)


def test_verify_survives_a_subprocess_error(ops_file: Path) -> None:
    """A timeout is a SubprocessError, not a crash."""
    def run(cmd, **kw):
        if kw.get("timeout") is None:
            return _cfg("drv")
        raise subprocess.TimeoutExpired(cmd, 30)

    with patch("hypergumbo_tracker.setup.subprocess.run", side_effect=run):
        reason = _verify_textconv_runs(None, ops_file)
    assert reason is not None and "could not be executed" in reason
