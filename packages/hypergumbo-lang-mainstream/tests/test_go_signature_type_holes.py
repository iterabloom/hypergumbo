# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/go.py
"""A Go callable's signature writes every parameter, and its id hashes it.

``_extract_go_signature`` is both the displayed ``Symbol.signature`` and the
text normalized and hashed into the typed ``stable_id``. It wrote a parameter
only when its type node was one of ten kinds: a ``variadic_parameter_declaration``
(``xs ...int``) was always dropped, and so was a parameter typed by a
``generic_type`` (``List[int]``, ``pkg.List[int]``) or a ``parenthesized_type``
(``(int)``). ``f(xs ...int)`` and ``f(xs ...string)`` hashed one text. A
generic function's type parameter list was not hashed at all, so
``f[T any](x T)`` and ``f[T comparable](x T)`` shared an id.

Every parameter is now written with the source text of its type, and the
type parameter list leads the hashed text. A declaration that used none of
these forms hashes exactly as before: the last tests pin base-arm ids.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tree_sitter
import tree_sitter_go

from hypergumbo_lang_mainstream.go import (
    _go_type_parameters_text,
    analyze_go,
)


def _symbols(repo: Path, source: str) -> dict:
    (repo / "a.go").write_text(source)
    return {
        s.name: s for s in analyze_go(repo).symbols
        if s.kind in ("function", "method")
    }


def _func(tmp_path: Path, tag: str, decl: str) -> tuple[str, str]:
    """(signature, stable_id) of ``f`` declared as ``func f<decl> {}``."""
    repo = tmp_path / tag
    repo.mkdir()
    f = _symbols(repo, f"package p\n\nfunc f{decl} {{}}\n")["f"]
    return f.signature, f.stable_id


# Declaration -> the displayed signature, which now contains the parameter.
WRITTEN = {
    "(xs ...int)": "(xs ...int)",  # variadic_parameter_declaration
    "(...string)": "(...string)",  # unnamed variadic
    "(a int, xs ...interface{})": "(a int, xs ...interface{})",
    "(xs ...any)": "(xs ...any)",
    "(x List[int])": "(x List[int])",  # generic_type
    "(x pkg.List[int, string])": "(x pkg.List[int, string])",  # qualified generic
    "(x (int))": "(x (int))",  # parenthesized_type
    "(int, string)": "(int, string)",  # unnamed parameters
}


@pytest.mark.parametrize(("decl", "shown"), WRITTEN.items())
def test_every_parameter_is_written(tmp_path: Path, decl: str, shown: str) -> None:
    signature, _ = _func(tmp_path, "a", decl)
    assert signature == shown


# Two declarations that differ only inside a former hole.
PAIRS = [
    ("(xs ...int)", "(xs ...string)"),
    ("(a int, xs ...int)", "(a int)"),
    ("(x List[int])", "(x List[string])"),
    ("(x List[int])", "()"),
    ("(x pkg.List[int])", "(x pkg.List[string])"),
    ("(x (int))", "()"),
    ("[T any](x T)", "[T comparable](x T)"),
    ("[T any](x T)", "(x T)"),
    ("[K comparable, V any](m map[K]V)", "[K comparable, V comparable](m map[K]V)"),
]


@pytest.mark.parametrize(("left", "right"), PAIRS)
def test_two_declarations_that_differ_inside_a_hole_have_two_ids(
    tmp_path: Path, left: str, right: str,
) -> None:
    assert _func(tmp_path, "l", left)[1] != _func(tmp_path, "r", right)[1]


def test_an_interface_method_spec_writes_its_variadic_parameter(tmp_path: Path) -> None:
    ids = []
    for tag, elem in (("l", "int"), ("r", "string")):
        repo = tmp_path / tag
        repo.mkdir()
        (repo / "a.go").write_text(
            f"package p\n\ntype I interface {{\n\tM(xs ...{elem})\n}}\n")
        m = {s.name: s for s in analyze_go(repo).symbols}["I.M"]
        ids.append(m.stable_id)
    assert ids[0] != ids[1]


def _type_params(source: bytes) -> str:
    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_go.language()))
    decl = parser.parse(source).root_node.children[1]
    return _go_type_parameters_text(decl, source)


def test_the_type_parameter_list_is_its_source_text() -> None:
    assert _type_params(
        b"package p\nfunc Map[T any, U comparable](x T) U {}\n",
    ) == "[T any, U comparable]"
    assert _type_params(
        b"package p\nfunc F[\n\tT interface{ ~int | ~string },\n](x T) {}\n",
    ) == "[ T interface{ ~int | ~string }, ]"
    assert _type_params(b"package p\nfunc F(x T) {}\n") == ""


def test_the_display_signature_has_no_type_parameters(tmp_path: Path) -> None:
    signature, _ = _func(tmp_path, "a", "[T any](x T) T")
    assert signature == "(x T) T"


# Ids minted by the base arm (dev 3da1f85b37) for declarations whose types use
# only the kinds that already had a branch: they must not move.
_HOLE_FREE = (
    "package p\n\n"
    'import "net/http"\n\n'
    "type S struct{}\n\n"
    "type I interface {\n"
    "\tGet(key string) (*S, error)\n"
    "}\n\n"
    "func Plain(a, b int, s []string, m map[string]*S) (n int, err error) { return }\n\n"
    "func Handler(w http.ResponseWriter, r *http.Request) {}\n\n"
    "func Ret() *http.Response { return nil }\n\n"
    "func Chan() chan int { return nil }\n\n"
    "func (s *S) Do(x [4]byte, f func(int)) error { return nil }\n"
)
_BASE_IDS = {
    "I.Get": "hgstableid15:c3d6e6c6be984913",
    "Plain": "hgstableid15:b0498b443211caed",
    "Handler": "hgstableid15:d6d25163f98eaf16",
    "Ret": "hgstableid15:2ca5bcb183918520",
    "Chan": "hgstableid15:97fc58949ca7e6f1",
    "S.Do": "hgstableid15:c60b10f5dc28c3f1",
}


def test_hole_free_declarations_keep_their_ids(tmp_path: Path) -> None:
    symbols = _symbols(tmp_path, _HOLE_FREE)
    assert {name: symbols[name].stable_id for name in _BASE_IDS} == _BASE_IDS
