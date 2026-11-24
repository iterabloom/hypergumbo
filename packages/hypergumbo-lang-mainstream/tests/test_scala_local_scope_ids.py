# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/scala.py
"""A Scala local declaration is scoped by the ``def`` or ``val`` that holds it.

Every Scala stable_id hashes the chain of scopes enclosing it
(``_scala_scope``, WI-tusad / INV-fabih). Until scheme v19 a ``def`` was a
label in that chain by its NAME alone and a ``val`` / ``var`` was none, so two
same-named locals of two secondary constructors (``def this(a: Int)`` /
``def this(b: String)``), of two overloads, or of the blocks bound to ``val
p`` and ``val q`` tied (WI-finam, the defect WI-podor fixed for Swift); the
run's occurrence split told them apart by order only.

Pinned: each pair of holders gives its locals two ids; deleting the first
holder's local moves no id of the second's; a local of a ``def`` whose hashed
text is ``()`` keeps the formula it had, and so does every member of a class;
a parameter's name and the layout of the parameter list move no local's id.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import (
    make_file_stable_id,
    make_typed_stable_id,
    split_within_file_stable_id_collisions,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.scala import analyze_scala


def _symbols(root: Path, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.scala").write_text(text)
    return [s for s in analyze_scala(root).symbols if s.kind != "file"]


def _short(sym: Symbol) -> str:
    return sym.name.rsplit(".", 1)[-1]


def _local_ids(root: Path, text: str, local: str) -> list[str]:
    found = sorted(
        (s for s in _symbols(root, text) if _short(s) == local),
        key=lambda s: (s.span.start_line, s.span.start_col),
    )
    assert found, local
    return [s.stable_id or "" for s in found]


#: Two holders of a same-named local ``h``: member text of ``class T``.
_HOLDERS: dict[str, tuple[str, str]] = {
    "two-secondary-constructors": ("def this(a: Int) = { this(); def h() = 1 }",
                                   "def this(b: String) = { this(); def h() = 1 }"),
    "two-overloads-by-type": ("def f(a: Int): Int = { def h() = 1; h() }",
                              "def f(b: String): Int = { def h() = 1; h() }"),
    "overloads-by-clause": ("def f(a: Int): Int = { def h() = 1; h() }",
                            "def f(a: Int)(b: Int): Int = { def h() = 1; h() }"),
    "two-operator-defs": ("def &&(o: T): T = { def h() = 1; h(); this }",
                          "def ||(o: T): T = { def h() = 1; h(); this }"),
    "two-vals": ("val p = { def h() = 1; h() }", "val q = { def h() = 1; h() }"),
    "val-and-var": ("val p = { def h() = 1; h() }", "var p = { def h() = 1; h() }"),
    "val-and-def-of-one-name": ("val p = { def h() = 1; h() }",
                                "def p(a: Int): Int = { def h() = 1; h() }"),
}


def _holder_text(first: str, second: str) -> str:
    return "class T {\n  " + first + "\n  " + second + "\n}\n"


@pytest.mark.parametrize("shape", sorted(_HOLDERS))
def test_the_locals_of_two_holders_get_two_ids(tmp_path: Path, shape: str) -> None:
    ids = _local_ids(tmp_path / "r", _holder_text(*_HOLDERS[shape]), "h")
    assert len(ids) == 2
    assert ids[0] != ids[1], shape


@pytest.mark.parametrize("shape", sorted(_HOLDERS))
def test_a_local_class_of_two_holders_gets_two_ids(tmp_path: Path, shape: str) -> None:
    """The same for a local CLASS (a table-formula id) and its method."""
    first, second = (
        part.replace("def h() = 1; h()", "class L { def m() = 1 }; 1")
        .replace("def h() = 1", "class L { def m() = 1 }")
        for part in _HOLDERS[shape]
    )
    text = _holder_text(first, second)
    for local in ("L", "m"):
        ids = _local_ids(tmp_path / local, text, local)
        assert len(ids) == 2
        assert ids[0] != ids[1], (shape, local)


@pytest.mark.parametrize("shape", sorted(_HOLDERS))
def test_deleting_the_first_holders_local_moves_no_id_of_the_second(
    tmp_path: Path, shape: str,
) -> None:
    first, second = _HOLDERS[shape]
    both = _symbols(tmp_path / "both", _holder_text(first, second))
    blanked = first.replace("def h() = 1; h()", "1").replace("; def h() = 1", "")
    alone = _symbols(tmp_path / "alone", _holder_text(blanked, second))
    split_within_file_stable_id_collisions(both)
    split_within_file_stable_id_collisions(alone)
    last = max((s for s in both if _short(s) == "h"), key=lambda s: s.span.start_line)
    survivor = [s for s in alone if _short(s) == "h"]
    assert len(survivor) == 1
    assert survivor[0].stable_id == last.stable_id, shape


def test_members_and_locals_of_an_empty_signature_def_keep_their_formula(
    tmp_path: Path,
) -> None:
    """A class's members, and what a ``def`` whose hashed text is ``()``
    declares, hash the chain they hashed before."""
    src = (
        "class T {\n"
        "  def m(a: Int): Int = a\n"
        "  def f() = { def h(): Int = 1; h() }\n"
        "}\n"
    )
    fsid = make_file_stable_id("scala", "a.scala")
    got = {_short(s): s for s in _symbols(tmp_path / "r", src)}
    for local, (sig, chain) in {"m": ("(Int)Int", "T.m"), "h": ("()Int", "T.f.h")}.items():
        assert got[local].stable_id == make_typed_stable_id(
            "method", sig, "", name=local, qualified_name=chain, file_stable_id=fsid,
        ), local


#: Edits to a holder that are not a change of its identity.
_SAME_HOLDER: dict[str, tuple[str, str]] = {
    "parameter-name": ("def f(a: Int): Int = { def h() = 1; h() }",
                       "def f(b: Int): Int = { def h() = 1; h() }"),
    "parameter-list-layout": ("def f(a: Map[String,\n    Int]): Int = { def h() = 1; h() }",
                              "def f(a: Map[String, Int]): Int = { def h() = 1; h() }"),
    "lazy-val": ("lazy val p = { def h() = 1; h() }", "val p = { def h() = 1; h() }"),
}


@pytest.mark.parametrize("shape", sorted(_SAME_HOLDER))
def test_an_edit_that_is_not_the_holders_identity_moves_no_local(
    tmp_path: Path, shape: str,
) -> None:
    one, other = _SAME_HOLDER[shape]
    a = _local_ids(tmp_path / "a", "class T {\n  " + one + "\n}\n", "h")
    b = _local_ids(tmp_path / "b", "class T {\n  " + other + "\n}\n", "h")
    assert a == b, shape


def test_a_pattern_val_adds_no_label(tmp_path: Path) -> None:
    """A ``val`` that binds no single name is no label."""
    with_pair = _local_ids(
        tmp_path / "a", "class T {\n  val (a, b) = { def h() = 1; (h(), 2) }\n}\n", "h")
    bare = _local_ids(tmp_path / "b", "class T {\n  locally { def h() = 1; h() }\n}\n", "h")
    assert with_pair == bare


_QUALIFIED = (
    "package cats.instances\n"
    "object EqInstances {\n"
    "  private val cacheA: Defer[Eq] = new Defer[Eq] {\n"
    "    case class Deferred[A](fa: () => Eq[A]) extends Eq[A]\n"
    "  }\n"
    "  private val cacheB: Defer[Eq] = new Defer[Eq] {\n"
    "    case class Deferred[A](fa: () => Eq[A]) extends Eq[A]\n"
    "  }\n"
    "  def build[T](x: Int): Unit = {\n"
    "    class Local\n"
    "  }\n"
    "}\n"
)


def test_a_types_qualified_name_carries_names_not_hash_labels(tmp_path: Path) -> None:
    """``Symbol.qualified_name`` is the name a reader and a name lookup use.
    The scope labels of WI-finam (``val cacheA``, ``build[$0](Int)Unit``) are
    hash inputs: they tell the two ``Deferred`` apart in the stable id and
    stay out of the qualified name, which reads as it did before v19."""
    symbols = _symbols(tmp_path, _QUALIFIED)
    deferred = [s for s in symbols if s.name == "Deferred"]
    assert [s.qualified_name for s in deferred] == ["cats.instances.EqInstances.Deferred"] * 2
    (local,) = [s for s in symbols if s.name == "Local"]
    assert local.qualified_name == "cats.instances.EqInstances.build.Local"
    # The labels still do their work where they belong: two ids.
    assert len({s.stable_id for s in deferred}) == 2
    for s in symbols:
        assert " " not in (s.qualified_name or "") and "(" not in (s.qualified_name or ""), s.qualified_name
