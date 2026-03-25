# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for bakeoff-deep assess_metric threshold scaling for small repos."""

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path


def _load_bakeoff_features():
    """Import scripts/bakeoff-deep as a module despite no .py extension."""
    script_path = str(
        Path(__file__).resolve().parent.parent / "scripts" / "bakeoff-deep"
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


class TestIoTagRateScaling:
    """Test io_tag_rate threshold scaling for repos of varying sizes."""

    def test_small_repo_scales_gently(self):
        """Repos with ~2K nodes get a gently scaled threshold (~3.7%).

        Scaling now starts at 500 nodes, so even small repos get some adjustment.
        """
        # 3.0% is below scaled threshold (~3.65%) at 2000 nodes
        status, msg = bf.assess_metric("io_tag_rate", 3.0, total_nodes=2000)
        assert status == "WARN", f"Expected WARN below scaled threshold: {msg}"
        # 4.0% is above scaled threshold
        status, msg = bf.assess_metric("io_tag_rate", 4.0, total_nodes=2000)
        assert status == "GOOD"

    def test_tiny_repo_no_scaling(self):
        """Repos under 500 nodes use the base 5% threshold."""
        status, msg = bf.assess_metric("io_tag_rate", 4.0, total_nodes=400)
        assert status == "WARN", f"Expected WARN below 5% for tiny repo: {msg}"
        status, msg = bf.assess_metric("io_tag_rate", 6.0, total_nodes=400)
        assert status == "GOOD"

    def test_mid_repo_scales_down(self):
        """Repos with 3K-10K nodes get scaled threshold.

        mitmproxy (7577 nodes, 2.5% io_tag_rate) should not WARN.
        """
        status, msg = bf.assess_metric("io_tag_rate", 2.5, total_nodes=7577)
        assert status == "GOOD", f"mitmproxy-like repo (7577 nodes, 2.5%) should be GOOD: {msg}"

    def test_tokenizers_sized_repo(self):
        """tokenizers (3050 nodes, 1.3%) gets a WARN but not FAIL.

        Small ML library doing minimal IO — low rate is legitimate, but the
        threshold appropriately flags it for investigation.
        """
        status, msg = bf.assess_metric("io_tag_rate", 1.3, total_nodes=3050)
        assert status == "WARN", f"tokenizers-like repo (3050 nodes, 1.3%) should WARN: {msg}"
        # But 3.5% at this size should be GOOD (above scaled threshold ~3.2%)
        status, msg = bf.assess_metric("io_tag_rate", 3.5, total_nodes=3050)
        assert status == "GOOD", f"3050 nodes at 3.5% should be GOOD: {msg}"

    def test_large_repo_scales_aggressively(self):
        """Repos with 50K+ nodes get deeply scaled threshold.

        netty (49K nodes, 0.9%) should not WARN.
        """
        status, msg = bf.assess_metric("io_tag_rate", 0.9, total_nodes=49162)
        assert status == "GOOD", f"netty-like repo (49K nodes, 0.9%) should be GOOD: {msg}"

    def test_very_large_repo(self):
        """Repos with 100K+ nodes have very low threshold but still have a floor."""
        # 0.3% should be GOOD for a 100K node repo
        status, msg = bf.assess_metric("io_tag_rate", 0.3, total_nodes=100000)
        assert status == "GOOD", f"100K node repo at 0.3% should be GOOD: {msg}"
        # But 0.05% should still FAIL (below warn_min)
        status, msg = bf.assess_metric("io_tag_rate", 0.05, total_nodes=100000)
        assert status != "GOOD", "0.05% should not be GOOD even for large repos"

    def test_zero_io_always_fails(self):
        """Zero IO tags should always fail regardless of repo size."""
        status, msg = bf.assess_metric("io_tag_rate", 0.0, total_nodes=50000)
        assert status != "GOOD"

    def test_existing_large_repo_still_scales(self):
        """Repos above 10K still scale (preserving existing behavior)."""
        # gvisor (35K nodes, 2.6%) should be GOOD
        status, msg = bf.assess_metric("io_tag_rate", 2.6, total_nodes=35571)
        assert status == "GOOD", f"gvisor-like repo (35K nodes, 2.6%) should be GOOD: {msg}"


class TestDataflowSliceRatioConditional:
    """Test that dataflow_slice_ratio is assessed conditionally on access_mode_coverage."""

    def test_high_ratio_with_low_coverage_not_warned(self):
        """100% dataflow_slice_ratio should not WARN when access_mode_coverage < 30%.

        When annotation coverage is low, the dataflow slice is expected to equal
        the regular slice because most edges lack access_mode and are followed anyway.
        """
        status, msg = bf.assess_metric(
            "dataflow_slice_ratio", 100.0,
            total_nodes=5000, access_mode_coverage=20.0,
        )
        assert status == "GOOD", (
            f"100% dataflow_slice_ratio with 20% access_mode_coverage should be GOOD: {msg}"
        )

    def test_high_ratio_with_high_coverage_warned(self):
        """100% dataflow_slice_ratio should WARN when access_mode_coverage >= 30%.

        If many edges have annotations but slicing isn't narrowing, something is wrong.
        """
        status, msg = bf.assess_metric(
            "dataflow_slice_ratio", 100.0,
            total_nodes=5000, access_mode_coverage=50.0,
        )
        assert status == "WARN", (
            f"100% dataflow_slice_ratio with 50% access_mode_coverage should WARN: {msg}"
        )

    def test_moderate_ratio_with_high_coverage_good(self):
        """50% dataflow_slice_ratio with good coverage is GOOD."""
        status, msg = bf.assess_metric(
            "dataflow_slice_ratio", 50.0,
            total_nodes=5000, access_mode_coverage=50.0,
        )
        assert status == "GOOD"

    def test_no_coverage_kwarg_uses_default_behavior(self):
        """Without access_mode_coverage kwarg, use existing threshold logic."""
        status, msg = bf.assess_metric("dataflow_slice_ratio", 100.0, total_nodes=5000)
        assert status == "WARN", "Without coverage info, 100% should still WARN"


class TestLimitHitFrequencyScaling:
    """Test limit_hit_frequency threshold scaling for large repos."""

    def test_small_repo_flat_threshold(self):
        """Small repos use the flat 50% threshold."""
        status, msg = bf.assess_metric("limit_hit_frequency", 55.0, total_nodes=5000)
        assert status == "WARN", f"55% limit hits in 5K node repo should WARN: {msg}"

    def test_large_repo_scales_up(self):
        """Large repos (35K+ nodes) get a higher threshold.

        gvisor (35571 nodes, 55.6%) should not WARN — large Go repos with
        deeply interconnected call graphs naturally hit slice limits more often.
        """
        status, msg = bf.assess_metric("limit_hit_frequency", 55.6, total_nodes=35571)
        assert status == "GOOD", f"gvisor-like repo (35K nodes, 55.6%) should be GOOD: {msg}"

    def test_very_large_repo(self):
        """Very large repos (50K+ nodes) get even more lenient threshold."""
        status, msg = bf.assess_metric("limit_hit_frequency", 65.0, total_nodes=50000)
        assert status == "GOOD", f"50K node repo at 65% should be GOOD: {msg}"

    def test_still_warns_for_extreme_values(self):
        """Even large repos should WARN at very high frequencies."""
        status, msg = bf.assess_metric("limit_hit_frequency", 85.0, total_nodes=35000)
        assert status != "GOOD", "85% limit hits should not be GOOD for any repo"

    def test_no_total_nodes_uses_default(self):
        """Without total_nodes, use the flat 50% threshold."""
        status, msg = bf.assess_metric("limit_hit_frequency", 55.0)
        assert status == "WARN"
