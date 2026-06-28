# SPDX-License-Identifier: AGPL-3.0-or-later
"""Evidence -> confidence derivation (the ADR-0039 detection-reliability layer).

Per ADR-0039, ``Edge.confidence`` names *detection reliability* — how sure the
analyzer is that the edge exists — and that reliability should be DERIVED from
the inference pathway (``Edge.evidence_type``) rather than hardcoded as a
literal at each of the ~575 emit sites (the per-emitter accident INV-suvil
names). This module is the central derivation reader.

This first slice (confidence:F1) is intentionally narrow: it derives only the
single-valued, behavior-preserving inference pathways whose ``base_confidence``
is seeded in :data:`hypergumbo_core.evidence_types.EVIDENCE_TYPES` (verified to
carry exactly one observed confidence each on the live behavior map, so the
seeded value reproduces today's published value). It does NOT yet route
``Edge.create`` through the reader (flipping the flat ~0.85 dataclass default
touches ~44k edges) and does NOT migrate producers; those are later,
higher-blast-radius F1 steps that the strategy gates on the full vocab:F1
axis-gating wave. Until a producer is wired, ``derive_confidence`` is a pure,
side-effect-free lookup with no consumers in the emit path — introducing the
layer without changing any published value.

Contract: ``derive_confidence(evidence_type)`` returns the registered
``base_confidence`` for that inference pathway, or ``None`` when the type is
unregistered OR registered-but-not-yet-seeded. ``None`` deliberately means "no
derived value" so the caller keeps its own literal — there is NO single
fallback constant. The spec's 0.30 unknown-evidence MUST was withdrawn
(confidence:F3 Stage A); re-introducing any blanket default would recreate the
wrong-for-most-of-corpus problem WI-gifat filed. Multimodal pathways (e.g.
``ast_call`` / ``ast_call_direct``, whose confidence is driven by the sibling
``Edge.is_resolved`` field) are deliberately left unseeded here and gain
conditioning in a later F1 step.
"""

from __future__ import annotations

from hypergumbo_core.evidence_types import find_evidence_type


def derive_confidence(evidence_type: str, *, is_resolved: bool = True) -> float | None:
    """Return the ADR-0039 detection-reliability for an inference pathway.

    For pathways whose reliability is conditioned on resolution (the
    multimodal call types), an unresolved edge (``is_resolved=False``) gets
    the lower ``base_confidence_unresolved``; resolved edges and
    non-conditioned pathways get ``base_confidence``.

    ``None`` when ``evidence_type`` is not in the registry, or is registered
    without a seeded ``base_confidence`` (the caller then keeps its literal).
    """
    spec = find_evidence_type(evidence_type)
    if spec is None:
        return None
    if not is_resolved and spec.base_confidence_unresolved is not None:
        return spec.base_confidence_unresolved
    return spec.base_confidence
