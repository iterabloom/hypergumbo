# SPDX-License-Identifier: AGPL-3.0-or-later
from hypergumbo_core.schema import new_behavior_map, SCHEMA_VERSION


def test_new_behavior_map_has_required_top_level_fields():
    bm = new_behavior_map()

    # Fixed identifiers
    assert bm["schema_version"] == SCHEMA_VERSION
    assert bm["view"] == "behavior_map"
    assert bm["confidence_model"] == "hypergumbo-evidence-v2.2"
    assert bm["stable_id_scheme"] == "hypergumbo-stableid-v15"
    assert bm["shape_id_scheme"] == "hypergumbo-shapeid-v4"
    assert bm["repo_fingerprint_scheme"] == "hypergumbo-repofp-v3"
    assert bm["scip_stable_id_scheme"] == "hypergumbo-scip-stableid-v1"

    # Basic structure
    assert bm["analysis_incomplete"] is False
    assert isinstance(bm["analysis_runs"], list)
    assert isinstance(bm["profile"], dict)
    assert isinstance(bm["nodes"], list)
    assert isinstance(bm["edges"], list)
    assert bm["module_sets"] == {}
    assert isinstance(bm["features"], list)
    assert isinstance(bm["metrics"], dict)
    assert isinstance(bm["limits"], dict)
    assert isinstance(bm["entrypoints"], list)
    assert "generated_at" in bm



def test_confidence_model_matches_documented_grammar() -> None:
    """WI-huhin: the emitted confidence_model must match spec Appendix C.

    Appendix C ("Semantic versioning") defines the format as
    ``hypergumbo-evidence-vMAJOR.MINOR`` and assigns MINOR a job: "Refinements
    (new evidence types, score adjustments)". The value shipped as a bare ``v2``,
    which does not match that grammar and makes MINOR unexpressible — so
    ADR-0039's refinement (new evidence types, exactly what MINOR is for) had no
    way to signal itself, and the next one would have had to choose between a
    misleading MAJOR bump and saying nothing.

    Pinned as a format assertion rather than a literal so a legitimate MAJOR or
    MINOR bump passes and only a malformed value fails.
    """
    import re

    from hypergumbo_core.schema import CONFIDENCE_MODEL

    assert re.fullmatch(r"hypergumbo-evidence-v\d+\.\d+", CONFIDENCE_MODEL), (
        f"confidence_model {CONFIDENCE_MODEL!r} does not match the grammar spec "
        f"Appendix C mandates (hypergumbo-evidence-vMAJOR.MINOR). A bare vMAJOR "
        f"leaves MINOR — the channel for refinements like new evidence types — "
        f"unexpressible (WI-huhin)."
    )


def test_each_value_label_is_derived_from_its_scheme_constant() -> None:
    """ADR-0035 "Value labels": one source per label. Each prefix is
    ``value_label`` of its scheme constant, so bumping the constant relabels
    every value; the literals pin what the labels are today."""
    from hypergumbo_core import schema

    pairs = (
        (schema.STABLE_ID_PREFIX, "stableid", schema.STABLE_ID_SCHEME, "hgstableid15:"),
        (schema.SHAPE_ID_PREFIX, "shapeid", schema.SHAPE_ID_SCHEME, "hgshapeid4:"),
        (schema.REPO_FINGERPRINT_PREFIX, "repofp", schema.REPO_FINGERPRINT_SCHEME, "hgrepofp3:"),
        (schema.SCIP_STABLE_ID_PREFIX, "scipid", schema.SCIP_STABLE_ID_SCHEME, "hgscipid1:"),
    )
    for prefix, word, scheme, literal in pairs:
        assert prefix == schema.value_label(word, scheme) == literal
    # The number is the constant's: a bumped constant gives a bumped label.
    assert schema.value_label("stableid", "hypergumbo-stableid-v16") == "hgstableid16:"
    # The four labels are distinct, so no value reads as another kind.
    assert len({p for p, *_ in pairs}) == 4


def test_value_label_rejects_a_scheme_without_a_version_number() -> None:
    import pytest

    from hypergumbo_core.schema import value_label

    with pytest.raises(ValueError, match="-v<N>"):
        value_label("stableid", "hypergumbo-stableid")


def test_identity_digest_strips_any_label() -> None:
    """A consumer comparing digests across the v14 -> v15 relabel gets the
    same hex from both forms of one value."""
    from hypergumbo_core.schema import identity_digest

    assert identity_digest("sha256:0123456789abcdef") == "0123456789abcdef"
    assert identity_digest("hgstableid15:0123456789abcdef") == "0123456789abcdef"
    assert identity_digest("hgscipid1:0123456789abcdef") == "0123456789abcdef"
