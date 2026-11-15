# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pipeline forces a garbage collection only on a large graph.

``run_survey`` called ``gc.collect()`` after writing each budget tier and
once before the final write: four full collections per run. They were put
there to give memory back early on very large repositories. A full
collection walks every tracked object in the process, so its cost depends on
the size of the PROCESS, not of the run: 0.17 to 0.34 s each on five real
repositories of 1,000 to 14,000 symbols and 1.4 to 1.6 s on one of 117,000,
freeing nothing on any of the six (0 MB; the lists are released by reference
counting when they are deleted), and 0.36 s each inside a loaded test worker:
30% of the test time of the forty slowest test files
(``~/hypergumbo_lab_notebook/slow_tests_11142026``).

So nothing is forced below a graph larger than any of those, and a larger
run behaves as before. What is pinned: the rule itself, and that BOTH sites
go through it.
"""

from __future__ import annotations

import gc
import json
from pathlib import Path

import pytest

from hypergumbo_core import cli


@pytest.fixture
def collections(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the collections the pipeline asks for (and do none)."""
    asked: list[int] = []
    monkeypatch.setattr(gc, "collect", lambda *a, **k: asked.append(1) or 0)
    return asked


def _repo(root: Path) -> Path:
    (root / "app.py").write_text("def main():\n    return helper()\n\n\ndef helper():\n    return 1\n")
    return root


def test_a_small_graph_forces_nothing(collections: list[int]) -> None:
    cli._collect_if_large(0)
    cli._collect_if_large(cli._FORCED_GC_MIN_GRAPH - 1)
    assert collections == []


def test_a_large_graph_forces_one_collection_per_call(collections: list[int]) -> None:
    cli._collect_if_large(cli._FORCED_GC_MIN_GRAPH)
    cli._collect_if_large(cli._FORCED_GC_MIN_GRAPH * 10)
    assert collections == [1, 1]


def test_a_run_on_a_small_repository_forces_no_collection(
    tmp_path: Path, collections: list[int],
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "out" / "map.json"
    out.parent.mkdir()
    cli.run_behavior_map(repo_root=_repo(repo), out_path=out)
    assert json.loads(out.read_text())["nodes"], "the run must have produced a graph"
    assert collections == []


def test_both_sites_go_through_the_rule(
    tmp_path: Path, collections: list[int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the threshold at zero the same small run collects after each
    budget tier it writes and once before the final write."""
    monkeypatch.setattr(cli, "_FORCED_GC_MIN_GRAPH", 0)
    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "out" / "map.json"
    out.parent.mkdir()
    cli.run_behavior_map(repo_root=_repo(repo), out_path=out)
    tiers = sorted(out.parent.glob("map.*k.json"))
    assert len(tiers) >= 2, "the run must have written budget tiers"
    assert len(collections) == len(tiers) + 1
