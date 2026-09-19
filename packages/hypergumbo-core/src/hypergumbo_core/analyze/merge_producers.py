# SPDX-License-Identifier: AGPL-3.0-or-later
"""The merge pass: two producers' records for one declaration become one
Symbol (ADR-0057 §3, WI-kokiz).

WHY A PASS, AND WHY HERE. When two backends analyse one language — the
tree-sitter ``rust`` incumbent and the ``rust_analyzer`` SCIP arm — each
emits its own Symbol for every declaration it sees, and until this pass
nothing folded them: ``run_all_analyzers`` concatenated both lists and every
Phase-C reader saw two records for one function. The merge key is
whole-table (per file, the item whose span contains the other producer's
name token), it rewrites identity that hundreds of ``.src`` / ``.dst`` /
``.kind`` reads key on, and membership must be settled before serialize
(ADR-0043 R1). So it is a declared pass at the top of Phase C, after
relativization and before ``refine_frameworks``, not an on-demand facade.

WHAT IT READS, AND FROM WHERE. Nothing here is Rust-shaped:

* WHO produced a record: ``Symbol.origin_run_id`` joins to an
  ``AnalysisRun`` whose ``pass`` is the producing analyzer's registration
  name. A record whose run is not an anchored producer of its language
  (linkers, synthesis passes, a producer that stayed off) is left alone.
* HOW two producers' records pair: each producer's ``MergeAnchor``
  (:mod:`.registry`, ADR-0057 §10) — ``name_key`` maps ``Symbol.name`` to
  the backend-neutral declaration name, ``span_role`` says whether
  ``Symbol.span`` is the identifier token or the item. Two records pair
  when they share a path and a key and their spans satisfy the roles: a
  token inside an item, or item equal to item. ``merge_participants``
  supplies the anchored producers and REFUSES, naming the analyzer, a
  producer of a shared language with no declaration; that exception is
  this pass's refusal, and it is not caught here.
* WHICH producer's value the scalar slot keeps: the incumbent's
  (:func:`~.registry.incumbent_first` — ADR-0057 §5's built-in default,
  incumbent first for every categorical attribute, until WI-hukuf makes it
  a ``config.toml`` preference). Where the incumbent did not observe an
  attribute and the other producer did (``signature``, ``docstring``,
  ``qualified_name``, ...), the merged record takes the value that exists —
  disjoint coverage is one value with one provenance (§1).

THE MERGED RECORD. A copy of the incumbent's, with: a NEW ``id`` minted from
the merged attributes through ``make_symbol_id`` (ADR-0036: an id derives
from attributes, so the merged attributes give the merged id — which
coincides with the incumbent's exactly when incumbent-first arbitration
leaves every identity attribute the incumbent's); the ITEM span (the
INV-lodum cure for every merged record — a SCIP record spanned over its
name token is folded into the item the incumbent spanned; SCIP-only
leftovers keep their token span); ``origin`` = both producers' pass ids,
incumbent first; ``meta`` = the union, the incumbent winning a key both
set, so the SCIP moniker (``scip_symbol``) survives on the merged record;
``origin_run_id`` = this pass's run, since this pass emitted the record as
it now exists, and the two producers are recoverable from ``origin``.

WHAT IS PRESERVED FOR THE PROVENANCE SLOT. Every merged record's
:class:`MergedRecord` lists its members and, per tracked attribute, the
(value, producers) candidates — agreement is one value with two producers,
disagreement two values with one each. That is ADR-0057 §1 in memory; the
schema-versioned slot that serializes it is WI-binis, the next row.

EDGES (§11). ``src``, ``dst`` and ``edge_type`` are identity. Every edge
endpoint (and ``derived_from``) that named a folded record is rewired to
the merged id and its ``edge_key`` reset, so the caller's
``deduplicate_edges`` collapses two producers' edges that agree on all
three; a disagreement stays two edges, each with its own ``origin`` — that
IS the provenance of the disagreement. Usage contexts' ``symbol_ref`` is
rewired the same way.

WHAT IT REFUSES TO GUESS. A record with two candidate partners, or a
candidate claimed by two records, is left unmerged and reported
(:attr:`MergeReport.ambiguous`); a wrong fold would be a false identity
claim, and the fixture measures zero such cases on aardvark-dns. A language
with one producer present is untouched: a tree-sitter-only run is
byte-identical before and after, and no AnalysisRun is appended for a pass
that folded nothing (the file-symbol synthesizer's convention; whether a
no-op pass gets a run is the ADR-0056 question the owner reserved).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..ir import PASS_VERSION, AnalysisRun, Edge, Symbol, UsageContext
from .base import make_symbol_id
from .registry import (
    SPAN_ROLE_ITEM,
    SPAN_ROLE_TOKEN,
    MergeAnchor,
    RegisteredAnalyzer,
    incumbent_first,
    merge_participants,
)

PASS_ID = "producer-merge"

#: Attributes whose candidates are recorded per merged record (ADR-0057 §1).
TRACKED_ATTRIBUTES: Tuple[str, ...] = (
    "name", "kind", "stable_id", "span", "signature", "docstring",
    "qualified_name", "visibility", "modifiers", "is_exported",
)

#: Scalar attributes the merged record takes from the other producer when the
#: incumbent did not observe them (disjoint coverage, §1).
_FILL_WHEN_INCUMBENT_EMPTY: Tuple[str, ...] = (
    "stable_id", "signature", "docstring", "qualified_name", "visibility",
    "display_label", "cyclomatic_complexity", "line_span", "modifiers",
)


@dataclass
class MergedRecord:
    """One merged Symbol's provenance, kept for the WI-binis slot."""

    merged_id: str
    language: str
    path: str
    #: producer registration name -> the id of the record it emitted
    members: Dict[str, str]
    #: attribute -> [(value, producers that hold it)]
    candidates: Dict[str, List[Tuple[Any, Tuple[str, ...]]]]


@dataclass
class MergeReport:
    """What the pass did; ``merged`` empty means the pass was a no-op."""

    languages: List[str] = field(default_factory=list)
    merged: List[MergedRecord] = field(default_factory=list)
    #: (record id that could not be folded, candidate partner ids)
    ambiguous: List[Tuple[str, List[str]]] = field(default_factory=list)
    #: every folded id -> the merged id (both members, when they differ)
    id_remap: Dict[str, str] = field(default_factory=dict)
    run: Optional[AnalysisRun] = None


def _pass_of_run(analysis_runs: Iterable[Mapping[str, Any]]) -> Dict[str, str]:
    return {
        str(run["execution_id"]): str(run["pass"])
        for run in analysis_runs
        if run.get("execution_id") and run.get("pass")
    }


def _anchor(analyzer: RegisteredAnalyzer) -> MergeAnchor:
    # merge_participants returns anchored producers only.
    assert isinstance(analyzer.merge, MergeAnchor)
    return analyzer.merge


def _spans_pair(
    incumbent: Symbol, incumbent_role: str, other: Symbol, other_role: str,
) -> bool:
    a, b = incumbent.span, other.span
    if a is None or b is None:
        return False
    if incumbent_role == SPAN_ROLE_ITEM and other_role == SPAN_ROLE_TOKEN:
        return a.start_line <= b.start_line <= a.end_line
    if incumbent_role == SPAN_ROLE_TOKEN and other_role == SPAN_ROLE_ITEM:
        return b.start_line <= a.start_line <= b.end_line
    if incumbent_role == SPAN_ROLE_ITEM:  # item against item
        return (a.start_line, a.end_line) == (b.start_line, b.end_line)
    return (a.start_line, a.start_col) == (b.start_line, b.start_col)  # token against token


def _value(symbol: Symbol, attribute: str) -> Any:
    got = getattr(symbol, attribute)
    if attribute == "span" and got is not None:
        return (got.start_line, got.end_line, got.start_col, got.end_col)
    return got


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _candidates(
    attribute: str, members: Sequence[Tuple[str, Symbol]],
) -> List[Tuple[Any, Tuple[str, ...]]]:
    """(value, producers) per distinct observed value, incumbent's value first."""
    out: List[Tuple[Any, List[str]]] = []
    for producer, symbol in members:
        value = _value(symbol, attribute)
        if _empty(value):
            continue  # not observed by this producer: no entry (§1)
        for existing in out:
            if existing[0] == value:
                existing[1].append(producer)
                break
        else:
            out.append((value, [producer]))
    return [(value, tuple(producers)) for value, producers in out]


def _merge_one(
    incumbent_name: str,
    incumbent: Symbol,
    incumbent_role: str,
    other_name: str,
    other: Symbol,
    other_role: str,
    run_id: str,
) -> Tuple[Symbol, MergedRecord]:
    merged = replace(incumbent)
    # The ITEM span: whichever member spans the item (INV-lodum cure).
    # (A member with no span never pairs — _spans_pair — so both spans exist here.)
    if incumbent_role != SPAN_ROLE_ITEM and other_role == SPAN_ROLE_ITEM and other.span is not None:
        merged.span = replace(other.span)
    for attribute in _FILL_WHEN_INCUMBENT_EMPTY:
        if _empty(getattr(merged, attribute)) and not _empty(getattr(other, attribute)):
            setattr(merged, attribute, getattr(other, attribute))
    # meta: union, incumbent wins a key both set.
    meta: Dict[str, Any] = dict(other.meta or {})
    meta.update(incumbent.meta or {})
    merged.meta = meta or None
    # origin: both producers, incumbent first (INV-jidat: pass ids that contributed).
    origins = list(incumbent.origin if isinstance(incumbent.origin, list) else [incumbent.origin])
    for origin in (other.origin if isinstance(other.origin, list) else [other.origin]):
        if origin and origin not in origins:
            origins.append(origin)
    merged.origin = origins
    merged.origin_run_id = run_id
    span = merged.span
    language = merged.language or ""
    merged.id = make_symbol_id(
        language, merged.path,
        span.start_line if span else 0, span.end_line if span else 0,
        merged.name, merged.kind,
    )
    members = [(incumbent_name, incumbent), (other_name, other)]
    record = MergedRecord(
        merged_id=merged.id,
        language=language,
        path=merged.path,
        members={incumbent_name: incumbent.id, other_name: other.id},
        candidates={attr: _candidates(attr, members) for attr in TRACKED_ATTRIBUTES},
    )
    return merged, record


def merge_producer_records(
    symbols: List[Symbol],
    edges: List[Edge],
    analysis_runs: List[Dict[str, Any]],
    *,
    usage_contexts: Optional[List[UsageContext]] = None,
) -> MergeReport:
    """Fold two anchored producers' records for one declaration into one.

    Mutates ``symbols`` (folded records replaced in place by the merged one,
    the other member dropped), ``edges`` and ``usage_contexts`` (endpoints
    rewired), and ``analysis_runs`` (this pass's run appended when it merged
    anything). Returns the :class:`MergeReport`.

    Raises:
        UndeclaredProducerError: a producer of a shared language has no merge
            declaration (from :func:`~.registry.merge_participants`). This is
            the refusal ADR-0057 §10 requires; it is not caught here.
    """
    started = time.perf_counter()
    report = MergeReport()
    pass_of_run = _pass_of_run(analysis_runs)

    # language -> producer -> its records
    by_language: Dict[str, Dict[str, List[Symbol]]] = {}
    for symbol in symbols:
        producer = pass_of_run.get(symbol.origin_run_id)
        if producer is None or symbol.language is None:
            continue  # not a producer's record, or nothing to key a language on
        by_language.setdefault(symbol.language, {}).setdefault(producer, []).append(symbol)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)  # nosec B106 — a pass id, not a password
    replacement: Dict[int, Symbol] = {}  # id(incumbent record) -> merged record
    dropped: set[int] = set()

    for language in sorted(by_language):
        present = by_language[language]
        if len(present) < 2:
            continue
        participants = [a for a in merge_participants(language) if a.name in present]
        if len(participants) < 2:
            continue
        ordered = incumbent_first(participants)
        incumbent_analyzer, alternatives = ordered[0], ordered[1:]
        incumbent_anchor = _anchor(incumbent_analyzer)
        report.languages.append(language)

        index: Dict[Tuple[str, str], List[Symbol]] = {}
        for record in present[incumbent_analyzer.name]:
            index.setdefault((record.path, incumbent_anchor.name_key(record.name)), []).append(record)

        for alternative in alternatives:
            anchor = _anchor(alternative)
            claims: Dict[int, List[Symbol]] = {}  # id(incumbent record) -> claimants
            partner_of: Dict[int, List[Symbol]] = {}  # id(alt record) -> candidates
            for record in present[alternative.name]:
                key = (record.path, anchor.name_key(record.name))
                candidates = [
                    inc for inc in index.get(key, [])
                    if _spans_pair(inc, incumbent_anchor.span_role, record, anchor.span_role)
                ]
                partner_of[id(record)] = candidates
                for inc in candidates:
                    claims.setdefault(id(inc), []).append(record)
            for record in present[alternative.name]:
                candidates = partner_of[id(record)]
                if not candidates:
                    continue  # this producer alone saw it: stays as emitted
                if len(candidates) > 1 or len(claims[id(candidates[0])]) > 1:
                    report.ambiguous.append((record.id, [c.id for c in candidates]))
                    continue
                incumbent_record = candidates[0]
                survivor = replacement.get(id(incumbent_record), incumbent_record)
                merged, merged_record = _merge_one(
                    incumbent_analyzer.name, survivor, incumbent_anchor.span_role,
                    alternative.name, record, anchor.span_role, run.execution_id,
                )
                if id(incumbent_record) in replacement:
                    # A third producer joining an already-merged record.
                    earlier = next(m for m in report.merged if m.merged_id == survivor.id)
                    earlier.members.update(merged_record.members)
                    earlier.merged_id = merged.id
                    earlier.candidates = merged_record.candidates
                else:
                    report.merged.append(merged_record)
                replacement[id(incumbent_record)] = merged
                dropped.add(id(record))
                for original in (incumbent_record.id, survivor.id, record.id):
                    if original != merged.id:
                        report.id_remap[original] = merged.id

    if not report.merged:
        return report

    symbols[:] = [
        replacement.get(id(s), s) for s in symbols if id(s) not in dropped
    ]
    remap = report.id_remap
    for edge in edges:
        changed = False
        if edge.src in remap:
            edge.src = remap[edge.src]
            changed = True
        if edge.dst in remap:
            edge.dst = remap[edge.dst]
            changed = True
        if edge.derived_from and any(x in remap for x in edge.derived_from):
            edge.derived_from = [remap.get(x, x) for x in edge.derived_from]
        if changed:
            edge.edge_key = None  # the caller's deduplicate_edges recomputes it
    for context in usage_contexts or []:
        if context.symbol_ref in remap:
            context.symbol_ref = remap[context.symbol_ref]

    run.nodes_emitted = len(report.merged)
    run.duration_ms = int((time.perf_counter() - started) * 1000)
    report.run = run
    analysis_runs.append(run.to_dict())
    return report
