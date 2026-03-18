# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for security claim verification (ADR-0016 Phase 3).

Covers claim YAML parsing, boundary map checking, and verdict generation.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hypergumbo_core.verify_claims import (
    Claim,
    ClaimVerdict,
    load_claims,
    verify_claim,
    verify_claims,
)
from hypergumbo_core.io_boundary import BoundaryMap, BoundaryMapEntry, IoChain


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
