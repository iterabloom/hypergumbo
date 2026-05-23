# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runtime corpus-based coherence check for the ADR-0023 edge-type axis.

Static drift detection (``axis_drift.find_drift``) catches consumer-side
hardcoded sets that drift from the canonical registry. This module
catches the producer-side complement: emit-time endpoint-shape leakage,
where the same ``(src, dst)`` partition produces multiple distinct
``edge_type`` values depending on framework / language / construct
specifics. ADR-0023 §3 specifies the test as:

    test_edge_type_does_not_encode_endpoint_metadata:
      for each emitted edge in a corpus run:
        assert edge.edge_type is not derivable from
          (src.kind, src.language, src.framework,
           dst.kind, dst.language, dst.framework)
        by any pure function.

The ADR names six fields including framework. This implementation uses
four — ``(src.kind, src.language, dst.kind, dst.language)`` — because
hypergumbo doesn't store ``framework`` as a top-level Symbol field; it
lives in ``meta.concepts[*].framework`` (a list, since one symbol can
carry multiple concepts), which is awkward to use as a partition key.
The four-field partition is a *coarser* key than the ADR specifies, so
any leak the four-field captures the six-field would also capture; the
six-field would only catch additional finer-grained leaks. If a future
``Symbol.framework`` registry lands, expand the partition key here.

Phase-1 deployment is WARN-only: this checker is invoked by humans (or
by sibling Phase 2/3 migration items) rather than wired into pre-commit
/ CI. As Phase 2/3 reduce the offender set, the deployment posture can
tighten — Phase 4's expectation is that the offender set goes empty
modulo the allow-list.

Allow-list: ``docs/edge-type-runtime-allowlist.yaml``. Each entry
permits multiple ``edge_type`` values within a single partition.
ADR-0023 §3 mandates that allow-list growth requires an ADR amendment.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .behavior_map_io import load_behavior_map


PartitionKey = tuple[str, str, str, str]
"""``(src_kind, src_language, dst_kind, dst_language)`` — the empirical
partition key. Empty strings are valid components (an unset field
matches an unset field)."""


@dataclass(frozen=True)
class AllowlistEntry:
    """A single allow-list entry permitting multi-``edge_type`` variance
    within one partition."""

    src_kind: str
    src_language: str
    dst_kind: str
    dst_language: str
    permitted_edge_types: frozenset[str]
    rationale: str = ""
    adr_reference: str = ""

    @property
    def partition_key(self) -> PartitionKey:
        return (
            self.src_kind, self.src_language,
            self.dst_kind, self.dst_language,
        )


@dataclass(frozen=True)
class PartitionOffender:
    """A partition where ``edge_type`` varies — one offender per partition."""

    partition_key: PartitionKey
    edge_types: frozenset[str]
    edge_count: int


def _build_symbol_index(
    behavior_map: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Map ``id`` → node-dict for every node in the behavior map.

    Nodes without an ``id`` field (malformed input) are silently
    skipped — the partition lookup just won't find them, and edges
    referencing them are then skipped at partition time.
    """
    return {
        node["id"]: node
        for node in behavior_map.get("nodes", [])
        if "id" in node
    }


def _partition_key_for_edge(
    edge: dict[str, Any],
    symbol_index: dict[str, dict[str, Any]],
) -> PartitionKey | None:
    """Return the four-field partition key for *edge*, or ``None`` if
    either endpoint isn't in the symbol index.

    Edges to/from external symbols typically still have a synthetic
    Symbol node (``kind="external_symbol"``) so they aren't skipped;
    only edges whose endpoint IDs aren't in the index at all are
    dropped. That happens for malformed maps, not for normal output.
    """
    src = symbol_index.get(edge.get("src", ""))
    dst = symbol_index.get(edge.get("dst", ""))
    if src is None or dst is None:
        return None
    return (
        src.get("kind", ""),
        src.get("language", ""),
        dst.get("kind", ""),
        dst.get("language", ""),
    )


def find_offenders(
    behavior_map: dict[str, Any],
) -> list[PartitionOffender]:
    """Scan *behavior_map* and return partitions where ``edge_type``
    varies.

    Each offender is a partition with two or more distinct
    ``edge_type`` values. Returned in descending order of edge count
    (largest leakage first), with the partition key as a stable
    tiebreaker.

    Edges with empty ``type`` or with endpoints not in the node index
    are silently skipped. Empty input (no nodes / no edges) returns
    an empty list, not an error.
    """
    symbol_index = _build_symbol_index(behavior_map)
    partitions: dict[PartitionKey, dict[str, int]] = {}
    for edge in behavior_map.get("edges", []):
        key = _partition_key_for_edge(edge, symbol_index)
        if key is None:
            continue
        edge_type = edge.get("type", "")
        if not edge_type:
            continue
        per_type = partitions.setdefault(key, {})
        per_type[edge_type] = per_type.get(edge_type, 0) + 1

    offenders: list[PartitionOffender] = []
    for key, per_type in partitions.items():
        if len(per_type) > 1:
            offenders.append(PartitionOffender(
                partition_key=key,
                edge_types=frozenset(per_type),
                edge_count=sum(per_type.values()),
            ))
    offenders.sort(key=lambda o: (-o.edge_count, o.partition_key))
    return offenders


def load_allowlist(path: Path | None) -> list[AllowlistEntry]:
    """Load allow-list entries from a YAML file at *path*.

    Returns an empty list if *path* is ``None`` or doesn't exist
    (unset allow-list is the warn-only deployment shape). YAML schema:

    .. code-block:: yaml

       allowlist:
         - src_kind: function
           src_language: python
           dst_kind: type
           dst_language: python
           permitted_edge_types: [references, type_ref]
           rationale: "..."
           adr_reference: "ADR-0023 amendment §X"
    """
    if path is None or not path.exists():
        return []
    import yaml
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not raw:
        return []
    entries: list[AllowlistEntry] = []
    for entry in raw.get("allowlist", []) or []:
        entries.append(AllowlistEntry(
            src_kind=entry.get("src_kind", ""),
            src_language=entry.get("src_language", ""),
            dst_kind=entry.get("dst_kind", ""),
            dst_language=entry.get("dst_language", ""),
            permitted_edge_types=frozenset(
                entry.get("permitted_edge_types", []),
            ),
            rationale=entry.get("rationale", ""),
            adr_reference=entry.get("adr_reference", ""),
        ))
    return entries


def filter_by_allowlist(
    offenders: list[PartitionOffender],
    allowlist: list[AllowlistEntry],
) -> tuple[list[PartitionOffender], int]:
    """Return ``(remaining_offenders, allowlisted_count)``.

    An entry covers an offender when its ``partition_key`` matches AND
    its ``permitted_edge_types`` is a superset of the offender's
    ``edge_types``. Partial coverage (allow-list permits some but not
    all of the offender's variance) does NOT remove the offender —
    growing the allow-list to cover the new types requires an ADR
    amendment per ADR-0023 §3.
    """
    by_key = {entry.partition_key: entry for entry in allowlist}
    remaining: list[PartitionOffender] = []
    allowlisted = 0
    for offender in offenders:
        entry = by_key.get(offender.partition_key)
        if (
            entry is not None
            and offender.edge_types <= entry.permitted_edge_types
        ):
            allowlisted += 1
        else:
            remaining.append(offender)
    return remaining, allowlisted


def format_report(
    remaining: list[PartitionOffender],
    *,
    allowlisted_count: int = 0,
    total_edges_scanned: int | None = None,
) -> str:
    """Format a human-readable report.

    Lines are deterministic for snapshot-style testing:

    - First line: a single summary sentence ("clean" / "N offenders" /
      "all allow-listed").
    - One indented line per remaining offender.
    - Trailing summary line if *total_edges_scanned* is provided.
    """
    lines: list[str] = []
    if not remaining:
        if allowlisted_count > 0:
            lines.append(
                f"All {allowlisted_count} partition variance(s) "
                f"are allow-listed."
            )
        else:
            lines.append("No edge-type partition variance detected.")
    else:
        lines.append(
            f"Edge-type partition variance: {len(remaining)} "
            f"un-allow-listed offender(s)"
            + (
                f" ({allowlisted_count} additional allow-listed)."
                if allowlisted_count > 0
                else "."
            )
        )
        for offender in remaining:
            sk, sl, dk, dl = offender.partition_key
            lines.append(
                f"  ({sk or '<empty>'}/{sl or '<empty>'}) -> "
                f"({dk or '<empty>'}/{dl or '<empty>'}): "
                f"{sorted(offender.edge_types)} "
                f"({offender.edge_count} edges)"
            )

    if total_edges_scanned is not None:
        lines.append(f"Scanned {total_edges_scanned} edges.")
    return "\n".join(lines)


def _count_edges(behavior_map: dict[str, Any]) -> int:
    return len(behavior_map.get("edges", []))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code.

    Exit codes:
        0 — no un-allow-listed offenders
        1 — un-allow-listed offenders detected
        2 — could not read or parse the input behavior map
    """
    parser = argparse.ArgumentParser(
        description=(
            "Check edge-type runtime coherence per ADR-0023 §3. "
            "Reads a behavior-map JSON and reports partitions where "
            "edge_type varies within a single (src.kind, src.language, "
            "dst.kind, dst.language) partition — the runtime "
            "complement to scripts/check-edge-type-drift's static "
            "consumer-side check."
        ),
    )
    parser.add_argument(
        "behavior_map",
        type=Path,
        help="Path to a behavior-map JSON (output of `hypergumbo run`).",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=None,
        help=(
            "Path to a YAML allow-list permitting specific multi-"
            "edge-type partitions. Adding entries requires an ADR "
            "amendment per ADR-0023 §3. Defaults to no allow-list "
            "(warn-only deployment posture)."
        ),
    )
    args = parser.parse_args(argv)

    try:
        behavior_map = load_behavior_map(args.behavior_map)
    except (FileNotFoundError, OSError) as exc:
        print(
            f"check-edge-type-runtime-coherence: cannot read "
            f"{args.behavior_map}: {exc}",
            file=sys.stderr,
        )
        return 2
    except json.JSONDecodeError as exc:
        print(
            f"check-edge-type-runtime-coherence: invalid JSON in "
            f"{args.behavior_map}: {exc}",
            file=sys.stderr,
        )
        return 2

    offenders = find_offenders(behavior_map)
    allowlist = load_allowlist(args.allowlist)
    remaining, allowlisted = filter_by_allowlist(offenders, allowlist)

    print(format_report(
        remaining,
        allowlisted_count=allowlisted,
        total_edges_scanned=_count_edges(behavior_map),
    ))

    return 1 if remaining else 0
