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
the group as written. The hashed text, read from the tree one parameter at
a time (``_dart_hashed_signature``), leaves the default out, so a parameter
hashes its modifiers and type, as one without a default does: neither a
default's value nor its presence is hashed (a caller calls ``f({int? x =
1})`` and ``f({int? x})`` alike).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from tree_sitter_language_pack import get_parser

from hypergumbo_core.analyze.base import iter_tree
from hypergumbo_lang_common.dart import _dart_hashed_signature, analyze_dart


def _hashed(decl: str) -> str:
    """The hashed text of the one function ``decl`` declares."""
    source = decl.encode()
    tree = get_parser("dart").parse(source)
    [sig] = [n for n in iter_tree(tree.root_node) if n.type == "function_signature"]
    return _dart_hashed_signature(sig, source)


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
    ("void f({bool loud = false}) {}", "void f({bool loud}) {}"),
    ("void f([int c = 1, String? d]) {}", "void f([int c, String? d]) {}"),
    ("Foo f(int a, {required p.Foo x, int b = 2, List<int>? c}) => a;",
     "Foo f(int a, {required p.Foo x, int b, List<int>? c}) => a;"),
    ("void f({int b: 3}) {}", "void f({int b}) {}"),
    ("void f({\n  required String text,\n  int sid = 0,\n}) {}",
     "void f({\n  required String text,\n  int sid,\n}) {}"),
    ('void f({String t = "x,y", Size s = const Size(1, 2)}) {}', "void f({String t, Size s}) {}"),
])
def test_a_default_is_not_hashed(with_default: str, without: str) -> None:
    assert _hashed(with_default) == _hashed(without)


def test_a_named_parameter_hashes_its_name_and_type() -> None:
    """A caller writes a named parameter's name (``f(loud: true)``), so it is
    hashed, as ``name:Type`` (stable-id v17); its default is not."""
    assert _hashed("int f({bool loud = false}) => 1;") == "({loud:bool})int"
    assert _hashed("void f({bool loud = false}) {}") != _hashed("void f({bool quiet}) {}")
    assert _hashed("void f({bool loud = false}) {}") != _hashed("void f({int loud = 1}) {}")


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
