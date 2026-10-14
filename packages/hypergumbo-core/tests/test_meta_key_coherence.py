# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the ``Edge.meta`` key gate (WI-lijaz).

The fixture tests pin each write shape the gate reads, and the two kinds of
answer it must not give: a key it did not see, and silence where it could not
look (``unresolved``). The live-tree tests pin what the gate cannot enumerate
on the shipped tree, by equality, and check that it reaches the item's named
instance -- ``make_unresolved_edge``'s keys -- before trusting its verdict.
The registry verdict itself is the ``Edge.meta key`` axis of
``test_producer_coherence.py::test_live_tree_producer_axis_ratchet``.
"""

from __future__ import annotations

import textwrap
from functools import lru_cache
from pathlib import Path

from hypergumbo_core.axis_meta_keys import AXIS_EDGE_META, find_meta_key
from hypergumbo_core.meta_key_coherence import (
    MetaKeyEmits,
    scan_meta_key_emits,
    unregistered_edge_meta_keys,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO = "packages/demo/src/demo.py"


def _scan(tmp_path: Path, source: str, rel: str = DEMO) -> MetaKeyEmits:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source))
    return scan_meta_key_emits(tmp_path)


class TestConstruction:
    def test_a_dict_display(self, tmp_path: Path) -> None:
        emits = _scan(tmp_path, 'Edge.create(src="a", meta={"k1": 1, "k2": 2})\n')
        assert emits.constructed == {"k1": [f"{DEMO}:1"], "k2": [f"{DEMO}:1"]}
        assert emits.unresolved == []

    def test_a_named_dict_and_its_stores(self, tmp_path: Path) -> None:
        emits = _scan(tmp_path, '''\
            def make(x):
                meta = {"a": 1}
                if x:
                    meta["b"] = x
                return Edge(src="s", meta=meta or None)
        ''')
        assert sorted(emits.constructed) == ["a", "b"]

    def test_two_constructions_share_one_name(self, tmp_path: Path) -> None:
        """``lua_ffi``: each use is the other's escape unless the constructor
        is declared a reader."""
        emits = _scan(tmp_path, '''\
            def make(x):
                meta = {"a": 1}
                Edge.create(meta=meta)
                meta = {"b": 1}
                Edge.create(meta=meta)
        ''')
        assert sorted(emits.constructed) == ["a", "b"]
        assert emits.unresolved == []

    def test_a_helper_reports_at_its_call_site_once(self, tmp_path: Path) -> None:
        emits = _scan(tmp_path, '''\
            def _mk(m):
                return Edge.create(src="s", meta=m)
            _mk({"h": 1})
        ''')
        assert emits.constructed == {"h": [f"{DEMO}:3"]}

    def test_a_copy_of_another_records_meta_is_unresolved(self, tmp_path: Path) -> None:
        """A symbol's meta copied onto an edge adds keys no edge writer declared."""
        emits = _scan(tmp_path, 'Edge.create(meta=dict(sym.meta))\n')
        assert emits.constructed == {}
        assert emits.unresolved == [(DEMO, "dict(sym.meta)")]

    def test_a_symbol_construction_is_not_scanned(self, tmp_path: Path) -> None:
        emits = _scan(tmp_path, 'Symbol(meta={"k": 1})\n')
        assert emits.constructed == {} and emits.written == {}


class TestPostHocWrites:
    def test_each_shape(self, tmp_path: Path) -> None:
        emits = _scan(tmp_path, '''\
            def f(edge, mode):
                edge.meta["sub"] = 1
                edge.meta = {**(edge.meta or {}), "whole": 1}
                edge.meta.update({"upd": 1})
                edge.meta.setdefault("sdef", [])
                write_meta_key(edge.meta, "wmk", mode)
                m = edge.meta
                m["alias_sub"] = 1
                m.update({"alias_upd": 1})
                c = dict(edge.meta or {})
                c["copy_sub"] = 1
                edge.meta = c
        ''')
        assert sorted(emits.written) == [
            "alias_sub", "alias_upd", "copy_sub", "sdef", "sub", "upd", "whole",
            "wmk",
        ]
        assert emits.constructed == {} and emits.unresolved == []

    def test_an_alias_in_another_function_is_not_one(self, tmp_path: Path) -> None:
        emits = _scan(tmp_path, '''\
            def f(edge):
                m = edge.meta
            def g(m):
                m["k"] = 1
        ''')
        assert emits.written == {}

    def test_a_key_it_cannot_list_is_unresolved(self, tmp_path: Path) -> None:
        emits = _scan(tmp_path, '''\
            def f(edge, other):
                for k, v in other.items():
                    edge.meta[k] = v
        ''')
        assert emits.written == {}
        assert emits.unresolved == [(DEMO, "k")]

    def test_shapes_that_write_nothing(self, tmp_path: Path) -> None:
        emits = _scan(tmp_path, '''\
            def f(edge, k):
                write_meta_key(edge.meta)
                edge.meta.update()
                edge.meta.get("k")
                other.update({"no": 1})
        ''')
        assert emits.written == {} and emits.unresolved == []


class TestScope:
    def test_tests_and_meta_free_files_are_skipped(self, tmp_path: Path) -> None:
        _scan(tmp_path, 'Edge.create(meta={"t": 1})\n', "packages/demo/tests/test_x.py")
        _scan(tmp_path, 'Edge.create(src="no keys here")\n', "packages/demo/src/plain.py")
        emits = _scan(tmp_path, 'x.meta["k"] = 1\n', "scripts/tool.py")
        assert emits.written == {"k": ["scripts/tool.py:1"]}
        assert emits.constructed == {}


class TestRegistryVerdict:
    def test_construction_needs_the_edge_axis_and_a_write_any_axis(
        self, tmp_path: Path,
    ) -> None:
        assert find_meta_key("callee_name").axis == AXIS_EDGE_META
        assert find_meta_key("framework_role").axis != AXIS_EDGE_META
        _scan(tmp_path, '''\
            Edge.create(meta={"callee_name": 1, "framework_role": 2, "zz_new": 3})
            def f(sym):
                sym.meta["framework_role"] = 1
                sym.meta["zz_late"] = 1
        ''')
        assert unregistered_edge_meta_keys(tmp_path) == {
            "framework_role": (f"{DEMO}:1",),
            "zz_new": (f"{DEMO}:1",),
            "zz_late": (f"{DEMO}:4",),
        }


@lru_cache(maxsize=1)
def _live() -> MetaKeyEmits:
    return scan_meta_key_emits(REPO_ROOT)


#: Writes on the shipped tree whose keys the gate cannot enumerate, each with
#: why. Pinned by equality: a new one must be read and added here, so the gate
#: cannot go silently blind (the WI-nakur pattern).
_UNENUMERABLE_META_WRITES: dict[tuple[str, str], str] = {
    ("packages/hypergumbo-core/src/hypergumbo_core/ir.py", "meta"): (
        "ir._absorb_call_site: the edge-dedup merge copies the kept edge's "
        "meta and passes it to _absorb_per_call_site_key, which writes the "
        "registry-DERIVED '<key>_values' companions (axis_meta_keys."
        "_per_call_site_values_specs); a call that may add keys is an escape."
    ),
}


class TestLiveTree:
    def test_it_reaches_the_items_named_instance(self) -> None:
        """WI-lijaz's title instance: make_unresolved_edge's two keys."""
        base = "packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py"
        for key in ("enclosing_class", "inherited_field_receiver"):
            assert any(s.startswith(base) for s in _live().constructed[key])

    def test_unenumerable_writes_are_exactly_the_acknowledged_ones(self) -> None:
        assert set(_live().unresolved) == set(_UNENUMERABLE_META_WRITES)
