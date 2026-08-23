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

from hypergumbo_core.backend_selection import (
    RUST_ANALYZER_ENV_VAR,
    RUST_ANALYZER_OFF_FLAGS,
    RUST_ANALYZER_ON_FLAGS,
    resolve_optin,
)

#: Re-exported from :mod:`hypergumbo_core.backend_selection`, which owns the
#: name so the CLI can resolve the same choice without importing this
#: optional package. Kept as a module attribute because callers and tests
#: import it from here.
ENV_VAR_NAME = RUST_ANALYZER_ENV_VAR

# Backend-selector strings accepted by the --backend CLI flag. ``None`` /
# empty string means "use the default (tree-sitter rust.py)".
_RUST_ANALYZER_FLAG_VALUES = RUST_ANALYZER_ON_FLAGS

# Backend-selector strings that explicitly DESELECT the SCIP backend. These
# are load-bearing rather than cosmetic: before ADR-0045 ruling 4 there was no
# such set, so ``--backend tree-sitter`` was a silent no-op and could not turn
# off a backend that ``HYPERGUMBO_RUST_ANALYZER=1`` had turned on — which for
# this backend means the analysed repository's ``build.rs`` ran anyway, after
# the tool had told the user how to prevent exactly that.
_TREE_SITTER_FLAG_VALUES = RUST_ANALYZER_OFF_FLAGS


def _is_env_enabled(environ: Mapping[str, str]) -> bool:
    """Return True when the opt-in env var resolves to a truthy value.

    Delegates rather than re-deciding. Truthiness had two homes for exactly
    as long as this function parsed the variable itself and
    :mod:`hypergumbo_core.backend_selection` also did — the classic shape
    where the second home wins silently once the two drift.
    """
    return resolve_optin(
        flag_choice=None,
        environ=environ,
        env_var=ENV_VAR_NAME,
        on_flag_values=_RUST_ANALYZER_FLAG_VALUES,
        off_flag_values=_TREE_SITTER_FLAG_VALUES,
    ) is True


def _is_flag_enabled(backend_flag: Optional[str]) -> bool:
    """Return True when the caller-supplied ``--backend`` flag selects SCIP.

    Note the asymmetry with the resolver this delegates to: a flag that
    explicitly deselects the backend is ``False`` here and *also* ``False``
    from :func:`resolve_optin`, but for different reasons — here it merely
    "does not select SCIP", there it is a decision that outranks the
    environment. Callers wanting the second meaning must ask the resolver.
    """
    return resolve_optin(
        flag_choice=backend_flag,
        environ={},
        env_var=ENV_VAR_NAME,
        on_flag_values=_RUST_ANALYZER_FLAG_VALUES,
        off_flag_values=_TREE_SITTER_FLAG_VALUES,
    ) is True


def should_use_rust_analyzer_backend(
    *,
    backend_flag: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    is_available: Optional[Callable[[], bool]] = None,
) -> bool:
    """Return True iff the rust-analyzer backend should run.

    Two conditions must both hold:

    - The opt-in resolved to True. ``backend_flag`` and the
      ``HYPERGUMBO_RUST_ANALYZER`` env var are TIERS, not alternatives:
      the flag outranks the variable in both directions, so
      ``--backend tree-sitter`` turns the backend off even when the variable
      says on (ADR-0045 ruling 4). Ordering lives in
      :func:`hypergumbo_core.backend_selection.resolve_optin`, not here.
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
    decision = resolve_optin(
        flag_choice=backend_flag,
        environ=env,
        env_var=ENV_VAR_NAME,
        on_flag_values=_RUST_ANALYZER_FLAG_VALUES,
        off_flag_values=_TREE_SITTER_FLAG_VALUES,
    )
    if decision is not True:
        return False

    if is_available is None:
        from hypergumbo_core.rust_analyzer_install import (
            is_rust_analyzer_available,
        )
        is_available = is_rust_analyzer_available
    return is_available()
