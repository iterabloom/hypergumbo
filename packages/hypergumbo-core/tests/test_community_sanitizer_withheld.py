# SPDX-License-Identifier: AGPL-3.0-or-later
"""A community sanitizer does not clear a flow; the verdict names it (WI-dikit).

ADR-0061 ruling 2: a row from a community file may add a finding but never
remove one. A sanitizer only removes. So a sanitizer loaded from a file that
declares ``provenance: community`` -- the third-party ``cryptography``,
``aes-gcm``, ``ring`` and ``hkdf`` rows in ``taint_sanitizers/
encryption_community.yaml`` -- is not a barrier in either walk: the flow is
reported, and it names the sanitizer it crossed
(``TaintFlowFinding.withheld_sanitizers``) so a reader knows what vouching for
it would change.

Measured before the change (the row's audit fixture): the shipped
``Fernet.encrypt`` sanitizer turned a plaintext -> host_fs claim from violated
into plain ``confirmed`` with ``sanitized_flows: 1``, no caveat and no
community notice -- an unmaintained row licensing a clean verdict.
"""

from __future__ import annotations

from hypergumbo_core.taint import (
    TaintSanitizer,
    TaintSink,
    TaintSource,
    load_builtin_taint_catalog,
    propagate_taint_structural,
)
from hypergumbo_core.verify_claims import (
    CAVEAT_WITHHELD_COMMUNITY_SANITIZER,
    Claim,
    TaintFlowConstraint,
    verify_taint_claim,
)


def _edge(src: str, dst: str) -> dict:
    return {"src": src, "dst": dst, "type": "calls", "is_resolved": False,
            "meta": {"evidence_type": "ast_call_direct"}}


_HANDLER = "py:a.py:1-5:handler:function"
_STORE = "py:a.py:10-15:encrypt_and_store:function"
_ENCRYPT = "py:external:0-0:Fernet.encrypt:unresolved"
_EDGES = [
    _edge(_HANDLER, "py:external:0-0:Fernet.decrypt:unresolved"),
    _edge(_HANDLER, _STORE),
    _edge(_STORE, _ENCRYPT),
    _edge(_STORE, "py:pathlib.Path:0-0:write_text:unresolved"),
]
#: The same route with no sanitizer call on it.
_UNSANITIZED = [e for e in _EDGES if e["dst"] != _ENCRYPT]
_SOURCES = [TaintSource(
    taint_label="plaintext", module="cryptography.fernet",
    name="Fernet.decrypt", kind="function", return_tainted=True,
)]
_SINKS = [TaintSink(
    zone="host_fs", trust_level="untrusted",
    module="pathlib.Path", name="write_text", kind="method",
)]


def _sanitizer(*, community: bool) -> TaintSanitizer:
    return TaintSanitizer(
        input_taint="plaintext", output_taint="ciphertext",
        qualified_name="Fernet.encrypt", community=community,
    )


class TestTheWalk:
    def test_a_vouched_sanitizer_still_clears_the_flow(self) -> None:
        """THE CONTROL: the same barrier, vouched for, still sanitizes."""
        (finding,) = propagate_taint_structural(
            _EDGES, _SOURCES, _SINKS, [_sanitizer(community=False)],
        )
        assert finding.sanitized is True
        assert finding.withheld_sanitizers == ()

    def test_a_community_sanitizer_does_not_and_is_named(self) -> None:
        (finding,) = propagate_taint_structural(
            _EDGES, _SOURCES, _SINKS, [_sanitizer(community=True)],
        )
        assert finding.sanitized is False
        assert finding.withheld_sanitizers == ("Fernet.encrypt",)
        assert finding.to_dict()["withheld_sanitizers"] == ["Fernet.encrypt"]

    def test_a_route_that_crosses_no_community_sanitizer_names_none(self) -> None:
        (finding,) = propagate_taint_structural(
            _UNSANITIZED, _SOURCES, _SINKS, [_sanitizer(community=True)],
        )
        assert finding.sanitized is False
        assert finding.withheld_sanitizers == ()


class TestTheCatalogue:
    def test_the_shipped_third_party_sanitizers_load_as_community(self) -> None:
        catalog = load_builtin_taint_catalog()
        py = {s.qualified_name: s for s in catalog._sanitizers["python"]}
        java = {s.qualified_name: s for s in catalog._sanitizers["java"]}
        assert py["cryptography.fernet.Fernet.encrypt"].community is True
        assert java["javax.crypto.Cipher.doFinal"].community is False

    def test_no_default_overlays_drops_them_altogether(self) -> None:
        catalog = load_builtin_taint_catalog(include_community=False)
        assert not any(
            s.qualified_name.startswith("cryptography.")
            for s in catalog._sanitizers.get("python", [])
        )


class TestTheVerdict:
    def _claim(self) -> Claim:
        return Claim(
            id="PT-FS", text="plaintext never reaches the host filesystem",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext", prohibited_sink_zone="host_fs",
            ),
        )

    def test_the_flow_is_violated_and_the_caveat_names_the_sanitizer(self) -> None:
        findings = propagate_taint_structural(
            _EDGES, _SOURCES, _SINKS, [_sanitizer(community=True)],
        )
        verdict = verify_taint_claim(self._claim(), findings)
        assert verdict.verdict == "violated"
        (caveat,) = [c for c in verdict.caveats
                     if c["kind"] == CAVEAT_WITHHELD_COMMUNITY_SANITIZER]
        assert caveat["entries"] == ["Fernet.encrypt"]
        assert "taint_sanitizers.d" in caveat["detail"]
        assert verdict.evidence[0]["withheld_sanitizers"] == ["Fernet.encrypt"]

    def test_a_violated_verdict_with_nothing_withheld_carries_no_such_caveat(
        self,
    ) -> None:
        findings = propagate_taint_structural(
            _UNSANITIZED, _SOURCES, _SINKS, [_sanitizer(community=True)],
        )
        verdict = verify_taint_claim(self._claim(), findings)
        assert verdict.verdict == "violated"
        assert not any(c["kind"] == CAVEAT_WITHHELD_COMMUNITY_SANITIZER
                       for c in verdict.caveats)
