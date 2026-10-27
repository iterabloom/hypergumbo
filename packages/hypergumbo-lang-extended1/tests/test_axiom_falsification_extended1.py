# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0027's axiom on this package's analyzers' output (WI-ziruv).

"Symbol.kind names the source-language syntactic construct that the symbol
represents." A kind off the registry's ``language_construct`` section names
something else, or nothing anyone has argued for. The static drift linter
cannot see a kind an analyzer COMPUTES (pony.py builds its kind from
``node.type.replace("_definition", "")``), so only output shows it: this test
surveys one small fixture per language through ``run_behavior_map`` and checks
every emitted kind. The mainstream half, with the other axioms, is
hypergumbo-lang-mainstream's ``test_axiom_falsification.py``; the
classification is ``docs/surveys/axiom-falsifiability.md``.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/symbol_kinds.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.symbol_kinds import AXIS_LANGUAGE_CONSTRUCT, symbol_kinds_on_axis

_FIXTURES: dict[str, dict[str, str]] = {
    "pony": {"main.pony": (
        "actor Main\n  new create(env: Env) =>\n    env.out.print(\"hi\")\n\n"
        "primitive Helper\n  fun twice(x: U32): U32 => x * 2\n\n"
        "class Box\n  var v: U32 = 0\n"
    )},
    "zig": {"a.zig": "pub fn twice(x: u32) u32 {\n    return x * 2;\n}\nconst Box = struct { v: u32 };\n"},
    "nim": {"a.nim": "proc twice(x: int): int = x * 2\ntype Box = object\n  v: int\n"},
    "gleam": {"src/a.gleam": "pub fn twice(x: Int) -> Int {\n  x * 2\n}\npub type Box {\n  Box(v: Int)\n}\n"},
}
#: The fixture's own construct per language, so the test cannot pass on a
#: survey that emitted nothing.
_REACHED = {"pony": "class", "zig": "function", "nim": "function", "gleam": "function"}


def off_axis_kinds(nodes: list[dict]) -> set[str]:
    canonical = {spec.name for spec in symbol_kinds_on_axis(AXIS_LANGUAGE_CONSTRUCT)}
    return {n["kind"] for n in nodes} - canonical


def test_the_checker_reports_a_kind_off_the_language_construct_section() -> None:
    assert off_axis_kinds([{"kind": "function"}, {"kind": "event_publisher"}]) == {"event_publisher"}


@pytest.mark.parametrize("language", [
    pytest.param("pony", marks=pytest.mark.xfail(
        strict=True, reason="WI-higik: pony emits unregistered kinds 'actor' and 'primitive'")),
    "zig", "nim", "gleam",
])
def test_every_emitted_kind_is_a_language_construct(tmp_path: Path, language: str) -> None:
    for rel, text in _FIXTURES[language].items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    out = tmp_path / "map.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    nodes = json.loads(out.read_text())["nodes"]
    assert _REACHED[language] in {n["kind"] for n in nodes}  # reach
    assert off_axis_kinds(nodes) == set()
