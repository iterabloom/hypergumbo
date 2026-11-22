# SPDX-License-Identifier: AGPL-3.0-or-later
"""The F# analyzer records the calls the framework rows of F# key on.

giraffe.yaml's rows are all ``usage:`` rows (``kind: call`` and the name
called), among them Saturn's ``application { ... }`` that makes a hosted
application. The analyzer recorded no UsageContext at all, so none of them
could fire. These tests pin what is recorded for each call form, which symbol
each record names (the caller: the declaration the call sits in), and, end to
end, that the Saturn application object gets its concept and its entry.
"""

from pathlib import Path

from hypergumbo_core.entrypoints import EntrypointKind, detect_entrypoint_candidates
from hypergumbo_core.framework_patterns import (
    clear_pattern_cache, enrich_symbols, load_framework_patterns,
)
from hypergumbo_core.ir import Span, UsageContext

from hypergumbo_lang_common.fsharp import analyze_fsharp

SATURN_APP = """module Program
open Saturn
open Giraffe

let webApp = router { get "/" (text "ok") }

let app = application {
    use_router webApp
}

[<EntryPoint>]
let main _ =
    run app
    0
"""


def _contexts(tmp_path: Path, source: str, name: str = "Program.fs"):
    (tmp_path / name).write_text(source, encoding="utf-8")
    result = analyze_fsharp(tmp_path)
    names = {s.id: s.name for s in result.symbols}
    return result, [
        (c.context_name, c.metadata.get("form"), names.get(c.symbol_ref or ""))
        for c in result.usage_contexts
    ]


def test_a_computation_expression_is_a_call_of_its_builder_on_the_binding(
    tmp_path: Path,
) -> None:
    result, found = _contexts(tmp_path, SATURN_APP)
    assert ("application", "computation_expression", "app") in found
    assert ("router", "computation_expression", "webApp") in found
    app = next(s for s in result.symbols if s.name == "app")
    ctx = next(c for c in result.usage_contexts if c.context_name == "application")
    assert isinstance(ctx, UsageContext)
    assert (ctx.kind, ctx.position, ctx.symbol_ref) == ("call", "caller", app.id)
    assert app.kind == "variable"


def test_an_application_is_a_call_of_its_function_named_as_written(tmp_path: Path) -> None:
    _, found = _contexts(tmp_path, """module M

let handler (name : string) =
    let local = sprintf "hi %s" name
    text local

let pick xs = List.choose id xs

let bind = bindJson<Car> handle

let viaOp = (>=>) first second
""")
    assert ("sprintf", "application", "handler") in found  # a local `let` is walked past
    assert ("text", "application", "handler") in found
    assert ("List.choose", "application", "pick") in found
    assert not any(name == "choose" for name, _, _ in found)
    assert ("bindJson", "application", "bind") in found
    assert (">=>", "application", "viaOp") in found


def test_a_function_after_an_infix_operator_is_the_one_applied(tmp_path: Path) -> None:
    """``route "/" >=> text "ok"`` parses as ``(route "/" >=> text) "ok"``."""
    _, found = _contexts(tmp_path, """module M

let webApp = route "/" >=> htmlFile "index.html"
""")
    assert sorted(found) == sorted([
        ("route", "application", "webApp"),
        ("htmlFile", "application", "webApp"),
        (">=>", "operator", "webApp"),
    ])


def test_a_pipe_calls_its_function_operand_once(tmp_path: Path) -> None:
    _, found = _contexts(tmp_path, """module M

let a = x |> json
let b = negotiate <| x
let c = x |> setBody y
let d = f >> g <| x
""")
    assert ("json", "pipe", "a") in found
    assert ("negotiate", "pipe", "b") in found
    assert [n for n, _, owner in found if owner == "c"] == ["setBody"]
    assert [n for n, _, owner in found if owner == "d"] == []


def test_fsharp_core_operators_are_not_recorded(tmp_path: Path) -> None:
    _, found = _contexts(tmp_path, """module M

let total = a + b * c
let same = a = b
let one = a >=> b >=> c
""")
    assert [n for n, _, owner in found if owner in ("total", "same")] == []
    # Two `>=>` starting on one line are one record (the id hashes the line).
    assert [n for n, _, owner in found if owner == "one"] == [">=>"]


def test_a_call_in_no_declaration_names_no_symbol(tmp_path: Path) -> None:
    result, found = _contexts(tmp_path, 'printfn "hi"\n', name="script.fsx")
    assert found == [("printfn", "application", None)]
    assert result.usage_contexts[0].symbol_ref is None


def test_the_saturn_application_object_is_a_hosted_application(tmp_path: Path) -> None:
    """End to end: the rows giraffe.yaml loads for a Saturn-only app."""
    clear_pattern_cache()
    (tmp_path / "Program.fs").write_text(SATURN_APP, encoding="utf-8")
    result = analyze_fsharp(tmp_path)
    symbols = enrich_symbols(result.symbols, ["saturn"], result.usage_contexts)
    app = next(s for s in symbols if s.name == "app")
    assert {"concept": "application", "framework": "giraffe"} in app.meta["concepts"]
    hosted = [
        ep for ep in detect_entrypoint_candidates(symbols, result.edges)
        if ep.kind is EntrypointKind.HOSTED_APPLICATION
    ]
    assert [ep.symbol_id for ep in hosted] == [app.id]


GIRAFFE_APP = """module App
open Giraffe

let notFound =
    setStatusCode 404 >=> text "Not Found"

let teapot = setStatusCode 418

let webApp =
    choose [
        GET >=> choose [
            route "/" >=> htmlFile "index.html"
            routef "/hello/%s" sayHello
        ]
        notFound ]
"""


def _giraffe(tmp_path: Path):
    clear_pattern_cache()
    (tmp_path / "App.fs").write_text(GIRAFFE_APP, encoding="utf-8")
    result = analyze_fsharp(tmp_path)
    symbols = enrich_symbols(result.symbols, ["giraffe"], result.usage_contexts)
    by_name = {s.name: s for s in symbols}
    name_of = {s.id: s.name for s in symbols}
    entries = {
        (name_of[ep.symbol_id], ep.kind)
        for ep in detect_entrypoint_candidates(symbols, result.edges)
    }
    return by_name, entries


def _concepts(sym) -> set[str]:
    return {c["concept"] for c in (sym.meta or {}).get("concepts", [])}


def test_a_route_table_holder_is_a_router_and_no_route(tmp_path: Path) -> None:
    """End to end through giraffe.yaml: `route "/" >=> h` composes a handler
    value; the binding holding the table is a router, not a path-less route."""
    by_name, entries = _giraffe(tmp_path)
    assert [e for e in entries if e[1] is EntrypointKind.HTTP_ROUTE] == []
    assert {"router", "handler", "response"} <= _concepts(by_name["webApp"])
    assert "route" not in _concepts(by_name["webApp"])


def test_a_handler_that_sets_a_status_writes_a_response_and_is_no_middleware(
    tmp_path: Path,
) -> None:
    by_name, entries = _giraffe(tmp_path)
    assert [e for e in entries if e[1] is EntrypointKind.MIDDLEWARE_HANDLER] == []
    assert _concepts(by_name["teapot"]) == {"response"}
    assert "middleware" not in _concepts(by_name["notFound"])


def test_the_application_row_matches_a_caller_context_only() -> None:
    clear_pattern_cache()
    pattern_def = load_framework_patterns("giraffe")
    assert pattern_def is not None
    (row,) = [p for p in pattern_def.patterns if p.concept == "application"]
    span = Span(start_line=1, end_line=1, start_col=0, end_col=1)

    def ctx(position: str) -> UsageContext:
        return UsageContext.create(
            kind="call", context_name="application", position=position,
            path="App.fs", span=span, symbol_ref="fsharp:App.fs:1-1:app:variable",
        )

    assert row.matches_usage(ctx("caller")) == {"concept": "application"}
    assert row.matches_usage(ctx("args[0]")) is None
