#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-language census of ``meta.call_construct`` on the call edges of a behavior map.

THE QUESTION THIS SETTLES. ``call_construct`` is consumed by the io-boundary F3
gate, which does TWO jobs with it — refuse an unresolved ``"method"`` call
(precision) and filter ``non_method`` entries when there is no module hint
(recall). Open items fail different jobs, so "does language X stamp the field?"
is the wrong question and the wrong number. The right one is: **on how many of
its call edges, and with which VALUES.**

WHY VALUES AND NOT PRESENCE (LIVE.md rule 11). Asking only "does cpp carry the
field" returns 41.1% and reads like adequate coverage. Asking which value it
carries returns ``method`` on **1 of 34,983** call edges — and ``method`` is the
only value the gate reads. Presence and usefulness differ here by four orders of
magnitude, so this script prints the value breakdown next to the rate and never
the rate alone.

WHY ONLY CALL EDGES. The F3 gate consults call edges exclusively. Counting
containment or import edges in the denominator would deflate every language's
rate uniformly and hide which ones are actually starved.

TWO FIELD-NAME TRAPS, both of which produced empty or wrong output before being
found: the *serialized* edge key is ``type`` (the IR dataclass attribute is
``edge_type``), and language attribution is read from ``origin[0]`` rather than
parsed out of the ``src`` symbol-id prefix.

Run::

    python scripts/measure-call-construct-census.py <survey.json> [more.json ...]
"""
from __future__ import annotations

import collections
import json
import sys

# The F3 gate only consults call edges; containment/import edges are not
# candidates for it, so counting them would deflate every rate uniformly and
# hide which languages are actually starved.
CALL_TYPES = {"calls", "imported_call", "dispatches_to", "method_call"}

if len(sys.argv) < 2:
    print(__doc__, file=sys.stderr)
    raise SystemExit(2)

for path in sys.argv[1:]:
    with open(path) as fh:
        data = json.load(fh)
    edges = data.get("edges", [])
    per_lang: dict = collections.defaultdict(
        lambda: {"call_edges": 0, "with": 0, "values": collections.Counter()}
    )
    for e in edges:
        if e.get("type") not in CALL_TYPES:
            continue
        origin = e.get("origin") or ["?"]
        lang = origin[0]
        row = per_lang[lang]
        row["call_edges"] += 1
        meta = e.get("meta") or {}
        cc = meta.get("call_construct")
        if cc is not None:
            row["with"] += 1
            row["values"][cc] += 1

    print(f"\n=== {path}  ({len(edges)} edges total) ===")
    print(f"{'language':<14}{'call edges':>11}{'w/ construct':>14}{'rate':>8}   values")
    for lang, row in sorted(per_lang.items(), key=lambda kv: -kv[1]["call_edges"]):
        n, w = row["call_edges"], row["with"]
        rate = f"{100.0 * w / n:.1f}%" if n else "-"
        vals = ", ".join(f"{k}={v}" for k, v in row["values"].most_common())
        print(f"{lang:<14}{n:>11}{w:>14}{rate:>8}   {vals}")
