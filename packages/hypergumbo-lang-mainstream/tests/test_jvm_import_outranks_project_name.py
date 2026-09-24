# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-tipoh: an explicit import outranks a same-named project symbol elsewhere.

The scala and kotlin analyzers resolved a call by the callee's simple name across
the whole project, and the file's own import did not take part. spark's
``TestUtils.scala`` imports ``scala.sys.process.Process``, and its
``Process(cmd).!`` bound to a ``case class Process`` in an unrelated test suite.
scala3's ``TestScripts.scala`` did the same with the sbt build's ``object
Process``. The edge was false, and the launch never reached the catalogue.
kotlin had the same shape: ``import java.io.File`` then ``File("x")`` bound to a
project ``class File``.

Every test runs the production analyzer on real files. REACH comes first: a
fixture that emitted no call edge would pass every negative control.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import classify_call, load_catalog
from hypergumbo_lang_mainstream.jvm_implicit_imports import imported_elsewhere


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def _calls(result, name: str) -> list:
    """Every calls edge whose dst names ``name`` (a project symbol's name may be
    qualified by its type: ``Launcher.start``)."""
    by_id = {s.id for s in result.symbols}
    edges = [e for e in result.edges if e.edge_type == "calls"
             and e.dst.split(":")[-2].split(".")[-1] == name]
    return [(e, e.dst in by_id) for e in edges]


_SCALA_PROJECT_PROCESS = """\
package build

object Process {
  def apply(s: String): Int = 0
}
"""


class TestScala:
    def _analyze(self, tmp_path: Path, caller: str):
        from hypergumbo_lang_mainstream.scala import analyze_scala

        return analyze_scala(_write(tmp_path, {
            "project/Process.scala": _SCALA_PROJECT_PROCESS,
            "bin/TestScripts.scala": caller,
        }))

    def test_an_imported_stdlib_process_is_not_the_project_object(self, tmp_path: Path) -> None:
        """scala3's shape, exactly: a selector import, and a project object named
        Process in the build directory."""
        result = self._analyze(tmp_path, """\
package dotty.tools.scripting

import scala.sys.process.{Process, ProcessLogger}

object TestScripts {
  def run(script: String): Unit = { Process(script).! }
}
""")
        calls = _calls(result, "Process")
        assert calls, "reach: the call must be emitted"
        assert not any(resolved for _, resolved in calls), [e.dst for e, _ in calls]
        (edge, _), = calls
        assert edge.dst == "scala:scala.sys.process.Process:0-0:Process:unresolved"
        prim = classify_call({"scala": load_catalog("scala")}, edge.dst, edge.meta,
                             dst_ref=edge.dst_ref)
        assert prim is not None and prim.module == "scala.sys.process.Process"

    def test_the_explicit_apply_on_the_import_is_not_the_project_object(
            self, tmp_path: Path) -> None:
        result = self._analyze(tmp_path, """\
package tools

import scala.sys.process.Process

object T {
  def run(c: String): Unit = { Process.apply(c) }
}
""")
        calls = _calls(result, "apply")
        assert calls, "reach"
        assert not any(resolved for _, resolved in calls), [e.dst for e, _ in calls]
        assert calls[0][0].dst == "scala:scala.sys.process.Process:0-0:apply:unresolved"

    def test_a_typed_value_of_the_imported_type_is_not_the_project_object(
            self, tmp_path: Path) -> None:
        from hypergumbo_lang_mainstream.scala import analyze_scala

        result = analyze_scala(_write(tmp_path, {
            "project/Process.scala": "package build\n\nclass Process {\n  def run(): Int = 0\n}\n",
            "bin/T.scala": """\
package tools

import scala.sys.process.Process

object T {
  def go(p: Process): Unit = { p.run() }
}
""",
        }))
        calls = _calls(result, "run")
        assert calls, "reach"
        assert not any(resolved for _, resolved in calls), [e.dst for e, _ in calls]
        assert calls[0][0].dst == "scala:scala.sys.process.Process:0-0:run:unresolved"

    @pytest.mark.parametrize("imports", [
        "import build.Process",
        "import build.{Process => Process}",
        "",
    ], ids=["import-of-the-project-path", "selector", "no-import"])
    def test_the_project_object_still_resolves(self, tmp_path: Path, imports: str) -> None:
        """The control. An import that names the project's own path, or no
        import at all, keeps the in-project edge."""
        result = self._analyze(tmp_path, f"""\
package tools

{imports}

object T {{
  def run(c: String): Unit = {{ Process(c) }}
}}
""")
        calls = _calls(result, "Process")
        assert calls and all(resolved for _, resolved in calls), [e.dst for e, _ in calls]

    @pytest.mark.parametrize("package,imports,resolves", [
        ("org.apache.pekko.actor", "import pekko.util.OptionVal", True),
        ("org.apache.pekko.actor", "import util.OptionVal", True),
        ("org.apache.pekko.actor", "import _root_.org.apache.pekko.util.OptionVal", True),
        ("org.apache.pekko.actor", "import _root_.pekko.util.OptionVal", False),
        ("com.elsewhere", "import other.util.OptionVal", False),
    ], ids=["relative-to-org.apache", "relative-to-org.apache.pekko", "root-absolute",
            "root-is-not-relative", "another-package-tree"])
    def test_a_relative_import_resolves_against_the_enclosing_packages(
            self, tmp_path: Path, package: str, imports: str, resolves: bool) -> None:
        """pekko writes ``import pekko.util.OptionVal`` inside ``org.apache.pekko``.
        Reading that as an absolute path withheld correct edges on pekko. An
        import that fits no reading, and a ``_root_`` path outside the symbol's
        package, are still refused."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        result = analyze_scala(_write(tmp_path, {
            "a/OptionVal.scala": "package org.apache.pekko.util\n\n"
                                 "object OptionVal {\n  def apply(s: String): Int = 0\n}\n",
            "b/T.scala": f"package {package}\n\n{imports}\n\n"
                         "object T {\n  def run(c: String): Unit = { OptionVal(c) }\n}\n",
        }))
        calls = _calls(result, "OptionVal")
        assert calls, "reach"
        assert all(resolved is resolves for _, resolved in calls), [e.dst for e, _ in calls]

    @pytest.mark.parametrize("target,imports,call", [
        ("package org.apache.spark.sql.catalyst.analysis\n\nobject TypeCheckResult {\n"
         "  case class DataTypeMismatch(s: String)\n}\n",
         "import org.apache.spark.sql.catalyst.analysis.TypeCheckResult.DataTypeMismatch",
         "DataTypeMismatch(c)"),
        ("package org.apache.spark.sql.catalyst\n\npackage object util {\n"
         "  def truncatedString(s: String): String = s\n}\n",
         "import org.apache.spark.sql.catalyst.util.truncatedString",
         "truncatedString(c)"),
        # A second ``hl`` makes the resolver choose by the import (its
        # path-hint branch), as scala3's several ``hl`` definitions do. A lone
        # suffix match to a method is deferred before any import is read.
        ("package dotty.tools.dotc.core\n\nobject SymDenotations {\n"
         "  class SymDenotation(x: String)\n}\n",
         "import dotty.tools.dotc.core.SymDenotations\nimport SymDenotations.SymDenotation",
         "SymDenotation(c)"),
        ("package dotty.tools.dotc.core\n\nobject SymDenotations {\n"
         "  class SymDenotation(x: String) {\n    def is(f: String): Boolean = true\n  }\n}\n",
         "import dotty.tools.dotc.core.SymDenotations\nimport SymDenotations.SymDenotation",
         "d.is(c)"),
        ("package scala.collection.mutable\n\nclass AnyRefMap(x: String) {\n"
         "  def get(k: String): Int = 0\n}\n",
         "import scala.collection.{mutable => cm}\nimport cm.{AnyRefMap, HashMap}",
         "val m: AnyRefMap = null; m.get(c)"),
        ("package dotty.tools.dotc.printing\n\nobject Formatting {\n"
         "  def hl(s: String): String = s\n}\nobject Other {\n"
         "  def hl(s: String): String = s\n}\n",
         "import Formatting.hl",
         "hl(c)"),
    ], ids=["nested-type-in-an-object", "package-object-member",
            "relative-to-an-object-in-scope", "a-member-of-the-imported-type",
            "through-a-renamed-import", "relative-to-a-type"])
    def test_a_name_the_symbol_spells_differently_still_resolves(
            self, tmp_path: Path, target: str, imports: str, call: str) -> None:
        """Spark's shapes: the symbol's name omits its enclosing object, or its
        package omits the package object. Both withheld correct edges when the
        import was compared to an exact ``<package>.<name>``."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        result = analyze_scala(_write(tmp_path, {
            "a/Target.scala": target,
            "b/T.scala": f"package dotty.tools.dotc.printing\n\n{imports}\n\n"
                         f"object T {{\n  def run(c: String, d: SymDenotation): Unit = {{ {call} }}\n}}\n",
        }))
        name = call.rsplit(";", 1)[-1].split("(")[0].split(".")[-1]
        calls = _calls(result, name)
        assert calls and all(resolved for _, resolved in calls), [e.dst for e, _ in calls]

    def test_a_block_scoped_import_applies_only_inside_its_block(self, tmp_path: Path) -> None:
        """zio's shape: ``import scala.concurrent.Promise`` inside ONE test, and
        ``zio.Promise`` everywhere else in the file. The analyzer reads imports
        file-wide, so the scoped import had refused the calls outside it."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        result = analyze_scala(_write(tmp_path, {
            "a/Process.scala": _SCALA_PROJECT_PROCESS,
            "b/T.scala": """\
package build

object T {
  def inside(c: String): Unit = {
    import scala.sys.process.Process
    Process(c)
  }
  def outside(c: String): Unit = { Process(c) }
}
""",
        }))
        by_line = {e.line: e.dst for e, _ in _calls(result, "Process")}
        assert set(by_line) == {6, 8}, by_line  # reach
        assert by_line[6] == "scala:scala.sys.process.Process:0-0:Process:unresolved", by_line
        assert ":a/Process.scala:" in by_line[8], by_line

    def test_chained_package_clauses_make_one_package(self, tmp_path: Path) -> None:
        """``package org.apache.spark`` then ``package sql`` is org.apache.spark.sql."""
        from hypergumbo_lang_mainstream.scala import analyze_scala

        result = analyze_scala(_write(tmp_path, {
            "a/Process.scala": "package org.apache.spark\npackage sql\n\n"
                               "object Process {\n  def apply(s: String): Int = 0\n}\n",
            "b/T.scala": "package other\n\nimport org.apache.spark.sql.Process\n\n"
                         "object T {\n  def run(c: String): Unit = { Process(c) }\n}\n",
        }))
        calls = _calls(result, "Process")
        assert calls and all(resolved for _, resolved in calls), [e.dst for e, _ in calls]


_KOTLIN_PROJECT = """\
package dev.acme.model

class File(val name: String)

object Launcher {
    fun start(cmd: String): Int = 0
}

fun launch(cmd: String): Int = 0
"""


class TestKotlin:
    def _analyze(self, tmp_path: Path, caller: str):
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        return analyze_kotlin(_write(tmp_path, {
            "a/Model.kt": _KOTLIN_PROJECT, "b/Use.kt": caller}))

    def test_an_imported_jdk_class_is_not_the_project_class(self, tmp_path: Path) -> None:
        result = self._analyze(tmp_path, """\
package dev.acme.cli

import java.io.File

class Use {
    fun make(): Any = File("x")
}
""")
        calls = _calls(result, "File")
        assert calls, "reach"
        assert not any(resolved for _, resolved in calls), [e.dst for e, _ in calls]
        assert calls[0][0].dst == "kotlin:java.io.File:0-0:File:unresolved"

    def test_an_imported_function_is_not_the_project_function(self, tmp_path: Path) -> None:
        result = self._analyze(tmp_path, """\
package dev.acme.cli

import org.other.launch

class Use {
    fun go(): Int = launch("ls")
}
""")
        calls = _calls(result, "launch")
        assert calls, "reach"
        assert not any(resolved for _, resolved in calls), [e.dst for e, _ in calls]

    def test_an_imported_object_is_not_the_project_object(self, tmp_path: Path) -> None:
        result = self._analyze(tmp_path, """\
package dev.acme.cli

import org.other.Launcher

class Use {
    fun go(): Int = Launcher.start("ls")
}
""")
        calls = _calls(result, "start")
        assert calls, "reach"
        assert not any(resolved for _, resolved in calls), [e.dst for e, _ in calls]
        assert calls[0][0].dst == "kotlin:org.other.Launcher:0-0:start:unresolved"

    @pytest.mark.parametrize("imports", [
        "import dev.acme.model.File\nimport dev.acme.model.launch\nimport dev.acme.model.Launcher",
        "",
    ], ids=["import-of-the-project-path", "no-import"])
    def test_the_project_symbols_still_resolve(self, tmp_path: Path, imports: str) -> None:
        result = self._analyze(tmp_path, f"""\
package dev.acme.cli

{imports}

class Use {{
    fun make(): Any = File("x")
    fun go(): Int = launch("ls")
    fun run(): Int = Launcher.start("ls")
}}
""")
        for name in ("File", "launch", "start"):
            calls = _calls(result, name)
            assert calls and all(resolved for _, resolved in calls), (name, [e.dst for e, _ in calls])


@pytest.mark.parametrize("imported", ["org.apache.spark.util", "org.apache.spark.sql.util"])
def test_a_scala_import_of_a_project_path_keeps_the_placeholder_slot(
        tmp_path: Path, imported: str) -> None:
    """spark imports ``org.apache.spark.util.Utils`` while the name-keyed registry
    holds another project ``Utils``. The import still names a project type, so the
    call keeps the ``external`` placeholder the Tier-2 linkers resolve from
    (WI-kilap). Reading the mismatch as "not a project type" moved 1217 spark
    slots to the import path. Both imports are tried: the registry keeps ONE
    ``Utils``, so one of the two contradicts it, whichever it keeps."""
    from hypergumbo_lang_mainstream.scala import analyze_scala

    result = analyze_scala(_write(tmp_path, {
        "a/Utils.scala": "package org.apache.spark.util\n\nobject Utils {\n  def one(): Int = 1\n}\n",
        "b/Utils.scala": "package org.apache.spark.sql.util\n\nobject Utils {\n  def two(): Int = 2\n}\n",
        "c/T.scala": f"package org.apache.spark.deploy\n\nimport {imported}.Utils\n\n"
                     "object T {\n  def run(x: String): Unit = { Utils.localHostName(x) }\n}\n",
    }))
    calls = [e for e in result.edges if e.edge_type == "calls" and "localHostName" in e.dst]
    assert calls, "reach"
    assert [e.dst for e in calls] == ["scala:external:0-0:localHostName:unresolved"]


_KOTLIN_TWO_PACKAGES = {
    "a/One.kt": "package p.one\n\nfun assertThat(x: Any): Int = 1\n\n"
                "object Launcher {\n    fun start(cmd: String): Int = 0\n}\n",
    "b/Two.kt": "package p.two\n\nfun assertThat(x: Any): Int = 2\n\n"
                "object Launcher {\n    fun start(cmd: String): Int = 0\n}\n",
}


class TestKotlinBindsTheImportedPath:
    """kotlin records a qualified name for every symbol, so an import that the
    registry's same-named symbol contradicts can bind the project symbol AT the
    imported path. The registry keeps one symbol per name; the one it keeps is
    one of the two, so each test imports each in turn and one of the two runs
    contradicts it."""

    def _dsts(self, tmp_path: Path, name: str, pkg: str) -> list[str]:
        from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

        result = analyze_kotlin(_write(tmp_path, {**_KOTLIN_TWO_PACKAGES, "c/Use.kt": (
            f"package p.use\n\nimport p.{pkg}.assertThat\nimport p.{pkg}.Launcher\n\n"
            "class Use {\n    fun a(): Int = assertThat(1)\n"
            "    fun b(): Int = Launcher.start(\"x\")\n}\n")}))
        by_id = {s.id: s for s in result.symbols}
        return [by_id[e.dst].path for e in result.edges
                if e.edge_type == "calls" and e.dst in by_id and by_id[e.dst].name.endswith(name)]

    @pytest.mark.parametrize("pkg,folder", [("one", "a"), ("two", "b")])
    def test_a_bare_call_binds_the_imported_function(
            self, tmp_path: Path, pkg: str, folder: str) -> None:
        assert [Path(p).parent.name for p in self._dsts(tmp_path, "assertThat", pkg)] == [folder]

    @pytest.mark.parametrize("pkg,folder", [("one", "a"), ("two", "b")])
    def test_an_object_call_binds_the_imported_object(
            self, tmp_path: Path, pkg: str, folder: str) -> None:
        assert [Path(p).parent.name for p in self._dsts(tmp_path, "start", pkg)] == [folder]


def test_a_kotlin_companion_import_still_resolves(tmp_path: Path) -> None:
    """okhttp imports ``okhttp3.Headers.Companion.headersOf``. The member's
    qualified name has no ``Companion`` segment, and reading the import literally
    withheld 207 correct okhttp edges."""
    from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

    result = analyze_kotlin(_write(tmp_path, {
        "a/Headers.kt": "package okhttp3\n\nclass Headers {\n    companion object {\n"
                        "        fun headersOf(vararg s: String): Headers = Headers()\n    }\n}\n",
        "b/Use.kt": "package app\n\nimport okhttp3.Headers.Companion.headersOf\n\n"
                    "class Use {\n    fun go(): Any = headersOf(\"a\", \"b\")\n}\n",
    }))
    calls = _calls(result, "headersOf")
    assert calls and all(resolved for _, resolved in calls), [e.dst for e, _ in calls]


class TestImportedElsewhere:
    @pytest.mark.parametrize("qualified,imported,expected", [
        ("dev.acme.api.Issue", "dev.acme.api.Issue", False),
        ("dev.acme.api.Issue.Entity", "dev.acme.api.Issue", False),
        ("build.Process", "scala.sys.process.Process", True),
        ("build.ProcessX", "build.Process", True),
        ("build.Process", None, False),
        ("build.Process", "Process", False),
        (None, "scala.sys.process.Process", False),
        ("okhttp3.Headers.headersOf", "okhttp3.Headers.Companion.headersOf", False),
        ("okhttp3.TlsVersion.forJavaName", "okhttp3.CipherSuite.Companion.forJavaName", True),
    ])
    def test_cases(self, qualified: "str | None", imported: "str | None", expected: bool) -> None:
        assert imported_elsewhere(qualified, imported) is expected
