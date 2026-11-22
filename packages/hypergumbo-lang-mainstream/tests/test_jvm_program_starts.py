# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/frameworks/main-functions.yaml
"""Scala and Kotlin program starts that are not a method named ``main``.

The convention rows for Scala and Kotlin matched a ``main`` method of an
object (Scala) and a top-level ``fun main`` (Kotlin). Three other ways the
two languages start a program were not entries, so everything such a program
calls had no caller:

- Scala 3 ``@main def run()``: the scala analyzer records the annotation as
  the decorator ``main``. On tapir's examples 46 such functions, 0 entries.
- ``object Server extends App``: ``scala.App`` runs the object's body as the
  program. The analyzer records the base as written (``App``).
- Kotlin ``object Tool { @JvmStatic fun main(args: Array<String>) }`` (and
  the same in a ``companion object``): the JVM starts the static method. The
  kotlin analyzer records it as a ``method`` with the decorator
  ``JvmStatic``; the existing Kotlin row asks for a ``function``.

Every test runs the analyzer on source and gives its symbols to the shipped
rows (``enrich_symbols``), as the pipeline does. Each first finds the symbol
it is about under the name the comment says, so a change in naming fails here
by name. The controls are the similar lines that start nothing: a Kotlin
``main`` without ``@JvmStatic``, a ``class`` (not
an object) extending ``App``, an object extending a type whose name merely
ends in ``App``, a ``@JvmStatic`` method with another name.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.entrypoints import EntrypointKind, detect_entrypoints
from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin
from hypergumbo_lang_mainstream.scala import analyze_scala


def _enriched(
    tmp_path: Path, files: dict[str, str], analyze: Callable[[Path], AnalysisResult],
) -> dict[str, Symbol]:
    """Analyze ``files``, enrich with the shipped rows; keyed by symbol name."""
    for relative, source in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    symbols = list(analyze(tmp_path).symbols)
    clear_pattern_cache()
    enrich_symbols(symbols, set())
    return {s.name: s for s in symbols}


def _concepts(symbol: Symbol) -> set[str]:
    return {c["concept"] for c in (symbol.meta or {}).get("concepts", [])}


def _main_entries(symbols: dict[str, Symbol]) -> set[str]:
    by_id = {s.id: s.name for s in symbols.values()}
    entries = detect_entrypoints(list(symbols.values()), [])
    return {by_id[e.symbol_id] for e in entries if e.kind == EntrypointKind.MAIN_FUNCTION}


_SCALA3_MAIN = """\
package example

def helper(): Int = 1

@main def runServer(): Unit =
  println(helper())

object Tools:
  @main def migrate(): Unit = println("migrate")
"""

_SCALA_APP = """\
package example

object Server extends App {
  println("serving")
}

object Qualified extends scala.App {
  println("qualified")
}

// controls
class NotAnObject extends App
trait MyApp
object Widget extends MyApp
object Other extends foo.App
object Launcher {
  def main(args: Array[String]): Unit = println("x")
}
"""

_KOTLIN = """\
package example

object Tool {
    @JvmStatic
    fun main(args: Array<String>) {
        println("tool")
    }

    @JvmStatic
    fun helper(): Int = 1
}

class Cli {
    companion object {
        @JvmStatic fun main(args: Array<String>) = println("cli")
    }
}

// controls
object NotStatic {
    fun main(args: Array<String>) = println("not a program start")
}
"""


class TestScala3MainAnnotation:
    def test_a_main_annotated_def_is_a_program_start(self, tmp_path: Path) -> None:
        symbols = _enriched(tmp_path, {"src/Main.scala": _SCALA3_MAIN}, analyze_scala)
        run = symbols["runServer"]
        assert run.kind == "function"
        assert [d["name"] for d in run.meta["decorators"]] == ["main"]
        assert "main_function" in _concepts(run)
        assert "runServer" in _main_entries(symbols)

    def test_one_inside_an_object_too(self, tmp_path: Path) -> None:
        symbols = _enriched(tmp_path, {"src/Main.scala": _SCALA3_MAIN}, analyze_scala)
        assert "main_function" in _concepts(symbols["Tools.migrate"])

    def test_an_undecorated_function_is_not(self, tmp_path: Path) -> None:
        symbols = _enriched(tmp_path, {"src/Main.scala": _SCALA3_MAIN}, analyze_scala)
        assert "main_function" not in _concepts(symbols["helper"])


class TestScalaAppObject:
    def test_an_object_extending_app_is_a_program_start(self, tmp_path: Path) -> None:
        symbols = _enriched(tmp_path, {"src/Server.scala": _SCALA_APP}, analyze_scala)
        server = symbols["Server"]
        assert server.kind == "object"
        assert server.meta["base_classes"] == ["App"]
        assert "main_function" in _concepts(server)
        assert "main_function" in _concepts(symbols["Qualified"])
        assert {"Server", "Qualified"} <= _main_entries(symbols)

    def test_the_lookalikes_are_not(self, tmp_path: Path) -> None:
        symbols = _enriched(tmp_path, {"src/Server.scala": _SCALA_APP}, analyze_scala)
        assert symbols["NotAnObject"].meta["base_classes"] == ["App"]
        assert symbols["Widget"].meta["base_classes"] == ["MyApp"]
        assert symbols["Other"].meta["base_classes"] == ["foo.App"]
        for name in ("NotAnObject", "Widget", "Other"):
            assert "main_function" not in _concepts(symbols[name]), name

    def test_the_existing_main_method_row_still_holds(self, tmp_path: Path) -> None:
        symbols = _enriched(tmp_path, {"src/Server.scala": _SCALA_APP}, analyze_scala)
        assert "main_function" in _concepts(symbols["Launcher.main"])


class TestKotlinJvmStaticMain:
    def test_a_jvmstatic_main_in_an_object_is_a_program_start(self, tmp_path: Path) -> None:
        symbols = _enriched(tmp_path, {"src/Tool.kt": _KOTLIN}, analyze_kotlin)
        main = symbols["Tool.main"]
        assert main.kind == "method"
        assert [d["name"] for d in main.meta["decorators"]] == ["JvmStatic"]
        assert "main_function" in _concepts(main)
        assert "Tool.main" in _main_entries(symbols)

    def test_one_in_a_companion_object_too(self, tmp_path: Path) -> None:
        symbols = _enriched(tmp_path, {"src/Tool.kt": _KOTLIN}, analyze_kotlin)
        companion = [s for name, s in symbols.items() if name.endswith(".main") and "Cli" in name]
        assert len(companion) == 1, sorted(symbols)
        assert "main_function" in _concepts(companion[0])

    def test_the_lookalikes_are_not(self, tmp_path: Path) -> None:
        symbols = _enriched(tmp_path, {"src/Tool.kt": _KOTLIN}, analyze_kotlin)
        assert [d["name"] for d in symbols["Tool.helper"].meta["decorators"]] == ["JvmStatic"]
        assert "main_function" not in _concepts(symbols["Tool.helper"])
        assert symbols["NotStatic.main"].kind == "method"
        assert "main_function" not in _concepts(symbols["NotStatic.main"])
