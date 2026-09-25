# SPDX-License-Identifier: AGPL-3.0-or-later
"""A clean boundary verdict must disclose the calls whose receiver it could not type.

WHAT WAS BROKEN (INV-fibis), reproduced on the shipped CLI at dev 9e3bc55e5c with a
two-arm control and nothing else in the file:

    def control_fsread(p):
        with open(p) as fh:
            return fh.read()

    def send_unannotated(sock, payload):     # ARM 1 -- the residual
        return sock.sendall(payload)

    def send_annotated(sock: socket.socket, payload):   # ARM 2 -- otherwise identical
        return sock.sendall(payload)

      ARM 1:  x [CONTROL-FSREAD] violated (2 chains)   <- the run WORKED
              v [NET-SEND] "This service never sends data over the network."
                           Verdict: CONFIRMED          <- FALSE
      ARM 2:  x [CONTROL-FSREAD] violated (2 chains)
              x [NET-SEND] violated (1 chain)          <- SEEN

Exactly one of the two spellings is visible, and the control fires in BOTH arms, so
the null is not a broken run: a security tool certified absence of network egress
about a function whose entire body is a network send.

WHY THE EXISTING GATES MISSED IT. ``_uncatalogued_external_modules`` counts only a dst
that NAMES a module. ARM 1's call edge is ``python:external:0-0:sendall:external_symbol``
-- the bare placeholder for a receiver the analysis could not type -- which names none,
so it is skipped by the ``if not module: continue`` residual that function documents.
That skip is CORRECT and is unchanged here: the placeholder is the largest edge
population in a Python repo and identifies no library to report, so counting it as an
uncatalogued module would downgrade nearly every repo to ``inconclusive`` while telling
the reader nothing. ``compute_boundary_coverage`` therefore still returns
``complete=True``, and ``test_untyped_receiver_population_is_the_disclosed_residual``
still pins that.

WHAT CHANGED IS THE VERDICT, NOT THE COVERAGE. The claim still HOLDS everywhere the
analysis could see; what it stops doing is being SILENT about the calls it could not
adjudicate. ``CAVEAT_UNTYPED_RECEIVER`` is the fourth member of an established family
and reuses ADR-0016 s4's fourth verdict for exactly the distinction its sibling
already argues -- a DISCLAIMER ("a whole language here has no catalogue, I am blind")
versus a QUALIFIED OPINION ("I saw this call, I know ``sendall`` is a catalogued
net_send method, and I could not determine the receiver's type").

BOUNDARY SCOPING IS THE LOAD-BEARING PART, and it is what makes this survivable where
the previously-proposed DOWNGRADE was not. That proposal was measured on 2026-08-11
and recorded DO NOT BUILD IT: on poetry every boundary would downgrade (db_read 234,
ipc_recv 160, fs_read 93, fs_write 67, db_write 40, net_send 37, net_recv 35,
ipc_send 1), because the catalogued method names include ``close`` / ``get`` / ``read``
/ ``write`` / ``send`` -- the most common method names in Python. Two things answer
that: the verdict is QUALIFIED rather than WITHHELD, and a name is matched only
against primitives catalogued for THE CLAIMED boundary, so the unrelated dict ``.get``
from that measurement cannot touch a ``net_send`` verdict at all.

THE OTHER HALF OF THAT REFUTATION DOES NOT SURVIVE AND IS NOT CITED HERE. It also
argued the signal was "anti-correlated with the truth", from ``session.post`` -- a
primitive absent from the catalogue entirely (INV-fotav's third-party gap), not one
reached through an untyped receiver. ``sendall`` IS catalogued (python.yaml net_send,
``socket.socket``), which is why the fixture above is the discriminating case.
"""

import json
from pathlib import Path

import yaml

from hypergumbo_core.cli import cmd_verify_claims
from hypergumbo_core.io_boundary import IoBoundaryCatalog, IoPrimitive
from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.verify_claims import (
    CAVEAT_OPAQUE_BOUNDARY,
    CAVEAT_UNKNOWN_RECEIVER_SCOPE,
    CAVEAT_UNTYPED_RECEIVER,
    BoundaryCoverage,
    Claim,
    _merge_caveat,
    _untyped_receiver_caveat,
    compute_boundary_coverage,
    unknown_receiver_scope,
    untyped_receiver_sites,
    verify_claim,
)
from hypergumbo_core.io_boundary import BoundaryMap


def _py_catalog() -> IoBoundaryCatalog:
    """python.yaml's shape where it matters, and asymmetric where the gate must
    discriminate.

    - ``socket.socket.sendall`` is METHOD-kind and ``net_send``: the primitive the
      repro's untyped receiver actually reaches.
    - ``dict.get`` stands in for the ``db_read`` collision the 2026-08-11
      measurement caught -- a catalogued method name that is also the single most
      common method name in Python.
    - ``builtins.open`` is FUNCTION-kind: a bare ``open()`` has no receiver at all,
      so it must never raise a caveat about one.
    """
    return IoBoundaryCatalog(
        language="python",
        primitives=[
            IoPrimitive(boundary="net_send", module="socket.socket",
                        name="sendall", kind="method"),
            IoPrimitive(boundary="db_read", module="sqlite3.Cursor",
                        name="get", kind="method"),
            IoPrimitive(boundary="fs_read", module="builtins",
                        name="open", kind="function"),
            IoPrimitive(boundary="fs_read", module="pathlib.Path",
                        name="read_text", kind="method"),
        ],
        stdlib_modules=frozenset({"socket", "sqlite3", "pathlib", "builtins"}),
        module_completeness={"pathlib": "2026-08-12", "socket": "2026-08-12",
                             "sqlite3": "2026-08-12", "builtins": "2026-08-12"},
    )


def _untyped(name: str, *, line: int = 10,
             src: str = "python:svc.py:9-10:send_unannotated:function") -> dict:
    """A call through a receiver the analysis could not type.

    Copied slot-for-slot from a real edge emitted by the repro fixture, including
    ``meta.call_construct`` -- the producer's own statement that a RECEIVER was
    there. Reconstructing the shape by hand is how a fixture comes to test
    something the producer never emits.
    """
    return {
        "src": src,
        "dst": f"python:external:0-0:{name}:external_symbol",
        "type": "calls",
        "line": line,
        "meta": {"call_construct": "method", "evidence_type": "ast_call",
                 "evidence_lang": "python"},
    }


def _typed(dst: str, src: str = "python:svc.py:9-10:handler:function") -> dict:
    return {"src": src, "dst": dst, "type": "calls", "line": 3,
            "meta": {"call_construct": "method"}}


def _coverage(edges: list[dict]) -> BoundaryCoverage:
    return compute_boundary_coverage(edges, {"python"}, {"python": _py_catalog()})


def _claim(boundary: str, **kw) -> Claim:
    return Claim(id="C", text="t", constraint_boundary=boundary, **kw)


class TestTheRepro:
    """ARM 1 of the two-arm fixture, at the unit the CLI calls."""

    def test_untyped_receiver_of_a_catalogued_method_qualifies_the_verdict(self) -> None:
        """``sock.sendall(payload)`` with no annotation: the claim may still be
        reported clean, but not silently."""
        coverage = _coverage([_untyped("sendall")])
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        assert verdict.verdict == "confirmed_with_caveats"
        kinds = [c["kind"] for c in verdict.caveats]
        # The scope caveat rides alongside from INV-fibis's unscoped half: this
        # receiver is untyped, so the verdict is closed-world whether or not the
        # method it calls happens to be catalogued. The boundary-scoped
        # disclosure is the one that says something SPECIFIC about net_send.
        assert CAVEAT_UNTYPED_RECEIVER in kinds
        assert kinds == [CAVEAT_UNTYPED_RECEIVER, CAVEAT_UNKNOWN_RECEIVER_SCOPE]

    def test_the_caveat_names_sites_a_reader_can_check_against_the_source(self) -> None:
        """A disclosure that says "some calls" sends nobody anywhere. The receiver's
        TYPE is what is unknown; the call site is known exactly, so name it."""
        coverage = _coverage([_untyped("sendall", line=10)])
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        entries = verdict.caveats[0]["entries"]
        assert entries == ["svc.py:10 sendall()"]
        assert "sendall" in verdict.caveats[0]["detail"]

    def test_coverage_completeness_is_deliberately_unchanged(self) -> None:
        """The residual in ``_uncatalogued_external_modules`` is NOT reversed. This
        item is closed at the VERDICT layer precisely so the blanket-inconclusive
        outcome PR #251 rejected is not reintroduced."""
        assert _coverage([_untyped("sendall")]).complete is True


class TestBoundaryScoping:
    """The 2026-08-11 mis-fire, inverted into an assertion."""

    def test_a_catalogued_name_for_another_boundary_cannot_touch_this_verdict(
        self,
    ) -> None:
        """The measured example verbatim: an unrelated ``.get`` is catalogued
        (db_read) and must leave a ``net_send`` verdict alone. Unscoped, this is
        the branch that downgraded every boundary on poetry."""
        coverage = _coverage([_untyped("get")])
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        # NARROWED, NOT WEAKENED, when INV-fibis's unscoped half landed. The
        # guarantee this test exists for is that an unrelated ``.get`` never
        # produces a NET_SEND-SPECIFIC claim -- "a method the net_send catalogue
        # declares" is false of it, and asserting it was the 2026-08-11
        # mis-fire. That still holds exactly. What the verdict now also carries
        # is the boundary-AGNOSTIC fact that a receiver went untyped, which is
        # true of this edge and says nothing about net_send.
        kinds = [c["kind"] for c in verdict.caveats]
        assert CAVEAT_UNTYPED_RECEIVER not in kinds
        assert kinds == [CAVEAT_UNKNOWN_RECEIVER_SCOPE]

    def test_the_same_call_does_qualify_the_boundary_it_is_catalogued_for(self) -> None:
        """The other side of the same edge -- scoping must narrow the caveat, not
        delete it. Without this, refusing every caveat passes the test above."""
        coverage = _coverage([_untyped("get")])
        verdict = verify_claim(
            _claim("db_read", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        assert verdict.verdict == "confirmed_with_caveats"
        assert verdict.caveats[0]["kind"] == CAVEAT_UNTYPED_RECEIVER


class TestItDoesNotFireWhereThereIsNothingToDisclose:
    """Positive controls in the refusing direction. Caveating unconditionally would
    satisfy every test above."""

    def test_an_uncatalogued_method_name_raises_nothing(self) -> None:
        """``thing.frobnicate()`` through an untyped receiver is not evidence about
        any boundary. Counting it is the blanket downgrade, one rename later."""
        coverage = _coverage([_untyped("frobnicate")])
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        # Still no net_send-SPECIFIC claim -- that is what this test guards.
        # The closed-world scope caveat does fire, because the receiver's type
        # is unknown and ``frobnicate`` is therefore not evidence of ABSENCE
        # either; see TestTheResidualNowClosed for why that changed.
        assert [c["kind"] for c in verdict.caveats] == [
            CAVEAT_UNKNOWN_RECEIVER_SCOPE
        ]

    def test_a_function_kind_primitive_raises_nothing(self) -> None:
        """A bare ``open()`` has NO receiver, so "I could not type the receiver" is
        not a true sentence about it. Matching function-kind rows would make the
        disclosure text false on its own evidence."""
        coverage = _coverage([_untyped("open")])
        verdict = verify_claim(
            _claim("fs_read", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        # The function-kind row still raises no fs_read-specific caveat. This
        # fixture DOES stamp ``call_construct == "method"``, so the scope caveat
        # correctly fires; the genuinely receiver-less shape (no call_construct
        # at all) is pinned by
        # ``test_a_bare_function_call_has_no_receiver_to_be_unknown``.
        assert [c["kind"] for c in verdict.caveats] == [
            CAVEAT_UNKNOWN_RECEIVER_SCOPE
        ]

    def test_a_typed_receiver_raises_nothing(self) -> None:
        """ARM 2. The dst names ``socket.socket``, so there is nothing untyped to
        disclose -- the ordinary machinery adjudicates it."""
        coverage = _coverage(
            [_typed("python:socket.socket:0-0:sendall:external_symbol")]
        )
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        assert verdict.verdict == "confirmed"

    def test_a_producer_stamped_launch_raises_nothing(self) -> None:
        """INV-vokog, held to. A launch is an EXAMINED call reported by name
        through ``CAVEAT_OPAQUE_BOUNDARY``; adding "and its receiver was untyped"
        about the same edge is a second, contradictory statement about one call.
        That disagreement between two consumers of the same population is what
        made rc 3 unreachable in any repo containing a shell script."""
        edge = _untyped("sendall")
        edge["meta"]["io_boundary"] = "command_launch"
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), _coverage([edge]),
        )
        # The verdict IS qualified — by the opaque channel, which is the one
        # that owns this edge. Asserting ``confirmed`` here would have been
        # asserting the launch away; what must hold is that the two channels
        # stay disjoint over one call.
        assert [c["kind"] for c in verdict.caveats] == [CAVEAT_OPAQUE_BOUNDARY]
        assert untyped_receiver_sites([edge], {"python": _py_catalog()}) == {}

    def test_a_non_method_construct_raises_nothing(self) -> None:
        """The placeholder alone is not the signal; the producer's ``call_construct``
        stamp is what says a receiver existed. Without that test the caveat would
        claim a receiver it has no evidence for."""
        edge = _untyped("sendall")
        edge["meta"] = {"evidence_type": "ast_call"}
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), _coverage([edge]),
        )
        assert verdict.verdict == "confirmed"


class TestItDoesNotDisturbTheOtherVerdicts:

    def test_a_violated_verdict_is_untouched(self) -> None:
        """Coverage never gates a positive finding, and this must not become the
        first exception. The direction nobody looks at (INV-fibis's own acceptance
        criteria)."""
        bmap = BoundaryMap()
        bmap.entries["net_send"] = _entry_with_chains(2)
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            bmap, _coverage([_untyped("sendall")]),
        )
        assert verdict.verdict == "violated"
        assert verdict.caveats == []

    def test_a_within_limit_max_chains_verdict_is_qualified_too(self) -> None:
        """``max_chains`` asserts absence of FURTHER chains, so it rests on the same
        completeness. ``BoundaryCoverage``'s own docstring already treats the two
        together; leaving one gated and the other silent is the two-homes defect."""
        verdict = verify_claim(
            _claim("net_send", constraint_max_chains=3),
            BoundaryMap(), _coverage([_untyped("sendall")]),
        )
        assert verdict.verdict == "confirmed_with_caveats"
        assert verdict.caveats[0]["kind"] == CAVEAT_UNTYPED_RECEIVER

    def test_an_inconclusive_verdict_stays_inconclusive(self) -> None:
        """Blindness DOMINATES a caveat: "I could not look" is strictly worse than
        "I looked and could not type a receiver", so a caveat must not launder an
        incomplete analysis into a qualified pass."""
        coverage = BoundaryCoverage(
            complete=False, reason="the analysis produced no call edges at all",
            untyped_receiver_sites={"net_send": ["svc.py:10 sendall()"]},
        )
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        assert verdict.verdict == "inconclusive"


class TestItComposesWithTheSiblingCaveat:

    def test_both_kinds_ride_one_verdict(self) -> None:
        """A repo can both launch a program and call through an untyped receiver.
        ``_merge_caveat`` exists because a second writer erasing the first is this
        module's documented failure (INV-virat); this is that path for the new kind."""
        coverage = BoundaryCoverage(
            complete=False,
            reason="the analysis launches an external program at 1 call site(s)",
            opaque_sites=["subprocess.run"],
            qualifying_only=True,
            untyped_receiver_sites={"net_send": ["svc.py:10 sendall()"]},
        )
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        assert verdict.verdict == "confirmed_with_caveats"
        assert sorted(c["kind"] for c in verdict.caveats) == sorted(
            [CAVEAT_OPAQUE_BOUNDARY, CAVEAT_UNTYPED_RECEIVER]
        )


class TestTheDisclosureScales:
    """At poetry's scale the sites are not the fact; the method names are."""

    def test_a_small_site_list_is_spelled_out(self) -> None:
        cav = _untyped_receiver_caveat("net_send", ["a.py:1 sendall()"])
        assert "a.py:1 sendall()" in cav["detail"]
        assert "distinct method" not in cav["detail"]

    def test_a_large_site_list_names_the_methods_and_keeps_every_entry(self) -> None:
        """Measured shape: poetry's 306 ``fs_read`` sites are 16 names, and its
        103 ``ipc_recv`` sites are ONE name (``get``). Five arbitrary line numbers
        out of three hundred is not a disclosure a reader can act on; the names
        are, because ``group`` is almost all ``re.Match`` and ``read_text`` is
        almost all ``pathlib``. ``entries`` keeps everything."""
        sites = [f"a.py:{i} read()" for i in range(4)] + [
            f"b.py:{i} exists()" for i in range(4)
        ]
        cav = _untyped_receiver_caveat("fs_read", sorted(sites))
        assert "8 call site(s)" in cav["detail"]
        assert "2 distinct method(s): exists(), read()" in cav["detail"]
        assert cav["entries"] == sorted(sites)
        assert "a.py:0 read()" not in cav["detail"]

    def test_the_method_list_is_itself_bounded(self) -> None:
        """A wall of names is a wall too."""
        sites = [f"a.py:{i} m{i}()" for i in range(9)]
        cav = _untyped_receiver_caveat("fs_read", sorted(sites))
        assert "9 distinct method(s)" in cav["detail"]
        assert "(+4 more)" in cav["detail"]


class TestTheCaveatSurvivesAMerge:
    """``_merge_caveat`` re-renders a widened list so the sentence cannot quote a
    stale count — the rule the opaque kind already follows, applied to this one."""

    def test_widening_re_renders_the_prose(self) -> None:
        first = _untyped_receiver_caveat("net_send", ["a.py:1 sendall()"])
        merged = _merge_caveat(
            [first],
            _untyped_receiver_caveat("net_send", ["b.py:2 sendall()"]),
        )
        assert len(merged) == 1
        assert merged[0]["entries"] == ["a.py:1 sendall()", "b.py:2 sendall()"]
        assert "2 call site(s)" in merged[0]["detail"], (
            "the prose still quotes the pre-merge count"
        )

    def test_an_identical_list_is_not_rebuilt(self) -> None:
        first = _untyped_receiver_caveat("net_send", ["a.py:1 sendall()"])
        merged = _merge_caveat([first], dict(first))
        assert merged == [first]


class TestTheResidualNowClosed:
    """WAS ``TestTheResidualThisDoesNotClose``. Its assertion was that
    ``session.post(...)`` through an untyped receiver "still confirms", carrying
    the note: *a PR that changes it should be closing INV-fibis, not passing
    by*. This is that PR, so the old argument is recorded here rather than
    deleted -- it was right about the mechanism and wrong about the conclusion.

    WHAT IT ARGUED, verbatim in substance: ``post`` is declared for nothing, so
    there is no signal distinguishing ``session.post(...)`` from
    ``items.append(1)``; raising a caveat for every such call is "a caveat on
    essentially every method call in a Python repo, which is the blanket outcome
    PR #251 rejected wearing new clothes".

    WHY THE PREMISE SURVIVES AND THE CONCLUSION DOES NOT. There is indeed no
    signal distinguishing the two -- that is precisely the finding, not an
    objection to it. Where the argument fails is in treating "no signal" as
    grounds for SILENCE. A method call on an object of unknown type is not
    evidence of absence either, and a ``must_not_exist`` verdict is an assertion
    about absence.

    THE THREE THINGS THAT CHANGED THE ANSWER, all measured after that text was
    written:

    1. A corpus hunt over 32,593 non-test files found 90 real scopes whose
       untyped receiver calls a genuine I/O verb outside the 120 catalogued
       method-kind names (``post`` 56, ``upload_file`` 6, ``upload`` 6,
       ``put_item``/``get_item`` 6, ``download_file`` 5, ``execute_command`` 3).
       The boundary-scoped caveat fired on **zero** of them.
    2. Not one of those 90 was protected by anything this module owns. All were
       protected by a COVERAGE GAP -- 68 uncatalogued-module, 13 opaque-launch,
       5 unsupported-language -- every one of which the project intends to
       close. The protection is anti-correlated with the tool improving.
    3. It is demonstrable end-to-end on unmodified real code. polis
       ``deploy-static-assets.py`` uploads to S3 via ``s3_client.upload_file()``
       on an unannotated parameter; with a realistic project overlay that audits
       ``boto3`` but omits ``upload_file``, the shipped CLI returned **rc 0,
       "never sends data over the network: confirmed"**. The documented remedy
       for the other half of INV-fibis (overlays) is what REMOVES the accidental
       protection for this half.

    AND THE BLANKET COMPARISON DOES NOT HOLD. PR #251 rejected WITHHOLDING the
    verdict -- ``inconclusive`` on every boundary of every repo, which tells the
    reader nothing and destroys the exit-code ladder. This QUALIFIES it: the
    verdict still reads clean, rc moves 0 -> 3 (documented as fail-closed), and
    the sentence carries a count and a denominator so the reader can size the
    unknown. The falsifiability control
    (``test_all_typed_receivers_stays_bare_confirmed``) pins that a bare
    ``confirmed`` is still reachable.
    """

    def test_an_uncatalogued_callee_on_an_untyped_receiver_is_disclosed(
        self,
    ) -> None:
        """``session.post(...)`` -- the exact fixture the old assertion pinned as
        confirming silently."""
        coverage = _coverage([_untyped("post")])
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        assert verdict.verdict == "confirmed_with_caveats"
        cav = verdict.caveats[0]
        assert cav["kind"] == CAVEAT_UNKNOWN_RECEIVER_SCOPE
        assert cav["entries"] == ["post"]

    def test_the_annotated_arm_is_still_handled_by_the_existing_gate(
        self,
    ) -> None:
        """The discriminating control the old class shipped, unchanged in
        meaning: type the receiver and the uncatalogued-module gate takes over,
        so this class is not quietly duplicating that gate's job."""
        coverage = _coverage(
            [_typed("python:requests.Session:0-0:post:external_symbol")]
        )
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        assert verdict.verdict == "inconclusive"
        assert "requests.Session" in verdict.details


def _entry_with_chains(n: int):
    from hypergumbo_core.io_boundary import BoundaryMapEntry
    return BoundaryMapEntry(boundary="net_send", chains=[{}] * n)


class TestEndToEndOnTheShippedCommand:
    """The repro at the surface a user reads, including the exit code."""

    def _run(self, tmp_path: Path, capsys, edges: list[dict]) -> tuple[int, str]:
        bmap = {"schema_version": SCHEMA_VERSION,
                "nodes": [{"id": "python:svc.py:9-10:send_unannotated:function",
                           "name": "send_unannotated", "kind": "function",
                           "language": "python", "path": "svc.py",
                           "span": {"start_line": 9, "end_line": 10}}],
                "edges": edges}
        (tmp_path / "survey.json").write_text(json.dumps(bmap))
        (tmp_path / "claims.yaml").write_text(yaml.dump({"claims": [
            {"id": "NET-SEND",
             "text": "This service never sends data over the network.",
             "constraint": {"boundary": "net_send", "must_not_exist": True}},
        ]}))

        class Args:
            pass
        args = Args()
        args.path = str(tmp_path)
        args.input = str(tmp_path / "survey.json")
        args.claims = str(tmp_path / "claims.yaml")
        args.json_output = False
        rc = cmd_verify_claims(args)
        return rc, capsys.readouterr().out

    def test_the_untyped_arm_exits_3_and_discloses(self, tmp_path, capsys) -> None:
        """Was exit 0 with "Verdict: confirmed". Fail-closed is deliberate and
        matches INV-pojib: a gate written ``verify-claims ... || exit 1`` now fails
        where it silently passed."""
        rc, out = self._run(tmp_path, capsys, [_untyped("sendall")])
        assert rc == 3
        assert "sendall" in out

    def test_the_named_receiver_arm_is_seen_not_caveated(self, tmp_path, capsys) -> None:
        """ARM 2 at the CLI. The same call through a receiver the analysis CAN
        type is not qualified — it is FOUND.

        THE FIRST DRAFT OF THIS TEST ASSERTED ``rc != 3`` AND WAS WORTHLESS. Run
        against the shipped python catalogue this arm returns ``violated`` at rc
        1, because ``socket.socket.sendall`` matches and produces a chain — so
        ``rc != 3`` held for a reason that had nothing to do with this change and
        would have held whether or not the fix worked. Measured before being
        written down, which is how the first draft's other guess (rc 2,
        ``inconclusive`` from the uncatalogued-module gate) was caught."""
        rc, out = self._run(
            tmp_path, capsys,
            [_typed("python:socket.socket:0-0:sendall:external_symbol")],
        )
        assert rc == 1
        assert "violated" in out
        assert "untyped_receiver" not in out


class TestUnknownReceiverScope:
    """INV-fibis, the unscoped half: a clean boundary verdict must disclose that
    it is CLOSED-WORLD over the receivers the analysis could type.

    WHY THE BOUNDARY-SCOPED CAVEAT ABOVE IS NOT ENOUGH, measured rather than
    argued. A corpus hunt over 32,593 non-test files found 90 real scopes whose
    untyped receiver calls a genuine I/O verb that is NOT one of the 120
    catalogued method-kind names (``post`` 56, ``upload_file`` 6, ``upload`` 6,
    ``put_item``/``get_item`` 6, ``download_file`` 5, ``execute_command`` 3).
    The boundary-scoped caveat fired on **ZERO** of them, because it matches the
    method NAME against the catalogue -- and a name lookup is exactly what an
    untyped receiver makes meaningless. All 90 were protected instead by a
    COVERAGE GAP (68 uncatalogued-module, 13 opaque-launch, 5
    unsupported-language), every one of which the project intends to close.

    THE DEMONSTRATION, on unmodified real code
    (``polis/deploy/deploy-static-assets.py``, a CLI that uploads to S3)::

        def upload_file(s3_client, file_path, bucket, base_path):   # UNANNOTATED
            s3_client.upload_file(str(file_path), bucket, relative_path, ...)
        s3_client = boto3.client("s3")

    With a realistic project overlay that audits ``boto3`` but omits
    ``upload_file`` from its rows, the shipped CLI returned **rc 0, "never sends
    data over the network: confirmed"**. The documented remedy for the
    third-party half of INV-fibis (overlays) is what REMOVES the accidental
    protection for this half.

    WHAT KEEPS THIS FROM BEING THE REFUSED DOWNGRADE. The 2026-08-11 measurement
    refused an unscoped signal that WITHHELD the verdict (``inconclusive`` on
    every boundary of every repo). This one QUALIFIES it: the verdict still
    reads clean, the exit code moves 0 -> 3, and the sentence carries a COUNT
    AND A DENOMINATOR so a reader can size the unknown instead of being told
    only that one exists. ``test_all_typed_receivers_stays_bare_confirmed``
    is the falsifiability control: this caveat must be capable of NOT firing.
    """

    def test_uncatalogued_method_on_untyped_receiver_is_disclosed(self) -> None:
        """THE HEADLINE. ``upload_file`` is not in the net_send catalogue, so the
        boundary-scoped caveat cannot see it; before this class the verdict was a
        bare ``confirmed`` at rc 0 for a live S3 upload."""
        coverage = _coverage([_untyped("upload_file")])
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        assert verdict.verdict == "confirmed_with_caveats"
        kinds = [c["kind"] for c in verdict.caveats]
        assert CAVEAT_UNKNOWN_RECEIVER_SCOPE in kinds
        assert CAVEAT_UNTYPED_RECEIVER not in kinds

    def test_all_typed_receivers_stays_bare_confirmed(self) -> None:
        """FALSIFIABILITY CONTROL, and the reason this is a caveat rather than a
        blanket. A caveat that is always present is discounted by its reader --
        the failure ``_repo_supplied_sanitizer_caveat`` documents -- so the
        guarantee has to be capable of being CLEAN. Every receiver here is typed,
        so the verdict must stay bare ``confirmed`` at rc 0."""
        coverage = _coverage([
            _typed("python:pathlib.Path:0-0:read_text:external_symbol"),
            _typed("python:app/db.py:1-2:Repo.load:method"),
        ])
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        assert verdict.verdict == "confirmed"
        assert verdict.caveats == []

    def test_scope_caveat_carries_a_count_and_a_denominator(self) -> None:
        """A bare "some receivers were unknown" is unactionable; 1-of-3 and
        1-of-3000 are different verdicts to a reader deciding whether to trust
        this one."""
        coverage = _coverage([
            _untyped("upload_file"),
            _typed("python:pathlib.Path:0-0:read_text:external_symbol"),
            _typed("python:app/db.py:1-2:Repo.load:method"),
        ])
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        cav = next(c for c in verdict.caveats
                   if c["kind"] == CAVEAT_UNKNOWN_RECEIVER_SCOPE)
        assert cav["sites"] == 1
        assert cav["method_calls"] == 3
        assert "1 of 3" in cav["detail"]

    def test_entries_are_the_distinct_method_names(self) -> None:
        """At scale the sites are not the fact, the METHOD NAMES are -- the same
        trade ``_untyped_receiver_caveat`` already makes at its cap, made
        unconditionally here because this population is the larger one."""
        coverage = _coverage([
            _untyped("upload_file"), _untyped("upload_file", line=11),
            _untyped("post", line=12),
        ])
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        cav = next(c for c in verdict.caveats
                   if c["kind"] == CAVEAT_UNKNOWN_RECEIVER_SCOPE)
        assert cav["entries"] == ["post", "upload_file"]
        assert cav["sites"] == 3

    def test_coexists_with_the_boundary_scoped_caveat(self) -> None:
        """``sendall`` IS catalogued for net_send, so both disclosures apply and
        they say different things: one names a checkable site the catalogue
        recognises, the other sizes the unknown around it. Neither subsumes the
        other, so neither is dropped."""
        coverage = _coverage([_untyped("sendall"), _untyped("post", line=11)])
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True),
            BoundaryMap(), coverage,
        )
        kinds = sorted(c["kind"] for c in verdict.caveats)
        assert kinds == sorted([CAVEAT_UNTYPED_RECEIVER,
                                CAVEAT_UNKNOWN_RECEIVER_SCOPE])

    def test_violated_verdict_never_acquires_it(self) -> None:
        """Coverage gates the ALL-CLEAR and nothing else: found evidence is
        trustworthy regardless of what went unadjudicated."""
        bmap = BoundaryMap()
        bmap.entries["net_send"] = _entry_with_chains(1)
        coverage = _coverage([_untyped("upload_file")])
        verdict = verify_claim(
            _claim("net_send", constraint_must_not_exist=True), bmap, coverage,
        )
        assert verdict.verdict == "violated"
        assert verdict.caveats == []

    def test_max_chains_clean_path_also_discloses(self) -> None:
        """"Within limit" asserts no FURTHER chains exist -- the same assertion
        about absence, one chain-count over. Gating one path and leaving the
        other silent is the two-homes-for-one-fact defect."""
        bmap = BoundaryMap()
        bmap.entries["net_send"] = _entry_with_chains(1)
        coverage = _coverage([_untyped("upload_file")])
        verdict = verify_claim(
            _claim("net_send", constraint_max_chains=5), bmap, coverage,
        )
        assert verdict.verdict == "confirmed_with_caveats"
        assert CAVEAT_UNKNOWN_RECEIVER_SCOPE in [c["kind"] for c in verdict.caveats]

    def test_unknown_receiver_scope_counts_the_population(self) -> None:
        """The producer-level function, so a consumer that forgets the verdict
        layer still has one place to ask."""
        sites, total, names = unknown_receiver_scope(
            [_untyped("upload_file"), _untyped("post", line=11),
             _typed("python:pathlib.Path:0-0:read_text:external_symbol")],
            {"python": _py_catalog()},
        )
        assert (sites, total) == (2, 3)
        assert names == ["post", "upload_file"]

    def test_a_language_without_a_catalogue_enters_neither_side_of_the_ratio(
        self,
    ) -> None:
        """THE COMMENSURABILITY GUARANTEE, asserted rather than asserted-in-prose.
        A Go method call in a polyglot repo could not have been adjudicated by
        the python catalogue in either direction, so counting it in the
        DENOMINATOR would silently shrink the reported ratio and make the
        disclosure understate the unknown. It is excluded from both."""
        go_edge = {
            "src": "go:svc.go:1-2:Handler.Serve:method",
            "dst": "go:external:0-0:Write:external_symbol",
            "type": "calls", "line": 2,
            "meta": {"call_construct": "method"},
        }
        sites, total, names = unknown_receiver_scope(
            [go_edge, _untyped("upload_file")], {"python": _py_catalog()},
        )
        assert (sites, total, names) == (1, 1, ["upload_file"])

    def test_a_bare_function_call_has_no_receiver_to_be_unknown(self) -> None:
        """``open(p)`` carries no ``call_construct == "method"``, so it must not
        be reported as a receiver of unknown type -- the same guard the
        boundary-scoped population applies."""
        edge = {"src": "python:svc.py:1-2:f:function",
                "dst": "python:builtins:0-0:open:external_symbol",
                "type": "calls", "line": 2, "meta": {"evidence_type": "ast_call"}}
        sites, total, names = unknown_receiver_scope(
            [edge], {"python": _py_catalog()},
        )
        assert (sites, total, names) == (0, 0, [])
