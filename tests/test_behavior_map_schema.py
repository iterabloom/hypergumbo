from hypergumbo.schema import new_behavior_map, SCHEMA_VERSION


def test_new_behavior_map_has_required_top_level_fields():
    bm = new_behavior_map()

    # Fixed identifiers
    assert bm["schema_version"] == SCHEMA_VERSION
    assert bm["view"] == "behavior_map"
    assert bm["confidence_model"] == "hypergumbo-evidence-v1"
    assert bm["stable_id_scheme"] == "hypergumbo-stableid-v1"
    assert bm["shape_id_scheme"] == "hypergumbo-shapeid-v1"
    assert bm["repo_fingerprint_scheme"] == "hypergumbo-repofp-v1"

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

