# SPDX-License-Identifier: AGPL-3.0-or-later
"""A declaration a formatter reflowed keeps its stable id, in every language.

Through stable-id scheme v15 the typed tier hashed its signature and scope
texts with their layout, and C++ hashed a conversion operator's name with its
spacing: splitting a parameter list over lines, re-indenting it, adding the
trailing comma or the leading union bar a formatter writes, or respacing
``& mut T`` / ``operator void *`` moved the id (WI-kumoh; owner ruling
2026-11-19: layout must not be an id input).

Each case below is one file written twice, at the same path: on one line, and
reflowed the way ``rustfmt``, ``swift-format``, ``ktfmt``, ``scalafmt``,
prettier, ``gofmt``, ``black``, ``clang-format`` and hand-wrapping do. Every
declared symbol keeps its stable id. Symbols are paired by kind and order,
not by name, since a C++ operator's name shows its spacing.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import pytest

from hypergumbo_lang_mainstream.cpp import CppAnalyzer, analyze_cpp
from hypergumbo_lang_mainstream.csharp import analyze_csharp
from hypergumbo_lang_mainstream.go import analyze_go
from hypergumbo_lang_mainstream.groovy import analyze_groovy
from hypergumbo_lang_mainstream.java import analyze_java
from hypergumbo_lang_mainstream.js_ts import analyze_javascript
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin
from hypergumbo_lang_mainstream.php import analyze_php
from hypergumbo_lang_mainstream.py import analyze_python
from hypergumbo_lang_mainstream.rust import analyze_rust
from hypergumbo_lang_mainstream.scala import analyze_scala
from hypergumbo_lang_mainstream.swift import analyze_swift

_RUST_ONE_LINE = """\
struct T;
pub(crate) fn f(x: &mut T, y: Option<Vec<u8>>) -> Result<(), String> { Ok(()) }
fn h<A: Clone + Send, B>(a: A, b: B) -> (A, B) where B: Default { (a, b) }
trait Tr { fn t(&self) -> u8; }
impl<A: Clone> Tr for Vec<A> where A: Send { fn t(&self) -> u8 { 0 } }
"""

_RUST_REFLOWED = """\
struct T;
pub( crate ) fn f(
    x: & mut T,
    y: Option<
        Vec<u8>,
    >,
) -> Result<
    (),
    String,
> { Ok(()) }
fn h<
    A: Clone
        + Send,
    B,
>(
    a: A,
    b: B,
) -> (A, B)
where
    B: Default,
{ (a, b) }
trait Tr { fn t(&self) -> u8; }
impl<A: Clone>
    Tr for Vec<A>
where
    A: Send,
{ fn t(&self) -> u8 { 0 } }
"""

_SWIFT_ONE_LINE = """\
protocol P {}
struct S {
  func r(a: Int, b: [String: Int]) -> Result<Int, Error> { fatalError() }
  func g<T: P>(x: T) -> T where T: Equatable { x }
  subscript(i: Int, j: Int) -> Int { 0 }
  init(a: Int, b: Int) {}
}
extension Array where Element: Equatable, Element: P { func e() {} }
"""

_SWIFT_REFLOWED = """\
protocol P {}
struct S {
  func r(
    a: Int,
    b: [
      String: Int
    ]
  )
    -> Result<
      Int,
      Error
    >
  { fatalError() }
  func g<
    T: P
  >(
    x: T
  ) -> T
  where
    T: Equatable
  { x }
  subscript(
    i: Int,
    j: Int
  ) -> Int { 0 }
  init(
    a: Int,
    b: Int
  ) {}
}
extension Array
where
  Element: Equatable,
  Element: P
{ func e() {} }
"""

_KOTLIN_ONE_LINE = """\
class A {
  fun f(a: Int, b: Map<String, Int>): List<Int> = listOf()
  fun <T : Comparable<T>> g(x: T): T where T : Any = x
  suspend fun s(cb: suspend (Int) -> Unit) {}
}
"""

_KOTLIN_REFLOWED = """\
class A {
  fun f(
    a: Int,
    b: Map<
      String,
      Int
      >,
  ): List<
    Int
    > = listOf()
  fun <
    T : Comparable<T>
    > g(
    x: T,
  ): T
    where T : Any = x
  suspend fun s(
    cb: suspend (
      Int,
    ) -> Unit,
  ) {}
}
"""

_JAVA_ONE_LINE = """\
import java.util.*;
class B {
  public <T extends Comparable<T>> Map<String, List<T>> m(Map<String, Integer> a, int[] b, String... c) { return null; }
  B(int a, List<String> b) {}
}
"""

_JAVA_REFLOWED = """\
import java.util.*;
class B {
  public <T extends
      Comparable<T>>
  Map<String,
      List<T>>
  m(
      Map<
          String,
          Integer>
        a,
      int []
        b,
      String
        ...
        c) { return null; }
  B(
     int a,
     List< String > b
  ) {}
}
"""

_SCALA_ONE_LINE = """\
class C {
  def f[A <: AnyRef, B](a: A, b: Map[String, B])(implicit o: Ordering[A]): List[A] = Nil
  def g(cb: (Int, String) => Unit): Unit = ()
}
"""

_SCALA_REFLOWED = """\
class C {
  def f[
    A <: AnyRef,
    B,
  ](
    a: A,
    b: Map[
      String,
      B,
    ],
  )(implicit
    o: Ordering[A],
  ): List[
    A,
  ] = Nil
  def g(
    cb: (
      Int,
      String,
    ) => Unit,
  ): Unit = ()
}
"""

_CSHARP_ONE_LINE = """\
using System.Collections.Generic;
class D {
  public Dictionary<string, int> F<T>(List<T> a, (int, string) t, params int[] r) where T : class, new() { return null; }
  public Dictionary<string, int> P { get; set; }
  public int this[int i, string j] { get { return 0; } }
}
"""

_CSHARP_REFLOWED = """\
using System.Collections.Generic;
class D {
  public Dictionary<
      string,
      int>
    F<
      T
    >(
      List< T >
        a,
      ( int , string )
        t,
      params int [ ]
        r)
    where T :
      class,
      new()
  { return null; }
  public Dictionary<string,
                    int> P { get; set; }
  public int this[
      int i,
      string j] { get { return 0; } }
}
"""

_TYPESCRIPT_ONE_LINE = """\
export class E {
  m<T extends object>(a: number, b: Map<string, T>): Promise<void> { return Promise.resolve(); }
  n(cb: (x: number) => void, o: { a: string; b: number }, u: 'a' | 'b'): void {}
}
export function f(a: string = "x  y", ...rest: number[]): void {}
"""

_TYPESCRIPT_REFLOWED = """\
export class E {
  m<
    T extends object,
  >(
    a: number,
    b: Map<
      string,
      T
    >,
  ): Promise<
    void
  > { return Promise.resolve(); }
  n(
    cb: (
      x: number,
    ) => void,
    o: {
      a: string;
      b: number;
    },
    u:
      | 'a'
      | 'b',
  ): void {}
}
export function f(
  a: string = "x  y",
  ...rest: number[]
): void {}
"""

_GO_ONE_LINE = """\
package g
func F[K comparable, V any](a map[K]V, b func(int) error) (int, error) { return 0, nil }
type T struct{}
func (t *T) M(a []int, b ...string) map[string][]int { return nil }
"""

_GO_REFLOWED = """\
package g
func F[
	K comparable,
	V any,
](
	a map[K]V,
	b func(
		int,
	) error,
) (
	int,
	error,
) { return 0, nil }
type T struct{}
func (t * T) M(
	a [] int,
	b ... string,
) map[string][]int { return nil }
"""

_PYTHON_ONE_LINE = """\
from typing import Dict, Callable
def f(a: Dict[str, int], b: Callable[[int], str] = None, *args: int, **kw: str) -> Dict[str, int]:
    return a
"""

_PYTHON_REFLOWED = """\
from typing import Dict, Callable
def f(
    a: Dict[
        str,
        int,
    ],
    b: Callable[
        [int],
        str,
    ] = None,
    *args: int,
    **kw: str,
) -> Dict[
    str,
    int,
]:
    return a
"""

_PHP_ONE_LINE = """\
<?php
class I {
  public function m(int $a, ?array $b = null, string ...$c): ?array { return null; }
}
"""

_PHP_REFLOWED = """\
<?php
class I {
  public function m(
    int $a,
    ? array $b = null,
    string ...$c,
  ): ? array { return null; }
}
"""

_GROOVY_ONE_LINE = """\
class J {
  Map<String, Integer> m(Map<String, Integer> a, int b) { null }
}
"""

_GROOVY_REFLOWED = """\
class J {
  Map<String,
      Integer> m(
      Map< String , Integer > a,
      int b) { null }
}
"""

_CPP_ONE_LINE = """\
#include <vector>
struct W { operator void*() const; int operator()(int a); };
W::operator void*() const { return nullptr; }
int W::operator()(int a) { return a; }
std::vector<std::vector<int> > f(int a, const char* b) { return {}; }
int g(int a) { return a; }
int g(double a) { return 0; }
"""

_CPP_REFLOWED = """\
#include <vector>
struct W { operator void*() const; int operator()(int a); };
W::operator void *() const { return nullptr; }
int W::operator ()(int a) { return a; }
std::vector<std::vector<int>> f(
    int a,
    const char *b) { return {}; }
int g(
    int a) { return a; }
int g(double
      a) { return 0; }
"""

_CASES: list[tuple[str, str, Callable[[Path], Any], str, str]] = [
    ("rust", "a.rs", analyze_rust, _RUST_ONE_LINE, _RUST_REFLOWED),
    ("swift", "a.swift", analyze_swift, _SWIFT_ONE_LINE, _SWIFT_REFLOWED),
    ("kotlin", "A.kt", analyze_kotlin, _KOTLIN_ONE_LINE, _KOTLIN_REFLOWED),
    ("java", "B.java", analyze_java, _JAVA_ONE_LINE, _JAVA_REFLOWED),
    ("scala", "C.scala", analyze_scala, _SCALA_ONE_LINE, _SCALA_REFLOWED),
    ("csharp", "D.cs", analyze_csharp, _CSHARP_ONE_LINE, _CSHARP_REFLOWED),
    ("typescript", "e.ts", analyze_javascript, _TYPESCRIPT_ONE_LINE, _TYPESCRIPT_REFLOWED),
    ("go", "g.go", analyze_go, _GO_ONE_LINE, _GO_REFLOWED),
    ("python", "h.py", analyze_python, _PYTHON_ONE_LINE, _PYTHON_REFLOWED),
    ("php", "i.php", analyze_php, _PHP_ONE_LINE, _PHP_REFLOWED),
    ("groovy", "J.groovy", analyze_groovy, _GROOVY_ONE_LINE, _GROOVY_REFLOWED),
    ("cpp", "k.cpp", analyze_cpp, _CPP_ONE_LINE, _CPP_REFLOWED),
]

_DECLARED = frozenset({
    "function", "method", "constructor", "field", "property", "getter", "class", "struct",
    "interface", "trait", "enum", "object", "protocol", "subscript", "type_alias",
})


def _ids(tmp_path: Path, analyze: Callable[[Path], Any], name: str,
         text: str) -> dict[tuple[str, int], tuple[str, str | None]]:
    """Each declared symbol's (name, stable id), by kind and order in the file."""
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


@pytest.mark.parametrize(("lang", "name", "analyze", "one_line", "reflowed"), _CASES,
                         ids=[c[0] for c in _CASES])
def test_a_reflowed_declaration_keeps_its_stable_id(
    tmp_path: Path, lang: str, name: str, analyze: Callable[[Path], Any],
    one_line: str, reflowed: str,
) -> None:
    before = _ids(tmp_path, analyze, name, one_line)
    after = _ids(tmp_path, analyze, name, reflowed)
    assert before
    assert before.keys() == after.keys()
    moved = {k: (before[k], after[k]) for k in before if before[k][1] != after[k][1]}
    assert moved == {}


def test_a_cpp_untyped_id_hashes_its_names_layout_free(tmp_path: Path) -> None:
    """``compute_stable_id`` (C++ fields, classes, members without a declared
    arity) hashes a C++ name layout-free, as ``declared_arity_stable_id`` does."""
    analyzer = CppAnalyzer()
    tree = analyzer._create_parser().parse(b"struct W { int f; };\n")
    node = tree.root_node.children[0]

    def sid(name: str) -> str:
        return analyzer.compute_stable_id(
            node, "struct", name=name, qualified_name=f"ns::{name}", file_stable_id="anchor",
        )
    assert sid("W<int, char>") == sid("W<int,\n      char>")
    assert sid("W<int, char>") != sid("W<int, int>")
