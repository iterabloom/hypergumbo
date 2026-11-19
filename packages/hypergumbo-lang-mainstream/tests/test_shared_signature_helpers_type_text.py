# SPDX-License-Identifier: AGPL-3.0-or-later
"""Types the shared signature helpers collapsed under v15 now hash apart.

Under stable-id scheme v15 the shared helpers of ``hypergumbo_core.analyze.base``
reduced distinct types to one hashed text (see the core test
``test_signature_helpers_type_text``): ``strip_fqn_prefix`` cut a compound type
at its last dot (a tuple, a function type, an annotated or ``ref`` type), a
JVM nested type to its last segment and a Swift metatype to ``Type``; the
names-first rule skipped Rust's ``self``, ``&self`` and ``&mut self`` alike;
``split_params_top_level`` split inside a brace or a string. Two declarations
differing only there shared a typed stable_id, and in one file the occurrence
split told them apart by ORDER, so deleting one moved its id onto the other.

Each case below is analysed end to end. Declaration A and declaration B, each
alone in a repository at the same relative path (so only the hashed text can
tell them apart), get different ids; and B's id in a file that also declares A
equals B's id alone, so neither takes the other's id when one is deleted.
Signatures the v15 helpers lost nothing of keep their v15 text, checked
directly on each language's normalizer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_lang_mainstream.csharp import analyze_csharp, normalize_csharp_signature
from hypergumbo_lang_mainstream.go import normalize_go_signature
from hypergumbo_lang_mainstream.groovy import analyze_groovy, normalize_groovy_signature
from hypergumbo_lang_mainstream.java import analyze_java, normalize_java_signature
from hypergumbo_lang_mainstream.js_ts import normalize_jsts_signature
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin, normalize_kotlin_signature
from hypergumbo_lang_mainstream.php import normalize_php_signature
from hypergumbo_lang_mainstream.py import normalize_python_signature
from hypergumbo_lang_mainstream.rust import analyze_rust, normalize_rust_signature
from hypergumbo_lang_mainstream.scala import analyze_scala, normalize_scala_signature
from hypergumbo_lang_mainstream.swift import analyze_swift, normalize_swift_signature

Analyzer = Callable[[Path], AnalysisResult]

#: language -> (analyzer, file name, text around the declarations)
_LANG: dict[str, tuple[Analyzer, str, str]] = {
    "java": (analyze_java, "p/C.java", "package p;\nclass C {\n%s\n}\n"),
    "groovy": (analyze_groovy, "p/C.groovy", "package p\nclass C {\n%s\n}\n"),
    "kotlin": (analyze_kotlin, "p/C.kt", "package p\nclass C {\n%s\n}\n"),
    "scala": (analyze_scala, "p/C.scala", "package p\nclass C {\n%s\n}\n"),
    "swift": (analyze_swift, "C.swift", "struct C {\n%s\n}\n"),
    "csharp": (analyze_csharp, "C.cs", "namespace N {\nclass C {\n%s\n}\n}\n"),
    "rust": (analyze_rust, "src/lib.rs", "struct C;\n%s\n"),
}

# (case, language, symbol name, declaration A, declaration B)
_CASES = [
    ("java-nested-type", "java", "C.f", "void f(Foo.Bar x) {}", "void f(Baz.Bar x) {}"),
    ("java-nested-return", "java", "C.f",
     "Map.Entry f(int a) { return null; }", "Other.Entry f(long a) { return null; }"),
    ("groovy-nested-type", "groovy", "C.f", "void f(Foo.Bar x) {}", "void f(Baz.Bar x) {}"),
    ("kotlin-function-type", "kotlin", "C.f",
     "fun f(g: (Int) -> a.B) {}", "fun f(g: (String) -> a.B) {}"),
    ("kotlin-suspend-type", "kotlin", "C.f",
     "fun f(g: suspend (Int) -> a.B) {}", "fun f(g: a.B) {}"),
    ("kotlin-annotated-type", "kotlin", "C.f", "fun f(x: @A a.B) {}", "fun f(x: a.B) {}"),
    ("kotlin-nested-receiver", "kotlin", "C.f",
     "fun Map.Entry<Int, Int>.f() {}", "fun Other.Entry<Int, Int>.f() {}"),
    ("scala-function-type", "scala", "C.f",
     "def f(g: Int => a.B): Unit = ()", "def f(g: a.B): Unit = ()"),
    ("scala-singleton-type", "scala", "C.f", "def f(x: a.type): Unit = ()",
     "def f(x: b.type): Unit = ()"),
    ("swift-metatype", "swift", "C.f", "func f(t: Int.Type) {}", "func f(t: String.Type) {}"),
    ("swift-tuple-element", "swift", "C.f", "func f(t: (Int, A.B)) {}", "func f(t: (Str, A.B)) {}"),
    ("swift-function-result", "swift", "C.f",
     "func f(g: (Int) -> A.B) {}", "func f(g: (Str) -> A.B) {}"),
    ("csharp-nullable-tuple", "csharp", "C.F",
     "void F((int, A.B)? x) {}", "void F((string, A.B)? x) {}"),
    ("csharp-array-tuple", "csharp", "C.F",
     "void F((int, A.B)[] x) {}", "void F((long, A.B)[] x) {}"),
    ("csharp-ref-return", "csharp", "C.F",
     "ref A.B F(int x) { throw null; }", "A.B F(int x) { throw null; }"),
    ("rust-by-value-receiver", "rust", "C::f",
     "impl C { fn f(self) {} }", "impl C { fn f(&self) {} }"),
    ("rust-mut-receiver", "rust", "C::f",
     "impl C { fn f(&mut self) {} }", "impl C { fn f(&self) {} }"),
]


def _ids(root: Path, lang: str, decls: list[str], name: str) -> list[str]:
    analyze, rel, frame = _LANG[lang]
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frame % "\n".join(decls))
    found = [s for s in analyze(root).symbols if s.name == name]
    assert len(found) == len(decls), [s.name for s in analyze(root).symbols]
    found.sort(key=lambda s: s.span.start_line if s.span else 0)
    assert all(s.stable_id for s in found)
    return [str(s.stable_id) for s in found]


@pytest.mark.parametrize(("case", "lang", "name", "decl_a", "decl_b"), _CASES,
                         ids=[c[0] for c in _CASES])
def test_collapsed_types_hash_apart(
    tmp_path: Path, case: str, lang: str, name: str, decl_a: str, decl_b: str,
) -> None:
    [a] = _ids(tmp_path / "a", lang, [decl_a], name)
    [b] = _ids(tmp_path / "b", lang, [decl_b], name)
    assert a != b, case


@pytest.mark.parametrize(("case", "lang", "name", "decl_a", "decl_b"),
                         [c for c in _CASES if not c[0].startswith(("rust", "kotlin-nested"))],
                         ids=[c[0] for c in _CASES if not c[0].startswith(("rust", "kotlin-nested"))])
def test_deleting_one_overload_leaves_the_other_id(
    tmp_path: Path, case: str, lang: str, name: str, decl_a: str, decl_b: str,
) -> None:
    both = _ids(tmp_path / "both", lang, [decl_a, decl_b], name)
    [alone] = _ids(tmp_path / "alone", lang, [decl_b], name)
    assert both[1] == alone, case


# (normalizer, signature, the text v15 hashed): signatures the v15 helpers lost
# nothing of keep that text exactly.
_UNCHANGED = [
    (normalize_java_signature, "(java.lang.String s, int x) java.util.List<String>",
     "(String,int)List<String>"),
    (normalize_java_signature, "(Map<String, Integer> m)", "(Map<String, Integer>)"),
    (normalize_java_signature, "(java.lang.@A String s)", "(@A String)"),
    (normalize_groovy_signature, "(java.lang.String s): int", "(String)int"),
    (normalize_kotlin_signature, "(a: kotlin.String, b: (Int) -> Unit): Int",
     "(String,(Int) -> Unit)Int"),
    (normalize_scala_signature, "(a: scala.Int)(implicit o: Ordering[A]): A",
     "(Int)(implicit Ordering[A])A"),
    (normalize_swift_signature, "(a: Foundation.Date, b: ((Int) -> Int, (Int) -> Int)) -> Int",
     "(Date,((Int) -> Int,(Int) -> Int))Int"),
    (normalize_csharp_signature, "(ref System.Int32 x, (int, string) t) System.String",
     "(ref Int32,(int, string))String"),
    (normalize_rust_signature, "(&self, x: &'a str, y: Vec<T>) -> bool", "(&'a str,Vec<T>)bool"),
    (normalize_rust_signature, "(mut self, x: i32)", "(mut self,i32)"),
    (normalize_rust_signature, "(x: &'a str, y: &'a T) -> &'a str", "(&'a str,&'a T)&'a str"),
    (normalize_go_signature, "(c <-chan int) (<-chan int, error)", "(<-chan int)(<-chan int, error)"),
    (normalize_go_signature, "() (r *http.Response, err error)", "()(Response,err error)"),
    (normalize_php_signature, "(App\\Foo $a, int $b): string", "(App\\Foo,int)string"),
    (normalize_python_signature, "(self, a: typing.List[int]) -> bool", "(List[int])bool"),
    (normalize_jsts_signature, "(a: React.ReactNode, b: number): void", "(ReactNode,number)void"),
]


@pytest.mark.parametrize(("normalize", "signature", "v15_text"), _UNCHANGED)
def test_lossless_v15_text_is_kept(
    normalize: Callable[..., str | None], signature: str, v15_text: str,
) -> None:
    assert normalize(signature) == v15_text


def test_split_inside_tuple_of_function_types_keeps_v15_spacing() -> None:
    # v15 split this tuple at its inner comma and lost only a space: v15's text stays.
    assert normalize_swift_signature("(a: ((Int) -> Int, (Int) -> Int))") == (
        "(((Int) -> Int,(Int) -> Int))")


def test_typescript_object_type_keeps_its_second_member() -> None:
    a = normalize_jsts_signature("(o: {a: string, b: number}): void")
    b = normalize_jsts_signature("(o: {a: string, c: number}): void")
    assert a is not None and "b: number" in a and a != b
