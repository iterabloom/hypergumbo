# SPDX-License-Identifier: AGPL-3.0-or-later
"""A disjunctive module slot is a short handle, expanded where it is read (INV-jahuf).

envoy-half carried 2.07 GB of repeated #include lists in 151,591 edges'
module slots, against 14 MB of distinct content. The slot now holds a
content-addressed handle, and the list is stored once. The two expansion
points must see exactly what they saw before, so every verdict is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core import module_sets
from hypergumbo_core.finalize import _finalize_commit_dicts
from hypergumbo_core.io_boundary import (
    _module_hint_candidates,
    module_hint_disjuncts,
    slot_names_one_owner,
)
from hypergumbo_core.ir import Edge, ExternalRef
from hypergumbo_core.limits import Limits
from hypergumbo_core.pass_metadata import PassMetadataLookup
from hypergumbo_core.schema import new_behavior_map
from hypergumbo_core.survey_io import load_behavior_map

_HEADERS = ["cstdint", "util.h", "cstdio"] + [f"proj/h{i}.h" for i in range(300)]


def test_one_disjunct_is_the_slot_itself() -> None:
    assert module_sets.module_slot(["cstdio"]) == "cstdio"


def test_many_disjuncts_are_a_bounded_deterministic_handle() -> None:
    handle = module_sets.module_slot(_HEADERS)
    assert module_sets.is_module_set_handle(handle)
    assert len(handle) == len(module_sets.MODULE_SET_PREFIX) + 16
    assert module_sets.module_slot(list(_HEADERS)) == handle
    assert module_sets.module_slot(_HEADERS[::-1]) != handle


def test_a_handle_expands_to_the_list_it_names() -> None:
    handle = module_sets.module_slot(_HEADERS)
    assert module_sets.expand_module_slot(handle) == ",".join(_HEADERS)
    assert module_sets.expand_module_slot("cstdio") == "cstdio"
    assert module_sets.expand_module_slot("a,b") == "a,b"


@pytest.mark.parametrize("slot", [
    "module-set-", "module-set-0123", "module-set-0123456789abcdeg",
    "module-set-0123456789ABCDEF", "xmodule-set-0123456789abcdef",
])
def test_only_the_exact_spelling_is_a_handle(slot: str) -> None:
    assert not module_sets.is_module_set_handle(slot)


def test_both_expansion_points_read_a_handle_as_its_list() -> None:
    """Classification (ANY) and the coverage gate (ALL) see the same slot as
    the comma-joined form gave them, so no verdict can move."""
    joined = ",".join(_HEADERS)
    handle = module_sets.module_slot(_HEADERS)
    assert module_hint_disjuncts(handle, "cpp") == module_hint_disjuncts(joined, "cpp")
    assert _module_hint_candidates(handle, "cpp") == _module_hint_candidates(joined, "cpp")
    pair = module_sets.module_slot(["wait.h", "sys/wait.h"])
    assert slot_names_one_owner(pair) is slot_names_one_owner("wait.h,sys/wait.h") is True


def test_an_unknown_handle_fails_closed_and_warns_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No table entry: one disjunct nothing enumerates, so the coverage gate
    withholds. Never an empty slot, which would read as nothing to check."""
    stranger = module_sets.MODULE_SET_PREFIX + "f" * 16
    assert module_hint_disjuncts(stranger, "cpp") == [[stranger]]
    module_sets.expand_module_slot(stranger)
    assert capsys.readouterr().err.count(stranger) == 1


def test_a_registered_table_restores_expansion() -> None:
    disjuncts = ["sys/socket.h", "netdb.h", "only-in-this-test.h"]
    joined = ",".join(disjuncts)
    handle = module_sets._handle_for(joined)
    assert module_sets.expand_module_slot(handle) == handle
    module_sets.register_module_sets({handle: disjuncts})
    assert module_sets.expand_module_slot(handle) == joined


def test_a_table_entry_that_does_not_hash_to_its_list_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    forged = module_sets._handle_for("forged-a.h,forged-b.h")
    module_sets.register_module_sets({forged: ["cstdio", "stdlib.h"]})
    assert module_sets.expand_module_slot(forged) == forged
    assert "does not hash" in capsys.readouterr().err


def test_the_table_names_each_registered_handle_once_in_handle_order() -> None:
    a = module_sets.module_slot(["x1.h", "x2.h"])
    b = module_sets.module_slot(["y1.h", "y2.h"])
    unknown = module_sets.MODULE_SET_PREFIX + "e" * 16
    table = module_sets.module_sets_for([b, "cstdio", a, b, unknown])
    assert table == dict(sorted({a: ["x1.h", "x2.h"], b: ["y1.h", "y2.h"]}.items()))
    assert list(table) == sorted(table)


def _unresolved(slot: str) -> Edge:
    ref = ExternalRef(lang="cpp", module_path=slot, name="fopen")
    return Edge.create(
        src="cpp:main.cpp:1-2:save:function", dst=f"cpp:{slot}:0-0:fopen:unresolved",
        edge_type="calls", line=1, origin="cpp-v1", origin_run_id="uuid:t", dst_ref=ref,
    )


def test_finalize_writes_the_table_the_edges_reference(tmp_path: Path) -> None:
    handle = module_sets.module_slot(_HEADERS)
    bm = new_behavior_map()
    assert bm["module_sets"] == {}
    from hypergumbo_core.finalize import FinalizeContext

    ctx = FinalizeContext(
        symbols=[], edges=[_unresolved(handle), _unresolved("cstdio")],
        usage_contexts=[], analysis_runs=[], behavior_map=bm, limits=Limits(),
        repo_root=tmp_path, pass_metadata=PassMetadataLookup({}),
    )
    _finalize_commit_dicts(ctx)
    assert bm["module_sets"] == {handle: _HEADERS}


def test_loading_a_behavior_map_registers_its_table(tmp_path: Path) -> None:
    """The verify-claims hand-off re-reads the survey in the same process, but
    a cached survey or ``--input`` is read by a process that never ran the
    analyzer."""
    disjuncts = ["only-in-the-file.h", "cstdio"]
    joined = ",".join(disjuncts)
    handle = module_sets._handle_for(joined)
    path = tmp_path / "survey.json"
    path.write_text(json.dumps({"nodes": [], "module_sets": {handle: disjuncts}}))
    assert module_sets.expand_module_slot(handle) == handle
    load_behavior_map(path)
    assert module_sets.expand_module_slot(handle) == joined


def test_loading_a_non_map_document_is_unaffected(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1, 2]")
    assert load_behavior_map(path) == [1, 2]
