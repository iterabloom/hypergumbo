#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Can each catalogued I/O primitive be REACHED from an idiomatic call site?

THE INVARIANT UNDER TEST is INV-linub's statement verbatim: *a catalogued I/O
call site must reach its catalogue entry regardless of receiver syntax*. Every
prior measurement of it has been indirect — count sinks, count edges, count
escapes — and indirect counts cannot separate "the catalogue is thin" from
"the catalogue is fine and nothing can reach it". This instrument closes that
by generating a call site for EVERY entry in a language's catalogue and asking
production whether a boundary comes back.

WHY GENERATE THE FIXTURE FROM THE CATALOGUE ITSELF. A hand-written fixture
measures the primitives someone remembered, and it silently stops covering new
entries the day one is added. Reading ``load_catalog(language).primitives`` and
emitting one call site per entry means the probe's denominator IS the shipped
catalogue and cannot drift from it.

TWO IMPORT FORMS, AND THAT IS THE POINT. ``import os; os.makedirs(p)`` and
``from os import makedirs; makedirs(p)`` are both idiomatic and they exercise
different resolution paths — the first leaves the module in the receiver, the
second erases it. A single-form probe reports "reachable" for a primitive that
half of real code cannot reach. Each entry is therefore emitted twice and
scored per form, so the output distinguishes:

    BOTH     reachable either way
    DOTTED   only ``import M; M.f()``          — the from-import path is blind
    BARE     only ``from M import f; f()``     — the dotted path is blind
    NEITHER  no call site of either form reaches the entry

WHAT THIS DOES *NOT* MEASURE, stated because the number invites over-reading.
It is a LOWER BOUND on gaps in the idiomatic direction only. A primitive
scoring BOTH here can still be unreachable through an alias, a re-export, a
conditional import or a wrapped receiver; a primitive scoring NEITHER is
unreachable for these two forms and no claim is made about others. It is a
reachability probe, not a recall estimate on real code.

NOT EVERY ENTRY IS EXPRESSIBLE, and those are reported separately rather than
counted as failures. ``builtins.open`` is idiomatically a bare ``open(...)``
with no import; ``file.write`` names no importable type. Folding an
inexpressible entry into the failure bucket would inflate the gap with the
probe's own limitation, which is the reverse of the error this file exists to
avoid.

POSITIVE CONTROL, printed before any verdict: at least one entry must score
reachable. A run where nothing resolves is measuring a broken fixture or a
broken pipeline, not a catalogue gap, and says so instead of reporting 100%.

Usage:
    scripts/measure-catalogue-reach.py LANGUAGE [--workdir DIR] [--json OUT]
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import pathlib
import shutil
import sys
import tempfile
from typing import Any

from hypergumbo_core.io_boundary import load_catalog

# Entries whose idiomatic call site has no import at all, or whose module slot
# names no importable thing. Enumerated as PREFIXES so the list stays short and
# its intent is readable; anything matching is reported as inexpressible.
_INEXPRESSIBLE_MODULES = ("builtins", "file", "self", "cls")


def _safe(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text)


def _call_sites(prim: Any) -> tuple[str, str] | None:
    """(dotted-form body, bare-form body) for *prim*, or None if inexpressible.

    ``module`` means different things per kind and the generator has to respect
    that: for a ``function`` it is the importable module, for a ``method`` it is
    the TYPE the method hangs off (``pathlib.Path``, ``socket.socket``), which
    is itself imported from its parent module and then constructed.
    """
    module, name, kind = prim.module, prim.name, prim.kind
    if not module or not name:
        return None
    if module.split(".")[0] in _INEXPRESSIBLE_MODULES:
        return None
    if "." in module:
        parent, leaf = module.rsplit(".", 1)
    else:
        parent, leaf = module, ""

    if kind == "function":
        dotted = f"    import {module}\n    return {module}.{name}(a, b)\n"
        bare = f"    from {module} import {name}\n    return {name}(a, b)\n"
    elif kind == "method":
        # The receiver is constructed from the type named in the module slot.
        if leaf:
            dotted = (f"    import {parent}\n"
                      f"    return {parent}.{leaf}(a).{name}(b)\n")
            bare = (f"    from {parent} import {leaf}\n"
                    f"    return {leaf}(a).{name}(b)\n")
        else:
            dotted = f"    import {module}\n    return {module}(a).{name}(b)\n"
            bare = dotted
    elif kind == "attribute":
        dotted = f"    import {module}\n    return {module}.{name}\n"
        bare = f"    from {module} import {name}\n    return {name}\n"
    else:  # pragma: no cover - kind axis is closed; guard for a new value
        return None
    return dotted, bare


def _write_fixture(language: str, prims: list[Any], root: pathlib.Path
                   ) -> tuple[dict[str, Any], list[Any]]:
    """Emit one function per (primitive, form). Returns fn-name -> primitive."""
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    index: dict[str, Any] = {}
    skipped: list[Any] = []
    lines = ["# generated by scripts/measure-catalogue-reach.py\n"]
    for i, prim in enumerate(prims):
        sites = _call_sites(prim)
        if sites is None:
            skipped.append(prim)
            continue
        dotted, bare = sites
        for form, body in (("dotted", dotted), ("bare", bare)):
            fn = f"probe_{i}_{form}_{_safe(prim.module)}_{_safe(prim.name)}"
            index[fn] = (prim, form)
            lines.append(f"\n\ndef {fn}(a, b):\n{body}")
    (root / "probe.py").write_text("".join(lines))
    return index, skipped


def _boundaries_for(root: pathlib.Path) -> dict[str, set[str]]:
    """fn-name -> set of primitives production attributed to it."""
    from hypergumbo_core.cli import main

    argv = sys.argv
    sys.argv = ["hypergumbo", "io-boundaries", str(root), "--format", "json"]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            with contextlib.suppress(SystemExit):
                main()
    finally:
        sys.argv = argv
    raw = buf.getvalue()
    start = raw.find("{")
    report = json.loads(raw[start:]) if start >= 0 else {}
    hits: dict[str, set[str]] = collections.defaultdict(set)
    for bucket in report.get("boundaries", {}).values():
        for chain in bucket.get("chains", []):
            src = chain.get("io_edge_src", "")
            parts = src.split(":")
            if len(parts) >= 2:
                hits[parts[-2]].add(chain.get("primitive", ""))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("language")
    ap.add_argument(
        "--workdir",
        default=str(pathlib.Path(tempfile.gettempdir()) / "hg-catalogue-reach"),
        help="scratch dir for the generated probe fixture",
    )
    ap.add_argument("--json", dest="out")
    args = ap.parse_args()

    catalog = load_catalog(args.language)
    prims = list(catalog.primitives)
    root = pathlib.Path(args.workdir) / args.language
    index, skipped = _write_fixture(args.language, prims, root)
    print(f"language                 : {args.language}")
    print(f"catalogued primitives    : {len(prims)}")
    print(f"  ... inexpressible      : {len(skipped)} "
          f"({sorted({p.qualified_name for p in skipped})[:6]}...)")
    print(f"generated probe functions: {len(index)}")

    hits = _boundaries_for(root)
    print(f"functions with a boundary: {len(hits)}")
    if not hits:
        print("\n!! NOTHING RESOLVED. This is a broken fixture or a broken "
              "pipeline, not a catalogue gap. Refusing to report a 100% miss.")
        return 2

    per_prim: dict[str, set[str]] = collections.defaultdict(set)
    for fn, (prim, form) in index.items():
        # Attribution is by NAME, not merely by "some boundary appeared": a
        # probe function that resolves to a DIFFERENT primitive has not shown
        # the entry under test is reachable.
        if prim.qualified_name in hits.get(fn, ()):
            per_prim[prim.qualified_name].add(form)

    rows = []
    verdicts: collections.Counter[str] = collections.Counter()
    for prim in prims:
        if prim in skipped:
            continue
        forms = per_prim.get(prim.qualified_name, set())
        verdict = ("BOTH" if len(forms) == 2 else
                   "DOTTED" if forms == {"dotted"} else
                   "BARE" if forms == {"bare"} else "NEITHER")
        verdicts[verdict] += 1
        rows.append({"primitive": prim.qualified_name, "kind": prim.kind,
                     "boundary": prim.boundary, "verdict": verdict})

    total = sum(verdicts.values())
    print(f"\n=== REACH over {total} expressible primitives ===")
    for v in ("BOTH", "DOTTED", "BARE", "NEITHER"):
        n = verdicts[v]
        print(f"  {v:<9}{n:>5}{100 * n / max(total, 1):>7.1f}%")

    misses = [r for r in rows if r["verdict"] == "NEITHER"]
    if misses:
        by_kind = collections.Counter(r["kind"] for r in misses)
        by_bound = collections.Counter(r["boundary"] for r in misses)
        print(f"\nUNREACHABLE by kind    : {dict(by_kind)}")
        print(f"UNREACHABLE by boundary: {dict(by_bound.most_common(8))}")
        print("\nfirst 25 unreachable:")
        for r in misses[:25]:
            print(f"  {r['primitive']:<44}{r['kind']:<10}{r['boundary']}")

    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(
            {"language": args.language, "total": len(prims),
             "inexpressible": [p.qualified_name for p in skipped],
             "verdicts": dict(verdicts), "rows": rows}, indent=2))
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
