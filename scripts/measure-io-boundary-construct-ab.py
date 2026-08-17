#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A/B the io-boundary module-hint branch's method-construct filter.

THE QUESTION. ``lookup_with_module``'s module-hint branch drops
``kind == "function"`` candidates when the edge was stamped
``call_construct == "method"`` **and the module slot resolved to more than one
candidate**. The arity clause is not decoration — it is the whole safety
margin, and it is there because the construct-only form of this rule was
shipped, measured, and reverted.

WHY THE ARITY CLAUSE EXISTS (the 51/49 result). The first version of this
filter keyed on the construct alone: "method ⇒ not a free function". On
whisper.cpp it removed **51** io-boundary matches — 2 intended and **49 true
positives**. The premise was false. ``call_construct == "method"`` does not
mean "instance method"; it means "the call was spelled as a selector
expression", and Go spells ``os.Open(p)`` exactly that way, identically in
shape to ``f.Close()``. Re-keyed on module-slot arity, the same filter removes
2 on whisper.cpp and 0 on alertmanager.

That is the standing rule this instrument serves (LIVE.md §2): **A/B any
consumer gate on a Go repo.** A gate verified only on C++ is not verified.

METHOD, and why it is a counterfactual rather than a two-tree A/B. The filter
keys exclusively on ``call_construct`` inside one branch, so "the code without
the filter" is exactly "the code called with the construct withheld" for that
branch. Every lookup is therefore answered TWICE in one process — once as
production calls it, once with ``call_construct=None`` — and the pair compared.
No second checkout, no second analysis run, and no risk that the two arms
differ in anything but the field under test.

WHAT IT REPORTS, and why the denominators are printed. A bare "removed N" is
not a decision input (LIVE.md rule 11). The census names the population at each
narrowing: total lookups, how many carried a usable module hint, how many were
method-construct, how many were both, and only then how many lost a hit. The
removed matches are broken out by primitive so a reviewer can judge whether
what vanished was noise or signal — which is the step that caught the 49.

``ADDED`` should be structurally impossible (the filter only removes
candidates); it is counted anyway, because a control that cannot fail is not a
control.

Run::

    PROBE_REPO=~/ALL_REPOS/<a-go-repo> python scripts/measure-io-boundary-construct-ab.py
    PROBE_REPO=~/ALL_REPOS/whisper.cpp python scripts/measure-io-boundary-construct-ab.py

Reads through production's own CLI entry point and catalogues; nothing is
reimplemented.
"""
from __future__ import annotations

import collections
import os
import sys

from hypergumbo_core import io_boundary as iob

TALLY: collections.Counter = collections.Counter()
LOST: list = []

_real = iob.IoBoundaryCatalog.lookup_with_module


def _traced(self, name, module_hint=None, *, call_construct=None, **kw):
    """Answer every lookup twice: as production asks, and with the field withheld."""
    new = _real(self, name, module_hint, call_construct=call_construct, **kw)
    TALLY["lookups"] += 1
    usable = bool(module_hint) and module_hint != "external"
    if usable:
        TALLY["with_module_hint"] += 1
    if call_construct == "method":
        TALLY["method_construct"] += 1
    if usable and call_construct == "method":
        TALLY["method_construct_with_hint"] += 1
        old = _real(self, name, module_hint, call_construct=None, **kw)
        if old is not None and new is None:
            TALLY["REMOVED"] += 1
            LOST.append((name, module_hint[:60], old.module, old.name, old.kind))
        elif old is None and new is not None:
            TALLY["ADDED"] += 1
    return new


iob.IoBoundaryCatalog.lookup_with_module = _traced

from hypergumbo_core.cli import main  # noqa: E402

repo = os.environ.get("PROBE_REPO")
if not repo:
    print("set PROBE_REPO to the repo to probe", file=sys.stderr)
    raise SystemExit(2)

rc = main(["io-boundaries", repo, "--format", "json"])

print("\n" + "=" * 66, file=sys.stderr)
print(f"io-boundaries rc={rc}  repo={repo}", file=sys.stderr)
for k in ("lookups", "with_module_hint", "method_construct",
          "method_construct_with_hint", "REMOVED", "ADDED"):
    print(f"  {k:<28} {TALLY[k]}", file=sys.stderr)
counted: collections.Counter = collections.Counter(
    (m, n, k) for _, _, m, n, k in LOST
)
print("  removed matches by primitive:", file=sys.stderr)
for (m, n, k), c in counted.most_common(20):
    print(f"    {m}.{n} (kind={k})  x{c}", file=sys.stderr)
print("=" * 66, file=sys.stderr)
