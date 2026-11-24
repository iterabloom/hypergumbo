# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/cpp.py
"""C++ overloads that differ only in a parameter's declarator marks do not
share a stable id (WI-pohom-turot part 2).

Until scheme v20 the hashed parameter type was the text before the
declarator, so ``f(int)`` / ``f(int*)``, ``g(int)`` / ``g(int&)``,
``n(int&)`` / ``n(int&&)``, ``k(int**)`` / ``k(int*&)``, ``m(int a[])`` /
``m(int a)`` and ``p(int (*cb)(int))`` / ``p(int cb)`` tied before the
occurrence split, and so did the declarations local to them (their scope
label is the function's name and hashed types). The analyzer runs on each
fixture; ties are counted on its own output, before the split.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.cpp import analyze_cpp

OVERLOADS = """\
void f(int a) { struct L { int x; }; }
void f(int* a) { struct L { int x; }; }
void g(int a) {}
void g(int& a) {}
void k(int** a) {}
void k(int*& a) {}
void m(int a[]) {}
void m(int a) {}
void n(int&& a) {}
void n(int& a) {}
void p(int (*cb)(int)) {}
void p(int cb) {}
"""


def _symbols(root: Path, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.cpp").write_text(text)
    return [s for s in analyze_cpp(root).symbols if s.kind != "file"]


def _ties(symbols: list[Symbol]) -> list[tuple[str, str]]:
    counts = Counter(s.stable_id for s in symbols)
    return sorted({(s.kind, s.name) for s in symbols if counts[s.stable_id] > 1})


def test_overloads_differing_only_in_marks_do_not_tie(tmp_path: Path) -> None:
    symbols = _symbols(tmp_path, OVERLOADS)
    names = Counter(s.name for s in symbols if s.kind == "function")
    assert names == {"f": 2, "g": 2, "k": 2, "m": 2, "n": 2, "p": 2}  # reach
    assert None not in {s.stable_id for s in symbols}
    assert _ties(symbols) == []


def test_the_type_a_reader_takes_as_a_name_is_unchanged(tmp_path: Path) -> None:
    """``meta["parameters"][i]["type"]`` stays the type NAME (taint contracts,
    ``parameter_type`` patterns read it); the marks sit beside it."""
    (sym,) = [s for s in _symbols(tmp_path, "void f(const char* s, int& n) {}\n")
              if s.kind == "function"]
    assert [(e["type"], e.get("declarator")) for e in sym.meta["parameters"]] == [
        ("const char", "*"), ("int", "&"),
    ]


def test_a_prototype_spelling_and_layout_do_not_move_the_id(tmp_path: Path) -> None:
    """An unnamed parameter hashes like the named one, and the spacing of the
    marks is layout."""
    spellings = [
        "void f(int*, int (*)(int)) {}\n",
        "void f(int* a, int (*cb)(int x)) {}\n",
        "void f(int *a, int ( * cb ) ( int y )) {}\n",
        "void f(int * a, int (*cb)(int)) {}\n",
    ]
    ids = set()
    for index, text in enumerate(spellings):
        (sym,) = [s for s in _symbols(tmp_path / str(index), text) if s.kind == "function"]
        ids.add(sym.stable_id)
    assert len(ids) == 1


def test_a_plain_overload_keeps_its_id_when_a_marked_one_is_deleted(tmp_path: Path) -> None:
    """The control: blanking the earlier overload moves no survivor."""
    both = {(s.kind, s.name, s.span.start_line): s.stable_id
            for s in _symbols(tmp_path / "with", OVERLOADS)}
    lines = OVERLOADS.splitlines(keepends=True)
    lines[1] = "\n"  # ``void f(int* a)`` and its local struct
    after = _symbols(tmp_path / "without", "".join(lines))
    assert len(after) < len(both)
    for s in after:
        assert both[(s.kind, s.name, s.span.start_line)] == s.stable_id, (s.kind, s.name)
