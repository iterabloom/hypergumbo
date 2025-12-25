import json

from hypergumbo.cli import run_behavior_map


def test_run_behavior_map_writes_behavior_map_json(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    out_path = tmp_path / "hypergumbo.results.json"

    run_behavior_map(repo_root=repo_root, out_path=out_path)

    assert out_path.is_file()

    data = json.loads(out_path.read_text())

    assert data["schema_version"] == "0.1.0"
    assert data["view"] == "behavior_map"
    assert data["confidence_model"] == "hypergumbo-evidence-v1"
    assert data["analysis_incomplete"] is False
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)


def test_run_behavior_map_classifies_supply_chain_tiers(tmp_path):
    """Nodes should have supply_chain tier classification based on path."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    # Create files in different tier locations
    # Tier 1: src/ directory (first-party)
    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")

    # Tier 3: node_modules/ (external dep) - but this is excluded by default
    # So we test with a file in root (defaults to first-party)
    (repo_root / "utils.py").write_text("def helper(): pass\n")

    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(repo_root=repo_root, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Find the nodes and check supply_chain field
    nodes = data["nodes"]
    assert len(nodes) >= 2, "Expected at least 2 nodes"

    for node in nodes:
        assert "supply_chain" in node, f"Node missing supply_chain: {node['id']}"
        sc = node["supply_chain"]
        assert "tier" in sc
        assert "tier_name" in sc
        assert "reason" in sc
        assert isinstance(sc["tier"], int)
        assert sc["tier"] in [1, 2, 3, 4]

    # Check specific file classifications
    src_nodes = [n for n in nodes if "src/app.py" in n["path"]]
    assert len(src_nodes) >= 1
    assert src_nodes[0]["supply_chain"]["tier"] == 1
    assert src_nodes[0]["supply_chain"]["tier_name"] == "first_party"
    assert "src/" in src_nodes[0]["supply_chain"]["reason"]


def test_run_behavior_map_includes_supply_chain_summary(tmp_path):
    """Output should include supply_chain_summary with counts per tier."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    # Create some source files
    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")
    (repo_root / "utils.py").write_text("def helper(): pass\n")

    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(repo_root=repo_root, out_path=out_path)

    data = json.loads(out_path.read_text())

    # Should have supply_chain_summary
    assert "supply_chain_summary" in data
    summary = data["supply_chain_summary"]

    # Should have entries for each tier
    assert "first_party" in summary
    assert "internal_dep" in summary
    assert "external_dep" in summary
    assert "derived_skipped" in summary

    # First party should have counts
    fp = summary["first_party"]
    assert "files" in fp
    assert "symbols" in fp
    assert isinstance(fp["files"], int)
    assert isinstance(fp["symbols"], int)

    # derived_skipped should have paths list
    assert "paths" in summary["derived_skipped"]
    assert isinstance(summary["derived_skipped"]["paths"], list)

