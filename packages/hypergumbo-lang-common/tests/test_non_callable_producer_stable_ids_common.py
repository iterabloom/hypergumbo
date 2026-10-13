# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer stable_ids for the common package's non-callable kinds (WI-tusad).

These analyzers computed no ``stable_id`` for the kinds below, which rode the
ADR-0035 backstop floor key ``(kind, language, path, name)``: the same LaTeX
``\\subsection`` under two sections, a ``[linux]`` and a ``[macos]`` just
recipe of one name, the same Handlebars partial inside two blocks, two
Terraform configurations of one provider, each shared the key and was left
null. Each now mints ``overload_arity.scoped_stable_id`` (or
``declared_arity_stable_id``) with the scope its grammar nests the symbol in.
Julia also emits what it dropped: ``function Base.show(...)`` and the
zero-method ``function f end``.

Pinned per analyzer, on its own output: every symbol of a migrated kind holds a
non-null id that is not the floor key (matched by kind, name and position,
never through a dict keyed on name alone), the exact value for a scoped symbol,
and the delete-an-earlier-sibling property (the deleted lines are kept as blank
lines, so each survivor matches itself by position).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from hypergumbo_core.analyze.base import AnalysisResult, floor_stable_id_key
from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import (
    declared_arity, declared_arity_stable_id, parameter_entry, scoped_stable_id,
)
from hypergumbo_lang_common.erlang import analyze_erlang
from hypergumbo_lang_common.graphql import analyze_graphql_files
from hypergumbo_lang_common.handlebars import _scopes_at, analyze_handlebars
from hypergumbo_lang_common.hcl import analyze_hcl
from hypergumbo_lang_common.julia import analyze_julia
from hypergumbo_lang_common.just import analyze_just
from hypergumbo_lang_common.latex import analyze_latex
from hypergumbo_lang_common.r_lang import analyze_r_files

Analyze = Callable[[Path], AnalysisResult]
Key = tuple[str, str, int, int]


def _symbols(analyze: Analyze, root: Path, name: str, text: str) -> list[Symbol]:
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    return list(analyze(root).symbols)


def _at(symbols: list[Symbol], kinds: set[str]) -> dict[Key, Symbol]:
    picked = [s for s in symbols if s.kind in kinds]
    by_pos = {(s.kind, s.name, s.span.start_line, s.span.start_col): s for s in picked}
    assert len(by_pos) == len(picked), "two symbols of one kind and name at one position"
    return by_pos


def _assert_producer_ids(by_pos: dict[Key, Symbol], lang: str, path: str) -> None:
    """Every symbol holds its own non-null id, none of them the floor key."""
    assert by_pos
    for key, sym in by_pos.items():
        assert sym.stable_id is not None, key
        assert sym.stable_id != floor_stable_id_key(sym.kind, lang, path, sym.name), key


def _assert_deletion_moves_nothing(
    analyze: Analyze, tmp_path: Path, name: str, text: str, deleted: str,
    kinds: set[str], lang: str,
) -> dict[Key, Symbol]:
    """Delete ``deleted`` (replaced by as many blank lines) and compare."""
    assert text.count(deleted) == 1
    edited = text.replace(deleted, "\n" * deleted.count("\n"))
    before = _at(_symbols(analyze, tmp_path / "r1", name, text), kinds)
    after = _at(_symbols(analyze, tmp_path / "r2", name, edited), kinds)
    assert 0 < len(after) < len(before)  # reach
    _assert_producer_ids(before, lang, name)
    for key, sym in after.items():
        assert sym.stable_id == before[key].stable_id, key
    return before


def test_latex_sections_and_labels_are_scoped_by_enclosing_sections(tmp_path: Path) -> None:
    text = (
        "\\documentclass{article}\n\\newcommand{\\foo}{bar}\n"
        "\\newenvironment{note}{}{}\n\\begin{document}\n"
        "\\section{Intro}\n\\subsection{Setup}\n\\label{sec:a}\n"
        "\\section{Usage}\n\\subsection{Setup}\n\\label{sec:b}\n\\end{document}\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_latex, tmp_path, "main.tex", text,
        "\\section{Intro}\n\\subsection{Setup}\n\\label{sec:a}\n",
        {"section", "label", "command", "environment"}, "latex",
    )
    assert before[("section", "Setup", 9, 0)].stable_id == scoped_stable_id(
        kind="section", lang="latex", path="main.tex", name="Setup", scope=("Usage",),
    )
    assert before[("label", "sec:b", 10, 0)].stable_id == scoped_stable_id(
        kind="label", lang="latex", path="main.tex", name="sec:b",
        scope=("Usage", "Setup"),
    )


def test_r_source_is_scoped_by_enclosing_function(tmp_path: Path) -> None:
    text = (
        "setup_a <- function() {\n  source(\"helpers.R\")\n}\n"
        "setup_b <- function() {\n  source(\"helpers.R\")\n}\nsource(\"main.R\")\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_r_files, tmp_path, "R/load.R", text,
        "setup_a <- function() {\n  source(\"helpers.R\")\n}\n", {"source"}, "r",
    )
    sym = next(s for k, s in before.items() if k[2] == 5)
    assert sym.stable_id == declared_arity_stable_id(
        kind="source", lang="r", path="R/load.R", name="helpers.R",
        qualified_name="setup_b/helpers.R", arity=None,
    )


def test_just_recipes_take_attributes_and_parameters(tmp_path: Path) -> None:
    text = (
        "[linux]\nbuild:\n    echo linux\n\n"
        "[macos]\nbuild:\n    echo mac\nalias b := build\nversion := \"1\"\n"
        "[group('ci'), private]\ntest target='x' *args:\n    echo {{args}}\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_just, tmp_path, "justfile", text,
        "[linux]\nbuild:\n    echo linux\n", {"recipe", "alias"}, "just",
    )
    test = before[("recipe", "test", 11, 0)]
    assert test.meta["parameters"] == [
        parameter_entry("target", default=True), parameter_entry("args", variadic=True),
    ]
    assert test.stable_id == scoped_stable_id(
        kind="recipe", lang="just", path="justfile", name="test",
        arity=declared_arity(test.meta), decorators="group('ci'),private",
    )
    assert before[("recipe", "build", 6, 0)].stable_id == scoped_stable_id(
        kind="recipe", lang="just", path="justfile", name="build",
        arity=declared_arity({"parameters": []}), decorators="macos",
    )


def test_graphql_floor_kinds_take_producer_ids_and_table_kinds_keep_theirs(
    tmp_path: Path,
) -> None:
    text = (
        "input NewUser { name: String }\nscalar Date\ndirective @auth on FIELD_DEFINITION\n"
        "type Query { u: Int }\nunion Result = Query\nenum Role { ADMIN }\n"
        "query GetUser { u }\nfragment F on Query { u }\nmutation M { u }\n"
        "subscription S { u }\n"
    )
    syms = _symbols(analyze_graphql_files, tmp_path, "schema.graphql", text)
    floor_kinds = {"input", "scalar", "directive", "union", "query", "fragment",
                   "mutation", "subscription"}
    by_pos = _at(syms, floor_kinds)
    assert {k[0] for k in by_pos} == floor_kinds
    _assert_producer_ids(by_pos, "graphql", "schema.graphql")
    assert by_pos[("input", "NewUser", 1, 0)].stable_id == scoped_stable_id(
        kind="input", lang="graphql", path="schema.graphql", name="NewUser",
    )
    assert [s.stable_id for s in syms if s.kind in ("type", "enum")] == [None, None]


def test_handlebars_tags_are_scoped_by_open_blocks(tmp_path: Path) -> None:
    text = (
        "{{#if user}}\n  {{> header}}\n{{/if}}\n"
        "{{#if admin}}\n  {{> header}}\n{{/if}}\n{{#each items}}{{this}}{{/each}}\n"
        "{{#formatDate when}}x{{/formatDate}}\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_handlebars, tmp_path, "page.hbs", text,
        "{{#if user}}\n  {{> header}}\n{{/if}}\n", {"partial", "block", "helper"},
        "handlebars",
    )
    assert before[("partial", "header", 5, 2)].stable_id == scoped_stable_id(
        kind="partial", lang="handlebars", path="page.hbs", name="header",
        scope=("if admin",),
    )
    assert before[("block", "if", 4, 0)].stable_id == scoped_stable_id(
        kind="block", lang="handlebars", path="page.hbs", name="if",
        scope=("if admin",),
    )


def test_handlebars_scope_tracking_pops_to_the_matching_block() -> None:
    stack: list[tuple[str, str]] = [("each", "each rows")]
    chains = _scopes_at("{{#if b}}{{> p}}{{/each}}{{> q}}{{/nothing}}", stack)
    assert chains == {0: ("each rows", "if b"), 9: ("each rows", "if b"), 25: ()}
    assert stack == []


def test_erlang_records_take_producer_ids(tmp_path: Path) -> None:
    text = "-module(demo).\n-record(first, {a}).\n-record(point, {x, y}).\n"
    before = _assert_deletion_moves_nothing(
        analyze_erlang, tmp_path, "src/demo.erl", text, "-record(first, {a}).\n",
        {"record"}, "erlang",
    )
    assert before[("record", "point", 3, 0)].stable_id == declared_arity_stable_id(
        kind="record", lang="erlang", path="src/demo.erl", name="point",
        qualified_name="point", arity=None,
    )


def test_hcl_provider_alias_is_its_scope(tmp_path: Path) -> None:
    text = (
        "provider \"aws\" {\n  region = \"us-east-1\"\n}\n"
        "provider \"aws\" {\n  alias  = \"west\"\n  region = \"us-west-2\"\n}\n"
        "provider \"google\" {}\n"
        "resource \"aws_s3_bucket\" \"b\" {\n  bucket = \"x\"\n}\n"
        "data \"aws_ami\" \"a\" {\n  most_recent = true\n}\n"
        "output \"o\" {\n  value = 1\n}\nlocals {\n  l = 1\n}\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_hcl, tmp_path, "main.tf", text,
        "provider \"aws\" {\n  region = \"us-east-1\"\n}\n",
        {"provider", "resource", "data", "output", "local"}, "hcl",
    )
    assert before[("provider", "provider.aws", 4, 0)].stable_id == scoped_stable_id(
        kind="provider", lang="hcl", path="main.tf", name="provider.aws", scope=("west",),
    )
    assert before[("provider", "provider.google", 8, 0)].stable_id == scoped_stable_id(
        kind="provider", lang="hcl", path="main.tf", name="provider.google",
    )


def test_julia_emits_qualified_methods_and_zero_method_generics(tmp_path: Path) -> None:
    """``function Base.show(...)``, ``Base.length(x) = ...`` and ``function f
    end`` emitted no symbol; a generic and a zero-parameter method of one
    name hold different ids."""
    text = (
        "module M\nfunction first end\nfunction f end\nf() = 1\n"
        "function Base.show(io::IO, x::Int)\n    print(io, x)\nend\n"
        "Base.length(x::Foo) = 1\nend\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_julia, tmp_path, "src/m.jl", text, "function first end\n",
        {"function"}, "julia",
    )
    assert {k[1] for k in before} == {"first", "f", "Base.show", "Base.length"}
    generic = before[("function", "f", 3, 0)]
    assert generic.stable_id == declared_arity_stable_id(
        kind="function", lang="julia", path="src/m.jl", name="f",
        qualified_name="M.f", arity=None, decorators="generic",
    )
    assert generic.stable_id != before[("function", "f", 4, 0)].stable_id
    show = before[("function", "Base.show", 5, 0)]
    assert show.stable_id == declared_arity_stable_id(
        kind="function", lang="julia", path="src/m.jl", name="Base.show",
        qualified_name="M.Base.show", arity=declared_arity(show.meta),
    )


def test_julia_callable_object_definitions_still_emit_nothing(tmp_path: Path) -> None:
    """A callable-object method (``function (o::Foo)(x)``, ``(o::Foo)(y) =``)
    has no name in its call head, and is neither a method of a named function
    nor a zero-method generic: nothing is invented for it."""
    syms = _symbols(analyze_julia, tmp_path, "src/o.jl",
                    "function (o::Foo)(x)\n    x\nend\n(o::Foo)(y) = y\n")
    assert [s for s in syms if s.kind == "function"] == []
