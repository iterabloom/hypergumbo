#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A/B the sanitizer registration gate's module-slot permit branch.

THE QUESTION. `_register_sanitizer_callers` refuses an unresolved
`call_construct == "method"` edge unless the callee is *qualified* — that is
the INV-finoh gate, and it exists because binding a bare `x.encrypt()` to
`Fernet.encrypt` would falsely sanitize a real flow (a false NEGATIVE, the
expensive direction for a security tool).

The permit branch read only the **name slot** of the dst symbol id. Production
analyzers put an inferred receiver type in the **module slot**:

    py:external:0-0:Fernet.encrypt:unresolved     <- synthetic, tests only
    py:cryptography.fernet.Fernet:0-0:encrypt:…   <- what analyzers emit

So for a method-shaped sanitizer the comparison was
`"cryptography.fernet.Fernet.encrypt" == "encrypt"`, false by construction, and
the refusal was unconditional. Every shipped sanitizer is method-shaped.

Reading the module slot too is therefore a **suppressive** change: more
sanitizers register, more barrier walks run, more walks return `False`, and
since PR #214 a `False` earns `sanitized` and DROPS the flow from the violation
set. That is the direction that can delete a real finding, so it gets measured
on findings in BOTH signs before it ships, not argued about.

METHOD, and why it is not confounded. The two arms differ in exactly one
respect: whether `_module_from_symbol_path` is consulted *inside sanitizer
registration*. Arm A rebinds it to a constant `""` for the duration of that one
call and restores it immediately, which reproduces the pre-fix gate exactly;
source and sink matching, which call the same helper, are untouched. Patching
the helper globally would have changed those too and measured a different
change than the one under test.

Everything is read through production's own code — the real CLI entry point,
the real catalogues, the real analyzer — wrapped rather than reimplemented.

POSITIVE CONTROL, run first and printed before any corpus number is believed:
a fixture whose two arms are KNOWN to differ (idiomatic `f: Fernet` receiver,
decrypt -> encrypt -> os.remove in one function). If the arms agree there the
instrument cannot detect the change, and every corpus zero it reports is an
uncontrolled null rather than evidence. Three successive mechanisms proposed
for the barrier-arm zero were wrong for exactly that reason.

DECOMPOSING A ZERO. A corpus zero has two readings with identical evidence, so
both are reported separately:

  ABSENCE  no edge anywhere in the repo even reaches the gate — the short name
           never collides with a sanitizer leaf. Nothing to permit or refuse.
  REFUSAL  edges reached the gate and were turned away. Only this reading means
           the fix has live blast radius.

Usage:
    scripts/measure-sanitizer-module-slot-ab.py CLAIMS_YAML REPO [REPO ...]
                                                [--json OUT.json]
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


class _GateTally:
    """Counts what the gate saw, per arm, without re-deriving its predicates."""

    def __init__(self) -> None:
        self.reached = 0          # edges whose short name hit a sanitizer leaf
        self.registered = 0       # (caller, input_taint) call sites recorded
        self.barrier_walks = 0    # DDG walks that ran with barrier_lines
        self.barrier_false = 0    # ... of those, the ones returning False

    def as_dict(self) -> dict[str, int]:
        return {
            "gate_reached": self.reached,
            "sanitizer_call_sites": self.registered,
            "barrier_walks": self.barrier_walks,
            "barrier_false": self.barrier_false,
        }


def _run_arm(
    repo: str, claims: str, *, module_slot: bool,
) -> tuple[dict[str, Any], _GateTally]:
    """Run verify-claims once with the module-slot permit on or off."""
    tally = _GateTally()
    real_register = taint_mod._register_sanitizer_callers
    real_walk = taint_mod._ddg_taint_reaches
    real_module_of = taint_mod._module_from_symbol_path

    def counting_register(
        edges: list[dict[str, Any]],
        sanitizer_by_callee: dict[str, Any],
        sanitizer_callers: dict[str, Any],
        ambiguous_names: frozenset[str] = frozenset(),
        sanitizer_lines: dict[Any, list[int]] | None = None,
    ) -> None:
        for edge in edges:
            if not taint_mod._is_taint_call_edge(edge):
                continue
            name = taint_mod._extract_callee_name(edge["dst"])
            if sanitizer_by_callee.get(name):
                tally.reached += 1
        # Arm A: the gate cannot see the module slot. Scoped to this call so
        # source/sink matching keeps the helper they have always had.
        if not module_slot:
            taint_mod._module_from_symbol_path = lambda _s: ""
        try:
            real_register(
                edges, sanitizer_by_callee, sanitizer_callers,
                ambiguous_names=ambiguous_names, sanitizer_lines=sanitizer_lines,
            )
        finally:
            taint_mod._module_from_symbol_path = real_module_of
        if sanitizer_lines is not None:
            tally.registered += sum(len(v) for v in sanitizer_lines.values())

    def counting_walk(*args: Any, **kwargs: Any) -> Any:
        result = real_walk(*args, **kwargs)
        if kwargs.get("barrier_lines"):
            tally.barrier_walks += 1
            if result is False:
                tally.barrier_false += 1
        return result

    taint_mod._register_sanitizer_callers = counting_register
    taint_mod._ddg_taint_reaches = counting_walk
    try:
        from hypergumbo_core.cli import main

        argv = sys.argv
        sys.argv = ["hypergumbo", "verify-claims", repo,
                    "--claims", claims, "--json"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                with contextlib.suppress(SystemExit):
                    main()
        finally:
            sys.argv = argv
        raw = buf.getvalue().strip()
        report = json.loads(raw) if raw.startswith("{") else {}
    finally:
        taint_mod._register_sanitizer_callers = real_register
        taint_mod._ddg_taint_reaches = real_walk
        taint_mod._module_from_symbol_path = real_module_of
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
    before, tally_a = _run_arm(repo, claims, module_slot=False)
    after, tally_b = _run_arm(repo, claims, module_slot=True)
    fa, fb = _findings(before), _findings(after)
    return {
        "repo": repo,
        "arm_a_no_module_slot": {**tally_a.as_dict(), **fa},
        "arm_b_module_slot": {**tally_b.as_dict(), **fb},
        "delta": {
            "sanitizer_call_sites": (tally_b.registered - tally_a.registered),
            "barrier_walks": tally_b.barrier_walks - tally_a.barrier_walks,
            "evidence": fb["evidence"] - fa["evidence"],
            "sanitized_flows": fb["sanitized_flows"] - fa["sanitized_flows"],
        },
        "zero_reading": (
            "ABSENCE — no edge reached the gate"
            if tally_a.reached == 0 else
            "REFUSAL — edges reached the gate"
        ),
    }


_CONTROL_SOURCE = '''import os
from cryptography.fernet import Fernet


def handler(f: Fernet, token):
    plain = f.decrypt(token)
    safe = f.encrypt(plain)
    os.remove(safe)
'''


def _positive_control(claims: str, workdir: pathlib.Path) -> dict[str, Any]:
    """A fixture whose arms are KNOWN to differ. Believed before any zero."""
    fixture = workdir / "control"
    fixture.mkdir(parents=True, exist_ok=True)
    (fixture / "h.py").write_text(_CONTROL_SOURCE)
    result = _compare(str(fixture), claims)
    result["detects_the_change"] = (
        result["delta"]["sanitizer_call_sites"] > 0
        and result["delta"]["barrier_walks"] > 0
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("claims", help="claims YAML selecting plaintext flows")
    parser.add_argument("repos", nargs="+", help="repo roots to measure")
    parser.add_argument("--json", dest="out", help="write full results here")
    parser.add_argument(
        "--workdir", default="/tmp/hg-sanitizer-ab",
        help="scratch dir for the positive-control fixture",
    )
    args = parser.parse_args()

    control = _positive_control(args.claims, pathlib.Path(args.workdir))
    print("POSITIVE CONTROL (arms must differ):")
    print(f"  sanitizer_call_sites delta = "
          f"{control['delta']['sanitizer_call_sites']}")
    print(f"  barrier_walks        delta = "
          f"{control['delta']['barrier_walks']}")
    print(f"  detects the change         = {control['detects_the_change']}")
    if not control["detects_the_change"]:
        print("\nINSTRUMENT CANNOT DETECT THE CHANGE. Every zero below would "
              "be an uncontrolled null. Refusing to report corpus numbers.")
        if args.out:
            pathlib.Path(args.out).write_text(
                json.dumps({"control": control, "repos": []}, indent=2))
        return 2

    results = []
    for repo in args.repos:
        print(f"\n=== {repo} ===")
        row = _compare(repo, args.claims)
        results.append(row)
        print(f"  gate reached (A/B): {row['arm_a_no_module_slot']['gate_reached']}"
              f" / {row['arm_b_module_slot']['gate_reached']}")
        print(f"  sanitizer call sites (A/B): "
              f"{row['arm_a_no_module_slot']['sanitizer_call_sites']}"
              f" / {row['arm_b_module_slot']['sanitizer_call_sites']}")
        print(f"  barrier walks (A/B): "
              f"{row['arm_a_no_module_slot']['barrier_walks']}"
              f" / {row['arm_b_module_slot']['barrier_walks']}")
        print(f"  evidence (A/B): {row['arm_a_no_module_slot']['evidence']}"
              f" / {row['arm_b_module_slot']['evidence']}")
        print(f"  zero reading: {row['zero_reading']}")

    if args.out:
        pathlib.Path(args.out).write_text(
            json.dumps({"control": control, "repos": results}, indent=2))
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
