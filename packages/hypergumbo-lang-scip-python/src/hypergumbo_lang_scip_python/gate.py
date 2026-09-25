# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opt-in gate for the scip-python backend (WI-nanom).

Two conditions, both required: the user asked, and the binary is there.

"The user asked" is :func:`hypergumbo_core.backend_selection
.resolve_scip_python_optin` — the ADR-0045 ruling 4 chain, flag >
environment > CONFIG tiers. The config tiers are the tier the Rust backend
cannot have: enabling rust-analyzer executes the analysed crate's
``build.rs``, so its durable opt-in is a per-repository trust grant;
pyright executes nothing, so ``[backends] scip_python = true`` in
``config.toml`` or ``.hypergumbo.toml`` is an ordinary preference (ruling 5).
The vocabulary and the ordering live in core, shared with the CLI, so the
two cannot disagree about what ``--backend tree-sitter`` means.

"The binary is there" is :func:`hypergumbo_core.scip_python_install
.is_scip_python_available` — ``which`` plus a ``--version`` smoke test.
Opted in but not installed returns False silently here; the CLI's
``--backend scip-python`` path reports the missing binary at exit 2 before
a run starts, and the analyzer records ``dependency_unavailable`` if the
binary vanishes between the gate and the invocation.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Mapping, Optional

from hypergumbo_core.backend_selection import (
    SCIP_PYTHON_ENV_VAR,
    resolve_scip_python_optin,
)

#: Re-exported so callers and tests import the variable name from here.
ENV_VAR_NAME = SCIP_PYTHON_ENV_VAR


def should_use_scip_python_backend(
    *,
    backend_flag: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    is_available: Optional[Callable[[], bool]] = None,
    repo_root: Optional[Path] = None,
) -> bool:
    """True iff the scip-python backend should run for ``repo_root``."""
    env = environ if environ is not None else os.environ
    decision = resolve_scip_python_optin(
        flag_choice=backend_flag, environ=env, repo_root=repo_root,
    )
    if decision is not True:
        return False
    if is_available is None:
        from hypergumbo_core.scip_python_install import is_scip_python_available

        is_available = is_scip_python_available
    return is_available()
