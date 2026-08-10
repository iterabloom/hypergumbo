#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Count symbol ids whose colon layout makes two parsers disagree (INV-fokik / WI-ribuz).

THE QUESTION, and why it is not answerable from the grammar alone. ADR-0036 Ruling 1
assigns a per-slot colon policy: ``path`` is colon-TOLERANT, and ``lang`` / ``span`` /
``name`` / ``kind`` MUST be colon-free. Parsing is therefore anchored from the ends —
``name`` is the second-to-last slot, and everything between ``lang`` and the trailing
three tokens is the path. ``ir.symbol_path_slot`` implements exactly that.

Two consumers disagree with it in opposite directions:

  LEFT-ANCHORED   ``io_boundary._extract_module_hint`` takes ``parts[1]``. Correct only
                  when the path is colon-FREE. On ``rust:std::fs:0-0:write:...`` it
                  returns ``std``, which then fails ``_module_matches`` against the
                  catalogue's ``std::fs`` — and a present-but-mismatched module is a
                  REJECTION, not a degrade, so the finding is dropped silently.

  SPAN-ANCHORED   ``taint._extract_callee_name`` locates the ``\\d+-\\d+`` token and
                  slices around it. Correct for a colon-bearing path AND for a
                  colon-bearing NAME — which the grammar forbids but an emitter may
                  still produce.

So the migration INV-fokik asks for (route the hint through the chokepoint) is right by
the grammar, and its cost depends entirely on a fact nobody has counted: **how many real
ids violate the colon-free-name rule today?** For those, right-anchored parsing walks
out of the path slot and into the name, and the left-anchored parser that is wrong in
theory happens to be right. WI-ribuz says this outright — "NOT YET MEASURED ... count it
before deciding this is cosmetic".

WHAT THIS REPORTS, per id, over the nodes and edge endpoints of a behavior map:
  conformant            right-anchored == span-anchored. The migration is a no-op.
  path_has_colon        legal colon-tolerant path. **Migration FIXES these.**
  name_has_colon        grammar VIOLATION. Migration REGRESSES these (right-anchored
                        over-consumes into the name); they belong to INV-dulah.
  unparseable           fewer than five tokens, or no span token.

Usage:
    scripts/measure-symbol-id-colon-conformance.py MAP.json [MAP.json ...] [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter


def right_anchored_path(symbol_id: str) -> str | None:
    """ADR-0036's parse: everything between lang and the trailing span/name/kind."""
    parts = symbol_id.split(":")
    if len(parts) < 5:
        return None
    return ":".join(parts[1:-3])


def span_index(parts: list[str]) -> int:
    """Index of the ``\\d+-\\d+`` span token, or -1."""
    for i in range(1, len(parts) - 1):
        tok = parts[i]
        if "-" in tok and tok.replace("-", "").isdigit():
            return i
    return -1


def span_anchored_path(symbol_id: str) -> str | None:
    parts = symbol_id.split(":")
    if len(parts) < 5:
        return None
    idx = span_index(parts)
    if idx < 0:
        return None
    return ":".join(parts[1:idx])


def left_anchored_path(symbol_id: str) -> str | None:
    """What ``io_boundary._extract_module_hint`` does today: ``parts[1]``."""
    parts = symbol_id.split(":")
    if len(parts) < 5:
        return None
    return parts[1]


def classify(symbol_id: str) -> str:
    r = right_anchored_path(symbol_id)
    s = span_anchored_path(symbol_id)
    left = left_anchored_path(symbol_id)
    if r is None or s is None:
        return "unparseable"
    if r != s:
        # Right- and span-anchored can only disagree when the NAME carries colons,
        # which the grammar forbids. Checked before the left-anchored comparison
        # because such an id is malformed and no parser is "right" on it.
        pass
    elif left != r:
        # Grammar-correct parse agrees with itself and the LEFT-anchored consumer
        # disagrees: a legal colon-tolerant path. THIS is what the migration fixes.
        return "path_has_colon_LEFT_WRONG"
    if r == s:
        return "conformant"
    # They disagree. Which slot carries the extra colons decides who is right.
    parts = symbol_id.split(":")
    idx = span_index(parts)
    name = ":".join(parts[idx + 1: -1])
    path = ":".join(parts[1:idx])
    if ":" in name:
        return "name_has_colon"
    if ":" in path:
        return "path_has_colon"
    return "disagree_other"  # pragma: no cover - defensive


def ids_in_map(doc: dict):
    for node in doc.get("nodes", []) or []:
        nid = node.get("id")
        if nid:
            yield "node", nid
    for edge in doc.get("edges", []) or []:
        for slot in ("src", "dst"):
            val = edge.get(slot)
            if val:
                yield f"edge.{slot}", val


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("maps", nargs="+")
    ap.add_argument("--json", dest="out")
    ap.add_argument("--examples", type=int, default=6)
    args = ap.parse_args()

    report: dict = {"maps": {}}
    grand: Counter = Counter()
    for raw in args.maps:
        path = pathlib.Path(raw)
        try:
            doc = json.loads(path.read_text())
        except Exception as exc:
            print(f"{path.name}: UNREADABLE ({exc})", file=sys.stderr)
            continue
        counts: Counter = Counter()
        examples: dict[str, list[str]] = {}
        for _slot, sid in ids_in_map(doc):
            verdict = classify(sid)
            counts[verdict] += 1
            if verdict not in ("conformant",):
                bucket = examples.setdefault(verdict, [])
                if len(bucket) < args.examples and sid not in bucket:
                    bucket.append(sid)
        report["maps"][path.name] = {
            "counts": dict(counts), "examples": examples,
        }
        grand.update(counts)
        total = sum(counts.values())
        print(
            f"{path.name:34s} total={total:8d} "
            f"conformant={counts.get('conformant', 0):8d} "
            f"LEFT_WRONG={counts.get('path_has_colon_LEFT_WRONG', 0):6d} "
            f"NAME_colon={counts.get('name_has_colon', 0):6d} "
            f"unparseable={counts.get('unparseable', 0):6d}",
            file=sys.stderr,
        )
    report["TOTAL"] = dict(grand)
    text = json.dumps(report, indent=2)
    if args.out:
        pathlib.Path(args.out).write_text(text)
    else:  # pragma: no cover - interactive
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
