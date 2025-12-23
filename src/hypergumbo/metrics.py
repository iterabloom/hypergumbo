"""Metrics computation for behavior map output.

Computes summary statistics from nodes and edges:
- Total counts (nodes, edges, files)
- Average confidence across edges
- Per-language breakdowns

These metrics help agents quickly assess the scope and quality
of an analysis without traversing the full graph.
"""
from __future__ import annotations

from typing import Any, Dict, List


def compute_metrics(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute metrics from nodes and edges.

    Args:
        nodes: List of node dicts (must have 'language', 'path' fields).
        edges: List of edge dicts (must have 'confidence', 'src' fields).

    Returns:
        Metrics dict with total_nodes, total_edges, avg_confidence,
        total_files, and per-language breakdowns.
    """
    total_nodes = len(nodes)
    total_edges = len(edges)

    # Compute average confidence
    confidences = [e.get("confidence", 0.0) for e in edges if "confidence" in e]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    # Count unique files
    files = {n.get("path") for n in nodes if n.get("path")}
    total_files = len(files)

    # Group by language
    languages: Dict[str, Dict[str, int]] = {}
    node_id_to_lang: Dict[str, str] = {}

    for node in nodes:
        lang = node.get("language", "unknown")
        node_id = node.get("id", "")
        node_id_to_lang[node_id] = lang

        if lang not in languages:
            languages[lang] = {"nodes": 0, "edges": 0}
        languages[lang]["nodes"] += 1

    # Count edges per language (based on source node's language)
    for edge in edges:
        src_id = edge.get("src", "")
        lang = node_id_to_lang.get(src_id, "unknown")
        if lang not in languages:
            languages[lang] = {"nodes": 0, "edges": 0}
        languages[lang]["edges"] += 1

    return {
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "total_files": total_files,
        "avg_confidence": round(avg_confidence, 3),
        "languages": languages,
    }
