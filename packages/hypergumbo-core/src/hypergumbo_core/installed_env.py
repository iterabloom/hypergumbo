# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is installed changes rarely: read it once per environment.

Several answers a run needs are facts about the Python environment and not
about the repository being analyzed: which tree-sitter grammars are installed
and at what version (``schema._detect_tree_sitter_versions``), which import
names an installed distribution provides (``py_deps``). Each is computed by
reading the metadata of every installed distribution through
``importlib.metadata``, which opens and parses one ``METADATA`` file per
distribution: 33 ms for the grammar versions and 360 ms for the import-name
map in a development environment of 260 distributions. A run on a one-file
repository takes about 100 ms in all, so recomputing them on every run was a
third of it, and a test suite makes thousands of runs in one process.

How it works
------------
:func:`environment_key` is a value that changes when the set of installed
distributions changes: for each ``sys.path`` entry, the metadata directories
in it (``*.dist-info``, ``*.egg-info``) with their modification times. Those
directories are exactly what ``importlib.metadata`` reads, and installing,
upgrading, reinstalling or removing a distribution adds, removes or rewrites
one. An entry that cannot be listed (it does not exist, or is a zip file) is
in the key as such, so creating it later changes the key.

The key is deliberately NOT the modification time of the ``sys.path`` entries
themselves. An entry is often a working directory (the repository root, under
pytest), which changes whenever anything writes a file there; a key made of
that rebuilt on every call.

:func:`kept_per_environment` holds one answer per *slot* for as long as the
key stays the same, and drops every answer when it changes. A slot is any
hashable value; a caller puts into it whatever else its answer depends on.
The convention used by the callers here is to include the
``importlib.metadata`` functions the builder calls, so that a test's stand-in
for one of them is a different slot and is never answered with the real
environment's value, or the reverse.

Computing the key costs about a millisecond (one directory listing per
``sys.path`` entry, one ``stat`` per metadata directory), so this pays only
for an answer that costs clearly more than that: a single
``importlib.metadata.version(name)`` lookup (0.1 to 0.3 ms) is cheaper left
alone.

What is kept is handed to every later caller as the same object. A builder
must therefore return something immutable, or its caller must copy before
handing it on.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Hashable, TypeVar

_T = TypeVar("_T")

#: The key the kept answers were built under, and the answers by slot.
_KEPT: "tuple[tuple[Any, ...], dict[Hashable, Any]] | None" = None


def environment_key() -> tuple[Any, ...]:
    """A value that changes when the installed distributions change.

    See the module docstring for what it is made of and what it leaves out.
    """
    entries: list[tuple[str, tuple[tuple[str, int], ...] | None]] = []
    for entry in sys.path:
        try:
            with os.scandir(entry or ".") as listing:
                found = sorted(
                    (item.name, item.stat().st_mtime_ns) for item in listing
                    if item.name.endswith((".dist-info", ".egg-info"))
                )
        except OSError:
            entries.append((entry, None))
            continue
        entries.append((entry, tuple(found)))
    return tuple(entries)


def kept_per_environment(slot: Hashable, build: Callable[[], _T]) -> _T:
    """``build()``, computed once per environment and slot.

    The first call for ``slot`` under the current :func:`environment_key`
    runs ``build`` and keeps what it returns; later calls return that same
    object without running it. When the key changes, everything kept is
    dropped. A ``build`` that raises keeps nothing.
    """
    global _KEPT
    key = environment_key()
    kept = _KEPT
    if kept is None or kept[0] != key:
        kept = _KEPT = (key, {})
    answers = kept[1]
    if slot not in answers:
        answers[slot] = build()
    answer: _T = answers[slot]
    return answer
