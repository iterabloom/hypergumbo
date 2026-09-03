# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical registry of I/O-boundary values — the io-boundary axis (ADR-0050).

THE AXIOM, taken verbatim from ADR-0049 ruling 1 so that ruling stays the
value-level ruling and this module is the axis it lives on:

    A boundary value names WHAT DATA CROSSES THE PROCESS BOUNDARY AT THIS
    CALL SITE, IN WHICH DIRECTION -- not what the program is thereby
    arranged to do later.

WHY THIS MODULE EXISTS (INV-tafig). ADR-0016 and ``taint.py`` both call this
vocabulary "hypergumbo's canonical I/O-boundary risk taxonomy", and six
consumers branch on its values, but until now it had none of ADR-0024's four
artifacts -- no axiom, no registry, no consumer helper, no drift linter -- and
``io-boundary`` was absent from ``multi_value_field_axis._known_axes()``. The
cost was measured twice rather than theorised:

  * INV-tutar. ``env_read`` carried TWO readings -- ambient configuration and
    secret material -- and ``AUTO_SOURCE_LABEL_MAP`` read the wrong one. 134 of
    195 shipped ``env_read`` rows were host DESCRIPTION or user identity, which
    is why host-secret claims carried 48 of 85 adjudicated flows at 22.9%
    precision. The catalogues were already distorting themselves to cope:
    ``python.yaml`` deliberately WITHHELD ``getpid`` and ``cpu_count`` while
    ``go.yaml`` rowed ``GOOS`` and ``Getwd``. One value, two membership rules,
    two shipped files. The split into ``host_info_read`` is the fix; this
    registry is where the next such ruling can be recorded instead of being
    re-derived.
  * WI-johuk. The server-launch question was derived four times over five
    months and ruled zero times, because there was nowhere a ruling could live.
    The only governing text was a TEST DOCSTRING. Four independent censuses
    produced four different numbers (37 / 52 / 63 / 75) because there was no
    membership rule to count against.

WHAT THIS REGISTRY IS THE SOURCE OF. ``io_boundary.py`` no longer writes its
five vocabulary constants out by hand; each is derived here:

    CATALOG_BOUNDARY_TYPES      names where catalog_declarable
    KNOWN_IO_BOUNDARIES         every name
    OPAQUE_BOUNDARIES           axis=opacity AND catalog_declarable
    PRODUCER_OPAQUE_BOUNDARIES  axis=opacity AND NOT catalog_declarable
    _DISCLOSED_ONLY_BOUNDARIES  names where NOT counts_in_headline

The two opacity sets fall out of one field because their difference IS the
channel -- ``io_boundary.py``'s own comment says "each set is reachable through
exactly one channel", and a catalogue-declarable boundary is inert unless it is
in ``CATALOG_BOUNDARY_TYPES`` while a producer-stamped one is inert if it is.
Deriving both from ``catalog_declarable`` makes that sentence executable rather
than a comment someone has to keep true.

THE SECTIONS, and why headline membership is NOT one of them
------------------------------------------------------------
Four axes partition the nineteen values:

``data_crossing``     the axiom's canonical section. Data crosses the process
                      boundary at this call site, in a named direction.
``opacity``           control LEFT the process. The call is correctly
                      classified and the analysis cannot see past it, so it
                      does not license "I looked and found nothing".
``deferred_crossing`` ADR-0049. The call ARRANGES a crossing it does not
                      itself perform -- precisely what the axiom's second
                      clause excludes from ``data_crossing``, which is why it
                      needs a section rather than being a violation.
``speculative``       synthesised uncertainty, declarable by no catalogue.

Headline membership (``total_io_edges``) cuts ACROSS that partition and is
therefore PER-VALUE METADATA, not a section. ``subprocess`` is the value that
proves it: it is opaque AND curated, so it counts in the headline, while
``external_potential``, ``command_launch`` and ``net_listen`` are disclosed and
excluded. An axis query would have to special-case exactly one value, which is
the shape ADR-0024's fold-residue discipline says to put on the spec instead.

KNOWN GAPS, stated rather than left for the next reader to rediscover
--------------------------------------------------------------------
1. ADR-0016's "controlled vocabulary" table documents NINE of these nineteen.
   ``env_write``, ``db_read``, ``db_write``, ``process_send``, ``logging``,
   ``browser_storage_read``, ``browser_storage_write``, ``net_listen``,
   ``external_potential`` and ``command_launch`` are absent from it. The
   vocabulary grew and its documentation did not, which is the drift INV-tafig
   describes; the descriptions below are sourced from the shipped catalogue
   rows and the consumer code, not from that table.
2. ``logging`` is the one ``data_crossing`` value naming a PURPOSE rather than
   a medium, and it OVERLAPS ``ipc_send`` on stdout -- go's ``fmt.Println`` and
   haskell's ``Prelude.putStrLn`` are catalogued ``logging`` while
   ``stdout.write`` is catalogued ``ipc_send``. That is a genuine per-value
   question and it is NOT settled here. It is recorded as the first candidate
   for a per-value audit under ADR-0024's family-audit methodology, which is
   the whole point of having an axiom to audit against. NO ROW MOVES on the
   strength of this note.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final


AXIS_DATA_CROSSING: Final[str] = "data_crossing"
AXIS_OPACITY: Final[str] = "opacity"
AXIS_DEFERRED_CROSSING: Final[str] = "deferred_crossing"
AXIS_SPECULATIVE: Final[str] = "speculative"

VALID_AXES: Final[frozenset[str]] = frozenset({
    AXIS_DATA_CROSSING,
    AXIS_OPACITY,
    AXIS_DEFERRED_CROSSING,
    AXIS_SPECULATIVE,
})

DIRECTION_INBOUND: Final[str] = "inbound"
DIRECTION_OUTBOUND: Final[str] = "outbound"

VALID_DIRECTIONS: Final[frozenset[str]] = frozenset({
    DIRECTION_INBOUND,
    DIRECTION_OUTBOUND,
})


@dataclass(frozen=True)
class IoBoundarySpec:
    """A single I/O-boundary value and its axis classification.

    ``direction`` is part of the AXIOM rather than decoration -- the axiom's
    second half is "IN WHICH DIRECTION" -- so every ``data_crossing`` value
    carries one and a property test enforces it. ``None`` is legal only off
    that axis, where there is no data crossing at this call site to direct:
    ``external_potential`` is an unmatched call that may be no I/O at all.

    ``catalog_declarable`` records whether an ``io_primitives/*.yaml`` may
    declare the value. ``_parse_catalog`` iterates exactly the declarable
    names, so a value with ``False`` here can only ever be stamped by a
    producer, and a value with ``True`` can only ever arrive through a
    catalogue. That is the channel split ``OPAQUE_BOUNDARIES`` versus
    ``PRODUCER_OPAQUE_BOUNDARIES`` encodes.

    ``counts_in_headline`` records whether the value is part of the verified,
    curated I/O surface reported as ``total_io_edges``. False means DISCLOSED
    BUT EXCLUDED -- surfaced in its own count field so a consumer sees it
    without it inflating the headline.
    """

    name: str
    axis: str
    direction: str | None
    catalog_declarable: bool
    counts_in_headline: bool
    description: str


# The declaration ORDER of the catalogue-declarable specs is load-bearing and
# is preserved from the hand-written ``CATALOG_BOUNDARY_TYPES`` tuple this
# registry replaces: ``_parse_catalog`` iterates it, and several sites resolve
# a primitive declared under two boundaries by first-declared-wins. A property
# test pins the order, not merely the membership.
IO_BOUNDARY_TYPES: Final[tuple[IoBoundarySpec, ...]] = (
    # ------------------------------------------------------------------
    # AXIS_DATA_CROSSING -- the axiom's canonical section.
    # ------------------------------------------------------------------
    IoBoundarySpec(
        name="fs_read",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_INBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Read data from the local filesystem. Deliberately QUIET as a "
            "taint source -- sensitivity depends on what is stored, so it "
            "mints nothing (ADR-0016 risk-classification note)."
        ),
    ),
    IoBoundarySpec(
        name="fs_write",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_OUTBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description="Write data to the local filesystem.",
    ),
    IoBoundarySpec(
        name="net_send",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_OUTBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Send data to the network. Egress risk is additionally graded by "
            "the supply-chain dst_tier rather than by this value alone."
        ),
    ),
    IoBoundarySpec(
        name="net_recv",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_INBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Receive data from the network. Shadowed by net_listen: a "
            "deferred-crossing site blocks a clean net_recv verdict and "
            "nothing else (ADR-0049 ruling 2)."
        ),
    ),
    IoBoundarySpec(
        name="ipc_recv",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_INBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Receive data from another process -- stdin, a pipe read, shared "
            "memory read."
        ),
    ),
    IoBoundarySpec(
        name="ipc_send",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_OUTBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Send data to another process -- stdout write, pipe write, shared "
            "memory write. See the module docstring's gap 2: the stdout half "
            "overlaps `logging`, unsettled."
        ),
    ),
    IoBoundarySpec(
        name="env_read",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_INBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Read ambient CONFIGURATION -- values that may carry a credential. "
            "Narrowed by INV-tutar: host description and user identity are "
            "host_info_read, not this. Mints a host_secret taint source, which "
            "is why the narrowing mattered."
        ),
    ),
    IoBoundarySpec(
        name="host_info_read",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_INBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Read host DESCRIPTION or user identity -- not a secret (split "
            "from env_read by INV-tutar) -- INCLUDING THE CLOCK (WI-pavob). "
            "Fires almost universally, so its discriminating power is "
            "deliberately low and that was accepted on the record."
        ),
    ),
    IoBoundarySpec(
        name="env_write",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_OUTBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Mutate the process environment -- os.environ assignment, "
            "std::env::set_var, System.Environment setEnv."
        ),
    ),
    IoBoundarySpec(
        name="subprocess",
        axis=AXIS_OPACITY,
        direction=DIRECTION_OUTBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Launch or communicate with a child process. OPAQUE: control left "
            "this process for a program whose behaviour is not in the edge "
            "set, so a subprocess site withholds a clean verdict on EVERY "
            "boundary. Without that, a program whose only statement was "
            "subprocess.run(['curl', '-o', '/etc/cron.d/pwned', ...]) returned "
            "confirmed rc 0 for both fs_write and net_send must_not_exist "
            "claims. The one boundary carrying the display-only high_risk "
            "marker (ADR-0016)."
        ),
    ),
    IoBoundarySpec(
        name="db_read",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_INBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Read from a database or persistent store -- java.sql.Connection, "
            "erlang ets/dets, CoreData NSManagedObjectContext, sqlite3."
        ),
    ),
    IoBoundarySpec(
        name="db_write",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_OUTBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Write to a database or persistent store -- java.sql.Statement, "
            "erlang ets/dets, CoreData NSManagedObjectContext."
        ),
    ),
    IoBoundarySpec(
        name="process_send",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_OUTBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Send a message to another runtime-managed process or actor -- "
            "erlang send, Control.Concurrent. Distinct from ipc_send: the far "
            "side is a peer inside the same runtime, not an OS pipe."
        ),
    ),
    IoBoundarySpec(
        name="logging",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_OUTBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Emit data to a log sink -- java.util.logging.Logger, go fmt/io "
            "print, Prelude.putStrLn, Swift print. THE ONE CANONICAL VALUE "
            "NAMING A PURPOSE RATHER THAN A MEDIUM, and it overlaps ipc_send "
            "on stdout. First candidate for a per-value audit; see the module "
            "docstring, gap 2. No row moves on that note."
        ),
    ),
    IoBoundarySpec(
        name="browser_storage_write",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_OUTBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Write to browser-local storage (localStorage and peers). "
            "Structurally distinct from the host filesystem -- reachable via "
            "XSS, not via local-user FS access (WI-lokuv)."
        ),
    ),
    IoBoundarySpec(
        name="browser_storage_read",
        axis=AXIS_DATA_CROSSING,
        direction=DIRECTION_INBOUND,
        catalog_declarable=True,
        counts_in_headline=True,
        description=(
            "Read from browser-local storage. Like fs_read and for the same "
            "reason, deliberately NOT in AUTO_SOURCE_LABEL_MAP: sensitivity "
            "depends on what is stored, so a project-local catalogue adds its "
            "own taint_sources rows for its threat model."
        ),
    ),
    # ------------------------------------------------------------------
    # AXIS_DEFERRED_CROSSING -- ADR-0049.
    # ------------------------------------------------------------------
    IoBoundarySpec(
        name="net_listen",
        axis=AXIS_DEFERRED_CROSSING,
        direction=DIRECTION_INBOUND,
        catalog_declarable=True,
        counts_in_headline=False,
        description=(
            "Bind or accept: the call ARRANGES inbound network data to arrive "
            "somewhere it does not name, and returns no such data itself. "
            "DISCLOSED, NEVER MINTED (ADR-0049). Its shadow over net_recv is "
            "required, not optional -- since INV-buzab a classified call is "
            "what `examined` means, so a tag that mints nothing would still "
            "count as an examined negative and hand verify-claims a green tick "
            "over live ingress."
        ),
    ),
    # ------------------------------------------------------------------
    # AXIS_SPECULATIVE and the producer-stamped half of AXIS_OPACITY.
    # Neither is declarable by any catalogue.
    # ------------------------------------------------------------------
    IoBoundarySpec(
        name="external_potential",
        axis=AXIS_SPECULATIVE,
        direction=None,
        catalog_declarable=False,
        counts_in_headline=False,
        description=(
            "Synthesised by _compute_external_potential for an unmatched "
            "first-party call edge: receiver-unresolved speculative noise that "
            "MIGHT be I/O. Not a classification, an admission of uncertainty -- "
            "hence no direction."
        ),
    ),
    IoBoundarySpec(
        name="command_launch",
        axis=AXIS_OPACITY,
        direction=DIRECTION_OUTBOUND,
        catalog_declarable=False,
        counts_in_headline=False,
        description=(
            "The same question as subprocess -- did control leave this "
            "process? -- asked of the producer-stamped channel (INV-larol). "
            "bash.py stamps it directly because there is no bash catalogue and "
            "per ADR-0016 there is not going to be one: cataloguing curl as "
            "net_send would attribute curl's network activity to the shell "
            "script. Definite but uncurated, so disclosed and excluded from "
            "the headline."
        ),
    ),
)


def all_io_boundary_names() -> frozenset[str]:
    """Return every canonical I/O-boundary name.

    This is the callable wired into
    :func:`hypergumbo_core.multi_value_field_axis._known_axes` under the
    ``io-boundary`` key, so a ``# axis: io-boundary`` field annotation
    resolves against it.
    """
    return frozenset(spec.name for spec in IO_BOUNDARY_TYPES)


def io_boundaries_on_axis(axis: str) -> tuple[IoBoundarySpec, ...]:
    """Return every I/O-boundary spec whose axis equals *axis*.

    Use this in place of a hardcoded set. ``OPAQUE_BOUNDARIES`` was
    ``frozenset({"subprocess"})`` written out by hand with a comment
    explaining why it had exactly one member; it is now
    ``io_boundaries_on_axis(AXIS_OPACITY)`` filtered by channel, so a future
    opacity-meaning boundary joins it by being classified rather than by
    someone remembering to edit a second place. That comment already
    anticipated this: "if a future boundary is added whose meaning is
    'control left this process', it belongs here too, and the
    axis-conformance tests are what will ask."
    """
    return tuple(spec for spec in IO_BOUNDARY_TYPES if spec.axis == axis)


def find_io_boundary(name: str) -> IoBoundarySpec | None:
    """Look up an I/O-boundary spec by name; None if not registered."""
    for spec in IO_BOUNDARY_TYPES:
        if spec.name == name:
            return spec
    return None


def catalog_declarable_names() -> tuple[str, ...]:
    """Return the catalogue-declarable names IN DECLARATION ORDER.

    Order is preserved rather than sorted because ``_parse_catalog`` iterates
    this tuple and several sites resolve a primitive declared under two
    boundaries by first-declared-wins.
    """
    return tuple(
        spec.name for spec in IO_BOUNDARY_TYPES if spec.catalog_declarable
    )


def disclosed_only_names() -> frozenset[str]:
    """Return the boundaries DISCLOSED but EXCLUDED from ``total_io_edges``."""
    return frozenset(
        spec.name for spec in IO_BOUNDARY_TYPES if not spec.counts_in_headline
    )


def opacity_names(*, catalog_declarable: bool) -> frozenset[str]:
    """Return the opacity boundaries reachable through ONE channel.

    The channel split is the whole design: a catalogue-declarable boundary is
    inert unless it is in ``CATALOG_BOUNDARY_TYPES``, and a producer-stamped
    one is inert if it IS, so collapsing the two sets makes one half
    unreachable whichever way it is spelled.
    """
    return frozenset(
        spec.name for spec in io_boundaries_on_axis(AXIS_OPACITY)
        if spec.catalog_declarable is catalog_declarable
    )


def find_axis_drift(repo_root: Path) -> list[str]:
    """Scan the repo for hardcoded ``*BOUNDAR*`` sets that drift from the registry.

    Wraps the field-agnostic AST walker in :mod:`hypergumbo_core.axis_drift`
    with the parameterisation for the io-boundary axis.

    IT COLLECTS NOTHING ON THE LIVE TREE TODAY, AND THAT IS NOT A BUG TO HIDE.
    The walker collects module-level ``{...}`` and ``frozenset({...})``
    string-literal assignments whose target name contains the filter. After
    this ADR's single-source-of-truth refactor, io_boundary.py has none left:
    ``OPAQUE_BOUNDARIES`` and its four peers are now calls into this registry,
    which is the improvement, and the side effect is that the set-shaped scan
    passes over an EMPTY collection. So this function is a guard against a
    FUTURE hand-rolled set -- the ADR-0023 silent-bug shape -- and not a check
    on anything present today. ``test_the_drift_scanner_actually_fires`` pins
    that weaker claim by proving the scanner reports a synthetic offender,
    because a broken scanner and a clean tree are otherwise indistinguishable.

    THE SHAPES IT STRUCTURALLY CANNOT SEE are the ones this vocabulary's real
    consumers use: a ``frozenset(A + B)`` built from other names (what
    ``KNOWN_IO_BOUNDARIES`` was), a bare tuple (what ``CATALOG_BOUNDARY_TYPES``
    was), and dicts keyed or valued by boundary. All three live dict consumers
    -- ``taint.AUTO_SOURCE_LABEL_MAP``,
    ``io_boundary.DEFERRED_CROSSING_SHADOWS``,
    ``io_boundary._READ_TARGET_KIND_BOUNDARY`` and its write-direction twin
    ``io_boundary._WRITE_TARGET_KIND_BOUNDARY`` -- are asserted explicitly in
    ``tests/test_io_boundary_types.py`` instead. Widening the shared walker to
    reach dicts and tuples would serve every axis and is filed separately; it
    is not done here because it changes machinery three other registries
    depend on.

    ``VALID_BOUNDARY_RULINGS`` is excluded PRECAUTIONARILY, and the exclusion
    does not currently fire: that set's elements are name references
    (``BOUNDARY_RULING_UNDECIDABLE``) rather than string literals, so the
    walker skips it regardless. It is named here because it matches the
    ``BOUNDAR`` filter and enumerates catalogue RULING verbs -- a separate
    vocabulary -- so inlining those strings later must not turn it into a
    false offender.
    """
    from hypergumbo_core.axis_drift import find_drift
    return find_drift(
        repo_root,
        name_filter="BOUNDAR",
        registry_names=all_io_boundary_names(),
        excluded_target_names=(
            "VALID_BOUNDARY_RULINGS",
            # WI-jinuj: ``_READ_TARGET_KIND_BOUNDARY`` is a CROSS-AXIS map --
            # keyed by ``Edge.meta['io_target_kind']`` values (host_path,
            # std_stream) and VALUED by io-boundary names. Only the KEYS are
            # off this axis, so only the keys are excluded; the values side
            # stays checked, which is the whole reason a dict's two sides are
            # collected separately rather than unioned.
            "_READ_TARGET_KIND_BOUNDARY:keys",
            # WI-suhug: its write-direction twin, same shape, same rule.
            "_WRITE_TARGET_KIND_BOUNDARY:keys",
        ),
    )
