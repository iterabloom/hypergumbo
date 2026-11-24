# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/kotlin.py
"""A Kotlin declaration is scoped by every declaration that owns its code.

Every Kotlin stable_id hashes the chain of declarations enclosing it
(``_kotlin_scope``, INV-fabih). Until this change only classes, objects and
function NAMES were labels in that chain, so a function local to a secondary
constructor, an accessor, an ``init`` block or a lambda bound to a property
was scoped by its CLASS, and one local to a ``fun`` by the bare name its
overloads share. Two same-named locals of two constructors, of two overloads,
of two property getters, of a getter and its setter, of a companion ``fun``
and its class's tied (WI-finam, the defect WI-podor fixed for Swift); so did
the members two enum entries override. The run's occurrence split told them apart by order, so deleting
the first moved the second's id.

The properties pinned here: each pair of holders gives its locals two ids;
deleting the first holder's local moves no id of the second's through the
occurrence split; a local of a ``fun`` whose hashed text is ``()`` keeps the
formula it had, and so does every member of a class or of its companion
object; and what is not the
holder's identity -- a parameter's name, the layout of its parameter list,
its visibility, a default value -- moves no local's id.
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
from hypergumbo_core.overload_arity import scoped_table_stable_id
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin


def _symbols(root: Path, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.kt").write_text(text)
    return [s for s in analyze_kotlin(root).symbols if s.kind != "file"]


def _short(sym: Symbol) -> str:
    return sym.name.rsplit(".", 1)[-1]


def _local_ids(root: Path, text: str, local: str) -> list[str]:
    """The stable ids of the symbols whose last name part is ``local``, in
    source order."""
    found = sorted(
        (s for s in _symbols(root, text) if _short(s) == local),
        key=lambda s: (s.span.start_line, s.span.start_col),
    )
    assert found, local
    return [s.stable_id or "" for s in found]


#: Two holders of a same-named local function ``h``: each is ``(first,
#: second)`` member text of ``class T``; ``h`` is declared in each.
_HOLDERS: dict[str, tuple[str, str]] = {
    "two-constructors": ("constructor(a: Int) { fun h() {} }",
                         "constructor(b: String) { fun h() {} }"),
    "two-overloads-by-type": ("fun f(a: Int) { fun h() {} }", "fun f(b: String) { fun h() {} }"),
    "overloads-by-receiver": ("fun Int.f() { fun h() {} }", "fun String.f() { fun h() {} }"),
    "overloads-by-vararg": ("fun f(a: Int) { fun h() {} }", "fun f(vararg a: Int) { fun h() {} }"),
    "two-property-getters": ("val p: Int get() { fun h() {}; return 1 }",
                             "val q: Int get() { fun h() {}; return 1 }"),
    "getter-and-setter": ("var p: Int = 0\n        get() { fun h() {}; return 1 }\n",
                          "        set(v) { fun h() {} }"),
    "two-extension-property-getters": ("val Int.p: Int get() { fun h() {}; return 1 }",
                                       "val String.p: Int get() { fun h() {}; return 1 }"),
    "two-bound-lambdas": ("val a = { fun h() {} }", "val b = { fun h() {} }"),
    "two-object-literals": ("val a = object { fun h() {} }", "val b = object { fun h() {} }"),
    "companion-and-class": ("companion object { fun f() { fun h() {} } }", "fun f() { fun h() {} }"),
    "companion-and-class-with-parameters": ("companion object { fun f(a: Int) { fun h() {} } }",
                                            "fun f(a: Int) { fun h() {} }"),
    "companion-and-class-properties": ("companion object { val p = { fun h() {} } }",
                                       "val p = { fun h() {} }"),
    "init-block-and-member": ("init { fun h() {} }", "fun h() {}"),
    "init-block-and-init-function": ("init { fun h() {} }", "fun init() { fun h() {} }"),
    "property-and-function-of-one-name": ("val p = { fun h() {} }", "fun p() { fun h() {} }"),
    "receiver-and-name-do-not-run-together": ("fun Int.f() { fun h() {} }",
                                              "fun nt.fI() { fun h() {} }"),
    "constructor-and-function-named-constructor": (
        "constructor(a: Int) { fun h() {} }", "fun constructor(a: Int) { fun h() {} }"),
}


def _holder_text(first: str, second: str) -> str:
    return "class T {\n    " + first + "\n    " + second + "\n}\n"


@pytest.mark.parametrize("shape", sorted(_HOLDERS))
def test_the_locals_of_two_holders_get_two_ids(tmp_path: Path, shape: str) -> None:
    ids = _local_ids(tmp_path / "r", _holder_text(*_HOLDERS[shape]), "h")
    assert len(ids) == 2
    assert ids[0] != ids[1], shape


@pytest.mark.parametrize("shape", sorted(_HOLDERS))
def test_a_local_type_of_two_holders_gets_two_ids(tmp_path: Path, shape: str) -> None:
    """The same for a local CLASS (a table-formula id) and its method (a
    typed id): ``class L { fun m() {} }`` in place of ``fun h() {}``."""
    first, second = (
        part.replace("fun h() {}", "class L { fun m() {} }") for part in _HOLDERS[shape]
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
    blanked = first.replace("fun h() {}; ", "").replace("fun h() {}", "")
    alone = _symbols(tmp_path / "alone", _holder_text(blanked, second))
    split_within_file_stable_id_collisions(both)
    split_within_file_stable_id_collisions(alone)
    last = max((s for s in both if _short(s) == "h"), key=lambda s: s.span.start_line)
    survivor = [s for s in alone if _short(s) == "h"]
    assert len(survivor) == 1
    assert survivor[0].stable_id == last.stable_id, shape


def test_the_members_two_enum_entries_override_get_two_ids(tmp_path: Path) -> None:
    """An enum entry's body is scoped by the entry: ``PLUS``'s and
    ``MINUS``'s ``apply`` and the abstract one they override are three ids."""
    src = (
        "enum class Op {\n"
        "    PLUS { override fun apply(a: Int) = a },\n"
        "    MINUS { override fun apply(a: Int) = -a };\n"
        "    abstract fun apply(a: Int): Int\n"
        "}\n"
    )
    ids = _local_ids(tmp_path / "r", src, "apply")
    assert len(ids) == 3
    assert len(set(ids)) == 3


def test_the_items_repro(tmp_path: Path) -> None:
    """The item's three pairs: two constructors, two overloads, two getters."""
    src = (
        "class K {\n"
        "    constructor(a: Int) { fun open() {} }\n"
        "    constructor(b: String) { fun open() {} }\n"
        "    fun f(a: Int) { fun helper() {} }\n"
        "    fun f(b: String) { fun helper() {} }\n"
        "    val p: Int get() { fun g() {}; return 1 }\n"
        "    val q: Int get() { fun g() {}; return 1 }\n"
        "}\n"
    )
    for local in ("open", "helper", "g"):
        ids = _local_ids(tmp_path / local, src, local)
        assert len(ids) == 2
        assert ids[0] != ids[1], local


def test_members_and_locals_of_an_empty_signature_fun_keep_their_formula(
    tmp_path: Path,
) -> None:
    """A class's members, and what a ``fun`` whose hashed text is ``()``
    declares, hash the chain they hashed before: a class by its name, a
    ``fun f()`` by its bare name. Their ids do not move."""
    src = (
        "class T {\n"
        "    fun m(a: Int) {}\n"
        "    fun f() { fun h() {} }\n"
        "    class N { fun g() {} }\n"
        "    companion object { fun c(a: Int) {} }\n"
        "}\n"
        "fun top() { fun t() {} }\n"
    )
    fsid = make_file_stable_id("kotlin", "a.kt")
    got = {_short(s): s for s in _symbols(tmp_path / "r", src)}
    typed = {"m": ("(Int)", "T.m"), "h": ("()", "T.f.h"), "g": ("()", "T.N.g"),
             "t": ("()", "top.t"), "c": ("(Int)", "T.c")}
    for local, (sig, chain) in typed.items():
        sym = got[local]
        assert sym.stable_id == make_typed_stable_id(
            sym.kind, sig, "", name=local, qualified_name=chain, file_stable_id=fsid,
        ), local
    assert got["N"].stable_id == scoped_table_stable_id(
        kind="class", lang="kotlin", path="a.kt", name="N", scope=["T"])


#: Edits to a holder that are not a change of its identity: the local ``h``
#: keeps its id.
_SAME_HOLDER: dict[str, tuple[str, str]] = {
    "parameter-name": ("fun f(a: Int) { fun h() {} }", "fun f(b: Int) { fun h() {} }"),
    "parameter-list-layout": (
        "fun f(a: Map<String,\n        Int>,\n        b: Int) { fun h() {} }",
        "fun f(a: Map<String, Int>, b: Int) { fun h() {} }"),
    "visibility": ("private fun f(a: Int) { fun h() {} }", "fun f(a: Int) { fun h() {} }"),
    "default-value": ("fun f(a: Int = 1) { fun h() {} }", "fun f(a: Int = 2) { fun h() {} }"),
    "constructor-parameter-name": ("constructor(a: Int) { fun h() {} }",
                                   "constructor(b: Int) { fun h() {} }"),
    "receiver-layout": ("fun Map<String,Int>.f() { fun h() {} }",
                        "fun Map<String, Int>.f() { fun h() {} }"),
    "extension-property-receiver-layout": (
        "val Map<String,Int>.p: Int get() { fun h() {}; return 1 }",
        "val Map<String, Int>.p: Int get() { fun h() {}; return 1 }"),
    "unnamed-and-named-companion": ("companion object { fun f() { fun h() {} } }",
                                    "companion object Companion { fun f() { fun h() {} } }"),
}


@pytest.mark.parametrize("local", ["h", "L"])
@pytest.mark.parametrize("shape", sorted(_SAME_HOLDER))
def test_an_edit_that_is_not_the_holders_identity_moves_no_local(
    tmp_path: Path, shape: str, local: str,
) -> None:
    """For a local function (a typed id) and a local class (a table-formula
    id, whose scope slot nothing else canonicalizes)."""
    one, other = (
        part.replace("fun h() {}", "class L {}") if local == "L" else part
        for part in _SAME_HOLDER[shape]
    )
    a = _local_ids(tmp_path / "a", "class T {\n    " + one + "\n}\n", local)
    b = _local_ids(tmp_path / "b", "class T {\n    " + other + "\n}\n", local)
    assert a == b, shape


def test_a_destructuring_property_adds_no_label(tmp_path: Path) -> None:
    """A declaration that binds no single name is no label: a local of its
    initialiser lambda is scoped by what encloses the property."""
    with_pair = _local_ids(
        tmp_path / "a",
        "class T {\n    fun f() {\n        val (a, b) = run { fun h() {}; 1 to 2 }\n    }\n}\n", "h")
    bare = _local_ids(
        tmp_path / "b", "class T {\n    fun f() {\n        run { fun h() {} }\n    }\n}\n", "h")
    assert with_pair == bare
