# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for hypergumbo_core.fingerprint (WI-fanun).

The structural fingerprint captures shape + identifiers + literals.
These tests pin the boundary properties:

- whitespace / blank-line changes do NOT change the fingerprint
- comment changes do NOT change the fingerprint
- identifier renames DO change the fingerprint
- literal-value changes DO change the fingerprint

Plus the orchestrator-pass behavior:

- Symbols with non-None ``fingerprint`` are not touched
- Symbols without a span / path / readable source get None
- The pass reads each file at most once (cache hit on second symbol)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.fingerprint import (
    SYMBOL_FINGERPRINT_SCHEME,
    compute_symbol_fingerprint,
    stamp_symbol_fingerprints,
)
from hypergumbo_core.ir import Span, Symbol


def _span(start: int, end: int) -> Span:
    return Span(start_line=start, end_line=end, start_col=0, end_col=0)


def _sym(language: str, path: str, start: int, end: int, name: str = "f") -> Symbol:
    return Symbol(
        id=f"{language}:{path}:{start}-{end}:{name}:function",
        name=name,
        kind="function",
        language=language,
        path=path,
        span=_span(start, end),
        origin="test-pass",
        origin_run_id="test-run",
    )


class TestPythonFingerprint:
    """Python uses the `ast` module path."""

    def test_emits_scheme_prefixed_hex(self) -> None:
        source = b"def f(x):\n    return x + 1\n"
        fp = compute_symbol_fingerprint("python", _span(1, 2), source)
        assert fp is not None
        assert fp.startswith("hgfp1:")
        assert len(fp) == len("hgfp1:") + 16

    def test_whitespace_changes_dont_change_fingerprint(self) -> None:
        a = b"def f(x):\n    return x + 1\n"
        b = b"def f(x):\n\n\n    return x +     1\n"
        fp_a = compute_symbol_fingerprint("python", _span(1, 2), a)
        fp_b = compute_symbol_fingerprint("python", _span(1, 4), b)
        assert fp_a == fp_b

    def test_comment_changes_dont_change_fingerprint(self) -> None:
        a = b"def f(x):\n    return x + 1\n"
        b = b"def f(x):\n    # different comment\n    return x + 1\n"
        fp_a = compute_symbol_fingerprint("python", _span(1, 2), a)
        fp_b = compute_symbol_fingerprint("python", _span(1, 3), b)
        assert fp_a == fp_b

    def test_rename_changes_fingerprint(self) -> None:
        a = b"def f(x):\n    return x + 1\n"
        b = b"def g(x):\n    return x + 1\n"
        fp_a = compute_symbol_fingerprint("python", _span(1, 2), a)
        fp_b = compute_symbol_fingerprint("python", _span(1, 2), b)
        assert fp_a != fp_b

    def test_literal_change_changes_fingerprint(self) -> None:
        a = b"def f(x):\n    return x + 1\n"
        b = b"def f(x):\n    return x + 2\n"
        fp_a = compute_symbol_fingerprint("python", _span(1, 2), a)
        fp_b = compute_symbol_fingerprint("python", _span(1, 2), b)
        assert fp_a != fp_b

    def test_syntax_error_returns_none(self) -> None:
        fp = compute_symbol_fingerprint(
            "python", _span(1, 1), b"def f(:\n  invalid",
        )
        assert fp is None

    def test_empty_span_returns_none(self) -> None:
        fp = compute_symbol_fingerprint("python", _span(5, 4), b"def f(): pass\n")
        assert fp is None

    def test_class_method_indented_snippet_parses_via_dedent(self) -> None:
        """Slicing a method's span yields an indented snippet that fails
        bare ``ast.parse``. The dedent fallback recovers it."""
        source = b"class Foo:\n    def bar(self, x):\n        return x + 1\n"
        # Method span: lines 2-3 (the ``def bar`` body, indented).
        fp = compute_symbol_fingerprint("python", _span(2, 3), source)
        assert fp is not None
        assert fp.startswith("hgfp1:")

    def test_attribute_access_is_part_of_fingerprint(self) -> None:
        """ast.Attribute leaf — renaming the attribute changes the fingerprint."""
        a = b"def f():\n    return x.foo\n"
        b = b"def f():\n    return x.bar\n"
        fp_a = compute_symbol_fingerprint("python", _span(1, 2), a)
        fp_b = compute_symbol_fingerprint("python", _span(1, 2), b)
        assert fp_a is not None
        assert fp_b is not None
        assert fp_a != fp_b

    def test_class_name_is_part_of_fingerprint(self) -> None:
        """ast.ClassDef leaf — renaming the class changes the fingerprint."""
        a = b"class Foo:\n    pass\n"
        b = b"class Bar:\n    pass\n"
        fp_a = compute_symbol_fingerprint("python", _span(1, 2), a)
        fp_b = compute_symbol_fingerprint("python", _span(1, 2), b)
        assert fp_a is not None
        assert fp_b is not None
        assert fp_a != fp_b


class TestTreeSitterFingerprint:
    """Tree-sitter languages use the language pack."""

    def test_javascript_basic(self) -> None:
        source = b"function f(x) { return x + 1; }\n"
        fp = compute_symbol_fingerprint("javascript", _span(1, 1), source)
        assert fp is not None
        assert fp.startswith("hgfp1:")

    def test_javascript_comment_filtered(self) -> None:
        a = b"function f(x) { return x + 1; }\n"
        b = b"function f(x) { /* commentary */ return x + 1; }\n"
        fp_a = compute_symbol_fingerprint("javascript", _span(1, 1), a)
        fp_b = compute_symbol_fingerprint("javascript", _span(1, 1), b)
        assert fp_a == fp_b

    def test_javascript_rename_changes(self) -> None:
        a = b"function f(x) { return x + 1; }\n"
        b = b"function g(x) { return x + 1; }\n"
        fp_a = compute_symbol_fingerprint("javascript", _span(1, 1), a)
        fp_b = compute_symbol_fingerprint("javascript", _span(1, 1), b)
        assert fp_a != fp_b

    def test_unsupported_grammar_returns_none(self) -> None:
        fp = compute_symbol_fingerprint(
            "definitely_not_a_real_language",
            _span(1, 1), b"some code\n",
        )
        assert fp is None


class TestStampOrchestrator:
    """Cover the post-pass that mutates Symbol.fingerprint in place."""

    def test_skips_already_populated(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("def f(): pass\n")
        sym = _sym("python", "a.py", 1, 1)
        sym.fingerprint = "preexisting-from-toml-v1"
        stamp_symbol_fingerprints([sym], tmp_path)
        assert sym.fingerprint == "preexisting-from-toml-v1"

    def test_stamps_when_null(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("def f(): pass\n")
        sym = _sym("python", "a.py", 1, 1)
        assert sym.fingerprint is None
        stamp_symbol_fingerprints([sym], tmp_path)
        assert sym.fingerprint is not None
        assert sym.fingerprint.startswith("hgfp1:")

    def test_missing_file_leaves_null(self, tmp_path: Path) -> None:
        sym = _sym("python", "nonexistent.py", 1, 1)
        stamp_symbol_fingerprints([sym], tmp_path)
        assert sym.fingerprint is None

    def test_no_span_leaves_null(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("def f(): pass\n")
        sym = _sym("python", "a.py", 1, 1)
        sym.span = None
        stamp_symbol_fingerprints([sym], tmp_path)
        assert sym.fingerprint is None

    def test_no_path_leaves_null(self, tmp_path: Path) -> None:
        sym = _sym("python", "", 1, 1)
        sym.path = ""
        stamp_symbol_fingerprints([sym], tmp_path)
        assert sym.fingerprint is None

    def test_zero_span_leaves_null(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("def f(): pass\n")
        sym = _sym("python", "a.py", 0, 0)
        stamp_symbol_fingerprints([sym], tmp_path)
        assert sym.fingerprint is None

    def test_unsupported_language_leaves_null(self, tmp_path: Path) -> None:
        f = tmp_path / "a.weird"
        f.write_text("placeholder\n")
        sym = _sym("definitely_unsupported", "a.weird", 1, 1)
        stamp_symbol_fingerprints([sym], tmp_path)
        assert sym.fingerprint is None

    def test_negative_read_result_is_cached(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a file read fails, the second symbol on the same path
        shouldn't trigger a second I/O attempt."""
        attempt_count = 0
        original_read = Path.read_bytes

        def counting_read(self: Path, *args, **kwargs):
            nonlocal attempt_count
            if self.name == "missing.py":
                attempt_count += 1
                raise OSError("simulated read failure")
            return original_read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_bytes", counting_read)
        sym1 = _sym("python", "missing.py", 1, 1, name="f")
        sym2 = _sym("python", "missing.py", 2, 2, name="g")
        stamp_symbol_fingerprints([sym1, sym2], tmp_path)
        assert sym1.fingerprint is None
        assert sym2.fingerprint is None
        # Negative cached — second lookup skipped the read.
        assert attempt_count == 1

    def test_caches_source_reads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two Symbols on the same file should only trigger one file read."""
        f = tmp_path / "a.py"
        f.write_text("def f(): pass\ndef g(): pass\n")
        read_count = 0
        original_read = Path.read_bytes

        def counting_read(self: Path, *args, **kwargs):
            nonlocal read_count
            if self.name == "a.py":
                read_count += 1
            return original_read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_bytes", counting_read)
        sym1 = _sym("python", "a.py", 1, 1, name="f")
        sym2 = _sym("python", "a.py", 2, 2, name="g")
        stamp_symbol_fingerprints([sym1, sym2], tmp_path)
        assert sym1.fingerprint is not None
        assert sym2.fingerprint is not None
        assert read_count == 1


def test_scheme_constant_is_stable() -> None:
    """The scheme tag string is part of the on-disk contract; pin it."""
    assert SYMBOL_FINGERPRINT_SCHEME == "hypergumbo-symbol-fp-v1"
