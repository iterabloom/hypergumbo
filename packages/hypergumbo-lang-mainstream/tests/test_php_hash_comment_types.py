# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/php.py
"""A PHP ``#`` comment inside a parameter or return type is not an id input.

The shared ``without_comments`` (applied when a signature is normalized and
hashed) writes ``//`` and ``/* */`` comments as a space, not PHP's ``#`` line
comment, which other languages' texts use for code. A ``#`` comment between
the parameters, between a type and its variable, or after the ``:`` of the
return type is outside the type node and was never hashed; one INSIDE a type
node (a union, an intersection, a DNF type, a nullable ``?``) was, so ``int|#
c\\n string`` and ``int|string`` were two ids (WI-radim, found checking more
``#`` positions). The type text is now written with the grammar's comment
nodes blanked.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.php import analyze_php


def _id_of(root: Path, decl: str, name: str = "f") -> str:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.php").write_text(f"<?php\n{decl}\n")
    found = [s for s in analyze_php(root).symbols if s.name == name]
    assert len(found) == 1, name
    assert found[0].stable_id
    return found[0].stable_id


#: (as written with a comment, the same without it)
_SAME = {
    "union-hash": ("function f(int|# c\n string $a): int { return 1; }",
                   "function f(int|string $a): int { return 1; }"),
    "nullable-hash": ("function f(? # c\n int $a): int { return 1; }",
                      "function f(?int $a): int { return 1; }"),
    "intersection-hash": ("function f(A&# c\n B $a): int { return 1; }",
                          "function f(A&B $a): int { return 1; }"),
    "dnf-hash": ("function f((A&# c\n B)|null $a): int { return 1; }",
                 "function f((A&B)|null $a): int { return 1; }"),
    "return-union-hash": ("function f(int $a): int|# c\n string { return 1; }",
                          "function f(int $a): int|string { return 1; }"),
    "union-slash": ("function f(int|// c\n string $a): int { return 1; }",
                    "function f(int|string $a): int { return 1; }"),
    "union-block": ("function f(int|/* c */string $a): int { return 1; }",
                    "function f(int|string $a): int { return 1; }"),
    "between-parameters-hash": ("function f(int $a, # c\n string $b): int { return 1; }",
                                "function f(int $a, string $b): int { return 1; }"),
}


@pytest.mark.parametrize("shape", sorted(_SAME))
def test_a_comment_in_a_type_moves_no_id(tmp_path: Path, shape: str) -> None:
    commented, plain = _SAME[shape]
    assert _id_of(tmp_path / "a", commented) == _id_of(tmp_path / "b", plain), shape


def test_a_method_type_too(tmp_path: Path) -> None:
    commented = _id_of(
        tmp_path / "a", "class K { public function m(int|# c\n string $a): void {} }", "K.m")
    plain = _id_of(tmp_path / "b", "class K { public function m(int|string $a): void {} }", "K.m")
    assert commented == plain


def test_two_types_stay_two_ids(tmp_path: Path) -> None:
    """Control: a hash sign that is code is not read as a comment, and two
    types differ."""
    a = _id_of(tmp_path / "a", "function f(int|string $a): int { return 1; }")
    b = _id_of(tmp_path / "b", "function f(int|float $a): int { return 1; }")
    c = _id_of(tmp_path / "c", "function f(#[Attr] int|string $a): int { return 1; }")
    assert a != b
    assert a == c
