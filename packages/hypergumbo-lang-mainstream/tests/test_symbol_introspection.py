# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the shared symbol_introspection dispatcher module.

INV-jahiv Phase 4 PR3.

These tests cover the dispatch logic itself: every supported language
string routes to the correct per-language extractor, unknown languages
return None without raising, and the underlying extractors are reached
end-to-end with a real tree-sitter parse from each named language.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hypergumbo_lang_mainstream.symbol_introspection import (
    BRANCH_NODE_TYPES,
    SUPPORTED_LANGUAGES,
    compute_cyclomatic_complexity,
    extract_preceding_doc_comment,
    extract_signature,
)


class _DummyNode:
    """Minimal stand-in for a tree-sitter Node used by negative paths.

    The dispatcher must not call ``.type`` or other tree-sitter methods
    when the language is unknown, so this dummy is sufficient.
    """

    type = "unknown_node_type"
    prev_named_sibling = None


class TestUnknownLanguage:
    """The dispatcher returns None for languages not in SUPPORTED_LANGUAGES."""

    def test_extract_signature_unknown_language(self) -> None:
        """Unknown language string yields None, no exception."""
        result = extract_signature(_DummyNode(), b"src", "klingon")
        assert result is None

    def test_extract_preceding_doc_comment_unknown_language(self) -> None:
        """Unknown language string yields None, no exception."""
        result = extract_preceding_doc_comment(_DummyNode(), b"src", "klingon")
        assert result is None

    def test_compute_cyclomatic_complexity_unknown_language(self) -> None:
        """Unknown language returns None, no exception."""
        result = compute_cyclomatic_complexity(_DummyNode(), "klingon")
        assert result is None

    def test_supported_languages_set_is_frozen(self) -> None:
        """SUPPORTED_LANGUAGES is the documented set of language strings."""
        assert "go" in SUPPORTED_LANGUAGES
        assert "rust" in SUPPORTED_LANGUAGES
        assert "javascript" in SUPPORTED_LANGUAGES
        assert "typescript" in SUPPORTED_LANGUAGES
        assert "klingon" not in SUPPORTED_LANGUAGES


# ---------------------------------------------------------------------------
# End-to-end dispatch checks via real tree-sitter parses.
#
# Each test parses a tiny snippet of the named language, locates the
# declaration node, then invokes the dispatcher and confirms a non-None
# result. The point is *dispatch wiring*, not signature/docstring
# correctness (which is tested by each analyzer's own test file).
# ---------------------------------------------------------------------------


def _first_node_of_type(root: Any, node_type: str) -> Any:
    """Iterative pre-order search for the first node with the given type."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == node_type:
            return node
        stack.extend(reversed(node.children))
    return None


class TestGoDispatch:
    def test_go_signature_dispatch(self, tmp_path: Path) -> None:
        """``language="go"`` dispatches to the Go signature extractor."""
        from hypergumbo_core.analyze.base import TreeSitterAnalyzer

        class _GoAnalyzer(TreeSitterAnalyzer):
            lang = "go"
            grammar_module = "tree_sitter_go"

        analyzer = _GoAnalyzer()
        if not analyzer._check_grammar_available():
            pytest.skip("tree-sitter-go unavailable")  # pragma: no cover
        parser = analyzer._create_parser()
        source = b"package main\nfunc Greet(x int) int { return x }\n"
        tree = parser.parse(source)
        fn = _first_node_of_type(tree.root_node, "function_declaration")
        sig = extract_signature(fn, source, "go")
        assert sig is not None
        assert "int" in sig

    def test_go_docstring_dispatch(self, tmp_path: Path) -> None:
        """``language="go"`` dispatches to the multi-language doc extractor."""
        from hypergumbo_core.analyze.base import TreeSitterAnalyzer

        class _GoAnalyzer(TreeSitterAnalyzer):
            lang = "go"
            grammar_module = "tree_sitter_go"

        analyzer = _GoAnalyzer()
        if not analyzer._check_grammar_available():
            pytest.skip("tree-sitter-go unavailable")  # pragma: no cover
        parser = analyzer._create_parser()
        source = b"package main\n// Greet says hi.\nfunc Greet() {}\n"
        tree = parser.parse(source)
        fn = _first_node_of_type(tree.root_node, "function_declaration")
        ds = extract_preceding_doc_comment(fn, source, "go")
        assert ds == "Greet says hi."


class TestRustDispatch:
    def test_rust_signature_dispatch(self) -> None:
        from hypergumbo_core.analyze.base import TreeSitterAnalyzer

        class _RsAnalyzer(TreeSitterAnalyzer):
            lang = "rust"
            grammar_module = "tree_sitter_rust"

        analyzer = _RsAnalyzer()
        if not analyzer._check_grammar_available():
            pytest.skip("tree-sitter-rust unavailable")  # pragma: no cover
        parser = analyzer._create_parser()
        source = b"pub fn greet(x: i32) -> i32 { x + 1 }\n"
        tree = parser.parse(source)
        fn = _first_node_of_type(tree.root_node, "function_item")
        sig = extract_signature(fn, source, "rust")
        assert sig is not None
        assert "i32" in sig


class TestJavaDispatch:
    def test_java_signature_dispatch_method_vs_constructor(self) -> None:
        """The dispatcher infers is_constructor from node type for Java."""
        from hypergumbo_core.analyze.base import TreeSitterAnalyzer

        class _JvAnalyzer(TreeSitterAnalyzer):
            lang = "java"
            grammar_module = "tree_sitter_java"

        analyzer = _JvAnalyzer()
        if not analyzer._check_grammar_available():
            pytest.skip("tree-sitter-java unavailable")  # pragma: no cover
        parser = analyzer._create_parser()
        source = (
            b"public class Foo {\n"
            b"  public Foo() {}\n"
            b"  public int add(int a, int b) { return a + b; }\n"
            b"}\n"
        )
        tree = parser.parse(source)
        ctor = _first_node_of_type(tree.root_node, "constructor_declaration")
        method = _first_node_of_type(tree.root_node, "method_declaration")
        ctor_sig = extract_signature(ctor, source, "java")
        method_sig = extract_signature(method, source, "java")
        assert ctor_sig is not None
        assert method_sig is not None
        assert "int" in method_sig


class TestCSharpDispatch:
    def test_csharp_signature_dispatch(self) -> None:
        from hypergumbo_core.analyze.base import TreeSitterAnalyzer

        class _CsAnalyzer(TreeSitterAnalyzer):
            lang = "csharp"
            grammar_module = "tree_sitter_c_sharp"

        analyzer = _CsAnalyzer()
        if not analyzer._check_grammar_available():
            pytest.skip("tree-sitter-c-sharp unavailable")  # pragma: no cover
        parser = analyzer._create_parser()
        source = (
            b"namespace N { public class Foo {"
            b" public int Add(int a, int b) { return a+b; } } }\n"
        )
        tree = parser.parse(source)
        m = _first_node_of_type(tree.root_node, "method_declaration")
        sig = extract_signature(m, source, "csharp")
        assert sig is not None
        assert "int" in sig.lower()


class TestPhpDispatch:
    def test_php_signature_dispatch(self) -> None:
        from hypergumbo_lang_mainstream.php import _get_php_parser

        parser = _get_php_parser()
        if parser is None:
            pytest.skip("tree-sitter-php unavailable")  # pragma: no cover
        source = b"<?php\nfunction add($a, $b) { return $a + $b; }\n"
        tree = parser.parse(source)
        fn = _first_node_of_type(tree.root_node, "function_definition")
        sig = extract_signature(fn, source, "php")
        assert sig is not None
        assert "$a" in sig


class TestJsTsDispatch:
    def test_javascript_signature_dispatch(self) -> None:
        from hypergumbo_core.analyze.base import TreeSitterAnalyzer

        class _JsAnalyzer(TreeSitterAnalyzer):
            lang = "javascript"
            grammar_module = "tree_sitter_javascript"

        analyzer = _JsAnalyzer()
        if not analyzer._check_grammar_available():
            pytest.skip("tree-sitter-javascript unavailable")  # pragma: no cover
        parser = analyzer._create_parser()
        source = b"function add(a, b) { return a + b; }\n"
        tree = parser.parse(source)
        fn = _first_node_of_type(tree.root_node, "function_declaration")
        sig_js = extract_signature(fn, source, "javascript")
        sig_ts = extract_signature(fn, source, "typescript")
        assert sig_js is not None
        assert sig_ts is not None
        # Both language strings hit the same _extract_jsts_signature helper.
        assert sig_js == sig_ts


class TestRubyDispatch:
    def test_ruby_signature_dispatch(self) -> None:
        from hypergumbo_core.analyze.base import TreeSitterAnalyzer

        class _RbAnalyzer(TreeSitterAnalyzer):
            lang = "ruby"
            grammar_module = "tree_sitter_ruby"

        analyzer = _RbAnalyzer()
        if not analyzer._check_grammar_available():
            pytest.skip("tree-sitter-ruby unavailable")  # pragma: no cover
        parser = analyzer._create_parser()
        source = b"class Foo\n  def add(a, b)\n    a + b\n  end\nend\n"
        tree = parser.parse(source)
        m = _first_node_of_type(tree.root_node, "method")
        sig = extract_signature(m, source, "ruby")
        assert sig is not None


class TestKotlinDispatch:
    def test_kotlin_signature_dispatch(self) -> None:
        from hypergumbo_core.analyze.base import TreeSitterAnalyzer

        class _KtAnalyzer(TreeSitterAnalyzer):
            lang = "kotlin"
            grammar_module = "tree_sitter_kotlin"

        analyzer = _KtAnalyzer()
        if not analyzer._check_grammar_available():
            pytest.skip("tree-sitter-kotlin unavailable")  # pragma: no cover
        parser = analyzer._create_parser()
        source = b"fun add(a: Int, b: Int): Int { return a + b }\n"
        tree = parser.parse(source)
        fn = _first_node_of_type(tree.root_node, "function_declaration")
        sig = extract_signature(fn, source, "kotlin")
        assert sig is not None


class TestSwiftDispatch:
    def test_swift_signature_dispatch(self) -> None:
        from hypergumbo_core.analyze.base import TreeSitterAnalyzer

        class _SwAnalyzer(TreeSitterAnalyzer):
            lang = "swift"
            grammar_module = "tree_sitter_swift"

        analyzer = _SwAnalyzer()
        if not analyzer._check_grammar_available():
            pytest.skip("tree-sitter-swift unavailable")  # pragma: no cover
        parser = analyzer._create_parser()
        source = b"func add(a: Int, b: Int) -> Int {\n    return a + b\n}\n"
        tree = parser.parse(source)
        fn = _first_node_of_type(tree.root_node, "function_declaration")
        sig = extract_signature(fn, source, "swift")
        assert sig is not None


# ---------------------------------------------------------------------------
# Cyclomatic complexity dispatch / per-language coverage.
#
# Each test parses a tiny snippet for the named language whose body
# exercises one or more branch-node types from BRANCH_NODE_TYPES, then
# asserts the returned complexity is >= the expected lower bound. The
# tests do not pin the exact integer (tree-sitter grammars may name
# nodes slightly differently across versions); they verify the walker
# saw the branches.
# ---------------------------------------------------------------------------


def _ts_parser_for(lang: str, grammar_module: str):
    """Helper: return a tree-sitter parser for the named language or
    skip the test if the grammar is unavailable."""
    from hypergumbo_core.analyze.base import TreeSitterAnalyzer

    class _A(TreeSitterAnalyzer):
        lang = ""
        grammar_module = ""

    _A.lang = lang
    _A.grammar_module = grammar_module
    analyzer = _A()
    if not analyzer._check_grammar_available():
        pytest.skip(f"{grammar_module} unavailable")  # pragma: no cover
    return analyzer._create_parser()


class TestCyclomaticBaseAndUnknown:
    """The base complexity for a body with no branches is 1."""

    def test_no_branches_returns_one_go(self) -> None:
        parser = _ts_parser_for("go", "tree_sitter_go")
        tree = parser.parse(b"package m\nfunc f() int { return 1 }\n")
        fn = _first_node_of_type(tree.root_node, "function_declaration")
        assert compute_cyclomatic_complexity(fn, "go") == 1

    def test_supported_languages_all_have_branch_node_types(self) -> None:
        """Every signature/docstring language also has a cyclomatic-complexity
        entry.

        ``SUPPORTED_LANGUAGES`` (signature/docstring extractors) and
        ``BRANCH_NODE_TYPES`` (cyclomatic-complexity decision-point tables)
        are two *distinct* capability axes that happened to coincide while
        only the 10 ADR-0033-Phase-4 languages populated either. The
        invariant is one-directional: a language that can produce a signature
        or docstring MUST also be able to compute complexity (no callable
        should regress to "has a signature but no CC"). The reverse is NOT
        required — INV-loguk wires cyclomatic_complexity for bash/c/cpp, which
        have no signature/docstring extractor yet, so they are CC-only.
        """
        assert set(SUPPORTED_LANGUAGES) <= set(BRANCH_NODE_TYPES)

    def test_cc_only_languages_have_branch_entries_but_no_signature(self) -> None:
        """bash/c/cpp populate cyclomatic_complexity (INV-loguk) without a
        signature/docstring extractor: present in BRANCH_NODE_TYPES, absent
        from SUPPORTED_LANGUAGES. Locks the slice-A additions so a future
        CC-only language is added deliberately."""
        for lang in ("bash", "c", "cpp"):
            assert lang in BRANCH_NODE_TYPES
            assert lang not in SUPPORTED_LANGUAGES


class TestCyclomaticPerLanguage:
    """Per-language fixtures that exercise branch nodes."""

    def test_go(self) -> None:
        parser = _ts_parser_for("go", "tree_sitter_go")
        # if + for + && + switch case + default + select case → at least 6
        src = (
            b"package m\n"
            b"func f(a int) int {\n"
            b"  if a > 0 && a < 10 { return 1 }\n"
            b"  for i := 0; i < 3; i++ { a++ }\n"
            b"  switch a {\n"
            b"  case 1: return 1\n"
            b"  default: return 0\n"
            b"  }\n"
            b"  ch := make(chan int)\n"
            b"  select {\n"
            b"  case <-ch: return 2\n"
            b"  }\n"
            b"  return a\n"
            b"}\n"
        )
        tree = parser.parse(src)
        fn = _first_node_of_type(tree.root_node, "function_declaration")
        cc = compute_cyclomatic_complexity(fn, "go")
        # base=1, if=1, &&=1, for=1, expr_case=1, default=1, comm_case=1
        assert cc is not None and cc >= 7

    def test_rust(self) -> None:
        parser = _ts_parser_for("rust", "tree_sitter_rust")
        src = (
            b"fn f(a: i32) -> i32 {\n"
            b"  if a > 0 || a < -1 { return 1; }\n"
            b"  for _i in 0..3 { }\n"
            b"  while a > 0 { return 2; }\n"
            b"  loop { break; }\n"
            b"  match a { 0 => 0, _ => 1, }\n"
            b"}\n"
        )
        tree = parser.parse(src)
        fn = _first_node_of_type(tree.root_node, "function_item")
        cc = compute_cyclomatic_complexity(fn, "rust")
        # if + || + for + while + loop + 2 match_arm → base=1, total >= 7
        assert cc is not None and cc >= 7

    def test_java(self) -> None:
        parser = _ts_parser_for("java", "tree_sitter_java")
        src = (
            b"class C {\n"
            b"  int f(int a) {\n"
            b"    if (a > 0 && a < 10) return 1;\n"
            b"    for (int i = 0; i < 3; i++) {}\n"
            b"    int[] xs = {1,2,3};\n"
            b"    for (int x : xs) {}\n"
            b"    while (a > 0) return 2;\n"
            b"    do { a--; } while (a > 0);\n"
            b"    switch (a) { case 1: return 1; default: return 0; }\n"
            b"    try { a = 1; } catch (Exception e) {}\n"
            b"    return a > 0 ? 1 : 0;\n"
            b"  }\n"
            b"}\n"
        )
        tree = parser.parse(src)
        m = _first_node_of_type(tree.root_node, "method_declaration")
        cc = compute_cyclomatic_complexity(m, "java")
        # All branch types appear; expect well above 8.
        assert cc is not None and cc >= 9

    def test_csharp(self) -> None:
        parser = _ts_parser_for("csharp", "tree_sitter_c_sharp")
        src = (
            b"class C {\n"
            b"  int F(int a) {\n"
            b"    if (a > 0 && a < 10) return 1;\n"
            b"    for (int i = 0; i < 3; i++) {}\n"
            b"    int[] xs = {1};\n"
            b"    foreach (var x in xs) {}\n"
            b"    while (a > 0) return 2;\n"
            b"    do { a--; } while (a > 0);\n"
            b"    switch (a) { case 1: return 1; default: return 0; }\n"
            b"    try { a = 1; } catch { }\n"
            b"    return a > 0 ? 1 : 0;\n"
            b"  }\n"
            b"}\n"
        )
        tree = parser.parse(src)
        m = _first_node_of_type(tree.root_node, "method_declaration")
        cc = compute_cyclomatic_complexity(m, "csharp")
        assert cc is not None and cc >= 8

    def test_php(self) -> None:
        from hypergumbo_lang_mainstream.php import _get_php_parser
        parser = _get_php_parser()
        if parser is None:
            pytest.skip("tree-sitter-php unavailable")  # pragma: no cover
        src = (
            b"<?php\n"
            b"function f($a) {\n"
            b"  if ($a > 0 && $a < 10) return 1;\n"
            b"  for ($i = 0; $i < 3; $i++) {}\n"
            b"  foreach ([1,2] as $x) {}\n"
            b"  while ($a > 0) return 2;\n"
            b"  do { $a--; } while ($a > 0);\n"
            b"  switch ($a) { case 1: return 1; }\n"
            b"  try { $a = 1; } catch (Exception $e) {}\n"
            b"  return $a > 0 ? 1 : 0;\n"
            b"}\n"
        )
        tree = parser.parse(src)
        fn = _first_node_of_type(tree.root_node, "function_definition")
        cc = compute_cyclomatic_complexity(fn, "php")
        assert cc is not None and cc >= 8

    def test_javascript(self) -> None:
        parser = _ts_parser_for("javascript", "tree_sitter_javascript")
        src = (
            b"function f(a) {\n"
            b"  if (a > 0 && a < 10) return 1;\n"
            b"  for (let i = 0; i < 3; i++) {}\n"
            b"  for (let k in {}) {}\n"
            b"  while (a > 0) return 2;\n"
            b"  do { a--; } while (a > 0);\n"
            b"  switch (a) { case 1: return 1; default: return 0; }\n"
            b"  try { a = 1; } catch (e) {}\n"
            b"  return a > 0 ? 1 : 0;\n"
            b"}\n"
        )
        tree = parser.parse(src)
        fn = _first_node_of_type(tree.root_node, "function_declaration")
        cc = compute_cyclomatic_complexity(fn, "javascript")
        assert cc is not None and cc >= 9

    def test_typescript(self) -> None:
        parser = _ts_parser_for("javascript", "tree_sitter_javascript")
        # Reuse the JS fixture; "typescript" should resolve via the same
        # BRANCH_NODE_TYPES entry and yield the same count.
        src = b"function f(a) {\n  if (a) return 1;\n  return 0;\n}\n"
        tree = parser.parse(src)
        fn = _first_node_of_type(tree.root_node, "function_declaration")
        cc_js = compute_cyclomatic_complexity(fn, "javascript")
        cc_ts = compute_cyclomatic_complexity(fn, "typescript")
        assert cc_js == cc_ts == 2  # base + if

    def test_ruby(self) -> None:
        parser = _ts_parser_for("ruby", "tree_sitter_ruby")
        src = (
            b"def f(a)\n"
            b"  if a > 0 && a < 10\n"
            b"    return 1\n"
            b"  end\n"
            b"  for i in 1..3 do end\n"
            b"  while a > 0\n"
            b"    return 2\n"
            b"  end\n"
            b"  until a < 0\n"
            b"    a -= 1\n"
            b"  end\n"
            b"  case a\n"
            b"  when 1 then 1\n"
            b"  end\n"
            b"  begin\n"
            b"    a = 1\n"
            b"  rescue => e\n"
            b"    nil\n"
            b"  end\n"
            b"  a > 0 ? 1 : 0\n"
            b"end\n"
        )
        tree = parser.parse(src)
        m = _first_node_of_type(tree.root_node, "method")
        cc = compute_cyclomatic_complexity(m, "ruby")
        assert cc is not None and cc >= 7

    def test_ruby_unless_and_short_circuit_and(self) -> None:
        """Exercise ``unless`` branch node and ``and`` short-circuit op."""
        parser = _ts_parser_for("ruby", "tree_sitter_ruby")
        src = (
            b"def f(a)\n"
            b"  unless a > 0 and a < 10\n"
            b"    return 1\n"
            b"  end\n"
            b"  return 0\n"
            b"end\n"
        )
        tree = parser.parse(src)
        m = _first_node_of_type(tree.root_node, "method")
        cc = compute_cyclomatic_complexity(m, "ruby")
        # base + unless + and = 3
        assert cc is not None and cc >= 3

    def test_kotlin(self) -> None:
        parser = _ts_parser_for("kotlin", "tree_sitter_kotlin")
        src = (
            b"fun f(a: Int): Int {\n"
            b"  if (a > 0 && a < 10) return 1\n"
            b"  for (i in 0..3) {}\n"
            b"  while (a > 0) return 2\n"
            b"  do { } while (a > 0)\n"
            b"  when (a) { 1 -> return 1; else -> return 0 }\n"
            b"  try { } catch (e: Exception) {}\n"
            b"  return a\n"
            b"}\n"
        )
        tree = parser.parse(src)
        fn = _first_node_of_type(tree.root_node, "function_declaration")
        cc = compute_cyclomatic_complexity(fn, "kotlin")
        assert cc is not None and cc >= 7

    def test_swift(self) -> None:
        parser = _ts_parser_for("swift", "tree_sitter_swift")
        src = (
            b"func f(a: Int) -> Int {\n"
            b"  if a > 0 && a < 10 { return 1 }\n"
            b"  guard a > 0 else { return 0 }\n"
            b"  for _ in 0..<3 { }\n"
            b"  while a > 0 { return 2 }\n"
            b"  repeat { } while a > 0\n"
            b"  switch a {\n"
            b"    case 1: return 1\n"
            b"    default: return 0\n"
            b"  }\n"
            b"  do { } catch { }\n"
            b"  return a > 0 ? 1 : 0\n"
            b"}\n"
        )
        tree = parser.parse(src)
        fn = _first_node_of_type(tree.root_node, "function_declaration")
        cc = compute_cyclomatic_complexity(fn, "swift")
        # Don't pin exact: the grammar may not expose every node type
        # we listed (e.g. ``guard_statement`` only fires on a separate
        # ``guard`` clause). Expect at least if + for + while + 2 case +
        # ternary = 6 above base.
        assert cc is not None and cc >= 5

    def test_bash(self) -> None:
        parser = _ts_parser_for("bash", "tree_sitter_bash")
        # if + elif + for + while + 2 case items → at least 5 above base.
        # Bash short-circuit ``&&``/``||`` between commands live in ``list``
        # nodes (not ``binary_expression``) and are deliberately NOT counted
        # (conservative scope), so the asserted floor excludes them.
        src = (
            b"greet() {\n"
            b"  if [ \"$1\" = a ]; then echo a\n"
            b"  elif [ \"$1\" = b ]; then echo b\n"
            b"  else echo c\n"
            b"  fi\n"
            b"  for i in 1 2 3; do echo \"$i\"; done\n"
            b"  while true; do break; done\n"
            b"  case \"$1\" in a) echo x;; *) echo y;; esac\n"
            b"}\n"
        )
        tree = parser.parse(src)
        fn = _first_node_of_type(tree.root_node, "function_definition")
        cc = compute_cyclomatic_complexity(fn, "bash")
        # base=1, if=1, elif=1, for=1, while=1, case_item x2 = 2 → >= 6
        assert cc is not None and cc >= 6

    def test_c(self) -> None:
        parser = _ts_parser_for("c", "tree_sitter_c")
        src = (
            b"int f(int x) {\n"
            b"  if (x > 10) { return 1; } else if (x > 5) { return 2; }\n"
            b"  for (int i = 0; i < x; i++) { if (i % 2 == 0 && i > 0) return i; }\n"
            b"  while (x > 0) { x--; }\n"
            b"  switch (x) { case 0: return 9; default: return 8; }\n"
            b"  do { x++; } while (x < 3);\n"
            b"  return x > 0 ? 1 : 0;\n"
            b"}\n"
        )
        tree = parser.parse(src)
        fn = _first_node_of_type(tree.root_node, "function_definition")
        cc = compute_cyclomatic_complexity(fn, "c")
        # if + else-if + for + inner-if + && + while + 2 case + do + ternary
        assert cc is not None and cc >= 8

    def test_cpp(self) -> None:
        parser = _ts_parser_for("cpp", "tree_sitter_cpp")
        src = (
            b"int f(int x) {\n"
            b"  if (x > 10) { return 1; } else if (x > 5) { return 2; }\n"
            b"  for (int i = 0; i < x; i++) { if (i % 2 == 0 || i < 0) return i; }\n"
            b"  for (auto v : items) { use(v); }\n"
            b"  while (x > 0) { x--; }\n"
            b"  switch (x) { case 0: return 9; default: return 8; }\n"
            b"  try { x++; } catch (...) { x = 0; }\n"
            b"  return x > 0 ? 1 : 0;\n"
            b"}\n"
        )
        tree = parser.parse(src)
        fn = _first_node_of_type(tree.root_node, "function_definition")
        cc = compute_cyclomatic_complexity(fn, "cpp")
        # if + else-if + for + inner-if + || + range-for + while + 2 case +
        # catch + ternary
        assert cc is not None and cc >= 9


class TestDocstringDispatchAllLanguages:
    """Each supported language reaches extract_doc_comment via the dispatcher."""

    @pytest.mark.parametrize(
        "language",
        sorted(SUPPORTED_LANGUAGES),
    )
    def test_dispatch_routes_to_extract_doc_comment(
        self, language: str,
    ) -> None:
        """All supported languages funnel into base.extract_doc_comment."""
        with patch(
            "hypergumbo_lang_mainstream.symbol_introspection.extract_doc_comment",
            return_value="MARKER",
        ) as patched:
            result = extract_preceding_doc_comment(
                _DummyNode(), b"src", language,
            )
        assert result == "MARKER"
        assert patched.called
