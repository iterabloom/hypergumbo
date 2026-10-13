# SPDX-License-Identifier: AGPL-3.0-or-later
"""Zig functions left the stable_id floor for the producer formula (WI-motiz, WI-tusad).

``zig.py`` computed no ``stable_id`` and ``function`` has no entry in the
backstop's factory table, so the backstop (core ``populate_kind_stable_ids``,
ADR-0035 §1) gave every zig function the floor key
``make_declaration_stable_id(kind, language, path, name)``. The producer now
mints the ADR-0035 formula itself (``compute_stable_id``: parameter count and
flags, the container chain as qualified name), so through the real survey path
each function carries that value and not the floor key. The scoped cases
(two nested ``Inner`` containers) are pinned in
``test_idl_member_producer_stable_ids.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.analyze.base import make_declaration_stable_id
from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.overload_arity import (
    declared_arity_of_entries, declared_arity_stable_id, parameter_entry,
)


def test_zig_functions_carry_the_producer_formula(tmp_path: Path) -> None:
    (tmp_path / "a.zig").write_text(
        "fn greet(x: i32) i32 { return x; }\n"
        "pub fn main() void { _ = greet(1); }\n"
    )
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    nodes = json.loads(out.read_text())["nodes"]
    functions = {n["name"]: n for n in nodes
                 if n.get("language") == "zig" and n["kind"] == "function"}
    assert sorted(functions) == ["greet", "main"]
    params = {"greet": ["x"], "main": []}
    for name, n in functions.items():
        assert n["stable_id"] == declared_arity_stable_id(
            kind="function", lang="zig", path=n["path"], name=name,
            qualified_name=name,
            arity=declared_arity_of_entries([parameter_entry(p) for p in params[name]]),
        ) != make_declaration_stable_id("function", "zig", n["path"], name)
