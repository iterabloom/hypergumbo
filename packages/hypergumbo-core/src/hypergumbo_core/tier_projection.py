# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a budget tier carries of each part of a survey (WI-lavum).

A tier (``survey.4k.json``, ...) is a survey cut to a token budget. The cut
used to fall on nodes alone: ``compact_node`` slims a node to nine fields,
and everything else was copied at full size. Measured on five repositories,
the parts that do not depend on which nodes were chosen came to 4.2k-13.9k
tokens, so a 4k tier held one node on every one of them and a 16k tier one
node on two. This module gives every other part a tier form.

The rules, and where each stops
-------------------------------
**Records that differ in one key are grouped** (``group_records``).
``limits.skipped_passes`` has one record per analyzer that found nothing,
identical but for ``pass``; ``limits.failed_files`` one per file, often
identical but for ``path``. A tier carries one record per distinct
remainder, with the varying values in a list beside it
(``skipped_passes_by_reason`` / ``failed_files_by_reason``; the ungrouped
keys are absent from a tier, never empty). Largest group first.

**A list that grows with the repository is cut to ``cap`` entries and says
how many it had** (``bound_lists``): ``paths`` becomes its first ``cap``
entries and ``paths_total`` holds the full length. The cap is a function of
the budget alone (``list_cap``), and the walk covers every list under every
section that is not owned by the node selection, so a section added later
is bounded without being named here. A list at or under the cap is left as
it is, with no ``_total`` key: the key's presence means "cut".

**A list of sentences is kept whole** (``_is_prose``). ``limits.not_captured``
and the lists under ``reproducibility_context`` say what the analysis cannot
see and what follows from that. They are statements, a handful of them, and
do not grow with the repository; a tier that dropped four of six caveats to
save forty tokens would be shorter and less true. A list counts as sentences
when every entry is a string and at least half contain a space, which paths,
ids and names do not.

**Edges, entry points and features are slimmed like nodes.** An edge keeps
what a reader of a graph needs (``id``, ``src``, ``dst``, ``type``, ``line``,
``confidence`` and ``meta.evidence_type``) and drops its provenance and
ranking fields. An entry point keeps ``symbol_id``, ``kind``, ``label`` and
``confidence``. A feature keeps its identity and its node and edge pointers
and drops the per-node maps and the query that produced it.

Nothing dropped here is lost: the full survey beside the tier has all of it.
What a tier owes its reader is that every count it prints is true of the
repository or of the tier, and that a cut is visible as a cut.
"""

from __future__ import annotations

from typing import Any, Sequence

#: Top-level sections the node selection owns: their size is set by which
#: nodes were chosen, and their lists are the selection, never a tail to cut.
SELECTION_SECTIONS = frozenset({
    "nodes", "edges", "entrypoints", "features",
    "nodes_summary", "features_summary", "metrics",
})

_EDGE_FIELDS = ("id", "src", "dst", "type", "line", "confidence")
_ENTRYPOINT_FIELDS = ("symbol_id", "kind", "label", "confidence")
_FEATURE_FIELDS = ("id", "name", "entry_nodes", "node_ids", "edge_ids", "limits_hit")

#: ``limits`` record lists and the one key each varies in.
_GROUPED_LIMITS = (
    ("skipped_passes", "pass", "passes", "skipped_passes_by_reason"),
    ("failed_files", "path", "paths", "failed_files_by_reason"),
)


def list_cap(target_tokens: int) -> int:
    """How many entries of a repository-sized list a tier of this budget keeps.

    Two at 4k, eight at 16k, twenty from 40k up: enough to show what the
    list holds, small enough that a dozen such lists do not crowd out the
    nodes the tier exists to carry.
    """
    return max(2, min(20, target_tokens // 2000))


def group_records(
    records: Sequence[dict[str, Any]], varying: str, collected: str,
) -> list[dict[str, Any]]:
    """Fold records that are equal apart from ``varying`` into one record
    each, with the ``varying`` values, sorted, under ``collected``.

    Lossless: every input record can be rebuilt from the output. A record
    without the ``varying`` key is kept as its own group with an empty list.
    Groups are ordered largest first, then by their remaining fields.
    """
    groups: dict[str, tuple[dict[str, Any], list[Any]]] = {}
    for record in records:
        rest = {k: v for k, v in record.items() if k != varying}
        key = repr(sorted(rest.items(), key=lambda item: item[0]))
        _rest, values = groups.setdefault(key, (rest, []))
        if varying in record:
            values.append(record[varying])
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1][1]), item[0]))
    return [{**rest, collected: sorted(values, key=str)} for _key, (rest, values) in ordered]


def _is_prose(items: list[Any]) -> bool:
    """Whether a list holds sentences rather than paths, ids or names."""
    return (
        all(isinstance(item, str) for item in items)
        and 2 * sum(1 for item in items if " " in item) >= len(items)
    )


def bound_lists(value: Any, cap: int) -> Any:
    """A copy of ``value`` in which every list that is a dict's value and is
    longer than ``cap`` is cut to ``cap`` entries, with ``<key>_total``
    beside it holding the length it had. A list of sentences is not cut.

    A list directly inside a list has no key to hang a total on and is left
    whole; none of the survey's sections nests lists that way.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, list) and len(item) > cap and not _is_prose(item):
                out[key] = [bound_lists(entry, cap) for entry in item[:cap]]
                out.setdefault(f"{key}_total", len(item))
            else:
                out[key] = bound_lists(item, cap)
        return out
    if isinstance(value, list):
        return [bound_lists(entry, cap) for entry in value]
    return value


def project_fixed_sections(tiered_map: dict[str, Any], target_tokens: int) -> None:
    """Give every section the node selection does not own its tier form, in
    place: group the two ``limits`` record lists, then bound every list."""
    cap = list_cap(target_tokens)
    limits = tiered_map.get("limits")
    if isinstance(limits, dict):
        limits = dict(limits)
        for key, varying, collected, grouped_key in _GROUPED_LIMITS:
            if key in limits:
                limits[grouped_key] = group_records(limits.pop(key), varying, collected)
        tiered_map["limits"] = limits
    for key in list(tiered_map):
        if key not in SELECTION_SECTIONS:
            tiered_map[key] = bound_lists(tiered_map[key], cap)


def compact_edge(edge: dict[str, Any]) -> dict[str, Any]:
    """The tier form of an edge record."""
    slim = {field: edge[field] for field in _EDGE_FIELDS if field in edge}
    evidence = (edge.get("meta") or {}).get("evidence_type")
    if evidence is not None:
        slim["meta"] = {"evidence_type": evidence}
    return slim


def compact_entrypoint(entrypoint: dict[str, Any]) -> dict[str, Any]:
    """The tier form of an entry point record."""
    return {field: entrypoint[field] for field in _ENTRYPOINT_FIELDS if field in entrypoint}


def compact_feature(feature: dict[str, Any]) -> dict[str, Any]:
    """The tier form of a (re-projected) feature: identity and pointers.
    An empty ``limits_hit`` is left out."""
    return {
        field: feature[field] for field in _FEATURE_FIELDS
        if field in feature and not (field == "limits_hit" and not feature[field])
    }
