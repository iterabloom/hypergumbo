# SPDX-License-Identifier: AGPL-3.0-or-later
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


class TestRoutePromotionFromDerived:
    """Test that route symbols in derived (tier 4) files are promoted to tier 2.

    Routes represent the API surface and should be visible regardless of
    whether the code is generated (e.g., go-swagger, protobuf gRPC stubs).
    """

    def test_route_symbol_promoted_from_tier_4(self, tmp_path: Path) -> None:
        """A route symbol in a generated file (tier 4) gets promoted to tier 2.

        Files with '// Code generated' or 'DO NOT EDIT' headers are classified
        as tier 4 by content heuristics. But route symbols in those files
        should be promoted because routes represent the API surface.
        """
        repo = tmp_path / "repo"
        src = repo / "src"
        src.mkdir(parents=True)
        (src / "main.py").write_text("def main(): pass\n")

        # Flask framework detection requires a requirements.txt
        (repo / "requirements.txt").write_text("flask\n")

        # Create a generated file with a route (mimics go-swagger output).
        # Must be in an analyzed directory (not dist/) but with generated header.
        api_dir = repo / "api" / "v2"
        api_dir.mkdir(parents=True)
        (api_dir / "api.py").write_text(
            "# Code generated by go-swagger; DO NOT EDIT.\n"
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "\n"
            "@app.get('/health')\n"
            "def health():\n"
            "    return 'ok'\n"
        )

        out_path = tmp_path / "results.json"
        # Default tier (3) should include the route even though file is generated
        run_behavior_map(repo, out_path, include_sketch_precomputed=False)

        data = json.loads(out_path.read_text())
        nodes = data["nodes"]

        # The route symbol should be present (promoted from tier 4 to tier 2)
        route_nodes = [
            n for n in nodes
            if n.get("kind") == "route"
            or any(
                isinstance(c, dict) and c.get("concept") == "route"
                for c in (n.get("meta") or {}).get("concepts", [])
            )
        ]
        promoted = [
            n for n in route_nodes
            if n.get("supply_chain", {}).get("reason") == "route promoted from derived"
        ]
        assert len(promoted) >= 1, (
            f"Expected at least 1 route promoted from derived, "
            f"found {len(promoted)}. Route nodes: {[n.get('name') for n in route_nodes]}"
        )
        for n in promoted:
            assert n["supply_chain"]["tier"] == 2

    def test_non_route_symbol_in_generated_stays_tier_4(self, tmp_path: Path) -> None:
        """Non-route symbols in generated files remain tier 4 and are filtered."""
        repo = tmp_path / "repo"
        src = repo / "src"
        src.mkdir(parents=True)
        (src / "main.py").write_text("def main(): pass\n")

        # Generated file without routes
        api_dir = repo / "api" / "v2"
        api_dir.mkdir(parents=True)
        (api_dir / "util.py").write_text(
            "# Code generated by go-swagger; DO NOT EDIT.\n"
            "def helper(): pass\n"
        )

        out_path = tmp_path / "results.json"
        run_behavior_map(repo, out_path, include_sketch_precomputed=False)

        data = json.loads(out_path.read_text())
        nodes = data["nodes"]

        # No tier-4 non-route node should be in the output
        for n in nodes:
            if "api/v2/" in n.get("path", ""):
                tier = n.get("supply_chain", {}).get("tier", 1)
                assert tier <= 3, (
                    f"Non-route symbol in generated file should remain tier 4 "
                    f"and be filtered, but {n['name']} has tier {tier}"
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
    media, keyframes, font_face) and CSS custom property / SCSS variable
    kinds ("variable") are degree-0 noise that add no architectural
    insight.  They are excluded by default alongside documentation/config
    kinds.
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
        # CSS file (generates class_selector, id_selector, variable, etc.)
        (repo / "styles.css").write_text(
            ":root {\n"
            "    --primary-color: blue;\n"
            "}\n"
            "\n"
            ".button {\n"
            "    color: var(--primary-color);\n"
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
            "variable",
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


class TestFileKindRankingSuppression:
    """WI-ramuv: file-kind Symbols are kept in the graph but suppressed from ranking.

    The orchestrator post-process synthesizes one ``kind="file"`` Symbol
    per analyzed source file (replacing dangling ``make_file_id`` boundary
    pseudo-IDs). Because each file's in-degree equals its import count
    (5-50 typical for Python), they would deterministically displace
    every real ranked symbol on a polyglot repo. The ranking suppression
    keeps them in the graph (so containment, slice traversal, and per-
    file metrics still see them) but zeros their centrality so they
    never displace real functions/classes in the ranked output.
    """

    @pytest.fixture()
    def repo_with_imports(self, tmp_path: Path) -> Path:
        """A small Python repo whose import edges synthesize file Symbols."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "common.py").write_text("def helper():\n    return 1\n")
        (repo / "app.py").write_text(
            "from common import helper\n"
            "def main():\n"
            "    return helper()\n"
        )
        return repo

    def test_file_kind_symbols_are_kept_in_output(
        self, repo_with_imports: Path, tmp_path: Path
    ) -> None:
        """Synthesized kind="file" Symbols stay in the JSON (graph
        containment, slice traversal, and per-file metrics rely on them)."""
        out_path = tmp_path / "results.json"
        run_behavior_map(
            repo_with_imports, out_path, include_sketch_precomputed=False
        )
        data = json.loads(out_path.read_text())
        kinds = {n["kind"] for n in data["nodes"]}
        assert "file" in kinds, (
            "kind=file Symbols should be present in the JSON output "
            "(needed for slice / containment); WI-ramuv suppresses them "
            "from ranking, not from the graph."
        )

    def test_top_ranked_symbol_is_not_kind_file(
        self, repo_with_imports: Path, tmp_path: Path
    ) -> None:
        """The most-central Symbol in the fixture is a real function, not a
        synthesized kind="file" Symbol whose centrality is suppressed."""
        out_path = tmp_path / "results.json"
        run_behavior_map(
            repo_with_imports, out_path, include_sketch_precomputed=False
        )
        data = json.loads(out_path.read_text())
        nodes = data["nodes"]
        # The fixture has at least one real function (helper) with an
        # incoming call edge — its centrality is > 0 and dominates the
        # suppressed-to-0 kind="file" entries.
        assert any(n["kind"] == "file" for n in nodes), (
            "expected synthesized file Symbol(s) in the fixture"
        )
        assert nodes[0]["kind"] != "file", (
            f"rank-0 Symbol should be a real function/class, not kind=file: "
            f"got {nodes[0]['kind']} ({nodes[0]['id']})"
        )

    def test_file_kind_ranks_below_real_symbol_with_incoming_edges(
        self, repo_with_imports: Path, tmp_path: Path
    ) -> None:
        """A real function with incoming call edges always outranks every
        kind="file" Symbol, regardless of how many file Symbols exist."""
        out_path = tmp_path / "results.json"
        run_behavior_map(
            repo_with_imports, out_path, include_sketch_precomputed=False
        )
        data = json.loads(out_path.read_text())
        nodes = data["nodes"]
        helper_idx = next(
            (i for i, n in enumerate(nodes) if n["name"] == "helper"), None
        )
        first_file_idx = next(
            (i for i, n in enumerate(nodes) if n["kind"] == "file"), None
        )
        assert helper_idx is not None, "fixture's helper() must be present"
        assert first_file_idx is not None, "synthesized file Symbol expected"
        assert helper_idx < first_file_idx, (
            "helper() (incoming call from main) must outrank every "
            "synthesized kind=file Symbol"
        )
