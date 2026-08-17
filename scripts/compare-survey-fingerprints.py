#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Diff ``Symbol.fingerprint`` across two surveys of the same tree.

WHY (WI-balaf). The python fingerprint locator was rewritten to consult a
per-tree index instead of rescanning the whole AST per symbol. A fingerprint is
SERIALIZED OUTPUT, so the rewrite's contract is "identical values, less time" —
and unit tests, however exhaustive on a handful of files, cover a handful of
files. This compares every fingerprint in a before-survey against an
after-survey of the same tree, which is the population the change actually
touches: tens of thousands of symbols across every construct the repo contains.

WHAT A DIFFERENCE MEANS. Any changed fingerprint is a REGRESSION, not a
finding: the located subtree moved, so the hash of "the same code" moved with
it. Consumers key on these values, and a silent shift would look like every
affected symbol had been edited.

WHAT THIS CANNOT TELL YOU. Symbols present in only one survey are reported
separately and are NOT fingerprint differences — they mean the two runs
disagreed about the node set (a dirty tree between runs, a non-deterministic id,
a differently-configured arm). Treat a nonzero only-in count as a reason to
distrust the comparison's denominator, not as a pass.

USAGE
    scripts/compare-survey-fingerprints.py BEFORE.json AFTER.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _fingerprints(path: Path) -> Tuple[Dict[str, Optional[str]], Counter]:
    """Map node id -> fingerprint, plus a per-language node census."""
    with path.open() as fh:
        survey = json.load(fh)
    out: Dict[str, Optional[str]] = {}
    langs: Counter = Counter()
    for node in survey.get("nodes", []):
        node_id = node.get("id")
        if not node_id:
            continue
        out[node_id] = node.get("fingerprint")
        langs[node.get("language") or "<none>"] += 1
    return out, langs


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Diff Symbol.fingerprint between two surveys.",
    )
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--show", type=int, default=15,
                    help="How many differing ids to print (default 15).")
    args = ap.parse_args(argv)

    before_path, after_path = Path(args.before), Path(args.after)
    for path in (before_path, after_path):
        if not path.is_file():
            print(f"error: no survey at {path}", file=sys.stderr)
            return 2

    print(f"reading {before_path} ...", file=sys.stderr)
    before, before_langs = _fingerprints(before_path)
    print(f"reading {after_path} ...", file=sys.stderr)
    after, after_langs = _fingerprints(after_path)

    shared = before.keys() & after.keys()
    only_before = before.keys() - after.keys()
    only_after = after.keys() - before.keys()

    changed = [nid for nid in shared if before[nid] != after[nid]]
    with_fp_before = sum(1 for nid in shared if before[nid])
    with_fp_after = sum(1 for nid in shared if after[nid])

    print()
    print("=" * 74)
    print("FINGERPRINT EQUIVALENCE")
    print("=" * 74)
    print(f"  nodes in before                 {len(before)}")
    print(f"  nodes in after                  {len(after)}")
    print(f"  shared ids (the comparison set) {len(shared)}")
    print(f"  only in before                  {len(only_before)}")
    print(f"  only in after                   {len(only_after)}")
    print(f"  shared with a fingerprint       "
          f"{with_fp_before} before / {with_fp_after} after")
    print(f"  CHANGED fingerprints            {len(changed)}")
    print()

    if only_before or only_after:
        print("  NOTE: the node sets differ, so the denominator above is the")
        print("  intersection only. Investigate before trusting a clean diff.")
        print()

    if changed:
        print("REGRESSIONS (a changed fingerprint means the located subtree moved):")
        for nid in sorted(changed)[: args.show]:
            print(f"  {nid}")
            print(f"     before: {before[nid]}")
            print(f"      after: {after[nid]}")
        if len(changed) > args.show:
            print(f"  ... and {len(changed) - args.show} more")
        print()
        print("VERDICT: NOT EQUIVALENT")
        return 1

    if with_fp_before == 0:
        print("VERDICT: VACUOUS — no fingerprints present in the before survey,")
        print("so agreement proves nothing. Check the surveys are real.")
        return 1

    print(f"language census (before): "
          f"{dict(before_langs.most_common(6))}")
    print(f"language census (after):  "
          f"{dict(after_langs.most_common(6))}")
    print()
    print(f"VERDICT: EQUIVALENT over {len(shared)} shared nodes "
          f"({with_fp_before} carrying a fingerprint)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
