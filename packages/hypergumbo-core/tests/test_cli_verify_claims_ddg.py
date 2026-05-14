# SPDX-License-Identifier: AGPL-3.0-or-later
"""Direct (non-subprocess) tests for the cmd_verify_claims DDG helpers.

Subprocess-shaped tests of ``hypergumbo verify-claims`` don't contribute
to coverage (per AGENTS.md). These tests call
``_build_python_ddg_for_verify_claims`` and ``_collect_python_function_ddg``
in-process to exercise the AST → CFG → def_use → DDG pipeline that was
added in Phase 3.
"""
from __future__ import annotations

from pathlib import Path


def _write_python_module(tmp_path: Path) -> Path:
    """Drop a small Python file with a function whose body contains a
    real assignment so DDG produces non-empty edges."""
    repo = tmp_path / "fake-repo"
    pkg = repo / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "mod.py").write_text(
        "def f(x):\n"
        "    y = x + 1\n"
        "    z = y * 2\n"
        "    return z\n",
        encoding="utf-8",
    )
    return repo


def test_build_python_ddg_returns_edges(tmp_path: Path) -> None:
    """End-to-end: tree-sitter parses the file, CFG builds, def/use
    populates, solve_reaching_defs produces ddg_edges, ddg_symbols
    contains the function's symbol id."""
    from hypergumbo_core.cli import _build_python_ddg_for_verify_claims
    from hypergumbo_core.cfg import (
        get_def_use_extractor, register_def_use_extractor,
    )
    # Ensure Python extractor is registered (sibling tests may have cleared it).
    if get_def_use_extractor("python") is None:
        from hypergumbo_lang_mainstream.py_def_use import (
            PythonDefUseExtractor,
        )
        register_def_use_extractor("python")(PythonDefUseExtractor)

    repo = _write_python_module(tmp_path)
    edges, symbols = _build_python_ddg_for_verify_claims(repo)
    assert len(edges) > 0
    assert any("mod.py" in sym for sym in symbols)


def test_build_python_ddg_skips_excluded_dirs(tmp_path: Path) -> None:
    """Files under .git / .venv / __pycache__ etc. are skipped."""
    from hypergumbo_core.cli import _build_python_ddg_for_verify_claims

    repo = tmp_path / "fake-repo"
    skip_dir = repo / ".venv" / "site-packages"
    skip_dir.mkdir(parents=True)
    (skip_dir / "garbage.py").write_text(
        "def g(x):\n    y = x\n    return y\n", encoding="utf-8",
    )
    # No files outside the skip dir.
    edges, symbols = _build_python_ddg_for_verify_claims(repo)
    assert edges == []
    assert symbols == set()


def test_build_python_ddg_handles_empty_repo(tmp_path: Path) -> None:
    """Empty repo → empty ddg output, no exception."""
    from hypergumbo_core.cli import _build_python_ddg_for_verify_claims

    repo = tmp_path / "fake-repo"
    repo.mkdir()
    edges, symbols = _build_python_ddg_for_verify_claims(repo)
    assert edges == []
    assert symbols == set()


def test_build_python_ddg_handles_nested_function(tmp_path: Path) -> None:
    """Nested function_definition nodes are captured by the tree walk."""
    from hypergumbo_core.cli import _build_python_ddg_for_verify_claims
    from hypergumbo_core.cfg import (
        get_def_use_extractor, register_def_use_extractor,
    )
    if get_def_use_extractor("python") is None:
        from hypergumbo_lang_mainstream.py_def_use import (
            PythonDefUseExtractor,
        )
        register_def_use_extractor("python")(PythonDefUseExtractor)

    repo = tmp_path / "fake-repo"
    repo.mkdir()
    (repo / "nested.py").write_text(
        "def outer():\n"
        "    def inner(x):\n"
        "        y = x + 1\n"
        "        return y\n"
        "    return inner\n",
        encoding="utf-8",
    )
    edges, symbols = _build_python_ddg_for_verify_claims(repo)
    # The inner function's body has an assignment, so DDG should pick it up.
    assert len(edges) > 0
    assert any("inner" in sym for sym in symbols)
