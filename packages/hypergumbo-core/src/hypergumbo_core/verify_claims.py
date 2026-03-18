# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security claim verification against I/O boundary maps (ADR-0016 Phase 3).

Loads security claims from a YAML file, checks each claim against the
boundary map produced by ``compute_boundary_map()``, and returns verdicts.

Claim Format
------------
Claims are YAML files with a ``claims`` list. Each claim specifies:

- ``id``: Unique identifier (e.g., SC-001)
- ``text``: Human-readable description of the security property
- ``constraint``: What to check against the boundary map
  - ``boundary``: Which I/O boundary type to check (e.g., "net_send")
  - ``must_not_exist``: If true, the boundary must have zero chains
  - ``max_chains``: Maximum allowed chain count for the boundary

Verdict Types
-------------
- ``confirmed``: All I/O chains consistent with claim
- ``violated``: Specific I/O chains contradict the claim (with evidence)

Future: ``confirmed_with_caveats`` (opaque boundaries exist) and
``inconclusive`` (insufficient coverage) will be added when transparency
tier classification is integrated.

How It Works
------------
1. ``load_claims(path)`` reads the YAML and returns ``Claim`` objects
2. ``verify_claim(claim, boundary_map)`` checks one claim → ``ClaimVerdict``
3. ``verify_claims(claims, boundary_map)`` checks all → list of verdicts
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from .io_boundary import BoundaryMap


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Claim:
    """A security claim to verify against a boundary map.

    Attributes:
        id: Unique identifier.
        text: Human-readable description.
        constraint_boundary: Which I/O boundary type to check.
        constraint_must_not_exist: If true, the boundary must have 0 chains.
        constraint_max_chains: Maximum allowed chains for the boundary.
    """

    id: str
    text: str
    constraint_boundary: str
    constraint_must_not_exist: bool = False
    constraint_max_chains: Optional[int] = None


@dataclass
class ClaimVerdict:
    """Result of verifying a single claim.

    Attributes:
        claim_id: The claim's ID.
        claim_text: The claim's human-readable text.
        verdict: One of "confirmed", "violated".
        evidence_count: Number of I/O chains that violate the claim (0 if confirmed).
        details: Human-readable explanation.
    """

    claim_id: str
    claim_text: str
    verdict: str  # "confirmed" or "violated"
    evidence_count: int = 0
    details: str = ""

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict."""
        return {
            "claim_id": self.claim_id,
            "claim_text": self.claim_text,
            "verdict": self.verdict,
            "evidence_count": self.evidence_count,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Claim loading
# ---------------------------------------------------------------------------


def load_claims(path: Path) -> list[Claim]:
    """Load security claims from a YAML file.

    Args:
        path: Path to the claims YAML file.

    Returns:
        List of Claim objects.
    """
    content = path.read_text(encoding="utf-8")
    data = yaml.safe_load(content) or {}

    claims: list[Claim] = []
    for entry in data.get("claims", []):
        constraint = entry.get("constraint", {})
        claim = Claim(
            id=entry.get("id", ""),
            text=entry.get("text", ""),
            constraint_boundary=constraint.get("boundary", ""),
            constraint_must_not_exist=constraint.get("must_not_exist", False),
            constraint_max_chains=constraint.get("max_chains"),
        )
        claims.append(claim)

    return claims


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_claim(claim: Claim, boundary_map: BoundaryMap) -> ClaimVerdict:
    """Verify a single claim against a boundary map.

    Args:
        claim: The claim to verify.
        boundary_map: The I/O boundary map to check against.

    Returns:
        ClaimVerdict with the result.
    """
    entry = boundary_map.entries.get(claim.constraint_boundary)
    chain_count = len(entry.chains) if entry else 0

    # Check must_not_exist constraint
    if claim.constraint_must_not_exist:
        if chain_count == 0:
            return ClaimVerdict(
                claim_id=claim.id,
                claim_text=claim.text,
                verdict="confirmed",
                details=f"No {claim.constraint_boundary} chains found.",
            )
        return ClaimVerdict(
            claim_id=claim.id,
            claim_text=claim.text,
            verdict="violated",
            evidence_count=chain_count,
            details=(
                f"{chain_count} {claim.constraint_boundary} chain(s) found, "
                f"but claim requires none."
            ),
        )

    # Check max_chains constraint
    if claim.constraint_max_chains is not None:
        if chain_count <= claim.constraint_max_chains:
            return ClaimVerdict(
                claim_id=claim.id,
                claim_text=claim.text,
                verdict="confirmed",
                details=(
                    f"{chain_count} {claim.constraint_boundary} chain(s) found, "
                    f"within limit of {claim.constraint_max_chains}."
                ),
            )
        return ClaimVerdict(
            claim_id=claim.id,
            claim_text=claim.text,
            verdict="violated",
            evidence_count=chain_count,
            details=(
                f"{chain_count} {claim.constraint_boundary} chain(s) found, "
                f"exceeds limit of {claim.constraint_max_chains}."
            ),
        )

    # No constraint matched — inconclusive
    return ClaimVerdict(
        claim_id=claim.id,
        claim_text=claim.text,
        verdict="confirmed",
        details="No constraint to check.",
    )


def verify_claims(
    claims: list[Claim],
    boundary_map: BoundaryMap,
) -> list[ClaimVerdict]:
    """Verify all claims against a boundary map.

    Args:
        claims: List of claims to verify.
        boundary_map: The I/O boundary map to check against.

    Returns:
        List of ClaimVerdict objects, one per claim.
    """
    return [verify_claim(claim, boundary_map) for claim in claims]
