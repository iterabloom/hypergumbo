# SPDX-License-Identifier: AGPL-3.0-or-later
"""Published data-flow coverage scope for taint output — INV-karud clause (a3).

WHY THIS EXISTS. The clause reads: "The output must state which flows were
adjudicated by data flow and which by call reachability alone. A reader must be
able to tell the two apart from the emitted record; the scope of data-flow
coverage may not be left to assumption." Per-flow that is
``TaintFlowFinding.analysis_method``. What no surface carried was the *scope* —
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
the walk silently inert — Rust shipped an extractor in March and produced zero
DDG edges for months while failing three of the four at once. Reporting a
single "supported?" boolean would have said "no" for Rust the whole time
without ever saying which of the four to fix, and reporting only the CFG
mapping would say "yes" for Java, which has one and can adjudicate nothing.

REGISTRATION IS THE TRAP THIS MODULE MUST NOT FALL INTO. Both registries are
populated as an *import side effect* of the language modules. A scope table
computed before that import reports every language incapable — a clean,
plausible, entirely wrong answer of exactly the shape L53 is about. So
:func:`compute_dataflow_scope` forces registration itself rather than trusting
its caller to have done it, and ``test_dataflow_scope`` carries a non-vacuity
floor asserting the four registered languages read capable.

WHAT THIS MODULE DELIBERATELY DOES NOT CLAIM — I. Capability is reported per
LANGUAGE, and that is not a per-function completeness claim. ``cfg_nodes/go.yaml``
self-documents that ``if err := do(); err != nil`` initializers are invisible to
def/use — 700 of caddy's 6,596 ``if`` statements carry a call there — so Go reads
``dataflow_capable`` while containing functions the walk cannot see into, and a
reader who took the bit for coverage would be making exactly the assumption
clause (a3) forbids. ``coverage_granularity`` says so in the emitted record
rather than here, where no consumer would find it. The finer signal is WI-joluk's
(forfeit refutation for any function whose CFG statement extents miss a call node
in its body); when it lands, this constant becomes ``function``.

WHAT THIS MODULE DELIBERATELY DOES NOT CLAIM — II. ``inclusion_decided_by`` is a
constant, not a measurement. **It changed on 2026-09-02, which is the R16
trigger firing exactly as designed** — the previous text said that when §3a
gained refutation this constant must change or its test fails, and WI-kabif
granted §3a removal authority. It now reads
``call_graph_reachability_minus_ddg_refutation``, and the compound name is the
honest one because BOTH halves are still true: a flow is still *included* by
call-graph reachability, and the walk can now *remove* one it refutes.

The asymmetry matters more than the rename. The walk only ever SUBTRACTS: it
adds no flow that reachability did not already report, and it removes only on
``unconfirmed`` — the walk exhausted every route with nothing unexplained.
``escaped`` is ignorance and removes nothing. So reading ``analysis_method ==
"ddg"`` as "this flow's inclusion was decided by data flow" is STILL the
misreading INV-sadah exists for, and it has been made twice in this
repository's own history: a surviving ``ddg`` flow was included by
reachability and merely corroborated by the walk.

WHAT THIS MODULE DELIBERATELY DOES NOT CLAIM — III, and it is the argument at
the top of this docstring applied to this module's OWN removal count.
``flows_removed_by_walk`` is published beside a ``findings_by_analysis_method``
rollup in which ``ddg_mixed`` collapses THREE walk verdicts and ``structural``
covers a fourth (``unavailable`` — the walk ran but the DDG held no reaching-def
data for that flow's source function). Only ``unconfirmed`` can remove a flow.
So a bare ``0`` could mean "the walk adjudicated every flow and refuted none" or
"the walk never got to look at any of them", which is the same pair of opposite
meanings on identical evidence that this module exists to prevent for "0 precise
findings". :func:`count_walk_verdicts` publishes the finer axis so the zero is
readable; MEASURED on hypergumbo's own repository the first time it ran, the
answer was ``unavailable 224`` out of 224 — the DDG built 177,518 edges and
covered none of the flows' source functions, a fact the previous output had no
way to state.

That gap is now a DECLARED limitation rather than a work item: the owner retired
the goal of closing §3a's escape sites on 2026-09-09 (INV-busis option (c)),
because most of them are not calls at all and no function summary can ever reach
them. The capability is untouched — §3a still removes a flow it refutes — but
the coverage it would need to fire at scale is not being pursued, and a reader
is told so in the emitted record rather than discovering it from a zero.
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

#: The granularity at which capability is reported. ``language`` is a statement
#: of what is NOT claimed: a capable language still holds functions the def/use
#: extractor does not model (see the module docstring). Becomes ``function``
#: when WI-joluk's per-function coverage gate lands. Like
#: ``INCLUSION_DECIDED_BY``, this is a declared property with a test on it, so
#: the claim cannot quietly outlive its truth (R16).
COVERAGE_GRANULARITY = "language"

#: Buckets in :func:`count_walk_verdicts` that are not one of taint's five
#: verdicts. ``mixed`` is a COLLAPSED row whose members disagreed: the scalar
#: ``walk_verdict`` is ``grp[0]``'s, and measured on beads 63.9% of groups
#: holding a ``sink_before_source`` member are not unanimous, so attributing
#: such a row to its first member would publish a clean, plausible, entirely
#: wrong breakdown — and in the flattering direction, since a row standing for
#: one confirmed and three escaped members would read as fully adjudicated.
#: ``unrecorded`` catches a finding whose verdict is ``""`` (deserialized from
#: a map written before the field existed) or a value this module does not
#: know; it is counted rather than dropped so the breakdown sums to the
#: finding count, which is the one arithmetic a reader can check.
WALK_VERDICT_MIXED = "mixed"
WALK_VERDICT_UNRECORDED = "unrecorded"


def count_walk_verdicts(findings: Iterable[Any]) -> dict[str, int]:
    """Break taint findings down by WHAT THE §3a WALK RETURNED.

    WHY THIS EXISTS, and it is this module's own founding argument turned on
    its own field. ``flows_removed_by_walk: 0`` is published beside a
    ``findings_by_analysis_method`` rollup in which ``ddg_mixed`` collapses
    THREE verdicts — ``taint`` records a production split of 0 ``unconfirmed``
    / 14 ``escaped`` / 139 ``not_attempted``. Only ``unconfirmed`` can remove a
    flow. So a reader looking at a zero could not tell "the walk adjudicated
    and refuted nothing" from "the walk never got to look", which is exactly
    the pair of opposite meanings on identical evidence that this module was
    written to prevent for ``0 precise findings``.

    IT IS ALSO A DECLARED BLINDNESS, not a gap awaiting work. INV-busis
    measured that most points where the walk loses a tainted value are not
    calls at all (66.7-90.6%), so no function summary can ever close them, and
    the owner RETIRED the goal of closing them on 2026-09-09 (option (c):
    accept confirm-only permanently). Retiring a goal without publishing its
    consequence is how a limitation becomes a silent one — hence this.

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
        # Empty tuple only for a duck-typed input; a real finding's
        # ``__post_init__`` derives the singleton from the scalar.
        verdict = next(iter(distinct), None)
        if verdict is None:
            verdict = getattr(finding, "walk_verdict", "") or ""
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
    many sinks does the uncovered language have" — Java's 69 and JavaScript's
    83 are the scope disclosure, and a bare capability boolean hides them.
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
    catalogue's input labels are disjoint sets. That has been measured and it is
    the latter: a nine-repo cohort produced zero sanitized flows for exactly
    this reason. ``sanitizable_labels`` is what makes the two readable apart.
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
    is a no-op and the decorators never re-run — meaning this function returned
    ``True`` while the registry was empty and every language reported
    ``dataflow_capable: False``. That is not hypothetical: ``test_cfg.py`` calls
    ``clear_def_use_extractors()`` five times, and ``test_ddg_build.py`` already
    carries a hand-rolled ``importlib.reload`` guard in one test with a docstring
    explaining exactly this hazard. Every other consumer was unguarded, so
    whether a taint claim reported the JavaScript extractor as wired depended on
    which tests happened to share an xdist worker — a green suite and a red one
    from the same tree. The guard belongs here, at the one place that owns the
    registration, rather than being copied into each test that trips over it.
    """
    import importlib

    from .cfg import registered_def_use_languages

    global _EXPECTED_DEF_USE_LANGUAGES
    try:
        modules = [
            importlib.import_module(name)
            for name in (
                "hypergumbo_lang_mainstream.go_def_use",
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
    # A SUBSET CHECK, NOT A NON-EMPTY CHECK, and the difference is the whole
    # defect. ``test_py_def_use.py`` clears the registry and reloads only its own
    # module, leaving it populated but missing ``javascript`` — so an emptiness
    # test reports health while the JavaScript extractor is gone. That is exactly
    # how a full-suite run produced two JavaScript data-flow failures that every
    # smaller scope passed.
    current = registered_def_use_languages()
    if not current:
        for module in modules:
            importlib.reload(module)
        current = registered_def_use_languages()
    if not _EXPECTED_DEF_USE_LANGUAGES:
        _EXPECTED_DEF_USE_LANGUAGES = current
    elif not _EXPECTED_DEF_USE_LANGUAGES <= current:
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
        "languages": [row.to_dict() for row in rows],
        "findings_by_analysis_method": counts,
        "findings_total": sum(counts.values()),
        # WI-kabif. How many flows the §3a walk REFUTED and removed this run.
        # Emitted always, and zero is a real answer: the alternative is a
        # security tool whose findings can silently be fewer than the analysis
        # produced, with no number a reader could check. ``findings_total``
        # counts what SURVIVED, so the two together say what the walk did.
        "flows_removed_by_walk": flows_removed_by_walk,
        # INV-busis. What makes the number ABOVE readable: only ``unconfirmed``
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
) -> list[str]:
    """The same scope, for the text view. Empty when nothing was analyzed.

    Text mode gets this because a disclosure that exists only in ``--json`` is
    half shipped — WI-bifob's exclusion bucket reached the dataclass and never
    the text renderer, so a text reader of a violated claim never learned flows
    had been set aside. The same mistake is not worth making twice.
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
        "  Only an 'unconfirmed' verdict can REMOVE a flow: 'escaped' and "
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
        "modelled (Go if-statement initializers, for one, are not)."
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
