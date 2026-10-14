# SPDX-License-Identifier: AGPL-3.0-or-later
"""CMake functions and macros mint their ADR-0035 producer stable_id (WI-tusad).

``function(run x)`` and a later ``function(run x y)`` in one file used to
share the backstop floor key (kind, language, path, name) and both be left
null. The analyzer now records each definition's named arguments in
``meta["parameters"]`` and hashes their count through
``overload_arity.declared_arity_stable_id``: the pair holds distinct ids, and
deleting the first leaves the second's unchanged.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, declared_arity, declared_arity_stable_id, parameter_entry,
)
from hypergumbo_lang_mainstream.cmake import analyze_cmake_files

_TEXT = (
    "function(run x)\nendfunction()\n"
    "function(run x y)\nendfunction()\n"
    "macro(helper)\nendmacro()\n"
)


def _definitions(root: Path, text: str) -> dict[int, Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "CMakeLists.txt").write_text(text)
    return {
        s.span.start_line: s
        for s in analyze_cmake_files(root).symbols
        if s.kind in ("function", "macro") and s.span is not None
    }


def test_function_and_macro_ids_are_the_producer_formula(tmp_path: Path) -> None:
    defs = _definitions(tmp_path, _TEXT)
    two = [parameter_entry("x"), parameter_entry("y")]
    assert defs[3].meta == {PARAMETERS_KEY: two}
    assert defs[3].signature == "(x, y)"
    assert defs[3].stable_id == declared_arity_stable_id(
        kind="function", lang="cmake", path="CMakeLists.txt", name="run",
        qualified_name="run", arity=declared_arity({PARAMETERS_KEY: two}),
    )
    assert defs[5].meta == {PARAMETERS_KEY: []}
    assert defs[5].stable_id == declared_arity_stable_id(
        kind="macro", lang="cmake", path="CMakeLists.txt", name="helper",
        qualified_name="helper", arity=declared_arity({PARAMETERS_KEY: []}),
    )
    ids = [defs[line].stable_id for line in (1, 3, 5)]
    assert None not in ids and len(set(ids)) == 3


def test_deleting_an_earlier_same_name_function_moves_no_id(
    tmp_path: Path,
) -> None:
    before = _definitions(tmp_path / "r1", _TEXT)
    after = _definitions(
        tmp_path / "r2",
        _TEXT.replace("function(run x)\nendfunction()\n", "\n\n"),
    )
    assert set(after) == {3, 5}
    for line, sym in after.items():
        assert sym.stable_id == before[line].stable_id is not None
