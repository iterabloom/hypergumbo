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
these tests load the SHIPPED go catalogue and the edge dicts are the shapes an
actual ``hypergumbo survey`` emitted over a Go accept-loop fixture.

THE SHIPPED TREE NOW CARRIES ``net_listen`` (INV-kanuk). Go's 21 setup and
serve-loop rows were retagged once the census (WI-hazop) and the reachability
pass (WI-vapud, measurement 0009) cleared ADR-0049 ruling 3's bar, so these
tests assert the REAL rows rather than synthesising one. THE FALSIFIABILITY
CONTROL MOVED WITH THEM: it is no longer "the shipped catalogue defers nothing"
-- that sentence is now false -- but :func:`_go_catalog_without_deferrals`, the
same catalogue with every deferred row forced back to ``net_recv``, i.e. the
tree as it stood before the retag. A test that cannot fail is worse than no
test, and the obvious edit here (delete the control, it goes red) is exactly
how a control stops controlling.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import hypergumbo_core.io_boundary as io_boundary
from hypergumbo_core.io_boundary import (
    BoundaryMap,
    unruled_multi_boundary_primitives,
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
    _MAX_REPORTED_UNTYPED_SITES,
    _deferred_crossing_caveat,
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


#: The go rows INV-kanuk moved, as (module, name). Written out rather than
#: derived from the catalogue, because a list derived from the thing under test
#: agrees with it by construction and would pass over a catalogue that lost
#: every row.
GO_DEFERRED_ROWS: frozenset[tuple[str, str]] = frozenset({
    ("net", "Listen"), ("net", "ListenTCP"), ("net", "ListenUDP"),
    ("net", "ListenUnix"), ("net", "ListenPacket"),
    ("syscall", "Socket"), ("syscall", "Bind"), ("syscall", "Listen"),
    ("unix", "Socket"), ("unix", "Bind"), ("unix", "Listen"),
    # THE LAUNCH ROWS, moved by measurement 0010 (ADR-0049 open work step 3).
    # Each blocks and serves: the request bytes reach a registered handler, and
    # the only value returned to this caller is an error. Both adjudicated
    # findings rooted in `net/http.ListenAndServe` are false positives, and in
    # one of them the sink path begins at a call that runs BEFORE the launch
    # statement.
    ("net/http", "ListenAndServe"), ("net/http", "ListenAndServeTLS"),
    ("net/http", "Serve"),
    ("github.com/gin-gonic/gin.Engine", "Run"),
    ("github.com/gin-gonic/gin.Engine", "RunTLS"),
    ("github.com/labstack/echo/v4.Echo", "Start"),
    ("github.com/labstack/echo/v4.Echo", "StartTLS"),
    ("github.com/gofiber/fiber/v2.App", "Listen"),
    ("github.com/gofiber/fiber/v2.App", "ListenTLS"),
    ("google.golang.org/grpc.Server", "Serve"),
})

#: The go rows that MUST NOT move: each returns data chosen by the far side.
GO_TRANSFER_ROWS: frozenset[tuple[str, str]] = frozenset({
    # WI-suhug: the bufio rows INV-bagok withheld until a target kind could
    # name a network stream. They are TRANSFERS (the read happens in the
    # scope that built the handle -- measurement 0010's finding), rowed under
    # net_recv beside their fs_read / ipc_recv twins and selected only by a
    # ``net_stream`` stamp; an unstamped call keeps its declared fallback.
    ("bufio", "NewScanner"), ("bufio", "NewReader"),
    ("bufio.Reader", "ReadString"), ("bufio.Reader", "ReadBytes"),
    ("bufio.Reader", "ReadLine"), ("bufio.Reader", "ReadRune"),
    ("bufio.Reader", "ReadSlice"), ("bufio.Reader", "Read"),
    ("bufio.Scanner", "Scan"), ("bufio.Scanner", "Text"),
    ("bufio.Scanner", "Bytes"),
    # Genuine transfers: each returns data chosen by the far side.
    ("net.Listener", "Accept"), ("net.Conn", "Read"),
    ("syscall", "Accept"), ("syscall", "Accept4"),
    ("syscall", "Recvfrom"), ("syscall", "Recvmsg"),
    ("unix", "Accept"), ("unix", "Accept4"),
    ("unix", "Recvfrom"), ("unix", "Recvmsg"),
})


def _go_catalog_without_deferrals():
    """The shipped go catalogue as it stood BEFORE INV-kanuk's retag.

    Every ``net_listen`` row forced back to ``net_recv``. This is the
    falsifiability control for every assertion below: the shipped catalogue can
    no longer play that role, because it now genuinely defers.
    """
    cat = load_catalog("go")
    prims = [
        replace(p, boundary="net_recv") if p.boundary == "net_listen" else p
        for p in cat.primitives
    ]
    assert not any(p.boundary == "net_listen" for p in prims)
    assert any(p.boundary == "net_listen" for p in cat.primitives), (
        "the shipped go catalogue carries no net_listen row, so this control "
        "is controlling for nothing -- INV-kanuk's retag has been reverted"
    )
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
        """Asked of the SHIPPED row, not a synthesised one (INV-kanuk)."""
        cat = load_catalog("go")
        assert cat.deferred_crossings("net", "Listen") == frozenset({"net_recv"})

    def test_an_ordinary_row_shadows_nothing(self) -> None:
        """The transfer rows are the counter-control: they survived the retag
        and must keep shadowing nothing, or the family took `accept` with it."""
        cat = load_catalog("go")
        assert cat.deferred_crossings("net.Conn", "Read") == frozenset()
        assert cat.deferred_crossings("net.Listener", "Accept") == frozenset()
        assert cat.deferred_crossings("syscall", "Recvfrom") == frozenset()

    def test_sites_are_grouped_by_the_boundary_they_shadow(self) -> None:
        assert deferred_crossing_sites([LISTEN_EDGE], {"go": load_catalog("go")}) == {
            "net_recv": ["net.Listen"],
        }

    def test_no_sites_when_no_row_defers(self) -> None:
        """FALSIFIABILITY CONTROL. Without it the test above is satisfied by a
        function that returns the same dict for any input."""
        cats = {"go": _go_catalog_without_deferrals()}
        assert deferred_crossing_sites([LISTEN_EDGE], cats) == {}


class TestTheThreeRowTable:
    """ADR-0016's table, reproduced for this boundary. The MIDDLE ROW is the
    reason the shadow is not optional."""

    def _verdict(self, boundary: str, claim_boundary: str = "net_recv"):
        cats = {"go": load_catalog("go") if boundary == "net_listen"
                else _go_catalog_without_deferrals()}
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


class TestTheDisclosureSentenceAtScale:
    """The truncation branch, which a one-site fixture never reaches.

    Every other test here uses a single deferred-crossing site, so the "+N more"
    path was uncovered while the file read as thorough. CI's package-isolated
    coverage gate is what said so -- a local run reached it through some other
    test and reported 100%, which is the cross-package gap
    ``check-package-coverage`` exists to catch.
    """

    def test_the_sentence_is_bounded_and_says_how_many_it_dropped(self) -> None:
        sites = [f"pkg{i}.Listen" for i in range(_MAX_REPORTED_UNTYPED_SITES + 3)]
        cav = _deferred_crossing_caveat("net_recv", sites)
        assert "(+3 more)" in cav["detail"]
        # The FULL list still reaches the machine surface; only the human
        # sentence is bounded, the same trade the sibling caveats make.
        assert cav["entries"] == sites
        assert str(len(sites)) in cav["detail"]

    def test_at_or_below_the_cap_there_is_no_suffix(self) -> None:
        """FALSIFIABILITY CONTROL for the branch above."""
        sites = [f"pkg{i}.Listen" for i in range(_MAX_REPORTED_UNTYPED_SITES)]
        assert "more)" not in _deferred_crossing_caveat("net_recv", sites)["detail"]


class TestTheUnruledDebtListIsReachable:
    """``unruled_multi_boundary_primitives`` over the REAL shipped catalogues.

    Not a deferred-crossing test. It is here because ADR-0049 added a value to
    ``CATALOG_BOUNDARY_TYPES``, which puts ``io_boundary.py`` in the changed-file
    set that CI holds to 100% -- and this function's body was reached only
    through a wider selection than the per-PR manifest runs. Asserting it
    directly makes the coverage independent of which other test happens to walk
    past it.
    """

    def test_it_reports_a_stable_debt_list_over_the_shipped_tree(self) -> None:
        from hypergumbo_core.io_boundary import load_catalog
        cats = {lang: load_catalog(lang) for lang in ("c", "erlang", "go", "python")}
        out = unruled_multi_boundary_primitives(cats)
        assert isinstance(out, list)
        assert out == sorted(out), "the list is sorted so a pin can be exact"
        for entry in out:
            assert ":" in entry, f"expected lang:qualified_name, got {entry!r}"

    def test_a_catalogue_with_no_multi_boundary_rows_reports_nothing(self) -> None:
        """CONTROL: an empty result must come from ABSENCE, not from the loop
        never running."""
        from hypergumbo_core.io_boundary import load_catalog
        assert unruled_multi_boundary_primitives({}) == []
        assert isinstance(
            unruled_multi_boundary_primitives({"go": load_catalog("go")}), list,
        )

    def test_an_unruled_multi_boundary_primitive_is_reported(self) -> None:
        """THE REPORTING BRANCH, WHICH THE SHIPPED TREE CANNOT REACH.

        ``unruled_multi_boundary_primitives`` returns ``[]`` across all fifteen
        catalogues, with and without default overlays -- every multi-boundary
        primitive in the tree today carries a ruling. So the append is
        unreachable from real data and a synthetic row is the ONLY way to
        exercise it, which is exactly why the line sat uncovered until CI's
        package-isolated gate asked.

        Kept honest by the control above: an empty answer must come from
        absence, not from a loop that never runs. Here the loop runs and finds
        something, so the two together pin both directions.
        """
        from hypergumbo_core.io_boundary import IoPrimitive

        cat = replace(
            load_catalog("go"),
            primitives=[
                # ``boundary_ruling="unruled"`` must be EXPLICIT. A
                # multi-boundary primitive with the field unset returns
                # ``None``, not ``unruled`` -- "nothing declared" and "declared
                # open" are different states, which is the INV-vaduk split that
                # made this predicate necessary in the first place.
                IoPrimitive(module="acme", name="ambiguous",
                            boundary="net_recv", kind="function",
                            boundary_ruling="unruled"),
                IoPrimitive(module="acme", name="ambiguous",
                            boundary="fs_read", kind="function",
                            boundary_ruling="unruled"),
            ],
        )
        assert unruled_multi_boundary_primitives({"go": cat}) == [
            "go:acme.ambiguous",
        ]


class TestTheGoRetag:
    """INV-kanuk: the first catalogue rows to use ``net_listen``.

    Go's 8 connection-SETUP rows minted ``untrusted_input`` at calls that return
    a descriptor or an ``error``. WI-dosov had already removed the same shape
    from Haskell, but the enforcing F2 gate could not see Go at all -- it
    parametrised over a hardcoded 9-language list Go was not in, AND matched
    lowercase names while Go capitalises every export, so adding Go to the list
    would still have reported clean.

    THE FIX IS A RETAG, NOT A REMOVAL, AND THAT DISTINCTION IS THE ITEM. This
    item was blocked on its own reasoning that removal was unlicensed: go's real
    receive surface is unreachable on idiomatic code (``ln, _ := net.Listen(...)``
    then ``ln.Accept()`` emits ``go:external:0-0:Accept``), so deleting the rows
    would take go's network-input surface to nothing rather than relocating it
    -- the WI-lunav failure at eight rows. A retag has no such problem: the call
    is still classified, so it still counts as examined (INV-buzab), and the
    shadow qualifies the clean verdict instead of granting it.
    """

    def test_every_setup_and_serve_row_moved(self) -> None:
        cat = load_catalog("go")
        listen = {(p.module, p.name) for p in cat.primitives
                  if p.boundary == "net_listen"}
        assert listen == GO_DEFERRED_ROWS

    def test_nothing_else_moved_with_them(self) -> None:
        """THE COUNTER-CONTROL, and it guards two different things at once.

        ADR-0049 ruling 4 keeps ``accept`` a TRANSFER, and a family-wide retag
        is exactly how it would have been swept up. The launch rows are a
        separate case: they ARE deferred crossings, and they stay only because
        the cross-language pin has not been released yet."""
        cat = load_catalog("go")
        recv = {(p.module, p.name) for p in cat.primitives
                if p.boundary == "net_recv"}
        assert recv == GO_TRANSFER_ROWS

    def test_nothing_was_dropped_on_the_way(self) -> None:
        """The two sets PARTITION what used to be net_recv. A retag that lost a
        row would satisfy both tests above and still be a recall regression."""
        cat = load_catalog("go")
        moved = {(p.module, p.name) for p in cat.primitives
                 if p.boundary in ("net_listen", "net_recv")}
        assert moved == GO_DEFERRED_ROWS | GO_TRANSFER_ROWS
        assert len(moved) == 42  # 31 + WI-suhug's 11 bufio net_recv rows

    def test_the_f2_predicate_now_comes_back_empty_for_go(self) -> None:
        """INV-kanuk's own repro, and it is EMPTY NOW -- which it was not.

        The item filed EIGHT offenders. INV-kanuk removed seven and left the
        eighth, ``fiber.App.Listen``, deliberately: a FALSE POSITIVE OF THE
        PREDICATE rather than a survivor, since the predicate matches lowercase
        NAMES while the family is cut by MECHANISM, and fiber's ``Listen`` is a
        server launch spelled like a setup call. It was exempted at ROW
        granularity so the exemption could not quietly cover anything else.

        The launch retag moved it, so the exemption is deleted and the
        predicate is clean. THE ASSERTION IS DELIBERATELY NOT JUST ``== []``:
        emptiness here is only meaningful if the row still EXISTS somewhere,
        and the second half checks it landed on ``net_listen`` rather than
        being dropped. An empty gate over a deleted row is the failure this
        whole file is about."""
        cat = load_catalog("go")
        offenders = sorted(f"{p.module}.{p.name}" for p in cat.primitives
                           if p.boundary == "net_recv"
                           and p.name.lower() in ("socket", "bind", "listen"))
        assert offenders == []
        listen_named = sorted(f"{p.module}.{p.name}" for p in cat.primitives
                              if p.boundary == "net_listen"
                              and p.name.lower() in ("socket", "bind", "listen"))
        assert "github.com/gofiber/fiber/v2.App.Listen" in listen_named

    def test_a_shipped_listener_qualifies_a_clean_net_recv_claim(self) -> None:
        """END TO END on the shipped catalogue: ADR-0049 ruling 3's bar.

        The retag must not buy a false all-clear. Over a real ``net.Listen``
        edge, a "no network input" claim comes back QUALIFIED rather than
        granted, naming the site.
        """
        cats = {"go": load_catalog("go")}
        coverage = compute_boundary_coverage([LISTEN_EDGE], {"go"}, cats)
        verdict = verify_claim(_claim("net_recv"), BoundaryMap(), coverage)
        assert verdict.verdict == "confirmed_with_caveats", verdict.verdict
        cav = next(c for c in verdict.caveats
                   if c["kind"] == "deferred_crossing")
        assert cav["entries"] == ["net.Listen"]

    def test_before_the_retag_the_same_claim_was_granted_outright(self) -> None:
        """WHAT THE RETAG BOUGHT, stated as a difference rather than asserted.

        On the pre-retag catalogue the identical edge and claim produce a bare
        ``confirmed``: ``net.Listen`` was a net_recv row, so it minted a source
        AND counted as examined. If this stops differing from the test above,
        the retag has stopped changing anything.
        """
        cats = {"go": _go_catalog_without_deferrals()}
        coverage = compute_boundary_coverage([LISTEN_EDGE], {"go"}, cats)
        verdict = verify_claim(_claim("net_recv"), BoundaryMap(), coverage)
        assert not any(c["kind"] == "deferred_crossing" for c in verdict.caveats)


# ---------------------------------------------------------------------------
# WI-fasap: the DATABASE twin -- ``db_compose`` shadows ``db_read``
# ---------------------------------------------------------------------------

#: Captured from ``hypergumbo survey`` over a two-function Django fixture
#: (``Order.objects.filter(active=False).delete()`` and ``for o in
#: Order.objects.filter(active=True):``), not composed by hand.
COMPOSE_EDGE = {
    "src": "python:app.py:8-9:purge:function",
    "dst": "python:django.db.models:0-0:filter:external_symbol",
    "type": "calls",
    "line": 9,
}
#: The evaluation call site py.py emits at the ``for`` -- the read itself.
EVALUATION_EDGE = {
    "src": "python:app.py:12-14:export:function",
    "dst": "python:django.db.models:0-0:__iter__:external_symbol",
    "type": "calls",
    "line": 13,
}

#: The ``django.db.models`` rows that carried ``db_read`` BEFORE the retag,
#: written out rather than derived, for the reason ``GO_DEFERRED_ROWS`` gives.
DJANGO_PRE_RETAG_READ_ROWS: frozenset[str] = frozenset({
    "all", "filter", "exclude", "get", "count", "exists", "first", "last",
    "values", "values_list", "annotate", "aggregate", "order_by", "distinct",
    "none", "iterator", "earliest", "latest", "in_bulk",
    "select_related", "prefetch_related",
})
#: The executing reads, which MUST stay: each returns rows, a row, or a scalar
#: the database chose -- plus the three evaluation dunders WI-fasap added.
DJANGO_READ_ROWS: frozenset[str] = frozenset({
    "get", "count", "exists", "first", "last", "aggregate", "earliest",
    "latest", "in_bulk", "iterator", "__iter__", "__aiter__", "__getitem__",
})


def _python_catalog_with_the_combinators_reading():
    """The shipped python catalogue as it stood BEFORE WI-fasap: every
    ``db_compose`` row forced back to ``db_read``. The falsifiability control
    for the assertions below, exactly as :func:`_go_catalog_without_deferrals`
    is for the net family."""
    cat = load_catalog("python")
    prims = [
        replace(p, boundary="db_read") if p.boundary == "db_compose" else p
        for p in cat.primitives
    ]
    assert not any(p.boundary == "db_compose" for p in prims)
    assert any(p.boundary == "db_compose" for p in cat.primitives), (
        "the shipped python catalogue carries no db_compose row, so this "
        "control is controlling for nothing -- WI-fasap's retag has been reverted"
    )
    return replace(cat, primitives=prims)


class TestTheDatabaseTwinVocabulary:
    """``db_compose`` carries net_listen's three clauses and nothing more."""

    def test_it_is_catalog_declarable_and_declared_last(self) -> None:
        assert "db_compose" in CATALOG_BOUNDARY_TYPES
        assert "db_compose" in KNOWN_IO_BOUNDARIES
        # Declared LAST so first-declared-wins resolution of every existing
        # row is untouched by the addition.
        assert CATALOG_BOUNDARY_TYPES[-1] == "db_compose"

    def test_clause_1_it_mints_no_taint(self) -> None:
        assert "db_compose" not in AUTO_SOURCE_LABEL_MAP

    def test_clause_2_it_is_disclosed_not_headlined(self) -> None:
        assert "db_compose" in _DISCLOSED_ONLY_BOUNDARIES

    def test_clause_3_the_shadow_is_scoped_to_db_read(self) -> None:
        assert "db_compose" not in OPAQUE_BOUNDARIES
        assert DEFERRED_CROSSING_SHADOWS["db_compose"] == "db_read"

    def test_it_is_not_named_db_query(self) -> None:
        """``db_query`` is the database_query linker's ``call_kind`` and names
        a call that EXECUTES a query -- the opposite of this value. One string
        with two readings on two axes is the INV-tutar shape."""
        assert "db_query" not in KNOWN_IO_BOUNDARIES


class TestTheDatabaseTwinCatalogueQuery:

    def test_a_combinator_shadows_db_read(self) -> None:
        cat = load_catalog("python")
        assert cat.deferred_crossings("django.db.models", "filter") == frozenset({"db_read"})
        assert cat.deferred_crossings("django.db.models", "select_for_update") == frozenset({"db_read"})

    def test_an_executing_read_shadows_nothing(self) -> None:
        """The counter-control: the reads survived the retag and must keep
        shadowing nothing, or the family took ``get`` with it."""
        cat = load_catalog("python")
        for name in ("get", "exists", "iterator", "__iter__", "__getitem__"):
            assert cat.deferred_crossings("django.db.models", name) == frozenset(), name

    def test_sites_are_grouped_under_db_read(self) -> None:
        cats = {"python": load_catalog("python")}
        assert deferred_crossing_sites([COMPOSE_EDGE, EVALUATION_EDGE], cats) == {
            "db_read": ["django.db.models.filter"],
        }

    def test_no_sites_when_the_combinators_read(self) -> None:
        """FALSIFIABILITY CONTROL."""
        cats = {"python": _python_catalog_with_the_combinators_reading()}
        assert deferred_crossing_sites([COMPOSE_EDGE, EVALUATION_EDGE], cats) == {}


def _vouched(cat):
    """The catalogue as a USER overlay would present it: every row vouched.

    The Django rows ship in the community overlay, and ADR-0047 stamps a
    default overlay's rows ``unvouched`` -- they add detections and never
    license an all-clear, so on the shipped tree a Django program's boundary
    claims are ``inconclusive`` before clause 3 is ever consulted (pinned
    below). A user's own ``io_primitives.d`` rows ARE vouched, and that is
    the population for which the shadow is the FIRST line of defence rather
    than the second. Vouching the shipped rows isolates the mechanism under
    test without inventing a row.
    """
    return replace(cat, primitives=[replace(p, unvouched=False) for p in cat.primitives])


class TestTheDatabaseThreeRowTable:
    """ADR-0016's table for ``db_compose`` (ADR-0049 open work 5, INV-buzab).
    The MIDDLE ROW is again the reason the shadow is not optional."""

    def _verdict(self, *, shipped: bool, claim_boundary: str = "db_read"):
        cat = load_catalog("python") if shipped else _python_catalog_with_the_combinators_reading()
        cats = {"python": _vouched(cat)}
        coverage = compute_boundary_coverage([COMPOSE_EDGE], {"python"}, cats)
        return verify_claim(_claim(claim_boundary), BoundaryMap(), coverage)

    def test_on_the_shipped_tree_the_community_gate_reaches_first(self) -> None:
        """WHAT SHIPS TODAY, so the mechanism tests below are not mistaken for
        it: the overlay's rows are unvouched (ADR-0047), the module is reported
        as unexamined, and the verdict is withheld outright -- while the
        disclosure clause 3 needs is computed all the same, ready for the
        vouched case."""
        cats = {"python": load_catalog("python")}
        coverage = compute_boundary_coverage([COMPOSE_EDGE], {"python"}, cats)
        verdict = verify_claim(_claim("db_read"), BoundaryMap(), coverage)
        assert verdict.verdict == "inconclusive", verdict.verdict
        assert "django.db.models" in (coverage.reason or "")
        assert coverage.deferred_crossing_sites == {"db_read": ["django.db.models.filter"]}

    def test_row_2_tagged_and_shadowed_qualifies_the_all_clear(self) -> None:
        verdict = self._verdict(shipped=True)
        assert verdict.verdict == "confirmed_with_caveats", verdict.verdict
        cav = next(c for c in verdict.caveats if c["kind"] == "deferred_crossing")
        assert cav["entries"] == ["django.db.models.filter"]
        assert cav["boundary"] == "db_read"
        assert "does not name" in cav["detail"]
        assert "evaluated elsewhere" in cav["detail"]

    def test_row_2_control_without_the_shadow_it_is_a_false_all_clear(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """THE CONTROL THAT COSTS SOMETHING: a query builder that merely mints
        nothing would hand ``verify-claims`` a bare ``confirmed`` over a
        database the program reads through a QuerySet it returns to a caller."""
        monkeypatch.setattr(io_boundary, "DEFERRED_CROSSING_SHADOWS", {})
        verdict = self._verdict(shipped=True)
        assert verdict.verdict == "confirmed", verdict.verdict
        assert not any(c["kind"] == "deferred_crossing" for c in verdict.caveats)

    def test_row_3_the_shadow_does_not_reach_an_unrelated_boundary(self) -> None:
        verdict = self._verdict(shipped=True, claim_boundary="fs_write")
        assert verdict.verdict == "confirmed", verdict.verdict
        assert not any(c["kind"] == "deferred_crossing" for c in verdict.caveats)

    def test_before_the_retag_the_combinator_was_a_read_and_shadowed_nothing(self) -> None:
        """WHAT THE RETAG BOUGHT, stated as a difference: on the pre-retag
        catalogue the same edge is a ``db_read`` row -- it minted a source AND
        counted as examined, and no disclosure was raised."""
        verdict = self._verdict(shipped=False)
        assert verdict.verdict == "confirmed", verdict.verdict
        assert not any(c["kind"] == "deferred_crossing" for c in verdict.caveats)


class TestTheDjangoRetag:
    """WI-fasap: which rows moved, which stayed, and that nothing was dropped."""

    def _rows(self, boundary: str) -> frozenset[str]:
        cat = load_catalog("python")
        return frozenset(p.name for p in cat.primitives
                         if p.module == "django.db.models" and p.boundary == boundary)

    def test_the_compose_rows_are_exactly_the_type_preserving_members(self) -> None:
        """ONE FACT, ONE HOME. "Lazy" and "returns a QuerySet" are the same
        statement about the same members, so the ``db_compose`` rows are pinned
        EQUAL to the signature table's ``django.db.models`` members in both
        directions. Dunders are excepted with a stated reason: ``__getitem__``
        serves the SLICE form in the signature table and the INDEX form under
        ``db_read``, split at the producer by AST shape."""
        from hypergumbo_core.library_signatures import load_library_signatures

        preserving = {
            key.rpartition(".")[2]
            for key, ret in load_library_signatures("python").items()
            if key.startswith("django.db.models.") and ret == "django.db.models"
            and not key.rpartition(".")[2].startswith("__")
        }
        assert self._rows("db_compose") == preserving
        assert len(preserving) == 21

    def test_the_executing_reads_stayed(self) -> None:
        assert self._rows("db_read") == DJANGO_READ_ROWS

    def test_nothing_was_dropped_on_the_way(self) -> None:
        """Every pre-retag ``db_read`` row is still rowed somewhere. A retag
        that lost a row would satisfy the two tests above and still be a
        recall regression."""
        assert DJANGO_PRE_RETAG_READ_ROWS <= self._rows("db_compose") | self._rows("db_read")

    def test_a_shipped_evaluation_site_is_a_db_read_chain_and_the_combinator_is_not(self) -> None:
        """END TO END on the shipped catalogue over the two captured edges: the
        ``for`` reads, the ``filter`` composes, and a "no database read" claim
        over the composing function alone comes back QUALIFIED, not granted."""
        from hypergumbo_core.io_boundary import tag_io_boundaries
        from dataclasses import dataclass
        from typing import Any

        @dataclass
        class _E:
            src: str
            dst: str
            edge_type: str
            meta: dict[str, Any] | None = None

        cats = {"python": load_catalog("python")}
        edges = [_E(e["src"], e["dst"], e["type"]) for e in (COMPOSE_EDGE, EVALUATION_EDGE)]
        assert tag_io_boundaries(edges, cats) == 2
        assert [e.meta["io_boundary"] for e in edges] == ["db_compose", "db_read"]
