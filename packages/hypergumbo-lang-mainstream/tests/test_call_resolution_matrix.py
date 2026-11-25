# SPDX-License-Identifier: AGPL-3.0-or-later
"""One call-resolution program in every mainstream language, and what each analyzer makes of it (WI-tazuk).

The same three ideas (what type a receiver has, which method a call reaches,
which declaration an import names) are worked out separately in each analyzer.
``_call_matrix_fixture.py`` writes one small program in 17 languages, with a
decoy for every callee, and this module pins the result: one row per language,
one cell per call shape, in each of two layouts. Read that module's docstring
for the program, the layouts, the arms and how a destination is judged.

FOUR THINGS ARE PINNED. ``MATRIX`` (every class in one file) and
``MATRIX_SPLIT`` (one class per file, WI-bobik) hold the cells. ``MARKS`` and
``MARKS_SPLIT`` hold what scoped the choice behind each cell
(``meta["resolved_by"]``), so a rollout PR shows not only that a cell stayed
``right`` but that it is now right for a reason. ``THIRD`` lists the cells a
third same-named candidate changes; every one of them goes to ``unresolved``
or ``no_edge``, none to a new bind.

WHAT THE TABLES ARE. Each is the behaviour MEASURED on the day a row was
last edited; it is not a statement that the behaviour is right. Three kinds of
cell are not right, and they are kept apart on purpose:

* a GUESS: ``order:...`` (the destination changes when two declarations swap
  places) or ``picked`` (a call nothing in the repository can answer is bound
  anyway). The analyzer bound a symbol it had no reason to prefer. Each one is
  a defect and names its open tracker item in ``GUESSES``.
* a GAP: ``unresolved`` or ``no_edge`` where the callee is in the repository.
  Nothing false is said; a call is missing. These are not listed one by one:
  the columns they fill (the chained call, the inherited method) are the
  evidence for or against one shared resolution pass, a decision this fixture
  informs and does not take.
* ``unresolved`` in an ``amb`` column, and in ``scope_ext``, is the RIGHT answer.

WHAT THE SECOND LAYOUT SHOWED (2026-11-25). Go, Rust, Swift, C++, Java,
Kotlin, Scala, JavaScript, TypeScript, Python, Ruby, PHP, Lua and Perl answer
the same in both layouts. Groovy and Objective-C do not: every bind they
made through a same-file name table (marked ``same_file``) is ``unresolved``
across files, and Objective-C resolves no method call across files at all.
Their one-file guesses are real, and a tree laid out one class per file does
not reach them. C# did the same until it handed its calls to the shared pass
(ADR-0064): it binds only its own file's declarations on C#'s own evidence and
reports the rest with facts, and its rows are now one row in both layouts.

HOW TO CHANGE IT. A fix moves a cell: the test for that language and layout
fails and shows the measured row. Edit the row, its marks, and when a guess is
gone from BOTH layouts delete its ``GUESSES`` entry
(``test_every_guess_names_its_item`` fails until you do). A cell may never
move TO a guess without a tracker item of its own.

The reach assertion comes first: a cell is only read after the program's
declarations and callers are found in the map, so a missing symbol cannot
pass for "unresolved".
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/cpp.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/csharp.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/go.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/groovy.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/java.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/kotlin.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/lua.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/objc.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/perl.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/php.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/rust.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/scala.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/swift.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/symbol_resolution.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/linkers/inherited_calls.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/linkers/receiver_type_dispatch.py
from __future__ import annotations

from pathlib import Path

import pytest

import _call_matrix_fixture as fx

SHAPES = [shape for shape, _, _ in fx.SHAPES]

#: THE ONE-FILE LAYOUT. language -> cells, in the order of ``SHAPES``:
#: var, chain, ctor, inherited, amb_ext, amb_param, import, scope_own, scope_ext, qual.
MATRIX: dict[str, tuple[str, ...]] = {
    "cpp": ("unresolved", "unresolved", "right", "right", "unresolved", "n/a", "order:right/decoy", "right",
            "unresolved", "order:right/decoy"),
    "csharp": ("right", "right", "right", "right", "unresolved", "n/a", "right", "right", "unresolved", "right"),
    "go": ("right", "unresolved", "right", "unresolved", "unresolved", "n/a", "right", "right", "unresolved",
           "n/a"),
    "groovy": ("order:decoy/right", "unresolved", "right", "order:decoy/right", "picked", "picked",
               "order:decoy/right", "right", "picked", "unresolved"),
    "java": ("right", "right", "right", "right", "unresolved", "n/a", "order:unresolved/right", "right",
             "unresolved", "order:unresolved/right"),
    "javascript": ("unresolved", "unresolved", "right", "right", "unresolved", "unresolved", "right", "right",
                   "unresolved", "right"),
    "kotlin": ("right", "unresolved", "right", "right", "unresolved", "n/a", "right", "right",
               "unresolved", "unresolved"),
    "lua": ("unresolved", "unresolved", "right", "unresolved", "unresolved", "unresolved", "right", "right",
            "unresolved", "n/a"),
    "objc": ("order:decoy/right", "order:decoy/right", "order:decoy/right", "order:decoy/right", "picked",
             "picked", "no_edge", "order:decoy/right", "picked", "n/a"),
    "perl": ("unresolved", "unresolved", "right", "unresolved", "unresolved", "unresolved", "unresolved",
             "right", "unresolved", "right"),
    "php": ("right", "right", "right", "right", "unresolved", "unresolved", "right", "right", "unresolved",
            "right"),
    "python": ("right", "right", "right", "right", "unresolved", "unresolved", "right", "right", "unresolved",
               "right"),
    "ruby": ("unresolved", "unresolved", "right", "no_edge", "no_edge", "unresolved", "order:decoy/right",
             "right", "no_edge", "n/a"),
    "rust": ("right", "right", "right", "right", "unresolved", "n/a", "right", "right", "unresolved",
             "right"),
    "scala": ("right", "right", "right", "right", "unresolved", "n/a", "unresolved", "right", "unresolved",
              "right"),
    "swift": ("right", "right", "right", "right", "unresolved", "n/a", "order:decoy/right", "right",
              "unresolved", "unresolved"),
    "typescript": ("right", "unresolved", "right", "right", "unresolved", "unresolved", "right", "right",
                   "unresolved", "right"),
}

#: ONE CLASS PER FILE, the same shapes.
MATRIX_SPLIT: dict[str, tuple[str, ...]] = {
    "cpp": ("unresolved", "unresolved", "right", "right", "unresolved", "n/a", "order:right/decoy", "right",
            "unresolved", "order:right/decoy"),
    "csharp": ("right", "right", "right", "right", "unresolved", "n/a", "right", "right", "unresolved", "right"),
    "go": ("right", "unresolved", "right", "unresolved", "unresolved", "n/a", "right", "right", "unresolved",
           "n/a"),
    "groovy": ("unresolved", "unresolved", "right", "unresolved", "unresolved", "unresolved",
               "order:decoy/right", "right", "unresolved", "unresolved"),
    "java": ("right", "right", "right", "right", "unresolved", "n/a", "order:unresolved/right", "right",
             "unresolved", "order:unresolved/right"),
    "javascript": ("unresolved", "unresolved", "right", "right", "unresolved", "unresolved", "right", "right",
                   "unresolved", "right"),
    "kotlin": ("right", "unresolved", "right", "right", "unresolved", "n/a", "right", "right",
               "unresolved", "unresolved"),
    "lua": ("unresolved", "unresolved", "right", "unresolved", "unresolved", "unresolved", "right", "right",
            "unresolved", "n/a"),
    "objc": ("unresolved", "unresolved", "unresolved", "unresolved", "unresolved", "unresolved", "no_edge",
             "unresolved", "unresolved", "n/a"),
    "perl": ("unresolved", "unresolved", "right", "unresolved", "unresolved", "unresolved", "unresolved",
             "right", "unresolved", "right"),
    "php": ("right", "right", "right", "right", "unresolved", "unresolved", "right", "right", "unresolved",
            "right"),
    "python": ("right", "right", "right", "right", "unresolved", "unresolved", "right", "right", "unresolved",
               "right"),
    "ruby": ("unresolved", "unresolved", "right", "no_edge", "no_edge", "unresolved", "order:decoy/right",
             "right", "no_edge", "n/a"),
    "rust": ("right", "right", "right", "right", "unresolved", "n/a", "right", "right", "unresolved",
             "right"),
    "scala": ("right", "right", "right", "right", "unresolved", "n/a", "unresolved", "right", "unresolved",
              "right"),
    "swift": ("right", "right", "right", "right", "unresolved", "n/a", "order:decoy/right", "right",
              "unresolved", "unresolved"),
    "typescript": ("right", "unresolved", "right", "right", "unresolved", "unresolved", "right", "right",
                   "unresolved", "right"),
}

#: The resolution marks (``meta["resolved_by"]``) on the resolved edges behind each cell of ``MATRIX``,
#: over its four arms; ``-`` where no edge was resolved.
MARKS: dict[str, tuple[str, ...]] = {
    "cpp": ("-", "-", "receiver_type", "receiver_type", "-", "-", "name", "receiver_type", "-", "name"),
    "csharp": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "qualified",
               "receiver_type", "-", "qualified"),
    "go": ("receiver_type", "-", "receiver_type", "-", "-", "-", "import", "receiver_type", "-", "-"),
    "groovy": ("same_file", "-", "receiver_type", "same_file", "same_file", "same_file", "name",
               "receiver_type", "same_file", "-"),
    "java": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "qualified",
             "receiver_type", "-", "qualified"),
    "javascript": ("-", "-", "receiver_type", "receiver_type", "-", "-", "import", "receiver_type", "-",
                   "import"),
    "kotlin": ("receiver_type", "-", "receiver_type", "receiver_type", "-", "-", "import", "receiver_type", "-", "-"),
    "lua": ("-", "-", "receiver_type", "-", "-", "-", "import", "receiver_type", "-", "-"),
    "objc": ("same_file", "same_file", "same_file", "same_file", "same_file", "same_file", "-", "same_file",
             "same_file", "-"),
    "perl": ("-", "-", "receiver_type", "-", "-", "-", "-", "receiver_type", "-", "qualified"),
    "php": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "import",
            "receiver_type", "-", "qualified"),
    "python": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "import",
               "receiver_type", "-", "import"),
    "ruby": ("-", "-", "receiver_type", "-", "-", "-", "name", "receiver_type", "-", "-"),
    "rust": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "import",
             "receiver_type", "-", "import"),
    "scala": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "-",
              "receiver_type", "-", "qualified"),
    "swift": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "name",
              "receiver_type", "-", "-"),
    "typescript": ("receiver_type", "-", "receiver_type", "receiver_type", "-", "-", "import", "receiver_type",
                   "-", "import"),
}

#: The same for ``MATRIX_SPLIT``.
MARKS_SPLIT: dict[str, tuple[str, ...]] = {
    "cpp": ("-", "-", "receiver_type", "receiver_type", "-", "-", "name", "receiver_type", "-", "name"),
    "csharp": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "qualified",
               "receiver_type", "-", "qualified"),
    "go": ("receiver_type", "-", "receiver_type", "-", "-", "-", "import", "receiver_type", "-", "-"),
    "groovy": ("-", "-", "receiver_type", "-", "-", "-", "name", "receiver_type", "-", "-"),
    "java": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "qualified",
             "receiver_type", "-", "qualified"),
    "javascript": ("-", "-", "receiver_type", "receiver_type", "-", "-", "import", "receiver_type", "-",
                   "import"),
    "kotlin": ("receiver_type", "-", "receiver_type", "receiver_type", "-", "-", "import", "receiver_type", "-", "-"),
    "lua": ("-", "-", "receiver_type", "-", "-", "-", "import", "receiver_type", "-", "-"),
    "objc": ("-", "-", "-", "-", "-", "-", "-", "-", "-", "-"),
    "perl": ("-", "-", "receiver_type", "-", "-", "-", "-", "receiver_type", "-", "qualified"),
    "php": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "import",
            "receiver_type", "-", "qualified"),
    "python": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "import",
               "receiver_type", "-", "import"),
    "ruby": ("-", "-", "receiver_type", "-", "-", "-", "name", "receiver_type", "-", "-"),
    "rust": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "import",
             "receiver_type", "-", "import"),
    "scala": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "-",
              "receiver_type", "-", "qualified"),
    "swift": ("receiver_type", "receiver_type", "receiver_type", "receiver_type", "-", "-", "name",
              "receiver_type", "-", "-"),
    "typescript": ("receiver_type", "-", "receiver_type", "receiver_type", "-", "-", "import", "receiver_type",
                   "-", "import"),
}

#: (layout, language, shape) -> the cell measured with a THIRD same-named candidate, where it differs from
#: the two-candidate cell. Every other cell is the same with three candidates as with two.
THIRD: dict[tuple[str, str, str], str] = {
    ("one_file", "groovy", "amb_ext"): "no_edge",
    ("one_file", "groovy", "amb_param"): "no_edge",
    ("one_file", "groovy", "chain"): "no_edge",
    ("one_file", "groovy", "inherited"): "no_edge",
    ("one_file", "groovy", "scope_ext"): "no_edge",
    ("one_file", "groovy", "var"): "no_edge",
    ("one_file", "ruby", "import"): "unresolved",
    ("split", "groovy", "amb_ext"): "no_edge",
    ("split", "groovy", "amb_param"): "no_edge",
    ("split", "groovy", "chain"): "no_edge",
    ("split", "groovy", "inherited"): "no_edge",
    ("split", "groovy", "scope_ext"): "no_edge",
    ("split", "groovy", "var"): "no_edge",
    ("split", "ruby", "import"): "unresolved",
}

#: layout -> (cells, marks).
TABLES = {"one_file": (MATRIX, MARKS), "split": (MATRIX_SPLIT, MARKS_SPLIT)}

#: Every guess in either table and the open item that owns it. INV-jutog states
#: the invariant all of them break. A cell that guesses in one layout only is
#: listed all the same: the analyzer has the defect, the layout decides whether
#: this program reaches it. C# and Groovy bind ``scope_ext`` by name alone, as
#: they do ``amb_ext``, so that cell cannot show whether a variable's type
#: crosses functions there: ``test_var_types_per_function.py`` does.
GUESSES: dict[tuple[str, str], str] = {
    **{("cpp", shape): "WI-joniz" for shape in ("import", "qual")},
    **{("groovy", shape): "WI-tiluh" for shape in ("var", "inherited", "amb_ext", "amb_param", "import",
       "scope_ext")},
    **{("java", shape): "WI-mugon" for shape in ("import", "qual")},
    **{("objc", shape): "WI-mokul" for shape in ("var", "chain", "ctor", "inherited", "amb_ext", "amb_param",
       "scope_own", "scope_ext")},
    ("ruby", "import"): "WI-madab",
    ("swift", "import"): "WI-nuvov",
}

#: Declarations of the program an analyzer emits no symbol for. Objective-C: a
#: C function defined in a ``.m`` file has none (WI-rivin).
NO_SYMBOL: dict[str, set[str]] = {"objc": {"helper:right", "helper:decoy"}}

#: Languages with no untyped parameter: the ``amb_param`` caller is not written.
NO_UNTYPED_PARAMETER = {"cpp", "csharp", "go", "java", "kotlin", "rust", "scala", "swift"}

#: Languages where the fixture's ``helper`` has no qualified call distinct from
#: ``via_import``: Go and Lua already call it through the package / module
#: table, a C function (Objective-C) and a top-level Ruby ``def`` have no
#: qualifier. The ``via_qual`` caller is not written.
NO_QUALIFIED_CALL = {"go", "lua", "objc", "ruby"}

CASES = [(layout, lang) for layout in TABLES for lang in sorted(fx.LANGS)]


def _is_guess(cell: str) -> bool:
    return cell == "picked" or cell.startswith(("order:", "mixed:"))


def test_the_tables_cover_every_language_and_shape() -> None:
    for cells, marks in TABLES.values():
        assert set(cells) == set(marks) == set(fx.LANGS)
        assert all(len(row) == len(SHAPES) for row in (*cells.values(), *marks.values()))
    assert {(layout, lang) for layout, lang, _ in THIRD} <= set(CASES)
    assert {shape for _, _, shape in THIRD} <= set(SHAPES)


@pytest.mark.parametrize(("layout", "lang"), CASES)
def test_the_language_is_as_measured(layout: str, lang: str, tmp_path: Path) -> None:
    """One measurement (six surveys), four readings, reach first.

    One test and not four: each reading needs the same six surveys, and a
    result kept between tests would be state another test depends on.
    """
    got = fx.measure(tmp_path, lang, split=layout == "split")

    # Reach: the callers and both declarations of each callee have symbols. A
    # cell is only read after this, so a missing symbol cannot pass for "unresolved".
    not_written = ({"via_amb_param"} if lang in NO_UNTYPED_PARAMETER else set()) | (
        {"via_qual"} if lang in NO_QUALIFIED_CALL else set())
    assert got.absent_callers == not_written, (layout, lang)
    assert got.missing == NO_SYMBOL.get(lang, set())

    row = tuple(got.cells[shape] for shape in SHAPES)
    assert row == TABLES[layout][0][lang], (
        f"{lang}, {layout}: the measured row differs from the table. If a fix moved a cell, edit the row "
        f"(and GUESSES when a guess is gone). Measured: {dict(zip(SHAPES, row, strict=True))}"
    )

    # What scoped the choice behind each cell (``resolved_by``, audit 0030).
    marks = tuple(got.marks[shape] for shape in SHAPES)
    assert marks == TABLES[layout][1][lang], (
        f"{lang}, {layout}: measured marks: {dict(zip(SHAPES, marks, strict=True))}"
    )

    # A third same-named candidate moves the listed cells and no other.
    moved = {shape: got.third[shape] for shape in SHAPES if got.third[shape] != got.cells[shape]}
    listed = {shape: cell for (at, language, shape), cell in THIRD.items() if (at, language) == (layout, lang)}
    assert moved == listed, f"{lang}, {layout}: cells a third candidate moved: {moved}"


def test_every_guess_names_its_item() -> None:
    """The cells that bind a symbol without a reason, in either layout, and nothing else, are in GUESSES."""
    guesses = {(lang, shape) for cells, _ in TABLES.values() for lang, row in cells.items()
               for shape, cell in zip(SHAPES, row, strict=True) if _is_guess(cell)}
    assert guesses == set(GUESSES)


def test_a_third_candidate_adds_no_guess() -> None:
    """With three candidates a cell goes to ``unresolved`` or ``no_edge``, never to a new bind."""
    assert not [key for key, cell in THIRD.items() if _is_guess(cell) or "right" in cell or "decoy" in cell]


def test_no_cell_depends_on_how_a_class_is_spelled() -> None:
    """Swapping the class names alone changed no cell when the tables were measured."""
    assert not [cell for cells, _ in TABLES.values() for row in cells.values() for cell in row
                if cell.startswith("mixed:")]


def test_an_ambiguous_call_is_never_reported_right() -> None:
    """In an ``amb`` column a bound edge is ``picked``, whichever class it reaches."""
    for cells, _ in TABLES.values():
        for row in cells.values():
            for shape, cell in zip(SHAPES, row, strict=True):
                if shape in fx.AMBIGUOUS:
                    assert cell in ("unresolved", "no_edge", "picked", "n/a"), (shape, cell)


def test_a_mark_stands_only_behind_a_bound_edge() -> None:
    """A cell has a mark exactly when some arm bound a declaration of the repository."""
    for cells, marks in TABLES.values():
        for lang, row in cells.items():
            for cell, mark in zip(row, marks[lang], strict=True):
                bound = any(word in cell for word in ("right", "decoy", "picked"))
                assert (mark != "-") == bound, (lang, cell, mark)
