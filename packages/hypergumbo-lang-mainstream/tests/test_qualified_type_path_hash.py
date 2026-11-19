# SPDX-License-Identifier: AGPL-3.0-or-later
"""C# and Swift hash a parameter's or return type's qualified path as written.

Through the PR 3 branch's WI-tojov commit, the C# and Swift typed stable ids
cut a qualified parameter or return type at its head to the last segment, so
``F(A.B x)`` and ``F(C.B x)`` -- two overloads on two same-named nested types,
legal in both languages -- hashed alike and the within-file occurrence split
told them apart by order. Java, Kotlin, Scala and Groovy keep a nested type's
enclosing types (``keep_type_path``), because their packages are lower-case
and can be told from an enclosing type; a C# namespace and a Swift module are
upper-case, so that rule cannot tell them apart.

Owner ruling (WI-ririg, 2026-11-19): C# and Swift hash the FULL path as
written. The hash is spelling-dependent on purpose: ``System.String`` and
``String`` hash apart, and so do ``Swift.Int`` and ``Int``. An unqualified
type hashes what it hashed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.csharp import analyze_csharp, normalize_csharp_signature
from hypergumbo_lang_mainstream.swift import analyze_swift, normalize_swift_signature


@pytest.mark.parametrize(("signature", "hashed"), [
    ("(A.B x)", "(A.B)"),
    ("(System.String s) System.Int32", "(System.String)System.Int32"),
    ("(global::System.String s)", "(global::System.String)"),
    ("(ref System.Int32 x, out Foo.Bar y)", "(ref System.Int32,out Foo.Bar)"),
    ("(System.Collections.Generic.List<A.B> xs)", "(System.Collections.Generic.List<A.B>)"),
    ("(T x) N.Outer<T>.Inner", "($0)N.Outer<$0>.Inner"),
    ("() ref readonly Foo.Bar", "()ref readonly Foo.Bar"),
    ("(int x, string y) int", "(int,string)int"),
])
def test_csharp_hashes_the_type_path_as_written(signature: str, hashed: str) -> None:
    type_params = ["T"] if "T x" in signature else None
    assert normalize_csharp_signature(signature, type_params) == hashed


@pytest.mark.parametrize(("signature", "hashed"), [
    ("(x: Feature.State)", "(Feature.State)"),
    ("(x: Swift.Int) -> Foundation.URL", "(Swift.Int)Foundation.URL"),
    ("(x: inout Self.State, y: A.B...)", "(inout Self.State,A.B...)"),
    ("(x: Swift.Int.Type)", "(Swift.Int.Type)"),
    ("(x: A.B & C.D)", "(A.B & C.D)"),
    ("(x: @escaping (A.B) -> C.D)", "(@escaping (A.B) -> C.D)"),
    ("(x: T.Element) -> T", "($0.Element)$0"),
    ("(x: Int, y: String) -> Int", "(Int,String)Int"),
])
def test_swift_hashes_the_type_path_as_written(signature: str, hashed: str) -> None:
    type_params = ["T"] if "T." in signature else None
    assert normalize_swift_signature(signature, type_params) == hashed


@pytest.mark.parametrize(("a", "b"), [
    ("(A.B x)", "(C.B x)"),
    ("() A.B", "() C.B"),
    ("(System.String s)", "(String s)"),
])
def test_csharp_paths_hash_apart(a: str, b: str) -> None:
    assert normalize_csharp_signature(a) != normalize_csharp_signature(b)


@pytest.mark.parametrize(("a", "b"), [
    ("(x: Feature.State)", "(x: Other.State)"),
    ("() -> A.B", "() -> C.B"),
    ("(x: Swift.Int)", "(x: Int)"),
])
def test_swift_paths_hash_apart(a: str, b: str) -> None:
    assert normalize_swift_signature(a) != normalize_swift_signature(b)


def _ids(root: Path, analyze, rel: str, text: str, name: str) -> list[str]:  # type: ignore[no-untyped-def]
    root.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)
    found = sorted((s for s in analyze(root).symbols if s.name == name),
                   key=lambda s: s.span.start_line)
    assert found and all(s.stable_id for s in found)
    return [str(s.stable_id) for s in found]


def test_csharp_overloads_on_two_nested_types_hash_apart(tmp_path: Path) -> None:
    head = "namespace N {\nclass A { public class B {} }\nclass C { public class B {} }\nclass K {\n"
    both = head + "public void F(A.B x) {}\npublic void F(C.B x) {}\n}\n}\n"
    alone = head + "public void F(C.B x) {}\n}\n}\n"
    a, b = _ids(tmp_path / "both", analyze_csharp, "K.cs", both, "K.F")
    assert a != b
    [c] = _ids(tmp_path / "alone", analyze_csharp, "K.cs", alone, "K.F")
    assert b == c


def test_swift_overloads_on_two_nested_types_hash_apart(tmp_path: Path) -> None:
    head = "enum A { struct S {} }\nenum B { struct S {} }\nstruct K {\n"
    both = head + "func f(x: A.S) {}\nfunc f(x: B.S) {}\n}\n"
    alone = head + "func f(x: B.S) {}\n}\n"
    a, b = _ids(tmp_path / "both", analyze_swift, "K.swift", both, "K.f")
    assert a != b
    [c] = _ids(tmp_path / "alone", analyze_swift, "K.swift", alone, "K.f")
    assert b == c
