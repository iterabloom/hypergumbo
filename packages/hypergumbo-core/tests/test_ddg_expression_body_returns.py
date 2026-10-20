# SPDX-License-Identifier: AGPL-3.0-or-later
"""An expression-bodied callable returns its value without a return statement (WI-sodoj).

The INV-komoj return lift decides "this function returns the source's value"
from the function's RETURN STATEMENTS (``ddg_build.return_statement_spans``).
Two common forms have none and return a value anyway, so a secret a helper
hands back that way was a false all-clear:

* Rust's tail expression -- ``fn get() -> String { env::var("TOOL").unwrap() }``,
  the language's idiomatic return; ``block_tail_returns`` in its cfg mapping;
* an expression body -- ``const get = () => process.env.TOOL`` and Python's
  ``lambda: os.environ["K"]``: the body IS the value.
"""
from __future__ import annotations

from typing import Any

import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core.cfg import clear_cfg_mapping_cache, load_cfg_mapping
from hypergumbo_core.ddg_build import LanguageDdgSpec, return_statement_spans


def _spans(lang: str, source: str, function_type: str) -> list:
    clear_cfg_mapping_cache()
    src = source.encode()
    root = tree_sitter.Parser(get_language(lang)).parse(src).root_node
    stack: list[Any] = [root]
    while stack:
        node = stack.pop()
        if node.type == function_type:
            spec = LanguageDdgSpec(language=lang, function_node_types=frozenset({function_type}))
            return [
                (s.start, s.end, sorted(sorted(r) for r in s.results))
                for s in return_statement_spans(
                    node.child_by_field_name("body"), load_cfg_mapping(lang), spec, src,
                )
            ]
        stack.extend(reversed(node.children))
    raise AssertionError("no function")  # pragma: no cover


def test_a_rust_tail_expression_is_a_return() -> None:
    assert _spans("rust", (
        "fn get() -> String {\n"
        "    let k = \"TOOL\";\n"
        "    env::var(k).unwrap()\n"
        "}\n"
    ), "function_item") == [(3, 3, [["env", "k", "var"]])]  # `unwrap` is a field name


def test_a_rust_block_ending_in_a_statement_returns_nothing_extra() -> None:
    """``x;`` is a statement (unit), an item is not a value, and an explicit
    ``return`` keeps its own span."""
    assert _spans("rust", (
        "fn f(x: u32) {\n"
        "    g(x);\n"
        "}\n"
    ), "function_item") == []
    assert _spans("rust", (
        "fn h() -> u32 {\n"
        "    return 1;\n"
        "}\n"
    ), "function_item") == [(2, 2, [[]])]


def test_a_js_arrow_expression_body_is_a_return() -> None:
    # Identifiers only, as for a return statement: ``env`` is a property name.
    assert _spans("javascript", "const get = () => process.env.TOOL;\n", "arrow_function") == [
        (1, 1, [["process"]])]


def test_a_js_arrow_block_body_keeps_its_return_statements() -> None:
    assert _spans("javascript", (
        "const get = () => {\n"
        "  return process.env.TOOL;\n"
        "};\n"
    ), "arrow_function") == [(2, 2, [["process"]])]


def test_a_python_lambda_body_is_a_return() -> None:
    assert _spans("python", "f = lambda: os.environ['K']\n", "lambda") == [
        (1, 1, [["environ", "os"]])]


def test_an_empty_rust_body_returns_nothing() -> None:
    assert _spans("rust", "fn f() {\n    // nothing\n}\n", "function_item") == []
