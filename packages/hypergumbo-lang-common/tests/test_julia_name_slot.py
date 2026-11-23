# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/julia.py
"""ADR-0036 Ruling 1: a Julia function or const id's name slot is its name.

The slot held ``Module.run`` while the name is ``run``: the module was in
the id and nowhere else in the record. The slot is now the name, and the
scope moves to ``qualified_name``: the same ``.``-joined chain the stable id
already hashes (``_julia_stable_id``), so the stable id does not change. Two
modules' ``run`` stay two ids (their spans differ).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import sanitize_id_name_segment
from hypergumbo_lang_common.julia import analyze_julia

_SRC = (
    "module A\n"
    "const LIMIT = 1\n"
    "function run()\n    inner()\nend\n"
    "function inner end\n"
    "end\n"
    "module B\n"
    "const LIMIT = 2\n"
    "function run()\n    2\nend\n"
    "end\n"
    "function top()\n    1\nend\n"
)


def _symbols(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.jl").write_text(_SRC)
    return [s for s in analyze_julia(tmp_path).symbols if s.kind in ("function", "const")]


def test_the_fixture_emits_module_functions_and_consts(tmp_path: Path) -> None:
    got = sorted((s.kind, s.name, s.qualified_name) for s in _symbols(tmp_path))
    assert got == [
        ("const", "LIMIT", "A.LIMIT"), ("const", "LIMIT", "B.LIMIT"),
        ("function", "inner", "A.inner"), ("function", "run", "A.run"),
        ("function", "run", "B.run"), ("function", "top", "top"),
    ]


def test_every_name_slot_is_the_sanitized_name(tmp_path: Path) -> None:
    symbols = _symbols(tmp_path)
    assert [(s.id, s.name) for s in symbols
            if s.id.rsplit(":", 2)[1] != sanitize_id_name_segment(s.name)] == []


def test_same_named_members_of_two_modules_keep_distinct_ids(tmp_path: Path) -> None:
    """Control: the span tells ``A.run`` from ``B.run`` and the stable id the
    module."""
    symbols = _symbols(tmp_path)
    runs = [s for s in symbols if s.name == "run"]
    assert len({s.id for s in runs}) == 2 and len({s.stable_id for s in runs}) == 2
