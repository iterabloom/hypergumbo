# SPDX-License-Identifier: AGPL-3.0-or-later
"""C++ definitions and SQL functions: overload ids from declared parameters (WI-tusad, scheme bump).

Both producers minted ``compute_stable_id`` on the definition node, which
looks for a parameter list as a direct child and finds none (C++ keeps it
under the declarator; tree-sitter-sql under ``function_arguments``), so every
overload hashed ``param_count 0``: ``run()`` / ``run(int)`` and
``run(x integer)`` / ``run(x integer, y integer)`` tied, the per-file split
numbered them by emission order, and deleting the first renumbered the
second. Each now hashes its ``meta["parameters"]`` through
``overload_arity.node_declared_arity_stable_id``; a callable with none keeps
``compute_stable_id``'s value. Changing these already-computed values is the
stable_id scheme bump (ADR-0035 §6).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.overload_arity import PARAMETERS_KEY, parameter_entry
from hypergumbo_lang_mainstream.cpp import analyze_cpp
from hypergumbo_lang_mainstream.sql import analyze_sql_files


def _symbols(analyze, root: Path, name: str, text: str) -> list:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    return list(analyze(root).symbols)


def _assert_deletion_moves_nothing(analyze, tmp_path, name, text, deleted, kinds, survivors):
    edited = text.replace(deleted, "\n" * deleted.count("\n"))
    before = {s.span.start_line: s for s in _symbols(analyze, tmp_path / "a", name, text)
              if s.kind in kinds}
    after = [s for s in _symbols(analyze, tmp_path / "b", name, edited) if s.kind in kinds]
    assert len(after) == survivors < len(before)
    for s in after:
        assert s.stable_id is not None
        assert s.stable_id == before[s.span.start_line].stable_id, s.name


_CPP = (
    "int run() { return 1; }\n"
    "int run(int x) { return x; }\n"
    "int run(float x) { return 2; }\n"
    "struct Box { int size() { return 0; } };\n"
)


def test_cpp_overloads_get_distinct_ids(tmp_path: Path) -> None:
    runs = [s for s in _symbols(analyze_cpp, tmp_path, "a.cpp", _CPP) if s.name == "run"]
    assert [s.meta[PARAMETERS_KEY] for s in runs] == [
        [], [parameter_entry("x", "int")], [parameter_entry("x", "float")],
    ]
    assert len({s.stable_id for s in runs}) == 3


def test_cpp_zero_parameter_ids_are_unchanged(tmp_path: Path) -> None:
    """``run()`` and ``Box::size()`` declare no parameter: compute_stable_id's
    value, so the bump moves only ids of callables with parameters."""
    from hypergumbo_core.analyze.base import assemble_stable_id, make_file_stable_id

    syms = _symbols(analyze_cpp, tmp_path, "a.cpp", _CPP)
    anchor = make_file_stable_id("cpp", "a.cpp")
    for name, kind in (("run", "function"), ("Box::size", "method")):
        sym = next(s for s in syms if s.name == name)
        assert sym.stable_id == assemble_stable_id(
            kind, 0, "False,False,False", "", anchor, name, "",
        )


def test_cpp_deleting_an_earlier_overload_moves_nothing(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_cpp, tmp_path, "a.cpp", _CPP, "int run() { return 1; }\n",
        {"function", "method"}, survivors=3,
    )


_SQL = (
    "CREATE FUNCTION run(x integer) RETURNS integer AS $$ SELECT x $$ LANGUAGE sql;\n"
    "CREATE FUNCTION run(x integer, y text DEFAULT 'a') RETURNS integer AS $$ SELECT x $$ "
    "LANGUAGE sql;\n"
    "CREATE FUNCTION run(IN mytype, VARIADIC z int[]) RETURNS integer AS $$ SELECT 1 $$ "
    "LANGUAGE sql;\n"
)


def test_sql_overloads_get_distinct_typed_ids(tmp_path: Path) -> None:
    runs = [s for s in _symbols(analyze_sql_files, tmp_path, "a.sql", _SQL) if s.kind == "function"]
    assert [s.meta[PARAMETERS_KEY] for s in runs] == [
        [parameter_entry("x", "integer")],
        [parameter_entry("x", "integer"), parameter_entry("y", "text", default=True)],
        [parameter_entry("", "mytype"), parameter_entry("z", "int []", variadic=True)],
    ]
    assert len({s.stable_id for s in runs}) == 3


def test_sql_deleting_an_earlier_overload_moves_nothing(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_sql_files, tmp_path, "a.sql", _SQL, _SQL.splitlines(keepends=True)[0],
        {"function"}, survivors=2,
    )
