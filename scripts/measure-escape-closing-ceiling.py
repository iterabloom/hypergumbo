#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is the MAXIMUM finding-level payoff of closing §3a escape sites?

THE QUESTION, AND WHY IT IS ASKED AS A CEILING. Several open items propose to
close escape sites in the ADR-0017 §3a walk: INV-busis's ``4a`` bucket (a
callee key with no §4 summary), INV-mumov (no call edge emitted at all),
INV-linub (an untyped receiver yielding the ``external`` placeholder, so no
key can be built). Each is filed with a site count, and a site count invites
the inference that closing those sites buys proportionate findings.

This instrument refuses to measure an increment and measures the CEILING
instead: it forces ``_use_site_terminates`` to return True unconditionally, so
EVERY escape site closes at once. No catalogue entry, no emission fix and no
receiver-typing fix can exceed that. The asymmetry is the point —

  * an INCREMENT measured at zero is indistinguishable from an instrument
    that cannot see the change (this repo has produced that reading four
    separate times, every one caught only by a control); whereas
  * a CEILING measured at zero is a proof about the whole family: if closing
    every site moves nothing, no member can move anything.

WHAT THE ARMS ARE.
  A ``control``  production, unmodified.
  B ``ceiling``  ``taint._use_site_terminates`` → True for every site.

WHAT IS COUNTED, AND WHY THE TWO WALK ARMS ARE COUNTED SEPARATELY. The walk is
invoked from two places with opposite consumers, and conflating them is how a
suppression risk gets reported as a null:

  * the **§3a arm** tests ``_ddg_taint_reaches(...) is True``, so ``False`` and
    ``None`` collapse there. Converting an escape into an accounted-for step
    moves a walk from ``None`` to ``False`` and CANNOT change ``adjudicated``.
  * the **barrier arm** (WI-fasub, same-function sanitizer) tests
    ``... is False``, and since PR #214 a ``False`` earns ``sanitized`` — a
    sanitized flow is dropped from the claim's violation set. Here the same
    ``None`` → ``False`` conversion DELETES a finding.

So a single number for "escape sites closed" hides a direction flip. The
tally therefore reports ``walks_3a_{true,false,none}`` and
``barrier_{walks,false}`` per arm, and the finding-level columns
(``violated_claims``, ``sanitized_flows``, ``evidence``) alongside them.

THE INSTRUMENT CONTROL, believed before any zero is reported. Arm B must move
§3a walks out of ``none``. That is a property of the patch being live, not of
the repo: any repo with at least one escaping walk must show it. If ``none``
does not fall, the monkeypatch is not on the path the walk actually takes and
every zero below is uncontrolled — the run says so and refuses the corpus
table rather than printing zeros that look like findings.

A repo with no walks at all is reported as ``NO WALKS`` and excluded from the
denominator rather than folded in as a zero (L: a bucket defined by an absence
cannot measure it — name the population).

Usage:
    scripts/measure-escape-closing-ceiling.py CLAIMS REPO [REPO ...] [--json OUT]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
import sys
from typing import Any

import hypergumbo_core.taint as taint_mod


class _Tally:
    """Per-arm counters, split by which walk arm produced them."""

    def __init__(self) -> None:
        self.walks_3a = 0
        self.walks_3a_true = 0
        self.walks_3a_false = 0
        self.walks_3a_none = 0
        self.barrier_walks = 0
        self.barrier_false = 0
        self.terminates_calls = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "walks_3a": self.walks_3a,
            "walks_3a_true": self.walks_3a_true,
            "walks_3a_false": self.walks_3a_false,
            "walks_3a_none": self.walks_3a_none,
            "barrier_walks": self.barrier_walks,
            "barrier_false": self.barrier_false,
            "terminates_calls": self.terminates_calls,
        }


def _run_arm(repo: str, claims: str, *, ceiling: bool) -> tuple[dict[str, Any], _Tally]:
    """One verify-claims run with escape-closing off (A) or maxed (B)."""
    tally = _Tally()
    real_walk = taint_mod._ddg_taint_reaches
    real_terminates = taint_mod._use_site_terminates

    def counting_terminates(*args: Any, **kwargs: Any) -> bool:
        tally.terminates_calls += 1
        if ceiling:
            # THE CEILING. Every use site is declared accounted-for, which is
            # the upper bound of every catalogue / emission / typing fix in
            # the family. Deliberately not "return a plausible summary" —
            # a plausible summary is an increment and would reintroduce the
            # unfalsifiable zero this instrument exists to avoid.
            return True
        return bool(real_terminates(*args, **kwargs))

    def counting_walk(*args: Any, **kwargs: Any) -> Any:
        result = real_walk(*args, **kwargs)
        if kwargs.get("barrier_lines"):
            tally.barrier_walks += 1
            if result is False:
                tally.barrier_false += 1
        else:
            tally.walks_3a += 1
            if result is True:
                tally.walks_3a_true += 1
            elif result is False:
                tally.walks_3a_false += 1
            else:
                tally.walks_3a_none += 1
        return result

    taint_mod._use_site_terminates = counting_terminates
    taint_mod._ddg_taint_reaches = counting_walk
    try:
        from hypergumbo_core.cli import main

        argv = sys.argv
        sys.argv = ["hypergumbo", "verify-claims", repo, "--claims", claims,
                    "--json"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                with contextlib.suppress(SystemExit):
                    main()
        finally:
            sys.argv = argv
        raw = buf.getvalue().strip()
        # The banner precedes the JSON on some paths; take the object only.
        start = raw.find("{")
        report = json.loads(raw[start:]) if start >= 0 else {}
    finally:
        taint_mod._use_site_terminates = real_terminates
        taint_mod._ddg_taint_reaches = real_walk
    return report, tally


def _findings(report: dict[str, Any]) -> dict[str, int]:
    violated = sanitized = evidence = 0
    for verdict in report.get("verdicts", []):
        if verdict.get("verdict") == "violated":
            violated += 1
        sanitized += verdict.get("sanitized_flows", 0)
        evidence += verdict.get("evidence_count", 0)
    return {"violated_claims": violated, "sanitized_flows": sanitized,
            "evidence": evidence}


def _compare(repo: str, claims: str) -> dict[str, Any]:
    before, ta = _run_arm(repo, claims, ceiling=False)
    after, tb = _run_arm(repo, claims, ceiling=True)
    fa, fb = _findings(before), _findings(after)
    moved_out_of_none = ta.walks_3a_none - tb.walks_3a_none
    if ta.walks_3a == 0:
        control = "NO WALKS — excluded from the denominator"
    elif ta.walks_3a_none == 0:
        control = "NO ESCAPING WALKS — the ceiling has nothing to close here"
    elif moved_out_of_none > 0:
        control = f"LIVE — {moved_out_of_none} walk(s) left 'none'"
    else:
        control = "DEAD — patch changed no verdict; zeros are uncontrolled"
    return {
        "repo": repo,
        "arm_a_control": {**ta.as_dict(), **fa},
        "arm_b_ceiling": {**tb.as_dict(), **fb},
        "delta": {
            "walks_3a_none": tb.walks_3a_none - ta.walks_3a_none,
            "walks_3a_false": tb.walks_3a_false - ta.walks_3a_false,
            "walks_3a_true": tb.walks_3a_true - ta.walks_3a_true,
            "barrier_false": tb.barrier_false - ta.barrier_false,
            "violated_claims": fb["violated_claims"] - fa["violated_claims"],
            "sanitized_flows": fb["sanitized_flows"] - fa["sanitized_flows"],
            "evidence": fb["evidence"] - fa["evidence"],
        },
        "instrument_control": control,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("claims")
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--json", dest="out")
    args = ap.parse_args()

    rows = []
    for repo in args.repos:
        print(f"\n=== {repo} ===", flush=True)
        row = _compare(repo, args.claims)
        rows.append(row)
        a, b, d = (row["arm_a_control"], row["arm_b_ceiling"], row["delta"])
        print(f"  instrument control : {row['instrument_control']}")
        print(f"  _use_site_terminates calls (A/B): "
              f"{a['terminates_calls']} / {b['terminates_calls']}")
        print(f"  §3a walks T/F/None (A): {a['walks_3a_true']}/"
              f"{a['walks_3a_false']}/{a['walks_3a_none']}")
        print(f"  §3a walks T/F/None (B): {b['walks_3a_true']}/"
              f"{b['walks_3a_false']}/{b['walks_3a_none']}")
        print(f"  barrier walks / False (A): {a['barrier_walks']}/"
              f"{a['barrier_false']}   (B): {b['barrier_walks']}/"
              f"{b['barrier_false']}")
        print(f"  FINDINGS violated/sanitized/evidence (A): "
              f"{a['violated_claims']}/{a['sanitized_flows']}/{a['evidence']}")
        print(f"  FINDINGS violated/sanitized/evidence (B): "
              f"{b['violated_claims']}/{b['sanitized_flows']}/{b['evidence']}")
        print(f"  DELTA findings: violated {d['violated_claims']:+d}  "
              f"sanitized {d['sanitized_flows']:+d}  "
              f"evidence {d['evidence']:+d}")

    live = [r for r in rows if r["instrument_control"].startswith("LIVE")]
    print("\n=== VERDICT ===")
    if not live:
        print("NO REPO GAVE A LIVE CONTROL. Nothing is measured here; this is "
              "an instrument result, not a finding about escape closing.")
    else:
        moved = [r for r in live if any(
            r["delta"][k] for k in
            ("violated_claims", "sanitized_flows", "evidence"))]
        print(f"repos with a live control : {len(live)}")
        print(f"  ... whose findings moved: {len(moved)}")
        if not moved:
            print("CEILING PAYOFF IS ZERO FINDINGS on every live repo. Closing "
                  "EVERY escape site changes no verdict, so no catalogue, "
                  "emission or receiver-typing fix in this family can either.")
        for r in moved:
            print(f"  {r['repo']}: {r['delta']}")

    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(rows, indent=2))
        print(f"\nWrote {args.out}")
    return 0 if live else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
