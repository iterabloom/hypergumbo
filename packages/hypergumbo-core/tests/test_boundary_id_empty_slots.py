# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/ir.py
"""A boundary node's id never has an empty path slot or an empty name slot.

``ir._canonical_external_id`` mints the id of every ``external_symbol`` from
the dedupe key ``(language, path, name)`` that ``create_boundary_nodes`` reads
off a dangling edge endpoint. Nothing stopped an empty slot: an endpoint such
as ``javascript::0-0:x:unresolved`` (or an ``ExternalRef`` whose module path is
empty, the abstaining ref ``ir.stated_module_of`` documents) minted
``javascript::0-0:x:external_symbol``, and a nameless endpoint minted
``javascript:m:0-0::external_symbol``. ``spec_validator`` rejects both, but only
after the run.

THE PATH SLOT IS REPAIRED, THE NAME SLOT IS REFUSED. An empty path is a
legitimate input -- a reference that states no module -- and ADR-0036 Ruling 3
rules the sentinel for it at this very factory: ``<external>``, "external
pseudo-symbol with no file anchor". An empty name is never legitimate (it names
nothing) and Ruling 3 rules no name sentinel, so the factory refuses it, and
its one caller hands it the endpoint as the producer wrote it when every name it
holds is empty. Neither repair changes the stable id: that is hashed from the
dedupe key, which keeps the slot as it was read.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.analyze.base import make_unresolved_edge
from hypergumbo_core.ir import (
    Edge,
    ExternalRef,
    _canonical_external_id,
    _canonical_external_stable_id,
    create_boundary_nodes,
)
from hypergumbo_core.spec_validator import _check_id_format, _check_id_roundtrip

SRC = "javascript:src/a.js:1-3:caller:function"


def _edge(dst: str, **kw: object) -> Edge:
    return Edge.create(
        src=SRC, dst=dst, edge_type="calls", line=2, origin="p", origin_run_id="r",
        is_resolved=False, **kw,  # type: ignore[arg-type]
    )


def _boundary(edges: list[Edge]) -> dict[str, object]:
    nodes, _remap = create_boundary_nodes([], edges)
    return {n.id: n for n in nodes}


def _id_format_errors(nodes: list[object]) -> list[str]:
    return [v.message for v in _check_id_format(nodes) + _check_id_roundtrip(nodes)
            if v.severity == "error"]


class TestTheFactory:
    def test_an_empty_path_slot_becomes_the_no_anchor_sentinel(self) -> None:
        assert _canonical_external_id("javascript", "", "x") == (
            "javascript:<external>:0-0:x:external_symbol"
        )

    def test_a_path_is_kept(self) -> None:
        assert _canonical_external_id("javascript", "lodash", "x") == (
            "javascript:lodash:0-0:x:external_symbol"
        )

    def test_an_empty_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="name"):
            _canonical_external_id("javascript", "lodash", "")


class TestTheBoundaryNodes:
    def test_a_ref_with_no_module_mints_the_sentinel_path(self) -> None:
        edge = make_unresolved_edge(
            "javascript", SRC, "x", 2, "p", "r",
            module_hint="m", dst_ref=ExternalRef(lang="javascript", module_path="", name="x"),
        )
        nodes = _boundary([edge])
        node = nodes["javascript:<external>:0-0:x:external_symbol"]
        assert node.name == "x"  # type: ignore[attr-defined]
        # The stable id is the key's, as it was before the repair (no move).
        assert node.stable_id == _canonical_external_stable_id(  # type: ignore[attr-defined]
            "javascript", "", "x")
        assert _id_format_errors(list(nodes.values())) == []

    def test_an_empty_path_slot_in_the_endpoint_mints_the_sentinel_path(self) -> None:
        nodes = _boundary([_edge("javascript::0-0:y:unresolved")])
        assert "javascript:<external>:0-0:y:external_symbol" in nodes
        assert _id_format_errors(list(nodes.values())) == []

    def test_a_module_is_kept(self) -> None:
        """Control: a stated module stays the path slot."""
        edge = make_unresolved_edge("javascript", SRC, "x", 2, "p", "r", module_hint="m")
        nodes = _boundary([edge])
        assert "javascript:m:0-0:x:external_symbol" in nodes

    def test_an_empty_and_a_sentinel_path_are_one_node(self) -> None:
        """Both now spell ``<external>``: one id, one node, and that node keeps
        the stable id of the group that already spelled it."""
        nodes, remap = create_boundary_nodes([], [
            _edge("javascript::0-0:z:unresolved"),
            _edge("javascript:<external>:0-0:z:unresolved"),
        ])
        same = [n for n in nodes if n.id == "javascript:<external>:0-0:z:external_symbol"]
        assert len(same) == 1
        assert same[0].stable_id == _canonical_external_stable_id(
            "javascript", "<external>", "z")
        assert remap["javascript::0-0:z:unresolved"] == same[0].id
        assert remap["javascript:<external>:0-0:z:unresolved"] == same[0].id

    def test_a_nameless_endpoint_is_named_by_what_was_written(self) -> None:
        nodes = _boundary([_edge("javascript:m:0-0::unresolved")])
        (node,) = [n for n in nodes.values() if n.id.startswith("javascript:m:")]  # type: ignore[attr-defined]
        assert node.name == "javascript:m:0-0::unresolved"  # type: ignore[attr-defined]
        assert node.stable_id == _canonical_external_stable_id(  # type: ignore[attr-defined]
            "javascript", "m", "")
        assert _id_format_errors(list(nodes.values())) == []

    def test_a_ref_without_a_name_falls_back_to_the_written_name_slot(self) -> None:
        edge = _edge(
            "javascript:m:0-0:w:unresolved",
            dst_ref=ExternalRef(lang="javascript", module_path="m", name=""),
        )
        nodes = _boundary([edge])
        assert nodes["javascript:m:0-0:w:external_symbol"].name == "w"  # type: ignore[attr-defined]

    def test_a_nameless_selector_keeps_its_lossless_name(self) -> None:
        """Control: an objc selector's name slot parses empty and its lossless
        ``meta.callee_name`` names it, as before."""
        edge = make_unresolved_edge("objc", SRC, "writeToFile:atomically:", 2, "p", "r")
        nodes = _boundary([edge])
        assert "objc:external:0-0:writeToFile.atomically.:external_symbol" in nodes

    def test_an_empty_endpoint_mints_no_node(self) -> None:
        nodes = _boundary([_edge("")])
        assert all(not i.startswith(":") for i in nodes)  # type: ignore[union-attr]
        assert _id_format_errors(list(nodes.values())) == []
