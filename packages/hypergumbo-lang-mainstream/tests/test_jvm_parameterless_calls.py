# SPDX-License-Identifier: AGPL-3.0-or-later
"""A JDK method invoked with no argument list gets a calls edge (WI-bojud-sator).

scala calls a parameterless method without parentheses
(``Runtime.getRuntime.availableProcessors``, ``System.currentTimeMillis``), and
kotlin reads a Java getter as a synthetic property (``proc.inputStream`` is
``getInputStream()``). Neither parsed as a call, so no edge existed for any
receiver typing to reach. The grammar cannot tell such a read from a FIELD read
(``System.out``), so a member access is taken as a call only where the library
is known to declare a METHOD there: a library-signature row or a function /
method io_primitives row.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/scala.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/kotlin.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/jvm_implicit_imports.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.jvm_implicit_imports import is_known_library_method
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin
from hypergumbo_lang_mainstream.scala import analyze_scala


def _calls(result) -> dict[str, list[str]]:
    names = {s.id: s.name for s in result.symbols}
    out: dict[str, list[str]] = {}
    for e in result.edges:
        if e.edge_type == "calls" and e.src in names:
            out.setdefault(names[e.src], []).append(e.dst)
    return out


def test_known_jdk_methods_come_from_signature_and_catalogue_rows() -> None:
    assert is_known_library_method("scala", "java.lang.Runtime.getRuntime")  # signature row
    assert is_known_library_method("scala", "java.lang.Runtime.availableProcessors")  # method row
    assert is_known_library_method("kotlin", "java.lang.System.currentTimeMillis")  # function row
    assert not is_known_library_method("scala", "java.lang.System.out")  # attribute row
    assert not is_known_library_method("scala", "java.lang.Runtime.nosuch")


def test_scala_parameterless_jdk_calls_emit_edges(tmp_path: Path) -> None:
    (tmp_path / "A.scala").write_text(
        "import scala.io.StdIn\n"
        "object A {\n"
        "  def n(): Int = Runtime.getRuntime.availableProcessors\n"
        "  def m(): Long = System.currentTimeMillis\n"
        "  def r(): String = StdIn.readLine\n"
        "  def q(): String = scala.io.StdIn.readLine\n"
        "  def s(c: String) = Runtime.getRuntime.exec(c).getInputStream\n"
        "  def o(): Unit = System.out.println(\"x\")\n"
        "  def f(): Unit = { val e = System.err; e.flush() }\n"
        "}\n"
    )
    calls = _calls(analyze_scala(tmp_path))
    assert calls["A.n"] == [
        "scala:java.lang.Runtime:0-0:availableProcessors:unresolved",
        "scala:java.lang.Runtime:0-0:getRuntime:unresolved",
    ]
    assert calls["A.m"] == ["scala:java.lang.System:0-0:currentTimeMillis:unresolved"]
    assert calls["A.r"] == ["scala:scala.io.StdIn:0-0:readLine:unresolved"]
    assert calls["A.q"] == ["scala:scala.io.StdIn:0-0:readLine:unresolved"]
    assert "scala:java.lang.Process:0-0:getInputStream:unresolved" in calls["A.s"]
    # A field read is not a call: ``System.out`` / ``System.err`` add no edge.
    assert calls["A.o"] == ["scala:java.io.PrintStream:0-0:println:unresolved"]
    assert not any(":out:" in d or ":err:" in d for d in calls.get("A.f", []))


def test_scala_member_reads_that_are_not_known_methods_stay_silent(tmp_path: Path) -> None:
    """A project member, an unknown library member, an untyped receiver and an
    assignment target add nothing; a parenthesised call is not counted twice."""
    (tmp_path / "B.scala").write_text(
        "class Box { var size: Int = 0 }\n"
        "object B {\n"
        "  def a(b: Box): Int = b.size\n"
        "  def u(x: Untyped) = x.getInputStream\n"
        "  def k(): Int = Runtime.getRuntime.nosuch\n"
        "  def w(b: Box): Unit = { b.size = 2 }\n"
        "  def p(): Long = System.currentTimeMillis()\n"
        "  def g() = Runtime.getRuntime.availableProcessors[Int]\n"
        "}\n"
    )
    calls = _calls(analyze_scala(tmp_path))
    assert "B.a" not in calls and "B.u" not in calls and "B.w" not in calls
    assert calls["B.k"] == ["scala:java.lang.Runtime:0-0:getRuntime:unresolved"]
    assert calls["B.p"] == ["scala:java.lang.System:0-0:currentTimeMillis:unresolved"]


def test_kotlin_synthetic_getter_reads_emit_the_getter_call(tmp_path: Path) -> None:
    (tmp_path / "a.kt").write_text(
        "fun go(c: String) {\n"
        "    val s = Runtime.getRuntime().exec(c).inputStream\n"
        "    val e = Runtime.getRuntime().exec(c).errorStream.read()\n"
        "}\n"
        "fun control(c: String) {\n"
        "    val s = Runtime.getRuntime().exec(c).getInputStream()\n"
        "}\n"
    )
    calls = _calls(analyze_kotlin(tmp_path))
    assert "kotlin:java.lang.Process:0-0:getInputStream:unresolved" in calls["go"]
    assert "kotlin:java.lang.Process:0-0:getErrorStream:unresolved" in calls["go"]
    assert calls["control"].count("kotlin:java.lang.Process:0-0:getInputStream:unresolved") == 1


def test_kotlin_property_reads_that_are_not_known_getters_stay_silent(tmp_path: Path) -> None:
    (tmp_path / "b.kt").write_text(
        "class Box { var size: Int = 0 }\n"
        "fun a(b: Box): Int = b.size\n"
        "fun k(c: String) = Runtime.getRuntime().exec(c).nosuch\n"
        "fun w(c: String) { Runtime.getRuntime().exec(c).inputStream = null }\n"
        "fun r() = Box::size\n"
        "fun o() = System.out\n"
        "fun n(c: String) = Runtime.getRuntime().exec(c).inputStream.nosuch\n"
        "fun u(x: Any) = x.foo.bar\n"
    )
    calls = _calls(analyze_kotlin(tmp_path))
    assert "a" not in calls and "o" not in calls and "r" not in calls
    assert not any("nosuch" in d or "getNosuch" in d for d in calls["k"])
    assert not any("getInputStream" in d for d in calls["w"])
    # A getter's returned type is the next link's owner; an unknown link stops.
    assert [d for d in calls["n"] if "getRuntime" not in d and ":exec:" not in d] == [
        "kotlin:java.lang.Process:0-0:getInputStream:unresolved"]
    assert "u" not in calls


def test_kotlin_boolean_getter_keeps_its_is_name(tmp_path: Path) -> None:
    """``isDirectory()`` reads as ``.isDirectory``, not ``.directory``."""
    (tmp_path / "c.kt").write_text(
        "import java.io.File\n"
        "fun d(f: File): Boolean = f.isDirectory\n"
    )
    assert _calls(analyze_kotlin(tmp_path))["d"] == ["kotlin:java.io.File:0-0:isDirectory:unresolved"]
