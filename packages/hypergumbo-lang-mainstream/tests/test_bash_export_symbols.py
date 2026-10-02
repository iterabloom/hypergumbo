# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-nasil: one ``export`` Symbol per exported variable per file.

THE MODEL. A bash ``export`` Symbol stands for a VARIABLE the file marks for
export with the ``export`` builtin, not for the statement that does it.
Exporting is an attribute of the variable (bash's ``declare -x`` sets the
``x`` attribute; a child process sees one environment entry however many
times the parent re-exports it), and the kind's own identity factory,
``make_export_stable_id``, keys on ``(language, path, name)``. So a file that
exports ``CI_TIMEOUT`` three times has ONE ``CI_TIMEOUT`` export Symbol, at
the FIRST statement that exports it (the precedent ``make.py`` sets for a
Makefile variable).

The same model answers every shape an ``export`` statement can take, because
each answers "which variables does this statement mark for export":

    export A=1 B=2     -> A, B          (every operand, not only the first)
    export X           -> X             (bare: marks an existing variable)
    export X Y=2 Z     -> X, Y, Z
    export -- Y=1      -> Y             (``--`` ends options)
    export -f foo      -> nothing       (exports a FUNCTION, not a variable)
    export -n X        -> nothing       (REMOVES the export attribute)
    export -p          -> nothing       (prints; names nothing)
    export "$V"=1      -> nothing       (name not statically known)
    export a[0]=1      -> nothing       (bash rejects it: not an identifier)

NOT covered here (WI-luzit's residual): ``declare -x`` / ``typeset -x`` /
``local -x``, which also export but are function-LOCAL inside a function, and
``readonly``, which does not export at all.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import (
    make_export_stable_id,
    populate_kind_stable_ids,
    split_within_file_stable_id_collisions,
)
from hypergumbo_lang_mainstream.bash import analyze_bash


def _exports(tmp_path: Path, body: str, name: str = "a.sh"):
    (tmp_path / name).write_text("#!/bin/bash\n" + body)
    result = analyze_bash(tmp_path)
    assert result.skipped is False  # reach: the grammar is installed
    return [s for s in result.symbols if s.kind == "export"]


def test_reexported_variable_is_one_symbol_at_its_first_export(
        tmp_path: Path) -> None:
    """The filed repro: two assignments and a bare re-export of one name."""
    exports = _exports(
        tmp_path,
        "export CI_TIMEOUT=1\n"   # line 2
        "foo(){ :; }\n"
        "export CI_TIMEOUT=2\n"   # line 4
        "export CI_TIMEOUT\n",    # line 5
    )
    assert [(s.name, s.span.start_line) for s in exports] == [
        ("CI_TIMEOUT", 2)]
    assert exports[0].id == "bash:a.sh:2-2:CI_TIMEOUT:export"


def test_every_operand_of_one_export_statement_is_a_symbol(
        tmp_path: Path) -> None:
    exports = _exports(tmp_path, "export A=1 B=2\nexport X Y=2 Z\n")
    assert [s.name for s in exports] == ["A", "B", "X", "Y", "Z"]
    # Distinct names on one line keep distinct ids (the name is in the id).
    assert len({s.id for s in exports}) == 5


def test_bare_export_marks_the_variable(tmp_path: Path) -> None:
    exports = _exports(tmp_path, "X=1\nexport X\n")
    assert [(s.name, s.span.start_line) for s in exports] == [("X", 3)]


def test_double_dash_ends_options(tmp_path: Path) -> None:
    exports = _exports(tmp_path, "export -- Y=1\n")
    assert [s.name for s in exports] == ["Y"]


def test_statements_that_export_no_variable_emit_nothing(
        tmp_path: Path) -> None:
    """-f exports functions, -n un-exports, -p prints; none is a variable."""
    exports = _exports(
        tmp_path,
        "foo(){ :; }\n"
        "export -f foo\n"
        "export -fn foo\n"
        "N=1\n"
        "export -n N\n"
        "export -n M=2\n"
        "export -p\n"
        "export\n",
    )
    assert exports == []


def test_a_dash_word_after_options_end_is_not_an_option(
        tmp_path: Path) -> None:
    """bash reads options only up to ``--`` or the first operand; a later
    ``-n`` is an invalid identifier, NOT an un-export. Verified on GNU bash
    5.2.21: ``export -- -n X`` and ``export X=1 -n`` both print "not a valid
    identifier" and leave X exported. A scan that read every ``-`` word as an
    option would drop X here."""
    exports = _exports(
        tmp_path,
        "export -- -n X\n"
        "export A=1 -n\n"
        "export -p Q=1\n",   # -p with operands still exports them
    )
    assert [s.name for s in exports] == ["X", "A", "Q"]


def test_dynamic_names_emit_nothing(tmp_path: Path) -> None:
    exports = _exports(
        tmp_path,
        'export "$V"=1\n'
        "export ${P}_X=1\n"
        "export $(cat .env | xargs)\n"
        "export 'Q'=1\n"
        "export a[0]=1\n",   # bash: not a valid identifier; nothing exported
    )
    assert exports == []


def test_dedup_is_per_file_not_per_repo(tmp_path: Path) -> None:
    (tmp_path / "b.sh").write_text("#!/bin/bash\nexport SHARED=1\n")
    exports = _exports(tmp_path, "export SHARED=2\nexport SHARED=3\n")
    assert sorted((s.path, s.name) for s in exports) == [
        ("a.sh", "SHARED"), ("b.sh", "SHARED")]


def test_export_inside_a_function_dedups_with_the_top_level_one(
        tmp_path: Path) -> None:
    """``export`` in a function sets the GLOBAL variable's attribute (bash is
    dynamically scoped), so it is the same exported variable."""
    exports = _exports(
        tmp_path,
        "setup() {\n"
        "  export MODE=ci\n"      # line 3
        "}\n"
        "export MODE=local\n",
    )
    assert [(s.name, s.span.start_line) for s in exports] == [("MODE", 3)]


def test_identity_factory_has_nothing_left_to_disambiguate(
        tmp_path: Path) -> None:
    """The kind's stable_id is ``(language, path, name)``. Before WI-nasil a
    re-export collided on it and leaned on the ``:occ:<n>`` re-hash; now each
    export keeps the factory's own value and the pass re-mints nothing."""
    exports = _exports(
        tmp_path,
        "export A=1\nexport A=2\nexport B=1 A=3\nexport B\n",
    )
    assert [s.name for s in exports] == ["A", "B"]
    populate_kind_stable_ids(exports)
    assert split_within_file_stable_id_collisions(exports) == 0
    assert [s.stable_id for s in exports] == [
        make_export_stable_id("bash", "a.sh", "A"),
        make_export_stable_id("bash", "a.sh", "B"),
    ]
