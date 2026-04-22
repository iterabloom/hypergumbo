# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for WI-zajob: stop-hook watched-process detector.

The stop hook pauses when a "watched" long-running command (pytest, auto-pr,
merge-pr, smart-test) is alive, so that legitimate stops during a test or
CI poll do not tick the circuit-breaker hash. The original implementation
used ``pgrep -af <regex>`` with a substring match against the full cmdline,
so ANY process whose arguments happened to contain the pattern text was
treated as a live watched process. This included ``inotifywait`` helpers
spawned by pytest tempdir fixtures (``/tmp/pytest-<id>/...``), which could
leak and stay alive for hours after the parent pytest run ended. The hook
would then sleep up to 30 minutes waiting for those helpers to exit.

The fix anchors detection to argv[0] (after path strip) and requires the
full pattern to match the LEADING tokens of the cmdline, not appear
anywhere inside it. These tests guard the pure ``is_watched_cmdline``
predicate so the regression cannot silently return.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


REPO_ROOT = Path(__file__).parent.parent
MODULE_PATH = REPO_ROOT / ".agent" / "hooks" / "_shared" / "watched_process.py"

DEFAULT_PATTERNS = [
    "pytest",
    "python -m pytest",
    "smart-test",
    "bash ./scripts/auto-pr",
    "bash ./scripts/merge-pr",
]


def _import_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("watched_process", str(MODULE_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- is_watched_cmdline: positive matches ---


@pytest.mark.parametrize(
    "cmdline",
    [
        "pytest tests/",
        "/home/user/.venv/bin/pytest -v",
        "/usr/bin/pytest",
    ],
)
def test_pytest_binary_is_watched(cmdline: str) -> None:
    mod = _import_module()
    assert mod.is_watched_cmdline(cmdline, DEFAULT_PATTERNS) is True


@pytest.mark.parametrize(
    "cmdline",
    [
        "python -m pytest",
        "python3 -m pytest tests/",
        "/usr/bin/python3.11 -m pytest",
        "python3.12 -m pytest -x",
    ],
)
def test_python_dash_m_pytest_is_watched(cmdline: str) -> None:
    mod = _import_module()
    assert mod.is_watched_cmdline(cmdline, DEFAULT_PATTERNS) is True


def test_smart_test_is_watched() -> None:
    mod = _import_module()
    assert mod.is_watched_cmdline(
        "/home/user/hypergumbo/scripts/smart-test -k foo", DEFAULT_PATTERNS
    ) is True


@pytest.mark.parametrize(
    "cmdline",
    [
        "bash ./scripts/auto-pr",
        "/bin/bash ./scripts/auto-pr",
        "/usr/bin/bash ./scripts/merge-pr 123",
    ],
)
def test_bash_scripts_watched(cmdline: str) -> None:
    mod = _import_module()
    assert mod.is_watched_cmdline(cmdline, DEFAULT_PATTERNS) is True


# --- is_watched_cmdline: negative matches (the over-match regression) ---


def test_inotifywait_with_pytest_tmpdir_is_not_watched() -> None:
    """The actual WI-zajob reproducer: an ``inotifywait`` helper spawned by a
    pytest fixture holds a ``/tmp/pytest-<id>/...`` path in argv. The old
    substring match treated this as "pytest is alive" and the hook slept for
    up to 30 minutes. The new detector must reject it."""
    mod = _import_module()
    cmdline = (
        "inotifywait -qq -e close_write "
        "/tmp/pytest-of-jgstern_agent/pytest-952/popen-gw5/"
        "test_launch_starts_watcher_wit0/new-src.jsonl"
    )
    assert mod.is_watched_cmdline(cmdline, DEFAULT_PATTERNS) is False


def test_absolute_path_inotifywait_is_not_watched() -> None:
    mod = _import_module()
    assert mod.is_watched_cmdline(
        "/usr/bin/inotifywait -qq /tmp/pytest-abc/src.jsonl", DEFAULT_PATTERNS
    ) is False


def test_grep_of_pytest_logs_is_not_watched() -> None:
    mod = _import_module()
    assert mod.is_watched_cmdline(
        "grep pytest /var/log/foo.log", DEFAULT_PATTERNS
    ) is False


def test_editor_viewing_auto_pr_is_not_watched() -> None:
    mod = _import_module()
    assert mod.is_watched_cmdline(
        "vim ./scripts/auto-pr", DEFAULT_PATTERNS
    ) is False


def test_bash_dash_c_wrapper_is_not_watched() -> None:
    """A ``bash -c "echo pytest"`` heredoc should never be treated as a live
    watched process, even though its cmdline mentions pytest. This was the
    only case the original filter caught; the new detector keeps that
    exclusion because ``-c`` means "execute this string", not "invoke the
    binary the string happens to name"."""
    mod = _import_module()
    assert mod.is_watched_cmdline(
        'bash -c "echo pytest && sleep 1"', DEFAULT_PATTERNS
    ) is False


def test_sh_dash_c_wrapper_is_not_watched() -> None:
    mod = _import_module()
    assert mod.is_watched_cmdline(
        "sh -c 'pytest foo'", DEFAULT_PATTERNS
    ) is False


def test_empty_cmdline_is_not_watched() -> None:
    mod = _import_module()
    assert mod.is_watched_cmdline("", DEFAULT_PATTERNS) is False


def test_whitespace_only_cmdline_is_not_watched() -> None:
    mod = _import_module()
    assert mod.is_watched_cmdline("   \t  ", DEFAULT_PATTERNS) is False


def test_no_patterns_matches_nothing() -> None:
    mod = _import_module()
    assert mod.is_watched_cmdline("pytest -v", []) is False


def test_empty_pattern_token_skipped() -> None:
    """An empty string in the pattern list must not accidentally match every
    cmdline — defensive against malformed config."""
    mod = _import_module()
    assert mod.is_watched_cmdline("pytest -v", ["", "pytest"]) is True
    assert mod.is_watched_cmdline("inotifywait foo", [""]) is False


# --- any_watched_alive: CLI wrapper around pgrep output ---


def test_any_watched_alive_parses_pgrep_output() -> None:
    mod = _import_module()
    pgrep_output = (
        "12345 pytest -v tests/\n"
        "67890 inotifywait -qq /tmp/pytest-abc/src.jsonl\n"
    )
    assert mod.any_watched_alive_in(pgrep_output, DEFAULT_PATTERNS) is True


def test_any_watched_alive_false_when_only_false_positives() -> None:
    mod = _import_module()
    pgrep_output = (
        "67890 inotifywait -qq /tmp/pytest-abc/src.jsonl\n"
        "67891 grep pytest /var/log/foo.log\n"
        "67892 bash -c 'echo pytest'\n"
    )
    assert mod.any_watched_alive_in(pgrep_output, DEFAULT_PATTERNS) is False


def test_any_watched_alive_handles_empty_input() -> None:
    mod = _import_module()
    assert mod.any_watched_alive_in("", DEFAULT_PATTERNS) is False
    assert mod.any_watched_alive_in("\n\n", DEFAULT_PATTERNS) is False


def test_any_watched_alive_handles_malformed_line() -> None:
    """pgrep -af lines are ``PID CMDLINE``. A line without the PID prefix
    would be unusual but must not crash the filter."""
    mod = _import_module()
    assert mod.any_watched_alive_in("no-pid-here\n", DEFAULT_PATTERNS) is False


# --- main() direct invocation (covers the function body, not just the CLI subprocess) ---


def test_main_returns_zero_when_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _import_module()
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("12345 pytest -v\n"))
    assert mod.main(["watched_process.py", *DEFAULT_PATTERNS]) == 0


def test_main_returns_one_when_not_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _import_module()
    import io
    monkeypatch.setattr(
        "sys.stdin", io.StringIO("67890 inotifywait /tmp/pytest-abc/src.jsonl\n")
    )
    assert mod.main(["watched_process.py", *DEFAULT_PATTERNS]) == 1


# --- CLI entry point ---


def test_cli_exits_zero_when_alive(tmp_path: Path) -> None:
    import subprocess
    import sys
    pgrep_output = "12345 pytest -v\n"
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), *DEFAULT_PATTERNS],
        input=pgrep_output,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_cli_exits_nonzero_when_not_alive() -> None:
    import subprocess
    import sys
    pgrep_output = "67890 inotifywait -qq /tmp/pytest-abc/src.jsonl\n"
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), *DEFAULT_PATTERNS],
        input=pgrep_output,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1


def test_cli_exits_nonzero_on_empty_input() -> None:
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), *DEFAULT_PATTERNS],
        input="",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
