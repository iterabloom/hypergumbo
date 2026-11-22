# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/kotlin.py
"""A Ktor module, ``fun Application.module()``, is what the server runs.

A Ktor server runs its modules when it starts: the extension functions on
``Application`` named in ``embeddedServer(Netty, module = Application::module)``
or in ``application.conf`` (``ktor.application.modules``). Everything an
application does hangs off them: they install the plugins and call the route
functions. Two facts were missing, so a module and all it reaches had no
caller:

1. ``Application::module`` resolved to nothing. The kotlin analyzer looked up
   ``Application.module``, a member of a class ``Application``; an extension
   function is registered under its own name. A ``Type::name`` reference now
   falls back to the extension function ``name`` declared on ``Type`` (the
   analyzer's index of extension functions by receiver), when exactly one
   exists.
2. Nothing marked a module. ktor.yaml now has a row on the receiver the
   analyzer records (``meta.extension_receiver``), concept ``application``:
   the hosted-application kind of ADR-0063, which an application object a
   server hosts gets, ranked with the routes. A module is not the program
   start; ``main`` (or EngineMain) is.

There is deliberately no row on ``Route`` receivers: a route function is
called by the module's ``routing { }`` block, and one nothing calls is dead.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.entrypoints import EntrypointKind, detect_entrypoints
from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Edge, Symbol
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

_APP = """\
package example

import io.ktor.server.application.*
import io.ktor.server.engine.embeddedServer
import io.ktor.server.netty.Netty
import io.ktor.server.routing.*

class Other

fun Application.module() {
    routing { userRoutes() }
}

fun Route.userRoutes() {
    get("/users") { }
}

fun Route.unusedRoutes() {
    get("/unused") { }
}

fun String.shout(): String = uppercase()

fun module(): Int = 1

fun main() {
    embeddedServer(Netty, port = 8080, module = Application::module).start(wait = true)
    val loud = listOf("a").map(String::shout)
    val none = Other::module
    val wrong = Application::userRoutes
}
"""

_ADMIN = """\
package example.admin

import io.ktor.server.application.*

@Suppress("unused") // application.conf names this module
fun Application.adminModule() { }

fun io.ktor.server.application.Application.qualifiedModule() { }
"""


def _analyze(tmp_path: Path) -> tuple[list[Symbol], list[Edge]]:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Application.kt").write_text(_APP)
    (tmp_path / "src" / "Admin.kt").write_text(_ADMIN)
    result = analyze_kotlin(tmp_path)
    return list(result.symbols), list(result.edges)


def _named(symbols: list[Symbol], name: str, *, extension: bool | None = None) -> Symbol:
    found = [
        s for s in symbols if s.name == name and s.kind == "function"
        and (extension is None or bool((s.meta or {}).get("extension_receiver")) == extension)
    ]
    assert len(found) == 1, (name, found)
    return found[0]


def _references(symbols: list[Symbol], edges: list[Edge]) -> set[tuple[str, str]]:
    by_id = {s.id: s for s in symbols}
    return {
        (by_id[e.src].name if e.src in by_id else e.src,
         by_id[e.dst].name if e.dst in by_id else e.dst)
        for e in edges if e.edge_type == "references"
    }


class TestTypeColonColonExtension:
    def test_application_module_reference_reaches_the_module(self, tmp_path: Path) -> None:
        symbols, edges = _analyze(tmp_path)
        module = _named(symbols, "module", extension=True)
        assert module.meta["extension_receiver"] == "Application"
        main = _named(symbols, "main")
        assert any(
            e.edge_type == "references" and e.src == main.id and e.dst == module.id
            and e.evidence_type == "callable_reference"
            for e in edges
        )

    def test_any_receiver_type_the_same_way(self, tmp_path: Path) -> None:
        symbols, edges = _analyze(tmp_path)
        assert ("main", "shout") in _references(symbols, edges)

    def test_not_an_extension_of_another_receiver(self, tmp_path: Path) -> None:
        symbols, edges = _analyze(tmp_path)
        # ``Other::module``: Other declares no member and no extension named
        # module, and the top-level ``fun module()`` is not it.
        plain = _named(symbols, "module", extension=False)
        assert not [e for e in edges if e.edge_type == "references" and e.dst == plain.id]
        # ``Application::userRoutes``: userRoutes extends Route, not Application.
        assert ("main", "userRoutes") not in _references(symbols, edges)


def _enriched(tmp_path: Path) -> tuple[list[Symbol], list[Edge]]:
    symbols, edges = _analyze(tmp_path)
    clear_pattern_cache()
    enrich_symbols(symbols, {"ktor"})
    return symbols, edges


def _concepts(symbol: Symbol) -> set[str]:
    return {c["concept"] for c in (symbol.meta or {}).get("concepts", [])}


class TestModuleRow:
    def test_a_module_is_a_hosted_application(self, tmp_path: Path) -> None:
        symbols, edges = _enriched(tmp_path)
        module = _named(symbols, "module", extension=True)
        admin = _named(symbols, "adminModule")
        assert admin.meta["extension_receiver"] == "Application"
        assert "application" in _concepts(module)
        assert "application" in _concepts(admin)
        hosted = {
            e.symbol_id for e in detect_entrypoints(symbols, edges)
            if e.kind == EntrypointKind.HOSTED_APPLICATION
        }
        assert {module.id, admin.id} <= hosted

    def test_a_receiver_written_with_its_package(self, tmp_path: Path) -> None:
        symbols, _ = _enriched(tmp_path)
        qualified = _named(symbols, "qualifiedModule")
        assert qualified.meta["extension_receiver"] == "io.ktor.server.application.Application"
        assert "application" in _concepts(qualified)

    def test_route_functions_and_others_are_not(self, tmp_path: Path) -> None:
        symbols, _ = _enriched(tmp_path)
        assert _named(symbols, "userRoutes").meta["extension_receiver"] == "Route"
        assert _named(symbols, "unusedRoutes").meta["extension_receiver"] == "Route"
        assert _named(symbols, "shout").meta["extension_receiver"] == "String"
        for symbol in (_named(symbols, "userRoutes"), _named(symbols, "unusedRoutes"),
                       _named(symbols, "shout"), _named(symbols, "module", extension=False)):
            assert "application" not in _concepts(symbol), symbol.name

    def test_not_without_ktor(self, tmp_path: Path) -> None:
        symbols, _ = _analyze(tmp_path)
        clear_pattern_cache()
        enrich_symbols(symbols, set())
        assert "application" not in _concepts(_named(symbols, "adminModule"))
