# SPDX-License-Identifier: AGPL-3.0-or-later
"""A clean TAINT verdict must disclose the sink calls whose receiver it could not type.

INV-nuhun: INV-fibis's defect in the other arm. Reproduced on the shipped CLI at dev
aa7e878572 -- the tree on which INV-fibis's fix was already merged -- with a control
firing in the SAME run:

    def control_fswrite(p):
        secret = os.environ["API_KEY"]
        with open(p, "w") as fh:
            fh.write(secret)

    def leak_unannotated(sock):
        secret = os.environ["API_KEY"]
        return sock.sendall(secret.encode())

    $ hypergumbo verify-claims . --claims docs/example-claims/generic-taint-claims.yaml
      x [host-secret-no-host-fs]  violated              <- the run WORKED
      v [host-secret-no-network]  confirmed             <- FALSE

WHAT MAKES THIS ITS OWN ITEM RATHER THAN A RESIDUE OF INV-fibis: the two arms now
DISAGREE ABOUT ONE CALL, in ONE invocation. Running the boundary and taint claims
together over that same file printed these two verdicts adjacent:

    ! [NET-SEND] confirmed_with_caveats
      CAVEAT (untyped_receiver): ... At 1 call site(s) -- leak.py:12 sendall() -- a
      method the net_send catalogue declares is called on a receiver whose type could
      not be determined ...
    v [host-secret-no-network] confirmed
      No unsanitized host_secret data reaches network zone.

The tool says it could not determine whether ``leak.py:12 sendall()`` reaches the
network, and then certifies that nothing reaches the network zone -- and the second
carries a tick where the first carries a warning, so the SILENT arm reads as the more
confident one. A reader who sees a disclosure on one verdict is entitled to infer the
other got equal scrutiny.

WHY THE FIX IS NOT "WIDEN RECALL", pinned here because the item's own ``root_cause``
field invites it. ``socket.socket.sendall`` is a METHOD-kind catalogue row, and the
untyped receiver supplies no module, so the sink lookup is refused TWICE over: first
by ``gate_named_entry``'s ``call_construct == "method"`` arm, then -- even with that
removed -- by its ``non_method`` filter, because the bucket for ``sendall`` holds only
method-kind rows. BOTH refusals are the deliberate closure of INV-tapat and INV-maluk
(satisfied P2, and INV-maluk was REOPENED precisely because taint.py's sink matcher
was the third consumer that did not honour it, a ~5541-violation false-positive
cascade). Matching a method-kind sink on a bare short name is the exact "unfiltered
short-name fallback" INV-maluk's statement forbids. So the flow CANNOT be built by
relaxing the gate; it can only be built by TYPING THE RECEIVER (INV-fibis's recall
half), and where the receiver cannot be typed the verdict must say so.
``test_a_typed_receiver_is_matched_not_caveated`` is the positive control that the
gate opens the moment real evidence arrives.
"""

import json
from pathlib import Path

import yaml

from hypergumbo_core.cli import cmd_verify_claims
from hypergumbo_core.io_boundary import IoBoundaryCatalog, IoPrimitive
from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.taint import TaintSink
from hypergumbo_core.verify_claims import (
    CAVEAT_UNKNOWN_RECEIVER_SCOPE,
    CAVEAT_UNTYPED_RECEIVER,
    BoundaryCoverage,
    Claim,
    TaintFlowConstraint,
    compute_boundary_coverage,
    untyped_receiver_sink_zones,
    verify_claims,
    verify_taint_claim,
)


def _py_catalog() -> IoBoundaryCatalog:
    """Same shape as the boundary arm's fixture, so the two arms are comparable."""
    return IoBoundaryCatalog(
        language="python",
        primitives=[
            IoPrimitive(boundary="net_send", module="socket.socket",
                        name="sendall", kind="method"),
            IoPrimitive(boundary="db_read", module="sqlite3.Cursor",
                        name="get", kind="method"),
            IoPrimitive(boundary="fs_write", module="builtins",
                        name="open", kind="function"),
        ],
        stdlib_modules=frozenset({"socket", "sqlite3", "builtins"}),
        module_completeness={"socket": "2026-08-12", "sqlite3": "2026-08-12",
                             "builtins": "2026-08-12"},
    )


def _sinks() -> dict[str, list[TaintSink]]:
    """The taint sink catalogue, asked in its own vocabulary.

    ZONES, NOT BOUNDARIES, AND ASKED OF THE TAINT CATALOGUE RATHER THAN MAPPED FROM
    THE IO ONE. Every shipped python sink today is auto-derived from an io primitive
    through ``AUTO_SINK_ZONE_MAP``, so deriving the zone map from boundaries would
    give an identical answer on the shipped catalogue -- and would silently
    under-disclose the moment a repository declares its own sink via
    ``--taint-sinks``, which is a sink the boundary catalogue has never heard of.
    ``test_a_repo_declared_sink_is_disclosed_too`` is that case.
    """
    return {"python": [
        TaintSink(zone="network", trust_level="untrusted",
                  module="socket.socket", name="sendall", kind="method"),
        TaintSink(zone="database", trust_level="untrusted",
                  module="sqlite3.Cursor", name="get", kind="method"),
        TaintSink(zone="host_fs", trust_level="untrusted",
                  module="builtins", name="open", kind="function"),
    ]}


def _untyped(name: str, *, line: int = 12,
             src: str = "python:leak.py:11-13:leak_unannotated:function") -> dict:
    return {
        "src": src,
        "dst": f"python:external:0-0:{name}:external_symbol",
        "type": "calls",
        "line": line,
        "meta": {"call_construct": "method", "evidence_type": "ast_call",
                 "evidence_lang": "python"},
    }


def _typed(dst: str, src: str = "python:leak.py:11-13:h:function") -> dict:
    return {"src": src, "dst": dst, "type": "calls", "line": 12,
            "meta": {"call_construct": "method"}}


def _coverage(edges: list[dict],
              sinks: dict[str, list[TaintSink]] | None = None) -> BoundaryCoverage:
    cov = compute_boundary_coverage(edges, {"python"}, {"python": _py_catalog()})
    cov.untyped_receiver_zones = untyped_receiver_sink_zones(
        edges, {"python": _py_catalog()},
        _sinks() if sinks is None else sinks,
    )
    return cov


def _claim(zone: str = "network", taint: str = "host_secret") -> Claim:
    return Claim(id="C", text="t", constraint_taint_flow=TaintFlowConstraint(
        source_taint=taint, prohibited_sink_zone=zone,
    ))


class TestTheRepro:
    """The filed repro at the unit the CLI calls."""

    def test_a_clean_taint_verdict_discloses_the_untyped_sink_receiver(self) -> None:
        cov = _coverage([_untyped("sendall")])
        v = verify_taint_claim(_claim(), [], coverage=cov)
        assert v.verdict == "confirmed_with_caveats"
        kinds = {c["kind"] for c in v.caveats}
        assert CAVEAT_UNTYPED_RECEIVER in kinds

    def test_the_caveat_names_a_site_a_reader_can_open(self) -> None:
        cov = _coverage([_untyped("sendall")])
        v = verify_taint_claim(_claim(), [], coverage=cov)
        cav = next(c for c in v.caveats if c["kind"] == CAVEAT_UNTYPED_RECEIVER)
        assert cav["entries"] == ["leak.py:12 sendall()"]

    def test_the_sentence_says_a_flow_could_not_be_ruled_out(self) -> None:
        """The taint arm's CONSEQUENCE differs from the boundary arm's. "Whether
        those calls perform this I/O was never decided" is the boundary question;
        what a taint reader loses is the FLOW."""
        cov = _coverage([_untyped("sendall")])
        v = verify_taint_claim(_claim(), [], coverage=cov)
        cav = next(c for c in v.caveats if c["kind"] == CAVEAT_UNTYPED_RECEIVER)
        assert "flow" in cav["detail"]
        assert "network sink catalogue" in cav["detail"]


class TestZoneScoping:
    """The scoping that made the boundary arm shippable, in the taint vocabulary."""

    def test_a_sink_for_another_zone_cannot_touch_this_verdict(self) -> None:
        """``.get`` is catalogued -- for DATABASE. The 2026-08-11 measurement that
        refused the unscoped downgrade turned on exactly this: ``get`` is the most
        common method name in Python, and an unrelated dict ``.get`` must not
        qualify a NETWORK verdict."""
        cov = _coverage([_untyped("get")])
        v = verify_taint_claim(_claim(zone="network"), [], coverage=cov)
        # The verdict IS qualified — by the UNSCOPED closed-world caveat, which
        # is claim-independent by construction. Asserting a bare ``confirmed``
        # here would be asserting that sibling away and would pass for the wrong
        # reason the moment it was removed. What must hold is that the ZONE-scoped
        # channel stayed silent about a sink belonging to another zone.
        assert [c["kind"] for c in v.caveats] == [CAVEAT_UNKNOWN_RECEIVER_SCOPE]

    def test_the_same_call_does_qualify_the_zone_it_is_catalogued_for(self) -> None:
        """The other half of the control: the scoping is a FILTER, not a mute."""
        cov = _coverage([_untyped("get")])
        v = verify_taint_claim(_claim(zone="database"), [], coverage=cov)
        assert v.verdict == "confirmed_with_caveats"

    def test_a_repo_declared_sink_is_disclosed_too(self) -> None:
        """A sink the IO boundary catalogue has never heard of. This is the case
        that decides the implementation: ask the TAINT catalogue, do not map from
        boundaries through AUTO_SINK_ZONE_MAP."""
        sinks = {"python": [TaintSink(
            zone="network", trust_level="untrusted",
            module="mylib.Client", name="ship_it", kind="method",
        )]}
        cov = _coverage([_untyped("ship_it")], sinks=sinks)
        v = verify_taint_claim(_claim(), [], coverage=cov)
        cav = next(c for c in v.caveats if c["kind"] == CAVEAT_UNTYPED_RECEIVER)
        assert cav["entries"] == ["leak.py:12 ship_it()"]


class TestItDoesNotFireWhereThereIsNothingToDisclose:

    def test_a_typed_receiver_is_matched_not_caveated(self) -> None:
        """THE POSITIVE CONTROL FOR THE WHOLE DIAGNOSIS. The gate is not broken --
        it is waiting for evidence. Supply the receiver's module and the same sink
        matches, which is why the fix is receiver typing and not a wider gate."""
        cov = _coverage([_typed("python:socket.socket:0-0:sendall:external_symbol")])
        assert cov.untyped_receiver_zones == {}

    def test_an_uncatalogued_method_name_raises_no_zone_caveat(self) -> None:
        cov = _coverage([_untyped("frobnicate")])
        v = verify_taint_claim(_claim(), [], coverage=cov)
        assert not [c for c in v.caveats if c["kind"] == CAVEAT_UNTYPED_RECEIVER]

    def test_a_function_kind_sink_raises_nothing(self) -> None:
        """``open()`` has no receiver, so "I could not type the receiver" is not a
        true sentence about it -- the same method-kind-only rule the boundary arm
        enforces, asked of the sink catalogue's ``kind``."""
        cov = _coverage([_untyped("open")])
        v = verify_taint_claim(_claim(zone="host_fs"), [], coverage=cov)
        assert not [c for c in v.caveats if c["kind"] == CAVEAT_UNTYPED_RECEIVER]

    def test_a_producer_stamped_launch_raises_nothing(self) -> None:
        """A launch is an EXAMINED call, disclosed by name through
        CAVEAT_OPAQUE_BOUNDARY. Saying "and its receiver was untyped" about the same
        edge is the contradictory second statement that made rc 3 unreachable once
        already -- excluded in the same position the boundary arm excludes it."""
        e = _untyped("sendall")
        e["meta"]["io_boundary"] = "command_launch"
        cov = _coverage([e])
        assert cov.untyped_receiver_zones == {}


class TestItDoesNotDisturbTheOtherVerdicts:

    def test_a_violated_verdict_is_untouched(self) -> None:
        """Finding something is trustworthy regardless of coverage; it is the CLEAN
        result that depends on having looked."""
        from hypergumbo_core.taint import TaintFlowFinding
        finding = TaintFlowFinding(
            taint_label="host_secret",
            source_symbol="python:leak.py:11-13:leak_unannotated:function",
            source_primitive="os.environ",
            sink_symbol="python:external:0-0:sendall:external_symbol",
            sink_primitive="socket.socket.sendall",
            sink_zone="network",
            sanitized=False,
            confidence="precise",
            analysis_method="ddg",
        )
        cov = _coverage([_untyped("sendall")])
        v = verify_taint_claim(_claim(), [finding], coverage=cov)
        assert v.verdict == "violated"
        assert v.caveats == []

    def test_no_coverage_object_keeps_the_previous_behaviour(self) -> None:
        """``coverage`` is optional, and its absence must not invent a disclosure."""
        v = verify_taint_claim(_claim(), [])
        assert v.verdict == "confirmed"
        assert v.caveats == []


class TestSymmetryWithTheBoundaryArm:
    """INV-nuhun is an ASYMMETRY item, so the parity is the property under test."""

    def test_the_unscoped_scope_caveat_reaches_taint_claims_too(self) -> None:
        """B's closed-world disclosure is arm-independent: a clean taint verdict is
        just as closed-world over typeable receivers as a clean boundary one."""
        cov = _coverage([_untyped("frobnicate")])
        v = verify_taint_claim(_claim(), [], coverage=cov)
        assert CAVEAT_UNKNOWN_RECEIVER_SCOPE in {c["kind"] for c in v.caveats}

    def test_one_run_cannot_disclose_on_one_arm_and_stay_silent_on_the_other(
        self,
    ) -> None:
        """THE ITEM, AS A TEST. Both claims over the same edge; neither may be a
        bare ``confirmed`` while the other discloses."""
        from hypergumbo_core.io_boundary import BoundaryMap
        edges = [_untyped("sendall")]
        cov = _coverage(edges)
        verdicts = verify_claims(
            [Claim(id="B", text="t", constraint_boundary="net_send",
                   constraint_must_not_exist=True),
             _claim()],
            BoundaryMap(entries={}), taint_findings=[], coverage=cov,
        )
        assert [v.verdict for v in verdicts] == [
            "confirmed_with_caveats", "confirmed_with_caveats",
        ]
        for v in verdicts:
            assert "sendall" in " ".join(c["detail"] for c in v.caveats)

    def test_both_arms_report_the_same_denominator(self) -> None:
        """One invocation must not print two different "N of M method call sites"
        figures for one repository."""
        from hypergumbo_core.io_boundary import BoundaryMap
        cov = _coverage([_untyped("sendall"), _untyped("frobnicate", line=13)])
        verdicts = verify_claims(
            [Claim(id="B", text="t", constraint_boundary="subprocess",
                   constraint_must_not_exist=True),
             _claim()],
            BoundaryMap(entries={}), taint_findings=[], coverage=cov,
        )
        scoped = [
            next(c for c in v.caveats if c["kind"] == CAVEAT_UNKNOWN_RECEIVER_SCOPE)
            for v in verdicts
        ]
        assert scoped[0]["detail"] == scoped[1]["detail"]


class TestEndToEndOnTheShippedCommand:
    """The repro at the surface a user reads, including the exit code."""

    def _run(self, tmp_path: Path, capsys, edges: list[dict]) -> tuple[int, str]:
        bmap = {"schema_version": SCHEMA_VERSION,
                "nodes": [{"id": "python:leak.py:11-13:leak_unannotated:function",
                           "name": "leak_unannotated", "kind": "function",
                           "language": "python", "path": "leak.py",
                           "span": {"start_line": 11, "end_line": 13}}],
                "edges": edges}
        (tmp_path / "survey.json").write_text(json.dumps(bmap))
        (tmp_path / "claims.yaml").write_text(yaml.dump({"claims": [
            {"id": "host-secret-no-network",
             "text": "Credentials are never sent over the network.",
             "constraint": {"taint_flow": {
                 "source_taint": "host_secret",
                 "prohibited_sink_zone": "network"}}},
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

    def test_the_untyped_arm_exits_3_and_names_the_method(
        self, tmp_path, capsys,
    ) -> None:
        """Was exit 0, "Verdict: confirmed", "No unsanitized host_secret data
        reaches network zone." -- about a call the tool had already told the reader,
        one verdict earlier in the same output, that it could not adjudicate."""
        rc, out = self._run(tmp_path, capsys, [_untyped("sendall")])
        assert rc == 3
        assert "sendall" in out
        assert CAVEAT_UNTYPED_RECEIVER in out

    def test_the_typed_arm_is_not_caveated(self, tmp_path, capsys) -> None:
        rc, out = self._run(
            tmp_path, capsys,
            [_typed("python:socket.socket:0-0:sendall:external_symbol")],
        )
        assert CAVEAT_UNTYPED_RECEIVER not in out
