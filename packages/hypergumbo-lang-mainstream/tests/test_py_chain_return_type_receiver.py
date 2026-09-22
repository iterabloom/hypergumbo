# SPDX-License-Identifier: AGPL-3.0-or-later
"""``obj.member.method()`` takes its receiver type from ``member``'s DECLARED
RETURN TYPE when that type resolves to a project class (WI-fikoh).

WI-fihun blocker 2. py.py's receiver-hint chain is entered only for a bare
``ast.Name`` receiver (py.py:8093), so a two-hop ``Name.attr.method()`` never
reaches it and falls through to the INV-mumov ``external`` placeholder with no
hint at all. The method-call-recovery linker then has nothing to filter on and
picks by line proximity -- which is how ``ts.workspace.add`` binds to
``TrackerSet.add`` instead of ``Store.add``.

BOTH HALVES ALREADY EXISTED and are merely joined here: ``var_types`` types the
root from a direct constructor, and the member carries ``meta["return_type"]``
(WI-ribak). ``_declared_return_type_name`` reads it -- including the QUOTED
forward-reference spelling, which WI-fihun taught it -- and
``_resolve_return_type_class`` resolves it to a class Symbol. Neither is
re-derived here.

THE NARROWNESS IS INHERITED, DELIBERATELY. Only a simple identifier infers, so
``-> Optional[Store]`` and ``-> list[Store]`` do NOT stamp a hint; that is
``_declared_return_type_name``'s documented policy and widening it is a separate,
measurable change. A return type that names no project class stamps nothing.

WHY THE NEGATIVE CONTROLS CARRY THE WEIGHT. A minted hint is TRUSTED downstream
-- test_py_annotated_receiver records that it bypasses both gate_named_entry and
the ambiguous_names net by design -- so a hint stamped on a receiver whose type
was NOT established converts an honestly-unresolved edge into a confidently wrong
one. Every negative case below therefore asserts the ABSENCE of the key, not a
different value.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import analyze_python


def _edges(root: Path, src: str) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod.py").write_text(src)
    return analyze_python(root).edges


def _unresolved_call(edges: list[Edge], method: str) -> Edge:
    """The single unresolved ``calls`` edge naming *method*."""
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.dst.endswith(f":{method}:unresolved")
    ]
    assert len(hits) == 1, [e.dst for e in edges if method in e.dst]
    return hits[0]


def _hint(edges: list[Edge], method: str) -> str | None:
    return (_unresolved_call(edges, method).meta or {}).get("receiver_type_hint")


_QUOTED = '''
class Store:
    def add(self, x):
        return x


class TrackerSet:
    @property
    def workspace(self) -> "Store":
        return Store()

    def add(self, y):
        return y


def go():
    ts = TrackerSet()
    ts.workspace.add(1)
'''

_UNQUOTED = _QUOTED.replace('-> "Store"', "-> Store")


class TestTheChainTakesTheReturnType:
    def test_quoted_forward_reference(self, tmp_path: Path) -> None:
        assert _hint(_edges(tmp_path / "q", _QUOTED), "add") == "Store"

    def test_unquoted_annotation(self, tmp_path: Path) -> None:
        assert _hint(_edges(tmp_path / "u", _UNQUOTED), "add") == "Store"

    def test_the_intermediate_hop_keeps_its_own_hint(self, tmp_path: Path) -> None:
        """``ts.workspace`` itself is still typed from ``ts`` -- unchanged."""
        assert _hint(_edges(tmp_path / "i", _QUOTED), "workspace") == "TrackerSet"


class TestNothingIsMintedWithoutEvidence:
    def test_member_with_no_return_annotation(self, tmp_path: Path) -> None:
        src = _QUOTED.replace('-> "Store"', "")
        e = _unresolved_call(_edges(tmp_path / "n", src), "add")
        assert "receiver_type_hint" not in (e.meta or {})

    def test_return_type_that_is_not_a_project_class(self, tmp_path: Path) -> None:
        src = _QUOTED.replace('-> "Store"', "-> int")
        e = _unresolved_call(_edges(tmp_path / "b", src), "add")
        assert "receiver_type_hint" not in (e.meta or {})

    def test_subscripted_return_type_stays_narrow(self, tmp_path: Path) -> None:
        """``_declared_return_type_name``'s policy: only a simple identifier."""
        src = _QUOTED.replace('-> "Store"', "-> list[Store]")
        e = _unresolved_call(_edges(tmp_path / "s", src), "add")
        assert "receiver_type_hint" not in (e.meta or {})

    def test_untyped_root_stamps_nothing(self, tmp_path: Path) -> None:
        """No constructor, so ``ts`` has no type and the chain has no root."""
        src = _QUOTED.replace("ts = TrackerSet()", "pass").replace(
            "def go():", "def go(ts):"
        )
        e = _unresolved_call(_edges(tmp_path / "r", src), "add")
        assert "receiver_type_hint" not in (e.meta or {})
