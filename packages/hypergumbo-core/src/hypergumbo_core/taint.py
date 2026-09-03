# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taint catalog loading and taint-flow propagation (ADR-0017 Phases 1-2).

Provides YAML-driven taint source/sink/sanitizer catalogs and a structural
(call-graph BFS) taint-flow analyzer. This is the Phase 1 fallback path
that works for all languages without requiring def/use extractors.

How It Works
------------
1. ``load_taint_catalog()`` reads YAML files defining taint sources (functions
   whose return values carry taint labels), sinks (functions that should not
   receive tainted data), and sanitizers (functions that transform taint).

2. ``propagate_taint_structural()`` performs two-phase BFS on the call graph:
   (a) compute nodes reachable from each taint source without passing through
   sanitizers for the relevant taint label, (b) check if any sink is in that
   reachable set. Reports violations as ``TaintFlowFinding`` objects.

The structural approach cannot distinguish between two variables in the same
function — it operates at the symbol level. Its findings are labeled
``confidence="approximate"`` and ``analysis_method="structural"`` per ADR-0017.

DDG-backed analysis has LANDED and is not the only producer any more: for
languages with def/use extractors, :func:`propagate_taint_ddg` (see
``ddg_build.py`` / ``taint_refine.py``) walks reaching-definitions and stamps
``analysis_method="ddg"`` when it confirms a dependence, or ``"ddg_mixed"``
when the walk ran without confirming one. So do NOT assume every finding
carries ``analysis_method="structural"`` — see the field's own docs below for
what each value licenses.

Catalog Format
--------------
Sources, sinks, and sanitizers use YAML files following patterns established
by the IO primitive catalogs (ADR-0016). See ``taint_sources/`` and
``taint_sanitizers/`` directories alongside this module, or project-local
catalogs provided via ``--taint-sources``, etc. Built-in sinks are derived
automatically from ``io_primitives/*.yaml`` — every write-side IO primitive
becomes an ``untrusted`` sink in a zone determined by its boundary category
(see :data:`AUTO_SINK_ZONE_MAP` below). Project-local sink overrides flow
through the ``--taint-sinks`` CLI flag.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    Iterator,
    NamedTuple,
    Optional,
    TypeVar,
    Union,
)

import yaml

from .axis_meta_keys import call_family_edge_types
from .edge_types import is_grpc_rpc_implementation
from .io_boundary import (
    _UNRESOLVED_MODULE_PLACEHOLDERS_IO,
    call_site_modes,
    call_site_target_kinds,
    read_boundary_for_target_kind,
    strip_redundant_module_qualifier,
    target_kind_discriminated_primitives,
    target_kind_fallback_boundaries,
    target_kinds_cross_no_boundary,
)
from .ir import symbol_name_slot, symbol_path_slot

if TYPE_CHECKING:
    from .cfg import DdgEdge
    from .function_summaries import FunctionSummary


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaintSource:
    """A function/method/attribute whose return-or-read value carries a taint label.

    Attributes:
        taint_label: The taint category (e.g. "plaintext", "key_material",
            "host_secret", "untrusted_input").
        module: The module or class path (e.g. "cryptography.fernet", "os").
        name: The function/method/attribute name (e.g. "Fernet.decrypt", "environ").
        kind: One of "function", "method", or "attribute".  ``"attribute"``
            covers bare reads like ``os.environ`` or ``sys.argv``; pairs with
            ``module_attr_ref`` edges emitted by the language analyzer
            (WI-guhok for Python; WI-gapam follow-up for tree-sitter langs).
        return_tainted: Whether the return (or read) value is tainted.
        argument_tainted: Indices of arguments that become tainted (optional).
        start_at: BFS seed origin. Default ``"caller"`` (original semantics)
            seeds at the function that *calls* a source — appropriate for
            value-tainting sources like ``os.environ.get`` or
            ``Fernet.decrypt``. ``"callee"`` seeds at the source-callee
            symbol itself, which models reachability-from-entry semantics
            for synthetic entry-point sources: a CLI handler like
            ``cmd_sketch`` declared with ``start_at: callee`` taints every
            symbol reachable downstream of itself, not every symbol
            reachable downstream of its dispatcher. Project-local
            catalogs declaring runtime/extras/dev CLI handlers as sources
            use ``"callee"`` so reachability is precisely scoped.
    """

    taint_label: str
    module: str
    name: str
    kind: str  # "function", "method", or "attribute"
    return_tainted: bool = True
    argument_tainted: tuple[int, ...] = ()
    start_at: str = "caller"  # "caller" or "callee"
    # WI-vazal: the io_primitives boundary this source was auto-derived from,
    # drawn from io_boundary.CATALOG_BOUNDARY_TYPES. Empty string for a
    # YAML-DECLARED source (crypto, key_material, project-local catalogs),
    # which has no boundary because it did not come from an io_primitives
    # entry.
    #
    # WHY IT IS CARRIED. AUTO_SOURCE_LABEL_MAP collapses THREE boundaries —
    # net_recv, ipc_recv and db_read — into the single label
    # `untrusted_input`, and until now discarded which one it was. So "a
    # request body reached the database" and "a row read from the database
    # reached the database" were the same fact downstream, and on an
    # ORM-backed application the second dominates: 93 of the 100 displayed
    # rows on pretix's largest violated claim are database-read to
    # database-write. Keeping the boundary makes them separable WITHOUT
    # changing the label, so no already-published claim changes meaning.
    source_boundary: str = ""
    # WI-lipis: non-empty only for a source derived from a primitive whose
    # boundary the STREAM ARGUMENT decides -- c's ``stdio.fgets`` is declared
    # under fs_read AND ipc_recv, and which one is true depends on what the
    # third argument was bound from. The sink side has carried the same idea as
    # ``requires_mode`` since INV-rusof; this is that idea on the axis no mode
    # literal can answer.
    #
    # WHY A SOURCE NEEDS IT AT ALL, given that ``_narrow_by_target_kind`` already
    # picks the row for io-boundaries. Only the MINTING row survives derivation
    # here -- ``fs_read`` is deliberately absent from AUTO_SOURCE_LABEL_MAP --
    # so the fs_read row simply does not become a TaintSource, and the ipc_recv
    # one would then match ``fgets`` by NAME at every call site, including the
    # file reads the boundary tagger correctly classified fs_read. That is one
    # fact with two homes and the second silently winning; measured on the c
    # repro before this field existed, ``fgets`` over a ``popen`` handle minted
    # untrusted_input while ``io-boundaries`` tagged the same edge fs_read.
    #
    # Empty means UNCONDITIONAL, which is what every other source is and must
    # stay: ``scanf`` reads stdin whatever its arguments say.
    requires_target_kind: str = ""
    # INV-minol: True on the ONE entry of a target-kind-gated primitive that
    # an ABSTAINING stamp (absent, unknown, or disagreeing across collapsed
    # sites) still admits -- the entry derived from the row ``classify_call``
    # falls back to (INV-fatok's ``abstains_to``, else the first declared).
    # Without it the matcher admitted only unconditional entries on an
    # abstention, and an unstamped ``bufio.NewScanner`` -- fallback
    # ``ipc_recv`` on INV-bagok's own measurement -- minted nothing while
    # ``io-boundaries`` tagged the same edge ``ipc_recv``: one catalogue, two
    # answers, 19 lost sources on six repositories. Set by the derivation from
    # the same reordered row list the classifier reads, never by hand.
    abstention_fallback: bool = False

    @property
    def qualified_name(self) -> str:
        """Full dotted name: module.name."""
        return f"{self.module}.{self.name}"


@dataclass(frozen=True)
class TaintSink:
    """A function/method/attribute that should not receive tainted data.

    Attributes:
        zone: The trust zone (e.g. "host_fs", "network", "host_env", "ipc",
            "browser_storage", "relay").
        trust_level: The trust level (e.g. "untrusted", "semi-trusted").
        module: The module or class path.
        name: The function/method/attribute name.
        kind: One of "function", "method", or "attribute".
        requires_mode: Non-empty only for a sink derived from a
            DUAL-CLASSIFIED I/O primitive, where the call's mode argument
            decides whether the boundary is crossed at all. ``builtins.open``
            is ``fs_read`` by default and ``fs_write`` only when handed
            ``"w"``/``"a"``/``"x"``, so its host_fs sink carries
            ``requires_mode="fs_write"`` and does not fire on ``open(p)``.

            Empty is the default and means UNCONDITIONAL, which is what the
            overwhelming majority of sinks are and must stay: ``os.remove``
            takes no mode, so gating it on mode evidence would delete every
            real deletion from the violation set — a false negative in a
            security gate, the expensive direction.
        requires_target_kind: WI-suhug, the mirror of ``TaintSource``'s field
            on the WRITE side. Non-empty only for a sink derived from a
            primitive declared under two or more write boundaries -- go's
            ``io.WriteString`` is ``fs_write`` to a file, ``ipc_send`` to a
            child's ``StdinPipe``, ``net_send`` to a connection and ``logging``
            to stderr, and which one depends on what its first argument was
            bound from. The stamp is resolved in the WRITE direction
            (``std_stream`` is ``logging`` here and ``ipc_recv`` for a source).
        abstention_fallback: INV-minol; see ``TaintSource``. True on the sink
            derived from the row an unstamped call falls back to, so that an
            abstention keeps TODAY'S sink (``host_fs`` for ``io.WriteString``,
            ``logging`` for ``fmt.Fprintf``) instead of deleting the finding.
    """

    zone: str
    trust_level: str
    module: str
    name: str
    kind: str  # "function", "method", or "attribute"
    requires_mode: str = ""
    requires_target_kind: str = ""
    abstention_fallback: bool = False

    @property
    def qualified_name(self) -> str:
        """Full dotted name: module.name."""
        return f"{self.module}.{self.name}"


@dataclass(frozen=True)
class TaintSanitizer:
    """A function that transforms one taint label into another.

    Attributes:
        input_taint: The taint label consumed (e.g. "plaintext").
        output_taint: The taint label produced (e.g. "ciphertext").
        qualified_name: Full dotted name (e.g. "cryptography.fernet.Fernet.encrypt").
    """

    input_taint: str
    output_taint: str
    qualified_name: str
    #: INV-pojib: True when this entry came from a PROJECT-LOCAL catalogue
    #: (``--taint-sanitizers`` or a claims-file ``extra_catalogs:`` block)
    #: rather than the shipped one. Stamped once where the user layer is
    #: assembled, so every consumer reads the same answer instead of trying to
    #: re-derive provenance from a path it no longer has.
    user_supplied: bool = False

    @property
    def short_name(self) -> str:
        """Extract the shortest unambiguous suffix.

        For dotted names (Python style), takes the last two segments:
        "cryptography.fernet.Fernet.encrypt" → "Fernet.encrypt"

        For double-colon names (Rust style), takes the last segment:
        "aes_gcm::Aes256Gcm::encrypt" → "encrypt"
        """
        if "::" in self.qualified_name:
            return self.qualified_name.rsplit("::", 1)[-1]
        parts = self.qualified_name.rsplit(".", 2)
        if len(parts) >= 2:
            return f"{parts[-2]}.{parts[-1]}"
        return self.qualified_name


# Catalog entries that flow through the callee index and the user-override
# merge. Both expose the (module, name, kind) triple those helpers key on.
# Deliberately NOT widened to TaintSanitizer: every call site passes a
# source or sink list, and widening would claim a capability untested here.
TaintEntry = Union[TaintSource, TaintSink]

# The user-override merge is type-PRESERVING: hand it sources and you get
# sources back. Widening its return to TaintEntry would let a merged
# source+sink mapping be assigned onto a sources-only catalog field.
TEntry = TypeVar("TEntry", bound=TaintEntry)


def _qualify(module: str, name: str) -> str:
    """``module.name``, or the bare name when the entry declares no module.

    A YAML-declared source may have no module at all, and ``".remove"`` is a
    lookup key that matches nothing — worse than the bare name, because it
    looks qualified.
    """
    return f"{module}.{name}" if module else name


# ---------------------------------------------------------------------------
# INV-zidur: what the ADR-0017 §3a walk RETURNED, as its own fact.
# ---------------------------------------------------------------------------
# ``analysis_method`` answers "which analysis produced this finding". It was
# also being asked "and what did the walk conclude", and it cannot: the call
# site collapses the walk's three-valued result with ``is True``, and the label
# is then chosen on ``fn_has_ddg`` — *did the DDG cover the source function* —
# so everything that is not a confirmation lands on ``ddg_mixed``.
#
# BIGGER THAN THE FILING. INV-zidur names two facts under that label (the walk
# returned False vs the walk returned None). Re-derived at the call site there
# are THREE, and the third is the one that matters most for pricing: every
# guard above the walk (same-function, recorded sink call lines, a tracked
# source def, sink lexically after source) can fail with ``fn_has_ddg`` still
# true, and the finding is stamped ``ddg_mixed`` anyway. So ``ddg_mixed`` today
# means "the DDG covered the function", and inside it live *refuted*, *escaped*
# AND *never ran*. Pricing §7a as "drop every ddg_mixed" therefore proposes to
# remove findings on the authority of a walk that did not execute — which is
# what WI-kabif's pre-registered tripwire caught (26.8% measured against 18%
# predicted).
#
# A SEPARATE FIELD, NOT A FOURTH METHOD VALUE. The precedent INV-zidur cites —
# splitting ``structural`` out of ``ddg_mixed`` — separated two different
# ANALYSES. This separates one analysis's RESULTS, a different axis; folding a
# verdict into a method name would repeat the one-name-two-facts shape being
# fixed, and would silently invalidate every published use of ``ddg_mixed``
# (docs/measurements/0006, docs/VERIFY-CLAIMS-SCOPE.md).
#
# NOTHING IS ACTED ON *BY THIS FIELD*. §3a gained removal authority on
# 2026-09-02 (WI-kabif) and now acts on ``walk_verdict``; this one is still
# recorded only, so the addressable domain can be MEASURED rather than
# upper-bounded. No verdict moves because of ``walk_blocked_by``.
#: Why the §3a walk did not run, when the DDG DID cover the source function.
#: The measurement that made this necessary: across the 11 cohort repositories
#: that carry a ``ddg_mixed`` finding, 153 such rows split 0 ``unconfirmed`` /
#: 14 ``escaped`` / 139 ``not_attempted``. Nothing rests on a walk that ran and
#: refuted, so "which guard stopped it" is the only question left that can
#: price a remedy, and answering it from outside the code would mean a second
#: copy of the guard conditions — the disagreeing-copies shape this module has
#: paid for repeatedly.
WALK_BLOCKED_CROSS_FUNCTION: str = "cross_function"
WALK_BLOCKED_NO_SOURCE_CALL_LINE: str = "no_source_call_line"
WALK_BLOCKED_NO_SINK_CALL_LINE: str = "no_sink_call_line"
WALK_BLOCKED_SOURCE_NOT_TRACKED: str = "source_not_tracked"
WALK_BLOCKED_SINK_BEFORE_SOURCE: str = "sink_before_source"

WALK_BLOCKERS: frozenset[str] = frozenset({
    WALK_BLOCKED_CROSS_FUNCTION,
    WALK_BLOCKED_NO_SOURCE_CALL_LINE,
    WALK_BLOCKED_NO_SINK_CALL_LINE,
    WALK_BLOCKED_SOURCE_NOT_TRACKED,
    WALK_BLOCKED_SINK_BEFORE_SOURCE,
})

WALK_VERDICT_CONFIRMED: str = "confirmed"
WALK_VERDICT_UNCONFIRMED: str = "unconfirmed"
WALK_VERDICT_ESCAPED: str = "escaped"
WALK_VERDICT_NOT_ATTEMPTED: str = "not_attempted"
WALK_VERDICT_UNAVAILABLE: str = "unavailable"

WALK_VERDICTS: frozenset[str] = frozenset({
    WALK_VERDICT_CONFIRMED,
    WALK_VERDICT_UNCONFIRMED,
    WALK_VERDICT_ESCAPED,
    WALK_VERDICT_NOT_ATTEMPTED,
    WALK_VERDICT_UNAVAILABLE,
})


def walk_verdict_for(
    reached: bool | None, *, ran: bool, covered: bool,
) -> str:
    """Name what the §3a walk did, from its result and whether it ran at all.

    ``reached`` is :func:`_ddg_taint_reaches`'s three-valued return, whose
    discipline is already documented there: ``True`` found a dependence,
    ``False`` exhausted every route with nothing unexplained, ``None`` means
    the value escaped tracked ground on some route. The two negatives are NOT
    interchangeable — "I looked everywhere and it is not there" and "I lost
    track of it" license opposite actions, and INV-busis measures escapes as
    common (86.5% of production escape sites are not a call statement node).

    ``ran`` and ``covered`` carry what ``reached`` cannot say, because the walk
    is guarded: ``covered`` is ``fn_has_ddg`` (the DDG held reaching-def data
    for this flow's source function at all) and ``ran`` is whether the
    preconditions above the call were met. ``ran`` implies ``covered``; the
    remaining pairing is not a state the caller can produce, and is resolved in
    the informative direction rather than by an assertion that would turn a
    labelling question into a crash.
    """
    if ran:
        if reached is True:
            return WALK_VERDICT_CONFIRMED
        if reached is False:
            return WALK_VERDICT_UNCONFIRMED
        return WALK_VERDICT_ESCAPED
    return (
        WALK_VERDICT_NOT_ATTEMPTED if covered else WALK_VERDICT_UNAVAILABLE
    )


def method_for_walk_verdict(verdict: str) -> str:
    """The PUBLISHED ``analysis_method`` name for a walk verdict.

    One function so the two vocabularies cannot drift: the verdict is the finer
    axis and the method is the coarse one every published document already
    reads. Deliberately many-to-one — three verdicts share ``ddg_mixed``, which
    is precisely the collapse INV-zidur exists to make visible WITHOUT changing
    what the coarse name means to an existing consumer.
    """
    if verdict == WALK_VERDICT_CONFIRMED:
        return "ddg"
    if verdict == WALK_VERDICT_UNAVAILABLE:
        return "structural"
    return "ddg_mixed"


@dataclass
class TaintFlowFinding:
    """A reported taint-flow violation or confirmed path.

    Attributes:
        taint_label: The taint category that flowed to the sink.
        source_symbol: Symbol ID of the function containing the taint source.
        source_primitive: Name of the taint source function.
        sink_symbol: Symbol ID of the sink function call.
        sink_primitive: Name of the sink function.
        source_module / sink_module: The MODULE declared by the catalog entry
            that matched, which is not recoverable from the emitted symbol.
            An emitted symbol may record a package (``go:net/http:0-0:Do``)
            where the catalog records package.Type (``net/http.Client``);
            without both, a reader cannot tell a correct match from a
            short-name collision without re-running the matcher.
        sink_zone: Trust zone of the sink.
        sanitized: Whether all paths from source to sink are sanitized.
        confidence: ``precise`` only where the ADR-0017 §3a walk actually
            confirmed a data dependence; ``approximate`` everywhere else. NOT
            "DDG-backed" — running the DDG is not the same as having used it,
            and stamping ``precise`` on the strength of a walk whose result was
            discarded is exactly what INV-sadah was filed for.
        analysis_method: ``structural`` (the DDG held no reaching-def data
            for THIS FLOW'S SOURCE FUNCTION — the language has no def/use
            extractor, or that particular function was not analyzed — so no
            walk was possible), ``ddg`` (the walk ran and confirmed a
            dependence), or ``ddg_mixed`` (the walk ran and did not confirm
            one, so inclusion rests on call-graph reachability). ``confidence``
            collapses the last two into ``approximate``; this is the finer
            axis, because "the analysis looked and found nothing" and "the
            analysis could not look" are different facts. The discriminant is
            per FUNCTION. It was effectively per REPO until INV-karud (a3):
            the CLI chose a propagator once for the whole repository, so this
            field reported on which languages happened to be present rather
            than on the flow it describes.
        path: Symbol IDs along ONE call-graph route from source to sink —
            a witness, not "the path". A source frequently reaches a sink by
            several equally-valid routes and this reports one of them; the
            others are real and are not listed. Which one is a DECLARED
            tie-break (BFS visiting each node's callees in sorted id order),
            not an artifact of iteration order: adjacency is set-valued, so
            before INV-havos the winner followed ``str`` set iteration and
            therefore ``PYTHONHASHSEED``, and two runs of the same binary on
            an unchanged repo reported different middle hops for the same
            flow. Reproducible now, but still one witness — a consumer that
            needs "every route" must not read this field as if it were that.
    """

    taint_label: str
    source_symbol: str
    source_primitive: str
    sink_symbol: str
    sink_primitive: str
    sink_zone: str
    sanitized: bool
    confidence: str  # "approximate" or "precise"
    # "structural" / "ddg" / "ddg_mixed" — ddg_mixed was assigned by the
    # propagator while this comment named only two values, so a reader
    # enumerating the vocabulary from here got it wrong.
    analysis_method: str
    path: list[str] = field(default_factory=list)
    # INV-pojib: WHICH sanitizer made this flow safe, and whether the analysed
    # repository is the one that said so. ``sanitized`` was a bare bool, so a
    # verdict could report "a sanitizer protects this route" without being able
    # to say whose sanitizer — and a repo-supplied entry naming a no-op function
    # took a measured `violated` rc 1 to `confirmed` rc 0 with byte-identical
    # verdict text. Both tuples name QUALIFIED sanitizer names;
    # ``sanitized_by_user_supplied`` is the subset that came from a
    # project-local catalogue, kept as its own tuple rather than a bool so a
    # verdict can mark the repo-supplied ones individually when a route crosses
    # several. Empty whenever ``sanitized`` is False.
    sanitized_by: tuple[str, ...] = ()
    sanitized_by_user_supplied: tuple[str, ...] = ()
    source_module: str = ""
    sink_module: str = ""
    # WI-vazal: the io_primitives boundary the SOURCE was derived from
    # (io_boundary.CATALOG_BOUNDARY_TYPES), carried through from the matched
    # TaintSource. Empty for a YAML-declared source, which has no boundary.
    # Lets a consumer separate "data off the wire reached the database" from
    # "a row read from the database reached the database" without either
    # flow's taint_label changing — so no published claim changes meaning.
    source_boundary: str = ""
    #: INV-zidur: WHAT THE §3a WALK RETURNED, which is a different fact from
    #: WHICH analysis ran (``analysis_method``). One of :data:`WALK_VERDICTS`,
    #: or ``""`` for a finding deserialized from a map written before the field
    #: existed. ``ddg_mixed`` covers THREE of these — ``unconfirmed`` (the walk
    #: exhausted every route and found no dependence), ``escaped`` (the value
    #: left tracked ground, so the walk knows nothing) and ``not_attempted``
    #: (the DDG covered the function but a guard above the call was not met, so
    #: the walk never executed) — and treating them alike is what made ADR-0017
    #: §7a's removal authority impossible to price: dropping every ``ddg_mixed``
    #: removes findings on the authority of a walk that did not run.
    #:
    #: ACTED ON SINCE 2026-09-02 (WI-kabif). This field WAS recorded-only,
    #: and the note here said so; §3a now removes a flow whose verdict is
    #: ``unconfirmed`` -- and ONLY that one, since ``escaped`` is ignorance.
    #: A consumer reading this field is therefore reading the thing that
    #: decides removal, not a label beside it.
    walk_verdict: str = ""
    #: INV-zidur: the FIRST guard that stopped the §3a walk, for a finding whose
    #: ``walk_verdict`` is ``not_attempted``. One of :data:`WALK_BLOCKERS`;
    #: ``""`` for every other verdict, where the question does not arise.
    #:
    #: FIRST, not all: the guards are evaluated in a fixed order and reported in
    #: that order, so the counts partition the population rather than
    #: double-counting a flow that fails several. A remedy for the top blocker
    #: therefore promotes flows to the NEXT one rather than to the walk, and a
    #: reader must not add the categories up as if each were independently
    #: addressable.
    walk_blocked_by: str = ""
    #: INV-muhij Finding A: what the WHOLE GROUP reported, for a collapsed row.
    #:
    #: The two scalars above describe ONE finding. On a collapsed row they are
    #: ``grp[0]``'s and nothing recorded what the other members said, so a row
    #: printed ``walk_blocked_by: sink_before_source`` while standing for
    #: members whose walk ran, or was blocked somewhere else entirely. Measured
    #: on beads through the production path: 208 groups contain a
    #: ``sink_before_source`` member, 133 of them (63.9%) are NOT unanimous,
    #: and only 349 of the 1,073 members under them (32.5%) carry it.
    #:
    #: THIS IS A PRECONDITION, NOT A REMEDY. No verdict moves because of
    #: these two union fields -- §3a's removal authority (WI-kabif) reads the
    #: scalar ``walk_verdict``, never these. But INV-muhij's remedy —
    #: stop a ``sink_before_source`` row from carrying a verdict alone — has to
    #: read a fact that is true of everything the row stands for, and the
    #: scalar is not one. A rule may act on ``walk_blocked_by_values ==
    #: ("sink_before_source",)``; it may not act on the scalar.
    #:
    #: UNIONED LIKE THEIR FIVE SIBLINGS in :func:`collapse_unadjudicated_flows`,
    #: and NEVER EMPTY for the same reason the primitive tuples are not:
    #: ``__post_init__`` derives the singleton. ``""`` is a VALUE here (the walk
    #: ran), not an absence — dropping it would make a mixed group read as
    #: unanimous, which is the exact misreading these fields exist to prevent.
    #:
    #: ``_values`` rather than a plural because ``walk_blocked_by`` does not
    #: pluralise, and because that is already this project's spelling for
    #: "the collapsed sites disagreed about a per-site key".
    walk_verdict_values: tuple[str, ...] = ()
    walk_blocked_by_values: tuple[str, ...] = ()
    #: INV-karud: THE AUTHORITATIVE STATEMENT OF WHAT THIS FINDING CLAIMS.
    #:
    #: The scalar ``source_primitive`` / ``sink_primitive`` / ``sink_symbol``
    #: fields above assert that ONE named primitive reached ONE other named
    #: primitive. For a flow the ADR-0017 §3a walk adjudicated
    #: (``analysis_method == "ddg"``) that claim is earned. For every other
    #: flow inclusion rests on call-graph reachability alone, and emitting one
    #: such finding per (source call site, sink call site) pair asserts n x m
    #: data dependences from a walk that established none of them. Measured on
    #: six repos: 359 reported flows describing 78 situations (4.60x), 78% of
    #: rows restating a situation already reported.
    #:
    #: So an unadjudicated finding is collapsed to one per situation —
    #: (taint_label, source_symbol, sink_zone, sanitized, source_boundary,
    #: analysis_method) — and these tuples carry the full sets it stands for:
    #: "symbol S reads {source_primitives} and reaches zone Z via
    #: {sink_primitives}". Names are MODULE-QUALIFIED (clause a1: a reader must
    #: be able to confirm the match by catalogue lookup, and the emitted symbol
    #: frequently does not carry the module the entry declared — WI-joruv).
    #:
    #: They are NEVER empty: ``__post_init__`` derives singletons from the
    #: scalars, so a hand-built finding is not silently primitive-less to the
    #: consumers that read the tuple. The scalars survive as the witness the
    #: ``path`` belongs to, and are always members of their tuple.
    source_primitives: tuple[str, ...] = ()
    sink_primitives: tuple[str, ...] = ()
    sink_symbols: tuple[str, ...] = ()
    #: Every sink CALL SITE this finding stands for, as ``(caller, callee)``
    #: pairs. NOT the callers alone and NOT the callees alone — INV-kakad was
    #: reopened once by recording only the caller, and the corrected shape is
    #: the pair because BOTH sides multiply:
    #:
    #: * shellcheck ``striptests`` — ONE callee (``redirect.>``) reached from
    #:   TWO callers (the file node, and ``sponge`` which it reaches). One
    #:   sink name, two sites.
    #: * kamaraflow ``train_script`` — FOUR callees (``open``, ``file.write``,
    #:   ``os.makedirs``, ``shutil.copyfile``) all called from ONE caller.
    #:   One caller, four sites.
    #:
    #: The record used to carry ``sink_symbols`` (callees) and nothing else, so
    #: neither multiplicity was visible and three independent refuters read
    #: ``collapsed_flow_count`` as unreconcilable against the repository. With
    #: the pairs emitted the multiplier is bounded by
    #: ``len(source_primitives) * len(sink_call_sites)``, and any shortfall is
    #: reachability having excluded a pair.
    sink_call_sites: tuple[tuple[str, str], ...] = ()
    #: How many (source call site, sink call site) pairs this finding stands
    #: for. 1 for an uncollapsed or adjudicated finding. Kept so the pair count
    #: stays available to a consumer that wants it rather than being traded
    #: away for the situation count.
    collapsed_flow_count: int = 1

    def __post_init__(self) -> None:
        """Derive the tuples from the scalars when a caller did not set them.

        A ``()`` default reads as "this finding names no primitives", which is
        never true — so the derivation lives here rather than at the call
        sites, where the two propagators, the collapse pass and every test
        fixture would each have to remember it (L53: a second home for one
        fact drifts immediately).
        """
        if not self.source_primitives:
            self.source_primitives = (
                _qualify(self.source_module, self.source_primitive),
            )
        if not self.sink_primitives:
            self.sink_primitives = (
                _qualify(self.sink_module, self.sink_primitive),
            )
        if not self.sink_symbols:
            self.sink_symbols = (self.sink_symbol,)
        # INV-muhij: ``not`` rather than ``is None`` would be wrong here in a
        # way it is not above — ``("",)`` is a legitimate derived value and
        # must not be re-derived on every construction, but ``()`` is the
        # "caller did not set it" default. ``len(...) == 0`` says exactly that.
        if len(self.walk_verdict_values) == 0:
            self.walk_verdict_values = (self.walk_verdict,)
        if len(self.walk_blocked_by_values) == 0:
            self.walk_blocked_by_values = (self.walk_blocked_by,)

    @property
    def verdict(self) -> str:
        """Return verdict string based on sanitization status."""
        return "confirmed_safe" if self.sanitized else "violated"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-friendly dict.

        ``source_module`` / ``sink_module`` / ``source_boundary`` ARE EMITTED,
        and used not to be. Those are precisely the three fields whose own
        docstrings justify them as *not recoverable from the emitted symbol* —
        the module a catalog entry declared (WI-joruv) and the io_primitives
        boundary the source came from (WI-vazal) — so dropping them here made a
        serialized finding strictly weaker than the object it came from. The
        verify-claims path survived only because it reaches the dataclass
        directly; any consumer of the serialized form silently lost the
        distinction WI-vazal shipped, which is the "data off the wire reached
        the database" versus "a row read from the database reached the
        database" split.
        """
        return {
            "taint_label": self.taint_label,
            "source_symbol": self.source_symbol,
            "source_primitive": self.source_primitive,
            "source_module": self.source_module,
            "sink_symbol": self.sink_symbol,
            "sink_primitive": self.sink_primitive,
            "sink_module": self.sink_module,
            "sink_zone": self.sink_zone,
            "source_boundary": self.source_boundary,
            "verdict": self.verdict,
            "sanitized": self.sanitized,
            "confidence": self.confidence,
            "analysis_method": self.analysis_method,
            # INV-zidur. The finer axis beside the coarse one: three different
            # walk outcomes share ``ddg_mixed``, and a JSON consumer that
            # cannot tell them apart cannot tell removal-on-knowledge from
            # removal-on-ignorance. Measured on the 0007 census, ``ddg_mixed``
            # is 0 unconfirmed / 14 escaped / 139 not_attempted.
            "walk_verdict": self.walk_verdict,
            "walk_blocked_by": self.walk_blocked_by,
            # INV-muhij Finding A: the scalars are the REPRESENTATIVE's. A
            # consumer deciding what a collapsed row is entitled to claim must
            # read the union, so serializing only the scalars would leave the
            # misreading in place for every JSON consumer.
            "walk_verdict_values": list(self.walk_verdict_values),
            "walk_blocked_by_values": list(self.walk_blocked_by_values),
            "path": self.path,
            # INV-pojib. Emitted because a JSON consumer asking "is this clean
            # verdict resting on something the analysed repo said about itself"
            # should not have to parse it out of the prose in ``details`` --
            # and because this module's own property test requires every
            # declared field to serialize, which is what caught them missing.
            "sanitized_by": list(self.sanitized_by),
            "sanitized_by_user_supplied": list(self.sanitized_by_user_supplied),
            # INV-karud: the sets are what the finding actually claims.
            # Serializing only the witness scalars would put the n x m
            # over-claim back for every consumer that reads JSON.
            "source_primitives": list(self.source_primitives),
            "sink_primitives": list(self.sink_primitives),
            "sink_symbols": list(self.sink_symbols),
            "sink_call_sites": [list(site) for site in self.sink_call_sites],
            "collapsed_flow_count": self.collapsed_flow_count,
        }


#: The two ``analysis_method`` values that mean "inclusion rests on call-graph
#: reachability, not on a confirmed data dependence". ``structural`` = no
#: reaching-def data existed for the source function so no walk was possible;
#: ``ddg_mixed`` = the walk ran and did not confirm one. Different facts, same
#: consequence for what the finding is entitled to claim.
UNADJUDICATED_METHODS = frozenset({"structural", "ddg_mixed"})


def collapse_unadjudicated_flows(
    findings: list[TaintFlowFinding],
) -> list[TaintFlowFinding]:
    """Collapse unadjudicated pair findings into one finding per situation.

    INV-karud. A finding whose ``analysis_method`` is in
    :data:`UNADJUDICATED_METHODS` was included because a call path exists, not
    because a data dependence was shown — so "primitive P1 reached primitive
    P2" is more than the analysis established. What it did establish is "symbol
    S reads {P1..Pn} and reaches zone Z via {Q1..Qm}", which is one fact rather
    than n x m of them.

    TWO MULTIPLIERS DIE HERE, and the second had not been named before it was
    measured (censused over six repos, 359 flows -> 78 situations, 4.60x):

    * **2.87x** the |sources| x |sinks| product inside one symbol. It scales
      with the CATALOGUE rather than the code — adding a seventh browser global
      adds rows to a repo whose source did not change.
    * **1.60x** distinct call-graph ROUTES to the same primitive pair, which
      are distinct rows because the consumer's flow identity keys on ``path``.
      ``path`` is documented as one witness route, not the route set, so a
      second witness was never a second fact.

    Grouping on neither primitive is what addresses both at once; a key built
    from the pair would leave the route multiplier standing.

    WHAT IS IN THE KEY, AND WHY EACH:

    ``taint_label``, ``source_symbol``, ``sink_zone``
        the situation itself — the unit a reader acts on ("is this function a
        problem?"), and the granularity a claim is written at.
    ``sanitized``
        a sanitized flow is excluded from the violation set. A group that mixed
        them would have to be counted both ways.
    ``source_boundary``
        WI-vazal split net_recv / ipc_recv / db_read inside the single
        ``untrusted_input`` label precisely so "a request body reached the
        database" and "a row read from the database reached the database" stay
        separable. Merging across it would undo that.
    ``analysis_method``
        ``ddg_mixed`` and ``structural`` are different facts about how hard the
        analysis looked, and INV-karud clause (a3) requires a reader to be able
        to tell them apart from the record.

    ADJUDICATED FLOWS PASS THROUGH UNTOUCHED. ``ddg`` means the walk confirmed
    a reaching-def chain from the variable the source defines to a use at the
    sink call site; that IS a pair claim, and it is 6 of 359 census flows.
    Collapsing it would trade earned precision for 1.7% of the noise.

    ORDER is first-appearance: each group is emitted where its first member
    was, adjudicated findings in place. Deterministic, and it keeps the
    consumer's "first five rows" rendering stable.

    THIS DOES NOT CHANGE ANY VERDICT. A claim verdict is a disjunction over its
    flows, and every filter the consumer applies — label, zone, ``sanitized``,
    and the production-scope test, which reads ``source_symbol`` and nothing
    else — tests a field that is in this key. So no group can be half-included,
    and existence is preserved exactly.
    """
    slots: list[TaintFlowFinding | None] = []
    at: dict[tuple[Any, ...], int] = {}
    members: dict[tuple[Any, ...], list[TaintFlowFinding]] = {}
    for f in findings:
        if f.analysis_method not in UNADJUDICATED_METHODS:
            slots.append(f)
            continue
        key = (
            f.taint_label, f.source_symbol, f.sink_zone,
            f.sanitized, f.source_boundary, f.analysis_method,
        )
        if key not in at:
            at[key] = len(slots)
            members[key] = [f]
            slots.append(None)
        else:
            members[key].append(f)

    for key, idx in at.items():
        grp = members[key]
        # ``replace`` rather than in-place assignment: this is a public
        # function and mutating findings the caller still holds is a side
        # effect the signature does not announce. ``__post_init__`` re-runs
        # and leaves the explicit tuples alone (it only fills EMPTY ones).
        slots[idx] = replace(
            grp[0],
            collapsed_flow_count=len(grp),
            source_primitives=tuple(sorted(
                {p for m in grp for p in m.source_primitives}
            )),
            sink_primitives=tuple(sorted(
                {p for m in grp for p in m.sink_primitives}
            )),
            sink_symbols=tuple(sorted(
                {sym for m in grp for sym in m.sink_symbols}
            )),
            # INV-kakad: unioned like its siblings, so the group names every
            # site it stands for and ``collapsed_flow_count`` can be checked
            # against |source_primitives| x |sink_call_sites|.
            sink_call_sites=tuple(sorted(
                {site for m in grp for site in m.sink_call_sites}
            )),
            # A route through a different barrier is a different sanitizer
            # credit, and INV-pojib requires the user-supplied ones to stay
            # individually nameable. Union, not the representative's tuple.
            sanitized_by=tuple(sorted(
                {b for m in grp for b in m.sanitized_by}
            )),
            sanitized_by_user_supplied=tuple(sorted(
                {b for m in grp for b in m.sanitized_by_user_supplied}
            )),
            # INV-muhij Finding A. The scalars stay ``grp[0]``'s -- they are
            # the witness the ``path`` belongs to -- but the row now also says
            # what the REST of the group reported, because a rule deciding
            # whether this row may carry a verdict alone has to read a fact
            # true of every member. 63.9% of the groups containing a
            # ``sink_before_source`` member are not unanimous.
            walk_verdict_values=tuple(sorted(
                {v for m in grp for v in m.walk_verdict_values}
            )),
            walk_blocked_by_values=tuple(sorted(
                {v for m in grp for v in m.walk_blocked_by_values}
            )),
        )
    return [s for s in slots if s is not None]


# ---------------------------------------------------------------------------
# Catalog container
# ---------------------------------------------------------------------------


# The two spellings the analyzers use for "receiver module could not be
# recovered". ADR-0017 §3a exempts both from module filtering; keeping them in
# one frozenset is what stops the pair drifting apart again — the live sink
# matcher tested only the bare spelling for months while the (never-wired)
# _sink_module_compatible tested both.
#: Re-exported from :mod:`io_boundary`, which is the canonical home (the two
#: consumers must not drift about what "no module" looks like).
_UNRESOLVED_MODULE_PLACEHOLDERS = _UNRESOLVED_MODULE_PLACEHOLDERS_IO


def _lookup_named_entry(
    hits: Sequence[TaintEntry] | None,
    callee_name: str,
    module_hint: str | None,
    ambiguous_names: frozenset[str],
    call_construct: str | None = None,
):
    """Pick the matching catalog entry from ``hits``, mirroring
    :meth:`io_boundary.IoBoundaryCatalog.lookup_with_module` (WI-razol).

    ``hits`` is the index bucket for ``callee_name`` (entries registered under
    that short OR qualified name); each entry exposes ``module`` / ``name`` /
    ``kind`` attributes.

    * No hits → ``None``.
    * With a usable module hint, filter to entries whose ``module`` matches
      (via :func:`io_boundary._module_matches`) and return the first match;
      if none match, return ``None`` — a present-but-mismatched module means
      this is not the catalogued primitive (e.g. ``sys.stdout.write`` is not
      ``asyncio.StreamWriter.write``; F156.A1).
    * With no usable module hint, delegate to the shared kind-aware gate
      (:func:`io_boundary.gate_named_entry`, io-boundary:F3): an untyped
      method call (``call_construct == "method"``) has no receiver evidence
      and never matches (INV-tapat/INV-maluk — ``str.replace`` must not match
      ``Path.replace``), a free-function call may match only a function-kind
      hit, and the ``ambiguous_names`` short-name set is retained as the
      meta-absent / non-Python safety net.

    A qualified ``callee_name`` (e.g. ``"os.replace"`` /
    ``"pathlib.Path.write_text"``) carries its own receiver evidence (the full
    module path), so an exact ``qualified_name`` match wins regardless of
    ambiguity OR kind — mirroring :meth:`lookup_with_module`'s qualified-name-
    first branch, which runs before its kind-aware gate (io-boundary:F3).
    """
    if not hits:
        return None
    # Both spellings of the unresolved-receiver placeholder are exempted, per
    # ADR-0017 §3a: when the analyzer could not recover module information,
    # degrade to short-name matching rather than reject, because rejecting
    # outright suppresses legitimate findings. `<external>` was missing here
    # (only the bare `external` was tested), so it fell into the module-FILTER
    # branch below and was compared as though it were a real module name —
    # matching nothing and silently dropping the finding. Harvested from the
    # retired `_sink_module_compatible`, which had it right.
    if module_hint and module_hint not in _UNRESOLVED_MODULE_PLACEHOLDERS:
        from .io_boundary import _module_matches
        for h in hits:
            if _module_matches(h.module, module_hint):
                return h
        return None
    # Exact qualified-name match carries its own receiver evidence — allow it
    # before the kind-aware no-module gate (parity with lookup_with_module's
    # qualified-name-first branch).
    for h in hits:
        if h.qualified_name == callee_name:
            return h
    from .io_boundary import gate_named_entry
    return gate_named_entry(
        hits, callee_name, module_hint, ambiguous_names,
        call_construct=call_construct,
    )


def _retry_name_unqualified(
    idx: Mapping[str, Sequence[TaintEntry]],
    callee_name: str,
    module_hint: str | None,
) -> str:
    """Return the unqualified callee when the qualified one indexes nothing.

    INV-januj / INV-fofoj. The public ``match_source`` / ``match_sink`` entry
    points take a callee name a caller already extracted, so the retry cannot be
    folded into :func:`_match_propagation_entry`'s copy; this is the shared shape
    both use, and it returns the name to look up rather than the hits so the
    caller's own ``idx.get`` stays the single lookup.

    ONLY ON A MISS. A name that already indexes something is returned unchanged,
    which is what makes this recall-only: no currently-matching call can be
    re-pointed at a different entry.
    """
    if idx.get(callee_name):
        return callee_name
    bare = strip_redundant_module_qualifier(module_hint, callee_name)
    if bare is not None and idx.get(bare):
        return bare
    return callee_name


def _build_callee_index(
    entries: Sequence[TaintEntry],
) -> dict[str, list[TaintEntry]]:
    """Index source/sink entries by short name, qualified name, and bare
    method name (last dotted component), each mapping to the LIST of entries
    registered under that key.

    A list (not a single overwrite-on-collision value) is required so
    :func:`_lookup_named_entry` can disambiguate by module / ambiguity when
    several catalog entries share a short name (WI-razol).
    """
    idx: dict[str, list[TaintEntry]] = defaultdict(list)
    for entry in entries:
        idx[entry.name].append(entry)
        idx[entry.qualified_name].append(entry)
        if "." in entry.name:
            idx[entry.name.rsplit(".", 1)[-1]].append(entry)
    return idx


def _target_kind_admits(entry: object, needed: Optional[str]) -> bool:
    """Does the stamp's resolution in this entry's direction admit ``entry``?

    Three admissions and nothing else: an UNCONDITIONAL entry (no
    ``requires_target_kind`` -- every entry the catalogue does not gate,
    including sanitizers, which carry neither field); a gated entry whose
    boundary the stamp RESOLVED to; and, when the stamp ABSTAINS
    (``needed is None``: no stamp, an unknown kind, a non-crossing kind, or
    collapsed sites that disagree), the primitive's single
    ``abstention_fallback`` entry. ``getattr`` because the index is typed
    ``TaintEntry`` and sanitizer entries carry neither attribute.
    """
    required = getattr(entry, "requires_target_kind", "")
    if not required:
        return True
    if needed is None:
        return bool(getattr(entry, "abstention_fallback", False))
    return bool(required == needed)


def _match_propagation_entry(
    index: Mapping[str, Sequence[TaintEntry]],
    edge_dst: str,
    ambiguous_names: frozenset[str],
    call_construct: str | None = None,
    *,
    is_resolved: bool = True,
    language: str = "",
    io_modes: "Sequence[str] | None" = None,
    io_target_kinds: "Sequence[str] | None" = None,
):
    """Match an edge's callee against a propagation source/sink ``index``.

    ``language``, when given, is the language whose catalogue built ``index``,
    and a callee from a DIFFERENT language is refused before any lookup. This
    is what :func:`_extract_callee_language` was written for — its docstring
    has always said it is "used by sink/source matching to filter cross-language
    pollution", and it had ZERO production callers until now.

    The pollution is structural rather than hypothetical: ``cmd_verify_claims``
    selects a language's edges with ``src.startswith(lang:) OR
    dst.startswith(lang:)``, so a bridge edge ``python:… → go:…`` is handed to
    BOTH languages' matchers, and the wrong one then indexes a ``go:`` callee
    against the Python catalogue. The OR is deliberate and must stay — the
    propagation BFS needs both endpoints to walk a cross-language call — so the
    gate belongs at the match, not at the selection.

    Measured on a 9-repo cohort: 208 cross-language taint call edges exist
    (95% of them typescript↔javascript) and **zero** currently match a sink in
    the wrong language's index, so this changes no flow today. It is a latent
    guard, and the honest reading of that null is the same as everywhere else in
    this subsystem — the cohort holds no elixir or ruby, and the shape it would
    take there (a short name like ``get`` colliding across catalogues) is
    exactly what the dead function's docstring warned about and what no repo
    here can exercise.

    A *resolved* (first-party) edge matches by exact callee name — the symbol
    is already disambiguated by resolution, and its symbol-ID "module" segment
    is a file path, not a dotted module to filter on (so module filtering would
    spuriously reject e.g. a ``cmd_sketch`` source whose declared module is
    ``hypergumbo_core.cli`` against the edge's ``cli.py`` path). An *unresolved*
    edge is the short-name-collision risk surface, so it goes through
    :func:`_lookup_named_entry`: a bare ambiguous callee with no module hint, or
    a module-mismatched hint, is not falsely matched (WI-razol), and an untyped
    *method* call (``call_construct``, threaded from the edge's ``meta``) never
    matches a method-kind sink/source (io-boundary:F3, INV-tapat/INV-maluk).

    ADR-0037 ruling 4: the resolution verdict is read from ``Edge.is_resolved``,
    NOT from the ``:unresolved`` dst-string suffix. That suffix is a producer
    convention that the WI-pubiv boundary-id remap rewrites to ``:external_symbol``
    on the final graph, so a string check would make every unresolved edge look
    "resolved" here and silently bypass the collision guard below.
    """
    if language and _extract_callee_language(edge_dst) != language:
        # Cross-language pollution guard. Refused BEFORE the index lookup, so a
        # short name that collides across two catalogues cannot match at all —
        # checking after the lookup would still let `hits` decide.
        return None
    callee_name = _extract_callee_name(edge_dst)
    hits = index.get(callee_name)
    if not hits:
        # INV-januj / INV-fofoj: the name slot may re-state the qualifier the
        # module slot already carries (java ``System`` + ``System.in``, python
        # ``sys`` + ``sys.stderr``). Retry once unqualified — STRICTLY after the
        # miss, so no edge that matches today can change. The io-boundary seam
        # carries the mirror of this; both call the one helper so they cannot
        # drift about what "redundant" means.
        bare = strip_redundant_module_qualifier(
            _extract_callee_module(edge_dst), callee_name,
        )
        if bare is None:
            return None
        hits = index.get(bare)
        if not hits:
            return None
        callee_name = bare
    # Mode gate for DUAL-CLASSIFIED primitives, applied before every other
    # arm so both the resolved and unresolved paths inherit it rather than
    # growing a second copy. Only entries that opted in via ``requires_mode``
    # are affected; an entry without it is unconditional and untouched, which
    # is what keeps ``os.remove`` firing on a call that carries no mode.
    # ``getattr`` for BOTH reads, not just the guard: the index is typed
    # ``TaintSource | TaintSink`` and only sinks carry ``requires_mode``, so
    # a direct attribute read is a strict-mode union-attr error.
    from .io_boundary import (
        resolve_mode_boundary_across_sites,
        resolve_target_kind_across_sites,
    )
    # INV-vukiv: EVERY collapsed site's mode, not the first one's. A function
    # that opens a path 'r' at one line and 'w' at another arrives here as one
    # edge, and asking only the survivor's singular ``io_mode`` dropped the
    # ``fs_write``-gated sink on the strength of a different call site — the
    # same false-negative shape ``call_arg_shape``'s conservative merge exists
    # to prevent, one key over.
    _needed = resolve_mode_boundary_across_sites(io_modes)
    hits = [
        h for h in hits
        if getattr(h, "requires_mode", "") in ("", _needed)
    ]
    # WI-lipis: the same gate on the axis a mode literal cannot answer. Kept
    # beside the mode gate rather than in a second pass so both dual-classified
    # shapes are refused in one place, and ``getattr`` for the same reason --
    # only sources carry this one.
    #
    # ``None`` from the resolver is an ABSTENTION, and it deliberately admits
    # only the unconditional entries: a stream whose origin the analyzer could
    # not recover keeps today's classification instead of minting on a guess.
    # That is the conservative direction HERE because this seam ADDS findings,
    # the mirror of ``_source_call_can_mint_taint``, which removes them.
    #
    # INV-minol / WI-suhug: an abstention admits the primitive's FALLBACK entry
    # too -- the one derived from the row ``classify_call`` selects for an
    # unstamped call -- so the two consumers of one catalogue give one answer.
    # The stamp is resolved in the ENTRY'S direction: a source reads (a
    # ``std_stream`` mints ``ipc_recv``), a sink writes (the same
    # ``std_stream`` is a ``logging`` sink).
    _needed_read = resolve_target_kind_across_sites(io_target_kinds)
    _needed_write = resolve_target_kind_across_sites(
        io_target_kinds, direction="write",
    )
    hits = [
        h for h in hits
        if _target_kind_admits(
            h, _needed_write if isinstance(h, TaintSink) else _needed_read,
        )
    ]
    if not hits:
        return None
    if is_resolved:
        # WI-damir. This used to be an ungated `return hits[0]`, justified as
        # "the symbol is already disambiguated by resolution". That premise is
        # FALSE, and it was the single largest source of non-realizable sinks
        # on fresh substrate. Resolution establishes WHICH IN-REPO SYMBOL is
        # called; it says nothing about whether that symbol IS the catalogued
        # primitive. The built-in catalogs describe stdlib and third-party
        # surfaces, so a first-party definition matching one BY NAME ALONE is a
        # category error — measured 30 of 30 false on the 9-repo cohort:
        # caddy's `func Log() *zap.Logger` (which RETURNS a logger and writes
        # nothing) reported as a logging sink 18 times, and d3's `function
        # log()` — the LOGARITHM, building d3.scaleLog — reported as
        # console.log. The latter is INV-karud's headline example and survived
        # WI-zazul because it was never a substring defect.
        #
        # The gate cannot be "resolved edges never match": a user-supplied
        # catalog may legitimately name a first-party symbol, which is what the
        # old comment was protecting. So compare the entry's declared module
        # against the symbol's own PATH, normalised to module shape. The
        # component-aware predicate's SUFFIX arm is what makes that work —
        # a catalog module of `hypergumbo_core.cli` matches a path of
        # `packages/…/hypergumbo_core/cli.py` because the trailing components
        # agree, while `log/slog` does not match `logging.go`.
        path_module = _module_from_symbol_path(edge_dst)
        if not path_module:
            # INV-fazim. There is no path evidence to judge on — the dst is
            # external-shaped (`_UNRESOLVED_MODULE_PLACEHOLDERS`) or malformed
            # enough that `_extract_callee_module` returns falsy. This used to
            # fall through to `return hits[0]`, "keep the legacy exact-name
            # behaviour rather than silently dropping the finding" — but that
            # is the SAME ungated bare-name match the block above exists to
            # refuse, reached through a different door: not by being a
            # first-party symbol, but by being flagged resolved while carrying
            # nothing to verify the receiver against. ADR-0037 ruling 4 makes
            # the flag authoritative (consumers may not string-check the
            # `:unresolved` suffix), so a producer that sets it wrongly landed
            # straight here.
            #
            # REFUSING IS MEASURED TO COST NOTHING, not argued to. The branch
            # was instrumented on dev 09921c57a1 during real verify-claims runs,
            # recording production's own inputs and classifying them with
            # production's own `_module_from_symbol_path`: across sops, grype,
            # act, poetry, winston and knex — 98,422 calls, 48,516 of them with
            # is_resolved=True — this condition held ZERO times. The counted
            # condition is a superset of the branch (it ignores the non-empty
            # `hits` requirement), so it errs toward over-reporting. A positive
            # control driving the branch deliberately fires it, so the zero is
            # a property of the inputs and not of a probe that never attached.
            #
            # That measurement is what licenses the change, because this index
            # also backs SANITIZER registration: losing a sanitizer match loses
            # a barrier, which moves flows in the opposite direction from losing
            # a sink. A line that never executes cannot do either.
            #
            # Pinned by test_taint_pathless_resolved_match_refused.py, which
            # ships both refusal cases AND two positive controls — refusing
            # unconditionally would satisfy the refusal tests alone.
            return None
        from .io_boundary import _module_matches
        for h in hits:
            # An entry that declares no module carries no evidence to
            # contradict the match; legacy behaviour is preserved for it.
            if not getattr(h, "module", None) or _module_matches(
                h.module, path_module,
            ):
                return h
        return None
    return _lookup_named_entry(
        hits, callee_name, _extract_callee_module(edge_dst), ambiguous_names,
        call_construct=call_construct,
    )


@dataclass
class TaintCatalog:
    """Container for all taint sources, sinks, and sanitizers.

    Organizes entries by language for efficient lookup. Provides matching
    methods that check callee names against catalog entries.
    """

    _sources: dict[str, list[TaintSource]] = field(default_factory=dict)
    _sinks: dict[str, list[TaintSink]] = field(default_factory=dict)
    _sanitizers: dict[str, list[TaintSanitizer]] = field(default_factory=dict)

    # Per-language ambiguous short names, sourced from the io_primitives
    # ``ambiguous_names`` lists (WI-razol). These are short names that collide
    # with common non-IO methods (``str.replace``, ``dict.get``, ``sys.stdout``
    # vs ``socket`` ``write``...). match_source / match_sink return None for
    # them when no module hint disambiguates, mirroring
    # ``io_boundary.IoBoundaryCatalog.lookup_with_module`` so taint analysis
    # agrees with io-boundaries instead of blindly matching the first entry.
    _ambiguous_names: dict[str, frozenset[str]] = field(default_factory=dict)

    # INV-faput: SHIPPED entries a user entry took the place of, per language.
    # Populated only where a user layer overrides the built-in one — a
    # user-over-user override removes no shipped coverage and is not recorded.
    #
    # These exist because the fact is otherwise UNRECOVERABLE downstream: the
    # displaced sink leaves the catalogue before propagation, so no flow is
    # constructed, nothing is sanitized, and `caveats` is correctly empty. The
    # finding-level attribution INV-pojib built cannot see this by construction
    # — there is no finding to attribute. Detecting it means comparing the
    # merged catalogue against the shipped one, which is only possible at the
    # moment of the merge.
    _displaced_sources: dict[str, list[TaintSource]] = field(
        default_factory=dict,
    )
    _displaced_sinks: dict[str, list[TaintSink]] = field(default_factory=dict)

    # Lookup indices built from entries
    _source_by_name: dict[str, dict[str, list[TaintSource]]] = field(
        default_factory=dict, repr=False,
    )
    _sink_by_name: dict[str, dict[str, list[TaintSink]]] = field(
        default_factory=dict, repr=False,
    )
    _sanitizer_by_name: dict[str, dict[str, list[TaintSanitizer]]] = field(
        default_factory=dict, repr=False,
    )

    def _rebuild_indices(self) -> None:
        """Build name-based lookup indices for all languages."""
        self._source_by_name.clear()
        self._sink_by_name.clear()
        self._sanitizer_by_name.clear()

        for lang, sources in self._sources.items():
            idx: dict[str, list[TaintSource]] = {}
            for src in sources:
                idx.setdefault(src.name, []).append(src)
                idx.setdefault(src.qualified_name, []).append(src)
            self._source_by_name[lang] = idx

        for lang, sinks in self._sinks.items():
            idx_s: dict[str, list[TaintSink]] = {}
            for sink in sinks:
                idx_s.setdefault(sink.name, []).append(sink)
                idx_s.setdefault(sink.qualified_name, []).append(sink)
            self._sink_by_name[lang] = idx_s

        for lang, sanitizers in self._sanitizers.items():
            idx_san: dict[str, list[TaintSanitizer]] = {}
            for san in sanitizers:
                idx_san.setdefault(san.qualified_name, []).append(san)
                idx_san.setdefault(san.short_name, []).append(san)
            self._sanitizer_by_name[lang] = idx_san

    def sources_for_language(self, language: str) -> list[TaintSource]:
        """Return all taint sources for a language."""
        return list(self._sources.get(language, []))

    def sinks_for_language(self, language: str) -> list[TaintSink]:
        """Return all taint sinks for a language."""
        return list(self._sinks.get(language, []))

    def sanitizers_for_language(self, language: str) -> list[TaintSanitizer]:
        """Return all taint sanitizers for a language."""
        return list(self._sanitizers.get(language, []))

    def all_source_labels(self) -> frozenset[str]:
        """Every taint label a FINDING could carry, across all languages.

        THE VOCABULARY A ``taint_flow`` CLAIM'S ``source_taint`` IS CHECKED
        AGAINST (INV-todas). ``verify_claim`` filters findings on
        ``f.taint_label == tf.source_taint``, so a value outside this set can
        match nothing, and before the check existed it confirmed the claim
        instead of refusing it.

        UNIONED WITH :data:`AUTO_SOURCE_LABEL_MAP`'s values rather than read
        off the catalogue alone: those labels are minted from io_primitives
        boundaries, so a run whose catalogue happens to contain no ``env_read``
        primitive would otherwise drop ``host_secret`` from the vocabulary and
        reject a correct claim. Rejecting a valid claim is a different failure
        from the one this exists to fix, and not an improvement.

        SANITIZER ``output_taint`` VALUES ARE DELIBERATELY EXCLUDED. They are
        stored on the sanitizer record and never assigned to a finding —
        sanitization sets the separate ``sanitized`` flag rather than
        relabelling the flow — so admitting ``ciphertext`` here would loosen
        the check for values that still cannot match anything.

        Across all languages, not the repo's: a claim naming a label whose
        sources are all Go must still be accepted when run on a Python repo.
        """
        labels = {
            src.taint_label
            for sources in self._sources.values()
            for src in sources
        }
        return frozenset(labels | set(AUTO_SOURCE_LABEL_MAP.values()))

    def all_sink_zones(self) -> frozenset[str]:
        """Every trust zone a FINDING could report, across all languages.

        The counterpart of :meth:`all_source_labels` for a claim's
        ``prohibited_sink_zone`` (INV-todas), unioned with
        :data:`AUTO_SINK_ZONE_MAP`'s zones for the same reason.

        This is why the claim-vocabulary check cannot live in ``load_claims``
        beside the ``constraint.boundary`` one: ``KNOWN_IO_BOUNDARIES`` is a
        constant, but a project-local ``--taint-sinks`` file may declare a zone
        no built-in catalogue mentions, and one already does in this suite's
        own fixtures. So the check runs against the RESOLVED catalogue.
        """
        zones = {
            sink.zone
            for sinks in self._sinks.values()
            for sink in sinks
        }
        return frozenset(zones | {z for z, _ in AUTO_SINK_ZONE_MAP.values()})

    def ambiguous_names_for_language(self, language: str) -> frozenset[str]:
        """Return the ambiguous short names for a language (WI-razol).

        These collide with common non-IO methods (``str.replace``,
        ``dict.get``); propagation passes them to
        :func:`propagate_taint_structural` / :func:`propagate_taint_ddg` so a
        bare ambiguous callee with no module hint is not matched to a sink.
        """
        return self._ambiguous_names.get(language, frozenset())

    def match_source(
        self,
        language: str,
        callee_name: str,
        module_hint: str | None = None,
        call_construct: str | None = None,
    ) -> Optional[TaintSource]:
        """Match a callee name against taint sources for a language.

        Honors the module qualifier and ``ambiguous_names`` via
        :func:`_lookup_named_entry` (WI-razol): a module hint that matches
        nothing yields ``None`` rather than the first source, and an ambiguous
        short name with no hint yields ``None`` rather than a false match.
        ``call_construct`` (io-boundary:F3) lets a bare untyped method call be
        rejected without the name needing to be in ``ambiguous_names``.
        """
        idx = self._source_by_name.get(language, {})
        callee_name = _retry_name_unqualified(idx, callee_name, module_hint)
        return _lookup_named_entry(
            idx.get(callee_name), callee_name, module_hint,
            self._ambiguous_names.get(language, frozenset()),
            call_construct=call_construct,
        )

    def match_sink(
        self,
        language: str,
        callee_name: str,
        module_hint: str | None = None,
        call_construct: str | None = None,
    ) -> Optional[TaintSink]:
        """Match a callee name against taint sinks for a language.

        Honors the module qualifier and ``ambiguous_names`` via
        :func:`_lookup_named_entry` (WI-razol): ``str.replace`` no longer
        matches ``Path.replace`` (the 5541-FP cascade) and ``sys.stdout.write``
        no longer matches ``asyncio.StreamWriter.write`` net_send (F156.A1).
        ``call_construct`` (io-boundary:F3) lets a bare untyped method call be
        rejected without the name needing to be in ``ambiguous_names``.
        """
        idx = self._sink_by_name.get(language, {})
        callee_name = _retry_name_unqualified(idx, callee_name, module_hint)
        return _lookup_named_entry(
            idx.get(callee_name), callee_name, module_hint,
            self._ambiguous_names.get(language, frozenset()),
            call_construct=call_construct,
        )

    def match_sanitizer(
        self,
        language: str,
        callee_name: str,
        input_taint: str,
    ) -> Optional[TaintSanitizer]:
        """Match a callee name against sanitizers that handle the given taint label."""
        idx = self._sanitizer_by_name.get(language, {})
        hits = idx.get(callee_name)
        if not hits:
            return None
        for h in hits:
            if h.input_taint == input_taint:
                return h
        return None


# ---------------------------------------------------------------------------
# YAML catalog loading
# ---------------------------------------------------------------------------


class TaintCatalogError(Exception):
    """A project-local taint catalog file could not be parsed or has an
    invalid shape.

    This is the single umbrella for taint-catalog load failures —
    malformed YAML, a non-mapping document, a wrong-typed top-level section
    (``sources``/``sinks``/``transforms``), or an invalid ``start_at`` value.
    The CLI maps it to exit code 2 (inconclusive): a broken taint
    configuration means verification could not proceed, which must never be
    reported as a confirmed (exit 0) or violated (exit 1) verdict
    (INV-nufob / ADR-0033 Phase 3 silent-confirm closure). Before this, the
    loaders raised raw ``yaml.YAMLError`` / ``AttributeError`` / ``ValueError``
    that escaped the CLI as an uncaught traceback, or — worse, when no
    ``taint_flow`` claim was present — were never reached, so a bad
    ``--taint-*`` path silently fell through to "all CONFIRMED".
    """


def _safe_load_catalog_yaml(
    path: Path, section: str, section_type: type,
) -> dict[str, Any]:
    """Parse a taint-catalog YAML file with shape validation.

    Raises :class:`TaintCatalogError` (never a raw ``yaml.YAMLError`` or
    ``AttributeError``) on malformed YAML, a non-mapping document, or a
    top-level ``section`` whose value is not an instance of ``section_type``.
    Returns the parsed mapping (``{}`` for an empty file).
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TaintCatalogError(
            f"could not parse taint catalog {path}: {exc}"
        ) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TaintCatalogError(
            f"taint catalog {path} must be a mapping at top level, got "
            f"{type(data).__name__}."
        )
    section_val = data.get(section)
    if section_val is not None and not isinstance(section_val, section_type):
        raise TaintCatalogError(
            f"taint catalog {path}: '{section}:' must be a "
            f"{section_type.__name__}, got {type(section_val).__name__}."
        )
    return data


def _load_source_yaml(path: Path) -> tuple[str, dict[str, list[TaintSource]]]:
    """Load a single taint source YAML file.

    Returns (taint_label, per-language dict of TaintSource entries). The stale
    ``list[TaintSource]`` annotation misdescribed the returned value — the body
    builds and returns ``sources_by_lang`` keyed by language, and every caller
    iterates it via ``.items()``.
    """
    data = _safe_load_catalog_yaml(path, "sources", dict)
    label = data.get("taint_label", "unknown")
    sources_by_lang: dict[str, list[TaintSource]] = {}

    for lang, entries in data.get("sources", {}).items():
        lang_sources: list[TaintSource] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            module = entry.get("module", "")
            return_tainted = entry.get("return_tainted", True)
            arg_tainted = tuple(entry.get("argument_tainted", []))
            start_at = entry.get("start_at", "caller")
            if start_at not in {"caller", "callee"}:
                raise TaintCatalogError(
                    f"Invalid start_at={start_at!r} in {path}; "
                    f"must be 'caller' or 'callee'."
                )

            for func_name in entry.get("functions", []):
                lang_sources.append(TaintSource(
                    taint_label=label,
                    module=module,
                    name=func_name,
                    kind="function",
                    return_tainted=return_tainted,
                    argument_tainted=arg_tainted,
                    start_at=start_at,
                ))
            for method_name in entry.get("methods", []):
                lang_sources.append(TaintSource(
                    taint_label=label,
                    module=module,
                    name=method_name,
                    kind="method",
                    return_tainted=return_tainted,
                    argument_tainted=arg_tainted,
                    start_at=start_at,
                ))
        sources_by_lang[lang] = lang_sources

    return label, sources_by_lang


def _load_sink_yaml(path: Path) -> dict[str, list[TaintSink]]:
    """Load a single taint sink YAML file.

    Returns dict mapping language → list of TaintSink entries.
    """
    data = _safe_load_catalog_yaml(path, "sinks", dict)
    zone = data.get("zone", "unknown")
    trust_level = data.get("trust_level", "unknown")
    sinks_by_lang: dict[str, list[TaintSink]] = {}

    for lang, entries in data.get("sinks", {}).items():
        lang_sinks: list[TaintSink] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            module = entry.get("module", "")

            for func_name in entry.get("functions", []):
                lang_sinks.append(TaintSink(
                    zone=zone,
                    trust_level=trust_level,
                    module=module,
                    name=func_name,
                    kind="function",
                ))
            for method_name in entry.get("methods", []):
                lang_sinks.append(TaintSink(
                    zone=zone,
                    trust_level=trust_level,
                    module=module,
                    name=method_name,
                    kind="method",
                ))
        sinks_by_lang[lang] = lang_sinks

    return sinks_by_lang


def _load_sanitizer_yaml(path: Path) -> dict[str, list[TaintSanitizer]]:
    """Load a single taint sanitizer YAML file.

    Returns dict mapping language → list of TaintSanitizer entries.
    """
    data = _safe_load_catalog_yaml(path, "transforms", list)
    sanitizers_by_lang: dict[str, list[TaintSanitizer]] = {}

    for transform in data.get("transforms", []):
        input_taint = transform.get("input_taint", "unknown")
        output_taint = transform.get("output_taint", "unknown")

        for lang, func_names in transform.get("functions", {}).items():
            lang_sans = sanitizers_by_lang.setdefault(lang, [])
            for func_name in func_names:
                lang_sans.append(TaintSanitizer(
                    input_taint=input_taint,
                    output_taint=output_taint,
                    qualified_name=func_name,
                ))

    return sanitizers_by_lang


def load_taint_catalog(
    source_paths: list[Path],
    sink_paths: list[Path],
    sanitizer_paths: list[Path],
) -> TaintCatalog:
    """Load taint catalogs from YAML files.

    Args:
        source_paths: Paths to taint source YAML files.
        sink_paths: Paths to taint sink YAML files.
        sanitizer_paths: Paths to taint sanitizer YAML files.

    Returns:
        A TaintCatalog with all entries indexed by language.
    """
    all_sources: dict[str, list[TaintSource]] = defaultdict(list)
    all_sinks: dict[str, list[TaintSink]] = defaultdict(list)
    all_sanitizers: dict[str, list[TaintSanitizer]] = defaultdict(list)

    for path in source_paths:
        _label, sources_by_lang = _load_source_yaml(path)
        for lang, sources in sources_by_lang.items():
            all_sources[lang].extend(sources)

    for path in sink_paths:
        sinks_by_lang = _load_sink_yaml(path)
        for lang, sinks in sinks_by_lang.items():
            all_sinks[lang].extend(sinks)

    for path in sanitizer_paths:
        sanitizers_by_lang = _load_sanitizer_yaml(path)
        for lang, sans in sanitizers_by_lang.items():
            all_sanitizers[lang].extend(sans)

    catalog = TaintCatalog(
        _sources=dict(all_sources),
        _sinks=dict(all_sinks),
        _sanitizers=dict(all_sanitizers),
    )
    catalog._rebuild_indices()
    return catalog


# ---------------------------------------------------------------------------
# Auto-import from io_primitives (WI-lokuv)
# ---------------------------------------------------------------------------
#
# ADR-0017 deliberately separates io_primitives (syscall-level IO boundary
# classification) from taint sources/sinks (trust-zone classification).  The
# rationale holds for project-local extension — every project has its own
# trust-zone structure — but the shipped *first-party* catalogs should not
# drift: every io_primitives write-side primitive is, by construction, a
# candidate sink for tainted data; every io_primitives read-side primitive
# for a sensitive category is a candidate source.
#
# Since 2026-04 (commit 51e1d232f3) the shipped sink catalog no longer
# exists as a separate ``taint_sinks/`` directory — sinks derive entirely
# from io_primitives via the mapping below.  Sources still ship as YAML in
# ``taint_sources/`` because their taint_label is project-meaningful
# (host_secret vs untrusted_input vs ...) and not derivable from the
# IO-boundary category alone.
#
# Auto-import is paranoid by design ("reading A" in the WI-lokuv discussion):
# each auto-derived sink is trust_level=untrusted and matches ANY taint
# label; each auto-derived source carries the label indicated below.  Users
# narrow the default by contributing overrides via ``--taint-sources`` or
# ``--taint-sinks`` whose entries match the auto-derived ``(module, name,
# kind)`` triple — the user entry wins, the auto entry is dropped.
#
# `fs_read` is intentionally absent from the source map: reading a file
# does not by itself make its contents sensitive; the label is project-
# specific (a config file vs. a credential vault vs. user-uploaded JSON).
# Projects that want every fs_read tainted can declare their own source
# catalog entries.
#
# This sink/source split IS hypergumbo's canonical I/O-boundary risk
# taxonomy: write-side/outbound boundaries are untrusted sinks (where
# tainted data lands or escapes), read-side sensitive boundaries are
# untrusted sources, and it is what ``verify-claims`` consumes. Do NOT
# confuse it with ``io_boundary.HIGH_RISK_PRIMITIVES`` — that is a narrow,
# display-only ``high_risk`` marker scoped to ``subprocess`` alone, not a
# competing risk axis. Destructive-filesystem and network-egress risk live
# HERE (and, for network, additionally at the chain ``dst_tier`` level),
# NOT in a second hand-curated high_risk set.
AUTO_SINK_ZONE_MAP: dict[str, tuple[str, str]] = {
    # io_primitives boundary -> (taint zone, trust_level)
    "fs_write": ("host_fs", "untrusted"),
    # WI-bibuk: subprocess gets its own zone rather than collapsing into
    # host_fs. Shelling out to a trusted external program (``pip``, ``git``,
    # ``rustup``, ``gitleaks``) is not the same trust surface as a direct
    # arbitrary-path filesystem write — the external program owns where
    # its bytes land. Claims that prohibit ``host_fs`` no longer fire on
    # legitimate ``subprocess.run`` calls; claims that need to prohibit
    # subprocess use ``prohibited_sink_zone: subprocess`` explicitly.
    "subprocess": ("subprocess", "untrusted"),
    "net_send": ("network", "untrusted"),
    "env_write": ("host_env", "untrusted"),
    "ipc_send": ("ipc", "untrusted"),
    "browser_storage_write": ("browser_storage", "untrusted"),
    # WI-gofaz: previously undocumented exclusions — now mapped.
    "db_write": ("database", "untrusted"),
    "process_send": ("ipc", "untrusted"),
    "logging": ("logging", "untrusted"),
}

AUTO_SOURCE_LABEL_MAP: dict[str, str] = {
    # io_primitives boundary -> taint_label for auto-derived source
    #
    # A BOUNDARY THAT AUTO-DERIVES A LABEL MUST MEAN WHAT THE LABEL MEANS
    # (INV-tutar). ``env_read`` used to carry BOTH readings and this map read
    # the wrong one: 134 of the 195 shipped ``env_read`` rows were host
    # DESCRIPTION (``runtime.GOOS``, ``os.uname``, ``navigator.platform``,
    # ``platform.system``) or user identity (``os.getlogin``, ``pwd.getpwnam``),
    # and calling those a *secret* is why ``host-secret-*`` claims carried 48 of
    # 85 adjudicated flows at 22.9% precision -- the weakest family in
    # measurement 0001. The catalogue was already distorting itself to cope:
    # ``python.yaml`` deliberately withheld ``getpid`` / ``cpu_count`` because
    # rowing them here "would manufacture false sources", while ``go.yaml``
    # rowed ``GOOS`` and ``Getwd`` -- one boundary value, two membership rules,
    # two shipped files.
    #
    # THE SPLIT IS OF THE BOUNDARY, NOT THE LABEL, and not a per-row override.
    # The boundary vocabulary is the registry-backed thing
    # (``CATALOG_BOUNDARY_TYPES``); a per-row ``taint_label`` would let the row
    # and the boundary each decide, which is one fact in two homes.
    "env_read": "host_secret",
    "host_info_read": "host_description",
    "net_recv": "untrusted_input",
    "ipc_recv": "untrusted_input",
    "db_read": "untrusted_input",
}


def _derive_auto_imports_from_io_primitives(
    io_catalog_dir: Path,
    overlay_paths: "Sequence[Path] | None" = None,
) -> tuple[
    dict[str, list[TaintSource]],
    dict[str, list[TaintSink]],
    dict[str, frozenset[str]],
]:
    """Scan io_primitives/*.yaml and derive default taint sources + sinks.

    Returns ``(sources_by_lang, sinks_by_lang, ambiguous_by_lang)``.  Each
    IoPrimitive whose ``boundary`` matches :data:`AUTO_SOURCE_LABEL_MAP` yields
    a TaintSource; each whose ``boundary`` matches :data:`AUTO_SINK_ZONE_MAP`
    yields a TaintSink.  Language is taken from each YAML's ``language:``
    field.  Primitives declared under YAML ``attributes:`` produce
    ``kind="attribute"`` records — these pair with ``module_attr_ref``
    edges emitted by language analyzers (see WI-guhok, WI-gapam).

    ``ambiguous_by_lang`` carries each catalog's ``ambiguous_names`` so the
    taint matchers can disambiguate exactly as io-boundaries does (WI-razol).

    PARENT INHERITANCE IS APPLIED HERE, VIA ``load_catalog``, AND IT USED NOT TO
    BE. ``io_boundary._CATALOG_PARENTS`` declares ``cpp <- c``,
    ``kotlin <- java``, ``scala <- java`` and ``elixir <- erlang``, and
    ``load_catalog`` merges the parent; this function called
    ``IoBoundaryCatalog.from_yaml`` directly and inherited nothing, while the
    paragraph above claimed parity with io-boundaries (L50 — a docstring
    asserting a parity that does not hold). The four inheriting languages
    therefore had BOTH a fraction of their primitive surface AND a weaker
    short-name collision guard:

        cpp        3 ->   70 taint entries   (C had 67; C++ ran on THREE)
        elixir   231 ->  469
        kotlin    26 ->  138
        scala     23 ->  137
        ambiguous_names: cpp 0->19, kotlin 34->58, scala 73->80, elixir 50->54

    ``load_catalog`` takes a language rather than a path, so the glob supplies
    the language via each file's stem; the merged catalogue keeps the CHILD's
    ``language`` field, which is what keys the returned dicts.

    ``overlay_paths`` carries PROJECT-LOCAL io_primitive overlays (INV-fotav)
    into this derivation, so a user declares a third-party primitive ONCE and
    both arms see it. Without this the unification ADR-0017 §453 established for
    built-ins — io_primitives as the single source of truth, no shipped
    ``taint_sinks/`` directory, "without a second source of truth that could
    drift out of sync" — would hold for hypergumbo's rows and NOT for the user's,
    who would have to declare ``requests.post`` twice in two schemas. Overlays
    are grouped by their declared ``language:`` and applied only to that
    language's catalogue, because ``load_catalog`` refuses a cross-language
    overlay rather than attributing I/O to the wrong tree.
    """
    from hypergumbo_core.io_boundary import (
        _CATALOG_ALIASES,
        load_catalog,
        load_overlay_catalog,
        mode_discriminated_primitives,
    )

    overlays_by_lang: dict[str, list[Path]] = defaultdict(list)
    for overlay_path in overlay_paths or ():
        overlays_by_lang[
            load_overlay_catalog(Path(overlay_path)).language
        ].append(Path(overlay_path))

    sources_by_lang: dict[str, list[TaintSource]] = defaultdict(list)
    sinks_by_lang: dict[str, list[TaintSink]] = defaultdict(list)
    ambiguous_by_lang: dict[str, frozenset[str]] = {}

    if not io_catalog_dir.is_dir():
        return dict(sources_by_lang), dict(sinks_by_lang), ambiguous_by_lang

    # THE LANGUAGES ASKED FOR, NOT THE FILES ON DISK (INV-potuf). An alias has
    # no catalogue file of its own — ``_CATALOG_ALIASES`` maps
    # ``typescript -> javascript`` and ``groovy -> java`` — so a loop over
    # ``*.yaml`` never visits either, and both derived ZERO sinks while their
    # sources keyed under their own name. One language, two halves, two
    # different spellings: a flow could start in typescript and never arrive.
    #
    # ``load_catalog`` already resolves the alias (and the ``_CATALOG_PARENTS``
    # chain); nothing here needed to learn about aliasing beyond ASKING.
    for language in sorted(
        {p.stem for p in io_catalog_dir.glob("*.yaml")} | set(_CATALOG_ALIASES)
    ):
        catalog = load_catalog(
            language,
            overlay_paths=overlays_by_lang.get(language) or None,
        )
        # BUCKET UNDER THE LANGUAGE REQUESTED, NOT ``catalog.language``. For an
        # alias those differ — ``load_catalog("typescript").language`` is
        # ``"javascript"``, because the field comes from the YAML that was
        # actually read — so bucketing by the catalogue's own name is what fed
        # typescript's rows to javascript and left typescript empty.
        #
        # The boundary arm already does exactly this, and has since the alias
        # was introduced: ``cli.py`` keys ``catalogs`` under both names with a
        # comment naming these same two aliases. This is that symmetry
        # restored on the taint arm, not a new policy.
        lang = language
        ambiguous_by_lang[lang] = (
            ambiguous_by_lang.get(lang, frozenset()) | catalog.ambiguous_names
        )
        # Primitives this catalogue declares under BOTH fs_read and fs_write,
        # so the sink derived from the write row can record that it only
        # applies when the call's mode says so. Derived from the catalogue
        # rather than listed here — see :func:`mode_discriminated_primitives`.
        #
        # KEYED ON (module, name, kind), NOT on the short name. This loop holds
        # the whole primitive, so it can ask the precise question; keying on
        # ``prim.name`` gated rust's ``std::fs::OpenOptions.open`` because
        # ``std::fs::File.open`` shares its short name, and since rust stamps
        # no ``io_mode`` that deleted rust's only host_fs write sink outright
        # (INV-kaduh's control finding).
        mode_gated = mode_discriminated_primitives(catalog)
        # WI-lipis: primitives this catalogue declares under two or more READ
        # boundaries, so the source derived from the minting row records that
        # it only applies when the call's stream argument says so. Derived from
        # the catalogue for the same reason ``mode_gated`` is, and keyed on
        # (module, name, kind) for INV-kaduh's reason -- a short name is shared
        # across modules and gating on it would silence an unrelated row.
        target_kind_gated = target_kind_discriminated_primitives(catalog)
        # INV-minol: which row of each gated primitive an ABSTAINING stamp
        # falls back to, read off the same reordered list the classifier
        # reads so the two consumers cannot disagree about "first".
        target_kind_fallback = target_kind_fallback_boundaries(catalog)
        for prim in catalog.primitives:
            key = (prim.module, prim.name, prim.kind)
            gated_boundary = prim.boundary if key in target_kind_gated else ""
            is_fallback = target_kind_fallback.get(key) == prim.boundary
            if prim.boundary in AUTO_SOURCE_LABEL_MAP:
                sources_by_lang[lang].append(TaintSource(
                    taint_label=AUTO_SOURCE_LABEL_MAP[prim.boundary],
                    module=prim.module,
                    name=prim.name,
                    kind=prim.kind,
                    requires_target_kind=gated_boundary,
                    abstention_fallback=is_fallback,
                    # The map above is many-to-one: net_recv, ipc_recv and
                    # db_read all become `untrusted_input`. Carry the
                    # boundary so the collapse is reversible downstream
                    # (WI-vazal) instead of information the label ate.
                    source_boundary=prim.boundary,
                ))
            if prim.boundary in AUTO_SINK_ZONE_MAP:
                zone, trust = AUTO_SINK_ZONE_MAP[prim.boundary]
                sinks_by_lang[lang].append(TaintSink(
                    zone=zone,
                    trust_level=trust,
                    module=prim.module,
                    name=prim.name,
                    kind=prim.kind,
                    requires_mode=(
                        prim.boundary if key in mode_gated else ""
                    ),
                    # WI-suhug: the write-side twin of the source field above,
                    # resolved in the WRITE direction at match time.
                    requires_target_kind=gated_boundary,
                    abstention_fallback=is_fallback,
                ))

    return dict(sources_by_lang), dict(sinks_by_lang), ambiguous_by_lang


def _merge_with_user_override(
    auto_by_lang: Mapping[str, Sequence[TEntry]],
    user_by_lang: Mapping[str, Sequence[TEntry]],
) -> tuple[dict[str, list[TEntry]], dict[str, list[TEntry]]]:
    """Merge auto-derived entries with user entries; user entries win on
    (module, name, kind) match.

    Returns ``(merged, displaced)``. ``merged`` preserves every user entry and
    adds auto entries whose (module, name, kind) triple is not already declared
    by the user. ``displaced`` holds the entries that were DROPPED — the ones a
    user entry took the place of.

    INV-faput: the displacement set was always computed here and thrown away,
    and that is the whole defect. A user sink re-declaring a shipped one does
    not ADD to the catalogue, it REPLACES it — so the shipped row leaves before
    propagation runs, no flow is ever constructed, and the claim reads
    ``confirmed`` with ``caveats: []``. Measured: a repo whose only statement is
    ``os.remove(os.environ["API_KEY"])`` against "host secrets must not reach
    the host filesystem" goes ``violated`` rc 1 -> ``confirmed`` rc 0 when a
    user file re-declares ``os.remove`` into a ``dev_zone``.

    That is strictly worse than the two disclosure gaps already closed. An
    overlay GRANTS coverage; a user sanitizer DELETES a finding already made
    and is attributed on the flow (INV-pojib); an override PREVENTS THE FINDING
    FROM EXISTING, which is the only one of the three that can leave no trace
    on any per-flow record. Nothing downstream can reconstruct it, because the
    evidence is gone by the time anything downstream runs. Returning it is
    therefore not bookkeeping — it is the only moment the fact exists.
    """
    merged: dict[str, list[TEntry]] = {}
    displaced: dict[str, list[TEntry]] = {}
    all_langs = set(auto_by_lang) | set(user_by_lang)
    for lang in all_langs:
        user_list = user_by_lang.get(lang, [])
        user_keys = {(e.module, e.name, e.kind) for e in user_list}
        auto_list = auto_by_lang.get(lang, [])
        filtered_auto = [
            e for e in auto_list
            if (e.module, e.name, e.kind) not in user_keys
        ]
        dropped = [
            e for e in auto_list
            if (e.module, e.name, e.kind) in user_keys
        ]
        if dropped:
            displaced.setdefault(lang, []).extend(dropped)
        merged[lang] = filtered_auto + list(user_list)
    return merged, displaced


# ---------------------------------------------------------------------------
# Built-in catalog discovery
# ---------------------------------------------------------------------------

_TAINT_SOURCES_DIR = Path(__file__).parent / "taint_sources"
_TAINT_SANITIZERS_DIR = Path(__file__).parent / "taint_sanitizers"
_IO_PRIMITIVES_DIR = Path(__file__).parent / "io_primitives"
# Note: there is no ``_TAINT_SINKS_DIR``.  Commit 51e1d232f3 retired the
# shipped ``taint_sinks/`` directory and derives all built-in sinks from
# ``io_primitives/*.yaml`` via :func:`_derive_auto_imports_from_io_primitives`.
# Project-local sinks still flow in via the ``--taint-sinks`` CLI flag.


def _resolve_catalog_paths(paths: list[Path]) -> list[Path]:
    """Resolve project-local taint-catalog path arguments to a file list.

    Each input path is either a single YAML file (``*.yaml``/``*.yml``) or a
    directory — directories are globbed for ``*.yaml`` (sorted for
    deterministic merge order).  Raises :class:`FileNotFoundError` on any
    missing path so a typo in a CLI flag or claims-file entry does not
    silently fall through to the built-in defaults.
    """
    resolved: list[Path] = []
    for p in paths:
        if not p.exists():
            raise FileNotFoundError(f"Taint catalog path not found: {p}")
        if p.is_dir():
            resolved.extend(sorted(p.glob("*.yaml")))
        else:
            resolved.append(p)
    return resolved


def load_full_taint_catalog(
    extra_source_paths: list[Path] | None = None,
    extra_sink_paths: list[Path] | None = None,
    extra_sanitizer_paths: list[Path] | None = None,
    *,
    cli_source_paths: list[Path] | None = None,
    cli_sink_paths: list[Path] | None = None,
    cli_sanitizer_paths: list[Path] | None = None,
    io_overlay_paths: "Sequence[Path] | None" = None,
) -> TaintCatalog:
    """Load built-in taint catalogs and merge in user-supplied YAML files.

    Path arguments accept YAML files or directories of YAMLs — resolved via
    :func:`_resolve_catalog_paths`.  Four layers stack, each overriding the
    ones below it on ``(module, name, kind)`` for sources/sinks (sanitizers
    key on ``qualified_name`` and so concatenate, never replace):

    1. Auto-derived taint entries from ``io_primitives/*.yaml`` (paranoid
       default: every write-side primitive is a sink, every read-side
       sensitive primitive is a source).
    2. Built-in YAML under ``taint_sources/`` and ``taint_sanitizers/``
       alongside this module.  (Built-in sinks come from layer 1 only;
       the ``taint_sinks/`` directory was retired in 51e1d232f3.)
    3. Claims-file extras — ``extra_*_paths`` (the ``extra_catalogs:`` key in
       the claims YAML, WI-votan).
    4. CLI extras — ``cli_*_paths`` (the ``--taint-sources`` /
       ``--taint-sinks`` / ``--taint-sanitizers`` flags).

    INV-hukug: layers 3 and 4 are kept distinct so a CLI flag (layer 4)
    *replaces* a claims-file entry (layer 3) on a matching
    ``(module, name, kind)`` instead of coexisting with it as a duplicate.
    Previously both were concatenated into one user layer with no intra-layer
    dedup, so a CLI ``--taint-sources`` override silently failed to displace a
    claims-file ``extra_catalogs.sources`` entry. Passing only ``extra_*_paths``
    (no ``cli_*``) preserves the prior single-user-layer behavior.

    The helper is the single entry point for end-users running
    ``verify-claims`` on a repo other than hypergumbo's own.
    """
    extra_source_paths = _resolve_catalog_paths(extra_source_paths or [])
    extra_sink_paths = _resolve_catalog_paths(extra_sink_paths or [])
    extra_sanitizer_paths = _resolve_catalog_paths(extra_sanitizer_paths or [])
    cli_source_paths = _resolve_catalog_paths(cli_source_paths or [])
    cli_sink_paths = _resolve_catalog_paths(cli_sink_paths or [])
    cli_sanitizer_paths = _resolve_catalog_paths(cli_sanitizer_paths or [])

    catalog = load_builtin_taint_catalog(io_overlay_paths)

    any_extra = extra_source_paths or extra_sink_paths or extra_sanitizer_paths
    any_cli = cli_source_paths or cli_sink_paths or cli_sanitizer_paths
    if not (any_extra or any_cli):
        return catalog

    # Two user layers: claims-file extras (lower) and CLI extras (higher).
    # CLI overrides claims on (module, name, kind) for sources/sinks
    # (INV-hukug); sanitizers concatenate (claims then CLI). The unified user
    # layer then overrides the built-in catalog (layers 1+2).
    claims_layer = load_taint_catalog(
        extra_source_paths, extra_sink_paths, extra_sanitizer_paths,
    )
    cli_layer = load_taint_catalog(
        cli_source_paths, cli_sink_paths, cli_sanitizer_paths,
    )
    # User-over-user (claims file vs CLI). A displacement here is one user
    # layer overriding another, which INV-hukug already documents as intended
    # precedence and which removes no SHIPPED coverage — so it is not carried.
    user_sources, _ = _merge_with_user_override(
        claims_layer._sources, cli_layer._sources,
    )
    user_sinks, _ = _merge_with_user_override(
        claims_layer._sinks, cli_layer._sinks,
    )
    # INV-pojib: STAMPED HERE, which is the only place that still knows these
    # entries came from a user path. Below this line the layers merge into one
    # catalogue and a consumer asking "did the repo supply this?" would have to
    # re-derive it from paths it no longer holds.
    user_sanitizers: dict[str, list[TaintSanitizer]] = {}
    for layer in (claims_layer._sanitizers, cli_layer._sanitizers):
        for lang, sans in layer.items():
            user_sanitizers.setdefault(lang, []).extend(
                replace(san, user_supplied=True) for san in sans
            )

    # THIS is the displacement that matters: user entries taking the place of
    # SHIPPED ones, which is the only layer boundary where a user file can
    # remove coverage the tool would otherwise have had.
    catalog._sources, displaced_sources = _merge_with_user_override(
        catalog._sources, user_sources,
    )
    catalog._sinks, displaced_sinks = _merge_with_user_override(
        catalog._sinks, user_sinks,
    )
    catalog._displaced_sources = displaced_sources
    catalog._displaced_sinks = displaced_sinks
    for lang, sans in user_sanitizers.items():
        catalog._sanitizers.setdefault(lang, []).extend(sans)
    catalog._rebuild_indices()
    return catalog


def load_builtin_taint_catalog(
    io_overlay_paths: "Sequence[Path] | None" = None,
) -> TaintCatalog:
    """Load built-in taint catalogs shipped with hypergumbo.

    Two contributions merge into one catalog:

    1. YAML-declared entries in ``taint_sources/`` and ``taint_sanitizers/``.
       These cover project-agnostic domains the core team maintains
       explicitly (crypto decryption labels, key material generation,
       sanitizer pairings, ...) and provide the project-local extension
       point described in ADR-0017.
    2. Auto-derived entries from ``io_primitives/*.yaml`` (WI-lokuv).
       Every write-side IO primitive becomes a TaintSink at
       trust_level=untrusted with a zone determined by its boundary
       category; every read-side sensitive-category primitive becomes a
       TaintSource with a default taint_label.  User YAML entries that
       match (module, name, kind) override the auto-derived defaults.

    The merge makes io_primitives the single source of truth for primitive
    enumeration: adding a primitive there propagates into taint analysis
    automatically, which replaces the manual drift-guard previously shipped
    under WI-hizik.  Built-in sinks come entirely from layer 2 — the
    shipped ``taint_sinks/`` directory was retired in 51e1d232f3.
    """
    source_paths = sorted(_TAINT_SOURCES_DIR.glob("*.yaml")) if _TAINT_SOURCES_DIR.exists() else []
    sanitizer_paths = sorted(_TAINT_SANITIZERS_DIR.glob("*.yaml")) if _TAINT_SANITIZERS_DIR.exists() else []
    # No built-in sinks: 51e1d232f3 retired the shipped ``taint_sinks/``
    # directory and derives them from ``io_primitives/`` instead.
    user_catalog = load_taint_catalog(source_paths, [], sanitizer_paths)

    auto_sources, auto_sinks, ambiguous_by_lang = (
        _derive_auto_imports_from_io_primitives(
            _IO_PRIMITIVES_DIR, io_overlay_paths,
        )
    )
    user_catalog._sources, displaced_sources = _merge_with_user_override(
        auto_sources, user_catalog._sources,
    )
    user_catalog._sinks, displaced_sinks = _merge_with_user_override(
        auto_sinks, user_catalog._sinks,
    )
    user_catalog._displaced_sources = displaced_sources
    user_catalog._displaced_sinks = displaced_sinks
    # WI-razol: carry the io_primitives ambiguous_names onto the catalog so
    # match_source / match_sink disambiguate exactly as io-boundaries does.
    user_catalog._ambiguous_names = ambiguous_by_lang
    user_catalog._rebuild_indices()
    return user_catalog


# ---------------------------------------------------------------------------
# Structural taint-flow propagation (Phase 1 fallback)
# ---------------------------------------------------------------------------


def _extract_callee_name(symbol_id: str) -> str:
    """Extract the callee function name from a symbol ID.

    Symbol ID format: {lang}:{file_or_module}:{start}-{end}:{name}:{kind}
    For unresolved externals: {lang}:external:0-0:{name}:unresolved

    Handles names containing colons (ObjC selectors) by anchoring on the span
    token rather than on slot count.

    Delegates to :func:`ir.symbol_name_slot`, which is this function's own logic
    promoted to a chokepoint (INV-fokik). It was the CORRECT of the two name
    parsers — ``io_boundary._extract_callee_name`` assumed a colon-free path and
    shredded every Rust sink — and the two disagreeing about one string is what
    exposed the defect. Sharing the implementation is what stops that recurring;
    a comment asserting they agree is exactly what this project has been burned by.
    """
    if len(symbol_id.split(":")) < 5:
        return symbol_id
    return symbol_name_slot(symbol_id)


def _qualified_callee(symbol_id: str) -> str:
    """``{module}.{name}`` for a callee id, or "" when either slot is missing.

    The key ADR-0017 §4 summaries are declared under: ``fmt.Printf``,
    ``net/http.Get``, ``os.getenv``. Built from the two existing production
    extractors rather than a fresh parse of the id grammar — a third naive
    split of a colon-tolerant format is exactly what ``_symbol_path_slot``'s
    header warns about.

    THE MODULE HALF GOES THROUGH ``_module_from_symbol_path``, NOT THE RAW
    SLOT (INV-rozaj). An in-repo id carries a *file path* where an external
    one carries a module, so composing the raw slot embeds the source
    extension in the middle of the key —
    ``python:src/app/views.py:10-20:handler:function`` yielded
    ``src/app/views.py.handler`` — which no declared summary can equal. That
    made first-party callees structurally uncatalogueable while looking like
    an ordinary catalogue miss. ``_module_from_symbol_path`` was added by
    WI-damir to normalise exactly this and was sitting sixty lines below;
    using it here is the L53 rule applied to the code rather than to a
    measurement ("when a production classification exists for the thing you
    are computing, computing it yourself IS the bug").

    Returns "" for an id with no module evidence — ``python:external:0-0:
    print:unresolved`` has the placeholder ``external`` in the module slot,
    and a key built from it is a well-formed string naming nothing. An empty
    key keeps such a callee uncatalogued and therefore unknown, which is the
    safe direction: an unknown callee keeps a branch open.
    """
    module = _module_from_symbol_path(symbol_id)
    name = _extract_callee_name(symbol_id)
    if not module or not name or name == symbol_id:
        return ""
    return f"{module}.{name}"


def _extract_callee_language(symbol_id: str) -> str:
    """Extract the language prefix from a symbol ID.

    Symbol id format: ``{lang}:{path}:{line-range}:{name}:{kind}``.
    Returns the language token. Used by sink/source matching to filter
    cross-language pollution — without this filter, a sink declared in
    elixir (``HTTPoison.get``) collides with every Python ``.get()`` call
    via short-name indexing, producing thousands of false positives.
    """
    parts = symbol_id.split(":", 1)
    return parts[0] if parts else ""


def _extract_callee_module(symbol_id: str) -> str:
    """Extract the callee module/path hint from a symbol ID.

    Delegates to :func:`ir.symbol_path_slot`. For unresolved externals this is
    typically ``"external"`` (entirely ambiguous) or a module path like
    ``"os.environ"`` / ``"subprocess"`` / ``"std::fs"`` when the analyzer
    pinned it down. For in-repo dsts it's the relative file path.

    Used by sink-matching to filter short-name collisions: a sink declared as
    ``multiprocessing.Queue.get`` should NOT match an edge whose dst is
    ``python:external:0-0:get:unresolved`` because the edge could equally be
    ``dict.get``, ``args.get``, etc.

    THIS USED TO SAY "mirrors ``_extract_callee_name``'s parsing" AND TAKE
    ``parts[1]``, WHICH IS NOT THAT PARSE. The path slot is colon-tolerant
    (ADR-0036 D1a), so the naive split truncated every colon-bearing module to
    its first component. That was fatal rather than lossy for Rust — all nine
    of its catalogued sink modules are colon-bearing, ``std::fs`` became
    ``std``, and because :func:`_lookup_named_entry` rejects on a
    present-but-mismatched module rather than degrading, an edge that correctly
    named ``std::fs`` was refused while an edge carrying no module at all
    matched. The docstring's claim was the tell: it described the right parse
    and the code did another one (L50).
    """
    return symbol_path_slot(symbol_id)


# Source-file extensions stripped when reading a symbol id's PATH segment as a
# module (WI-damir). Named explicitly rather than inferred by length: `net.ws`
# is a real module whose trailing component is two characters, and a heuristic
# that treats short tails as extensions silently rewrites it to `net`.
_SOURCE_FILE_EXTENSIONS = frozenset({
    "py", "pyi", "js", "mjs", "cjs", "jsx", "ts", "tsx", "go", "rs", "rb",
    "java", "kt", "kts", "swift", "scala", "sc", "php", "cs", "c", "h", "cc",
    "cpp", "hpp", "cxx", "m", "mm", "ex", "exs", "erl", "hrl", "sh", "bash",
    "zsh", "pl", "pm", "lua", "dart", "sol", "vue", "svelte",
})


def _module_from_symbol_path(symbol_id: str) -> str:
    """Normalise an in-repo symbol id's PATH segment to a module-shaped string.

    An in-repo symbol id carries a file path where an external one carries a
    module (``go:logging.go:779-783:Log:function`` vs
    ``go:os:0-0:Remove:external_symbol``). To judge a resolved symbol against a
    catalog entry's declared module (WI-damir) the two have to be comparable,
    so the trailing file extension is dropped and the rest is handed to
    :func:`io_boundary._module_matches`, which normalises ``/`` to ``.`` and
    compares whole components.

    Returns ``""`` when there is no usable path evidence — an ``external``
    placeholder or a malformed id — so the caller can fall back rather than
    reject on the strength of nothing.

    Examples::

        packages/hypergumbo-core/src/hypergumbo_core/cli.py
            -> packages/hypergumbo-core/src/hypergumbo_core/cli   (suffix-matches
                                                                   hypergumbo_core.cli)
        logging.go                    -> logging     (does NOT match log/slog)
        src/pretix/static/d3/d3.v6.js -> src/pretix/static/d3/d3.v6
                                                     (does NOT match console)
    """
    raw = _extract_callee_module(symbol_id)
    if not raw or raw in _UNRESOLVED_MODULE_PLACEHOLDERS:
        return ""
    head, sep, tail = raw.rpartition(".")
    # Strip a trailing SOURCE-FILE EXTENSION, matched against an explicit list.
    #
    # The first draft used a length heuristic — "a short alphanumeric segment
    # after a dot is an extension" — and an existing test refuted it
    # immediately: the module `net.ws` had its real trailing component `ws`
    # stripped to `net`, which then failed to match a catalog entry declared as
    # `net.ws`. A short component is not evidence of an extension, and no
    # length threshold can separate the two; the set has to be named.
    if sep and head and tail.lower() in _SOURCE_FILE_EXTENSIONS:
        raw = head
    return raw


# Edge types that represent call-like relationships for taint propagation.
# Includes direct calls and cross-language linker bridge edges (ADR-0017 §5).
#
# ADR-0023 §6 Phase 2 audit (WI-sahab-fatoz): mixes relationship-axis
# (``calls``, ``module_attr_ref``), pending_classification
# (``implements_rpc``), and endpoint_shape bridge values. Forward-
# compatible through Phase 3 because ``calls`` is already a member, so
# bridges folding into ``calls`` + ``meta["bridge_kind"]`` continue to
# match; bridge entries become dead-but-harmless and get pruned in
# Phase 4.
# INV-lalad: the call-family half is DERIVED, not listed. It was
# ``frozenset({"calls", ...})`` — a private copy that silently disagreed with
# the registry once ``instantiates`` joined the family, and the disagreement
# was measurable: ``subprocess.Popen(tainted)`` verified CLEAN while
# ``subprocess.run(tainted)`` verified VIOLATED, because py.py types a
# PascalCase ``module.Attr()`` as ``instantiates`` and this set could not
# traverse it. Reading ``call_family_edge_types()`` means a future addition to
# the call family reaches taint automatically.
#
# UNION, never replace. The two entries below are taint-specific and are NOT
# call constructs; deriving this whole set from the registry would delete them.
TAINT_CALL_EDGE_TYPES = call_family_edge_types() | frozenset({
    # WI-lokuv: attribute-read edges for IO primitives declared under
    # ``attributes:`` in io_primitives YAML (os.environ, sys.argv, ...).
    # Emitted by the Python analyzer per WI-guhok; extending to the
    # tree-sitter analyzer base class is tracked as WI-gapam.  Without
    # this edge type, auto-imported TaintSource records for attribute
    # kind primitives would never match in structural propagation.
    "module_attr_ref",
    # Bridge edges no longer enumerated explicitly: post-Phase-3
    # (WI-mifor-vabul), every bridge folds to 'calls' which is already a
    # member; meta['bridge_kind'] carries the bridge type. Protocol-call
    # family (WI-vumum-juvil) similarly folds into 'calls' + meta['protocol'],
    # so HTTP/gRPC/GraphQL call taint propagation transfers automatically.
    # implements_rpc folded to 'implements' + meta['protocol']='grpc'
    # (audit-findings 0016) — NOT a plain set member (that would wholesale-
    # include every structural 'implements' edge); matched by the
    # is_grpc_rpc_implementation predicate via _is_taint_call_edge below.
    #
    # INV-zuhig: framework-dispatch edges are call-shaped for taint. The
    # Framework linkers (go_cobra, argparse_dispatch, decorator/django/
    # jackson/kafka/caddy/airflow/rust-trait dispatch) emit
    # ``dispatches_to`` for "runtime dispatch will invoke dst with data
    # src controls". Excluding the family made every framework-dispatched
    # handler unmintable as a start_at:callee source — the self-proof's
    # cmd_* handlers were reachable only through argparse dispatch, so
    # sources minted ZERO flows and all 18 confirms were vacuous on the
    # taint side. Membership grows adjacency and minting monotonically;
    # the one non-additive surface (sanitizer registration over dispatch
    # edges) shares the same predicate deliberately, so a dispatched-to
    # barrier function still registers — one rule, one home.
    "dispatches_to",
})


def _is_taint_call_edge(edge: dict[str, Any]) -> bool:
    """True if *edge* (a behavior-map edge dict) carries taint like a call.

    Membership in :data:`TAINT_CALL_EDGE_TYPES`, OR the folded gRPC
    RPC-implementation edge (``implements`` + ``meta['protocol']='grpc'``,
    audit-findings 0016) — the one place taint recognizes the folded form,
    so gRPC taint propagation is preserved without demoting or over-
    including structural ``implements`` edges.
    """
    etype = edge.get("type", "")
    return etype in TAINT_CALL_EDGE_TYPES or is_grpc_rpc_implementation(
        etype, edge.get("meta")
    )


LITERAL_ONLY_ARG_SHAPE: Final[str] = "literal_only"


def _source_and_sink_are_one_call(
    source_caller: str,
    sink_caller: str,
    source_callee: str,
    sink_callee: str,
    call_lines: Mapping[tuple[str, str], Sequence[int]],
) -> bool:
    """True when the source call site and the sink call site are ONE call.

    A value a call RETURNS cannot be an argument to that same call, so this
    pair anchors no flow. The third per-call-site gate in this module, and
    deliberately the same shape as the other two: `_sink_call_can_carry_taint`
    refuses a site that provably discards, `_source_call_can_mint_taint`
    refuses a site that provably reads memory, and this refuses a *pair* that
    is one invocation. None of the three is the ADR-0017 §3a walk. That walk
    may now remove a flow (WI-kabif) but only on its own ``unconfirmed``
    verdict; these three refusals are upstream of it and are about what
    counts as a source/sink PAIR at all.

    WHY THIS ONLY BECAME REACHABLE WITH INV-lozat. Until a content-returning
    launch was catalogued, no shipped primitive was a taint source AND a taint
    sink at the same call: `db_read` pairs with `db_write`, `net_recv` with
    `net_send`, always two different calls. `exec.Command(...).Output()`
    receives the child's bytes and IS a subprocess sink, so both propagators
    paired the call with itself and reported "data from a subprocess reaches a
    subprocess" over a call that cannot feed its own arguments. Measured on the
    measurement-0006 cohort, both arms cold: hypergumbo's own
    `walk_blocked_by == "sink_before_source"` marks 4 of 692 pre-existing
    evidence rows (0.6%) and 18 of the 51 rows those catalogue rows add
    (35.3%) -- sixty times the background rate, and two of the three verdicts
    that moved `inconclusive -> violated` rested on one such row each.

    POSITIVE EVIDENCE IS REQUIRED, and the asymmetry is the whole design.
    Removal is the expensive direction for a security tool, so the gate fires
    only when the edge records EXACTLY ONE call line:

    * TWO OR MORE lines means the caller invokes the primitive twice, and
      iteration N's output really can reach iteration N+1's arguments
      (`out = check_output(out)`), so the flow stays.
    * NONE recorded means we do not know how many calls there are. `meta.
      call_lines` absence is documented as "exactly one site" only alongside
      `edge["line"]`; with neither, an unknown count is not a known one, and
      the flow stays.

    The residual false negative is stated rather than hidden: a single call
    site whose own output feeds its own arguments on a later loop iteration is
    suppressed. That requires the primitive to take the value it returns, in a
    loop, through no intervening call -- `exec.Cmd.Output` takes no arguments
    at all, and no instance appears anywhere in the cohort.
    """
    if source_caller != sink_caller or source_callee != sink_callee:
        return False
    return len(set(call_lines.get((source_caller, source_callee), ()))) == 1


def _source_call_can_mint_taint(edge: dict[str, Any]) -> bool:
    """False when this call site provably reads nothing from outside.

    WI-lipis, and the twin of :func:`_sink_call_can_carry_taint`. The sink side
    already refuses a call site that hands its value to nothing
    (``> /dev/null``); a source site that takes its value from nothing is the
    same argument pointed the other way, and until now nothing asked it.

    THE MEASURED CASE. ``go.yaml`` files ``bufio.{NewScanner,NewReader}`` as
    ``ipc_recv`` -- which is in :data:`AUTO_SOURCE_LABEL_MAP`, so every call
    site mints ``untrusted_input`` -- on the note "When wrapping os.Stdin", a
    condition no catalogue row can enforce because the row sees the callee and
    the answer is in the ARGUMENT. So
    ``bufio.NewScanner(strings.NewReader(s))`` invented an untrusted-input
    SOURCE out of a caller's string, and the DDG then confirmed a route from it
    to ``exec.Command`` at ``confidence: precise`` -- a fully-confident false
    positive built on a value that never left the process.

    Measured on the ADR-0049 cohort's Go repositories: of the 83 bare-local
    sites whose origin the shipped reaching-def solver resolves, 63 wrap an
    ``os.Open`` handle (``fs_read``, deliberately NOT a taint source), 3 an
    HTTP body, 1 a buffer, and **zero** wrap ``os.Stdin``. The row's own stated
    condition holds nowhere in that population.

    THE SAME VOCABULARY AS THE SINK SIDE, ON PURPOSE, and the second
    deliverable generalised the question rather than adding a second gate
    beside it. The sink side asks "does this site cross a boundary AT ALL"
    (:func:`io_boundary.target_kinds_cross_no_boundary`). The source side needs
    a strictly finer question -- "does it cross one that MINTS" -- because an
    ``os.Open`` handle crosses a real boundary (``fs_read``) that is absent
    from :data:`AUTO_SOURCE_LABEL_MAP` by design: the sensitivity of a file
    read depends on what is stored. So this asks
    :func:`io_boundary.read_boundary_for_target_kind`, which answers from the
    non-crossing set FIRST and therefore still moves when that set is widened
    at its single home. Both directions read one vocabulary; neither can drift.

    THREE ANSWERS, AND THE DEFAULT IS THE CONSERVATIVE ONE:

    * the vocabulary has NO opinion on some site's kind (``unresolved``, or a
      value from a future analyzer) -- mint, and let the catalogue row decide;
    * every site crosses nothing, or crosses only non-minting boundaries --
      refuse;
    * any site crosses a minting boundary -- mint. ANY, not every, for
      INV-vukiv's reason: silencing a real receive on the strength of a
      DIFFERENT collapsed call site is the false-negative trade.

    WHAT IT STILL DOES NOT DO. A bare local whose binding the analyzer cannot
    find in the enclosing function -- a parameter, a struct field -- stamps
    nothing and is untouched here. INV-zumin's ruling forbids answering it by
    emitting BOTH boundaries, so the abstention stays an abstention.
    """
    kinds = call_site_target_kinds(edge.get("meta") or {})
    if not kinds:
        return True
    resolved = [read_boundary_for_target_kind(kind) for kind in kinds]
    if any(not known for known, _boundary in resolved):
        return True
    return any(
        boundary in AUTO_SOURCE_LABEL_MAP
        for _known, boundary in resolved
        if boundary is not None
    )


def _sink_call_can_carry_taint(edge: dict[str, Any]) -> bool:
    """False when this call site provably cannot be the sink of a flow.

    THREE INDEPENDENT PROOFS, and they live together because this is the one
    place both propagators ask the question. The structural arm's own comment
    at its call site says why: two copies of this question is how the
    call-family set drifted across three consumers.

    PROOF ONE — INV-fubag: no argument at this call site can be the tainted
    value. PROOF TWO — INV-nular/INV-kosur: whatever is handed over is
    discarded, so it reaches no zone at all. PROOF THREE — WI-zovuz: no
    externally-derived NAME can reach what the shell itself writes at this
    redirect. Any one makes the finding false rather than merely unproven,
    which is what licenses a gate here at all.

    PROOF THREE, and why it is a proof rather than a heuristic. bash carries no
    dataflow, so a redirect-sink finding rested on reachability alone: "this
    file reads the environment somewhere AND reaches a function that writes
    somewhere". ``redirect_origin_names`` closes the derivation over the whole
    file — assignments, and positional parameters bound at every call site of
    the enclosing function — and reports which externally-derived names can
    reach the three things the SHELL contributes: the target operand, a
    heredoc body it expands itself, and every producing stage's arguments. An
    EMPTY list therefore says no value this program holds can be what crossed,
    and bash's only taint sources are name-derived.

    IT IS DELIBERATELY NOT THE BYTE-PRODUCER QUESTION, which was measured
    WRONG. "Is the writer an external program?" deletes
    ``echo "$SIGNING_CERT" | base64 -d > cert.pfx`` (beads), where a real
    certificate is written and only stage ONE is a builtin, and it mistakes
    ``sed -E "s#x#${v}#" f > out`` (cilium), where an in-process value is
    interpolated THROUGH an external command. Asking which NAME can reach
    survives both.

    THE FETCH CASE IS LEFT OPEN ON PURPOSE. ``curl -L "$URL" > "$DEST"``
    credits ``URL`` here, because deciding that curl's argument SELECTS a
    remote resource rather than being interpolated into its output is
    per-command semantics this gate does not have. That is INV-fumod shape (b)
    and it stays open; ablation measured the omission at 3 of 186 names and
    zero of 69 files, so the conservative answer costs almost nothing.

    INV-fubag. Taint models a flow as the tainted value being an ARGUMENT to
    the sink call or its RECEIVER. When a producer can prove every argument at
    a call site is a literal constant -- or that there are no arguments at all
    -- neither can be the tainted value, and the receiver of a call like
    ``tempfile.TemporaryDirectory()`` is a module. So the flow is not merely
    unlikely, it is impossible under the model the tool itself uses. This is a
    proof, which is why it needs no threshold and costs no recall.

    Measured (docs/measurements/0003): of the 34 adjudicated false positives
    the construction-edge widening added, 24 were sink calls with no arguments
    at all. The constructor is the wrong anchor for a constructor-shaped I/O
    sink -- ``ZipFile(path,'w')`` opens and ``zipp.writestr(...)`` writes -- so
    anchored there the sink witnesses only "an fs resource was created in this
    function" and any tainted value in scope produces a flow.

    IT STAYS A SINK WHEN ITS ARGUMENT IS TAINTED. 0003's single true positive
    is exactly that: mitmproxy's ``ZipFile(path, "w")`` where ``path`` came
    from ``os.path.expanduser``. This gate must never remove it, which is what
    the test of that name pins.

    DEFAULT-DENY ON THE SILENCING DIRECTION. Only the one value we can prove
    safe suppresses; an absent key, an unrelated key, or an unrecognised value
    all keep the finding. Absence is the state of every edge in every behavior
    map written before this key existed, and a gate whose default silenced
    findings would be a false-negative generator on a security analysis.

    INV-kosur — THE DISCARD CLAUSE, AND WHY IT IS HERE AND NOT UPSTREAM.
    ``target_kinds_cross_no_boundary`` was wired into ``tag_io_boundaries``
    and nowhere else, so ``echo "$API_KEY" > /dev/null`` returned ``confirmed``
    against ``{boundary: fs_write, must_not_exist: true}`` and ``violated``
    against ``{taint_flow: host_secret -> host_fs}``: same tree, same edge,
    opposite verdicts, because the taint arm derives its sinks straight from
    the catalogue (:data:`AUTO_SINK_ZONE_MAP` over every ``fs_write`` row) and
    never saw the per-call-site fact. The gate's own reasoning — "the kernel
    discards the bytes and no observation anywhere differs because the
    redirect ran" — is about the CALL SITE and says nothing about claim shape,
    so it belongs on every path that asks whether that site is a sink.

    Refusing the match UPSTREAM in ``classify_call_in_catalog`` instead was
    tried for INV-nular and measured worse: the coverage gate asks "did the
    catalogue EXAMINE this call" and the answer is emphatically yes, so
    refusing there traded a false ``violated`` for an ``inconclusive``. This
    arm inherits that reasoning unchanged.

    THE SOURCE SIDE IS DELIBERATELY NOT GATED. ``< /dev/null`` yields EOF and
    is exactly as vacuous, but no boundary that can carry an
    ``io_target_kind`` stamp derives a taint source: the stamp rides on bash
    redirects, and bash files ``redirect`` under ``fs_write``/``fs_read``,
    neither of which is in :data:`AUTO_SOURCE_LABEL_MAP`. A source-side clause
    would be unreachable, and an unreachable gate proves nothing. The premise
    is re-derived from the shipped catalogues by
    ``test_no_shipped_catalogue_derives_a_source_from_a_redirect``, which
    fails with the remedy if someone files ``redirect.<`` as ``ipc_recv``.
    """
    meta = edge.get("meta") or {}
    if target_kinds_cross_no_boundary(call_site_target_kinds(meta)):
        return False
    origins = meta.get("redirect_origin_names")
    if isinstance(origins, list) and not origins:
        return False
    return meta.get("call_arg_shape") != LITERAL_ONLY_ARG_SHAPE


def _source_names_can_reach_sink(
    source_names: Optional[frozenset[str]],
    sink_names: Optional[frozenset[str]],
) -> bool:
    """False when no name this source carries can reach what the sink writes.

    INV-fumod shape (b). A PAIR-level proof, which is why it lives beside
    :func:`_source_and_sink_are_one_call` rather than inside either
    per-call-site gate: neither edge alone answers it. The source edge knows
    WHICH environment name it read (``env_var``); the sink edge knows which
    names can reach what the shell writes there (``redirect_origin_names``);
    the flow is false only if those sets are disjoint.

    WHY THE PER-EDGE GATE IS NOT ENOUGH, on the item's own instance.
    guacamole's ``curl -L "$URL" > "$DEST_PATH/$DEST_JAR"`` IS reached by a
    name — ``DESTINATION``, via ``DEST_PATH="$DESTINATION/drivers/"`` — so
    ``_sink_call_can_carry_taint`` keeps it, correctly. The row INV-fumod
    filed is sourced at ``MYSQL_JDBC_VERSION``, read at line 87 on the FILE
    symbol while ``DESTINATION`` is read at line 59 inside
    ``download_driver``. Two source sites, one sink, so the false pair can be
    refused while the other stands.

    DEFAULT-DENY ON THE SILENCING DIRECTION. The pair survives unless BOTH
    sides are known and provably disjoint. ``None`` on either side means the
    question was not answered — a language with no name-level flow, a map
    written before these keys existed — and an EMPTY source set means the
    source carries no name at all, which is every source in every other
    language. An empty SINK stamp is deliberately not answered here: "no name
    reaches this redirect" is already a proof and it is
    :func:`_sink_call_can_carry_taint`'s to make, so answering it twice would
    put one fact in two homes.
    """
    if not source_names or sink_names is None:
        return True
    if not sink_names:
        return True
    return bool(source_names & sink_names)


def _name_flow_indexes(
    edges: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], frozenset[str]],
           dict[tuple[str, str], Optional[frozenset[str]]]]:
    """Per (caller, callee): the names a source read, and the names a sink can write.

    Both are keyed by the pair rather than by the edge because that is the
    granularity the propagator pairs at. A source key unions every environment
    name read at that site — guacamole's file symbol reads three JDBC version
    variables and they share one ``env.environ`` callee — which is the
    conservative direction: refusing the pair then requires ALL of them to be
    unreachable.

    A sink key is ``None`` the moment ANY edge under it lacks the stamp. A
    partial answer must not read as a complete one: the unstamped edge could
    be reached by a name the stamped ones are not.
    """
    source_names: dict[tuple[str, str], set[str]] = defaultdict(set)
    sink_names: dict[tuple[str, str], Optional[set[str]]] = {}
    for edge in edges:
        meta = edge.get("meta") or {}
        key = (edge.get("src", ""), edge.get("dst", ""))
        env_var = meta.get("env_var")
        if isinstance(env_var, str) and env_var:
            source_names[key].add(env_var)
        # ``<key>_values`` is where a collapsed edge's DISAGREEING per-site
        # values live (:func:`ir._absorb_per_call_site_key`), and reading only
        # the singular silently loses exactly the interesting case: guacamole's
        # file symbol reads three JDBC version variables at three lines, so
        # ``env_var`` is gone and ``env_var_values`` holds all three.
        for value in meta.get("env_var_values") or ():
            if isinstance(value, str) and value:
                source_names[key].add(value)
        if str(meta.get("io_primitive", "")).startswith("redirect."):
            stamps = meta.get("redirect_origin_names_values")
            if isinstance(stamps, list):
                # Sites disagreed. Union them: any of those sites could be the
                # one this pair flows to, and a bigger set keeps more findings.
                merged = {str(n) for stamp in stamps
                          if isinstance(stamp, list) for n in stamp}
            elif isinstance(meta.get("redirect_origin_names"), list):
                merged = {str(n) for n in meta["redirect_origin_names"]}
            else:
                sink_names[key] = None
                continue
            if key in sink_names and sink_names[key] is None:
                continue
            bucket = sink_names.setdefault(key, set())
            assert bucket is not None  # narrowed by the branch above
            bucket.update(merged)
    return (
        {k: frozenset(v) for k, v in source_names.items()},
        {k: (None if v is None else frozenset(v)) for k, v in sink_names.items()},
    )


def _build_adjacency(
    edges: list[dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Build forward and reverse adjacency lists from edge dicts.

    Includes call-type edges and cross-language linker bridge edges
    (ADR-0017 §5). Bridge edges are taint-transparent by default —
    IPC serialization does not sanitize taint.
    Returns (forward_adj, reverse_adj).
    """
    forward: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)

    for edge in edges:
        if not _is_taint_call_edge(edge):
            continue
        src = edge["src"]
        dst = edge["dst"]
        forward[src].add(dst)
        reverse[dst].add(src)

    return dict(forward), dict(reverse)


def _build_sanitizer_index_multi(
    sanitizers: list[TaintSanitizer],
) -> dict[str, list[TaintSanitizer]]:
    """Index sanitizers by callee name as a list, not a single entry.

    One function may be declared as a sanitizer for several distinct
    input_taint labels — the zone-barrier pattern in hypergumbo's
    self-audit uses one barrier function per wrapper to sanitize every
    entry-point label so a single call blocks BFS regardless of which
    entry-point seeded the trace. Indexing as a flat dict would
    overwrite all but one entry; the list-indexed version preserves
    every (qualified_name → input_taint) declaration so the BFS
    consumer can register sanitization for every applicable label.
    """
    index: dict[str, list[TaintSanitizer]] = defaultdict(list)
    for san in sanitizers:
        index[san.qualified_name].append(san)
        index[san.short_name].append(san)
        # Bare-method-name fallback (parity with the source/sink indexers)
        # so unresolved edges that only have the leaf name still match.
        if "." in san.qualified_name:
            leaf = san.qualified_name.rsplit(".", 1)[-1]
            if leaf != san.short_name:
                index[leaf].append(san)
    return index


def _iter_sink_sites(
    sink_callers: "dict[str, list[tuple[str, TaintSink]]]",
) -> "Iterator[tuple[str, str, TaintSink]]":
    """Yield ``(caller, sink_callee_id, sink)`` for every sink call site.

    ``sink_callers`` maps a CALLER to every sink it calls. It used to hold a
    single tuple per caller and was populated by assignment, so a function
    calling two different sinks reported only the last edge encountered —
    a silent under-report present in both the structural and the DDG pass.
    Shared by both so the two cannot drift again.
    """
    for caller, entries in sink_callers.items():
        for sink_callee_id, taint_sink in entries:
            yield caller, sink_callee_id, taint_sink


def _edge_call_sites(edge: dict[str, Any]) -> list[int]:
    """Every line this call edge is known to occur on.

    ``edge["line"]`` alone is NOT every call site. ``ir.deduplicate_edges``
    keeps one edge per ``(src, dst, edge_type)`` carrying whichever site was
    encountered FIRST, so asking "does the taint reach ``line``?" asks about an
    arbitrary one of N. That produced a verified false negative on caddy:
    ``printEnvironment`` calls ``fmt.Printf`` twelve times, the edge recorded
    line 454, and the tainted call sat at 469. ``meta.call_lines`` preserves the
    rest; absence of that field is its contract for "exactly one site".

    ONE HOME, because two consumers now need it — the §3a walk's line index and
    the sanitizer-line index that WI-fasub's fix keys on. A second copy of this
    parse is exactly the shape that put four parsers of the symbol-id path slot
    in the tree, two of them wrong.

    Validated rather than trusted: ``meta`` is an open dict deserialized from an
    artifact that may predate the field or have been hand-edited, and a non-int
    member would reach a ``sink_line > source_line`` comparison as a TypeError.
    """
    sites: list[int] = []
    recorded = (edge.get("meta") or {}).get("call_lines")
    if isinstance(recorded, list):
        sites = [ln for ln in recorded if isinstance(ln, int)]
    edge_line = edge.get("line")
    if isinstance(edge_line, int) and edge_line not in sites:
        sites.append(edge_line)
    return sites


def _catalogue_key_for_edge(edge: dict[str, Any]) -> str | None:
    """The §4 catalogue key for *edge*, or None if it may not be catalogued.

    WI-zumud, and it exists because the INV-rozaj fix removed an ACCIDENTAL
    barrier. ``_qualified_callee`` normalises an in-repo callee's module slot
    with ``_module_from_symbol_path``, which strips the source file extension::

        before:  go:net/http.go:1-5:Get:function -> 'net/http.go.Get'  != stdlib
        after:   go:net/http.go:1-5:Get:function -> 'net/http.Get'     == stdlib

    Stripping the extension is CORRECT — it is what makes a first-party callee
    catalogueable at all — but it also means a Go repo with a root-level
    ``net/http.go`` now produces a key equal to a SHIPPED stdlib summary. That
    is the WI-damir shape (a first-party symbol matching a catalogue primitive
    by name alone) reappearing in the §4 lookup rather than in sink matching,
    and WI-damir already recorded the verdict on that premise: resolution
    establishes WHICH IN-REPO SYMBOL is called and says nothing about whether
    that symbol IS the catalogued primitive.

    NOT IN ``_qualified_callee``, deliberately. Gating key CONSTRUCTION would
    also break ``test_in_repo_callee_key_has_no_file_extension``, which pins the
    INV-rozaj fix. The key SHAPE is right; what is wrong is handing a
    first-party key to a catalogue that describes stdlib and third-party
    surfaces. So the provenance decision gets its own home and the key builder
    is left alone.

    ADR-0037 ruling 4: the verdict is read from ``is_resolved``, NEVER from the
    dst string's ``:unresolved`` suffix. WI-pubiv's boundary-id remap rewrites
    that suffix to ``:external_symbol`` on the final graph, so a string check
    would read every unresolved edge as resolved and bypass this gate entirely.

    DIRECTION, and it is why this is safe to land before the exposure is live.
    A summary that says "terminates" lets the §3a walk CLOSE a branch, and a
    false close REMOVES a real finding. Refusing to catalogue can only produce
    FEWER terminations, hence more unknowns and more surviving violations. The
    ``.get(..., True)`` default carries the same direction: an edge with no
    resolution verdict — an older artifact, a hand-edited map — is treated as
    first-party and is not catalogued (L54 default-deny; enumerate the
    PERMITTING case, which here is "the callee is known to be external").

    The shipped catalogue declares no first-party summaries today, so this
    gives up nothing real. Should one ever be added, the fix is catalogue
    PROVENANCE (match a first-party callee only against a project-local entry),
    not weakening this gate.
    """
    if edge.get("is_resolved", True):
        return None
    return _qualified_callee(edge.get("dst", ""))


def _register_sanitizer_callers(
    edges: list[dict[str, Any]],
    sanitizer_by_callee: dict[str, list[TaintSanitizer]],
    sanitizer_callers: "dict[str, dict[str, list[TaintSanitizer]]]",
    ambiguous_names: frozenset[str] = frozenset(),
    sanitizer_lines: "dict[tuple[str, str], list[int]] | None" = None,
) -> None:
    """Populate sanitizer_callers from edges + multi-sanitizer index.

    Each edge whose callee matches one or more sanitizers adds an entry
    per matched sanitizer's input_taint label to the caller's sanitizer
    dict — so a caller of a multi-label barrier picks up every label.

    INV-finoh: sanitizer matching applies the same resolution-/kind-aware
    gate that source/sink matching does (``_match_propagation_entry`` /
    ``gate_named_entry``), so a phantom barrier is never registered from a
    bare-name collision — which would silently SUPPRESS a real taint flow (a
    false negative, worse than a missed barrier for a security tool). A
    *resolved* edge trusts its resolution (exact-name match, unchanged). An
    *unresolved* edge is the short-name-collision surface: a qualified callee
    carries its own receiver evidence (an exact ``qualified_name`` match wins,
    parity with ``_lookup_named_entry``'s qualified-first branch), but a bare
    untyped *method* call (``call_construct == "method"``, threaded from the
    edge meta) has no receiver evidence and must NOT match — ``x.encrypt()``
    must not bind ``Fernet.encrypt`` and falsely sanitize a flow (the
    INV-tapat/INV-maluk rule ``gate_named_entry`` enforces). An
    ``ambiguous_names`` bare short name is the meta-absent safety net.

    That receiver evidence is read from BOTH slots it can occupy. The
    name-slot form is the synthetic one; production analyzers put the
    inferred type in the MODULE slot, and consulting only the name slot left
    the permit branch unreachable for every method-shaped sanitizer — which
    is every sanitizer shipped. The parity with ``_lookup_named_entry``
    claimed above was therefore aspirational rather than actual, since that
    function consults the module slot; the module-slot check below is what
    makes it true. An untyped receiver still carries the ``external``
    placeholder, still yields no module, and is still refused. (The
    ``kind``-filter for a free-function call matching a method-kind sanitizer
    is not applied here because the sanitizer catalog carries no explicit
    ``kind`` — a documented follow-up requiring a sanitizer-YAML schema field.)

    ``sanitizer_lines``, when supplied, additionally records
    ``(caller, input_taint) -> [line, ...]`` for every barrier call site. That
    index is what lets the DDG pass honour a sanitizer in the SAME function as
    the source (WI-fasub), and it is collected HERE rather than re-derived at
    the point of use so the INV-finoh resolution-/kind-aware gate above governs
    both. Re-matching sanitizers at a second site is how this module acquired a
    private, ungated copy of this registration that silently deleted real flows;
    the caller that needs lines passes a dict, the one that does not passes
    nothing.
    """
    for edge in edges:
        if not _is_taint_call_edge(edge):
            continue
        callee_name = _extract_callee_name(edge["dst"])
        matched_list = sanitizer_by_callee.get(callee_name)
        if not matched_list:
            continue
        if not edge.get("is_resolved", True):
            qualified = any(
                s.qualified_name == callee_name for s in matched_list
            )
            if not qualified:
                # Receiver evidence also arrives in the MODULE slot, and in
                # production that is the ONLY place it arrives. An analyzer
                # that inferred the receiver's type emits
                # ``py:Fernet:0-0:encrypt:…``; the name-slot form
                # ``py:external:0-0:Fernet.encrypt:…`` the branch above
                # matches is a synthetic shape no analyzer produces for a
                # method call. Reading only the name slot made the permit
                # branch ``"Fernet.encrypt" == "encrypt"`` — false by
                # construction for every method-shaped sanitizer, which is
                # the entire shipped catalogue — so the gate was
                # unconditional in production and the barrier arm was dead
                # at every idiomatic call site.
                #
                # ``_module_from_symbol_path`` returns "" for the ``external``
                # placeholder, so an UNTYPED receiver still yields no
                # candidate and is still refused: INV-finoh's guarantee is
                # preserved rather than widened. The WHOLE qualified name
                # must match — a typed receiver of the wrong type is evidence
                # AGAINST this sanitizer, not permission to assume it.
                module = _module_from_symbol_path(edge["dst"])
                if module:
                    fq = f"{module}.{callee_name}"
                    qualified = any(
                        s.qualified_name == fq for s in matched_list
                    )
            if not qualified:
                call_construct = edge.get("meta", {}).get("call_construct")
                if call_construct == "method":
                    continue
                if ambiguous_names and callee_name in ambiguous_names:
                    continue
        for matched in matched_list:
            # A LIST, NOT A SLOT (INV-pojib). This used to assign, so the LAST
            # short-name match won: all four shipped ``*.encrypt`` sanitizers
            # match a bare ``encrypt`` callee, and a fixture calling
            # ``Fernet.encrypt`` was attributed to
            # ``ChaCha20Poly1305.encrypt``. The barrier never noticed because it
            # only asks WHETHER some sanitizer carries this label; attribution
            # asks WHICH, and the honest answer is "one of these".
            bucket = sanitizer_callers[edge["src"]].setdefault(
                matched.input_taint, [],
            )
            if matched not in bucket:
                bucket.append(matched)
            if sanitizer_lines is not None:
                sanitizer_lines.setdefault(
                    (edge.get("src", ""), matched.input_taint), [],
                ).extend(_edge_call_sites(edge))


def subsume_slot_family_parents(
    callers: Sequence[tuple[str, str, TEntry]],
) -> list[tuple[str, str, TEntry]]:
    """Drop a parent ATTRIBUTE match when its own slot family matched too.

    INV-sukoh. ``python.yaml`` deliberately carries a parent attribute row
    beside a child slot-family row — ``os`` / ``environ`` beside
    ``os.environ`` / ``get`` — and the child's note says why: *"a method call
    on the mapping carries os.environ as its module slot, where the parent's
    attribute row cannot reach."* They were meant to be COMPLEMENTARY: parent
    for a bare ``os.environ[...]`` read, child for ``os.environ.get(...)``.

    On the ``.get`` form both reach, because the analyzer emits TWO edges for
    the one expression — a ``module_attribute_reference`` to ``os.environ`` and
    an ``ast_call_direct`` to ``os.environ.get``. So one read became two flows,
    inflating the row denominator of every measurement taken over them.

    THE RELATION IS COMPUTED FROM THE CATALOGUE, NOT LISTED HERE. An entry is a
    slot-family PARENT when its qualified name is the MODULE SLOT of another
    entry matched AT THE SAME CALLER. That is the catalogue's own structure, so
    a user-supplied catalogue with the shape is covered without a table anyone
    has to remember to extend. Four shipped rows have it today, all Python:
    ``os.environ`` (env_read), ``sys.stdin`` (ipc_recv), ``sys.stdout`` and
    ``sys.stderr`` (logging) — so it is SYMMETRIC, and this runs on the sink
    side as well as the source side.

    CONDITIONAL ON THE CHILD, which is what makes it safe. A bare
    ``os.environ["X"]`` emits only the attribute edge; nothing subsumes it and
    the read is still reported. Dropping the parent unconditionally would
    delete the read outright.

    A ``callee``-seeded source is never subsumed and never subsumes. Its BFS
    seeds at the source callee rather than the caller, so parent and child
    search DIFFERENT subgraphs; folding them would silently change which
    subgraph was searched, which is not the duplicate-report this fixes.

    WHAT IS DEDUCTED IS A NAME, NOT A FLOW. Where both forms genuinely occur in
    one symbol, the two edges are indistinguishable from the single-form case:
    edges are deduplicated on ``(src, dst, edge_type)`` (INV-vukiv), so the
    multiplicity is already gone before this sees it. The coarser NAME is
    dropped and the finding is still reported — no verdict moves, and the name
    retained is the strictly more specific description of the same crossing.
    """
    children_by_caller: dict[str, set[str]] = defaultdict(set)
    for caller_id, _callee_id, entry in callers:
        if _seeds_at_caller(entry):
            children_by_caller[caller_id].add(entry.module)

    kept: list[tuple[str, str, TEntry]] = []
    for caller_id, callee_id, entry in callers:
        qualified = _qualify(entry.module, entry.name)
        if (
            _seeds_at_caller(entry)
            and qualified != entry.module
            and qualified in children_by_caller[caller_id]
        ):
            continue
        kept.append((caller_id, callee_id, entry))
    return kept


def _subsume_sink_sites(
    sink_callers: dict[str, list[tuple[str, TaintSink]]],
) -> None:
    """Apply :func:`subsume_slot_family_parents` to the sink index, in place.

    The sink index is keyed by caller and holds ``(callee_id, sink)`` pairs
    rather than the flat triples the source arm carries, so this adapts the
    shape instead of duplicating the relation (L9: one fact, one home).
    """
    for caller_id, sites in list(sink_callers.items()):
        kept = subsume_slot_family_parents(
            [(caller_id, callee_id, sink) for callee_id, sink in sites],
        )
        sink_callers[caller_id] = [
            (callee_id, sink) for _caller, callee_id, sink in kept
        ]


def _seeds_at_caller(entry: TaintEntry) -> bool:
    """Whether this entry's BFS seeds at the CALL SITE rather than the callee.

    Only :class:`TaintSource` carries ``start_at``; a sink has no seed of its
    own, so it always answers True.
    """
    return not isinstance(entry, TaintSource) or entry.start_at == "caller"


def propagate_taint_structural(
    edges: list[dict[str, Any]],
    sources: list[TaintSource],
    sinks: list[TaintSink],
    sanitizers: list[TaintSanitizer],
    ambiguous_names: frozenset[str] = frozenset(),
    language: str = "",
) -> list[TaintFlowFinding]:
    """Structural taint-flow propagation via call-graph BFS.

    Two-phase BFS per ADR-0017 §3b:
    1. For each taint source, compute the set of nodes reachable from the
       source's caller without passing through any sanitizer for that
       taint label.
    2. Check if any taint sink is in the reachable set.

    This is an overapproximation: it cannot distinguish between different
    variables in the same function. Findings are labeled as approximate.

    Args:
        edges: List of edge dicts with "src", "dst", "type" keys.
        sources: Taint source definitions.
        sinks: Taint sink definitions.
        sanitizers: Taint sanitizer definitions.
        ambiguous_names: Short names the catalog flags as ambiguous (e.g.
            ``replace`` / ``write`` / ``get``); a bare ambiguous callee with
            no usable module hint is not matched to a source/sink (WI-razol).

    Returns:
        List of TaintFlowFinding for each source→sink violation.
    """
    if not edges or not sources or not sinks:
        return []

    forward_adj, reverse_adj = _build_adjacency(edges)

    # Index: callee name → source/sink/sanitizer (list per name).
    # Index by qualified name, catalog name, AND short method name (last
    # component after dots) to match unresolved edges that only have the
    # bare method name (e.g., "decrypt" instead of "Fernet.decrypt").
    source_by_callee = _build_callee_index(sources)
    sink_by_callee = _build_callee_index(sinks)
    sanitizer_by_callee = _build_sanitizer_index_multi(sanitizers)

    # Step 1: Find source call sites — which symbol IDs call taint sources?
    # A "source caller" is a node that has an outgoing call edge to a source.
    # _lookup_named_entry honors the edge's module hint and ambiguous_names so
    # a bare ambiguous callee (str.replace, dict.get) is not falsely matched
    # (WI-razol).
    source_callers: list[tuple[str, str, TaintSource]] = []
    # (caller_symbol_id, source_callee_symbol_id, TaintSource)
    for edge in edges:
        if not _is_taint_call_edge(edge):
            continue
        matched = _match_propagation_entry(
            source_by_callee, edge["dst"], ambiguous_names,
            call_construct=edge.get("meta", {}).get("call_construct"),
            is_resolved=edge.get("is_resolved", True),
            language=language,
            io_modes=call_site_modes(edge.get("meta")),
            io_target_kinds=call_site_target_kinds(edge.get("meta")),
        )
        if matched and _source_call_can_mint_taint(edge):
            source_callers.append((edge["src"], edge["dst"], matched))

    # INV-sukoh: one expression, one flow. ``os.environ.get(...)`` emits an
    # attribute-reference edge AND a call edge, so the parent row and its own
    # slot-family row both match the single read.
    source_callers = subsume_slot_family_parents(source_callers)

    # Step 2: Find sink call sites — which symbol IDs call taint sinks?
    sink_callers: dict[str, list[tuple[str, TaintSink]]] = defaultdict(list)
    # Maps caller_symbol_id → (sink_callee_symbol_id, TaintSink)
    for edge in edges:
        if not _is_taint_call_edge(edge):
            continue
        matched = _match_propagation_entry(
            sink_by_callee, edge["dst"], ambiguous_names,
            call_construct=edge.get("meta", {}).get("call_construct"),
            is_resolved=edge.get("is_resolved", True),
            language=language,
            io_modes=call_site_modes(edge.get("meta")),
        )
        if matched and _sink_call_can_carry_taint(edge):
            site = (edge["dst"], matched)
            if site not in sink_callers[edge["src"]]:
                sink_callers[edge["src"]].append(site)

    # INV-sukoh on the sink side: two of the four shipped parent rows are
    # sinks, so ``sys.stdout.write(x)`` doubles exactly as the env read does.
    _subsume_sink_sites(sink_callers)

    # (caller, callee) -> every line that call occurs on, for
    # ``_source_and_sink_are_one_call``. The DDG pass already builds this index
    # for the §3a walk; the structural pass has no walk and needed it only when
    # a primitive became both a source and a sink (INV-lozat), so it is built
    # here rather than hoisted into a shared helper that one caller would use
    # for two unrelated purposes.
    # INV-fumod shape (b): which names a source read, and which can reach what
    # a sink writes. Built here beside call_lines_by_pair for the same reason —
    # the pairing loop is the only consumer.
    source_name_index, sink_name_index = _name_flow_indexes(edges)

    call_lines_by_pair: dict[tuple[str, str], list[int]] = defaultdict(list)
    for edge in edges:
        if not _is_taint_call_edge(edge):
            continue
        sites = _edge_call_sites(edge)
        if sites:
            call_lines_by_pair[
                (edge.get("src", ""), edge.get("dst", ""))
            ].extend(sites)

    # Step 3: Find sanitizer call sites — multi-label-aware so one
    # caller of a barrier function picks up every input_taint label it
    # sanitizes.
    sanitizer_callers: dict[str, dict[str, list[TaintSanitizer]]] = (
        defaultdict(dict)
    )
    _register_sanitizer_callers(
        edges, sanitizer_by_callee, sanitizer_callers, ambiguous_names,
    )

    # Step 4: For each source, BFS forward to find reachable sinks
    # without passing through sanitizers.
    findings: list[TaintFlowFinding] = []

    for caller_id, source_callee_id, taint_source in source_callers:
        taint_label = taint_source.taint_label

        # Choose BFS seed by source's start_at field. "caller" (default)
        # preserves legacy semantics: BFS from the call site of the source
        # function. "callee" seeds at the source callee itself — used by
        # synthetic entry-point sources (CLI handlers declared in
        # project-local catalogs) so the reachable set is exactly the
        # downstream of that one entry point, not everything reachable
        # from the dispatcher.
        seed_id = (
            source_callee_id
            if taint_source.start_at == "callee"
            else caller_id
        )

        # Phase 1: forward reachability, split by whether a sanitizer
        # intervened. See _reachability_past_sanitizers for why the sanitized
        # side is retained rather than pruned into silence.
        (
            reachable, parent, sanitized_reachable, sanitized_parent,
            barrier_sanitizers,
        ) = _reachability_past_sanitizers(
            seed_id, taint_label, forward_adj, sanitizer_callers,
        )

        # Phase 2: Check if any sink caller or sink callee is reachable
        for sink_node, sink_callee_id, taint_sink in _iter_sink_sites(
            sink_callers,
        ):
            # INV-lozat: one call is not a source->sink pair with itself.
            if _source_and_sink_are_one_call(
                caller_id, sink_node, source_callee_id, sink_callee_id,
                call_lines_by_pair,
            ):
                continue
            # INV-fumod shape (b): the name this source read cannot reach what
            # this sink writes, so the pair is false rather than unproven.
            if not _source_names_can_reach_sink(
                source_name_index.get((caller_id, source_callee_id)),
                sink_name_index.get((sink_node, sink_callee_id)),
            ):
                continue
            if sink_node in reachable:
                is_sanitized = False
                path = _reconstruct_path(parent, seed_id, sink_node)
            elif sink_node in sanitized_reachable:
                is_sanitized = True
                path = _reconstruct_path(
                    {**parent, **sanitized_parent}, seed_id, sink_node,
                )
            else:
                continue
            sanitized_by, sanitized_by_user = _attribute_sanitizers(
                path, barrier_sanitizers,
            ) if is_sanitized else ((), ())
            findings.append(TaintFlowFinding(
                taint_label=taint_label,
                source_symbol=seed_id,
                source_primitive=taint_source.name,
                source_module=taint_source.module,
                source_boundary=taint_source.source_boundary,
                sink_symbol=sink_callee_id,
                sink_primitive=taint_sink.name,
                sink_module=taint_sink.module,
                sink_zone=taint_sink.zone,
                # INV-kakad: the SITE is the (caller, callee) pair. Recording
                # the caller alone under-counts a function that calls four
                # different sinks; recording the callee alone under-counts one
                # sink reached from two callers. Both shapes are live.
                sink_call_sites=((sink_node, sink_callee_id),),
                sanitized=is_sanitized,
                sanitized_by=sanitized_by,
                sanitized_by_user_supplied=sanitized_by_user,
                confidence="approximate",
                analysis_method="structural",
                # INV-zidur. This is the STRUCTURAL propagator: it is selected
                # when the repo produced no DDG edges at all, so no walk was
                # possible for any flow here. Stamped rather than left blank
                # because "" is reserved for a finding deserialized from a map
                # written before the field existed, and conflating "no walk was
                # possible" with "this record predates the question" is the
                # absence-means-two-things shape the field exists to remove.
                walk_verdict=WALK_VERDICT_UNAVAILABLE,
                path=path,
            ))

    # INV-karud: every finding this arm emits is call-reachability-only, so
    # none of them is entitled to a pair claim. Collapsing HERE rather than in
    # the consumer keeps one home for the rule — a second consumer of
    # ``propagate_taint_*`` would otherwise get the raw n x m product back.
    return collapse_unadjudicated_flows(findings)


def _reachability_past_sanitizers(
    seed_id: str,
    taint_label: str,
    forward_adj: dict[str, set[str]],
    sanitizer_callers: dict[str, dict[str, list["TaintSanitizer"]]],
) -> tuple[
    set[str], dict[str, str | None], set[str], dict[str, str | None],
    dict[str, list["TaintSanitizer"]],
]:
    """Forward reachability, split by whether a sanitizer intervened.

    Returns ``(reachable, parent, sanitized_reachable, sanitized_parent)``.

    WHY BOTH SETS. The barrier used to prune the subtree beyond a sanitizer,
    which meant a protected flow produced exactly the output of a flow that
    does not exist: nothing. A reader could not distinguish "no path from this
    source to this sink" from "a path exists and your ``encrypt()`` call is
    what makes it safe" — and the second is what they need before deleting
    that call. ``TaintFlowFinding.sanitized`` existed for this and was written
    ``False`` at both and only construction sites, so ``verify_claims``'
    ``and not f.sanitized`` was a tautology and ``confirmed_safe`` was
    unreachable in production (owner ruling 2026-08-03: emit it labelled).

    UNSANITIZED WINS. ``sanitized_reachable`` excludes everything in
    ``reachable``, so a sink the taint reaches by *any* unprotected route is
    reported unsanitized even when another route encrypts. Labelling that
    ``sanitized`` would be the dangerous direction of wrong.

    ONE IMPLEMENTATION, TWO CALLERS. The structural and DDG propagators each
    carried a copy of this walk and had already drifted (the structural one
    tracked ``sanitized_nodes``, the DDG one dropped them on the floor). A
    barrier that means one thing in one pass and another in the other is the
    shape that produces "fixed in Python, still broken in Go" reports.

    THE SEED IS EXEMPT FROM THE BARRIER IN BOTH PASSES, and that exemption has
    a consequence worth naming rather than rediscovering. The seed must stay
    reachable — it is the taint origin, whether ``start_at`` puts it at the
    caller or the callee — and the same exemption means a sanitizer called
    *from* the seed function is never consulted here. So ``plain =
    decrypt(t); safe = encrypt(plain); write(safe)`` in one function reports an
    unsanitized flow about code that visibly sanitizes (WI-fasub).

    THAT IS NOT FIXABLE IN THIS FUNCTION, AND THE REASON IS STRUCTURAL. This
    walk sees a call GRAPH. "handler calls encrypt" and "handler calls write"
    are two edges with no order between them, and the graph is byte-identical
    whichever order the two calls occur in the source — so no amount of work on
    call-graph reachability can distinguish encrypt-then-write from
    write-then-encrypt. Answering it requires statement ordering inside the seed
    function, which is ``stmt_defuse`` (PR #203), and that reaches
    :func:`propagate_taint_ddg` alone. The fix therefore lives at that caller,
    keyed on the sanitizer call lines the registrar now records.

    CONSEQUENCE FOR THE STRUCTURAL PASS: it cannot honour same-function
    sanitization at all, for any language, permanently — a scope limit rather
    than a deferral, since :func:`propagate_taint_structural` has no statement
    data to be given. Every language without a def/use extractor is served by
    that pass, which is most of the catalogue. The limit is published in the
    emitted record by ``sanitizer_scope`` (see :mod:`.dataflow_scope`) rather
    than left in this docstring, because a reader of the OUTPUT is the one who
    needs it.
    """
    reachable: set[str] = set()
    barrier_nodes: list[str] = []
    # INV-pojib: WHICH sanitizer stopped the taint here. The object is already
    # in hand at the barrier test below and used to be discarded, so a verdict
    # could report "a sanitizer protects every route" without being able to name
    # it -- or to say the analysed repository is what supplied it.
    barrier_sanitizers: dict[str, list["TaintSanitizer"]] = {}
    parent: dict[str, str | None] = {seed_id: None}
    queue: deque[str] = deque([seed_id])

    while queue:
        node = queue.popleft()
        if node in reachable:
            continue  # pragma: no cover
        node_sanitizers = sanitizer_callers.get(node, {})
        if taint_label in node_sanitizers and node != seed_id:
            barrier_nodes.append(node)
            barrier_sanitizers[node] = node_sanitizers[taint_label]
            continue
        reachable.add(node)
        for neighbor in sorted(forward_adj.get(node, set())):
            if neighbor not in reachable and neighbor not in parent:
                parent[neighbor] = node
                queue.append(neighbor)

    # Second pass: what the taint reaches only AFTER being transformed. Seeded
    # at the barrier nodes themselves, since the sanitizer call site is where
    # the protected value comes into existence. Further sanitizers are not
    # barriers here — re-encrypting already-ciphertext changes nothing about
    # the fact that this route is protected.
    sanitized_reachable: set[str] = set()
    sanitized_parent: dict[str, str | None] = {}
    queue = deque()
    for node in barrier_nodes:
        if node in reachable:
            continue  # pragma: no cover
        sanitized_parent.setdefault(node, parent.get(node))
        queue.append(node)

    while queue:
        node = queue.popleft()
        if node in sanitized_reachable or node in reachable:
            # Defensive. Both conditions are already enforced at every enqueue
            # site — barrier seeds skip `reachable`, and neighbours are
            # enqueued only when absent from `reachable`, `sanitized_reachable`
            # AND `sanitized_parent` — so nothing can be queued twice today.
            # Kept because the alternative to a redundant guard here is an
            # infinite loop if a future edit relaxes one of those enqueue
            # conditions.
            continue  # pragma: no cover
        sanitized_reachable.add(node)
        for neighbor in sorted(forward_adj.get(node, set())):
            if neighbor in reachable or neighbor in sanitized_reachable:
                continue
            if neighbor not in sanitized_parent:
                sanitized_parent[neighbor] = node
                queue.append(neighbor)

    return (
        reachable, parent, sanitized_reachable, sanitized_parent,
        barrier_sanitizers,
    )


def _attribute_sanitizers(
    path: list[str],
    barrier_sanitizers: dict[str, list["TaintSanitizer"]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Which sanitizers a sanitized route actually crossed (INV-pojib).

    Reads the barrier map against THE ROUTE THAT WAS RECONSTRUCTED, not against
    every barrier the walk saw: a seed can reach several sinks by different
    routes, and naming a sanitizer the reported route never crossed would be a
    new way of saying something the analysis did not establish — the class of
    defect this attribution exists to close.

    Order follows the path, so the reported names read source-to-sink. The
    user-supplied names are returned as a SUBSET rather than a flag, so a
    verdict crossing both a shipped and a repo-supplied sanitizer can mark
    exactly the repo-supplied one.
    """
    named: list[str] = []
    user: list[str] = []
    for node in path:
        for sanitizer in barrier_sanitizers.get(node, ()):
            if sanitizer.qualified_name in named:
                continue
            named.append(sanitizer.qualified_name)
            if sanitizer.user_supplied:
                user.append(sanitizer.qualified_name)
    return tuple(named), tuple(user)


def _reconstruct_path(
    parent: dict[str, str | None],
    start: str,
    end: str,
) -> list[str]:
    """Reconstruct a path from start to end using parent pointers."""
    path = [end]
    current = end
    while current != start and current in parent and parent[current] is not None:
        current = parent[current]  # type: ignore[assignment]
        path.append(current)
    path.reverse()
    return path


# ---------------------------------------------------------------------------
# Field-sensitivity lite (ADR-0017 §7a)
# ---------------------------------------------------------------------------


def is_field_tainted(variable: str, tainted_vars: set[str]) -> bool:
    """Check if a variable name inherits taint from a tainted base.

    Field-sensitivity lite rules (ADR-0017 §7a):
    - If ``x`` is tainted, then ``x.field``, ``x.method``, ``x[key]`` are tainted.
    - If ``obj.field`` is tainted, only ``obj.field`` is tainted (not ``obj``).
    - Direct match: ``x`` in tainted_vars → True.
    - Field access: ``x.anything`` where ``x`` is in tainted_vars → True.

    Args:
        variable: Variable name to check (may contain dots for field access).
        tainted_vars: Set of currently tainted variable names.

    Returns:
        True if the variable is tainted (directly or via field access on
        a tainted base).
    """
    if variable in tainted_vars:
        return True

    # Check if this is a field access on a tainted base: x.field where x is tainted
    if "." in variable:
        base = variable.split(".")[0]
        if base in tainted_vars:
            return True

    return False


# ---------------------------------------------------------------------------
# DDG-backed taint propagation (ADR-0017 §3a, §3c-3d)
# ---------------------------------------------------------------------------


class EscapeSite(NamedTuple):
    """One place the §3a walk stopped knowing where a tainted value went.

    A ``NamedTuple`` rather than a bare pair because the LINE alone is not the
    fact a consumer needs. Two of the walk's escapes are *extraction* failures
    — the def/use graph never held the fact — and two are *classification*
    questions about a use the walk did see. Those have different owners and
    opposite remedies, yet a ``(symbol_id, line)`` record renders them
    identical, so a shape histogram taken over lines silently attributes
    extractor gaps to ADR-0017 §7b's scope exclusion. Tuple-shaped so the
    positional reads that the original pair supported keep working.

    ``reason`` is a bounded enum; see :data:`ESCAPE_REASONS`.
    """

    symbol_id: str
    line: int
    reason: str


#: Why the walk lost the value, one per ``escaped = True`` site. Bounded and
#: named here so a measurement can assert it partitions the population rather
#: than discovering a fifth cause by finding an unfamiliar string in a bucket.
#:
#: * ``source_undefined`` — the DDG recorded no definition at the SOURCE call
#:   line, so the walk was never handed anything to follow. An extraction gap
#:   (INV-lupav), not an escape: ``if err := do(); err != nil`` initializers
#:   are invisible to Go's def/use extractor.
#: * ``definition_unrecorded`` — a frontier entry the DDG holds no uses for.
#:   Defensive and unreachable as the code stands; see the branch comment.
#: * ``call_beside_heir`` — the taint DID continue along a chain still
#:   understood, but the same line also calls something no summary accounts
#:   for. One statement doing two things (WI-votom hole 2).
#: * ``no_heir`` — the use derived nothing the DDG tracked and no catalogued
#:   callee consumed it. This is the bucket ADR-0017 §7b's alias exclusion is
#:   invoked for, and the only one for which that invocation can be correct.
ESCAPE_REASONS = frozenset(
    {
        "source_undefined",
        "definition_unrecorded",
        "call_beside_heir",
        "no_heir",
    }
)


def _ddg_taint_reaches(
    symbol_id: str,
    source_lines: list[int],
    sink_lines: list[int],
    ddg_uses: Mapping[tuple[str, str, int], AbstractSet[int]],
    # Read-only, and typed as such. These were declared ``dict[..., frozenset]``
    # while every production caller builds ``defaultdict(set)`` — and because
    # ``dict`` is INVARIANT in its value type, that is not merely a stylistic
    # mismatch, it is three genuine type errors at the one call site that
    # matters. ``Mapping`` + ``AbstractSet`` says what the function actually
    # requires (iterate and membership-test, never mutate), which is satisfied
    # by both spellings.
    callees_at: Mapping[tuple[str, int], AbstractSet[str]] | None = None,
    summaries: Mapping[str, "FunctionSummary"] | None = None,
    defs_at: Mapping[tuple[str, int], AbstractSet[str]] | None = None,
    inherits: Mapping[tuple[str, int, str], AbstractSet[str]] | None = None,
    barrier_lines: AbstractSet[int] | None = None,
    forfeit_refutation: bool = False,
    escape_sites: list[EscapeSite] | None = None,
    credited_user_summaries: set[str] | None = None,
) -> bool | None:
    """Does a value defined at a source call reach a use at a sink call?

    TWO CALLERS ASK TWO QUESTIONS OF THIS ONE WALK. With no ``barrier_lines``
    it answers §3a's "is there a data dependence from source to sink" — which
    since WI-kabif both confirms a flow and, on ``False``, removes it. With the sanitizer call sites as barriers it
    answers "is there such a dependence that does NOT pass through a
    sanitizer", and the difference between the two runs is what earns
    ``sanitized`` for a same-function barrier (WI-fasub). Both readings rest on
    the same three-valued discipline below, which is why they share an
    implementation rather than growing a second walk that can drift from it.

    THREE-VALUED, and that is the load-bearing part. ``True`` means a data
    dependence was found; ``False`` means the walk ran to completion without
    finding one AND accounted for the tainted value at every step; ``None``
    means the value escaped somewhere the DDG cannot follow, so nothing is
    known either way.

    TWO RETURN VALUES, THREE CAUSES, and the third one is why this docstring
    is long. A negative walk means the value was never found to reach a sink,
    and that happens because (1) it genuinely goes nowhere near one, (2) it
    left the tracked chain into a container the DDG cannot follow, or (3) the
    construct that defined it was never modelled, so no use was ever recorded.
    Only (1) is exhaustion. (2) and (3) are both ignorance and both return
    ``None``.

    Cause (3) is the dangerous one and it was returning ``False`` until a
    review panel reproduced it: ``_ddg_taint_reaches("f", [1], [9], {})`` —
    an index holding nothing at all — reported the removal-licensing verdict.
    (2) at least leaves an escape to classify; (3) leaves silence, so no
    amount of work on the escape vocabulary would ever have surfaced it. The
    principled remedy is a coverage gate — forfeit refutation for any function
    whose CFG statement extents fail to cover every call node in its body —
    which catches the class mechanically instead of enumerating known gaps.
    Treating an unrecorded definition as unknown is the conservative floor
    under that gate, not a substitute for it.

    Collapsing ``None`` into ``False`` — the obvious implementation — silently
    converts ADR-0017 §7b's exclusion of alias analysis into false negatives.
    Measured on pretix: ``vouchers_send`` does
    ``voucher_list.append(vouchers.pop(0))`` and later
    ``bulk_update(voucher_list, ...)``; the taint leaves the tracked definition
    chain the moment it enters the list, and a two-valued walk "proves" the
    flow absent. Three of nine verified removals in the first cohort arm were
    this shape.

    WHY NOTHING IS REMOVED TODAY, EVEN ON ``False``. Distinguishing a use that
    *terminates* the taint (``fmt.Printf(cwd)`` — argument consumed, result
    discarded) from one that *propagates* it (``lst.append(x)`` — argument
    escapes into the receiver) requires knowing whether the callee mutates its
    arguments. That is precisely ADR-0017 §4 function summaries, and §3a's own
    step 3 says so: "At call sites, apply function summaries (§4)."

    THAT PARAGRAPH USED TO END "§4a and §4b have zero production callers, so
    the information does not exist at runtime". Half of it is now false and it
    was load-bearing, so it is corrected rather than deleted: **§4b runs in
    production today** — ``propagate_taint_ddg`` calls
    ``load_function_summaries()`` on its default path, and the live index
    holds 38 terminating summaries (``fmt.Printf``, ``log.Println``,
    ``builtins.print``, ``console.log``, …) that this walk consults. §4a
    (``infer_summary``) still has zero production callers.

    ``False`` IS ALSO NO LONGER INERT. Since PR #214 a ``False`` from this
    walk earns the ``sanitized`` label on the barrier arm, and a sanitized
    flow is dropped from a claim's violation set — so an unearned ``False``
    deletes a real finding. Any change to the escape accounting below must
    state which direction it moves ``False``, and only the direction that
    produces FEWER of them is safe without new evidence.

    Inclusion is still decided by call-graph reachability: the walk CONFIRMS
    and never refutes, earning the ``precise`` label where it finds a
    dependence. Removal authority is WI-kabif's, and it remains behind
    INV-busis.

    The ADR-0017 §3a forward walk, over one function's reaching-definition
    edges. Seeds at the lines where the taint source is called — the value it
    returns is defined there — and follows def→use edges transitively: if a
    tainted value is used at line U, then whatever is *defined* at U inherits
    the taint, so U becomes a new seed.

    Returns True as soon as a tainted value is used at a line where the sink is
    called, which is the ADR's step 5 read correctly. The ADR words it "if
    tainted data reaches a sink", and implemented as "a tainted definition
    reaches the sink's basic block" it removes almost nothing: the definition
    and the sink call routinely share a block, so the test passes for a
    function that calls a source and a sink on unrelated data. The load-bearing
    reading is that a tainted variable is an ARGUMENT AT THE SINK CALL SITE,
    and a use recorded at the sink's line is exactly that.

    Intraprocedural by construction — every edge belongs to one function, so
    this can only adjudicate a flow whose source and sink share a function.
    Callers must fall back to call-graph reachability for everything else,
    which is not a shortcut but ADR-0017 §7b's exclusion of alias and
    whole-program analysis.

    Args:
        symbol_id: The function whose DDG is being walked.
        source_lines: Lines where the taint source is called.
        sink_lines: Lines where the sink is called.
        ddg_uses: ``(symbol_id, variable, def_line) -> {use_line, ...}``.
        defs_at: ``(symbol_id, line) -> {variable, ...}`` defined at that line;
            used to seed on the source call site's own definitions.
        inherits: ``(symbol_id, line, used_variable) -> {variable, ...}`` —
            the variables a statement at that line defines while consuming
            ``used_variable``. This is what keeps two variables defined on one
            line from laundering taint between each other.
        barrier_lines: Lines where a SANITIZER is called. A tainted value
            consumed at one of these is accounted for — it went into a barrier
            and what came out carries a different taint label — so the walk
            stops following it there WITHOUT flagging an escape. Running the
            walk twice, once with barriers and once without, is what lets
            :func:`propagate_taint_ddg` tell "every data route to this sink
            passes through the sanitizer" from "some route does not" (WI-fasub).
            Empty by default, so the §3a adjudicating walk is unaffected.
        forfeit_refutation: The caller has established that this function's
            CFG statement extents do not cover every call node in its AST
            body — i.e. the def/use extractor did not see part of it. The
            walk then may not return ``False`` for this function and returns
            ``None`` instead (WI-joluk). Blocks ``False`` only, never
            ``True``. Defaults to ``False`` so turning the gate on is a
            deliberate act at each call site rather than a tree-wide
            behaviour change on landing.
        escape_sites: Optional out-param. When given, an :class:`EscapeSite`
            is appended for every point at which the walk lost track of the
            value, in encounter order. This exists so INV-busis's shape
            split can be taken from the walk itself rather than from a
            re-derivation of it: the instrument that produced the filed split
            lives outside the repo and no longer matches this signature, which
            the item records as its own durability hazard. Each site names
            WHICH of the four branches below fired, because a line alone
            cannot separate "the DDG never gave the walk anything here"
            (extraction gap) from "the walk followed the value and lost it"
            (the §7b classification question) — and folding those is how the
            expression-read family was first priced. Purely observational —
            the verdict is identical whether or not it is passed.

    Returns:
        True if a tainted value is used at a line where the sink is called;
        False if the walk exhausted every reachable definition with each step
        accounted for; None if the value escaped tracked ground OR was never
        recorded in the first place. Only False may ever license a removal.
    """
    targets = set(sink_lines)
    barriers = barrier_lines or frozenset()
    seen: set[tuple[str, int]] = set()
    escaped = False

    # SEED ON VARIABLES, NOT LINES. The source's return value is whatever the
    # call site defines; if the DDG recorded no definition there, nothing is
    # known about where the value went (INV-lupav).
    frontier: list[tuple[str, int]] = []
    for line in source_lines:
        seeds = (defs_at or {}).get((symbol_id, line))
        if not seeds:
            escaped = True
            if escape_sites is not None:
                escape_sites.append(
                    EscapeSite(symbol_id, line, "source_undefined")
                )
            continue
        frontier.extend((var, line) for var in sorted(seeds))

    while frontier:
        var, line = frontier.pop()
        if (var, line) in seen:
            continue
        seen.add((var, line))
        uses = ddg_uses.get((symbol_id, var, line))
        if not uses:  # pragma: no cover - unreachable; see below
            # DEFENSIVE, AND UNREACHABLE AS THE CODE STANDS. Two invariants
            # make it so: every seed comes from `defs_at`, which is built from
            # the same edges as `ddg_uses`, and every frontier append below is
            # membership-tested against `ddg_uses` first. So a popped
            # `(var, line)` always has a non-empty use set.
            #
            # Kept rather than deleted because the alternative to a redundant
            # guard here is a TypeError on `uses & targets` the first time a
            # future edit appends a pair without the membership test — and the
            # correct answer in that case is this one: a definition the DDG
            # holds nothing about is IGNORANCE, not absence (INV-lupav), so it
            # must escape rather than fall through to the removal-licensing
            # `False`.
            #
            # The untracked-source-call-site case that INV-lupav was filed for
            # is now caught at SEEDING instead — `if not seeds` above — which
            # is the earlier and more precise place for it: the DDG holding no
            # definition at a source call line is exactly "was never given
            # anything", and `cfg_nodes/go.yaml` self-documents the extraction
            # gap that produces it (`if err := do(); err != nil` initializers
            # invisible to def/use; 700 of caddy's 6,596 `if` statements carry
            # a call there).
            escaped = True
            if escape_sites is not None:
                escape_sites.append(
                    EscapeSite(symbol_id, line, "definition_unrecorded")
                )
            continue
        if uses & targets:
            return True
        for use_line in uses:
            if use_line in barriers:
                # ACCOUNTED FOR, NOT ESCAPED. The tainted value was consumed by
                # a sanitizer here, so what continues from this line carries the
                # barrier's OUTPUT label, not the one being tracked. Stopping
                # without setting ``escaped`` is the whole point: it lets an
                # exhausted barrier walk return ``False`` — "every route to the
                # sink went through the sanitizer" — which is the positive
                # evidence WI-fasub's fix requires before suppressing a
                # violation.
                #
                # KNOWN RESIDUAL, stated because the alternative is folklore.
                # Dropping every heir at this line is wrong for a statement
                # that BOTH sanitizes and rebinds the raw value —
                # ``safe, leak = encrypt(plain), plain`` — where ``leak`` is
                # never followed and a later use of it would go unreported. The
                # conservative alternative (treat >1 heir as ambiguous and
                # escape) was measured against the idioms that actually occur
                # and rejected: Go's ``ct, err := aead.Seal(...)`` and Rust's
                # ``let (ct, tag) = ...`` bind two names from EVERY sanitizer
                # call in those languages, so it would forfeit the fix wherever
                # a multiple-return language is involved — trading a rare false
                # negative for a systematic false positive, which is the defect
                # being fixed here. The two heirs are indistinguishable from the
                # DDG alone (one statement row, ``defines=(a, b) uses=(x,)`` in
                # both cases), so closing it needs the argument-position
                # information ADR-0017 §7b excludes.
                continue
            # WHICH VARIABLE INHERITS THE TAINT HERE? Only one defined at this
            # line by a statement that actually CONSUMES `var`. Following the
            # line instead of the variable is what made the label unearned:
            # `keep = str(server); path = name` defines two variables at one
            # line, and a line-keyed step credited the taint in `server` with
            # reaching everything `path` later touches.
            followed = False
            for heir in sorted((inherits or {}).get((symbol_id, use_line, var), ())):
                if (symbol_id, heir, use_line) in ddg_uses:
                    if (heir, use_line) not in seen:
                        frontier.append((heir, use_line))
                    followed = True
            if followed:
                # The taint continues along a chain we still understand — but
                # ONE STATEMENT CAN DO TWO THINGS. ``acc.append(x); y = x``
                # both hands ``x`` to a receiver we cannot follow and derives
                # a tracked ``y``. Following the heir accounts for the heir,
                # not for the statement, and skipping the escape question here
                # is how an unearned ``False`` was produced (WI-votom hole 2).
                #
                # Only two things license skipping it, and they are enumerated
                # as the PERMITTING cases rather than the blocking ones, so a
                # callee shape nobody has modelled reads as "ask the question"
                # instead of "assume it is fine":
                #   1. no call at this line at all — a pure rebinding, so the
                #      heir really is the value's only exit; and
                #   2. a catalogued callee that consumes and discards, which
                #      is exactly what ``_use_site_terminates`` decides.
                #
                # Direction, deliberately: this can only produce FEWER
                # ``False``s. Since PR #214 a ``False`` earns ``sanitized``
                # and a sanitized flow is dropped from a claim's violation
                # set, so fewer ``False``s means strictly MORE surviving
                # violations. A fix here can never suppress a finding.
                if not (callees_at or {}).get((symbol_id, use_line)):
                    continue
                if _use_site_terminates(
                    symbol_id, use_line, callees_at, summaries,
                    credited_user_summaries,
                ):
                    continue
                escaped = True
                if escape_sites is not None:
                    escape_sites.append(
                        EscapeSite(symbol_id, use_line, "call_beside_heir")
                    )
                continue
            # The tainted value is consumed at a line that defines nothing the
            # DDG tracked, or defines only variables no statement derived from
            # `var` — it went into a container, a call argument, a field, or a
            # closure. ADR-0017 §7b excludes alias analysis, so we cannot say
            # where it went next. That is unknown, not absent — UNLESS §4 can
            # tell us the callee consumed it.
            if _use_site_terminates(
                symbol_id, use_line, callees_at, summaries,
                credited_user_summaries,
            ):
                continue
            escaped = True
            if escape_sites is not None:
                escape_sites.append(
                    EscapeSite(symbol_id, use_line, "no_heir")
                )
    if escaped:
        return None
    if forfeit_refutation:
        # WI-joluk. The DDG facts closed, but they are not the whole picture:
        # the caller has evidence that the CFG recorded no statement covering
        # some call node in this function's body, so the def/use extractor
        # demonstrably did not see part of it. An exhausted walk over an
        # incomplete graph is not the same fact as an exhausted walk, and
        # ``False`` is the only verdict that may license removing a reported
        # flow.
        #
        # WHY THIS IS COVERAGE-GATED AND NOT GAP-ENUMERATED. The population is
        # whatever a language's def/use extractor does not model, which is not
        # knowable from inside this walk — it cannot tell a construct nobody
        # taught it about from one that genuinely has no uses. A fix shaped as
        # "handle the known gaps" is a list that decays silently, and it
        # decays in the direction that deletes findings.
        #
        # Blocks ``False`` ONLY. A ``True`` above is positive evidence of a
        # dependence the walk actually found, and an incomplete picture cannot
        # unmake it — downgrading that would turn a safety gate into a recall
        # regression.
        return None
    return False


def _summary_terminates(summary: "FunctionSummary") -> bool:
    """Does this callee CONSUME its arguments without passing them anywhere?

    True only for a side-effecting function that returns nothing derived from
    its arguments, mutates no receiver, invokes no callback and transforms no
    taint label — ``fmt.Printf``, ``log.Println``, ``os.Exit``. Every other
    shape leaves the value somewhere the intraprocedural walk cannot follow.

    Deliberately conservative in the SAFE direction. A false "terminates" lets
    the walk close a branch that is really open, which (once refutation acts on
    ``False``) deletes a real security finding; a false "does not terminate"
    only leaves an unknown unknown. Those costs are not symmetric, so every
    clause here is a conjunction and any doubt reads as "no".
    """
    return bool(
        summary.side_effect
        and not summary.param_to_return
        and not summary.param_to_self
        and not summary.mutates_self
        and summary.callback is None
        and not summary.sanitizes
    )


def _use_site_terminates(
    symbol_id: str,
    use_line: int,
    # Read-only, and widened for the same reason as its caller's parameters:
    # ``dict`` is invariant in its value type, so declaring ``frozenset`` here
    # rejects the ``defaultdict(set)`` every production caller builds.
    callees_at: Mapping[tuple[str, int], AbstractSet[str]] | None,
    summaries: Mapping[str, "FunctionSummary"] | None,
    credited_user_summaries: set[str] | None = None,
) -> bool:
    """Does EVERY call at this line consume the tainted value and stop?

    ADR-0017 §3a step 3 — "at call sites, apply function summaries (§4)" — is
    exactly this. Three properties, each load-bearing:

    **Uncatalogued means unknown, not "assume it propagates".**
    ``function_summaries.get_default_summary`` returns
    ``param_to_return = {0..9: True}``, and using it here would make an EMPTY
    catalogue change behaviour: every callee would read as passing the taint
    on. Returning False for an unknown callee instead means a catalogue
    covering nothing reproduces the pre-§4 output exactly, so every behaviour
    change is attributable to an entry somebody deliberately wrote.

    **All-or-nothing.** Several calls can share a line (``log(transform(x))``)
    and the value may have gone into any of them. Closing the branch because
    one of them terminates would be a guess dressed as an analysis.

    **Qualified names only.** ``load_function_summaries`` also indexes every
    entry under its bare last component, and those aliases include ``log``,
    ``map``, ``filter``, ``parse``, ``get``, ``info`` and ``error`` — roughly
    two fifths of the loaded index. A short-name match would let
    ``audit.log(secret)`` resolve to ``console.log`` and read as terminating.
    Since a false "terminates" removes a real finding, the alias index is
    never consulted; the caller passes qualified names and the lookup is
    exact.

    The exact alias count is deliberately not written here: it moved 33 → 108
    → 113 across two catalogue edits in two days, so a hardcoded figure is a
    rationale that decays with nobody deciding anything (L50). The *property*
    this argument rests on — that the index contains bare short names capable
    of colliding — is pinned executably by
    ``test_alias_index_contains_dangerous_short_names``.
    """
    if not callees_at or not summaries:
        return False
    callees = callees_at.get((symbol_id, use_line))
    if not callees:
        return False
    for qualified in callees:
        summary = summaries.get(qualified)
        if summary is None or not _summary_terminates(summary):
            return False
    # ADR-0047 ruling 10 (WI-sofov). Record WHOSE word this closure rests on,
    # and only once the line has actually terminated -- an entry consulted on a
    # line that then escapes was not credited with anything.
    #
    # THIS IS COLLECTED HERE BECAUSE THERE IS NOWHERE ELSE TO READ IT FROM. A
    # sanitized flow still surfaces as a finding carrying
    # ``sanitized_by_user_supplied``, so the existing caveat reads it off the
    # finding. A TERMINATED branch produces NO finding -- that is the whole
    # point of terminating -- so if the walk does not say what it credited,
    # nothing downstream can.
    if credited_user_summaries is not None:
        for qualified in callees:
            summary = summaries.get(qualified)
            if summary is not None and getattr(summary, "user_supplied", False):
                credited_user_summaries.add(qualified)
    return True


def propagate_taint_ddg(
    ddg_edges: list[DdgEdge],
    call_edges: list[dict[str, Any]],
    sources: list[TaintSource],
    sinks: list[TaintSink],
    sanitizers: list[TaintSanitizer],
    ddg_symbols: set[str] | None = None,
    ambiguous_names: frozenset[str] = frozenset(),
    language: str = "",
    function_summaries: dict[str, "FunctionSummary"] | None = None,
    stmt_defuse: dict[
        str, list[tuple[int, tuple[str, ...], tuple[str, ...]]]
    ] | None = None,
    forfeit_refutation: set[str] | None = None,
    credited_user_summaries: set[str] | None = None,
    refuted_flows: list["TaintFlowFinding"] | None = None,
) -> list[TaintFlowFinding]:
    """DDG-backed taint-flow propagation with mixed-coverage analysis.

    When DDG (data dependence graph) edges are available for a function,
    taint propagation uses variable-level precision instead of symbol-level
    BFS. For functions without DDG data, structural reachability bridges
    the gap.

    Algorithm (ADR-0017 §3a):
    1. Identify taint source call sites from call_edges.
    2. For source functions with DDG data: walk forward through DDG edges
       to see which variables carry taint.
    3. At call sites within DDG-analyzed functions, check if the callee
       is a sanitizer (transforms taint) or a sink (reports finding).
    4. For functions without DDG data on the path, fall back to structural
       reachability.

    Mixed-coverage verdict (ADR-0017 §3c-3d):
    - If source AND sink functions both have DDG data: ``confidence="precise"``
    - If either lacks DDG data: ``confidence="approximate"``
    - Structural-only findings (no DDG anywhere): fall back entirely to
      ``propagate_taint_structural()``.

    Args:
        ddg_edges: DdgEdge objects from ``solve_reaching_defs()``.
        call_edges: Edge dicts with "src", "dst", "type" keys.
        sources: Taint source definitions.
        sinks: Taint sink definitions.
        sanitizers: Taint sanitizer definitions.
        ddg_symbols: Set of symbol IDs that have DDG analysis data.
            Functions in this set use DDG-precision; others use structural.
        ambiguous_names: Short names the catalog flags as ambiguous; a bare
            ambiguous callee with no usable module hint is not matched to a
            source/sink (WI-razol).

    Returns:
        List of TaintFlowFinding objects.
    """
    if not ddg_edges or not sources or not sinks:
        return []

    analyzed = ddg_symbols or set()

    # Forward index for the §3a walk: (function, def line) → lines that use
    # the value defined there.
    #
    # Keyed on ``symbol_id``, NOT ``def_block``. Block ids are function-local —
    # ``bb_5`` occurs in every function — so once edges from a whole repo are
    # aggregated into one list a block id identifies nothing. The predecessor
    # of this index was keyed ``(def_block, variable)`` and compared a block id
    # against a symbol id, which cannot match; it was also never read.
    #
    # KEYED ON THE VARIABLE AS WELL AS THE LINE (INV-sadah). The first version
    # keyed `(symbol_id, def_line)` and discarded `DdgEdge.variable`, which
    # merged the use-sets of every variable defined on one line. Measured on a
    # real fixture: `keep = str(server); path = name` emits
    # `keep def@7 -> use@9` and `path def@7 -> use@8`, and the merged entry
    # `def_line 7 -> {8, 9}` let a walk carrying the taint in `server` inherit
    # `path`'s use of the sink at line 8 — stamping "precise" on a data
    # dependence that does not exist.
    #
    # Lines are still HALF the key because that is what makes the index
    # composable with call sites: a call edge records the LINE it occurs on, so
    # "is the tainted value an argument here" stays a set membership test.
    ddg_uses: dict[tuple[str, str, int], set[int]] = defaultdict(set)
    defs_at: dict[tuple[str, int], set[str]] = defaultdict(set)
    for ddg_edge in ddg_edges:
        ddg_uses[
            (ddg_edge.symbol_id, ddg_edge.variable, ddg_edge.def_line)
        ].add(ddg_edge.use_line)
        defs_at[(ddg_edge.symbol_id, ddg_edge.def_line)].add(ddg_edge.variable)

    # (symbol, line, consumed variable) -> variables that inherit from it.
    #
    # Variable-keying alone does NOT fix the conflation, and this is the half
    # that does. `path` genuinely IS defined at line 7, so separating the index
    # entries still leaves "which variable defined here inherits from
    # `server`?" unanswered — and the edge set cannot answer it. The statement's
    # own defines/uses can: `keep = str(server)` consumes `server`,
    # `path = name` does not.
    # (caller, callee) → every line that call occurs on. A caller may invoke
    # the same callee more than once, and a flow is real if the taint reaches
    # ANY of those call sites, so this is a list rather than a single line.
    # ``_edge_call_sites`` owns the parse — see its docstring for why one edge
    # does not mean one line.
    call_lines: dict[tuple[str, str], list[int]] = defaultdict(list)
    callee_names: dict[tuple[str, int], set[str]] = defaultdict(set)
    for edge in call_edges:
        sites = _edge_call_sites(edge)
        if sites:
            call_lines[(edge.get("src", ""), edge.get("dst", ""))].extend(sites)
            # (function, line) → the QUALIFIED names called there, for §4.
            # This is the index `meta.call_lines` exists to make possible:
            # "which callee is invoked at line U" was unanswerable for every
            # call site but the first while one edge carried one line.
            #
            # Through the PROVENANCE GATE, not `_qualified_callee` directly:
            # this index feeds a lookup against SHIPPED stdlib summaries, and a
            # resolved first-party callee whose key collides with one must not
            # be allowed to close a branch (WI-zumud).
            qualified = _catalogue_key_for_edge(edge)
            if qualified:
                src_id = edge.get("src", "")
                for site in sites:
                    callee_names[(src_id, site)].add(qualified)

    # INV-fumod: a definition does NOT inherit taint across an I/O boundary.
    #
    # Built AFTER ``callee_names`` because it now asks a question about the
    # defining line's callee, and the two indexes were previously built in the
    # opposite order for no reason but history.
    #
    # THE RULE, and it is the 0001 rubric's own tie-break made executable:
    # *taint flows through in-program computation, not through an external
    # resource selected by the tainted value*. An I/O primitive's return value
    # comes from the OTHER SIDE of the boundary — a handle on a resource the
    # argument merely NAMED, or bytes the argument merely ADDRESSED — so it is
    # not a computation on that argument.
    #
    # MEASURED, with a control that discriminates. `out = open(args.outfile,
    # "w"); out.write("a constant banner")` reported TWO findings, `open` and
    # `file.write`, where only the first is earned: nothing tainted is written.
    # The control `out = open("/tmp/fixed.txt", "w"); out.write(args.payload)`
    # reports `file.write` and MUST keep reporting it — there the tainted value
    # reaches the write's own argument. The tool was already internally
    # inconsistent about this, naming `open` correctly and then crediting the
    # handle as well, which is what INV-fumod's statement calls out.
    #
    # DERIVED FROM THE CATALOGUE, NOT CURATED. Every I/O primitive is already
    # enumerated per language; a hand-written list of "opening" calls would be
    # the second home for that fact and would be wrong the moment a row is
    # added. It costs nothing in recall where the far side is itself a source:
    # `resp = requests.get(url)` stops inheriting `url`'s label and instead
    # carries `untrusted_input` from the net_recv row, which is the more
    # accurate statement of what `resp` holds.
    _io_names: frozenset[str] = frozenset()
    if language:
        from .io_boundary import load_catalog
        _io_names = frozenset(
            f"{p.module}.{p.name}" if p.module else p.name
            for p in load_catalog(language).primitives
        )
    inherits: dict[tuple[str, int, str], set[str]] = defaultdict(set)
    for sym_id, statements in (stmt_defuse or {}).items():
        for line, defines, uses in statements:
            if not defines:
                continue
            if callee_names[(sym_id, line)] & _io_names:
                continue
            for used in uses:
                inherits[(sym_id, line, used)].update(defines)

    # ADR-0017 §4b declared summaries, QUALIFIED KEYS ONLY.
    # ``load_function_summaries`` also indexes every entry under its bare last
    # component (``console.log`` → ``log``); an entry is its own qualified key
    # exactly when the key equals ``summary.function``, so this filter drops
    # the alias index without re-deriving what the loader parsed. The aliases
    # are dangerous here specifically: a false "this callee terminates the
    # taint" removes a real finding, and ``log`` / ``map`` / ``filter`` /
    # ``parse`` collide with almost anything.
    if function_summaries is None:
        from .function_summaries import load_function_summaries
        function_summaries = load_function_summaries()
    summaries = {
        k: v for k, v in function_summaries.items() if k == v.function
    }

    # Index sources, sinks, sanitizers by name (same as structural) — a list
    # per name so _lookup_named_entry can disambiguate by module/ambiguity.
    source_by_callee = _build_callee_index(sources)
    sink_by_callee = _build_callee_index(sinks)
    sanitizer_by_callee = _build_sanitizer_index_multi(sanitizers)

    # Build call-graph adjacency for structural fallback
    forward_adj, _reverse_adj = _build_adjacency(call_edges)

    # INV-fumod shape (b): the same two indexes the structural arm builds.
    # Built from ``call_edges`` because that is where the analyzer's env-read
    # and redirect edges live — ``ddg_edges`` carry def-use, not I/O meta.
    source_name_index, sink_name_index = _name_flow_indexes(call_edges)

    # Step 1: Find source call sites (module + ambiguous_names aware — WI-razol)
    source_callers: list[tuple[str, str, TaintSource]] = []
    for edge in call_edges:
        if not _is_taint_call_edge(edge):
            continue
        matched = _match_propagation_entry(
            source_by_callee, edge["dst"], ambiguous_names,
            call_construct=edge.get("meta", {}).get("call_construct"),
            is_resolved=edge.get("is_resolved", True),
            language=language,
            io_modes=call_site_modes(edge.get("meta")),
            io_target_kinds=call_site_target_kinds(edge.get("meta")),
        )
        if matched and _source_call_can_mint_taint(edge):
            # WI-lipis: the ddg arm asks the identical question through the
            # identical predicate. Two copies is how the sink side's call
            # family drifted across three consumers.
            source_callers.append((edge["src"], edge["dst"], matched))

    # INV-sukoh: one expression, one flow. ``os.environ.get(...)`` emits an
    # attribute-reference edge AND a call edge, so the parent row and its own
    # slot-family row both match the single read.
    source_callers = subsume_slot_family_parents(source_callers)

    # Step 2: Find sink call sites (module + ambiguous_names aware — WI-razol)
    sink_callers: dict[str, list[tuple[str, TaintSink]]] = defaultdict(list)
    for edge in call_edges:
        if not _is_taint_call_edge(edge):
            continue
        matched = _match_propagation_entry(
            sink_by_callee, edge["dst"], ambiguous_names,
            call_construct=edge.get("meta", {}).get("call_construct"),
            is_resolved=edge.get("is_resolved", True),
            language=language,
            io_modes=call_site_modes(edge.get("meta")),
        )
        if matched:
            # ``sink_site``, not ``site``: the call-line loop above binds
            # ``site`` to an ``int`` in this same function, so reusing the
            # name gave one variable two unrelated types and made mypy report
            # the membership test below as a non-overlapping container check
            # — i.e. as dead — when it is the deduplication this loop rests
            # on. The structural propagator's identical block keeps ``site``,
            # because there the name is not already taken.
            if not _sink_call_can_carry_taint(edge):
                # INV-fubag, through the SHARED predicate: the structural arm
                # applies the identical gate. Two copies of this question is
                # how the call-family set drifted across three consumers.
                continue
            sink_site = (edge["dst"], matched)
            if sink_site not in sink_callers[edge["src"]]:
                sink_callers[edge["src"]].append(sink_site)

    # INV-sukoh, same as the structural arm: the parent attribute row and its
    # own slot family both match one ``sys.stdout.write(x)``.
    _subsume_sink_sites(sink_callers)

    # Step 3: Find sanitizer call sites — through the SHARED helper, so the
    # INV-finoh resolution-/kind-aware gate applies here too.
    #
    # This used to be a private copy that matched on bare short name with no
    # is_resolved, call_construct or ambiguous_names filter. Because a
    # phantom barrier PRUNES the walk, the copy did not merely miss
    # sanitizers — an unrelated unresolved `x.encrypt()` bound
    # `Fernet.encrypt` and silently deleted a real flow. That is a false
    # negative, the expensive direction for a security tool, and it was live
    # on the path verify-claims runs Python through. INV-finoh's own filing
    # named this site; the fix landed only at the structural one.
    sanitizer_callers: dict[str, dict[str, list[TaintSanitizer]]] = (
        defaultdict(dict)
    )
    # (caller, input_taint) → the lines the barrier is called on, for WI-fasub's
    # same-function check. Collected by the shared registrar so it cannot
    # disagree with the barrier set above about what counts as a sanitizer.
    sanitizer_lines: dict[tuple[str, str], list[int]] = {}
    _register_sanitizer_callers(
        call_edges, sanitizer_by_callee, sanitizer_callers, ambiguous_names,
        sanitizer_lines=sanitizer_lines,
    )

    findings: list[TaintFlowFinding] = []

    for caller_id, source_callee_id, taint_source in source_callers:
        taint_label = taint_source.taint_label
        # Seed selection mirrors the structural pass — see propagate_taint_structural.
        seed_id = (
            source_callee_id
            if taint_source.start_at == "callee"
            else caller_id
        )
        # The function the source is called FROM, which is what the DDG is
        # keyed on. This is ``caller_id`` regardless of ``start_at``: a
        # ``callee`` seed relocates where the BFS begins, not where the call
        # physically occurs.
        source_fn = caller_id
        source_call_lines = call_lines.get((caller_id, source_callee_id), [])
        fn_has_ddg = source_fn in analyzed

        # Structural BFS for reachability (used for mixed-coverage), through
        # the SHARED helper — this pass and the structural one had already
        # drifted on the sanitizer barrier, which is how a barrier ends up
        # meaning one thing in Python and another in Go.
        (
            reachable, parent, sanitized_reachable, sanitized_parent,
            barrier_sanitizers,
        ) = _reachability_past_sanitizers(
            seed_id, taint_label, forward_adj, sanitizer_callers,
        )

        # Check sinks
        for sink_node, sink_callee_id, taint_sink in _iter_sink_sites(
            sink_callers,
        ):
            # INV-lozat: one call is not a source->sink pair with itself. This
            # is NOT the §3a walk refuting -- that walk removes only on its own
            # `unconfirmed` verdict, and its `sink_before_source` blocker stays
            # recorded, never acted on.
            if _source_and_sink_are_one_call(
                caller_id, sink_node, source_callee_id, sink_callee_id,
                call_lines,
            ):
                continue
            # INV-fumod shape (b), the SECOND home of this question. A language
            # with no DDG still reaches findings through this arm (bash reports
            # analysis_method "structural" with walk "unavailable" from here),
            # so gating only the structural propagator left the item's own
            # instance standing while every unit test passed.
            if not _source_names_can_reach_sink(
                source_name_index.get((caller_id, source_callee_id)),
                sink_name_index.get((sink_node, sink_callee_id)),
            ):
                continue
            is_sanitized = False
            # INV-pojib: the sanitizer the DDG arm credited, if that is the arm
            # that fired. Reset per sink site, because two sinks in one function
            # can be adjudicated differently.
            ddg_barrier: list["TaintSanitizer"] = []
            if sink_node not in reachable:
                if sink_node not in sanitized_reachable:
                    continue
                is_sanitized = True

            # ADR-0017 §3a. The DDG can adjudicate exactly one shape: a sink
            # called in the SAME function as the source, where that function
            # has reaching-definition coverage. There the walk is authoritative
            # and may REMOVE a flow — structural reachability is trivially true
            # for such a pair (the two callers are the same symbol), so it has
            # no way to tell "the source's value is passed to the sink" apart
            # from "both happen to be called here".
            #
            # For every other shape — different functions, or no coverage —
            # the DDG has nothing to say, because it is intraprocedural. Those
            # keep call-graph reachability and are labelled as such. Silently
            # dropping them would convert ADR-0017 §7b's declared limitation
            # into false negatives, the expensive direction for a security
            # tool.
            # TWO SOUNDNESS GUARDS, both of which exist because the walk may
            # only REFUTE a flow on positive evidence. Neither is tuning; each
            # was added after a verified false negative on real code, and a
            # false negative is the expensive direction for a security tool.
            #
            # (1) The source's value must actually be tracked. If the DDG
            #     recorded no use of whatever the source call defines, we know
            #     nothing about where that value went — absence of evidence,
            #     not evidence of absence. caddy's printEnvironment binds
            #     `for _, v := range os.Environ()`; the Go CFG mapping's loop
            #     hook never names the range clause, so `v` has no definition
            #     in the DDG at all (WI-losod), and without this guard the
            #     walk "proved" that a literal `fmt.Println(v)` on the next
            #     line was unreachable.
            #
            # (2) A sink call site recorded BEFORE the source cannot be the
            #     one that consumes it, and — crucially — is not necessarily
            #     the only one. The call graph emits ONE edge per
            #     (caller, callee) pair, so ``line`` is *a* line where that
            #     callee is invoked, not every line. printEnvironment calls
            #     fmt.Printf twelve times and the edge records only the first
            #     (454); the tainted call at 469 is invisible. When the
            #     recorded line precedes the source, later call sites we
            #     cannot see may well receive the taint, so the absence of a
            #     dependence to the one line we can see licenses nothing.
            adjudicated = False
            # INV-zidur. ``adjudicated`` is the CONFIRMATION question and stays
            # a bool because every consumer of it below asks exactly that. It is
            # deliberately NOT the removal question: removal reads ``verdict``
            # further down, so that ``False`` and ``None`` stay distinguishable
            # right up to the point one of them acts. The
            # walk's raw three-valued answer, and whether it ran at all, are
            # kept alongside so the label can say which of the three
            # non-confirming outcomes this was.
            walk_result: bool | None = None
            walk_ran = False
            blocked_by = ""
            if not fn_has_ddg:
                pass
            elif sink_node != source_fn:
                # §3a is intraprocedural by construction. WI-kabif's own
                # filing predicted this would dominate ("69 percent of flows
                # have source and sink in different functions"), and it is the
                # blocker §4 function summaries exist to lift.
                blocked_by = WALK_BLOCKED_CROSS_FUNCTION
            elif not source_call_lines:
                blocked_by = WALK_BLOCKED_NO_SOURCE_CALL_LINE
            if fn_has_ddg and sink_node == source_fn and source_call_lines:
                sink_call_lines = call_lines.get(
                    (sink_node, sink_callee_id), [],
                )
                source_tracked = any(
                    defs_at.get((source_fn, line)) for line in source_call_lines
                )
                sink_after_source = any(
                    sink_line > source_line
                    for sink_line in sink_call_lines
                    for source_line in source_call_lines
                )
                if not sink_call_lines:
                    blocked_by = WALK_BLOCKED_NO_SINK_CALL_LINE
                elif not source_tracked:
                    blocked_by = WALK_BLOCKED_SOURCE_NOT_TRACKED
                elif not sink_after_source:
                    blocked_by = WALK_BLOCKED_SINK_BEFORE_SOURCE
                if sink_call_lines and source_tracked and sink_after_source:
                    # ADJUDICATING, NOT CONFIRM-ONLY (WI-kabif, granted
                    # 2026-09-02). ``None`` (escaped) and ``False``
                    # (exhausted) are no longer alike: the first is ignorance
                    # and keeps the flow, the second is positive evidence of
                    # NO dependence and REMOVES it below. That asymmetry is
                    # the entire grant, and it is why ``walk_verdict_for``
                    # carries two names for the two negatives.
                    walk_ran = True
                    # INV-lupav L2, AND IT IS NO LONGER A NO-OP HERE. The rule
                    # is that a site which CONSUMES the walk's ``False`` must
                    # pass the forfeit gate; a site that COLLAPSES it with
                    # ``is True`` need not, because ``False`` is then
                    # indistinguishable from ``None``. This site used to
                    # collapse and now consumes: ``walk_verdict`` distinguishes
                    # ``unconfirmed`` from ``escaped``, so an UNEARNED ``False``
                    # — the walk exhausted only because a later use sits in a
                    # construct the extractor does not model — would be
                    # published as "the walk looked everywhere and found
                    # nothing", which is exactly the claim INV-lupav says is not
                    # earned. Forfeiting downgrades it to ``None`` and the
                    # finding reads ``escaped``, which is the true statement.
                    #
                    # ``test_taint_refutation_gate_contract`` asserts this
                    # pairing structurally, and it FIRED on the first cut of
                    # this change — the guard doing the job it was built for.
                    walk_result = _ddg_taint_reaches(
                        source_fn, source_call_lines, sink_call_lines,
                        ddg_uses, callee_names, summaries,
                        defs_at=defs_at, inherits=inherits,
                        forfeit_refutation=(
                            source_fn in (forfeit_refutation or set())
                        ),
                        credited_user_summaries=credited_user_summaries,
                    )
                    adjudicated = walk_result is True

                    # WI-fasub: a sanitizer in the SAME function as the source.
                    #
                    # The BFS barrier cannot see this one. It exempts the seed —
                    # it must, since the seed is the taint origin and has to
                    # stay reachable — and a barrier called FROM the seed
                    # function is therefore never consulted, so
                    # `encrypt-then-write in one function`, an entirely ordinary
                    # shape, reported a violation about code that visibly
                    # sanitizes. Call-graph reachability cannot fix that at any
                    # price: both calls have the same caller, so the graph is
                    # identical whichever order they occur in. Statement
                    # ordering is the only thing that can answer it, which is
                    # why this lives here and NOT in the shared barrier helper.
                    #
                    # TWO WALKS, AND BOTH HALVES ARE LOAD-BEARING. Marking a
                    # flow sanitized suppresses it from a claim's violation set,
                    # so it needs positive evidence exactly as §3a's removal arm
                    # does. `adjudicated` says a data route source→sink exists;
                    # the guarded walk says no such route AVOIDS the barrier,
                    # with every step accounted for. Only `False` — exhausted,
                    # nothing unexplained — earns the label. `None` means the
                    # value escaped tracked ground on some route, and "I lost
                    # track of it" is not "you protected it" (L58 applied to
                    # sanitization rather than to removal).
                    barrier_sites = sanitizer_lines.get(
                        (source_fn, taint_label), [],
                    )
                    #
                    # ``or``, never a plain assignment: ``is_sanitized`` may
                    # already be True from the call-graph barrier (a ``callee``
                    # seed puts the source's own function downstream of the
                    # seed, so it can reach this line via
                    # ``sanitized_reachable``). This check ADDS a way to earn
                    # the label and must never take one away.
                    #
                    # WI-joluk, AND ONLY ON THIS ARM. The §3a arm above tests
                    # `is True`, so `False` and `None` already collapse there
                    # and the gate would change nothing. HERE a `False` earns
                    # `sanitized` and a sanitized flow is dropped from the
                    # claim's violation set — so a `False` from a function the
                    # extractor did not fully see suppresses a real violation.
                    # Forfeiting downgrades that to `None`, which produces
                    # strictly FEWER suppressions and therefore strictly MORE
                    # surviving violations: the safe direction, and the reason
                    # this could land before removal authority exists.
                    if adjudicated and barrier_sites:
                        ddg_sanitized = _ddg_taint_reaches(
                            source_fn, source_call_lines, sink_call_lines,
                            ddg_uses, callee_names, summaries,
                            defs_at=defs_at, inherits=inherits,
                            barrier_lines=frozenset(barrier_sites),
                            forfeit_refutation=(
                                source_fn in (forfeit_refutation or set())
                            ),
                            credited_user_summaries=credited_user_summaries,
                        ) is False
                        # INV-pojib: THIS ARM DECIDES THE SAME-FUNCTION SHAPE,
                        # and it is the arm the measured repro went through --
                        # `os.remove(launder(os.environ["API_KEY"]))` puts the
                        # sanitizer in the seed function, which the call-graph
                        # barrier exempts by design. Attributing only on that
                        # other arm left exactly the case this was filed for
                        # still printing the unattributed clause; caught by
                        # re-running the live repro after the unit tests were
                        # already green.
                        #
                        # ``sanitizer_callers`` is read rather than a second
                        # line->sanitizer map being built: the registrar keys it
                        # by the SAME (caller, input_taint) pair as
                        # ``sanitizer_lines``, so the identity is already here
                        # and a parallel map could only drift from it (L53).
                        if ddg_sanitized:
                            ddg_barrier = sanitizer_callers.get(
                                source_fn, {},
                            ).get(taint_label, [])
                        is_sanitized = is_sanitized or ddg_sanitized

            # The label now records what actually decided inclusion, which is
            # what INV-sadah asserts. Before §3a existed every finding here was
            # stamped "ddg"/"precise" on the strength of a walk whose result
            # was discarded; "precise" is now earned only where the walk ran.
            #
            # The ``fn_has_ddg`` arm is INV-karud clause (a3). "The walk ran
            # and did not confirm" (``ddg_mixed``) and "no reaching-def data
            # existed, so no walk was possible" (``structural``) are different
            # facts, and this field's own docstring has always defined them
            # that way — but only the first was reachable from here, because
            # the choice of propagator is made once for the whole repo
            # (``cli.py``: ``if ddg_edges:``). A JavaScript flow therefore came
            # out ``ddg_mixed`` when the repo also held a Python file and
            # ``structural`` when it did not, with identical JavaScript. A
            # reader cannot tell data-flow-adjudicated from
            # call-reachability-only when the distinguishing field is set by an
            # unrelated language's presence, which is exactly what (a3)
            # forbids. Deciding it here, on whether the DDG actually covered
            # the source function, makes the label a property of the flow.
            #
            # INV-zidur: the WALK'S OWN RESULT is carried beside the method now.
            # ``method`` is derived from the verdict rather than recomputed, so
            # the coarse name and the fine one cannot disagree — and the coarse
            # name's published meaning is unchanged, which is why every
            # ``ddg_mixed`` in docs/measurements/0006 still says what it said.
            verdict = walk_verdict_for(
                walk_result, ran=walk_ran, covered=fn_has_ddg,
            )
            method = method_for_walk_verdict(verdict)
            confidence = (
                "precise" if verdict == WALK_VERDICT_CONFIRMED
                else "approximate"
            )

            path = _reconstruct_path(
                {**parent, **sanitized_parent} if is_sanitized else parent,
                seed_id, sink_node,
            )
            sanitized_by, sanitized_by_user = _attribute_sanitizers(
                path, barrier_sanitizers,
            ) if is_sanitized else ((), ())
            # The call-graph barrier is EXEMPT inside the seed function, so a
            # same-function sanitizer is credited only by the DDG arm above and
            # leaves ``_attribute_sanitizers`` with nothing to report. That is
            # the shape the measured repro takes, and attributing only on the
            # other arm left exactly the filed case printing the unattributed
            # clause -- caught by re-running the live repro after the unit
            # tests were already green.
            if is_sanitized and not sanitized_by and ddg_barrier:
                sanitized_by = tuple(b.qualified_name for b in ddg_barrier)
                sanitized_by_user = tuple(
                    b.qualified_name for b in ddg_barrier if b.user_supplied
                )

            # ADR-0017 §3a REMOVAL AUTHORITY (WI-kabif + WI-joluk).
            #
            # ``unconfirmed`` is the ONLY verdict that may remove a flow, and
            # it means something narrow: the walk seeded on a definition the
            # DDG actually recorded, followed every route out of it, and every
            # route ended somewhere ACCOUNTED FOR -- a §4 terminating summary
            # or a barrier -- without ever reaching a sink argument.
            # ``escaped`` (the value went into a container, a field, a closure,
            # or a callee nothing is declared about) is IGNORANCE and keeps its
            # flow; ``not_attempted`` never ran.
            #
            # THREE THINGS KEEP THIS EARNED, EACH INDIVIDUALLY LOAD-BEARING.
            # (1) WI-joluk's forfeit gate has already downgraded any ``False``
            # from a function whose CFG missed a call node in its body, so an
            # exhausted walk over a demonstrably incomplete graph never arrives
            # here. (2) ``_summary_terminates`` is a conjunction in which any
            # doubt reads as "no", so an unmodelled callee escapes rather than
            # terminating. (3) The walk is intraprocedural, so "every route" is
            # a claim about one function body, not about the program.
            #
            # WHAT IS BEING TRADED, IN WRITING. ADR-0017 §7b excludes alias
            # analysis and states a preference for overapproximation, so this
            # introduces FALSE NEGATIVES on container and alias mutation. The
            # owner granted that trade explicitly on 2026-09-02. It is not a
            # trade this code may make on its own authority.
            #
            # MEASURED EFFECT ON TODAY'S CORPUS: ZERO. hypergumbo-core reports
            # 15 confirmed / 12 escaped / 0 unconfirmed across 27 walks, and
            # the 11-repo cohort reports the same zero (153 ``ddg_mixed`` rows:
            # 0 unconfirmed / 14 escaped / 139 not_attempted). These are live
            # semantics over a currently EMPTY class; they activate as escape
            # sites close, which is why ``refuted_flows`` exists -- a removal
            # nobody can count is a security tool deleting findings in silence.
            if verdict == WALK_VERDICT_UNCONFIRMED:
                if refuted_flows is not None:
                    refuted_flows.append(TaintFlowFinding(
                        taint_label=taint_label,
                        source_symbol=seed_id,
                        source_primitive=taint_source.name,
                        source_module=taint_source.module,
                        source_boundary=taint_source.source_boundary,
                        sink_symbol=sink_callee_id,
                        sink_primitive=taint_sink.name,
                        sink_module=taint_sink.module,
                        sink_zone=taint_sink.zone,
                        sink_call_sites=((sink_node, sink_callee_id),),
                        sanitized=is_sanitized,
                        sanitized_by=sanitized_by,
                        sanitized_by_user_supplied=sanitized_by_user,
                        confidence=confidence,
                        analysis_method=method,
                        walk_verdict=verdict,
                        path=path,
                    ))
                continue

            findings.append(TaintFlowFinding(
                taint_label=taint_label,
                source_symbol=seed_id,
                source_primitive=taint_source.name,
                source_module=taint_source.module,
                source_boundary=taint_source.source_boundary,
                sink_symbol=sink_callee_id,
                sink_primitive=taint_sink.name,
                sink_module=taint_sink.module,
                sink_zone=taint_sink.zone,
                sink_call_sites=((sink_node, sink_callee_id),),  # INV-kakad
                sanitized=is_sanitized,
                sanitized_by=sanitized_by,
                sanitized_by_user_supplied=sanitized_by_user,
                confidence=confidence,
                analysis_method=method,
                walk_verdict=verdict,
                walk_blocked_by=(
                    blocked_by
                    if verdict == WALK_VERDICT_NOT_ATTEMPTED else ""
                ),
                path=path,
            ))

    # INV-karud. This arm emits all three methods, and the collapse is
    # method-aware: ``ddg`` findings pass through with their pair claim intact
    # because the walk actually confirmed a dependence for them.
    return collapse_unadjudicated_flows(findings)
