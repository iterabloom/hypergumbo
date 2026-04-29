# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the linker docstring/comment masker."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from hypergumbo_core.linkers import _text_filters
from hypergumbo_core.linkers._text_filters import mask_doc_regions


@pytest.fixture(autouse=True)
def _clear_parser_cache():
    """Each test gets a fresh parser cache so monkeypatching takes effect."""
    _safe_clear = getattr(_text_filters._get_parser, "cache_clear", lambda: None)
    _safe_clear()
    yield
    # `_get_parser` may have been monkeypatched out; restore-then-clear is fine.
    _safe_clear = getattr(_text_filters._get_parser, "cache_clear", lambda: None)
    _safe_clear()


def _has_pattern(text: str, needle: str) -> bool:
    return needle in text


# --- Python docstrings ---

def test_python_module_docstring_is_masked():
    src = '"""producer.send(\'topic\', msg)"""\nx = 1\n'
    out = mask_doc_regions(src, "python")
    assert not _has_pattern(out, "producer.send")
    # The "x = 1" payload survives.
    assert _has_pattern(out, "x = 1")


def test_python_function_docstring_is_masked():
    src = (
        "def f():\n"
        "    \"\"\"call producer.send('topic') here\"\"\"\n"
        "    return 1\n"
    )
    out = mask_doc_regions(src, "python")
    assert not _has_pattern(out, "producer.send")
    assert _has_pattern(out, "return 1")


def test_python_class_docstring_is_masked():
    src = (
        "class C:\n"
        "    \"\"\"consumer.subscribe(['topic'])\"\"\"\n"
        "    a = 1\n"
    )
    out = mask_doc_regions(src, "python")
    assert not _has_pattern(out, "consumer.subscribe")
    assert _has_pattern(out, "a = 1")


def test_python_regular_string_literal_is_not_masked():
    src = "query = \"SELECT * FROM producer.send WHERE x = 1\"\n"
    out = mask_doc_regions(src, "python")
    assert _has_pattern(out, "producer.send")


def test_python_non_first_string_is_not_treated_as_docstring():
    src = "x = 1\n\"producer.send('y')\"\n"
    out = mask_doc_regions(src, "python")
    assert _has_pattern(out, "producer.send")


def test_python_module_docstring_after_spdx_comment_is_masked():
    # Tree-sitter Python lists comments as named children, so a leading
    # SPDX/license header would otherwise displace the docstring out of
    # the "first named child" position. The rule must skip leading
    # comments — every file in this repo starts this way.
    src = (
        "# SPDX-License-Identifier: AGPL-3.0-or-later\n"
        '"""Module that documents producer.send(\'topic\') in prose."""\n'
        "x = 1\n"
    )
    out = mask_doc_regions(src, "python")
    assert not _has_pattern(out, "producer.send")
    assert _has_pattern(out, "x = 1")


def test_python_module_docstring_after_multiple_leading_comments_is_masked():
    src = (
        "# SPDX-License-Identifier: AGPL-3.0-or-later\n"
        "# Copyright 2026 Iterabloom\n"
        "# Author: nobody\n"
        '"""Calls consumer.subscribe([\'topic\']) at startup."""\n'
        "y = 2\n"
    )
    out = mask_doc_regions(src, "python")
    assert not _has_pattern(out, "consumer.subscribe")
    assert _has_pattern(out, "y = 2")


def test_python_string_after_leading_comment_and_statement_is_not_docstring():
    # If a real statement appears before the string, the string is no
    # longer a positional docstring even with a leading comment.
    src = (
        "# header\n"
        "x = 1\n"
        "\"producer.send('y')\"\n"
    )
    out = mask_doc_regions(src, "python")
    assert _has_pattern(out, "producer.send")


def test_python_comment_is_masked():
    src = "x = 1  # producer.send('topic')\n"
    out = mask_doc_regions(src, "python")
    assert not _has_pattern(out, "producer.send")
    assert _has_pattern(out, "x = 1")


def test_python_newlines_preserved_for_line_stability():
    src = (
        '"""line1\nline2 with producer.send("x")\nline3"""\n'
        "y = 1\n"
    )
    out = mask_doc_regions(src, "python")
    assert out.count("\n") == src.count("\n")
    assert _has_pattern(out, "y = 1")
    assert not _has_pattern(out, "producer.send")


# --- JavaScript / TypeScript ---

def test_javascript_line_comment_is_masked():
    src = (
        "// producer.send('topic', msg)\n"
        "const x = 1;\n"
    )
    out = mask_doc_regions(src, "javascript")
    assert not _has_pattern(out, "producer.send")
    assert _has_pattern(out, "const x = 1")


def test_javascript_block_comment_is_masked():
    src = "/* call sqs.send_message here */\nconst x = 1;\n"
    out = mask_doc_regions(src, "javascript")
    assert not _has_pattern(out, "sqs.send_message")
    assert _has_pattern(out, "const x = 1")


def test_javascript_template_literal_is_not_masked():
    src = "const t = `producer.send('x')`;\n"
    out = mask_doc_regions(src, "javascript")
    assert _has_pattern(out, "producer.send")


# --- Java ---

def test_java_javadoc_block_comment_is_masked():
    src = (
        "/**\n"
        " * Calls producer.send('topic', msg) at startup.\n"
        " */\n"
        "class C {}\n"
    )
    out = mask_doc_regions(src, "java")
    assert not _has_pattern(out, "producer.send")
    assert _has_pattern(out, "class C")


# --- Failure modes (fail-closed: return content unchanged) ---

def test_unknown_language_returns_unchanged():
    src = "// pattern producer.send('x')\n"
    out = mask_doc_regions(src, "not-a-real-language")
    assert out == src


def test_empty_language_returns_unchanged():
    src = "/* pattern producer.send */"
    assert mask_doc_regions(src, None) == src
    assert mask_doc_regions(src, "") == src
    assert mask_doc_regions(src, "unknown") == src


def test_empty_content_returns_empty():
    assert mask_doc_regions("", "python") == ""


def test_syntax_error_does_not_crash_and_masks_what_it_can():
    src = (
        '"""docstring with producer.send"""\n'
        "def broken(:\n"  # syntax error
        "x = 1\n"
    )
    out = mask_doc_regions(src, "python")
    # Mask succeeded for the docstring, did not crash on the broken def.
    assert not _has_pattern(out, "producer.send")


def test_string_literals_too_kwarg_masks_everything():
    src = "query = \"producer.send('x')\"\n"
    out = mask_doc_regions(src, "python", mask_string_literals_too=True)
    assert not _has_pattern(out, "producer.send")


def test_no_doc_regions_returns_content_unchanged():
    src = "x = 1\ny = 2\n"
    out = mask_doc_regions(src, "python")
    assert out == src


def test_parser_unavailable_falls_through(monkeypatch):
    # When the parser factory returns None (e.g., grammar import failed),
    # the masker must return content unchanged.
    monkeypatch.setattr(_text_filters, "_get_parser", lambda lang: None)
    src = "// producer.send('x')\nconst x = 1;\n"
    out = mask_doc_regions(src, "javascript")
    assert out == src


def test_get_parser_handles_grammar_load_exception(monkeypatch):
    def _broken(_):
        raise RuntimeError("simulated grammar load failure")

    monkeypatch.setattr(
        "tree_sitter_language_pack.get_language", _broken
    )
    _text_filters._get_parser.cache_clear()
    # Should not raise; should return None.
    assert _text_filters._get_parser("python") is None


# --- Helpers ---

def test_language_from_path_known_extensions():
    from pathlib import Path

    from hypergumbo_core.linkers._text_filters import language_from_path

    assert language_from_path(Path("a.py")) == "python"
    assert language_from_path(Path("a.js")) == "javascript"
    assert language_from_path(Path("a.ts")) == "typescript"
    assert language_from_path(Path("a.tsx")) == "tsx"
    assert language_from_path(Path("a.java")) == "java"
    assert language_from_path(Path("a.unknown")) is None


def test_read_masked_source_masks_python_docstring(tmp_path):
    from hypergumbo_core.linkers._text_filters import read_masked_source

    f = tmp_path / "x.py"
    f.write_text('"""contains producer.send(\'x\') here"""\nx = 1\n')
    out = read_masked_source(f)
    assert "producer.send" not in out
    assert "x = 1" in out


def test_read_masked_source_unknown_extension_returns_unchanged(tmp_path):
    from hypergumbo_core.linkers._text_filters import read_masked_source

    f = tmp_path / "x.unknown"
    text = "// producer.send('x')\nfoo bar baz\n"
    f.write_text(text)
    assert read_masked_source(f) == text


def test_read_masked_source_explicit_language_overrides(tmp_path):
    from hypergumbo_core.linkers._text_filters import read_masked_source

    f = tmp_path / "x.unknown"
    f.write_text("// producer.send('x')\nconst y = 1;\n")
    out = read_masked_source(f, language="javascript")
    assert "producer.send" not in out
