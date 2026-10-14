# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nested same-name scopes: ids fixed in the WI-tusad stable_id scheme bump
(INV-fabih and the cousins its sweep found; ADR-0035 §1, §6).

A member hashed only its NEAREST owner (``Inner.go``) and a type took the
backstop's bare-name table key, so ``class Inner`` in two outer classes (or in
two namespace blocks, or a local function in two functions) tied with its
twin, the per-file split pass numbered the two by emission order, and deleting
the first outer scope moved the survivor's id. Every producer below now hashes
the full chain of enclosing scopes; a top-level declaration and a member of a
top-level type keep the value they had.

Each case is the analyzer run directly, its paths made repo-relative and
the backstop's table formula applied to what the producer leaves unset, as
the pipeline does. Survivors are matched by their own position (kind, name,
line, column), never through a name-keyed map.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import (
    make_declaration_stable_id, make_doc_stable_id, make_file_stable_id, make_typed_stable_id,
    populate_kind_stable_ids, visibility_from_modifiers,
)
from hypergumbo_lang_mainstream import kotlin as kotlin_mod
from hypergumbo_lang_mainstream.c import analyze_c
from hypergumbo_lang_mainstream.cmake import analyze_cmake_files
from hypergumbo_lang_mainstream.cpp import analyze_cpp
from hypergumbo_lang_mainstream.csharp import analyze_csharp
from hypergumbo_lang_mainstream.groovy import analyze_groovy
from hypergumbo_lang_mainstream.java import analyze_java
from hypergumbo_lang_mainstream.js_ts import analyze_javascript
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin
from hypergumbo_lang_mainstream.json_config import analyze_json_files
from hypergumbo_lang_mainstream.lua import analyze_lua
from hypergumbo_lang_mainstream.markdown import analyze_markdown
from hypergumbo_lang_mainstream.php import analyze_php
from hypergumbo_lang_mainstream.powershell import analyze_powershell
from hypergumbo_lang_mainstream.rust import analyze_rust
from hypergumbo_lang_mainstream.rust_scip import compute_rust_stable_id_from_source
from hypergumbo_lang_mainstream.scala import analyze_scala
from hypergumbo_lang_mainstream.swift import analyze_swift
from hypergumbo_lang_mainstream.toml_config import analyze_toml_files
from hypergumbo_lang_mainstream.xml_config import analyze_xml_files


def _symbols(analyze: Callable, root: Path, rel: str, text: str) -> list:
    """The file's symbols, with repo-relative paths and the backstop's table
    values filled, as the pipeline would (``analyze.all_analyzers``)."""
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    symbols = [s for s in analyze(root).symbols if s.kind != "file" and s.path.endswith(rel)]
    for sym in symbols:
        sym.path = rel
    populate_kind_stable_ids(symbols)
    return symbols


def _position(sym) -> tuple:
    return (sym.kind, sym.name, sym.span.start_line, sym.span.start_col)


def _check(
    analyze: Callable, tmp_path: Path, rel: str, earlier: str, rest: str, head: str = "",
) -> list:
    """``earlier`` holds the first outer scope, ``rest`` the second and the
    remainder (``head`` precedes both). No two symbols of the file share a
    stable_id, and blanking ``earlier`` (lines keep their numbers) leaves
    every survivor's id alone."""
    text = head + earlier + rest
    with_sibling = _symbols(analyze, tmp_path / "with", rel, text)
    ids = [s.stable_id for s in with_sibling]
    assert None not in ids, [s.name for s in with_sibling if s.stable_id is None]
    assert len(set(ids)) == len(ids), sorted(
        (s.kind, s.name) for s in with_sibling if ids.count(s.stable_id) > 1)
    before: dict[tuple, list] = {}
    for s in with_sibling:
        before.setdefault(_position(s), []).append(s.stable_id)
    edited = head + "\n" * earlier.count("\n") + rest
    after = _symbols(analyze, tmp_path / "without", rel, edited)
    for s in after:
        assert s.stable_id in before.get(_position(s), []), (s.kind, s.name, s.span.start_line)
    return after


CASES = {
    "scala": (analyze_scala, "src/Outer.scala",
              "class A {\n  class Inner {\n    def go(): Int = 1\n    val v: Int = 1\n  }\n"
              "  object Obj {\n    def go(): Int = 1\n  }\n}\n",
              "class B {\n  class Inner {\n    def go(): Int = 2\n    val v: Int = 2\n  }\n"
              "  object Obj {\n    def go(): Int = 2\n  }\n  trait T\n}\n"
              "package p {\n  class InBlock\n}\n"),
    "kotlin": (analyze_kotlin, "src/Outer.kt",
               "class A {\n    class Inner {\n        fun go(): Int = 1\n        val v: Int = 1\n    }\n"
               "    class Line { fun go(): Int = 1 }\n    enum class E { X }\n}\n",
               "class B {\n    class Inner {\n        fun go(): Int = 2\n        val v: Int = 2\n    }\n"
               "    class Line { fun go(): Int = 2 }\n    enum class E { X }\n}\n"),
    "csharp": (analyze_csharp, "src/Outer.cs",
               "namespace A {\n    class Inner {\n        Inner() {}\n        int Go() { return 1; }\n"
               "        int f;\n        public int this[int i] { get { return i; } }\n"
               "        enum E { X }\n    }\n    interface I { }\n    struct S { }\n}\n",
               "namespace B {\n    class Inner {\n        Inner() {}\n        int Go() { return 2; }\n"
               "        int f;\n        public int this[int i] { get { return i; } }\n"
               "        enum E { X }\n    }\n    interface I { }\n    struct S { }\n}\n"),
    # A property hashed its qualified name, which kept only the innermost
    # namespace: ``Inner.C.P`` in namespaces A and B tied.
    "csharp-nested-namespace": (analyze_csharp, "src/Spaces.cs",
                                "namespace A {\n    namespace Inner {\n        class C {\n"
                                "            int P { get; set; }\n        }\n    }\n}\n",
                                "namespace B {\n    namespace Inner {\n        class C {\n"
                                "            int P { get; set; }\n        }\n    }\n}\n"),
    "swift": (analyze_swift, "src/Outer.swift",
              "class A {\n    class Inner {\n        func go() -> Int { return 1 }\n        var v: Int = 1\n"
              "    }\n    enum E {\n        case x\n    }\n    protocol P {\n        func p()\n    }\n}\n",
              "class B {\n    class Inner {\n        func go() -> Int { return 2 }\n        var v: Int = 2\n"
              "    }\n    enum E {\n        case x\n    }\n    protocol P {\n        func p()\n    }\n}\n"),
    "java": (analyze_java, "src/Outer.java",
             "    int a() {\n        class Local {\n            int v;\n"
             "            int go() { return 1; }\n            Local() {}\n        }\n"
             "        enum LocalE { X }\n        record LocalR(int x) {}\n        return 1;\n    }\n",
             "    int b() {\n        class Local {\n            int v;\n            int go() { return 2; }\n"
             "            Local() {}\n        }\n        enum LocalE { X }\n"
             "        record LocalR(int x) {}\n        return 2;\n    }\n}\n",
             "class Outer {\n"),
    "typescript": (analyze_javascript, "src/outer.ts",
                   "namespace A {\n  export class Inner {\n    v: number = 1;\n    go(): number { return 1; }\n  }\n"
                   "  export interface I { x: number; }\n  export enum E { X }\n  export type T = number;\n"
                   "  export function run(): number { return 1; }\n}\n",
                   "namespace B {\n  export class Inner {\n    v: number = 2;\n    go(): number { return 2; }\n  }\n"
                   "  export interface I { x: number; }\n  export enum E { X }\n  export type T = number;\n"
                   "  export function run(): number { return 2; }\n}\n"),
    "javascript": (analyze_javascript, "src/outer.js",
                   "function a() {\n  class Inner {\n    go() { return 1; }\n  }\n"
                   "  const local = () => 1;\n  return [Inner, local];\n}\n"
                   "const objA = {\n  go() { return 1; },\n};\n",
                   "function b() {\n  class Inner {\n    go() { return 2; }\n  }\n"
                   "  const local = () => 2;\n  return [Inner, local];\n}\n"
                   "const objB = {\n  go() { return 2; },\n};\n"),
    "php": (analyze_php, "src/spaces.php",
            "namespace A {\n    class Inner {\n        private $v = 1;\n        const C = 1;\n"
            "        function go() { return 1; }\n    }\n    function run() { return 1; }\n}\n",
            "namespace B {\n    class Inner {\n        private $v = 2;\n        const C = 2;\n"
            "        function go() { return 2; }\n    }\n    function run() { return 2; }\n}\n",
            "<?php\n"),
    "cpp": (analyze_cpp, "src/outer.cpp",
            "class A {\n  class Inner {\n    int go() { return 1; }\n    int v;\n"
            "    virtual int f(int x) = 0;\n  };\n  enum E { X };\n};\n"
            "namespace na {\nint run() { return 1; }\nint value = 1;\nstruct S { int v; };\n}\n",
            "class B {\n  class Inner {\n    int go() { return 2; }\n    int v;\n"
            "    virtual int f(int x) = 0;\n  };\n  enum E { X };\n};\n"
            "namespace nb {\nint run() { return 2; }\nint value = 2;\nstruct S { int v; };\n}\n"),
    "rust": (analyze_rust, "src/nested.rs",
             "mod a {\n    struct Inner { v: i32 }\n    impl Inner {\n        fn go(&self) -> i32 { 1 }\n    }\n"
             "    enum E { X }\n    trait T { fn t(&self); }\n    fn run() -> i32 { 1 }\n}\n"
             "fn outer_a() -> i32 {\n    fn local() -> i32 { 1 }\n    local()\n}\n",
             "mod b {\n    struct Inner { v: i32 }\n    impl Inner {\n        fn go(&self) -> i32 { 2 }\n    }\n"
             "    enum E { X }\n    trait T { fn t(&self); }\n    fn run() -> i32 { 2 }\n}\n"
             "fn outer_b() -> i32 {\n    fn local() -> i32 { 2 }\n    local()\n}\n"),
    "groovy": (analyze_groovy, "src/Outer.groovy",
               "class A {\n    class Inner {\n        int go() { 1 }\n    }\n    interface I {}\n    enum E { X }\n}\n",
               "class B {\n    class Inner {\n        int go() { 2 }\n    }\n    interface I {}\n    enum E { X }\n}\n"),
    "lua": (analyze_lua, "src/nested.lua",
            "function a()\n  local function inner() return 1 end\n  return inner()\nend\n",
            "function b()\n  local function inner() return 2 end\n  return inner()\nend\n"),
    "powershell": (analyze_powershell, "src/nested.ps1",
                   "function A {\n  function Inner { 1 }\n  Inner\n}\n",
                   "function B {\n  function Inner { 2 }\n  Inner\n}\n"),
}


@pytest.mark.parametrize("lang", sorted(CASES))
def test_deleting_an_earlier_same_name_scope_moves_no_id(tmp_path: Path, lang: str) -> None:
    analyze, rel, earlier, rest, *head = CASES[lang]
    assert _check(analyze, tmp_path, rel, earlier, rest, *head)


# -- members of a top-level type keep the value they had ----------------------


def test_kotlin_top_level_member_and_class_keep_their_values(tmp_path: Path) -> None:
    """The chain of a top-level class's member is its nearest owner, and a
    top-level class's scoped table value is the backstop's table value."""
    syms = _symbols(analyze_kotlin, tmp_path, "src/T.kt",
                    "class Mod {\n    fun run(): Int = 1\n}\n")
    cls = next(s for s in syms if s.kind == "class")
    run = next(s for s in syms if s.kind == "method")
    assert cls.stable_id == make_declaration_stable_id("class", "kotlin", "src/T.kt", "Mod")
    assert run.stable_id == make_typed_stable_id(
        "method", kotlin_mod.normalize_kotlin_signature(run.signature),
        visibility_from_modifiers(run.modifiers), name="run", qualified_name="Mod.run",
        file_stable_id=make_file_stable_id("kotlin", "src/T.kt"),
    )


def test_rust_scip_hashes_the_same_scope_chain_as_rust(tmp_path: Path) -> None:
    """WI-zakub parity: the SCIP helper recomputes a nested function's id with
    the mod/function chain rust.py hashes."""
    text = ("mod a {\n    fn run() -> i32 {\n        1\n    }\n}\n"
            "fn outer() -> i32 {\n    fn local() -> i32 {\n        2\n    }\n    local()\n}\n")
    syms = _symbols(analyze_rust, tmp_path, "src/lib.rs", text)
    for sym in (s for s in syms if s.name in ("run", "local")):
        assert compute_rust_stable_id_from_source(
            text.encode(), sym.span.start_line, sym.span.end_line, rel_path="src/lib.rs",
        ) == sym.stable_id


def test_php_namespace_is_read_per_declaration(tmp_path: Path) -> None:
    """A second namespace's declarations carried the first one's name in
    qualified_name (both ``A\\\\Inner``); statement-form and braced namespaces
    are now read where each declaration sits (``namespace {`` is global)."""
    stmt = _symbols(analyze_php, tmp_path / "s", "x.php",
                    "<?php\nnamespace A;\nclass X {}\nnamespace B;\nclass X {}\n")
    assert sorted(s.qualified_name for s in stmt) == ["A\\X", "B\\X"]
    braced = _symbols(analyze_php, tmp_path / "b", "y.php",
                      "<?php\nnamespace A {\n class Y {}\n}\nnamespace {\n class Y {}\n}\n")
    assert sorted(s.qualified_name for s in braced) == ["A\\Y", "Y"]
    plain = _symbols(analyze_php, tmp_path / "p", "z.php", "<?php\nclass Z {}\n")
    assert [s.qualified_name for s in plain] == ["Z"]
    assert plain[0].stable_id == make_declaration_stable_id("class", "php", "z.php", "Z")


def test_csharp_qualified_name_carries_every_enclosing_namespace(tmp_path: Path) -> None:
    """Nested block namespaces (``namespace A { namespace B {`` and the dotted
    ``namespace X.Y { namespace Z {``) are all in ``qualified_name``; it kept
    the innermost one only (``B.C.Go``). A single namespace, block or
    file-scoped, reads as before."""
    syms = _symbols(analyze_csharp, tmp_path / "n", "a.cs",
                    "namespace A {\n  namespace B {\n    class C {\n      void Go() {}\n"
                    "      int P { get; set; }\n      int f;\n      enum E { X }\n    }\n  }\n}\n"
                    "namespace X.Y {\n  namespace Z {\n    class D { }\n  }\n}\n")
    assert {s.name: s.qualified_name for s in syms} == {
        "C": "A.B.C", "C.Go": "A.B.C.Go", "C.P": "A.B.C.P", "C.f": "A.B.C.f",
        "E": "A.B.C.E", "E.X": "A.B.C.E.X", "D": "X.Y.Z.D",
    }
    single = _symbols(analyze_csharp, tmp_path / "s", "b.cs",
                      "namespace N.M;\nclass K {\n  int P { get; set; }\n}\n")
    assert {s.name: s.qualified_name for s in single} == {"K": "N.M.K", "K.P": "N.M.K.P"}


def test_typescript_qualified_name_carries_enclosing_namespaces(tmp_path: Path) -> None:
    """A TypeScript ``namespace`` / ``module`` block is a source-level
    namespace, so ``qualified_name`` carries it (``A.B.C.go``); it held the
    class chain alone (``C.go``). Functions are not namespaces and stay out,
    and a string-named ambient ``declare module "m"`` names a module
    specifier, not a namespace."""
    syms = _symbols(analyze_javascript, tmp_path, "src/n.ts",
                    "namespace A {\n  export namespace B {\n    export class C {\n"
                    "      go(): number { return 1; }\n    }\n"
                    "    export function f(): number { return 1; }\n"
                    "    export interface I { x: number; }\n  }\n}\n"
                    "declare module \"m\" {\n  export class K {}\n}\n"
                    "function outer() {\n  class Local {}\n  return Local;\n}\n")
    assert {s.name: s.qualified_name for s in syms if s.kind != "module"} == {
        "C": "A.B.C", "C.go": "A.B.C.go", "f": "A.B.f", "I": "A.B.I", "I.x": "A.B.I.x",
        "K": "K", "outer": "outer", "Local": "Local",
    }


# -- the tree-sitter-kotlin nested one-line body -------------------------------


def test_kotlin_nested_one_line_body_is_no_longer_dropped(tmp_path: Path) -> None:
    """tree-sitter-kotlin 1.1.0 made ``class A {`` / ``class Inner { fun go() =
    1 }`` / ``}`` one ERROR node: only a stray ``function go`` came out."""
    syms = _symbols(analyze_kotlin, tmp_path, "src/O.kt",
                    "class A {\n  class Inner { fun go(): Int = 1 }\n}\n")
    assert sorted((s.kind, s.name) for s in syms) == [
        ("class", "A"), ("class", "Inner"), ("method", "Inner.go")]


@pytest.mark.parametrize(("source", "repaired"), [
    ("class A {\n  class I { val x = 1 }\n}\n", "class A {\n  class I { val x = 1;}\n}\n"),
    # an opening brace, a closing brace first on its line, no blank: untouched
    ("class A { }\n  }\nfun f() {g()}\n", "class A { }\n  }\nfun f() {g()}\n"),
    # comments, nested comments, strings, raw strings, char literals, templates
    ('// a }\nval s = "x }" + "\\" }"\n', '// a }\nval s = "x }" + "\\" }"\n'),
    ("/* a /* b */ } */ val x = 1 }\n", "/* a /* b */ } */ val x = 1;}\n"),
    ('val r = """a } "" }""""\nval t = \'}\'\nval u = \'\\\'\'\n',
     'val r = """a } "" }""""\nval t = \'}\'\nval u = \'\\\'\'\n'),
    ('val s = "${ a } }" + "${f { it }}"\nval w = 1 }\n',
     'val s = "${ a } }" + "${f { it }}"\nval w = 1;}\n'),
    ('val r = """${x }\n }"""\n', 'val r = """${x }\n }"""\n'),
    ("val c = '", "val c = '"),
    ("// unterminated", "// unterminated"),
])
def test_close_brace_repair_keeps_length_and_skips_non_code(source: str, repaired: str) -> None:
    got = kotlin_mod._close_brace_repair(source.encode())
    assert got == repaired.encode()
    assert len(got) == len(source.encode())


def test_parse_kotlin_keeps_the_original_when_the_repair_does_not_help() -> None:
    parser = kotlin_mod._analyzer._create_parser()
    clean = b"class A { fun f() {} }\n"
    assert not kotlin_mod._parse_kotlin(parser, clean).root_node.has_error
    broken = b"class A {\n  class I { fun go(): Int = 1 }\n}\n"
    assert not kotlin_mod._parse_kotlin(parser, broken).root_node.has_error
    hopeless = b"class {{{ ) fun\n"
    tree = kotlin_mod._parse_kotlin(parser, hopeless)
    assert tree.root_node.has_error
    assert kotlin_mod._error_count(tree) >= 1


# -- INV-punig: one construct under two different parents ---------------------

_MD_CHILD = "## Details\n\nSee [home](index.md).\n\n```python\nprint(1)\n```\n\n"
_C_CHILD = "  struct inner { int w; };\n  enum e { X };\n  typedef int t;\n  int f(void);\n"
_CMAKE_CHILD = "  add_library(core a.c)\n  function(helper x)\n  endfunction()\n"

PARENT_CASES = {
    "markdown": (analyze_markdown, "docs/two.md",
                 "# Alpha\n\n" + _MD_CHILD, "# Beta\n\n" + _MD_CHILD),
    "c": (analyze_c, "src/two.c",
          "void a(void) {\n" + _C_CHILD + "}\n", "void b(void) {\n" + _C_CHILD + "}\n"),
    "cmake": (analyze_cmake_files, "CMakeLists.txt",
              "if(WIN32)\n" + _CMAKE_CHILD + "else()\n" + _CMAKE_CHILD + "endif()\n",
              "if(UNIX)\n" + _CMAKE_CHILD + "else()\n" + _CMAKE_CHILD + "endif()\n",
              "cmake_minimum_required(VERSION 3.10)\n"),
    "json": (analyze_json_files, "package.json",
             '  "dependencies": {"lodash": "1.0.0"},\n',
             '  "devDependencies": {"lodash": "1.0.0"}\n}\n', '{\n  "name": "two",\n'),
    "toml": (analyze_toml_files, "Cargo.toml", "[dependencies]\nserde = \"1\"\n",
             "[dev-dependencies]\nserde = \"1\"\n"
             "[target.'cfg(windows)'.dependencies]\nserde = \"1\"\n",
             "[package]\nname = \"two\"\n"),
    "xml": (analyze_xml_files, "pom.xml",
            "    <dependency>\n      <groupId>a</groupId>\n      <artifactId>x</artifactId>\n"
            "    </dependency>\n",
            "    <dependency>\n      <groupId>b</groupId>\n      <artifactId>x</artifactId>\n"
            "    </dependency>\n  </dependencies>\n</project>\n",
            "<project>\n  <dependencies>\n"),
}


@pytest.mark.parametrize("lang", sorted(PARENT_CASES))
def test_deleting_the_first_of_two_parents_moves_no_id(tmp_path: Path, lang: str) -> None:
    analyze, rel, earlier, rest, *head = PARENT_CASES[lang]
    assert _check(analyze, tmp_path, rel, earlier, rest, *head)


def test_top_level_declarations_keep_their_scope_free_values(tmp_path: Path) -> None:
    """A heading under no section, a production dependency and a ``[dependencies]``
    crate hash no scope: the doc formula, and the backstop's table value."""
    md = _symbols(analyze_markdown, tmp_path / "m", "README.md", "# Top\n\nText.\n")
    assert md[0].stable_id == make_doc_stable_id("markdown", "README.md", "section", "Top", 0, 0)
    js = _symbols(analyze_json_files, tmp_path / "j", "package.json",
                  '{"name": "x", "dependencies": {"lodash": "1.0.0"}}\n')
    dep = next(s for s in js if s.kind == "dependency")
    assert dep.stable_id == make_declaration_stable_id("dependency", "json", "package.json", "lodash")
    toml = _symbols(analyze_toml_files, tmp_path / "t", "Cargo.toml",
                    "[package]\nname = \"x\"\n[dependencies]\nserde = \"1\"\n")
    crate = next(s for s in toml if s.kind == "dependency")
    assert crate.stable_id == make_declaration_stable_id("dependency", "toml", "Cargo.toml", "serde")


def test_cmake_else_branches_carry_their_if(tmp_path: Path) -> None:
    """The ``else()`` of two conditionals were one label; under their ``if``
    they are two, and the then-branch keeps its chain."""
    from hypergumbo_core.overload_arity import scoped_stable_id

    syms = _symbols(analyze_cmake_files, tmp_path, "CMakeLists.txt",
                    "if(A)\n  add_library(x a.c)\nelseif(B)\n  add_library(x a.c)\nelse()\n"
                    "  add_library(x a.c)\nendif()\n")
    ids = [s.stable_id for s in syms if s.kind == "library"]
    assert ids == [
        scoped_stable_id(kind="library", lang="cmake", path="CMakeLists.txt", name="x", scope=chain)
        for chain in (["if(A)"], ["if(A)", "elseif(B)"], ["if(A)", "else()"])
    ]


def test_markdown_empty_heading_names_nothing_and_scopes_nothing(tmp_path: Path) -> None:
    """A bare ``#`` emits no section, and its section adds no label to the
    chain of what sits in it."""
    syms = _symbols(analyze_markdown, tmp_path, "docs/e.md", "#\n\n## Sub\n\ntext\n")
    assert [(s.kind, s.name) for s in syms] == [("section", "Sub")]
    assert syms[0].stable_id == make_doc_stable_id("markdown", "docs/e.md", "section", "Sub", 0, 0)
