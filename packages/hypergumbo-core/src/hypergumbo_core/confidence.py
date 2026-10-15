# SPDX-License-Identifier: AGPL-3.0-or-later
"""Evidence -> confidence derivation (the ADR-0039 detection-reliability layer).

Per ADR-0039, ``Edge.confidence`` names *detection reliability* — how sure the
analyzer is that the edge exists — and that reliability should be DERIVED from
the inference pathway (``Edge.evidence_type``) rather than hardcoded as a
literal at each of the ~575 emit sites (the per-emitter accident INV-suvil
names). This module is the central derivation reader.

Seeded values live in :data:`hypergumbo_core.evidence_types.EVIDENCE_TYPES`.
The derivation is LIVE (WI-nurun): the table seeds the single-valued inference
pathways plus the resolution-conditioned ones (``ast_call`` /
``ast_call_direct`` / ``macro_expansion``), whose unresolved edges
(``Edge.is_resolved`` False) take ``base_confidence_unresolved``;
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

``derive_resolved_confidence(evidence_type, resolution_score)`` is ADR-0039
ruling 6: a producer whose name resolver graded the match (1.0 exact, 0.90
path hint, 0.85 suffix, ``0.70/sqrt(n)`` ambiguous) passes that grade, and
the confidence is interpolated inside the pathway's band,
``low + (base - low) * score``. The band is the same one
``confidence_within_band`` checks, so the result is in band by construction;
an exact match lands on the base a producer passing nothing would get. The
rejected alternative, ``base * score``, fell below the floor
(``0.85 * 0.70/sqrt(4) = 0.30`` against ``ast_call``'s 0.40) and kept a
private base per producer. Unlike ``derive_confidence`` it raises rather
than returning ``None`` for an unseeded pathway: an unseeded pathway has no
band to interpolate in, and a producer that passed a score asked for one.

Linker edges are a separate category (ADR-0039 ruling 7): their confidence is
match quality, judged against :data:`LINKER_CONFIDENCE_RANGE` (spec §12,
0.40-0.95) whatever ``evidence_type`` they declare. ``confidence_within_band``
takes ``linker=True`` for them; deciding which edges are linker edges (the
producing pass is a registered linker) is the caller's job, because it needs
the pass catalog.

``confidence_within_band`` is the WI-nurun step-4 range-validation reader: it
checks an edge's confidence sits inside the derived ``[low, base]`` band for
its pathway — a forward regression guard surfaced as an advisory
``cross_field`` violation by :mod:`hypergumbo_core.spec_validator`.

``find_constant_confidence_violations`` is the ADR-0039 ruling-2 guard: it
groups edges by (``origin`` emitter, confidence value) and reports every group
of more than ``threshold`` (default 100) edges that is neither entirely
``confidence_source='emitter_constant'`` nor entirely ``evidence_derived`` with
each edge at its registry base value, meaning an undeclared flat constant. It
is exercised as a property test over a behavior map and has no production
caller.
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

#: Spec §12 linker edge confidence range (ADR-0039 ruling 7): match quality,
#: a different quantity from an analyzer pathway band.
LINKER_CONFIDENCE_RANGE: tuple[float, float] = (0.40, 0.95)


def _band(spec: Any) -> tuple[float, float]:
    """Return the ``(low, high)`` band of a seeded pathway spec.

    ``low`` is ``base_confidence_unresolved`` on the is_resolved-conditioned
    pathways, else the analyzer floor; ``high`` is ``base_confidence``.
    """
    low = (
        spec.base_confidence_unresolved
        if spec.base_confidence_unresolved is not None
        else _BAND_FLOOR
    )
    return low, spec.base_confidence


def derive_resolved_confidence(evidence_type: str, resolution_score: float) -> float:
    """ADR-0039 ruling 6: interpolate a resolver's match quality within the band.

    Returns ``low + (base - low) * resolution_score`` for the pathway's band
    (see :func:`_band`). ``resolution_score`` is a match quality in [0, 1] —
    a ``LookupResult.confidence``, or a producer's product of such factors.

    Raises ``ValueError`` when the score is outside [0, 1] or the pathway has
    no seeded ``base_confidence`` (no band to interpolate in).
    """
    if not 0.0 <= resolution_score <= 1.0:
        raise ValueError(
            f"resolution_score={resolution_score!r} is outside [0, 1]"
        )
    spec = find_evidence_type(evidence_type)
    if spec is None or spec.base_confidence is None:
        raise ValueError(
            f"evidence_type={evidence_type!r} has no seeded base_confidence, "
            "so a resolution_score has no band to interpolate in (ADR-0039 "
            "ruling 6); pass confidence= or use a seeded pathway"
        )
    low, high = _band(spec)
    # The endpoints are returned exactly: an exact match must equal the base a
    # producer passing nothing derives (0.40 + 0.45 * 1.0 is 0.8500000000000001).
    if resolution_score == 1.0:
        return high
    if resolution_score == 0.0:
        return low
    return low + (high - low) * resolution_score


def confidence_within_band(
    evidence_type: str, confidence: float, *, linker: bool = False,
) -> bool:
    """Return True if ``confidence`` is within the valid band for a pathway.

    ``linker=True`` (ADR-0039 ruling 7) checks a linker edge against
    :data:`LINKER_CONFIDENCE_RANGE` and ignores ``evidence_type``: the
    analyzer band of the pathway a linker edge declares does not apply to it.

    For a *seeded* inference pathway the band is ``[low, base_confidence]``
    where ``low`` is ``base_confidence_unresolved`` when the pathway is
    is_resolved-conditioned (the multimodal call types), else the analyzer
    floor :data:`_BAND_FLOOR`. A confidence outside this band is a per-emitter
    value that no longer tracks its inference pathway — a derivation regression
    or an over-claim (e.g. the reserved-ceiling 1.0). Unregistered or
    not-yet-seeded pathways have no band and are reported in-band (the caller's
    literal stands). This is the WI-nurun step-4 range-validation reader.
    """
    if linker:
        low, high = LINKER_CONFIDENCE_RANGE
        return low - 1e-9 <= confidence <= high + 1e-9
    spec = find_evidence_type(evidence_type)
    if spec is None or spec.base_confidence is None:
        return True
    low, high = _band(spec)
    return low - 1e-9 <= confidence <= high + 1e-9


def _is_derivable(edge: Any) -> bool:
    """True when an ``evidence_derived`` edge's value is one the layer can produce.

    That is its pathway's derived base, or — for a resolver-graded edge
    (ADR-0039 ruling 6) — any value inside the seeded band.
    """
    base = derive_confidence(edge.evidence_type, is_resolved=edge.is_resolved)
    if base is None:
        return False
    return base == edge.confidence or confidence_within_band(
        edge.evidence_type, edge.confidence,
    )


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
    - entirely ``confidence_source='evidence_derived'`` with each edge's value
      equal to its pathway's derived base, or (ADR-0039 ruling 6) inside the
      pathway's seeded band. A resolver-graded edge is derived but sits
      between the floor and the base: every suffix match on ``ast_call`` gets
      the same interpolated value, so a large group of them is a derivation,
      not a hidden constant.

    Anything else — a ``composite`` value (ranking still fused; ruling 3 has not
    yet relocated it) held constant across >100 edges, or an ``evidence_derived``
    value outside its pathway's band or on an unseeded pathway (a hardcoded
    constant masquerading as derived) — is reported. Returns one descriptor per offending
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
            _is_derivable(e) for e in group
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
