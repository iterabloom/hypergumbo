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
            language="fakelang", file_globs=("*.fake",),
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
            language="x", file_globs=("*.x",), function_node_types=frozenset({"fn"}),
            name_for=lambda node, source: "OVERRIDDEN",
        )
        assert _function_name(_FakeNode(), b"", spec) == "OVERRIDDEN"

    def test_returns_none_when_the_node_has_no_name(self) -> None:
        spec = LanguageDdgSpec(
            language="x", file_globs=("*.x",), function_node_types=frozenset({"fn"}),
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
            language="x", file_globs=("*.x",), function_node_types=frozenset({"fn"}),
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
            language="x", file_globs=("*.x",), function_node_types=frozenset({"fn"}),
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
            language="not-a-real-grammar", file_globs=("*.zzz",),
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
            language="python", file_globs=("*.py",),
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


class TestBindingsThatHoldCallables:
    """WI-rovun: a bound node that is a BINDING rather than a callable.

    Go's ``var rootCmd = &Command{Run: func(...) {...}}`` is anchored by the
    analyzer on the ``var_spec`` -- a node with no ``body`` of its own, holding
    one or more function literals and possibly other initializer code. The
    spec's ``bound_bodies_for`` hook names the bodies; each is walked under the
    analyzer's id for the binding, and the walk may not refute (forfeit) when
    the binding holds more than one body or code outside its bodies.

    Python stands in for the shape here because it is the language core's tests
    can drive: ``decorated_definition`` has no ``body`` field (it has
    ``definition``), and a class holds several bodies.
    """

    _ONE = (
        "@{decorator}\n"
        "def handler(req):\n"
        "    secret = req.password\n"
        "    send(secret)\n"
    )
    _TWO = (
        "class Holder:\n"
        "    def a(self, req):\n"
        "        secret = req.password\n"
        "        send(secret)\n"
        "    def b(self, req):\n"
        "        other = req.name\n"
        "        send(other)\n"
    )
    _ID = "python:m.py:1-4:bound_name:variable"

    @staticmethod
    def _decorated_body(node: Any, source: bytes) -> list[Any]:
        return [node.child_by_field_name("definition").child_by_field_name("body")]

    @staticmethod
    def _method_bodies(node: Any, source: bytes) -> list[Any]:
        return [
            child.child_by_field_name("body")
            for child in node.child_by_field_name("body").children
            if child.type == "function_definition"
        ]

    def _register(self, node_type: str, hook: Any) -> None:
        import importlib

        import hypergumbo_lang_mainstream.py_def_use as py_mod
        from hypergumbo_core.cfg import get_def_use_extractor

        if get_def_use_extractor("python") is None:
            importlib.reload(py_mod)
        register_ddg_language(LanguageDdgSpec(
            language="python", file_globs=("*.py",),
            function_node_types=frozenset(),
            bound_callable_node_types=frozenset({node_type}),
            bound_bodies_for=hook,
        ))

    def _one(self, tmp_path: Path, decorator: str, hook: Any) -> RepoDdg:
        self._register("decorated_definition", hook)
        (tmp_path / "m.py").write_text(self._ONE.format(decorator=decorator))
        return build_repo_ddg(
            tmp_path, ["python"], {("python", "m.py", 1, 0, 4): self._ID},
        )

    def test_a_body_named_by_the_hook_is_walked_under_the_binding_id(
        self, tmp_path: Path,
    ) -> None:
        result = self._one(tmp_path, "staticmethod", self._decorated_body)
        assert result.ddg_symbols == {self._ID}
        assert result.ddg_edges
        assert {e.symbol_id for e in result.ddg_edges} == {self._ID}

    def test_without_the_hook_a_node_with_no_body_field_is_not_walked(
        self, tmp_path: Path,
    ) -> None:
        """THE CONTROL: the hook is what reaches the body."""
        assert self._one(tmp_path, "staticmethod", None).ddg_symbols == set()

    def test_one_body_and_nothing_else_keeps_refutation(self, tmp_path: Path) -> None:
        result = self._one(tmp_path, "staticmethod", self._decorated_body)
        assert self._ID in result.ddg_symbols
        assert self._ID not in result.forfeit_refutation

    def test_a_call_outside_the_bodies_forfeits_refutation(self, tmp_path: Path) -> None:
        """``@app.route("/x")`` runs in the binding's context, not the body's:
        the walk never sees it, so an exhausted walk is not evidence."""
        result = self._one(tmp_path, 'app.route("/x")', self._decorated_body)
        assert self._ID in result.ddg_symbols
        assert self._ID in result.forfeit_refutation

    def test_every_body_is_walked_and_merged_and_refutation_is_forfeit(
        self, tmp_path: Path,
    ) -> None:
        """Two bodies under one anchor are two functions to the program: a value
        crossing between them is a CROSS-function flow the intraprocedural walk
        cannot follow, so its ``False`` would be unearned."""
        self._register("class_definition", self._method_bodies)
        (tmp_path / "m.py").write_text(self._TWO)
        sid = "python:m.py:1-7:Holder:variable"
        result = build_repo_ddg(
            tmp_path, ["python"], {("python", "m.py", 1, 0, 7): sid},
        )
        assert result.ddg_symbols == {sid}
        assert {e.use_line for e in result.ddg_edges} >= {4, 7}
        # Merged, not overwritten: the first body's statements survive the second.
        assert {line for line, _, _ in result.stmt_defuse[sid]} >= {3, 4, 6, 7}
        assert sid in result.forfeit_refutation

    def test_a_language_with_no_call_node_types_forfeits(self, tmp_path: Path) -> None:
        """Coverage outside the bodies is unknowable without call node types,
        and unknowable forfeits -- the same default-deny as the body gate."""
        from hypergumbo_core.ddg_build import _binding_walk_incomplete

        mapping = type("M", (), {"call_node_types": []})()
        assert _binding_walk_incomplete(_FakeNode(), [object()], mapping) is True

    def test_merge_unions_unaccounted_names_and_hints(self) -> None:
        """A second body under the same id ADDS to the first's record."""
        from hypergumbo_core.cfg import DdgEdge

        out = RepoDdg()
        spec = LanguageDdgSpec(
            language="python", file_globs=("*.py",),
            function_node_types=frozenset(),
            refine=lambda **kw: {(kw["body_node"], "x"): "T"},
        )
        result = type("R", (), {
            "bailed_out": False,
            "ddg_edges": [DdgEdge("x", "b", 1, "b", 2, "s")],
        })()
        deps = {
            "build_function_cfg": lambda *a: type("C", (), {"blocks": {}})(),
            "populate_def_use_for_cfg": lambda *a: None,
            "solve_reaching_defs": lambda cfg: result,
            "uncovered_semantic_lines": lambda *a: frozenset(),
            "unaccounted_names": lambda cfg, body, src: frozenset({body}),
        }
        for body in (1, 2):
            _solve_one_function(None, body, b"", spec, "s", out, deps, None, {})
        assert out.unaccounted_names["s"] == frozenset({1, 2})
        assert out.hints_by_caller["s"] == {(1, "x"): "T", (2, "x"): "T"}


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

    def test_a_spec_declared_anchor_kind_is_admitted_for_its_language_only(self) -> None:
        """WI-rovun: Go anchors a package-level literal on a ``variable``. The
        kind is admitted for a language whose spec declares it, and no other --
        javascript's ``variable`` stays out, as the test above pins."""
        from hypergumbo_core.ddg_build import analyzer_symbol_index

        register_ddg_language(LanguageDdgSpec(
            language="fakelang", file_globs=("*.f",),
            function_node_types=frozenset(),
            bound_anchor_kinds=frozenset({"variable"}),
        ))
        fake_var = {**self._node("fv", kind="variable"), "language": "fakelang"}
        fake_cls = {**self._node("fc", kind="class", line=20), "language": "fakelang"}
        index = analyzer_symbol_index(
            [fake_var, fake_cls, self._node("jv", kind="variable", line=9)],
        )
        assert index == {("fakelang", "a.js", 1, 4, 3): "fv"}


class TestFileGlobsComeFromTheTaxonomy:
    """WI-fovus: a spec walks its language's taxonomy extensions unless it
    declares a narrowing, and each file is parsed with its own grammar."""

    def test_default_globs_are_the_languages_taxonomy_extensions(self) -> None:
        spec = LanguageDdgSpec(language="typescript", function_node_types=frozenset())
        assert spec.globs() == ("*.ts", "*.tsx", "*.mts", "*.cts")

    def test_declared_globs_win(self) -> None:
        spec = LanguageDdgSpec(
            language="python", function_node_types=frozenset(), file_globs=("*.py",),
        )
        assert spec.globs() == ("*.py",)

    def test_spec_files_are_deduplicated_sorted_and_skip_vendored(self, tmp_path: Path) -> None:
        from hypergumbo_core.ddg_build import _spec_files

        (tmp_path / "b.d.ts").write_text("")
        (tmp_path / "a.ts").write_text("")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "c.ts").write_text("")
        spec = LanguageDdgSpec(
            language="typescript", function_node_types=frozenset(),
            file_globs=("*.ts", "*.d.ts"),
        )
        assert [p.name for p in _spec_files(tmp_path, spec)] == ["a.ts", "b.d.ts"]

    def test_parser_cache_builds_one_parser_per_grammar(self) -> None:
        import tree_sitter
        from tree_sitter_language_pack import get_language

        from hypergumbo_core.ddg_build import _ParserCache

        calls: list[str] = []

        def counting(name: Any) -> Any:
            calls.append(name)
            return get_language(name)

        cache = _ParserCache(tree_sitter, counting)
        assert cache.get("tsx") is cache.get("tsx")
        assert calls == ["tsx"]

    def test_parser_cache_returns_none_for_a_missing_grammar(self) -> None:
        import tree_sitter

        from hypergumbo_core.ddg_build import _ParserCache

        def missing(name: Any) -> Any:
            raise LookupError(name)

        cache = _ParserCache(tree_sitter, missing)
        assert cache.get("nope") is None
        assert cache.get("nope") is None  # cached, not retried

    def test_a_file_whose_grammar_is_missing_is_skipped(self, tmp_path: Path) -> None:
        from hypergumbo_core.ddg_build import _walk_language

        (tmp_path / "a.x").write_text("")

        asked: list[str] = []

        class _NoParsers:
            def get(self, grammar: str) -> None:
                asked.append(grammar)
                return None

        out = RepoDdg()
        spec = LanguageDdgSpec(
            language="x", function_node_types=frozenset({"fn"}), file_globs=("*.x",),
        )
        # Reached the file (asked for its grammar), then skipped it -- a None
        # parser would raise on ``.parse`` had the guard been missing.
        _walk_language(tmp_path, spec, _NoParsers(), None, out, {}, {})  # type: ignore[arg-type]
        assert asked == ["x"]
        assert out.ddg_symbols == set()
