# SPDX-License-Identifier: AGPL-3.0-or-later
"""Solidity, V and Twig ids fixed in the WI-tusad stable_id scheme bump
(ADR-0035 §6).

Solidity hashed the ABSOLUTE file path, so the same repository checked out in
two places had two sets of ids. V methods hashed neither their receiver nor
their own parameters (the receiver list sat where ``compute_stable_id`` looks
for parameters), so ``fn (a A) run()`` and ``fn (b B) run(x int)`` tied. Twig
reported every directive on the line above it (the grammar folds leading
whitespace into the opening delimiter) and hashed no scope, so the same call in
two blocks tied.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_file_stable_id
from hypergumbo_core.overload_arity import PARAMETERS_KEY
from hypergumbo_lang_extended1.solidity import analyze_solidity
from hypergumbo_lang_extended1.twig import analyze_twig
from hypergumbo_lang_extended1.v_lang import VAnalyzer, analyze_v


def _symbols(analyze, root: Path, rel: str, text: str) -> list:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return [s for s in analyze(root).symbols if s.kind != "file"]


def _position(sym) -> tuple:
    return (sym.kind, sym.name, sym.span.start_line, sym.span.start_col)


def _assert_deletion_moves_nothing(analyze, tmp_path, rel, text, deleted) -> int:
    """Delete ``deleted`` (blanked, so lines keep their numbers) and check every
    surviving symbol, matched by its own position, keeps its non-null id."""
    edited = text.replace(deleted, "\n" * deleted.count("\n"))
    before: dict[tuple, list] = {}
    for s in _symbols(analyze, tmp_path / "with", rel, text):
        before.setdefault(_position(s), []).append(s.stable_id)
    after = _symbols(analyze, tmp_path / "without", rel, edited)
    for s in after:
        assert s.stable_id is not None, s.name
        assert s.stable_id in before[_position(s)], (s.name, s.span.start_line)
    return len(after)


# -- solidity ------------------------------------------------------------------

_SOL = (
    "pragma solidity ^0.8.0;\n"
    "uint constant LIMIT = 3;\n"
    "contract Demo {\n"
    "    function run() public pure returns (uint) { return 1; }\n"
    "    function run(uint x) public pure returns (uint) { return x; }\n"
    "    uint public size;\n"
    "}\n"
)


def test_solidity_ids_do_not_depend_on_the_checkout(tmp_path: Path) -> None:
    here = _symbols(analyze_solidity, tmp_path / "a", "src/Demo.sol", _SOL)
    there = _symbols(analyze_solidity, tmp_path / "b" / "deeper", "src/Demo.sol", _SOL)
    assert {s.kind for s in here} >= {"method", "field", "variable"}
    assert [(_position(s), s.stable_id) for s in here] == [
        (_position(s), s.stable_id) for s in there]


def test_solidity_symbols_carry_the_repo_relative_path(tmp_path: Path) -> None:
    syms = _symbols(analyze_solidity, tmp_path, "src/Demo.sol", _SOL)
    assert {s.path for s in syms} == {"src/Demo.sol"}


# -- v -------------------------------------------------------------------------

_V = (
    "module demo\n\nstruct A {}\nstruct B {}\n\n"
    "fn (a A) run() int {\n\treturn 1\n}\n"
    "fn (b B) run(x int) int {\n\treturn x\n}\n"
    "fn (mut b B) push(mut dx int, xs ...int) {}\n"
    "fn helper() int {\n\treturn 2\n}\n"
)


def test_v_methods_of_two_receivers_get_distinct_ids(tmp_path: Path) -> None:
    runs = [s for s in _symbols(analyze_v, tmp_path, "src/demo.v", _V) if s.name == "run"]
    assert len(runs) == 2 and len({s.stable_id for s in runs}) == 2


def test_v_deleting_an_earlier_method_moves_nothing(tmp_path: Path) -> None:
    survivors = _assert_deletion_moves_nothing(
        analyze_v, tmp_path, "src/demo.v", _V, "fn (a A) run() int {\n\treturn 1\n}\n",
    )
    # run, push, helper (an empty `struct A {}` is a parse ERROR in the grammar)
    assert survivors == 3


def test_v_parameters_exclude_the_receiver(tmp_path: Path) -> None:
    syms = {s.name: s for s in _symbols(analyze_v, tmp_path, "src/demo.v", _V)
            if s.kind == "function"}
    assert syms["helper"].meta[PARAMETERS_KEY] == []
    push = syms["push"].meta[PARAMETERS_KEY]
    assert [(p["name"], p["type"], p.get("variadic", False)) for p in push] == [
        ("dx", "mut int", False), ("xs", "int", True)]
    run = next(s for s in _symbols(analyze_v, tmp_path / "again", "src/demo.v", _V)
               if s.name == "run" and s.span.start_line == 9)
    assert [(p["name"], p["type"]) for p in run.meta[PARAMETERS_KEY]] == [("x", "int")]


def test_v_plain_function_id_is_unchanged(tmp_path: Path) -> None:
    """Only methods changed: a plain function still mints compute_stable_id."""
    root = tmp_path / "r"
    helper = next(s for s in _symbols(analyze_v, root, "src/demo.v", _V)
                  if s.name == "helper")
    analyzer = VAnalyzer()
    tree = analyzer._create_parser().parse((root / "src/demo.v").read_bytes())
    node = next(n for n in tree.root_node.children
                if n.type == "function_declaration" and b"helper" in n.text)
    assert helper.stable_id == analyzer.compute_stable_id(
        node, kind="fn", name="helper",
        file_stable_id=make_file_stable_id("v", "src/demo.v"),
    )


# -- twig ----------------------------------------------------------------------

_TWIG = (
    "{% macro first(x) %}{{ x }}{% endmacro %}\n"
    "{% macro run(x, y) %}{{ x }}{% endmacro %}\n"
    "{% block header %}\n"
    "  {% if user %}{{ greet(user) }}{% endif %}\n"
    "{% endblock %}\n"
    "{% block content %}\n"
    "  {% for item in items %}{{ greet(item) }}{% endfor %}\n"
    "  {{ greet(user) }}\n"
    "{% endblock %}\n"
    "{% block title \"Hi\" %}\n"
    "{{ greet(user) }}\n"
)


def test_twig_symbols_start_on_their_own_line(tmp_path: Path) -> None:
    syms = _symbols(analyze_twig, tmp_path, "t/page.twig", _TWIG)
    lines = {(s.kind, s.name, s.span.start_line, s.span.start_col) for s in syms}
    assert ("macro", "run", 2, 0) in lines
    assert ("block", "header", 3, 0) in lines
    assert ("conditional", "if user", 4, 2) in lines
    assert ("call_site", "greet", 8, 2) in lines


def test_twig_block_shorthand_is_named_by_its_first_argument(tmp_path: Path) -> None:
    syms = _symbols(analyze_twig, tmp_path, "t/page.twig", _TWIG)
    assert ("block", "title") in {(s.kind, s.name) for s in syms}


def test_twig_same_call_in_different_scopes_gets_distinct_ids(tmp_path: Path) -> None:
    greets = [s for s in _symbols(analyze_twig, tmp_path, "t/page.twig", _TWIG)
              if s.name == "greet"]
    # in header/if, in content/for, in content, after the shorthand block
    # (which opened no scope, so the last one is at top level)
    assert len(greets) == 4 and len({s.stable_id for s in greets}) == 4


def test_twig_deleting_an_earlier_macro_or_block_moves_nothing(tmp_path: Path) -> None:
    assert _assert_deletion_moves_nothing(
        analyze_twig, tmp_path / "m", "t/page.twig", _TWIG,
        "{% macro first(x) %}{{ x }}{% endmacro %}\n",
    ) == 10
    assert _assert_deletion_moves_nothing(
        analyze_twig, tmp_path / "b", "t/page.twig", _TWIG,
        "{% block header %}\n  {% if user %}{{ greet(user) }}{% endif %}\n{% endblock %}\n",
    ) == 8


def test_twig_edges_start_on_their_own_line(tmp_path: Path) -> None:
    root = tmp_path / "e"
    target = root / "t" / "page.twig"
    target.parent.mkdir(parents=True)
    target.write_text("<p>\n{% extends \"base.twig\" %}\n  {% include \"p.twig\" %}\n")
    lines = sorted((e.edge_type, e.line) for e in analyze_twig(root).edges)
    assert lines == [("extends", 2), ("includes", 3)]
