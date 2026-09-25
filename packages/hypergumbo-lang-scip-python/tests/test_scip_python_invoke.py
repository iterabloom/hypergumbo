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
    assert seen["argv"] == ["/fake/bin/scip-python", "index", str(project), "--project-name", "proj",
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
