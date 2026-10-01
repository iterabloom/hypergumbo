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

    def test_by_tier_edges_src_vs_edges_incident(self) -> None:
        """WI-modom: per-tier ``edges`` is source-tier (sinks read ~0); the new
        ``edges_incident`` exposes each tier's graph contribution (either-endpoint).
        """
        nodes = [
            {"id": "a", "language": "python",
             "supply_chain": {"tier_name": "first_party"}},
            {"id": "a2", "language": "python",
             "supply_chain": {"tier_name": "first_party"}},
            {"id": "dep", "language": "python",
             "supply_chain": {"tier_name": "external_dep"}},
        ]
        # Two first-party -> external_dep calls (dep is a pure SINK), one
        # first-party -> first-party call.
        edges = [
            {"id": "e1", "src": "a", "dst": "dep"},
            {"id": "e2", "src": "a2", "dst": "dep"},
            {"id": "e3", "src": "a", "dst": "a2"},
        ]
        tiers = compute_metrics(nodes=nodes, edges=edges)["by_supply_chain_tier"]

        # ``edges`` (source-tier): all 3 originate in first_party; external_dep
        # is a pure sink -> 0 (the misleading "no contribution" reading).
        assert tiers["first_party"]["edges"] == 3
        assert tiers["external_dep"]["edges"] == 0
        # ``edges_incident`` (either-endpoint, distinct): external_dep is incident
        # on e1+e2 -> 2; first_party on all 3 (e3 same-tier counts once).
        assert tiers["external_dep"]["edges_incident"] == 2
        assert tiers["first_party"]["edges_incident"] == 3
        # ``edges`` reconciles to the source-resolved total; ``edges_incident``
        # does not (cross-tier edges double-count by design).
        assert sum(t["edges"] for t in tiers.values()) == 3
        assert sum(t["edges_incident"] for t in tiers.values()) == 5

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

    def test_edge_confidence_histogram_exposes_spikes(self) -> None:
        """WI-zimor: the distribution behind ``avg_confidence`` is published.

        Edge confidence is a handful of per-evidence-type literals, so the
        mean lands between spikes on a value no edge carries. The histogram
        (keyed by value at 0.01 resolution, ascending) shows the spikes; the
        median is a value an edge actually carries.
        """
        edges = (
            [{"id": f"a{i}", "confidence": 0.4} for i in range(3)]
            + [{"id": f"b{i}", "confidence": 0.85} for i in range(5)]
            + [{"id": f"c{i}", "confidence": 0.95} for i in range(2)]
        )
        metrics = compute_metrics(nodes=[], edges=edges)

        dist = metrics["edge_confidence"]
        assert dist["histogram"] == {"0.40": 3, "0.85": 5, "0.95": 2}
        assert list(dist["histogram"]) == ["0.40", "0.85", "0.95"]
        assert dist["median"] == 0.85
        # The scalar mean (0.735) is not a value any edge carries -- the
        # defect the histogram exists to make visible.
        assert metrics["avg_confidence"] == pytest.approx(0.735)
        assert f"{metrics['avg_confidence']:.2f}" not in dist["histogram"]

    def test_edge_confidence_median_is_a_carried_value(self) -> None:
        """WI-zimor: with an even count the median is the LOWER middle value
        (a value some edge carries), never the midpoint between two spikes."""
        edges = [
            {"id": "e1", "confidence": 0.4},
            {"id": "e2", "confidence": 0.4},
            {"id": "e3", "confidence": 0.9},
            {"id": "e4", "confidence": 0.9},
        ]
        dist = compute_metrics(nodes=[], edges=edges)["edge_confidence"]
        assert dist["median"] == 0.4

    def test_edge_confidence_histogram_is_bounded_for_computed_values(
        self,
    ) -> None:
        """WI-zimor: some producers multiply confidences (e.g. 0.85 * lookup *
        penalty), so raw values are not a closed set. Keys are the value at
        0.01 resolution, so the histogram has at most 101 keys on [0, 1]."""
        edges = [
            {"id": "e1", "confidence": 0.85 * 0.9 * 0.5},  # 0.38250000000000006
            {"id": "e2", "confidence": 0.3825},
            {"id": "e3", "confidence": 0.381},
        ]
        dist = compute_metrics(nodes=[], edges=edges)["edge_confidence"]
        assert dist["histogram"] == {"0.38": 3}
        assert dist["median"] == pytest.approx(0.3825, abs=0.001)

    def test_edge_confidence_counts_only_edges_carrying_confidence(
        self,
    ) -> None:
        """WI-zimor: the histogram covers exactly the edges ``avg_confidence``
        averages -- edges carrying a ``confidence`` key. Nodes carry no
        confidence and contribute nothing."""
        nodes = [{"id": "n1", "language": "python", "confidence": 0.1}]
        edges = [
            {"id": "e1"},
            {"id": "e2", "confidence": 0.8},
        ]
        metrics = compute_metrics(nodes=nodes, edges=edges)
        dist = metrics["edge_confidence"]
        assert dist["histogram"] == {"0.80": 1}
        assert dist["median"] == 0.8
        assert metrics["avg_confidence"] == 0.8

    def test_edge_confidence_empty_says_nothing_was_measured(self) -> None:
        """WI-zimor: with no confidence-bearing edge there is no median (null,
        not 0.0) and the histogram is empty, so a reader can tell the
        ``avg_confidence`` 0.0 placeholder from a measured mean."""
        dist = compute_metrics(nodes=[], edges=[])["edge_confidence"]
        assert dist == {"histogram": {}, "median": None}

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

    def test_files_counted_per_language(self) -> None:
        """WI-ninaj: metrics.languages.<lang>.files == count of file-kind nodes
        for that language (per-language rollup of the node-derived total_files),
        not an always-0 placeholder."""
        nodes = [
            {"id": "1", "language": "python", "kind": "file", "path": "a.py"},
            {"id": "2", "language": "python", "kind": "function", "path": "a.py"},
            {"id": "3", "language": "python", "kind": "file", "path": "b.py"},
            {"id": "4", "language": "javascript", "kind": "file", "path": "c.js"},
            {"id": "5", "language": "javascript", "kind": "class", "path": "c.js"},
        ]
        metrics = compute_metrics(nodes=nodes, edges=[])

        assert metrics["languages"]["python"]["files"] == 2  # a.py, b.py
        assert metrics["languages"]["javascript"]["files"] == 1  # c.js
        # per-language files sum to the total file-kind node count
        assert sum(l["files"] for l in metrics["languages"].values()) == 3

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

    def test_total_files_equals_node_distinct_paths(self) -> None:
        """INV-mozaf canonical re-definition (Phase 6 PR2): total_files is
        the count of distinct ``node.path`` values, NOT the profile-language
        files sum.

        The post-analysis number consumers see when they group nodes by
        path must match ``metrics.total_files``. The legacy WI-soraj
        profile-sum value (``sum(profile.languages[L].files)``) over-
        counts vs the node-distinct count by the number of files a
        profile counted but no analyzer Symbol-emitted for; it now rides
        in ``debug.profile_files_sum`` for introspection.
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

        # Canonical total_files = unique node paths = 3 (a.py, b.py, x.sh).
        assert metrics["total_files"] == 3
        assert metrics["debug"]["unique_paths_in_analysis"] == 3
        assert metrics["debug"]["analyzed_file_symbols"] == 2  # b.py + x.sh
        # Profile-sum rides in debug for introspection.
        assert metrics["debug"]["profile_files_sum"] == 8  # 5 python + 3 bash

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

    def test_total_files_excludes_external_sentinel(self) -> None:
        """INV-mozaf: the ``<external>`` sentinel path carried by
        external_symbol boundary nodes is not a real file — total_files (and
        debug.unique_paths_in_analysis) count only path-bearing source files,
        not the external placeholder."""
        nodes = [
            {"id": "1", "language": "python", "path": "src/a.py", "kind": "file"},
            {"id": "2", "language": "python", "path": "src/b.py", "kind": "file"},
            {"id": "3", "language": "python", "path": "<external>", "kind": "external_symbol"},
            {"id": "4", "language": "python", "path": "<external>", "kind": "external_symbol"},
        ]
        metrics = compute_metrics(nodes=nodes, edges=[])

        assert metrics["total_files"] == 2  # a.py, b.py — NOT <external>
        assert metrics["debug"]["unique_paths_in_analysis"] == 2
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

    def test_edges_with_unresolved_src_dont_mint_unknown_tier(self) -> None:
        """INV-jukok: edges whose ``src`` doesn't resolve to a known node
        must NOT mint an ``unknown`` tier entry in ``by_supply_chain_tier``.

        Pre-Phase-6, dangling-src edges added a phantom
        ``by_supply_chain_tier["unknown"]: {edges: N, nodes: 0}`` entry
        (23 such edges on self-analysis). Tier counts must reference a
        real classified node; edges without a known src are simply
        excluded from the tier-edge total.
        """
        nodes = [
            {
                "id": "1",
                "language": "python",
                "path": "src/a.py",
                "supply_chain": {"tier": 1, "tier_name": "first_party", "reason": "src/"},
            },
        ]
        edges = [
            {"id": "e1", "src": "1", "dst": "phantom", "confidence": 0.9},
            # phantom src — would have minted unknown tier pre-fix
            {"id": "e2", "src": "missing_node", "dst": "1", "confidence": 0.8},
        ]
        metrics = compute_metrics(nodes=nodes, edges=edges)
        # Only the first edge counts in first_party; the second is silently
        # excluded. No "unknown" key minted by edges alone.
        assert metrics["by_supply_chain_tier"]["first_party"]["edges"] == 1
        assert "unknown" not in metrics["by_supply_chain_tier"]

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
