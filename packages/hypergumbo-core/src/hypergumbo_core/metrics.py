# SPDX-License-Identifier: AGPL-3.0-or-later
"""Metrics computation for behavior map output.

Computes summary statistics from nodes and edges:
- Total counts (nodes, edges, files)
- Average confidence across edges, plus the distribution behind it
  (``edge_confidence``: histogram + median)
- Per-language breakdowns
- Per-supply-chain-tier breakdowns
- A ``debug`` sub-block with introspection counts
  (``unique_paths_in_analysis``, ``analyzed_file_symbols``, and an
  optional ``profile_files_sum`` when a ``profile`` is supplied)

These metrics help agents quickly assess the scope and quality
of an analysis without traversing the full graph. The supply chain
tier breakdown shows how many nodes/edges come from first-party code
vs external dependencies.

Why ``edge_confidence`` exists (WI-zimor): edge confidence is not a
continuous quantity. Producers stamp per-evidence-type literals (ADR-0039
ruling 1; WI-famiv), so a real map is a handful of tight spikes -- on this
repository's self map 0.85 / 0.4 / 0.5 / 0.95 / 0.9 / 0.8 carry 99.8% of
edges -- and the scalar ``avg_confidence`` (0.746 there) lands BETWEEN
spikes, on a value no edge carries. Thresholding on it cuts between spikes.
The histogram shows the shape; the median is the LOWER median
(as ``statistics.median_low``), so it is always a value some edge carries
(an even-count midpoint could again sit between two spikes). Histogram
keys are the value at 0.01 resolution (``f"{c:.2f}"``), ascending: the
literal producers use two decimals, so their spikes are exact, while the
few producers that multiply confidences (e.g. ``0.85 * lookup * penalty``)
fold into their 0.01 bucket, which bounds the key set to 101 on [0, 1].
Mode and multimodality are derivable from the histogram and are not
published separately. Both summaries are over EDGES only: nodes carry no
``confidence`` (``Symbol`` has none). With no confidence-bearing edge the
median is ``None`` and the histogram empty -- no measurement, said
positively -- while ``avg_confidence`` keeps its legacy ``0.0``
placeholder, which the empty histogram lets a reader recognise.
"""
from __future__ import annotations

from typing import Any, Dict, List


def _edge_confidence_distribution(confidences: List[float]) -> Dict[str, Any]:
    """Histogram (0.01 resolution, ascending keys) + lower median.

    ``median`` is ``None`` when there is nothing to summarise, so an empty
    input is not reported as a measured confidence of 0.
    """
    ordered = sorted(confidences)
    histogram: Dict[str, int] = {}
    for value in ordered:
        key = f"{value:.2f}"
        histogram[key] = histogram.get(key, 0) + 1
    # Lower median (== statistics.median_low): the middle value for an odd
    # count, the lower of the two middle values for an even one -- always a
    # value some edge carries.
    median = round(ordered[(len(ordered) - 1) // 2], 3) if ordered else None
    return {"histogram": histogram, "median": median}


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
        edge_confidence (``{histogram, median}`` over edge confidence --
        see the module docstring), total_files, per-language breakdowns,
        and a ``debug`` sub-block with introspection counts.

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

    # Edge confidence: mean (legacy scalar) plus the distribution behind it
    # (WI-zimor; see module docstring). Edges only -- nodes carry none.
    confidences = [e.get("confidence", 0.0) for e in edges if "confidence" in e]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    edge_confidence = _edge_confidence_distribution(confidences)

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
    #   Third-party tier 3 is a graph SINK (dependencies are referenced, not
    #   sources), so its ``edges`` legitimately reads ~0 (INV-higop: tier 2 is
    #   internal / project-side per ADR-0041 — a source that CAN have out-edges,
    #   not lumped with tier 3 as an external-dependency sink).
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
        "edge_confidence": edge_confidence,
        "languages": languages,
        "by_supply_chain_tier": by_supply_chain_tier,
        "debug": debug,
    }
