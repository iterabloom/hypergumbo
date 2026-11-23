# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/luau.py
"""A Luau function name written with spaces keeps its id.

The Luau analyzer names ``function a.b.c()`` by its name text, so
``function a . b . c()`` and ``function a : m()`` were other names; the
untyped formula hashes names ``without_layout`` in every language but the
content ones (WI-sidus).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_extended1.luau import analyze_luau


def _ids(tmp_path: Path, text: str) -> list[str | None]:
    for old in tmp_path.iterdir():
        old.unlink()
    (tmp_path / "m.luau").write_text("local a = {}\na.b = {}\n" + text)
    syms = sorted((s for s in analyze_luau(tmp_path).symbols if s.kind == "function"),
                  key=lambda s: s.span.start_line)
    return [s.stable_id for s in syms]


def test_a_spaced_function_name_keeps_its_id(tmp_path: Path) -> None:
    tight = _ids(tmp_path, "function a.b.c(x) return x end\nfunction a:m(y) return y end\n")
    spaced = _ids(tmp_path, "function a . b . c(x) return x end\nfunction a : m(y) return y end\n")
    assert len(tight) == 2 and all(tight)  # reach
    assert tight == spaced


def test_another_function_name_is_another_id(tmp_path: Path) -> None:
    assert _ids(tmp_path, "function a.b.c(x) return x end\n") != _ids(tmp_path, "function a.b.d(x) return x end\n")
