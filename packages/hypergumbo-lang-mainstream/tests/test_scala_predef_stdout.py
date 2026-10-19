# SPDX-License-Identifier: AGPL-3.0-or-later
"""scala's own stdout writes are logging (WI-tunul).

``println(secret)`` -- Predef's, imported into every compilation unit -- is the
commonest stdout write in scala, and scala.yaml had no stdio row, so a
never-logs-a-secret claim read ``confirmed`` with no caveat. ``scala.Console``
(visible without an import, from ``scala._``) had no row either, and the
analyzer named no owner for ``Console.println(k)``, nor typed
``Console.err.println(k)`` as a write to the process's stream the way
WI-rabum types ``System.err.println(k)``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/scala.py
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog
from hypergumbo_lang_mainstream.scala import analyze_scala

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


def _main(body: str, *, before: str = "") -> str:
    return (f"{before}object Main {{\n  def main(args: Array[String]): Unit = {{\n"
            f"    val k = System.getenv(\"API_KEY\")\n{body}  }}\n}}\n")


def _verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> dict[str, str]:
    from hypergumbo_core.cli import main

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Main.scala").write_text(source)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    return {v["claim_id"]: v["verdict"] for v in json.loads(buf.getvalue())["verdicts"]}


_WRITES = {
    "bare-println": "    println(k)\n",
    "bare-print": "    print(k)\n",
    "bare-printf": "    printf(\"%s\", k)\n",
    "predef": "    Predef.println(k)\n",
    "console": "    Console.println(k)\n",
    "console-err": "    Console.err.println(k)\n",
    "console-out": "    Console.out.print(k)\n",
    "qualified-console": "    scala.Console.println(k)\n",
    "qualified-console-err": "    scala.Console.err.println(k)\n",
}


@pytest.mark.parametrize("shape", list(_WRITES))
def test_a_secret_written_to_stdout_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str,
) -> None:
    verdicts = _verdicts(tmp_path, monkeypatch, _main(_WRITES[shape]))
    assert verdicts["NO-LOGGING"] == "violated"  # reach: the sink is seen
    assert verdicts["SECRET-NOT-LOGGED"] == "violated"


def _classified(edge) -> str | None:
    prim = classify_call({"scala": load_catalog("scala")}, edge.dst, edge.meta,
                         dst_ref=edge.dst_ref)
    return None if prim is None else f"{prim.module}.{prim.name} {prim.boundary}"


def _edges(tmp_path: Path, source: str) -> list:
    (tmp_path / "Main.scala").write_text(source)
    return sorted(analyze_scala(tmp_path).edges, key=lambda e: e.line)


def test_console_err_is_a_typed_std_stream_write(tmp_path: Path) -> None:
    edges = _edges(tmp_path, _main("    Console.err.println(k)\n"))
    (call,) = [e for e in edges if e.edge_type == "calls" and ":println:" in e.dst]
    assert call.dst == "scala:java.io.PrintStream:0-0:println:unresolved"
    assert (call.meta or {}).get("io_target_kind") == "std_stream"


def test_console_streams_read_as_values_are_attribute_refs(tmp_path: Path) -> None:
    edges = _edges(tmp_path, _main("    val o = Console.out\n    val i = Console.in\n"))
    refs = [e for e in edges if e.edge_type == "module_attr_ref"]
    assert [r.dst for r in refs] == [
        "scala:scala.Console:0-0:scala.Console.out:attribute",
        "scala:scala.Console:0-0:scala.Console.in:attribute",
    ]
    assert [_classified(r) for r in refs] == [
        "scala.Console.out logging", "scala.Console.in ipc_recv"]


_NOT_STDOUT = {
    "own-println": (
        "class Sink {\n  def println(x: String): Unit = {}\n"
        "  def go(): Unit = { val k = System.getenv(\"API_KEY\"); println(k) }\n}\n"
    ),
    "imported-Console": _main("    Console.println(k)\n", before="import acme.io.Console\n\n"),
    "project-Console": _main(
        "    Console.println(k)\n",
        before="object Console {\n  def println(x: String): Unit = {}\n}\n\n"),
}


@pytest.mark.parametrize("shape", list(_NOT_STDOUT))
def test_a_println_that_is_not_scalas_is_not_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str,
) -> None:
    """An imported ``acme.io.Console`` is an uncatalogued module, so its claim
    is honestly ``inconclusive``; none of these may read ``violated``."""
    verdicts = _verdicts(tmp_path, monkeypatch, _NOT_STDOUT[shape])
    assert verdicts["SECRET-NOT-LOGGED"] != "violated"
    assert verdicts["NO-LOGGING"] != "violated"
