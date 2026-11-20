# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dart: two spellings of one type, through the file's imports, hash alike.

Stable-id scheme v17 (owner ruling 2026-11-20). A prefixed import (``import
'foo.dart' as f;``) binds ``f``, so ``f.Foo`` is the library's ``Foo``; a
``show`` list (``import 'foo.dart' show Foo;``) binds ``Foo`` to the same type.
Both are written ``import("foo.dart").Foo`` before hashing, so ``List<Foo>``
and ``List<f.Foo>`` are one declaration. A plain import brings every exported
name in, unlisted, and binds no name the file writes: ``Foo`` through it stays
as written. A head type keeps the name its library prefix is cut to.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_common.dart import analyze_dart

_SHOW = "import 'foo.dart' as f;\nimport 'foo.dart' show Foo;\n"
_PLAIN = "import 'foo.dart' as f;\nimport 'foo.dart';\n"


def _id(root: Path, text: str, name: str) -> str:
    root.mkdir(parents=True)
    (root / "foo.dart").write_text("class Foo {}\n")
    (root / "d.dart").write_text(text)
    found = [s for s in analyze_dart(root).symbols if s.name == name]
    assert len(found) == 1, name
    assert found[0].stable_id is not None
    return found[0].stable_id


SAME = {
    "generic": (_SHOW + "void g(List<Foo> xs) {}\n", _SHOW + "void g(List<f.Foo> xs) {}\n", "g"),
    "function-type": (_SHOW + "void g(void Function(Foo) cb) {}\n",
                      _SHOW + "void g(void Function(f.Foo) cb) {}\n", "g"),
    "return": (_SHOW + "List<Foo> g() => [];\n", _SHOW + "List<f.Foo> g() => [];\n", "g"),
    "field": (_SHOW + "class C { List<Foo> x = []; }\n", _SHOW + "class C { List<f.Foo> x = []; }\n", "C.x"),
    "named": (_SHOW + "void g({required List<Foo> a}) {}\n",
              _SHOW + "void g({required List<f.Foo> a}) {}\n", "g"),
    "head": (_SHOW + "void g(Foo x) {}\n", _SHOW + "void g(f.Foo x) {}\n", "g"),
}

APART = {
    # a plain import binds nothing: ``Foo`` through it stays as written
    "plain-import": (_PLAIN + "void g(List<Foo> xs) {}\n", _PLAIN + "void g(List<f.Foo> xs) {}\n", "g"),
    # a prefixed import's show list is reached through the prefix only
    "prefixed-show": ("import 'foo.dart' as f show Foo;\nvoid g(List<Foo> xs) {}\n",
                      "import 'foo.dart' as f show Foo;\nvoid g(List<f.Foo> xs) {}\n", "g"),
    # a declared type keeps its name
    "declared": (_SHOW + "void g(List<Foo> xs) {}\nclass Foo {}\n",
                 _SHOW + "void g(List<f.Foo> xs) {}\nclass Foo {}\n", "g"),
}


@pytest.mark.parametrize("case", sorted(SAME))
def test_two_spellings_of_one_type_hash_alike(tmp_path: Path, case: str) -> None:
    first, second, name = SAME[case]
    assert _id(tmp_path / "x", first, name) == _id(tmp_path / "y", second, name)


@pytest.mark.parametrize("case", sorted(APART))
def test_an_unbound_spelling_hashes_apart(tmp_path: Path, case: str) -> None:
    first, second, name = APART[case]
    assert _id(tmp_path / "x", first, name) != _id(tmp_path / "y", second, name)
