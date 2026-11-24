# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/php.py
"""A PHP function declared inside another function or a method is scoped by
the declarations that hold it.

PHP declares a nested ``function h() {}`` in the global (namespace) scope when
its holder runs, so its ``qualified_name`` is the namespace's ``h``. Its stable
id hashed that name alone, so the ``h`` declared in ``A::f``, in ``A::g`` and
in ``B::f`` were one id (WI-finam, the scope-chain defect WI-podor fixed for
Swift); the run's occurrence split told them apart by order only. The hash
now names the holders (``App\\A::f::h``); the ``qualified_name`` field and a
top-level function's id are unchanged.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import (
    make_file_stable_id,
    make_typed_stable_id,
    split_within_file_stable_id_collisions,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.php import analyze_php

_SRC = (
    "<?php\n"
    "namespace App;\n"
    "function top(int $x) { function g() {} }\n"
    "class A {\n"
    "    public function f() { function h() {} }\n"
    "    public static function s() { function h() {} }\n"
    "}\n"
    "class B {\n"
    "    public function f() { function h() {} $c = function () { }; }\n"
    "}\n"
    "trait T {\n"
    "    public function f() { function h() {} }\n"
    "}\n"
)


def _symbols(root: Path, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.php").write_text(text)
    return [s for s in analyze_php(root).symbols if s.kind != "file"]


def _h(symbols: list[Symbol]) -> list[Symbol]:
    return sorted((s for s in symbols if s.name == "h"), key=lambda s: s.span.start_line)


def test_the_nested_functions_of_four_holders_get_four_ids(tmp_path: Path) -> None:
    found = _h(_symbols(tmp_path / "r", _SRC))
    assert len(found) == 4
    assert len({s.stable_id for s in found}) == 4
    assert {s.qualified_name for s in found} == {"App\\h"}


def test_deleting_the_first_nested_function_moves_no_other_id(tmp_path: Path) -> None:
    both = _symbols(tmp_path / "both", _SRC)
    alone = _symbols(
        tmp_path / "alone",
        _SRC.replace("public function f() { function h() {} }\n    public static",
                     "public function f() { }\n    public static"),
    )
    split_within_file_stable_id_collisions(both)
    split_within_file_stable_id_collisions(alone)
    assert [s.stable_id for s in _h(alone)] == [s.stable_id for s in _h(both)][1:]


def test_a_top_level_functions_id_is_unchanged(tmp_path: Path) -> None:
    """A top-level function, and one nested in it, hash the namespace and the
    holder chain: the top-level one exactly as before."""
    fsid = make_file_stable_id("php", "a.php")
    got = {s.name: s for s in _symbols(tmp_path / "r", _SRC)}
    assert got["top"].stable_id == make_typed_stable_id(
        "function", "(int)", "", name="top", qualified_name="App\\top", file_stable_id=fsid)
    assert got["g"].stable_id == make_typed_stable_id(
        "function", "()", "", name="g", qualified_name="App\\top::g", file_stable_id=fsid)
    assert got["g"].qualified_name == "App\\g"
