# SPDX-License-Identifier: AGPL-3.0-or-later
"""Direct (non-subprocess) tests for the cmd_verify_claims DDG helpers.

Subprocess-shaped tests of ``hypergumbo verify-claims`` don't contribute
to coverage (per AGENTS.md). These tests call
``_build_ddg_for_verify_claims`` and the ddg_build pipeline it adapts
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
    from hypergumbo_core.cli import _build_ddg_for_verify_claims
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
    edges, symbols, hints, _stmts, _forfeits = _build_ddg_for_verify_claims(repo)
    assert len(edges) > 0
    assert any("mod.py" in sym for sym in symbols)


def test_build_python_ddg_skips_excluded_dirs(tmp_path: Path) -> None:
    """Files under .git / .venv / __pycache__ etc. are skipped."""
    from hypergumbo_core.cli import _build_ddg_for_verify_claims

    repo = tmp_path / "fake-repo"
    skip_dir = repo / ".venv" / "site-packages"
    skip_dir.mkdir(parents=True)
    (skip_dir / "garbage.py").write_text(
        "def g(x):\n    y = x\n    return y\n", encoding="utf-8",
    )
    # No files outside the skip dir.
    edges, symbols, hints, _stmts, _forfeits = _build_ddg_for_verify_claims(repo)
    assert edges == []
    assert symbols == set()


def test_build_python_ddg_handles_empty_repo(tmp_path: Path) -> None:
    """Empty repo → empty ddg output, no exception."""
    from hypergumbo_core.cli import _build_ddg_for_verify_claims

    repo = tmp_path / "fake-repo"
    repo.mkdir()
    edges, symbols, hints, _stmts, _forfeits = _build_ddg_for_verify_claims(repo)
    assert edges == []
    assert symbols == set()


def test_build_python_ddg_handles_nested_function(tmp_path: Path) -> None:
    """Nested function_definition nodes are captured by the tree walk."""
    from hypergumbo_core.cli import _build_ddg_for_verify_claims
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
    edges, symbols, hints, _stmts, _forfeits = _build_ddg_for_verify_claims(repo)
    # The inner function's body has an assignment, so DDG should pick it up.
    assert len(edges) > 0
    assert any("inner" in sym for sym in symbols)


def test_build_python_ddg_collects_receiver_hints(tmp_path: Path) -> None:
    """WI-dilih: the helper returns ``hints_by_caller`` keyed by function
    symbol_id, with ``(call_line, attr_name)`` entries naming the
    module-of-origin recovered from the DDG."""
    from hypergumbo_core.cli import _build_ddg_for_verify_claims
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
    (repo / "m.py").write_text(
        "import os\n"
        "def f():\n"
        "    x = os.environ\n"
        "    return x.get('FOO')\n",
        encoding="utf-8",
    )
    edges, symbols, hints, _stmts, _forfeits = _build_ddg_for_verify_claims(repo)
    # Exactly one function with hints (``f``). The call is at line 4
    # with attr ``get``; the receiver ``x`` was bound to ``os.environ``.
    assert len(hints) == 1
    caller_id = next(iter(hints))
    assert "m.py" in caller_id and "f:function" in caller_id
    assert hints[caller_id] == {(4, "get"): "os.environ"}


def test_forfeit_set_is_actually_populated(tmp_path: Path) -> None:
    """WI-joluk: the adapter must carry a NON-EMPTY forfeit set for real code.

    Non-vacuity floor. Everything downstream of this — the sanitizer arm's
    refusal to earn ``sanitized`` for a function the extractor did not fully
    see — is keyed on membership in this set. An adapter that returned an
    empty set forever would keep every existing test green while silently
    disabling the gate, and the failure would be invisible: no error, no
    changed verdict, just a guard that never fires.

    The fixture uses the shape that ACTUALLY forfeits in Python, established by
    reading real forfeiting functions rather than assumed: the call in a ``for``
    loop's iterable expression, and in a ``with`` statement's context-manager
    expression. Neither is covered by a recorded statement extent.

    An earlier draft of this test used a call inside an ``if`` test, reasoning
    by analogy from Go's ``if err := do(); err != nil``. That fixture forfeits
    NOTHING in Python — the ``if`` condition contains its calls, so they are
    covered, and the analogy was wrong. Which is also why Python measures 12.8%
    forfeiting against Go's 33.5%: the languages fail in different places.
    """
    from hypergumbo_core.cfg import (
        get_def_use_extractor, register_def_use_extractor,
    )
    from hypergumbo_core.cli import _build_ddg_for_verify_claims

    if get_def_use_extractor("python") is None:
        from hypergumbo_lang_mainstream.py_def_use import PythonDefUseExtractor
        register_def_use_extractor("python")(PythonDefUseExtractor)

    (tmp_path / "m.py").write_text(
        "import os\n"
        "def handler(p):\n"
        "    v = os.getenv('X')\n"
        "    for item in os.listdir(p):\n"
        "        v = v + item\n"
        "    return v\n"
    )
    _edges, symbols, _hints, _stmts, forfeits = _build_ddg_for_verify_claims(
        tmp_path,
    )
    assert symbols, "fixture produced no DDG symbols — nothing to forfeit"
    assert forfeits, (
        "forfeit set is empty for code with calls inside an if-test; the gate "
        "would never fire in production"
    )
    assert forfeits <= symbols, (
        "forfeit set must be a subset of walkable symbols"
    )
