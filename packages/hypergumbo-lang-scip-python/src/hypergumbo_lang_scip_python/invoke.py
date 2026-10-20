# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shell-out wrapper for ``scip-python index`` (WI-nanom).

``scip-python index --cwd <project> --project-name NAME --project-version 0
--output FILE`` writes a SCIP index for a Python project; pyright resolves the project's own
modules and whatever is importable from the invoking interpreter's
environment (so a project analysed inside its own virtualenv gets its
dependencies typed too). It executes NOTHING from the project — static
inference only — which is why this backend's opt-in is a preference and
not a trust grant.

The project goes in ``--cwd``. scip-python 0.6.6 takes no positional
project: handed one, it ignores it, indexes its own working directory and
exits 0 -- and the subprocess runs in a scratch directory, so until WI-nijod
every run indexed that empty directory ("Total Project Files 0") and
returned a valid, empty index. The version is a constant: left out, it is
the project's git revision, and outside a git checkout the indexer crashes
on the first module (``normalizeNameOrVersion``). Nothing downstream reads
it -- translation keys on the descriptors -- and a constant keeps symbol
strings identical from one commit to the next.

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


#: V8's fatal-error text when the heap limit is reached. The default limit
#: (~4 GB) is too small for a large project: hypergumbo's own tree (1,712
#: files) aborted at 4.2 GB RSS and indexed in 306 s at 8.4 GB with
#: ``--max-old-space-size=9000`` (WI-nijod). The limit is the user's to set --
#: it is memory on their machine -- so the failure names the knob instead.
_NODE_HEAP_EXHAUSTED = b"JavaScript heap out of memory"


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
        resolved, "index", "--cwd", str(project),
        "--project-name", project_name,
        "--project-version", "0",
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
        stderr = completed.stderr or b""
        hint = (
            "; node ran out of heap -- raise it with NODE_OPTIONS=--max-old-space-size=<MB>"
            if _NODE_HEAP_EXHAUSTED in stderr else ""
        )
        raise ScipPythonInvocationFailed(
            f"scip-python index exited {completed.returncode} on {project}{hint}",
            stderr, returncode=completed.returncode,
        )
    if not output.is_file():
        raise ScipPythonNoOutput(
            f"scip-python index exited 0 but wrote no {output}", completed.stderr or b"",
        )
    return output.read_bytes()
