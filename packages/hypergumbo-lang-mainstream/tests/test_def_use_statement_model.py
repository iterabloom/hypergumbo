# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-losod / WI-faful: header bindings and atomic statements, end to end.

The CFG builder (hypergumbo-core) now records a loop header or an
``if``/``switch`` initializer as statements that define their bindings, and
keeps an atomic statement holding a nested function or a control-flow
expression as ONE statement. That only matters if the def/use extractors give
those statements the right definitions, so this module checks three layers:

1. the extractor handlers the new statements reach (Go ``range_clause``, the
   Java enhanced-for header, Rust ``let_condition`` and closures);
2. the DDG through the real pipeline (CFG + populate + reaching defs), one
   fixture per language and shape, each with the edge it lacked before;
3. ``verify-claims`` on the production path for the shapes the items name:
   Go's ``for _, e := range os.Environ()`` (caddy's printEnvironment) and
   ``if err := ...; err != nil`` (the INV-dukam executor shape), a Rust
   ``let v = env::var(..)?;`` and a Java ``switch`` expression initializer.
   Each read ``structural`` or ``ddg_mixed`` before and reads ``ddg`` /
   ``confirmed`` now; a co-located control does not.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core.cfg import (
    build_function_cfg,
    load_cfg_mapping,
    populate_def_use_for_cfg,
    solve_reaching_defs,
)
from hypergumbo_core.cli import main
from hypergumbo_core.dataflow_scope import ensure_def_use_extractors_registered
from hypergumbo_lang_mainstream.go_def_use import GoDefUseExtractor
from hypergumbo_lang_mainstream.java_def_use import JavaDefUseExtractor
from hypergumbo_lang_mainstream.rust_def_use import RustDefUseExtractor

_FUNCTION_TYPES = {
    "python": "function_definition",
    "go": "function_declaration",
    "java": "method_declaration",
    "typescript": "function_declaration",
    "rust": "function_item",
    "c": "function_definition",
}


@pytest.fixture(autouse=True)
def _extractors() -> None:
    # A registry filled by import side effect is empty until something imports
    # it, and test_cfg.py clears it; assert reach first.
    ensure_def_use_extractors_registered()


def _first(lang: str, source: str, node_type: str) -> tuple[Any, bytes]:
    parser = tree_sitter.Parser(get_language(lang))
    src = source.encode("utf-8")
    stack = [parser.parse(src).root_node]
    while stack:
        node = stack.pop()
        if node.type == node_type:
            return node, src
        stack.extend(reversed(node.children))
    raise AssertionError(f"no {node_type}")  # pragma: no cover


def _ddg(lang: str, source: str) -> set[tuple[str, int, int]]:
    """(variable, def line, use line) of the outermost function's DDG."""
    fn, src = _first(lang, source, _FUNCTION_TYPES[lang])
    body = fn.child_by_field_name("body")
    mapping = load_cfg_mapping(lang)
    assert mapping is not None
    cfg = build_function_cfg(body, src, mapping, f"{lang}:t:1-99:f:function")
    populate_def_use_for_cfg(cfg, body, src, lang)
    return {
        (e.variable, e.def_line, e.use_line)
        for e in solve_reaching_defs(cfg).ddg_edges
    }


# ---------------------------------------------------------------------------
# 1. Handlers
# ---------------------------------------------------------------------------


class TestHandlers:
    @pytest.mark.parametrize("header, defines, uses", [
        ("k, v := range xs", ["k", "v"], ["xs"]),
        ("_, e := range os.Environ()", ["e"], ["os"]),
        ("k = range m", ["k"], ["m"]),
        ("range ch", [], ["ch"]),
    ])
    def test_go_range_clause(self, header: str, defines: list[str], uses: list[str]) -> None:
        clause, src = _first(
            "go", "package m\nfunc f() {\n\tfor " + header + " {\n\t}\n}\n", "range_clause",
        )
        result = GoDefUseExtractor().extract(clause, src)
        assert (result.defines, result.uses) == (defines, uses)

    def test_java_enhanced_for_reads_the_header_only(self) -> None:
        loop, src = _first("java", (
            "class A { void f() { for (String s : xs) { t = s; } } }"
        ), "enhanced_for_statement")
        result = JavaDefUseExtractor().extract(loop, src)
        assert (result.defines, result.uses) == (["s"], ["xs"])

    def test_java_local_class_method_handed_directly_defines_nothing(self) -> None:
        block, src = _first("java", (
            "class A { void f() { class L { void g() { String t = c; } } } }"
        ), "block")
        local = block.named_children[0]
        assert local.type == "class_declaration"
        inner = local.child_by_field_name("body").named_children[0]
        assert inner.type == "method_declaration"
        result = JavaDefUseExtractor().extract(inner, src)
        assert result.defines == [] and "c" in result.uses

    def test_rust_let_condition_defines_its_pattern(self) -> None:
        cond, src = _first("rust", "fn f() { if let Some(x) = opt { use_(x); } }", "let_condition")
        result = RustDefUseExtractor().extract(cond, src)
        assert (result.defines, result.uses) == (["x"], ["opt"])

    def test_rust_if_expression_delegates_its_let_condition(self) -> None:
        node, src = _first("rust", "fn f() { if let Some(x) = opt { use_(x); } }", "if_expression")
        result = RustDefUseExtractor().extract(node, src)
        assert (result.defines, result.uses) == (["x"], ["opt"])

    def test_rust_let_chain_defines_every_pattern(self) -> None:
        chain, src = _first("rust", (
            "fn f() { if let Some(a) = x && let Ok(b) = y && c { use_(a, b); } }"
        ), "let_chain")
        result = RustDefUseExtractor().extract(chain, src)
        assert result.defines == ["a", "b"]
        assert result.uses == ["x", "y", "c"]

    def test_rust_closure_parameters_are_neither_defined_nor_used(self) -> None:
        closure, src = _first("rust", "fn f() { run(|y, _| y + t); }", "closure_expression")
        result = RustDefUseExtractor().extract(closure, src)
        assert (result.defines, result.uses) == ([], ["t"])


# ---------------------------------------------------------------------------
# 2. The DDG through the real pipeline
# ---------------------------------------------------------------------------


class TestHeaderBindingsReachTheBody:
    """WI-losod: one fixture per language and header shape."""

    def test_go_if_initializer(self) -> None:
        edges = _ddg("go", (
            "package main\n"
            "func f() {\n"
            "\tcmd := exec.Command(\"ls\")\n"
            "\tif err := cmd.Run(); err != nil {\n"
            "\t\tlog(err)\n"
            "\t}\n"
            "}\n"
        ))
        # cmd is USED at the initializer (INV-dukam's shape), err defined there.
        assert {("cmd", 3, 4), ("err", 4, 4), ("err", 4, 5)} <= edges

    def test_go_for_clause(self) -> None:
        edges = _ddg("go", (
            "package main\n"
            "func f(n int) {\n"
            "\tfor j := 0; j < n; j++ {\n"
            "\t\tuse(j)\n"
            "\t}\n"
            "}\n"
        ))
        assert {("j", 3, 4), ("j", 3, 3)} <= edges

    def test_go_range(self) -> None:
        edges = _ddg("go", (
            "package main\n"
            "func f() {\n"
            "\tfor _, v := range os.Environ() {\n"
            "\t\tfmt.Println(v)\n"
            "\t}\n"
            "}\n"
        ))
        assert ("v", 3, 4) in edges

    def test_go_switch_initializer(self) -> None:
        edges = _ddg("go", (
            "package main\n"
            "func f(a int) {\n"
            "\tswitch x := g(a); x {\n"
            "\tcase 1:\n"
            "\t\tuse(x)\n"
            "\t}\n"
            "}\n"
        ))
        assert {("x", 3, 3), ("x", 3, 5)} <= edges

    def test_python_for(self) -> None:
        assert ("v", 2, 3) in _ddg("python", (
            "def f():\n"
            "    for v in os.environ.values():\n"
            "        use(v)\n"
        ))

    def test_rust_for(self) -> None:
        assert ("v", 2, 3) in _ddg("rust", (
            "fn f(xs: Vec<String>) {\n"
            "    for v in xs {\n"
            "        use_(v);\n"
            "    }\n"
            "}\n"
        ))

    def test_rust_if_let(self) -> None:
        assert ("x", 2, 3) in _ddg("rust", (
            "fn f(opt: Option<String>) {\n"
            "    if let Some(x) = opt {\n"
            "        use_(x);\n"
            "    }\n"
            "}\n"
        ))

    def test_typescript_for_of_and_classic_for(self) -> None:
        edges = _ddg("typescript", (
            "function f(items, n) {\n"
            "  for (const v of items) {\n"
            "    use(v);\n"
            "  }\n"
            "  for (let k = 0; k < n; k++) {\n"
            "    use(k);\n"
            "  }\n"
            "}\n"
        ))
        assert {("v", 2, 3), ("k", 5, 6)} <= edges

    def test_java_enhanced_and_classic_for(self) -> None:
        edges = _ddg("java", (
            "class A {\n"
            "  void f(java.util.List<String> xs, int n) {\n"
            "    for (String s : xs) {\n"
            "      use(s);\n"
            "    }\n"
            "    for (int k = 0; k < n; k++) {\n"
            "      use(k);\n"
            "    }\n"
            "  }\n"
            "}\n"
        ))
        assert {("s", 3, 4), ("k", 6, 7), ("k", 6, 6)} <= edges

    def test_c_classic_for(self) -> None:
        edges = _ddg("c", (
            "void f(int n) {\n"
            "  for (char *k = getenv(\"K\"); k; k = 0) {\n"
            "    use(k);\n"
            "  }\n"
            "}\n"
        ))
        assert ("k", 2, 3) in edges


class TestAtomicStatementDefinesItsTarget:
    """WI-faful: one fixture per language and shape."""

    def test_java_switch_expression_initializer(self) -> None:
        assert ("r", 3, 4) in _ddg("java", (
            "class A {\n"
            "  void f(int x) {\n"
            "    int r = switch (x) { case 1 -> 2; default -> 3; };\n"
            "    use(r);\n"
            "  }\n"
            "}\n"
        ))

    def test_java_lambda_initializer(self) -> None:
        edges = _ddg("java", (
            "class A {\n"
            "  void f(int x, String v) {\n"
            "    Runnable q = () -> { if (x > 0) { go(v); } };\n"
            "    q.run();\n"
            "  }\n"
            "}\n"
        ))
        assert ("q", 3, 4) in edges

    def test_rust_let_if_let_match_let_try(self) -> None:
        edges = _ddg("rust", (
            "fn f(c: bool, n: i32, v: String) -> Result<(), E> {\n"
            "    let x = if c { v.clone() } else { String::new() };\n"
            "    let y = match n { 1 => x.clone(), _ => x };\n"
            "    let k = std::env::var(\"K\")?;\n"
            "    use_(y, k);\n"
            "    Ok(())\n"
            "}\n"
        ))
        assert {("x", 2, 3), ("y", 3, 5), ("k", 4, 5)} <= edges

    def test_rust_closure_initializer(self) -> None:
        assert ("h", 2, 3) in _ddg("rust", (
            "fn f(c: bool) {\n"
            "    let h = |y: bool| { if y { go1(); } };\n"
            "    run(h);\n"
            "}\n"
        ))

    def test_typescript_arrow_initializer(self) -> None:
        assert ("g", 2, 3) in _ddg("typescript", (
            "function f(c) {\n"
            "  const g = () => { if (c) { go(); } };\n"
            "  run(g);\n"
            "}\n"
        ))

    def test_go_function_literal_initializer(self) -> None:
        assert ("h", 3, 8) in _ddg("go", (
            "package main\n"
            "func f(c bool) {\n"
            "\th := func() {\n"
            "\t\tif c {\n"
            "\t\t\tgo1()\n"
            "\t\t}\n"
            "\t}\n"
            "\trun(h)\n"
            "}\n"
        ))

    @pytest.mark.parametrize("lang, source, sink_line", [
        ("go", (
            "package main\n"
            "func f() {\n"
            "\tt := os.Getenv(\"K\")\n"
            "\tfunc() {\n"
            "\t\tt := \"constant\"\n"
            "\t\tif t != \"\" {\n"
            "\t\t\treturn\n"
            "\t\t}\n"
            "\t}()\n"
            "\tsink(t)\n"
            "}\n"
        ), 10),
        ("typescript", (
            "function f(items) {\n"
            "  let t = process.env.K;\n"
            "  items.forEach((x) => {\n"
            "    let t = \"constant\";\n"
            "    if (x) { log(t); }\n"
            "  });\n"
            "  sink(t);\n"
            "}\n"
        ), 7),
        ("python", (
            "def f(c):\n"
            "    t = os.environ[\"K\"]\n"
            "\n"
            "    def helper():\n"
            "        t = \"constant\"\n"
            "        if c:\n"
            "            return t\n"
            "\n"
            "    sink(t)\n"
        ), 9),
    ])
    def test_a_nested_function_binding_no_longer_kills_the_outer_one(
        self, lang: str, source: str, sink_line: int,
    ) -> None:
        """Inlined, the nested function's ``t = "constant"`` killed the outer
        tainted ``t`` before the sink. As a may-run region it cannot."""
        assert ("t", 2 if lang != "go" else 3, sink_line) in _ddg(lang, source)

    def test_a_sink_inside_a_callback_still_reads_the_captured_value(self) -> None:
        """The other half of the closure over-approximation: the body still
        sees what reaches the statement that creates it, at its own line."""
        assert ("v", 3, 6) in _ddg("java", (
            "class A {\n"
            "  void f(java.util.List<String> xs) {\n"
            "    String v = System.getenv(\"K\");\n"
            "    xs.forEach(x -> {\n"
            "      if (x != null) {\n"
            "        sink(v);\n"
            "      }\n"
            "    });\n"
            "  }\n"
            "}\n"
        ))


# ---------------------------------------------------------------------------
# 3. verify-claims, production path
# ---------------------------------------------------------------------------

_CLAIM = """claims:
  - id: SECRET-FS
    text: Secrets never reach a filesystem write.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: host_fs
"""


def _evidence(
    tmp_path: Path, name: str, source: str,
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / name).write_text(source)
    (tmp_path / "claims.yaml").write_text(_CLAIM)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    main(["verify-claims", str(repo), "--claims", str(tmp_path / "claims.yaml"),
          "--format", "json"])
    [verdict] = json.loads(capsys.readouterr().out)["verdicts"]
    return [(e["analysis_method"], e["walk_verdict"]) for e in verdict["evidence"]]


_GO_RANGE = """package main

import "os"

func leak() {
	for _, e := range os.Environ() {
		os.WriteFile("o.txt", []byte(e), 0o600)
	}
}
"""

_GO_RANGE_CONTROL = """package main

import "os"

func leak() {
	for _, e := range os.Environ() {
		_ = e
		os.WriteFile("o.txt", []byte("constant"), 0o600)
	}
}
"""

_GO_IF_INIT = """package main

import "os"

func leak() {
	k := os.Getenv("API_KEY")
	if err := os.WriteFile("o.txt", []byte(k), 0o600); err != nil {
		return
	}
}
"""

_RUST_LET_TRY = """fn leak() -> Result<(), Box<dyn std::error::Error>> {
    let v = std::env::var("API_KEY")?;
    std::fs::write("o.txt", v)?;
    Ok(())
}
"""

_JAVA_SWITCH = """import java.io.FileWriter;

class Env {
    void dump(int n) throws Exception {
        String v = System.getenv("API_KEY");
        String w = switch (n) { case 1 -> v; default -> v.trim(); };
        FileWriter f = new FileWriter("o.txt");
        f.write(w);
    }
}
"""


class TestProductionPath:
    @pytest.mark.parametrize("name, source", [
        ("main.go", _GO_RANGE),
        ("main.go", _GO_IF_INIT),
        ("main.rs", _RUST_LET_TRY),
        ("Env.java", _JAVA_SWITCH),
    ], ids=["go-range", "go-if-init", "rust-let-try", "java-switch-init"])
    def test_flow_through_the_statement_is_confirmed(
        self, tmp_path: Path, name: str, source: str,
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        assert _evidence(tmp_path, name, source, capsys, monkeypatch) == [
            ("ddg", "confirmed"),
        ]

    def test_co_located_control_is_not_confirmed(
        self, tmp_path: Path,
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        [(method, walk)] = _evidence(
            tmp_path, "main.go", _GO_RANGE_CONTROL, capsys, monkeypatch,
        )
        assert method == "ddg_mixed" and walk != "confirmed"
