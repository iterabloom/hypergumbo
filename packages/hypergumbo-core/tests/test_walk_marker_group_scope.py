# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-muhij Finding A: a walk marker must describe the row it is printed on.

WHAT WAS WRONG. ``collapse_unadjudicated_flows`` unions six fields across the
members of a group -- ``source_primitives``, ``sink_primitives``,
``sink_symbols``, ``sink_call_sites``, ``sanitized_by``,
``sanitized_by_user_supplied`` -- so the surviving row names every site it
stands for. ``walk_verdict`` and ``walk_blocked_by`` were NOT among them: the
collapsed row silently inherited ``grp[0]``'s, and there was no field anywhere
saying what the other members reported.

WHY THAT MATTERS RATHER THAN BEING UNTIDY. INV-muhij's remedy is to stop a
``sink_before_source`` row from moving a verdict on its own. Such a rule keyed
on the ROW's scalar would act on a fact true of a minority of what the row
represents. Measured on beads through the production ``verify-claims`` path,
spying on the real collapse function (this tree, 2026-09-02):

    1,870 groups / 6,857 members
      396 groups (21.2%) disagree internally about walk_blocked_by,
          and 2,519 members (36.7%) sit inside one
      171 groups disagree about walk_verdict -- the SIBLING field, inherited
          by the identical mechanism, which is why both move here
      208 groups contain a sink_before_source member
      133 of those 208 (63.9%) are NOT unanimous
      349 of the 1,073 members under them (32.5%) actually carry the marker

So a suppression rule keyed on the scalar would act on a fact true of about
one member in three. The census filed on the item measured 35.7% on beads'
production groups alone; 32.5% over all groups is the same number, re-derived
on today's tree rather than read off the thread.

THE SHAPE OF THE FIX is the one this function already uses five times: a
companion tuple carrying the union, with the scalar kept as the representative
witness. The names take the ``_values`` suffix rather than a plural because
``walk_blocked_by`` does not pluralise, and ``_values`` is the spelling this
project already uses when collapsed sites disagree about a per-site key.

WHAT THIS DOES NOT DO. It does not suppress anything, and no verdict moves.
ADR-0017 section 3a stays confirm-only. This makes the record honest enough
that a suppression rule COULD be written against it -- which the item names as
a precondition, and which was the actual finding of its census.
"""
from hypergumbo_core.taint import (
    TaintFlowFinding,
    collapse_unadjudicated_flows,
)

HANDLER = "py:app.py:1-9:handler:function"


def _make_finding(**kw: object) -> TaintFlowFinding:
    base: dict[str, object] = {
        "taint_label": "untrusted_input",
        "source_symbol": HANDLER,
        "source_primitive": "recv_into",
        "source_module": "socket",
        "sink_symbol": "py:os:0-0:remove:unresolved",
        "sink_primitive": "remove",
        "sink_module": "os",
        "sink_zone": "host_fs",
        "sanitized": False,
        "confidence": "approximate",
        "analysis_method": "structural",
        "path": [HANDLER],
    }
    base.update(kw)
    return TaintFlowFinding(**base)  # type: ignore[arg-type]


class TestTheMarkerBecomesAGroupProperty:
    """The union, which is what a suppression rule would have to read."""

    def test_a_disagreeing_group_reports_both_blockers(self) -> None:
        out = collapse_unadjudicated_flows([
            _make_finding(walk_blocked_by="sink_before_source",
                          sink_primitive="remove"),
            _make_finding(walk_blocked_by="cross_function",
                          sink_primitive="rmdir"),
        ])
        assert len(out) == 1
        assert out[0].walk_blocked_by_values == (
            "cross_function", "sink_before_source")

    def test_a_unanimous_group_reports_one_blocker(self) -> None:
        """This is the shape a suppression rule may act on."""
        out = collapse_unadjudicated_flows([
            _make_finding(walk_blocked_by="sink_before_source",
                          sink_primitive="remove"),
            _make_finding(walk_blocked_by="sink_before_source",
                          sink_primitive="rmdir"),
        ])
        assert out[0].walk_blocked_by_values == ("sink_before_source",)

    def test_an_empty_marker_is_a_value_not_an_absence(self) -> None:
        """A member whose walk RAN must not vanish from the union.

        15 members in the item's own census sat inside a group labelled "the
        walk never ran" with the walk having run. Dropping the empty string
        would make such a group read as unanimous.
        """
        out = collapse_unadjudicated_flows([
            _make_finding(walk_blocked_by="sink_before_source",
                          sink_primitive="remove"),
            _make_finding(walk_blocked_by="", sink_primitive="rmdir"),
        ])
        assert out[0].walk_blocked_by_values == ("", "sink_before_source")

    def test_the_verdict_unions_too(self) -> None:
        """The sibling field, inherited by the identical mechanism."""
        out = collapse_unadjudicated_flows([
            _make_finding(walk_verdict="not_attempted", sink_primitive="remove"),
            _make_finding(walk_verdict="escaped", sink_primitive="rmdir"),
        ])
        assert out[0].walk_verdict_values == ("escaped", "not_attempted")


class TestTheScalarIsUnchanged:
    """Nothing is removed; the witness scalar keeps its documented meaning."""

    def test_the_scalar_still_reports_the_representative(self) -> None:
        out = collapse_unadjudicated_flows([
            _make_finding(walk_blocked_by="sink_before_source",
                          sink_primitive="remove"),
            _make_finding(walk_blocked_by="cross_function",
                          sink_primitive="rmdir"),
        ])
        assert out[0].walk_blocked_by == "sink_before_source"

    def test_no_verdict_moves(self) -> None:
        """Section 3a stays confirm-only. This changes the record, not a verdict."""
        findings = [
            _make_finding(walk_blocked_by="sink_before_source",
                          sink_primitive="remove"),
            _make_finding(walk_blocked_by="cross_function",
                          sink_primitive="rmdir"),
        ]
        out = collapse_unadjudicated_flows(findings)
        assert [f.verdict for f in out] == [findings[0].verdict]


class TestASingletonIsNeverEmpty:
    """Same guarantee the primitive tuples carry: derived, never ``()``."""

    def test_a_hand_built_finding_derives_its_singletons(self) -> None:
        f = _make_finding(walk_verdict="escaped",
                          walk_blocked_by="sink_before_source")
        assert f.walk_verdict_values == ("escaped",)
        assert f.walk_blocked_by_values == ("sink_before_source",)

    def test_the_empty_default_is_still_a_singleton(self) -> None:
        f = _make_finding()
        assert f.walk_verdict_values == ("",)
        assert f.walk_blocked_by_values == ("",)

    def test_an_adjudicated_finding_passes_through_with_its_singleton(
        self,
    ) -> None:
        """``ddg`` bypasses collapse entirely and must still carry the tuple."""
        out = collapse_unadjudicated_flows([
            _make_finding(analysis_method="ddg", walk_verdict="confirmed"),
        ])
        assert out[0].walk_verdict_values == ("confirmed",)


class TestTheRecordReachesAJsonConsumer:
    """A field a consumer cannot read is a field that does not exist."""

    def test_both_unions_serialize(self) -> None:
        out = collapse_unadjudicated_flows([
            _make_finding(walk_blocked_by="sink_before_source",
                          walk_verdict="not_attempted", sink_primitive="remove"),
            _make_finding(walk_blocked_by="cross_function",
                          walk_verdict="escaped", sink_primitive="rmdir"),
        ])
        d = out[0].to_dict()
        assert d["walk_blocked_by_values"] == ["cross_function", "sink_before_source"]
        assert d["walk_verdict_values"] == ["escaped", "not_attempted"]
