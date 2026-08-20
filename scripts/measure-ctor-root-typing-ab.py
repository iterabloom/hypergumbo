#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A/B the constructor-ROOT receiver typing at the call-emission site.

THE QUESTION. PR #253 taught the resolver that a constructor call carries a type, but
wired it only into the two ASSIGNMENT call sites. So ``p = Path(raw); p.write_text(x)``
tagged an I/O boundary and the identical ``Path(raw).write_text(x)`` tagged none — the
``.write_text`` call emitted no edge at all. This change passes the same constructor
resolver to the emission site, so a chain rooted at a constructor is typed exactly as an
assigned one is.

WHY IT IS MEASURED IN BOTH DIRECTIONS RATHER THAN ARGUED ABOUT. Typing a receiver has
two opposite effects and this tool reports both:

  RECALL (adds findings)      a typed receiver reaches its python.yaml entry, so real
                              filesystem I/O that was invisible becomes a boundary and
                              a candidate taint sink.
  SUPPRESSION (deletes them)  a typed dst also populates ``callees_at``, which lets
                              ``_use_site_terminates`` decide a use site it previously
                              had to treat as an escape. Since PR #214 a ``False`` earns
                              ``sanitized`` and DROPS the flow from the violation set.

The second direction is the expensive one for a security tool, so a net gain in
boundaries does NOT license ignoring a drop in violated claims.

THE ONE KNOB, and why it is scoped by caller rather than by patching the resolver.
Arm A must reproduce the PRE-fix emission site exactly: it called
``_derived_receiver_module(func.value, external_var_types)`` with no constructor
resolver. Patching ``_external_constructor_type`` globally would ALSO revert #253's
assignment-site typing and measure a larger change than the one under test. So the arm
drops ``ctor_type`` only when ``_receiver_type`` is called directly from
``_process_call`` — the emission site — and leaves every ``_preserved_receiver_type``
caller untouched. For a non-``ast.Name`` receiver (the only kind that branch accepts)
``_receiver_type(v, evt, None)`` is definitionally the old
``_derived_receiver_module(v, evt)``, so this is the pre-fix behaviour and not an
approximation of it.

POSITIVE CONTROL, run first and printed before any corpus number is believed: a fixture
whose two arms are KNOWN to differ (``Path(raw).write_text(data)`` — 0 boundaries before,
1 after). If the arms agree there, the instrument cannot detect this change and every
corpus zero it reports is an uncontrolled null rather than evidence. This project has
produced three such nulls already.

Usage:
    scripts/measure-ctor-root-typing-ab.py REPO [REPO ...] [--json OUT.json]
    scripts/measure-ctor-root-typing-ab.py --self-claims [--json OUT.json]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
import sys
import tempfile
from typing import Any

import hypergumbo_lang_mainstream.py as py_mod
from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries


@contextlib.contextmanager
def _arm(ctor_root_typing: bool) -> Any:
    """Run the body with constructor-root typing at the emission site on or off."""
    real = py_mod._receiver_type
    if ctor_root_typing:
        yield
        return

    def prefix_receiver_type(
        receiver: Any, external_var_types: Any, ctor_type: Any = None,
    ) -> Any:
        # ``_process_call`` IS the emission site. Every other caller reaches this
        # through ``_preserved_receiver_type`` and keeps the resolver PR #253 gave it.
        if sys._getframe(1).f_code.co_name == "_process_call":
            ctor_type = None
        return real(receiver, external_var_types, ctor_type)

    py_mod._receiver_type = prefix_receiver_type
    try:
        yield
    finally:
        py_mod._receiver_type = real


def _boundaries(repo: str) -> dict[str, int]:
    """Boundary chains and typed-call edges on one repo, through production's own
    analyzer and catalogue rather than a re-implementation."""
    result = py_mod.analyze_python(pathlib.Path(repo))
    edges = result.edges
    tagged = tag_io_boundaries(edges, {"python": load_catalog("python")})
    typed = sum(
        1 for e in edges
        if e.edge_type == "calls"
        and (e.dst or "").startswith("python:")
        and (e.dst or "").split(":")[1] not in ("", "external")
        and not (e.dst or "").split(":")[1].startswith("/")
    )
    return {"boundaries": tagged, "typed_call_edges": typed, "edges": len(edges)}


_CONTROL_SOURCE = """from pathlib import Path


def handler(raw, data):
    Path(raw).write_text(data)
"""


def _positive_control(workdir: pathlib.Path) -> dict[str, Any]:
    src = workdir / "control"
    src.mkdir(parents=True, exist_ok=True)
    (src / "app.py").write_text(_CONTROL_SOURCE)
    with _arm(False):
        before = _boundaries(str(src))
    with _arm(True):
        after = _boundaries(str(src))
    return {
        "arm_a_boundaries": before["boundaries"],
        "arm_b_boundaries": after["boundaries"],
        "DETECTS_THE_CHANGE": after["boundaries"] > before["boundaries"],
    }


def _self_claims_arm(claims: str, repo: str, *, ctor_root_typing: bool) -> dict[str, int]:
    """verify-claims through the real CLI, so the taint/sanitizer direction is read
    from production rather than inferred from edge counts."""
    from hypergumbo_core.cli import main

    with _arm(ctor_root_typing):
        argv = sys.argv
        sys.argv = ["hypergumbo", "verify-claims", repo, "--claims", claims, "--json"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                with contextlib.suppress(SystemExit):
                    main()
        finally:
            sys.argv = argv
    raw = buf.getvalue().strip()
    report = json.loads(raw) if raw.startswith("{") else {}
    violated = sanitized = evidence = inconclusive = 0
    for verdict in report.get("verdicts", []):
        if verdict.get("verdict") == "violated":
            violated += 1
        if verdict.get("verdict") == "inconclusive":
            inconclusive += 1
        sanitized += verdict.get("sanitized_flows", 0) or 0
        evidence += verdict.get("evidence_count", 0) or 0
    return {
        "violated_claims": violated,
        "inconclusive_claims": inconclusive,
        "sanitized_flows": sanitized,
        "evidence": evidence,
    }


def main_cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repos", nargs="*")
    ap.add_argument("--json", dest="out")
    ap.add_argument("--self-claims", action="store_true")
    ap.add_argument("--claims", default="docs/hypergumbo.claims.yaml")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        control = _positive_control(pathlib.Path(td))
    print("POSITIVE CONTROL:", json.dumps(control), file=sys.stderr)
    if not control["DETECTS_THE_CHANGE"]:
        print("FAILED POSITIVE CONTROL — this run measured NOTHING. Every zero "
              "below is an uncontrolled null, not evidence.", file=sys.stderr)
        return 3

    report: dict[str, Any] = {"positive_control": control, "repos": {}}
    for repo in args.repos:
        with _arm(False):
            before = _boundaries(repo)
        with _arm(True):
            after = _boundaries(repo)
        report["repos"][pathlib.Path(repo).name] = {
            "arm_a_prefix": before,
            "arm_b_ctor_root": after,
            "delta": {k: after[k] - before[k] for k in before},
        }
        print(f"  {pathlib.Path(repo).name}: boundaries "
              f"{before['boundaries']} -> {after['boundaries']} "
              f"(delta {after['boundaries'] - before['boundaries']:+d})",
              file=sys.stderr)

    if args.self_claims:
        a = _self_claims_arm(args.claims, ".", ctor_root_typing=False)
        b = _self_claims_arm(args.claims, ".", ctor_root_typing=True)
        report["self_claims"] = {
            "arm_a_prefix": a,
            "arm_b_ctor_root": b,
            "delta": {k: b[k] - a[k] for k in a},
        }
        print("  self-claims:", json.dumps(report["self_claims"]["delta"]),
              file=sys.stderr)

    text = json.dumps(report, indent=2)
    if args.out:
        pathlib.Path(args.out).write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
