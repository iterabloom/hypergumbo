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

from hypergumbo_core.confidence import derive_confidence
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
