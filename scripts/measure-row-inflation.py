#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What does a composed walk COST in emitted rows?  (WI-famig step 2)

THE ORDERING THIS BREAKS. WI-famig's own last note records a deadlock: the
load-bearing unmeasured claim (would `unconfirmed` stay ~0 among walks that
composition MAKES run?) needs a composition prototype, and the item says the
prototype must not be built before the row-inflation cost is priced. So price
the cost. This instrument builds nothing and changes no behaviour.

THE MECHANISM, verified in the code rather than taken from the item.
``collapse_unadjudicated_flows`` (taint.py) collapses only findings whose
``analysis_method`` is in ``UNADJUDICATED_METHODS`` = {structural, ddg_mixed},
grouping on

    (taint_label, source_symbol, sink_zone, sanitized, source_boundary,
     analysis_method)

and everything else -- ``ddg`` -- is appended untouched ("ADJUDICATED FLOWS
PASS THROUGH UNTOUCHED"). A cross-function finding is ``ddg_mixed`` today, so
it sits inside a collapsed group. If composition confirms it, it becomes
``ddg`` and LEAVES that group to become its own row, while the group it left
survives one member lighter.

    group of |G|, with p members promoted
      rows before = 1
      rows after  = p + (1 if p < |G| else 0)

So the cost is NOT "one row per confirmation" -- it depends on the GROUP SIZE
DISTRIBUTION, which nobody has measured. A group of 40 that loses 10 members
becomes eleven rows. A group of 1 that is promoted stays one row.

EXACT EXPECTATION UNDER PARTIAL YIELD. The ceiling assumes every composable
finding composes; sufficiency (``param_to_calls``, which does not exist) is
unmeasured, so the achievable rate r is unknown and strictly below 1. For a
group with c composable members, promoting each independently with prob r:

    E[rows] = c*r + (1 if |G| > c else 1 - r**c)

reported at r = 1.00 / 0.50 / 0.25 so the answer is a curve, not a point.

RECONCILIATION IS THE CONTROL. Cross-function finding counts must reproduce
measurement 0008's per-repo table; if they do not, this instrument is wrong
and its inflation number is meaningless.
"""
from __future__ import annotations
import argparse
import collections
import contextlib
import io
import json
import sys
from typing import Any
import hypergumbo_core.taint as taint_mod


def _run(repo: str, claims: str) -> dict:
    real_prop = taint_mod.propagate_taint_ddg
    real_path = taint_mod._reconstruct_path
    real_find = taint_mod.TaintFlowFinding
    st: dict[str, Any] = {"analyzed": set(), "pending": None, "recs": []}

    def prop(*a: Any, **k: Any) -> Any:
        syms = k.get("ddg_symbols")
        if syms is None and len(a) > 5:
            syms = a[5]
        if syms:
            st["analyzed"] |= set(syms)
        return real_prop(*a, **k)

    def path(parent: Any, start: str, end: str) -> Any:
        p = real_path(parent, start, end)
        st["pending"] = (start, end, list(p))
        return p

    def make(*a: Any, **k: Any) -> Any:
        obj = real_find(*a, **k)
        pend = st["pending"]
        st["recs"].append({
            # the six collapse-key fields, read off the CONSTRUCTED object so
            # __post_init__ defaults are included rather than guessed from kwargs
            "key": (
                obj.taint_label, obj.source_symbol, obj.sink_zone,
                obj.sanitized, obj.source_boundary, obj.analysis_method,
            ),
            "method": obj.analysis_method,
            "blocked": k.get("walk_blocked_by", "") or "",
            "hops": max(len(pend[2]) - 1, 0) if pend else None,
            "sink_node": pend[1] if pend else None,
        })
        st["pending"] = None
        return obj

    taint_mod.propagate_taint_ddg = prop
    taint_mod._reconstruct_path = path
    taint_mod.TaintFlowFinding = make
    try:
        from hypergumbo_core.cli import main
        argv = sys.argv
        sys.argv = ["hypergumbo", "verify-claims", repo, "--claims", claims, "--json"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                with contextlib.suppress(SystemExit):
                    main()
        finally:
            sys.argv = argv
    finally:
        taint_mod.propagate_taint_ddg = real_prop
        taint_mod._reconstruct_path = real_path
        taint_mod.TaintFlowFinding = real_find

    analyzed = st["analyzed"]
    XF = taint_mod.WALK_BLOCKED_CROSS_FUNCTION
    for r in st["recs"]:
        r["composable"] = bool(
            r["blocked"] == XF and r["hops"] == 1
            and r["sink_node"] in analyzed
        )
    return {"recs": st["recs"], "analyzed": len(analyzed)}


def _price(recs: list[dict]) -> dict:
    UN = taint_mod.UNADJUDICATED_METHODS
    groups: dict[tuple, list[dict]] = collections.defaultdict(list)
    adjudicated = 0
    for r in recs:
        if r["method"] in UN:
            groups[r["key"]].append(r)
        else:
            adjudicated += 1
    rows_before = len(groups) + adjudicated
    out: dict[str, Any] = {
        "findings": len(recs), "adjudicated_today": adjudicated,
        "groups": len(groups), "rows_before": rows_before,
        "xf_findings": sum(
            1 for r in recs
            if r["blocked"] == taint_mod.WALK_BLOCKED_CROSS_FUNCTION
        ),
        "composable": sum(1 for r in recs if r["composable"]),
        "group_sizes": dict(collections.Counter(len(g) for g in groups.values())),
    }
    for r_rate in (1.0, 0.5, 0.25):
        total = float(adjudicated)
        for g in groups.values():
            c = sum(1 for m in g if m["composable"])
            if c == 0:
                total += 1.0
                continue
            total += c * r_rate + (1.0 if len(g) > c else 1.0 - r_rate ** c)
        out[f"rows_after_r{r_rate:g}"] = round(total, 1)
        out[f"delta_r{r_rate:g}"] = round(total - rows_before, 1)
        out[f"inflation_r{r_rate:g}"] = (
            round(total / rows_before, 3) if rows_before else None
        )
    # how many groups contain at least one composable member, and how big are they
    touched = [len(g) for g in groups.values()
               if any(m["composable"] for m in g)]
    out["groups_touched"] = len(touched)
    out["touched_group_sizes"] = dict(collections.Counter(touched))
    return out


ap = argparse.ArgumentParser()
ap.add_argument("claims")
ap.add_argument("repos", nargs="+")
ap.add_argument("--json", dest="out")
args = ap.parse_args()

allout = []
for repo in args.repos:
    name = repo.rstrip("/").split("/")[-1]
    print(f"\n=== {name} ===", flush=True)
    res = _run(repo, args.claims)
    p = _price(res["recs"])
    p["repo"] = name
    p["analyzed_symbols"] = res["analyzed"]
    if p["xf_findings"] == 0:
        p["control"] = "EXCLUDED"
        print(f"  EXCLUDED — 0 cross_function findings "
              f"({p['findings']} findings)")
    else:
        p["control"] = "LIVE"
        print(f"  findings {p['findings']}  xf {p['xf_findings']}  "
              f"composable {p['composable']}")
        print(f"  rows today {p['rows_before']}  "
              f"(groups {p['groups']} + adjudicated {p['adjudicated_today']})")
        for r_rate in (1.0, 0.5, 0.25):
            print(f"    r={r_rate:<5} rows {p[f'rows_after_r{r_rate:g}']:>8}  "
                  f"delta {p[f'delta_r{r_rate:g}']:>+8}  "
                  f"x{p[f'inflation_r{r_rate:g}']}")
        print(f"  groups touched {p['groups_touched']}/{p['groups']}  "
              f"sizes {sorted(p['touched_group_sizes'].items())[:8]}")
    allout.append(p)

if args.out:
    import pathlib
    pathlib.Path(args.out).write_text(json.dumps(allout, indent=1))
    print(f"\ndetail: {args.out}")
