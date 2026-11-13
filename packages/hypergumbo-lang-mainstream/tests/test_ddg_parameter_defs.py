# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-sosov: a function's parameters are reaching definitions at its own line.

No def/use extractor defines a parameter, so without entry definitions
``def save(req): p = req.get("p")`` has no DDG edge for ``req``: the use at
line 2 has no reaching definition, and a value that ARRIVES as a parameter
(Express ``req``, Go ``*http.Request``) is invisible to the taint walk. Each
language's DDG spec names the parameters (``params_for``),
``_solve_one_function`` hands them to the solver as entry definitions at the
function's line, and records them on ``RepoDdg.params`` in order for the
taint stage.

Verdict-inert by construction: no CFG statement is added, so ``stmt_defuse``
and ``unaccounted_names`` are unaffected -- pinned below.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core.dataflow_scope import ensure_def_use_extractors_registered
from hypergumbo_core.ddg_build import build_repo_ddg
from hypergumbo_lang_mainstream.go_def_use import _go_params
from hypergumbo_lang_mainstream.ts_def_use import _jsts_params


@pytest.fixture(autouse=True)
def _extractors() -> None:
    # A registry filled by import side effect is empty until something imports it.
    ensure_def_use_extractors_registered()


def _ddg(tmp_path: Path, name: str, text: str, language: str):
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / name).write_text(text)
    return build_repo_ddg(root, languages=(language,))


def _sym(ddg, suffix: str) -> str:
    [sym] = [s for s in ddg.ddg_symbols if s.endswith(suffix)]
    return sym


def _edges(ddg, sym: str) -> set[tuple[str, int, int]]:
    return {(e.variable, e.def_line, e.use_line) for e in ddg.ddg_edges if e.symbol_id == sym}


class TestPython:
    def test_a_parameter_reaches_its_use_from_the_def_line(self, tmp_path: Path) -> None:
        ddg = _ddg(tmp_path, "a.py", 'def save(req):\n    p = req.get("p")\n    return open(p)\n', "python")
        sym = _sym(ddg, ":save:function")
        assert ("req", 1, 2) in _edges(ddg, sym)
        assert ddg.params[sym] == ("req",)

    def test_every_parameter_shape_is_named_in_order(self, tmp_path: Path) -> None:
        ddg = _ddg(
            tmp_path, "a.py",
            "def f(self, a, b: int, c=1, d: str = 'x', *args, **kw):\n"
            "    return (self, a, b, c, d, args, kw)\n", "python",
        )
        sym = _sym(ddg, ":f:function")
        assert ddg.params[sym] == ("self", "a", "b", "c", "d", "args", "kw")
        assert {(v, 1, 2) for v in ddg.params[sym]} <= _edges(ddg, sym)

    def test_a_reassignment_kills_the_parameter_definition(self, tmp_path: Path) -> None:
        ddg = _ddg(tmp_path, "a.py", "def f(a):\n    a = 1\n    return a\n", "python")
        sym = _sym(ddg, ":f:function")
        edges = _edges(ddg, sym)
        assert ("a", 2, 3) in edges and ("a", 1, 3) not in edges

    def test_an_unused_parameter_has_no_edge_but_is_still_named(self, tmp_path: Path) -> None:
        ddg = _ddg(tmp_path, "a.py", "def g(req, res):\n    x = 1\n    return x\n", "python")
        sym = _sym(ddg, ":g:function")
        assert not [e for e in _edges(ddg, sym) if e[0] in ("req", "res")]
        assert ddg.params[sym] == ("req", "res")

    def test_the_statement_records_and_the_names_gate_are_untouched(self, tmp_path: Path) -> None:
        """Inertness pin: the parameter is a solver entry definition, not a statement."""
        ddg = _ddg(tmp_path, "a.py", 'def save(req):\n    p = req.get("p")\n    return open(p)\n', "python")
        sym = _sym(ddg, ":save:function")
        assert not [s for s in ddg.stmt_defuse[sym] if s[0] == 1], ddg.stmt_defuse[sym]
        assert "req" not in ddg.unaccounted_names.get(sym, frozenset())

    def test_module_code_has_no_parameters(self, tmp_path: Path) -> None:
        ddg = _ddg(tmp_path, "a.py", "import os\nx = os.getenv('A')\nprint(x)\n", "python")
        assert not any(k.endswith(":file:file") and v for k, v in ddg.params.items())


class TestJavaScriptAndTypeScript:
    def test_a_declared_function_parameter_reaches_its_use(self, tmp_path: Path) -> None:
        ddg = _ddg(
            tmp_path, "h.js", "function h(req, res) {\n  const q = req.query;\n  return q;\n}\n", "javascript",
        )
        sym = _sym(ddg, ":h:function")
        assert ("req", 1, 2) in _edges(ddg, sym)
        assert ddg.params[sym] == ("req", "res")

    def test_pattern_default_and_rest_parameters_name_their_leaves(self, tmp_path: Path) -> None:
        ddg = _ddg(
            tmp_path, "k.js",
            "function k({ body }, [first], z = 1, ...rest) {\n  return [body, first, z, rest];\n}\n",
            "javascript",
        )
        sym = _sym(ddg, ":k:function")
        assert ddg.params[sym] == ("body", "first", "z", "rest")
        assert {(v, 1, 2) for v in ddg.params[sym]} <= _edges(ddg, sym)

    def test_typescript_required_optional_and_defaulted_parameters(self, tmp_path: Path) -> None:
        ddg = _ddg(
            tmp_path, "g.ts",
            "function g(req: Request, res?: Response, n: number = 1): void {\n  console.log(req, n);\n}\n",
            "typescript",
        )
        sym = _sym(ddg, ":g:function")
        assert ddg.params[sym] == ("req", "res", "n")
        assert {("req", 1, 2), ("n", 1, 2)} <= _edges(ddg, sym)


def _first(lang: str, source: str, node_type: str) -> tuple[Any, bytes]:
    src = source.encode("utf-8")
    stack = [tree_sitter.Parser(get_language(lang)).parse(src).root_node]
    while stack:
        node = stack.pop()
        if node.type == node_type:
            return node, src
        stack.extend(reversed(node.children))
    raise AssertionError(f"no {node_type}")  # pragma: no cover


class TestHooksDirectly:
    """The shapes the repo walk does not reach on its own: an arrow is walked
    only under the analyzer's id for its span (WI-mufag), so its two parameter
    spellings are asked of the hook; a renamed object-pattern leaf likewise."""

    def test_a_single_identifier_arrow_names_its_one_parameter(self) -> None:
        node, src = _first("javascript", "const k = req => req.body;\n", "arrow_function")
        assert _jsts_params(node, src) == ["req"]

    def test_an_arrow_with_a_parameter_list(self) -> None:
        node, src = _first("javascript", "const h = (req, res) => res.send(req.query);\n", "arrow_function")
        assert _jsts_params(node, src) == ["req", "res"]

    def test_a_renamed_object_pattern_leaf_is_the_binding(self) -> None:
        node, src = _first(
            "javascript", "function m({ a: renamed, b }) { return renamed + b; }\n", "function_declaration",
        )
        assert _jsts_params(node, src) == ["renamed", "b"]

    def test_the_program_root_has_no_parameters(self) -> None:
        node, src = _first("javascript", "const x = 1;\n", "program")
        assert _jsts_params(node, src) == []

    def test_a_go_var_spec_has_no_parameters(self) -> None:
        node, src = _first("go", "package m\n\nvar h = func(w int) {}\n", "var_spec")
        assert _go_params(node, src) == []


class TestGo:
    def test_a_method_names_its_receiver_and_parameters(self, tmp_path: Path) -> None:
        ddg = _ddg(
            tmp_path, "s.go",
            'package m\n\nimport "net/http"\n\ntype saver struct{}\n\n'
            "func (s saver) ServeHTTP(w http.ResponseWriter, r *http.Request) {\n"
            "\tw.Write([]byte(r.URL.Path))\n}\n", "go",
        )
        sym = _sym(ddg, ":saver.ServeHTTP:method")
        assert ddg.params[sym] == ("s", "w", "r")
        assert {("w", 7, 8), ("r", 7, 8)} <= _edges(ddg, sym)

    def test_grouped_and_variadic_parameters(self, tmp_path: Path) -> None:
        ddg = _ddg(
            tmp_path, "f.go", "package m\n\nfunc f(a, b int, c ...string) {\n\tprintln(a, b, c)\n}\n", "go",
        )
        sym = _sym(ddg, ":f:function")
        assert ddg.params[sym] == ("a", "b", "c")
        assert {("a", 3, 4), ("b", 3, 4), ("c", 3, 4)} <= _edges(ddg, sym)
