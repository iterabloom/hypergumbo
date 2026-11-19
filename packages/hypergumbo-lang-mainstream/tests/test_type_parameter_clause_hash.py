# SPDX-License-Identifier: AGPL-3.0-or-later
"""A declaration's own type-parameter clause is hashed, by position.

Through stable-id scheme v15 no analyzer passed a declaration's type
parameters to its signature normalizer: a type parameter was hashed by its
NAME and its bounds and ``where`` constraints were not hashed at all. Two
overloads differing only in a bound (``<T extends Number> f(T x)`` and
``<T extends CharSequence> f(T x)``, a legal Java pair; Swift's constraint
overloads, three groups in swift-composable-architecture) shared one hash
input, and the within-file occurrence split told them apart by ORDER, so
deleting one moved its id onto the other (WI-bubak; owner ruling and gate
count in the notebook's pr3/bubak).

Java, Groovy, Kotlin, Scala, Swift and C# now hash the clause (its type
parameters with their bounds, and the declaration's ``where`` constraints)
in front of the signature, with each type parameter renamed by its position
(``$0``, ``$1``) in the clause and in the signature. So:

- a bound, a constraint or the number of type parameters separates two ids,
  and deleting one overload leaves the other's id where it was;
- renaming a type parameter moves no id (``<T> f(T)`` = ``<U> f(U)``);
- a declaration without a type-parameter clause hashes what it hashed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_lang_mainstream.csharp import analyze_csharp
from hypergumbo_lang_mainstream.groovy import analyze_groovy
from hypergumbo_lang_mainstream.java import analyze_java
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin
from hypergumbo_lang_mainstream.scala import analyze_scala
from hypergumbo_lang_mainstream.swift import analyze_swift

Analyzer = Callable[[Path], AnalysisResult]

_LANG: dict[str, tuple[Analyzer, str, str]] = {
    "java": (analyze_java, "p/C.java", "package p;\nclass C {\n%s\n}\n"),
    "groovy": (analyze_groovy, "p/C.groovy", "package p\nclass C {\n%s\n}\n"),
    "kotlin": (analyze_kotlin, "p/C.kt", "package p\nclass C {\n%s\n}\n"),
    "scala": (analyze_scala, "p/C.scala", "package p\nclass C {\n%s\n}\n"),
    "swift": (analyze_swift, "C.swift", "protocol P {}\nprotocol Q {}\nstruct C {\n%s\n}\n"),
    "csharp": (analyze_csharp, "C.cs", "namespace N {\nclass C {\n%s\n}\n}\n"),
}

# (case, language, symbol name, declaration A, declaration B): A and B differ
# only in their type-parameter clause, so v15 hashed them alike.
_BOUND_ONLY = [
    ("java-bound", "java", "C.f",
     "<T extends Number> void f(T x) {}", "<T extends CharSequence> void f(T x) {}"),
    ("java-constructor-bound", "java", "C.C",
     "<T extends Number> C(T x) {}", "<T extends CharSequence> C(T x) {}"),
    ("groovy-bound", "groovy", "C.f",
     "public <T extends Number> void f(T x) {}", "public <T extends CharSequence> void f(T x) {}"),
    ("kotlin-bound", "kotlin", "C.f", "fun <T : Number> f(x: T) {}", "fun <T : CharSequence> f(x: T) {}"),
    ("kotlin-where", "kotlin", "C.g",
     "fun <T> g(x: T) where T : Number {}", "fun <T> g(x: T) where T : CharSequence {}"),
    ("scala-bound", "scala", "C.f",
     "def f[T <: Number](x: T): Unit = ()", "def f[T <: CharSequence](x: T): Unit = ()"),
    ("scala-context-bound", "scala", "C.f",
     "def f[T: Ordering](x: T): Unit = ()", "def f[T](x: T): Unit = ()"),
    ("swift-bound", "swift", "C.f", "func f<T: P>(_ x: T) {}", "func f<T: Q>(_ x: T) {}"),
    ("swift-where", "swift", "C.g",
     "func g<T>(x: T) where T: P {}", "func g<T>(x: T) where T: Q {}"),
    ("swift-unconstrained", "swift", "C.h", "func h<T: P>(_ x: T) {}", "func h<T>(_: T) {}"),
    ("swift-init", "swift", "C.init", "init<T: P>(x: T) {}", "init<T: Q>(x: T) {}"),
    ("csharp-count", "csharp", "C.F", "public void F<T>(int x) {}", "public void F<T, U>(int x) {}"),
    ("csharp-where", "csharp", "C.G",
     "public void G<T>(T x) where T : class {}", "public void G<T>(T x) where T : struct {}"),
]

# (case, language, symbol name, declaration, the same with its type
# parameters renamed): one id.
_RENAMED = [
    ("java", "java", "C.f", "<T extends Comparable<T>> T f(T x) { return x; }",
     "<U extends Comparable<U>> U f(U x) { return x; }"),
    ("groovy", "groovy", "C.f", "public <T> T f(T x) { return x }", "public <U> U f(U x) { return x }"),
    ("kotlin", "kotlin", "C.f", "fun <T : Comparable<T>> f(x: List<T>): T = x[0]",
     "fun <U : Comparable<U>> f(x: List<U>): U = x[0]"),
    ("kotlin-receiver", "kotlin", "C.f", "fun <T> List<T>.f(): T = this[0]",
     "fun <U> List<U>.f(): U = this[0]"),
    ("scala", "scala", "C.f", "def f[A, B <: A](x: A, y: B): A = x", "def f[X, Y <: X](x: X, y: Y): X = x"),
    ("swift", "swift", "C.f", "func f<T: P>(_ x: [T]) -> T? { nil }", "func f<U: P>(_ x: [U]) -> U? { nil }"),
    ("csharp", "csharp", "C.F", "public T F<T>(T x) where T : class { return x; }",
     "public U F<U>(U x) where U : class { return x; }"),
]


def _ids(root: Path, lang: str, decls: list[str], name: str) -> list[str]:
    analyze, rel, frame = _LANG[lang]
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frame % "\n".join(decls))
    found = [s for s in analyze(root).symbols if s.name == name]
    assert len(found) == len(decls), [(s.name, s.kind) for s in analyze(root).symbols]
    found.sort(key=lambda s: s.span.start_line if s.span else 0)
    assert all(s.stable_id for s in found)
    return [str(s.stable_id) for s in found]


@pytest.mark.parametrize(("case", "lang", "name", "decl_a", "decl_b"), _BOUND_ONLY,
                         ids=[c[0] for c in _BOUND_ONLY])
def test_bound_only_overloads_hash_apart(
    tmp_path: Path, case: str, lang: str, name: str, decl_a: str, decl_b: str,
) -> None:
    [a] = _ids(tmp_path / "a", lang, [decl_a], name)
    [b] = _ids(tmp_path / "b", lang, [decl_b], name)
    assert a != b, case


@pytest.mark.parametrize(("case", "lang", "name", "decl_a", "decl_b"), _BOUND_ONLY,
                         ids=[c[0] for c in _BOUND_ONLY])
def test_deleting_one_overload_leaves_the_other_id(
    tmp_path: Path, case: str, lang: str, name: str, decl_a: str, decl_b: str,
) -> None:
    both = _ids(tmp_path / "both", lang, [decl_a, decl_b], name)
    [alone] = _ids(tmp_path / "alone", lang, [decl_b], name)
    assert both[1] == alone, case
    assert both[0] != both[1], case


@pytest.mark.parametrize(("case", "lang", "name", "decl", "renamed"), _RENAMED,
                         ids=[c[0] for c in _RENAMED])
def test_renaming_a_type_parameter_moves_no_id(
    tmp_path: Path, case: str, lang: str, name: str, decl: str, renamed: str,
) -> None:
    [a] = _ids(tmp_path / "a", lang, [decl], name)
    [b] = _ids(tmp_path / "b", lang, [renamed], name)
    assert a == b, case


def test_the_java_probe_splits(tmp_path: Path) -> None:
    """The item's probe: two overloads in one class differing only in a bound."""
    a, b = _ids(tmp_path, "java", [
        "public <T extends Number> void f(T x) {}",
        "public <T extends CharSequence> void f(T x) {}",
    ], "C.f")
    assert a != b
