# SPDX-License-Identifier: AGPL-3.0-or-later
"""One call-resolution program in every mainstream language, and what each analyzer makes of it (WI-tazuk).

The same three ideas (what type a receiver has, which method a call reaches,
which declaration an import names) are worked out separately in each analyzer.
``_call_matrix_fixture.py`` writes one small program in 17 languages, with a
decoy for every callee, and this module pins the result: one row per language,
one cell per call shape. Read that module's docstring for the program, the
four arms and how a destination is judged.

WHAT THE TABLE IS. ``MATRIX`` is the behaviour MEASURED on the day a row was
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

HOW TO CHANGE IT. A fix moves a cell: the test for that language fails and
shows the measured row. Edit the row, and when a guess is gone delete its
``GUESSES`` entry (``test_every_guess_names_its_item`` fails until you do).
A cell may never move TO a guess without a tracker item of its own.

The reach assertion comes first in every test: a cell is only read after the
program's declarations and callers are found in the map, so a missing symbol
cannot pass for "unresolved".
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

#: language -> cells, in the order of ``SHAPES``:
#: var, chain, ctor, inherited, amb_ext, amb_param, import, scope_own, scope_ext.
MATRIX: dict[str, tuple[str, ...]] = {
    "cpp": ("unresolved", "unresolved", "right", "right", "unresolved", "n/a", "order:right/decoy", "right",
            "unresolved"),
    "csharp": ("right", "unresolved", "right", "order:decoy/right", "picked", "n/a", "order:decoy/right",
               "right", "picked"),
    "go": ("right", "unresolved", "right", "unresolved", "unresolved", "n/a", "right", "right", "unresolved"),
    "groovy": ("order:decoy/right", "unresolved", "right", "order:decoy/right", "picked", "picked",
               "order:decoy/right", "right", "picked"),
    "java": ("right", "right", "right", "right", "unresolved", "n/a", "order:unresolved/right", "right",
             "unresolved"),
    "javascript": ("unresolved", "unresolved", "right", "unresolved", "unresolved", "unresolved",
                   "order:decoy/right", "right", "picked"),
    "kotlin": ("right", "unresolved", "right", "unresolved", "unresolved", "n/a", "right", "right",
               "order:unresolved/picked"),
    "lua": ("unresolved", "unresolved", "right", "unresolved", "unresolved", "unresolved", "right",
            "order:right/decoy", "picked"),
    "objc": ("order:decoy/right", "order:decoy/right", "order:decoy/right", "order:decoy/right", "picked",
             "picked", "no_edge", "order:decoy/right", "picked"),
    "perl": ("unresolved", "unresolved", "right", "unresolved", "unresolved", "unresolved", "unresolved",
             "right", "unresolved"),
    "php": ("unresolved", "unresolved", "right+unresolved", "unresolved", "unresolved", "unresolved",
            "order:decoy/right", "right+unresolved", "unresolved"),
    "python": ("right", "right", "right", "right", "unresolved", "unresolved", "right", "right",
               "unresolved"),
    "ruby": ("unresolved", "unresolved", "right", "no_edge", "no_edge", "no_edge", "order:decoy/right",
             "order:right/decoy", "picked"),
    "rust": ("right", "right", "right", "unresolved", "unresolved", "n/a", "right", "order:right/decoy",
             "picked"),
    "scala": ("right", "right", "right", "right", "unresolved", "n/a", "unresolved", "right", "picked"),
    "swift": ("right", "right", "right", "right", "unresolved", "n/a", "order:decoy/right", "right",
              "unresolved"),
    "typescript": ("right", "unresolved", "right", "unresolved", "unresolved", "unresolved", "right", "right",
                   "picked"),
}

#: Every guess in ``MATRIX`` and the open item that owns it. INV-jutog states
#: the invariant all of them break. C# and Groovy bind ``scope_ext`` by name
#: alone, as they do ``amb_ext``, so that cell cannot show whether a variable's
#: type crosses functions there: ``test_var_types_per_function.py`` does.
GUESSES: dict[tuple[str, str], str] = {
    ("cpp", "import"): "WI-joniz",
    **{("csharp", shape): "WI-dovoz" for shape in ("inherited", "amb_ext", "import", "scope_ext")},
    **{("groovy", shape): "WI-tiluh" for shape in ("var", "inherited", "amb_ext", "amb_param", "import",
       "scope_ext")},
    ("java", "import"): "WI-mugon",
    ("javascript", "import"): "WI-lizis",
    ("javascript", "scope_ext"): "INV-zabud",
    ("kotlin", "scope_ext"): "INV-zabud",
    **{("lua", shape): "INV-zabud" for shape in ("scope_own", "scope_ext")},
    **{("objc", shape): "WI-mokul" for shape in ("var", "chain", "ctor", "inherited", "amb_ext", "amb_param",
       "scope_own", "scope_ext")},
    ("php", "import"): "WI-jafiz",
    ("ruby", "import"): "WI-madab",
    **{("ruby", shape): "INV-zabud" for shape in ("scope_own", "scope_ext")},
    **{("rust", shape): "INV-zabud" for shape in ("scope_own", "scope_ext")},
    ("scala", "scope_ext"): "INV-zabud",
    ("swift", "import"): "WI-nuvov",
    ("typescript", "scope_ext"): "INV-zabud",
}

#: Declarations of the program an analyzer emits no symbol for. Objective-C: a
#: C function defined in a ``.m`` file has none (WI-rivin).
NO_SYMBOL: dict[str, set[str]] = {"objc": {"helper:right", "helper:decoy"}}

#: Languages with no untyped parameter: the ``amb_param`` caller is not written.
NO_UNTYPED_PARAMETER = {"cpp", "csharp", "go", "java", "kotlin", "rust", "scala", "swift"}


def _is_guess(cell: str) -> bool:
    return cell == "picked" or cell.startswith(("order:", "mixed:"))


def test_the_table_covers_every_language_and_shape() -> None:
    assert set(MATRIX) == set(fx.LANGS)
    assert all(len(row) == len(SHAPES) for row in MATRIX.values())


@pytest.mark.parametrize("lang", sorted(fx.LANGS))
def test_every_arm_finds_the_program(lang: str, tmp_path: Path) -> None:
    """Reach: the callers and both declarations of each callee have symbols."""
    for decoy_last, swap in fx.ARMS:
        directory = fx.write_arm(tmp_path, lang, decoy_last, swap)
        behavior_map = fx.survey(directory)
        for shape, caller, _ in fx.SHAPES:
            written = not (shape == "amb_param" and lang in NO_UNTYPED_PARAMETER)
            assert bool(fx.callers(behavior_map, caller)) == written, (lang, caller)
        assert fx.missing_declarations(behavior_map, directory) == NO_SYMBOL.get(lang, set())


@pytest.mark.parametrize("lang", sorted(fx.LANGS))
def test_the_row_is_as_measured(lang: str, tmp_path: Path) -> None:
    cells, _, _ = fx.measure(tmp_path, lang)
    measured = tuple(cells[shape] for shape in SHAPES)
    assert measured == MATRIX[lang], (
        f"{lang}: the measured row differs from MATRIX. If a fix moved a cell, edit the row "
        f"(and GUESSES when a guess is gone). Measured: {dict(zip(SHAPES, measured, strict=True))}"
    )


def test_every_guess_names_its_item() -> None:
    """The cells that bind a symbol without a reason, and nothing else, are in GUESSES."""
    guesses = {(lang, shape) for lang, row in MATRIX.items()
               for shape, cell in zip(SHAPES, row, strict=True) if _is_guess(cell)}
    assert guesses == set(GUESSES)


def test_no_cell_depends_on_how_a_class_is_spelled() -> None:
    """Swapping the class names alone changed no cell when the table was measured."""
    assert not [cell for row in MATRIX.values() for cell in row if cell.startswith("mixed:")]


def test_an_ambiguous_call_is_never_reported_right() -> None:
    """In an ``amb`` column a bound edge is ``picked``, whichever class it reaches."""
    for row in MATRIX.values():
        for shape, cell in zip(SHAPES, row, strict=True):
            if shape in fx.AMBIGUOUS:
                assert cell in ("unresolved", "no_edge", "picked", "n/a"), (shape, cell)
