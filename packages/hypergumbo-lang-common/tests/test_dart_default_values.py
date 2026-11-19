# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Dart default value is written ``...`` whole, and is not hashed.

Through the WI-tojov commit, ``_extract_dart_signature`` replaced a default
value with ``...`` by a regex that stopped at the default's first ``,``,
``}`` or ``]``: ``{Size s = const Size(1, 2)}`` was written ``{Size s = ...,
2)}``, a string ``= "x,y"`` and a literal ``= const [1, 2]`` likewise, and
the fragment was split as a parameter (or closed the list early) when the
text was hashed. A default also changed what the types-first rule dropped
as the parameter's name: ``{bool loud = ...}`` hashed ``{bool loud =`` where
``{bool? loud}`` hashed ``{bool?`` (WI-doboh).

The renderer now reads each default from the tree (the ``=`` or ``:`` and
the expression after it) and writes it ``...`` whole, leaving the rest of
the group as written. The normalizer takes a default out before the
types-first rule, so a parameter hashes its modifiers and type, as one
without a default does: neither a default's value nor its presence is
hashed (a caller calls ``f({int? x = 1})`` and ``f({int? x})`` alike).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_common.dart import analyze_dart, normalize_dart_signature


def _signature(tmp_path: Path, decl: str, name: str = "f") -> str:
    (tmp_path / "a.dart").write_text(decl + "\n")
    [f] = [s for s in analyze_dart(tmp_path).symbols if s.name == name]
    assert f.signature is not None
    return f.signature


@pytest.mark.parametrize(("decl", "rendered"), [
    ("void f({Size s = const Size(1, 2), Foo g = const Foo(1, a.b + c)}) {}",
     "({Size s = ..., Foo g = ...})"),
    ('void f({String t = "x,y", int n = 1}) {}', "({String t = ..., int n = ...})"),
    ("void f([List<int> l = const [1, 2], Map m = const {1: 2}]) {}",
     "([List<int> l = ..., Map m = ...])"),
    ("void f({List<int> l = const <int>[], Set<int> s = const {}}) {}",
     "({List<int> l = ..., Set<int> s = ...})"),
    ("void f({bool loud = false}) {}", "({bool loud = ...})"),
    ("void f({int b: 3}) {}", "({int b: ...})"),
    ("void f({\n  required String text,\n  int sid = 0,\n}) {}",
     "({\n  required String text,\n  int sid = ...,\n})"),
    ("void f(int a, [int c = 1, String? d]) {}", "(int a, [int c = ..., String? d])"),
    # Whatever stands between a default and the next ``,`` goes with it, as
    # the regex took it: a comment there would otherwise survive the hash.
    ("void f({int a = 1 /* one */, int b = 2 }) {}", "({int a = ..., int b = ...})"),
])
def test_a_default_value_is_written_whole(tmp_path: Path, decl: str, rendered: str) -> None:
    assert _signature(tmp_path, decl) == rendered


@pytest.mark.parametrize(("with_default", "without"), [
    ("({bool loud = ...})", "({bool loud})"),
    ("({bool loud = ...})", "({bool quiet})"),
    ("([int c = ..., String? d])", "([int c, String? d])"),
    ("(int a, {required p.Foo x, int b = ..., List<int>? c}) Foo",
     "(int a, {required p.Foo x, int b, List<int>? c}) Foo"),
    ("({int b: ...})", "({int b})"),
    ("({\n  required String text,\n  int sid = ...,\n})", "({\n  required String text,\n  int sid,\n})"),
])
def test_a_default_is_not_hashed(with_default: str, without: str) -> None:
    assert normalize_dart_signature(with_default) == normalize_dart_signature(without)


def test_a_named_parameter_hashes_its_type() -> None:
    assert normalize_dart_signature("({bool loud = ...}) int") == "({bool)int"
    assert normalize_dart_signature("({bool loud = ...})") != normalize_dart_signature("({int loud = ...})")


def test_two_defaults_hash_alike_and_the_types_after_them_are_kept(tmp_path: Path) -> None:
    ids = {}
    for key, decl in {
        "a": "void f({Size s = const Size(1, 2), int x = 1}) {}",
        "b": "void f({Size s = const Size(3, 4), int x = 2}) {}",
        "c": "void f({Size s = const Size(1, 2), String x = ''}) {}",
    }.items():
        root = tmp_path / key
        root.mkdir()
        (root / "a.dart").write_text(decl + "\n")
        [f] = [s for s in analyze_dart(root).symbols if s.name == "f"]
        ids[key] = f.stable_id
    assert ids["a"] == ids["b"]
    assert ids["a"] != ids["c"]
