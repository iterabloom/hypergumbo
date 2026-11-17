# SPDX-License-Identifier: AGPL-3.0-or-later
"""The §3a walk WITHHOLDS a refutation over a definition another name may reach.

WI-buzup, ADR-0017 §7b. The walk follows names. ``t := s; t[0] = secret;
fmt.Println(len(t)); os.WriteFile(..., s[0])`` exhausts along ``t`` and used to
return ``False``, which REMOVED the flow and let the claim read ``confirmed``.
A walk that relied on a definition recorded in ``cfg.DefinitionExposure`` (an
in-place store, a definition reading its own target, an address taken, a
closure capture) now returns ``None`` and hands the sites out through
``withheld``; the §3a site reports ``withheld``, diverts the flow to
``withheld_flows``, and ``verify_claims`` turns a confirming verdict on the
flow's (source label, sink zone) into ``inconclusive``. On the WI-fasub barrier
arm the same ``None`` means no ``sanitized`` credit.

These are unit-level; the shapes on real code are in
``test_unmodeled_alias_shapes.py``.
"""
from __future__ import annotations

from typing import Any, ClassVar

from hypergumbo_core.cfg import (
    EXPOSURE_ADDRESS_TAKEN,
    EXPOSURE_CAPTURED,
    EXPOSURE_IN_PLACE_STORE,
    EXPOSURE_SELF_REFERENCE,
    EXPOSURES,
    DdgEdge,
    DefinitionExposure,
)
from hypergumbo_core.dataflow_scope import (
    dataflow_scope_dict,
    render_dataflow_scope_text,
)
from hypergumbo_core.taint import (
    ESCAPE_REASONS,
    WALK_VERDICT_WITHHELD,
    WALK_VERDICTS,
    EscapeSite,
    TaintSanitizer,
    TaintSink,
    TaintSource,
    WithheldFlow,
    WithheldSite,
    _ddg_taint_reaches,
    propagate_taint_ddg,
)
from hypergumbo_core.verify_claims import ClaimVerdict, _withhold_for_unmodeled_alias

_F = "f"

#: ``t`` defined at the source line 11, used only at the barrier line 12: an
#: exhausted, accounted walk (``False``) with nothing else going on.
_USES = {(_F, "t", 11): {12}}
_DEFS = {(_F, 11): {"t"}}


def _walk(exposure: DefinitionExposure | None, **kw):  # type: ignore[no-untyped-def]
    sites: list[WithheldSite] = []
    escapes: list[EscapeSite] = []
    result = _ddg_taint_reaches(
        _F, [11], kw.pop("sinks", [13]), kw.pop("uses", _USES),
        defs_at=_DEFS, barrier_lines=frozenset({12}), exposure=exposure,
        withheld=sites, escape_sites=escapes, **kw,
    )
    return result, sites, escapes


class TestTheWalk:
    def test_without_exposure_the_walk_refutes(self) -> None:
        assert _walk(None)[0] is False
        assert _walk(DefinitionExposure())[0] is False

    def test_an_in_place_store_withholds(self) -> None:
        result, sites, escapes = _walk(DefinitionExposure(stores=frozenset({("t", 11)})))
        assert result is None
        assert sites == [WithheldSite("t", 11, EXPOSURE_IN_PLACE_STORE)]
        assert [e.reason for e in escapes] == ["unmodeled_alias"]

    def test_a_self_reference_withholds(self) -> None:
        result, sites, _ = _walk(DefinitionExposure(self_references=frozenset({("t", 11)})))
        assert result is None
        assert sites == [WithheldSite("t", 11, EXPOSURE_SELF_REFERENCE)]

    def test_an_address_taken_name_withholds(self) -> None:
        result, sites, _ = _walk(DefinitionExposure(address_taken=frozenset({"t"})))
        assert result is None
        assert sites == [WithheldSite("t", 11, EXPOSURE_ADDRESS_TAKEN)]

    def test_a_captured_name_withholds(self) -> None:
        result, sites, _ = _walk(DefinitionExposure(captured=frozenset({"t"})))
        assert result is None
        assert sites == [WithheldSite("t", 11, EXPOSURE_CAPTURED)]

    def test_a_store_on_another_line_withholds_nothing(self) -> None:
        assert _walk(DefinitionExposure(stores=frozenset({("t", 99)})))[0] is False

    def test_a_dependence_still_wins(self) -> None:
        """Positive evidence is not unmade by exposure (the L4 discipline)."""
        result, sites, _ = _walk(
            DefinitionExposure(stores=frozenset({("t", 11)})),
            uses={(_F, "t", 11): {12, 13}},
        )
        assert result is True
        assert sites == []

    def test_an_escape_is_not_reported_as_withheld(self) -> None:
        """``withheld`` is filled only when exposure is the SOLE reason for ``None``."""
        result, sites, _ = _walk(
            DefinitionExposure(stores=frozenset({("t", 11)})),
            uses={(_F, "t", 11): {12, 14}},
        )
        assert result is None
        assert sites == []

    def test_a_forfeit_is_not_reported_as_withheld(self) -> None:
        result, sites, _ = _walk(
            DefinitionExposure(stores=frozenset({("t", 11)})), forfeit_refutation=True,
        )
        assert result is None
        assert sites == []

    def test_the_vocabularies_name_the_new_outcome(self) -> None:
        assert "unmodeled_alias" in ESCAPE_REASONS
        assert WALK_VERDICT_WITHHELD == "withheld"
        assert WALK_VERDICT_WITHHELD in WALK_VERDICTS


# ---------------------------------------------------------------------------
# propagate_taint_ddg: the §3a removal site and the barrier arm
# ---------------------------------------------------------------------------

_FN = "python:a.py:1-9:handler:function"
SOURCES = [TaintSource(taint_label="plaintext", module="crypto", name="decrypt", kind="function")]
SINKS = [TaintSink(zone="relay", trust_level="untrusted", module="net", name="send", kind="function")]
SANITIZERS = [TaintSanitizer(
    input_taint="plaintext", output_taint="ciphertext", qualified_name="crypto.encrypt",
)]


def _call(name: str, line: int) -> dict:
    q = {"encrypt": "crypto.encrypt"}.get(name, name)
    return {"src": _FN, "dst": f"python:external:0-0:{q}:unresolved",
            "is_resolved": False, "type": "calls", "line": line}


def _ddg(*edges: tuple[str, int, int]) -> list[DdgEdge]:
    return [DdgEdge(variable=v, def_block="bb_0", def_line=d, use_block="bb_0",
                    use_line=u, symbol_id=_FN) for v, d, u in edges]


#   1  plain = decrypt(x)
#   2  y = plain ; y           (a self-feeding heir: exhausted, no call)
#   3  send(z)
_REFUTED: dict[str, Any] = {
    "ddg_edges": _ddg(("plain", 1, 2), ("y", 2, 2)),
    "call_edges": [_call("decrypt", 1), _call("send", 3)],
    "stmt_defuse": {_FN: [(2, ("y",), ("plain", "y"))]},
}


def _propagate(exposure=None, **kw):  # type: ignore[no-untyped-def]
    refuted: list = []
    withheld: list = []
    args = {**_REFUTED, **kw}
    findings = propagate_taint_ddg(
        args.pop("ddg_edges"), args.pop("call_edges"), SOURCES, SINKS,
        args.pop("sanitizers", []), ddg_symbols={_FN},
        refuted_flows=refuted, withheld_flows=withheld, exposure=exposure, **args,
    )
    return findings, refuted, withheld


class TestTheRemovalSite:
    def test_today_s_refutation_still_removes(self) -> None:
        findings, refuted, withheld = _propagate()
        assert findings == [] and len(refuted) == 1 and withheld == []

    def test_an_exposed_refutation_is_diverted_not_removed(self) -> None:
        exposure = {_FN: DefinitionExposure(stores=frozenset({("plain", 1)}))}
        findings, refuted, withheld = _propagate(exposure)
        assert findings == [] and refuted == []
        assert withheld == [WithheldFlow(
            taint_label="plaintext", sink_zone="relay", source_symbol=_FN,
            function=_FN, source="crypto.decrypt", sink="net.send",
            site=WithheldSite("plain", 1, EXPOSURE_IN_PLACE_STORE),
        )]

    def test_exposure_of_another_function_changes_nothing(self) -> None:
        exposure = {"other": DefinitionExposure(stores=frozenset({("plain", 1)}))}
        findings, refuted, withheld = _propagate(exposure)
        assert len(refuted) == 1 and withheld == []


class TestTheBarrierArm:
    """WI-fasub's ``sanitized`` credit rests on a barrier walk's ``False``.

    Owner ruling 2026-11-16: a walk the rule withholds earns no credit.
    """

    #   1  plain = decrypt(token)   2  safe = encrypt(plain)   3  send(safe)
    _CANON: ClassVar[dict[str, Any]] = {
        "ddg_edges": _ddg(("plain", 1, 2), ("safe", 2, 3)),
        "call_edges": [_call("decrypt", 1), _call("encrypt", 2), _call("send", 3)],
        "stmt_defuse": {_FN: [(2, ("safe",), ("plain",))]},
        "sanitizers": SANITIZERS,
    }

    def test_the_credit_is_earned_without_exposure(self) -> None:
        findings, _, _ = _propagate(None, **self._CANON)
        assert len(findings) == 1 and findings[0].sanitized is True

    def test_an_exposed_barrier_walk_earns_no_credit(self) -> None:
        exposure = {_FN: DefinitionExposure(stores=frozenset({("plain", 1)}))}
        findings, refuted, withheld = _propagate(exposure, **self._CANON)
        assert len(findings) == 1
        assert findings[0].sanitized is False
        assert findings[0].walk_verdict == "reached"
        assert refuted == [] and withheld == []


# ---------------------------------------------------------------------------
# verify_claims: the (label, zone)-scoped withhold
# ---------------------------------------------------------------------------

_PROD = "go:main.go:8-14:leak:function"
_TEST = "go:main_test.go:8-14:leak:function"
_FLOW = WithheldFlow(
    taint_label="host_secret", sink_zone="host_fs", source_symbol=_PROD,
    function=_PROD, source="os.Getenv", sink="os.WriteFile",
    site=WithheldSite("t", 11, EXPOSURE_IN_PLACE_STORE),
)


def _verdict(v: str) -> ClaimVerdict:
    return ClaimVerdict(claim_id="C", claim_text="t", verdict=v,
                        details="No unsanitized host_secret data reaches host_fs zone.")


class TestTheClaimWithhold:
    def test_a_confirming_verdict_is_withheld(self) -> None:
        for clean in ("confirmed", "confirmed_with_caveats"):
            out = _withhold_for_unmodeled_alias(_verdict(clean), "host_secret", "host_fs", [_FLOW], False)
            assert out.verdict == "inconclusive"
            assert out.withheld_flows == 1
            assert "host_secret from os.Getenv stored into `t` at main.go:11" in out.details
            assert "does not follow aliases" in out.details

    def test_another_zone_is_not_withheld(self) -> None:
        """Scoped by sink zone, not only by label."""
        out = _withhold_for_unmodeled_alias(_verdict("confirmed"), "host_secret", "network", [_FLOW], False)
        assert out.verdict == "confirmed" and out.withheld_flows == 0

    def test_another_label_is_not_withheld(self) -> None:
        out = _withhold_for_unmodeled_alias(_verdict("confirmed"), "untrusted_input", "host_fs", [_FLOW], False)
        assert out.verdict == "confirmed" and out.withheld_flows == 0

    def test_a_test_source_counts_only_when_asked(self) -> None:
        in_test = _FLOW._replace(source_symbol=_TEST)
        assert _withhold_for_unmodeled_alias(
            _verdict("confirmed"), "host_secret", "host_fs", [in_test], False).verdict == "confirmed"
        assert _withhold_for_unmodeled_alias(
            _verdict("confirmed"), "host_secret", "host_fs", [in_test], True).verdict == "inconclusive"

    def test_a_violated_verdict_keeps_its_verdict_and_records_the_count(self) -> None:
        out = _withhold_for_unmodeled_alias(_verdict("violated"), "host_secret", "host_fs", [_FLOW, _FLOW], False)
        assert out.verdict == "violated" and out.withheld_flows == 2

    def test_each_exposure_kind_has_wording(self) -> None:
        words = {
            EXPOSURE_IN_PLACE_STORE: "stored into", EXPOSURE_SELF_REFERENCE: "rebuilt from itself in",
            EXPOSURE_ADDRESS_TAKEN: "held by address-taken", EXPOSURE_CAPTURED: "held by closure-captured",
        }
        assert set(words) == set(EXPOSURES)
        for kind, word in words.items():
            flow = _FLOW._replace(site=WithheldSite("t", 11, kind))
            out = _withhold_for_unmodeled_alias(_verdict("confirmed"), "host_secret", "host_fs", [flow], False)
            assert f"{word} `t`" in out.details

    def test_the_count_is_serialized(self) -> None:
        out = _withhold_for_unmodeled_alias(_verdict("confirmed"), "host_secret", "host_fs", [_FLOW], False)
        assert out.to_dict()["withheld_flows"] == 1
        assert _verdict("confirmed").to_dict()["withheld_flows"] == 0


class TestTheScopeBlock:
    def test_the_count_is_published(self) -> None:
        assert dataflow_scope_dict([], {}, flows_withheld_by_walk=3)["flows_withheld_by_walk"] == 3
        assert dataflow_scope_dict([], {})["flows_withheld_by_walk"] == 0

    def test_the_text_names_it(self) -> None:
        from hypergumbo_core.dataflow_scope import LanguageDataflowScope

        row = LanguageDataflowScope(
            language="go", catalog_sources=1, catalog_sinks=1, catalog_sanitizers=0,
            cfg_mapping=True, atomic_statement=True, def_use_extractor=True, ddg_spec=True,
        )
        lines = render_dataflow_scope_text([row], {}, flows_withheld_by_walk=2)
        assert any(line.startswith("  Flows WITHHELD by the walk this run: 2.") for line in lines)
