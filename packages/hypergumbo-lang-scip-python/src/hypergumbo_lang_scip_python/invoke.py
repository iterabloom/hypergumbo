# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shell-out wrapper for ``scip-python index`` (WI-nanom).

``scip-python index <project> --project-name NAME --output FILE`` writes a
SCIP index for a Python project; pyright resolves the project's own
modules and whatever is importable from the invoking interpreter's
environment (so a project analysed inside its own virtualenv gets its
dependencies typed too). It executes NOTHING from the project — static
inference only — which is why this backend's opt-in is a preference and
not a trust grant.

:func:`run_scip_python_index` confirms the binary resolves, runs it with
stdout/stderr captured and a timeout, and returns the index bytes. Failure
modes map to three exceptions the analyzer turns into pass-silence codes:
not installed (``dependency_unavailable``), non-zero exit or timeout
(``pass_crashed``), and exit-0-with-no-index (``unreported``). ``which`` and
``runner`` are injectable so tests never touch ``PATH`` or shell out.
"""
from __future__ import annotations

import shutil
import subprocess  # nosec B404 — required for the scip-python invocation
from pathlib import Path
from typing import Any, Callable, Optional

from hypergumbo_core.scip_python_install import SCIP_PYTHON_BINARY


class ScipPythonError(Exception):
    """Base class for :func:`run_scip_python_index` failures."""


class ScipPythonNotInstalled(ScipPythonError):
    """The ``scip-python`` binary is not resolvable."""


class ScipPythonInvocationFailed(ScipPythonError):
    """The binary ran but exited non-zero or timed out; carries stderr and the exit code."""

    def __init__(self, message: str, stderr: bytes, *, returncode: Optional[int] = None) -> None:
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode


class ScipPythonNoOutput(ScipPythonError):
    """The invocation exited 0 but wrote no index."""

    def __init__(self, message: str, stderr: bytes) -> None:
        super().__init__(message)
        self.stderr = stderr


def run_scip_python_index(
    project: Path,
    *,
    cwd: Path,
    project_name: str = "hypergumbo-target",
    scip_python_bin: str = SCIP_PYTHON_BINARY,
    timeout_sec: float = 900.0,
    which: Optional[Callable[[str], Optional[str]]] = None,
    runner: Optional[Callable[..., Any]] = None,
) -> bytes:
    """Run ``scip-python index`` on ``project`` and return the SCIP bytes.

    ``cwd`` is a caller-owned scratch directory that receives ``index.scip``;
    the project itself is never written to. The timeout defaults to 15
    minutes: scip-python indexed 179 files in 40 s at 2.1 GB RSS, and a
    large monorepo should fail visibly rather than hang the pipeline.
    """
    resolve = which if which is not None else shutil.which
    resolved = resolve(scip_python_bin)
    if resolved is None:
        raise ScipPythonNotInstalled(
            f"'{scip_python_bin}' is not on PATH; install it with "
            "npm install -g @sourcegraph/scip-python"
        )
    output = cwd / "index.scip"
    argv = [
        resolved, "index", str(project),
        "--project-name", project_name,
        "--output", str(output),
    ]
    run = runner if runner is not None else subprocess.run
    try:
        completed = run(  # nosec B603 — argv is built here from a resolved binary
            argv, cwd=str(cwd), capture_output=True, timeout=timeout_sec, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr if isinstance(exc.stderr, bytes) else b""
        raise ScipPythonInvocationFailed(
            f"scip-python index timed out after {timeout_sec:.0f}s on {project}", stderr,
        ) from exc
    if completed.returncode != 0:
        raise ScipPythonInvocationFailed(
            f"scip-python index exited {completed.returncode} on {project}",
            completed.stderr or b"", returncode=completed.returncode,
        )
    if not output.is_file():
        raise ScipPythonNoOutput(
            f"scip-python index exited 0 but wrote no {output}", completed.stderr or b"",
        )
    return output.read_bytes()
