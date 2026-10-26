# SPDX-License-Identifier: AGPL-3.0-or-later
"""A resolver-graded bind publishes its graded confidence (WI-sakum).

ADR-0039 ruling 6: an analyzer that resolves a call by a graded match passes
``resolution_score`` and ``Edge.create`` interpolates the confidence inside
the pathway's band. Finalize then re-derived every ``evidence_derived`` edge
from ``(evidence_type, is_resolved)`` alone and reset it to the base, so the
grading reached no published edge. C's function-pointer resolver grades a
pointer with two possible targets at 0.5: created at 0.575, published 0.85.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/finalize.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.confidence import derive_resolved_confidence


def test_function_pointer_with_two_targets_publishes_its_graded_confidence(
    tmp_path: Path,
) -> None:
    (tmp_path / "fp.c").write_text(
        "int f1(int x) { return x; }\n"
        "int f2(int x) { return -x; }\n"
        "int run(int c) {\n"
        "    int (*fp)(int) = f1;\n"
        "    if (c) fp = f2;\n"
        "    return fp(1);\n"
        "}\n"
    )
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    names = {n["id"]: n["name"] for n in data["nodes"]}
    edges = {
        names.get(e["dst"]): e for e in data["edges"]
        if e["type"] == "calls" and names.get(e["src"]) == "run"
    }
    graded = derive_resolved_confidence("function_pointer", 0.5)
    for target in ("f1", "f2"):
        assert edges[target]["confidence"] == graded, edges[target]
        assert edges[target]["rank_score"] == graded
        assert edges[target]["confidence_source"] == "evidence_derived"
