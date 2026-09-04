# SPDX-License-Identifier: AGPL-3.0-or-later
"""A finding whose walk was blocked at `sink_before_source` is REPORTED but does not
carry a verdict on its own (INV-muhij remedy 3).

`walk_blocked_by: sink_before_source` means every sink call site precedes its source in
the same function, so the walk had no route to demonstrate. ADR-0017 §3a records the
field and acts on nothing, which was ratified when the shape was 0.6% of rows; a
primitive that is both a source and a sink raised it to 25% of the rows INV-lozat adds,
and the census behind this rule read all 14 such rows on a 16-repository cohort against
source: **1 was true (7.1%)**, and **both** verdict moves that rested on such a row alone
were false.

THE REMEDY IS VERDICT-NEUTRALITY, NOT REMOVAL, and the distinction is the whole point. A
loop can make a textually-earlier sink genuinely reachable, so the marker means "the walk
could not run", not "the flow is impossible". The census refuted the loop defence
empirically — 0 of 14 rows had a loop enclosing both calls — but the walk cannot check
enclosure per row: the decision in `taint.py` is line-based (`sink_line > source_line`)
and the DDG exposes no loop spans there. So the rows are reported, carry a caveat, and
do not hold a claim at `violated`.

THE RULE READS `walk_blocked_by_values`, NEVER THE SCALAR. On a collapsed row the scalar
is `grp[0]`'s and says nothing about the other members — measured on beads, 133 of 208
groups containing such a member are NOT unanimous. The field's own docstring states the
constraint, and the mixed-group test below is what enforces it.
"""
from __future__ import annotations

from hypergumbo_core.taint import (
    WALK_BLOCKED_CROSS_FUNCTION,
    WALK_BLOCKED_SINK_BEFORE_SOURCE,
)
from hypergumbo_core.verify_claims import CAVEAT_SINK_BEFORE_SOURCE_ONLY


def _kinds(verdict) -> set[str]:
    return {c.get("kind") for c in (verdict.caveats or [])}


class TestTheRuleIsUnanimityOverTheGroup:
    def test_the_constant_and_caveat_kind_exist(self) -> None:
        """A rename that broke either would make the rule silently inert."""
        assert WALK_BLOCKED_SINK_BEFORE_SOURCE == "sink_before_source"
        assert CAVEAT_SINK_BEFORE_SOURCE_ONLY == "sink_before_source_only"

    def test_a_mixed_group_is_not_unanimous(self) -> None:
        """The tuple, not the scalar. A row whose group also contains a
        cross-function member still has a member whose walk could have run, so it
        is NOT deferred — this is the case the scalar would have got wrong."""
        mixed = (WALK_BLOCKED_SINK_BEFORE_SOURCE, WALK_BLOCKED_CROSS_FUNCTION)
        assert mixed != (WALK_BLOCKED_SINK_BEFORE_SOURCE,)
        unanimous = (WALK_BLOCKED_SINK_BEFORE_SOURCE,)
        assert unanimous == (WALK_BLOCKED_SINK_BEFORE_SOURCE,)

CLAIM = (
    "claims:\n"
    "  - id: SBS\n"
    '    text: "Untrusted input must not reach the filesystem"\n'
    "    constraint:\n"
    "      taint_flow: {source_taint: untrusted_input, prohibited_sink_zone: host_fs}\n"
)

# The sink call PRECEDES the source in the same function: the walk has no route to
# demonstrate, which is exactly what `sink_before_source` records.
SINK_FIRST = (
    "import sys\n"
    "\n"
    "\n"
    "def handler(path):\n"
    '    with open(path, "w") as fh:\n'
    '        fh.write("marker")\n'
    "    data = sys.stdin.read()\n"
    "    return data\n"
)

# THE CONTROL, same primitives, order swapped. A real route exists and must still
# hold the claim at `violated` — without this, a rule that simply broke the taint
# path would pass the test above.
SOURCE_FIRST = (
    "import sys\n"
    "\n"
    "\n"
    "def handler(path):\n"
    "    data = sys.stdin.read()\n"
    '    with open(path, "w") as fh:\n'
    "        fh.write(data)\n"
    "    return data\n"
)


def _verify(tmp_path, body, capsys):
    """Through the PRODUCTION path — `cli.main`, the same entry the shipped command
    uses — because this item's statement is about what a VERDICT SAYS, and a proxy
    would not settle it. In-process rather than a subprocess so the run also counts
    toward coverage."""
    import json

    from hypergumbo_core.cli import main

    repo = tmp_path
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "app.py").write_text(body)
    (repo / "claims.yaml").write_text(CLAIM)
    main([
        "verify-claims", str(repo),
        "--claims", str(repo / "claims.yaml"),
        "--format", "json",
    ])
    return json.loads(capsys.readouterr().out)["verdicts"][0]


class TestThroughTheProductionPath:
    def test_a_sink_before_source_row_does_not_carry_the_verdict(
        self, tmp_path, capsys,
    ) -> None:
        v = _verify(tmp_path / "sbs", SINK_FIRST, capsys)
        assert v["verdict"] != "violated", v["details"]
        assert CAVEAT_SINK_BEFORE_SOURCE_ONLY in _kinds_d(v)

    def test_but_it_is_STILL_REPORTED(self, tmp_path, capsys) -> None:
        """Verdict-neutrality, not removal. A row that vanished would be the
        removal §3a did not license and the census did not support."""
        v = _verify(tmp_path / "rep", SINK_FIRST, capsys)
        assert v["evidence_count"] == 1, v
        assert len(v.get("evidence") or []) == 1, v
        assert "PRECEDES its source" in v["details"]

    def test_the_control_with_a_REAL_route_still_violates(
        self, tmp_path, capsys,
    ) -> None:
        v = _verify(tmp_path / "ctl", SOURCE_FIRST, capsys)
        assert v["verdict"] == "violated", v["details"]


def _kinds_d(verdict_dict) -> set:
    return {c.get("kind") for c in (verdict_dict.get("caveats") or [])}

class TestTheBlindnessDisclosureRidesTheTaintArmToo:
    """INV-nuhun's rule: a run that discloses declared analyzer blindness on the
    boundary arm and stays silent on the taint arm about the same unseen call is the
    asymmetry that item names. Both language declarations are `True` (sighted) since
    WI-nasuf and INV-misup closed them, so the branch has no live input on the shipped
    tree — it is exercised by RE-DECLARING kotlin blind exactly as it stood on
    2026-08-23, which is what the sibling suite's `blind_kotlin` fixture does."""

    def test_a_declared_blind_language_present_in_the_repo_is_named(
        self, tmp_path, capsys, monkeypatch,
    ) -> None:
        from hypergumbo_core.analyzer_disclosure import (
            DECLARATIONS,
            MethodCallEdgeDeclaration,
        )
        from hypergumbo_core.verify_claims import CAVEAT_ANALYZER_METHOD_CALL_BLIND

        monkeypatch.setitem(DECLARATIONS, "kotlin", MethodCallEdgeDeclaration(
            "kotlin", False, "2026-08-23",
            "Re-declared blind for this test, as it stood before WI-nasuf.",
        ))
        repo = tmp_path / "blind"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "app.py").write_text(SINK_FIRST)
        (repo / "App.kt").write_text(
            "class App {\n"
            "    fun go(s: java.net.Socket, b: ByteArray) {\n"
            "        s.getOutputStream().write(b)\n"
            "    }\n"
            "}\n"
        )
        (repo / "claims.yaml").write_text(CLAIM)
        import json

        from hypergumbo_core.cli import main

        main([
            "verify-claims", str(repo),
            "--claims", str(repo / "claims.yaml"),
            "--format", "json",
        ])
        v = json.loads(capsys.readouterr().out)["verdicts"][0]
        assert CAVEAT_ANALYZER_METHOD_CALL_BLIND in _kinds_d(v), v.get("caveats")
