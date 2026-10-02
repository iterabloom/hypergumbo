# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-motiz: zig functions carry the stable_id floor.

``zig.py`` computes no ``stable_id`` and ``function`` has no entry in the
backstop's factory table, so every zig function shipped ``null``. Zig has no
overloading, so a top-level function name is unique in its file and the floor
key ``make_declaration_stable_id(kind, language, path, name)`` identifies it;
the backstop (core ``populate_kind_stable_ids``, ADR-0035 §1) now fills it.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.analyze.base import make_declaration_stable_id
from hypergumbo_core.cli import run_behavior_map


def test_zig_functions_take_the_floor(tmp_path: Path) -> None:
    (tmp_path / "a.zig").write_text(
        "fn greet(x: i32) i32 { return x; }\n"
        "pub fn main() void { _ = greet(1); }\n"
    )
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    nodes = json.loads(out.read_text())["nodes"]
    functions = [n for n in nodes if n.get("language") == "zig" and n["kind"] == "function"]
    assert sorted(n["name"] for n in functions) == ["greet", "main"]
    for n in functions:
        assert n["stable_id"] == make_declaration_stable_id(
            "function", "zig", n["path"], n["name"])
