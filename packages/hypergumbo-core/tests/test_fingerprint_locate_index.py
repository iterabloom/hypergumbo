# SPDX-License-Identifier: AGPL-3.0-or-later
"""Equivalence tests for the python fingerprint locator's per-tree index (WI-balaf).

WHY THIS FILE EXISTS. ``_python_context_fingerprint`` locates the smallest AST
node covering a symbol's span. It used to do that with a fresh ``ast.walk`` over
the WHOLE module on EVERY call, which made ``stamp_symbol_fingerprints``
quadratic in file size — cost ~= symbols(file) x nodes(file). Measured on this
monorepo (``scripts/measure-survey-phase-split.py``): 324s of a 517s cold
``run_survey``, 62.6% of wall, in that one post-pass. The locator now consults a
per-tree index built once per (path, language).

WHAT THESE TESTS PROTECT. A fingerprint is OUTPUT DATA — it is serialized on
every Symbol and consumed downstream. So the index is only permitted to be
faster, never different: every fingerprint must be byte-identical to what the
full-tree scan produced. The tests therefore keep a REFERENCE implementation of
the original algorithm and assert agreement across real repository files and a
battery of hand-built edge cases.

A differential test against a copy of the old code is the right shape for a
pure-refactor contract, but on its own it is a weak guard: if the index and the
reference drifted together the test would still pass, and a differential test
cannot fail for the ONE rule a "smarter" rewrite is most likely to break. So
this file also PINS that rule directly, with a positive control:

    when two nested nodes cover the span with EQUAL extent, the OUTER one wins.

That falls out of the original's strict ``extent < best_extent`` against
``ast.walk``'s breadth-first order, and it is exactly what a tempting
descend-to-the-deepest-covering-node rewrite would invert — silently changing
the hashed subtree, and with it every affected fingerprint. ``test_tie_...``
below fails loudly if that ever flips, and ``test_reference_detects_a_tie_flip``
proves the reference itself can tell the two rules apart (a guard that cannot be
shown to fire is indistinguishable from one matching nothing).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hypergumbo_core.fingerprint import (
    _hash,
    _py_effective_lines,
    _python_context_fingerprint,
    _walk_python_ast,
)


def _reference_context_fingerprint(
    tree: ast.Module, start_line: int, end_line: int,
) -> str | None:
    """The pre-index algorithm, verbatim in behavior.

    Kept here (not imported) precisely so the production implementation is free
    to change while this stays the fixed point of comparison.
    """
    best: ast.AST = tree
    best_extent = float("inf")
    for node in ast.walk(tree):
        if isinstance(node, ast.Module):
            continue
        lines = _py_effective_lines(node)
        if lines is None:
            continue
        n_start, n_end = lines
        if n_start <= start_line and n_end >= end_line:
            extent = n_end - n_start
            if extent < best_extent:
                best, best_extent = node, extent
    if not isinstance(best, ast.Module):
        lines = _py_effective_lines(best)
        assert lines is not None
        decl_start = (
            best.lineno
            if isinstance(best, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            else lines[0]
        )
        if decl_start >= start_line and lines[1] <= end_line:
            parts: list[str] = []
            _walk_python_ast(best, parts)
            return _hash(parts)
    parts = []
    for child in ast.iter_child_nodes(best):
        lines = _py_effective_lines(child)
        if lines is None:
            continue
        if lines[0] >= start_line and lines[1] <= end_line:
            _walk_python_ast(child, parts)
    if not parts:
        return None
    return _hash(parts)


def _deepest_first_fingerprint(
    tree: ast.Module, start_line: int, end_line: int,
) -> str | None:
    """The plausible-but-WRONG variant: on an extent tie, the INNER node wins.

    Exists only as the positive control's other arm — something the reference
    must be able to disagree with, so that "reference and production agree" is
    a meaningful statement rather than a vacuous one.
    """
    best: ast.AST = tree
    best_extent = float("inf")
    for node in ast.walk(tree):
        if isinstance(node, ast.Module):
            continue
        lines = _py_effective_lines(node)
        if lines is None:
            continue
        n_start, n_end = lines
        if n_start <= start_line and n_end >= end_line:
            extent = n_end - n_start
            if extent <= best_extent:  # <= : later (deeper) node wins ties
                best, best_extent = node, extent
    if not isinstance(best, ast.Module):
        lines = _py_effective_lines(best)
        assert lines is not None
        decl_start = (
            best.lineno
            if isinstance(best, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            else lines[0]
        )
        if decl_start >= start_line and lines[1] <= end_line:
            parts: list[str] = []
            _walk_python_ast(best, parts)
            return _hash(parts)
    parts = []
    for child in ast.iter_child_nodes(best):
        lines = _py_effective_lines(child)
        if lines is None:
            continue
        if lines[0] >= start_line and lines[1] <= end_line:
            _walk_python_ast(child, parts)
    if not parts:
        return None
    return _hash(parts)


# --- hand-built sources covering the branches the locator distinguishes -----

_TIE_SOURCE = """\
def outer():
    helper(1)
"""

_DECORATED = """\
@decorator
@another(arg=1)
def decorated(a, b):
    return a + b
"""

_SIBLINGS = """\
def first():
    return 1


def second():
    return 2
"""

_CLASS_BODY = """\
class Holder:
    attribute = 1

    def method(self):
        return self.attribute
"""

_NESTED = """\
def outer():
    def inner():
        return 1
    return inner
"""

_ASYNC = """\
@wrap
async def coro(x):
    await x
"""

_ONE_LINERS = """\
a = 1
b = 2
c = a + b
"""

_MULTILINE_EXPR = """\
value = (
    1
    + 2
    + 3
)
"""

_EMPTY_TAIL = """\
def only():
    pass
"""

_SOURCES = {
    "tie": _TIE_SOURCE,
    "decorated": _DECORATED,
    "siblings": _SIBLINGS,
    "class_body": _CLASS_BODY,
    "nested": _NESTED,
    "async": _ASYNC,
    "one_liners": _ONE_LINERS,
    "multiline_expr": _MULTILINE_EXPR,
    "empty_tail": _EMPTY_TAIL,
}


def _all_spans(source: str) -> list[tuple[int, int]]:
    """Every (start, end) line pair within the source, plus out-of-range ones.

    Exhaustive rather than sampled: these sources are a handful of lines each,
    and the interesting disagreements live at the boundaries (a span one line
    past the end, a span covering two siblings, a zero-width span).
    """
    n = source.count("\n") + 1
    spans = [
        (s, e)
        for s in range(1, n + 2)
        for e in range(s, n + 2)
    ]
    spans.extend([(1, n + 5), (n + 3, n + 4)])
    return spans


@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_index_matches_reference_on_every_span(name: str) -> None:
    """Exhaustive equivalence over small sources: no span may disagree."""
    source = _SOURCES[name]
    tree = ast.parse(source)
    for start, end in _all_spans(source):
        assert _python_context_fingerprint(tree, start, end) == (
            _reference_context_fingerprint(tree, start, end)
        ), f"{name}: disagreement at span {start}-{end}"


def _repo_python_files(limit: int = 6) -> list[Path]:
    """A size-spread sample of real first-party python files.

    Real files are what exposed the cost, and they carry construct
    combinations (decorated nested classes, comprehensions, match statements,
    long multi-line calls) that hand-built sources do not.
    """
    root = Path(__file__).resolve().parents[3]
    src = root / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"
    files = sorted(
        (p for p in src.glob("*.py") if p.stat().st_size > 2000),
        key=lambda p: p.stat().st_size,
    )
    if not files:  # pragma: no cover - the package always ships .py sources
        return []
    step = max(1, len(files) // limit)
    return files[::step][:limit]


@pytest.mark.parametrize("path", _repo_python_files(), ids=lambda p: p.name)
def test_index_matches_reference_on_real_sources(path: Path) -> None:
    """Equivalence on real modules, sampling spans across the whole file."""
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source)
    n_lines = source.count("\n") + 1
    # 40 probes spread through the file, each a few lines wide, plus the
    # whole-file span (the container branch) and a past-the-end span.
    spans = [
        (max(1, n_lines * i // 41), min(n_lines, max(1, n_lines * i // 41) + 4))
        for i in range(1, 41)
    ]
    spans.append((1, n_lines))
    spans.append((n_lines, n_lines + 3))
    for start, end in spans:
        assert _python_context_fingerprint(tree, start, end) == (
            _reference_context_fingerprint(tree, start, end)
        ), f"{path.name}: disagreement at span {start}-{end}"


def test_tie_on_equal_extent_keeps_the_outer_node() -> None:
    """PIN: equal-extent ties resolve to the OUTER (breadth-first-earlier) node.

    ``helper(1)`` on line 2 of ``_TIE_SOURCE`` is covered by an ``Expr``
    statement and, with identical line extent, by the ``Call`` inside it. The
    original picked the ``Expr`` because ``ast.walk`` reaches it first and the
    comparison is strictly ``<``. Hashing the ``Call`` instead would yield a
    different fingerprint for the same source, so this must not drift.
    """
    tree = ast.parse(_TIE_SOURCE)
    got = _python_context_fingerprint(tree, 2, 2)

    expr_stmt = tree.body[0].body[0]
    assert isinstance(expr_stmt, ast.Expr)
    outer_parts: list[str] = []
    _walk_python_ast(expr_stmt, outer_parts)
    inner_parts: list[str] = []
    _walk_python_ast(expr_stmt.value, inner_parts)

    # The two candidates really are distinguishable — otherwise this test
    # would pass no matter which node the locator picked.
    assert _hash(outer_parts) != _hash(inner_parts)
    assert got == _hash(outer_parts)


def test_reference_detects_a_tie_flip() -> None:
    """POSITIVE CONTROL: the reference disagrees with the deepest-wins variant.

    Without this, "production matches the reference" could be true simply
    because no test input distinguishes the two rules — a guard that cannot be
    shown to fire.
    """
    tree = ast.parse(_TIE_SOURCE)
    assert _reference_context_fingerprint(tree, 2, 2) != (
        _deepest_first_fingerprint(tree, 2, 2)
    )


def test_index_is_built_once_per_tree() -> None:
    """The index must be REUSED across calls, else the quadratic is intact.

    Counts ``ast.walk`` invocations through the pass's shared cache: N locator
    calls against one cached tree must not cost N tree walks. Without this the
    refactor could pass every equivalence test above while rebuilding the index
    per call — the exact defect it exists to remove.
    """
    from hypergumbo_core import fingerprint as fp_mod

    tree = ast.parse(_SIBLINGS)
    cache: dict = {}
    calls = {"n": 0}
    real_walk = ast.walk

    def counting_walk(node):
        calls["n"] += 1
        return real_walk(node)

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(fp_mod.ast, "walk", counting_walk)
        for _ in range(5):
            fp_mod._python_context_fingerprint_cached(tree, 1, 2, cache, "k")
    finally:
        monkey.undo()

    # One walk to build the index. The container branch walks a located
    # subtree via _walk_python_ast (its own recursion, not ast.walk), so a
    # correct implementation stays at exactly 1.
    assert calls["n"] == 1, (
        f"index rebuilt: {calls['n']} ast.walk calls for 5 locator calls"
    )
