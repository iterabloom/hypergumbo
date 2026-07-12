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
seeded value reproduces today's published value). The derivation is now LIVE (WI-nurun): the table seeds 75 inference pathways
(single-valued + the multimodal call types ``ast_call`` / ``ast_call_direct``,
which are ``Edge.is_resolved``-conditioned via ``base_confidence_unresolved``);
``Edge.create`` derives confidence when a producer omits it (the default-flip),
and the producer migration dropped the literal ``confidence=`` at the analyzer
emit sites — so analyzer edge confidence is now derived from evidence, closing
INV-suvil. Linker edges keep their separate match-quality value (spec §12), and
a handful of sites whose confidence encodes context the table cannot express
(dynamically-computed match-strength / the ``type_hierarchy`` dampener; the
ambiguity / unresolved context-encoders) deliberately retain an explicit
``confidence=``.

Contract: ``derive_confidence(evidence_type)`` returns the registered
``base_confidence`` for that inference pathway, or ``None`` when the type is
unregistered OR registered-but-not-yet-seeded. ``None`` deliberately means "no
derived value" so the caller keeps its own literal — there is NO single
fallback constant. The spec's 0.30 unknown-evidence MUST was withdrawn
(confidence:F3 Stage A); re-introducing any blanket default would recreate the
wrong-for-most-of-corpus problem WI-gifat filed.

``confidence_within_band`` is the WI-nurun step-4 range-validation reader: it
checks an edge's confidence sits inside the derived ``[low, base]`` band for
its pathway — a forward regression guard surfaced as an advisory
``cross_field`` violation by :mod:`hypergumbo_core.spec_validator`.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

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


# Analyzer/linker confidence floor (spec §12). 1.0 is a reserved ceiling — no
# detection method is *certain* — so the band's upper bound is the pathway's
# seeded base_confidence (<= 0.95), never above it.
_BAND_FLOOR: float = 0.30


def confidence_within_band(evidence_type: str, confidence: float) -> bool:
    """Return True if ``confidence`` is within the valid band for a pathway.

    For a *seeded* inference pathway the band is ``[low, base_confidence]``
    where ``low`` is ``base_confidence_unresolved`` when the pathway is
    is_resolved-conditioned (the multimodal call types), else the analyzer
    floor :data:`_BAND_FLOOR`. A confidence outside this band is a per-emitter
    value that no longer tracks its inference pathway — a derivation regression
    or an over-claim (e.g. the reserved-ceiling 1.0). Unregistered or
    not-yet-seeded pathways have no band and are reported in-band (the caller's
    literal stands). This is the WI-nurun step-4 range-validation reader.
    """
    spec = find_evidence_type(evidence_type)
    if spec is None or spec.base_confidence is None:
        return True
    low = (
        spec.base_confidence_unresolved
        if spec.base_confidence_unresolved is not None
        else _BAND_FLOOR
    )
    return low - 1e-9 <= confidence <= spec.base_confidence + 1e-9


def find_constant_confidence_violations(
    edges: Iterable[Any],
    *,
    threshold: int = 100,
) -> list[dict[str, Any]]:
    """ADR-0039 ruling 2 guard: flag an emitter shipping an UNDECLARED flat constant.

    The pathology INV-suvil names — a modal published confidence (0.85 on
    ~41.7k edges) that is modal only because the largest emit path passes
    nothing and inherits the flat dataclass default — is now machine-detectable
    via ``Edge.confidence_source``. This guard groups emitted edges by
    ``(emitter, confidence value)`` (emitter = the ``origin`` pass tuple) and
    reports any group larger than ``threshold`` that is not a legitimately
    declared constant.

    A large constant group is LEGITIMATE only when it is either:

    - entirely ``confidence_source='emitter_constant'`` (a declared hardcoded
      producer value — the legal transitional state), or
    - entirely ``confidence_source='evidence_derived'`` with the value equal to
      the registry ``base_confidence`` for each edge's ``evidence_type`` (a
      genuine derivation; every edge of one pathway shares its seeded base).

    Anything else — a ``composite`` value (ranking still fused; ruling 3 has not
    yet relocated it) held constant across >100 edges, or an ``evidence_derived``
    value that does NOT match its registry base (a hardcoded constant
    masquerading as derived) — is reported. Returns one descriptor per offending
    group (``emitter``, ``confidence``, ``count``, ``sources``, ``reason``);
    empty list means clean. Expressible as a property test over any behavior map.
    """
    groups: dict[tuple[tuple[str, ...], float], list[Any]] = defaultdict(list)
    for edge in edges:
        origin = getattr(edge, "origin", None) or []
        key = (tuple(origin), round(float(edge.confidence), 9))
        groups[key].append(edge)

    violations: list[dict[str, Any]] = []
    for (emitter, value), group in groups.items():
        if len(group) <= threshold:
            continue
        sources = {getattr(e, "confidence_source", None) for e in group}
        if sources == {"emitter_constant"}:
            continue
        if sources == {"evidence_derived"} and all(
            derive_confidence(e.evidence_type, is_resolved=e.is_resolved) == e.confidence
            for e in group
        ):
            continue
        violations.append({
            "emitter": emitter,
            "confidence": value,
            "count": len(group),
            "sources": sorted(s for s in sources if s is not None),
            "reason": (
                "constant confidence across >{} edges without "
                "confidence_source='emitter_constant' or a matching "
                "evidence-derived base".format(threshold)
            ),
        })
    return violations
