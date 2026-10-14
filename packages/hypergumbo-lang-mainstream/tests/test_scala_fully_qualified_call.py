# SPDX-License-Identifier: AGPL-3.0-or-later
"""A fully-qualified Scala call names its module (INV-vokut).

``scala.io.StdIn.readLine()`` written inline, with no import, emitted
``scala:external:0-0:readLine``: no module slot. ``readLine`` is in scala.yaml's
``ambiguous_names``, so the no-module gate withheld it and the stdin reader's
``ipc_recv`` row was present and unreachable. The imported forms reached it
(``import scala.io.StdIn`` then ``StdIn.readLine()``). The same inline shape
lost ``scala.sys.process.Process(cmd)`` (a launch) and
``java.nio.file.Files.readAllBytes(p)``.

The receiver branch took the method name from the call's own identifier and
the receiver from a sibling identifier. An inline path parses as nested
``field_expression`` nodes, so there was no sibling identifier and the path was
dropped.

THE RULE, and why it is narrow. A receiver that is a pure dotted path of
identifiers is its own qualification, as WI-pokam already rules for a type
written inline (``val b: java.io.File``): the file states the path at the use
site. It is applied only when the path's ROOT is a JDK or Scala standard root
(``scala``, ``java``, ``javax``) and the file does not bind that root name (a
local, parameter or import). A chain on a local value
(``cfg.inner.Target.go()``) is not a package path, and naming it as one would
put a non-module in the slot, which the coverage gate would then report as an
unexamined module. A third-party fully-qualified call (``org.x.Y.f()``) keeps
the sentinel: no shipped Scala row could match it, and filling the slot there
would only add a gate entry.

A capitalised callee on an all-lowercase package path is a companion apply
(``scala.sys.process.Process(cmd)``) and gets the slot the imported form gets,
``scala.sys.process.Process``.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_FQ = '''object Fq {
  def line(): String = scala.io.StdIn.readLine()
  def launch(s: String): Unit = { scala.sys.process.Process(s).run() }
  def read(p: String): Array[Byte] =
    java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(p))
  def local(): Unit = { val cfg = new Holder(); cfg.inner.Target.go() }
  def vendor(): Unit = { org.example.Sdk.push() }
}
class Holder { val inner = this }
'''


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, dict]:
    root = tmp_path_factory.mktemp("vokut")
    repo = root / "repo"
    repo.mkdir()
    (repo / "Fq.scala").write_text(_FQ)
    mp = pytest.MonkeyPatch()
    mp.setenv("XDG_CACHE_HOME", str(root / "cache"))
    survey = root / "s.json"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            main(["survey", str(repo), "--out", str(survey)])
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            main(["io-boundaries", str(repo), "--format", "json", "--include-tests"])
    finally:
        mp.undo()
    calls: dict[str, set[str]] = {}
    for e in json.loads(survey.read_text())["edges"]:
        if e["type"] == "calls":
            calls.setdefault(e["src"].split(":")[-2], set()).add(e["dst"])
    chains: dict[str, set[str]] = {}
    for b, v in json.loads(buf.getvalue())["boundaries"].items():
        for c in v["chains"]:
            chains.setdefault(b, set()).add(c["primitive"])
    return calls, chains


def test_the_stdin_reader_reaches_its_row(results: tuple[dict, dict]) -> None:
    calls, chains = results
    assert "scala:scala.io.StdIn:0-0:readLine:external_symbol" in calls["Fq.line"]
    assert "scala.io.StdIn.readLine" in chains["ipc_recv"]


def test_an_inline_companion_apply_is_the_launch(results: tuple[dict, dict]) -> None:
    calls, chains = results
    assert "scala:scala.sys.process.Process:0-0:Process:external_symbol" in calls["Fq.launch"]
    assert "scala.sys.process.Process.Process" in chains["subprocess"]


def test_an_inline_jdk_call_names_its_class(results: tuple[dict, dict]) -> None:
    calls, chains = results
    assert "scala:java.nio.file.Files:0-0:readAllBytes:external_symbol" in calls["Fq.read"]
    assert "java.nio.file.Files.readAllBytes" in chains["fs_read"]


def test_a_chain_on_a_local_value_keeps_the_sentinel(results: tuple[dict, dict]) -> None:
    calls, _ = results
    assert "scala:external:0-0:go:external_symbol" in calls["Fq.local"]


def test_a_third_party_path_keeps_the_sentinel(results: tuple[dict, dict]) -> None:
    calls, _ = results
    assert "scala:external:0-0:push:external_symbol" in calls["Fq.vendor"]


def _receiver(text: str):
    import tree_sitter
    import tree_sitter_scala

    source = text.encode()
    tree = tree_sitter.Parser(tree_sitter.Language(tree_sitter_scala.language())).parse(source)
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "call_expression":
            return node.child_by_field_name("function").child_by_field_name("value"), source
        stack.extend(node.children)
    raise AssertionError("no call")  # pragma: no cover


@pytest.mark.parametrize(("text", "expected"), [
    ("object A { def f() = a.b.c() }", "a.b"),
    ("object A { def f() = g(1).h.k() }", None),     # a call in the chain: a value
    ("object A { def f() = a.+.b() }", None),        # an operator field: not a path
])
def test_only_a_chain_of_identifiers_is_a_path(text: str, expected: str | None) -> None:
    from hypergumbo_lang_mainstream.scala import _inline_path

    value, source = _receiver(text)
    assert _inline_path(value, source) == expected


def test_a_root_the_file_binds_is_not_a_package() -> None:
    """``val java = ...; java.util.X.f()`` would name the local, not the JDK."""
    from hypergumbo_lang_mainstream.scala import _inline_qualified_owner

    assert _inline_qualified_owner("java.util.X", "f", bound={"java"}) is None
    assert _inline_qualified_owner("java.util.X", "f", bound=set()) == "java.util.X"


@pytest.mark.parametrize(("path", "callee", "expected"), [
    ("scala.io.StdIn", "readLine", "scala.io.StdIn"),
    ("scala.math", "max", "scala.math"),                    # a package object
    ("scala.sys.process", "Process", "scala.sys.process.Process"),  # companion apply
    ("java.util.Map.Entry", "comparingByKey", "java.util.Map.Entry"),  # nested type
    ("scala.Console.err", "println", None),                 # a member value, measured on sbt
    ("org.example.Sdk", "push", None),                      # third-party root
])
def test_only_a_package_then_types_is_a_module(
    path: str, callee: str, expected: str | None,
) -> None:
    from hypergumbo_lang_mainstream.scala import _inline_qualified_owner

    assert _inline_qualified_owner(path, callee, bound=set()) == expected
