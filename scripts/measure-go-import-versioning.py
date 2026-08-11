#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Paired A/B for INV-javid: what does binding a ``/vN`` package's real name move?

THE CHANGE UNDER TEST. ``_go_package_identifier`` used to be
``import_path.rsplit("/", 1)[-1]``, so a module at Go major version 2 or higher
bound the literal string ``vN`` and its real identifier was never registered.
Every call on such a package lost its module hint and fell to the ``external``
placeholder.

WHY A PAIRED A/B AND NOT A ONE-ARM COUNT. "N edges now carry an import path" is
not a result — the denominator matters and so does the other direction. This
runs BOTH arms over the same tree in the same session and reports gains AND
losses, because the only genuinely dangerous outcome here is an edge that used
to resolve and no longer does.

THE THREE NUMBERS THAT DECIDE IT:

  1. ``calls_total`` must be IDENTICAL across arms. This change rewrites the
     module slot of a dst; it must not create or destroy an edge. A difference
     is a defect in the change, not a finding about the corpus — which is why
     it is asserted rather than reported. When "no change" is the correct
     answer for a metric, a broken run looks BETTER than a working one, so
     non-destruction has to be checked explicitly.

  2. ``external_slot`` should FALL. That is the payoff, and it is the recall
     direction: an edge that named nothing now names its module.

  3. ``moved_to_in_repo`` is THE HAZARD and gets its own count. When the
     versioned module is the repo's own (``module github.com/x/y/v2`` importing
     ``github.com/x/y``), binding the alias lets resolution find a FIRST-PARTY
     symbol. A prior Go typing attempt moved 692 edges and put 118 of them
     (17.1%) on the WRONG in-repo symbol (CHANGELOG.md:244), so this bucket is
     reported separately and never folded into the headline.

ARMS RUN IN SEPARATE PROCESSES, deliberately. Patching the derivation in-process
and analysing twice would share every module-level cache the analyzer holds, and
a cache that survives the patch reports the first arm's answer twice — which is
the shape of null this project has been fooled by before. Each arm is a fresh
interpreter.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import subprocess
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _legacy_identifier(import_path: str) -> str:
    """The pre-INV-javid derivation, reproduced here as the A arm."""
    return import_path.rsplit("/", 1)[-1]


def _module_slot(dst: str) -> str:
    """The path/module slot of a symbol id, tolerant of colons in it."""
    parts = dst.split(":")
    return ":".join(parts[1:-3]) if len(parts) >= 5 else ""


def run_arm(repo: pathlib.Path, legacy: bool) -> dict:
    from hypergumbo_lang_mainstream import go as go_mod

    if legacy:
        go_mod._go_package_identifier = _legacy_identifier

    analysis = go_mod.analyze_go(repo)
    edges = [e for e in analysis.edges if e.edge_type == "calls"]
    kinds = {s.id: s.kind for s in analysis.symbols}
    sites: dict[str, list[str]] = {}
    for e in edges:
        # THE SITE KEY MUST NOT CONTAIN ANYTHING THE CHANGE CAN ALTER.
        # The first draft keyed on (src, line, dst NAME slot) and the arms
        # promptly disagreed about the site SET on caddy — because resolving a
        # dst in-repo can re-spell its name slot receiver-qualified
        # (`Duration` -> `Values.Set`). That is a discriminator derived from the
        # thing under test, so it reported the change as a non-destruction
        # failure. Only the CALLER side is arm-invariant, so the key is
        # (src, line) and the dsts at that key are compared as a multiset.
        sites.setdefault(f"{e.src}|{e.line}", []).append(e.dst)
    return {
        "calls_total": len(edges),
        "external_slot": sum(1 for e in edges if _module_slot(e.dst) == "external"),
        "sites": {k: sorted(v) for k, v in sites.items()},
        "kinds": kinds,
    }


def compare(a: dict, b: dict) -> dict:
    """A = legacy, B = fixed."""
    a_sites, b_sites = a["sites"], b["sites"]
    shared = set(a_sites) & set(b_sites)
    changed_keys = [k for k in shared if a_sites[k] != b_sites[k]]

    recovered, moved_in_repo, regressed = [], [], []
    in_repo_kinds: dict[str, int] = {}
    changed = 0
    for key in changed_keys:
        # MULTISET DIFFERENCE, NOT POSITIONAL PAIRING. The first draft zipped the
        # two arms' dst lists together inside a site, which silently paired
        # UNRELATED calls whenever one dst changed and the sort order shifted:
        # it reported `BlobStatter.Stat -> external:Digest` as a regression when
        # those are two different calls on one line. Nothing here needs the two
        # arms aligned — a dst present after and absent before is a gain, and the
        # reverse is a loss — so the pairing was pure invented error.
        olds = collections.Counter(a_sites[key])
        news = collections.Counter(b_sites[key])
        gained = news - olds
        changed += sum(gained.values())
        for dst, n in gained.items():
            if _module_slot(dst) == "external":
                # An edge now names NOTHING where it named something before.
                regressed.extend([(key, dst)] * n)
                continue
            kind = b["kinds"].get(dst)
            if kind and kind != "external_symbol":
                in_repo_kinds[kind] = in_repo_kinds.get(kind, 0) + n
                moved_in_repo.extend([(key, dst)] * n)
            else:
                recovered.extend([(key, dst)] * n)

    return {
        "calls_total_legacy": a["calls_total"],
        "calls_total_fixed": b["calls_total"],
        "NON_DESTRUCTION_OK": a["calls_total"] == b["calls_total"]
        and set(a_sites) == set(b_sites),
        "external_slot_legacy": a["external_slot"],
        "external_slot_fixed": b["external_slot"],
        "sites_changed": changed,
        "recovered_import_path": len(recovered),
        "moved_to_in_repo": len(moved_in_repo),
        "REGRESSED_to_external": len(regressed),
        "sample_recovered": [d for _, d in recovered[:5]],
        "sample_moved_to_in_repo": [{"now": d} for _, d in moved_in_repo[:8]],
        "in_repo_landing_kinds": in_repo_kinds,
        "sample_regressed": [{"now": d, "at": k} for k, d in regressed[:5]],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo")
    ap.add_argument("--arm", choices=["legacy", "fixed"])
    ap.add_argument("--json", dest="out")
    args = ap.parse_args()
    repo = pathlib.Path(args.repo).resolve()

    if args.arm:  # child process: run one arm, emit its raw counts
        print(json.dumps(run_arm(repo, legacy=args.arm == "legacy")))
        return 0

    arms = {}
    for arm in ("legacy", "fixed"):
        proc = subprocess.run(  # noqa: S603
            [sys.executable, __file__, str(repo), "--arm", arm],
            capture_output=True, text=True, cwd=_REPO_ROOT, check=False,
        )
        if proc.returncode != 0:
            sys.stderr.write(f"arm {arm} failed:\n{proc.stderr[-3000:]}\n")
            return 2
        arms[arm] = json.loads(proc.stdout)

    result = compare(arms["legacy"], arms["fixed"])
    result["repo"] = repo.name

    print(f"\n=== {repo.name} ===")
    if not result["NON_DESTRUCTION_OK"]:
        print("  !! NON-DESTRUCTION FAILED — the arms disagree on the call-site "
              "SET, so this change is adding or dropping edges rather than "
              "relabelling them. Do not read the numbers below as a payoff.")
    print(f"  calls edges          {result['calls_total_legacy']} -> "
          f"{result['calls_total_fixed']}"
          f"   {'IDENTICAL' if result['NON_DESTRUCTION_OK'] else 'DIFFER'}")
    print(f"  'external' slot      {result['external_slot_legacy']} -> "
          f"{result['external_slot_fixed']}")
    print(f"  sites changed        {result['sites_changed']}")
    print(f"    recovered path     {result['recovered_import_path']}")
    print(f"    -> IN-REPO symbol  {result['moved_to_in_repo']}   "
          f"<- the hijack surface; read the samples")
    print(f"       landing kinds   {result['in_repo_landing_kinds']}")
    print(f"    REGRESSED          {result['REGRESSED_to_external']}   "
          f"<- must be 0")
    for s in result["sample_moved_to_in_repo"]:
        print(f"      in-repo: {s['now']}")
    for s in result["sample_regressed"]:
        print(f"      REGRESS: {s['now']}  at {s['at']}")

    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(result, indent=2))
        print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
