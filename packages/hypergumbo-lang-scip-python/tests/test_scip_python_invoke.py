# SPDX-License-Identifier: AGPL-3.0-or-later
"""``run_scip_python_index`` through its injection points — no real binary."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from hypergumbo_lang_scip_python.invoke import (
    ScipPythonInvocationFailed,
    ScipPythonNoOutput,
    ScipPythonNotInstalled,
    run_scip_python_index,
)


@dataclass
class _Completed:
    returncode: int = 0
    stderr: bytes = b""
    stdout: bytes = b""


def _found(name: str) -> Optional[str]:
    return f"/fake/bin/{name}"


def test_missing_binary_raises_not_installed(tmp_path: Path) -> None:
    with pytest.raises(ScipPythonNotInstalled, match="npm install -g @sourcegraph/scip-python"):
        run_scip_python_index(tmp_path, cwd=tmp_path, which=lambda n: None)


def test_a_successful_run_returns_the_index_bytes_and_the_argv_is_pinned(tmp_path: Path) -> None:
    seen: dict = {}

    def runner(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        (tmp_path / "index.scip").write_bytes(b"\x00scip")
        return _Completed()

    project = tmp_path / "proj"
    project.mkdir()
    out = run_scip_python_index(project, cwd=tmp_path, project_name="proj", which=_found, runner=runner)
    assert out == b"\x00scip"
    # The project travels as ``--cwd``: scip-python 0.6.6 takes no positional
    # project and silently indexes its own working directory -- the scratch dir,
    # which is empty -- when handed one. And the version is given: left out,
    # it is read from git, and outside a git checkout the indexer crashes on
    # the first module (WI-nijod).
    assert seen["argv"] == ["/fake/bin/scip-python", "index", "--cwd", str(project),
                            "--project-name", "proj", "--project-version", "0",
                            "--output", str(tmp_path / "index.scip")]
    assert seen["kwargs"]["cwd"] == str(tmp_path) and seen["kwargs"]["timeout"] == 900.0


def test_non_zero_exit_carries_stderr_and_code(tmp_path: Path) -> None:
    with pytest.raises(ScipPythonInvocationFailed) as excinfo:
        run_scip_python_index(tmp_path, cwd=tmp_path, which=_found,
                              runner=lambda argv, **kw: _Completed(returncode=3, stderr=b"boom"))
    assert excinfo.value.returncode == 3 and excinfo.value.stderr == b"boom"


def test_a_timeout_is_an_invocation_failure(tmp_path: Path) -> None:
    def runner(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1.0, stderr=b"slow")

    with pytest.raises(ScipPythonInvocationFailed, match="timed out") as excinfo:
        run_scip_python_index(tmp_path, cwd=tmp_path, which=_found, runner=runner, timeout_sec=1.0)
    assert excinfo.value.returncode is None and excinfo.value.stderr == b"slow"


def test_exit_zero_without_an_index_is_no_output(tmp_path: Path) -> None:
    with pytest.raises(ScipPythonNoOutput, match="wrote no"):
        run_scip_python_index(tmp_path, cwd=tmp_path, which=_found, runner=lambda argv, **kw: _Completed())


def test_a_node_heap_exhaustion_names_the_remedy(tmp_path: Path) -> None:
    """V8's default heap (~4 GB) is too small for a large project: indexing
    hypergumbo itself (1,712 files) aborted at 4.2 GB RSS and finished with an
    8 GB heap (WI-nijod). The crash is an exit code 134 and a V8 trace; the
    message says what to set."""
    oom = b"FATAL ERROR: Ineffective mark-compacts near heap limit Allocation failed - JavaScript heap out of memory"
    with pytest.raises(ScipPythonInvocationFailed, match="NODE_OPTIONS=--max-old-space-size") as excinfo:
        run_scip_python_index(tmp_path, cwd=tmp_path, which=_found,
                              runner=lambda argv, **kw: _Completed(returncode=134, stderr=oom))
    assert excinfo.value.returncode == 134 and excinfo.value.stderr == oom
    with pytest.raises(ScipPythonInvocationFailed) as other:
        run_scip_python_index(tmp_path, cwd=tmp_path, which=_found,
                              runner=lambda argv, **kw: _Completed(returncode=1, stderr=b"boom"))
    assert "NODE_OPTIONS" not in str(other.value)


def test_with_no_runner_the_launch_goes_through_the_analyser_backend_zone(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """INV-tusos: the default launcher is the declared zone wrapper, never a bare
    ``subprocess.run`` the self-proof cannot see."""
    import hypergumbo_lang_scip_python.invoke as invoke_mod

    seen: list[list[str]] = []

    def wrapper(argv, **kwargs):
        seen.append(argv)
        (tmp_path / "index.scip").write_bytes(b"\x01")
        return _Completed()

    monkeypatch.setattr(invoke_mod, "backend_index_scip_python", wrapper)
    assert run_scip_python_index(tmp_path, cwd=tmp_path, which=_found) == b"\x01"
    assert seen and seen[0][1] == "index"
