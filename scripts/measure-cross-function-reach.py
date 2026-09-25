#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How much of the ``cross_function`` blocker could §4a composition actually lift?  (WI-famig)

STEP 1 IS PRICING, NOT BUILDING. Measurement 0007 established that 106 of the
139 never-ran walks (76.3%) bail at ``sink_node != source_fn``. That is the
SIZE OF THE BLOCKER, not the size of the REMEDY: the three blockers are
successive ``elif`` branches over the same rows, so a row reading
``cross_function`` today may simply RELABEL to ``source_not_tracked`` once
composition lands rather than becoming a walk.

WHAT THIS MEASURES. For every evidence row the production binary blocks at
``cross_function``, the NECESSARY conditions for a summary-composed walk to
connect it:

    hops          call-graph distance from the taint seed to the function
                  containing the sink. 1 = the seed calls it directly, which is
                  the only shape single-hop composition can address.
    sink_in_ddg   does the SINK's function carry reaching-def coverage? Without
                  it there is nothing for a composed walk to walk.
    all_in_ddg    do ALL functions on the route carry coverage? This is the
                  condition for composing the whole chain rather than one hop.

These are NECESSARY, NOT SUFFICIENT. Sufficiency additionally requires that the
tainted value is an ARGUMENT at the call -- which is precisely ``param_to_calls``,
the field that does not exist (repo-wide grep: it appears only in ADR-0017).
So every number here is a CEILING on what §4a composition could reach, and the
achievable figure is strictly below it.

READ THE SEED CAVEAT BEFORE READING THE HOPS. ``path`` starts at ``seed_id``,
which is ``caller_id`` for a ``start_at: caller`` source but the source CALLEE
for ``start_at: callee``. For the latter the first element is a primitive, not a
function, so its hop count is inflated by one. ``seed_in_ddg`` is reported so
the two populations can be separated rather than silently pooled.

INSTRUMENT CONTROL, stated before any number: if a repo produces zero
cross_function rows, its zero measures the claims/catalogue rather than the
code, and it is reported as EXCLUDED rather than as a zero (LIVE.md rule 6).

ATTRIBUTION IS 1:1 BY CONSTRUCTION, the same argument the p4 payoff instrument
rests on: ``_reconstruct_path`` is called once immediately before the single
``findings.append(TaintFlowFinding(...))`` of that loop iteration, with only
``_attribute_sanitizers`` in between. The pending pair is consumed by the next
finding constructed and then cleared.
"""
from __future__ import annotations
import argparse, collections, contextlib, io, json, sys
from typing import Any
import hypergumbo_core.taint as taint_mod


def _run(repo: str, claims: str) -> dict:
    real_prop = taint_mod.propagate_taint_ddg
    real_path = taint_mod._reconstruct_path
    real_find = taint_mod.TaintFlowFinding
    st: dict[str, Any] = {
        "analyzed": set(), "pending": None, "rows": [],
        "findings": 0, "blocked": collections.Counter(),
    }

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
        st["findings"] += 1
        blocked = k.get("walk_blocked_by", "")
        if blocked:
            st["blocked"][blocked] += 1
        if blocked == taint_mod.WALK_BLOCKED_CROSS_FUNCTION and st["pending"]:
            seed, sink_node, p = st["pending"]
            st["rows"].append({
                "seed": seed, "sink_node": sink_node, "path": p,
                "hops": max(len(p) - 1, 0),
                "label": k.get("taint_label", ""),
                "sink_primitive": k.get("sink_primitive", ""),
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
        raw = buf.getvalue()
        i = raw.find("{")
        st["report"] = json.loads(raw[i:]) if i >= 0 else {}
    finally:
        taint_mod.propagate_taint_ddg = real_prop
        taint_mod._reconstruct_path = real_path
        taint_mod.TaintFlowFinding = real_find

    analyzed = st["analyzed"]
    for r in st["rows"]:
        r["seed_in_ddg"] = r["seed"] in analyzed
        r["sink_in_ddg"] = r["sink_node"] in analyzed
        r["all_in_ddg"] = bool(r["path"]) and all(x in analyzed for x in r["path"])
    # RECONCILE TO A TOTAL (LIVE.md rule 10). The rows above are findings as
    # CONSTRUCTED; measurement 0007's unit is the evidence row the user reads,
    # which is what survives ``collapse_unadjudicated_flows``. A finding count
    # is not a row count, and reporting one against the other's denominator is
    # how a payoff gets overstated. Both are emitted.
    ev = collections.Counter()
    for v in (st.get("report") or {}).get("verdicts", []):
        for e in v.get("evidence", []) or []:
            for f in (e.get("flows", []) or [e]):
                if not isinstance(f, dict):
                    continue
                if f.get("walk_verdict"):
                    key = (f.get("walk_verdict"), f.get("walk_blocked_by", ""))
                    ev[key] += 1
    return {
        "findings_made": st["findings"],
        "blocked_by": dict(st["blocked"]),
        "analyzed_symbols": len(analyzed),
        "evidence_by_verdict": {f"{a}/{b}": c for (a, b), c in ev.items()},
        "evidence_cross_function": sum(
            c for (a, b), c in ev.items() if b == "cross_function"
        ),
        "rows": st["rows"],
    }


ap = argparse.ArgumentParser()
ap.add_argument("claims")
ap.add_argument("repos", nargs="+")
ap.add_argument("--json", dest="out")
args = ap.parse_args()

out = []
for repo in args.repos:
    name = repo.rstrip("/").split("/")[-1]
    print(f"\n=== {name} ===", flush=True)
    res = _run(repo, args.claims)
    rows = res["rows"]
    if not rows:
        print(f"  EXCLUDED — 0 cross_function rows "
              f"({res['findings_made']} findings, blocked={res['blocked_by']})")
        out.append({"repo": name, "control": "EXCLUDED", **res})
        continue
    hop = collections.Counter(r["hops"] for r in rows)
    sink_ok = sum(1 for r in rows if r["sink_in_ddg"])
    all_ok = sum(1 for r in rows if r["all_in_ddg"])
    seed_ok = sum(1 for r in rows if r["seed_in_ddg"])
    n = len(rows)
    print(f"  cross_function FINDINGS : {n}   (analyzed symbols {res['analyzed_symbols']})")
    print(f"  cross_function EVIDENCE ROWS (post-collapse, 0007's unit): "
          f"{res['evidence_cross_function']}")
    print(f"  hop distribution    : "
          + ", ".join(f"{h}:{c}" for h, c in sorted(hop.items())))
    print(f"  seed_in_ddg         : {seed_ok}/{n} ({seed_ok/n*100:.1f}%)")
    print(f"  sink_in_ddg         : {sink_ok}/{n} ({sink_ok/n*100:.1f}%)   <- necessary")
    print(f"  all_in_ddg          : {all_ok}/{n} ({all_ok/n*100:.1f}%)   <- whole chain")
    one_hop = sum(1 for r in rows if r["hops"] == 1 and r["sink_in_ddg"])
    print(f"  1-hop AND sink_in_ddg: {one_hop}/{n} ({one_hop/n*100:.1f}%)  <- single-hop ceiling")
    out.append({"repo": name, "control": "LIVE", "n": n,
                "hops": dict(hop), "seed_in_ddg": seed_ok,
                "sink_in_ddg": sink_ok, "all_in_ddg": all_ok,
                "one_hop_sink_ddg": one_hop, **res})

live = [r for r in out if r["control"] == "LIVE"]
if live:
    print("\n=== PER-REPO (pooling is itself a choice — INV-duvup) ===")
    print(f"{'repo':<20}{'rows':>6}{'1hop+ddg':>10}{'sink_ddg':>10}{'all_ddg':>9}")
    for r in live:
        print(f"{r['repo']:<20}{r['n']:>6}{r['one_hop_sink_ddg']:>10}"
              f"{r['sink_in_ddg']:>10}{r['all_in_ddg']:>9}")
    tn = sum(r["n"] for r in live)
    print(f"{'TOTAL':<20}{tn:>6}{sum(r['one_hop_sink_ddg'] for r in live):>10}"
          f"{sum(r['sink_in_ddg'] for r in live):>10}"
          f"{sum(r['all_in_ddg'] for r in live):>9}")
print(f"\nexcluded (0 cross_function rows): "
      f"{[r['repo'] for r in out if r['control']=='EXCLUDED'] or 'none'}")
if args.out:
    import pathlib
    pathlib.Path(args.out).write_text(json.dumps(out, indent=1))
    print(f"detail: {args.out}")
