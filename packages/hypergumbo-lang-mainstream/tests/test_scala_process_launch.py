# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-narij: a scala process launch reaches its catalogue row.

``Process(cmd).!`` is the most common way to launch a process in scala. It is
companion-apply sugar for ``scala.sys.process.Process.apply(cmd)``, and the
scala analyzer emits it under the name ``Process``. The catalogue rowed only
``apply``, so this spelling was never classified. On sbt, 6 of 6 explicit-import
launches classified as nothing. A ``{boundary: subprocess, must_not_exist: true}``
claim therefore found no chain on a program that launches processes.

Every assertion goes through the production path: ``analyze_scala`` on a real
source file, then ``classify_call`` with the edge's ``dst_ref``, as verify-claims
calls it. REACH comes first: a fixture that emitted no edge would pass every
negative control vacuously.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog
from hypergumbo_lang_mainstream.scala import analyze_scala

_MODULE = "scala.sys.process.Process"


def _classified(tmp_path: Path, source: str) -> dict[str, "str | None"]:
    """``{callee name: "module.name" or None}`` for every unresolved call edge."""
    (tmp_path / "A.scala").write_text(source)
    catalog = {"scala": load_catalog("scala")}
    out: dict[str, "str | None"] = {}
    for edge in analyze_scala(tmp_path).edges:
        if edge.edge_type != "calls" or edge.is_resolved:
            continue
        prim = classify_call(catalog, edge.dst, edge.meta, dst_ref=edge.dst_ref)
        out[edge.dst.split(":")[-2]] = None if prim is None else f"{prim.module}.{prim.name}"
    return out


@pytest.mark.parametrize("imports", [
    "import scala.sys.process.Process",
    "import scala.sys.process._",
    "import scala.sys.process.*",
], ids=["explicit", "wildcard-2", "wildcard-3"])
def test_the_companion_sugar_is_a_launch(tmp_path: Path, imports: str) -> None:
    got = _classified(tmp_path, f"""{imports}

object A {{
  def run(c: String): Int = Process(c).!
  def control(p: String): Unit = {{ val f = new java.io.File(p); f.delete() }}
}}
""")
    assert got.get("delete") == "java.io.File.delete", got  # reach
    assert got.get("Process") == f"{_MODULE}.Process", got


def test_the_launch_crosses_both_boundaries() -> None:
    """The fs_write and subprocess rows are ``simultaneous``. A launch that
    tagged only fs_write was INV-zumin's defect: the subprocess claim found no
    chain."""
    assert load_catalog("scala").simultaneous_boundaries_for(f"{_MODULE}.Process") == {
        "fs_write", "subprocess"}


def test_the_explicit_apply_still_classifies(tmp_path: Path) -> None:
    got = _classified(tmp_path, """import scala.sys.process.Process

object A { def run(c: String): Unit = { Process.apply(c).run() } }
""")
    assert got.get("apply") == f"{_MODULE}.apply", got


def test_a_project_process_is_not_a_launch(tmp_path: Path) -> None:
    """A project type named Process resolves in-repo, so it never reaches the
    catalogue. An unrelated bare ``apply`` does not match the companion row: it
    needs module context."""
    got = _classified(tmp_path, """case class Process(name: String)

object A {
  def make(c: String): Process = Process(c)
  def look(m: Map[String, Int]): Int = m.apply("x")
  def bare(): Unit = apply()
  def control(p: String): Unit = { val f = new java.io.File(p); f.delete() }
}
""")
    assert got.get("delete") == "java.io.File.delete", got  # reach
    assert "apply" in got, got  # reach: the bare apply was emitted
    assert "Process" not in got, got
    assert got["apply"] is None, got
