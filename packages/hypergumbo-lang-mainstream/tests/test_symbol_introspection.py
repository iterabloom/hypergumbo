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
    SUPPORTED_LANGUAGES,
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
