#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A/B the catalogue-derived constructor table on real repositories.

WHAT CHANGED. ``py.EXTERNAL_CONSTRUCTOR_TYPES`` was a hand-curated four-row table
naming three receiver types; it is now DERIVED from the seventeen types
``io_primitives/python.yaml`` already declares in the ``module`` slot of its
``kind: method`` primitives. On the generated reach probe that moved unreachable
primitives 83/215 → 5/215 — but a generated probe is a CEILING. It shows the
shape can resolve; it says nothing about how often real code contains it.

WHY THIS MEASURES EDGES AND NOT io-boundary CHAINS. ``io-boundaries`` caches its
analysis under ``~/.cache/hypergumbo`` keyed on repo state and analyzer identity,
and an in-process monkeypatch changes neither — so a second arm would be served
arm A's map and report a flawless zero delta. That is the "caches fake nulls"
failure mode, and the fix here is to call ``analyze_python`` DIRECTLY, which
touches no cache and is also the exact layer the change lives at.

BOTH DIRECTIONS, NEVER A SINGLE "IMPROVEMENT" NUMBER. Typing a receiver is not
free: an external qualified type walks into the strip-to-bare-name lookup against
the repo's own symbols, the channel behind alertmanager's "13 spurious in-edges".
So call edges are counted as GAINED and LOST separately, and a gain is only
called attributable when its dst module slot is one of the NEWLY mintable types.

POSITIVE CONTROL, printed before any zero is believed: arm B must mint strictly
more receiver types than arm A, and the repo must yield some python at all. A
repo with no python files is reported as such rather than folded in as a zero.

Usage:
    scripts/measure-ctor-table-derivation-ab.py REPO [REPO ...] [--json OUT]
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import pathlib
from typing import Any

import hypergumbo_lang_mainstream.py as py_mod

#: The table exactly as it was hand-curated, so arm A reproduces the old binary.
_LEGACY_TABLE = {
    "open": "file",
    "socket.socket": "socket.socket",
    "Path": "pathlib.Path",
    "pathlib.Path": "pathlib.Path",
}


def _module_slot(dst: str) -> str:
    parts = dst.split(":")
    return ":".join(parts[1:-3]) if len(parts) >= 5 else ""


def _call_edges(repo: pathlib.Path) -> collections.Counter:
    """(module_slot, name_slot) for every method-construct call edge."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        analysis = py_mod.analyze_python(repo)
    counter: collections.Counter = collections.Counter()
    for edge in analysis.edges:
        if edge.edge_type != "calls":
            continue
        dst = edge.dst
        parts = dst.split(":")
        if len(parts) < 5:
            continue
        counter[(_module_slot(dst), parts[-2])] += 1
    return counter


def _run_arm(repo: pathlib.Path, *, derived: bool) -> collections.Counter:
    real = py_mod.EXTERNAL_CONSTRUCTOR_TYPES
    if not derived:
        py_mod.EXTERNAL_CONSTRUCTOR_TYPES = dict(_LEGACY_TABLE)
    try:
        return _call_edges(repo)
    finally:
        py_mod.EXTERNAL_CONSTRUCTOR_TYPES = real


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--json", dest="out")
    args = ap.parse_args()

    derived_types = set(py_mod.EXTERNAL_CONSTRUCTOR_TYPES.values())
    legacy_types = set(_LEGACY_TABLE.values())
    newly = sorted(derived_types - legacy_types)
    print("POSITIVE CONTROL (the arms must be different binaries):")
    print(f"  receiver types, arm A (legacy) : {len(legacy_types)}")
    print(f"  receiver types, arm B (derived): {len(derived_types)}")
    print(f"  newly mintable                 : {len(newly)}")
    if not newly:
        print("\nARMS ARE IDENTICAL. Nothing below is measured. Refusing to report.")
        return 2

    rows: list[dict[str, Any]] = []
    for repo_str in args.repos:
        repo = pathlib.Path(repo_str).resolve()
        print(f"\n=== {repo} ===", flush=True)
        before = _run_arm(repo, derived=False)
        after = _run_arm(repo, derived=True)
        if not before and not after:
            print("  NO PYTHON CALL EDGES — excluded from the denominator, not "
                  "counted as a zero.")
            continue
        gained = after - before
        lost = before - after
        attributable = sum(
            n for (mod, _), n in gained.items() if mod in set(newly)
        )
        row = {
            "repo": str(repo),
            "edges_before": sum(before.values()),
            "edges_after": sum(after.values()),
            "gained": sum(gained.values()),
            "lost": sum(lost.values()),
            "gained_attributable_to_new_types": attributable,
            "gained_top": {f"{m}.{n}": c for (m, n), c in gained.most_common(12)},
            "lost_top": {f"{m}.{n}": c for (m, n), c in lost.most_common(12)},
        }
        rows.append(row)
        print(f"  call edges before/after : {row['edges_before']} / "
              f"{row['edges_after']}")
        print(f"  GAINED                  : {row['gained']}  "
              f"({attributable} onto a newly mintable receiver type)")
        print(f"  LOST                    : {row['lost']}")
        if row["gained_top"]:
            print(f"  gained top              : {row['gained_top']}")
        if row["lost_top"]:
            print(f"  LOST top                : {row['lost_top']}")

    print("\n=== VERDICT ===")
    if not rows:
        print("NO REPO YIELDED PYTHON CALL EDGES. Nothing measured.")
        return 2
    tg = sum(r["gained"] for r in rows)
    tl = sum(r["lost"] for r in rows)
    ta = sum(r["gained_attributable_to_new_types"] for r in rows)
    print(f"gained {tg} edge(s) ({ta} onto a newly mintable type), "
          f"lost {tl} edge(s), across {len(rows)} repo(s)")
    if tl:
        print("LOSSES ARE NOT ZERO. A receiver that used to resolve one way now "
              "resolves another; read lost_top before shipping.")
    if tg and ta != tg:
        print(f"{tg - ta} gained edge(s) are NOT attributable to a newly "
              f"mintable type — a side effect, not the intended win.")
    if not tg:
        print("NO REPO IN THIS COHORT CONTAINS THE SHAPE. A statement about the "
              "cohort, not evidence the change is inert: the reach probe already "
              "showed the shape resolves.")

    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(
            {"newly_mintable": newly, "repos": rows}, indent=2))
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
