# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0049 ruling 2: a deferred crossing is DISCLOSED, and its shadow is SCOPED.

WHAT A DEFERRED CROSSING IS. A call that opens, registers, subscribes to,
schedules or defers a crossing whose data arrives in a scope the call does not
name -- a server launch, a route registration, a callback subscription. It is
not a transfer: ``net/http.ListenAndServe`` returns an ``error`` and nothing
else, so a taint source minted there is attributed to a scope that never sees a
request.

WHY THE TAG ALONE IS NOT ENOUGH, WHICH IS WHAT THIS FILE EXISTS TO PIN. Since
INV-buzab, a call the catalogue CLASSIFIED is what ``examined`` means. Retagging
a launch to a boundary that mints no taint STILL CLASSIFIES IT, so without a
shadow the call becomes an examined negative and ``verify-claims`` returns a
green tick over live ingress. ADR-0016's own table measures that shape in the
outbound direction: cataloguing ``curl`` as ``net_send`` took a cron-dropper
claim from ``inconclusive`` rc 2 to ``confirmed`` rc 0 -- six correct lines
bought a false all-clear. A disclosure boundary without its shadow is strictly
worse than leaving the false source in place, so the middle row of the table
below is the one that matters.

AND WHY THE SHADOW IS SCOPED RATHER THAN TOTAL. ``OPAQUE_BOUNDARIES`` is total
opacity: control left the process for a program that could do anything, so a
launch site makes EVERY boundary unexaminable. A deferred crossing is the
opposite -- we know exactly what we cannot see. A listener says nothing about
whether the program writes files. Routing this through ``opaque_sites`` would
send every server in every language to ``inconclusive`` on ``fs_write`` and
``env_read``: a change that REPORTS LESS, on a gate that already withholds, and
one that every existing opacity test would have stayed green through. The
scoping is ADR-0049 ruling 2 clause 3 verbatim ("over the CORRESPONDING data
boundary"), not an optimisation of it.

REAL CATALOGUE, REAL EDGE SHAPES. The sibling file
``test_verify_claims_examined_negative.py`` records that a hand-written
``IoBoundaryCatalog`` fixture is how a false-all-clear survived once already, so
these tests load the SHIPPED go catalogue and mutate one row's boundary, and the
edge dicts are the shapes an actual ``hypergumbo survey`` emitted over a Go
accept-loop fixture. No row in the shipped tree carries ``net_listen`` yet --
ADR-0049 ruling 3 licenses no retag before the census (WI-hazop) and the
reachability pass (WI-vapud) -- so the row is synthesised here rather than
assumed.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import hypergumbo_core.io_boundary as io_boundary
from hypergumbo_core.io_boundary import (
    BoundaryMap,
    CATALOG_BOUNDARY_TYPES,
    DEFERRED_CROSSING_SHADOWS,
    KNOWN_IO_BOUNDARIES,
    OPAQUE_BOUNDARIES,
    _DISCLOSED_ONLY_BOUNDARIES,
    load_catalog,
)
from hypergumbo_core.taint import AUTO_SOURCE_LABEL_MAP
from hypergumbo_core.verify_claims import (
    Claim,
    compute_boundary_coverage,
    deferred_crossing_sites,
    verify_claim,
)



#: Captured from `hypergumbo survey` over a Go accept loop, not composed by hand.
LISTEN_EDGE = {
    "src": "go:server.go:27-42:serve:function",
    "dst": "go:net:0-0:Listen:external_symbol",
    "type": "calls",
    "line": 29,
}


def _go_catalog_with(boundary: str):
    """The SHIPPED go catalogue with ``net.Listen`` re-tagged to ``boundary``."""
    cat = load_catalog("go")
    prims = [
        replace(p, boundary=boundary)
        if (p.module, p.name) == ("net", "Listen") else p
        for p in cat.primitives
    ]
    assert any(p.boundary == boundary for p in prims), "the row was not re-tagged"
    return replace(cat, primitives=prims)


def _claim(boundary: str) -> Claim:
    return Claim(id="C", text="t", constraint_boundary=boundary,
                 constraint_must_not_exist=True)


class TestTheVocabulary:
    """The value exists, and carries exactly the three clauses -- no more."""

    def test_it_is_catalog_declarable(self) -> None:
        assert "net_listen" in CATALOG_BOUNDARY_TYPES
        assert "net_listen" in KNOWN_IO_BOUNDARIES

    def test_clause_1_it_mints_no_taint(self) -> None:
        """A deferred crossing that minted would be the defect, not the fix."""
        assert "net_listen" not in AUTO_SOURCE_LABEL_MAP

    def test_clause_2_it_is_disclosed_not_headlined(self) -> None:
        assert "net_listen" in _DISCLOSED_ONLY_BOUNDARIES

    def test_clause_3_the_shadow_is_scoped_not_total(self) -> None:
        """THE DESIGN, ASSERTED AS AN ABSENCE.

        ``net_listen`` must NOT be in ``OPAQUE_BOUNDARIES``: that set is total
        opacity and would withhold clean verdicts for every boundary at once.
        Asserting the absence is what stops a later reader "tidying" it in
        there -- the tidy would pass every opacity test in the tree.
        """
        assert "net_listen" not in OPAQUE_BOUNDARIES
        assert DEFERRED_CROSSING_SHADOWS["net_listen"] == "net_recv"

    def test_every_shadow_names_a_real_boundary_on_both_sides(self) -> None:
        """A GATE ON THE MAP ITSELF, so a typo cannot create a silent no-op."""
        for declared, shadowed in DEFERRED_CROSSING_SHADOWS.items():
            assert declared in CATALOG_BOUNDARY_TYPES, declared
            assert shadowed in CATALOG_BOUNDARY_TYPES, shadowed
            assert declared not in AUTO_SOURCE_LABEL_MAP, declared
            assert declared in _DISCLOSED_ONLY_BOUNDARIES, declared


class TestTheCatalogueQuery:

    def test_it_reports_the_shadowed_boundary_not_the_declared_one(self) -> None:
        cat = _go_catalog_with("net_listen")
        assert cat.deferred_crossings("net", "Listen") == frozenset({"net_recv"})

    def test_an_ordinary_row_shadows_nothing(self) -> None:
        cat = load_catalog("go")
        assert cat.deferred_crossings("net", "Listen") == frozenset()
        assert cat.deferred_crossings("net.Conn", "Read") == frozenset()

    def test_sites_are_grouped_by_the_boundary_they_shadow(self) -> None:
        cats = {"go": _go_catalog_with("net_listen")}
        assert deferred_crossing_sites([LISTEN_EDGE], cats) == {
            "net_recv": ["net.Listen"],
        }

    def test_no_sites_when_no_row_defers(self) -> None:
        """FALSIFIABILITY CONTROL. Without it the test above is satisfied by a
        function that returns the same dict for any input."""
        assert deferred_crossing_sites([LISTEN_EDGE], {"go": load_catalog("go")}) == {}


class TestTheThreeRowTable:
    """ADR-0016's table, reproduced for this boundary. The MIDDLE ROW is the
    reason the shadow is not optional."""

    def _verdict(self, boundary: str, claim_boundary: str = "net_recv"):
        cats = {"go": _go_catalog_with(boundary)}
        coverage = compute_boundary_coverage([LISTEN_EDGE], {"go"}, cats)
        return verify_claim(_claim(claim_boundary), BoundaryMap(), coverage)

    def test_row_2_tagged_and_shadowed_qualifies_the_all_clear(self) -> None:
        """The shipped behaviour: disclosed, and the clean verdict says so."""
        verdict = self._verdict("net_listen")
        assert verdict.verdict == "confirmed_with_caveats", verdict.verdict
        cav = next(c for c in verdict.caveats
                   if c["kind"] == "deferred_crossing")
        assert cav["entries"] == ["net.Listen"]
        assert cav["boundary"] == "net_recv"
        assert "does not name" in cav["detail"]

    def test_row_2_control_without_the_shadow_it_is_a_false_all_clear(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """THE CONTROL THAT COSTS SOMETHING, and the whole argument for clause 3.

        With the shadow map emptied -- i.e. a disclosure tag that merely mints
        nothing -- the SAME edge and the SAME claim produce a BARE ``confirmed``
        over a live listener, because the call is still classified and therefore
        still counts as examined (INV-buzab). If this test ever stops
        distinguishing itself from the one above, the shadow has stopped working
        and the boundary has become a false-all-clear generator.
        """
        monkeypatch.setattr(io_boundary, "DEFERRED_CROSSING_SHADOWS", {})
        verdict = self._verdict("net_listen")
        assert verdict.verdict == "confirmed", verdict.verdict
        assert not any(c["kind"] == "deferred_crossing" for c in verdict.caveats)

    def test_row_3_the_shadow_does_not_reach_an_unrelated_boundary(self) -> None:
        """SCOPE, ASSERTED POSITIVELY. A listener must not qualify an fs_write
        claim -- that is the over-withholding this design refuses."""
        verdict = self._verdict("net_listen", claim_boundary="fs_write")
        assert not any(c["kind"] == "deferred_crossing" for c in verdict.caveats)


class TestTheTwoEnvelopePathsCannotDrift:
    """A GATE ON THE SHAPE, not on a remembered list (INV-pubom).

    ``cmd_io_boundaries`` rebuilds the JSON envelope BY HAND on the filtered
    path instead of calling :meth:`BoundaryMap.to_dict`, so every new
    disclosed-only bucket has to be added in two homes. Adding ``net_listen``
    to one of them is exactly what happened here, and the existing key-lock
    test caught it -- but only because someone had already written the new key
    into a hardcoded ``expected_keys``. That is a gate that fires after the
    author has done the work, not one that does the work for them.

    This asserts the two paths agree by DERIVATION, so the next bucket is
    covered on the commit that adds it, with no list to remember.
    """

    def test_every_disclosed_only_boundary_has_an_envelope_count_key(self) -> None:
        keys = set(BoundaryMap().to_dict())
        for boundary in _DISCLOSED_ONLY_BOUNDARIES:
            assert f"{boundary}_edges" in keys, (
                f"{boundary} is held out of total_io_edges but has no "
                f"{boundary}_edges disclosure key, so it is suppressed rather "
                f"than disclosed -- the opposite of what the hold-out is for"
            )

    def test_the_filtered_path_declares_the_same_keys_as_to_dict(self) -> None:
        """Read out of the SOURCE rather than by running the CLI, because the
        filtered branch needs a whole behavior-map fixture to reach and the
        thing under test is whether the two key sets were written to match."""
        import inspect
        from hypergumbo_core import cli

        src = inspect.getsource(cli.cmd_io_boundaries)
        for boundary in _DISCLOSED_ONLY_BOUNDARIES:
            assert f'"{boundary}_edges"' in src, (
                f"the filtered envelope in cmd_io_boundaries omits "
                f"{boundary}_edges; the unfiltered path emits it, so the two "
                f"JSON paths would disagree (INV-pubom)"
            )
