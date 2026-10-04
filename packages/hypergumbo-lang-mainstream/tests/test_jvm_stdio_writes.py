# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-rabum: a kotlin or scala write to ``System.out`` / ``System.err`` is seen.

java emits TWO records for ``System.err.println(k)`` and kotlin and scala
emitted neither:

* a ``module_attr_ref`` for ``System.err`` (``emit_module_attribute_refs``),
  which ``java.lang.System``'s ``attributes: [out, err]`` logging row matches
  (WI-runos, audit-findings 0021: the process's own stdout/stderr is
  ``logging``), carrying the call that uses the stream (INV-hopib's
  ``attr_carrier``); and
* the ``println`` call typed ``java.io.PrintStream`` and stamped
  ``io_target_kind: std_stream``, which java.yaml's PrintStream rows require
  (``requires_target_kind: std_stream``, WI-dorus).

Both catalogue rows reach kotlin and scala through ``_CATALOG_PARENTS``; the
records did not. Measured before this change (dev 2a98abb55d): kotlin and scala
emitted only ``<lang>:external:0-0:println`` and a ``host_secret -> logging``
claim read ``confirmed_with_caveats`` (``untyped_receiver``), where java's reads
``violated``.

Every assertion goes through the production analyzer on a real source file;
the verdict tests run ``verify-claims`` itself. REACH is asserted before every
control: a control fixture whose secret read emitted nothing would pass
vacuously.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog

_EXT = {"kotlin": "kt", "scala": "scala", "java": "java"}


def _edges(tmp_path: Path, lang: str, source: str) -> list[object]:
    if lang == "kotlin":
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin as analyze
    elif lang == "scala":
        from hypergumbo_lang_mainstream.scala import analyze_scala as analyze
    else:
        from hypergumbo_lang_mainstream.java import analyze_java as analyze
    (tmp_path / f"Main.{_EXT[lang]}").write_text(source)
    return sorted(analyze(tmp_path).edges, key=lambda e: e.line)


def _attr_refs(edges: list[object]) -> list[object]:
    return [e for e in edges if e.edge_type == "module_attr_ref"]


def _calls_named(edges: list[object], name: str) -> list[object]:
    # The name slot's LAST component: a call resolved into the project is
    # named ``Sink.println`` / ``out.println`` there.
    found = [e for e in edges if e.edge_type == "calls"
             and e.dst.split(":")[-2].rsplit(".", 1)[-1] == name]
    assert found, f"reach: no {name!r} call in {[e.dst for e in edges]}"
    return found


def _classified(lang: str, edge) -> str | None:
    prim = classify_call({lang: load_catalog(lang)}, edge.dst, edge.meta,
                         dst_ref=edge.dst_ref)
    return None if prim is None else f"{prim.module}.{prim.name} {prim.boundary}"


def _kt(body: str, *, before: str = "") -> str:
    return f"{before}fun go(k: String) {{\n{body}}}\n"


def _sc(body: str, *, before: str = "") -> str:
    return (f"{before}object Main {{\n  def go(k: String): Unit = {{\n"
            f"{body}  }}\n}}\n")


# --- the two records, per stream and per spelling --------------------------

_WRITES = [
    ("kotlin", "err", "println", _kt("    System.err.println(k)\n")),
    ("kotlin", "out", "print", _kt("    System.out.print(k)\n")),
    ("kotlin", "out", "println", _kt("    java.lang.System.out.println(k)\n")),
    ("scala", "err", "println", _sc("    System.err.println(k)\n")),
    ("scala", "out", "print", _sc("    System.out.print(k)\n")),
    ("scala", "out", "println", _sc("    java.lang.System.out.println(k)\n")),
]


@pytest.mark.parametrize(
    "lang,stream,method,source", _WRITES,
    ids=[f"{w[0]}-{w[1]}-{w[2]}-{i}" for i, w in enumerate(_WRITES)])
def test_the_write_is_a_typed_std_stream_call(
    tmp_path: Path, lang: str, stream: str, method: str, source: str,
) -> None:
    (call,) = _calls_named(_edges(tmp_path, lang, source), method)
    assert call.dst == f"{lang}:java.io.PrintStream:0-0:{method}:unresolved"
    assert call.dst_ref is not None
    assert call.dst_ref.module_path == "java.io.PrintStream"
    meta = call.meta or {}
    assert meta.get("io_target_kind") == "std_stream"
    assert meta.get("receiver_type_hint") == "java.io.PrintStream"
    assert meta.get("call_construct") == "method"
    assert _classified(lang, call) == f"java.io.PrintStream.{method} logging"


@pytest.mark.parametrize(
    "lang,stream,method,source", _WRITES,
    ids=[f"{w[0]}-{w[1]}-{w[2]}-{i}" for i, w in enumerate(_WRITES)])
def test_the_stream_is_a_module_attr_ref_carried_by_the_call(
    tmp_path: Path, lang: str, stream: str, method: str, source: str,
) -> None:
    edges = _edges(tmp_path, lang, source)
    (ref,) = _attr_refs(edges)
    assert ref.dst == (
        f"{lang}:java.lang.System:0-0:java.lang.System.{stream}:attribute")
    assert ref.evidence_type == "module_attribute_reference"
    # INV-fafol: anchored on the callable that performs the read.
    assert ref.src.split(":")[-2].rsplit(".", 1)[-1] == "go", ref.src
    callee = "java.lang.System" if "java.lang" in source else "System"
    assert (ref.meta or {}).get("attr_carrier") == (
        f"{callee}.{stream}.{method}@{ref.line}")
    assert _classified(lang, ref) == f"java.lang.System.{stream} logging"


_HANDED_ON = [
    ("kotlin", _kt("    val w = java.io.PrintWriter(System.out)\n"
                   "    w.println(k)\n")),
    ("scala", _sc("    val w = new java.io.PrintWriter(System.out)\n"
                  "    w.println(k)\n")),
]


@pytest.mark.parametrize("lang,source", _HANDED_ON, ids=["kotlin", "scala"])
def test_a_stream_handed_to_a_call_names_that_call(
    tmp_path: Path, lang: str, source: str,
) -> None:
    (ref,) = _attr_refs(_edges(tmp_path, lang, source))
    assert ref.dst.endswith(":java.lang.System.out:attribute")
    carrier = (ref.meta or {}).get("attr_carrier")
    assert carrier is not None and carrier.endswith(f"PrintWriter@{ref.line}")


_STDIN = [
    ("kotlin", _kt("    val r = System.`in`\n")),
    ("scala", _sc("    val r = System.in\n")),
]


@pytest.mark.parametrize("lang,source", _STDIN, ids=["kotlin", "scala"])
def test_stdin_is_the_same_mechanism(tmp_path: Path, lang: str, source: str) -> None:
    (ref,) = _attr_refs(_edges(tmp_path, lang, source))
    assert ref.dst == f"{lang}:java.lang.System:0-0:java.lang.System.in:attribute"
    assert "carrier" not in str(ref.meta)
    assert _classified(lang, ref) == "java.lang.System.in ipc_recv"


# --- controls: nothing here is the JDK's System ------------------------------

#: A user object named ``out``, a local named ``out`` and a project ``System``
#: with its own ``out``. Each still makes a ``println`` call (asserted first).
_NOT_STDIO = [
    ("kotlin", "object", _kt(
        "    out.println(k)\n",
        before="object out {\n    fun println(x: String) {}\n}\n\n")),
    ("kotlin", "local", _kt(
        "    val out = Sink()\n    out.println(k)\n",
        before="class Sink {\n    fun println(x: String) {}\n}\n\n")),
    ("kotlin", "project-System", _kt(
        "    System.out.println(k)\n",
        before=("object System {\n    val out = Sink()\n}\n"
                "class Sink {\n    fun println(x: String) {}\n}\n\n"))),
    ("kotlin", "imported-System", _kt(
        "    System.out.println(k)\n", before="import acme.io.System\n\n")),
    ("scala", "object", _sc(
        "    out.println(k)\n",
        before="object out {\n  def println(x: String): Unit = {}\n}\n\n")),
    ("scala", "local", _sc(
        "    val out = new Sink()\n    out.println(k)\n",
        before="class Sink {\n  def println(x: String): Unit = {}\n}\n\n")),
    ("scala", "project-System", _sc(
        "    System.out.println(k)\n",
        before=("object System {\n  val out = new Sink()\n}\n"
                "class Sink {\n  def println(x: String): Unit = {}\n}\n\n"))),
    ("scala", "imported-System", _sc(
        "    System.out.println(k)\n", before="import acme.io.System\n\n")),
]


@pytest.mark.parametrize("lang,shape,source", _NOT_STDIO,
                         ids=[f"{lang}-{shape}" for lang, shape, _ in _NOT_STDIO])
def test_a_receiver_that_is_not_the_jdks_stream_is_not_typed(
    tmp_path: Path, lang: str, shape: str, source: str,
) -> None:
    edges = _edges(tmp_path, lang, source)
    for call in _calls_named(edges, "println"):  # reach
        assert "java.io.PrintStream" not in call.dst
        assert (call.meta or {}).get("io_target_kind") is None
    assert not [e for e in _attr_refs(edges) if "java.lang.System" in e.dst]


def test_a_scala_parameterless_call_on_system_is_not_an_attribute(
    tmp_path: Path,
) -> None:
    """``System.currentTimeMillis`` is a CALL scala writes without parentheses.

    ``java.lang.System``'s fields are ``in``, ``out`` and ``err``; anything
    else after ``System.`` is a method, and an attribute record for it would
    misstate how it is reached (ADR-0059).
    """
    edges = _edges(tmp_path, "scala", _sc(
        "    val t = System.currentTimeMillis\n    System.err.println(t)\n"))
    assert [e.dst.rsplit(":", 2)[-2] for e in _attr_refs(edges)] == [
        "java.lang.System.err"]


# --- the verdict -------------------------------------------------------------

_CLAIMS = '''\
claims:
  - id: SECRET-NOT-LOGGED
    text: A secret is never logged.
    constraint:
      taint_flow:
        source_taint: host_secret
        prohibited_sink_zone: logging
  - id: NO-LOGGING
    text: Nothing is logged.
    constraint:
      boundary: logging
      must_not_exist: true
'''

_SECRET_WRITES = {
    "kotlin": ('fun main() {\n    val k = System.getenv("API_KEY")\n'
               '    System.err.println(k)\n}\n'),
    "scala": ('object Main {\n  def main(args: Array[String]): Unit = {\n'
              '    val k = System.getenv("API_KEY")\n'
              '    System.err.println(k)\n  }\n}\n'),
    "java": ('public class Main {\n    public static void main(String[] a) {\n'
             '        String k = System.getenv("API_KEY");\n'
             '        System.err.println(k);\n    }\n}\n'),
}

#: The secret reaches a ``println`` on a user object named ``out``.
_SECRET_TO_USER_OUT = {
    "kotlin": ('object out {\n    fun println(x: String?) {}\n}\n\n'
               'fun main() {\n    val k = System.getenv("API_KEY")\n'
               '    out.println(k)\n}\n'),
    "scala": ('object out {\n  def println(x: String): Unit = {}\n}\n\n'
              'object Main {\n  def main(args: Array[String]): Unit = {\n'
              '    val k = System.getenv("API_KEY")\n'
              '    out.println(k)\n  }\n}\n'),
}


def _verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lang: str,
              source: str) -> dict[str, dict]:
    from hypergumbo_core.cli import main

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / f"Main.{_EXT[lang]}").write_text(source)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    return {v["claim_id"]: v for v in json.loads(buf.getvalue())["verdicts"]}


@pytest.mark.parametrize("lang", ["kotlin", "scala", "java"])
def test_a_secret_written_to_stderr_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lang: str,
) -> None:
    verdicts = _verdicts(tmp_path, monkeypatch, lang, _SECRET_WRITES[lang])
    assert verdicts["NO-LOGGING"]["verdict"] == "violated"  # reach: the sink is seen
    taint = verdicts["SECRET-NOT-LOGGED"]
    assert taint["verdict"] == "violated"
    # ONE write, one finding: the stream's own sink is subsumed by the call
    # that carries it (INV-hopib), as in java.
    assert len(taint["evidence"]) == 1


@pytest.mark.parametrize("lang", ["kotlin", "scala"])
def test_a_secret_handed_to_a_user_out_is_not_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lang: str,
) -> None:
    verdicts = _verdicts(tmp_path, monkeypatch, lang, _SECRET_TO_USER_OUT[lang])
    # scala reads ``confirmed_with_caveats`` before and after this change:
    # it does not resolve a call on a lowercase project object, so this
    # ``out.println`` stays an untyped-receiver call. The control is that
    # nothing is a sink.
    assert verdicts["NO-LOGGING"]["verdict"] in ("confirmed", "confirmed_with_caveats")
    assert verdicts["SECRET-NOT-LOGGED"]["verdict"] in (
        "confirmed", "confirmed_with_caveats")
