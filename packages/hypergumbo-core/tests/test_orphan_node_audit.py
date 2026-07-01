# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-hahor: Orphan-node structural invariants and baseline ratchet.

Orphan nodes are Symbol nodes with zero edges (no inbound, no outbound).
Some orphans are legitimate (external_symbol boundary nodes, declared
dependencies never imported, project metadata), but certain kinds signal
graph quality gaps.

This module tests two things:

1. **Structural invariants on synthetic repos** — verifying that the
   analyzer + linker pipeline connects symbols that should be connected.
   These run fast in CI.

2. **Baseline ratchet** — asserting that orphan counts by kind stay
   below a ceiling derived from the most recent self-analysis triage.
   This prevents regressions when modifying analyzers or linkers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import run_behavior_map


def _run_and_load(repo_root: Path, out_path: Path) -> dict[str, Any]:
    """Run behavior map on repo_root, return parsed JSON."""
    run_behavior_map(
        repo_root=repo_root,
        out_path=out_path,
        include_sketch_precomputed=False,
    )
    return json.loads(out_path.read_text())


def _orphan_ids(bm: dict[str, Any]) -> set[str]:
    """Return IDs of nodes with zero edges."""
    has_edge: set[str] = set()
    for e in bm.get("edges", []):
        has_edge.add(e["src"])
        has_edge.add(e["dst"])
    return {n["id"] for n in bm.get("nodes", []) if n["id"] not in has_edge}


def _orphan_nodes(bm: dict[str, Any]) -> list[dict[str, Any]]:
    """Return full node dicts for orphan nodes."""
    ids = _orphan_ids(bm)
    return [n for n in bm.get("nodes", []) if n["id"] in ids]


def _nodes_by_kind(nodes: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [n for n in nodes if n.get("kind") == kind]


# ---------------------------------------------------------------------------
# Structural invariants on synthetic repos
# ---------------------------------------------------------------------------


class TestCalledFunctionIsNotOrphan:
    """A function called by another function in the same module must
    have at least one inbound edge."""

    SAMPLE_CODE = '''\
def helper():
    return 42

def main():
    return helper()
'''

    def test_called_function_has_inbound_edge(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text(self.SAMPLE_CODE)
        bm = _run_and_load(repo, tmp_path / "out.json")
        orphans = _orphan_ids(bm)
        helper_orphans = [
            oid for oid in orphans
            if "helper" in oid and "function" in oid
        ]
        assert helper_orphans == [], (
            f"helper() is called by main() but is orphaned: {helper_orphans}"
        )


class TestImportedSymbolIsNotOrphan:
    """A symbol imported via 'from X import Y' should have an inbound
    references edge from the import site."""

    def test_imported_function_has_edge(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "lib.py").write_text("def compute():\n    return 1\n")
        (repo / "main.py").write_text(
            "from lib import compute\n\ndef run():\n    return compute()\n"
        )
        bm = _run_and_load(repo, tmp_path / "out.json")
        orphans = _orphan_ids(bm)
        compute_orphans = [
            oid for oid in orphans
            if "compute" in oid and "function" in oid
        ]
        assert compute_orphans == [], (
            f"compute() is imported and called but orphaned: {compute_orphans}"
        )


class TestClassMethodCallCreatesEdge:
    """A method defined on a class and called from within the module should
    produce at least one edge (either contains or calls)."""

    SAMPLE_CODE = '''\
class Calculator:
    def add(self, a, b):
        return a + b

def use_calculator():
    c = Calculator()
    return c.add(1, 2)
'''

    def test_class_method_has_edge(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "calc.py").write_text(self.SAMPLE_CODE)
        bm = _run_and_load(repo, tmp_path / "out.json")
        orphans = _orphan_ids(bm)
        add_orphans = [
            oid for oid in orphans
            if "add" in oid and "function" in oid
        ]
        assert add_orphans == [], (
            f"Calculator.add() is called but orphaned: {add_orphans}"
        )


# ---------------------------------------------------------------------------
# Baseline ratchet — orphan ceilings per kind
# ---------------------------------------------------------------------------

# Baselines from 2026-05-27 self-analysis triage (WI-hahor).
# Each ceiling is the observed count + 10% headroom, rounded up.
# Ratchet down as root causes are fixed.
#
# dispatch:F4 (2026-06-25): adding function/variable to CONTAINABLE_KINDS rooted
# top-level members at their file anchor (~8.8k new file->member contains edges),
# de-orphaning them. Self-analysis dropped function orphans 93->14 and variable
# orphans 130->0, so those ceilings ratchet down (240->30, 120->15).
# WI-zajaz (2026-07-01): adding `field` to CONTAINABLE_KINDS rooted class-body
# attributes at their class (1862 field orphans -> ~0), which had dominated the
# ratio.
# WI-logon (2026-07-01): `kind="file"` anchors are EXEMPT from the ratchet — the
# file-anchor work (WI-dagif/WI-rajod) mints a kind="file" anchor per discovered
# path, and the nodeless config/doc cohort is legitimately edgeless (a config
# file with no code has an anchor and no edges), the same rationale by which the
# module docstring already calls external_symbol boundary nodes legitimate. File
# anchors are dropped from BOTH the per-kind ceilings and the ratio's
# numerator+denominator (the ratio then measures the orphan rate among
# *connectable* nodes), via _RATCHET_EXEMPT_KINDS below.
_RATCHET_EXEMPT_KINDS = frozenset({"file"})

ORPHAN_CEILINGS: dict[str, int] = {
    "function": 30,
    "call_site": 125,
    "variable": 15,
    "external_symbol": 120,
    "dependency": 75,
    "export": 25,
    "project": 10,
    "class": 5,
    "module": 5,
}

# Overall orphan ratio ceiling (%) — measured over connectable (non-exempt) nodes.
ORPHAN_RATIO_CEILING = 2.5


def test_orphan_baseline_ratchet(tmp_path: Path) -> None:
    """Ratchet test: orphan counts must stay below ceilings.

    Run with: HYPERGUMBO_RUN_SELF_ANALYSIS=1 pytest -k test_orphan_baseline
    Skipped by default because self-analysis takes ~90s.
    Consolidated into one test to avoid redundant analysis runs
    under pytest-xdist.
    """
    if not os.environ.get("HYPERGUMBO_RUN_SELF_ANALYSIS"):
        pytest.skip("set HYPERGUMBO_RUN_SELF_ANALYSIS=1 to enable")

    repo_root = Path(__file__).resolve().parents[4]
    bm = _run_and_load(repo_root, tmp_path / "self-analysis.json")

    # WI-logon: exempt kind="file" anchors (legitimately edgeless) from both the
    # ratio numerator and denominator so the ratio measures the orphan rate among
    # connectable nodes.
    all_nodes = bm.get("nodes", [])
    total = sum(1 for n in all_nodes if n.get("kind") not in _RATCHET_EXEMPT_KINDS)
    orphans = [
        n for n in _orphan_nodes(bm) if n.get("kind") not in _RATCHET_EXEMPT_KINDS
    ]
    orphan_count = len(orphans)

    ratio = 100 * orphan_count / total if total else 0
    assert ratio < ORPHAN_RATIO_CEILING, (
        f"Orphan ratio {ratio:.1f}% exceeds ceiling {ORPHAN_RATIO_CEILING}%. "
        f"({orphan_count} orphans / {total} nodes)"
    )

    from collections import Counter
    by_kind = Counter(n.get("kind", "unknown") for n in orphans)

    violations = []
    for kind, ceiling in ORPHAN_CEILINGS.items():
        actual = by_kind.get(kind, 0)
        if actual > ceiling:
            violations.append(f"  {kind}: {actual} > ceiling {ceiling}")
    assert not violations, (
        "Orphan count ceilings breached (WI-hahor ratchet):\n"
        + "\n".join(violations)
    )

    orphan_classes = _nodes_by_kind(orphans, "class")
    assert len(orphan_classes) == 0, (
        f"Regression: {len(orphan_classes)} orphan classes found "
        f"(was 0 post-INV-mofav/WI-vuton). IDs: "
        f"{[c['id'] for c in orphan_classes[:5]]}"
    )
