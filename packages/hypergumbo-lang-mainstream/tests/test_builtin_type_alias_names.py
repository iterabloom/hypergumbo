# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/csharp.py
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
alias of ``object`` in the specification's table) stay apart. Each case
declares one symbol in two files at the same path; ``SAME`` asserts one id,
``APART`` two.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_lang_mainstream.csharp import analyze_csharp

_ANALYZERS: dict[str, tuple[str, Callable[[Path], AnalysisResult]]] = {
    "csharp": ("C.cs", analyze_csharp),
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
    lang, first, second, name = case
    return _id(tmp_path / "x", lang, first, name), _id(tmp_path / "y", lang, second, name)


def _cs(body: str, usings: str = "using System;\n") -> str:
    return usings + "namespace P { public class C { " + body + " } }\n"


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
}


@pytest.mark.parametrize("case", sorted(SAME))
def test_two_spellings_of_one_built_in_type_hash_alike(tmp_path: Path, case: str) -> None:
    first, second = _pair(tmp_path, SAME[case])
    assert first == second


@pytest.mark.parametrize("case", sorted(APART))
def test_another_type_or_an_unresolved_spelling_hashes_apart(tmp_path: Path, case: str) -> None:
    first, second = _pair(tmp_path, APART[case])
    assert first != second
