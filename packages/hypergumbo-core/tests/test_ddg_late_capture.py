# SPDX-License-Identifier: AGPL-3.0-or-later
"""A definition made AFTER a nested function reaches that function's reads (WI-lokok).

``function setup() { u = u + k; }`` written before ``const k = process.env.K``
and called after it puts the secret into ``u``, and ``fetch(u)`` sends it. The
nested body is a may-run region hung off its definition (WI-faful), and
reaching definitions follow CFG order, so the assignment to ``k`` had no edge
into ``setup``: the walk saw the secret's only use at a terminating
``console.log``, exhausted, and REMOVED the flow.

A closure reads a captured variable when it RUNS, which may be any time after
its definition. :func:`cfg.late_capture_edges` adds the edges that model, from
each definition placed after the nested function to each read of that name in
it. Edges only ever add paths, so this turns ``False`` into ``True`` or
``None``, never the reverse.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core import dataflow_scope
from hypergumbo_core.cli import main
from hypergumbo_core.ddg_build import build_repo_ddg

_CLAIMS = '''claims:
  - id: SECRET-NET
    text: Secrets never reach the network.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: network
'''


def _verdict(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    """Run the shipped CLI and return the SECRET-NET verdict."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    for name, text in files.items():
        (repo / name).write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    mp = pytest.MonkeyPatch()
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(io.StringIO()):
            main(["verify-claims", str(repo), "--claims", str(claims),
                  "--format", "json"])
    finally:
        mp.undo()
    verdicts = {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}
    return verdicts["SECRET-NET"]


_JS_SETUP_BEFORE_SECRET = """\
function main() {
  let u = "http://x/";
  function setup() { u = u + %s; }
  const k = process.env.K;
  console.log(k.length);
  setup();
  fetch(u);
}
"""


def test_a_closure_defined_before_the_secret_carries_it_to_the_sink(tmp_path: Path) -> None:
    """The filed repro. It read ``confirmed`` with no evidence."""
    verdict = _verdict(tmp_path, {"app.js": _JS_SETUP_BEFORE_SECRET % "k"})
    assert verdict["verdict"] == "violated"
    assert [(e["analysis_method"], e["walk_verdict"]) for e in verdict["evidence"]] == [
        ("ddg", "reached"),
    ]


def test_a_closure_that_does_not_read_the_secret_still_lets_the_walk_remove_it(
    tmp_path: Path,
) -> None:
    """The control: the same layout with ``setup`` appending a literal. The
    secret dies at ``console.log`` and the walk may still remove the flow."""
    verdict = _verdict(tmp_path, {"app.js": _JS_SETUP_BEFORE_SECRET % '"y"'})
    assert verdict["evidence"] == []


def test_only_definitions_after_the_nested_function_gain_an_edge(tmp_path: Path) -> None:
    """``k = "b"`` reaches ``f`` by ordinary reaching definitions and ``k = "a"``
    is killed before it; only the definition after ``f`` gets a new edge."""
    (tmp_path / "app.py").write_text(
        "import os\n"
        "def main():\n"
        "    k = 'a'\n"
        "    k = 'b'\n"
        "    def f():\n"
        "        return k\n"
        "    k = os.environ['K']\n"
        "    return f\n"
    )
    dataflow_scope.ensure_def_use_extractors_registered()
    ddg = build_repo_ddg(tmp_path, languages=("python",))
    into_f = {(e.def_line, e.use_line) for e in ddg.ddg_edges if e.variable == "k"}
    # Line 5 is the ``def`` statement (it reads what the body captures), 6 the body.
    assert into_f == {(4, 5), (4, 6), (7, 5), (7, 6)}


def test_a_read_after_the_nested_function_binds_the_name_gets_no_edge(tmp_path: Path) -> None:
    """``f`` binds its own ``k`` first, so its body never reads the enclosing
    one. The ``def`` statement (line 3) lists every name its body mentions and
    keeps its edge: it hands the value to nothing, so the walk can only read it
    as an escape there."""
    (tmp_path / "app.py").write_text(
        "import os\n"
        "def main():\n"
        "    def f():\n"
        "        k = 'local'\n"
        "        return k\n"
        "    k = os.environ['K']\n"
        "    return f\n"
    )
    dataflow_scope.ensure_def_use_extractors_registered()
    ddg = build_repo_ddg(tmp_path, languages=("python",))
    assert {e.use_line for e in ddg.ddg_edges if e.variable == "k" and e.def_line == 6} == {3}


# An EXPRESSION-bodied closure reads late too (WI-pubat). ``() => x`` has no
# statement in its body, so the region the builder hung off its definition held
# nothing and no late edge could reach its read; the block-bodied ``() => {
# return x; }`` was followed. Each form: (language, file, source, the line of
# the later definition, the line of the closure's read).
_EXPRESSION_BODIES = {
    "javascript-arrow": ("javascript", "app.js", (
        "function f() {\n"
        "  let x = '';\n"
        "  const get = () => x;\n"
        "  x = process.env.K;\n"
        "  return get;\n"
        "}\n"
    ), 4, 3),
    "typescript-arrow": ("typescript", "app.ts", (
        "export function f(): () => string {\n"
        "  let x: string = '';\n"
        "  const get = (): string => x;\n"
        "  x = process.env.K ?? '';\n"
        "  return get;\n"
        "}\n"
    ), 4, 3),
    "python-lambda": ("python", "app.py", (
        "import os\n"
        "def f():\n"
        "    x = ''\n"
        "    get = lambda: x\n"
        "    x = os.environ['K']\n"
        "    return get\n"
    ), 5, 4),
    "rust-closure": ("rust", "lib.rs", (
        "pub fn f() -> String {\n"
        "    let mut x = String::new();\n"
        "    let get = || x;\n"
        "    x = std::env::var(\"K\").unwrap();\n"
        "    get()\n"
        "}\n"
    ), 4, 3),
    "java-lambda": ("java", "A.java", (
        "class A {\n"
        "  Supplier<String> f() {\n"
        "    String x = \"\";\n"
        "    Supplier<String> get = () -> x;\n"
        "    x = System.getenv(\"K\");\n"
        "    return get;\n"
        "  }\n"
        "}\n"
    ), 5, 4),
}


@pytest.mark.parametrize("form", sorted(_EXPRESSION_BODIES))
def test_an_expression_bodied_closure_reads_a_later_definition(
    tmp_path: Path, form: str,
) -> None:
    """The definition after the closure gains an edge into its read."""
    language, name, text, def_line, read_line = _EXPRESSION_BODIES[form]
    (tmp_path / name).write_text(text)
    dataflow_scope.ensure_def_use_extractors_registered()
    ddg = build_repo_ddg(tmp_path, languages=(language,))
    late = {e.use_line for e in ddg.ddg_edges if e.variable == "x" and e.def_line == def_line}
    assert read_line in late


_FS_CLAIMS = '''claims:
  - id: SECRET-FS
    text: Secrets never reach the file system.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: host_fs
'''

# The filed repro and the forms whose walk REMOVED or withheld the flow:
# the secret's only other use terminates (``console.log`` / ``print``), so
# without the late edge the walk never sees the closure hand it to the sink.
_EXPRESSION_BODY_WRITES = {
    "javascript-arrow": ("app.js", (
        "const fs = require('fs');\n"
        "function f() {\n"
        "  let x = '';\n"
        "  const get = () => %s;\n"
        "  x = process.env.API_KEY;\n"
        "  console.log(x);\n"
        "  fs.writeFileSync('o.txt', get());\n"
        "}\n"
        "module.exports = { f };\n"
    )),
    "typescript-arrow": ("app.ts", (
        "import * as fs from 'fs';\n"
        "export function f(): void {\n"
        "  let x: string = '';\n"
        "  const get = (): string => %s;\n"
        "  x = process.env.API_KEY ?? '';\n"
        "  console.log(x);\n"
        "  fs.writeFileSync('o.txt', get());\n"
        "}\n"
    )),
    "python-lambda": ("app.py", (
        "import os\n"
        "def f():\n"
        "    x = ''\n"
        "    get = lambda: %s\n"
        "    x = os.environ['API_KEY']\n"
        "    print(x)\n"
        "    open('o.txt', 'w').write(get())\n"
    )),
}


def _fs_verdict(tmp_path: Path, name: str, text: str) -> dict[str, Any]:
    """Run the shipped CLI on one file and return the SECRET-FS verdict."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / name).write_text(text)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_FS_CLAIMS)
    mp = pytest.MonkeyPatch()
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(io.StringIO()):
            main(["verify-claims", str(repo), "--claims", str(claims),
                  "--format", "json"])
    finally:
        mp.undo()
    verdicts = {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}
    return verdicts["SECRET-FS"]


@pytest.mark.parametrize("form", sorted(_EXPRESSION_BODY_WRITES))
def test_an_expression_bodied_closure_carries_a_later_secret_to_the_sink(
    tmp_path: Path, form: str,
) -> None:
    """The secret is stored into ``x`` after the closure is made and the
    closure's result is written: violated. It read inconclusive (the capture
    rule withheld the removal) and, before that rule, confirmed."""
    name, text = _EXPRESSION_BODY_WRITES[form]
    verdict = _fs_verdict(tmp_path, name, text % "x")
    assert verdict["verdict"] == "violated"


@pytest.mark.parametrize("form", sorted(_EXPRESSION_BODY_WRITES))
def test_an_expression_bodied_closure_that_reads_no_secret_lets_the_walk_remove_it(
    tmp_path: Path, form: str,
) -> None:
    """The control: the closure returns a literal and mentions no ``x``, so the
    secret dies at the terminating print and the walk still removes the flow."""
    name, text = _EXPRESSION_BODY_WRITES[form]
    verdict = _fs_verdict(tmp_path, name, text % "'y'")
    # TypeScript reads ``confirmed_with_caveats``: its analyzer emits no call
    # edge for an external instance-method call, a caveat unrelated to closures.
    assert verdict["verdict"].startswith("confirmed")
    assert verdict["evidence"] == []
