# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security claim verification against I/O boundary and taint-flow analysis.

Loads security claims from a YAML file, checks each claim against either the
boundary map (ADR-0016) or taint-flow results (ADR-0017), and returns verdicts.

Claim Format
------------
Claims are YAML files with a ``claims`` list. Each claim specifies:

- ``id``: Unique identifier (e.g., SC-001)
- ``text``: Human-readable description of the security property
- ``constraint``: What to check — one of:

  **Boundary constraint** (ADR-0016):
  - ``boundary``: Which I/O boundary type to check (e.g., "net_send")
  - ``must_not_exist``: If true, the boundary must have zero chains
  - ``max_chains``: Maximum allowed chain count for the boundary

  **Taint-flow constraint** (ADR-0017):
  - ``taint_flow``: Sub-object with taint-flow verification parameters
    - ``source_taint``: Taint label that must not reach the sink zone
    - ``prohibited_sink_zone``: Zone where tainted data must not arrive
    - ``allowed_sanitizers``: List of sanitizer qualified names (optional)

Verdict Types
-------------
- ``confirmed``: Claim holds (no violations found)
- ``violated``: Specific evidence contradicts the claim

For taint-flow claims, structural analysis produces ``approximate`` confidence.

How It Works
------------
1. ``load_claims(path)`` reads the YAML and returns ``Claim`` objects
2. ``verify_claim(claim, boundary_map)`` checks one claim → ``ClaimVerdict``
3. ``verify_taint_claim(claim, findings)`` checks taint-flow → ``ClaimVerdict``
4. ``verify_claims(claims, boundary_map, findings)`` checks all
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
class TaintFlowConstraint:
    """Taint-flow constraint for ADR-0017 claims.

    Attributes:
        source_taint: Taint label that must not reach the sink zone.
        prohibited_sink_zone: Zone where tainted data must not arrive.
        allowed_sanitizers: Sanitizer names that neutralize the taint (optional).
    """

    source_taint: str
    prohibited_sink_zone: str
    allowed_sanitizers: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.allowed_sanitizers is None:
            self.allowed_sanitizers = []


@dataclass
class Claim:
    """A security claim to verify against a boundary map or taint-flow analysis.

    Attributes:
        id: Unique identifier.
        text: Human-readable description.
        constraint_boundary: Which I/O boundary type to check (ADR-0016).
        constraint_must_not_exist: If true, the boundary must have 0 chains.
        constraint_max_chains: Maximum allowed chains for the boundary.
        constraint_taint_flow: Taint-flow constraint (ADR-0017, optional).
    """

    id: str
    text: str
    constraint_boundary: str = ""
    constraint_must_not_exist: bool = False
    constraint_max_chains: Optional[int] = None
    constraint_taint_flow: Optional[TaintFlowConstraint] = None


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

        # Parse optional taint_flow sub-constraint (ADR-0017)
        taint_flow_data = constraint.get("taint_flow")
        taint_flow = None
        if isinstance(taint_flow_data, dict):
            taint_flow = TaintFlowConstraint(
                source_taint=taint_flow_data.get("source_taint", ""),
                prohibited_sink_zone=taint_flow_data.get(
                    "prohibited_sink_zone", "",
                ),
                allowed_sanitizers=taint_flow_data.get(
                    "allowed_sanitizers", [],
                ),
            )

        claim = Claim(
            id=entry.get("id", ""),
            text=entry.get("text", ""),
            constraint_boundary=constraint.get("boundary", ""),
            constraint_must_not_exist=constraint.get("must_not_exist", False),
            constraint_max_chains=constraint.get("max_chains"),
            constraint_taint_flow=taint_flow,
        )
        claims.append(claim)

    return claims


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_claim(claim: Claim, boundary_map: BoundaryMap) -> ClaimVerdict:
    """Verify a single boundary-constraint claim against a boundary map.

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


def verify_taint_claim(
    claim: Claim,
    findings: list,
) -> ClaimVerdict:
    """Verify a single taint-flow claim against propagation findings.

    Checks whether any TaintFlowFinding matches the claim's constraint:
    the source taint label flows to the prohibited sink zone without
    being sanitized.

    Args:
        claim: The claim with a taint_flow constraint.
        findings: List of TaintFlowFinding objects from propagation.

    Returns:
        ClaimVerdict with the result.
    """
    tf = claim.constraint_taint_flow
    if tf is None:
        return ClaimVerdict(
            claim_id=claim.id,
            claim_text=claim.text,
            verdict="confirmed",
            details="No taint_flow constraint to check.",
        )

    # Filter findings matching this claim's taint label and sink zone
    violations = [
        f for f in findings
        if f.taint_label == tf.source_taint
        and f.sink_zone == tf.prohibited_sink_zone
        and not f.sanitized
    ]

    if not violations:
        return ClaimVerdict(
            claim_id=claim.id,
            claim_text=claim.text,
            verdict="confirmed",
            details=(
                f"No unsanitized {tf.source_taint} data reaches "
                f"{tf.prohibited_sink_zone} zone."
            ),
        )

    # Build detailed violation message
    paths_desc = "; ".join(
        f"{v.source_primitive} -> {v.sink_primitive}" for v in violations[:5]
    )
    suffix = ""
    if len(violations) > 5:
        suffix = f" (and {len(violations) - 5} more)"

    return ClaimVerdict(
        claim_id=claim.id,
        claim_text=claim.text,
        verdict="violated",
        evidence_count=len(violations),
        details=(
            f"{len(violations)} unsanitized {tf.source_taint} flow(s) "
            f"to {tf.prohibited_sink_zone} zone "
            f"[{tf.source_taint} confidence: approximate]: "
            f"{paths_desc}{suffix}"
        ),
    )


def verify_claims(
    claims: list[Claim],
    boundary_map: BoundaryMap,
    taint_findings: list | None = None,
) -> list[ClaimVerdict]:
    """Verify all claims against boundary map and/or taint-flow findings.

    Claims with ``constraint_taint_flow`` are verified against taint findings.
    Claims with boundary constraints are verified against the boundary map.

    Args:
        claims: List of claims to verify.
        boundary_map: The I/O boundary map to check against.
        taint_findings: Optional list of TaintFlowFinding objects.

    Returns:
        List of ClaimVerdict objects, one per claim.
    """
    verdicts: list[ClaimVerdict] = []
    for claim in claims:
        if claim.constraint_taint_flow is not None:
            verdicts.append(verify_taint_claim(
                claim, taint_findings or [],
            ))
        else:
            verdicts.append(verify_claim(claim, boundary_map))
    return verdicts
