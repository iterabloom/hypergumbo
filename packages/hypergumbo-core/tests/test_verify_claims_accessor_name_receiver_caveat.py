# SPDX-License-Identifier: AGPL-3.0-or-later
"""A clean verdict must stay qualified when a receiver was typed from a NAME.

THE HOLE THIS CLOSES. ``CAVEAT_UNTYPED_RECEIVER`` qualifies a clean boundary
verdict with the calls whose receiver the analysis could not type. INV-mumov
taught the Python analyzer to type a receiver from a relation-accessor NAME that
the project's own models declare, with nothing known about the root -- which
REMOVES those sites from ``untyped_receiver_sites``. Left there, the verdict
would fall silent about exactly the calls whose typing rests on a name rather
than on a resolved class, and it would get quieter every time the analyzer got
better at inferring. Loosening a withholding gate is the false-all-clear
direction; a recall gain is not a licence to take a disclosure away with it.

THE QUALIFICATION IS NOT DROPPED, IT IS MADE MORE PRECISE. "I could not type N
receivers" becomes "I typed N receivers from a declared accessor name, not from
a typed root."

WHY A CAVEAT AND NOT A REFUSAL, measured rather than argued. A shuffled-index
ablation over pretix -- 20 size- and frequency-matched WRONG accessor sets --
put the rule's true:shuffled firing ratio at 78.9 against a kill threshold of 10
fixed before the number existed, on +2,943 correctly-slotted edges with 0 lost.
Refusing the inference would cost all of that to avoid a rare error. Being right
is not being VERIFIED, and this caveat says which one the reader is getting.

WHY THE MEASUREMENT COULD NOT ANSWER THIS AND A FIXTURE MUST. The four-arm
verify-claims measurement on pretix showed 45 findings generated and 0 lost, but
it could not test this direction AT ALL: the caveat only qualifies CLEAN
verdicts, and every one of pretix's seven claims comes back violated. That is
structural, not luck -- the boundary most sensitive to Django receiver typing is
``db_read``, which pretix crosses constantly -- so no claims file fixes it on
that repo. The question moved here, where both a correct and an incorrect typing
can be constructed on purpose.

EDGES ARE BUILT BY HAND HERE BECAUSE CI TESTS PACKAGES IN ISOLATION, and this
file's sibling warns that a hand-built fixture is how you come to test a shape
the producer never emits. The other half of that pact is
``test_py_django_untyped_relation_root.py::TestTheProvenanceStamp``, which
asserts the very shape assumed below -- ``resolution_quality="accessor_name"``
on a method-construct edge into ``django.db.models`` -- against the real
producer. Neither file can drift without the other going red.
"""

from hypergumbo_core.io_boundary import BoundaryMap, IoBoundaryCatalog, IoPrimitive
from hypergumbo_core.verify_claims import (
    CAVEAT_ACCESSOR_NAME_RECEIVER,
    _accessor_name_receiver_caveat,
    CAVEAT_UNTYPED_RECEIVER,
    BoundaryCoverage,
    Claim,
    accessor_name_receiver_sites,
    compute_boundary_coverage,
    untyped_receiver_sites,
    verify_claim,
)

ORM = "django.db.models"


def _py_catalog() -> IoBoundaryCatalog:
    """``get`` catalogued METHOD-kind under TWO boundaries, which is the point.

    The real python catalogue declares ``get`` for ``db_read``
    (``django.db.models``) AND for ``net_send`` (``requests.Session``,
    ``httpx.Client``, ...). That collision is not an inconvenience here, it is
    the whole reason the caveat is reachable -- see
    :class:`TestTheCaveatIsNotScopedToTheBelievedModule`.
    """
    return IoBoundaryCatalog(
        language="python",
        primitives=[
            IoPrimitive(boundary="db_read", module=ORM, name="get", kind="method"),
            IoPrimitive(boundary="net_send", module="requests.Session",
                        name="get", kind="method"),
            IoPrimitive(boundary="net_send", module="socket.socket",
                        name="sendall", kind="method"),
            IoPrimitive(boundary="fs_read", module="pathlib.Path",
                        name="read_text", kind="method"),
        ],
        stdlib_modules=frozenset({"socket", "pathlib"}),
        module_completeness={"socket": "2026-09-10", "pathlib": "2026-09-10"},
    )


def _accessor_named(name: str = "get", *, line: int = 4) -> dict:
    """``thing.seats.get(1)`` -- the slot filled from the accessor NAME.

    Slot-for-slot what ``py.py`` emits for an untyped root, pinned by
    ``TestTheProvenanceStamp`` on the producer side.
    """
    return {
        "src": "python:svc.py:3-4:handler:function",
        "dst": f"python:{ORM}:0-0:{name}:unresolved",
        "type": "calls",
        "line": line,
        "meta": {"call_construct": "method", "framework_dispatch": "django_orm",
                 "resolution_quality": "accessor_name"},
    }


def _typed_orm(name: str = "get", *, line: int = 9) -> dict:
    """``ev.seats.get(1)`` where ``ev`` IS a resolved model instance."""
    return {
        "src": "python:svc.py:8-9:typed:function",
        "dst": f"python:{ORM}:0-0:{name}:unresolved",
        "type": "calls",
        "line": line,
        "meta": {"call_construct": "method", "framework_dispatch": "django_orm",
                 "resolution_quality": "type_inferred"},
    }


def _untyped(name: str = "sendall", *, line: int = 14) -> dict:
    """A receiver the analysis could not type at all -- the sibling's population."""
    return {
        "src": "python:svc.py:13-14:blind:function",
        "dst": f"python:external:0-0:{name}:external_symbol",
        "type": "calls",
        "line": line,
        "meta": {"call_construct": "method"},
    }


def _coverage(edges: list[dict]) -> BoundaryCoverage:
    return compute_boundary_coverage(edges, {"python"}, {"python": _py_catalog()})


def _claim(boundary: str) -> Claim:
    return Claim(id="C", text="t", constraint_boundary=boundary,
                 constraint_must_not_exist=True)


def _kinds(edges: list[dict], boundary: str) -> list[str]:
    verdict = verify_claim(_claim(boundary), BoundaryMap(), _coverage(edges))
    return [c["kind"] for c in verdict.caveats]


class TestTheDisclosureSurvivesTheInference:
    """The blocker: typing a receiver by name must not buy silence."""

    def test_a_clean_verdict_is_qualified_by_an_accessor_named_receiver(self) -> None:
        assert CAVEAT_ACCESSOR_NAME_RECEIVER in _kinds([_accessor_named()], "net_send")

    def test_the_verdict_is_still_reported_clean(self) -> None:
        """QUALIFIED, not withheld. The 2026-08-11 measurement recorded DO NOT
        BUILD the downgrade: on poetry every boundary would have gone
        inconclusive, because the catalogued names include ``get`` / ``read`` /
        ``close``."""
        verdict = verify_claim(
            _claim("net_send"), BoundaryMap(), _coverage([_accessor_named()]),
        )
        assert verdict.verdict == "confirmed_with_caveats"

    def test_the_caveat_names_sites_a_reader_can_check(self) -> None:
        verdict = verify_claim(
            _claim("net_send"), BoundaryMap(), _coverage([_accessor_named(line=4)]),
        )
        caveat = next(c for c in verdict.caveats
                      if c["kind"] == CAVEAT_ACCESSOR_NAME_RECEIVER)
        assert caveat["entries"] == ["svc.py:4 get()"]
        assert caveat["boundary"] == "net_send"
        assert "accessor" in caveat["detail"].lower()


class TestTheControlsThatKeepItFromBeingNoise:
    """A disclosure that fires on everything discloses nothing."""

    def test_a_typed_root_raises_no_accessor_name_caveat(self) -> None:
        """Otherwise every ORM edge in every Django repo would raise it."""
        assert CAVEAT_ACCESSOR_NAME_RECEIVER not in _kinds([_typed_orm()], "net_send")

    def test_it_is_scoped_to_the_claimed_boundary(self) -> None:
        """``get`` is catalogued for net_send and db_read, NOT for fs_read. The
        unscoped version of the sibling signal was measured and refused."""
        assert CAVEAT_ACCESSOR_NAME_RECEIVER not in _kinds([_accessor_named()], "fs_read")

    def test_a_violated_verdict_does_not_acquire_it(self) -> None:
        """Same rule as every caveat in this module: coverage gates the ALL-CLEAR
        and nothing else. Found evidence is trustworthy regardless of what went
        unadjudicated, and this must not become the first exception."""
        from hypergumbo_core.io_boundary import BoundaryMapEntry

        bmap = BoundaryMap()
        bmap.entries["net_send"] = BoundaryMapEntry(boundary="net_send", chains=[{}])
        verdict = verify_claim(
            _claim("net_send"), bmap, _coverage([_accessor_named()]),
        )
        assert verdict.verdict == "violated"
        assert verdict.caveats == []


class TestTheTwoPopulationsAreComplements:
    """A site moves from one list to the other when the analyzer learns to type
    it. That must be a change of SENTENCE, never a change of silence."""

    def test_an_accessor_named_site_is_not_in_the_untyped_map(self) -> None:
        edges = [_accessor_named()]
        cat = {"python": _py_catalog()}
        assert accessor_name_receiver_sites(edges, cat)
        assert untyped_receiver_sites(edges, cat) == {}

    def test_an_untyped_site_is_not_in_the_accessor_named_map(self) -> None:
        edges = [_untyped()]
        cat = {"python": _py_catalog()}
        assert untyped_receiver_sites(edges, cat)
        assert accessor_name_receiver_sites(edges, cat) == {}

    def test_both_disclosures_ride_together_when_both_populations_exist(self) -> None:
        kinds = _kinds([_accessor_named(), _untyped()], "net_send")
        assert CAVEAT_UNTYPED_RECEIVER in kinds
        assert CAVEAT_ACCESSOR_NAME_RECEIVER in kinds


class TestTheCaveatIsNotScopedToTheBelievedModule:
    """THE CRUX. The ``dst`` names ``django.db.models`` -- and that module is the
    INFERENCE, which is the thing that might be wrong.

    Scoping the catalogue lookup to the believed module would assume the answer,
    and would also make the caveat unreachable: a ``db_read`` claim is VIOLATED
    by the very chain that would qualify it, so it could never fire and would be
    a disclosure nothing reads.
    """

    def test_it_qualifies_a_boundary_the_believed_module_does_not_declare(self) -> None:
        """If ``thing.seats`` is NOT a Django manager -- a dict, a
        ``requests.Session`` -- then ``.get`` may well be a ``net_send``. The
        clean net_send verdict is qualified precisely because the analysis
        believes otherwise and could be believing it wrongly."""
        sites = accessor_name_receiver_sites([_accessor_named()],
                                             {"python": _py_catalog()})
        assert "net_send" in sites
        assert "db_read" in sites

    def test_a_method_no_catalogue_declares_raises_nothing(self) -> None:
        sites = accessor_name_receiver_sites([_accessor_named("nonesuch")],
                                             {"python": _py_catalog()})
        assert sites == {}


class TestTheDenominator:
    """"17 sites" is unactionable; "17 of 4,206" tells a reader whether the
    verdict LEANS on the inference or merely touches it."""

    def test_it_counts_accessor_named_sites_over_method_call_sites(self) -> None:
        from hypergumbo_core.verify_claims import accessor_name_receiver_scope
        edges = [_accessor_named(), _typed_orm(), _untyped()]
        assert accessor_name_receiver_scope(edges, {"python": _py_catalog()}) == (1, 3)

    def test_the_denominator_excludes_a_language_with_no_catalogue(self) -> None:
        """Commensurable by construction: a polyglot repo must not dilute the
        ratio with calls nothing could have adjudicated anyway."""
        from hypergumbo_core.verify_claims import accessor_name_receiver_scope
        go = {"src": "go:s.go:1-2:h:function", "dst": "go:net/http:0-0:Do:external_symbol",
              "type": "calls", "line": 2, "meta": {"call_construct": "method"}}
        edges = [_accessor_named(), go]
        assert accessor_name_receiver_scope(edges, {"python": _py_catalog()}) == (1, 1)

    def test_a_non_method_construct_counts_in_neither(self) -> None:
        """A bare ``open()`` asserts no receiver at all."""
        from hypergumbo_core.verify_claims import accessor_name_receiver_scope
        fn = {"src": "python:svc.py:1-2:h:function",
              "dst": "python:builtins:0-0:open:external_symbol",
              "type": "calls", "line": 2, "meta": {}}
        assert accessor_name_receiver_scope([fn], {"python": _py_catalog()}) == (0, 0)

    def test_a_malformed_dst_is_skipped(self) -> None:
        from hypergumbo_core.verify_claims import accessor_name_receiver_scope
        bad = {"src": "python:svc.py:1-2:h:function", "dst": "python:short",
               "type": "calls", "line": 2, "meta": {"call_construct": "method"}}
        assert accessor_name_receiver_scope([bad], {"python": _py_catalog()}) == (0, 0)

    def test_a_non_call_edge_type_is_skipped(self) -> None:
        """``imports`` performs no I/O -- the constant that excludes it is shared."""
        from hypergumbo_core.verify_claims import accessor_name_receiver_scope
        imp = dict(_accessor_named(), type="imports")
        assert accessor_name_receiver_scope([imp], {"python": _py_catalog()}) == (0, 0)

    def test_the_ratio_reaches_the_sentence(self) -> None:
        verdict = verify_claim(
            _claim("net_send"), BoundaryMap(),
            _coverage([_accessor_named(), _typed_orm(), _untyped()]),
        )
        caveat = next(c for c in verdict.caveats
                      if c["kind"] == CAVEAT_ACCESSOR_NAME_RECEIVER)
        assert caveat["scope"] == [1, 3]
        assert "1 of 3 method-call receivers" in caveat["detail"]

    def test_no_clause_is_rendered_without_a_denominator(self) -> None:
        """A denominator of zero is not a ratio, and a fraction rendered from one
        would be a misleading disclosure rather than a missing one."""
        from hypergumbo_core.verify_claims import _scope_clause
        assert _scope_clause((0, 0)) == ""
        assert _scope_clause((0, 5)) == ""
        assert _scope_clause((3, 0)) == ""
        assert "3 of 9" in _scope_clause((3, 9))


class TestAtScaleAndAtTheEdges:
    def test_a_protocol_edge_is_out_of_scope_by_construction(self) -> None:
        """``for x in thing.seats.all()`` emits ``call_construct="protocol"``.

        The producer stamps the provenance there too, but a protocol edge
        asserts no RECEIVER -- it is a language construct invoked by syntax --
        and every consumer in this family filters on ``"method"`` because that
        is what "a receiver was there" means. Excluded here for the same reason
        a bare ``open()`` is excluded from the sibling.
        """
        proto = dict(_accessor_named(),
                     meta={"call_construct": "protocol",
                           "resolution_quality": "accessor_name"})
        assert accessor_name_receiver_sites([proto], {"python": _py_catalog()}) == {}

    def test_at_scale_the_sentence_reports_names_not_sites(self) -> None:
        """Beyond five sites the SITES are not the fact, the method NAMES are --
        the same trade the sibling caveat and ``_MAX_EVIDENCE_ROWS`` make. The
        full list stays in ``entries``, which is the machine surface."""
        edges = [_accessor_named(line=n) for n in range(1, 9)]
        verdict = verify_claim(
            _claim("net_send"), BoundaryMap(), _coverage(edges),
        )
        caveat = next(c for c in verdict.caveats
                      if c["kind"] == CAVEAT_ACCESSOR_NAME_RECEIVER)
        assert "distinct method(s)" in caveat["detail"]
        assert len(caveat["entries"]) == 8

    def test_at_scale_with_many_distinct_names_says_how_many_more(self) -> None:
        cat = IoBoundaryCatalog(
            language="python",
            primitives=[
                IoPrimitive(boundary="net_send", module="requests.Session",
                            name=n, kind="method")
                for n in ("get", "post", "put", "patch", "delete", "head", "options")
            ],
            stdlib_modules=frozenset(),
            module_completeness={},
        )
        edges = [_accessor_named(n, line=i) for i, n in enumerate(
            ("get", "post", "put", "patch", "delete", "head", "options"), start=1)]
        sites = accessor_name_receiver_sites(edges, {"python": cat})["net_send"]
        caveat = _accessor_name_receiver_caveat("net_send", sites, (7, 7))
        assert "+2 more" in caveat["detail"]
