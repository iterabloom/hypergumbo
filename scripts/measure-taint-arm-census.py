#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Census of PRODUCTION ``_ddg_taint_reaches`` calls, by arm, gate and verdict.

WHAT THIS EXISTS TO PREVENT. A claim of the form "that arm never runs" was
load-bearing on THREE open tracker items (INV-lupav, INV-busis, INV-sadah) and
was false. It had been measured once, on one repo, and inherited thereafter.
This instrument is the re-measurement, kept in-repo so the next such claim costs
one command instead of one session.

THE ARMS, and why they are not interchangeable::

    barrier_lines=None   section-3a CONFIRM-ONLY arm. The call site tests
                         `is True`, so False and None collapse — a False here
                         is inert TODAY.
    barrier_lines set    WI-fasub same-function sanitizer arm. Since PR #214 a
                         False EARNS `sanitized` and DELETES a finding from the
                         violation set. This is the arm where an unearned False
                         is a live falsehood in a security tool.

A census that does not separate them reports a single number for two different
risk profiles.

WHY A ZERO HERE IS SUSPECT (LIVE.md rule 12: check the control, not the
subject). The barrier arm is reachable only for a flow whose LABEL some
sanitizer claims. The built-in catalogue expresses two transforms, so on a repo
that declares no sanitizers of its own the arm is unreachable **by
construction** — the zero is measuring the catalogue, not the code. The first
probe run against this hypothesis had no sanitizer for the flow's label, so
`barrier_sites` was empty and the arm could not fire; adding a `sanitizers.yaml`
produced 21 barrier walks on the same repo, same binary. **If this reports zero
barrier walks, check whether the target repo declares a sanitizer for the label
in question before reporting the zero as a property of the code.**

THE FORFEIT COUNTERFACTUAL. On the barrier arm with ``forfeit_refutation`` set,
the walk is run a second time with the gate OFF. A pair of
(gated=None, ungated=False) is a suppression the gate actually prevented — that
is, a finding that would have been deleted without it. That count is the gate's
measured worth, as opposed to its argued worth.

Taint runs ONLY inside ``cmd_verify_claims``: no claims file, no taint, and this
script reports an empty census rather than an error.

Run (defaults probe this repo against its own claims file)::

    python scripts/measure-taint-arm-census.py
    python scripts/measure-taint-arm-census.py ~/ALL_REPOS/alertmanager \\
        --claims /path/to/claims.yaml

``PROBE_REPO`` / ``PROBE_CLAIMS`` are honoured as fallbacks for the positional
and ``--claims`` arguments respectively.
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

from hypergumbo_core import taint as taint_mod

TALLY: collections.Counter = collections.Counter()
FORFEIT_FLIPS: list = []

_real = taint_mod._ddg_taint_reaches


def _traced(symbol_id, source_lines, sink_lines, ddg_uses, *a, **kw):
    """Tally each production walk by (arm, gate flag, verdict)."""
    barrier = kw.get("barrier_lines")
    forfeit = kw.get("forfeit_refutation", False)
    arm = "barrier" if barrier else "s3a"
    result = _real(symbol_id, source_lines, sink_lines, ddg_uses, *a, **kw)
    TALLY[(arm, forfeit, repr(result))] += 1
    # Counterfactual: on the barrier arm with the gate ON, what would the
    # UNGATED walk have said?  A (gated=None, ungated=False) pair is a live
    # suppression the gate actually prevented.
    if arm == "barrier" and forfeit:
        kw2 = dict(kw)
        kw2["forfeit_refutation"] = False
        ungated = _real(symbol_id, source_lines, sink_lines, ddg_uses, *a, **kw2)
        if ungated is False and result is None:
            FORFEIT_FLIPS.append(symbol_id)
    return result


taint_mod._ddg_taint_reaches = _traced

from hypergumbo_core.cli import main  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("repo", nargs="?",
                    default=os.environ.get("PROBE_REPO"),
                    help="repo to verify (default: this repo)")
parser.add_argument("--claims",
                    default=os.environ.get("PROBE_CLAIMS",
                                           "docs/hypergumbo.claims.yaml"),
                    help="claims file; taint runs ONLY under verify-claims")
opts = parser.parse_args()

argv = ["verify-claims"]
if opts.repo:
    argv.append(opts.repo)
argv += ["--claims", opts.claims]
rc = main(argv)

print("\n" + "=" * 62, file=sys.stderr)
print(f"verify-claims rc={rc}  repo={opts.repo or '.'}  claims={opts.claims}",
      file=sys.stderr)
print("ARM CENSUS  (arm, forfeit_flag, result) -> count", file=sys.stderr)
if not TALLY:
    print("  <no _ddg_taint_reaches calls at all>", file=sys.stderr)
for key, n in sorted(TALLY.items(), key=lambda kv: -kv[1]):
    print(f"  {key} -> {n}", file=sys.stderr)
print(f"TOTAL walks: {sum(TALLY.values())}", file=sys.stderr)
barrier_walks = sum(n for (arm, _, _), n in TALLY.items() if arm == "barrier")
if barrier_walks == 0:
    print("  NOTE: 0 barrier walks. Before reporting that as a property of the",
          file=sys.stderr)
    print("        CODE, check whether this repo declares a sanitizer for the",
          file=sys.stderr)
    print("        label in question — the arm is unreachable without one.",
          file=sys.stderr)
print(f"GATE-PREVENTED SUPPRESSIONS (barrier False -> None): "
      f"{len(FORFEIT_FLIPS)}", file=sys.stderr)
for s in FORFEIT_FLIPS[:10]:
    print(f"    {s}", file=sys.stderr)
print("=" * 62, file=sys.stderr)
