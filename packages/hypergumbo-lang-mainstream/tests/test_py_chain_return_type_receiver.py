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


class TestTheReturnTypeResolvesInTheCalleesNamespace:
    """WI-fihun: an annotation is a name in the DEFINING module's namespace.

    ``TrackerSet.workspace`` is declared ``-> Store`` in ``trackerset.py``,
    which IMPORTS ``Store`` from ``store.py``. ``_resolve_return_type_class``
    searched the caller's locals, the caller's imports and the callee's own
    file -- never the callee's imports -- so across modules the chain stopped
    and ``ts.workspace.add`` kept its line-proximity binding to
    ``TrackerSet.add``. Every fixture above was single-file, where the callee's
    own file happens to define the class, so none of them could see it.
    """

    @staticmethod
    def _project(root: Path, caller: str) -> list[Edge]:
        pkg = root / "pkg"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "store.py").write_text(
            "class Store:\n    def add(self, x):\n        return x\n"
        )
        (pkg / "trackerset.py").write_text(
            "from pkg.store import Store\n\n\n"
            "class TrackerSet:\n"
            "    @property\n"
            "    def workspace(self) -> Store:\n"
            "        return Store()\n\n"
            "    def add(self, y):\n"
            "        return y\n"
        )
        (root / "use.py").write_text(caller)
        return analyze_python(root).edges

    _CALLER = (
        "from pkg.trackerset import TrackerSet\n\n\n"
        "def go():\n"
        "    ts = TrackerSet()\n"
        "    ts.workspace.add(1)\n"
    )

    def test_an_imported_return_type_is_found(self, tmp_path: Path) -> None:
        edges = self._project(tmp_path / "r", self._CALLER)
        assert _hint(edges, "add") == "Store"

    def test_the_callees_meaning_wins_over_a_same_named_caller_class(
        self, tmp_path: Path,
    ) -> None:
        """The caller defines an unrelated ``Store``. Python binds the
        annotation in ``trackerset.py``, so the hint must name pkg's class --
        the id says which, since both are called ``Store``."""
        caller = self._CALLER + (
            "\n\nclass Store:\n    def add(self, z):\n        return z\n"
        )
        edges = self._project(tmp_path / "r", caller)
        meta = _unresolved_call(edges, "add").meta or {}
        assert meta.get("receiver_type_hint") == "Store"
        assert "pkg/store.py" in meta.get("receiver_type_id", "")
