# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/d_lang.py
"""A D name's layout is not a stable-id input (``LAYOUT_FREE_NAME_LANGUAGES``).

The D analyzer mints untyped-tier ids, whose formula hashes the name as
written unless the language is in core's ``LAYOUT_FREE_NAME_LANGUAGES``. A D
module's name is its declaration's text, so ``module pkg.sub.m;`` and the
same declaration written over lines were two names and two ids (WI-sidus).
Every other D name the analyzer emits is an identifier, which
``layout_free`` leaves as it is (0 of 21,706 D untyped ids changed on dub
and dmd).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_extended1.d_lang import analyze_d


def _ids(tmp_path: Path, text: str) -> dict[str, str | None]:
    """Each symbol's stable id, by kind and name without whitespace."""
    for old in tmp_path.iterdir():
        old.unlink()
    (tmp_path / "m.d").write_text(text)
    return {
        f"{sym.kind}:{''.join(sym.name.split())}": sym.stable_id
        for sym in analyze_d(tmp_path).symbols
    }


_BODY = "struct V { int x; }\nint f(int a) { return a; }\n"


def test_a_module_name_written_over_lines_keeps_its_id(tmp_path: Path) -> None:
    one_line = _ids(tmp_path, "module pkg.sub.m;\n" + _BODY)
    split = _ids(tmp_path, "module pkg .\n    sub . m;\n" + _BODY)
    assert one_line["module:pkg.sub.m"] is not None  # reach
    assert one_line == split


def test_another_module_name_is_another_id(tmp_path: Path) -> None:
    ids_m = _ids(tmp_path, "module pkg.sub.m;\n" + _BODY)
    ids_n = _ids(tmp_path, "module pkg.sub.n;\n" + _BODY)
    assert ids_m["module:pkg.sub.m"] != ids_n["module:pkg.sub.n"]
