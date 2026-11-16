# SPDX-License-Identifier: AGPL-3.0-or-later
"""Published data-flow coverage scope for taint output — INV-karud clause (a3).

WHY THIS EXISTS. The clause reads: "The output must state which flows were
adjudicated by data flow and which by call reachability alone. A reader must be
able to tell the two apart from the emitted record; the scope of data-flow
coverage may not be left to assumption." Per-flow that is
``TaintFlowFinding.analysis_method``. This module publishes the *scope* —
which languages the data-flow machinery can run on at all, and therefore what a
``structural`` label means for a given repository. Without it, "0 precise
findings" is unreadable: it may mean the analysis looked everywhere and found
no data dependence, or that it was structurally incapable of looking. Those
have opposite consequences for a security reader and identical evidence, which
is L58 stated at the level of the whole run rather than one walk.

WHY FOUR CAPABILITY BITS. A language can only be data-flow adjudicated when all
four hold: a ``cfg_nodes/<lang>.yaml`` mapping exists, that mapping declares
``atomic_statement`` (without it the CFG bottoms out at leaf tokens and def/use
sees nothing), a def/use extractor is registered, and a ``LanguageDdgSpec`` is
registered. They are independent, and each is *individually* sufficient to keep
the walk silently inert while the others look healthy. A single "supported?"
boolean would say "no" without saying which of the four to fix, and reporting
only the CFG mapping would say "yes" for a language with a mapping but no
def/use extractor behind it, which can adjudicate nothing.

REGISTRATION IS THE TRAP THIS MODULE MUST NOT FALL INTO. Both registries are
populated as an *import side effect* of the language modules. A scope table
computed before that import reports every language incapable — a clean,
plausible, entirely wrong answer of exactly the shape L53 is about. So
:func:`compute_dataflow_scope` forces registration itself rather than trusting
its caller to have done it, and ``test_dataflow_scope`` carries a non-vacuity
floor asserting that the registered languages read capable.

WHAT THIS MODULE DELIBERATELY DOES NOT CLAIM — I. Capability is reported per
LANGUAGE, and that is not a per-function completeness claim. A capable language
still contains constructs the CFG does not model (a Go ``select`` case's
``v := <-ch`` binding, a type switch's ``v := x.(type)``), and a reader who
took the bit for coverage would be making exactly the assumption clause (a3)
forbids. The finer signal is the per-function coverage gate: a function whose
CFG statement extents miss a call node or semantic leaf in its body forfeits
refutation. So
``coverage_granularity`` reads ``function``, and the emitted record counts the
forfeiting functions rather than leaving that fact here, where no consumer
would find it (history: WI-joluk).

WHAT THIS MODULE DELIBERATELY DOES NOT CLAIM — II. ``inclusion_decided_by`` is a
constant, not a measurement. It reads
``call_graph_reachability_minus_ddg_refutation`` because both halves are true:
a flow is *included* by call-graph reachability, and the §3a walk can *remove*
one it refutes. The constant has a test on it, so if §3a's authority changes
the declared value must change with it (R16; history: WI-kabif).

The walk only ever SUBTRACTS: it adds no flow that reachability did not already
report, and it removes only on ``refuted`` — the walk exhausted every route
with nothing unexplained. ``escaped`` is ignorance and removes nothing. So
reading ``analysis_method == "ddg"`` as "this flow's inclusion was decided by
data flow" is the misreading INV-sadah exists for: a surviving ``ddg`` flow was
included by reachability and merely corroborated by the walk.

WHAT THIS MODULE DELIBERATELY DOES NOT CLAIM — III: the same argument applied
to this module's own removal count. ``flows_removed_by_walk`` is published
beside a ``findings_by_analysis_method`` rollup in which ``ddg_mixed``
collapses THREE walk verdicts and ``structural`` covers a fourth
(``unavailable`` — the walk did not run, because the DDG held no reaching-def
data for that flow's source function). Only ``refuted`` can remove a flow.
So a bare ``0`` could mean "the walk adjudicated every flow and refuted none" or
"the walk never got to look at any of them" — opposite meanings on identical
evidence. :func:`count_walk_verdicts` publishes the finer axis so the zero is
readable; a run whose every finding reads ``unavailable`` is the case it
exists to expose.

The walk's escape sites are a DECLARED limitation, not a work item: most of
them are not calls at all, so no function summary can close them. §3a still
removes a flow it refutes, but the coverage it would need to fire at scale is
not being pursued, and the emitted record tells the reader so rather than
leaving them to infer it from a zero (history: INV-busis option (c)).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .cfg import get_def_use_extractor, load_cfg_mapping
from .ddg_build import registered_ddg_languages
from .taint import WALK_VERDICTS

#: What decided that a reported flow is a flow, as opposed to what raised its
#: confidence afterwards. See the module docstring — this is a declared
#: property of ADR-0017 §3a's adjudicating design, not a per-run measurement.
INCLUSION_DECIDED_BY = "call_graph_reachability_minus_ddg_refutation"

#: The granularity at which coverage is decided: ``function``. A capable
#: language still holds functions the def/use extractor does not fully model,
#: so the per-function coverage gate (wired to both walk arms) decides which
#: functions may refute, and the functions that forfeit are counted in the
#: emitted record rather than left to inference (history: WI-joluk).
#:
#: The test on this constant catches a change to the CONSTANT, not a change in
#: the code that makes it true or false: if the coverage gate is removed or
#: coarsened, update this value by hand — nothing will flag it (R16).
COVERAGE_GRANULARITY = "function"

#: Buckets in :func:`count_walk_verdicts` that are not one of taint's five
#: verdicts. ``mixed`` is a COLLAPSED row whose members disagreed: the scalar
#: ``walk_verdict`` is ``grp[0]``'s, and a collapsed group's members need not
#: agree, so attributing such a row to its first member would publish a
#: clean, plausible, entirely wrong breakdown — and in the flattering direction, since a row standing for
#: one confirmed and three escaped members would read as fully adjudicated.
#: ``unrecorded`` catches a finding whose verdict is ``""`` (deserialized from
#: a map written before the field existed) or a value this module does not
#: know; it is counted rather than dropped so the breakdown sums to the
#: finding count, which is the one arithmetic a reader can check.
WALK_VERDICT_MIXED = "mixed"
WALK_VERDICT_UNRECORDED = "unrecorded"


def count_walk_verdicts(findings: Iterable[Any]) -> dict[str, int]:
    """Break taint findings down by WHAT THE §3a WALK RETURNED.

    WHY THIS EXISTS: this module's founding argument applied to its own
    field. ``flows_removed_by_walk`` is published beside a
    ``findings_by_analysis_method`` rollup in which ``ddg_mixed`` collapses
    THREE verdicts (``refuted``, ``escaped``, ``not_attempted``), and only
    ``refuted`` can remove a flow. Without this breakdown a reader looking at
    a zero could not tell "the walk adjudicated and refuted nothing" from "the
    walk never got to look" — opposite meanings on identical evidence.

    It also publishes a DECLARED BLINDNESS, not a gap awaiting work: most
    points where the walk loses a tainted value are not calls at all, so no
    function summary can close them, and closing them is not pursued. A
    limitation that is accepted but not published becomes a silent one
    (history: INV-busis option (c)).

    Counts ROWS, on the same denominator as ``findings_by_analysis_method``:
    both describe findings the propagators produced, post-collapse.
    """
    counts: dict[str, int] = dict.fromkeys(sorted(WALK_VERDICTS), 0)
    counts[WALK_VERDICT_MIXED] = 0
    counts[WALK_VERDICT_UNRECORDED] = 0
    for finding in findings:
        values = tuple(getattr(finding, "walk_verdict_values", ()) or ())
        distinct = set(values)
        if len(distinct) > 1:
            counts[WALK_VERDICT_MIXED] += 1
            continue
        # ``next(..., "")`` rather than a fallback to the scalar: a real
        # finding's ``__post_init__`` derives the singleton, so an empty tuple
        # reaches here only from a duck-typed input that carries no verdict at
        # all -- and ``""`` is exactly the ``unrecorded`` cell below. Reading
        # the scalar as a second source would give one fact two homes for a
        # case that cannot occur, as an uncoverable branch.
        verdict = next(iter(distinct), "")
        if verdict in counts and verdict != WALK_VERDICT_MIXED:
            counts[verdict] += 1
        else:
            counts[WALK_VERDICT_UNRECORDED] += 1
    return counts

#: Which analysis methods honour a sanitizer called in the SAME function as the
#: taint source. Deciding that needs statement ordering inside the seed
#: function, so it is available to the DDG pass and structurally unavailable to
#: the call-graph one: "handler calls encrypt" and "handler calls write" are two
#: edges with no order between them, and the graph is identical whichever order
#: the source actually has. ``propagate_taint_structural`` therefore cannot ever
#: honour this shape, for any language — a scope limit, not a deferral, and one
#: that applies to most of the catalogue because every language without a
#: def/use extractor is served by that pass (WI-fasub). Declared as data with a
#: test on it so the claim cannot quietly outlive its truth (R16).
SAME_FUNCTION_SANITIZATION_HONOURED_BY = ("ddg",)

#: The capability bits, in the order a reader should fix them: a mapping is
#: prerequisite to declaring atomic statements, which is prerequisite to an
#: extractor producing anything, which is prerequisite to a spec being useful.
_CAPABILITY_BITS = (
    "cfg_mapping",
    "atomic_statement",
    "def_use_extractor",
    "ddg_spec",
)


@dataclass(frozen=True)
class LanguageDataflowScope:
    """One language's data-flow capability, with the catalog it would serve.

    The catalog counts are here rather than in a sibling structure because the
    number that matters to a reader is not "is this language covered" but "how
    many sinks does the uncovered language have" — an incapable language's
    sink count is the scope disclosure, and a bare capability boolean hides it.
    """

    language: str  # axis: language
    catalog_sources: int
    catalog_sinks: int
    catalog_sanitizers: int
    cfg_mapping: bool
    atomic_statement: bool
    def_use_extractor: bool
    ddg_spec: bool

    @property
    def dataflow_capable(self) -> bool:
        """True only when all four bits hold — see the module docstring."""
        return all(getattr(self, bit) for bit in _CAPABILITY_BITS)

    @property
    def blockers(self) -> tuple[str, ...]:
        """The bits that are missing, so the table says what to fix."""
        return tuple(bit for bit in _CAPABILITY_BITS if not getattr(self, bit))

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "catalog_sources": self.catalog_sources,
            "catalog_sinks": self.catalog_sinks,
            "catalog_sanitizers": self.catalog_sanitizers,
            "cfg_mapping": self.cfg_mapping,
            "atomic_statement": self.atomic_statement,
            "def_use_extractor": self.def_use_extractor,
            "ddg_spec": self.ddg_spec,
            "dataflow_capable": self.dataflow_capable,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class SanitizerScope:
    """What the sanitizer catalogue can say anything about — INV-karud (b).

    Clause (b) asks that a sanitizer on a route actually neutralize the flow.
    Verifying that is only meaningful for a taint label some sanitizer CLAIMS to
    transform, and the catalogue is far narrower than the source/sink one: every
    entry is cryptographic. So "0 sanitized flows" across a whole repository is
    ambiguous in the way L58 is about — it may mean nothing was protected, or it
    may mean nothing *could* be, because the claim's taint labels and the
    catalogue's input labels are disjoint sets. With an all-cryptographic
    catalogue the second reading is the common case, not a corner case.
    ``sanitizable_labels`` is what makes the two readable apart.
    """

    total: int
    languages: tuple[str, ...]
    taint_categories: tuple[str, ...]
    sanitizable_labels: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "languages": list(self.languages),
            "taint_categories": list(self.taint_categories),
            "sanitizable_labels": list(self.sanitizable_labels),
            "same_function_honoured_by": list(
                SAME_FUNCTION_SANITIZATION_HONOURED_BY
            ),
        }


def compute_sanitizer_scope(
    catalog: Any,
    languages: Iterable[str],
) -> SanitizerScope:
    """Summarise the sanitizer catalogue over the analyzed languages.

    Derived from the catalogue the propagators actually loaded, never from a
    hand-kept list — a second home for these counts would decay silently, and
    counting something the pipeline already classifies is the bug rather than a
    shortcut (L53).
    """
    entries = [
        san
        for language in sorted(set(languages))
        for san in catalog.sanitizers_for_language(language)
    ]
    with_any = sorted({
        language for language in set(languages)
        if catalog.sanitizers_for_language(language)
    })
    return SanitizerScope(
        total=len(entries),
        languages=tuple(with_any),
        taint_categories=tuple(sorted({
            f"{san.input_taint} -> {san.output_taint}" for san in entries
        })),
        sanitizable_labels=tuple(sorted({san.input_taint for san in entries})),
    )


#: What a healthy def/use registry looks like, learned from the first successful
#: registration in this process rather than hardcoded. Empty until then.
_EXPECTED_DEF_USE_LANGUAGES: frozenset[str] = frozenset()


def ensure_def_use_extractors_registered() -> bool:
    """Import the language def/use modules for their registration side effect.

    Named and centralised because the import list is a second home for a fact
    the filesystem already holds; ``test_ddg_language_wiring`` pins the two
    together. Both the DDG build and this module's scope computation route
    through here, so there is one home rather than two that can drift.

    Returns False when the language package is unavailable, in which case no
    language can be data-flow capable and the scope table says so honestly.

    RE-REGISTERS AFTER A CLEAR, which a bare import cannot do. Registration is an
    import SIDE EFFECT, so once a module sits in ``sys.modules`` the import here
    is a no-op and the decorators never re-run. Without the reload, a call made
    after ``clear_def_use_extractors()`` (several tests call it) would return
    ``True`` over an empty or partial registry, every affected language would
    report ``dataflow_capable: False``, and the result would depend on which
    tests shared an xdist worker. The guard belongs here, at the one place that
    owns the registration, rather than being copied into each test that trips
    over it.
    """
    import importlib

    from .cfg import registered_def_use_languages

    global _EXPECTED_DEF_USE_LANGUAGES
    try:
        modules = [
            importlib.import_module(name)
            for name in (
                "hypergumbo_lang_mainstream.c_def_use",
                "hypergumbo_lang_mainstream.go_def_use",
                "hypergumbo_lang_mainstream.java_def_use",
                "hypergumbo_lang_mainstream.py_def_use",
                "hypergumbo_lang_mainstream.rust_def_use",
                "hypergumbo_lang_mainstream.ts_def_use",
            )
        ]
    except ImportError:  # pragma: no cover - lang package is a hard dep
        return False

    # THE EXPECTED SET IS LEARNED, NOT LISTED. Hardcoding the language names
    # here would be a second home for a fact the decorators already hold — the
    # drift this module's docstring warns about — so the first successful
    # registration in the process records what a healthy registry looks like and
    # every later call checks against that.
    #
    # A SUBSET CHECK, NOT A NON-EMPTY CHECK. A caller that clears the registry
    # and reloads only its own module (``test_py_def_use.py`` does) leaves it
    # populated but missing other languages — so an emptiness test would report
    # health while, say, the JavaScript extractor is gone.
    #
    # THE BASELINE IS PRODUCED, NOT FOUND. Learning the expected set from
    # whatever happens to be registered on the first call has the same flaw one
    # level up: if an earlier caller imported the modules and then cleared the
    # registry, the first call would find a PARTIAL registry (the import is a
    # no-op once the module is in ``sys.modules``), record it as healthy, and
    # every later subset check would pass against the clipped baseline. The
    # first call therefore reloads unconditionally and learns from the state
    # it produced itself.
    if not _EXPECTED_DEF_USE_LANGUAGES:
        for module in modules:
            importlib.reload(module)
        _EXPECTED_DEF_USE_LANGUAGES = registered_def_use_languages()
        return True
    current = registered_def_use_languages()
    if not _EXPECTED_DEF_USE_LANGUAGES <= current:
        for module in modules:
            importlib.reload(module)
    return True


def compute_dataflow_scope(
    catalog: Any,
    languages: Iterable[str],
) -> list[LanguageDataflowScope]:
    """Build one scope row per language, reading production registries only.

    ``catalog`` is a :class:`~hypergumbo_core.taint.TaintCatalog`; its own
    ``*_for_language`` accessors supply the counts, so the table cannot drift
    from what propagation actually loaded (L53).
    """
    ensure_def_use_extractors_registered()
    specs = registered_ddg_languages()
    rows: list[LanguageDataflowScope] = []
    for language in sorted(set(languages)):
        mapping = load_cfg_mapping(language)
        rows.append(LanguageDataflowScope(
            language=language,
            catalog_sources=len(catalog.sources_for_language(language)),
            catalog_sinks=len(catalog.sinks_for_language(language)),
            catalog_sanitizers=len(catalog.sanitizers_for_language(language)),
            cfg_mapping=mapping is not None,
            atomic_statement=bool(mapping and mapping.atomic_statements),
            def_use_extractor=get_def_use_extractor(language) is not None,
            ddg_spec=language in specs,
        ))
    return rows


_EMPTY_SANITIZER_SCOPE = SanitizerScope(
    total=0, languages=(), taint_categories=(), sanitizable_labels=(),
)


def dataflow_scope_dict(
    rows: Sequence[LanguageDataflowScope],
    findings_by_analysis_method: Mapping[str, int],
    sanitizer_scope: SanitizerScope | None = None,
    flows_removed_by_walk: int = 0,
    walk_verdicts: Mapping[str, int] | None = None,
    functions_walkable: int = 0,
    functions_forfeited: int = 0,
) -> dict[str, Any]:
    """The machine-readable scope block.

    ``findings_by_analysis_method`` counts every taint finding the propagators
    produced, BEFORE claim filtering. That denominator is deliberately
    different from a verdict's own ``analysis_methods``, which counts only the
    flows that verdict rests on: one describes the analysis, the other
    describes a claim. Two numbers for two questions, each with its denominator
    stated, rather than one number a reader has to guess the scope of (L60).
    """
    counts = dict(findings_by_analysis_method)
    return {
        "inclusion_decided_by": INCLUSION_DECIDED_BY,
        "coverage_granularity": COVERAGE_GRANULARITY,
        # What the per-function granularity above actually COSTS. A function
        # lands in the forfeited count when the CFG's recorded statement
        # extents miss code in its body — the extractor demonstrably did not
        # see part of it — so its walk may not refute. The forfeit rate can be
        # a large share of walkable functions, and it decides how much a clean
        # verdict is worth. Emitted as a PAIR because a bare count is
        # unreadable: 400 forfeits out of 800 and out of 40,000 are different
        # facts. Zero-filled like every other key here (history: WI-mugop).
        "functions_walkable": functions_walkable,
        "functions_forfeited": functions_forfeited,
        "languages": [row.to_dict() for row in rows],
        "findings_by_analysis_method": counts,
        "findings_total": sum(counts.values()),
        # How many flows the §3a walk REFUTED and removed this run.
        # Emitted always, and zero is a real answer: the alternative is a
        # security tool whose findings can silently be fewer than the analysis
        # produced, with no number a reader could check. ``findings_total``
        # counts what SURVIVED, so the two together say what the walk did.
        "flows_removed_by_walk": flows_removed_by_walk,
        # What makes the number ABOVE readable: only ``refuted``
        # can remove a flow, so a zero beside a large ``escaped`` /
        # ``not_attempted`` count means the walk never got to look. Always
        # present and fully zero-filled, for the same reason as every other key
        # here. See :func:`count_walk_verdicts`.
        "walk_verdicts": dict(
            walk_verdicts if walk_verdicts is not None
            else count_walk_verdicts(())
        ),
        # Always present, like every other key here: a disclosure that appears
        # only when it has something to say teaches a consumer to treat its
        # absence as "not applicable" rather than "zero".
        "sanitizer_scope": (
            sanitizer_scope or _EMPTY_SANITIZER_SCOPE
        ).to_dict(),
    }


def render_dataflow_scope_text(
    rows: Sequence[LanguageDataflowScope],
    findings_by_analysis_method: Mapping[str, int],
    sanitizer_scope: SanitizerScope | None = None,
    flows_removed_by_walk: int = 0,
    walk_verdicts: Mapping[str, int] | None = None,
    functions_walkable: int = 0,
    functions_forfeited: int = 0,
) -> list[str]:
    """The same scope, for the text view. Empty when nothing was analyzed.

    Text mode gets this because a disclosure that exists only in ``--json`` is
    half shipped: a text-mode reader never sees it.
    """
    if not rows:
        return []

    counts = dict(findings_by_analysis_method)
    total = sum(counts.values())
    width = max(len(row.language) for row in rows)
    lines = [
        "",
        f"Data-flow coverage (ADR-0017 §3a) for the {len(rows)} analyzed "
        f"language(s) with a taint catalog:",
    ]
    for row in rows:
        if row.dataflow_capable:
            verdict = "yes"
        else:
            verdict = f"NO (missing: {', '.join(row.blockers)})"
        lines.append(
            f"  {row.language.ljust(width)}  "
            f"sources {row.catalog_sources}, sinks {row.catalog_sinks}  "
            f"data-flow machinery wired: {verdict}"
        )
    breakdown = ", ".join(
        f"{count} {name}" for name, count in sorted(counts.items())
    ) or "none"
    lines.append(f"  Taint findings by analysis method ({total} total): {breakdown}.")
    lines.append(
        "  Flow INCLUSION rests on call-graph reachability for every finding "
        "above, so a 'ddg' label corroborates a flow rather than deciding it. "
        "The walk can now SUBTRACT (ADR-0017 §3a removal authority): a flow is "
        "dropped only where the walk exhausted every route inside one function "
        "and found no dependence. A walk that LOST the value removes nothing, "
        "and no flow is ever added by the walk."
    )
    lines.append(
        f"  Flows REMOVED by the walk this run: {flows_removed_by_walk}. "
        "Counted separately from the total above, which counts survivors — a "
        "removal a reader cannot see is the failure mode this number exists "
        "to prevent."
    )
    verdicts = dict(
        walk_verdicts if walk_verdicts is not None else count_walk_verdicts(())
    )
    lines.append(
        "  §3a walk verdicts for those findings: "
        + ", ".join(f"{name} {count}" for name, count in sorted(verdicts.items()))
        + "."
    )
    lines.append(
        "  Only a 'refuted' verdict can REMOVE a flow: 'escaped' and "
        "'not_attempted' are ignorance, not absence of dependence. So a "
        "removal count of 0 beside a large 'escaped' or 'not_attempted' count "
        "means the walk did not get to look — NOT that it looked and found "
        "nothing. Closing those escapes is a RETIRED goal (INV-busis, "
        "2026-09-09): most of them are not calls at all, so no function "
        "summary can close them. This is a declared limitation, not work in "
        "progress."
    )
    lines.append(
        f"  Coverage is reported per {COVERAGE_GRANULARITY}: 'wired' means the "
        "machinery runs for that language, NOT that every function in it is "
        "modelled (Go type switches and select statements, for one, are "
        "walked as straight-line code)."
    )
    if functions_walkable:
        _pct = 100.0 * functions_forfeited / functions_walkable
        lines.append(
            f"  {functions_forfeited} of {functions_walkable} analysed "
            f"functions ({_pct:.1f}%) may not refute a flow: the CFG did not "
            "record every construct in their bodies, so an exhausted walk over "
            "them is not evidence a flow is absent."
        )

    scope = sanitizer_scope or _EMPTY_SANITIZER_SCOPE
    categories = ", ".join(scope.taint_categories) or "none"
    lines.append(
        f"  Sanitizers: {scope.total} entr(ies) across "
        f"{len(scope.languages)} language(s), covering {categories}. A flow "
        "whose taint label is not one of those CANNOT be reported sanitized, "
        "so a zero there means 'not expressible', not 'not protected'."
    )
    lines.append(
        "  A sanitizer called in the SAME function as the source is honoured "
        f"only by: {', '.join(SAME_FUNCTION_SANITIZATION_HONOURED_BY)}. The "
        "call-graph pass cannot order two calls that share a caller, so for a "
        "language with no def/use extractor that shape reports UNSANITIZED "
        "even when the code protects it."
    )
    return lines
