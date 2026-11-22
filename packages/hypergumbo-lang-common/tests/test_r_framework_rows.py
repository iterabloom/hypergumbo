# SPDX-License-Identifier: AGPL-3.0-or-later
"""plumber.yaml and shiny.yaml say only what an R caller context can say.

The R analyzer (r_lang.py) gives each named call one UsageContext with
position ``caller`` that names the function holding the call, else the file
(test_r_usage_contexts.py). A row whose subject is that holder (an app is
built, a UI laid out, a router mounted) carries ``position: "^caller$"``. A
row whose subject is a function passed as an argument (a route handler, a
filter, a serializer handed to ``register_serializer``) carries the handler
position other analyzers use (``args[last]``, ``args[1]``) and cannot fire
until r_lang.py emits such contexts: stamping the registering function gave
a path-less http_route, a middleware_handler and a serializer entry on it.
These tests read the shipped YAML.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import (
    make_file_id,
    synthesize_file_symbols_for_dangling_edges,
)
from hypergumbo_core.entrypoints import EntrypointKind, detect_entrypoints
from hypergumbo_core.framework_patterns import (
    clear_pattern_cache,
    enrich_symbols,
    load_framework_patterns,
)
from hypergumbo_core.ir import Span, UsageContext
from hypergumbo_lang_common.r_lang import analyze_r_files


def _ctx(name: str, position: str) -> UsageContext:
    return UsageContext.create(
        kind="call", context_name=name, position=position, path="x.R",
        span=Span(start_line=1, end_line=1, start_col=0, end_col=1),
        symbol_ref="r:x.R:1-1:f:function",
    )


def _concepts(framework: str, name: str, position: str) -> set[str]:
    clear_pattern_cache()
    pattern_def = load_framework_patterns(framework)
    assert pattern_def is not None
    found = set()
    for pattern in pattern_def.patterns:
        match = pattern.matches_usage(_ctx(name, position))
        if match:
            found.add(match["concept"])
    return found


_APPLICATION_CALLS = [
    ("shiny", "shinyApp"), ("shiny", "shinyAppDir"), ("shiny", "shinyAppFile"),
    ("plumber", "plumb"), ("plumber", "Plumber$new"), ("plumber", "plumb_api"),
    ("plumber", "pr"),
]


@pytest.mark.parametrize(("framework", "name"), _APPLICATION_CALLS)
def test_an_application_row_matches_only_a_caller_context(
    framework: str, name: str,
) -> None:
    assert _concepts(framework, name, "caller") == {"application"}
    for position in ("args[0]", "args[1]", "args[last]"):
        assert _concepts(framework, name, position) == set(), position


@pytest.mark.parametrize(("framework", "name", "concept"), [
    ("shiny", "runApp", "server"),
    ("shiny", "fluidPage", "ui_component"),
    ("shiny", "sliderInput", "input"),
    ("shiny", "renderPlot", "output"),
    ("shiny", "plotOutput", "output"),
    ("shiny", "observeEvent", "reactive"),
    ("shiny", "moduleServer", "module"),
    ("shiny", "enableBookmarking", "bookmark"),
    ("plumber", "pr_mount", "router"),
    ("plumber", "pr_static", "static_handler"),
])
def test_the_other_caller_rows_match_only_a_caller_context(
    framework: str, name: str, concept: str,
) -> None:
    assert _concepts(framework, name, "caller") == {concept}
    assert _concepts(framework, name, "args[last]") == set()


@pytest.mark.parametrize("name", ["pr_run", "run", "pr$run", "api$run", "a$b$run"])
def test_every_form_of_running_the_api_is_server(name: str) -> None:
    assert _concepts("plumber", name, "caller") == {"server"}


@pytest.mark.parametrize("name", ["rerun", "pr_runner", "pr$runner", "$run", "run_api"])
def test_names_that_only_contain_run_are_not_server(name: str) -> None:
    assert _concepts("plumber", name, "caller") == set()


@pytest.mark.parametrize(("name", "handler_position", "concept"), [
    ("pr_get", "args[last]", "route"),
    ("pr_post", "args[last]", "route"),
    ("pr_filter", "args[last]", "middleware"),
    ("pr_hook", "args[last]", "middleware"),
    ("register_serializer", "args[1]", "serializer"),
])
def test_a_handler_row_never_matches_the_registering_caller(
    name: str, handler_position: str, concept: str,
) -> None:
    assert _concepts("plumber", name, "caller") == set()
    # The row waits on a handler-argument context, in the shared vocabulary.
    assert _concepts("plumber", name, handler_position) == {concept}


@pytest.mark.parametrize("name", [
    "serializer_json", "serializer_csv", "serializer_html", "serializer_xml"])
def test_a_built_in_serializer_factory_is_no_serializer(name: str) -> None:
    for position in ("caller", "args[0]", "args[1]", "args[last]"):
        assert _concepts("plumber", name, position) == set()


_APP_R = (
    "library(shiny)\n"
    "ui <- fluidPage(sliderInput('n', 'N', 1, 100, 50), plotOutput('plot'))\n"
    "server <- function(input, output, session) {\n"
    "  output$plot <- renderPlot(hist(rnorm(input$n)))\n"
    "}\n"
    "launch <- function() runApp(shinyAppDir('inst/app'))\n"
    "shinyApp(ui = ui, server = server)\n"
)
_ROUTER_R = (
    "library(plumber)\n"
    "health <- function() list(ok = TRUE)\n"
    "build_router <- function() {\n"
    "  pr() %>%\n"
    "    pr_get('/health', health) %>%\n"
    "    pr_post('/echo', function(msg = '') list(msg = msg)) %>%\n"
    "    pr_filter('logger', function(req) plumber::forward()) %>%\n"
    "    pr_hook('exit', function() message('bye')) %>%\n"
    "    pr_mount('/v2', pr('v2.R')) %>%\n"
    "    pr_static('/assets', './www') %>%\n"
    "    pr_set_serializer(serializer_json())\n"
    "}\n"
    "add_ser <- function() register_serializer('custom', function(...) identity)\n"
    "ser_only <- function(p) pr_set_serializer(p, serializer_json())\n"
    "filter_only <- function(p) pr_filter(p, 'log', function(req) forward())\n"
    "routes_only <- function(p) pr_get(p, '/x', health)\n"
    "serve_tidy <- function() build_router() %>% pr_run(port = 8000)\n"
    "serve_r6 <- function() { api <- plumb('api.R'); api$run(port = 8000) }\n"
    "serve_bare <- function(x) run(x)\n"
    "from_pkg <- function() plumb_api('mypkg', 'api')\n"
)


def _enriched(tmp_path: Path, frameworks: set[str]):
    (tmp_path / "app.R").write_text(_APP_R)
    (tmp_path / "router.R").write_text(_ROUTER_R)
    result = analyze_r_files(tmp_path)
    symbols = result.symbols + synthesize_file_symbols_for_dangling_edges(
        result.symbols, result.edges)
    clear_pattern_cache()
    enrich_symbols(symbols, frameworks, result.usage_contexts)
    by_name = {s.name: s for s in symbols if s.kind == "function"}
    by_name["app.R"] = next(s for s in symbols if s.id == make_file_id("r", "app.R"))
    return symbols, result.edges, by_name


def _concepts_of(symbol) -> set[tuple[str, str]]:
    return {(c["concept"], c["framework"])
            for c in (symbol.meta or {}).get("concepts", [])}


def test_the_shipped_rows_end_to_end(tmp_path: Path) -> None:
    symbols, edges, by_name = _enriched(tmp_path, {"shiny", "plumber"})
    names = {s.id: n for n, s in by_name.items()}
    entries = detect_entrypoints(symbols, edges)
    wrong = {EntrypointKind.HTTP_ROUTE, EntrypointKind.MIDDLEWARE_HANDLER,
             EntrypointKind.SERIALIZER}
    assert [(names.get(ep.symbol_id, ep.symbol_id), ep.kind) for ep in entries
            if ep.kind in wrong] == []
    hosted = {(names[ep.symbol_id], ep.label) for ep in entries
              if ep.kind == EntrypointKind.HOSTED_APPLICATION}
    assert hosted == {
        ("app.R", "Shiny application"),
        ("launch", "Shiny application"),
        ("build_router", "Plumber application"),
        ("serve_r6", "Plumber application"),
        ("from_pkg", "Plumber application"),
    }
    assert _concepts_of(by_name["build_router"]) == {
        ("application", "plumber"), ("router", "plumber"),
        ("static_handler", "plumber")}
    for registrar in ("ser_only", "filter_only", "routes_only", "add_ser", "health"):
        assert _concepts_of(by_name[registrar]) == set(), registrar
    for runner in ("serve_tidy", "serve_r6", "serve_bare"):
        assert ("server", "plumber") in _concepts_of(by_name[runner]), runner
    assert _concepts_of(by_name["launch"]) == {
        ("application", "shiny"), ("server", "shiny")}
    assert _concepts_of(by_name["app.R"]) == {
        ("application", "shiny"), ("ui_component", "shiny"),
        ("input", "shiny"), ("output", "shiny")}
    assert _concepts_of(by_name["server"]) == {("output", "shiny")}


def test_pr_is_an_application_only_where_plumber_is_detected(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    _, _, with_plumber = _enriched(tmp_path / "a", {"plumber"})
    assert ("application", "plumber") in _concepts_of(with_plumber["build_router"])
    (tmp_path / "b").mkdir()
    _, _, without = _enriched(tmp_path / "b", {"shiny"})
    assert _concepts_of(without["build_router"]) == set()
