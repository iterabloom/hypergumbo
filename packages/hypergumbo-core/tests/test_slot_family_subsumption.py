# SPDX-License-Identifier: AGPL-3.0-or-later
"""One I/O expression must produce one taint flow, not one per catalogue row
that describes it (INV-sukoh).

THE SHAPE. ``python.yaml`` deliberately carries a parent ATTRIBUTE row beside a
child SLOT-FAMILY row, and the child's own note says why: *"a method call on the
mapping carries os.environ as its module slot, where the parent's attribute row
cannot reach."* They were meant to be complementary — parent for a bare
``os.environ[...]`` read, child for ``os.environ.get(...)``::

    env_read:
      - module: os.environ                  # child: the slot family
        functions: [get, keys, items, values, copy]
      - module: os
        attributes: [environ]               # parent: the attribute

On ``os.environ.get("X")`` the ANALYZER EMITS TWO EDGES for the one expression —
a ``module_attribute_reference`` to ``os.environ`` and an ``ast_call_direct`` to
``os.environ.get`` — so both rows match and the single read becomes two flows.
Confirmed by live repro before this file was written.

THE RELATION IS COMPUTED, NOT LISTED. A row is a slot-family PARENT when its
qualified name is the MODULE SLOT of another row: ``os.environ`` is the module
of ``get``. That is the catalogue's own structure, so the fix needs no per-
language table and picks up a user-supplied catalogue for free. Four rows in the
shipped catalogues have the shape today, all Python: ``os.environ`` (env_read),
``sys.stdin`` (ipc_recv), ``sys.stdout`` and ``sys.stderr`` (logging).

IT IS SYMMETRIC, WHICH THE ITEM DID NOT MEASURE. Two of those four are SINK
rows, so ``sys.stdout.write(x)`` doubles a sink exactly as ``os.environ.get()``
doubles a source. Both directions are pinned below.

SUBSUMPTION IS CONDITIONAL ON THE CHILD BEING PRESENT AT THE SAME CALLER. A bare
``os.environ["X"]`` emits only the attribute edge, and that row must still match
— dropping the parent unconditionally would delete the read outright.

WHAT IS DEDUCTED IS A NAME, NOT A FLOW. Where both forms genuinely occur in one
symbol the two edges are indistinguishable from the single-form case, because
edges are deduplicated on ``(src, dst, edge_type)`` (INV-vukiv) and the
multiplicity is already gone. So the coarser NAME is dropped and the finding is
still reported: no verdict moves, and the retained name is the strictly more
specific description of the same crossing.
"""

import pytest

from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.taint import (
    TaintSanitizer,
    TaintSink,
    TaintSource,
    propagate_taint_ddg,
    propagate_taint_structural,
    subsume_slot_family_parents,
)

_CALLER = "python:app.py:5-8:leak:function"
_OTHER_CALLER = "python:app.py:12-14:other:function"
_ENVIRON = "python:os:0-0:os.environ:external_symbol"
_ENVIRON_GET = "python:os.environ:0-0:get:external_symbol"

ENVIRON = TaintSource(
    taint_label="host_secret", module="os", name="environ", kind="attribute",
    source_boundary="env_read",
)
ENVIRON_GET = TaintSource(
    taint_label="host_secret", module="os.environ", name="get",
    kind="function", source_boundary="env_read",
)
GETENV = TaintSource(
    taint_label="host_secret", module="os", name="getenv", kind="function",
    source_boundary="env_read",
)

STDOUT = TaintSink(
    zone="logging", trust_level="untrusted", module="sys", name="stdout",
    kind="attribute",
)
STDOUT_WRITE = TaintSink(
    zone="logging", trust_level="untrusted", module="sys.stdout",
    name="write", kind="function",
)


class TestSubsumptionHelper:
    """The relation itself, in isolation from either propagator."""

    def test_parent_dropped_when_child_present_at_same_caller(self) -> None:
        kept = subsume_slot_family_parents([
            (_CALLER, _ENVIRON, ENVIRON),
            (_CALLER, _ENVIRON_GET, ENVIRON_GET),
        ])
        assert [e[2] for e in kept] == [ENVIRON_GET]

    def test_parent_kept_when_no_child_present(self) -> None:
        """A bare ``os.environ["X"]`` is the parent row's whole job."""
        kept = subsume_slot_family_parents([(_CALLER, _ENVIRON, ENVIRON)])
        assert [e[2] for e in kept] == [ENVIRON]

    def test_parent_kept_when_child_is_at_a_different_caller(self) -> None:
        """Two symbols reading two ways are two reads, not one doubled."""
        kept = subsume_slot_family_parents([
            (_CALLER, _ENVIRON, ENVIRON),
            (_OTHER_CALLER, _ENVIRON_GET, ENVIRON_GET),
        ])
        assert len(kept) == 2

    def test_unrelated_siblings_both_kept(self) -> None:
        """``os.getenv`` is not the module slot of anything: control."""
        kept = subsume_slot_family_parents([
            (_CALLER, _ENVIRON, ENVIRON),
            (_CALLER, "python:os:0-0:getenv:external_symbol", GETENV),
        ])
        assert len(kept) == 2

    def test_order_is_preserved(self) -> None:
        """Deterministic output keeps the consumer's row rendering stable."""
        kept = subsume_slot_family_parents([
            (_CALLER, "python:os:0-0:getenv:external_symbol", GETENV),
            (_CALLER, _ENVIRON, ENVIRON),
            (_CALLER, _ENVIRON_GET, ENVIRON_GET),
        ])
        assert [e[2] for e in kept] == [GETENV, ENVIRON_GET]

    def test_start_at_callee_is_never_subsumed(self) -> None:
        """A ``callee``-seeded source analyses a DIFFERENT reachable set.

        Parent and child seed at different nodes, so collapsing them would
        silently change which subgraph was searched — not a duplicate report.
        """
        entry = TaintSource(
            taint_label="host_secret", module="os", name="environ",
            kind="attribute", source_boundary="env_read", start_at="callee",
        )
        kept = subsume_slot_family_parents([
            (_CALLER, _ENVIRON, entry),
            (_CALLER, _ENVIRON_GET, ENVIRON_GET),
        ])
        assert len(kept) == 2

    def test_sinks_subsume_on_the_same_relation(self) -> None:
        """``sys.stdout.write`` subsumes ``sys.stdout``: the sink analogue."""
        kept = subsume_slot_family_parents([
            (_CALLER, "python:sys:0-0:sys.stdout:external_symbol", STDOUT),
            (_CALLER, "python:sys.stdout:0-0:write:external_symbol",
             STDOUT_WRITE),
        ])
        assert [e[2] for e in kept] == [STDOUT_WRITE]


def _call(src: str, dst: str) -> dict:
    return {"src": src, "dst": dst, "type": "calls", "is_resolved": True}


FS_SINKS = [TaintSink(
    zone="host_fs", trust_level="untrusted", module="file", name="write",
    kind="function",
)]
NO_SANITIZERS: list[TaintSanitizer] = []
_SINK_CALLEE = "python:file:0-0:write:external_symbol"


class TestProductionPath:
    """Through :func:`propagate_taint_structural`, not the helper alone."""

    def _run(self, edges: list[dict], sources: list[TaintSource]) -> list:
        return propagate_taint_structural(
            edges, sources, FS_SINKS, NO_SANITIZERS, language="python",
        )

    def test_one_read_two_edges_is_one_finding(self) -> None:
        """The filed defect, end to end."""
        findings = self._run(
            [_call(_CALLER, _ENVIRON), _call(_CALLER, _ENVIRON_GET),
             _call(_CALLER, _SINK_CALLEE)],
            [ENVIRON, ENVIRON_GET],
        )
        assert len(findings) == 1
        assert findings[0].source_primitives == ("os.environ.get",)

    def test_bare_attribute_read_still_reports(self) -> None:
        """Non-vacuity guard: the fix must not delete the parent's own job."""
        findings = self._run(
            [_call(_CALLER, _ENVIRON), _call(_CALLER, _SINK_CALLEE)],
            [ENVIRON, ENVIRON_GET],
        )
        assert len(findings) == 1
        assert findings[0].source_primitives == ("os.environ",)

    def test_two_distinct_sources_keep_both_names(self) -> None:
        """Control: the fix must not eat a genuinely different read.

        The structural arm ends in :func:`collapse_unadjudicated_flows`
        (INV-karud), so two sources at one caller reaching one sink are one
        finding carrying BOTH names and a ``collapsed_flow_count`` of 2. That
        is the number INV-sukoh is about, so this pins the shape the defect
        inflates as well as the fix not deflating it.
        """
        findings = self._run(
            [_call(_CALLER, _ENVIRON),
             _call(_CALLER, "python:os:0-0:getenv:external_symbol"),
             _call(_CALLER, _SINK_CALLEE)],
            [ENVIRON, GETENV],
        )
        assert len(findings) == 1
        assert findings[0].source_primitives == ("os.environ", "os.getenv")
        assert findings[0].collapsed_flow_count == 2

    def test_doubling_no_longer_inflates_the_row_count(self) -> None:
        """The filed defect stated in its own unit: the row denominator.

        Before the fix the same single read produced
        ``source_primitives == ("os.environ", "os.environ.get")`` and
        ``collapsed_flow_count == 2`` — 0005 measured 9 such rows in 170.
        """
        findings = self._run(
            [_call(_CALLER, _ENVIRON), _call(_CALLER, _ENVIRON_GET),
             _call(_CALLER, _SINK_CALLEE)],
            [ENVIRON, ENVIRON_GET],
        )
        assert findings[0].collapsed_flow_count == 1

    def test_sink_doubling_is_one_finding_with_one_site(self) -> None:
        """The sink analogue, which also feeds ``collapsed_flow_count``."""
        findings = propagate_taint_structural(
            [_call(_CALLER, "python:os:0-0:getenv:external_symbol"),
             _call(_CALLER, "python:sys:0-0:sys.stdout:external_symbol"),
             _call(_CALLER, "python:sys.stdout:0-0:write:external_symbol")],
            [GETENV], [STDOUT, STDOUT_WRITE], [], language="python",
        )
        assert len(findings) == 1
        assert findings[0].sink_primitives == ("sys.stdout.write",)
        assert findings[0].collapsed_flow_count == 1


class TestDdgArmWhereItCostsASituation:
    """The ddg arm does NOT collapse, so there the doubling cost a SITUATION.

    :data:`UNADJUDICATED_METHODS` is ``{structural, ddg_mixed}``; a confirmed
    ``ddg`` walk is a pair claim and passes through
    :func:`collapse_unadjudicated_flows` untouched. So on the ddg path the two
    rows never merged and the read was reported as two separate findings —
    which the item, measured on a structural-and-ddg_mixed population, records
    as row-unit-only. It is not, and this pins the difference.
    """

    _FN = "python:app.py:1-3:leak:function"

    def _call(self, dst: str, line: int) -> dict:
        return {"src": self._FN, "dst": dst, "is_resolved": True,
                "type": "calls", "line": line}

    def _run(self, sources: list[TaintSource]) -> list:
        # 1  secret = os.environ.get("API_KEY")
        # 2  file.write(secret)
        return propagate_taint_ddg(
            [DdgEdge(variable="secret", def_block="bb_0", def_line=1,
                     use_block="bb_0", use_line=2, symbol_id=self._FN)],
            [self._call(_ENVIRON, 1), self._call(_ENVIRON_GET, 1),
             self._call("python:file:0-0:write:external_symbol", 2)],
            sources, FS_SINKS, [], ddg_symbols={self._FN},
            stmt_defuse={self._FN: [(1, ("secret",), ())]},
            language="python",
        )

    def test_one_read_is_one_ddg_finding(self) -> None:
        findings = self._run([ENVIRON, ENVIRON_GET])
        assert len(findings) == 1
        assert findings[0].source_primitives == ("os.environ.get",)

    def test_bare_read_alone_still_reports(self) -> None:
        """Non-vacuity floor: the fixture must violate without the child."""
        findings = self._run([ENVIRON])
        assert len(findings) == 1
        assert findings[0].source_primitives == ("os.environ",)


class TestShippedCatalogueHasTheShape:
    """The relation is real in the shipped rows, not only in fixtures."""

    def test_python_declares_exactly_the_four_known_parents(self) -> None:
        from hypergumbo_core.io_boundary import load_catalog
        prims = list(load_catalog("python").primitives)
        modules = {p.module for p in prims}
        parents = {
            p.qualified_name for p in prims
            if p.qualified_name in modules and p.qualified_name != p.module
        }
        assert parents == {
            "os.environ", "sys.stdin", "sys.stdout", "sys.stderr",
        }

    @pytest.mark.parametrize("language", ["bash", "go", "javascript", "rust"])
    def test_other_catalogues_have_no_parent_rows_today(
        self, language: str,
    ) -> None:
        """Pins the measured scope. If one grows the shape, this fires."""
        from hypergumbo_core.io_boundary import load_catalog
        prims = list(load_catalog(language).primitives)
        modules = {p.module for p in prims}
        assert not {
            p.qualified_name for p in prims
            if p.qualified_name in modules and p.qualified_name != p.module
        }
