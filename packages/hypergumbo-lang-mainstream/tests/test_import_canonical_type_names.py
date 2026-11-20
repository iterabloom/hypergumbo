# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two spellings of one type, through the file's imports, hash alike.

Stable-id scheme v17 (owner ruling 2026-11-20): a type name the file imports
is written as the path it names before it is hashed, and a qualified spelling
of a type the language imports into every file is written as its simple name,
so ``List<String>`` and ``List<java.lang.String>``, ``List<Callable>`` (with
``import java.util.concurrent.Callable``) and
``List<java.util.concurrent.Callable>``, Rust's ``impl fmt::Display`` (``use
std::fmt``) and ``impl std::fmt::Display`` are one declaration. Until v17 a
type inside a compound type (a generic argument, a function type, a tuple, an
array, a union) and every type a field, an impl header or a type-parameter
bound hashes as written hashed as written, so the two spellings hashed apart.

Local information only: a name the file does not import (or imports only by a
star / glob / module import), and a name the file declares itself, stays as
written; two same-named types stay two (``a.Foo`` / ``b.Foo``). Reordering the
imports, adding an unrelated one, or importing what is already written
qualified moves no id. Each case below declares one symbol in two files at the
same path; ``SAME`` asserts one id, ``APART`` two.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_lang_mainstream.csharp import analyze_csharp
from hypergumbo_lang_mainstream.groovy import analyze_groovy
from hypergumbo_lang_mainstream.java import analyze_java
from hypergumbo_lang_mainstream.js_ts import analyze_javascript
from hypergumbo_lang_mainstream.jupyter import analyze_jupyter
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin
from hypergumbo_lang_mainstream.py import analyze_python
from hypergumbo_lang_mainstream.rust import analyze_rust
from hypergumbo_lang_mainstream.rust_scip import compute_rust_stable_id_from_source
from hypergumbo_lang_mainstream.scala import analyze_scala
from hypergumbo_lang_mainstream.swift import analyze_swift


def _notebook(root: Path) -> AnalysisResult:
    return analyze_jupyter(root)


_ANALYZERS: dict[str, tuple[str, Callable[[Path], AnalysisResult]]] = {
    "java": ("C.java", analyze_java),
    "kotlin": ("K.kt", analyze_kotlin),
    "scala": ("S.scala", analyze_scala),
    "groovy": ("G.groovy", analyze_groovy),
    "rust": ("lib.rs", analyze_rust),
    "python": ("m.py", analyze_python),
    "ts": ("t.ts", analyze_javascript),
    "csharp": ("C.cs", analyze_csharp),
    "swift": ("s.swift", analyze_swift),
    "notebook": ("n.ipynb", _notebook),
}


def _write(root: Path, lang: str, text: str) -> None:
    fname = _ANALYZERS[lang][0]
    root.mkdir(parents=True)
    if lang == "notebook":
        text = json.dumps({
            "cells": [{"cell_type": "code", "source": text, "metadata": {},
                       "outputs": [], "execution_count": None}],
            "metadata": {"kernelspec": {"language": "python", "name": "python3"}},
            "nbformat": 4, "nbformat_minor": 5,
        })
    (root / fname).write_text(text)


def _id(root: Path, lang: str, text: str, name: str) -> str:
    _write(root, lang, text)
    symbols = _ANALYZERS[lang][1](root).symbols
    found = [s for s in symbols if s.name == name]
    assert len(found) == 1, [s.name for s in symbols]
    assert found[0].stable_id is not None, name
    return found[0].stable_id


def _pair(tmp_path: Path, lang: str, first: str, second: str, name: str) -> tuple[str, str]:
    """The two ids; ``name`` is ``first|second`` when the displayed names
    differ (a Swift extension's member is named as the extension spells its
    type)."""
    first_name, _, second_name = name.partition("|")
    return (_id(tmp_path / "x", lang, first, first_name),
            _id(tmp_path / "y", lang, second, second_name or first_name))


_J = "package p;\nimport java.util.List;\nimport java.util.Map;\nimport java.util.concurrent.Callable;\n"
_K = "package p\nimport java.util.concurrent.Callable\n"
_S = "package p\nimport java.util.concurrent.Callable\n"
_G = "package p\nimport java.util.concurrent.Callable\n"
_R = "use std::fmt;\nuse std::sync::Arc;\nuse std::collections::HashMap;\npub struct X;\n"
_P = "import typing\nimport collections.abc as cabc\nfrom typing import List, Optional\nfrom collections.abc import Callable\n"
_T = 'import { Foo } from "./foo";\nimport * as ns from "./foo";\n'

#: (language, first, second, symbol): one declaration, its types spelled two
#: ways the file's imports make one type. Each was hashed apart before v17.
SAME: dict[str, tuple[str, str, str, str]] = {
    # Java: java.lang defaults and single-type imports, wherever a type is hashed
    "java-generic-default": ("java", _J + "class C { void f(List<String> x) {} }\n",
                             _J + "class C { void f(List<java.lang.String> x) {} }\n", "C.f"),
    "java-generic-import": ("java", _J + "class C { void f(List<Callable> x) {} }\n",
                            _J + "class C { void f(List<java.util.concurrent.Callable> x) {} }\n", "C.f"),
    "java-return": ("java", _J + "class C { Map<String, Callable> f() { return null; } }\n",
                    _J + "class C { Map<java.lang.String, java.util.concurrent.Callable> f() "
                         "{ return null; } }\n", "C.f"),
    "java-wildcard": ("java", _J + "class C { void f(List<? extends Callable> x) {} }\n",
                      _J + "class C { void f(List<? extends java.util.concurrent.Callable> x) {} }\n",
                      "C.f"),
    "java-field": ("java", _J + "class C { List<Callable> f; }\n",
                   _J + "class C { java.util.List<java.util.concurrent.Callable> f; }\n", "C.f"),
    "java-bound": ("java", _J + "class C { <T extends Comparable<String>> void f(T t) {} }\n",
                   _J + "class C { <T extends java.lang.Comparable<java.lang.String>> void f(T t) {} }\n",
                   "C.f"),
    "java-anonymous": (
        "java",
        _J + "class C { Object o = new Callable<String>() { public String call() { return null; } }; }\n",
        _J + "class C { Object o = new java.util.concurrent.Callable<java.lang.String>() "
             "{ public String call() { return null; } }; }\n", "C.call"),
    "java-nested-through-import": ("java", _J + "class C { void f(List<Map.Entry<String, String>> e) {} }\n",
                                   _J + "class C { void f(List<java.util.Map.Entry<String, String>> e) {} }\n",
                                   "C.f"),
    # an import of what is written qualified changes nothing
    "java-import-added": ("java", "package p;\nclass C { void f(List<java.util.concurrent.Callable> x) {} }\n",
                          _J + "class C { void f(List<java.util.concurrent.Callable> x) {} }\n", "C.f"),
    "java-reordered": ("java", _J + "class C { void f(List<Callable> x) {} }\n",
                       "package p;\nimport java.util.concurrent.Callable;\nimport java.io.File;\n"
                       "import java.util.List;\nclass C { void f(List<Callable> x) {} }\n", "C.f"),
    # Kotlin: defaults (kotlin.*, kotlin.collections.*, java.lang.*), imports, aliases
    "kt-function-type": ("kotlin", _K + "class K { fun f(cb: (String) -> Unit) {} }\n",
                         _K + "class K { fun f(cb: (kotlin.String) -> kotlin.Unit) {} }\n", "K.f"),
    "kt-generic-import": ("kotlin", _K + "fun f(xs: List<Callable<Int>>) {}\n",
                          _K + "fun f(xs: kotlin.collections.List<java.util.concurrent.Callable<Int>>) {}\n",
                          "f"),
    "kt-alias": ("kotlin", "package p\nimport java.util.concurrent.Callable as Job\nfun f(xs: List<Job<Int>>) {}\n",
                 "package p\nimport java.util.concurrent.Callable as Job\n"
                 "fun f(xs: List<java.util.concurrent.Callable<Int>>) {}\n", "f"),
    "kt-receiver": ("kotlin", _K + "fun f(cb: String.() -> Unit) {}\n",
                    _K + "fun f(cb: kotlin.String.() -> Unit) {}\n", "f"),
    "kt-extension-receiver": ("kotlin", _K + "fun List<Callable<Int>>.f() {}\n",
                              _K + "fun List<java.util.concurrent.Callable<Int>>.f() {}\n", "f"),
    "kt-property": ("kotlin", _K + "class K { val p: Map<String, Callable<Int>> = TODO() }\n",
                    _K + "class K { val p: Map<String, java.util.concurrent.Callable<Int>> = TODO() }\n",
                    "K.p"),
    "kt-java-lang-default": ("kotlin", _K + "fun f(t: List<Thread>) {}\n",
                             _K + "fun f(t: List<java.lang.Thread>) {}\n", "f"),
    # Scala: java.lang, scala, Predef defaults and selector imports
    "scala-generic": ("scala", _S + "class S { def f(xs: List[Callable[Int]]): Unit = () }\n",
                      _S + "class S { def f(xs: scala.collection.immutable.List[java.util.concurrent.Callable"
                           "[scala.Int]]): Unit = () }\n", "S.f"),
    "scala-predef": ("scala", _S + "class S { def f(m: Map[String, Int]): Unit = () }\n",
                     _S + "class S { def f(m: Map[java.lang.String, Int]): Unit = () }\n", "S.f"),
    "scala-selector": ("scala", "package p\nimport java.util.{List => JList}\n"
                                "class S { def f(xs: Option[JList[Int]]): Unit = () }\n",
                       "package p\nimport java.util.{List => JList}\n"
                       "class S { def f(xs: Option[java.util.List[Int]]): Unit = () }\n", "S.f"),
    "scala-field": ("scala", _S + "class S { val v: Option[Callable[Int]] = None }\n",
                    _S + "class S { val v: Option[java.util.concurrent.Callable[Int]] = None }\n", "S.v"),
    "scala-function": ("scala", _S + "class S { def f(g: Callable[Int] => Int): Unit = () }\n",
                       _S + "class S { def f(g: java.util.concurrent.Callable[Int] => Int): Unit = () }\n",
                       "S.f"),
    "scala3-as": ("scala", "package p\nimport java.util.concurrent.Callable as Job\n"
                           "class S { def f(xs: List[Job[Int]]): Unit = () }\n",
                  "package p\nimport java.util.concurrent.Callable as Job\n"
                  "class S { def f(xs: List[java.util.concurrent.Callable[Int]]): Unit = () }\n", "S.f"),
    # Groovy: java.lang, java.util and java.math defaults, imports
    "groovy-generic": ("groovy", _G + "class G { void f(List<String> x) {} }\n",
                       _G + "class G { void f(java.util.List<java.lang.String> x) {} }\n", "G.f"),
    "groovy-import": ("groovy", _G + "class G { List<Callable> f() { null } }\n",
                      _G + "class G { List<java.util.concurrent.Callable> f() { null } }\n", "G.f"),
    "groovy-math": ("groovy", _G + "class G { void f(List<BigDecimal> x) {} }\n",
                    _G + "class G { void f(List<java.math.BigDecimal> x) {} }\n", "G.f"),
    # Rust: use declarations and the prelude, heads included (Rust hashes them as written)
    "rust-plain": ("rust", _R + "pub fn f(m: HashMap<u8, u8>) {}\n",
                   _R + "pub fn f(m: std::collections::HashMap<u8, u8>) {}\n", "f"),
    "rust-generic": ("rust", _R + "pub fn f(v: Vec<Arc<u8>>) {}\n",
                     _R + "pub fn f(v: std::vec::Vec<std::sync::Arc<u8>>) {}\n", "f"),
    "rust-prelude": ("rust", _R + "pub fn f(v: Option<String>) {}\n",
                     _R + "pub fn f(v: core::option::Option<alloc::string::String>) {}\n", "f"),
    "rust-fn-pointer": ("rust", _R + "pub fn f(g: fn(Arc<u8>) -> u8) {}\n",
                        _R + "pub fn f(g: fn(std::sync::Arc<u8>) -> u8) {}\n", "f"),
    "rust-impl-header": (
        "rust", _R + "impl fmt::Display for X { fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result { Ok(()) } }\n",
        _R + "impl std::fmt::Display for X { fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result "
             "{ Ok(()) } }\n", "X::fmt"),
    "rust-field": ("rust", _R + "pub struct F { pub a: Arc<u8> }\n",
                   _R + "pub struct F { pub a: std::sync::Arc<u8> }\n", "F::a"),
    "rust-const": ("rust", _R + "pub const C: Option<Arc<u8>> = None;\n",
                   _R + "pub const C: Option<std::sync::Arc<u8>> = None;\n", "C"),
    "rust-alias": ("rust", _R + "pub type T = HashMap<u8, Arc<u8>>;\n",
                   _R + "pub type T = std::collections::HashMap<u8, std::sync::Arc<u8>>;\n", "T"),
    "rust-use-as": ("rust", "use std::sync::Arc as Shared;\npub fn f(v: Vec<Shared<u8>>) {}\n",
                    "use std::sync::Arc as Shared;\npub fn f(v: Vec<std::sync::Arc<u8>>) {}\n", "f"),
    "rust-use-group": ("rust", "use std::{fmt, sync::{Arc}};\npub fn f(v: Box<dyn fmt::Debug>, a: Arc<u8>) {}\n",
                       "use std::{fmt, sync::{Arc}};\n"
                       "pub fn f(v: Box<dyn std::fmt::Debug>, a: std::sync::Arc<u8>) {}\n", "f"),
    # Python: from-imports, module aliases
    "py-subscript": ("python", _P + "def f(x: Optional[List[int]]) -> None: ...\n",
                     _P + "def f(x: Optional[typing.List[int]]) -> None: ...\n", "f"),
    "py-module-alias": ("python", _P + "def f(x: dict[str, Callable]) -> None: ...\n",
                        _P + "def f(x: dict[str, cabc.Callable]) -> None: ...\n", "f"),
    "py-relative": ("python", "from . import mod\nfrom .mod import Foo\ndef f(x: list[Foo]) -> None: ...\n",
                    "from . import mod\nfrom .mod import Foo\ndef f(x: list[mod.Foo]) -> None: ...\n", "f"),
    "py-from-alias": ("python", "import typing\nfrom typing import List as L, Optional\n"
                               "def f(x: Optional[L[int]]) -> None: ...\n",
                      "import typing\nfrom typing import List as L, Optional\n"
                      "def f(x: Optional[typing.List[int]]) -> None: ...\n", "f"),
    "notebook": ("notebook", _P + "def f(x: Optional[List[int]]) -> None: ...\n",
                 _P + "def f(x: Optional[typing.List[int]]) -> None: ...\n", "f"),
    # TypeScript: a named import, a namespace import, a default import
    "ts-generic": ("ts", _T + "export function f(xs: Array<Foo>): void {}\n",
                   _T + "export function f(xs: Array<ns.Foo>): void {}\n", "f"),
    "ts-function-type": ("ts", _T + "export function f(cb: (x: Foo) => void): void {}\n",
                         _T + "export function f(cb: (x: ns.Foo) => void): void {}\n", "f"),
    "ts-field": ("ts", _T + "export class C { field: Array<Foo>; }\n",
                 _T + "export class C { field: Array<ns.Foo>; }\n", "C.field"),
    "ts-interface": ("ts", _T + "export interface I { m(x: Foo): Foo[]; }\n",
                     _T + "export interface I { m(x: ns.Foo): ns.Foo[]; }\n", "I.m"),
    "ts-alias": ("ts", 'import { Foo as Bar } from "./foo";\nimport * as ns from "./foo";\n'
                       "export function f(xs: Array<Bar>): void {}\n",
                 'import { Foo as Bar } from "./foo";\nimport * as ns from "./foo";\n'
                 "export function f(xs: Array<ns.Foo>): void {}\n", "f"),
    "ts-default": ("ts", 'import Foo from "./foo";\nimport * as ns from "./foo";\n'
                         "export function f(xs: Array<Foo>): void {}\n",
                   'import Foo from "./foo";\nimport * as ns from "./foo";\n'
                   "export function f(xs: Array<ns.default>): void {}\n", "f"),
    # C#: a using alias names one type (a namespace using binds no written name)
    "cs-alias": ("csharp", "using Str = System.String;\nnamespace P { public class C { public void F(Str s) {} } }\n",
                 "using Str = System.String;\nnamespace P { public class C { public void F(System.String s) {} } }\n",
                 "C.F"),
    "cs-namespace-alias": ("csharp", "using Gen = System.Collections.Generic;\n"
                                     "namespace P { public class C { public Gen.List<int> F; } }\n",
                           "using Gen = System.Collections.Generic;\n"
                           "namespace P { public class C { public System.Collections.Generic.List<int> F; } }\n",
                           "C.F"),
    "cs-tuple": ("csharp", "using Str = System.String;\n"
                           "namespace P { public class C { public void F((Str a, int b) t) {} } }\n",
                 "using Str = System.String;\n"
                 "namespace P { public class C { public void F((System.String a, int b) t) {} } }\n", "C.F"),
    "cs-property": ("csharp", "using Str = System.String;\n"
                              "namespace P { public class C { public Str P1 { get; set; } } }\n",
                    "using Str = System.String;\n"
                    "namespace P { public class C { public System.String P1 { get; set; } } }\n", "C.P1"),
    # Swift: a kind import names one type (a module import binds no written name)
    "swift-kind-import": ("swift", "import struct Foundation.URL\nclass S { func f(u: [URL]) {} }\n",
                          "import struct Foundation.URL\nclass S { func f(u: [Foundation.URL]) {} }\n", "S.f"),
    "swift-property": ("swift", "import struct Foundation.URL\nclass S { var u: URL? }\n",
                       "import struct Foundation.URL\nclass S { var u: Foundation.URL? }\n", "S.u"),
    "swift-extension": ("swift", "import struct Foundation.URL\nextension URL { func f() {} }\n",
                        "import struct Foundation.URL\nextension Foundation.URL { func f() {} }\n",
                        "URL.f|Foundation.URL.f"),
}

#: (language, first, second, symbol): spellings the file's own text does not
#: make one type, or two types; each must keep two ids.
APART: dict[str, tuple[str, str, str, str]] = {
    # two same-named types stay two
    "java-two-foos": ("java", "package p;\nclass C { void f(java.util.List<a.Foo> x) {} }\n",
                      "package p;\nclass C { void f(java.util.List<b.Foo> x) {} }\n", "C.f"),
    "rust-two-results": ("rust", "pub fn f() -> Box<std::fmt::Result> { todo!() }\n",
                         "pub fn f() -> Box<std::io::Result<()>> { todo!() }\n", "f"),
    # a star / glob / module import binds nothing
    "java-star": ("java", "package p;\nimport java.util.concurrent.*;\nclass C { void f(java.util.List<Callable> x) {} }\n",
                  "package p;\nimport java.util.concurrent.*;\n"
                  "class C { void f(java.util.List<java.util.concurrent.Callable> x) {} }\n", "C.f"),
    "kt-star": ("kotlin", "package p\nimport java.util.concurrent.*\nfun f(xs: List<Callable<Int>>) {}\n",
                "package p\nimport java.util.concurrent.*\nfun f(xs: List<java.util.concurrent.Callable<Int>>) {}\n",
                "f"),
    "scala-wildcard": ("scala", "package p\nimport java.util.concurrent._\n"
                                "class S { def f(xs: List[Callable[Int]]): Unit = () }\n",
                       "package p\nimport java.util.concurrent._\n"
                       "class S { def f(xs: List[java.util.concurrent.Callable[Int]]): Unit = () }\n", "S.f"),
    "rust-glob": ("rust", "use std::sync::*;\npub fn f(v: Vec<Arc<u8>>) {}\n",
                  "use std::sync::*;\npub fn f(v: Vec<std::sync::Arc<u8>>) {}\n", "f"),
    "py-star": ("python", "from typing import *\ndef f(x: Optional[List[int]]) -> None: ...\n",
                "from typing import *\ndef f(x: Optional[typing.List[int]]) -> None: ...\n", "f"),
    "cs-namespace-using": ("csharp", "using System;\nnamespace P { public class C { public void F(String s) {} } }\n",
                           "using System;\nnamespace P { public class C { public void F(System.String s) {} } }\n",
                           "C.F"),
    "swift-module-import": ("swift", "import Foundation\nclass S { func f(d: [Date]) {} }\n",
                            "import Foundation\nclass S { func f(d: [Foundation.Date]) {} }\n", "S.f"),
    # a star import binds no name, not even its package's last one
    "kt-star-package": ("kotlin", "package p\nimport java.util.concurrent.*\nfun f(xs: List<concurrent.Callable<Int>>) {}\n",
                        "package p\nimport java.util.concurrent.*\n"
                        "fun f(xs: List<java.util.concurrent.Callable<Int>>) {}\n", "f"),
    "scala-wildcard-package": ("scala", "package p\nimport java.util.concurrent._\n"
                                        "class S { def f(xs: List[concurrent.Callable[Int]]): Unit = () }\n",
                               "package p\nimport java.util.concurrent._\n"
                               "class S { def f(xs: List[java.util.concurrent.Callable[Int]]): Unit = () }\n",
                               "S.f"),
    # a Swift submodule import is a module import: it binds nothing
    "swift-submodule-import": ("swift", "import UIKit.UIGestureRecognizerSubclass\n"
                                       "class S { func f(u: [UIGestureRecognizerSubclass.X]) {} }\n",
                               "import UIKit.UIGestureRecognizerSubclass\n"
                               "class S { func f(u: [UIKit.UIGestureRecognizerSubclass.X]) {} }\n", "S.f"),
    # a relative import is not the absolute module of the same name
    "py-relative-vs-absolute": ("python", "import mod\nfrom .mod import Foo\ndef f(x: list[Foo]) -> None: ...\n",
                                "import mod\nfrom .mod import Foo\ndef f(x: list[mod.Foo]) -> None: ...\n", "f"),
    # a declared type shadows the import and the default: it keeps its name
    "java-declared-shadows-default": (
        "java", "package p;\nclass C { void f(java.util.List<String> x) {} }\nclass String {}\n",
        "package p;\nclass C { void f(java.util.List<java.lang.String> x) {} }\nclass String {}\n", "C.f"),
    "kt-declared-shadows-import": (
        "kotlin", _K + "fun f(xs: List<Callable<Int>>) {}\nclass Callable<T>\n",
        _K + "fun f(xs: List<java.util.concurrent.Callable<Int>>) {}\nclass Callable<T>\n", "f"),
    # a scala alias whose target moved between library versions is not folded
    "scala-seq-target": ("scala", "class S { def f(xs: Option[Seq[Int]]): Unit = () }\n",
                         "class S { def f(xs: Option[scala.collection.immutable.Seq[Int]]): Unit = () }\n",
                         "S.f"),
    # a kotlin java.lang name the kotlin default shadows is another type
    "kt-shadowed-java-lang": ("kotlin", "fun f(xs: List<String>) {}\n",
                              "fun f(xs: List<java.lang.String>) {}\n", "f"),
    # scala's StringBuilder is not java.lang's
    "scala-string-builder": ("scala", "class S { def f(xs: Option[StringBuilder]): Unit = () }\n",
                             "class S { def f(xs: Option[java.lang.StringBuilder]): Unit = () }\n", "S.f"),
    # a scala symbol literal opens no string, and a name before ``:`` is a label
    "ts-property-key": ("ts", _T + "export function f(o: { Foo: number }): void {}\n",
                        _T + "export function f(o: { Bar: number }): void {}\n", "f"),
}


@pytest.mark.parametrize("case", sorted(SAME))
def test_two_spellings_of_one_type_hash_alike(tmp_path: Path, case: str) -> None:
    lang, first, second, name = SAME[case]
    a, b = _pair(tmp_path, lang, first, second, name)
    assert a == b


@pytest.mark.parametrize("case", sorted(APART))
def test_two_types_or_an_unresolved_spelling_hash_apart(tmp_path: Path, case: str) -> None:
    lang, first, second, name = APART[case]
    a, b = _pair(tmp_path, lang, first, second, name)
    assert a != b


def test_an_import_change_that_changes_no_referent_moves_no_id(tmp_path: Path) -> None:
    """Reordering imports and adding unrelated ones keep every id, in each
    language whose imports name types."""
    cases = {
        "java": (_J + "class C { void f(List<Callable> x) {} }\n",
                 "package p;\nimport java.io.File;\nimport java.util.concurrent.Callable;\n"
                 "import java.util.Map;\nimport java.util.List;\nclass C { void f(List<Callable> x) {} }\n",
                 "C.f"),
        "kotlin": (_K + "fun f(xs: List<Callable<Int>>) {}\n",
                   "package p\nimport java.io.File\nimport java.util.concurrent.Callable\n"
                   "fun f(xs: List<Callable<Int>>) {}\n", "f"),
        "rust": ("use std::sync::Arc;\nuse std::fmt;\npub fn f(v: Vec<Arc<u8>>) {}\n",
                 "use std::fmt;\nuse std::io;\nuse std::sync::Arc;\npub fn f(v: Vec<Arc<u8>>) {}\n", "f"),
        "python": ("from typing import List\nfrom typing import Optional\ndef f(x: Optional[List[int]]) -> None: ...\n",
                   "import os\nfrom typing import Optional, List\ndef f(x: Optional[List[int]]) -> None: ...\n",
                   "f"),
    }
    for lang, (first, second, name) in cases.items():
        a, b = _pair(tmp_path / lang, lang, first, second, name)
        assert a == b, lang


def test_a_name_imported_from_two_places_is_left_as_written(tmp_path: Path) -> None:
    """Two scopes' ``use`` of one name to two paths: neither applies, so the
    name stays as written and does not take one of the two paths."""
    text = ("pub fn f(v: Vec<Arc<u8>>) {}\nmod a { use std::sync::Arc; }\n"
            "mod b { use std::rc::Rc as Arc; }\n")
    bare = "pub fn f(v: Vec<Arc<u8>>) {}\nmod a {}\nmod b {}\n"
    a, b = _pair(tmp_path, "rust", text, bare, "f")
    assert a == b


def test_rust_scip_parity_holds_through_the_use_declarations(tmp_path: Path) -> None:
    """``compute_rust_stable_id_from_source`` mints what rust.py mints for an
    imported type."""
    text = _R + "pub fn f(m: HashMap<u8, Arc<u8>>) {}\n"
    _write(tmp_path / "x", "rust", text)
    (sym,) = [s for s in analyze_rust(tmp_path / "x").symbols if s.name == "f"]
    assert sym.span is not None
    assert compute_rust_stable_id_from_source(
        text.encode(), sym.span.start_line, sym.span.end_line, rel_path="lib.rs",
    ) == sym.stable_id
