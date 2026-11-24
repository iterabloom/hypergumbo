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
item has several; an inner ``#![cfg]`` of a block and a ``cfg_attr(P,
cfg(Q))`` since v19, a ``cfg_attr`` without ``cfg`` gating no item); Swift's
``#if`` / ``#elseif`` / ``#else`` branch enclosing a declaration at each
level, a branch named by its chain's ``#if`` condition and its own (``#if A
#elseif B``, ``#if A #else``). An unguarded declaration hashes what it did.
C and C++ ``#if`` / ``#ifdef`` / ``#ifndef`` / ``#elif`` / ``#else``
branches since scheme v19 (WI-sifar), named as Swift's are; a whole-file
include guard is no branch, so renaming its macro moves no id.

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
from hypergumbo_lang_mainstream.c import analyze_c
from hypergumbo_lang_mainstream.cpp import analyze_cpp
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
    # WI-sifar: an inner #![cfg] guards its item as an outer one does.
    "inner-cfg-mod": ("", "mod imp {\n    #![cfg(unix)]\n    pub fn g() -> i32 { 1 }\n}\n",
                      "mod imp {\n    #![cfg(windows)]\n    pub fn g() -> i32 { 2 }\n}\n"),
    "inner-cfg-fn": ("", "pub fn outer() {\n    #![cfg(unix)]\n    fn inner() {}\n}\n",
                     "pub fn outer() {\n    #![cfg(windows)]\n    fn inner() {}\n}\n"),
    "inner-cfg-impl": (
        "pub struct Foo;\n",
        "impl Foo {\n    #![cfg(unix)]\n    pub fn f(&self) -> i32 { 1 }\n}\n",
        "impl Foo {\n    #![cfg(windows)]\n    pub fn f(&self) -> i32 { 2 }\n}\n",
    ),
    "inner-cfg-trait": ("", "pub trait T {\n    #![cfg(unix)]\n    fn t(&self);\n}\n",
                        "pub trait T {\n    #![cfg(windows)]\n    fn t(&self);\n}\n"),
    "inner-cfg-extern": ("", "extern \"C\" {\n    #![cfg(unix)]\n    fn c_open() -> i32;\n}\n",
                         "extern \"C\" {\n    #![cfg(windows)]\n    fn c_open() -> i32;\n}\n"),
    # WI-sifar: cfg_attr(P, cfg(Q)) decides whether the item exists.
    "cfg-attr-cfg": ("", "#[cfg_attr(unix, cfg(feature = \"a\"))]\npub fn h() {}\n",
                     "#[cfg_attr(unix, cfg(feature = \"b\"))]\npub fn h() {}\n"),
    "nested-cfg-attr": (
        "", "#[cfg_attr(unix, cfg_attr(feature = \"x\", cfg(a)))]\npub struct N { x: i32 }\n",
        "#[cfg_attr(unix, cfg_attr(feature = \"x\", cfg(b)))]\npub struct N { x: i32 }\n",
    ),
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
    c = _ids(analyze_rust, tmp_path / "c", "src/lib.rs", "#[cfg(all(a, b))]\npub fn f() {}\n")
    d = _ids(analyze_rust, tmp_path / "d", "src/lib.rs",
             "#[cfg(all(a, b // why\n))]\npub fn f() {}\n")
    assert list(c.values()) == list(d.values()) != list(a.values())


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


def test_a_rust_cfg_split_over_lines_keeps_the_id(tmp_path: Path) -> None:
    """rustfmt splits a long ``#[cfg(...)]`` over lines with a trailing comma;
    the guard's label is hashed with ``layout_free(separators=True)``, so the
    comma is layout like the line breaks (WI-sidus)."""
    def ids(text: str) -> dict[str, str | None]:
        for old in tmp_path.iterdir():
            old.unlink()
        (tmp_path / "lib.rs").write_text(text)
        return {s.name: s.stable_id for s in analyze_rust(tmp_path).symbols if s.kind == "function"}

    one = ids('#[cfg(all(feature = "x", unix))]\npub fn g() {}\n')
    split = ids('#[cfg(all(\n    feature = "x",\n    unix,\n))]\npub fn g() {}\n')
    other = ids('#[cfg(any(feature = "x", unix))]\npub fn g() {}\n')
    assert one["g"] is not None  # reach
    assert one == split
    assert one["g"] != other["g"]


#: (head, earlier, rest) for C; blanking ``earlier`` leaves a valid file.
C_CASES: dict[str, tuple[str, str, str]] = {
    "ifdef-else": ("#ifdef _WIN32\n", "static int open_file(const char *p) { return 1; }\n",
                   "#else\nstatic int open_file(const char *p) { return 2; }\n#endif\n"),
    "if-elif-struct": ("#if defined(A)\n", "struct S { int x; };\n",
                       "#elif defined(B)\nstruct S { int x; };\n#endif\n"),
    "elif-chain-and-else": (
        "#if A\nint f(void) { return 1; }\n#elif B\n", "int f(void) { return 2; }\n",
        "#else\nint f(void) { return 3; }\n#endif\n",
    ),
    "ifndef-enum-typedef-variable": (
        "#ifndef FAST\n", "enum E { A };\ntypedef int T;\nint counter = 1;\n",
        "#else\nenum E { A };\ntypedef int T;\nint counter = 2;\n#endif\n",
    ),
    "nested-guards-and-unguarded-twin": (
        "#ifdef A\n#ifdef B\n", "int h(void) { return 1; }\n",
        "#endif\n#endif\nint h(void) { return 2; }\n",
    ),
    "two-blocks": ("#ifdef A\n", "int k(void) { return 1; }\n",
                   "#endif\n#ifdef B\nint k(void) { return 2; }\n#endif\n"),
    "if-0": ("#if 0\n", "int dead(void) { return 1; }\n",
             "#endif\nint dead(void) { return 2; }\n"),
    "block-scope-struct": (
        "void g(void) {\n#ifdef A\n", "  struct inner { int a; };\n",
        "#else\n  struct inner { int b; };\n#endif\n}\n",
    ),
}


@pytest.mark.parametrize("case", sorted(C_CASES))
def test_c_preprocessor_alternatives_get_distinct_ids(tmp_path: Path, case: str) -> None:
    _check(analyze_c, tmp_path, "src/a.c", *C_CASES[case])


CPP_CASES: dict[str, tuple[str, str, str]] = {
    "member": ("class K {\npublic:\n#ifdef FAST\n", "  int run(int x) { return x; }\n",
               "#else\n  int run(int x) { return -x; }\n#endif\n};\n"),
    "out-of-line-member": (
        "struct K { int run(int x); };\n#ifdef FAST\n", "int K::run(int x) { return x; }\n",
        "#else\nint K::run(int x) { return -x; }\n#endif\n",
    ),
    "namespace-function": ("namespace n {\n#ifdef A\n", "int f(int x) { return x; }\n",
                           "#else\nint f(int x) { return -x; }\n#endif\n}\n"),
    "class-and-members": (
        "#ifdef A\n", "class C { public: int m() { return 1; } int v; };\n",
        "#else\nclass C { public: int m() { return 2; } int v; };\n#endif\n",
    ),
    "dispatch-table": (
        "int f(void) { return 1; }\n#ifdef A\n", "int (*tbl[])(void) = { f };\n",
        "#else\nint (*tbl[])(void) = { f, f };\n#endif\n",
    ),
}


@pytest.mark.parametrize("case", sorted(CPP_CASES))
def test_cpp_preprocessor_alternatives_get_distinct_ids(tmp_path: Path, case: str) -> None:
    _check(analyze_cpp, tmp_path, "src/a.cpp", *CPP_CASES[case])


@pytest.mark.parametrize(("analyze", "rel"), [(analyze_c, "inc/a.h"), (analyze_cpp, "inc/a.hpp")])
def test_include_guard_is_not_hashed(
    tmp_path: Path, analyze: Callable[[Path], object], rel: str,
) -> None:
    """Renaming an include guard's macro, or dropping the guard, moves no id;
    the same ``#ifndef`` that does not wrap the whole file is a branch."""
    body = "struct S { int x; };\nstatic int f(void) { return 1; }\n"
    guarded = _ids(analyze, tmp_path / "g", rel, f"#ifndef FOO_H\n#define FOO_H\n{body}#endif\n")
    renamed = _ids(analyze, tmp_path / "r", rel, f"#ifndef BAR_H\n#define BAR_H\n{body}#endif\n")
    bare = _ids(analyze, tmp_path / "b", rel, f"\n\n{body}\n")
    assert len(guarded) >= 2  # reach
    assert guarded == renamed == bare
    partial = _ids(
        analyze, tmp_path / "p", rel, f"#ifndef FOO_H\n#define FOO_H\n{body}#endif\nint g;\n",
    )
    moved = {k for k in guarded if k[1] in ("S", "f")}
    assert moved and all(partial[k] != guarded[k] for k in moved)


@pytest.mark.parametrize(("analyze", "rel"), [(analyze_c, "src/a.c"), (analyze_cpp, "src/a.cpp")])
def test_c_family_unguarded_and_guard_layout(
    tmp_path: Path, analyze: Callable[[Path], object], rel: str,
) -> None:
    """A guard's layout and comments are not hashed; a declaration outside
    every branch hashes what it would with no directive in the file."""
    a = _ids(analyze, tmp_path / "a", rel,
             "#if defined(A) && defined(B)\nint f(int x) { return x; }\n#endif\nint g(int x) { return x; }\n")
    b = _ids(analyze, tmp_path / "b", rel,
             "#  if defined( A ) /* c */ && \\\n defined(B)\nint f(int x) { return x; }\n#endif\n"
             "int g(int x) { return x; }\n")
    plain = _ids(analyze, tmp_path / "p", rel,
                 "\nint f(int x) { return x; }\n\nint g(int x) { return x; }\n")
    f_a = next(k for k in a if k[1] == "f")
    g_a = next(k for k in a if k[1] == "g")
    f_b = next(k for k in b if k[1] == "f")
    assert a[f_a] == b[f_b]
    assert a[f_a] != plain[f_a]
    assert a[g_a] == plain[g_a]


def test_cpp_slot_spells_the_branches() -> None:
    """A branch sits in the slot at its level; the names a member spells are
    dropped past it, and a member of ``class Outer::Inner`` has the slot a
    member of ``Inner`` nested in ``Outer`` has."""
    import tree_sitter
    import tree_sitter_cpp

    from hypergumbo_lang_mainstream.cpp import _cpp_scope_slot

    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_cpp.language()))

    def slots(code: str, name: str) -> list[str]:
        src = code.encode()
        out: list[str] = []
        stack = [parser.parse(src).root_node]
        while stack:
            n = stack.pop()
            if n.type == "function_definition":
                out.append(_cpp_scope_slot(n, src, name))
            stack.extend(n.children)
        return out

    qualified = slots(
        "class Outer { class Inner; };\n"
        "class Outer::Inner {\n#ifdef X\n  int go() { return 1; }\n#endif\n};\n", "Inner::go",
    )
    nested = slots(
        "class Outer {\n  class Inner {\n#ifdef X\n  int go() { return 1; }\n#endif\n  };\n};\n",
        "Inner::go",
    )
    assert qualified == nested == ["Outer/#ifdef X/Inner::go"]
    assert slots("#ifdef A\nnamespace n {\nstruct K {\n#ifndef B\n  int run() { return 1; }\n"
                 "#endif\n};\n}\n#endif\n", "K::run") == ["#ifdef A/n/#ifndef B/K::run"]
    assert slots("namespace n { struct K { int run() { return 1; } }; }\n", "K::run") == ["n/K::run"]


def test_rust_inner_and_outer_cfg_hash_alike(tmp_path: Path) -> None:
    """``mod m { #![cfg(unix)] .. }`` is ``#[cfg(unix)] mod m { .. }``: the
    inner form is hashed in the outer spelling (WI-sifar)."""
    outer = _ids(analyze_rust, tmp_path / "o", "src/lib.rs",
                 "#[cfg(unix)]\nmod m {\n    pub fn g() {}\n}\n")
    inner = _ids(analyze_rust, tmp_path / "i", "src/lib.rs",
                 "\nmod m {\n    #![cfg(unix)]\n    pub fn g() {}\n}\n")
    plain = _ids(analyze_rust, tmp_path / "p", "src/lib.rs", "\nmod m {\n\n    pub fn g() {}\n}\n")
    g_outer = next(v for k, v in outer.items() if k[1] == "g")
    g_inner = next(v for k, v in inner.items() if k[1] == "g")
    g_plain = next(v for k, v in plain.items() if k[1] == "g")
    assert g_outer == g_inner != g_plain


def test_rust_file_level_inner_cfg_is_not_hashed(tmp_path: Path) -> None:
    """A file's own ``#![cfg]`` guards every symbol of the file alike: the
    file anchor identifies the file, and it is not read."""
    plain = _ids(analyze_rust, tmp_path / "p", "src/lib.rs", "\npub fn f() {}\nmod m { pub fn g() {} }\n")
    guarded = _ids(analyze_rust, tmp_path / "g", "src/lib.rs",
                   "#![cfg(feature = \"x\")]\npub fn f() {}\nmod m { pub fn g() {} }\n")
    assert len(plain) >= 2  # reach
    assert plain == guarded


def test_rust_cfg_attr_keeps_only_its_cfg(tmp_path: Path) -> None:
    """A ``cfg_attr`` is hashed as its predicate and the ``cfg`` attributes it
    lists; ``derive``, ``doc(cfg(..))`` and the like decide no existence."""
    def g(text: str, root: str) -> str | None:
        ids = _ids(analyze_rust, tmp_path / root, "src/lib.rs", text + "pub fn g() {}\n")
        return next(v for k, v in ids.items() if k[1] == "g")

    lean = g("#[cfg_attr(unix, cfg(a))]\n", "a")
    mixed = g("#[cfg_attr(unix, derive(Debug), cfg(a), allow(dead_code))]\n", "b")
    doc_only = g("#[cfg_attr(docsrs, doc(cfg(feature = \"x\")))]\n", "c")
    plain = g("\n", "d")
    assert lean == mixed
    assert doc_only == plain != lean
