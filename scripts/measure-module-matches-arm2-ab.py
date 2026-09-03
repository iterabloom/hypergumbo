#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A/B arm 2 of ``_module_matches``: case VALUE against PARENT AGREEMENT.

THE QUESTION. When one module path is a strict component-prefix of the other,
arm 2 decides whether the first extra component names a TYPE inside the
matched module (match) or a SIBLING module (no match). It used to ask whether
that component is capitalised -- Go's convention, constant-true in every
language whose module names are capitalised. It now asks whether the
component's case DISAGREES with the parent it follows
(:func:`io_boundary._extra_component_names_a_type`). This instrument prices
the difference on a real repository, row by row.

WHY THIS IS IN-REPO. The 2026-09-02 census that established arm 2 fires nine
times across eight repos and names a type zero times was a session artifact
(LIVE.md rule 12: an uncommitted instrument's defects cannot be fixed, only
re-encountered). This is that instrument, committed, so the next such claim
costs one command.

METHOD: a counterfactual in ONE process, not a two-tree A/B. The rule lives in
exactly one function, so "the code under the old rule" is exactly "the code
with that function replaced". ``io-boundaries`` is run twice on the same
repository -- once with the helper swapped for the old test, once as
production -- and the chain sets are diffed. No second checkout, no editable-
install hazard, and the two arms differ in nothing but the predicate.

WHAT IT REPORTS. Every chain that exists under one arm and not the other,
with its primitive and its calling site, so each moved row can be read back
against source (LIVE.md rule 7: an A/B prices a change; only read-back prices
the claim). ``ADDED`` should be structurally impossible -- the new rule is
strictly weaker for a lowercase parent and identical otherwise -- and is
counted anyway, because a control that cannot fail is not a control. Every
arm's output is asserted non-empty before the diff is trusted.

Run::

    python scripts/measure-module-matches-arm2-ab.py ~/repos/livebook
    python scripts/measure-module-matches-arm2-ab.py ~/repos/stack --out /tmp/stack

``PROBE_REPO`` is honoured as a fallback for the positional argument.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import os
import sys
from pathlib import Path

from hypergumbo_core import io_boundary as iob
from hypergumbo_core.cli import main

FIRED: collections.Counter = collections.Counter()


def _old_rule(parent_raw: str, extra_raw: str) -> bool:
    """Arm 2 as it stood before WI-zozun: capitalised means type."""
    FIRED[(parent_raw, extra_raw)] += bool(extra_raw[:1].isupper())
    return extra_raw[:1].isupper()


def _run(repo: str) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["io-boundaries", repo, "--format", "json"])
    text = buf.getvalue()
    if rc not in (0, 1) or not text.strip():
        raise SystemExit(f"io-boundaries rc={rc} produced no output for {repo}")
    return json.loads(text)


def _chains(doc: dict) -> dict[tuple, dict]:
    out = {}
    for boundary, entry in (doc.get("boundaries") or {}).items():
        for ch in entry.get("chains", []):
            key = (boundary, ch["primitive"], ch["io_edge_src"], ch["io_edge_dst"])
            out[key] = ch
    return out


def main_ab(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", nargs="?", default=os.environ.get("PROBE_REPO"))
    parser.add_argument("--out", help="directory for the two arms' JSON")
    args = parser.parse_args(argv)
    if not args.repo:
        parser.error("repo required (positional or PROBE_REPO)")
    repo = str(Path(args.repo).expanduser())

    real = iob._extra_component_names_a_type
    iob._extra_component_names_a_type = _old_rule
    try:
        old_doc = _run(repo)
    finally:
        iob._extra_component_names_a_type = real
    new_doc = _run(repo)

    old, new = _chains(old_doc), _chains(new_doc)
    assert old and new, "an arm produced zero chains; the diff is untrustworthy"

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "arm-old.json").write_text(json.dumps(old_doc, indent=1))
        (out / "arm-new.json").write_text(json.dumps(new_doc, indent=1))

    removed = sorted(set(old) - set(new))
    added = sorted(set(new) - set(old))
    name = Path(repo).name
    print(f"\n{'=' * 66}")
    print(f"arm-2 A/B  repo={name}")
    print(f"  chains under OLD rule (capitalised => type)     {len(old)}")
    print(f"  chains under NEW rule (disagrees with parent)   {len(new)}")
    print(f"  REMOVED {len(removed)}   ADDED {len(added)}")
    print(f"  old-rule arm-2 firings (parent, extra) -> count:")
    for (parent, extra), n in sorted(FIRED.items()):
        if n:
            print(f"    {parent}.{extra:<24} x{n}")
    for label, keys in (("REMOVED", removed), ("ADDED", added)):
        if keys:
            print(f"  {label}, one line per chain (READ EACH BACK AGAINST SOURCE):")
            for boundary, prim, src, dst in keys:
                print(f"    [{boundary}] {prim:<40} at {src}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_ab())
