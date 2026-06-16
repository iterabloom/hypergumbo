# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-bokab (stable_id v7): file-identity fold for tree-sitter producers.

The v6 tree-sitter path left ``containing_stable_id=""`` for file-resident symbols
and carried scope only in ``qualified_name``, so two symbols with the same
``(kind, name, qualified_name)`` in DIFFERENT files hashed identically — a cross-file
collision (the corpus limb of INV-tazaj; bash ``usage``, Go ``main``/``init``,
Rust ``project_root``, TS ``createMockClient``, lua/ruby/objc confirmed live).

This module pins the CORE fold mechanism (the shared formula entrypoints in
``analyze/base.py``): both :meth:`TreeSitterAnalyzer.compute_stable_id` (untyped tier)
and :func:`make_typed_stable_id` (typed tier) accept a ``file_stable_id`` keyword and
fold it into the ``containing_stable_id`` slot of the shared ``assemble`` formula when
no enclosing-scope containing is supplied — mirroring py.py's ``file_containing_id``
threading (ADR-0035 §1/§4). :meth:`TreeSitterAnalyzer._file_anchor` is the canonical
producer of that value: ``make_file_stable_id(lang, normalize_path(rel_path))``, which
byte-matches the file Symbol's own stable_id (computed from the normalized
repo-relative path by ``populate_kind_stable_ids``). Per-analyzer threading of the
anchor into call sites is covered by per-analyzer integration tests.
"""
from __future__ import annotations

import types

from hypergumbo_core.analyze.base import (
    TreeSitterAnalyzer,
    make_file_stable_id,
    make_typed_stable_id,
)

_A = "sha256:aaaa000000000000"
_B = "sha256:bbbb000000000000"


class _StubAnalyzer(TreeSitterAnalyzer):
    """Minimal concrete analyzer — only ``lang`` is needed for the fold tests."""

    lang = "stublang"


def _fake_node() -> object:
    """A stand-in tree-sitter node: ``compute_stable_id`` only iterates ``.children``
    (via ``_find_params_node`` / ``_extract_decorator_names``), so an empty list is
    sufficient to exercise the fold without a real parse tree."""
    return types.SimpleNamespace(children=[])


# ----------------------------------------------------------------------
# make_typed_stable_id (typed tier)
# ----------------------------------------------------------------------


def test_make_typed_folds_file_stable_id_into_empty_containing() -> None:
    anchored = make_typed_stable_id(
        "function", "()void", "", name="f", qualified_name="f", file_stable_id=_A,
    )
    explicit = make_typed_stable_id(
        "function", "()void", "", containing_stable_id=_A, name="f", qualified_name="f",
    )
    assert anchored == explicit


def test_make_typed_explicit_containing_wins_over_file_stable_id() -> None:
    """A real enclosing-scope containing must NOT be clobbered by the file anchor."""
    real = "sha256:dead0000beef0000"
    with_both = make_typed_stable_id(
        "function", "()void", "", containing_stable_id=real,
        name="f", qualified_name="f", file_stable_id=_A,
    )
    only_real = make_typed_stable_id(
        "function", "()void", "", containing_stable_id=real, name="f", qualified_name="f",
    )
    assert with_both == only_real


def test_make_typed_no_anchor_is_back_compat() -> None:
    """Omitting file_stable_id reproduces the pre-v7 value (containing stays '')."""
    new = make_typed_stable_id("function", "()void", "", name="f", qualified_name="f")
    legacy = make_typed_stable_id(
        "function", "()void", "", containing_stable_id="", name="f", qualified_name="f",
    )
    assert new == legacy


def test_make_typed_distinct_file_anchors_yield_distinct_ids() -> None:
    a = make_typed_stable_id("function", "()void", "", name="f", qualified_name="f", file_stable_id=_A)
    b = make_typed_stable_id("function", "()void", "", name="f", qualified_name="f", file_stable_id=_B)
    assert a != b


# ----------------------------------------------------------------------
# TreeSitterAnalyzer.compute_stable_id (untyped tier)
# ----------------------------------------------------------------------


def test_compute_stable_id_folds_file_stable_id() -> None:
    a = _StubAnalyzer()
    folded = a.compute_stable_id(
        _fake_node(), "function", name="f", qualified_name="f", file_stable_id=_A,
    )
    explicit = a.compute_stable_id(
        _fake_node(), "function", containing_stable_id=_A, name="f", qualified_name="f",
    )
    assert folded == explicit


def test_compute_stable_id_distinct_file_anchors_yield_distinct_ids() -> None:
    a = _StubAnalyzer()
    x = a.compute_stable_id(_fake_node(), "function", name="f", qualified_name="f", file_stable_id=_A)
    y = a.compute_stable_id(_fake_node(), "function", name="f", qualified_name="f", file_stable_id=_B)
    assert x != y


def test_compute_stable_id_explicit_containing_wins() -> None:
    a = _StubAnalyzer()
    real = "sha256:dead0000beef0000"
    both = a.compute_stable_id(
        _fake_node(), "function", containing_stable_id=real,
        name="f", qualified_name="f", file_stable_id=_A,
    )
    only = a.compute_stable_id(
        _fake_node(), "function", containing_stable_id=real, name="f", qualified_name="f",
    )
    assert both == only


def test_compute_stable_id_no_anchor_is_back_compat() -> None:
    a = _StubAnalyzer()
    new = a.compute_stable_id(_fake_node(), "function", name="f", qualified_name="f")
    legacy = a.compute_stable_id(
        _fake_node(), "function", containing_stable_id="", name="f", qualified_name="f",
    )
    assert new == legacy


# ----------------------------------------------------------------------
# TreeSitterAnalyzer._file_anchor (the canonical anchor value)
# ----------------------------------------------------------------------


def test_file_anchor_equals_normalized_make_file_stable_id() -> None:
    a = _StubAnalyzer()
    assert a._file_anchor("pkg/mod.stub") == make_file_stable_id("stublang", "pkg/mod.stub")


def test_file_anchor_normalizes_backslashes() -> None:
    """Windows-separator paths fold to the same anchor as forward-slash paths, so the
    fold byte-matches the file node's stable_id cross-platform."""
    a = _StubAnalyzer()
    assert a._file_anchor("pkg\\mod.stub") == a._file_anchor("pkg/mod.stub")
