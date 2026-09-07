# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property tests for the io-boundary axis registry (ADR-0050 / INV-tafig).

These are the ADR-0024 step-4 artifact. They assert three separable things,
and the separation is deliberate — each one failed differently in the history
that produced the axis:

1. REGISTRY INVARIANTS. No duplicate names, every spec on a declared axis,
   every direction from the declared vocabulary, non-empty descriptions.

2. THE DERIVED CONSTANTS STILL EQUAL WHAT THEY USED TO BE. ``io_boundary.py``
   now derives ``CATALOG_BOUNDARY_TYPES``, ``KNOWN_IO_BOUNDARIES``,
   ``OPAQUE_BOUNDARIES``, ``PRODUCER_OPAQUE_BOUNDARIES`` and
   ``_DISCLOSED_ONLY_BOUNDARIES`` from the registry instead of writing them
   out by hand. These tests pin the *pre-existing* membership so the
   single-source-of-truth refactor is provably behaviour-preserving rather
   than merely plausible.

3. LIVE-TREE DRIFT. Every module-level ``*BOUNDAR*`` set in the tree is a
   subset of the registry, and every key of ``taint.AUTO_SOURCE_LABEL_MAP``
   names a registered boundary. The second is the consumer the name filter
   cannot see (it is a dict, not a set, and its name contains no ``BOUNDAR``),
   so it gets an explicit assertion rather than being silently out of scope.
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# 1. Registry invariants
# ---------------------------------------------------------------------------

def test_no_duplicate_names():
    from hypergumbo_core.io_boundary_types import IO_BOUNDARY_TYPES

    names = [spec.name for spec in IO_BOUNDARY_TYPES]
    assert len(names) == len(set(names)), "duplicate boundary name in registry"


def test_every_spec_is_on_a_declared_axis():
    from hypergumbo_core.io_boundary_types import IO_BOUNDARY_TYPES, VALID_AXES

    offenders = [s.name for s in IO_BOUNDARY_TYPES if s.axis not in VALID_AXES]
    assert not offenders, f"specs on an undeclared axis: {offenders}"


def test_every_spec_has_a_declared_direction():
    from hypergumbo_core.io_boundary_types import (
        IO_BOUNDARY_TYPES,
        VALID_DIRECTIONS,
    )

    offenders = [
        s.name for s in IO_BOUNDARY_TYPES
        if s.direction is not None and s.direction not in VALID_DIRECTIONS
    ]
    assert not offenders, f"specs with an unknown direction: {offenders}"


def test_every_spec_has_a_description():
    from hypergumbo_core.io_boundary_types import IO_BOUNDARY_TYPES

    offenders = [s.name for s in IO_BOUNDARY_TYPES if not s.description.strip()]
    assert not offenders, f"specs with an empty description: {offenders}"


def test_only_data_crossing_specs_are_undirected_never():
    """A data_crossing value must name a direction — the axiom demands it.

    The axiom is "a boundary value names WHAT DATA CROSSES THE PROCESS
    BOUNDARY AT THIS CALL SITE, IN WHICH DIRECTION". A canonical value with
    no direction would not satisfy the second half of its own axiom.
    """
    from hypergumbo_core.io_boundary_types import (
        AXIS_DATA_CROSSING,
        io_boundaries_on_axis,
    )

    offenders = [
        s.name for s in io_boundaries_on_axis(AXIS_DATA_CROSSING)
        if s.direction is None
    ]
    assert not offenders, f"data_crossing specs with no direction: {offenders}"


def test_accessors_agree_with_the_registry():
    from hypergumbo_core.io_boundary_types import (
        IO_BOUNDARY_TYPES,
        VALID_AXES,
        all_io_boundary_names,
        find_io_boundary,
        io_boundaries_on_axis,
    )

    assert all_io_boundary_names() == frozenset(
        s.name for s in IO_BOUNDARY_TYPES
    )
    covered = [s for axis in VALID_AXES for s in io_boundaries_on_axis(axis)]
    assert len(covered) == len(IO_BOUNDARY_TYPES)
    for spec in IO_BOUNDARY_TYPES:
        assert find_io_boundary(spec.name) is spec
    assert find_io_boundary("no_such_boundary") is None


# ---------------------------------------------------------------------------
# 2. The derived constants still equal what they used to be
# ---------------------------------------------------------------------------
#
# These literals are the PRE-REFACTOR values, copied from io_boundary.py as it
# stood at 2a1b11d98e. They are deliberately hand-written here rather than
# imported: importing them would make the assertion vacuous (the derivation
# would be compared against itself).

_PRE_REFACTOR_CATALOG_BOUNDARY_TYPES = (
    "fs_read", "fs_write", "net_send", "net_recv",
    "ipc_recv", "ipc_send", "env_read", "host_info_read", "env_write",
    "subprocess", "db_read", "db_write",
    "process_send", "logging",
    "browser_storage_write",
    "browser_storage_read",
    "net_listen",
    # WI-fasap: the database twin of net_listen, declared LAST so the
    # first-declared-wins resolution of every existing row is untouched.
    "db_compose",
)


def test_catalog_boundary_types_unchanged_including_order():
    """Order is pinned, not just membership.

    ``_parse_catalog`` iterates this tuple, and a YAML declaring the same
    primitive under two boundaries is resolved by first-declared-wins in
    several places. Membership equality would let a reordering through.
    """
    from hypergumbo_core.io_boundary import CATALOG_BOUNDARY_TYPES

    assert CATALOG_BOUNDARY_TYPES == _PRE_REFACTOR_CATALOG_BOUNDARY_TYPES


def test_known_io_boundaries_unchanged():
    from hypergumbo_core.io_boundary import KNOWN_IO_BOUNDARIES

    assert KNOWN_IO_BOUNDARIES == frozenset(
        _PRE_REFACTOR_CATALOG_BOUNDARY_TYPES
        + ("external_potential", "command_launch")
    )


def test_opaque_boundaries_unchanged():
    from hypergumbo_core.io_boundary import (
        OPAQUE_BOUNDARIES,
        PRODUCER_OPAQUE_BOUNDARIES,
    )

    assert OPAQUE_BOUNDARIES == frozenset({"subprocess"})
    assert PRODUCER_OPAQUE_BOUNDARIES == frozenset({"command_launch"})


def test_the_two_opacity_sets_stay_disjoint():
    """io_boundary.py's own docstring calls this disjointness "the point".

    A catalog-declarable boundary is inert unless it is in
    CATALOG_BOUNDARY_TYPES; a producer-stamped one is inert if it IS. Each
    set is reachable through exactly one channel. Deriving both from one
    registry is precisely where that could silently collapse, so it is
    asserted here rather than assumed.
    """
    from hypergumbo_core.io_boundary import (
        CATALOG_BOUNDARY_TYPES,
        OPAQUE_BOUNDARIES,
        PRODUCER_OPAQUE_BOUNDARIES,
    )

    assert not (OPAQUE_BOUNDARIES & PRODUCER_OPAQUE_BOUNDARIES)
    assert OPAQUE_BOUNDARIES <= set(CATALOG_BOUNDARY_TYPES)
    assert not (PRODUCER_OPAQUE_BOUNDARIES & set(CATALOG_BOUNDARY_TYPES))


def test_disclosed_only_boundaries_unchanged():
    from hypergumbo_core.io_boundary import _DISCLOSED_ONLY_BOUNDARIES

    assert _DISCLOSED_ONLY_BOUNDARIES == frozenset(
        {"external_potential", "command_launch", "net_listen", "db_compose"}
    )


def test_subprocess_is_opaque_but_still_counts_in_the_headline():
    """The one value that separates "opacity" from "disclosed-only".

    subprocess is opaque AND curated: it is catalog-declared, so it belongs
    in total_io_edges. external_potential, command_launch, net_listen and
    db_compose are disclosed but excluded. This is why headline membership is per-value
    metadata on the spec and NOT derivable from the axis.
    """
    from hypergumbo_core.io_boundary_types import find_io_boundary

    subprocess_spec = find_io_boundary("subprocess")
    assert subprocess_spec is not None
    assert subprocess_spec.counts_in_headline is True
    for name in ("external_potential", "command_launch", "net_listen", "db_compose"):
        spec = find_io_boundary(name)
        assert spec is not None
        assert spec.counts_in_headline is False, name


# ---------------------------------------------------------------------------
# 3. Live-tree drift
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (REPO_ROOT / "packages").is_dir(),
    reason="live-tree scan needs the repo layout",
)
def test_live_tree_has_no_boundary_set_drift():
    from hypergumbo_core.io_boundary_types import find_axis_drift

    offenders = find_axis_drift(REPO_ROOT)
    assert not offenders, "\n".join(offenders)


def test_the_drift_scanner_actually_fires(tmp_path):
    """THE GATE MUST BE PROVED NON-VACUOUS, and here that is not a formality.

    ``find_axis_drift`` over the live tree returns zero offenders, and it would
    return zero offenders if it were broken, if the name filter matched
    nothing, or if the walker collected no node shape present in this codebase.
    All three look identical from the assertion above. In fact the third is
    CLOSE TO TRUE: the walker collects module-level ``{...}`` and
    ``frozenset({...})`` string-literal sets, and after the ADR-0050
    single-source-of-truth refactor io_boundary.py has none left --
    OPAQUE_BOUNDARIES and its peers are now calls into the registry. So the
    live-tree scan passes over an empty collection.

    That makes the scan a guard against FUTURE hand-rolled sets rather than a
    check on anything present today, which is a real but much weaker claim than
    "the vocabulary is enforced". This test pins the weaker claim honestly: it
    builds a synthetic tree containing exactly the ADR-0023 silent-bug shape
    and asserts the scanner reports it.
    """
    from hypergumbo_core.io_boundary_types import find_axis_drift

    pkg = tmp_path / "packages" / "fake" / "src"
    pkg.mkdir(parents=True)
    (pkg / "consumer.py").write_text(
        "SOME_BOUNDARY_SET = {'fs_read', 'not_a_real_boundary'}\n"
    )

    offenders = find_axis_drift(tmp_path)

    assert len(offenders) == 1, offenders
    assert "not_a_real_boundary" in offenders[0]
    assert "fs_read" not in offenders[0], (
        "a registered value must not be reported as drift"
    )


def test_read_target_kind_boundary_values_are_registered():
    """The third dict consumer, and the one whose name DOES match the filter.

    ``_READ_TARGET_KIND_BOUNDARY`` maps a read target kind to the boundary it
    produces. Its name contains ``BOUNDAR``, so a reader could reasonably
    assume the drift scan covers it -- it does not, because the walker collects
    sets and this is a dict. Asserting it here is what makes that assumption
    safe rather than merely wrong.
    """
    from hypergumbo_core.io_boundary import _READ_TARGET_KIND_BOUNDARY
    from hypergumbo_core.io_boundary_types import all_io_boundary_names

    unknown = set(_READ_TARGET_KIND_BOUNDARY.values()) - all_io_boundary_names()
    assert not unknown, f"_READ_TARGET_KIND_BOUNDARY produces unknown: {unknown}"


def test_auto_source_label_map_keys_are_registered_boundaries():
    """The consumer the NAME FILTER CANNOT SEE.

    taint.AUTO_SOURCE_LABEL_MAP branches on boundary values, but it is a dict
    whose name contains no ``BOUNDAR``, so ``find_axis_drift`` will never
    report it. Leaving it to the name filter would be a gate that looks like
    it covers the vocabulary and does not. Asserted explicitly instead.
    """
    from hypergumbo_core.io_boundary_types import all_io_boundary_names
    from hypergumbo_core.taint import AUTO_SOURCE_LABEL_MAP

    unknown = set(AUTO_SOURCE_LABEL_MAP) - all_io_boundary_names()
    assert not unknown, f"AUTO_SOURCE_LABEL_MAP keys not registered: {unknown}"


def test_deferred_crossing_shadows_names_registered_boundaries():
    """The sixth consumer, added by ADR-0049 AFTER INV-tafig was filed.

    Both sides of the mapping are boundary values — the shadowing tag and the
    data boundary it makes unexaminable — so both are asserted.
    """
    from hypergumbo_core.io_boundary import DEFERRED_CROSSING_SHADOWS
    from hypergumbo_core.io_boundary_types import all_io_boundary_names

    known = all_io_boundary_names()
    unknown = (
        set(DEFERRED_CROSSING_SHADOWS) | set(DEFERRED_CROSSING_SHADOWS.values())
    ) - known
    assert not unknown, f"DEFERRED_CROSSING_SHADOWS names unknown: {unknown}"


def test_io_boundary_axis_is_wired_into_known_axes():
    from hypergumbo_core.io_boundary_types import all_io_boundary_names
    from hypergumbo_core.multi_value_field_axis import _known_axes

    axes = _known_axes()
    assert "io-boundary" in axes
    assert frozenset(axes["io-boundary"]()) == all_io_boundary_names()


def test_write_target_kind_boundary_values_are_registered():
    """WI-suhug: the write-direction twin of the map above, same blind spot."""
    from hypergumbo_core.io_boundary import _WRITE_TARGET_KIND_BOUNDARY
    from hypergumbo_core.io_boundary_types import all_io_boundary_names

    unknown = set(_WRITE_TARGET_KIND_BOUNDARY.values()) - all_io_boundary_names()
    assert not unknown, f"_WRITE_TARGET_KIND_BOUNDARY produces unknown: {unknown}"
