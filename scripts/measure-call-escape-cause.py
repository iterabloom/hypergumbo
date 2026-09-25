#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Why does a §3a escape site whose CFG node is a CALL still escape?

THE QUESTION. ``scripts/measure-escape-shapes.py`` reports that the single
largest ``no_heir`` bucket is node type ``call`` — the walk lost a tainted
value at a line that plainly invokes something. INV-busis's taxonomy has no
such category (it names field write, container subscript, closure capture), so
these sites are being counted against ADR-0017 §7b's alias exclusion while
having nothing to do with aliasing. Something upstream failed, and "a call
edge is missing" is only ONE of four things it could be.

THE FUNNEL, and the point of this instrument is that a site can drop out at
any stage while every stage looks the same from inside the walk:

  1. **emitted?**    Did the analyzer produce ANY edge at (function, line)?
                     No  ⇒ an EMISSION gap — INV-mumov's territory.
  2. **taint-typed?** Did it survive ``_is_taint_call_edge``?
                     No  ⇒ the edge exists but carries a type taint does not
                     read as a call. Note this reads ``edge["type"]``, not
                     ``edge_type`` — the near-miss twin that once made a
                     positive control fail silently.
  3. **catalogue key?** Did ``_catalogue_key_for_edge`` yield a key?
                     No  ⇒ the edge exists and is a call, but names nothing a
                     summary can be looked up under. This is the documented
                     behaviour for an ``external`` placeholder dst:
                     ``python:external:0-0:print:unresolved`` has the
                     placeholder in its module slot, so the key is "" BY
                     DESIGN. An edge emitted with that dst can never reach
                     ``callees_at``, so emitting it moves nothing.
  4. **terminating summary?** Is the key in the §4 index, and does its summary
                     say the callee consumes the argument?
                     No  ⇒ an honest catalogue gap (key present, no summary)
                     or an honest propagating callee (summary says the taint
                     flows onward). Only the latter is a REAL escape.

Stages 1-3 are all defects with different owners, and only stage 4's second
half is the escape the walk is entitled to report. Collapsing them is how
"fixing emission" gets predicted to move findings that it cannot move.

METHOD. Everything is read through production's own predicates, wrapped rather
than reimplemented: ``_is_taint_call_edge``, ``_catalogue_key_for_edge`` and
``_summary_terminates`` are called, never re-derived. The escape sites come
from the walk's own ``escape_sites`` out-param, and ``callees_at`` /
``summaries`` are read off the walk's ARGUMENTS — positionally at index 4 and
5 respectively, which is worth stating because reading index 5 as
``callees_at`` once produced a clean, entirely false 100%.

POSITIVE CONTROL, printed before any verdict is believed: the number of edges
seen, the number surviving each stage, and the size of the summary index. A
run where stage 2 admits nothing, or where the summary index is empty, cannot
distinguish any of the four causes and says so instead of reporting zeros.

Usage:
    scripts/measure-call-escape-cause.py REPO CLAIMS_YAML [--json OUT.json]
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
from hypergumbo_core.taint import EscapeSite

_ORIG_WALK = taint_mod._ddg_taint_reaches
_ORIG_IS_CALL = taint_mod._is_taint_call_edge
_ORIG_KEY = taint_mod._catalogue_key_for_edge

CAPTURED: list[EscapeSite] = []
SEEN_EDGES: list[tuple[dict, bool]] = []
SUMMARIES: dict = {}
CALLEES_AT: dict = {}


def _wrap_walk(*args, **kwargs):  # type: ignore[no-untyped-def]
    sites: list[EscapeSite] = []
    kwargs["escape_sites"] = sites
    # Positional contract: (symbol_id, source_lines, sink_lines, ddg_uses,
    # callees_at, summaries, ...). Index 4 is callees_at; index 5 is
    # summaries. Reading 5 for 4 yields a tautological 100%.
    if len(args) > 4 and args[4]:
        CALLEES_AT.update(args[4])
    if len(args) > 5 and args[5]:
        SUMMARIES.update(args[5])
    verdict = _ORIG_WALK(*args, **kwargs)
    CAPTURED.extend(sites)
    return verdict


def _wrap_is_call(edge):  # type: ignore[no-untyped-def]
    verdict = _ORIG_IS_CALL(edge)
    SEEN_EDGES.append((edge, verdict))
    return verdict


def _run(repo: Path, claims: Path) -> None:
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

    taint_mod._ddg_taint_reaches = _wrap_walk
    taint_mod._is_taint_call_edge = _wrap_is_call
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            cmd_verify_claims(_Args())
    finally:
        taint_mod._ddg_taint_reaches = _ORIG_WALK
        taint_mod._is_taint_call_edge = _ORIG_IS_CALL
    json.loads(buf.getvalue())


#: CFG statement node types that ARE a call site, per language, keyed by the
#: language slot of the symbol id.
#:
#: WHY A DECLARED MAP AND NOT A STRING COMPARISON (WI-nukut). This filter was
#: ``!= "call"`` -- the PYTHON spelling -- so for a Go repository it discarded
#: every site and the instrument printed "CALL-node escape sites: 0" while its
#: own positive control was reporting 78,600 edges and 236 escape events. A
#: zero under a live control is the shape a reader trusts, and read literally
#: it said "Go produces no call-node escape sites", which is a claim about a
#: LANGUAGE drawn from an instrument that never looked at it.
#:
#: WHY MIXED-LANGUAGE REPOS MAKE THIS WORSE RATHER THAN BETTER: kserve is 34
#: python sites and 14 go, pretix is 15 python and 5 javascript. The dropped
#: sites are invisible there, because the python ones still come through and
#: the arm looks like it worked.
#:
#: EVERY ENTRY IS GROUNDED IN OBSERVED OUTPUT, not in grammar documentation --
#: these are the call-shaped node types the CFG actually emitted across
#: pretix / kserve / caddy / alertmanager. ``go_statement`` and
#: ``deferred_call`` are included because the call HAPPENS at that line and the
#: taxonomy below is about whether an edge was emitted for it; they are the
#: only members that are wrappers rather than call expressions, so a reader
#: comparing this instrument to measure-escape-shapes.py (which reports them
#: separately) knows where the two differ.
#:
#: A LANGUAGE WITH NO ENTRY IS REFUSED, NOT SILENTLY EMPTIED. javascript is
#: deliberately absent: the corpus produced no call-shaped node for it, so
#: there is nothing to ground an entry on, and guessing a spelling here is
#: exactly the failure this map exists to prevent.
_CALL_NODE_TYPES: dict[str, frozenset[str]] = {
    "python": frozenset({"call"}),
    "go": frozenset({"call_expression", "deferred_call", "go_statement"}),
}


def _node_types(symbol_id: str, lines: set[int], root: Path) -> dict[int, str]:
    from tree_sitter_language_pack import get_parser

    parts = symbol_id.split(":")
    if len(parts) < 5:
        return {}
    candidate = Path(":".join(parts[1:-3]))
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        source = candidate.read_bytes()
        mapping = load_cfg_mapping(parts[0])
        tree = get_parser(parts[0]).parse(source)
        lo, hi = (int(x) for x in parts[-3].split("-"))
    except Exception:
        return {}
    out: dict[int, str] = {}

    def walk(node) -> None:  # type: ignore[no-untyped-def]
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
                        out[stmt.line] = stmt.node_type
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("claims")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args(argv[1:])

    repo = Path(args.repo).resolve()
    _run(repo, Path(args.claims))

    taint_typed = [e for e, ok in SEEN_EDGES if ok]
    print(f"repo: {repo}")
    print("--- positive control ---")
    print(f"edges offered to _is_taint_call_edge : {len(SEEN_EDGES)}")
    print(f"  ... accepted as taint call edges   : {len(taint_typed)}")
    print(f"§4 summary index entries             : {len(SUMMARIES)}")
    print(f"callees_at entries the walk was given: {len(CALLEES_AT)}")
    print(f"escape events captured               : {len(CAPTURED)}")
    if not CAPTURED or not SEEN_EDGES:
        print("\n!! Nothing to decompose. This is an instrument result.")
        return 1
    if not taint_typed or not SUMMARIES:
        print(
            "\n!! Stage 2 admitted nothing, or the summary index is empty."
            "\n   Every site would classify as the same cause by construction"
            "\n   — a tautology of the configuration, not a finding."
        )
        return 1

    # Raw index: (src, line) -> [(dst, taint_typed)], built from production's
    # own _edge_call_sites so one edge carrying several lines is expanded the
    # way the walk expands it.
    at_line: dict[tuple[str, int], list[tuple[str, bool]]] = (
        collections.defaultdict(list)
    )
    for edge, ok in SEEN_EDGES:
        for site in taint_mod._edge_call_sites(edge):
            at_line[(edge.get("src", ""), site)].append(
                (edge.get("dst", ""), ok)
            )
    keyed: dict[tuple[str, int], list[str]] = collections.defaultdict(list)
    for edge, ok in SEEN_EDGES:
        if not ok:
            continue
        key = _ORIG_KEY(edge)
        if key:
            for site in taint_mod._edge_call_sites(edge):
                keyed[(edge.get("src", ""), site)].append(key)

    unique = sorted({(s.symbol_id, s.line, s.reason) for s in CAPTURED})
    lines_by_fn: dict[str, set[int]] = collections.defaultdict(set)
    for sym, line, _ in unique:
        lines_by_fn[sym].add(line)

    cache: dict[str, dict[int, str]] = {}
    #: Sites this run could not classify because no call vocabulary is declared
    #: for their language. Counted rather than ignored so the refusal below can
    #: say HOW MUCH went unexamined, not merely that something did.
    undeclared: collections.Counter[str] = collections.Counter()
    verdicts: collections.Counter[str] = collections.Counter()
    detail: list[dict[str, object]] = []
    for sym, line, reason in unique:
        if sym not in cache:
            cache[sym] = _node_types(sym, lines_by_fn[sym], repo)
        node_type = cache[sym].get(line)
        if node_type is None:
            continue
        language = sym.split(":", 1)[0]
        known = _CALL_NODE_TYPES.get(language)
        if known is None:
            undeclared[language] += 1
            continue
        if node_type not in known:
            continue
        edges = at_line.get((sym, line), [])
        keys = keyed.get((sym, line), [])
        if not edges:
            verdict = "1-NO EDGE EMITTED (INV-mumov)"
        elif not any(ok for _, ok in edges):
            verdict = "2-EDGE NOT TAINT-TYPED"
        elif not keys:
            # STAGE 3 SPLITS, and only one half is a defect.
            # ``_catalogue_key_for_edge`` returns None for any RESOLVED edge —
            # the WI-zumud provenance gate, which refuses to look a first-party
            # callee up in a catalogue describing stdlib surfaces. That is
            # correct behaviour and must not be counted as a gap. What IS a gap
            # is an UNRESOLVED edge whose module slot holds the ``external``
            # placeholder: the analyzer emitted the call but could not infer
            # the receiver's type, so no key can be built (INV-linub).
            resolved_only = all(
                not str(d).startswith(f"{d.split(':')[0]}:external:")
                for d, ok in edges if ok
            )
            verdict = (
                "3b-RESOLVED, PROVENANCE-GATED (correct, WI-zumud)"
                if resolved_only
                else "3a-NO RECEIVER TYPE: 'external' placeholder (INV-linub)"
            )
        elif not any(k in SUMMARIES for k in keys):
            verdict = "4a-KEY PRESENT, NO SUMMARY (catalogue gap)"
        elif not any(
            k in SUMMARIES and taint_mod._summary_terminates(SUMMARIES[k])
            for k in keys
        ):
            verdict = "4b-SUMMARY SAYS PROPAGATES (a real escape)"
        else:
            verdict = "5-SHOULD HAVE CLOSED (investigate)"
        verdicts[verdict] += 1
        detail.append(
            {
                "symbol_id": sym, "line": line, "reason": reason,
                "verdict": verdict,
                "dsts": [d for d, _ in edges][:4],
                "keys": keys[:4],
            }
        )

    total = sum(verdicts.values())
    if undeclared:
        # REFUSE THE TABLE RATHER THAN PRINT AN UNDERCOUNT. The same contract
        # measure-escape-closing-ceiling.py holds for its own monkeypatch: a
        # number produced by an instrument that could not see part of its
        # population is worse than no number, because it reads as a finding.
        named = ", ".join(
            f"{lang} ({n} site{'s' if n != 1 else ''})"
            for lang, n in sorted(undeclared.items())
        )
        print(
            f"\nREFUSING THE TABLE: no call-node vocabulary is declared for "
            f"{named}. Those sites were dropped unclassified, so the counts "
            f"below would be an undercount of unknown size. Add the language "
            f"to _CALL_NODE_TYPES with its observed call node types and "
            f"re-run. (WI-nukut)",
            file=sys.stderr,
        )
        return 2
    print(f"\n=== CALL-node escape sites: {total} ===")
    print(f"{'cause':<48}{'n':>6}{'%':>8}")
    print("-" * 62)
    for v, n in sorted(verdicts.items()):
        print(f"{v:<48}{n:>6}{100 * n / max(total, 1):>7.1f}%")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(detail, indent=1))
        print(f"\ndetail: {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
