#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measure one ARM of the Go composite-literal receiver-typing change.

THE CHANGE. ``c := &http.Client{}; c.Do(req)`` bound no receiver type, so the call
emitted ``go:external:0-0:Do:unresolved`` and the no-module gate (io-boundary:F3)
refused it -- ``Do`` sits in go.yaml's ``ambiguous_names`` by design, because a bare
``.Do()`` collides with every other ``Do`` in a corpus. Receiver typing is the only
thing that can reach those rows.

WHY THIS SCRIPT MEASURES ONE ARM AND NOT BOTH. The precedent instrument for the Python
constructor-root fix (``measure-ctor-root-typing-ab.py``) reverted the fix in-process
with a single scoped knob, because that change had exactly one insertion point. This one
has two observable parts -- the type extraction AND the WI-jopar guard that now keys on
the receiver instead of on hint-absence -- and the second is observable on receivers the
first never touches (``var c http.Client``, typed parameters). A single-knob arm would
therefore report a SMALLER change than the one that ships. So each arm runs in its own
process against its own working tree, and the driver diffs the two JSON outputs:

    git stash push -- <go.py>  &&  this --json pre.json  REPO...
    git stash pop              &&  this --json post.json REPO...

DIRECTION, REPORTED BOTH WAYS. Typing a receiver moves two counters in opposite
directions and only one of them is safe:

  RECALL (adds)       a typed receiver reaches its go.yaml row, so real network I/O that
                      was invisible becomes a boundary and a taint-sink candidate.
  SUPPRESSION (drops) a typed dst also populates ``callees_at``, letting the walk decide
                      a use site it previously had to treat as an escape. Since PR #214
                      a ``False`` earns ``sanitized`` and DROPS the flow from the
                      violation set.

A rise in boundaries does not license ignoring a fall in anything else, so this prints
resolved/unresolved and per-category counts rather than a single headline.

POSITIVE CONTROL, printed before any corpus number is believed. A fixture whose arms are
KNOWN to differ (``&http.Client{}`` -> 0 tagged boundaries before, 1 after). If it reads
0 in the post arm the instrument cannot see this change at all and every corpus zero it
reports is an uncontrolled null. This project has produced several of those.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
from collections import Counter

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_lang_mainstream.go import analyze_go

_CONTROL = '''\
package main

import "net/http"

func main() {
    req, _ := http.NewRequest("GET", "http://example.com", nil)
    client := &http.Client{}
    client.Do(req)
}
'''


def _measure(repo: pathlib.Path) -> dict:
    """Analyze one repo and count what the boundary tagger makes of its call edges."""
    analysis = analyze_go(repo)
    if analysis.skipped:  # pragma: no cover - grammar guard
        return {"skipped": True}

    calls = [e for e in analysis.edges if e.edge_type == "calls"]
    counts: Counter = Counter()
    counts["call_edges"] = len(calls)
    for edge in calls:
        parts = edge.dst.split(":")
        module_slot = parts[1] if len(parts) > 1 else ""
        counts["dst_external_placeholder"] += int(module_slot == "external")
        counts["is_resolved_true"] += int(bool(edge.is_resolved))
        # THE SAFETY COUNTER. ``is_resolved_true`` falling is ambiguous on its own: it
        # drops both when a wrongly-"resolved" EXTERNAL edge is corrected to False
        # (harmless -- finalize overwrites the producer verdict anyway) and when a
        # genuine INTRA-REPO edge stops resolving (destructive -- a real call
        # relationship is lost). Only the second matters, and the two are told apart by
        # the dst: an in-repo target names a FILE PATH in the module slot, an external
        # one names a package or the placeholder.
        counts["dst_intra_repo"] += int(module_slot.startswith(("/", "\\")))

    catalog = load_catalog("go")
    tagged = tag_io_boundaries(calls, {"go": catalog})
    counts["io_boundaries_tagged"] = tagged

    by_category: Counter = Counter()
    for edge in calls:
        cat = (edge.meta or {}).get("io_boundary")
        if cat:
            by_category[cat] += 1
    return {"counts": dict(counts), "by_category": dict(by_category)}


def _positive_control() -> dict:
    """Does the ``client.Do(req)`` edge specifically reach a catalogue row?

    Counting the fixture's TOTAL tags is not a control: ``http.NewRequest`` is itself a
    catalogued net/http function, so the fixture tags 1 in BOTH arms and a
    ``tagged > 0`` test reads as "change visible" in the arm where the change is
    absent. The control has to name the edge under test.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = pathlib.Path(tmp) / "ctl"
        repo.mkdir()
        (repo / "go.mod").write_text("module example.com/ctl\n\ngo 1.21\n")
        (repo / "main.go").write_text(_CONTROL)
        analysis = analyze_go(repo)
        calls = [e for e in analysis.edges if e.edge_type == "calls"]
        tag_io_boundaries(calls, {"go": load_catalog("go")})
        do_edges = [
            e for e in calls if e.dst.split(":")[-2].split(".")[-1] == "Do"
        ]
        return {
            "do_edge_dsts": [e.dst for e in do_edges],
            "do_edge_primitive": [
                (e.meta or {}).get("io_primitive") for e in do_edges
            ],
            "do_edge_tagged": sum(
                1 for e in do_edges if (e.meta or {}).get("io_boundary")
            ),
            "total_tagged": sum(
                1 for e in calls if (e.meta or {}).get("io_boundary")
            ),
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repos", nargs="*")
    ap.add_argument("--json", dest="out")
    ap.add_argument("--dump-edges", dest="dump",
                    help="write every call edge as 'src\\tdst' so the two arms can be "
                         "diffed edge-by-edge; an aggregate delta cannot tell a "
                         "removed FALSE edge from a removed TRUE one")
    args = ap.parse_args()

    control = _positive_control()
    print(
        f"POSITIVE CONTROL  &http.Client{{}} -> client.Do(req)\n"
        f"  Do edge dst   : {control['do_edge_dsts']}\n"
        f"  Do edge TAGGED: {control['do_edge_tagged']}   "
        f"(expected 0 in the PRE arm, 1 in the POST arm)\n"
        f"  fixture total : {control['total_tagged']} "
        f"(NOT a control — http.NewRequest tags in BOTH arms)",
        file=sys.stderr,
    )

    report: dict = {"control": control, "repos": {}}
    total: Counter = Counter()
    for raw in args.repos:
        repo = pathlib.Path(raw)
        res = _measure(repo)
        report["repos"][repo.name] = res
        total.update(res.get("counts", {}))
        c = res.get("counts", {})
        print(
            f"{repo.name:16s} calls={c.get('call_edges', 0):6d} "
            f"external_slot={c.get('dst_external_placeholder', 0):6d} "
            f"resolved={c.get('is_resolved_true', 0):6d} "
            f"intra_repo={c.get('dst_intra_repo', 0):6d} "
            f"tagged={c.get('io_boundaries_tagged', 0):5d}",
            file=sys.stderr,
        )
    if args.dump:
        lines = []
        for raw in args.repos:
            repo = pathlib.Path(raw)
            analysis = analyze_go(repo)
            for e in analysis.edges:
                if e.edge_type == "calls":
                    lines.append(f"{repo.name}\t{e.src}\t{e.dst}")
        pathlib.Path(args.dump).write_text("\n".join(sorted(lines)))
    report["TOTAL"] = dict(total)

    text = json.dumps(report, indent=2)
    if args.out:
        pathlib.Path(args.out).write_text(text)
    else:  # pragma: no cover - interactive use
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
