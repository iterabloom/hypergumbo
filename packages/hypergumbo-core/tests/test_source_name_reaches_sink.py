# SPDX-License-Identifier: AGPL-3.0-or-later
"""A source name that cannot reach the sink is not a flow (INV-fumod shape b).

The per-EDGE gate WI-zovuz shipped fires only when NO externally-derived name
reaches a redirect. INV-fumod's named instance is not that shape: guacamole's
`curl -L "$URL" > "$DEST_PATH/$DEST_JAR"` IS reached -- by DESTINATION, through
`DEST_PATH="$DESTINATION/drivers/"`. The row the item actually filed is sourced
at MYSQL_JDBC_VERSION, read at a DIFFERENT site (the file symbol, line 87) from
DESTINATION (the download_driver function, line 59), so the two pair with the
same sink separately and the false one can be refused on its own.

This is a PAIR-level question -- neither edge alone answers it -- which is why
it sits beside `_source_and_sink_are_one_call` rather than in either
per-call-site gate.

DEFAULT-DENY ON THE SILENCING DIRECTION, as everywhere in this family: the pair
survives unless BOTH sides are known AND the intersection is provably empty.
"""
from __future__ import annotations

from hypergumbo_core.taint import _source_names_can_reach_sink


class TestTheProof:

    def test_a_disjoint_name_set_is_refused(self):
        assert not _source_names_can_reach_sink(
            frozenset({"MYSQL_JDBC_VERSION"}), frozenset({"DESTINATION"}))

    def test_an_overlapping_name_set_survives(self):
        assert _source_names_can_reach_sink(
            frozenset({"DESTINATION"}), frozenset({"DESTINATION"}))

    def test_one_overlapping_name_out_of_several_survives(self):
        assert _source_names_can_reach_sink(
            frozenset({"A", "DESTINATION"}), frozenset({"DESTINATION"}))


class TestTheDefaultIsToKeep:
    """Unknown on either side must never silence."""

    def test_an_unknown_sink_stamp_keeps_the_pair(self):
        assert _source_names_can_reach_sink(frozenset({"SECRET"}), None)

    def test_an_unknown_source_name_set_keeps_the_pair(self):
        assert _source_names_can_reach_sink(None, frozenset({"DESTINATION"}))

    def test_both_unknown_keeps_the_pair(self):
        assert _source_names_can_reach_sink(None, None)

    def test_an_empty_source_name_set_keeps_the_pair(self):
        # A source that carries no name is not a bash env read at all -- every
        # other language's sources land here, and must be untouched.
        assert _source_names_can_reach_sink(frozenset(), frozenset({"X"}))

    def test_an_empty_sink_stamp_is_left_to_the_per_edge_gate(self):
        # "No name reaches this redirect" is already a proof, and it is
        # _sink_call_can_carry_taint's to make. Answering it here too would put
        # one fact in two homes.
        assert _source_names_can_reach_sink(frozenset({"X"}), frozenset())


# --------------------------------------------------------------------------
# The indexes, and the collapsed-edge case that is the whole reason they exist.
# --------------------------------------------------------------------------
from hypergumbo_core.cfg import DdgEdge  # noqa: E402
from hypergumbo_core.taint import (  # noqa: E402
    TaintSink,
    TaintSource,
    _name_flow_indexes,
    propagate_taint_ddg,
    propagate_taint_structural,
)

_FILE = "bash:s.sh:1-1:file:file"
_FUNC = "bash:s.sh:10-20:download:function"
_ENV = "bash:env:0-0:env.environ:external_symbol"
_RED = "bash:redirect:0-0:>:external_symbol"


def _env_edge(src, **meta):
    return {"src": src, "dst": _ENV, "type": "calls",
            "is_resolved": False,
            "meta": {"evidence_type": "module_attribute_reference", **meta}}


def _red_edge(src, **meta):
    return {"src": src, "dst": _RED, "type": "calls", "is_resolved": False,
            "meta": {"io_primitive": "redirect.>", "io_mode": "w", **meta}}


class TestTheIndexes:

    def test_a_singular_env_var_is_read(self):
        src, _ = _name_flow_indexes([_env_edge(_FILE, env_var="API_KEY")])
        assert src[(_FILE, _ENV)] == frozenset({"API_KEY"})

    def test_a_collapsed_env_edge_is_read_from_values(self):
        # THE CASE THAT MOTIVATED THIS. Three env reads at three lines under
        # one (src, dst) collapse, so `env_var` is REMOVED and the distinct
        # values move to `env_var_values` (ir._absorb_per_call_site_key).
        # Reading only the singular loses every multi-variable file, which is
        # exactly guacamole's.
        src, _ = _name_flow_indexes([
            _env_edge(_FILE, env_var_values=["MYSQL_V", "PGSQL_V", "MSSQL_V"]),
        ])
        assert src[(_FILE, _ENV)] == frozenset({"MYSQL_V", "PGSQL_V", "MSSQL_V"})

    def test_a_singular_stamp_is_read(self):
        _, sink = _name_flow_indexes([_red_edge(_FUNC, redirect_origin_names=["D"])])
        assert sink[(_FUNC, _RED)] == frozenset({"D"})

    def test_a_collapsed_stamp_unions_every_site(self):
        # Sites disagreed, so the singular is gone and `_values` holds a LIST
        # OF LISTS. Union rather than intersect: any of those sites could be
        # the one this pair flows to, and a bigger set keeps more findings.
        _, sink = _name_flow_indexes([
            _red_edge(_FUNC, redirect_origin_names_values=[["A"], ["B", "C"]]),
        ])
        assert sink[(_FUNC, _RED)] == frozenset({"A", "B", "C"})

    def test_a_missing_stamp_makes_the_key_unknown(self):
        _, sink = _name_flow_indexes([_red_edge(_FUNC)])
        assert sink[(_FUNC, _RED)] is None

    def test_one_unstamped_edge_poisons_the_key_whatever_the_order(self):
        # A partial answer must not read as a complete one: the unstamped edge
        # could be reached by a name the stamped one is not. Asserted BOTH
        # ways round, because "the later edge wins" would pass one order.
        stamped = _red_edge(_FUNC, redirect_origin_names=["D"])
        bare = _red_edge(_FUNC)
        for order in ([stamped, bare], [bare, stamped]):
            _, sink = _name_flow_indexes(order)
            assert sink[(_FUNC, _RED)] is None, order

    def test_a_non_string_value_is_ignored(self):
        src, _ = _name_flow_indexes([_env_edge(_FILE, env_var_values=["OK", 7, ""])])
        assert src[(_FILE, _ENV)] == frozenset({"OK"})

    def test_a_non_list_values_stamp_falls_back_to_the_singular(self):
        _, sink = _name_flow_indexes([
            _red_edge(_FUNC, redirect_origin_names=["D"],
                      redirect_origin_names_values="oops"),
        ])
        assert sink[(_FUNC, _RED)] == frozenset({"D"})


# --------------------------------------------------------------------------
# Both propagators, because bash reaches findings through the DDG arm.
# --------------------------------------------------------------------------
_BASH_SOURCE = TaintSource(
    taint_label="host_secret", module="env", name="environ", kind="attribute",
    source_boundary="env_read",
)
_BASH_SINK = TaintSink(
    zone="host_fs", trust_level="untrusted", module="redirect", name=">",
    kind="function",
)


def _guacamole_edges(stamp):
    """The item's own shape: version vars at the FILE, DESTINATION in the function."""
    return [
        _env_edge(_FILE, env_var_values=["MYSQL_V", "PGSQL_V"]),
        {"src": _FILE, "dst": _FUNC, "type": "calls", "is_resolved": True,
         "meta": {"call_construct": "function"}},
        _red_edge(_FUNC, redirect_origin_names=stamp),
    ]


class TestBothPropagatorsRefuseThePair:
    """ONE FACT, TWO HOMES. Gating only the structural arm left INV-fumod's own
    instance standing while every unit test passed -- bash reports
    analysis_method "structural" but reaches findings through the DDG arm."""

    def test_structural_refuses_a_name_that_cannot_reach(self):
        found = list(propagate_taint_structural(
            _guacamole_edges(["DESTINATION"]), [_BASH_SOURCE], [_BASH_SINK],
            [], language="bash",
        ))
        assert found == []

    def test_ddg_refuses_a_name_that_cannot_reach(self):
        found = list(propagate_taint_ddg(
            [DdgEdge(variable="u", def_block="b0", def_line=11,
                     use_block="b0", use_line=11, symbol_id=_FUNC)],
            _guacamole_edges(["DESTINATION"]), [_BASH_SOURCE], [_BASH_SINK],
            [], ddg_symbols={_FUNC}, language="bash",
        ))
        assert found == []

    def test_structural_keeps_a_name_that_reaches(self):
        found = list(propagate_taint_structural(
            _guacamole_edges(["MYSQL_V"]), [_BASH_SOURCE], [_BASH_SINK],
            [], language="bash",
        ))
        assert len(found) == 1, found

    def test_ddg_keeps_a_name_that_reaches(self):
        found = list(propagate_taint_ddg(
            [DdgEdge(variable="u", def_block="b0", def_line=11,
                     use_block="b0", use_line=11, symbol_id=_FUNC)],
            _guacamole_edges(["MYSQL_V"]), [_BASH_SOURCE], [_BASH_SINK],
            [], ddg_symbols={_FUNC}, language="bash",
        ))
        assert len(found) == 1, found
