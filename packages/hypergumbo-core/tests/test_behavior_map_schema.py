# SPDX-License-Identifier: AGPL-3.0-or-later
from hypergumbo_core.schema import new_behavior_map, SCHEMA_VERSION


def test_new_behavior_map_has_required_top_level_fields():
    bm = new_behavior_map()

    # Fixed identifiers
    assert bm["schema_version"] == SCHEMA_VERSION
    assert bm["view"] == "behavior_map"
    assert bm["confidence_model"] == "hypergumbo-evidence-v2.0"
    assert bm["stable_id_scheme"] == "hypergumbo-stableid-v8"
    assert bm["shape_id_scheme"] == "hypergumbo-shapeid-v3"
    assert bm["repo_fingerprint_scheme"] == "hypergumbo-repofp-v2"

    # Basic structure
    assert bm["analysis_incomplete"] is False
    assert isinstance(bm["analysis_runs"], list)
    assert isinstance(bm["profile"], dict)
    assert isinstance(bm["nodes"], list)
    assert isinstance(bm["edges"], list)
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
