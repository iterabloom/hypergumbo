# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the repo-level DDG builder (ADR-0017 §1c).

The registry is process-global, so every test that mutates it restores the
prior contents. A test that cleared it and did not restore would leave
`python` unregistered for whatever ran next, and the symptom — a later
suite's DDG silently coming back empty — looks nothing like its cause.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from hypergumbo_core.ddg_build import (
    LanguageDdgSpec,
    RepoDdg,
    _function_name,
    _python_refine,
    _refine_context,
    _solve_one_function,
    build_repo_ddg,
    clear_ddg_languages,
    get_ddg_language,
    register_ddg_language,
    registered_ddg_languages,
)


@pytest.fixture(autouse=True)
def _preserve_registry() -> Any:
    """Save and restore the global spec registry around every test."""
    import hypergumbo_core.ddg_build as mod

    saved = dict(mod._DDG_LANGUAGES)
    yield
    mod._DDG_LANGUAGES.clear()
    mod._DDG_LANGUAGES.update(saved)


class _FakeNode:
    """Minimal tree-sitter node stand-in."""

    def __init__(self, name_child: Any = None, fields: dict | None = None) -> None:
        self._fields = fields or {}
        if name_child is not None:
            self._fields["name"] = name_child
        self.start_byte = 0
        self.end_byte = 0

    def child_by_field_name(self, field: str) -> Any:
        return self._fields.get(field)


class TestRegistry:
    def test_get_returns_registered_spec(self) -> None:
        spec = LanguageDdgSpec(
            language="fakelang", file_glob="*.fake",
            function_node_types=frozenset({"fn"}),
        )
        register_ddg_language(spec)
        assert get_ddg_language("fakelang") is spec
        assert "fakelang" in registered_ddg_languages()

    def test_get_returns_none_for_unregistered(self) -> None:
        assert get_ddg_language("no-such-language") is None

    def test_clear_empties_the_registry(self) -> None:
        assert registered_ddg_languages()  # non-vacuity: something was there
        clear_ddg_languages()
        assert registered_ddg_languages() == frozenset()


class TestFunctionName:
    def test_uses_the_spec_override_when_present(self) -> None:
        spec = LanguageDdgSpec(
            language="x", file_glob="*.x", function_node_types=frozenset({"fn"}),
            name_for=lambda node, source: "OVERRIDDEN",
        )
        assert _function_name(_FakeNode(), b"", spec) == "OVERRIDDEN"

    def test_returns_none_when_the_node_has_no_name(self) -> None:
        spec = LanguageDdgSpec(
            language="x", file_glob="*.x", function_node_types=frozenset({"fn"}),
        )
        assert _function_name(_FakeNode(), b"", spec) is None


class TestSolveOneFunction:
    def test_bailed_out_result_records_nothing(self) -> None:
        """A solver bail-out must not be recorded as 'analyzed with no edges'.

        Those are different claims: the second would put the symbol in
        ddg_symbols and let a consumer treat the absence of edges as
        evidence rather than as a gap.
        """
        class _Result:
            bailed_out = True
            ddg_edges: ClassVar[list] = []

        out = RepoDdg()
        spec = LanguageDdgSpec(
            language="x", file_glob="*.x", function_node_types=frozenset({"fn"}),
        )
        deps = {
            "build_function_cfg": lambda *a: object(),
            "populate_def_use_for_cfg": lambda *a: None,
            "solve_reaching_defs": lambda *a: _Result(),
        }
        _solve_one_function(
            _FakeNode(), _FakeNode(), b"", spec, "sym", out, deps, None, {},
        )
        assert out.ddg_edges == []
        assert out.ddg_symbols == set()


class TestRefineContext:
    def test_no_hook_means_no_context(self) -> None:
        spec = LanguageDdgSpec(
            language="x", file_glob="*.x", function_node_types=frozenset({"fn"}),
        )
        assert _refine_context(spec, None, b"") == {}


class TestPythonRefine:
    def test_returns_empty_without_annotations_or_ddg_edges(self) -> None:
        """WI-dozon's condition, from the other side: with neither parameter
        annotations nor DDG edges there is no receiver signal to derive."""
        src = b"def f(a):\n    return a\n"
        import tree_sitter
        from tree_sitter_language_pack import get_language

        tree = tree_sitter.Parser(get_language("python")).parse(src)
        fn = tree.root_node.children[0]
        assert _python_refine(
            node=fn, body_node=fn.child_by_field_name("body"), source=src,
            ddg_edges=[], module_imports={}, imports={},
        ) == {}


class TestBuildRepoDdg:
    def test_unregistered_language_is_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("def f():\n    x = 1\n    return x\n")
        result = build_repo_ddg(tmp_path, ["no-such-language"])
        assert result.ddg_edges == []
        assert result.ddg_symbols == set()

    def test_language_with_no_grammar_is_skipped(self, tmp_path: Path) -> None:
        """A registered spec whose language the grammar pack cannot supply
        must degrade to 'no DDG for that language', not abort the walk."""
        register_ddg_language(LanguageDdgSpec(
            language="not-a-real-grammar", file_glob="*.zzz",
            function_node_types=frozenset({"fn"}),
        ))
        result = build_repo_ddg(tmp_path, ["not-a-real-grammar"])
        assert result.ddg_edges == []

    def test_python_still_produces_edges(self, tmp_path: Path) -> None:
        """Non-vacuity floor for this whole file: the builder must actually
        work for a real language, or the skip-path tests above prove nothing.

        The registry guard matters here. ``test_cfg.py`` calls
        ``clear_def_use_extractors()``; a bare ``import`` of the extractor
        module is a no-op once it is already in ``sys.modules``, so the
        registration would NOT be restored and this floor would fail — or,
        if it were ever written as an inequality the other way, pass
        vacuously against a pipeline with no extractor at all.
        """
        import importlib

        import hypergumbo_lang_mainstream.py_def_use as py_mod
        from hypergumbo_core.cfg import get_def_use_extractor

        if get_def_use_extractor("python") is None:
            importlib.reload(py_mod)
        assert get_def_use_extractor("python") is not None

        (tmp_path / "m.py").write_text(
            "def handler(req):\n"
            "    secret = req.password\n"
            "    send(secret)\n",
        )
        result = build_repo_ddg(tmp_path, ["python"])
        assert len(result.ddg_edges) >= 1
        assert any(e.symbol_id for e in result.ddg_edges)


class TestBindingNamedCallables:
    """WI-mufag: a node the ANALYZER names after its binding is walked under the
    analyzer's own id for its exact span, and not walked without one."""

    _SRC = "def handler(req):\n    secret = req.password\n    send(secret)\n"
    _KEY = ("python", "m.py", 1, 0, 3)
    _ID = "python:m.py:1-3:bound_name:function"

    def _spec_walking_only_bound_nodes(self) -> None:
        import importlib

        import hypergumbo_lang_mainstream.py_def_use as py_mod
        from hypergumbo_core.cfg import get_def_use_extractor

        if get_def_use_extractor("python") is None:
            importlib.reload(py_mod)
        register_ddg_language(LanguageDdgSpec(
            language="python", file_glob="*.py",
            function_node_types=frozenset(),
            bound_callable_node_types=frozenset({"function_definition"}),
        ))

    def test_walked_under_the_analyzer_id(self, tmp_path: Path) -> None:
        self._spec_walking_only_bound_nodes()
        (tmp_path / "m.py").write_text(self._SRC)
        result = build_repo_ddg(tmp_path, ["python"], {self._KEY: self._ID})
        assert result.ddg_symbols == {self._ID}
        assert result.ddg_edges

    def test_not_walked_without_an_analyzer_symbol(self, tmp_path: Path) -> None:
        """THE CONTROL: no index entry, no walk -- never a name derived here."""
        self._spec_walking_only_bound_nodes()
        (tmp_path / "m.py").write_text(self._SRC)
        assert build_repo_ddg(tmp_path, ["python"]).ddg_symbols == set()
        assert build_repo_ddg(tmp_path, ["python"], {}).ddg_symbols == set()


class TestAnalyzerSymbolIndex:
    def _node(self, sid: str, kind: str = "function", line: int = 1) -> dict[str, Any]:
        return {
            "id": sid, "kind": kind, "language": "javascript", "path": "a.js",
            "span": {"start_line": line, "start_col": 4, "end_line": line + 2},
        }

    def test_keys_a_callable_by_its_exact_span(self) -> None:
        from hypergumbo_core.ddg_build import analyzer_symbol_index

        index = analyzer_symbol_index([self._node("f"), self._node("v", kind="variable", line=9)])
        assert index == {("javascript", "a.js", 1, 4, 3): "f"}

    def test_a_shared_span_is_dropped_not_guessed(self) -> None:
        from hypergumbo_core.ddg_build import analyzer_symbol_index

        assert analyzer_symbol_index([self._node("f"), self._node("g")]) == {}
