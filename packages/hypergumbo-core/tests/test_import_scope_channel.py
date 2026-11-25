# SPDX-License-Identifier: AGPL-3.0-or-later
"""The channel that carries a file's import table from its analyzer to the linkers (ADR-0064, piece b).

An analyzer knows what each imported name means in a file; a linker did not,
because the per-file tables were dropped when the analyzer returned and the
``imports`` edges carry no local name. ``AnalysisResult.import_scopes`` is the
table an analyzer hands over, ``collect_import_scopes`` gathers them under
repo-relative paths, and every linker reads them from
``LinkerContext.import_scopes``. These tests pin the channel itself; what a
linker does with a scope is tested where that linker is.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/all_analyzers.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/linkers/registry.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hypergumbo_core.analyze.all_analyzers import collect_import_scopes
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.import_scope import ImportScope
from hypergumbo_core.linkers.registry import (
    _LINKER_REGISTRY,
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    RegisteredLinker,
    run_all_linkers,
)


def _scope(module: str, name: str) -> ImportScope:
    scope = ImportScope()
    scope.add_named(module, name)
    return scope


def test_a_result_hands_over_no_table_by_default() -> None:
    assert AnalysisResult().import_scopes == {}
    assert LinkerContext(repo_root=Path("/tmp")).import_scopes == {}


def test_scopes_are_gathered_under_repo_relative_paths(tmp_path: Path) -> None:
    relative, absolute = _scope("util", "helper"), _scope("lib", "make")
    result = AnalysisResult(import_scopes={
        "src/a.cs": relative,
        str(tmp_path / "src" / "b.cs"): absolute,
    })
    gathered: dict[str, ImportScope] = {}
    collect_import_scopes(result, gathered, tmp_path)
    assert gathered == {"src/a.cs": relative, "src/b.cs": absolute}


def test_the_first_table_for_a_file_is_kept(tmp_path: Path) -> None:
    """Two producers of one file (a second backend of a language) do not
    overwrite each other by arrival order: the first, in registry order, stays."""
    first, second = _scope("util", "helper"), _scope("other", "helper")
    gathered: dict[str, ImportScope] = {}
    collect_import_scopes(AnalysisResult(import_scopes={"a.rs": first}), gathered, tmp_path)
    collect_import_scopes(AnalysisResult(import_scopes={"a.rs": second}), gathered, tmp_path)
    assert gathered["a.rs"] is first


def test_a_skipped_result_hands_over_nothing(tmp_path: Path) -> None:
    gathered: dict[str, ImportScope] = {}
    skipped = AnalysisResult(skipped=True, import_scopes={"a.rs": _scope("util", "helper")})
    collect_import_scopes(skipped, gathered, tmp_path)
    assert gathered == {}


def test_every_linker_reads_the_same_tables(tmp_path: Path) -> None:
    """Alone in its priority group or one of several run in threads, a linker
    is handed the tables the caller passed."""
    scopes = {"a.cs": _scope("util", "helper")}
    seen: dict[str, object] = {}

    def linker(name: str, priority: int) -> RegisteredLinker:
        def run(ctx: LinkerContext) -> LinkerResult:
            seen[name] = ctx.import_scopes
            return LinkerResult()
        return RegisteredLinker(name=name, func=run, priority=priority,
                                activation=LinkerActivation(always=True))

    linkers = {n: linker(n, p) for n, p in (("alone", 10), ("one_of_two", 20), ("two_of_two", 20))}
    with patch.dict(_LINKER_REGISTRY, linkers, clear=True):
        run_all_linkers(LinkerContext(repo_root=tmp_path, import_scopes=scopes))
    assert set(seen) == set(linkers)
    assert all(tables is scopes for tables in seen.values())
