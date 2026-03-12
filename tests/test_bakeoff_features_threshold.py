# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for bakeoff-features assess_metric threshold scaling for small repos."""

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path


def _load_bakeoff_features():
    """Import scripts/bakeoff-features as a module despite no .py extension."""
    script_path = str(
        Path(__file__).resolve().parent.parent / "scripts" / "bakeoff-features"
    )
    loader = importlib.machinery.SourceFileLoader("bakeoff_features", script_path)
    spec = importlib.util.spec_from_loader("bakeoff_features", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


bf = _load_bakeoff_features()


class TestAssessMetricScaling:
    """Test that avg_slice_nodes threshold scales for small repos."""

    def test_small_repo_8_nodes_not_warned(self):
        """A repo with 72 nodes and avg_slice of 8.5 should not WARN."""
        status, msg = bf.assess_metric("avg_slice_nodes", 8.5, total_nodes=72)
        assert status == "GOOD", f"Expected GOOD for small repo, got {status}: {msg}"

    def test_large_repo_8_nodes_warned(self):
        """A repo with 5000 nodes and avg_slice of 8.5 should WARN."""
        status, msg = bf.assess_metric("avg_slice_nodes", 8.5, total_nodes=5000)
        assert status == "WARN"

    def test_no_total_nodes_uses_default(self):
        """Without total_nodes, the fixed threshold of 20 applies."""
        status, msg = bf.assess_metric("avg_slice_nodes", 8.5)
        assert status == "WARN"

    def test_medium_repo_scales_threshold(self):
        """A repo with 150 nodes: threshold = max(5, 150*0.1) = 15."""
        # 14 should WARN (below 15)
        status, msg = bf.assess_metric("avg_slice_nodes", 14, total_nodes=150)
        assert status == "WARN"
        # 16 should be GOOD (above 15)
        status, msg = bf.assess_metric("avg_slice_nodes", 16, total_nodes=150)
        assert status == "GOOD"

    def test_large_repo_uses_fixed_threshold(self):
        """A repo with 1000+ nodes uses the fixed threshold of 20."""
        status, msg = bf.assess_metric("avg_slice_nodes", 19, total_nodes=1000)
        assert status == "WARN"
        status, msg = bf.assess_metric("avg_slice_nodes", 21, total_nodes=1000)
        assert status == "GOOD"

    def test_very_small_repo_floor(self):
        """Threshold never goes below warn_min (5)."""
        status, msg = bf.assess_metric("avg_slice_nodes", 4, total_nodes=30)
        assert status != "GOOD", "Should not be GOOD when below floor of 5"

    def test_non_slice_metrics_unaffected(self):
        """Other metrics ignore total_nodes."""
        status, msg = bf.assess_metric("orphan_rate", 10, total_nodes=72)
        assert status == "GOOD"

    def test_scaling_boundary_200_nodes(self):
        """At 200 nodes: threshold = max(5, 200*0.1) = 20, same as default."""
        status, msg = bf.assess_metric("avg_slice_nodes", 19, total_nodes=200)
        assert status == "WARN"
        status, msg = bf.assess_metric("avg_slice_nodes", 21, total_nodes=200)
        assert status == "GOOD"
