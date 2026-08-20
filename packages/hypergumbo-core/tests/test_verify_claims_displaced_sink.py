# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-faput: a repo-supplied catalogue row that REPLACES a shipped one.

User sources/sinks merge through ``_merge_with_user_override``, where a
matching ``(module, name, kind)`` does not ADD to the catalogue — it FILTERS
OUT the shipped row. The shipped sink therefore leaves the catalogue *before*
propagation runs, so no flow is constructed, nothing is sanitized, and the
verdict reads ``confirmed`` with ``caveats: []``.

Measured on the shipped CLI, a repo whose only statement is
``os.remove(os.environ["API_KEY"])`` against "host secrets must not reach the
host filesystem":

    baseline                                        rc 1  violated
    + --taint-sinks re-declaring os.remove
      into a dev_zone / trusted row                 rc 0  confirmed   caveats []
    + the same, after this fix                      rc 3  confirmed_with_caveats

STRICTLY STRONGER THAN THE TWO DISCLOSURE GAPS ALREADY CLOSED. An overlay
GRANTS coverage. A user sanitizer DELETES a finding already made, and INV-pojib
attributes it on the flow. An override PREVENTS THE FINDING FROM EXISTING —
the only one of the three that can leave no trace on any per-flow record.
INV-pojib's finding-level attribution structurally cannot see it: there is no
finding to attribute, and ``caveats`` is *correctly* empty.

The run-level disclosure was already present and is not sufficient:
``catalog_provenance`` names the file and sets ``user_supplied: true``, so the
run says "a user catalogue was used" — not "and it removed the sink that would
have caught this claim". The verdict, which is the surface a CI gate branches
on, was byte-identical to an honest confirm.
"""

from __future__ import annotations

from hypergumbo_core.taint import (
    TaintFlowFinding,
    TaintSink,
    TaintSource,
    _merge_with_user_override,
)
from hypergumbo_core.verify_claims import (
    CAVEAT_DISPLACED_SHIPPED_ENTRY,
    Claim,
    TaintFlowConstraint,
    verify_taint_claim,
)


def _sink(module: str, name: str, zone: str, kind: str = "function"):
    return TaintSink(zone=zone, trust_level="untrusted", module=module,
                     name=name, kind=kind)


def _claim() -> Claim:
    return Claim(
        id="SC-faput",
        text="host secrets must not reach the host filesystem",
        constraint_taint_flow=TaintFlowConstraint(
            # No credential here: the generic-api-key rule keys on "secret"
            # next to '=' and reports "prohibited_sink_zone=" as the secret.
            source_taint="host_secret", prohibited_sink_zone="host_fs",  # gitleaks:allow
        ),
    )


class TestTheMergeReportsWhatItDrops:
    """The displacement set was always computed and thrown away.

    That is the whole defect. Nothing downstream can reconstruct it, because
    by the time anything downstream runs the shipped row is simply absent —
    indistinguishable from a row the tool never shipped. The moment of the
    merge is the only moment the fact exists.
    """

    def test_a_matching_user_entry_is_reported_as_displacing(self) -> None:
        auto = {"python": [_sink("os", "remove", "host_fs")]}
        user = {"python": [_sink("os", "remove", "dev_zone")]}
        merged, displaced = _merge_with_user_override(auto, user)
        assert [(e.module, e.name, e.zone) for e in merged["python"]] == [
            ("os", "remove", "dev_zone"),
        ], "the user row wins, as documented"
        assert [(e.module, e.name, e.zone) for e in displaced["python"]] == [
            ("os", "remove", "host_fs"),
        ], "and the shipped row it replaced is now reported"

    def test_an_additive_user_entry_displaces_nothing(self) -> None:
        auto = {"python": [_sink("os", "remove", "host_fs")]}
        user = {"python": [_sink("socket", "send", "network", "method")]}
        merged, displaced = _merge_with_user_override(auto, user)
        assert len(merged["python"]) == 2
        assert displaced == {}, (
            "adding a row removes nothing and must not be reported as a "
            "displacement — otherwise every customised catalogue caveats"
        )

    def test_a_differing_kind_is_a_different_entry(self) -> None:
        """The override key is (module, name, kind), so a method row does not
        displace a function row of the same name."""
        auto = {"python": [_sink("os", "remove", "host_fs", "function")]}
        user = {"python": [_sink("os", "remove", "dev_zone", "method")]}
        _, displaced = _merge_with_user_override(auto, user)
        assert displaced == {}


class TestTheVerdictCarriesTheDisplacement:
    def test_a_displaced_sink_in_the_claims_zone_downgrades(self) -> None:
        """The filed defect: this was `confirmed` with `caveats: []`."""
        verdict = verify_taint_claim(
            _claim(), [],
            displaced_sinks={"python": [_sink("os", "remove", "host_fs")]},
        )
        assert verdict.verdict == "confirmed_with_caveats"
        kinds = [c["kind"] for c in verdict.caveats]
        assert CAVEAT_DISPLACED_SHIPPED_ENTRY in kinds
        entry = next(c for c in verdict.caveats
                     if c["kind"] == CAVEAT_DISPLACED_SHIPPED_ENTRY)
        assert entry["entries"] == ["python:os.remove [sink/host_fs]"]

    def test_a_displaced_source_with_the_claims_label_downgrades(self) -> None:
        src = TaintSource(taint_label="host_secret", module="os",
                          name="environ", kind="attribute")
        verdict = verify_taint_claim(
            _claim(), [], displaced_sources={"python": [src]},
        )
        assert verdict.verdict == "confirmed_with_caveats"

    def test_a_displaced_sink_in_ANOTHER_zone_stays_plain(self) -> None:
        """THE DISCRIMINATION CONTROL, and the reason this is a qualified
        opinion rather than noise.

        A repo that customises its catalogue at all would otherwise caveat on
        every claim, and a caveat that fires on every run teaches the reader to
        discount it — the same argument that kept the sanitizer caveat narrow.
        A sink displaced out of ``network`` could not have produced evidence
        for a ``host_fs`` claim.
        """
        verdict = verify_taint_claim(
            _claim(), [],
            displaced_sinks={"python": [_sink("socket", "send", "network")]},
        )
        assert verdict.verdict == "confirmed"
        assert verdict.caveats == []

    def test_no_displacement_is_a_plain_confirmed(self) -> None:
        verdict = verify_taint_claim(_claim(), [])
        assert verdict.verdict == "confirmed"
        assert verdict.caveats == []

    def test_a_violated_claim_is_not_downgraded_into_a_confirm(self) -> None:
        """DIRECTION. The caveat may only ever weaken a clean verdict; it must
        never touch one that already found a flow, or a user row would become
        a way to convert `violated` into something softer — which is the very
        move this invariant exists to prevent.
        """

        finding = TaintFlowFinding(
            taint_label="host_secret",
            source_symbol="python:app.py:1-5:leak:function",
            source_primitive="os.environ",
            sink_symbol="python:app.py:1-5:leak:function",
            sink_primitive="os.remove",
            sink_zone="host_fs",
            sanitized=False,
            confidence="approximate",
            analysis_method="structural",
        )
        verdict = verify_taint_claim(
            _claim(), [finding],
            displaced_sinks={"python": [_sink("os", "remove", "host_fs")]},
        )
        assert verdict.verdict == "violated"
