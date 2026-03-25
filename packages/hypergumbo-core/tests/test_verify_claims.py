# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for security claim verification (ADR-0016 Phase 3 + ADR-0017 taint flow).

Covers claim YAML parsing, boundary map checking, taint-flow verdict generation.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hypergumbo_core.verify_claims import (
    Claim,
    ClaimVerdict,
    TaintFlowConstraint,
    load_claims,
    verify_claim,
    verify_claims,
    verify_taint_claim,
)
from hypergumbo_core.io_boundary import BoundaryMap, BoundaryMapEntry, IoChain
from hypergumbo_core.taint import TaintFlowFinding


def _make_boundary_map(**kwargs) -> BoundaryMap:
    """Create a BoundaryMap with specified boundaries."""
    bmap = BoundaryMap()
    for boundary, chain_count in kwargs.items():
        chains = [
            IoChain(
                boundary=boundary,
                primitive=f"test.{boundary}",
                io_edge_src=f"src:{i}",
                io_edge_dst=f"dst:{i}",
            )
            for i in range(chain_count)
        ]
        bmap.entries[boundary] = BoundaryMapEntry(
            boundary=boundary,
            chains=chains,
            primitives_used=[f"test.{boundary}"],
        )
        bmap.total_io_edges += chain_count
    return bmap


class TestLoadClaims:
    """Tests for loading claims from YAML."""

    def test_load_basic_claim(self, tmp_path: Path) -> None:
        claims_yaml = {
            "claims": [
                {
                    "id": "SC-001",
                    "text": "No network sends",
                    "constraint": {
                        "boundary": "net_send",
                        "must_not_exist": True,
                    },
                },
            ],
        }
        path = tmp_path / "claims.yaml"
        path.write_text(yaml.dump(claims_yaml))
        claims = load_claims(path)
        assert len(claims) == 1
        assert claims[0].id == "SC-001"
        assert claims[0].text == "No network sends"

    def test_load_multiple_claims(self, tmp_path: Path) -> None:
        claims_yaml = {
            "claims": [
                {"id": "SC-001", "text": "No net", "constraint": {"boundary": "net_send", "must_not_exist": True}},
                {"id": "SC-002", "text": "No sub", "constraint": {"boundary": "subprocess", "must_not_exist": True}},
            ],
        }
        path = tmp_path / "claims.yaml"
        path.write_text(yaml.dump(claims_yaml))
        claims = load_claims(path)
        assert len(claims) == 2

    def test_load_empty_claims(self, tmp_path: Path) -> None:
        path = tmp_path / "claims.yaml"
        path.write_text("claims: []\n")
        claims = load_claims(path)
        assert len(claims) == 0

    def test_load_max_chains_constraint(self, tmp_path: Path) -> None:
        claims_yaml = {
            "claims": [
                {
                    "id": "SC-003",
                    "text": "At most 5 fs writes",
                    "constraint": {
                        "boundary": "fs_write",
                        "max_chains": 5,
                    },
                },
            ],
        }
        path = tmp_path / "claims.yaml"
        path.write_text(yaml.dump(claims_yaml))
        claims = load_claims(path)
        assert claims[0].constraint_max_chains == 5


class TestVerifyClaim:
    """Tests for verifying individual claims against boundary maps."""

    def test_must_not_exist_confirmed(self) -> None:
        """Claim passes when boundary has no chains."""
        bmap = _make_boundary_map(fs_read=5)  # no net_send
        claim = Claim(
            id="SC-001",
            text="No network sends",
            constraint_boundary="net_send",
            constraint_must_not_exist=True,
        )
        verdict = verify_claim(claim, bmap)
        assert verdict.verdict == "confirmed"

    def test_must_not_exist_violated(self) -> None:
        """Claim fails when forbidden boundary has chains."""
        bmap = _make_boundary_map(fs_read=5, net_send=3)
        claim = Claim(
            id="SC-001",
            text="No network sends",
            constraint_boundary="net_send",
            constraint_must_not_exist=True,
        )
        verdict = verify_claim(claim, bmap)
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 3

    def test_max_chains_confirmed(self) -> None:
        """Claim passes when chain count is within limit."""
        bmap = _make_boundary_map(fs_write=3)
        claim = Claim(
            id="SC-002",
            text="At most 5 fs writes",
            constraint_boundary="fs_write",
            constraint_max_chains=5,
        )
        verdict = verify_claim(claim, bmap)
        assert verdict.verdict == "confirmed"

    def test_max_chains_violated(self) -> None:
        """Claim fails when chain count exceeds limit."""
        bmap = _make_boundary_map(fs_write=10)
        claim = Claim(
            id="SC-002",
            text="At most 5 fs writes",
            constraint_boundary="fs_write",
            constraint_max_chains=5,
        )
        verdict = verify_claim(claim, bmap)
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 10

    def test_no_constraint_defaults_confirmed(self) -> None:
        """Claim with no constraint flags defaults to confirmed."""
        bmap = _make_boundary_map(fs_read=5)
        claim = Claim(
            id="SC-X",
            text="No constraint",
            constraint_boundary="fs_read",
        )
        verdict = verify_claim(claim, bmap)
        assert verdict.verdict == "confirmed"
        assert "No constraint" in verdict.details

    def test_must_not_exist_no_boundary_data(self) -> None:
        """Claim passes when boundary type has no data at all."""
        bmap = BoundaryMap()  # empty
        claim = Claim(
            id="SC-001",
            text="No subprocess",
            constraint_boundary="subprocess",
            constraint_must_not_exist=True,
        )
        verdict = verify_claim(claim, bmap)
        assert verdict.verdict == "confirmed"


class TestVerifyClaims:
    """Tests for batch claim verification."""

    def test_all_confirmed(self) -> None:
        bmap = _make_boundary_map(fs_read=5)
        claims = [
            Claim(id="SC-001", text="No net", constraint_boundary="net_send", constraint_must_not_exist=True),
            Claim(id="SC-002", text="No sub", constraint_boundary="subprocess", constraint_must_not_exist=True),
        ]
        verdicts = verify_claims(claims, bmap)
        assert len(verdicts) == 2
        assert all(v.verdict == "confirmed" for v in verdicts)

    def test_mixed_verdicts(self) -> None:
        bmap = _make_boundary_map(fs_read=5, net_send=2)
        claims = [
            Claim(id="SC-001", text="No net", constraint_boundary="net_send", constraint_must_not_exist=True),
            Claim(id="SC-002", text="No sub", constraint_boundary="subprocess", constraint_must_not_exist=True),
        ]
        verdicts = verify_claims(claims, bmap)
        assert verdicts[0].verdict == "violated"
        assert verdicts[1].verdict == "confirmed"

    def test_to_dict(self) -> None:
        bmap = _make_boundary_map(net_send=2)
        claims = [
            Claim(id="SC-001", text="No net", constraint_boundary="net_send", constraint_must_not_exist=True),
        ]
        verdicts = verify_claims(claims, bmap)
        d = verdicts[0].to_dict()
        assert d["claim_id"] == "SC-001"
        assert d["verdict"] == "violated"
        assert d["evidence_count"] == 2


# ---------------------------------------------------------------------------
# Tests — Taint-flow claims (ADR-0017)
# ---------------------------------------------------------------------------


def _make_finding(
    taint_label: str = "plaintext",
    sink_zone: str = "host_fs",
    sanitized: bool = False,
    source_prim: str = "decrypt",
    sink_prim: str = "write",
) -> TaintFlowFinding:
    """Create a minimal TaintFlowFinding."""
    return TaintFlowFinding(
        taint_label=taint_label,
        source_symbol="src:1",
        source_primitive=source_prim,
        sink_symbol="dst:1",
        sink_primitive=sink_prim,
        sink_zone=sink_zone,
        sanitized=sanitized,
        confidence="approximate",
        analysis_method="structural",
    )


class TestTaintFlowConstraint:
    """Tests for TaintFlowConstraint dataclass."""

    def test_default_allowed_sanitizers(self) -> None:
        tf = TaintFlowConstraint(
            source_taint="plaintext",
            prohibited_sink_zone="host_fs",
        )
        assert tf.allowed_sanitizers == []

    def test_explicit_allowed_sanitizers(self) -> None:
        tf = TaintFlowConstraint(
            source_taint="plaintext",
            prohibited_sink_zone="host_fs",
            allowed_sanitizers=["Fernet.encrypt"],
        )
        assert tf.allowed_sanitizers == ["Fernet.encrypt"]


class TestLoadTaintFlowClaims:
    """Tests for loading claims with taint_flow constraints from YAML."""

    def test_load_taint_flow_claim(self, tmp_path: Path) -> None:
        claims_yaml = {
            "claims": [
                {
                    "id": "TF-001",
                    "text": "Plaintext must not reach host filesystem",
                    "constraint": {
                        "taint_flow": {
                            "source_taint": "plaintext",
                            "prohibited_sink_zone": "host_fs",
                            "allowed_sanitizers": ["Fernet.encrypt"],
                        },
                    },
                },
            ],
        }
        path = tmp_path / "claims.yaml"
        path.write_text(yaml.dump(claims_yaml))
        claims = load_claims(path)
        assert len(claims) == 1
        assert claims[0].constraint_taint_flow is not None
        tf = claims[0].constraint_taint_flow
        assert tf.source_taint == "plaintext"
        assert tf.prohibited_sink_zone == "host_fs"
        assert tf.allowed_sanitizers == ["Fernet.encrypt"]

    def test_load_mixed_claims(self, tmp_path: Path) -> None:
        """Both boundary and taint-flow claims in the same file."""
        claims_yaml = {
            "claims": [
                {
                    "id": "SC-001",
                    "text": "No net sends",
                    "constraint": {"boundary": "net_send", "must_not_exist": True},
                },
                {
                    "id": "TF-001",
                    "text": "No plaintext to disk",
                    "constraint": {
                        "taint_flow": {
                            "source_taint": "plaintext",
                            "prohibited_sink_zone": "host_fs",
                        },
                    },
                },
            ],
        }
        path = tmp_path / "claims.yaml"
        path.write_text(yaml.dump(claims_yaml))
        claims = load_claims(path)
        assert claims[0].constraint_taint_flow is None
        assert claims[1].constraint_taint_flow is not None


class TestVerifyTaintClaim:
    """Tests for taint-flow claim verification."""

    def test_no_findings_confirmed(self) -> None:
        claim = Claim(
            id="TF-001",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )
        verdict = verify_taint_claim(claim, [])
        assert verdict.verdict == "confirmed"
        assert "No unsanitized" in verdict.details

    def test_violation_found(self) -> None:
        claim = Claim(
            id="TF-001",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )
        findings = [_make_finding()]
        verdict = verify_taint_claim(claim, findings)
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 1
        assert "approximate" in verdict.details

    def test_sanitized_finding_not_violation(self) -> None:
        claim = Claim(
            id="TF-001",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )
        findings = [_make_finding(sanitized=True)]
        verdict = verify_taint_claim(claim, findings)
        assert verdict.verdict == "confirmed"

    def test_wrong_taint_label_not_violation(self) -> None:
        claim = Claim(
            id="TF-001",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )
        findings = [_make_finding(taint_label="key_material")]
        verdict = verify_taint_claim(claim, findings)
        assert verdict.verdict == "confirmed"

    def test_wrong_zone_not_violation(self) -> None:
        claim = Claim(
            id="TF-001",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )
        findings = [_make_finding(sink_zone="network")]
        verdict = verify_taint_claim(claim, findings)
        assert verdict.verdict == "confirmed"

    def test_no_taint_flow_constraint(self) -> None:
        """Claim without taint_flow constraint returns confirmed."""
        claim = Claim(id="X", text="X")
        verdict = verify_taint_claim(claim, [])
        assert verdict.verdict == "confirmed"
        assert "No taint_flow constraint" in verdict.details

    def test_multiple_violations_truncated(self) -> None:
        """More than 5 violations shows truncated list."""
        claim = Claim(
            id="TF-001",
            text="No plaintext to host_fs",
            constraint_taint_flow=TaintFlowConstraint(
                source_taint="plaintext",
                prohibited_sink_zone="host_fs",
            ),
        )
        findings = [
            _make_finding(source_prim=f"decrypt{i}", sink_prim=f"write{i}")
            for i in range(7)
        ]
        verdict = verify_taint_claim(claim, findings)
        assert verdict.verdict == "violated"
        assert verdict.evidence_count == 7
        assert "and 2 more" in verdict.details


class TestVerifyClaimsMixed:
    """Tests for mixed boundary + taint-flow claim verification."""

    def test_mixed_claims_routing(self) -> None:
        """Boundary claims use boundary_map, taint claims use findings."""
        bmap = _make_boundary_map(net_send=2)
        findings = [_make_finding()]
        claims = [
            Claim(
                id="SC-001", text="No net",
                constraint_boundary="net_send",
                constraint_must_not_exist=True,
            ),
            Claim(
                id="TF-001", text="No plaintext to disk",
                constraint_taint_flow=TaintFlowConstraint(
                    source_taint="plaintext",
                    prohibited_sink_zone="host_fs",
                ),
            ),
        ]
        verdicts = verify_claims(claims, bmap, taint_findings=findings)
        assert verdicts[0].verdict == "violated"  # boundary claim
        assert verdicts[1].verdict == "violated"  # taint claim

    def test_taint_findings_default_none(self) -> None:
        """verify_claims works without taint_findings arg."""
        bmap = _make_boundary_map()
        claims = [
            Claim(
                id="TF-001", text="No plaintext to disk",
                constraint_taint_flow=TaintFlowConstraint(
                    source_taint="plaintext",
                    prohibited_sink_zone="host_fs",
                ),
            ),
        ]
        verdicts = verify_claims(claims, bmap)
        assert verdicts[0].verdict == "confirmed"  # no findings → confirmed
