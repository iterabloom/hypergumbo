# SPDX-License-Identifier: AGPL-3.0-or-later
"""The async marker in the shared stable_id formulas (ADR-0035 §1).

``is_async`` appends ``:async`` to the hash input of both formulas, so an
async declaration gets its own id and every other id is byte-identical to the
formula without the flag. :func:`declares_async` reads the marker from a
tree-sitter declaration: an ``async`` / ``async*`` / ``suspend`` token among
its direct children or inside a modifier wrapper, never inside a body or a
parameter list.
"""

from __future__ import annotations

from tree_sitter_language_pack import get_parser

from hypergumbo_core.analyze.base import (
    assemble_stable_id,
    declares_async,
    make_typed_stable_id,
)


def test_the_flag_changes_only_flagged_ids() -> None:
    plain = assemble_stable_id("function", 1, "False,False,False", "", "", "f", "f")
    assert assemble_stable_id(
        "function", 1, "False,False,False", "", "", "f", "f", is_async=False) == plain
    assert assemble_stable_id(
        "function", 1, "False,False,False", "", "", "f", "f", is_async=True) != plain
    typed = make_typed_stable_id("function", "(int)int", name="f", qualified_name="f")
    assert make_typed_stable_id(
        "function", "(int)int", name="f", qualified_name="f", is_async=False) == typed
    assert make_typed_stable_id(
        "function", "(int)int", name="f", qualified_name="f", is_async=True) != typed


def _first(lang: str, src: str, node_type: str):
    tree = get_parser(lang).parse(src.encode())
    stack = [tree.root_node]
    while stack:
        node = stack.pop(0)
        if node.type == node_type:
            return node
        stack.extend(node.children)
    raise AssertionError(f"no {node_type} in {src!r}")


def test_declares_async_reads_the_declarations_own_marker() -> None:
    cases = [
        ("javascript", "async function f(a) {}", "function_declaration", True),
        ("javascript", "function f(a) {}", "function_declaration", False),
        ("rust", "pub async fn f() {}", "function_item", True),
        ("rust", "pub fn f() {}", "function_item", False),
        ("csharp", "class C { public async Task M() {} }", "method_declaration", True),
        ("csharp", "class C { public Task M() { return null; } }", "method_declaration", False),
        ("kotlin", "suspend fun f() {}", "function_declaration", True),
        ("kotlin", "fun f() {}", "function_declaration", False),
        ("swift", "func f() async -> Int { return 1 }", "function_declaration", True),
        ("swift", "func f() -> Int { return 1 }", "function_declaration", False),
        ("dart", "Future<int> f() async { return 1; }", "function_body", True),
        ("dart", "Stream<int> f() async* { yield 1; }", "function_body", True),
        ("dart", "int f() { return 1; }", "function_body", False),
    ]
    for lang, src, node_type, expected in cases:
        assert declares_async(_first(lang, src, node_type)) is expected, (lang, src)


def test_a_marker_inside_the_body_or_parameters_is_not_the_declarations() -> None:
    """An async arrow in the body, or an async callback parameter, does not
    make the enclosing function async."""
    body = _first("javascript", "function f() { const g = async () => 1; }",
                  "function_declaration")
    assert declares_async(body) is False
    param = _first("typescript",
                   "function f(cb: () => Promise<void> = async () => {}) {}",
                   "function_declaration")
    assert declares_async(param) is False
