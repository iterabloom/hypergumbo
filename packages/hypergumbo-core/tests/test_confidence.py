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
from hypergumbo_core.evidence_types import EVIDENCE_TYPES, find_evidence_type


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
    # base_confidence (the long tail, incl. the deliberately-deferred
    # multimodal ast_call_direct) also yields None.
    spec = find_evidence_type("ast_call_direct")
    assert spec is not None
    assert spec.base_confidence is None
    assert derive_confidence("ast_call_direct") is None


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
