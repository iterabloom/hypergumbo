# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every Scala type node reaches the signature and the typed stable_id.

The signature text of a ``def`` (and the declared type of a ``val`` / ``var``
member) is the input hashed into the symbol's typed ``stable_id`` (spec §6).
The renderer used to have a branch for five type node kinds only
(``type_identifier``, ``generic_type``, ``tuple_type``, ``function_type``,
``infix_type``): a parameter of any other kind was dropped whole, a return type
of any other kind was dropped, and so was the return type of a ``def`` with no
parameter clause. Parameter clauses were flattened into one, losing the
``implicit`` / ``using`` keyword, and an extension method's receiver was not
written at all. A member ``val``'s type was read only when it was a bare
``type_identifier``. Two declarations that differed only there shared a hashed
text.

Each test below writes the two declarations at the SAME path in two trees, so
the file anchor is equal and the ids differ only if the hashed text does (in
one file the occurrence ordinal would separate them whatever the text). The
last tests pin hole-free texts and ids from before the fix, which must not move.
"""
from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.scala import analyze_scala, normalize_scala_signature


def _symbol(root: Path, source: str, name: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / "A.scala").write_text(source)
    return next(s for s in analyze_scala(root).symbols if s.name == name)


def _pair(tmp_path: Path, a: str, b: str, name: str = "O.f"):
    return _symbol(tmp_path / "a", a, name), _symbol(tmp_path / "b", b, name)


def _obj(body: str) -> str:
    return f"object O {{\n  {body}\n}}\n"


# (node kind, declaration with the kind, a declaration differing only there,
#  text the rendered signature must contain)
PARAMETER_KINDS = [
    ("lazy_parameter_type", "def f(a: => Int): Int = a", "def f(): Int = 1", "a: => Int"),
    ("repeated_parameter_type", "def f(a: Int*): Int = 1", "def f(a: Int): Int = 1", "a: Int*"),
    ("stable_type_identifier", "def f(a: m.T): Int = 1", "def f(): Int = 1", "a: m.T"),
    ("compound_type", "def f(a: A with B): Int = 1", "def f(a: A): Int = 1", "a: A with B"),
    ("projected_type", "def f(a: A#B): Int = 1", "def f(a: A): Int = 1", "a: A#B"),
    ("singleton_type", "def f(a: x.type): Int = 1", "def f(): Int = 1", "a: x.type"),
    ("structural_type", "def f(a: { def g: Int }): Int = 1", "def f(): Int = 1",
     "a: { def g: Int }"),
    ("annotated_type", "def f(a: Int @unchecked): Int = 1", "def f(a: Int): Int = 1",
     "a: Int @unchecked"),
    ("literal_type", "def f(a: 1): Int = 1", "def f(a: 2): Int = 1", "a: 1"),
    ("named_tuple_type", "def f(a: (x: Int, y: Int)): Int = 1",
     "def f(a: (x: Int, z: Int)): Int = 1", "a: (x: Int, y: Int)"),
    ("type_lambda", "def f(a: [X] =>> F[X]): Int = 1", "def f(): Int = 1", "a: [X] =>> F[X]"),
]


@pytest.mark.parametrize(
    "kind,with_kind,without,shown", PARAMETER_KINDS, ids=[k[0] for k in PARAMETER_KINDS])
def test_parameter_type_kind_is_hashed(
    tmp_path: Path, kind: str, with_kind: str, without: str, shown: str,
) -> None:
    a, b = _pair(tmp_path, _obj(with_kind), _obj(without))
    assert shown in (a.signature or "")
    assert a.stable_id != b.stable_id


RETURN_KINDS = [
    ("stable_type_identifier", "def f(): m.T = ???", ": m.T"),
    ("compound_type", "def f(): A with B = ???", ": A with B"),
    ("literal_type", "def f(): 1 = 1", ": 1"),
    ("singleton_type", "def f(): this.type = this", ": this.type"),
    ("projected_type", "def f(): A#B = ???", ": A#B"),
]


@pytest.mark.parametrize("kind,decl,shown", RETURN_KINDS, ids=[k[0] for k in RETURN_KINDS])
def test_return_type_kind_is_hashed(tmp_path: Path, kind: str, decl: str, shown: str) -> None:
    a, b = _pair(tmp_path, _obj(decl), _obj("def f(): Unit = ()"))
    assert (a.signature or "").endswith(shown)
    assert a.stable_id != b.stable_id


def test_parameterless_def_return_type_is_hashed(tmp_path: Path) -> None:
    """``def f: Int`` has no parameter clause; its return type was dropped."""
    a, b = _pair(tmp_path, _obj("def f: Int = 1"), _obj("def f: String = \"\""))
    assert a.signature == "(): Int"
    assert b.signature == "(): String"
    assert a.stable_id != b.stable_id


def test_abstract_declaration_types_are_hashed(tmp_path: Path) -> None:
    a, b = _pair(
        tmp_path, "trait T {\n  def f(a: m.T): m.U\n}\n", "trait T {\n  def f(): Int\n}\n",
        name="T.f")
    assert a.signature == "(a: m.T): m.U"
    assert a.stable_id != b.stable_id


def test_curried_clauses_are_not_flattened(tmp_path: Path) -> None:
    a, b = _pair(
        tmp_path, _obj("def f(a: Int)(b: Int): Int = a"), _obj("def f(a: Int, b: Int): Int = a"))
    assert a.signature == "(a: Int)(b: Int): Int"
    assert a.stable_id != b.stable_id


@pytest.mark.parametrize("keyword", ["implicit", "using"])
def test_implicit_and_using_clauses_keep_their_keyword(tmp_path: Path, keyword: str) -> None:
    a, b = _pair(
        tmp_path, _obj(f"def f(a: Int)({keyword} b: Ord): Int = a"),
        _obj("def f(a: Int)(b: Ord): Int = a"))
    assert a.signature == f"(a: Int)({keyword} b: Ord): Int"
    assert a.stable_id != b.stable_id


def test_anonymous_using_parameters_are_written(tmp_path: Path) -> None:
    """``(using Ord[Int], Show)`` has no ``parameter`` node; it was dropped whole."""
    a, b = _pair(
        tmp_path, _obj("def f(a: Int)(using Ord[Int], /* c */ Show): Int = a"),
        _obj("def f(a: Int)(using Ord[Long], /* c */ Show): Int = a"))
    assert a.signature == "(a: Int)(using Ord[Int], Show): Int"
    assert a.stable_id != b.stable_id


def test_comment_between_parameters_is_not_written(tmp_path: Path) -> None:
    sym = _symbol(tmp_path, _obj("def f(/* c */ a: Int, // d\n b: Int): Int = a"), "O.f")
    assert sym.signature == "(a: Int, b: Int): Int"


def test_extension_receiver_is_hashed(tmp_path: Path) -> None:
    a, b = _pair(
        tmp_path, "extension (x: Int) def g(y: Int): Int = y\n",
        "extension (x: String) def g(y: Int): Int = y\n", name="g")
    assert a.signature == "(x: Int)(y: Int): Int"
    assert a.stable_id != b.stable_id


def test_extension_using_clause_is_hashed(tmp_path: Path) -> None:
    a, b = _pair(
        tmp_path, "extension [T](xs: List[T])(using Ord[T])\n  def g: T = xs.head\n",
        "extension [T](xs: List[T])\n  def g: T = xs.head\n", name="g")
    assert a.signature == "(xs: List[T])(using Ord[T]): T"
    assert a.stable_id != b.stable_id


def test_secondary_constructor_implicit_clause_is_hashed(tmp_path: Path) -> None:
    cls = "class K(a: Int) {\n  def this(s: Int, t: Int)%s = this(s)\n}\n"
    a, b = _pair(tmp_path, cls % "(implicit u: Ec)", cls % "", name="K.this")
    assert a.kind == "constructor"
    assert a.signature == "(s: Int, t: Int)(implicit u: Ec)"
    assert a.stable_id != b.stable_id


FIELD_KINDS = [
    ("generic_type", "val v: List[Int] = Nil", "List[Int]"),
    ("stable_type_identifier", "var v: m.T = ???", "m.T"),
    ("function_type", "lazy val v: Int => Int = identity", "Int => Int"),
    ("compound_type", "val v: A with B = ???", "A with B"),
    ("tuple_type", "val v: (Int, Int) = (1, 1)", "(Int, Int)"),
]


@pytest.mark.parametrize("kind,decl,shown", FIELD_KINDS, ids=[k[0] for k in FIELD_KINDS])
def test_field_type_kind_is_hashed(tmp_path: Path, kind: str, decl: str, shown: str) -> None:
    a, b = _pair(tmp_path, _obj(decl), _obj("val v = ???"), name="O.v")
    assert a.kind == "field"
    assert a.signature == shown
    assert a.stable_id != b.stable_id


def test_abstract_field_declaration_type_is_hashed(tmp_path: Path) -> None:
    a, b = _pair(
        tmp_path, "trait T {\n  val v: List[Int]\n}\n", "trait T {\n  val v: List[Long]\n}\n",
        name="T.v")
    assert a.signature == "List[Int]"
    assert a.stable_id != b.stable_id


def test_normalizer_keeps_clauses_and_keywords() -> None:
    assert normalize_scala_signature("(a: Int)(b: m.T): m.U") == "(Int)(T)U"
    assert normalize_scala_signature("(a: Int)(implicit o: Ord)") == "(Int)(implicit Ord)"
    assert normalize_scala_signature("(using Ord[Int], Show): Int") == "(using Ord[Int],Show)Int"


def test_normalizer_unclosed_clause_falls_back_to_shared() -> None:
    assert normalize_scala_signature("(a: Int") is None


# Hole-free declarations: texts and ids recorded on the base arm (dev
# 3da1f85b37) for this exact file at path A.scala. They must not move.
HOLE_FREE = (
    "object O {\n"
    "  def p(a: Int, b: List[String]): Map[Int, String] = ???\n"
    "  def q(f: (Int, String) => Boolean, t: (Int, Int)): Unit = ()\n"
    "  def r(a: A | B, b: F[_]): Int = 1\n"
    "  def s(): Int = 1\n"
    "  def u = 1\n"
    "  val v: Int = 1\n"
    "  val w = 2\n"
    "}\n"
    "class K(a: Int) {\n"
    "  def this(s: String) = this(1)\n"
    "}\n"
)
HOLE_FREE_PINS = {
    "O.p": ("(a: Int, b: List[String]): Map[Int, String]", "hgstableid15:bb4038a1b0306639"),
    "O.q": ("(f: (Int, String) => Boolean, t: (Int, Int))", "hgstableid15:14d159e9ddc767c5"),
    "O.r": ("(a: A | B, b: F[_]): Int", "hgstableid15:a67407c0019ffdd7"),
    "O.s": ("(): Int", "hgstableid15:e5f6ef73b995c21d"),
    "O.u": ("()", "hgstableid15:cb227b6fa4999140"),
    "O.v": ("Int", "hgstableid15:efbf633667397858"),
    "O.w": (None, "hgstableid15:b056729bff274ba9"),
    "K.this": ("(s: String)", "hgstableid15:7e8221197b502170"),
}


def test_hole_free_declarations_hash_as_before(tmp_path: Path) -> None:
    (tmp_path / "A.scala").write_text(HOLE_FREE)
    by_name = {s.name: s for s in analyze_scala(tmp_path).symbols}
    got = {n: (by_name[n].signature, by_name[n].stable_id) for n in HOLE_FREE_PINS}
    assert got == HOLE_FREE_PINS
