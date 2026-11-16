# SPDX-License-Identifier: AGPL-3.0-or-later
"""Direct (non-subprocess) tests for the cmd_verify_claims DDG helpers.

Subprocess-shaped tests of ``hypergumbo verify-claims`` don't contribute
to coverage (per AGENTS.md). These tests call
``_build_ddg_for_verify_claims`` and the ddg_build pipeline it adapts
in-process to exercise the AST → CFG → def_use → DDG pipeline.
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
    edges, symbols, hints, _stmts, _forfeits, _unacc, _rets, _tgts, _params = _build_ddg_for_verify_claims(repo)
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
    edges, symbols, hints, _stmts, _forfeits, _unacc, _rets, _tgts, _params = _build_ddg_for_verify_claims(repo)
    assert edges == []
    assert symbols == set()


def test_build_python_ddg_handles_empty_repo(tmp_path: Path) -> None:
    """Empty repo → empty ddg output, no exception."""
    from hypergumbo_core.cli import _build_ddg_for_verify_claims

    repo = tmp_path / "fake-repo"
    repo.mkdir()
    edges, symbols, hints, _stmts, _forfeits, _unacc, _rets, _tgts, _params = _build_ddg_for_verify_claims(repo)
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
    edges, symbols, hints, _stmts, _forfeits, _unacc, _rets, _tgts, _params = _build_ddg_for_verify_claims(repo)
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
    edges, symbols, hints, _stmts, _forfeits, _unacc, _rets, _tgts, _params = _build_ddg_for_verify_claims(repo)
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

    The fixture is a Go package-level ``var`` binding TWO function literals
    (cobra's ``Pre``/``Run`` shape). The walk may not refute over a binding
    whose code it cannot see as one function (WI-rovun): that is a rule, not
    a gap, so it does not close. A one-literal binding beside it is the
    control: the set is populated, not everything. The analyzer's nodes are
    passed because a binding is walked only under the analyzer's own id.

    Go, not Python, because no probed Python shape forfeits: the Python CFG
    records the constructs that once did (a ``with`` statement's context-manager
    expression, a ``for`` loop's iterable and its ``else:`` clause; WI-simiv,
    WI-losod, INV-rotol), so a Python fixture would stand on a gap that is
    closed. The floor stands on the Go rule instead.

    Do not swap in a call inside an ``if`` test by analogy with Go's
    ``if err := do(); err != nil``: in Python the ``if`` condition contains its
    calls, so they are covered and nothing forfeits. The languages forfeit in
    different places.
    """
    from hypergumbo_core.cli import _build_ddg_for_verify_claims
    from hypergumbo_core.dataflow_scope import ensure_def_use_extractors_registered
    from hypergumbo_lang_mainstream.go import analyze_go

    # Registration is an import side effect; an empty registry would make
    # every assertion below vacuously true.
    assert ensure_def_use_extractors_registered()
    root = tmp_path / "proj"
    root.mkdir()
    (root / "main.go").write_text(
        "package main\n\n"
        "import \"os\"\n\n"
        "type Command struct {\n"
        "\tPre func()\n"
        "\tRun func(args []string)\n"
        "}\n\n"
        "func send(s string) {}\n\n"
        "var twoCmd = &Command{\n"
        "\tPre: func() {\n"
        "\t\tv := os.Getenv(\"A\")\n"
        "\t\tsend(v)\n"
        "\t},\n"
        "\tRun: func(args []string) {\n"
        "\t\tw := os.Getenv(\"B\")\n"
        "\t\tsend(w)\n"
        "\t},\n"
        "}\n\n"
        "var rootCmd = &Command{\n"
        "\tRun: func(args []string) {\n"
        "\t\tv := os.Getenv(\"C\")\n"
        "\t\tsend(v)\n"
        "\t},\n"
        "}\n"
    )
    # ``analyze_go`` on an absolute root reports absolute paths; the index
    # reads repo-relative ones, as the survey hands them over.
    prefix = f"{root}/"
    nodes = []
    for symbol in analyze_go(root).symbols:
        raw = symbol.to_dict()
        nodes.append({
            **raw,
            "id": raw["id"].replace(prefix, "", 1),
            "path": raw["path"].replace(prefix, "", 1),
        })
    ids = {n["name"]: n["id"] for n in nodes}
    _edges, symbols, _hints, _stmts, forfeits, _unacc, _rets, _tgts, _params = (
        _build_ddg_for_verify_claims(root, analyzer_nodes=nodes)
    )
    assert symbols, "fixture produced no DDG symbols — nothing to forfeit"
    assert ids["twoCmd"] in forfeits, (
        "forfeit set is empty for a binding of two literals; the gate would "
        "never fire in production"
    )
    assert ids["rootCmd"] in symbols and ids["rootCmd"] not in forfeits
    assert forfeits <= symbols, "forfeit set must be a subset of walkable symbols"


def test_unaccounted_names_reaches_the_walk_through_the_repo_ddg(
    tmp_path,
) -> None:
    """INV-lupav L4, END TO END: the signal survives the production plumbing.

    The unit tests pin ``cfg.unaccounted_names`` and the walk's use of it
    separately, and an extractor can be entirely correct against hand-picked
    nodes and contribute nothing — an extractor can produce zero DDG edges, or
    never be imported at all, while its unit tests hold 100% coverage. This
    asserts the whole chain:
    ``build_repo_ddg`` → ``RepoDdg.unaccounted_names`` → the adapter's return
    tuple, over a real file parsed by the real extractor.

    THE CONTROL IS THE SECOND FUNCTION, and it is what makes this a test of
    the SIGNAL rather than of the plumbing: ``clean`` differs from ``leaky``
    by one construct — ``c[key] = 1`` against ``c = key`` — and must come back
    with nothing unaccounted. Without it a function that reported every
    variable would pass just as well.
    """
    from hypergumbo_core.cfg import (
        get_def_use_extractor, register_def_use_extractor,
    )
    from hypergumbo_core.cli import _build_ddg_for_verify_claims

    if get_def_use_extractor("python") is None:
        from hypergumbo_lang_mainstream.py_def_use import PythonDefUseExtractor
        register_def_use_extractor("python")(PythonDefUseExtractor)

    (tmp_path / "m.py").write_text(
        "def leaky(p):\n"
        "    key = str(p)\n"
        "    c = {}\n"
        "    c[key] = 1\n"          # index never read -> `key` unaccounted
        "    return c\n"
        "\n"
        "def clean(p):\n"
        "    key = str(p)\n"
        "    c = key\n"
        "    return c\n"
    )
    _edges, symbols, _hints, _stmts, _forfeits, unaccounted, _rets, _tgts, _params = (
        _build_ddg_for_verify_claims(tmp_path)
    )
    assert symbols, "fixture produced no DDG symbols — nothing could escape"
    leaky = next(s for s in symbols if s.endswith(":leaky:function"))
    clean = next(s for s in symbols if s.endswith(":clean:function"))
    assert unaccounted.get(leaky) == frozenset({"key"})
    assert clean not in unaccounted, (
        "the control reports an unaccounted name; the signal is firing on "
        "code the extractor demonstrably did read"
    )
