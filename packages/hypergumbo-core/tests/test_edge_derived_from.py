# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-rukor: Edge.derived_from records which Symbols a linker consumed.

Tests cover the field itself (creation, serialization, deserialization,
backward compat) and a source-tree scan enforcing that every Edge.create()
call in a linker file passes derived_from explicitly.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import ClassVar

import pytest

from hypergumbo_core.ir import Edge


# ---------------------------------------------------------------------------
# Unit tests — field semantics
# ---------------------------------------------------------------------------


class TestDerivedFromField:
    """Edge.derived_from: list[str] field basics."""

    _ORIGIN: ClassVar[str] = "test-pass"
    _RUN_ID: ClassVar[str] = "run-1"

    def test_defaults_to_none(self) -> None:
        edge = Edge.create(
            src="s", dst="d", edge_type="calls", line=1,
            origin=self._ORIGIN, origin_run_id=self._RUN_ID,
        )
        assert edge.derived_from is None

    def test_create_accepts_derived_from(self) -> None:
        edge = Edge.create(
            src="s", dst="d", edge_type="calls", line=1,
            origin=self._ORIGIN, origin_run_id=self._RUN_ID,
            derived_from=["s", "d"],
        )
        assert edge.derived_from == ["s", "d"]

    def test_create_derived_from_with_intermediaries(self) -> None:
        edge = Edge.create(
            src="s", dst="d", edge_type="calls", line=1,
            origin=self._ORIGIN, origin_run_id=self._RUN_ID,
            derived_from=["s", "d", "intermediate-sym"],
        )
        assert edge.derived_from == ["s", "d", "intermediate-sym"]

    def test_to_dict_includes_derived_from(self) -> None:
        edge = Edge.create(
            src="s", dst="d", edge_type="calls", line=1,
            origin=self._ORIGIN, origin_run_id=self._RUN_ID,
            derived_from=["s", "d"],
        )
        d = edge.to_dict()
        assert d["derived_from"] == ["s", "d"]

    def test_to_dict_omits_derived_from_when_none(self) -> None:
        edge = Edge.create(
            src="s", dst="d", edge_type="calls", line=1,
            origin=self._ORIGIN, origin_run_id=self._RUN_ID,
        )
        d = edge.to_dict()
        assert "derived_from" not in d

    def test_from_dict_round_trip(self) -> None:
        edge = Edge.create(
            src="s", dst="d", edge_type="calls", line=1,
            origin=self._ORIGIN, origin_run_id=self._RUN_ID,
            derived_from=["s", "d", "extra"],
        )
        d = edge.to_dict()
        restored = Edge.from_dict(d)
        assert restored.derived_from == ["s", "d", "extra"]

    def test_from_dict_backward_compat_missing_key(self) -> None:
        """Legacy JSON without derived_from deserializes to None."""
        d = {
            "id": "e1", "src": "s", "dst": "d", "type": "calls",
            "line": 1, "origin": "p", "origin_run_id": "r",
            "meta": {},
        }
        edge = Edge.from_dict(d)
        assert edge.derived_from is None

    def test_from_dict_explicit_null(self) -> None:
        d = {
            "id": "e1", "src": "s", "dst": "d", "type": "calls",
            "line": 1, "origin": "p", "origin_run_id": "r",
            "derived_from": None, "meta": {},
        }
        edge = Edge.from_dict(d)
        assert edge.derived_from is None


# ---------------------------------------------------------------------------
# Source-tree scan — every linker Edge.create() must pass derived_from
# ---------------------------------------------------------------------------

_LINKERS_DIR = (
    Path(__file__).resolve().parents[1]
    / "src" / "hypergumbo_core" / "linkers"
)


def _find_edge_create_calls_missing_derived_from() -> list[str]:
    """Return 'file:line' for Edge.create() calls that lack derived_from=."""
    missing: list[str] = []
    for py_file in sorted(_LINKERS_DIR.glob("*.py")):
        if py_file.name.startswith("__"):
            continue
        source = py_file.read_text()
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_edge_create = (
                isinstance(func, ast.Attribute)
                and func.attr == "create"
                and isinstance(func.value, ast.Name)
                and func.value.id == "Edge"
            )
            if not is_edge_create:
                continue
            kwarg_names = {kw.arg for kw in node.keywords}
            if "derived_from" not in kwarg_names:
                missing.append(f"{py_file.name}:{node.lineno}")
    return missing


def test_all_linker_edge_creates_pass_derived_from() -> None:
    """Every Edge.create() call in a public linker must pass derived_from=."""
    missing = _find_edge_create_calls_missing_derived_from()
    assert missing == [], (
        f"{len(missing)} Edge.create() call(s) in linkers missing derived_from=:\n"
        + "\n".join(f"  {loc}" for loc in missing)
    )
