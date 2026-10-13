# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bash nested functions and Kotlin top-level ``val``s: ids fixed in the
WI-tusad stable_id scheme bump (ADR-0035 §6).

Bash: a function defined inside another hashed only its own name, so ``run``
nested in ``outer_a`` and ``run`` nested in ``outer_b`` tied, the per-file split
pass numbered them by emission order, and deleting ``outer_a`` renumbered
``outer_b``'s ``run``. The enclosing functions now enter the qualified-name
slot; a top-level function keeps its value.

Kotlin: a top-level ``val`` hashed the ABSOLUTE file path, so the same file in
two checkouts had two identities. It now hashes the repo-relative path.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import (
    ArityFlags, assemble_stable_id, make_file_stable_id, make_variable_stable_id,
)
from hypergumbo_lang_mainstream.bash import analyze_bash
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin


def _symbols(analyze, root: Path, rel: str, text: str) -> list:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return [s for s in analyze(root).symbols if s.kind != "file"]


def _position(sym) -> tuple:
    return (sym.kind, sym.name, sym.span.start_line, sym.span.start_col)


def _assert_deletion_moves_nothing(analyze, tmp_path, rel, text, deleted) -> int:
    """Delete ``deleted`` (blanked, so lines keep their numbers) and check every
    surviving symbol, matched by its own position, keeps a non-null id."""
    edited = text.replace(deleted, "\n" * deleted.count("\n"))
    before: dict[tuple, list] = {}
    for s in _symbols(analyze, tmp_path / "with", rel, text):
        before.setdefault(_position(s), []).append(s.stable_id)
    after = _symbols(analyze, tmp_path / "without", rel, edited)
    for s in after:
        assert s.stable_id is not None, s.name
        assert s.stable_id in before[_position(s)], (s.name, s.span.start_line)
    return len(after)


_BASH = (
    "#!/bin/bash\n"
    "outer_a() {\n  run() { echo 1; }\n}\n"
    "outer_b() {\n  run() { echo 2; }\n}\n"
    "helper() { echo 3; }\n"
)


def test_bash_nested_same_name_functions_get_distinct_ids(tmp_path: Path) -> None:
    runs = [s for s in _symbols(analyze_bash, tmp_path, "bin/x.sh", _BASH) if s.name == "run"]
    assert len(runs) == 2 and len({s.stable_id for s in runs}) == 2


def test_bash_deleting_an_earlier_outer_function_moves_nothing(tmp_path: Path) -> None:
    survivors = _assert_deletion_moves_nothing(
        analyze_bash, tmp_path, "bin/x.sh", _BASH, "outer_a() {\n  run() { echo 1; }\n}\n",
    )
    assert survivors == 3  # outer_b, its run, helper


def test_bash_top_level_function_id_is_unchanged(tmp_path: Path) -> None:
    """Only nested functions changed: a top-level one hashes an empty qualified
    name, exactly as before the bump."""
    helper = next(s for s in _symbols(analyze_bash, tmp_path, "bin/x.sh", _BASH)
                  if s.name == "helper")
    assert helper.stable_id == assemble_stable_id(
        "function", 0, ArityFlags(0, False, False, False).as_flags_str(), "",
        make_file_stable_id("bash", "bin/x.sh"), "helper", "",
    )


_KOTLIN = "package demo\n\nval limit: Int = 3\nfun helper(): Int = limit\n"


def test_kotlin_top_level_val_id_does_not_depend_on_the_checkout(tmp_path: Path) -> None:
    here = _symbols(analyze_kotlin, tmp_path / "a", "src/Mod.kt", _KOTLIN)
    there = _symbols(analyze_kotlin, tmp_path / "b" / "deeper", "src/Mod.kt", _KOTLIN)
    assert [(_position(s), s.stable_id) for s in here] == [
        (_position(s), s.stable_id) for s in there]
    limit = next(s for s in here if s.name == "limit")
    assert limit.kind == "variable"
    assert limit.stable_id == make_variable_stable_id("kotlin", "src/Mod.kt", "limit")
