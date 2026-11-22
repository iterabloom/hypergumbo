# SPDX-License-Identifier: AGPL-3.0-or-later
"""The R analyzer records each call as a UsageContext on the symbol holding it.

R's framework rows (plumber.yaml, shiny.yaml) are ``usage:`` rows keyed on
the name of a call (``shinyApp``, ``plumb``, ``Plumber$new``, ...). A row
stamps its concept on the symbol a context's ``symbol_ref`` names. In R the
application object is a value bound to a variable (``app <- shinyApp(...)``)
or no binding at all (a bare ``shinyApp(ui, server)``, the last expression
of ``app.R``), and the analyzer has no variable symbols, so the context names
the CALLER: the innermost named function whose body holds the call, else the
file (``position`` ``caller``, as the JS/TS analyzer does for a module-level
bootstrap call). These tests assert those properties, plus one end-to-end
run through ``enrich_symbols`` and ``detect_entrypoints``.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import (
    make_file_id,
    synthesize_file_symbols_for_dangling_edges,
)
from hypergumbo_core.entrypoints import EntrypointKind, detect_entrypoints
from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_lang_common.r_lang import analyze_r_files


def _ctxs(result, name: str):
    return [c for c in result.usage_contexts if c.context_name == name]


def _fn_id(result, name: str) -> str:
    return next(s.id for s in result.symbols if s.kind == "function" and s.name == name)


def test_a_top_level_call_names_its_file(tmp_path: Path) -> None:
    (tmp_path / "app.R").write_text(
        "library(shiny)\n"
        "ui <- fluidPage('x')\n"
        "server <- function(input, output) {}\n"
        "shinyApp(ui = ui, server = server)\n"
    )
    result = analyze_r_files(tmp_path)
    [ctx] = _ctxs(result, "shinyApp")
    assert (ctx.kind, ctx.position, ctx.symbol_ref) == (
        "call", "caller", make_file_id("r", "app.R"))
    assert ctx.span.start_line == 4
    assert ctx.path == "app.R"
    [ui] = _ctxs(result, "fluidPage")
    assert ui.symbol_ref == make_file_id("r", "app.R")
    assert ui.metadata == {"assigned_to": "ui"}


def test_an_assigned_call_names_its_caller_and_records_the_binding(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.R").write_text(
        "app <- shinyApp(ui, server)\n"
        "run_api <- function() {\n"
        "  pr <- plumb('api.R')\n"
        "  Plumber$new() -> pr2\n"
        "  plumb('b.R')\n"
        "  pr$run(port = 8000)\n"
        "}\n"
    )
    result = analyze_r_files(tmp_path)
    [app] = _ctxs(result, "shinyApp")
    assert app.symbol_ref == make_file_id("r", "app.R")
    assert app.metadata == {"assigned_to": "app"}
    plumbs = _ctxs(result, "plumb")
    # One context per (holder, name): the second plumb() in run_api adds none.
    assert [(c.symbol_ref, c.metadata) for c in plumbs] == [
        (_fn_id(result, "run_api"), {"assigned_to": "pr"})]
    [new] = _ctxs(result, "Plumber$new")
    assert new.metadata == {"assigned_to": "pr2"}
    [run] = _ctxs(result, "pr$run")
    assert (run.symbol_ref, run.metadata) == (_fn_id(result, "run_api"), {})


def test_qualified_dollar_and_piped_forms(tmp_path: Path) -> None:
    (tmp_path / "api.R").write_text(
        "start <- function() {\n"
        "  a <- Plumber$new('api.R')\n"
        "  b <- plumber::Plumber$new()\n"
        "  plumber::plumb(file = 'api.R') |> pr_run()\n"
        "  pr() %>% pr_get('/x', h) %>% pr_run(port = 1)\n"
        "  shiny:::shinyApp(ui, server)\n"
        "  s4obj@method(1)\n"
        "  `shinyApp`(ui, server)\n"
        "}\n"
    )
    result = analyze_r_files(tmp_path)
    start = _fn_id(result, "start")
    got = {(c.context_name, c.symbol_ref) for c in result.usage_contexts}
    for name in ("Plumber$new", "plumb", "pr_run", "pr", "pr_get",
                 "shinyApp", "s4obj@method"):
        assert (name, start) in got, name
    [plumb] = _ctxs(result, "plumb")
    assert plumb.metadata == {"package": "plumber"}
    [new] = _ctxs(result, "Plumber$new")
    # The first Plumber$new is the one kept; its binding is recorded.
    assert new.metadata == {"assigned_to": "a"}
    [shiny_app] = _ctxs(result, "shinyApp")
    assert shiny_app.metadata == {"package": "shiny"}


def test_a_call_in_an_anonymous_function_names_the_named_one_around_it(
    tmp_path: Path,
) -> None:
    (tmp_path / "server.R").write_text(
        "server <- function(input, output) {\n"
        "  output$plot <- renderPlot({ hist(rnorm(10)) })\n"
        "  lapply(1:3, function(i) observe({ i }))\n"
        "}\n"
        "shinyApp(ui, function(input, output) reactive(1))\n"
    )
    result = analyze_r_files(tmp_path)
    server = _fn_id(result, "server")
    assert {c.symbol_ref for c in _ctxs(result, "renderPlot")} == {server}
    assert {c.symbol_ref for c in _ctxs(result, "observe")} == {server}
    assert {c.symbol_ref for c in _ctxs(result, "reactive")} == {
        make_file_id("r", "server.R")}


def test_a_callee_with_no_name_gives_no_context(tmp_path: Path) -> None:
    (tmp_path / "x.R").write_text(
        "f <- function() {\n"
        "  make()(1)\n"
        "  obj[['run']](1)\n"
        "  get_app()$run()\n"
        "  pkg::'quoted'(1)\n"
        "}\n"
    )
    result = analyze_r_files(tmp_path)
    assert sorted(c.context_name for c in result.usage_contexts) == ["get_app", "make"]


def test_a_file_holding_only_calls_gets_its_file_symbol(tmp_path: Path) -> None:
    """Nothing else the analyzer emits names this file, so the orchestrator
    would mint no file node for the context to land on."""
    (tmp_path / "app.R").write_text(
        "shiny::shinyApp(shiny::fluidPage(), function(input, output) {})\n"
    )
    (tmp_path / "lib.R").write_text("library(shiny)\nfluidPage()\n")
    result = analyze_r_files(tmp_path)
    files = [s for s in result.symbols if s.kind == "file"]
    assert [(s.id, s.path, s.language) for s in files] == [
        (make_file_id("r", "app.R"), "app.R", "r")]
    assert files[0].span.end_line == 2
    # lib.R's file node comes from its imports edge (the orchestrator's
    # synthesis), not from the analyzer.
    assert {c.symbol_ref for c in _ctxs(result, "fluidPage")} == {
        make_file_id("r", "app.R"), make_file_id("r", "lib.R")}


def test_shiny_and_plumber_apps_get_a_hosted_application_entry(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.R").write_text(
        "library(shiny)\n"
        "library(plumber)\n"
        "ui <- fluidPage('x')\n"
        "server <- function(input, output) {}\n"
        "shinyApp(ui, server)\n"
        "run_api <- function() {\n"
        "  pr <- plumb('api.R')\n"
        "  pr$run(port = 8000)\n"
        "}\n"
        "helper <- function() Plumber$new()\n"
    )
    result = analyze_r_files(tmp_path)
    symbols = result.symbols + synthesize_file_symbols_for_dangling_edges(
        result.symbols, result.edges)
    clear_pattern_cache()
    enrich_symbols(symbols, {"shiny", "plumber"}, result.usage_contexts)
    by_id = {s.id: s for s in symbols}

    def concepts(sym_id: str) -> set[tuple[str, str]]:
        return {(c["concept"], c["framework"])
                for c in (by_id[sym_id].meta or {}).get("concepts", [])}

    file_id = make_file_id("r", "app.R")
    assert concepts(file_id) == {("application", "shiny"), ("ui_component", "shiny")}
    # pr$run() is the R6 form of running the API (plumber.yaml's server row).
    assert concepts(_fn_id(result, "run_api")) == {
        ("application", "plumber"), ("server", "plumber")}
    assert concepts(_fn_id(result, "helper")) == {("application", "plumber")}
    assert concepts(_fn_id(result, "server")) == set()
    entries = {(ep.symbol_id, ep.kind, ep.label)
               for ep in detect_entrypoints(symbols, result.edges)
               if ep.kind == EntrypointKind.HOSTED_APPLICATION}
    assert entries == {
        (file_id, EntrypointKind.HOSTED_APPLICATION, "Shiny application"),
        (_fn_id(result, "run_api"), EntrypointKind.HOSTED_APPLICATION,
         "Plumber application"),
        (_fn_id(result, "helper"), EntrypointKind.HOSTED_APPLICATION,
         "Plumber application"),
    }
