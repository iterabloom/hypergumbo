# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opt-in gate for the SCIP-backed Rust analyzer (WI-duzul Slice C gate).

The rust-analyzer backend is opt-in because SCIP indexing is ~10x slower
than tree-sitter at every realistic size (WI-zakub §4). Activating it
requires two conditions:

1. The user explicitly asked for it, either via the
   ``HYPERGUMBO_RUST_ANALYZER`` environment variable (``"1"`` / ``"true"``
   / ``"yes"``, case-insensitive) OR via the ``--backend rust-analyzer``
   CLI flag (which the caller resolves to a string and passes in).
2. The ``rust-analyzer`` binary is resolvable on ``PATH``
   (:func:`hypergumbo_core.rust_analyzer_install.is_rust_analyzer_available`).

:func:`should_use_rust_analyzer_backend` is the single decision point.
The function is pure — ``environ`` / ``is_available`` are injected so
tests can exercise every branch without mutating ``os.environ`` or
shelling out to ``shutil.which``. Production callers pass ``None`` for
both and pick up :data:`os.environ` + the real availability check.

The split between this module and :mod:`graceful_degrade` is
intentional: graceful-degrade answers "the user asked; did it work?"
(handling runtime failures), while this gate answers "did the user
actually ask?" (handling opt-in). Slice C's analyzer-registry wrapper
chains them in that order.
"""

from __future__ import annotations

import os
from typing import Callable, Mapping, Optional

ENV_VAR_NAME = "HYPERGUMBO_RUST_ANALYZER"

# Strings accepted as "yes, opt in" (case-insensitive). Matches the
# convention used by other tools' truthy-env-var parsing.
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

# Backend-selector strings accepted by the --backend CLI flag. ``None`` /
# empty string means "use the default (tree-sitter rust.py)".
_RUST_ANALYZER_FLAG_VALUES = frozenset({"rust-analyzer", "rust_analyzer", "scip"})


def _is_env_enabled(environ: Mapping[str, str]) -> bool:
    """Return True when the opt-in env var resolves to a truthy value."""
    raw = environ.get(ENV_VAR_NAME, "")
    return raw.strip().lower() in _TRUTHY_VALUES


def _is_flag_enabled(backend_flag: Optional[str]) -> bool:
    """Return True when the caller-supplied ``--backend`` flag selects SCIP."""
    if backend_flag is None:
        return False
    return backend_flag.strip().lower() in _RUST_ANALYZER_FLAG_VALUES


def should_use_rust_analyzer_backend(
    *,
    backend_flag: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    is_available: Optional[Callable[[], bool]] = None,
) -> bool:
    """Return True iff the rust-analyzer backend should run.

    Two conditions must both hold:

    - The user opted in, via either ``backend_flag`` or the
      ``HYPERGUMBO_RUST_ANALYZER`` env var. Either one alone is enough.
    - The ``rust-analyzer`` binary is resolvable on ``PATH`` (so
      activating the backend cannot fail at spawn-time for a configuration
      error the user can fix with ``hypergumbo install-rust-analyzer``).

    When the user opted in but the binary is missing, this helper
    returns False silently — the opt-in is honoured up to the limits of
    what is installed, and the analyzer-registry wrapper falls through
    to ``rust.py``. Callers that want to warn the user about the
    mismatch should check the two conditions separately.
    """
    env = environ if environ is not None else os.environ
    if not (_is_flag_enabled(backend_flag) or _is_env_enabled(env)):
        return False

    if is_available is None:
        from hypergumbo_core.rust_analyzer_install import (
            is_rust_analyzer_available,
        )
        is_available = is_rust_analyzer_available
    return is_available()
