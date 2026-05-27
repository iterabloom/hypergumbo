# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-jidat: Symbol.origin and Edge.origin are list[str].

Tests cover the field type change (construction, auto-normalization,
serialization, deserialization, backward compat with scalar JSON),
and the updated __post_init__ enforcement for Edge.
"""
from __future__ import annotations

from typing import ClassVar

import pytest

from hypergumbo_core.ir import Edge, LEGACY_DESERIALIZED_SENTINEL, Span, Symbol


# ---------------------------------------------------------------------------
# Symbol.origin tests
# ---------------------------------------------------------------------------


class TestSymbolOriginAsList:
    """Symbol.origin is list[str]."""

    _SPAN: ClassVar[Span] = Span(start_line=1, end_line=1, start_col=0, end_col=0)

    def _make(self, **kw) -> Symbol:
        defaults = {
            "id": "x:f:1-1:n:function", "name": "n", "kind": "function",
            "language": "x", "path": "f", "span": self._SPAN,
        }
        defaults.update(kw)
        return Symbol(**defaults)

    def test_default_is_empty_list(self) -> None:
        s = self._make()
        assert s.origin == []

    def test_list_value_preserved(self) -> None:
        s = self._make(origin=["python-v1"])
        assert s.origin == ["python-v1"]

    def test_multi_pass_attribution(self) -> None:
        s = self._make(origin=["python-v1", "scip"])
        assert s.origin == ["python-v1", "scip"]

    def test_str_auto_normalized_to_list(self) -> None:
        """Backward compat: str origin auto-wraps to single-element list."""
        s = self._make(origin="python-v1")
        assert s.origin == ["python-v1"]

    def test_empty_str_normalizes_to_empty_list(self) -> None:
        s = self._make(origin="")
        assert s.origin == []

    def test_to_dict_serializes_as_list(self) -> None:
        s = self._make(origin=["python-v1"])
        d = s.to_dict()
        assert d["origin"] == ["python-v1"]

    def test_from_dict_with_list_origin(self) -> None:
        d = {
            "id": "x:f:1-1:n:function", "name": "n", "kind": "function",
            "language": "x", "path": "f", "span": {"start_line": 1, "end_line": 1},
            "origin": ["python-v1", "scip"],
        }
        s = Symbol.from_dict(d)
        assert s.origin == ["python-v1", "scip"]

    def test_from_dict_backward_compat_scalar(self) -> None:
        """Legacy JSON with scalar origin deserializes to single-element list."""
        d = {
            "id": "x:f:1-1:n:function", "name": "n", "kind": "function",
            "language": "x", "path": "f", "span": {"start_line": 1, "end_line": 1},
            "origin": "python-v1",
        }
        s = Symbol.from_dict(d)
        assert s.origin == ["python-v1"]

    def test_from_dict_missing_origin(self) -> None:
        d = {
            "id": "x:f:1-1:n:function", "name": "n", "kind": "function",
            "language": "x", "path": "f", "span": {"start_line": 1, "end_line": 1},
        }
        s = Symbol.from_dict(d)
        assert s.origin == []

    def test_round_trip(self) -> None:
        s = self._make(origin=["python-v1", "scip"])
        d = s.to_dict()
        s2 = Symbol.from_dict(d)
        assert s2.origin == ["python-v1", "scip"]


# ---------------------------------------------------------------------------
# Edge.origin tests
# ---------------------------------------------------------------------------


class TestEdgeOriginAsList:
    """Edge.origin is list[str]."""

    _O: ClassVar[str] = "test-pass"
    _R: ClassVar[str] = "run-1"

    def test_create_with_str_normalizes(self) -> None:
        """Edge.create() with str origin auto-wraps to list."""
        e = Edge.create(
            src="s", dst="d", edge_type="calls", line=1,
            origin=self._O, origin_run_id=self._R,
        )
        assert e.origin == [self._O]

    def test_create_with_list(self) -> None:
        e = Edge.create(
            src="s", dst="d", edge_type="calls", line=1,
            origin=["pass-a", "pass-b"], origin_run_id=self._R,
        )
        assert e.origin == ["pass-a", "pass-b"]

    def test_empty_origin_raises(self) -> None:
        with pytest.raises(ValueError, match=r"Edge\.origin must be non-empty"):
            Edge.create(
                src="s", dst="d", edge_type="calls", line=1,
                origin="", origin_run_id=self._R,
            )

    def test_empty_list_origin_raises(self) -> None:
        with pytest.raises(ValueError, match=r"Edge\.origin must be non-empty"):
            Edge.create(
                src="s", dst="d", edge_type="calls", line=1,
                origin=[], origin_run_id=self._R,
            )

    def test_to_dict_serializes_as_list(self) -> None:
        e = Edge.create(
            src="s", dst="d", edge_type="calls", line=1,
            origin=self._O, origin_run_id=self._R,
        )
        d = e.to_dict()
        assert d["origin"] == [self._O]

    def test_from_dict_with_list(self) -> None:
        d = {
            "id": "e1", "src": "s", "dst": "d", "type": "calls",
            "line": 1, "origin": ["pass-a", "pass-b"],
            "origin_run_id": "r", "meta": {},
        }
        e = Edge.from_dict(d)
        assert e.origin == ["pass-a", "pass-b"]

    def test_from_dict_backward_compat_scalar(self) -> None:
        d = {
            "id": "e1", "src": "s", "dst": "d", "type": "calls",
            "line": 1, "origin": "python",
            "origin_run_id": "r", "meta": {},
        }
        e = Edge.from_dict(d)
        assert e.origin == ["python"]

    def test_from_dict_empty_origin_gets_sentinel(self) -> None:
        d = {
            "id": "e1", "src": "s", "dst": "d", "type": "calls",
            "line": 1, "origin": "",
            "origin_run_id": "r", "meta": {},
        }
        e = Edge.from_dict(d)
        assert e.origin == [LEGACY_DESERIALIZED_SENTINEL]

    def test_from_dict_missing_origin_gets_sentinel(self) -> None:
        d = {
            "id": "e1", "src": "s", "dst": "d", "type": "calls",
            "line": 1, "origin_run_id": "r", "meta": {},
        }
        e = Edge.from_dict(d)
        assert e.origin == [LEGACY_DESERIALIZED_SENTINEL]

    def test_round_trip(self) -> None:
        e = Edge.create(
            src="s", dst="d", edge_type="calls", line=1,
            origin=["pass-a", "pass-b"], origin_run_id=self._R,
        )
        d = e.to_dict()
        e2 = Edge.from_dict(d)
        assert e2.origin == ["pass-a", "pass-b"]
