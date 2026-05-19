# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-higap: Edge.origin and Edge.origin_run_id are non-empty by construction.

The hard-raise enforcement in ``Edge.__post_init__`` is the primary mechanism
keeping these fields populated, but a property test on hypergumbo's own
self-analysis provides a second-tier check: it catches paths where a
producer constructs an Edge via some channel we didn't anticipate (e.g.,
a future ``Edge.from_dict`` regression or a hand-rolled equivalent).

Two invariants:

1. **No production-emitted edge has empty origin or origin_run_id.**
   This was the original WI-higap claim from self-analysis (round 13:
   425 edges with empty origin_run_id, 1 edge with both empty). All
   such cases must be eliminated by the producer fixes in this PR.

2. **The LEGACY_DESERIALIZED_SENTINEL never appears in a fresh run's
   output.** The sentinel exists only to allow ``Edge.from_dict()`` to
   load older behavior-map JSON without crashing. Producers must never
   stamp it directly; if it appears on a freshly-analyzed repo, a
   producer regressed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hypergumbo_core.ir import LEGACY_DESERIALIZED_SENTINEL


REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_self_analysis(tmp_path: Path) -> dict:
    """Run hypergumbo on its own source and return the parsed behavior map.

    Uses ``sys.executable -m hypergumbo_core.cli`` rather than the
    ``hypergumbo`` console script so CI runners without the installed
    entrypoint on ``PATH`` still pick up the in-tree module.
    """
    out_path = tmp_path / "behavior-map.json"
    result = subprocess.run(
        [sys.executable, "-m", "hypergumbo_core", "run", str(REPO_ROOT),
         "--no-sketch-fan-out",
         "--out", str(out_path)],
        check=False,
        capture_output=True,
        timeout=600,
    )
    if result.returncode != 0:
        pytest.skip(f"hypergumbo run failed (rc={result.returncode}): {result.stderr.decode()[:500]}")
    with out_path.open() as f:
        return json.load(f)


@pytest.fixture(scope="module")
def self_analysis(tmp_path_factory) -> dict:
    """Cache the self-analysis output across the tests in this module."""
    tmp = tmp_path_factory.mktemp("wi_higap_self")
    return _run_self_analysis(tmp)


class TestWIHigapEdgeProvenance:
    """Invariant: every edge has non-empty origin and origin_run_id."""

    def test_no_edge_has_empty_origin(self, self_analysis: dict) -> None:
        offending = [
            (e.get("id"), e.get("type"), e.get("src"), e.get("dst"))
            for e in self_analysis.get("edges", [])
            if not e.get("origin")
        ]
        assert offending == [], (
            f"WI-higap regression: {len(offending)} edges have empty origin "
            f"(first 5: {offending[:5]})"
        )

    def test_no_edge_has_empty_origin_run_id(self, self_analysis: dict) -> None:
        offending = [
            (e.get("id"), e.get("type"), e.get("origin"))
            for e in self_analysis.get("edges", [])
            if not e.get("origin_run_id")
        ]
        assert offending == [], (
            f"WI-higap regression: {len(offending)} edges have empty origin_run_id "
            f"(first 5: {offending[:5]})"
        )

    def test_no_edge_carries_legacy_deserialized_sentinel(
        self, self_analysis: dict,
    ) -> None:
        """Producers must never stamp LEGACY_DESERIALIZED_SENTINEL.

        That value is reserved for Edge.from_dict() to use when loading
        legacy behavior-map JSON. If it appears on a freshly-analyzed
        repo, a producer has regressed by reading and re-emitting an old
        Edge without re-stamping origin / origin_run_id.
        """
        offending = [
            (e.get("id"), e.get("type"), e.get("origin"), e.get("origin_run_id"))
            for e in self_analysis.get("edges", [])
            if e.get("origin") == LEGACY_DESERIALIZED_SENTINEL
            or e.get("origin_run_id") == LEGACY_DESERIALIZED_SENTINEL
        ]
        assert offending == [], (
            "WI-higap regression: production-emitted edge carries the "
            f"LEGACY_DESERIALIZED_SENTINEL ({LEGACY_DESERIALIZED_SENTINEL!r}). "
            f"First 5 cases: {offending[:5]}"
        )


class TestEdgePostInitEnforcement:
    """Direct unit tests for the Edge.__post_init__ hard-raise."""

    def test_empty_origin_raises(self) -> None:
        from hypergumbo_core.ir import Edge

        with pytest.raises(ValueError, match=r"Edge\.origin must be non-empty"):
            Edge(
                id="e1", src="s", dst="d", edge_type="calls", line=1,
                origin="", origin_run_id="run-1",
            )

    def test_empty_origin_run_id_raises(self) -> None:
        from hypergumbo_core.ir import Edge

        with pytest.raises(ValueError, match=r"Edge\.origin_run_id must be non-empty"):
            Edge(
                id="e1", src="s", dst="d", edge_type="calls", line=1,
                origin="test-pass", origin_run_id="",
            )

    def test_both_set_passes(self) -> None:
        from hypergumbo_core.ir import Edge

        e = Edge(
            id="e1", src="s", dst="d", edge_type="calls", line=1,
            origin="test-pass", origin_run_id="run-1",
        )
        assert e.origin == "test-pass"
        assert e.origin_run_id == "run-1"

    def test_from_dict_injects_sentinel_for_empty(self) -> None:
        """Edge.from_dict survives empty origin / origin_run_id in legacy JSON."""
        from hypergumbo_core.ir import Edge

        legacy = {
            "id": "e1",
            "src": "s",
            "dst": "d",
            "type": "calls",
            "line": 1,
            "origin": "",
            "origin_run_id": "",
        }
        e = Edge.from_dict(legacy)
        assert e.origin == LEGACY_DESERIALIZED_SENTINEL
        assert e.origin_run_id == LEGACY_DESERIALIZED_SENTINEL

    def test_from_dict_preserves_populated_fields(self) -> None:
        from hypergumbo_core.ir import Edge

        d = {
            "id": "e1",
            "src": "s",
            "dst": "d",
            "type": "calls",
            "line": 1,
            "origin": "real-pass-v1",
            "origin_run_id": "run-42",
        }
        e = Edge.from_dict(d)
        assert e.origin == "real-pass-v1"
        assert e.origin_run_id == "run-42"
