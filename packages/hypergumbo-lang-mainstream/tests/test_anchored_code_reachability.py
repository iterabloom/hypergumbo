# SPDX-License-Identifier: AGPL-3.0-or-later
"""Code anchored on a file or class runs when its container does (WI-fakuh).

``REGISTRY = build_registry()`` runs when ``lib`` is imported; a class body
runs when its module loads; a test module's own module-level code and test
class bodies run when the test runner loads it. The analyzer anchors such
calls on the file or class, and the reachability consumers never entered an
anchor: dead-code read the helper dead, test-coverage listed it as a cold
spot, sketch's coverage line left it untested. They now share one contract,
``anchor_execution`` (see its module docstring).

Each fixture keeps one helper NOTHING calls, which must stay dead / cold: the
contract makes containers run, never a function nobody calls.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/anchor_execution.py
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from hypergumbo_core.cli import build_parser, cmd_dead_code_maybe, cmd_test_coverage
from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Edge, Symbol
from hypergumbo_core.sketch import _estimate_test_coverage


def _survey(repo: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    out = repo / "map.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return out


def _json(cmd, argv: list[str]) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cmd(build_parser().parse_args(argv)) == 0
    return json.loads(buf.getvalue())


def _dead(repo: Path, map_file: Path, seeds: str) -> set[str]:
    data = _json(cmd_dead_code_maybe, [
        "dead-code-maybe", str(repo), "--input", str(map_file),
        "--format", "json", "--seeds", seeds,
    ])
    return {c["name"] for c in data["dead_candidates"]}


_LIB = {
    "lib.py": (
        "def _build_registry():\n    return {}\n"
        "_REGISTRY = _build_registry()\n"
        "def build_cases():\n    return []\n"
        "class Holder:\n    X = build_cases()\n"
        "def _orphan():\n    return 0\n"
        "def run():\n    return _REGISTRY\n"
    ),
    "main.py": 'import lib\nif __name__ == "__main__":\n    lib.run()\n',
}


def test_module_init_and_class_body_calls_are_not_dead(tmp_path: Path) -> None:
    map_file = _survey(tmp_path, _LIB)
    assert _dead(tmp_path, map_file, "entrypoints") == {"_orphan"}
    assert _dead(tmp_path, map_file, "production") == {"_orphan"}


_TESTED = {
    "packages/p/src/p/__init__.py": "",
    "packages/p/src/p/mod.py": (
        "def f_mod_level():\n    return []\n"
        "def f_class_body():\n    return 1\n"
        "def f_test_fn():\n    return 2\n"
        "def f_uncalled():\n    return 3\n"
    ),
    "tests/test_mod.py": (
        "from p.mod import f_mod_level, f_class_body, f_test_fn\n"
        "CASES = f_mod_level()\n"
        "class TestThing:\n"
        "    X = f_class_body()\n"
        "    def test_it(self):\n"
        "        f_test_fn()\n"
    ),
}


def test_test_module_anchors_are_test_seeds_for_every_consumer(tmp_path: Path) -> None:
    map_file = _survey(tmp_path, _TESTED)

    assert _dead(tmp_path, map_file, "tests") == {"f_uncalled"}

    coverage = _json(cmd_test_coverage, [
        "test-coverage", str(tmp_path), "--input", str(map_file), "--format", "json",
    ])
    assert [c["name"] for c in coverage["cold_spots"]] == ["f_uncalled"]

    data = json.loads(map_file.read_text())
    symbols = [Symbol.from_dict(n) for n in data["nodes"]]
    edges = [Edge.from_dict(e) for e in data["edges"]]
    tested, total, _ = _estimate_test_coverage(symbols, edges)
    assert (tested, total) == (3, 4)
