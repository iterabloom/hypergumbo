# SPDX-License-Identifier: AGPL-3.0-or-later
"""The supply-chain tier filter: ONE function for ``run_survey`` and the per-PR
claims-coverage guard.

WHY THIS IS A MODULE AND NOT A BLOCK. Until WI-jizam this was an inline block
of ``cli.run_survey``. The guard (``claims_coverage_guard``) exists to give the
cron's coverage answer per PR from the Python analyzer's edges alone, and the
cron's answer is read AFTER this filter: a DERIVED (tier 4) file -- a generated
``_pb2.py``, a minified bundle -- leaves the map with every edge touching it,
so a cross-package call into it is left naming a module nobody analysed, and
the coverage gate reports that module. The guard could not run an inline
block, so on dev acf67df4cb it kept ``scip/_generated/scip_pb2.py`` analysed,
suppressed the call into it as first-party, and passed a tree the cron
withheld every self-claim on (INV-hotib). A second copy of the rule in the
guard would be the drift this repository keeps paying for; the block moved.

WHAT IT DOES, unchanged from the block it was lifted from:

1. **Route promotion.** A tier-4 symbol that carries a route (``framework_role``
   or a ``route`` concept) is promoted to tier 1 with the reason "route
   promoted from derived": generated API surface (go-swagger, protobuf gRPC
   stubs) lives IN the repo, distance 0, and ADR-0041 section 1 makes tier a
   distance, so first_party and not internal_dep.
2. **Symbol filter.** ``effective_tier`` is ``max_tier`` or 3: DERIVED is always
   excluded unless ``--max-tier 4``. DERIVED files are minified, bundled or
   generated artifacts whose symbols distort centrality and inflate edge
   counts through name collisions.
3. **Edge filter, membership on BOTH endpoints.** An edge goes when either
   endpoint is a node this filter removed. An endpoint that never had a node
   (an unresolved external dst, a producer-side gap as src) is kept: it is a
   real dependency, and boundary synthesis gives it a node afterwards
   (ADR-0043 section 4). INV-saruf (owner ruling 2026-11-03): the src rule is
   membership like the dst rule; it used to keep a removed file's edges by
   the SHAPE of the src id, so a tier-4 file came back from synthesis as
   ``<path>:0-0:<name>:external_symbol`` stand-ins. File-level srcs always
   have a node here: ``all_analyzers`` synthesizes one for every dangling
   file id before this runs (WI-ramuv). The ``is_external_boundary`` carve-out
   is a defensive no-op since WI-pozur moved synthesis after this filter; it
   stays so the carve-out restores itself if synthesis ever moves back.
4. **Limits.** Each dropped FILE node is recorded (``tier_filtered_files``,
   WI-tulit) and ``max_tier_applied`` is set, so a consumer can see what
   vanished; nothing else reflects a tier drop.

Nothing is recorded and nothing is removed when ``effective_tier`` is 4.
"""
from __future__ import annotations

from typing import Optional

from .ir import Edge, Symbol, is_external_boundary
from .limits import Limits

#: Tier 4 (DERIVED) is excluded unless the caller asks for it.
DEFAULT_MAX_TIER = 3

_ROUTE_PROMOTION_REASON = "route promoted from derived"


def _bears_a_route(symbol: Symbol) -> bool:
    meta = symbol.meta or {}
    if meta.get("framework_role") == "route":
        return True
    return any(
        isinstance(concept, dict) and concept.get("concept") == "route"
        for concept in meta.get("concepts", [])
    )


def apply_tier_filter(
    all_symbols: list[Symbol],
    all_edges: list[Edge],
    max_tier: Optional[int],
    limits: Limits,
) -> tuple[list[Symbol], list[Edge]]:
    """Promote route-bearing DERIVED symbols, then drop DERIVED symbols and
    every edge touching one; record what left in ``limits``.

    Returns the surviving ``(symbols, edges)``. Route promotion mutates the
    promoted symbols in place, as the block always did; the input lists are
    not modified.
    """
    for symbol in all_symbols:
        if symbol.supply_chain_tier == 4 and _bears_a_route(symbol):
            symbol.supply_chain_tier = 1
            symbol.supply_chain_reason = _ROUTE_PROMOTION_REASON
            symbol.supply_chain_classified = True

    effective_tier = max_tier if max_tier is not None else DEFAULT_MAX_TIER
    if effective_tier >= 4:
        return all_symbols, all_edges

    filtered_symbols = [
        s for s in all_symbols if s.supply_chain_tier <= effective_tier
    ]
    filtered_symbol_ids = {s.id for s in filtered_symbols}
    removed_symbol_ids = {
        s.id for s in all_symbols
        if s.id not in filtered_symbol_ids and not is_external_boundary(s)
    }
    filtered_edges = [
        e for e in all_edges
        if e.src not in removed_symbol_ids and e.dst not in removed_symbol_ids
    ]
    for dropped_path in sorted({
        s.path for s in all_symbols
        if s.supply_chain_tier > effective_tier and s.kind == "file"
    }):
        limits.add_tier_filtered_file(dropped_path)
    limits.max_tier_applied = effective_tier
    return filtered_symbols, filtered_edges
