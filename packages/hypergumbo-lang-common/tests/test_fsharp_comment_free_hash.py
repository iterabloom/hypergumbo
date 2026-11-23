# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/fsharp.py
"""An F# comment inside a member's parameter type is not a stable-id input.

F# members hash their parameter types (typed tier), read as written. A
``(* ... *)`` comment inside a type was hashed, so editing it moved the id
(WI-sidus). Core's ``without_comments`` reads C-style comments only (a
language-blind ``(* ... *)`` rule would eat C's ``int (*)(int)``), so the F#
analyzer leaves the grammar's comment nodes out of its type text.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_common.fsharp import analyze_fsharp

_HEAD = "module M\ntype K() =\n"


def _ids(tmp_path: Path, text: str) -> dict[str, str | None]:
    for old in tmp_path.iterdir():
        old.unlink()
    (tmp_path / "M.fs").write_text(_HEAD + text)
    return {s.name: s.stable_id for s in analyze_fsharp(tmp_path).symbols if s.kind == "method"}


def test_a_comment_in_a_parameter_type_moves_no_id(tmp_path: Path) -> None:
    plain = _ids(tmp_path, "    member this.A(f: int -> string, m: Map<int, string>) = 1\n")
    commented = _ids(tmp_path, "    member this.A(f: int (* x *) -> string, m: Map<int, (* v *) string>) = 1\n")
    assert plain["K.A"] is not None  # reach
    assert plain == commented


def test_another_parameter_type_is_another_id(tmp_path: Path) -> None:
    one = _ids(tmp_path, "    member this.A(f: int -> string) = 1\n")
    two = _ids(tmp_path, "    member this.A(f: int -> int) = 1\n")
    assert one["K.A"] != two["K.A"]
