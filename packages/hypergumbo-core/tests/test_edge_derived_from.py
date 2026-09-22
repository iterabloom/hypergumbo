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
# Source-tree scan — every linker Edge.create() states what it consumed
# ---------------------------------------------------------------------------
#
# INV-rukor was closed on a scan whose whole predicate was ``"derived_from" in
# kwarg_names``: it asserted the KEYWORD IS PRESENT, so it could not tell
# ``[src, dst]`` from a list naming a genuinely consumed input, and 86 of 93
# sites restated their own endpoints under it. The scan below reads the VALUE.
#
# The field names the INPUT records (Symbol / Edge ids the linker read from its
# context) whose presence decided the edge. Ids the linker MINTS are not
# consumed and are left out (inheritance.py's external placeholder set the
# precedent). ``[]`` is a positive statement — "consumed no graph record" —
# distinct from ``None``, which analyzer edges carry.
#
# A value that restates only the endpoints (or is empty) can be HONEST — a
# containment edge really is decided by its parent and child alone — so it is
# not refused. It must be DECLARED, inside the call, with a reason:
#
#     # derived-from endpoints: <why the endpoints are the whole derivation>
#     # derived-from consumed-none: <why no graph record was read>
#     # derived-from incomplete: <what is consumed but not yet named>
#
# Silence plus a restating value is the offence, because silence is exactly
# what the 86 vacuous sites looked like. A declaration on a site whose value
# already names more than its endpoints is stale and also refused. This is a
# SYNTACTIC check of a SEMANTIC claim: it cannot verify a declaration is true,
# only that one was made where the value alone says nothing.

_LINKERS_DIR = (
    Path(__file__).resolve().parents[1]
    / "src" / "hypergumbo_core" / "linkers"
)

_MARKER_RE = re.compile(
    r"#\s*derived-from\s+(endpoints|consumed-none|incomplete)\s*:\s*(\S.*)$"
)
_ANY_MARKER_RE = re.compile(r"#\s*derived-from\b")


def _is_edge_create(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "Edge"
    )


def _restates_endpoints(call: ast.Call, value: ast.expr) -> bool:
    """True when ``value`` is a list literal of only the call's src/dst exprs."""
    if not isinstance(value, ast.List):
        return False
    kw = {k.arg: k.value for k in call.keywords}
    endpoints = {ast.unparse(kw[k]) for k in ("src", "dst") if k in kw}
    return all(
        not isinstance(elt, ast.Starred) and ast.unparse(elt) in endpoints
        for elt in value.elts
    )


def _derived_from_violations(source: str, filename: str) -> list[str]:
    """Return 'file:line: problem' for each Edge.create() that fails the rule."""
    lines = source.splitlines()
    problems: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not _is_edge_create(node):
            continue
        assert isinstance(node, ast.Call)
        where = f"{filename}:{node.lineno}"
        value = next(
            (k.value for k in node.keywords if k.arg == "derived_from"), None,
        )
        if value is None:
            problems.append(f"{where}: no derived_from=")
            continue
        if isinstance(value, ast.Constant) and value.value is None:
            problems.append(f"{where}: derived_from=None from a linker")
            continue
        span = lines[node.lineno - 1:(node.end_lineno or node.lineno)]
        markers = [m for m in (_MARKER_RE.search(t) for t in span) if m]
        malformed = [t for t in span if _ANY_MARKER_RE.search(t)
                     and not _MARKER_RE.search(t)]
        if malformed:
            problems.append(f"{where}: malformed declaration {malformed[0].strip()!r}")
            continue
        restating = _restates_endpoints(node, value)
        if not restating:
            if markers:
                problems.append(
                    f"{where}: declaration on a value that already names more "
                    "than its endpoints (stale)"
                )
            continue
        if not markers:
            problems.append(
                f"{where}: derived_from restates only the endpoints "
                f"({ast.unparse(value)}) with no declaration"
            )
            continue
        kind = markers[0].group(1)
        assert isinstance(value, ast.List)
        if kind == "consumed-none" and value.elts:
            problems.append(f"{where}: consumed-none declared on a non-empty value")
        elif kind == "endpoints" and not value.elts:
            problems.append(f"{where}: endpoints declared on an empty value")
    return problems


def _live_tree_violations() -> list[str]:
    problems: list[str] = []
    for py_file in sorted(_LINKERS_DIR.glob("*.py")):
        if py_file.name.startswith("__"):
            continue
        problems.extend(_derived_from_violations(py_file.read_text(), py_file.name))
    return problems


def test_every_linker_edge_create_states_its_consumed_inputs() -> None:
    """INV-rukor: no linker Edge.create restates its endpoints silently."""
    problems = _live_tree_violations()
    assert problems == [], (
        f"{len(problems)} linker Edge.create() site(s) break the "
        "derived_from rule:\n" + "\n".join(f"  {p}" for p in problems)
    )


class TestDerivedFromScanner:
    """The scanner itself — each branch driven by a synthetic source."""

    @staticmethod
    def _scan(body: str) -> list[str]:
        return _derived_from_violations(body, "x.py")

    def test_missing_kwarg(self) -> None:
        assert self._scan("Edge.create(src=a, dst=b)") == ["x.py:1: no derived_from="]

    def test_none_value(self) -> None:
        (p,) = self._scan("Edge.create(src=a, dst=b, derived_from=None)")
        assert "derived_from=None" in p

    def test_undeclared_endpoints_restatement(self) -> None:
        (p,) = self._scan("Edge.create(src=a.id, dst=b.id, derived_from=[a.id, b.id])")
        assert "no declaration" in p

    def test_subset_and_empty_restatements_are_caught(self) -> None:
        assert self._scan("Edge.create(src=a, dst=b, derived_from=[a])")
        assert self._scan("Edge.create(src=a, dst=b, derived_from=[])")

    def test_declared_endpoints_passes(self) -> None:
        body = (
            "Edge.create(\n"
            "    src=a, dst=b,\n"
            "    # derived-from endpoints: span nesting of a and b decides it\n"
            "    derived_from=[a, b],\n"
            ")"
        )
        assert self._scan(body) == []

    def test_declared_consumed_none_passes_only_on_empty(self) -> None:
        ok = "Edge.create(src=a, dst=b,  # derived-from consumed-none: file scan\n derived_from=[])"
        bad = "Edge.create(src=a, dst=b,  # derived-from consumed-none: file scan\n derived_from=[a])"
        assert self._scan(ok) == []
        (p,) = self._scan(bad)
        assert "consumed-none declared on a non-empty" in p

    def test_endpoints_declared_on_empty_is_refused(self) -> None:
        body = "Edge.create(src=a, dst=b,  # derived-from endpoints: x\n derived_from=[])"
        (p,) = self._scan(body)
        assert "endpoints declared on an empty" in p

    def test_incomplete_declaration_passes(self) -> None:
        body = "Edge.create(src=a, dst=b,  # derived-from incomplete: walk ids dropped\n derived_from=[a, b])"
        assert self._scan(body) == []

    def test_richer_value_needs_no_declaration(self) -> None:
        assert self._scan("Edge.create(src=e.src, dst=t.id, derived_from=[e.src, t.id, e.id])") == []
        assert self._scan("Edge.create(src=a, dst=b, derived_from=[a, b, *path])") == []
        assert self._scan("Edge.create(src=a, dst=b, derived_from=ids)") == []

    def test_stale_declaration_on_richer_value(self) -> None:
        body = "Edge.create(src=a, dst=b,  # derived-from endpoints: x\n derived_from=[a, b, c])"
        (p,) = self._scan(body)
        assert "stale" in p

    def test_malformed_declaration(self) -> None:
        body = "Edge.create(src=a, dst=b,  # derived-from: endpoints\n derived_from=[a, b])"
        (p,) = self._scan(body)
        assert "malformed" in p

    def test_declaration_outside_the_call_does_not_count(self) -> None:
        body = "# derived-from endpoints: x\nEdge.create(src=a, dst=b, derived_from=[a, b])"
        (p,) = self._scan(body)
        assert "no declaration" in p

    def test_non_edge_create_calls_ignored(self) -> None:
        assert self._scan("Edge.make(src=a)\nfoo.create(src=a)\nEdge.create") == []
