# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nim, Ada and Pascal overloads: ids from declared parameters (WI-tusad, scheme bump).

Each producer minted ``compute_stable_id`` on a node whose grammar has no
parameter-list child where that method looks, so every overload hashed
``param_count 0``: Nim ``run(x: int)`` / ``run(x: string)``, Ada ``Run`` /
``Run (X : Integer)``, Pascal ``Run`` / ``Run(X: Integer)`` tied, the
per-file split numbered them by emission order, and deleting the first
renumbered the second. Each now hashes ``meta["parameters"]`` (types
included) through ``overload_arity.node_declared_arity_stable_id``.
Changing these already-computed values is the stable_id scheme bump
(ADR-0035 §6).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.overload_arity import PARAMETERS_KEY, parameter_entry
from hypergumbo_lang_extended1.ada import analyze_ada
from hypergumbo_lang_extended1.nim import analyze_nim
from hypergumbo_lang_extended1.pascal import analyze_pascal


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


_NIM = (
    "proc run(x: int): int = x\n"
    "proc run(x: string): int = len(x)\n"
    "proc helper(): int = 2\n"
)


def test_nim_overloads_get_distinct_typed_ids(tmp_path: Path) -> None:
    runs = [s for s in _symbols(analyze_nim, tmp_path, "a.nim", _NIM) if s.name == "run"]
    assert len(runs) == 2 and len({s.stable_id for s in runs}) == 2


def test_nim_deleting_an_earlier_overload_moves_nothing(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_nim, tmp_path, "a.nim", _NIM, "proc run(x: int): int = x\n",
        {"function"}, survivors=2,
    )


_ADA = (
    "package body Demo is\n"
    "   function Run return Integer is begin return 1; end Run;\n"
    "   function Run (X : Integer; Y, Z : in out Float := 1.0) return Integer is"
    " begin return X; end Run;\n"
    "   procedure Helper is begin null; end Helper;\n"
    "end Demo;\n"
)


def test_ada_overloads_get_distinct_typed_ids(tmp_path: Path) -> None:
    syms = _symbols(analyze_ada, tmp_path, "demo.adb", _ADA)
    runs = [s for s in syms if s.name == "Run"]
    assert [s.meta[PARAMETERS_KEY] for s in runs] == [
        [],
        [parameter_entry("X", "Integer"), parameter_entry("Y", "Float", default=True),
         parameter_entry("Z", "Float", default=True)],
    ]
    assert len({s.stable_id for s in runs}) == 2
    (helper,) = [s for s in syms if s.name == "Helper"]
    assert helper.meta == {PARAMETERS_KEY: []}


def test_ada_deleting_an_earlier_overload_moves_nothing(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_ada, tmp_path, "demo.adb", _ADA, _ADA.splitlines(keepends=True)[1],
        {"function", "procedure"}, survivors=2,
    )


_PASCAL = (
    "unit Demo;\ninterface\nimplementation\n"
    "function Run: Integer; overload;\nbegin\n  Result := 1;\nend;\n"
    "function Run(X, Y: Integer; var Z): Integer; overload;\nbegin\n  Result := X;\nend;\n"
    "procedure Helper;\nbegin\nend;\nend.\n"
)


def test_pascal_overloads_get_distinct_typed_ids(tmp_path: Path) -> None:
    runs = [s for s in _symbols(analyze_pascal, tmp_path, "demo.pas", _PASCAL)
            if s.name == "Run"]
    assert [s.meta[PARAMETERS_KEY] for s in runs] == [
        [],
        [parameter_entry("X", "Integer"), parameter_entry("Y", "Integer"),
         parameter_entry("Z", "var")],
    ]
    assert len({s.stable_id for s in runs}) == 2


def test_pascal_deleting_an_earlier_overload_moves_nothing(tmp_path: Path) -> None:
    deleted = "function Run: Integer; overload;\nbegin\n  Result := 1;\nend;\n"
    _assert_deletion_moves_nothing(
        analyze_pascal, tmp_path, "demo.pas", _PASCAL, deleted,
        {"function"}, survivors=2,
    )
