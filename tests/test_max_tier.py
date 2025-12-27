"""Tests for --max-tier CLI flag.

Tests for filtering analysis scope by supply chain tier:
- --max-tier 1: First-party only
- --max-tier 2: First-party + internal deps (examples, workspaces)
- --max-tier 3: All except derived artifacts
- --max-tier 4: All (default, no filtering)
"""

import json
from pathlib import Path

import pytest

from hypergumbo.cli import build_parser, run_behavior_map


class TestMaxTierParser:
    """Test --max-tier argument parsing."""

    def test_run_has_max_tier_argument(self):
        """Run command should accept --max-tier."""
        parser = build_parser()
        args = parser.parse_args(["run", ".", "--max-tier", "1"])
        assert args.max_tier == 1

    def test_max_tier_default_is_none(self):
        """Default max-tier should be None (no filtering)."""
        parser = build_parser()
        args = parser.parse_args(["run", "."])
        assert args.max_tier is None

    def test_max_tier_accepts_values_1_to_4(self):
        """max-tier should accept values 1, 2, 3, 4."""
        parser = build_parser()
        for tier in [1, 2, 3, 4]:
            args = parser.parse_args(["run", ".", "--max-tier", str(tier)])
            assert args.max_tier == tier

    def test_max_tier_invalid_value_rejected(self):
        """max-tier should reject invalid values."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["run", ".", "--max-tier", "5"])
        with pytest.raises(SystemExit):
            parser.parse_args(["run", ".", "--max-tier", "0"])
        with pytest.raises(SystemExit):
            parser.parse_args(["run", ".", "--max-tier", "abc"])

    def test_first_party_only_flag(self):
        """--first-party-only should be alias for --max-tier 1."""
        parser = build_parser()
        args = parser.parse_args(["run", ".", "--first-party-only"])
        assert args.max_tier == 1


class TestMaxTierFiltering:
    """Test tier-based filtering in behavior map output."""

    @pytest.fixture
    def mixed_tier_repo(self, tmp_path: Path) -> Path:
        """Create a repo with files at different supply chain tiers."""
        # Tier 1: first-party source
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("""
def main():
    '''Main entry point.'''
    helper()

def helper():
    '''Helper function.'''
    pass
""")

        # Tier 2: examples (internal dep)
        examples = tmp_path / "examples"
        examples.mkdir()
        (examples / "demo.py").write_text("""
def demo():
    '''Demo function.'''
    pass
""")

        # Tier 3: external deps
        node_modules = tmp_path / "node_modules" / "lodash"
        node_modules.mkdir(parents=True)
        (node_modules / "index.js").write_text("""
function chunk(arr, size) {
    // Split array into chunks
}

module.exports = { chunk };
""")

        # Tier 4: derived/minified
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "bundle.js").write_text("var a=1,b=2;")

        return tmp_path

    def test_no_max_tier_includes_all(self, mixed_tier_repo: Path, tmp_path: Path):
        """Without --max-tier, all tiers are included."""
        out_path = tmp_path / "results.json"
        run_behavior_map(mixed_tier_repo, out_path)

        data = json.loads(out_path.read_text())
        nodes = data["nodes"]

        # Should have nodes from multiple tiers
        tiers = {n.get("supply_chain", {}).get("tier", 1) for n in nodes}
        assert len(tiers) > 1  # Has nodes from multiple tiers

    def test_max_tier_1_only_first_party(self, mixed_tier_repo: Path, tmp_path: Path):
        """--max-tier 1 includes only first-party code."""
        out_path = tmp_path / "results.json"
        run_behavior_map(mixed_tier_repo, out_path, max_tier=1)

        data = json.loads(out_path.read_text())
        nodes = data["nodes"]

        # All nodes should be tier 1
        for node in nodes:
            tier = node.get("supply_chain", {}).get("tier", 1)
            assert tier <= 1, f"Found tier {tier} node: {node['name']}"

        # Should have first-party nodes
        paths = {n["path"] for n in nodes}
        assert any("src/" in p for p in paths)

    def test_max_tier_2_includes_internal_deps(
        self, mixed_tier_repo: Path, tmp_path: Path
    ):
        """--max-tier 2 includes first-party and internal deps (examples)."""
        out_path = tmp_path / "results.json"
        run_behavior_map(mixed_tier_repo, out_path, max_tier=2)

        data = json.loads(out_path.read_text())
        nodes = data["nodes"]

        # All nodes should be tier 1 or 2
        for node in nodes:
            tier = node.get("supply_chain", {}).get("tier", 1)
            assert tier <= 2, f"Found tier {tier} node: {node['name']}"

    def test_max_tier_3_excludes_derived(self, mixed_tier_repo: Path, tmp_path: Path):
        """--max-tier 3 excludes derived artifacts (tier 4)."""
        out_path = tmp_path / "results.json"
        run_behavior_map(mixed_tier_repo, out_path, max_tier=3)

        data = json.loads(out_path.read_text())
        nodes = data["nodes"]

        # No tier 4 nodes
        for node in nodes:
            tier = node.get("supply_chain", {}).get("tier", 1)
            assert tier <= 3, f"Found tier {tier} node: {node['name']}"

    def test_filtered_edges_removed(self, mixed_tier_repo: Path, tmp_path: Path):
        """Edges referencing filtered nodes should be removed."""
        out_path = tmp_path / "results.json"
        run_behavior_map(mixed_tier_repo, out_path, max_tier=1)

        data = json.loads(out_path.read_text())
        nodes = data["nodes"]
        edges = data["edges"]

        node_ids = {n["id"] for n in nodes}

        # All edge endpoints should reference existing nodes
        for edge in edges:
            assert edge["src"] in node_ids or edge["src"].endswith(
                ".py"
            ), f"Edge src {edge['src']} references filtered node"
            # dst might reference external modules or filtered symbols

    def test_metrics_reflect_filtered_data(
        self, mixed_tier_repo: Path, tmp_path: Path
    ):
        """Metrics should be computed after filtering."""
        out_path = tmp_path / "results.json"
        run_behavior_map(mixed_tier_repo, out_path, max_tier=1)

        data = json.loads(out_path.read_text())
        metrics = data["metrics"]
        nodes = data["nodes"]

        # Node count in metrics should match actual nodes
        assert metrics["total_nodes"] == len(nodes)

    def test_supply_chain_summary_reflects_filtered(
        self, mixed_tier_repo: Path, tmp_path: Path
    ):
        """Supply chain summary should reflect filtered data."""
        out_path = tmp_path / "results.json"
        run_behavior_map(mixed_tier_repo, out_path, max_tier=1)

        data = json.loads(out_path.read_text())
        summary = data["supply_chain_summary"]

        # With max_tier=1, should only have first_party counts
        # (Other tiers might be 0 or not present)
        first_party = summary.get("first_party", {})
        internal = summary.get("internal_dep", {})
        external = summary.get("external_dep", {})

        # Verify filtering worked - internal/external should have 0 or small counts
        assert internal.get("symbols", 0) == 0
        assert external.get("symbols", 0) == 0

    def test_file_level_import_edges_preserved(
        self, mixed_tier_repo: Path, tmp_path: Path
    ):
        """File-level import edges (with :file: in src) should pass tier filter.

        When filtering by tier, import edges with file-level sources should
        be preserved even if the file node itself isn't in the filtered set.
        These edges have sources like "python:path/file.py:1-1:file:file".
        """
        # Add an import statement to generate a file-level import edge
        src_main = mixed_tier_repo / "src" / "main.py"
        src_main.write_text("""
import os

def main():
    '''Main entry point.'''
    os.getcwd()
""")

        out_path = tmp_path / "results.json"
        run_behavior_map(mixed_tier_repo, out_path, max_tier=1)

        data = json.loads(out_path.read_text())
        edges = data["edges"]

        # Should have import edges preserved (src contains :file:)
        import_edges = [e for e in edges if e["type"] == "imports"]
        # Import edges should pass through even with tier filtering
        # The source has pattern like "python:...file:file"
        assert any(":file" in e["src"] for e in import_edges), (
            "Import edges with file-level sources should be preserved"
        )


class TestMaxTierLimitsReporting:
    """Test that tier filtering is reported in limits."""

    def test_limits_reports_tier_filter(self, tmp_path: Path):
        """Limits should report when tier filtering is applied."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("def main(): pass")

        out_path = tmp_path / "results.json"
        run_behavior_map(tmp_path, out_path, max_tier=1)

        data = json.loads(out_path.read_text())
        limits = data.get("limits", {})

        # Should indicate tier filtering was applied
        assert limits.get("max_tier_applied") == 1
