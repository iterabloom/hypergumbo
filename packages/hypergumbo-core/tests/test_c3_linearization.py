# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-hiziz (WI-noham D1): C3 linearization for the Python MRO walker.

Python's method resolution order is C3 linearization, NOT the insertion-order
BFS the Ruby/Groovy walker uses. The two agree on single inheritance and on
even diamonds, but DIVERGE on uneven-depth diamonds — and there insertion-order
picks the WRONG ancestor (a confidently-wrong edge). This file locks the C3
algorithm against real ``__mro__`` order plus the total-function guarantees a
static walker needs (never raise on a cycle or an inconsistent hierarchy).

The canonical divergence (verified against CPython)::

    class A: ...
    class B(A): def m(): ...
    class C(B): ...
    class D(A): def m(): ...
    class E(C, D): ...
    # E.__mro__ == [E, C, B, D, A]  -> E().m() resolves to B.m
    # insertion-order BFS visits [E, C, D, B, A] -> would pick D.m (WRONG)
"""

from __future__ import annotations

from hypergumbo_core.linkers.inherited_calls import _c3_merge, _linearize_c3


def _idx(**children: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Build a child -> [(parent, 'extends'), ...] index from name->bases."""
    return {c: [(p, "extends") for p in bases] for c, bases in children.items()}


class TestC3Merge:
    def test_empty(self) -> None:
        assert _c3_merge([]) == []

    def test_single_sequence(self) -> None:
        assert _c3_merge([["A", "B", "C"]]) == ["A", "B", "C"]

    def test_simple_merge(self) -> None:
        # merge([B, A], [C, A], [B, C]) -> [B, C, A]
        assert _c3_merge([["B", "A"], ["C", "A"], ["B", "C"]]) == ["B", "C", "A"]

    def test_inconsistent_hierarchy_degrades_without_raising(self) -> None:
        # No good head: X is in the tail of [Y, X] and Y in the tail of [X, Y].
        # A real C3 would raise TypeError; the static walker must degrade.
        out = _c3_merge([["X", "Y"], ["Y", "X"]])
        assert set(out) == {"X", "Y"}
        assert len(out) == 2  # no duplicates, terminates


class TestLinearizeC3:
    def test_single_class_no_bases(self) -> None:
        assert _linearize_c3("A", {}) == ["A"]

    def test_single_inheritance_chain(self) -> None:
        idx = _idx(C=["B"], B=["A"])
        assert _linearize_c3("C", idx) == ["C", "B", "A"]

    def test_simple_diamond_left_to_right(self) -> None:
        # D(B, C), B(A), C(A) -> [D, B, C, A]
        idx = _idx(D=["B", "C"], B=["A"], C=["A"])
        assert _linearize_c3("D", idx) == ["D", "B", "C", "A"]

    def test_uneven_diamond_matches_cpython_mro(self) -> None:
        # The critical case: E(C, D), C(B), B(A), D(A).
        # CPython: E.__mro__ == [E, C, B, D, A].  Insertion-order BFS would
        # give [E, C, D, B, A] -> the wrong ancestor for a method on both B & D.
        idx = _idx(E=["C", "D"], C=["B"], B=["A"], D=["A"])
        assert _linearize_c3("E", idx) == ["E", "C", "B", "D", "A"]

    def test_cycle_returns_none_without_raising(self) -> None:
        # A 2-cycle is a malformed hierarchy (can't be valid Python). The walker
        # must bias to unresolved (None), never a best-effort order that could
        # confidently resolve to the wrong ancestor — and never raise/loop.
        idx = _idx(A=["B"], B=["A"])
        assert _linearize_c3("A", idx) is None

    def test_self_cycle_returns_none(self) -> None:
        idx = _idx(A=["A"])
        assert _linearize_c3("A", idx) is None

    def test_depth_cap_returns_none_not_truncated_order(self) -> None:
        # A chain deeper than the cap: truncating a branch would silently drop a
        # precedence edge and REORDER real ancestors (a wrong positive). So an
        # exhausted budget biases to unresolved (None), never a partial order.
        idx = _idx(A=["B"], B=["C"], C=["D"])
        assert _linearize_c3("A", idx, depth_cap=2) is None

    def test_shared_ancestor_memoized_correctly(self) -> None:
        # Diamond forces the shared ancestor A to be reached twice; the memo
        # must produce a single, correctly-ordered A at the tail.
        idx = _idx(D=["B", "C"], B=["A"], C=["A"])
        out = _linearize_c3("D", idx)
        assert out.count("A") == 1
        assert out[-1] == "A"
