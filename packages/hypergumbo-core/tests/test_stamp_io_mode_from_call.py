# SPDX-License-Identifier: AGPL-3.0-or-later
"""Core-side branch coverage for :func:`analyze.base.stamp_io_mode_from_call`.

WHY THIS FILE EXISTS AND WHAT IT DELIBERATELY DOES NOT PROVE. The helper is the
tree-sitter half of INV-kaduh's producer: it reads a call's mode literal and
records it on the edges that call just emitted. It lives in **core**, beside
``mode_argument_for`` and the catalogue that decides which primitives need a
mode, because a private copy inside one analyzer is the arrangement INV-kaduh
was filed against — py.py had one, c.py had none, and no test could see the gap
because neither file mentioned the other.

Living in core means core must cover it in ISOLATION. ``check-package-coverage``
caught exactly that: every branch here was exercised, but only from
``hypergumbo-lang-mainstream``'s C/C++ tests, so core alone reported 13
uncovered lines. That is the cross-package gap the check exists for and it is
worth stating plainly rather than fixing silently.

THE STUB IS A PACKAGE-BOUNDARY CONSTRAINT, NOT A SHORTCUT. Core depends on
``tree-sitter`` but on **no grammar** — grammars belong to the language
packages — so a core test cannot parse C. The node protocol this function
consumes is four attributes wide (``child_by_field_name``, ``children``,
``is_named``, ``type``, plus byte offsets for ``node_text``), so it is stubbed
here exactly.

WHAT THAT COSTS, SO NOBODY LATER DELETES THE OTHER HALF: a stub proves the
BRANCHES, never that real grammar output has the shape assumed. The claim that
tree-sitter-c really yields ``argument_list`` → ``string_literal`` →
``string_content`` — including through a wide-string prefix like ``L"rb"`` — is
proved ONLY by ``hypergumbo-lang-mainstream``'s ``test_c_io_mode_emission.py``,
against real parses and a real catalogue lookup. Both halves are load-bearing.
Unit-green is not "the pipeline works"; this repo has paid for that twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from hypergumbo_core.analyze.base import stamp_io_mode_from_call
from hypergumbo_core.ir import Edge

SOURCE = b'fopen(path, "w")'


@dataclass
class _Node:
    """The four-attribute slice of the tree-sitter Node protocol used here."""

    type: str = "node"
    is_named: bool = True
    start_byte: int = 0
    end_byte: int = 0
    children: list["_Node"] = field(default_factory=list)
    fields: dict[str, "_Node"] = field(default_factory=dict)

    def child_by_field_name(self, name: str) -> Optional["_Node"]:
        return self.fields.get(name)


def _text_node(kind: str, text: bytes, source: bytes) -> _Node:
    start = source.index(text)
    return _Node(type=kind, start_byte=start, end_byte=start + len(text))


def _string_literal(value: bytes, source: bytes) -> _Node:
    """``"w"`` — a literal wrapping a ``string_content`` child, as C parses."""
    return _Node(
        type="string_literal",
        children=[_text_node("string_content", value, source)],
    )


def _call(
    callee: bytes = b"fopen",
    args: Optional[list[_Node]] = None,
    *,
    source: bytes = SOURCE,
    with_arguments_field: bool = True,
) -> _Node:
    fields: dict[str, _Node] = {
        "function": _text_node("identifier", callee, source),
    }
    if with_arguments_field:
        fields["arguments"] = _Node(type="argument_list", children=args or [])
    return _Node(type="call_expression", fields=fields)


def _edge() -> Edge:
    return Edge.create(
        src="c:a.c:1-3:f:function",
        dst="c:external:0-0:fopen:unresolved",
        edge_type="calls",
        line=1,
        origin="test",
        origin_run_id="run",
        evidence_type="ast_call_direct",
    )


def _stamp(node: _Node, language: str = "c", source: bytes = SOURCE) -> Edge:
    edges = [_edge()]
    stamp_io_mode_from_call(edges, 0, node, source, language)  # type: ignore[arg-type]
    return edges[0]


class TestTheModeIsRecorded:
    def test_a_string_literal_at_the_declared_position_is_stamped(self) -> None:
        node = _call(args=[
            _text_node("identifier", b"path", SOURCE),
            _string_literal(b"w", SOURCE),
        ])
        assert (_stamp(node).meta or {}).get("io_mode") == "w"

    def test_the_edge_gains_a_meta_dict_when_it_had_none(self) -> None:
        """Edges are routinely constructed with ``meta=None``."""
        edges = [_edge()]
        edges[0].meta = None
        node = _call(args=[
            _text_node("identifier", b"path", SOURCE),
            _string_literal(b"w", SOURCE),
        ])
        stamp_io_mode_from_call(edges, 0, node, SOURCE, "c")  # type: ignore[arg-type]
        assert (edges[0].meta or {}).get("io_mode") == "w"

    def test_only_edges_from_this_call_are_touched(self) -> None:
        """``first_new`` is the whole point — an earlier call's edges are its own."""
        earlier, mine = _edge(), _edge()
        edges = [earlier, mine]
        node = _call(args=[
            _text_node("identifier", b"path", SOURCE),
            _string_literal(b"w", SOURCE),
        ])
        stamp_io_mode_from_call(edges, 1, node, SOURCE, "c")  # type: ignore[arg-type]
        assert (earlier.meta or {}).get("io_mode") is None
        assert (mine.meta or {}).get("io_mode") == "w"


class TestAbsenceIsRecordedAsAbsence:
    """Every one of these must stamp NOTHING.

    ``resolve_mode_boundary`` applies the language default for a missing mode.
    Inventing ``"w"`` on suspicion in any of these branches would rebuild the
    false-positive population the mechanism exists to remove.
    """

    def test_a_callee_with_no_mode_argument_declared(self) -> None:
        node = _call(callee=b"fopen", args=[])
        node.fields["function"] = _text_node("identifier", b"path", SOURCE)
        assert (_stamp(node).meta or {}).get("io_mode") is None

    def test_a_language_that_declares_no_table(self) -> None:
        node = _call(args=[
            _text_node("identifier", b"path", SOURCE),
            _string_literal(b"w", SOURCE),
        ])
        assert (_stamp(node, language="go").meta or {}).get("io_mode") is None

    def test_a_call_with_no_arguments_field_at_all(self) -> None:
        assert (_stamp(_call(with_arguments_field=False)).meta or {}).get(
            "io_mode",
        ) is None

    def test_too_few_arguments_to_reach_the_declared_position(self) -> None:
        node = _call(args=[_text_node("identifier", b"path", SOURCE)])
        assert (_stamp(node).meta or {}).get("io_mode") is None

    def test_a_computed_mode_rather_than_a_literal(self) -> None:
        """``fopen(p, m)`` — ignorance, and ignorance licenses nothing."""
        node = _call(args=[
            _text_node("identifier", b"path", SOURCE),
            _text_node("identifier", b"path", SOURCE),
        ])
        assert (_stamp(node).meta or {}).get("io_mode") is None

    def test_unnamed_children_do_not_shift_the_argument_index(self) -> None:
        """``(`` / ``,`` / ``)`` are anonymous and must not be counted.

        Counting them would put the mode at a different index and stamp the
        PATH as the mode — a wrong value, which is worse than none.
        """
        punct = _Node(type=",", is_named=False)
        node = _call(args=[
            punct,
            _text_node("identifier", b"path", SOURCE),
            punct,
            _string_literal(b"w", SOURCE),
        ])
        assert (_stamp(node).meta or {}).get("io_mode") == "w"
