# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
"""A comment inside a declaration is not a stable-id input, in every language.

A typed stable id hashes its signature and scope texts, which the analyzers
read from the source, comments included: ``f(x: /* the id */ string)``, a doc
comment between a union's members, ``List</* item */ String>`` or a ``//``
comment inside a multi-line type were hashed, so editing a comment moved the
id (WI-sidus). Core reads ``/* ... */`` and a ``//`` comment that a line break
ends as whitespace, both in ``layout_free`` and before the shared signature
normalizers split the text (a comment holding ``,`` or ``:`` would otherwise
split a parameter); the TypeScript tree leaves comment nodes out of its
hashed text. Each case is one file written with no comments and with them,
at the same path; every declared symbol keeps its stable id. Dart (read from
the tree) and Python (from ``ast``) never hashed a comment and are controls.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import pytest

from hypergumbo_lang_mainstream.cpp import analyze_cpp
from hypergumbo_lang_mainstream.csharp import analyze_csharp
from hypergumbo_lang_mainstream.go import analyze_go
from hypergumbo_lang_mainstream.java import analyze_java
from hypergumbo_lang_mainstream.js_ts import analyze_javascript
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin
from hypergumbo_lang_mainstream.php import analyze_php
from hypergumbo_lang_mainstream.py import analyze_python
from hypergumbo_lang_mainstream.rust import analyze_rust
from hypergumbo_lang_mainstream.scala import analyze_scala
from hypergumbo_lang_mainstream.swift import analyze_swift

_JAVA_PLAIN = 'import java.util.*;\nclass M {\n  Map<String, Integer> f(int a, List<String> b) { return null; }\n  void g(final int c) {}\n}\n'

_JAVA_COMMENTED = 'import java.util.*;\nclass M {\n  Map<String, /* v */ Integer> f(int /* count */ a, // the list\n      List</* item */ String> b) { return null; }\n  void g(final /* c */ int c) {}\n}\n'

_KOTLIN_PLAIN = 'class M {\n  fun f(a: Int, b: List<String>): Map<String, Int> = mapOf()\n  fun g(cb: (Int) -> Unit) {}\n}\n'

_KOTLIN_COMMENTED = 'class M {\n  fun f(a: /* count */ Int, // the list\n        b: List</* item */ String>): Map<String, /* v */ Int> = mapOf()\n  fun g(cb: (Int /* x */) -> Unit) {}\n}\n'

_SWIFT_PLAIN = 'struct M {\n  func f(a: Int, b: [String]) -> [String: Int] { [:] }\n  func g(cb: (Int) -> Void) {}\n}\n'

_SWIFT_COMMENTED = 'struct M {\n  func f(a: /* count */ Int, // the list\n         b: [/* item */ String]) -> [String: /* v */ Int] { [:] }\n  func g(cb: (Int /* x */) -> Void) {}\n}\n'

_CSHARP_PLAIN = 'using System.Collections.Generic;\nclass M {\n  Dictionary<string, int> F(int a, List<string> b) { return null; }\n  void G(ref int c) {}\n}\n'

_CSHARP_COMMENTED = 'using System.Collections.Generic;\nclass M {\n  Dictionary<string, /* v */ int> F(int /* count */ a, // the list\n      List</* item */ string> b) { return null; }\n  void G(ref /* c */ int c) {}\n}\n'

_GO_PLAIN = 'package m\nfunc F(a int, b []string) (map[string]int, error) { return nil, nil }\ntype T struct{}\nfunc (t *T) G(cb func(int) error) {}\n'

_GO_COMMENTED = 'package m\nfunc F(a /* count */ int, // the list\n\tb []string) (map[string] /* v */ int, error) { return nil, nil }\ntype T struct{}\nfunc (t * /* recv */ T) G(cb func(int /* x */) error) {}\n'

_RUST_PLAIN = 'pub struct T;\npub fn f(a: u32, b: Vec<String>) -> Option<u32> { None }\nimpl T { pub fn g(&self, cb: fn(u32) -> u32) {} }\n'

_RUST_COMMENTED = 'pub struct T;\npub fn f(a: /* count */ u32, // the list\n         b: Vec</* item */ String>) -> Option</* v */ u32> { None }\nimpl T { pub fn g(&self, cb: fn(u32 /* x */) -> u32) {} }\n'

_PHP_PLAIN = '<?php\nclass M {\n  public function f(int $a, array $b): ?string { return null; }\n  public function g(string|int $c = "x"): void {}\n}\n'

_PHP_COMMENTED = '<?php\nclass M {\n  public function f(int /* count */ $a, // the list\n      array $b): /* r */ ?string { return null; }\n  public function g(string| /* or */ int $c = "x"): void {}\n}\n'

_SCALA_PLAIN = 'class M {\n  def f(a: Int, b: List[String]): Map[String, Int] = Map()\n  def g(cb: Int => Unit): Unit = ()\n}\n'

_SCALA_COMMENTED = 'class M {\n  def f(a: /* count */ Int, // the list\n        b: List[/* item */ String]): Map[String, /* v */ Int] = Map()\n  def g(cb: Int /* x */ => Unit): Unit = ()\n}\n'

_CPP_PLAIN = '#include <map>\nstruct M { std::map<int, long> f(int a, const char *b); };\nstd::map<int, long> M::f(int a, const char *b) { return {}; }\nint g(int a, double b) { return 0; }\n'

_CPP_COMMENTED = '#include <map>\nstruct M { std::map<int, /* v */ long> f(int /* count */ a, // name\n  const char *b); };\nstd::map<int, /* v */ long> M::f(int /* count */ a, // name\n  const char *b) { return {}; }\nint g(int /* a */ a, double b /* b */) { return 0; }\n'

_TS_PLAIN = 'export interface I {\n  feature:\n  | \'sideBarChat\'\n  | \'inline\';\n  obj: { a: string; b: number };\n}\nexport function f(x: string, y: number): Map<string, number> { return null as any; }\nexport function g(s: string = "a /* not */ b", r = /x\\/\\*y/): void {}\nexport class K { m(cb: (e: number) => void): void {} }\n'

_TS_COMMENTED = 'export interface I {\n  feature:\n  /** generated in the sidebar */\n  | \'sideBarChat\'\n  // inline\n  | \'inline\';\n  obj: { a: string; /* the b */ b: number };\n}\nexport function f(x: /* the id */ string, y: number /* count */): Map</* key */ string, number> { return null as any; }\nexport function g(s: string = /* d */ "a /* not */ b", r = /x\\/\\*y/): void {}\nexport class K { m(cb: (e: number /* evt */) => void): void {} }\n'

_JS_PLAIN = 'export function f(x, y = 1, ...rest) {}\nexport class K { m(a, b) {} }\n'

_JS_COMMENTED = 'export function f(/** @type {string} */ x, y /* num */ = 1, /* r */ ...rest) {}\nexport class K { m(a /* first */, // line\n  b) {} }\n'

_PYTHON_PLAIN = 'from typing import Dict, List\ndef f(a: int, b: List[str]) -> Dict[str, int]:\n    return {}\nclass K:\n    def g(self, c: "int" = 1) -> None: ...\n'

_PYTHON_COMMENTED = 'from typing import Dict, List\ndef f(a: int,  # count\n      b: List[str]  # the list\n      ) -> Dict[str, int]:  # result\n    return {}\nclass K:\n    def g(self, c: "int" = 1  # c\n          ) -> None: ...\n'

_CASES: list[tuple[str, str, Callable[[Path], Any], str, str]] = [
    ("java", "M.java", analyze_java, _JAVA_PLAIN, _JAVA_COMMENTED),
    ("kotlin", "M.kt", analyze_kotlin, _KOTLIN_PLAIN, _KOTLIN_COMMENTED),
    ("swift", "M.swift", analyze_swift, _SWIFT_PLAIN, _SWIFT_COMMENTED),
    ("csharp", "M.cs", analyze_csharp, _CSHARP_PLAIN, _CSHARP_COMMENTED),
    ("go", "m.go", analyze_go, _GO_PLAIN, _GO_COMMENTED),
    ("rust", "lib.rs", analyze_rust, _RUST_PLAIN, _RUST_COMMENTED),
    ("php", "m.php", analyze_php, _PHP_PLAIN, _PHP_COMMENTED),
    ("scala", "M.scala", analyze_scala, _SCALA_PLAIN, _SCALA_COMMENTED),
    ("cpp", "m.cpp", analyze_cpp, _CPP_PLAIN, _CPP_COMMENTED),
    ("ts", "m.ts", analyze_javascript, _TS_PLAIN, _TS_COMMENTED),
    ("js", "m.js", analyze_javascript, _JS_PLAIN, _JS_COMMENTED),
    ("python", "m.py", analyze_python, _PYTHON_PLAIN, _PYTHON_COMMENTED),
]

_DECLARED = frozenset({"function", "method", "constructor", "field", "property", "getter"})


def _ids(tmp_path: Path, analyze: Callable[[Path], Any], name: str,
         text: str) -> dict[tuple[str, int], tuple[str, str | None]]:
    """Each declared symbol's (name, stable id), by kind and order in the file."""
    for old in tmp_path.iterdir():
        old.unlink()
    (tmp_path / name).write_text(text)
    by_kind: defaultdict[str, list[Any]] = defaultdict(list)
    for sym in analyze(tmp_path).symbols:
        if sym.kind in _DECLARED:
            by_kind[sym.kind].append(sym)
    return {
        (kind, index): (sym.name, sym.stable_id)
        for kind, syms in by_kind.items()
        for index, sym in enumerate(sorted(syms, key=lambda s: (s.span.start_line, s.span.start_col)))
    }


@pytest.mark.parametrize(("lang", "name", "analyze", "plain", "commented"), _CASES,
                         ids=[c[0] for c in _CASES])
def test_a_comment_in_a_declaration_moves_no_id(
    tmp_path: Path, lang: str, name: str, analyze: Callable[[Path], Any],
    plain: str, commented: str,
) -> None:
    before = _ids(tmp_path, analyze, name, plain)
    after = _ids(tmp_path, analyze, name, commented)
    assert before and all(sid for _, sid in before.values())
    assert before.keys() == after.keys()
    assert {k: (before[k], after[k]) for k in before if before[k][1] != after[k][1]} == {}


def test_a_comment_looking_literal_is_the_literal(tmp_path: Path) -> None:
    """``"a /* b */ c"`` and ``"a  c"`` are two defaults, two ids."""
    one = _ids(tmp_path, analyze_javascript, "m.ts", 'export function g(s: string = "a /* b */ c"): void {}\n')
    two = _ids(tmp_path, analyze_javascript, "m.ts", 'export function g(s: string = "a  c"): void {}\n')
    assert one[("function", 0)][1] != two[("function", 0)][1]


@pytest.mark.parametrize(("left", "right"), [
    ("/[/*]x/, ", "/[/*]y/, "),      # a regex class holding ``/*`` is no comment
    ("[\n  /x\\//g,\n  /y/], ", "[\n  /x\\//m,\n  /y/], "),  # nor a ``\\/`` before the closing ``/``
])
def test_a_regex_holding_comment_characters_stays_apart(tmp_path: Path, left: str, right: str) -> None:
    one = _ids(tmp_path, analyze_javascript, "m.ts", f"export function g(r = {left}s: number): void {{}}\n")
    two = _ids(tmp_path, analyze_javascript, "m.ts", f"export function g(r = {right}s: number): void {{}}\n")
    assert one[("function", 0)][1] != two[("function", 0)][1]


def test_a_javascript_default_split_ignores_a_comment(tmp_path: Path) -> None:
    """A JavaScript default is hashed as ``name = ...``, cut at its first
    ``=``; a comment holding ``=`` before it must not move the cut."""
    plain = _ids(tmp_path, analyze_javascript, "m.js", "export function f(y = 1) {}\n")
    commented = _ids(tmp_path, analyze_javascript, "m.js", "export function f(y /* a=b */ = 1) {}\n")
    assert plain[("function", 0)][1] is not None
    assert plain == commented
