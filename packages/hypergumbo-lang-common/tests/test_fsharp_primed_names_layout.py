# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/fsharp.py
"""A primed F# name (``T'``) does not open a string literal in the hashed type.

F# members take the typed tier, whose ``layout_free`` keeps a string or
character literal whole. It read a ``'`` that closes before a non-word
character as a quote, so the two primes of ``T' -> U'`` paired into the
literal ``' -> U'`` and the spacing between them stayed hashed:
``(f: T'->U')`` and ``(f: T' -> U')`` were two ids (WI-sidus). A ``'``
right after a word is a prime unless the word is a literal's prefix
(``L'a'``, ``b'a'``).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_common.fsharp import analyze_fsharp

_HEAD = "module M\ntype T' = int\ntype U' = string\ntype K() =\n"

_SPACED = _HEAD + """\
    member this.A(f: T' -> U') = 1
    member this.B(m: Map<T', U'>) = 1
    member this.C(p: T' * U') = 1
"""

_TIGHT = _HEAD + """\
    member this.A(f: T'->U') = 1
    member this.B(m: Map<T',U'>) = 1
    member this.C(p: T'*U') = 1
"""

_SPLIT = _HEAD + """\
    member this.A(f: T'
                    -> U') = 1
    member this.B(m: Map<T',
                         U'>) = 1
    member this.C(p: T'  *  U') = 1
"""


def _ids(tmp_path: Path, text: str) -> dict[str, str | None]:
    for old in tmp_path.iterdir():
        old.unlink()
    (tmp_path / "M.fs").write_text(text)
    return {s.name: s.stable_id for s in analyze_fsharp(tmp_path).symbols if s.kind == "method"}


def test_a_primed_type_in_another_layout_keeps_its_id(tmp_path: Path) -> None:
    spaced = _ids(tmp_path, _SPACED)
    assert set(spaced) == {"K.A", "K.B", "K.C"} and all(spaced.values())  # reach
    assert _ids(tmp_path, _TIGHT) == spaced
    assert _ids(tmp_path, _SPLIT) == spaced


def test_another_primed_type_is_another_id(tmp_path: Path) -> None:
    ids_tu = _ids(tmp_path, _HEAD + "    member this.A(f: T' -> U') = 1\n")
    ids_ut = _ids(tmp_path, _HEAD + "    member this.A(f: U' -> T') = 1\n")
    assert ids_tu["K.A"] != ids_ut["K.A"]
