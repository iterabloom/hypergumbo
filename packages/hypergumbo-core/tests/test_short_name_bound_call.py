# SPDX-License-Identifier: AGPL-3.0-or-later
"""A call the graph binds to an in-repo symbol never takes a row by short name.

WI-vidof. On gatling, ``LogFileReader``'s subclasses call their superclass's
own ``readInt()`` / ``readLong()`` / ``readBoolean()`` / ``readByte()`` as bare
names. The scala analyzer emits each as an unresolved stub
(``scala:external:0-0:readInt:...``) and the inherited-calls linker then
resolves the SAME call to ``LogFileParser.readInt``; finalize demotes the stub
and stamps ``meta.superseded_by`` on it (ADR-0057 §14). The row choice never
read that stamp, so the stub still reached ``scala.io.StdIn.readInt`` through
the no-module gate: 11 false ``ipc_recv`` edges, and as many false
``untrusted_input`` taint sources.

THE RULE IS AT THE ROW-CHOICE LAYER, FOR BOTH CONSUMERS (INV-foda): a stub that
carries ``superseded_by`` is withheld the short-name (gate) arm exactly as a
dst resolved to a first-party callable is. The qualified and module arms are
untouched, so a stub that STATES a module still classifies by it.

THE CONTROL THAT MATTERS: ``import scala.io.StdIn._; readInt()`` emits the
same ``external`` stub with NO superseder. It is a genuine stdin read and must
still classify -- which is the reason ``ambiguous_names`` (the item's first
proposal) was the wrong lever: it cannot tell the two apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import (
    classify_call,
    load_catalog,
    short_name_fallback_withheld,
)
from hypergumbo_core.taint import TaintSource, find_source_callers

_STUB = "scala:external:0-0:readInt:external_symbol"
_SUPERSEDED = {
    "superseded_by": "edge:sha256:3919d38e47744991",
    "superseded_by_origin": ["inherited-calls-linker"],
}


@pytest.fixture(scope="module")
def scala_catalogs():
    return {"scala": load_catalog("scala")}


class TestThePredicate:
    def test_a_superseded_stub_is_withheld(self) -> None:
        assert short_name_fallback_withheld(_STUB, _SUPERSEDED)

    def test_an_unbound_stub_is_not(self) -> None:
        assert not short_name_fallback_withheld(_STUB, {})
        assert not short_name_fallback_withheld(_STUB, None)

    def test_a_first_party_callable_dst_is_withheld(self) -> None:
        assert short_name_fallback_withheld(
            "scala:/repo/P.scala:4-4:Parser.readInt:method", None,
        )


class TestIoBoundaryRowChoice:
    def test_an_unbound_bare_call_still_reaches_the_stdin_row(self, scala_catalogs) -> None:
        """Positive control: ``import scala.io.StdIn._; readInt()``."""
        hit = classify_call(scala_catalogs, _STUB, {})
        assert hit is not None
        assert (hit.module, hit.name, hit.boundary) == (
            "scala.io.StdIn", "readInt", "ipc_recv",
        )

    def test_a_superseded_stub_reaches_no_row(self, scala_catalogs) -> None:
        assert classify_call(scala_catalogs, _STUB, _SUPERSEDED) is None

    def test_a_superseded_stub_stating_its_module_still_classifies(
        self, scala_catalogs,
    ) -> None:
        """Only the SHORT-NAME arm is withheld: a module the stub states is
        evidence the gate never needed."""
        hit = classify_call(
            scala_catalogs, "scala:scala.io.StdIn:0-0:readInt:external_symbol",
            _SUPERSEDED,
        )
        assert hit is not None and hit.module == "scala.io.StdIn"


class TestTaintRowChoice:
    _SOURCE = TaintSource(
        taint_label="untrusted_input", module="scala.io.StdIn",
        name="readInt", kind="function",
    )

    def _edge(self, meta: dict) -> dict:
        return {
            "src": "scala:P.scala:10-10:First.parse:method",
            "dst": _STUB, "type": "calls", "is_resolved": False,
            "meta": dict(meta),
        }

    def test_an_unbound_bare_call_mints_a_source(self) -> None:
        found = find_source_callers(
            [self._edge({})], [self._SOURCE], frozenset(), "scala",
        )
        assert [s.name for _src, _dst, s in found] == ["readInt"]

    def test_a_superseded_stub_mints_none(self) -> None:
        found = find_source_callers(
            [self._edge(_SUPERSEDED)], [self._SOURCE], frozenset(), "scala",
        )
        assert found == []


_SCALA = """import java.io.DataInputStream

abstract class Parser(is: DataInputStream) {
  protected def readInt(): Int = is.readInt()
  def parse(): Int
}

final class First(is: DataInputStream) extends Parser(is) {
  override def parse(): Int = readInt()
}

object UsesStdIn {
  import scala.io.StdIn._
  def ask(): Int = readInt()
}
"""


def test_io_boundaries_keeps_the_stdin_read_and_drops_the_inherited_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through ``io-boundaries`` itself, so the stamp is the one finalize
    writes and not one this file invented."""
    from hypergumbo_core.cli import main

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "P.scala").write_text(_SCALA)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    main(["io-boundaries", str(repo), "--format", "json"])
    report = json.loads(capsys.readouterr().out)
    callers = {
        (b, chain["primitive"], chain["io_edge_src"].split(":")[-2])
        for b, entry in report["boundaries"].items()
        for chain in entry["chains"]
    }
    assert ("ipc_recv", "scala.io.StdIn.readInt", "UsesStdIn.ask") in callers, callers
    assert not any(c[2] == "First.parse" for c in callers), callers
