"""Tests for --max-tier CLI flag.

Tests for filtering analysis scope by supply chain tier:
- Default (no flag): Excludes derived/minified (tier 4)
- --max-tier 1: First-party only
- --max-tier 2: First-party + internal deps (examples, workspaces)
- --max-tier 3: All except derived artifacts (same as default)
- --max-tier 4: All including derived/minified
"""

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import build_parser, run_behavior_map


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

    def test_default_excludes_derived(self, mixed_tier_repo: Path, tmp_path: Path):
        """Without --max-tier, derived (tier 4) is excluded by default."""
        out_path = tmp_path / "results.json"
        run_behavior_map(mixed_tier_repo, out_path, include_sketch_precomputed=False)

        data = json.loads(out_path.read_text())
        nodes = data["nodes"]

        # Should have nodes from tiers 1-3 but NOT tier 4
        for node in nodes:
            tier = node.get("supply_chain", {}).get("tier", 1)
            assert tier <= 3, f"Found tier {tier} node: {node['name']}"

    def test_max_tier_4_includes_all(self, mixed_tier_repo: Path, tmp_path: Path):
        """--max-tier 4 includes all tiers including derived."""
        out_path = tmp_path / "results.json"
        run_behavior_map(mixed_tier_repo, out_path, max_tier=4, include_sketch_precomputed=False)

        data = json.loads(out_path.read_text())
        nodes = data["nodes"]

        # Should have nodes from multiple tiers including tier 4
        tiers = {n.get("supply_chain", {}).get("tier", 1) for n in nodes}
        assert len(tiers) > 1  # Has nodes from multiple tiers

    def test_max_tier_1_only_first_party(self, mixed_tier_repo: Path, tmp_path: Path):
        """--max-tier 1 includes only first-party code."""
        out_path = tmp_path / "results.json"
        run_behavior_map(mixed_tier_repo, out_path, max_tier=1, include_sketch_precomputed=False)

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
        run_behavior_map(mixed_tier_repo, out_path, max_tier=2, include_sketch_precomputed=False)

        data = json.loads(out_path.read_text())
        nodes = data["nodes"]

        # All nodes should be tier 1 or 2
        for node in nodes:
            tier = node.get("supply_chain", {}).get("tier", 1)
            assert tier <= 2, f"Found tier {tier} node: {node['name']}"

    def test_max_tier_3_excludes_derived(self, mixed_tier_repo: Path, tmp_path: Path):
        """--max-tier 3 excludes derived artifacts (tier 4)."""
        out_path = tmp_path / "results.json"
        run_behavior_map(mixed_tier_repo, out_path, max_tier=3, include_sketch_precomputed=False)

        data = json.loads(out_path.read_text())
        nodes = data["nodes"]

        # No tier 4 nodes
        for node in nodes:
            tier = node.get("supply_chain", {}).get("tier", 1)
            assert tier <= 3, f"Found tier {tier} node: {node['name']}"

    def test_filtered_edges_removed(self, mixed_tier_repo: Path, tmp_path: Path):
        """Edges with src referencing filtered nodes should be removed."""
        # Get all node IDs before filtering
        out_all = tmp_path / "all.json"
        run_behavior_map(
            mixed_tier_repo, out_all, max_tier=4, include_sketch_precomputed=False
        )
        all_node_ids = {
            n["id"] for n in json.loads(out_all.read_text())["nodes"]
        }

        out_path = tmp_path / "results.json"
        run_behavior_map(
            mixed_tier_repo, out_path, max_tier=1, include_sketch_precomputed=False
        )

        data = json.loads(out_path.read_text())
        node_ids = {n["id"] for n in data["nodes"]}
        removed_ids = all_node_ids - node_ids

        for edge in data["edges"]:
            # src must be a kept node or file-level ref
            assert edge["src"] in node_ids or edge["src"].endswith(
                ":file"
            ) or ":file:" in edge["src"], (
                f"Edge src {edge['src']} references filtered node"
            )
            # dst must not reference a node that was explicitly removed
            assert edge["dst"] not in removed_ids, (
                f"Edge dst {edge['dst']} references filtered node"
            )

    def test_cross_tier_edges_filtered_on_dst(self, tmp_path: Path):
        """Edges whose dst references a tier-filtered node must be removed.

        When a first-party function calls a function in a derived (tier 4)
        file, both nodes exist pre-filter. After filtering to tier <= 3,
        the tier-4 node is removed. Edges pointing to it must also be removed;
        otherwise they create dangling references in the output.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        src = repo / "src"
        src.mkdir()
        # First-party code that calls a function with same name as one in dist/
        (src / "app.js").write_text(
            "const utils = require('../dist/utils');\n"
            "\n"
            "function main() {\n"
            "  return utils.format('hello');\n"
            "}\n"
            "\n"
            "module.exports = { main };\n"
        )

        # Tier 4: derived/minified code that defines the same function
        dist = repo / "dist"
        dist.mkdir()
        (dist / "utils.js").write_text(
            "function format(s){return s.trim()}\n"
            "module.exports={format:format};\n"
        )

        # Get all edges before filtering (max_tier=4)
        out_all = tmp_path / "all.json"
        run_behavior_map(
            repo, out_all, max_tier=4, include_sketch_precomputed=False
        )
        data_all = json.loads(out_all.read_text())
        all_node_ids = {n["id"] for n in data_all["nodes"]}
        tier4_ids = {
            n["id"]
            for n in data_all["nodes"]
            if n.get("supply_chain", {}).get("tier", 1) == 4
        }

        # Now filter to tier <= 3 (default)
        out_path = tmp_path / "filtered.json"
        run_behavior_map(
            repo, out_path, include_sketch_precomputed=False
        )

        data = json.loads(out_path.read_text())
        filtered_node_ids = {n["id"] for n in data["nodes"]}

        # No tier-4 nodes should be in filtered output
        assert not (filtered_node_ids & tier4_ids), (
            "Tier-4 nodes should be filtered out"
        )

        # No edge should point to a removed node
        removed_ids = all_node_ids - filtered_node_ids
        for edge in data["edges"]:
            assert edge["dst"] not in removed_ids, (
                f"Edge dst {edge['dst']} references a tier-filtered node"
            )

    def test_metrics_reflect_filtered_data(
        self, mixed_tier_repo: Path, tmp_path: Path
    ):
        """Metrics should be computed after filtering."""
        out_path = tmp_path / "results.json"
        run_behavior_map(mixed_tier_repo, out_path, max_tier=1, include_sketch_precomputed=False)

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
        run_behavior_map(mixed_tier_repo, out_path, max_tier=1, include_sketch_precomputed=False)

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
        run_behavior_map(mixed_tier_repo, out_path, max_tier=1, include_sketch_precomputed=False)

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
        run_behavior_map(tmp_path, out_path, max_tier=1, include_sketch_precomputed=False)

        data = json.loads(out_path.read_text())
        limits = data.get("limits", {})

        # Should indicate tier filtering was applied
        assert limits.get("max_tier_applied") == 1


class TestDocKindFiltering:
    """Test default exclusion of documentation/config node kinds.

    Non-code kinds (section, table, table_array, code_block, link, paragraph,
    label, heading, setting, config) are excluded by default to reduce noise.
    Use include_docs=True to include them.
    """

    @pytest.fixture()
    def repo_with_docs(self, tmp_path: Path) -> Path:
        """Create a repo with both code and documentation files."""
        repo = tmp_path / "repo"
        repo.mkdir()
        # Python code file (generates function nodes)
        (repo / "app.py").write_text(
            "def main():\n"
            "    pass\n"
        )
        # Markdown doc file (generates section nodes)
        (repo / "README.md").write_text(
            "# Getting Started\n"
            "\n"
            "## Installation\n"
            "\n"
            "Run `pip install app`.\n"
            "\n"
            "## Usage\n"
            "\n"
            "Call `main()`.\n"
        )
        return repo

    def test_default_excludes_doc_kinds(
        self, repo_with_docs: Path, tmp_path: Path
    ) -> None:
        """By default, documentation kinds (section, etc.) are excluded."""
        out_path = tmp_path / "results.json"
        run_behavior_map(
            repo_with_docs, out_path, include_sketch_precomputed=False
        )
        data = json.loads(out_path.read_text())
        doc_kinds = {"section", "table", "table_array", "code_block",
                     "link", "paragraph", "label", "heading",
                     "setting", "config"}
        for node in data["nodes"]:
            assert node["kind"] not in doc_kinds, (
                f"Doc kind '{node['kind']}' should be excluded by default: "
                f"{node['name']}"
            )

    def test_include_docs_includes_doc_kinds(
        self, repo_with_docs: Path, tmp_path: Path
    ) -> None:
        """include_docs=True includes documentation kinds."""
        out_path = tmp_path / "results.json"
        run_behavior_map(
            repo_with_docs, out_path, include_docs=True,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        kinds = {n["kind"] for n in data["nodes"]}
        assert "section" in kinds, (
            "With include_docs=True, section nodes should be present"
        )


class TestCssNoiseFiltering:
    """Test default exclusion of CSS structural node kinds.

    CSS selector kinds (class_selector, id_selector, rule_set, property,
    media, keyframes, font_face, variable) are degree-0 noise that add
    no architectural insight. They are excluded by default alongside
    documentation/config kinds.
    """

    @pytest.fixture()
    def repo_with_css(self, tmp_path: Path) -> Path:
        """Create a repo with both code and CSS files."""
        repo = tmp_path / "repo"
        repo.mkdir()
        # Python code file (generates function nodes)
        (repo / "app.py").write_text(
            "def main():\n"
            "    pass\n"
        )
        # CSS file (generates class_selector, id_selector, etc.)
        (repo / "styles.css").write_text(
            ".button {\n"
            "    color: blue;\n"
            "}\n"
            "\n"
            "#header {\n"
            "    font-size: 16px;\n"
            "}\n"
        )
        return repo

    def test_default_excludes_css_selector_kinds(
        self, repo_with_css: Path, tmp_path: Path
    ) -> None:
        """By default, CSS selector kinds are excluded."""
        out_path = tmp_path / "results.json"
        run_behavior_map(
            repo_with_css, out_path, include_sketch_precomputed=False
        )
        data = json.loads(out_path.read_text())
        css_noise_kinds = {
            "class_selector", "id_selector", "rule_set",
            "property", "media", "keyframes", "font_face",
        }
        for node in data["nodes"]:
            assert node["kind"] not in css_noise_kinds, (
                f"CSS kind '{node['kind']}' should be excluded by default: "
                f"{node['name']}"
            )

    def test_include_docs_includes_css_kinds(
        self, repo_with_css: Path, tmp_path: Path
    ) -> None:
        """include_docs=True also includes CSS structural kinds."""
        out_path = tmp_path / "results.json"
        run_behavior_map(
            repo_with_css, out_path, include_docs=True,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        kinds = {n["kind"] for n in data["nodes"]}
        assert "class_selector" in kinds, (
            "With include_docs=True, CSS class_selector nodes should be present"
        )


class TestConfigNoiseFiltering:
    """Test default exclusion of config-metadata node kinds.

    Kinds like 'pattern' (.gitignore entries), 'script' (npm scripts,
    pyproject.toml entry points), and 'requirement' (pip requirements.txt
    entries) are configuration metadata with no call graph edges. They are
    consistently degree-0 orphans across all tested repos and add noise
    without architectural insight.
    """

    @pytest.fixture()
    def repo_with_config_noise(self, tmp_path: Path) -> Path:
        """Create a repo with code, .gitignore, package.json, and requirements.txt."""
        repo = tmp_path / "repo"
        repo.mkdir()
        # Python code file (generates function nodes)
        (repo / "app.py").write_text(
            "def main():\n"
            "    pass\n"
        )
        # .gitignore file (generates pattern nodes)
        (repo / ".gitignore").write_text(
            "node_modules/\n"
            "*.pyc\n"
            "__pycache__/\n"
        )
        # package.json with scripts (generates script nodes)
        (repo / "package.json").write_text(
            '{\n'
            '  "name": "test-project",\n'
            '  "scripts": {\n'
            '    "test": "jest",\n'
            '    "build": "webpack"\n'
            '  }\n'
            '}\n'
        )
        # requirements.txt (generates requirement nodes)
        (repo / "requirements.txt").write_text(
            "flask>=2.0\n"
            "requests\n"
        )
        return repo

    def test_default_excludes_config_metadata_kinds(
        self, repo_with_config_noise: Path, tmp_path: Path
    ) -> None:
        """By default, pattern, script, and requirement kinds are excluded."""
        out_path = tmp_path / "results.json"
        run_behavior_map(
            repo_with_config_noise, out_path, include_sketch_precomputed=False
        )
        data = json.loads(out_path.read_text())
        config_noise_kinds = {"pattern", "script", "requirement"}
        for node in data["nodes"]:
            assert node["kind"] not in config_noise_kinds, (
                f"Config kind '{node['kind']}' should be excluded by default: "
                f"{node['name']}"
            )

    def test_include_docs_includes_config_metadata_kinds(
        self, repo_with_config_noise: Path, tmp_path: Path
    ) -> None:
        """include_docs=True also includes config-metadata kinds."""
        out_path = tmp_path / "results.json"
        run_behavior_map(
            repo_with_config_noise, out_path, include_docs=True,
            include_sketch_precomputed=False,
        )
        data = json.loads(out_path.read_text())
        kinds = {n["kind"] for n in data["nodes"]}
        # At least one of the config-metadata kinds should be present
        assert kinds & {"pattern", "script", "requirement"}, (
            "With include_docs=True, pattern/script/requirement nodes should be present"
        )
