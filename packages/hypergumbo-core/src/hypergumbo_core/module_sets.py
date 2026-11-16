# SPDX-License-Identifier: AGPL-3.0-or-later
"""A disjunctive module slot is stored once and named by a short handle (INV-jahuf).

WHAT A DISJUNCTIVE SLOT IS. A C or C++ call the analyzer cannot resolve is
stamped with every header its file can see (``include_closure``, INV-zikul):
"this call could come from any of these". The stamp is read twice, through
``io_boundary.module_hint_disjuncts`` and ``_module_hint_candidates``.
Classification asks ANY (does some header declare the name?). The coverage
gate asks ALL (INV-zimud: was every header enumerated?). Both need the
whole list, so it cannot be shortened (INV-valom).

WHY IT IS NOT WRITTEN INTO THE SLOT. The slot is ``ExternalRef.module_path``,
which is part of the external symbol's id, its display label, the edge's
``dst`` and its ``dst_ref``. INV-valom made the list every visible header.
On envoy a file sees ~140 headers, so each of those four strings carried
1-12 KB, once per call site. envoy-half's survey had 151,591 such edges and
only 3,657 distinct lists, about one per file: 2.07 GB of repeated text
against 14 MB of distinct content. The survey was 5.5 GB on full envoy, and
``verify-claims`` was killed at 20.5 GB when ``load_substrate`` re-read it.

WHAT THE SLOT HOLDS INSTEAD. A slot with two or more disjuncts becomes
``module-set-<16 hex>``, the first 16 hex digits of the sha256 of the
comma-joined list. :func:`module_slot` makes the handle and records the list
in a process-wide table. :func:`expand_module_slot` turns a handle back into
the comma-joined list, and it is called at the two expansion points, so
every consumer downstream of them sees exactly the string it saw before.
A slot with one disjunct is written as itself (``cstdio``), so single-module
slots in every language are byte-identical to what they were.

WHY A PROCESS-WIDE TABLE IS SAFE. The handle is content-addressed: one
handle names one list in every run, so entries from different runs and
different repos can share the table without conflict, and registering an
entry twice is a no-op. Nothing has to be threaded through call signatures
that only ever see the slot string.

HOW THE TABLE TRAVELS. finalize writes every handle the edges reference to
the behavior map's top-level ``module_sets`` (handle -> list of disjuncts,
sorted by handle). ``survey_io.load_behavior_map`` registers that table on
every load, so a re-read survey (the ``verify-claims`` hand-off, a cached
survey, ``--input``) expands exactly as the in-process run did. On load each
handle is recomputed from its list, and an entry whose handle does not match
is refused with a warning, because a hand-edited table would otherwise move
verdicts with no trace.

AN UNKNOWN HANDLE FAILS CLOSED. A handle with no table entry expands to
itself: one disjunct that no catalogue enumerates. Coverage then withholds,
and the reason names the handle. Classification gets fewer matches, and the
withheld coverage means the verdict is ``inconclusive``, never a confirmed
all-clear. A warning is printed once per handle.

STATED LIMIT. The handle bounds the SIZE of an id. It does not make the id
independent of call-site context: the same ``fopen`` still gets one external
node per distinct include list (52,571 nodes for 12,160 names on envoy-half).
That half of INV-jahuf's statement is a separate decision.
"""

from __future__ import annotations

import hashlib
import sys
from typing import Iterable, Mapping, Sequence

#: Every handle starts with this. No catalogue module, header or package
#: in any language is spelled this way.
MODULE_SET_PREFIX = "module-set-"

_HEX_DIGITS = 16

_SETS: dict[str, str] = {}
_WARNED: set[str] = set()


def _handle_for(joined: str) -> str:
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return f"{MODULE_SET_PREFIX}{digest[:_HEX_DIGITS]}"


def is_module_set_handle(slot: str) -> bool:
    """Whether ``slot`` is spelled as a handle (registered or not)."""
    rest = slot[len(MODULE_SET_PREFIX):]
    return (
        slot.startswith(MODULE_SET_PREFIX)
        and len(rest) == _HEX_DIGITS
        and all(c in "0123456789abcdef" for c in rest)
    )


def module_slot(disjuncts: Sequence[str]) -> str:
    """The module slot for a call that could come from any of ``disjuncts``.

    One disjunct is the slot itself. Two or more become a handle, recorded
    so :func:`expand_module_slot` can give the list back. Order is kept:
    the coverage gate reports unenumerated disjuncts in slot order.
    """
    if len(disjuncts) == 1:
        return disjuncts[0]
    joined = ",".join(disjuncts)
    handle = _handle_for(joined)
    _SETS.setdefault(handle, joined)
    return handle


def expand_module_slot(slot: str) -> str:
    """The comma-joined disjunct list a handle names; any other slot as is."""
    if not is_module_set_handle(slot):
        return slot
    joined = _SETS.get(slot)
    if joined is None:
        if slot not in _WARNED:
            _WARNED.add(slot)
            print(
                f"Warning: module slot {slot} has no module_sets entry; it is "
                f"read as one unenumerated module (INV-jahuf).",
                file=sys.stderr,
            )
        return slot
    return joined


def register_module_sets(table: Mapping[str, Sequence[str]]) -> None:
    """Record a serialized ``module_sets`` table (handle -> disjuncts)."""
    for handle, disjuncts in table.items():
        joined = ",".join(disjuncts)
        if _handle_for(joined) != handle:
            print(
                f"Warning: module_sets entry {handle} does not hash to its "
                f"disjuncts; refused (INV-jahuf).",
                file=sys.stderr,
            )
            continue
        _SETS.setdefault(handle, joined)


def module_sets_for(slots: Iterable[str]) -> dict[str, list[str]]:
    """The serializable table for every registered handle among ``slots``."""
    out: dict[str, list[str]] = {}
    for slot in slots:
        if slot in out or not is_module_set_handle(slot):
            continue
        joined = _SETS.get(slot)
        if joined is not None:
            out[slot] = joined.split(",")
    return dict(sorted(out.items()))
