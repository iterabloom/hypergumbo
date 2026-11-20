# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Rust ``#[cfg(...)]`` and a Swift ``#if`` branch are part of the scope a
stable_id hashes.

Conditional compilation lets one file declare a name twice, once per
configuration: ``#[cfg(unix)] fn open()`` beside ``#[cfg(windows)] fn
open()``, two ``impl Foo`` blocks under ``feature = "std"`` and its negation,
two ``mod imp``; in Swift ``#if os(iOS) func f() #else func f() #endif``.
Until scheme v17 neither guard was an input, so each pair hashed one text and
tied; the run's within-file occurrence split numbered them by order, and
deleting the first moved the second's id onto the first's.

The guard is hashed as written, layout dropped (``layout_free``): Rust's
``cfg`` attributes on an item and on every enclosing item (sorted when one
item has several; ``cfg_attr`` gates no item and is not hashed); Swift's
``#if`` / ``#elseif`` / ``#else`` branch enclosing a declaration at each
level, a branch named by its chain's ``#if`` condition and its own (``#if A
#elseif B``, ``#if A #else``). An unguarded declaration hashes what it did.

Each case runs the analyzer directly, paths made repo-relative and the
backstop's table formula applied as the pipeline does: no two symbols of the
file share a stable_id, and blanking the first alternative (its lines kept)
moves no survivor's id. Survivors are matched by (kind, name, line, column).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import populate_kind_stable_ids
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.rust import _rust_hash_scope, analyze_rust
from hypergumbo_lang_mainstream.swift import _swift_scope, analyze_swift


def _symbols(analyze: Callable[[Path], object], root: Path, rel: str, text: str) -> list[Symbol]:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    result = analyze(root)
    symbols = [
        s for s in result.symbols  # type: ignore[attr-defined]
        if s.kind != "file" and s.path.endswith(rel)
    ]
    for sym in symbols:
        sym.path = rel
    populate_kind_stable_ids(symbols)
    return symbols


def _position(sym: Symbol) -> tuple[str, str, int, int]:
    return (sym.kind, sym.name, sym.span.start_line, sym.span.start_col)


def _check(
    analyze: Callable[[Path], object], tmp_path: Path, rel: str,
    head: str, earlier: str, rest: str,
) -> None:
    text = head + earlier + rest
    with_sibling = _symbols(analyze, tmp_path / "with", rel, text)
    ids = [s.stable_id for s in with_sibling]
    assert None not in ids, [s.name for s in with_sibling if s.stable_id is None]
    assert len(set(ids)) == len(ids), sorted(
        (s.kind, s.name, s.span.start_line) for s in with_sibling if ids.count(s.stable_id) > 1
    )
    before: dict[tuple[str, str, int, int], list[str | None]] = {}
    for s in with_sibling:
        before.setdefault(_position(s), []).append(s.stable_id)
    edited = head + "\n" * earlier.count("\n") + rest
    after = _symbols(analyze, tmp_path / "without", rel, edited)
    assert len(after) < len(with_sibling)
    for s in after:
        assert s.stable_id in before.get(_position(s), []), (s.kind, s.name, s.span.start_line)


#: (head, earlier, rest): blanking ``earlier`` leaves a valid file.
RUST_CASES: dict[str, tuple[str, str, str]] = {
    "fn": ("", "#[cfg(unix)]\npub fn open() -> i32 { 1 }\n",
           "#[cfg(windows)]\npub fn open() -> i32 { 2 }\n"),
    "impl-block": (
        "pub struct Foo;\n",
        "#[cfg(feature = \"std\")]\nimpl Foo { pub fn f(&self) -> i32 { 1 } }\n",
        "#[cfg(not(feature = \"std\"))]\nimpl Foo { pub fn f(&self) -> i32 { 2 } }\n",
    ),
    "inline-mod": ("", "#[cfg(unix)]\nmod imp { pub fn g() -> i32 { 1 } }\n",
                   "#[cfg(windows)]\nmod imp { pub fn g() -> i32 { 2 } }\n"),
    "method": (
        "pub struct Foo;\nimpl Foo {\n",
        "    #[cfg(debug_assertions)]\n    pub fn h(&self) {}\n",
        "    #[cfg(not(debug_assertions))]\n    pub fn h(&self) {}\n}\n",
    ),
    "struct-and-fields": ("", "#[cfg(unix)]\npub struct Bar { x: i32 }\n",
                          "#[cfg(windows)]\npub struct Bar { x: i32 }\n"),
    "enum-and-variants": ("", "#[cfg(unix)]\npub enum E { A }\n",
                          "#[cfg(windows)]\npub enum E { A }\n"),
    "field": ("pub struct Q {\n", "    #[cfg(unix)]\n    x: i32,\n",
              "    #[cfg(windows)]\n    x: i32,\n}\n"),
    "variant": ("pub enum V {\n", "    #[cfg(unix)]\n    A,\n", "    #[cfg(windows)]\n    A,\n}\n"),
    "const": ("", "#[cfg(unix)]\npub const N: i32 = 1;\n", "#[cfg(windows)]\npub const N: i32 = 2;\n"),
    "static": ("", "#[cfg(unix)]\npub static S: i32 = 1;\n", "#[cfg(windows)]\npub static S: i32 = 2;\n"),
    "type-alias": ("", "#[cfg(unix)]\npub type Fd = i32;\n", "#[cfg(windows)]\npub type Fd = i32;\n"),
    "trait-and-methods": ("", "#[cfg(unix)]\npub trait T { fn t(&self); }\n",
                          "#[cfg(windows)]\npub trait T { fn t(&self); }\n"),
    "extern-block": ("", "#[cfg(unix)]\nextern \"C\" { fn c_open() -> i32; }\n",
                     "#[cfg(windows)]\nextern \"C\" { fn c_open() -> i32; }\n"),
    "local-fn-in-guarded-fn": ("", "#[cfg(unix)]\npub fn outer() { fn inner() {} }\n",
                               "#[cfg(windows)]\npub fn outer() { fn inner() {} }\n"),
    "two-attributes": ("", "#[cfg(unix)]\n#[cfg(feature = \"a\")]\npub fn two() {}\n",
                       "#[cfg(unix)]\n#[cfg(feature = \"b\")]\npub fn two() {}\n"),
}


@pytest.mark.parametrize("case", sorted(RUST_CASES))
def test_rust_cfg_alternatives_get_distinct_ids(tmp_path: Path, case: str) -> None:
    _check(analyze_rust, tmp_path, "src/lib.rs", *RUST_CASES[case])


SWIFT_CASES: dict[str, tuple[str, str, str]] = {
    "members": (
        "struct S {\n#if os(iOS)\n",
        "  func f() -> Int { 1 }\n",
        "#else\n  func f() -> Int { 2 }\n#endif\n}\n",
    ),
    "elseif-and-else": (
        "struct S {\n#if os(iOS)\n  func f() -> Int { 1 }\n#elseif os(macOS)\n",
        "  func f() -> Int { 2 }\n",
        "#else\n  func f() -> Int { 3 }\n#endif\n}\n",
    ),
    "top-level-and-types": (
        "#if DEBUG\n",
        "func g() {}\nstruct T { func m() {} }\n",
        "#else\nfunc g() {}\nstruct T { func m() {} }\n#endif\n",
    ),
    "nested-guards": (
        "#if canImport(UIKit)\n#if DEBUG\n",
        "func h() {}\n",
        "#endif\n#endif\nfunc h() {}\n",
    ),
    "two-blocks": (
        "#if A\n",
        "func k() {}\n",
        "#endif\n#if B\nfunc k() {}\n#endif\n",
    ),
    "properties": (
        "struct P {\n#if DEBUG\n",
        "  var v: Int { 1 }\n",
        "#else\n  var v: Int { 2 }\n#endif\n}\n",
    ),
}


@pytest.mark.parametrize("case", sorted(SWIFT_CASES))
def test_swift_if_branches_get_distinct_ids(tmp_path: Path, case: str) -> None:
    _check(analyze_swift, tmp_path, "Sources/a.swift", *SWIFT_CASES[case])


def _ids(analyze: Callable[[Path], object], root: Path, rel: str, text: str) -> dict[tuple[str, str, int, int], str | None]:
    return {_position(s): s.stable_id for s in _symbols(analyze, root, rel, text)}


def test_rust_guard_layout_is_not_hashed(tmp_path: Path) -> None:
    a = _ids(analyze_rust, tmp_path / "a", "src/lib.rs",
             "#[cfg(feature=\"x\")]\npub fn f() {}\n")
    b = _ids(analyze_rust, tmp_path / "b", "src/lib.rs",
             "#[cfg( feature = \"x\" )]\npub fn f() {}\n")
    assert a == b


def test_rust_cfg_attr_and_other_attributes_are_not_hashed(tmp_path: Path) -> None:
    """``cfg_attr`` gates an attribute, not the item: it and any other
    attribute leave the id where an unattributed item has it."""
    plain = _ids(analyze_rust, tmp_path / "p", "src/lib.rs", "\n\npub struct B;\npub fn f() {}\n")
    attr = _ids(analyze_rust, tmp_path / "a", "src/lib.rs",
                "#[cfg_attr(feature = \"serde\", derive(Debug))]\n#[derive(Clone)]\n"
                "pub struct B;\npub fn f() {}\n")
    assert plain == attr


def test_rust_scope_spells_the_guards() -> None:
    import tree_sitter
    import tree_sitter_rust

    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))
    src = (
        b"#[cfg(unix)]\nmod imp {\n    #[cfg(test)]\n    #[inline]\n"
        b"    #[cfg(feature  =  \"a\")]\n    pub fn g() {}\n}\n"
        b"#[cfg(windows)]\nimpl Foo { fn m(&self) {} }\n"
        b"pub fn plain() {}\n"
    )
    tree = parser.parse(src)
    fns: list[tree_sitter.Node] = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "function_item":
            fns.append(n)
        stack.extend(n.children)
    fns.sort(key=lambda n: n.start_byte)
    assert [_rust_hash_scope(n, src) for n in fns] == [
        ["#[cfg(unix)] imp", "#[cfg(feature = \"a\")] #[cfg(test)]"],
        ["#[cfg(windows)]"],
        [],
    ]


def test_swift_scope_spells_the_branches() -> None:
    import tree_sitter
    import tree_sitter_swift

    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_swift.language()))
    src = (
        b"#if  canImport( UIKit )\nstruct S {\n#if A\n  func a() {}\n#elseif B\n  func b() {}\n"
        b"#else\n  func c() {}\n#endif\n  func d() {}\n}\n#endif\nfunc e() {}\n"
        b"#if P\n#if Q\nfunc z() {}\n#endif\n#endif\n"
    )
    tree = parser.parse(src)
    fns: list[tree_sitter.Node] = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "function_declaration":
            fns.append(n)
        stack.extend(n.children)
    fns.sort(key=lambda n: n.start_byte)
    assert [_swift_scope(n, src) for n in fns] == [
        ["#if canImport(UIKit)", "S", "#if A"],
        ["#if canImport(UIKit)", "S", "#if A #elseif B"],
        ["#if canImport(UIKit)", "S", "#if A #else"],
        ["#if canImport(UIKit)", "S"],
        [],
        ["#if P", "#if Q"],
    ]


def test_swift_unbalanced_directives_do_not_fail() -> None:
    """A stray ``#else`` / ``#endif`` (no open ``#if`` in that list, as error
    recovery can leave one) is ignored."""
    import tree_sitter
    import tree_sitter_swift

    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_swift.language()))
    src = b"struct S {\n#endif\n#else\n  func a() {}\n}\n"
    tree = parser.parse(src)
    fns: list[tree_sitter.Node] = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "function_declaration":
            fns.append(n)
        stack.extend(n.children)
    assert [_swift_scope(n, src) for n in fns] == [["S"]]
