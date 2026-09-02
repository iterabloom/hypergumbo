#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Of the taint flows hypergumbo reports as violations, how many are real?

THE QUESTION HAS ONE PRIOR DATAPOINT AND IT IS WEAK. WI-ruhol closed
2026-03-25 at 50% precision (3 of 6) against a synthetic reference
implementation, before any of ADR-0017's nine phases shipped. It landed
exactly on its own decision boundary, on code written to be analysed, and it
cannot be compared to anything measured after. Re-running it would reproduce
its weakness rather than answer the question. This instrument answers it on
real repositories instead.

THE PRE-REGISTERED POPULATION NO LONGER EXISTS, WHICH IS WHY THIS FILE TARGETS
OTHER REPOS. WI-sivuz fixed its sample frame in advance as "the 1,890 flows on
hypergumbo's two violated self-claims", parked behind a trigger on the WI-vanun
thread. Both of those claims were subsequently repaired — `runtime-cli-no-host-fs`
by wrapping eight unsanitized writes, `runtime-cli-no-subprocess` by declaring
the repo_inspection zone — and a repaired claim reports zero violating flows,
because `verify_claims` never downgrades a `violated` verdict, only a would-be
`confirmed` one. So the frame is empty by construction, the park is moot, and
the population has to come from elsewhere. That revision is recorded on the
item; this docstring is the other half of the record.

WHAT COUNTS AS ONE RECORD, AND WHY THAT IS TWO QUESTIONS. Distinctness is
`verify_claims._flow_identity` — source primitives, source symbol, sink
primitives, sink symbol, call-graph path — which is production's own notion
and the instrument does not mint a second one. But since INV-karud's collapse
a record is a SITUATION ("S reads {P...} and reaches zone Z via {Q...}"),
standing for `collapsed_flow_count` source->sink PAIRS. Measurement 0004 found
the two units differ by 2.9x on one population, in a direction the item could
not predict in advance, so this instrument reports BOTH and labels every rate
with its unit. A situation is TRUE POSITIVE iff AT LEAST ONE of its pairs
satisfies the rubric; how many of them do is a separate fact the ledger has to
carry (`tp_pairs`), because it cannot be derived from the situation label.

WHY THE EVIDENCE CAP IS LIFTED HERE AND ONLY HERE. `_MAX_EVIDENCE_ROWS = 100`
head-truncates the deduplicated list for DISPLAY. Head-truncation is not
sampling: rows arrive in propagation order, which correlates with file order,
so the first hundred of a four-hundred-flow claim are a biased slice of it. An
instrument that sampled from that slice would be measuring the first few files
of each repo. This raises the cap for its own in-process run — production is
unchanged, and the effective limit is printed with every run so a reader can
tell a complete population from a truncated one.

POSITIVE CONTROL, PRINTED BEFORE ANY NUMBER. A run that reports zero flows is
indistinguishable from a run where the analysis never happened, and the second
is the failure this project keeps rediscovering. `collect` therefore refuses to
write an empty flow file without saying loudly that the repo produced no
violations at all, and prints the per-claim verdict map — including the
`inconclusive` verdicts, which are the ones that say the analysis was blind
rather than clean.

WHAT THIS MEASURES AND WHAT IT CANNOT. Precision only: of what was reported,
how much is real. It says NOTHING about recall — a repo with one reported flow
and forty real ones scores 100% here. Recall needs a labelled corpus this
project does not have, and quoting a precision number as though it were an
accuracy number is the specific misreading this paragraph exists to block.

THE SAMPLE IS STRATIFIED, NOT UNIFORM. Two axes partition behaviour and are
known in advance to do so: the CLAIM (a `db_read -> db_write` round trip is a
different animal from `env_read -> net_send`) and the ANALYSIS METHOD
(`precise`/ddg versus `approximate`/structural — INV-sadah's axis, where a
mislabel is a different defect from a precision miss). A uniform sample of a
population dominated by one claim would mostly measure that claim.

Usage:
    scripts/measure-taint-precision.py collect --repo PATH [--claims FILE]
                                               --out DIR [--label NAME]
    scripts/measure-taint-precision.py sample  --flows DIR --n 60 --seed 20260811
                                               --out DIR
    scripts/measure-taint-precision.py score   --flows DIR --labels LEDGER.json
                                               [--compare SECOND_PASS.json]

The label ledger consumed by ``score`` is written by hand — adjudication is
reading source code, and no part of it is automated here. Its shape is
``{"labels": {flow_id: {"label": "TP"|"FP"|"UNADJ", "mechanism": str,
"signal": str, "tp_pairs": int, "unadj_pairs": int}}}``, with an optional
``block`` for a large uniform verdict. ``tp_pairs`` is REQUIRED on a ``TP``
whose record collapses more than one pair and is meaningless on the others;
``score`` refuses to print a row rate rather than guess one. ``unadj_pairs``
is optional and exists so a situation-unit run reconciles with a pair-unit
run of the same population instead of folding those rows into FP.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import random
import re
import sys
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _pkg in sorted((_REPO_ROOT / "packages").glob("*/src")):
    if str(_pkg) not in sys.path:
        sys.path.insert(0, str(_pkg))

#: Effective evidence ceiling for this instrument's in-process runs. Large
#: enough that no real claim reaches it, and PRINTED so that a run which does
#: reach it is visible as truncated rather than silently complete.
EVIDENCE_LIMIT = 1_000_000

#: Default claims file: the six generic claims, not hypergumbo's self-claims.
DEFAULT_CLAIMS = _REPO_ROOT / "docs" / "example-claims" / "generic-taint-claims.yaml"

_SPAN_RE = re.compile(r"^\d+-\d+$")


# --------------------------------------------------------------------------
# Symbol-id parsing. The grammar is {lang}:{path}:{span}:{name}:{kind} with a
# colon-TOLERANT path slot (ADR-0036 D1a), so the path is located by finding
# the span token rather than by counting from the left.
# --------------------------------------------------------------------------
def parse_symbol_id(symbol_id: str) -> dict[str, Any]:
    """Split a symbol id into its slots, reusing production's path parse.

    Returns ``path``/``start``/``end``/``name``/``kind``; ``start`` and ``end``
    are ``None`` when the id carries no ``\\d+-\\d+`` span (a bare line number,
    or a synthetic id). A caller must treat a missing span as "cannot excerpt",
    never as line 0 — an excerpt at the top of a file reads as evidence and is
    not.
    """
    from hypergumbo_core.ir import symbol_path_slot

    parts = symbol_id.split(":") if symbol_id else []
    out: dict[str, Any] = {
        "path": symbol_path_slot(symbol_id),
        "start": None,
        "end": None,
        "name": parts[-2] if len(parts) >= 5 else "",
        "kind": parts[-1] if len(parts) >= 5 else "",
    }
    for token in parts:
        if _SPAN_RE.match(token):
            start_s, end_s = token.split("-")
            out["start"], out["end"] = int(start_s), int(end_s)
            break
    return out


def _is_external(parsed: dict[str, Any]) -> bool:
    """A symbol that names no file in the repo cannot be read against source."""
    path = parsed.get("path") or ""
    return (
        not path
        or path.startswith("<")
        or path == "external"
        or parsed.get("kind") in {"external_symbol", "unresolved"}
    )


# --------------------------------------------------------------------------
# collect
# --------------------------------------------------------------------------
def run_verify_claims(
    repo: Path, claims: Path, no_collapse: bool = False,
) -> dict[str, Any]:
    """Drive production's own `verify-claims` in-process, cap lifted.

    In-process rather than by subprocess because the cap lives in a module
    global; going through the shell would mean either patching production or
    reading a truncated list. The parser is production's own `build_parser`,
    so a flag this instrument forgets to pass gets production's default rather
    than a second answer invented here.
    """
    from hypergumbo_core import taint as taint_mod
    from hypergumbo_core import verify_claims as vc_mod
    from hypergumbo_core.cli import build_parser, cmd_verify_claims

    vc_mod._MAX_EVIDENCE_ROWS = EVIDENCE_LIMIT
    if no_collapse:
        # THE ROW UNIT, MEASURED RATHER THAN APPORTIONED. INV-karud's collapse
        # groups pair findings into situations and reports only how many it
        # swallowed (`collapsed_flow_count`), which is enough to count rows
        # but not to ADJUDICATE them — a reader cannot tell which primitive
        # pair each swallowed row named. 0004 had to hand-block that; here the
        # same run is repeated with the collapse turned off, so both units are
        # adjudicated on their own records. Production is untouched: this
        # rebinds a module global inside this process only, and `collect`
        # prints which arm it ran.
        taint_mod.collapse_unadjudicated_flows = (  # type: ignore[assignment]
            lambda findings: list(findings)
        )

    parser = build_parser()
    args = parser.parse_args(
        ["verify-claims", str(repo), "--claims", str(claims), "--json"]
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cmd_verify_claims(args)
    payload = json.loads(buf.getvalue())
    payload["_exit_code"] = rc
    return payload


def flows_from_payload(
    payload: dict[str, Any], repo_label: str,
) -> list[dict[str, Any]]:
    """Flatten every violated verdict's evidence into per-flow rows."""
    rows: list[dict[str, Any]] = []
    for verdict in payload.get("verdicts", []):
        if verdict.get("verdict") != "violated":
            continue
        claim_id = verdict.get("claim_id", "")
        for idx, ev in enumerate(verdict.get("evidence", [])):
            src = parse_symbol_id(ev.get("source_symbol", ""))
            snk = parse_symbol_id(ev.get("sink_symbol", ""))
            rows.append(
                {
                    "flow_id": f"{repo_label}:{claim_id}:{idx}",
                    "repo": repo_label,
                    "claim_id": claim_id,
                    "analysis_method": ev.get("analysis_method") or "structural",
                    "confidence": ev.get("confidence") or "approximate",
                    # THE TOOL'S OWN DISCLOSURE ABOUT ITS OWN ROUTE, carried
                    # forward rather than dropped. `verify-claims` stamps
                    # `walk_verdict: "unavailable"` on every finding from the
                    # STRUCTURAL propagator -- deliberately, because blank
                    # would conflate "no walk was possible" with "this record
                    # predates the field". This collector rebuilt each row
                    # from an explicit key list and both fields were missing
                    # from it, so every adjudication packet ever built here
                    # printed an N-hop route and silently withheld the
                    # statement that NO DATAFLOW WALK RAN and the route is
                    # call-graph reachability. Measurement 0006's own
                    # sample-112.json has neither key, so its two independent
                    # 16-agent panels judged without it too.
                    #
                    # NOT defaulted. An absent field stays None: a map written
                    # before the field existed made no claim about the walk,
                    # and inventing "unavailable" for it would be the same
                    # absence-means-two-things error the propagator's comment
                    # exists to prevent. `walk_blocked_by` is the field
                    # measurement 0007 partitioned blocked walks with, so
                    # dropping it made that partition unrecoverable from a
                    # flow file.
                    "walk_verdict": ev.get("walk_verdict"),
                    "walk_blocked_by": ev.get("walk_blocked_by"),
                    "source_symbol": ev.get("source_symbol", ""),
                    "source_primitive": ev.get("source_primitive", ""),
                    "source_module": ev.get("source_module", ""),
                    "source_boundary": ev.get("source_boundary", ""),
                    "sink_symbol": ev.get("sink_symbol", ""),
                    "sink_primitive": ev.get("sink_primitive", ""),
                    "sink_module": ev.get("sink_module", ""),
                    "path": ev.get("path", []),
                    # `path` holds the REPO-side call route only: it starts at
                    # the function containing the source read and ends at the
                    # function containing the sink call. The external sink
                    # symbol is NOT a path element, so the number of
                    # intermediate hops is len(path) - 1, not - 2. Scoring
                    # recomputes this from `path` regardless, so a flow file
                    # written before this was corrected still scores right.
                    "hops": max(0, len(ev.get("path", [])) - 1),
                    "source_file": src["path"],
                    "source_lines": [src["start"], src["end"]],
                    "source_name": src["name"],
                    "sink_file": snk["path"],
                    "sink_lines": [snk["start"], snk["end"]],
                    "sink_name": snk["name"],
                    "source_is_external": _is_external(src),
                    "sink_is_external": _is_external(snk),
                    # INV-karud's collapse fields. An evidence record is a
                    # SITUATION -- "S reads {P...} and reaches zone Z via
                    # {Q...}" -- and `collapsed_flow_count` is how many
                    # source->sink PAIRS it stands for. The scalars above are
                    # the witness pair the `path` belongs to, not the whole
                    # claim, so a scorer that reads only them silently
                    # measures one unit while calling it the other. Default 1
                    # so a flow file written before the collapse (0001, 0003)
                    # still scores: there, one record WAS one pair.
                    "source_primitives": list(ev.get("source_primitives", [])),
                    "sink_primitives": list(ev.get("sink_primitives", [])),
                    "sink_symbols": list(ev.get("sink_symbols", [])),
                    "collapsed_flow_count": int(
                        ev.get("collapsed_flow_count", 1) or 1
                    ),
                }
            )
    return rows


def cmd_collect(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    label = args.label or repo.name
    claims = Path(args.claims).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[collect] repo={label} path={repo}")
    print(f"[collect] claims={claims}")
    print(f"[collect] evidence limit raised to {EVIDENCE_LIMIT:,} for this run")

    print(f"[collect] collapse={'OFF (pair unit)' if args.no_collapse else 'ON (situation unit)'}")
    payload = run_verify_claims(repo, claims, no_collapse=args.no_collapse)

    verdict_map = {
        v.get("claim_id"): v.get("verdict") for v in payload.get("verdicts", [])
    }
    print(f"[collect] exit code: {payload['_exit_code']}")
    print("[collect] per-claim verdicts (POSITIVE CONTROL — read these first):")
    for cid, verdict in sorted(verdict_map.items()):
        count = next(
            (
                v.get("evidence_count", 0)
                for v in payload["verdicts"]
                if v.get("claim_id") == cid
            ),
            0,
        )
        print(f"           {verdict:<13} {cid}  (evidence_count={count})")

    blind = payload.get("unsupported_taint_languages", [])
    if blind:
        print(f"[collect] languages with NO taint catalogue: {', '.join(blind)}")

    rows = flows_from_payload(payload, label)
    if not rows:
        print(
            "[collect] *** ZERO violating flows. This is NOT evidence the repo "
            "is clean — check the verdict map above: an 'inconclusive' verdict "
            "means the analysis was blind, and a 'confirmed' one means it "
            "looked and found nothing. ***"
        )
    else:
        by_claim = collections.Counter(r["claim_id"] for r in rows)
        by_method = collections.Counter(r["analysis_method"] for r in rows)
        print(f"[collect] {len(rows)} distinct violating flow(s)")
        for cid, n in by_claim.most_common():
            print(f"           {n:>6}  {cid}")
        for method, n in by_method.most_common():
            print(f"           {n:>6}  analysis_method={method}")
        if len(rows) >= EVIDENCE_LIMIT:
            print(
                "[collect] *** POPULATION TRUNCATED at the evidence limit — "
                "the sample frame is incomplete. ***"
            )

    flows_path = out_dir / f"flows-{label}.jsonl"
    with flows_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    meta_path = out_dir / f"meta-{label}.json"
    meta_path.write_text(
        json.dumps(
            {
                "repo": label,
                "repo_path": str(repo),
                "claims": str(claims),
                "exit_code": payload["_exit_code"],
                "verdicts": verdict_map,
                "unsupported_taint_languages": blind,
                "dataflow_coverage": payload.get("dataflow_coverage", {}),
                "flow_count": len(rows),
                "evidence_limit": EVIDENCE_LIMIT,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"[collect] wrote {flows_path}")
    print(f"[collect] wrote {meta_path}")
    return 0


# --------------------------------------------------------------------------
# sample
# --------------------------------------------------------------------------
def load_flows(flows_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(flows_dir.glob("flows-*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def stratify(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], list[dict]]:
    """Partition on (claim_id, analysis_method) — the two pre-registered axes."""
    cells: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for row in rows:
        cells[(row["claim_id"], row["analysis_method"])].append(row)
    return cells


def allocate(
    cells: dict[tuple[str, str], list[dict]], target: int, minimum: int,
) -> dict[tuple[str, str], int]:
    """Proportional allocation with a per-cell floor.

    The floor is what makes a rare-but-different stratum adjudicable at all; a
    strictly proportional allocation would give a 12-flow cell zero slots and
    then report a precision number that had never looked at it. A cell smaller
    than the floor contributes all of its flows and no more.
    """
    total = sum(len(v) for v in cells.values())
    if total == 0:
        return {}
    alloc: dict[tuple[str, str], int] = {}
    for key, members in cells.items():
        want = max(minimum, round(target * len(members) / total))
        alloc[key] = min(want, len(members))
    return alloc


def cmd_sample(args: argparse.Namespace) -> int:
    flows_dir = Path(args.flows).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_flows(flows_dir)
    if not rows:
        print(f"[sample] no flows found under {flows_dir}", file=sys.stderr)
        return 1

    cells = stratify(rows)
    alloc = allocate(cells, args.n, args.minimum)

    print(f"[sample] population = {len(rows)} distinct flows "
          f"in {len(cells)} strata")
    print(f"[sample] seed = {args.seed}   target n = {args.n}   "
          f"per-cell floor = {args.minimum}")
    print(f"{'claim_id':<32} {'method':<12} {'pop':>6} {'draw':>6}")
    for key in sorted(cells):
        claim_id, method = key
        print(f"{claim_id:<32} {method:<12} "
              f"{len(cells[key]):>6} {alloc.get(key, 0):>6}")

    # Seeded, reproducible draw. Not cryptographic and must not be: a
    # measurement someone else cannot reproduce from the seed is not evidence.
    rng = random.Random(args.seed)  # noqa: S311  # nosec B311
    sample: list[dict[str, Any]] = []
    for key in sorted(cells):
        members = sorted(cells[key], key=lambda r: r["flow_id"])
        sample.extend(rng.sample(members, alloc.get(key, 0)))

    print(f"[sample] drew {len(sample)} flows")

    sample_path = out_dir / "sample.jsonl"
    with sample_path.open("w", encoding="utf-8") as fh:
        for row in sorted(sample, key=lambda r: r["flow_id"]):
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"[sample] wrote {sample_path}")
    return 0


# --------------------------------------------------------------------------
# score
# --------------------------------------------------------------------------
#: Canonical label vocabulary. UNADJ is a THIRD label, never a coin-flip into
#: one of the first two, and it is never folded into the precision ratio.
_LABELS = ("TP", "FP", "UNADJ")


def load_labels(paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """Merge one or more label ledgers; a later file outranks an earlier one.

    A ledger is ``{"labels": {flow_id: {"label": ..., "mechanism": ...,
    "signal": ...}}}``. A bare string value is accepted as shorthand for
    ``{"label": <string>}`` so a second-pass ledger that carries only verdicts
    does not have to invent mechanisms it was never asked for.
    """
    merged: dict[str, dict[str, Any]] = {}
    for path in paths:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        for flow_id, value in raw.get("labels", {}).items():
            if isinstance(value, str):
                value = {"label": value}
            merged[flow_id] = value
        # A block form lets a large uniform verdict (e.g. every unanchored
        # flow) be recorded once with one rationale instead of copy-pasted.
        block = raw.get("block")
        if block:
            for flow_id in block.get("flow_ids", []):
                merged[flow_id] = {
                    "label": block["label"],
                    "mechanism": block.get("mechanism", ""),
                    "signal": block.get("signal", "n/a"),
                }
    return merged


def _rate(rows: list[dict], labels: dict[str, dict]) -> tuple[int, int, int, str]:
    tp = sum(1 for r in rows if labels.get(r["flow_id"], {}).get("label") == "TP")
    fp = sum(1 for r in rows if labels.get(r["flow_id"], {}).get("label") == "FP")
    un = sum(1 for r in rows if labels.get(r["flow_id"], {}).get("label") == "UNADJ")
    denom = tp + fp
    return tp, fp, un, (f"{100 * tp / denom:.1f}%" if denom else "n/a")


# --------------------------------------------------------------------------
# THE SECOND UNIT. `_rate` above counts RECORDS, and post-INV-karud a record
# is a SITUATION, not a source->sink pair. Measurement 0004 established that
# the two units differ by 2.9x on one population and that WHICH WAY they
# differ is not predictable, so reporting either alone is reporting a number
# whose denominator the reader will guess wrong. Both are printed, always,
# and neither is called "precision" without its unit.
# --------------------------------------------------------------------------
def _pairs(row: dict[str, Any]) -> int:
    """Pre-collapse pair count for one record. 1 for pre-collapse files."""
    return max(1, int(row.get("collapsed_flow_count", 1) or 1))


def _tp_pairs(row: dict[str, Any], entry: dict[str, Any]) -> int | None:
    """How many of a situation's pairs are real — NEVER guessed.

    A situation is TP iff AT LEAST ONE of its pairs satisfies the rubric
    (0004's existential reading), so a TP situation label does not say how
    many pairs are real: caddy's `cmdRun` collapses 76 pairs into 3 records.
    Assuming "all of them" inflates the row rate and assuming "one" deflates
    it, so a multi-pair TP without an explicit ``tp_pairs`` returns ``None``
    and the scorer refuses to report a row rate at all. FP needs no annotation
    — if no pair is real, none is — and a single-pair record is unambiguous.
    """
    label = entry.get("label")
    if label == "FP":
        return 0
    if label != "TP":
        return None
    if "tp_pairs" in entry:
        return max(0, min(_pairs(row), int(entry["tp_pairs"])))
    return 1 if _pairs(row) == 1 else None


def _unresolved_pair_counts(
    rows: list[dict], labels: dict[str, dict],
) -> list[str]:
    """TP situations holding >1 pair with no ``tp_pairs`` recorded."""
    out = []
    for row in rows:
        entry = labels.get(row["flow_id"])
        if entry is None:
            continue
        if entry.get("label") == "TP" and _tp_pairs(row, entry) is None:
            out.append(f'{row["flow_id"]} ({_pairs(row)} pairs)')
    return sorted(out)


def _row_rate(
    rows: list[dict], labels: dict[str, dict],
) -> tuple[int, int, int, str]:
    """Precision at the PAIR unit — 0001's and 0004's row denominator."""
    tp = fp = un = 0
    for row in rows:
        entry = labels.get(row["flow_id"])
        if entry is None:
            continue
        label = entry.get("label")
        if label == "UNADJ":
            un += _pairs(row)
            continue
        real = _tp_pairs(row, entry)
        if real is None:
            continue
        # A situation can hold pairs that are individually UNADJUDICABLE even
        # when the situation itself is decided (caddy's config-field sinks
        # sit behind a json.Unmarshal-into-a-registered-module hop). Without
        # somewhere to record them the situation arm would fold them into FP
        # and stop reconciling with a pair-unit run of the same population.
        unadj = max(0, min(_pairs(row) - real, int(entry.get("unadj_pairs", 0))))
        un += unadj
        tp += real
        fp += _pairs(row) - real - unadj
    denom = tp + fp
    return tp, fp, un, (f"{100 * tp / denom:.1f}%" if denom else "n/a")


def _is_unanchored(row: dict[str, Any]) -> bool:
    """The source names no readable place — nothing to open, nothing to judge."""
    return row.get("source_file", "") in ("", "external", "<external>")


def _table(title: str, groups: list[tuple[str, list[dict]]],
           labels: dict[str, dict]) -> None:
    print(f"\n{title}")
    print(f"  {'group':<34} {'sit':>4} {'TP':>4} {'UN':>3} {'prec/sit':>9}"
          f"   {'rows':>5} {'TP':>4} {'prec/row':>9}")
    for name, rows in groups:
        tp, fp, un, pct = _rate(rows, labels)
        r_tp, r_fp, _, r_pct = _row_rate(rows, labels)
        print(f"  {name:<34} {len(rows):>4} {tp:>4} {un:>3} {pct:>9}"
              f"   {sum(_pairs(r) for r in rows):>5} {r_tp:>4} {r_pct:>9}")


def cmd_score(args: argparse.Namespace) -> int:
    rows = load_flows(Path(args.flows).resolve())
    labels = load_labels([Path(p) for p in args.labels])
    if not rows:
        print(f"[score] no flows under {args.flows}", file=sys.stderr)
        return 1

    scored = [r for r in rows if r["flow_id"] in labels]
    unlabelled = [r for r in rows if r["flow_id"] not in labels]
    print(f"[score] population {len(rows)} situations "
          f"({sum(_pairs(r) for r in rows)} pre-collapse rows); "
          f"{len(scored)} labelled, {len(unlabelled)} UNLABELLED")
    if unlabelled:
        # Silence here would let a partial adjudication read as a complete one.
        print("[score] *** UNLABELLED FLOWS ARE NOT COUNTED ANYWHERE BELOW. "
              "The denominator is the labelled set, not the population. ***")

    bad = sorted({
        labels[r["flow_id"]].get("label", "?") for r in scored
    } - set(_LABELS))
    if bad:
        print(f"[score] unknown label(s) {bad}", file=sys.stderr)
        return 1

    # REFUSE BEFORE REPORTING. A multi-pair situation labelled TP does not
    # say how many of its pairs are real, and the row rate is meaningless
    # without that. This is the same refusal `collect` makes about a zero it
    # cannot distinguish from "the analysis never ran".
    unresolved = _unresolved_pair_counts(scored, labels)
    if unresolved:
        print("[score] *** REFUSING TO SCORE: these TP situations hold more "
              "than one pair and carry no `tp_pairs`. A row rate cannot be "
              "computed from a situation label alone (0004). ***",
              file=sys.stderr)
        for item in unresolved:
            print(f"           {item}", file=sys.stderr)
        return 1

    tp, fp, un, pct = _rate(scored, labels)
    r_tp, r_fp, r_un, r_pct = _row_rate(scored, labels)
    print(f"\n[score] POOLED, per SITUATION: TP={tp} FP={fp} precision={pct} "
          f"(UNADJUDICABLE={un}, reported beside, never folded in)")
    print(f"[score] POOLED, per ROW:       TP={r_tp} FP={r_fp} "
          f"precision={r_pct} (UNADJUDICABLE={r_un})")
    print("[score] the two units are NOT comparable to each other; 0004 "
          "measured 2.9x between them on one population.")

    # WHEN THE LABELLED SET IS A SAMPLE, THE UNWEIGHTED RATE IS THE WRONG
    # NUMBER. Allocation is deliberately disproportionate — a per-cell floor
    # over-samples small strata so a rare-but-different one is adjudicated at
    # all — so pooling the raw counts would let those small strata speak for a
    # share of the population they do not hold. The population-weighted
    # estimate re-weights each stratum's observed rate by its share of the
    # FULL flow population. Strata with no labelled flow contribute no rate,
    # and the share of the population they carry is printed rather than
    # silently redistributed.
    if unlabelled:
        pop_cells: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
        for r in rows:
            pop_cells[(r["claim_id"], r["analysis_method"])].append(r)
        weighted, covered_pop, uncovered_pop = 0.0, 0, 0
        for members in pop_cells.values():
            sub = [r for r in members if r["flow_id"] in labels]
            c_tp, c_fp, _, _ = _rate(sub, labels)
            if c_tp + c_fp == 0:
                uncovered_pop += len(members)
                continue
            covered_pop += len(members)
            weighted += len(members) * (c_tp / (c_tp + c_fp))
        est = f"{100 * weighted / covered_pop:.1f}%" if covered_pop else "n/a"
        print(f"[score] POPULATION-WEIGHTED estimate (per SITUATION): {est} "
              f"over {covered_pop} of {len(rows)} situations "
              f"({uncovered_pop} in strata with no adjudicable label)")

    def group(key) -> list[tuple[str, list[dict]]]:
        buckets: dict[str, list[dict]] = collections.defaultdict(list)
        for r in scored:
            buckets[str(key(r))].append(r)
        return sorted(buckets.items())

    _table("BY analysis_method (the pre-registered stratification axis)",
           group(lambda r: r["analysis_method"]), labels)
    _table("BY source anchor",
           [("unanchored <external>", [r for r in scored if _is_unanchored(r)]),
            ("anchored to a repo file",
             [r for r in scored if not _is_unanchored(r)])], labels)
    _table("BY claim", group(lambda r: r["claim_id"]), labels)
    _table("BY repo", group(lambda r: r["repo"]), labels)
    _table("BY language",
           group(lambda r: r["source_symbol"].split(":")[0]), labels)

    mechs = collections.Counter(
        labels[r["flow_id"]].get("mechanism", "unstated")
        for r in scored if labels[r["flow_id"]]["label"] == "FP"
    )
    if mechs:
        print("\nFALSE-POSITIVE MECHANISMS")
        for name, count in mechs.most_common():
            print(f"  {count:>5}  {name}")

    # A verdict is a DISJUNCTION over its flows, so a claim is correctly
    # violated when ANY flow is real. Reporting only the flow rate would
    # understate what a user actually sees first.
    by_claim: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for r in scored:
        by_claim[(r["repo"], r["claim_id"])].append(labels[r["flow_id"]]["label"])
    supported = sum(1 for v in by_claim.values() if "TP" in v)
    print(f"\nCLAIM-LEVEL: {supported}/{len(by_claim)} violated verdicts rest on "
          f"at least one true flow")
    for key, values in sorted(by_claim.items()):
        mark = "OK " if "TP" in values else "BAD"
        print(f"  {mark} {key[0]:<16} {key[1]:<32} "
              f"flows={len(values):>4} tp={values.count('TP')}")

    # The counterfactual anyone reading the method table will propose. Print
    # what it would COST, not only what it would buy.
    precise = [r for r in scored if r["analysis_method"] == "ddg"]
    lost = [r["flow_id"] for r in scored
            if r["analysis_method"] != "ddg"
            and labels[r["flow_id"]]["label"] == "TP"]
    p_tp, p_fp, _, p_pct = _rate(precise, labels)
    print("\nCOUNTERFACTUAL — report only `ddg` (precise) flows:")
    print(f"  kept {len(precise)} flow(s), precision {p_pct} "
          f"(TP={p_tp} FP={p_fp})")
    print(f"  TRUE POSITIVES LOST: {len(lost)} of {tp}")
    for flow_id in lost:
        print(f"     - {flow_id}")

    if args.compare:
        other = load_labels([Path(p) for p in args.compare])
        shared = [f for f in labels if f in other]
        disagree = [f for f in shared if labels[f]["label"] != other[f]["label"]]
        rate = f"{100 * len(disagree) / len(shared):.1f}%" if shared else "n/a"
        print(f"\nINDEPENDENT-PASS DISAGREEMENT: {len(disagree)}/{len(shared)} "
              f"= {rate}")
        for flow_id in disagree:
            print(f"  {flow_id}: {labels[flow_id]['label']} vs "
                  f"{other[flow_id]['label']}")
        print("  NOTE: a low disagreement rate shows the RUBRIC is applied "
              "consistently. It is not evidence the rubric is right — both "
              "passes share it.")
    return 0


# --------------------------------------------------------------------------
# packet — the adjudication packet, and the four defects 0006 filed against it
#
# Measurement 0006 §F names ~12 reports against "the measurement's own packet
# builder, NOT hypergumbo", and ends "Fix before reuse." That builder was a
# session artifact and was never committed, so its defects could only be
# re-encountered. This is it, in-repo, with each defect closed and pinned by
# ``tests/test_adjudication_packet.py``:
#
#   1. substring matching (`inFORMATion` matched `format`; `-> ` inside a
#      quoted pattern matched the bash redirect `>`)
#   2. matching inside comments and string literals
#   3. missing literal call sites — only the situation's HEADLINE sink name was
#      searched, though a collapsed situation stands for several primitives
#   4. searching only the SOURCE symbol's span, which makes the sink listing
#      structurally empty for every multi-hop situation
#
# Defect 4 is the load-bearing one: ArkLib#3's path is main (201-255) then
# generate_dot_graph (110-137), `open` is called in the second, and the packet
# printed "(none found in span)". That is manufactured evidence of absence, and
# an adjudicator has no way to tell it from the real thing.
# --------------------------------------------------------------------------

#: Line-comment openers by language. Block comments are deliberately NOT
#: handled: doing it correctly needs a lexer, and doing it by regex across 16
#: languages would trade a known limitation for an unknown one. A block comment
#: therefore still yields candidate lines, which errs toward showing the
#: adjudicator too much rather than too little.
_COMMENT_OPENERS: dict[str, tuple[str, ...]] = {
    "bash": ("#",), "python": ("#",), "ruby": ("#",), "elixir": ("#",),
    "yaml": ("#",), "perl": ("#",), "r": ("#",),
    "go": ("//",), "javascript": ("//",), "typescript": ("//",),
    "java": ("//",), "rust": ("//",), "c": ("//",), "cpp": ("//",),
    "kotlin": ("//",), "swift": ("//",), "scala": ("//",), "objc": ("//",),
    "haskell": ("--",), "sql": ("--",), "lua": ("--",),
}

_QUOTES = ("'", '"', "`")


def scrub(line: str, language: str) -> str:
    """Blank string literals and line comments, PRESERVING LENGTH.

    Length is preserved so a caller can still report a column. Single-line and
    quote-naive by design: it tracks one opening quote at a time and does not
    follow a string across lines, so a multi-line string's interior is still
    searchable. Stated rather than hidden — the honest limitation is narrower
    than the defect it replaces.
    """
    openers = _COMMENT_OPENERS.get(language, ())
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote is not None:
            out.append(" ")
            if ch == "\\" and i + 1 < len(line):
                out.append(" ")
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in _QUOTES:
            quote = ch
            out.append(" ")
            i += 1
            continue
        if any(line.startswith(o, i) for o in openers):
            out.append(" " * (len(line) - i))
            return "".join(out)
        out.append(ch)
        i += 1
    return "".join(out)


def _site_pattern(name: str) -> "re.Pattern[str]":
    """Identifier-boundary for a name, literal for an operator.

    ``format`` must not match inside ``information``; ``>`` has no identifier
    boundary to require and is matched literally (after scrubbing, so a ``>``
    inside a quoted pattern is already gone).
    """
    bare = name.rsplit(".", 1)[-1]
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", bare):
        return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(bare) + r"(?![A-Za-z0-9_])")
    return re.compile(re.escape(bare))


def find_sites(
    lines: "list[str]", names: "list[str]", start: int, end: int, language: str,
) -> "list[tuple[int, str]]":
    """Lines in ``[start, end]`` where any of ``names`` occurs as a real use.

    Returns the ORIGINAL line text, not the scrubbed one — the adjudicator
    reads source, and a blanked string would misrepresent it. Scrubbing decides
    only WHETHER the line is a site.
    """
    patterns = [_site_pattern(n) for n in names if n]
    out: list[tuple[int, str]] = []
    for lineno in range(max(1, start), min(len(lines), end) + 1):
        raw = lines[lineno - 1]
        cooked = scrub(raw, language)
        if any(p.search(cooked) for p in patterns):
            out.append((lineno, raw.rstrip("\n")))
    return out


def sink_names(flow: dict[str, Any]) -> "list[str]":
    """Every primitive the situation stands for, not just its headline.

    ADR-0017's collapse means one situation can carry several sink primitives
    (ArkLib#3: ``builtins.open``, ``file.write``, ``json.dump``). Searching
    only ``sink_name`` is defect 3 and reported "nothing found" for a sink
    plainly present two lines away.
    """
    names = {
        p.rsplit(".", 1)[-1]
        for p in (flow.get("sink_primitives") or [])
        if p
    }
    if flow.get("sink_name"):
        names.add(str(flow["sink_name"]).rsplit(".", 1)[-1])
    return sorted(names)


def sink_search_spans(
    flow: dict[str, Any],
) -> "list[tuple[str, int, int, str]]":
    """``(path, start, end, name)`` for every FIRST-PARTY symbol on the path.

    Defect 4. The sink of a multi-hop flow is called in a callee, not in the
    source function, so searching the source span alone answers a question
    nobody asked. External symbols name no file and are skipped; a symbol with
    no span is skipped rather than read as line 0, because an excerpt at the
    top of a file reads as evidence and is not.
    """
    symbols = list(flow.get("path") or []) or [flow.get("source_symbol") or ""]
    seen: set[tuple[str, int, int, str]] = set()
    spans: list[tuple[str, int, int, str]] = []
    for symbol in symbols:
        if not symbol:
            continue
        parsed = parse_symbol_id(symbol)
        if _is_external(parsed):
            continue
        if parsed.get("kind") != "file" and (
            parsed["start"] is None or parsed["end"] is None
        ):
            continue
        if parsed.get("kind") == "file":
            # Widened by the caller, which holds the line count; the sentinel
            # keeps the row addressable here.
            parsed["start"], parsed["end"] = 1, -1
        row = (parsed["path"], parsed["start"], parsed["end"], parsed["name"])
        if row not in seen:
            seen.add(row)
            spans.append(row)
    return spans


#: Shell parameters that are not environment reads: positionals and specials.
_SHELL_NON_ENV = frozenset("0123456789@*#?$!-_")

_SHELL_EXPANSION = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
_SHELL_ASSIGN = re.compile(
    r"^\s*(?:export\s+|declare\s+(?:-\w+\s+)?|local\s+|readonly\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*=",
)

#: The other ways a shell binds a LOCAL name. ``NAME=`` alone is not enough:
#: ArkLib's lintWhitespace.sh binds ``file`` with ``read -r file`` and ``$file``
#: was then listed as an environment read on four lines. Found by reading the
#: rendered packet back against source.
_SHELL_BINDERS = (
    re.compile(r"\bread\b(?:\s+-\w+)*\s+((?:[A-Za-z_][A-Za-z0-9_]*\s*)+)"),
    re.compile(r"\bfor\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\b"),
    re.compile(r"\bselect\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\b"),
)


def shell_env_sites(lines: "list[str]") -> "list[tuple[int, str]]":
    """Lines where an AMBIENT shell parameter is expanded.

    A bash ``environ`` source names no literal token, so name matching finds
    nothing and the packet shows an empty listing for a source that is really
    there. The evidence is the EXPANSION. 0006 §F names two things the old
    builder got wrong about that — it matched "any ``$VAR`` including shell
    COMMENTS and LOCALS" — and both are closed here:

      * comments are scrubbed (``scrub`` with the bash openers);
      * a name ASSIGNED anywhere in the file is a local, not an environment
        read, so ``tmpfile=$(mktemp)`` disqualifies every ``$tmpfile``.

    STRINGS ARE DELIBERATELY NOT EXCLUDED, unlike everywhere else in this
    module: the shell expands inside double quotes, so ``"$HOME/x"`` is a real
    read and scrubbing it would reintroduce the false-absence defect.

    HONEST LIMIT: file-scoped assignment detection has no notion of order or
    scope, so a name both inherited AND reassigned reads as local and is
    dropped. That errs toward showing the adjudicator less, which is the safe
    direction for a listing whose job is to support a refutation.
    """
    assigned: set[str] = set()
    for line in lines:
        cooked = scrub(line, "bash")
        if m := _SHELL_ASSIGN.match(cooked):
            assigned.add(m.group(1))
        for binder in _SHELL_BINDERS:
            for hit in binder.finditer(cooked):
                assigned.update(hit.group(1).split())
    out: list[tuple[int, str]] = []
    for lineno, raw in enumerate(lines, start=1):
        # Comment-strip only: keep string interiors, per the note above.
        body = raw.split("#", 1)[0] if raw.lstrip().startswith("#") else raw
        names = {
            n for n in _SHELL_EXPANSION.findall(body)
            if n not in assigned and n not in _SHELL_NON_ENV
        }
        if names:
            out.append((lineno, raw.rstrip("\n")))
    return out


def effective_span(
    parsed: dict[str, Any], line_count: int,
) -> "tuple[int, int] | None":
    """The lines a symbol actually covers, honouring FILE-anchored symbols.

    A ``kind == "file"`` symbol declares span ``1-1`` — bash's
    ``lintWhitespace.sh`` is ``bash:scripts/lintWhitespace.sh:1-1:file:file``
    for a 26-line script. Reading that span literally searches ONE line and
    reports "none found" for a redirect on line 10, which is the same
    manufactured absence as defect 4 arriving by another route. A file symbol
    means "somewhere in this file", so its scope is the whole file.

    Found by running the fixed builder against 0006's own ArkLib flows and
    reading the output back against source, not by a test — the unit fixtures
    all used function-kind symbols, where the declared span is right.
    """
    if parsed.get("kind") == "file":
        return (1, line_count) if line_count else None
    start, end = parsed.get("start"), parsed.get("end")
    if start is None or end is None:
        return None
    return (int(start), int(end))


_EXT_LANGUAGES = {
    ".py": "python", ".sh": "bash", ".bash": "bash", ".go": "go",
    ".js": "javascript", ".ts": "typescript", ".java": "java",
    ".rs": "rust", ".rb": "ruby", ".c": "c", ".h": "c", ".cpp": "cpp",
    ".kt": "kotlin", ".swift": "swift", ".scala": "scala",
    ".ex": "elixir", ".exs": "elixir", ".hs": "haskell", ".m": "objc",
}

# Only interpreters whose language the table above already knows. A shebang
# for anything else resolves to "" exactly as an unknown extension does --
# the point is to stop MISSING a language, not to start guessing one.
_SHEBANG_LANGUAGES = {
    "sh": "bash", "bash": "bash", "dash": "bash", "ksh": "bash",
    "zsh": "bash", "python": "python", "python2": "python",
    "python3": "python", "ruby": "ruby",
}


def _interpreter_of(first_line: str) -> str:
    """The interpreter a shebang names, or "" if the line is not one.

    Handles both spellings that occur: a direct path (``#!/bin/bash``) and
    the env form (``#!/usr/bin/env bash``), where the interpreter is the
    ARGUMENT rather than the executable.
    """
    if not first_line.startswith("#!"):
        return ""
    parts = first_line[2:].split()
    if not parts:
        return ""
    name = Path(parts[0]).name
    if name == "env":
        return Path(parts[1]).name if len(parts) > 1 else ""
    return name


def _language_of(path: str, first_line: str = "") -> str:
    """The language of a file, by extension and then by shebang.

    THE EXTENSION IS CONSULTED FIRST AND WINS. It is the author's declaration
    about the whole file, where a shebang says only how one entry point is
    executed; letting the interpreter line win would relabel a ``.py`` wrapper
    that happens to open ``#!/bin/bash``.

    The shebang exists here because ``Path(".github_deploy").suffix`` is ""
    -- a leading dot is not an extension -- so shellcheck's two extensionless
    bash scripts were read as an unknown language. That silently skipped the
    bash-only ambient-expansion listing and printed "(none found)" for a file
    whose line 11 is ``for tag in $TAGS``: evidence of absence the instrument
    manufactured, in the direction of FALSE POSITIVE.
    """
    return (
        _EXT_LANGUAGES.get(Path(path).suffix)
        or _SHEBANG_LANGUAGES.get(_interpreter_of(first_line), "")
    )


def _read_lines(repo_root: Path, rel: str) -> "list[str] | None":
    try:
        return (repo_root / rel).read_text(
            encoding="utf-8", errors="replace",
        ).splitlines()
    except OSError:
        return None


def render_packet(flow: dict[str, Any], repo_root: Path) -> str:
    """One adjudication packet: the flow's facts and the source behind them.

    Carries NO verdict and no precision language — the primary and the blind
    second pass consume the same text, which is what makes the second pass
    blind rather than merely independent.
    """
    out: list[str] = []
    out.append(
        f"## {flow.get('flow_id', '?')}  claim={flow.get('claim_id', '?')}  "
        f"method={flow.get('analysis_method', '?')}  "
        f"stands_for={flow.get('collapsed_flow_count', 1)}"
    )
    out.append(
        f"   SOURCE {flow.get('source_primitive', '?')} "
        f"({flow.get('source_boundary', '?')}) in {flow.get('source_file', '?')}"
    )
    out.append(
        f"   SINK   {flow.get('sink_name', '?')} @ {flow.get('sink_module', '?')}"
        f"   hops={flow.get('hops', '?')}"
    )

    src_rel = flow.get("source_file") or ""
    src_span = flow.get("source_lines") or [0, 0]
    src_lines = _read_lines(repo_root, src_rel)
    src_lang = _language_of(src_rel, src_lines[0] if src_lines else "")
    if src_lines is None:
        out.append(f"   -- SOURCE sites in {src_rel} --")
        out.append(f"        (unreadable: {src_rel})")
    else:
        span = effective_span(
            parse_symbol_id(flow.get("source_symbol") or ""), len(src_lines),
        ) or (int(src_span[0]), int(src_span[1]))
        # The label reports the EFFECTIVE span, not the declared one: a
        # file-anchored symbol declares 1-1 and is searched whole, and a header
        # saying 1-1 above a hit on line 16 is a contradiction a reader would
        # have to resolve by guessing.
        out.append(
            f"   -- SOURCE sites in {src_rel} span {span[0]}-{span[1]} --"
        )
        primitive = str(flow.get("source_primitive") or "")
        if src_lang == "bash" and primitive in {"environ", "env"}:
            hits = [
                (n, s) for n, s in shell_env_sites(src_lines)
                if span[0] <= n <= span[1]
            ]
        else:
            hits = find_sites(
                src_lines, [primitive], span[0], span[1], src_lang,
            )
        for lineno, text in hits or []:
            out.append(f"     {lineno:6}: {text}")
        if not hits:
            out.append(
                f"        (none found; searched {src_rel} "
                f"{span[0]}-{span[1]})"
            )

    names = sink_names(flow)
    spans = sink_search_spans(flow)
    out.append(
        f"   -- SINK {names} sites across {len(spans)} path symbol(s) --"
    )
    total = 0
    for rel, start, end, symbol_name in spans:
        lines = _read_lines(repo_root, rel)
        if lines is None:
            out.append(f"     {symbol_name} ({rel}): (unreadable)")
            continue
        if end == -1:
            end = len(lines)
        hits = find_sites(
            lines, names, start, end,
            _language_of(rel, lines[0] if lines else ""),
        )
        total += len(hits)
        if hits:
            out.append(f"     in {symbol_name} ({rel} {start}-{end}):")
            for lineno, text in hits:
                out.append(f"     {lineno:6}: {text}")
    if not total:
        scope = ", ".join(
            f"{n} {r} {s}-{e}" for r, s, e, n in spans
        ) or "no first-party symbol on the path"
        out.append(f"        (none found; searched {scope})")
    return "\n".join(out)


def cmd_packet(args: argparse.Namespace) -> int:
    """Render one packet per collected flow, grouped by repository."""
    flows = load_flows(Path(args.flows))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_repo: dict[str, list[dict[str, Any]]] = {}
    for flow in flows:
        by_repo.setdefault(str(flow.get("repo") or "unknown"), []).append(flow)
    for repo, rows in sorted(by_repo.items()):
        root = Path(args.repo_root) / repo if args.repo_root else Path(repo)
        body = [f"# PACKET v3 — {repo}", ""]
        for flow in rows:
            body.append(render_packet(flow, root))
            body.append("")
        (out_dir / f"{repo}.md").write_text("\n".join(body), encoding="utf-8")
        print(f"  {repo}: {len(rows)} packet(s)")
    print(f"wrote {len(by_repo)} file(s) to {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="run verify-claims, dump flows")
    p_collect.add_argument("--repo", required=True)
    p_collect.add_argument("--claims", default=str(DEFAULT_CLAIMS))
    p_collect.add_argument("--out", required=True)
    p_collect.add_argument("--label", default="")
    p_collect.add_argument(
        "--no-collapse", action="store_true",
        help="disable INV-karud's situation collapse so each record is one "
             "source->sink PAIR — the 0001/0004 row unit, adjudicable on its "
             "own terms instead of apportioned from a situation label",
    )
    p_collect.set_defaults(func=cmd_collect)

    p_sample = sub.add_parser("sample", help="stratified sample of collected flows")
    p_sample.add_argument("--flows", required=True)
    p_sample.add_argument("--out", required=True)
    p_sample.add_argument("--n", type=int, default=60)
    p_sample.add_argument("--minimum", type=int, default=10)
    p_sample.add_argument("--seed", type=int, default=20260811)
    p_sample.set_defaults(func=cmd_sample)

    p_score = sub.add_parser("score", help="score adjudicated flows")
    p_score.add_argument("--flows", required=True)
    p_score.add_argument("--labels", required=True, action="append",
                         help="label ledger JSON; repeatable, later wins")
    p_score.add_argument("--compare", action="append", default=[],
                         help="second ledger for the disagreement rate")
    p_score.set_defaults(func=cmd_score)

    p_packet = sub.add_parser(
        "packet", help="render blind adjudication packets from collected flows",
    )
    p_packet.add_argument("--flows", required=True)
    p_packet.add_argument("--out", required=True)
    p_packet.add_argument(
        "--repo-root", default="",
        help="directory holding the analysed repositories, one per label",
    )
    p_packet.set_defaults(func=cmd_packet)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
