# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jubim: kotlin and scala type a receiver from what a JDK call RETURNS.

``Runtime.getRuntime().exec(c)``: ``exec`` is called on the VALUE
``getRuntime`` returns, and ``java.lang.Runtime.exec`` is a method-kind
``subprocess`` row, so it needs a typed receiver. Java reads that type from
``library_signatures/java.yaml`` (WI-lalot, WI-halin). Kotlin and scala read no
row at all, so before this change both emitted ``<lang>:external:0-0:exec``,
the subprocess sink was unreachable, and a secret piped into the command was
reported ``inconclusive`` rather than ``violated``. Reproduced before the fix on
``~/hypergumbo_lab_notebook/suril_09072026/fxkt`` and ``fxsc``.

The rows are java's own, read through ``library_signatures.ROW_PARENTS``: no
row is copied into a kotlin or scala file.

Every assertion goes through the production path: the analyzer on a real
source file, then ``classify_call`` with the edge's ``dst_ref`` as
``verify_claims`` calls it, and at the end ``verify-claims`` itself. REACH is
asserted first in each fixture, because a fixture that emitted no ``exec``
edge would pass every "stays untyped" control vacuously.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog

_EXT = {"kotlin": "kt", "scala": "scala"}


def _calls(tmp_path: Path, lang: str, source: str, *,
           resolved: bool = False) -> list[object]:
    """The unresolved call edges of one fixture (every call edge with
    ``resolved``), in source order."""
    if lang == "kotlin":
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin as analyze
    else:
        from hypergumbo_lang_mainstream.scala import analyze_scala as analyze
    (tmp_path / f"A.{_EXT[lang]}").write_text(source)
    edges = [e for e in analyze(tmp_path).edges
             if e.edge_type == "calls" and (resolved or not e.is_resolved)]
    return sorted(edges, key=lambda e: e.line)


def _named(edges: list[object], name: str) -> list[object]:
    found = [e for e in edges if e.dst.split(":")[-2] == name]
    assert found, f"reach: no {name!r} edge in {[e.dst for e in edges]}"
    return found


def _slot(edge) -> str:
    return edge.dst.split(":")[1]


def _classified(lang: str, edge) -> str | None:
    prim = classify_call({lang: load_catalog(lang)}, edge.dst, edge.meta,
                         dst_ref=edge.dst_ref)
    return None if prim is None else f"{prim.module}.{prim.name} {prim.boundary}"


# --- the shapes the rows now type, one fixture per shape -------------------

_KOTLIN_SHAPES = {
    "chained": 'fun f(c: String) {\n    Runtime.getRuntime().exec(c)\n}\n',
    "bound": (
        'fun f(c: String) {\n    val r = Runtime.getRuntime()\n    r.exec(c)\n}\n'),
    "inline": 'fun f(c: String) {\n    java.lang.Runtime.getRuntime().exec(c)\n}\n',
}

_SCALA_SHAPES = {
    "chained": (
        'object A {\n  def f(c: String): Unit = {\n'
        '    Runtime.getRuntime().exec(c)\n  }\n}\n'),
    "bound": (
        'object A {\n  def f(c: String): Unit = {\n'
        '    val r = Runtime.getRuntime()\n    r.exec(c)\n  }\n}\n'),
    # scala calls a parameterless method without parentheses
    "parameterless": (
        'object A {\n  def f(c: String): Unit = {\n'
        '    Runtime.getRuntime.exec(c)\n  }\n}\n'),
}

_SHAPES = ([("kotlin", k, v) for k, v in _KOTLIN_SHAPES.items()]
           + [("scala", k, v) for k, v in _SCALA_SHAPES.items()])


@pytest.mark.parametrize("lang,shape,source", _SHAPES,
                         ids=[f"{lang}-{shape}" for lang, shape, _ in _SHAPES])
def test_exec_on_getRuntime_reaches_the_subprocess_row(
    tmp_path: Path, lang: str, shape: str, source: str,
) -> None:
    (edge,) = _named(_calls(tmp_path, lang, source), "exec")
    assert _slot(edge) == "java.lang.Runtime"
    assert edge.dst_ref is not None and edge.dst_ref.module_path == "java.lang.Runtime"
    assert (edge.meta or {}).get("call_construct") == "method"
    assert _classified(lang, edge) == "java.lang.Runtime.exec subprocess"


_KOTLIN_TWO_LINKS = '''\
import java.net.Socket
import java.sql.DriverManager

fun chained(c: String) {
    Runtime.getRuntime().exec(c).waitFor()
}

fun bound(c: String) {
    val p = Runtime.getRuntime().exec(c)
    p.destroy()
}

fun viaLocal(c: String) {
    val r = Runtime.getRuntime()
    r.exec(c).exitValue()
}

fun imported(u: String, s: String) {
    DriverManager.getConnection(u).prepareStatement(s)
}

fun constructed(h: String, b: ByteArray) {
    Socket(h, 80).getOutputStream().write(b)
}
'''

_SCALA_TWO_LINKS = '''\
import java.net.Socket
import java.sql.DriverManager

object A {
  def chained(c: String): Unit = { Runtime.getRuntime.exec(c).waitFor() }
  def bound(c: String): Unit = {
    val p = Runtime.getRuntime().exec(c)
    p.destroy()
  }
  def viaLocal(c: String): Unit = {
    val r = Runtime.getRuntime()
    r.exec(c).exitValue()
  }
  def imported(u: String, s: String): Unit = {
    DriverManager.getConnection(u).prepareStatement(s)
  }
  def constructed(h: String, b: Array[Byte]): Unit = {
    new Socket(h, 80).getOutputStream().write(b)
  }
}
'''


@pytest.mark.parametrize("lang,source", [("kotlin", _KOTLIN_TWO_LINKS),
                                         ("scala", _SCALA_TWO_LINKS)],
                         ids=["kotlin", "scala"])
class TestEachLinkTypesTheNext:
    """The answer for one link is the owner the next link is looked up on."""

    def test_a_chain_through_exec(self, tmp_path: Path, lang: str, source: str) -> None:
        (edge,) = _named(_calls(tmp_path, lang, source), "waitFor")
        assert _slot(edge) == "java.lang.Process"

    def test_a_local_bound_to_a_two_link_chain(
        self, tmp_path: Path, lang: str, source: str,
    ) -> None:
        (edge,) = _named(_calls(tmp_path, lang, source), "destroy")
        assert _slot(edge) == "java.lang.Process"

    def test_a_chain_rooted_at_a_typed_local(
        self, tmp_path: Path, lang: str, source: str,
    ) -> None:
        (edge,) = _named(_calls(tmp_path, lang, source), "exitValue")
        assert _slot(edge) == "java.lang.Process"

    def test_an_explicitly_imported_owner(self, tmp_path: Path, lang: str, source: str) -> None:
        (edge,) = _named(_calls(tmp_path, lang, source), "prepareStatement")
        assert _slot(edge) == "java.sql.Connection"
        assert _classified(lang, edge) == "java.sql.Connection.prepareStatement db_read"

    def test_a_constructor_rooted_chain(self, tmp_path: Path, lang: str, source: str) -> None:
        (edge,) = _named(_calls(tmp_path, lang, source), "write")
        assert _slot(edge) == "java.io.OutputStream"


# --- controls: what the rows must NOT type ------------------------------------

#: A project that defines its OWN ``Runtime``: that type wins over java.lang's,
#: so its ``getRuntime`` is not the JDK's and ``exec`` is not a launch.
_KOTLIN_OWN_RUNTIME = '''\
class Runtime {
    fun exec(c: String): Int = 1
    companion object {
        fun getRuntime(): Runtime = Runtime()
    }
}

fun f(c: String) {
    Runtime.getRuntime().exec(c)
}
'''

_SCALA_OWN_RUNTIME = '''\
class Runtime {
  def exec(c: String): Int = 1
}
object Runtime {
  def getRuntime(): Runtime = new Runtime
}
object A {
  def f(c: String): Unit = { Runtime.getRuntime().exec(c) }
}
'''


@pytest.mark.parametrize("lang,source", [("kotlin", _KOTLIN_OWN_RUNTIME),
                                         ("scala", _SCALA_OWN_RUNTIME)],
                         ids=["kotlin", "scala"])
def test_a_project_runtime_is_not_the_jdks(tmp_path: Path, lang: str, source: str) -> None:
    every = _calls(tmp_path, lang, source, resolved=True)
    assert any(e.dst.split(":")[-2] in ("exec", "Runtime.exec") for e in every), (
        f"reach: the exec call must still be emitted: {[e.dst for e in every]}")
    for edge in every:
        assert _slot(edge) != "java.lang.Runtime", edge.dst
        if not edge.is_resolved:
            assert _classified(lang, edge) is None, edge.dst


_KOTLIN_UNKNOWN = '''\
fun unknownLink(c: String) {
    Runtime.getRuntime().nope().exec(c)
}

fun untypedRoot(q: Any, c: String) {
    q.getRuntime().exec(c)
}

fun scoped(c: String) {
    val r = Runtime.getRuntime()
}

fun other(c: String) {
    val r = make()
    r.exec(c)
}

class H {
    val rt = 1
    fun valueChain(c: String) {
        this.rt.getRuntime().exec(c)
    }
}

fun functionRoot(c: String) {
    make().getRuntime().exec(c)
    make()().getRuntime().exec(c)
    "ls".trim().exec(c)
}
'''

_SCALA_UNKNOWN = '''\
object A {
  def unknownLink(c: String): Unit = { Runtime.getRuntime().nope().exec(c) }
  def untypedRoot(q: Any, c: String): Unit = { q.getRuntime().exec(c) }
  def scoped(c: String): Unit = { val r = Runtime.getRuntime() }
  def other(c: String): Unit = {
    val r = make()
    r.exec(c)
  }
}
'''


@pytest.mark.parametrize("lang,source", [("kotlin", _KOTLIN_UNKNOWN),
                                         ("scala", _SCALA_UNKNOWN)],
                         ids=["kotlin", "scala"])
def test_a_link_no_row_answers_ends_the_chain(tmp_path: Path, lang: str, source: str) -> None:
    """A link the rows do not know, an untyped root, a ``val r`` bound in
    ANOTHER function, and (kotlin) a chain rooted at a value path, a function
    call or a literal leave every ``exec`` untyped."""
    edges = _calls(tmp_path, lang, source)
    execs = _named(edges, "exec")
    sites = source.count(".exec(")
    assert len(execs) == sites, [e.dst for e in execs]  # reach: every site emitted
    assert [_slot(e) for e in execs] == ["external"] * sites
    # control on the same fixture: the link before the unknown one IS typed
    (nope,) = _named(edges, "nope")
    assert _slot(nope) == "java.lang.Runtime"


# --- the behavioral claim: a secret reaching the launched command -------------

_CLAIMS = '''claims:
  - id: SECRET-NOT-EXEC
    text: A secret never reaches a launched command.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: subprocess
  - id: NO-SUBPROCESS
    text: No subprocess.
    constraint:
      boundary: subprocess
      must_not_exist: true
'''

_KOTLIN_SECRET = {
    "chained": ('fun go() {\n    val cmd = System.getenv("CMD")\n'
                '    Runtime.getRuntime().exec(cmd)\n}\n'),
    "bound": ('fun go() {\n    val cmd = System.getenv("CMD")\n'
              '    val r = Runtime.getRuntime()\n    r.exec(cmd)\n}\n'),
}

_SCALA_SECRET = {
    "chained": ('object A {\n  def go(): Unit = {\n    val cmd = System.getenv("CMD")\n'
                '    Runtime.getRuntime().exec(cmd)\n  }\n}\n'),
    "bound": ('object A {\n  def go(): Unit = {\n    val cmd = System.getenv("CMD")\n'
              '    val r = Runtime.getRuntime()\n    r.exec(cmd)\n  }\n}\n'),
}

#: The secret is read in one function and a constant command launched in
#: another that never sees it: a launch exists, a flow does not.
_KOTLIN_APART = '''\
fun readIt() {
    val cmd = System.getenv("CMD")
    println(cmd)
}

fun launch() {
    Runtime.getRuntime().exec("ls")
}
'''

_SCALA_APART = '''\
object A {
  def readIt(): Unit = {
    val cmd = System.getenv("CMD")
    println(cmd)
  }
  def launch(): Unit = { Runtime.getRuntime().exec("ls") }
}
'''


def _verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lang: str,
              source: str) -> dict[str, str]:
    from hypergumbo_core.cli import main

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / f"A.{_EXT[lang]}").write_text(source)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    return {v["claim_id"]: v["verdict"] for v in json.loads(buf.getvalue())["verdicts"]}


_SECRET_CASES = ([("kotlin", k, v) for k, v in _KOTLIN_SECRET.items()]
                 + [("scala", k, v) for k, v in _SCALA_SECRET.items()])


@pytest.mark.parametrize("lang,shape,source", _SECRET_CASES,
                         ids=[f"{lang}-{shape}" for lang, shape, _ in _SECRET_CASES])
def test_a_secret_piped_into_exec_is_violated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lang: str, shape: str, source: str,
) -> None:
    verdicts = _verdicts(tmp_path, monkeypatch, lang, source)
    assert verdicts["NO-SUBPROCESS"] == "violated"  # reach: the sink is seen
    assert verdicts["SECRET-NOT-EXEC"] == "violated"


@pytest.mark.parametrize("lang,source", [("kotlin", _KOTLIN_APART),
                                         ("scala", _SCALA_APART)],
                         ids=["kotlin", "scala"])
def test_a_launch_the_secret_never_reaches_is_not_a_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lang: str, source: str,
) -> None:
    verdicts = _verdicts(tmp_path, monkeypatch, lang, source)
    assert verdicts["NO-SUBPROCESS"] == "violated"  # reach: the launch is seen
    assert verdicts["SECRET-NOT-EXEC"] != "violated"
