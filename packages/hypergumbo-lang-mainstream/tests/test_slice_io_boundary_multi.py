# SPDX-License-Identifier: AGPL-3.0-or-later
"""``slice --io-boundary`` keeps a call for every boundary it crosses (WI-votat).

``subprocess.check_output`` is ``ipc_recv`` AND ``subprocess`` (a
``simultaneous`` row), and the classification records both in
``meta['io_boundaries']``; the scalar ``meta['io_boundary']`` names the first
row only. ``io-boundaries`` reads the list; the slice filter read the scalar,
so ``slice --entry main --io-boundary subprocess`` reported one edge where
two calls launch a process, at exit 0 and with no warning.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/cli.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import build_parser, cmd_slice, run_behavior_map


def _slice_ids(map_file: Path, out: Path, boundary: str) -> set[str]:
    args = build_parser().parse_args([
        "slice", str(map_file), "--entry", "main",
        "--io-boundary", boundary, "--out", str(out),
    ])
    assert cmd_slice(args) == 0
    return set(json.loads(out.read_text())["feature"]["edge_ids"])


def test_a_call_crossing_two_boundaries_is_kept_for_both(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "import subprocess\n"
        "def run_check():\n"
        "    return subprocess.check_output(['ls'])\n"
        "def run_plain():\n"
        "    return subprocess.run(['ls'])\n"
        "def main():\n"
        "    run_check()\n"
        "    run_plain()\n"
    )
    map_file = tmp_path / "map.json"
    run_behavior_map(repo_root=tmp_path, out_path=map_file, include_sketch_precomputed=False)
    data = json.loads(map_file.read_text())
    names = {n["id"]: n["name"] for n in data["nodes"]}
    callers = {e["id"]: names.get(e["src"]) for e in data["edges"]}

    launched = _slice_ids(map_file, tmp_path / "s.json", "subprocess")
    received = _slice_ids(map_file, tmp_path / "r.json", "ipc_recv")
    assert {callers[i] for i in launched} == {"run_check", "run_plain"}
    assert {callers[i] for i in received} == {"run_check"}
