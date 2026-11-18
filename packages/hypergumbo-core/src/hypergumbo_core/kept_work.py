# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work a function keeps for later calls, shown to the test recorder.

WHY. Selection by coverage pairs each test with the statements it ran. A
function that keeps what it computed (a dict filled on first use, a value
put in a module global, a warning given once) runs that work under the
FIRST test that calls it; every later caller is handed the result and has no
row for the code that made it. A change to that code then selects one of its
dependents, and the others fail unselected. ``functools.lru_cache`` is
handled by the recorder itself (it replaces it before the repository is
imported); a cache written by hand has no boundary it can see, so the
function draws its own:

    def load(name):
        kept_work.used(load, name)          # every call, hit or miss
        if name in _CACHE:
            return _CACHE[name]
        with kept_work.filling(load, name):
            _CACHE[name] = parse(name)      # the work that is kept
        return _CACHE[name]

``fn`` is the function itself: its code and qualified name name the work
(``<file>::<qualname>``, the name a cached function gets). ``entry`` names
what one fill kept (the cache key; ``None`` when the function keeps one
thing); what a fill read or started is handed to the later callers of THAT
entry, not of every entry. A function that keeps nothing but a flag (a
warning given once) marks the branch that sets it the same way.

THE RECORDER. ``scripts/pytest_plugins/pytest_source_reads.py`` runs a fill
under the function's own coverage context, records every call as a use by
the running test, and the selection index gives every user the rows of the
work (``selection_index``, semantic 6). It is ``HOOK``: found here when this
module is imported after the plugin, set by the plugin when this module was
imported first. Outside a recorded test run ``HOOK`` is None and a mark is
one global read and one call.

THE GUARD. ``scripts/check-kept-work`` reads the syntax tree of the
repository's packages and fails on a function that writes module state or a
lazy attribute and neither calls ``filling`` nor is exempted with a reason.
"""
from __future__ import annotations

import contextlib
import sys
from typing import Any, Callable, ContextManager, Hashable, Optional, Protocol

#: The module name the recorder plugin is loaded under (``-p pytest_source_reads``).
RECORDER_MODULE = "pytest_source_reads"


class Recorder(Protocol):
    """What the recorder plugin provides."""

    def used(self, fn: Callable[..., Any], entry: Hashable) -> None: ...  # pragma: no cover

    def filling(self, fn: Callable[..., Any],
                entry: Hashable) -> ContextManager[None]: ...  # pragma: no cover


def installed_recorder() -> Optional[Recorder]:
    """The recorder of the loaded plugin module, if one is loaded."""
    return getattr(sys.modules.get(RECORDER_MODULE), "KEPT_WORK_RECORDER", None)


HOOK: Optional[Recorder] = installed_recorder()

_NOTHING = contextlib.nullcontext()


def used(fn: Callable[..., Any], entry: Hashable = None) -> None:
    """A call of ``fn`` that is handed (or makes) the work kept for ``entry``."""
    if HOOK is not None:
        HOOK.used(fn, entry)


def filling(fn: Callable[..., Any], entry: Hashable = None) -> ContextManager[None]:
    """The block that does the work ``fn`` keeps for ``entry``."""
    return _NOTHING if HOOK is None else HOOK.filling(fn, entry)
