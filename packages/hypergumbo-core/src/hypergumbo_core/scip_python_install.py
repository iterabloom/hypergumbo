# SPDX-License-Identifier: AGPL-3.0-or-later
"""Availability probes for the scip-python backend (WI-nanom).

scip-python (``@sourcegraph/scip-python``, an npm package bundling pyright)
is the SCIP producer behind ``hypergumbo-lang-scip-python``, the second
opt-in backend and the first that executes NOTHING from the analysed
repository: pyright performs static inference only. The Rust backend's
trust-store gate therefore does not apply; its opt-in is an ordinary
preference (ADR-0045 ruling 5, ``[backends] scip_python = true``).

Two probes, mirroring :mod:`rust_analyzer_install`:

* :func:`is_scip_python_available` — the ``scip-python`` binary resolves on
  ``PATH`` and answers ``--version`` (a rustup-proxy-shaped failure is the
  reason a bare ``which`` is not enough). Through the ``repo_inspection``
  safety zone's probe wrapper: it reads nothing and indexes nothing.
* :func:`is_scip_python_integration_installed` — the Python wrapper package
  imports, so ``--backend scip-python`` cannot silently no-op the way the
  Rust backend once did (BUG-06).

No installer command ships yet. Installing is one line —
``npm install -g @sourcegraph/scip-python@0.6.6`` — and a global npm
prefix is a machine-level choice this tool should not make for a user;
the CLI's error message names the line instead. The pinned version is the
one every recorded fixture was produced with (ADR-0057 §12).
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess  # nosec B404 — used only for its exception types; the probe runs through the safety-zone wrapper
from typing import Any, Callable, Optional

from .safety_zones import repo_inspect_probe

#: The producer binary on ``PATH``.
SCIP_PYTHON_BINARY = "scip-python"
#: The npm package that provides it.
SCIP_PYTHON_NPM_PACKAGE = "@sourcegraph/scip-python"
#: The version the recorded fixtures were produced with.
SCIP_PYTHON_PINNED_VERSION = "0.6.6"
#: The Python integration package's import name.
SCIP_PYTHON_INTEGRATION_MODULE = "hypergumbo_lang_scip_python"


def is_scip_python_available(
    *,
    which: Optional[Callable[[str], Optional[str]]] = None,
    runner: Optional[Callable[..., Any]] = None,
) -> bool:
    """True when the ``scip-python`` binary resolves and answers ``--version``."""
    resolve = which if which is not None else shutil.which
    resolved = resolve(SCIP_PYTHON_BINARY)
    if resolved is None:
        return False
    run = runner if runner is not None else repo_inspect_probe
    try:
        completed = run([resolved, "--version"], capture_output=True, timeout=5.0)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return bool(completed.returncode == 0)


def is_scip_python_integration_installed(
    *, find_spec: Optional[Callable[[str], Any]] = None,
) -> bool:
    """True when ``hypergumbo_lang_scip_python`` is importable (no import performed)."""
    lookup = find_spec if find_spec is not None else importlib.util.find_spec
    return lookup(SCIP_PYTHON_INTEGRATION_MODULE) is not None
