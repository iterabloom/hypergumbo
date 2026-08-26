# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-zidur: what the §3a walk RETURNED is a different fact from which analysis ran.

``analysis_method`` answers "which analysis produced this finding". It was also
being asked to answer "and what did the walk conclude", and it cannot: the call
site collapses the walk's three-valued result with ``is True`` and the label is
then chosen on ``fn_has_ddg`` — *did the DDG cover the source function* — so
everything that is not a confirmation lands on one value.

BIGGER THAN FILED. The item names two facts under ``ddg_mixed`` (walk returned
False vs walk returned None). Re-derived at the call site there are THREE, and
the third is the one that matters most for pricing::

    adjudicated = False
    if fn_has_ddg and sink_node == source_fn and source_call_lines:
        ...
        if sink_call_lines and source_tracked and sink_after_source:
            adjudicated = _ddg_taint_reaches(...) is True

Every guard above the walk can fail with ``fn_has_ddg`` still true — a
cross-function flow, a sink with no recorded call line, an untracked source, a
sink lexically before the source — and the finding is stamped ``ddg_mixed``
anyway. So ``ddg_mixed`` today means *the DDG covered the function*, and inside
it live: the walk ran and refuted, the walk ran and escaped, and THE WALK NEVER
RAN. Pricing ADR-0017 §7a as "drop every ``ddg_mixed``" therefore proposes to
remove findings on the authority of a walk that did not execute — which is why
WI-kabif's own pre-registered tripwire ("if a future measurement reports a much
larger number, suspect the measurement") fired at 26.8% against a predicted 18%
and was right.

WHY A NEW FIELD AND NOT A FOURTH ``analysis_method`` VALUE. The precedent the
item cites — splitting ``structural`` out of ``ddg_mixed`` — separated two
different ANALYSES. This separates one analysis's RESULTS, which is a different
axis, and folding a verdict into a method name is the same one-name-two-facts
shape being fixed. ``analysis_method``'s published vocabulary is unchanged, so
docs/measurements/0006, docs/VERIFY-CLAIMS-SCOPE.md and any consumer reading
``ddg_mixed`` keep meaning exactly what they meant.

CONFIRM-ONLY IS UNCHANGED. ``unconfirmed`` and ``escaped`` are recorded, not
acted on: §3a still never removes a flow, so no verdict moves because of this.
What moves is that the addressable domain can be MEASURED instead of
upper-bounded.
"""

from hypergumbo_core.taint import (
    WALK_VERDICTS,
    WALK_VERDICT_CONFIRMED,
    WALK_VERDICT_ESCAPED,
    WALK_VERDICT_NOT_ATTEMPTED,
    WALK_VERDICT_UNAVAILABLE,
    WALK_VERDICT_UNCONFIRMED,
    TaintFlowFinding,
    walk_verdict_for,
)


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_the_five_verdicts_are_declared_in_one_place():
    assert WALK_VERDICTS == frozenset({
        WALK_VERDICT_CONFIRMED,
        WALK_VERDICT_UNCONFIRMED,
        WALK_VERDICT_ESCAPED,
        WALK_VERDICT_NOT_ATTEMPTED,
        WALK_VERDICT_UNAVAILABLE,
    })


def test_the_field_defaults_to_empty_for_a_map_that_predates_it():
    finding = TaintFlowFinding(
        taint_label="host_secret", source_symbol="s", source_primitive="p",
        sink_symbol="k", sink_primitive="q", sink_zone="host_fs",
        sanitized=False, confidence="approximate", analysis_method="structural",
    )
    assert finding.walk_verdict == ""


# ---------------------------------------------------------------------------
# Mapping the walk's three-valued return
# ---------------------------------------------------------------------------


def test_a_dependence_found_is_confirmed():
    assert walk_verdict_for(True, ran=True, covered=True) == \
        WALK_VERDICT_CONFIRMED


def test_the_walk_exhausting_without_a_dependence_is_unconfirmed():
    """False: every route accounted for, none of them carried the value."""
    assert walk_verdict_for(False, ran=True, covered=True) == \
        WALK_VERDICT_UNCONFIRMED


def test_the_walk_losing_track_is_escaped_not_unconfirmed():
    """None: the value left tracked ground. THE distinction this item exists for.

    Removal on ``unconfirmed`` is removal on knowledge; removal on ``escaped``
    is removal on ignorance, and INV-busis measures escapes as common (86.5% of
    production escape sites are not a call statement node).
    """
    assert walk_verdict_for(None, ran=True, covered=True) == \
        WALK_VERDICT_ESCAPED


def test_a_walk_that_never_ran_says_so():
    """The third fact the filing did not name — and the largest, in practice."""
    assert walk_verdict_for(None, ran=False, covered=True) == \
        WALK_VERDICT_NOT_ATTEMPTED


def test_no_reaching_def_data_is_unavailable():
    assert walk_verdict_for(None, ran=False, covered=False) == \
        WALK_VERDICT_UNAVAILABLE


def test_coverage_cannot_be_claimed_without_the_data():
    """``ran`` implies ``covered``; the opposite pairing is not a state."""
    assert walk_verdict_for(True, ran=True, covered=False) == \
        WALK_VERDICT_CONFIRMED


# ---------------------------------------------------------------------------
# The relationship to analysis_method that must NOT change
# ---------------------------------------------------------------------------


def test_every_non_confirming_verdict_still_reads_as_approximate():
    """No published verdict moves: §3a stays confirm-only."""
    from hypergumbo_core.taint import method_for_walk_verdict
    assert method_for_walk_verdict(WALK_VERDICT_CONFIRMED) == "ddg"
    for verdict in (WALK_VERDICT_UNCONFIRMED, WALK_VERDICT_ESCAPED,
                    WALK_VERDICT_NOT_ATTEMPTED):
        assert method_for_walk_verdict(verdict) == "ddg_mixed"
    assert method_for_walk_verdict(WALK_VERDICT_UNAVAILABLE) == "structural"


def test_the_published_method_vocabulary_is_unchanged():
    """docs/measurements/0006 and VERIFY-CLAIMS-SCOPE.md read these names."""
    from hypergumbo_core.taint import method_for_walk_verdict
    assert {method_for_walk_verdict(v) for v in WALK_VERDICTS} == {
        "ddg", "ddg_mixed", "structural",
    }


# ---------------------------------------------------------------------------
# Why the walk did not run
# ---------------------------------------------------------------------------


def test_the_blockers_are_declared_in_one_place():
    from hypergumbo_core.taint import (
        WALK_BLOCKED_CROSS_FUNCTION,
        WALK_BLOCKED_NO_SINK_CALL_LINE,
        WALK_BLOCKED_NO_SOURCE_CALL_LINE,
        WALK_BLOCKED_SINK_BEFORE_SOURCE,
        WALK_BLOCKED_SOURCE_NOT_TRACKED,
        WALK_BLOCKERS,
    )
    assert WALK_BLOCKERS == frozenset({
        WALK_BLOCKED_CROSS_FUNCTION,
        WALK_BLOCKED_NO_SOURCE_CALL_LINE,
        WALK_BLOCKED_NO_SINK_CALL_LINE,
        WALK_BLOCKED_SOURCE_NOT_TRACKED,
        WALK_BLOCKED_SINK_BEFORE_SOURCE,
    })


def test_the_blocker_field_is_empty_unless_the_walk_was_not_attempted():
    """The question does not arise for a verdict the walk actually produced."""
    finding = TaintFlowFinding(
        taint_label="host_secret", source_symbol="s", source_primitive="p",
        sink_symbol="k", sink_primitive="q", sink_zone="host_fs",
        sanitized=False, confidence="approximate", analysis_method="ddg",
        walk_verdict=WALK_VERDICT_CONFIRMED,
    )
    assert finding.walk_blocked_by == ""
