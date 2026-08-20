#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A/B the dotted-module constructor resolution (WI-lifol) on real repositories.

WHAT CHANGED. ``_external_constructor_type``'s attribute branch required the
constructor's base to be a bare ``ast.Name``, so a type under a DOTTED module —
``http.client.HTTPConnection(h)``, whose base is itself an ``ast.Attribute`` —
could never be typed regardless of what the table held. The branch now unwinds
the whole chain via ``_unwind_attribute_chain``, which subsumes the
single-segment case rather than sitting beside it.

WHY THIS DOES NOT REUSE ``measure-ctor-table-derivation-ab.py``. That instrument
builds arm A by swapping the TABLE, which cannot reproduce this change: the table
already contains every dotted key and did so before this fix. The defect was in
the RESOLUTION branch, so arm A here restores the old branch itself. Two
instruments, two different arm mechanisms, one shared counting idiom — the idiom
is not extracted because a shared home for two callers is the abstraction this
project keeps regretting, not the drift it keeps meeting.

WHY IT COUNTS EDGES AND NOT io-boundary CHAINS. ``io-boundaries`` caches under
``~/.cache/hypergumbo`` keyed on repo state and analyzer identity; an in-process
monkeypatch changes neither, so a second arm would be served arm A's map and
report a flawless zero. Calling ``analyze_python`` directly touches no cache and
is the exact layer the change lives at. CACHES FAKE NULLS.

BOTH DIRECTIONS. Typing a receiver is not free — an external qualified type walks
into the strip-to-bare-name lookup against the repo's own symbols, the channel
behind alertmanager's "13 spurious in-edges". Gains and losses are counted
separately and a gain counts as attributable only when its dst module slot is one
of the types this change newly reaches.

Usage:
    scripts/measure-dotted-ctor-resolution-ab.py REPO [REPO ...] [--json OUT]
"""
from __future__ import annotations

import argparse
import ast
import collections
import contextlib
import io
import json
import pathlib
from typing import Any

import hypergumbo_lang_mainstream.py as py_mod


def _legacy_external_constructor_type(
    call: ast.Call,
    imports: dict[str, tuple[str, str]],
    module_imports: dict[str, str],
) -> str | None:
    """The branch exactly as it stood before WI-lifol, so arm A is the old binary.

    The bare-``ast.Name`` arm is copied verbatim rather than delegated, because
    delegating to the current function would let a change there silently alter
    arm A and turn this A/B into a comparison of a thing with itself.
    """
    func = call.func
    if isinstance(func, ast.Name):
        claimed = py_mod.EXTERNAL_CONSTRUCTOR_TYPES.get(func.id)
        if claimed is None:
            return None
        bound = py_mod._import_binding_for(func.id, imports, module_imports)
        if bound is None:
            return (
                claimed if func.id in py_mod.BUILTIN_CONSTRUCTOR_NAMES else None
            )
        return claimed if bound == claimed else None
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in module_imports
    ):
        return py_mod.EXTERNAL_CONSTRUCTOR_TYPES.get(
            f"{module_imports[func.value.id]}.{func.attr}",
        )
    return None


def _newly_reachable_types() -> list[str]:
    """Catalogued receiver types the OLD branch could not resolve — depth >= 2.

    Derived from the live catalogue rather than listed, so a type added to the
    YAML tomorrow appears here without editing this file.
    """
    return sorted(
        t for t in set(py_mod.EXTERNAL_CONSTRUCTOR_TYPES.values())
        if t.count(".") >= 2
    )


def _module_slot(dst: str) -> str:
    parts = dst.split(":")
    return ":".join(parts[1:-3]) if len(parts) >= 5 else ""


def _call_edges(repo: pathlib.Path) -> collections.Counter:
    """(module_slot, name_slot) for every call edge."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        analysis = py_mod.analyze_python(repo)
    counter: collections.Counter = collections.Counter()
    for edge in analysis.edges:
        if edge.edge_type != "calls":
            continue
        parts = edge.dst.split(":")
        if len(parts) < 5:
            continue
        counter[(_module_slot(edge.dst), parts[-2])] += 1
    return counter


def _run_arm(repo: pathlib.Path, *, fixed: bool) -> collections.Counter:
    real = py_mod._external_constructor_type
    if not fixed:
        py_mod._external_constructor_type = _legacy_external_constructor_type
    try:
        return _call_edges(repo)
    finally:
        py_mod._external_constructor_type = real


def _control_passes(newly: list[str]) -> bool:
    """The arms must differ, proven on a fixture before any repo null is believed.

    A FAILED CONTROL MEANS *NOT MEASURED*, NOT ZERO. Arm A must refuse the dotted
    form and arm B must resolve it; if both agree, the monkeypatch did not take
    (the call sites resolve the global at call time, so a stale binding anywhere
    would silently produce two identical arms).
    """
    if not newly:
        print("NO CATALOGUED TYPE SITS AT DEPTH >= 2 — nothing to measure.")
        return False
    probe = newly[0]
    src = f"import {probe.rsplit('.', 1)[0]}\n\n\ndef f(a):\n    return {probe}(a)\n"
    tree = ast.parse(src)
    imports, module_imports = py_mod._extract_imports(tree, "probe")
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    arm_a = _legacy_external_constructor_type(call, imports, module_imports)
    arm_b = py_mod._external_constructor_type(call, imports, module_imports)
    print("POSITIVE CONTROL (the arms must be different binaries):")
    print(f"  probe                     : {probe}(a)")
    print(f"  arm A (legacy branch)     : {arm_a!r}")
    print(f"  arm B (chain-unwinding)   : {arm_b!r}")
    print(f"  newly reachable types     : {len(newly)}  {newly}")
    if arm_a is not None or arm_b != probe:
        print("\nCONTROL FAILED. The arms do not differ as stated, so any number "
              "below is unmeasured rather than zero. Refusing to report.")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--json", dest="out")
    args = ap.parse_args()

    newly = _newly_reachable_types()
    if not _control_passes(newly):
        return 2

    rows: list[dict[str, Any]] = []
    for repo_str in args.repos:
        repo = pathlib.Path(repo_str).resolve()
        print(f"\n=== {repo} ===", flush=True)
        before = _run_arm(repo, fixed=False)
        after = _run_arm(repo, fixed=True)
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
            "gained_attributable": attributable,
            "gained_top": {f"{m}.{n}": c for (m, n), c in gained.most_common(12)},
            "lost_top": {f"{m}.{n}": c for (m, n), c in lost.most_common(12)},
        }
        rows.append(row)
        print(f"  call edges before/after : {row['edges_before']} / "
              f"{row['edges_after']}")
        print(f"  GAINED                  : {row['gained']}  "
              f"({attributable} onto a newly reachable receiver type)")
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
    ta = sum(r["gained_attributable"] for r in rows)
    print(f"gained {tg} edge(s) ({ta} onto a newly reachable type), "
          f"lost {tl} edge(s), across {len(rows)} repo(s)")
    if tl:
        print("LOSSES ARE NOT ZERO. A receiver that used to resolve one way now "
              "resolves another; read lost_top before shipping.")
    if tg and ta != tg:
        print(f"{tg - ta} gained edge(s) are NOT attributable to a newly "
              f"reachable type — a side effect, not the intended win.")
    if not tg:
        print("NO REPO IN THIS COHORT CONTAINS THE SHAPE. A statement about the "
              "cohort, not evidence the change is inert.")

    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(
            {"newly_reachable": newly, "repos": rows}, indent=2))
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
