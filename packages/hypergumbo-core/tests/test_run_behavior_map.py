import json

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.schema import SCHEMA_VERSION


def test_run_behavior_map_writes_behavior_map_json(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    out_path = tmp_path / "hypergumbo.results.json"

    run_behavior_map(repo_root=repo_root, out_path=out_path, include_sketch_precomputed=False)

    assert out_path.is_file()

    data = json.loads(out_path.read_text())

    assert data["schema_version"] == SCHEMA_VERSION
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
    run_behavior_map(repo_root=repo_root, out_path=out_path, include_sketch_precomputed=False)

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
    run_behavior_map(repo_root=repo_root, out_path=out_path, include_sketch_precomputed=False)

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


def test_run_behavior_map_compact_mode(tmp_path):
    """Compact mode produces coverage-based output with summaries."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    # Create source files so we have symbols to work with
    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("def main(): helper()\n")
    (src_dir / "utils.py").write_text("def helper(): pass\n")

    out_path = tmp_path / "compact.json"
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        compact=True,
        coverage=0.8,
        budgets="none",  # Disable tiers for this test
        include_sketch_precomputed=False,
    )

    data = json.loads(out_path.read_text())

    # Should have compact view and nodes_summary
    assert data["view"] == "compact"
    assert "nodes_summary" in data

    summary = data["nodes_summary"]
    assert "included" in summary
    assert "omitted" in summary

    # Included summary should have count and coverage
    assert "count" in summary["included"]
    assert "coverage" in summary["included"]

    # Omitted summary should have semantic flavor
    assert "count" in summary["omitted"]
    assert "top_words" in summary["omitted"]
    assert "top_paths" in summary["omitted"]
    assert "kinds" in summary["omitted"]


def test_run_behavior_map_default_tiered_output(tmp_path):
    """Default run generates tiered output files (4k, 16k, 64k)."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    # Create source files
    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "hypergumbo.results.json"
    run_behavior_map(repo_root=repo_root, out_path=out_path, include_sketch_precomputed=False)

    # Main file should exist
    assert out_path.is_file()

    # Default budget files should be generated
    budget_4k = tmp_path / "hypergumbo.results.4k.json"
    budget_16k = tmp_path / "hypergumbo.results.16k.json"
    budget_64k = tmp_path / "hypergumbo.results.64k.json"

    assert budget_4k.is_file(), "4k budget file should be generated"
    assert budget_16k.is_file(), "16k budget file should be generated"
    assert budget_64k.is_file(), "64k budget file should be generated"

    # Check budget file structure
    data_4k = json.loads(budget_4k.read_text())
    assert data_4k["view"] == "tiered"
    assert data_4k["tier_tokens"] == 4000
    assert "nodes_summary" in data_4k

    data_16k = json.loads(budget_16k.read_text())
    assert data_16k["view"] == "tiered"
    assert data_16k["tier_tokens"] == 16000

    data_64k = json.loads(budget_64k.read_text())
    assert data_64k["view"] == "tiered"
    assert data_64k["tier_tokens"] == 64000


def test_run_behavior_map_custom_budgets(tmp_path):
    """Custom budget specification generates specified budget files."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "output.json"
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        budgets="2k,8k",  # Custom budgets
        include_sketch_precomputed=False,
    )

    # Custom budget files should be generated
    budget_2k = tmp_path / "output.2k.json"
    budget_8k = tmp_path / "output.8k.json"

    assert budget_2k.is_file(), "2k budget file should be generated"
    assert budget_8k.is_file(), "8k budget file should be generated"

    # Default budgets should NOT be generated
    budget_4k = tmp_path / "output.4k.json"
    budget_16k = tmp_path / "output.16k.json"
    budget_64k = tmp_path / "output.64k.json"

    assert not budget_4k.exists(), "4k budget file should NOT be generated"
    assert not budget_16k.exists(), "16k budget file should NOT be generated"
    assert not budget_64k.exists(), "64k budget file should NOT be generated"

    # Check custom budget structure
    data_2k = json.loads(budget_2k.read_text())
    assert data_2k["view"] == "tiered"
    assert data_2k["tier_tokens"] == 2000


def test_run_behavior_map_budgets_none(tmp_path):
    """budgets='none' disables budget file generation."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "output.json"
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        budgets="none",  # Disable budget output
        include_sketch_precomputed=False,
    )

    # Main file should exist
    assert out_path.is_file()

    # No budget files should be generated
    budget_4k = tmp_path / "output.4k.json"
    budget_16k = tmp_path / "output.16k.json"
    budget_64k = tmp_path / "output.64k.json"

    assert not budget_4k.exists(), "4k budget file should NOT be generated when budgets=none"
    assert not budget_16k.exists(), "16k budget file should NOT be generated when budgets=none"
    assert not budget_64k.exists(), "64k budget file should NOT be generated when budgets=none"


def test_run_behavior_map_budgets_default_keyword(tmp_path):
    """budgets='default' generates standard budget files."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "output.json"
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        budgets="default",  # Explicit default
        include_sketch_precomputed=False,
    )

    # Default budget files should be generated
    budget_4k = tmp_path / "output.4k.json"
    budget_16k = tmp_path / "output.16k.json"
    budget_64k = tmp_path / "output.64k.json"

    assert budget_4k.is_file(), "4k budget file should be generated"
    assert budget_16k.is_file(), "16k budget file should be generated"
    assert budget_64k.is_file(), "64k budget file should be generated"


def test_run_behavior_map_budgets_invalid_spec_skipped(tmp_path):
    """Invalid budget specs are silently skipped, valid ones still work."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def main(): pass\n")

    out_path = tmp_path / "output.json"
    # Mix valid and invalid budget specs
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        budgets="4k,invalid_budget,16k",  # Invalid spec in the middle
        include_sketch_precomputed=False,
    )

    # Main file should exist
    assert out_path.is_file()

    # Valid budget files should be generated
    budget_4k = tmp_path / "output.4k.json"
    budget_16k = tmp_path / "output.16k.json"

    assert budget_4k.is_file(), "4k budget file should be generated"
    assert budget_16k.is_file(), "16k budget file should be generated"

    # Invalid budget file should NOT exist
    budget_invalid = tmp_path / "output.invalid_budget.json"
    assert not budget_invalid.exists(), "Invalid budget file should NOT be generated"

