# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/julia.py
"""A Julia type imported by name and its qualified spelling hash alike.

WI-rafis (stable-id scheme v19). A Julia method's stable id hashes its
positional parameters' types (``declared_arity_stable_id``, the typed tier) and
a struct field's id its type. Both were hashed as written, so ``f(x::T)``
under ``import Foo: T`` and ``f(x::Foo.T)``, one method, got two ids. The
file's ``import`` / ``using`` statements now bind names the way WI-palus's
mechanism reads them (``FileTypeNames``, ``resolving_type_names``):

- ``import Foo: T`` / ``using Foo: T`` binds ``T`` to ``Foo.T``;
- ``import Bar: S as Q`` binds ``Q`` to ``Bar.S``;
- ``import Pkg as P`` and ``import A.B`` bind the module name (``P.R`` is
  ``Pkg.R``); a relative path keeps its dots (``import .Sub: X``).

Not bound: ``using Mod`` (every exported name, unlisted: a glob), a method's
``where`` type parameters, and ``Base.X`` (no list of Base's exported names is
on hand, and an unexported ``Base.X`` is not the bare ``X``). Each case
declares one symbol in two files at the same path; ``SAME`` asserts one id,
``APART`` two.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_common.julia import analyze_julia


def _id(root: Path, text: str, name: str) -> str:
    root.mkdir(parents=True)
    (root / "m.jl").write_text(text)
    found = [s for s in analyze_julia(root).symbols if s.name == name]
    assert len(found) == 1, [s.name for s in analyze_julia(root).symbols]
    assert found[0].stable_id is not None, name
    return str(found[0].stable_id)


_H = ("module M\nimport Foo: T\nimport Bar: S as Q\nimport Pkg as P\nusing Qux: W, V\n"
      "import A.B\nimport .Sub: X\nusing Baz\n")

#: (first body, second body, symbol): one declaration, one type spelled two ways.
SAME: dict[str, tuple[str, str, str]] = {
    "selected-import": ("f(x::T) = 1", "f(x::Foo.T) = 1", "f"),
    "selected-alias": ("f(x::Q) = 1", "f(x::Bar.S) = 1", "f"),
    "module-alias": ("f(x::P.R) = 1", "f(x::Pkg.R) = 1", "f"),
    "using-selected": ("f(x::W, y::V) = 1", "f(x::Qux.W, y::Qux.V) = 1", "f"),
    "dotted-module": ("f(x::B.Y) = 1", "f(x::A.B.Y) = 1", "f"),
    "parametric": ("f(x::Vector{T}) = 1", "f(x::Vector{Foo.T}) = 1", "f"),
    "full-form": ("function f(x::Q, y::T)::Int\n 1\nend", "function f(x::Bar.S, y::Foo.T)::Int\n 1\nend",
                  "f"),
    "where-other": ("f(x::T, y::U) where {U} = 1", "f(x::Foo.T, y::U) where {U} = 1", "f"),
    "field": ("struct K\n    a::T\n    b::Vector{Q}\nend", "struct K\n    a::Foo.T\n    b::Vector{Bar.S}\nend",
              "K.a"),
    "field-subtype-struct": ("struct K <: Abs\n    a::Q\nend", "struct K <: Abs\n    a::Bar.S\nend", "K.a"),
}

APART: dict[str, tuple[str, str, str]] = {
    # a where-clause type parameter is not the imported T
    "where-shadows": ("f(x::T) where {T} = 1", "f(x::Foo.T) where {T} = 1", "f"),
    "where-bound": ("f(x::T) where T<:Real = 1", "f(x::Foo.T) where T<:Real = 1", "f"),
    "where-full-form": ("function f(x::T) where {T}\n 1\nend", "function f(x::Foo.T) where {T}\n 1\nend",
                        "f"),
    # ``using Baz`` lists no name
    "using-glob": ("f(x::U) = 1", "f(x::Baz.U) = 1", "f"),
    # Base is not folded
    "base": ("f(x::Int) = 1", "f(x::Base.Int) = 1", "f"),
    # two imported types stay two
    "two-types": ("f(x::T) = 1", "f(x::Bar.S) = 1", "f"),
    # a relative path keeps its dots (``.Sub`` is not resolved against the
    # enclosing module), as Python's does
    "relative": ("f(x::X) = 1", "f(x::Sub.X) = 1", "f"),
    # a type the file declares keeps its name
    "declared": ("struct T end\nf(x::T) = 1", "struct T end\nf(x::Foo.T) = 1", "f"),
}


@pytest.mark.parametrize("case", sorted(SAME))
def test_two_spellings_of_one_imported_type_hash_alike(tmp_path: Path, case: str) -> None:
    first, second, name = SAME[case]
    assert _id(tmp_path / "x", _H + first + "\nend\n", name) == _id(
        tmp_path / "y", _H + second + "\nend\n", name)


@pytest.mark.parametrize("case", sorted(APART))
def test_another_type_or_an_unbound_spelling_hashes_apart(tmp_path: Path, case: str) -> None:
    first, second, name = APART[case]
    assert _id(tmp_path / "x", _H + first + "\nend\n", name) != _id(
        tmp_path / "y", _H + second + "\nend\n", name)


def test_an_import_change_that_changes_no_referent_moves_no_id(tmp_path: Path) -> None:
    """Reordering the imports, adding an unrelated one, or importing what is
    already written qualified keeps the id."""
    body = "f(x::Foo.T, y::Q) = 1\nend\n"
    base = "module M\nimport Bar: S as Q\nimport Foo: T\n" + body
    ids = {
        _id(tmp_path / "base", base, "f"),
        _id(tmp_path / "reordered", "module M\nimport Foo: T\nimport Bar: S as Q\n" + body, "f"),
        _id(tmp_path / "unrelated", "module M\nimport Bar: S as Q\nimport Foo: T\nusing Zed: Z\n" + body,
            "f"),
        _id(tmp_path / "without", "module M\nimport Bar: S as Q\n" + body, "f"),
    }
    assert len(ids) == 1
