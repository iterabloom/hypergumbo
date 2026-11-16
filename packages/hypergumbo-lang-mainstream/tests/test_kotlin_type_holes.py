# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kotlin: every type a declaration writes reaches the text its stable_id hashes.

The function renderer (``_extract_kotlin_signature``) read three type node
kinds (``user_type``, ``nullable_type``, ``function_type``) and dropped any
other WITH its parameter: ``x: (Int)``, ``x: dynamic`` and ``x: T & Any``
vanished, so ``fun f(x: (Int))`` hashed like ``fun f()``. A return type of
those kinds vanished too. ``vararg`` was not written, so ``f(vararg x: Int)``
hashed like ``f(x: Int)``, and an extension function's RECEIVER was not
written at all, so ``fun String.f()`` and ``fun Int.f()`` in one file shared an
id and were told apart only by occurrence order. A class property hashed only
a ``user_type`` (``val x: Int?`` hashed like ``val x = 1``) and never its
extension receiver.

Each test below names one node kind or shape: two declarations that differ only
there get two ids, and the text a user reads carries the type. The pinned ids
at the end were taken from the code before the fix: a declaration whose types
use only the kinds that already had a branch keeps its id.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.kotlin import analyze_kotlin, normalize_kotlin_signature


def _symbols(tmp_path: Path, text: str, rel: str = "M.kt") -> list:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return [s for s in analyze_kotlin(tmp_path).symbols if s.kind != "file"]


def _named(symbols: list, name: str) -> list:
    return sorted(
        (s for s in symbols if s.name == name), key=lambda s: s.span.start_line,
    )


# (node kind or shape, two declarations differing only there, the name,
#  the text the FIRST one's signature must contain)
_PARAMETER_HOLES = [
    ("vararg", "fun f(vararg x: Int) {}\nfun f(x: Int) {}\n", "f", "vararg x: Int"),
    ("vararg_dotted", "fun f(vararg x: kotlin.String) {}\nfun f(x: kotlin.String) {}\n",
     "f", "vararg x: kotlin.String"),
    ("parenthesized_type", "fun f(x: (Int)) {}\nfun f() {}\n", "f", "x: (Int)"),
    ("dynamic", "fun f(x: dynamic) {}\nfun f() {}\n", "f", "x: dynamic"),
    ("non_nullable_type", "fun <T> f(x: T & Any) {}\nfun <T> f() {}\n", "f", "x: T & Any"),
    ("parenthesized_in_nullable", "fun f(x: (Int)?) {}\nfun f(x: (Long)?) {}\n",
     "f", "x: (Int)?"),
]

_RETURN_HOLES = [
    ("parenthesized_type", "fun f(): (Int) = 1\nfun f() {}\n", "f", "): (Int)"),
    ("dynamic", "fun f(): dynamic = 1\nfun f() {}\n", "f", "): dynamic"),
    ("non_nullable_type", "fun <T> f(x: T): T & Any = x!!\nfun <T> f(x: T) {}\n",
     "f", "): T & Any"),
]

# An extension function's receiver, one row per type node kind it can take.
_RECEIVER_HOLES = [
    ("user_type", "fun String.f(): Int = 1\nfun Int.f(): Int = 1\n", "f"),
    ("user_type_generic", "fun List<Int>.f() {}\nfun List<String>.f() {}\n", "f"),
    ("nullable_type", "fun String?.f() {}\nfun Int?.f() {}\n", "f"),
    ("parenthesized_type", "fun ((Int) -> Unit).f() {}\nfun ((Long) -> Unit).f() {}\n", "f"),
    ("dynamic", "fun dynamic.f() {}\nfun f() {}\n", "f"),
    ("receiver_vs_plain", "fun String.f() {}\nfun f() {}\n", "f"),
    ("member_extension", "class C {\n  fun String.f() {}\n  fun Int.f() {}\n}\n", "C.f"),
]

# A class property's declared type, one row per kind the field renderer dropped.
_FIELD_HOLES = [
    ("nullable_type", "class C {\n  val x: Int? = null\n}\n",
     "class C {\n  val x = 1\n}\n", "Int?"),
    ("function_type", "class C {\n  val x: (Int) -> Unit = {}\n}\n",
     "class C {\n  val x: (Long) -> Unit = {}\n}\n", "(Int) -> Unit"),
    ("parenthesized_type", "class C {\n  val x: (Int) = 1\n}\n",
     "class C {\n  val x: (Long) = 1\n}\n", "(Int)"),
    ("dynamic", "class C {\n  val x: dynamic = 1\n}\n",
     "class C {\n  val x = 1\n}\n", "dynamic"),
    ("non_nullable_type", "class C<T> {\n  val x: T & Any = TODO()\n}\n",
     "class C<T> {\n  val x = TODO()\n}\n", "T & Any"),
]


@pytest.mark.parametrize(
    "source,name,shown", [row[1:] for row in _PARAMETER_HOLES],
    ids=[row[0] for row in _PARAMETER_HOLES],
)
def test_parameter_type_kind_is_hashed_and_shown(
    tmp_path: Path, source: str, name: str, shown: str,
) -> None:
    first, second = _named(_symbols(tmp_path, source), name)
    assert first.stable_id and second.stable_id
    assert first.stable_id != second.stable_id
    assert shown in (first.signature or "")


@pytest.mark.parametrize(
    "source,name,shown", [row[1:] for row in _RETURN_HOLES],
    ids=[row[0] for row in _RETURN_HOLES],
)
def test_return_type_kind_is_hashed_and_shown(
    tmp_path: Path, source: str, name: str, shown: str,
) -> None:
    first, second = _named(_symbols(tmp_path, source), name)
    assert first.stable_id != second.stable_id
    assert shown in (first.signature or "")


@pytest.mark.parametrize(
    "source,name", [row[1:] for row in _RECEIVER_HOLES],
    ids=[row[0] for row in _RECEIVER_HOLES],
)
def test_extension_receiver_is_hashed(tmp_path: Path, source: str, name: str) -> None:
    first, second = _named(_symbols(tmp_path, source), name)
    assert first.stable_id and second.stable_id
    assert first.stable_id != second.stable_id


def test_receiver_is_hashed_without_package_prefix(tmp_path: Path) -> None:
    """The receiver takes the deliberate FQN stripping parameters take."""
    dotted = _named(_symbols(tmp_path / "a", "fun kotlin.text.StringBuilder.f() {}\n"), "f")
    simple = _named(_symbols(tmp_path / "b", "fun StringBuilder.f() {}\n"), "f")
    assert dotted[0].stable_id == simple[0].stable_id


@pytest.mark.parametrize(
    "first_src,second_src,shown", [row[1:] for row in _FIELD_HOLES],
    ids=[row[0] for row in _FIELD_HOLES],
)
def test_field_type_kind_is_hashed_and_shown(
    tmp_path: Path, first_src: str, second_src: str, shown: str,
) -> None:
    # Same repo-relative path in two trees: only the declared type differs.
    (first,) = _named(_symbols(tmp_path / "a", first_src), "C.x")
    (second,) = _named(_symbols(tmp_path / "b", second_src), "C.x")
    assert first.stable_id != second.stable_id
    assert first.signature == shown


def test_extension_property_receiver_is_hashed(tmp_path: Path) -> None:
    source = (
        "class C {\n"
        "  val String.ext: Int get() = 1\n"
        "  val Int.ext: Int get() = 1\n"
        "}\n"
    )
    first, second = _named(_symbols(tmp_path, source), "C.ext")
    assert first.stable_id != second.stable_id
    assert first.signature == "Int"


def test_normalizer_writes_vararg_and_receiver() -> None:
    assert normalize_kotlin_signature("(vararg x: Int)") == "(vararg Int)"
    assert normalize_kotlin_signature(
        "(a: Int, vararg b: kotlin.String): Unit?",
    ) == "(Int,vararg String)Unit?"
    assert normalize_kotlin_signature("(): Int", receiver="kotlin.String") == "String.()Int"
    assert normalize_kotlin_signature("()", receiver="List<Int>?") == "List<Int>?.()"
    # A parameter NAMED vararg is not a vararg parameter.
    assert normalize_kotlin_signature("(vararg: Int)") == "(Int)"
    assert normalize_kotlin_signature(None, receiver="String") is None


# Hole-free declarations: ids pinned from the code before the fix (dev
# 3da1f85b37), file ``Clean.kt`` at the analysed root. Each uses only type
# node kinds the renderer already read, so none may move.
_CLEAN_SOURCE = """package fx

fun cPlain(x: Int, y: String): Int = x
fun cNullable(x: Int?): String? = null
fun cFun(cb: (Int) -> Unit) {}
fun cSuspendType(cb: suspend () -> Unit) {}
fun cFunReceiverType(block: StringBuilder.() -> Unit) {}
fun cNamedLambda(cb: (x: Int) -> Unit) {}
fun cGeneric(x: List<out Number>, y: Array<*>, z: MutableList<in Int>) {}
fun <T : Comparable<T>> cBound(x: T): T = x
fun <T> cWhere(x: T) where T : Any {}
inline fun <reified T> cReified(): T? = null
fun cDefault(x: Int = 3, y: String = "a") {}
suspend fun cSuspend(): Int = 1
operator fun Holder.plus(o: Holder): Holder = o
fun cCross(crossinline a: () -> Unit, noinline b: () -> Unit) {}
fun cDotted(x: kotlin.collections.List<Int>): kotlin.String = ""
fun cAnnotated(x: @Ann Int): @Ann Int = x
fun cRetFun(): () -> Unit = {}
fun cUnit(): Unit {}

annotation class Ann

class Plain(val a: Int, var b: String?) {
    constructor(s: String) : this(1, s)
    val fUser: Int = 1
    val fInferred = 3
    fun method(x: Int): Int = x
}
"""

_CLEAN_PINS = {
    "cPlain": "sha256:b7b34c46e09dc869",
    "cNullable": "sha256:af36abebae2237c5",
    "cFun": "sha256:60420bdcb1a07587",
    "cSuspendType": "sha256:9c15b6c19b92f167",
    "cFunReceiverType": "sha256:7687ff26be87c825",
    "cNamedLambda": "sha256:bb51e3923da5dc74",
    "cGeneric": "sha256:d578ca9b48ce2f56",
    "cBound": "sha256:56cc8cccaca74124",
    "cWhere": "sha256:eb662138077f8f03",
    "cReified": "sha256:47917587af406244",
    "cDefault": "sha256:572450cc72dfa7d8",
    "cSuspend": "sha256:d6f66cb0c812e82a",
    "cCross": "sha256:8eb951c54c90d384",
    "cDotted": "sha256:517f144df997b496",
    "cAnnotated": "sha256:fd17c8c849403923",
    "cRetFun": "sha256:035de721da22c002",
    "cUnit": "sha256:733e8a396b5344ec",
    "Plain.fUser": "sha256:95ea475d1f874dd6",
    "Plain.fInferred": "sha256:47b982cf142034b2",
    "Plain.method": "sha256:f8579ec21a8b4fc4",
}


def test_hole_free_declarations_keep_their_ids(tmp_path: Path) -> None:
    by_name = {s.name: s for s in _symbols(tmp_path, _CLEAN_SOURCE, rel="Clean.kt")}
    assert {n: by_name[n].stable_id for n in _CLEAN_PINS} == _CLEAN_PINS
    # The one extension function in the file is the receiver hole, so it moves.
    assert by_name["plus"].stable_id != "sha256:367a0f601cc571ed"
