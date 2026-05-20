# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for metrics computation."""
import pytest

from hypergumbo_core.metrics import compute_metrics


class TestComputeMetrics:
    """Tests for compute_metrics function."""

    def test_empty_nodes_and_edges(self) -> None:
        """Empty input produces zero counts."""
        metrics = compute_metrics(nodes=[], edges=[])

        assert metrics["total_nodes"] == 0
        assert metrics["total_edges"] == 0
        assert metrics["avg_confidence"] == 0.0
        assert metrics["languages"] == {}

    def test_counts_nodes_and_edges(self) -> None:
        """Counts total nodes and edges."""
        nodes = [
            {"id": "1", "language": "python"},
            {"id": "2", "language": "python"},
            {"id": "3", "language": "python"},
        ]
        edges = [
            {"id": "e1", "confidence": 0.9},
            {"id": "e2", "confidence": 0.8},
        ]

        metrics = compute_metrics(nodes=nodes, edges=edges)

        assert metrics["total_nodes"] == 3
        assert metrics["total_edges"] == 2

    def test_computes_avg_confidence(self) -> None:
        """Computes average edge confidence."""
        nodes = [{"id": "1", "language": "python"}]
        edges = [
            {"id": "e1", "confidence": 0.9},
            {"id": "e2", "confidence": 0.7},
            {"id": "e3", "confidence": 0.8},
        ]

        metrics = compute_metrics(nodes=nodes, edges=edges)

        assert metrics["avg_confidence"] == pytest.approx(0.8, rel=0.01)

    def test_groups_by_language(self) -> None:
        """Groups node and edge counts by language."""
        nodes = [
            {"id": "1", "language": "python", "path": "a.py"},
            {"id": "2", "language": "python", "path": "b.py"},
            {"id": "3", "language": "javascript", "path": "c.js"},
        ]
        # Edges inherit language from source node (simplified: use first node's lang)
        edges = [
            {"id": "e1", "src": "1", "confidence": 0.9},
            {"id": "e2", "src": "3", "confidence": 0.8},
        ]

        metrics = compute_metrics(nodes=nodes, edges=edges)

        assert metrics["languages"]["python"]["nodes"] == 2
        assert metrics["languages"]["javascript"]["nodes"] == 1

    def test_handles_missing_confidence(self) -> None:
        """Handles edges without confidence field."""
        nodes = [{"id": "1", "language": "python"}]
        edges = [
            {"id": "e1"},  # No confidence
            {"id": "e2", "confidence": 0.8},
        ]

        metrics = compute_metrics(nodes=nodes, edges=edges)

        # Should not crash, uses default or skips
        assert "avg_confidence" in metrics

    def test_total_files_equals_profile_sum(self) -> None:
        """WI-soraj: total_files is the canonical sum of profile.languages[*].files.

        The headline number consumers see (`metrics.total_files`) must
        agree with `sum(profile.languages[L].files)`. Introspection
        numbers (unique paths across all node symbols, count of
        file-kind Symbols) live in `metrics.debug`.
        """
        nodes = [
            {"id": "1", "language": "python", "path": "src/a.py", "kind": "function"},
            {"id": "2", "language": "python", "path": "src/a.py", "kind": "function"},
            {"id": "3", "language": "python", "path": "src/b.py", "kind": "file"},
            {"id": "4", "language": "bash", "path": "scripts/x.sh", "kind": "file"},
        ]
        profile = {
            "languages": {
                "python": {"files": 5, "loc": 1234},
                "bash": {"files": 3, "loc": 56},
            },
        }
        metrics = compute_metrics(nodes=nodes, edges=[], profile=profile)

        assert metrics["total_files"] == 8  # 5 python + 3 bash
        assert metrics["debug"]["unique_paths_in_analysis"] == 3  # a.py, b.py, x.sh
        assert metrics["debug"]["analyzed_file_symbols"] == 2  # b.py + x.sh

    def test_total_files_without_profile_falls_back_to_unique_paths(self) -> None:
        """When no profile is supplied, total_files falls back to the unique
        node-path count (preserves the legacy semantics for callers that
        compute metrics outside the full run_behavior_map pipeline)."""
        nodes = [
            {"id": "1", "language": "python", "path": "src/a.py"},
            {"id": "2", "language": "python", "path": "src/a.py"},
            {"id": "3", "language": "python", "path": "src/b.py"},
        ]
        metrics = compute_metrics(nodes=nodes, edges=[])

        assert metrics["total_files"] == 2
        # Debug block is still populated so consumers can introspect.
        assert metrics["debug"]["unique_paths_in_analysis"] == 2

    def test_groups_by_supply_chain_tier(self) -> None:
        """Groups node and edge counts by supply chain tier."""
        nodes = [
            {
                "id": "1",
                "language": "python",
                "path": "src/a.py",
                "supply_chain": {"tier": 1, "tier_name": "first_party", "reason": "src/"},
            },
            {
                "id": "2",
                "language": "python",
                "path": "src/b.py",
                "supply_chain": {"tier": 1, "tier_name": "first_party", "reason": "src/"},
            },
            {
                "id": "3",
                "language": "javascript",
                "path": "node_modules/pkg/index.js",
                "supply_chain": {"tier": 3, "tier_name": "external_dep", "reason": "node_modules/"},
            },
        ]
        edges = [
            {"id": "e1", "src": "1", "dst": "2", "confidence": 0.9},
            {"id": "e2", "src": "1", "dst": "3", "confidence": 0.8},
        ]

        metrics = compute_metrics(nodes=nodes, edges=edges)

        assert "by_supply_chain_tier" in metrics
        assert metrics["by_supply_chain_tier"]["first_party"]["nodes"] == 2
        assert metrics["by_supply_chain_tier"]["first_party"]["edges"] == 2
        assert metrics["by_supply_chain_tier"]["external_dep"]["nodes"] == 1
        assert metrics["by_supply_chain_tier"]["external_dep"]["edges"] == 0

    def test_supply_chain_tier_handles_missing_data(self) -> None:
        """Handles nodes without supply_chain field gracefully."""
        nodes = [
            {"id": "1", "language": "python", "path": "src/a.py"},  # No supply_chain
            {
                "id": "2",
                "language": "python",
                "path": "src/b.py",
                "supply_chain": {"tier": 1, "tier_name": "first_party", "reason": "src/"},
            },
        ]
        edges = []

        metrics = compute_metrics(nodes=nodes, edges=edges)

        # Should still work, unknown tier for nodes without supply_chain
        assert "by_supply_chain_tier" in metrics
        assert metrics["by_supply_chain_tier"]["first_party"]["nodes"] == 1
        assert metrics["by_supply_chain_tier"]["unknown"]["nodes"] == 1
