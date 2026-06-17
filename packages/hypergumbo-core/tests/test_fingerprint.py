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


class TestNonStringGrammarGuard:
    """A None / non-string language hits the non-str-grammar guard and yields None.

    Directly covers ``_compute_fingerprint``'s non-tree-sitter-grammar guard
    (``if not isinstance(grammar, str): return None``, fingerprint.py:489-490),
    reached when a symbol's ``language`` is ``None`` — the value synthetic /
    boundary symbols carry — or maps to a non-string grammar. The orchestrator
    stamping pass cannot exercise this branch through synthetic nodes, because it
    span-filters zero-span synthetics (``span.start_line <= 0`` at
    fingerprint.py:540) before ever calling ``_compute_fingerprint``; the guard is
    nonetheless reachable legitimate code via the public one-shot API. It surfaced
    as the lone uncovered line while validating the WI-pozur Phase D/E reorder, so
    it gets a direct unit test here.
    """

    def test_none_language_returns_none(self) -> None:
        # language=None is exactly the boundary-node case; span is valid so the
        # empty-span guard (start_line<=0) is not what produces the None.
        fp = compute_symbol_fingerprint(None, _span(1, 1), b"x = 1\n")  # type: ignore[arg-type]
        assert fp is None


class TestPythonFingerprint:
    """Python uses the `ast` module path."""

    def test_emits_scheme_prefixed_hex(self) -> None:
        source = b"def f(x):\n    return x + 1\n"
        fp = compute_symbol_fingerprint("python", _span(1, 2), source)
        assert fp is not None
        assert fp.startswith("hgfp2:")
        assert len(fp) == len("hgfp2:") + 16

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
        assert fp.startswith("hgfp2:")

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
        assert fp.startswith("hgfp2:")

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

    def test_preserves_canonical_hgfp2_fingerprint(self, tmp_path: Path) -> None:
        """An already-canonical (hgfp2:) fingerprint is left untouched."""
        f = tmp_path / "a.py"
        f.write_text("def f(): pass\n")
        sym = _sym("python", "a.py", 1, 1)
        sym.fingerprint = "hgfp2:0123456789abcdef"
        stamp_symbol_fingerprints([sym], tmp_path)
        assert sym.fingerprint == "hgfp2:0123456789abcdef"

    def test_normalizes_noncanonical_fingerprint_on_source_node(
        self, tmp_path: Path,
    ) -> None:
        """WI-lisog: a non-canonical (bare-hex / legacy) fingerprint on a real
        source node (language is not None) is a producer-side leak — it is
        RECOMPUTED in the canonical hgfp2: scheme, not preserved. This inverts
        the old line-531 precedence trap that let producer bare-hex survive to
        output."""
        f = tmp_path / "a.py"
        f.write_text("def f(): pass\n")
        sym = _sym("python", "a.py", 1, 1)
        sym.fingerprint = "deadbeefdeadbeef"  # bare 16-hex producer leak
        stamp_symbol_fingerprints([sym], tmp_path)
        assert sym.fingerprint is not None
        assert sym.fingerprint.startswith("hgfp2:")
        assert sym.fingerprint != "deadbeefdeadbeef"

    def test_preserves_bare_hex_on_language_none_synthetic(
        self, tmp_path: Path,
    ) -> None:
        """A bare-hex identity fingerprint on a language=None Class-B synthetic
        is a documented second shape (the central pass cannot source-fingerprint
        a source-less synthetic) — it is preserved, not recomputed."""
        sym = _sym(None, "a.py", 1, 1)  # language=None ⇒ Class-B synthetic
        sym.fingerprint = "1b86eea11f129a27"
        stamp_symbol_fingerprints([sym], tmp_path)
        assert sym.fingerprint == "1b86eea11f129a27"

    def test_clears_noncanonical_leak_when_unfingerprintable(
        self, tmp_path: Path,
    ) -> None:
        """A bare-hex leak on a source node whose source can't be read is
        CLEARED to None (an honest null), never left as bare hex — the leak must
        not reach the output even when the recompute yields nothing."""
        sym = _sym("python", "nonexistent.py", 1, 1)
        sym.fingerprint = "deadbeefdeadbeef"
        stamp_symbol_fingerprints([sym], tmp_path)
        assert sym.fingerprint is None

    def test_stamps_when_null(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("def f(): pass\n")
        sym = _sym("python", "a.py", 1, 1)
        assert sym.fingerprint is None
        stamp_symbol_fingerprints([sym], tmp_path)
        assert sym.fingerprint is not None
        assert sym.fingerprint.startswith("hgfp2:")

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


class TestContextAwareFingerprint:
    """WI-falum / WI-lisog: spans are fingerprinted in their file's parse
    context, not as out-of-context snippets.

    The v1 implementation sliced the span out of the file and parsed the
    slice standalone. Two failure modes followed: a single-line TOML
    array element parses to an ERROR tree whose leaf walk drops the
    content (all 76 TOML dependency nodes collapsed to ONE fingerprint —
    WI-falum, a 6.0.0 regression), and a Python method containing a
    column-0 triple-quoted fixture defeats the dedent fallback
    (3,911 test methods fingerprint=None — WI-lisog facet a).
    """

    def test_toml_single_line_dep_spans_get_distinct_fingerprints(self) -> None:
        """The WI-falum regression case: distinct dependency lines must
        hash distinctly."""
        source = (
            b'[project]\n'
            b'name = "fixture"\n'
            b'dependencies = [\n'
            b'    "rich~=14.3.2",\n'
            b'    "bcrypt>=4.0",\n'
            b'    "ruamel.yaml>=0.18",\n'
            b']\n'
        )
        fps = [
            compute_symbol_fingerprint("toml", _span(line, line), source)
            for line in (4, 5, 6)
        ]
        assert all(fp is not None for fp in fps), fps
        assert len(set(fps)) == 3, fps

    def test_toml_identical_dep_lines_share_fingerprint(self) -> None:
        """Content-identical declarations hash identically (the structural
        contract): the same dep line in two manifests is the same content."""
        source_a = (
            b'[project]\nname = "a"\ndependencies = [\n'
            b'    "rich~=14.3.2",\n]\n'
        )
        source_b = (
            b'[project]\nname = "b"\ndependencies = [\n'
            b'    "click>=8.0",\n    "rich~=14.3.2",\n]\n'
        )
        fp_a = compute_symbol_fingerprint("toml", _span(4, 4), source_a)
        fp_b = compute_symbol_fingerprint("toml", _span(5, 5), source_b)
        assert fp_a is not None
        assert fp_a == fp_b

    def test_python_method_with_column0_heredoc_gets_fingerprint(self) -> None:
        """WI-lisog facet (a): a method whose body embeds a column-0
        triple-quoted fixture defeats textwrap.dedent (common prefix
        becomes 0, the ``def`` stays indented, ast.parse raises). In
        file context the method parses fine."""
        source = (
            b'class T:\n'
            b'    def m(self):\n'
            b'        s = """\n'
            b'class Order < ApplicationRecord\n'
            b'"""\n'
            b'        return s\n'
        )
        fp = compute_symbol_fingerprint("python", _span(2, 6), source)
        assert fp is not None

    def test_same_code_in_different_file_contexts_hashes_identically(self) -> None:
        """Parsing in context must not leak the surrounding file into the
        hash — structural identity of identical defs is preserved."""
        src1 = b"import os\n\ndef f(x):\n    return x + 1\n"
        src2 = b"import sys\nimport json\n\n\ndef f(x):\n    return x + 1\n"
        fp1 = compute_symbol_fingerprint("python", _span(3, 4), src1)
        fp2 = compute_symbol_fingerprint("python", _span(5, 6), src2)
        assert fp1 is not None
        assert fp1 == fp2

    def test_python_decorated_function_span_resolves(self) -> None:
        """Producer spans often start at the decorator line; the locator
        must still find the decorated def, not fall back to whole-module."""
        source = (
            b"import functools\n"
            b"@functools.cache\n"
            b"def f(x):\n"
            b"    return x + 1\n"
            b"def g(y):\n"
            b"    return y - 1\n"
        )
        fp_f = compute_symbol_fingerprint("python", _span(2, 4), source)
        fp_g = compute_symbol_fingerprint("python", _span(5, 6), source)
        assert fp_f is not None
        assert fp_g is not None
        assert fp_f != fp_g

    def test_python_span_straddling_siblings_hashes_contents(self) -> None:
        """A span covering two sibling defs (e.g. a region symbol) hashes
        the covered statements, never the whole module."""
        source = (
            b"import os\n"
            b"def f():\n    return 1\n"
            b"def g():\n    return 2\n"
        )
        fp_pair = compute_symbol_fingerprint("python", _span(2, 5), source)
        fp_whole = compute_symbol_fingerprint("python", _span(1, 5), source)
        assert fp_pair is not None
        assert fp_whole is not None
        assert fp_pair != fp_whole

    def test_unparseable_content_returns_none_never_constant(self) -> None:
        """The degenerate-hash failure mode: content the parser cannot
        see must yield None, not a constant value shared by everything."""
        fp = compute_symbol_fingerprint(
            "python", _span(1, 1), b"def f(:\n  invalid\n",
        )
        assert fp is None

    def test_toml_error_region_returns_none(self) -> None:
        """A span whose located subtree is an ERROR node yields None."""
        source = b'= = = garbage that is not toml = = =\n'
        fp = compute_symbol_fingerprint("toml", _span(1, 1), source)
        assert fp is None


class TestFallbackAndEdgePaths:
    """Cover the broken-file fallback and the degenerate-span guards."""

    BROKEN_TAIL = (
        b"def good(x):\n"
        b"    return x + 1\n"
        b"def broken(:\n"
        b"    nonsense\n"
    )

    def test_broken_file_falls_back_to_snippet_parse(self) -> None:
        """When the whole file fails ast.parse, a span over a valid
        region still fingerprints via the legacy snippet path."""
        fp = compute_symbol_fingerprint("python", _span(1, 2), self.BROKEN_TAIL)
        assert fp is not None
        assert fp.startswith("hgfp2:")

    def test_broken_file_span_past_eof_returns_none(self) -> None:
        fp = compute_symbol_fingerprint("python", _span(99, 99), self.BROKEN_TAIL)
        assert fp is None

    def test_parsed_file_span_over_comment_only_returns_none(self) -> None:
        """A span covering no AST-visible content (comment-only lines in
        a file that parses fine) yields an honest None."""
        source = b"# just a comment\n# another\ndef f():\n    return 1\n"
        fp = compute_symbol_fingerprint("python", _span(1, 2), source)
        assert fp is None

    def test_tree_sitter_span_past_eof_returns_none(self) -> None:
        source = b'[project]\nname = "x"\n'
        fp = compute_symbol_fingerprint("toml", _span(99, 99), source)
        assert fp is None

    def test_tree_sitter_whole_file_with_error_returns_none(self) -> None:
        """A whole-file span over a file whose root contains parse
        errors yields None via the fits-branch error guard. (No trailing
        newline: the document node must fit the trimmed range exactly to
        exercise the fits branch rather than the container branch.)"""
        source = b"a = 1\n= garbage here"
        fp = compute_symbol_fingerprint("toml", _span(1, 2), source)
        assert fp is None

    def test_tree_sitter_error_sibling_does_not_block_clean_span(self) -> None:
        """An error elsewhere in the file must not null a span whose own
        content parses cleanly (per-span honesty cuts both ways)."""
        source = b"a = 1\n= garbage here\n"
        fp = compute_symbol_fingerprint("toml", _span(1, 1), source)
        assert fp is not None

    def test_slice_source_defensive_empty_span(self) -> None:
        """Direct guard check: _slice_source returns b'' on inverted /
        zero spans (callers pre-filter, but the helper stays safe)."""
        from hypergumbo_core.fingerprint import _slice_source

        assert _slice_source(b"x = 1\n", _span(0, 0)) == b""


def test_scheme_constant_is_stable() -> None:
    """The scheme tag string is part of the on-disk contract; pin it.

    v2 (WI-falum): the context-aware rewrite changes every emitted value
    (subtree-rooted walks replace snippet-rooted walks), so the scheme
    tag and prefix bump per the module's own versioning convention.
    """
    assert SYMBOL_FINGERPRINT_SCHEME == "hypergumbo-symbol-fp-v2"


def test_prefix_is_hgfp2() -> None:
    """Every emitted fingerprint carries the v2 prefix."""
    fp = compute_symbol_fingerprint(
        "python", _span(1, 2), b"def f(x):\n    return x + 1\n",
    )
    assert fp is not None
    assert fp.startswith("hgfp2:")
    assert len(fp) == len("hgfp2:") + 16


# WI-tubuv: close hypergumbo-core's per-package isolation coverage gap on
# fingerprint.py (lines 310, 361). These paths were exercised cross-package in
# the combined suite but not by core's OWN tests, so `core` measured 99% in
# isolation — which kept the full-suite ALL_100 gate (and thus the last-green-sha
# marker) from ever firing. Covering them in isolation lets that gate work.


def test_content_byte_range_whitespace_only_span_returns_none() -> None:
    """A span whose lines hold only whitespace has no content to hash, so
    _content_byte_range returns None (fingerprint.py:310)."""
    from hypergumbo_core.fingerprint import _content_byte_range

    # Lines 2-3 are whitespace-only (spaces, tab, blank); the stripped chunk is
    # empty → None.
    source = b"x = 1\n   \n\t\ny = 2\n"
    assert _content_byte_range(source, 2, 3) is None


def test_node_has_error_true_for_error_typed_node() -> None:
    """A node whose ``type`` is 'ERROR' is a parse error → True
    (fingerprint.py:361), independent of the ``has_error`` attribute."""
    from hypergumbo_core.fingerprint import _node_has_error

    class _ErrorNode:
        type = "ERROR"

    assert _node_has_error(_ErrorNode()) is True
