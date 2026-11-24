# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/csharp.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/swift.py
"""A built-in type and its other spelling hash alike in a typed stable id.

WI-juzom (owner decision for stable-id scheme v19: one type, one spelling in
the hash). C#'s keyword types are aliases of framework types (C# language
specification, "Simple types" and "Native-sized integers"): ``string`` is
``System.String``, ``int`` is ``System.Int32``, and so on. Until v19 a C# type
was hashed as written (WI-ririg), so ``F(string s)`` and ``F(System.String s)``
hashed apart. The alias table now enters the file's ``FileTypeNames`` as its
defaults (the mechanism WI-palus built for imports), so the framework
spelling is written as the keyword:

- ``System.String`` and ``global::System.String`` always;
- the bare ``String`` only when the file has ``using System;`` and neither
  declares a ``String`` nor aliases the name.

Controls: a ``String`` of another namespace, a bare ``String`` with no
``using System;``, a ``String`` the file declares, and ``dynamic`` (not an
alias of ``object`` in the specification's table) stay apart.

Swift: ``Swift`` is the standard library's module, so ``Swift.Int`` and a
bare ``Int`` name one type unless the file declares its own ``Int``. Each
``Swift.X`` the file writes is a default (``Swift.X`` -> ``X``), and a Swift
type text has no labels to skip (``[Swift.String: Swift.Int]`` is a
dictionary, its key a type), so a dictionary's key is written through the
file's names too. Controls: a file that declares ``Int`` (``Swift.Int`` is
then not its ``Int``), another module's ``Int``, and a tuple's element labels.

Each case declares one symbol in two files at the same path; ``SAME`` asserts
one id, ``APART`` two.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_lang_mainstream.csharp import analyze_csharp
from hypergumbo_lang_mainstream.swift import analyze_swift

_ANALYZERS: dict[str, tuple[str, Callable[[Path], AnalysisResult]]] = {
    "csharp": ("C.cs", analyze_csharp),
    "swift": ("s.swift", analyze_swift),
}


def _id(root: Path, lang: str, text: str, name: str) -> str:
    fname, analyze = _ANALYZERS[lang]
    root.mkdir(parents=True)
    (root / fname).write_text(text)
    found = [s for s in analyze(root).symbols if s.name == name]
    assert len(found) == 1, [s.name for s in analyze(root).symbols]
    assert found[0].stable_id is not None, name
    return str(found[0].stable_id)


def _pair(tmp_path: Path, case: tuple[str, str, str, str]) -> tuple[str, str]:
    """The two ids; ``name`` is ``first|second`` when the displayed names
    differ (a Swift extension's member is named as the extension spells its
    type)."""
    lang, first, second, name = case
    first_name, _, second_name = name.partition("|")
    return (_id(tmp_path / "x", lang, first, first_name),
            _id(tmp_path / "y", lang, second, second_name or first_name))


def _cs(body: str, usings: str = "using System;\n") -> str:
    return usings + "namespace P { public class C { " + body + " } }\n"


def _sw(body: str) -> str:
    return "struct B {\n    " + body + "\n}\n"


#: (language, first, second, symbol): one declaration, one type spelled two ways.
SAME: dict[str, tuple[str, str, str, str]] = {
    "cs-qualified": ("csharp", _cs("public void F(string s) {}"),
                     _cs("public void F(System.String s) {}"), "C.F"),
    "cs-qualified-no-using": ("csharp", _cs("public void F(string s) {}", ""),
                              _cs("public void F(System.String s) {}", ""), "C.F"),
    "cs-global": ("csharp", _cs("public void F(string s) {}"),
                  _cs("public void F(global::System.String s) {}"), "C.F"),
    "cs-bare-under-using": ("csharp", _cs("public void F(string s) {}"),
                            _cs("public void F(String s) {}"), "C.F"),
    "cs-return": ("csharp", _cs("public object F(int a) { return null; }"),
                  _cs("public System.Object F(Int32 a) { return null; }"), "C.F"),
    "cs-generic": ("csharp", _cs("public void F(System.Collections.Generic.List<long> x) {}"),
                   _cs("public void F(System.Collections.Generic.List<System.Int64> x) {}"), "C.F"),
    "cs-array-nullable": ("csharp", _cs("public void F(int[] a, int? b) {}"),
                          _cs("public void F(System.Int32[] a, Int32? b) {}"), "C.F"),
    "cs-tuple": ("csharp", _cs("public void F((int, string) t) {}"),
                 _cs("public void F((Int32, System.String) t) {}"), "C.F"),
    "cs-ref": ("csharp", _cs("public void F(ref int a, out string b) { b = null; }"),
               _cs("public void F(ref Int32 a, out System.String b) { b = null; }"), "C.F"),
    "cs-property": ("csharp", _cs("public int P1 { get; set; }"),
                    _cs("public System.Int32 P1 { get; set; }"), "C.P1"),
    "cs-field": ("csharp", _cs("public string Fld;"), _cs("public String Fld;"), "C.Fld"),
    "cs-native-int": ("csharp", _cs("public void F(nint p, nuint q) {}"),
                      _cs("public void F(IntPtr p, System.UIntPtr q) {}"), "C.F"),
    "cs-every-simple-type": (
        "csharp",
        _cs("public void F(bool a, byte b, sbyte c, char d, decimal e, double f, float g, "
            "int h, uint i, long j, ulong k, short l, ushort m, object n, string o) {}"),
        _cs("public void F(Boolean a, Byte b, SByte c, Char d, Decimal e, Double f, Single g, "
            "Int32 h, UInt32 i, Int64 j, UInt64 k, Int16 l, UInt16 m, Object n, String o) {}"),
        "C.F"),
    # a file declaring its own String still means the framework's by System.String
    "cs-declared-qualified": ("csharp", _cs("public void F(string s) {} } public class String {"),
                              _cs("public void F(System.String s) {} } public class String {"),
                              "C.F"),
    # a using alias named String still names what it aliases under ``using System;``
    "cs-alias-over-using": ("csharp", _cs("public void F(String s) {}",
                                          "using System;\nusing String = MyLib.Text;\n"),
                            _cs("public void F(MyLib.Text s) {}",
                                "using System;\nusing String = MyLib.Text;\n"), "C.F"),
    # ``global using System;`` is a using
    "cs-global-using": ("csharp", _cs("public void F(string s) {}", "global using System;\n"),
                        _cs("public void F(String s) {}", "global using System;\n"), "C.F"),
    # Swift: the standard library's module qualifier, wherever a type is hashed
    "swift-param": ("swift", _sw("func f(_ x: Int) {}"), _sw("func f(_ x: Swift.Int) {}"), "B.f"),
    "swift-return": ("swift", _sw("func f(_ x: Int) -> String { \"\" }"),
                     _sw("func f(_ x: Swift.Int) -> Swift.String { \"\" }"), "B.f"),
    "swift-dictionary": ("swift", _sw("func f(_ m: [String: Int]) {}"),
                         _sw("func f(_ m: [Swift.String: Swift.Int]) {}"), "B.f"),
    "swift-generic": ("swift", _sw("func f(_ r: Result<Int, Error>) {}"),
                      _sw("func f(_ r: Swift.Result<Swift.Int, Swift.Error>) {}"), "B.f"),
    "swift-nested": ("swift", _sw("func f(_ i: String.Index) {}"),
                     _sw("func f(_ i: Swift.String.Index) {}"), "B.f"),
    "swift-function-type": ("swift", _sw("func f(_ c: (Int) -> Bool) {}"),
                            _sw("func f(_ c: (Swift.Int) -> Swift.Bool) {}"), "B.f"),
    "swift-optional-inout-variadic": ("swift", _sw("func f(_ o: Int?, _ a: inout Int, _ b: Int...) {}"),
                                      _sw("func f(_ o: Swift.Int?, _ a: inout Swift.Int, "
                                          "_ b: Swift.Int...) {}"), "B.f"),
    "swift-bound": ("swift", _sw("func f<T: Equatable>(_ t: T) {}"),
                    _sw("func f<T: Swift.Equatable>(_ t: T) {}"), "B.f"),
    "swift-property": ("swift", _sw("var p: Int = 0"), _sw("var p: Swift.Int = 0"), "B.p"),
    "swift-extension": ("swift", "extension Int { func g() {} }\n",
                        "extension Swift.Int { func g() {} }\n", "Int.g|Swift.Int.g"),
    # a dictionary key named by a kind import (no label to skip in Swift)
    "swift-kind-import-key": ("swift", "import struct Foundation.URL\n" + _sw("func f(_ m: [URL: Int]) {}"),
                              "import struct Foundation.URL\n"
                              + _sw("func f(_ m: [Foundation.URL: Int]) {}"), "B.f"),
}

APART: dict[str, tuple[str, str, str, str]] = {
    # no ``using System;``: a bare String may be any namespace's
    "cs-bare-no-using": ("csharp", _cs("public void F(string s) {}", ""),
                         _cs("public void F(String s) {}", ""), "C.F"),
    # a String of another namespace is another type
    "cs-other-namespace": ("csharp", _cs("public void F(string s) {}"),
                           _cs("public void F(MyLib.String s) {}"), "C.F"),
    "cs-other-namespace-system": ("csharp", _cs("public void F(System.String s) {}"),
                                  _cs("public void F(MyLib.System.String s) {}"), "C.F"),
    # a String the file declares shadows System's
    "cs-declared-bare": ("csharp", _cs("public void F(string s) {} } public class String {"),
                         _cs("public void F(String s) {} } public class String {"), "C.F"),
    # a using alias named String wins over the namespace using
    "cs-alias-wins": ("csharp", _cs("public void F(string s) {}",
                                    "using System;\nusing String = MyLib.Text;\n"),
                      _cs("public void F(String s) {}",
                          "using System;\nusing String = MyLib.Text;\n"), "C.F"),
    # dynamic is not in the alias table
    "cs-dynamic": ("csharp", _cs("public void F(object o) {}"),
                   _cs("public void F(dynamic o) {}"), "C.F"),
    # a ``using static System.Math;`` imports members, not the namespace
    "cs-using-static": ("csharp", _cs("public void F(string s) {}", "using static System.Math;\n"),
                        _cs("public void F(String s) {}", "using static System.Math;\n"), "C.F"),
    # two built-in types stay two
    "cs-two-types": ("csharp", _cs("public void F(int a) {}"),
                     _cs("public void F(System.Int64 a) {}"), "C.F"),
    # a file that declares Int: its Int is not Swift.Int
    "swift-declared-shadows": ("swift", "struct Int {}\n" + _sw("func f(_ x: Int) {}"),
                               "struct Int {}\n" + _sw("func f(_ x: Swift.Int) {}"), "B.f"),
    # another module's Int, and an Int under another qualifier that ends in Swift
    "swift-other-module": ("swift", _sw("func f(_ x: Int) {}"), _sw("func f(_ x: MyMod.Int) {}"), "B.f"),
    "swift-nested-swift": ("swift", _sw("func f(_ x: Int, _ y: Swift.Int) {}"),
                           _sw("func f(_ x: Int, _ y: MyMod.Swift.Int) {}"), "B.f"),
    # a file that declares a type named Swift (its Int declared in another
    # file's extension): its Swift.Int is its own
    "swift-declared-swift": ("swift", "enum Swift {}\n" + _sw("func f(_ x: Int) {}"),
                             "enum Swift {}\n" + _sw("func f(_ x: Swift.Int) {}"), "B.f"),
    "swift-two-types": ("swift", _sw("func f(_ x: Swift.Int) {}"), _sw("func f(_ x: Int64) {}"), "B.f"),
    # a tuple's element labels stay in the hash
    "swift-tuple-labels": ("swift", _sw("func f(_ t: (a: Int, b: Int)) {}"),
                           _sw("func f(_ t: (c: Int, d: Int)) {}"), "B.f"),
}


@pytest.mark.parametrize("case", sorted(SAME))
def test_two_spellings_of_one_built_in_type_hash_alike(tmp_path: Path, case: str) -> None:
    first, second = _pair(tmp_path, SAME[case])
    assert first == second


@pytest.mark.parametrize("case", sorted(APART))
def test_another_type_or_an_unresolved_spelling_hashes_apart(tmp_path: Path, case: str) -> None:
    first, second = _pair(tmp_path, APART[case])
    assert first != second
