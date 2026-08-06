#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where does the ADR-0017 §3a walk lose a tainted value, and to what?

WHAT THIS DECIDES. INV-busis is held open by the claim that §3a "cannot refute
because 40% of escape sites are not calls (field/container/closure), which §7b
excludes". Pricing that claim needs the escape population split two ways at
once, because two different questions are being asked of it:

  * BY REASON — which of the walk's four ``escaped = True`` branches fired.
    Two of them (``source_undefined``, ``definition_unrecorded``) mean the DDG
    never held the fact, which is an EXTRACTION gap and nothing to do with
    §7b's scope. Two (``call_beside_heir``, ``no_heir``) mean the walk saw the
    use and could not account for it. Only the latter pair is INV-busis's
    population at all, and only ``no_heir`` is the one §7b's alias exclusion
    is invoked for.
  * BY NODE TYPE — within a reason, what the construct actually is. This is
    what tells an ``assert x == y`` (read, compared, stored nowhere) from a
    ``return x`` (moves the value to the caller) from a bare ``call`` — which
    is not an escape category at all but some upstream failure, and node type
    alone cannot say WHICH. ``scripts/measure-call-escape-cause.py`` exists
    for that: measured, a call-node escape is a missing EDGE (INV-mumov) on
    one repo and a missing RECEIVER TYPE (INV-linub) on another, and reading
    the bucket as either one alone gets the remedy wrong.

Folding those two axes is not a presentational choice, it is the specific
error that priced the "expression read" family at 78.6% of blockers: a
histogram over ``(symbol_id, line)`` alone cannot see that a chunk of its
``call`` bucket is an unseeded SOURCE line rather than a followed-and-lost
use. Hence the cross-tab, and hence :class:`~hypergumbo_core.taint.EscapeSite`
carrying the reason out of the walk itself.

DISCIPLINE ENCODED HERE, each because it was violated once and the wrong
number survived to a write-up:

  1. **The walk reports its own sites.** This wraps ``_ddg_taint_reaches`` and
     passes the ``escape_sites`` out-param; it never re-derives the escape
     logic. The instrument that produced the filed 78.6% did re-derive it,
     lived in a session scratchpad, and drifted out of sync with the
     function's signature.
  2. **Node types are production's CFG parse**, read verbatim off
     ``CfgStatement.node_type``, and the quoted text is production's own
     ``code_snippet`` — never a re-read of the source line.
  3. **Positive control before any null is believed.** A run that captures
     nothing, or resolves nothing to a CFG statement, exits non-zero and says
     so rather than printing an empty table that reads as a finding.
  4. **Three denominators are named, because they differ and the choice
     changes which family is largest.** Escape EVENTS, unique
     ``(function, line, reason)`` SITES over all walks, and SITES over
     BLOCKED walks. Only the last is the blocker population: a walk that
     returned ``True`` still reports the escapes it passed on the way to the
     sink, and those held nothing open. On pretix that is 126 / 111 / 72, and
     restricting to blocked walks moves the largest production family from
     calls to field writes. The first version of this instrument did not
     restrict, which is the same mistake INV-busis's earlier run had already
     documented and fixed.
  5. **The arm is reported.** A reclassification can only reach a FINDING
     through the barrier arm; the §3a arm tests ``is True`` and collapses
     ``False``/``None``. A run with zero barrier walks bounds every proposal
     built on this histogram at zero, no matter how the shapes split.
  6. **The reason vocabulary is asserted closed** against ``ESCAPE_REASONS``,
     so a fifth branch added later surfaces as a failure instead of silently
     landing in whichever bucket happens to catch it.

Usage:
    scripts/measure-escape-shapes.py REPO CLAIMS_YAML [--json OUT.json]

The JSON detail dump is the adjudication input: one record per unique site
with its reason, node type and code snippet, which is what per-shape
adjudication needs and what a bare histogram cannot support.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import sys
from pathlib import Path

import hypergumbo_core.taint as taint_mod
from hypergumbo_core.cfg import build_function_cfg, load_cfg_mapping
from hypergumbo_core.paths import is_test_file
from hypergumbo_core.taint import ESCAPE_REASONS, EscapeSite

_ORIGINAL = taint_mod._ddg_taint_reaches
CAPTURED: list[EscapeSite] = []
VERDICTS: collections.Counter = collections.Counter()
WALKS: list[dict] = []


def _capturing(*args, **kwargs):  # type: ignore[no-untyped-def]
    """Wrap production's walk; never reimplement it."""
    sites: list[EscapeSite] = []
    kwargs["escape_sites"] = sites
    verdict = _ORIGINAL(*args, **kwargs)
    CAPTURED.extend(sites)
    # COUNT THE WALKS, not just the escapes. Zero escape events has two
    # completely different meanings — "no §3a walk ran on this repo at all"
    # and "walks ran and every one of them closed" — and the second would be
    # a headline result. Without this counter they are indistinguishable, and
    # a repo reporting zero reads as uninformative when it may be the
    # strongest datapoint in the cohort.
    name = {True: "True", False: "False", None: "None"}[verdict]
    VERDICTS[name] += 1
    # RECORD THE WALK, not just its sites. Two facts are invisible in a flat
    # list of escape sites and each changes what the histogram means.
    #
    #   WHICH VERDICT. The walk returns ``True`` the moment a tainted value
    #   is used at a sink line — but escapes recorded BEFORE that point stay
    #   in the out-param. A walk that CONFIRMED a flow therefore still
    #   reports escape sites, and those sites blocked nothing. Counting them
    #   inflates the blocker population (measured: pretix 52 production
    #   sites over all walks against 15 over blocked walks) and inverts
    #   which family is largest. INV-busis's own earlier run says so
    #   outright — "sums over UNKNOWN non-barrier walks only" — and this
    #   instrument's first version did not, which is how it happened again.
    #
    #   WHICH ARM. The §3a arm tests ``_ddg_taint_reaches(...) is True``, so
    #   ``False`` and ``None`` collapse there and no change to the escape
    #   accounting can move a §3a verdict. Only the BARRIER arm reads
    #   ``False``, where it earns ``sanitized`` and the flow is dropped from
    #   the violation set. Any reclassification's finding-level effect is
    #   therefore bounded by the barrier-walk count — which a share of
    #   escape sites cannot see, however carefully it is split.
    WALKS.append({
        "symbol_id": args[0] if args else kwargs.get("symbol_id", ""),
        "arm": "barrier" if kwargs.get("barrier_lines") else "3a",
        "verdict": name,
        "sites": [tuple(s) for s in sites],
    })
    return verdict


def _statements_for(
    symbol_id: str, lines: set[int], root: Path,
) -> dict[int, tuple[str, str]]:
    """``line -> (node_type, code_snippet)`` from a rebuilt function CFG.

    Rebuilding is the only way to get ``node_type``: ``RepoDdg`` does not carry
    it. Two traps are closed here explicitly because each produced a clean,
    entirely fabricated result once:

      * Symbol-id paths are REPO-RELATIVE. Resolving them against the process
        CWD silently matched nothing and reported every site as unresolved —
        a "0 shapes" that looked like a finding.
      * The function is matched by its RECORDED SPAN, not by "any node with a
        body". Without the span test a neighbouring function's statement at
        the same line supplies the node type.

    KNOWN LIMITATION, stated rather than left to be rediscovered: the index is
    LINE-keyed and first-writer-wins, so a line hosting several CFG statements
    reports whichever came first in block-iteration order. Observed on pretix
    ``views.py:479`` — the source reads ``elif sale['status'] == 'REFUNDED':``
    but the recorded type is ``identifier``. It does not change any family
    boundary here (both an identifier and a comparison at a condition
    adjudicate the same way), but a split that turned on that distinction
    would need a (line, col) key.
    """
    from tree_sitter_language_pack import get_parser

    parts = symbol_id.split(":")
    if len(parts) < 5:
        return {}
    language = parts[0]
    candidate = Path(":".join(parts[1:-3]))
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        source = candidate.read_bytes()
        mapping = load_cfg_mapping(language)
        tree = get_parser(language).parse(source)
    except Exception:
        return {}

    try:
        lo, hi = (int(x) for x in parts[-3].split("-"))
    except ValueError:
        return {}

    out: dict[int, tuple[str, str]] = {}

    def walk_fns(node) -> None:  # type: ignore[no-untyped-def]
        body = node.child_by_field_name("body")
        if (
            body is not None
            and node.start_point[0] + 1 == lo
            and node.end_point[0] + 1 == hi
        ):
            try:
                cfg = build_function_cfg(body, source, mapping, symbol_id)
            except Exception:
                return
            for block in cfg.blocks.values():
                for stmt in block.statements:
                    if stmt.line in lines and stmt.line not in out:
                        out[stmt.line] = (stmt.node_type, stmt.code_snippet)
        for child in node.children:
            walk_fns(child)

    walk_fns(tree.root_node)
    return out


def _run_walks(repo: Path, claims: Path) -> None:
    from hypergumbo_core.cli import cmd_verify_claims

    class _Args:
        def __init__(self) -> None:
            self.path = str(repo)
            self.claims = str(claims)
            self.format = "json"
            self.input = None
            self.taint_sources = None
            self.taint_sinks = None
            self.taint_sanitizers = None

    taint_mod._ddg_taint_reaches = _capturing
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            cmd_verify_claims(_Args())
    finally:
        taint_mod._ddg_taint_reaches = _ORIGINAL
    json.loads(buf.getvalue())


def _table(title: str, rows: list[tuple[str, int]], total: int) -> None:
    print(f"\n{title:<44}{'n':>7}{'%':>8}")
    print("-" * 59)
    for label, n in rows:
        print(f"{label:<44}{n:>7}{100 * n / max(total, 1):>7.1f}%")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo")
    ap.add_argument("claims")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args(argv[1:])

    repo = Path(args.repo).resolve()
    _run_walks(repo, Path(args.claims))

    print(f"repo: {repo}")
    walks = sum(VERDICTS.values())
    arms: collections.Counter[str] = collections.Counter(
        w["arm"] for w in WALKS
    )
    print(f"§3a walks run: {walks}   verdicts: {dict(VERDICTS)}")
    print(f"by arm: {dict(arms)}")
    print(
        f"BARRIER-ARM WALKS: {arms.get('barrier', 0)} — the ONLY arm a "
        "reclassification\n  can reach a finding through, because the §3a arm "
        "tests `is True` and\n  collapses False/None. Zero here means any "
        "escape-accounting change is\n  INERT on this repo, whatever share of "
        "sites it addresses."
    )
    print(f"escape EVENTS captured (walk invocations x sites): {len(CAPTURED)}")
    if not CAPTURED:
        if walks:
            print(
                f"\n!! ZERO escape sites across {walks} walk(s) that DID run."
                "\n   This is a real result, not an instrument failure: every"
                "\n   walk accounted for every step. Read the verdict counts"
                "\n   above before calling it uninformative."
            )
        else:
            print(
                "\n!! ZERO walks ran on this repo, so there is nothing to"
                "\n   classify. Says nothing about escape shapes — the §3a"
                "\n   walk needs a source and a sink in the SAME function."
            )
        return 1

    unknown = {s.reason for s in CAPTURED} - ESCAPE_REASONS
    if unknown:
        print(f"\n!! reasons outside ESCAPE_REASONS: {sorted(unknown)}")
        return 1

    # Unique SITE, not unique line: one line can escape for two reasons across
    # different walks, and collapsing them would hide exactly the distinction
    # this instrument exists to draw.
    every = sorted({(s.symbol_id, s.line, s.reason) for s in CAPTURED})
    # THE BLOCKER POPULATION IS NOT THE ESCAPE POPULATION. Only a walk that
    # returned None was actually held open; a walk that returned True reports
    # the escapes it passed on the way to the sink, and those blocked nothing.
    # Both are printed so the gap is visible rather than a choice made
    # silently — on pretix it is 111 sites against 72.
    blocked = sorted({
        (s[0], s[1], s[2])
        for w in WALKS if w["verdict"] == "None" for s in w["sites"]
    })
    print(f"unique SITES over ALL walks:                        {len(every)}")
    print(f"unique SITES over BLOCKED walks (verdict None):     {len(blocked)}"
          "   <- the blocker population")
    unique = blocked or every
    if not blocked:
        print("\n!! No blocked walk recorded any site; falling back to all"
              "\n   walks so the run is not silently empty. Read accordingly.")

    by_reason: collections.Counter[str] = collections.Counter(
        r for _, _, r in unique
    )
    _table("escape reason (which branch fired) — BLOCKED walks",
           by_reason.most_common(), len(unique))

    lines_by_fn: dict[str, set[int]] = collections.defaultdict(set)
    for sym, line, _ in unique:
        lines_by_fn[sym].add(line)

    # Resolve ONCE PER FUNCTION. Rebuilding a file's parse tree per SITE is
    # quadratic in a function with several escapes, and it dominated wall
    # clock badly enough to look like a hang on a self-analysis run.
    stmt_cache: dict[str, dict[int, tuple[str, str]]] = {}
    resolved: list[dict[str, object]] = []
    unresolved = 0
    for sym, line, reason in unique:
        if sym not in stmt_cache:
            stmt_cache[sym] = _statements_for(sym, lines_by_fn[sym], repo)
        hit = stmt_cache[sym].get(line)
        if hit is None:
            unresolved += 1
            continue
        node_type, snippet = hit
        path = ":".join(sym.split(":")[1:-3])
        resolved.append(
            {
                "symbol_id": sym,
                "path": path,
                "line": line,
                "reason": reason,
                "node_type": node_type,
                "snippet": " ".join(snippet.split())[:160],
                "test_path": bool(is_test_file(path)),
            }
        )

    print(
        f"\nresolved to a CFG statement: {len(resolved)}"
        f"   unresolved: {unresolved}"
    )
    if not resolved:
        print(
            "\n!! NOTHING resolved to a CFG statement. The node-type split\n"
            "   below would be empty for instrument reasons, not real ones."
        )
        return 1

    prod = [r for r in resolved if not r["test_path"]]
    for scope, rows in (("ALL PATHS", resolved), ("PRODUCTION PATHS", prod)):
        if not rows:
            continue
        print(f"\n===== {scope} ({len(rows)} sites) =====")
        cross: dict[str, collections.Counter[str]] = collections.defaultdict(
            collections.Counter
        )
        for r in rows:
            cross[str(r["reason"])][str(r["node_type"])] += 1
        for reason in sorted(cross, key=lambda k: -sum(cross[k].values())):
            n = sum(cross[reason].values())
            _table(
                f"{reason}  ({n} sites, "
                f"{100 * n / len(rows):.1f}% of {scope.lower()})",
                cross[reason].most_common(),
                n,
            )

    print(
        "\nprod = NOT paths.is_test_file — the BROAD test-OR-support predicate."
        "\n  It flags mocks/, fixtures/, testdata/, benches/ as non-production,"
        "\n  and is deliberately NOT Symbol.is_test_file (the narrow spec §14"
        "\n  'test code' role flag). The two diverge by design; picking one"
        "\n  silently would be choosing the answer."
    )

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(resolved, indent=1))
        print(f"\ndetail: {args.json_out}  ({len(resolved)} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
