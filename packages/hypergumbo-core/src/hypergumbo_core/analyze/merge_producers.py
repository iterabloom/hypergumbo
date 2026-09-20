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

THE ARBITRATION PROPERTY AND THE PROVENANCE SLOT (§1, §4, §6; WI-binis).
Per tracked attribute the members' values form a candidate set — agreement
is one value with two producers, disagreement two values with one each, an
attribute only one producer observed has one entry. The scalar the record
carries is produced by :func:`arbitrate`: a pure function over that set
with ONE stamped default, precedence in incumbent-first order for every
categorical attribute (``span`` is the one exception: the item-role
member's span, whichever producer that is — INV-lodum). The choice is
written into the record's ``attribution`` (``field -> [producers holding
the carried value]``) and the losers into ``alternatives`` (``field ->
[{value, origin}]``, present only when contested), the ADR-0057 §6 slot
that ``to_dict`` emits only when set — so a single-producer artifact is
byte-identical. An alternative policy is an opt-in read of the candidate
set, never a second default.

EDGES (§11, §13). ``src``, ``dst`` and ``edge_type`` are identity. Every
edge endpoint (and ``derived_from``) that named a folded record is rewired
to the merged id and its ``edge_key`` reset. Then the participants' edges
that now agree on all three are FOLDED here (not left for
``deduplicate_edges``, whose survivor is encounter order — the SCIP arm
runs first by priority, and the fold must be incumbent-first): the
survivor is the incumbent's edge, the others' call sites are absorbed
(``_absorb_call_site``), ``origin`` is the union, and ``confidence`` is
COMBINED, not arbitrated by precedence: two DISTINCT inference pathways
(different ``evidence_type``) reaching one edge make it ``corroborated``
at :data:`CORROBORATED_CONFIDENCE` with both originals kept in
``alternatives``; the same pathway twice is not new evidence and keeps the
incumbent's value. A disagreement on any of the three identity fields
stays two edges, each with its own ``origin`` — that IS the provenance of
the disagreement. Usage contexts' ``symbol_ref`` is rewired the same way.

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
from dataclasses import MISSING, dataclass, field, fields, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..arbitration import (
    BUILTIN_POLICY,
    ArbitrationPolicy,
)
from ..arbitration import CORROBORATED_CONFIDENCE as CORROBORATED_CONFIDENCE  # re-export: the §13 level
from ..ir import (
    PASS_VERSION,
    AnalysisRun,
    Edge,
    ExternalRef,
    Symbol,
    UsageContext,
    _absorb_call_site,
    _edge_call_lines,
    callee_name_of,
    format_legacy_dst,
    mint_edge_id,
)
from .base import make_symbol_id
from .registry import (
    SPAN_ROLE_ITEM,
    SPAN_ROLE_TOKEN,
    MergeAnchor,
    RegisteredAnalyzer,
    merge_participants,
)

PASS_ID = "producer-merge"

#: ADR-0057 §13: the confidence of an edge two DISTINCT inference pathways
#: reached — ADR-0012's number for a type-resolved call. A declared level,
#: not a formula: ``max`` would launder SCIP's 0.85 emitter constant into
#: evidence and noisy-OR overstates producers reading the same text. It
#: joins the per-attribute table WI-hukuf makes configurable.

#: Attributes whose candidates are recorded per merged record (ADR-0057 §1).
TRACKED_ATTRIBUTES: Tuple[str, ...] = (
    "name", "kind", "stable_id", "span", "signature", "docstring",
    "qualified_name", "visibility", "modifiers", "is_exported",
)

#: Scalar attributes OUTSIDE the tracked set that the merged record takes from
#: the other producer when the incumbent did not observe them (disjoint
#: coverage, §1, without a provenance entry — they are not in the slot). Every
#: tracked attribute reaches the merged record through :func:`arbitrate`
#: instead, where a sole observer's value is the only candidate.
_FILL_WHEN_INCUMBENT_EMPTY: Tuple[str, ...] = (
    "display_label", "cyclomatic_complexity", "line_span",
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
    #: edges removed by the §11 fold (their survivor carries the slot)
    edges_folded: int = 0
    #: folded edges whose two pathways were distinct (§13)
    corroborated: int = 0
    #: partial external identity keys absorbed by a complete one (§15)
    external_folds: int = 0
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


def abstention_blind_attributes(
    record_type: type, attributes: Sequence[str] = TRACKED_ATTRIBUTES,
) -> Tuple[str, ...]:
    """Those ``attributes`` whose default on ``record_type`` is a CONCRETE value.

    Such a field cannot say that nobody looked: an unassigned one is
    indistinguishable from a measured one, so a producer that never computes
    it contributes a phantom candidate on every record it emits (INV-huboz).
    For those attributes — and only those — the producer's
    ``MergeAnchor.observes`` decides instead of the value (ADR-0057 §10).

    Kept as a function over a record type so the derivation itself is
    testable: the live sets are empty today (INV-kubup made ``is_exported``
    ``Optional[bool] = None``, which was the last one), and a guard that can
    only be exercised by regressing the tree is not a guard.
    """
    declared = {f.name: f for f in fields(record_type)}
    blind = []
    for attribute in attributes:
        field_ = declared[attribute]
        if field_.default is not MISSING:
            default = field_.default
        elif field_.default_factory is not MISSING:
            default = field_.default_factory()
        else:
            continue  # a required field: every producer supplies it
        if not _empty(default):
            blind.append(attribute)
    return tuple(blind)


#: The live set, DERIVED not listed, so a tracked attribute that gains a
#: concrete default appears here the day it is added and the registry
#: contract test then demands that every anchored producer rule on it. Empty
#: since INV-kubup: every tracked attribute's default is absent, so each
#: record says for itself, per record, whether its producer observed it —
#: which is strictly better information than a per-producer declaration.
ABSTENTION_BLIND_ATTRIBUTES: Tuple[str, ...] = abstention_blind_attributes(Symbol)


def _candidates(
    attribute: str,
    members: Sequence[Tuple[str, Symbol]],
    observed_by: Optional[Mapping[str, frozenset[str]]] = None,
) -> List[Tuple[Any, Tuple[str, ...]]]:
    """(value, producers) per distinct observed value, incumbent's value first.

    ``observed_by`` maps a producer to the :data:`ABSTENTION_BLIND_ATTRIBUTES`
    it declared it computes. For every other attribute the VALUE says whether
    it was observed, per record, and the declaration is not consulted. Omit it
    (edge folding does) and no blind attribute contributes at all — the safe
    direction, since a phantom candidate is asserted evidence.
    """
    out: List[Tuple[Any, List[str]]] = []
    for producer, symbol in members:
        if attribute in ABSTENTION_BLIND_ATTRIBUTES and attribute not in (
            (observed_by or {}).get(producer) or frozenset()
        ):
            continue  # this producer did not declare that it observes it (§1, §10)
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


Candidate = Tuple[Any, Tuple[str, ...]]


def arbitrate(candidates: Sequence[Candidate], precedence: Sequence[str]) -> Candidate:
    """The ADR-0057 §4 arbitration property for a categorical attribute.

    Pure over the candidate set: the value held by the earliest producer in
    ``precedence`` wins; ties cannot arise because a producer holds one
    value. ``precedence`` is the stamped default — incumbent first (§5) —
    until WI-hukuf reads it from ``config.toml``; a caller with another
    policy passes another order and reads the same candidates.
    """
    rank = {producer: index for index, producer in enumerate(precedence)}
    return min(candidates, key=lambda c: min(rank.get(p, len(rank)) for p in c[1]))


def _serializable(attribute: str, value: Any) -> Any:
    if attribute == "span":
        start_line, end_line, start_col, end_col = value
        return {"start_line": start_line, "end_line": end_line,
                "start_col": start_col, "end_col": end_col}
    return value


def _stamp_slot(
    record: Any,
    members: Sequence[Tuple[str, Any]],
    attributes: Sequence[str],
    precedence: Sequence[str],
    *,
    chosen: Optional[Dict[str, Candidate]] = None,
    precedence_by_attribute: Optional[Mapping[str, Sequence[str]]] = None,
    observed_by: Optional[Mapping[str, frozenset[str]]] = None,
) -> Dict[str, Candidate]:
    """Arbitrate every attribute, set the scalar, write attribution / alternatives.

    ``chosen`` pre-decides attributes whose rule is not precedence (the item
    span; a corroborated confidence). ``precedence_by_attribute`` is the
    policy's per-attribute order where one is declared (WI-hukuf); every
    other attribute uses ``precedence``. Returns the winners so a caller can
    apply a value that is not a plain setattr (a ``Span`` object).
    """
    attribution: Dict[str, List[str]] = {}
    alternatives: Dict[str, List[Dict[str, Any]]] = {}
    winners: Dict[str, Candidate] = {}
    for attribute in attributes:
        candidates = _candidates(attribute, members, observed_by)
        if not candidates:
            continue  # nobody observed it: no entry at all (§1)
        order = (precedence_by_attribute or {}).get(attribute, precedence)
        winner = (chosen or {}).get(attribute) or arbitrate(candidates, order)
        winners[attribute] = winner
        attribution[attribute] = list(winner[1])
        losers = [c for c in candidates if c[0] != winner[0]]
        if losers:
            alternatives[attribute] = [
                {"value": _serializable(attribute, value), "origin": list(producers)}
                for value, producers in losers
            ]
        if attribute != "span":
            setattr(record, attribute, winner[0])
    record.attribution = attribution or None
    record.alternatives = alternatives or None
    return winners


def _merge_one(
    incumbent_name: str,
    incumbent: Symbol,
    incumbent_role: str,
    other_name: str,
    other: Symbol,
    other_role: str,
    run_id: str,
    *,
    precedence_by_attribute: Optional[Mapping[str, Sequence[str]]] = None,
    observed_by: Optional[Mapping[str, frozenset[str]]] = None,
) -> Tuple[Symbol, MergedRecord]:
    merged = replace(incumbent)
    members = [(incumbent_name, incumbent), (other_name, other)]
    precedence = [incumbent_name, other_name]
    # The ITEM span: whichever member spans the item (INV-lodum cure) — the
    # one attribute not decided by precedence. (A member with no span never
    # pairs — _spans_pair — so both spans exist here.)
    item_holder = other if (incumbent_role != SPAN_ROLE_ITEM and other_role == SPAN_ROLE_ITEM) else incumbent
    item_span = _value(item_holder, "span")
    span_candidates = _candidates("span", members)
    chosen_span = next(c for c in span_candidates if c[0] == item_span)
    _stamp_slot(merged, members, TRACKED_ATTRIBUTES, precedence, chosen={"span": chosen_span},
                precedence_by_attribute=precedence_by_attribute, observed_by=observed_by)
    if item_holder.span is not None:
        merged.span = replace(item_holder.span)
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
    record = MergedRecord(
        merged_id=merged.id,
        language=language,
        path=merged.path,
        members={incumbent_name: incumbent.id, other_name: other.id},
        candidates={attr: _candidates(attr, members, observed_by) for attr in TRACKED_ATTRIBUTES},
    )
    return merged, record


def _fold_edges(
    edges: List[Edge],
    pass_of_run: Mapping[str, str],
    precedence: Sequence[str],
    run_id: str,
    report: MergeReport,
    *,
    policy: ArbitrationPolicy = BUILTIN_POLICY,
    precedence_by_attribute: Optional[Mapping[str, Sequence[str]]] = None,
) -> None:
    """§11 / §13: fold the participants' edges that agree on (src, dst, edge_type)."""
    rank = {producer: index for index, producer in enumerate(precedence)}
    groups: Dict[Tuple[str, str, str], List[Tuple[str, Edge]]] = {}
    for edge in edges:
        producer = pass_of_run.get(edge.origin_run_id)
        if producer is None or producer not in rank:
            continue
        groups.setdefault((edge.src, edge.dst, edge.edge_type), []).append((producer, edge))
    dropped: set[int] = set()
    for group in groups.values():
        if len({producer for producer, _ in group}) < 2:
            continue
        group.sort(key=lambda item: rank[item[0]])
        survivor_name, survivor = group[0]
        others = group[1:]
        members = list(group)
        # A producer may hold several edges on one key (SCIP emits one per
        # occurrence): provenance names each producer once, in precedence order.
        producers = list(dict.fromkeys(producer for producer, _ in group))
        originals = list(dict.fromkeys((producer, edge.confidence) for producer, edge in group))
        distinct_pathways = len({edge.evidence_type for _, edge in group}) > 1
        chosen: Dict[str, Candidate] = {}
        if distinct_pathways:
            chosen["confidence"] = (policy.corroborated_confidence, tuple(producers))
            survivor.confidence_source = "corroborated"
            report.corroborated += 1
        winners = _stamp_slot(
            survivor, members, ("confidence", "evidence_type"), precedence, chosen=chosen,
            precedence_by_attribute=precedence_by_attribute,
        )
        if distinct_pathways:
            # Every original is an alternative to the combined value (§13).
            assert survivor.alternatives is not None
            survivor.alternatives["confidence"] = [
                {"value": confidence, "origin": [producer]} for producer, confidence in originals
            ]
        del winners
        origins = list(survivor.origin)
        for _producer_name, other in others:
            for origin in other.origin:
                if origin not in origins:
                    origins.append(origin)
            _absorb_call_site(survivor, other)
            dropped.add(id(other))
        survivor.origin = origins
        survivor.origin_run_id = run_id
        survivor.edge_key = None
        report.edges_folded += len(others)
    if dropped:
        edges[:] = [edge for edge in edges if id(edge) not in dropped]


#: An external target's legacy ``dst`` ends in this kind slot before
#: ``apply_external_id_remap`` rewrites it to ``external_symbol`` in finalize.
#: The merge pass runs long before that, so this is what "outside the repo"
#: looks like here.
_EXTERNAL_KIND_SUFFIX = ":unresolved"


def _external_language(edge: Edge) -> Optional[str]:
    """The language slot of an edge pointing outside the repo, else ``None``."""
    if not edge.dst.endswith(_EXTERNAL_KIND_SUFFIX):
        return None
    language = edge.dst.split(":", 1)[0]
    return language or None


def _stated_module(edge: Edge) -> Optional[ExternalRef]:
    """The edge's COMPLETE external key, or ``None`` when it abstains.

    §15.1: the abstention is signalled positively by ``dst_ref is None``,
    never inferred from the ``dst`` string — whose module segment carries the
    ``external`` sentinel that ADR-0051's axiom defines as not a marker for
    the absence of an answer. An ``ExternalRef`` with an empty module path
    states nothing either, and is neither partial nor complete.
    """
    ref = edge.dst_ref
    if ref is None or not ref.module_path:
        return None
    return ref


def _absorb_partial_external_keys(
    edges: List[Edge],
    pass_of_run: Mapping[str, str],
    precedence: Sequence[str],
    run_id: str,
    report: MergeReport,
    name_key_by_language: Mapping[str, Any],
    *,
    policy: ArbitrationPolicy = BUILTIN_POLICY,
    precedence_by_attribute: Optional[Mapping[str, Sequence[str]]] = None,
) -> None:
    """§15: a partial external identity key is absorbed by a complete one.

    Runs on what §11's fold left over. The two edges are the same call seen
    by two producers, one of which determined the receiver's module and one
    of which did not, so their ``dst`` strings differ and §11 kept both. The
    key is §14's — same ``src``, a shared call line, the same ``edge_type``,
    the same callee under the INCUMBENT's declared ``name_key`` — plus
    "exactly one side states a module".

    Placement is load-bearing and is why this lives in the merge pass rather
    than in finalize: ``deduplicate_edges`` has not run, so every edge here
    is still one call site. After it, a stub carries ``meta["call_lines"]``
    for N sites and absorbing it would assert the twin's module of all N.
    """
    rank = {producer: index for index, producer in enumerate(precedence)}
    sites: Dict[Tuple[str, int, str, str], List[Tuple[str, Edge]]] = {}
    for edge in edges:
        producer = pass_of_run.get(edge.origin_run_id)
        if producer is None or producer not in rank:
            continue
        language = _external_language(edge)
        if language is None:
            continue
        name_key = name_key_by_language.get(language)
        if name_key is None:
            continue
        ref = edge.dst_ref
        name = callee_name_of(
            edge.dst, meta=edge.meta, dst_ref_name=ref.name if ref is not None else None
        )
        if not name:
            continue  # no name information: nothing to pair on (INV-difud)
        keyed = name_key(name)
        for line in _edge_call_lines(edge):
            sites.setdefault((edge.src, line, edge.edge_type, keyed), []).append((producer, edge))

    dropped: set[int] = set()
    for site in sorted(sites):
        group = [(p, e) for p, e in sites[site] if id(e) not in dropped]
        partials = [(p, e) for p, e in group if e.dst_ref is None]
        completes = [(p, e) for p, e in group if _stated_module(e) is not None]
        if not partials or not completes:
            continue
        modules = {_stated_module(e).module_path for _, e in completes}  # type: ignore[union-attr]
        if len(modules) > 1 or len(partials) > 1:
            # §15.5: more than one complete candidate that disagree, or one
            # complete claimed by two partials. Refused and reported, never
            # guessed — a wrong fold would be a false identity.
            report.ambiguous.append((partials[0][1].id, [e.id for _, e in completes]))
            continue
        partial_producer = partials[0][0]
        stating = sorted(
            (item for item in completes if item[0] != partial_producer),
            key=lambda item: rank[item[0]],
        )
        if not stating:
            continue  # one producer's two calls on a line are not a contradiction
        stating_producer, stated = stating[0][0], _stated_module(stating[0][1])
        assert stated is not None  # `stating` is drawn from `completes`

        members = sorted(group, key=lambda item: rank[item[0]])
        survivor_name, survivor = members[0]
        del survivor_name
        others = members[1:]
        producers = list(dict.fromkeys(producer for producer, _ in members))
        originals = list(dict.fromkeys((p, e.confidence) for p, e in members))
        distinct_pathways = len({edge.evidence_type for _, edge in members}) > 1
        chosen: Dict[str, Candidate] = {}
        if distinct_pathways:
            chosen["confidence"] = (policy.corroborated_confidence, tuple(producers))
            survivor.confidence_source = "corroborated"
            report.corroborated += 1
        _stamp_slot(
            survivor, members, ("confidence", "evidence_type"), precedence, chosen=chosen,
            precedence_by_attribute=precedence_by_attribute,
        )
        if distinct_pathways:
            assert survivor.alternatives is not None
            survivor.alternatives["confidence"] = [
                {"value": confidence, "origin": [producer]} for producer, confidence in originals
            ]
        origins = list(survivor.origin)
        for _name, other in others:
            for origin in other.origin:
                if origin not in origins:
                    origins.append(origin)
            _absorb_call_site(survivor, other)
            dropped.add(id(other))
        survivor.origin = origins
        survivor.origin_run_id = run_id
        # §15.2: the survivor takes the STATED ref and a dst rebuilt from it,
        # so the id it derives from has changed and must be re-minted.
        survivor.dst_ref = stated
        survivor.dst = format_legacy_dst(stated)
        survivor.id = mint_edge_id(survivor.src, survivor.dst, survivor.edge_type, survivor.line)
        survivor.edge_key = None
        # §15.6: the loser is an ABSTENTION, and §1 says an abstention
        # contributes no entry at all — so the stating producer is named in
        # `attribution` and `alternatives` gains nothing. Recording
        # `module_path: "external"` here would stamp the defect into the slot
        # that exists to cure it.
        survivor.attribution = {**(survivor.attribution or {}), "dst_ref": [stating_producer]}
        report.edges_folded += len(others)
        report.external_folds += 1
    if dropped:
        edges[:] = [edge for edge in edges if id(edge) not in dropped]


def merge_producer_records(
    symbols: List[Symbol],
    edges: List[Edge],
    analysis_runs: List[Dict[str, Any]],
    *,
    usage_contexts: Optional[List[UsageContext]] = None,
    policy: ArbitrationPolicy = BUILTIN_POLICY,
) -> MergeReport:
    """Fold two anchored producers' records for one declaration into one.

    ``policy`` (WI-hukuf) decides precedence among a language's producers —
    the built-in is incumbent first — and the §13 corroboration level.

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
    precedence: List[str] = []  # every merged language's producers, in policy order
    by_attribute: Dict[str, List[str]] = {}  # the policy's per-attribute orders, across languages
    name_key_of: Dict[str, Any] = {}  # language -> the incumbent's declared name key (§10, §15)

    for language in sorted(by_language):
        present = by_language[language]
        if len(present) < 2:
            continue
        participants = [a for a in merge_participants(language) if a.name in present]
        if len(participants) < 2:
            continue
        ordered = policy.order(participants)
        # What each producer DECLARED it observes among the attributes whose
        # value cannot say so (§10). An anchored producer that has not ruled
        # observes none of them — the registry contract test is what stops a
        # shipped producer from silently landing there.
        observed_by = {
            a.name: frozenset(_anchor(a).observes or ()) for a in ordered
        }
        incumbent_analyzer, alternatives = ordered[0], ordered[1:]
        incumbent_anchor = _anchor(incumbent_analyzer)
        report.languages.append(language)
        precedence.extend(a.name for a in ordered if a.name not in precedence)
        name_key_of[language] = incumbent_anchor.name_key
        language_by_attribute = policy.precedence_by_attribute(participants)
        for attribute, names in language_by_attribute.items():
            known = by_attribute.setdefault(attribute, [])
            known.extend(n for n in names if n not in known)

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
                    precedence_by_attribute=language_by_attribute,
                    observed_by=observed_by,
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

    if report.merged:
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
    if precedence:
        _fold_edges(edges, pass_of_run, precedence, run.execution_id, report,
                    policy=policy, precedence_by_attribute=by_attribute)
        _absorb_partial_external_keys(
            edges, pass_of_run, precedence, run.execution_id, report, name_key_of,
            policy=policy, precedence_by_attribute=by_attribute,
        )
    if not report.merged and not report.edges_folded:
        return report

    run.nodes_emitted = len(report.merged)
    run.duration_ms = int((time.perf_counter() - started) * 1000)
    report.run = run
    analysis_runs.append(run.to_dict())
    return report
