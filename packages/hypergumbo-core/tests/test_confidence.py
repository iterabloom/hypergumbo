# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the confidence:F1 evidence->confidence derivation reader.

Per ADR-0039, ``Edge.confidence`` is *detection reliability* derived from
the inference pathway (``Edge.evidence_type``) rather than hardcoded per
emitter. This first slice introduces the registry-backed ``base_confidence``
field on :class:`EvidenceTypeSpec` and the central ``derive_confidence``
reader for the single-valued, behavior-preserving evidence types. It does
NOT yet route producers through it (the high-blast-radius ``Edge.create``
default flip and the ~575-site producer migration are later F1 steps), so
no published edge value changes here. See
``docs/adr/0039-confidence-separation.md``.
"""

from __future__ import annotations

from hypergumbo_core.confidence import (
    confidence_within_band,
    derive_confidence,
    find_constant_confidence_violations,
)
from hypergumbo_core.ir import Edge
from hypergumbo_core.evidence_types import (
    EVIDENCE_TYPES,
    _CONFIDENCE_SEEDS,
    _RAW_EVIDENCE_TYPES,
    find_evidence_type,
)


# The single-valued, in-band inference pathways seeded in this first slice.
# Each value was verified behavior-preserving against the live self-corpus
# behavior map (exactly one observed confidence per type). Multimodal types
# (ast_call / ast_call_direct, driven by is_resolved), ranking-contaminated
# types (type_hierarchy), and ceiling-breaching producers (naming_convention
# at 1.0) are deliberately deferred to later F1 / F2 / producer-fix slices.
_SEEDED = {
    "ast_import": 0.95,
    "ast_new": 0.95,
    "span_overlap": 0.90,
    "ast_name_read": 0.85,
    "module_attribute_reference": 0.85,
    "module_export_heuristic": 0.75,
    "callback_argument_reference": 0.75,
    "import_to_manifest": 0.90,
}


def test_derive_confidence_returns_seeded_base():
    for name, expected in _SEEDED.items():
        assert derive_confidence(name) == expected, name


def test_derive_confidence_unregistered_returns_none():
    # No re-introduced single default (guards the just-withdrawn 0.30 MUST,
    # WI-gifat): an unregistered evidence type yields None so the caller
    # keeps its own literal rather than being silently re-scored.
    assert derive_confidence("not-a-real-evidence-type") is None


def test_derive_confidence_registered_but_unseeded_returns_none():
    # Derivation is opt-in per type: a registered type without a
    # base_confidence (the long tail) yields None so the caller keeps its
    # own literal. Picked dynamically so seeding more pathways later does
    # not break this (only ~10 of ~125 pathways are seeded).
    unseeded = next((s for s in EVIDENCE_TYPES if s.base_confidence is None), None)
    assert unseeded is not None
    assert derive_confidence(unseeded.name) is None


def test_all_base_confidence_within_unit_interval():
    for spec in EVIDENCE_TYPES:
        if spec.base_confidence is not None:
            assert 0.0 <= spec.base_confidence <= 1.0, spec.name


def test_seeded_base_confidence_within_analyzer_linker_band():
    # Floor/ceiling guard (spec section 12: analyzer 0.30-0.95 / linker
    # 0.40-0.95). Rejects enshrining a ceiling-breach like
    # naming_convention=1.0 (which is why it is deferred to a producer fix).
    # Entrypoint-band pathways (0.70-0.99) are not yet seeded; widen this
    # per-axis when they are.
    for spec in EVIDENCE_TYPES:
        if spec.base_confidence is not None:
            assert 0.30 <= spec.base_confidence <= 0.95, spec.name


def test_seeded_types_are_canonical_inference_pathways():
    # base_confidence is only seeded on canonical inference pathways, never
    # on pending_classification placeholders.
    for name in _SEEDED:
        spec = find_evidence_type(name)
        assert spec is not None, name
        assert spec.axis == "inference_pathway", name
        assert spec.base_confidence == _SEEDED[name], name


# --- Multimodal (is_resolved-conditioned) pathways ---

# The call types whose detection reliability splits on Edge.is_resolved,
# verified against the live self-corpus cross-tab (resolved modal vs
# unresolved modal). Seeded as base_confidence (resolved) +
# base_confidence_unresolved.
_MULTIMODAL = {
    "ast_call_direct": (0.85, 0.50),
    "ast_call": (0.85, 0.40),
}


def test_derive_confidence_multimodal_splits_on_is_resolved():
    for name, (resolved, unresolved) in _MULTIMODAL.items():
        assert derive_confidence(name, is_resolved=True) == resolved, name
        assert derive_confidence(name, is_resolved=False) == unresolved, name


def test_derive_confidence_defaults_to_resolved():
    # is_resolved defaults to True (the common, higher-reliability case).
    assert derive_confidence("ast_call_direct") == 0.85


def test_single_valued_pathways_ignore_is_resolved():
    # A pathway without base_confidence_unresolved returns base_confidence
    # regardless of is_resolved.
    assert derive_confidence("ast_import", is_resolved=False) == 0.95
    assert derive_confidence("ast_import", is_resolved=True) == 0.95


def test_unresolved_variant_implies_resolved_base():
    # No spec may carry base_confidence_unresolved without base_confidence
    # (the unresolved value is a variant OF the resolved base).
    for spec in EVIDENCE_TYPES:
        if spec.base_confidence_unresolved is not None:
            assert spec.base_confidence is not None, spec.name


def test_unresolved_base_confidence_within_band():
    # Same analyzer/linker floor-ceiling guard as the resolved base.
    for spec in EVIDENCE_TYPES:
        if spec.base_confidence_unresolved is not None:
            assert 0.30 <= spec.base_confidence_unresolved <= 0.95, spec.name


# --- WI-nurun: the centralized _CONFIDENCE_SEEDS overlay (PR1) ---


def test_confidence_seeds_all_registered_inference_pathway_in_band():
    """Every seed key is a registered inference-pathway type within the
    analyzer/linker band (0.30-0.95). Guards future seed additions."""
    for name, val in _CONFIDENCE_SEEDS.items():
        spec = find_evidence_type(name)
        assert spec is not None, name
        assert spec.axis == "inference_pathway", name
        assert 0.30 <= val <= 0.95, name


def test_confidence_seeds_applied_to_registry():
    """The overlay sets base_confidence on the public registry, and
    derive_confidence returns the seeded value, for every seed key."""
    for name, val in _CONFIDENCE_SEEDS.items():
        assert find_evidence_type(name).base_confidence == val, name
        assert derive_confidence(name) == val, name


def test_confidence_seeds_disjoint_from_inline_seeds():
    """The dict and the inline-on-spec seeds are disjoint sources of truth
    (no type is seeded both inline and via the overlay table)."""
    inline = {s.name for s in _RAW_EVIDENCE_TYPES if s.base_confidence is not None}
    assert inline.isdisjoint(_CONFIDENCE_SEEDS), inline & _CONFIDENCE_SEEDS.keys()


# --- WI-nurun step 4: range-validation band reader ---


def test_confidence_within_band_seeded_in_band():
    # single-valued: band [0.30, base]
    assert confidence_within_band("ast_import", 0.95) is True   # == base
    assert confidence_within_band("ast_import", 0.30) is True   # == floor
    assert confidence_within_band("ast_import", 0.60) is True   # mid
    # multimodal: band [unresolved, resolved]
    assert confidence_within_band("ast_call", 0.85) is True     # == resolved
    assert confidence_within_band("ast_call", 0.40) is True     # == unresolved
    assert confidence_within_band("ast_call", 0.60) is True     # mid
    # a context-encoder value sits inside its pathway band
    assert confidence_within_band("trait_impl", 0.70) is True   # within [0.30, 0.95]


def test_confidence_within_band_out_of_band():
    assert confidence_within_band("ast_import", 1.0) is False   # > base (reserved ceiling)
    assert confidence_within_band("ast_import", 0.20) is False  # < floor
    assert confidence_within_band("ast_call", 0.95) is False    # > resolved base
    assert confidence_within_band("ast_call", 0.30) is False    # < unresolved low


def test_confidence_within_band_unseeded_or_unregistered_is_in_band():
    # naming_convention is registered but deliberately NOT seeded (1.0
    # ceiling-breach deferred to a producer fix) -> no band -> in-band.
    assert confidence_within_band("naming_convention", 1.0) is True
    # unregistered type -> no band -> in-band (caller's literal stands).
    assert confidence_within_band("not-a-real-evidence-type", 0.5) is True


# --- ADR-0039 ruling 2 guard: find_constant_confidence_violations ---

def _edges(n, *, src, confidence, evidence_type, confidence_source=None, is_resolved=True):
    return [
        Edge.create(
            src=f"{src}:{i}", dst="d", edge_type="calls", line=i,
            origin=src, origin_run_id="u",
            confidence=confidence, evidence_type=evidence_type,
            confidence_source=confidence_source, is_resolved=is_resolved,
        )
        for i in range(n)
    ]


def test_guard_empty_is_clean():
    assert find_constant_confidence_violations([]) == []


def test_guard_group_at_or_below_threshold_is_ignored():
    # 100 edges (== default threshold) at one composite constant -> not > threshold.
    edges = _edges(100, src="emit", confidence=0.5,
                   evidence_type="naming_convention", confidence_source="composite")
    assert find_constant_confidence_violations(edges) == []


def test_guard_emitter_constant_declared_is_clean():
    edges = _edges(150, src="emit", confidence=1.0, evidence_type="naming_convention")
    # explicit confidence -> emitter_constant, declared -> legitimate.
    assert all(e.confidence_source == "emitter_constant" for e in edges)
    assert find_constant_confidence_violations(edges) == []


def test_guard_evidence_derived_at_registry_base_is_clean():
    # ast_call_direct is seeded 0.85 (resolved) -> derived value matches base.
    edges = _edges(150, src="emit", confidence=None, evidence_type="ast_call_direct")
    assert all(e.confidence_source == "evidence_derived" for e in edges)
    assert all(e.confidence == 0.85 for e in edges)
    assert find_constant_confidence_violations(edges) == []


def test_guard_composite_constant_is_flagged():
    edges = _edges(150, src="emit", confidence=0.5,
                   evidence_type="naming_convention", confidence_source="composite")
    violations = find_constant_confidence_violations(edges)
    assert len(violations) == 1
    v = violations[0]
    assert v["count"] == 150
    assert v["confidence"] == 0.5
    assert v["sources"] == ["composite"]
    assert "emitter_constant" in v["reason"]


def test_guard_evidence_derived_masquerade_is_flagged():
    # Claim evidence_derived but ship a constant that is NOT the registry base
    # for the pathway (ast_call_direct base is 0.85, we ship 0.42).
    edges = _edges(150, src="emit", confidence=0.42,
                   evidence_type="ast_call_direct", confidence_source="evidence_derived")
    violations = find_constant_confidence_violations(edges)
    assert len(violations) == 1
    assert violations[0]["sources"] == ["evidence_derived"]


def test_guard_respects_custom_threshold():
    edges = _edges(60, src="emit", confidence=0.5,
                   evidence_type="naming_convention", confidence_source="composite")
    assert find_constant_confidence_violations(edges, threshold=100) == []
    assert len(find_constant_confidence_violations(edges, threshold=50)) == 1


def test_guard_partitions_by_emitter_and_value():
    # Two emitters, each 150 emitter_constant edges at distinct values -> clean;
    # a third emitter with 150 composite -> exactly one violation.
    a = _edges(150, src="emitA", confidence=0.9, evidence_type="naming_convention")
    b = _edges(150, src="emitB", confidence=0.8, evidence_type="naming_convention")
    c = _edges(150, src="emitC", confidence=0.5,
               evidence_type="naming_convention", confidence_source="composite")
    violations = find_constant_confidence_violations(a + b + c)
    assert len(violations) == 1
    assert violations[0]["emitter"] == ("emitC",)
