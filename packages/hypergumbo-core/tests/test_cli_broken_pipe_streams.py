# SPDX-License-Identifier: AGPL-3.0-or-later
"""A broken pipe on stdout OR stderr exits 141, from anywhere in ``main``.

THE DEFECT. ``hypergumbo sketch <repo> 2>&1 | head`` and
``hypergumbo sketch <repo> 2>&1 >/dev/null | head`` exited 120, not 141 (the
owner's ruling of 2026-11-19: a broken pipe exits 141 for the whole CLI). The
``print`` that hit the dead pipe was one of sketch's stderr lines. ``main``
caught the ``BrokenPipeError`` and returned 141, but pointed only STDOUT at
/dev/null; the unwritten stderr line stayed in stderr's buffer, Python's
shutdown flush of stderr failed again, and an interpreter whose final flush
fails exits 120 whatever ``main`` returned.

Two neighbours of the same mechanism exited 120 as well: a write that
failed before command dispatch (``--help`` into a closed pipe: argparse
swallows the ``OSError`` and calls ``sys.exit(0)``, leaving the text in the
buffer) and an argparse usage error into a closed stderr.

THE RULE these tests hold. ``main`` flushes BOTH standard streams on every
way out -- a return, a ``BrokenPipeError`` from anywhere in it, and a
``SystemExit`` -- points every stream whose flush fails with EPIPE at
/dev/null, and then exits 141.

The in-process tests drive the code path (subprocess tests contribute no
coverage); the subprocess tests are the only honest check of the exit
status itself, because the 120 is decided by the interpreter's shutdown,
after ``main`` has returned.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hypergumbo_core import cli


class _DeadPipe(io.StringIO):
    """A stream whose reader has gone: writing and flushing raise EPIPE.

    Like a real ``TextIOWrapper`` on a closed pipe, a failed write leaves its
    text pending, so the next flush fails too.
    """

    def __init__(self, fd: int) -> None:
        super().__init__()
        self._fd = fd
        self.pending = False

    def write(self, s: str) -> int:
        self.pending = True
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self) -> None:
        if self.pending:
            raise BrokenPipeError(32, "Broken pipe")

    def fileno(self) -> int:
        return self._fd


class _BufferingDeadPipe(_DeadPipe):
    """A dead pipe as a buffered stream meets it: ``write`` takes the text
    into the buffer and succeeds; the flush is what raises EPIPE.

    This is what a real block-buffered stdout does, and it reaches ``main``'s
    ``SystemExit`` arm without relying on the writer to swallow an error.
    """

    def write(self, s: str) -> int:
        self.pending = True
        return len(s)


@pytest.fixture
def dup2_calls(monkeypatch) -> list[tuple[int, int]]:
    """Record which fds ``main`` points at /dev/null (as ``(987, fd)``)
    instead of touching pytest's real fds."""
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        cli, "_point_stream_at_devnull",
        lambda stream: calls.append((987, stream.fileno())),
    )
    return calls


def test_point_stream_at_devnull_dup2s_devnull_over_the_streams_fd(
    monkeypatch,
) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(cli.os, "open", lambda path, flags: 987)
    monkeypatch.setattr(cli.os, "dup2", lambda src, dst: calls.append((src, dst)))
    cli._point_stream_at_devnull(_DeadPipe(fd=52))
    assert calls == [(987, 52)]


def _command(monkeypatch, body) -> None:
    """Route ``hypergumbo cache-status`` to ``body`` (build_parser reads the global)."""
    monkeypatch.setattr(cli, "cmd_cache_status", body)


def test_a_stderr_print_into_a_dead_pipe_exits_141_and_silences_stderr(
    monkeypatch, dup2_calls,
) -> None:
    stderr = _DeadPipe(fd=52)
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    def _warn(_args) -> int:
        print("Secret scan complete", file=sys.stderr)
        return 0

    _command(monkeypatch, _warn)
    assert cli.main(["cache-status"]) == cli.EXIT_BROKEN_PIPE
    assert dup2_calls == [(987, 52)]  # stderr, not stdout


def test_a_stdout_print_into_a_dead_pipe_silences_stdout_only(
    monkeypatch, dup2_calls,
) -> None:
    monkeypatch.setattr(cli.sys, "stdout", _DeadPipe(fd=51))

    def _print(_args) -> int:
        print("a sketch")
        return 0

    _command(monkeypatch, _print)
    assert cli.main(["cache-status"]) == cli.EXIT_BROKEN_PIPE
    assert dup2_calls == [(987, 51)]


def test_both_streams_dead_silences_both(monkeypatch, dup2_calls) -> None:
    monkeypatch.setattr(cli.sys, "stdout", _DeadPipe(fd=51))
    monkeypatch.setattr(cli.sys, "stderr", _DeadPipe(fd=52))

    def _print(_args) -> int:
        print("a sketch")
        return 0

    _command(monkeypatch, _print)
    cli.sys.stderr.pending = True  # an earlier progress line never left
    assert cli.main(["cache-status"]) == cli.EXIT_BROKEN_PIPE
    assert dup2_calls == [(987, 51), (987, 52)]


def test_a_write_error_a_command_swallowed_still_exits_141(
    monkeypatch, dup2_calls,
) -> None:
    # The command caught the error itself (as argparse and logging do) and
    # answered 0, but its text is still pending on the dead pipe: the
    # interpreter's own flush would fail and exit 120.
    stderr = _DeadPipe(fd=52)
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    def _swallow(_args) -> int:
        try:
            sys.stderr.write("progress\n")
        except OSError:
            pass
        return 0

    _command(monkeypatch, _swallow)
    assert cli.main(["cache-status"]) == cli.EXIT_BROKEN_PIPE
    assert dup2_calls == [(987, 52)]


def test_help_into_a_dead_stdout_exits_141_not_system_exit(
    monkeypatch, dup2_calls,
) -> None:
    # The write itself raises. Python 3.11+ argparse swallows the OSError and
    # raises SystemExit(0); 3.10 argparse lets the BrokenPipeError out. Either
    # way the answer is 141 -- but WHICH arm of ``main`` answers depends on the
    # interpreter, so this test does not cover the SystemExit arm on 3.10. The
    # next test does, on every version.
    monkeypatch.setattr(cli.sys, "stdout", _DeadPipe(fd=51))
    assert cli.main(["cache-status", "--help"]) == cli.EXIT_BROKEN_PIPE
    assert dup2_calls == [(987, 51)]


def test_help_buffered_for_a_dead_stdout_exits_141_from_the_system_exit_arm(
    monkeypatch, dup2_calls,
) -> None:
    # argparse's write succeeds into the buffer, so it raises SystemExit(0) on
    # every Python version; ``main``'s flush on the way out finds the dead
    # pipe. Without this test the SystemExit arm's 141 is reached only where
    # argparse swallows write errors (3.11+), and the 3.10 leg is one line
    # short of full coverage.
    stdout = _BufferingDeadPipe(fd=51)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    assert cli.main(["cache-status", "--help"]) == cli.EXIT_BROKEN_PIPE
    assert stdout.pending  # argparse did write: the arm was SystemExit's
    assert dup2_calls == [(987, 51)]


def test_a_usage_error_into_a_dead_stderr_exits_141(monkeypatch, dup2_calls) -> None:
    monkeypatch.setattr(cli.sys, "stderr", _DeadPipe(fd=52))
    assert cli.main(["cache-status", "--no-such-flag"]) == cli.EXIT_BROKEN_PIPE
    assert dup2_calls == [(987, 52)]


def test_a_print_before_dispatch_into_a_dead_stderr_exits_141(
    monkeypatch, dup2_calls,
) -> None:
    # The did-you-mean refusal prints before the dispatch try-block.
    monkeypatch.setattr(cli.sys, "stderr", _DeadPipe(fd=52))
    assert cli.main(["frobnicate"]) == cli.EXIT_BROKEN_PIPE
    assert dup2_calls == [(987, 52)]


def test_healthy_streams_keep_the_commands_answer(monkeypatch, dup2_calls) -> None:
    _command(monkeypatch, lambda _args: 3)
    assert cli.main(["cache-status"]) == 3
    assert dup2_calls == []


def test_healthy_streams_keep_argparse_system_exit(monkeypatch, dup2_calls, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["cache-status", "--no-such-flag"])
    assert exc.value.code == 2
    assert dup2_calls == []


def test_a_stream_that_cannot_be_flushed_for_another_reason_is_left_alone(
    monkeypatch, dup2_calls,
) -> None:
    # A closed stream (ValueError) or another OSError is not a broken pipe:
    # no redirection, and the command's answer stands.
    class _Closed(io.StringIO):
        def flush(self) -> None:
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(cli.sys, "stderr", _Closed())
    _command(monkeypatch, lambda _args: 0)
    assert cli.main(["cache-status"]) == 0
    assert dup2_calls == []


def test_a_broken_pipe_from_the_internal_error_message_exits_141(
    monkeypatch, dup2_calls,
) -> None:
    # The safety net prints "internal error" to stderr; if THAT write meets
    # a dead pipe the answer is 141, not 70 and not a traceback.
    monkeypatch.setattr(cli.sys, "stderr", _DeadPipe(fd=52))

    def _crash(_args) -> int:
        raise RuntimeError("boom")

    _command(monkeypatch, _crash)
    assert cli.main(["cache-status"]) == cli.EXIT_BROKEN_PIPE
    assert dup2_calls == [(987, 52)]


# --------------------------------------------------------------------------
# The real exit status: a real interpreter, a pipe whose reader has gone.
# --------------------------------------------------------------------------


def _run_with_dead_pipe(
    argv: list[str], broken: str, tmp_path: Path,
) -> tuple[int, str]:
    """Run the CLI with ``broken`` ("stdout", "stderr" or "both") on a pipe
    whose read end closed before the process started; return (rc, the other
    stream's text when only one is broken)."""
    read_end, write_end = os.pipe()
    os.close(read_end)
    other = tmp_path / "other.txt"
    with other.open("wb") as other_f:
        streams = {
            "both": {"stdout": write_end, "stderr": write_end},
            "stdout": {"stdout": write_end, "stderr": other_f},
            "stderr": {"stdout": other_f, "stderr": write_end},
        }[broken]
        try:
            rc = subprocess.run(
                [sys.executable, "-m", "hypergumbo_core", *argv],
                stdin=subprocess.DEVNULL, cwd=tmp_path, timeout=300,
                env=dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache")),
                **streams,
            ).returncode
        finally:
            os.close(write_end)
    return rc, other.read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def small_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "".join(f"def f{i}():\n    return f{i + 1}()\n\n" for i in range(40))
        + "def f40():\n    return 0\n",
        encoding="utf-8",
    )
    return repo


@pytest.mark.parametrize("broken", ["both", "stdout", "stderr"])
def test_sketch_into_a_dead_pipe_exits_141(
    broken: str, small_repo: Path, tmp_path: Path,
) -> None:
    rc, other = _run_with_dead_pipe(["sketch", str(small_repo)], broken, tmp_path)
    assert rc == cli.EXIT_BROKEN_PIPE, other[-2000:]
    assert "Traceback" not in other and "Exception ignored" not in other, other[-2000:]


def test_help_into_a_dead_stdout_exits_141(tmp_path: Path) -> None:
    rc, other = _run_with_dead_pipe(["--help"], "stdout", tmp_path)
    assert rc == cli.EXIT_BROKEN_PIPE, other[-2000:]
    assert other == ""


def test_a_usage_error_into_a_dead_stderr_exits_141_in_a_real_process(
    tmp_path: Path,
) -> None:
    rc, _ = _run_with_dead_pipe(["sketch", ".", "--no-such-flag"], "stderr", tmp_path)
    assert rc == cli.EXIT_BROKEN_PIPE
