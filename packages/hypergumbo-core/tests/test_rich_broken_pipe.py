# SPDX-License-Identifier: AGPL-3.0-or-later
"""A broken pipe under a rich Console exits 141, like every other command.

THE DEFECT (WI-vavag). ``hypergumbo symbols <repo> | head -1`` exited 1 with
nothing on stderr. ``cmd_symbols`` prints its table through
``rich.console.Console``; on EPIPE rich calls ``Console.on_broken_pipe``,
which points stdout at /dev/null and raises ``SystemExit(1)``. ``SystemExit``
is not an ``Exception``, so ``main()``'s ``except BrokenPipeError`` (exit 141,
``EXIT_BROKEN_PIPE``, owner ruling 2026-11-19 on WI-nidun) never saw it, and
1 is a code commands answer with (verify-claims' ``violated``).

THE FIX. ``pipe_console.PipeConsole`` re-raises ``BrokenPipeError`` from
``on_broken_pipe`` so ``main()`` handles it like any other write. Every rich
Console hypergumbo-core builds is one: ``cmd_symbols`` (stdout) and
``sketch``'s representativeness table (stderr, which under ``2>&1 | head``
is the same pipe). ``test_every_core_console_is_pipe_aware`` keeps it so.
"""
from __future__ import annotations

import ast
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hypergumbo_core.pipe_console import PipeConsole

REPO_ROOT = Path(__file__).resolve().parents[3]
_CORE_SRC = REPO_ROOT / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"


class _ClosedPipe(io.StringIO):
    """A stream whose reader has gone: every write raises EPIPE."""

    def write(self, s: str) -> int:
        raise BrokenPipeError(32, "Broken pipe")


def test_a_closed_pipe_raises_broken_pipe_error_not_system_exit() -> None:
    console = PipeConsole(file=_ClosedPipe(), width=40)
    with pytest.raises(BrokenPipeError):
        console.print("row")
    assert console.quiet is True  # nothing more is written to the dead pipe


def test_an_open_pipe_writes_as_rich_does() -> None:
    out = io.StringIO()
    PipeConsole(file=out, width=40).print("row")
    assert out.getvalue() == "row\n"


def _console_constructions(path: Path) -> list[tuple[int, str]]:
    """(line, called name) for each call of a name ending ``Console``."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if name is not None and name.endswith("Console"):
                found.append((node.lineno, name))
    return found


def test_every_core_console_is_pipe_aware() -> None:
    plain = {
        f"{path.relative_to(_CORE_SRC)}:{line}"
        for path in sorted(_CORE_SRC.rglob("*.py"))
        for line, name in _console_constructions(path)
        if name != "PipeConsole"
    }
    assert plain == set(), f"construct PipeConsole, not rich's Console: {sorted(plain)}"


def test_the_console_scan_sees_both_call_forms(tmp_path: Path) -> None:
    source = tmp_path / "m.py"
    source.write_text(
        "import rich.console\n"
        "a = Console()\n"
        "b = rich.console.Console(stderr=True)\n"
        "c = PipeConsole()\n",
        encoding="utf-8",
    )
    assert _console_constructions(source) == [
        (2, "Console"), (3, "Console"), (4, "PipeConsole"),
    ]


def _run_into_closed_pipe(argv: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    """Run the CLI, read 10 bytes of its stdout, close the pipe, return (rc, stderr)."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "hypergumbo_core", *argv],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
    )
    assert proc.stdout is not None and proc.stderr is not None
    proc.stdout.read(10)
    proc.stdout.close()
    stderr = proc.stderr.read().decode("utf-8", "replace")
    return proc.wait(timeout=300), stderr


def test_symbols_into_a_closed_pipe_exits_141_quietly(tmp_path: Path) -> None:
    """The real command, a real pipe: more rows than a pipe buffer holds."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "many.py").write_text(
        "".join(f"def function_number_{i:05d}():\n    return {i}\n\n" for i in range(3000)),
        encoding="utf-8",
    )
    env = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache"))
    rc, stderr = _run_into_closed_pipe(["symbols", str(repo), "--all"], tmp_path, env)
    assert rc == 141, stderr[-2000:]
    assert "Traceback" not in stderr, stderr[-2000:]
