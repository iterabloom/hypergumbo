# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for bakeoff-deep cmd_compare key_metric selection (WI-lazat).

The compare subcommand previously hardcoded a 4-metric KEY_METRICS list
(orphan_rate, avg_slice_nodes, cross_file_ratio, centrality_gini) that
omitted newer verdict-driving fields like slice_access_mode_coverage,
dataflow_slice_ratio, slice_coverage_pct, tier1_pct, and io_tag_rate.
The fix replaces it with a dynamic selector that ranks metrics by
absolute mean delta across common repos and falls back to a verdict-
relevant default set when there are no deltas.
"""

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


class TestSelectKeyMetrics:
    """Verify _select_key_metrics ranks by mean absolute delta."""

    def test_picks_largest_delta_metric(self):
        """The metric with the largest absolute mean delta ranks first."""
        metrics_a = {
            "repo1": {"verdict": "GOOD", "alpha": 1.0, "beta": 10.0},
            "repo2": {"verdict": "GOOD", "alpha": 2.0, "beta": 20.0},
        }
        metrics_b = {
            "repo1": {"verdict": "GOOD", "alpha": 1.5, "beta": 30.0},
            "repo2": {"verdict": "GOOD", "alpha": 2.5, "beta": 40.0},
        }
        result = bf._select_key_metrics(
            metrics_a, metrics_b, ["repo1", "repo2"], max_count=2,
        )
        # alpha mean abs delta = 0.5; beta mean abs delta = 20.0
        assert result[0] == "beta"
        assert "alpha" in result

    def test_respects_max_count(self):
        """The returned list is bounded by max_count."""
        metrics_a = {
            "repo1": {f"m{i}": float(i) for i in range(10)},
        }
        metrics_b = {
            "repo1": {f"m{i}": float(i) * 2 for i in range(10)},
        }
        result = bf._select_key_metrics(
            metrics_a, metrics_b, ["repo1"], max_count=3,
        )
        assert len(result) == 3

    def test_only_intersect_metrics(self):
        """Metrics in only one session are excluded."""
        metrics_a = {"repo1": {"only_a": 1.0, "shared": 2.0}}
        metrics_b = {"repo1": {"only_b": 1.0, "shared": 5.0}}
        result = bf._select_key_metrics(
            metrics_a, metrics_b, ["repo1"], max_count=10,
        )
        assert "shared" in result
        assert "only_a" not in result
        assert "only_b" not in result

    def test_excludes_non_numeric(self):
        """String metrics like 'verdict' are not selected."""
        metrics_a = {"repo1": {"verdict": "GOOD", "x": 1.0}}
        metrics_b = {"repo1": {"verdict": "WARN", "x": 2.0}}
        result = bf._select_key_metrics(
            metrics_a, metrics_b, ["repo1"], max_count=10,
        )
        assert "verdict" not in result
        assert "x" in result

    def test_zero_delta_falls_back_to_defaults(self):
        """When all deltas are zero, fall back to verdict-driving defaults."""
        identical = {
            "repo1": {
                "verdict": "GOOD",
                "dataflow_slice_ratio": 0.1,
                "slice_access_mode_coverage": 50.0,
                "orphan_rate": 5.0,
                "irrelevant_other": 1.0,
            },
        }
        result = bf._select_key_metrics(
            identical, identical, ["repo1"], max_count=10,
        )
        # The fallback list should be used since all deltas are zero
        assert "dataflow_slice_ratio" in result
        assert "slice_access_mode_coverage" in result

    def test_no_common_repos_returns_empty(self):
        """When the common repo list is empty, return an empty list."""
        result = bf._select_key_metrics(
            {"repo1": {"x": 1.0}}, {"repo2": {"x": 2.0}}, [], max_count=10,
        )
        assert result == []

    def test_no_numeric_metrics_returns_empty(self):
        """When no numeric metrics exist in the intersection, return []."""
        result = bf._select_key_metrics(
            {"repo1": {"verdict": "GOOD"}},
            {"repo1": {"verdict": "WARN"}},
            ["repo1"],
            max_count=10,
        )
        assert result == []

    def test_default_metrics_filter_to_intersection(self):
        """Fallback only includes default metrics that exist in the data."""
        # All deltas zero, but only 'orphan_rate' is in the data
        data = {"repo1": {"orphan_rate": 5.0, "random_metric": 1.0}}
        result = bf._select_key_metrics(data, data, ["repo1"], max_count=10)
        assert "orphan_rate" in result
        # dataflow_slice_ratio is in the default list but not in the data,
        # so it must NOT appear in the result
        assert "dataflow_slice_ratio" not in result
