# SPDX-License-Identifier: AGPL-3.0-or-later
"""Metrics computation for behavior map output.

Computes summary statistics from nodes and edges:
- Total counts (nodes, edges, files)
- Average confidence across edges
- Per-language breakdowns
- Per-supply-chain-tier breakdowns
- A ``debug`` sub-block with introspection counts
  (``unique_paths_in_analysis``, ``analyzed_file_symbols``, and an
  optional ``profile_files_sum`` when a ``profile`` is supplied)

These metrics help agents quickly assess the scope and quality
of an analysis without traversing the full graph. The supply chain
tier breakdown shows how many nodes/edges come from first-party code
vs external dependencies.
"""
from __future__ import annotations

from typing import Any, Dict, List


def compute_metrics(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    profile: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Compute metrics from nodes and edges.

    Args:
        nodes: List of node dicts (must have 'language', 'path' fields).
        edges: List of edge dicts (must have 'confidence', 'src' fields).
        profile: Optional repo profile dict (the ``behavior_map["profile"]``
            block). When supplied, the profile-language-sum file count
            rides in ``debug.profile_files_sum`` (introspection only).

    Returns:
        Metrics dict with total_nodes, total_edges, avg_confidence,
        total_files, per-language breakdowns, and a ``debug`` sub-block
        with introspection counts.

        ``total_files`` is the **node-distinct-path** count — the number
        of distinct ``node.path`` values that survive analysis. INV-mozaf
        canonical definition: the count consumers see when they group
        ``nodes`` by ``path`` is the same number that appears in
        ``metrics.total_files``. The profile-language sum (legacy
        WI-soraj value) over-counts because the profile counts files on
        disk before analyzer filtering / ``find_files`` size caps /
        skipped passes; it now rides in ``debug.profile_files_sum`` for
        diagnostic use only. The ``analyzed_file_symbols`` count
        (``kind == "file"`` Symbol entities) remains in ``debug`` for
        introspection — it counts file-as-Symbol nodes, not distinct paths.
    """
    total_nodes = len(nodes)
    total_edges = len(edges)

    # Compute average confidence
    confidences = [e.get("confidence", 0.0) for e in edges if "confidence" in e]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    # INV-mozaf canonical definition (WI-soraj re-canonicalization):
    # ``total_files`` = unique path count across nodes. Matches what
    # consumers see when they group ``nodes`` by path. The profile-
    # language sum (legacy "files on disk" semantics) over-counts vs the
    # node-distinct path count by the number of files an analyzer
    # discovered but couldn't fully analyze (e.g., over the size cap, or
    # syntax-error fail) and now rides in ``debug.profile_files_sum``
    # for introspection.
    # INV-mozaf: the ``<external>`` sentinel path on external_symbol boundary
    # nodes is a placeholder, not a real file — exclude it so total_files
    # counts only path-bearing source files (the invariant is "total_files ==
    # number of files that contributed at least one node"). Without this, the
    # single ``<external>`` bucket inflates the count by 1 on any repo with
    # external references.
    unique_paths = len({
        n.get("path")
        for n in nodes
        if n.get("path") and n.get("path") != "<external>"
    })
    file_kind_count = sum(1 for n in nodes if n.get("kind") == "file")
    total_files = unique_paths
    profile_files_sum: int | None = None
    if profile is not None:
        profile_languages = profile.get("languages") or {}
        profile_files_sum = sum(
            (stats or {}).get("files", 0) for stats in profile_languages.values()
        )

    # Group by language
    languages: Dict[str, Dict[str, int]] = {}
    node_id_to_lang: Dict[str, str] = {}

    for node in nodes:
        # ADR-0031: Class B synthetic stand-ins (linker-emitted protocol
        # symbols) carry language=None and discovery_language=<host>. For
        # per-language metric aggregation, attribute them to their host
        # discovery language so cross-language metrics stay meaningful.
        # node.get("language", "unknown") returns None when the key is
        # present with value None, not the default — so handle explicitly.
        lang = node.get("language") or node.get("discovery_language") or "unknown"
        node_id = node.get("id", "")
        node_id_to_lang[node_id] = lang

        if lang not in languages:
            languages[lang] = {"nodes": 0, "edges": 0, "files": 0}
        languages[lang]["nodes"] += 1
        # WI-ninaj: per-language file rollup — count file-kind nodes per
        # language so ``metrics.languages.<lang>.files`` reads the real count
        # instead of an always-0 placeholder. Node-derived (file-kind node
        # count), consistent with ``total_files`` being the distinct node-path
        # count rather than the over-counting profile-language sum.
        if node.get("kind") == "file":
            languages[lang]["files"] += 1

    # Count edges per language (based on source node's language)
    for edge in edges:
        src_id = edge.get("src", "")
        lang = node_id_to_lang.get(src_id, "unknown")
        if lang not in languages:
            languages[lang] = {"nodes": 0, "edges": 0, "files": 0}
        languages[lang]["edges"] += 1

    # Group by supply chain tier. Populated by iterating the ANALYZED node
    # set below, so it enumerates only tiers that carry >=1 analyzed node —
    # in practice the analyzed tiers 1-3 (first_party / internal_dep /
    # external_dep). Tier 4 (derived) is excluded from analysis (spec §14),
    # emits no nodes, and therefore never gets a bucket here; it surfaces
    # solely as ``supply_chain_summary.derived_skipped``. The resulting
    # tier-set disagreement between the two summary surfaces is intentional,
    # not an omission (WI-nibul): an always-empty ``derived`` bucket here
    # would be a structurally-always-0 field, which ADR-0040 forbids.
    by_supply_chain_tier: Dict[str, Dict[str, int]] = {}
    node_id_to_tier: Dict[str, str] = {}

    for node in nodes:
        supply_chain = node.get("supply_chain", {})
        tier_name = supply_chain.get("tier_name", "unknown")
        node_id = node.get("id", "")
        node_id_to_tier[node_id] = tier_name

        if tier_name not in by_supply_chain_tier:
            by_supply_chain_tier[tier_name] = {
                "nodes": 0, "edges": 0, "edges_incident": 0,
            }
        by_supply_chain_tier[tier_name]["nodes"] += 1

    # Count edges per supply chain tier. Two views (WI-modom):
    # - ``edges``: counted once by the SOURCE node's tier. Each edge counts once,
    #   so the per-tier ``edges`` sum reconciles to the resolved-src edge total.
    #   External-dependency tiers (2/3) are graph SINKS, not sources, so their
    #   ``edges`` legitimately reads ~0 — which read as "no contribution".
    # - ``edges_incident``: counts an edge once per DISTINCT resolved endpoint
    #   tier (either-endpoint), so a tier's actual graph contribution is visible
    #   (a tier-3 dependency referenced by N edges shows N incident, not 0). This
    #   view double-counts cross-tier edges by design and does NOT sum to the
    #   total (the src-tier ``edges`` view is the reconciling one).
    # INV-jukok: an unresolved endpoint (not in node_id_to_tier) is skipped
    # rather than minting an "unknown" bucket — tier counts reference real nodes.
    for edge in edges:
        src_tier = node_id_to_tier.get(edge.get("src", ""))
        dst_tier = node_id_to_tier.get(edge.get("dst", ""))
        if src_tier is not None:
            by_supply_chain_tier[src_tier]["edges"] += 1
        for incident_tier in {src_tier, dst_tier}:
            if incident_tier is not None:
                by_supply_chain_tier[incident_tier]["edges_incident"] += 1

    debug: Dict[str, Any] = {
        "unique_paths_in_analysis": unique_paths,
        "analyzed_file_symbols": file_kind_count,
    }
    if profile_files_sum is not None:
        debug["profile_files_sum"] = profile_files_sum
    return {
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "total_files": total_files,
        "avg_confidence": round(avg_confidence, 3),
        "languages": languages,
        "by_supply_chain_tier": by_supply_chain_tier,
        "debug": debug,
    }
