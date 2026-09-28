# SPDX-License-Identifier: AGPL-3.0-or-later
"""A collapsed site that carries NO value is recorded, not dropped (INV-rajak).

``ir._absorb_per_call_site_key`` already treated a site that omits a per-site
key as a disagreement and removed the singular key. But ``<key>_values`` then
listed only the values sites HAD, so the unstamped site vanished between the
two: ``io_target_kind_values: ['in_memory']`` on an edge whose other site was a
parameter scanner read as "every site crosses nothing", and the real receive
and its taint source were gone. The mode seam lost reads the same way:
``open(q, 'w')`` beside ``json.load(open(p))`` left ``io_mode_values: ['w']``
and a ``must_not_exist: fs_read`` claim CONFIRMED.

The fix makes absence a value: ``None`` (JSON ``null``) in ``<key>_values``,
and every reader gives that site the meaning a single unstamped call already
has -- an abstention, never agreement with a stamped sibling.
"""

from __future__ import annotations

from hypergumbo_core.io_boundary import (
    call_site_modes,
    call_site_target_kinds,
    load_catalog,
    mode_spanned_boundaries,
    resolve_mode_boundary_across_sites,
    resolve_target_kind_across_sites,
    target_kinds_cross_no_boundary,
)
from hypergumbo_core.ir import (
    _CALL_LINES_CAP,
    Edge,
    _absorb_per_call_site_key,
    deduplicate_edges,
)
from hypergumbo_core.taint import (
    _name_flow_indexes,
    _sink_call_can_carry_taint,
    _source_call_can_mint_taint,
)


def _edge(line: int, **meta: object) -> Edge:
    return Edge.create(
        src="go:f.go:1-9:F:function",
        dst="go:bufio:0-0:NewScanner:external_symbol",
        edge_type="calls",
        line=line,
        origin="go",
        origin_run_id="run-inv-rajak",
        evidence_type="ast_call",
        meta=dict(meta),
    )


def _collapse(*edges: Edge) -> dict:
    (kept,) = deduplicate_edges(list(edges))
    return kept.meta


# ---------------------------------------------------------------------------
# The collapse records the unstamped site
# ---------------------------------------------------------------------------


def test_an_unstamped_site_is_a_null_member_in_either_order():
    for meta in (
        _collapse(_edge(3, io_target_kind="in_memory"), _edge(4)),
        _collapse(_edge(3), _edge(4, io_target_kind="in_memory")),
    ):
        assert "io_target_kind" not in meta
        assert meta["io_target_kind_values"] == [None, "in_memory"]


def test_a_third_unstamped_site_joins_an_existing_values_list_once():
    meta = _collapse(
        _edge(3, io_target_kind="in_memory"),
        _edge(4, io_target_kind="null_device"),
        _edge(5),
        _edge(6),
    )
    assert meta["io_target_kind_values"] == [None, "in_memory", "null_device"]


def test_sites_that_all_omit_the_key_add_nothing():
    meta = _collapse(_edge(3), _edge(4))
    assert "io_target_kind_values" not in meta


def test_agreeing_sites_still_keep_the_singular():
    meta = _collapse(_edge(3, io_mode="w"), _edge(4, io_mode="w"))
    assert meta["io_mode"] == "w"
    assert "io_mode_values" not in meta


def test_the_cap_never_drops_the_unstamped_member():
    """A truncation may cost enumeration, never the warning."""
    edges = [_edge(i, redirect_target=f"/t/{i:03d}") for i in range(_CALL_LINES_CAP + 5)]
    edges.append(_edge(999))
    meta = _collapse(*edges)
    values = meta["redirect_target_values"]
    assert len(values) == _CALL_LINES_CAP
    assert values[0] is None


# ---------------------------------------------------------------------------
# The readers surface it
# ---------------------------------------------------------------------------


def test_the_readers_return_the_unstamped_site():
    meta = _collapse(_edge(3, io_target_kind="in_memory", io_mode="w"), _edge(4))
    assert call_site_target_kinds(meta) == (None, "in_memory")
    assert call_site_modes(meta) == (None, "w")


def test_a_malformed_member_is_an_unknown_site_not_a_missing_one():
    """Dropping it would be the defect again: a site read as absent."""
    assert call_site_target_kinds({"io_target_kind_values": ["in_memory", 7]}) \
        == ("in_memory", None)
    assert call_site_modes({"io_mode_values": ["w", 7]}) == ("w", None)


# ---------------------------------------------------------------------------
# Every consumer gives it the single-unstamped-call meaning
# ---------------------------------------------------------------------------


def test_an_unstamped_sibling_means_the_edge_may_cross():
    kinds = call_site_target_kinds(_collapse(_edge(3, io_target_kind="in_memory"), _edge(4)))
    assert target_kinds_cross_no_boundary(kinds) is False
    # control: every site stamped in-memory still crosses nothing
    assert target_kinds_cross_no_boundary(("in_memory", "null_device")) is True


def test_target_kind_selection_abstains_on_an_unstamped_site():
    assert resolve_target_kind_across_sites((None, "std_stream")) is None
    assert resolve_target_kind_across_sites(("std_stream",)) == "ipc_recv"  # control


def test_the_source_gate_mints_when_a_site_is_unstamped():
    meta = _collapse(_edge(3, io_target_kind="in_memory"), _edge(4))
    assert _source_call_can_mint_taint({"meta": meta}) is True
    only_in_memory = _collapse(
        _edge(3, io_target_kind="in_memory"), _edge(4, io_target_kind="in_memory"),
    )
    assert _source_call_can_mint_taint({"meta": only_in_memory}) is False  # control


def test_the_sink_gate_keeps_a_sink_with_an_unstamped_site():
    meta = _collapse(_edge(3, io_target_kind="null_device"), _edge(4))
    assert _sink_call_can_carry_taint({"meta": meta}) is True


def test_an_unmoded_site_reads_as_the_default_and_spans_the_edge():
    """``open(p)`` beside ``open(q, 'w')``: both crossings are true."""
    modes = call_site_modes(_collapse(_edge(3, io_mode="w"), _edge(4)))
    assert resolve_mode_boundary_across_sites(modes) == "fs_write"
    catalog = load_catalog("python")
    match = catalog.lookup_with_module("open", "builtins", io_modes=modes)
    assert match is not None
    assert match.boundary == "fs_write"
    assert mode_spanned_boundaries(catalog, match, modes) == {"fs_read", "fs_write"}


# ---------------------------------------------------------------------------
# The name-level pair proof (INV-fumod) sees the unnamed site
# ---------------------------------------------------------------------------

_SRC = "bash:s.sh:1-9:f:function"


def _env(**meta: object) -> dict:
    return {"src": _SRC, "dst": "bash:env:0-0:env.environ:attribute", "meta": meta}


def _red(**meta: object) -> dict:
    return {"src": _SRC, "dst": "bash:redirect:0-0:>:unresolved",
            "meta": {"io_primitive": "redirect.>", **meta}}


def test_an_unnamed_env_site_makes_the_source_names_unknown():
    src, _ = _name_flow_indexes([_env(env_var_values=[None, "SAFE"])])
    assert src[(_SRC, "bash:env:0-0:env.environ:attribute")] == frozenset()
    named, _ = _name_flow_indexes([_env(env_var_values=["OTHER", "SAFE"])])
    assert named[(_SRC, "bash:env:0-0:env.environ:attribute")] == {"OTHER", "SAFE"}


def test_an_unstamped_redirect_site_makes_the_sink_names_unknown():
    _, sink = _name_flow_indexes([_red(redirect_origin_names_values=[None, ["A"]])])
    assert sink[(_SRC, "bash:redirect:0-0:>:unresolved")] is None
    _, stamped = _name_flow_indexes([_red(redirect_origin_names_values=[["B"], ["A"]])])
    assert stamped[(_SRC, "bash:redirect:0-0:>:unresolved")] == {"A", "B"}


def test_merging_two_collapsed_edges_keeps_both_sides_sites():
    """``apply_external_id_remap`` can absorb an edge that is itself a collapse."""
    kept = {"io_target_kind_values": [None, "in_memory"]}
    _absorb_per_call_site_key(kept, {"io_target_kind_values": ["host_path", "std_stream"]},
                              "io_target_kind")
    assert kept["io_target_kind_values"] == [None, "host_path", "in_memory", "std_stream"]
    singular = {"io_target_kind": "in_memory"}
    _absorb_per_call_site_key(singular, {"io_target_kind_values": ["in_memory", "pipe"]},
                              "io_target_kind")
    assert singular == {"io_target_kind_values": ["in_memory", "pipe"]}
