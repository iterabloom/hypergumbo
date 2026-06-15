# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-pazur closure: emitted tiered budget maps report counts consistent with their arrays.

Production-path behavioral evidence (projection:F1) that for every emitted
``behavior_map.<budget>.json``, ``nodes_summary.included.count == len(nodes)`` and
``nodes_summary.included_edges_count == len(edges)`` — including the *tight* tiers whose
post-selection shrink loop prunes nodes below the connectivity selection. The filed repro
was hypergumbo self-analysis, where a 4k tier claimed ~19 nodes / 27 edges while
serializing 1 node / 0 edges.

This is **non-vacuous**: it was verified to actually catch the bug. With the
``recompute_view_summary`` call removed, this exact fixture reports pre-shrink counts that
exceed the on-disk arrays at the 1k / 2k / 4k tiers (e.g. 1k: included.count 3 vs
len(nodes) 1) — so the per-tier assertions below fail without the fix. The isolated
shrink-loop unit regression, with a forced + precondition-guarded shrink, is
``test_nodes_summary_reconciled_after_shrink`` in ``hypergumbo-core/tests/test_compact.py``;
the pure ``recompute_view_summary`` reconciler is unit-tested there too. This file is the
end-to-end closure: the analyzer must be present at runtime, so it lives in mainstream.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _build_hub_and_spoke_repo(root: Path, n_spokes: int) -> None:
    """A connected hub-and-spoke Python repo: every spoke calls the hub, so the induced
    subgraph keeps edges, and the node set is large enough that the tight tiers must shrink."""
    (root / "hub.py").write_text(
        'def hub(x):\n    """Central hub that every spoke forwards to."""\n    return x + 1\n'
    )
    for i in range(n_spokes):
        (root / f"spoke{i:02d}.py").write_text(
            "from hub import hub\n\n"
            f"def spoke{i:02d}(value):\n"
            f'    """Spoke {i} forwards its value to the central hub."""\n'
            f"    return hub(value) + {i}\n"
        )


def test_emitted_tiers_have_consistent_nodes_summary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_hub_and_spoke_repo(repo, n_spokes=30)
    out = tmp_path / "out.json"

    # 1k/2k/4k are tight enough that the shrink loop fires (verified by neutering the fix);
    # 64k is the loose control that carries the whole eligible set.
    run_behavior_map(
        repo_root=repo, out_path=out, include_sketch_precomputed=False,
        budgets="1k,2k,4k,64k",
    )

    node_counts: dict[str, int] = {}
    for tier in ("1k", "2k", "4k", "64k"):
        tier_path = tmp_path / f"out.{tier}.json"
        assert tier_path.exists(), f"{tier} budget file not generated"
        data = json.loads(tier_path.read_text())
        assert data["view"] == "tiered"
        summary = data["nodes_summary"]
        # INV-pazur: the summary counts equal the on-disk array lengths, for every tier.
        assert summary["included"]["count"] == len(data["nodes"]), (
            f"{tier}: included.count {summary['included']['count']} != "
            f"len(nodes) {len(data['nodes'])}"
        )
        assert summary["included_edges_count"] == len(data["edges"]), (
            f"{tier}: included_edges_count {summary['included_edges_count']} != "
            f"len(edges) {len(data['edges'])}"
        )
        node_counts[tier] = len(data["nodes"])

    # Non-vacuity guard: the tight tier projects a strict subset (the budget machinery is
    # actively constraining), so the invariant above is not holding trivially on an
    # all-included view. A tier never empties below one node (the shrink guard is len > 1).
    assert node_counts["1k"] >= 1
    assert node_counts["1k"] < node_counts["64k"], (
        f"1k tier ({node_counts['1k']}) not strictly smaller than 64k "
        f"({node_counts['64k']}) — fixture/budgets no longer constrain the projection"
    )
