# SPDX-License-Identifier: AGPL-3.0-or-later
"""I/O boundary analysis — catalog loading and edge matching (ADR-0016).

Provides a per-language catalog of I/O primitive functions/methods, each
classified by boundary type (fs_read, fs_write, net_send, net_recv,
ipc_recv, ipc_send, env_read, subprocess). Catalogs are YAML files in
the ``io_primitives/`` directory alongside this module.

How It Works
------------
1. ``load_catalog(language)`` reads the YAML for the given language and
   returns an ``IoBoundaryCatalog`` with a flat list of ``IoPrimitive``
   entries plus O(1) lookup by qualified name.
2. ``match_edge_to_primitive(catalog, callee_name)`` checks whether a
   call-edge target matches any I/O primitive, returning the match or None.
3. Downstream code (the boundary-tagging pass, Phase 1b) uses these
   matches to stamp ``io_boundary`` and ``io_primitive`` metadata onto
   edges in the graph.

Why YAML Catalogs
-----------------
The set of stdlib I/O functions per language is finite and stable — it
changes only with major language releases. Externalising the list to YAML
keeps the analysis logic independent of any single language, reuses the
pattern established by ADR-0015 dataflow YAML, and makes it easy to
add new languages or community-contributed corrections.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IoPrimitive:
    """A single I/O primitive function or method.

    Attributes:
        boundary: The I/O boundary classification (e.g. "fs_read", "net_send").
        module: The module or class path (e.g. "os", "pathlib.Path").
        name: The function or method name (e.g. "listdir", "read_text").
        kind: Either "function" or "method".
        notes: Optional human-readable notes about classification caveats.
    """

    boundary: str
    module: str
    name: str
    kind: str  # "function" or "method"
    notes: str = ""

    @property
    def qualified_name(self) -> str:
        """Full dotted name: module.name."""
        return f"{self.module}.{self.name}"


@dataclass
class IoBoundaryCatalog:
    """Loaded I/O primitive catalog for a single language.

    Provides O(1) lookup by qualified name and O(1) lookup by short name
    (unqualified). Short-name lookup may return multiple matches (e.g.
    ``open`` is both fs_read and fs_write).
    """

    language: str
    primitives: list[IoPrimitive] = field(default_factory=list)
    _by_qualified: dict[str, IoPrimitive] = field(
        default_factory=dict, repr=False,
    )
    _by_short: dict[str, list[IoPrimitive]] = field(
        default_factory=dict, repr=False,
    )

    def __post_init__(self) -> None:
        """Build lookup indices."""
        self._rebuild_indices()

    def _rebuild_indices(self) -> None:
        """Rebuild the qualified-name and short-name lookup dicts."""
        self._by_qualified.clear()
        self._by_short.clear()
        for p in self.primitives:
            # Qualified name: first one wins (shouldn't have duplicates)
            if p.qualified_name not in self._by_qualified:
                self._by_qualified[p.qualified_name] = p
            # Short name: may have multiple (e.g. open → fs_read + fs_write)
            self._by_short.setdefault(p.name, []).append(p)

    def lookup(self, name: str) -> Optional[IoPrimitive]:
        """Look up a primitive by qualified or short name.

        Returns the first match, or None if not found. For names that
        map to multiple boundaries (like ``open``), use ``lookup_all()``.
        """
        hit = self._by_qualified.get(name)
        if hit is not None:
            return hit
        hits = self._by_short.get(name)
        return hits[0] if hits else None

    def lookup_all(self, name: str) -> list[IoPrimitive]:
        """Look up all primitives matching a qualified or short name.

        Returns all matches (may be empty). Useful for names like ``open``
        that are classified under multiple boundary types.
        """
        # Qualified match is unique
        hit = self._by_qualified.get(name)
        if hit is not None:
            return [hit]
        return list(self._by_short.get(name, []))

    @classmethod
    def from_yaml(cls, path: Path) -> IoBoundaryCatalog:
        """Load a catalog from a YAML file."""
        content = path.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or {}
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> IoBoundaryCatalog:
        """Build a catalog from a parsed YAML dict."""
        language = data.get("language", "unknown")
        primitives: list[IoPrimitive] = []

        boundary_types = [
            "fs_read", "fs_write", "net_send", "net_recv",
            "ipc_recv", "ipc_send", "env_read", "subprocess",
        ]

        for boundary in boundary_types:
            entries = data.get(boundary, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                module = entry.get("module", "")
                notes = entry.get("notes", "")

                for func_name in entry.get("functions", []):
                    primitives.append(IoPrimitive(
                        boundary=boundary,
                        module=module,
                        name=func_name,
                        kind="function",
                        notes=notes,
                    ))
                for method_name in entry.get("methods", []):
                    primitives.append(IoPrimitive(
                        boundary=boundary,
                        module=module,
                        name=method_name,
                        kind="method",
                        notes=notes,
                    ))
                for attr_name in entry.get("attributes", []):
                    primitives.append(IoPrimitive(
                        boundary=boundary,
                        module=module,
                        name=attr_name,
                        kind="attribute",
                        notes=notes,
                    ))

        catalog = cls(language=language, primitives=primitives)
        return catalog


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

_CATALOG_DIR = Path(__file__).parent / "io_primitives"


def load_catalog(language: str) -> IoBoundaryCatalog:
    """Load the I/O primitive catalog for a language.

    Looks for ``io_primitives/<language>.yaml`` relative to this module.
    Returns an empty catalog if the file does not exist.
    """
    path = _CATALOG_DIR / f"{language}.yaml"
    if not path.exists():
        return IoBoundaryCatalog(language=language)
    return IoBoundaryCatalog.from_yaml(path)


# ---------------------------------------------------------------------------
# Edge matching
# ---------------------------------------------------------------------------


def match_edge_to_primitive(
    catalog: IoBoundaryCatalog,
    callee_name: str,
) -> Optional[IoPrimitive]:
    """Match a call-edge target name against the I/O primitive catalog.

    Tries qualified name first, then short (unqualified) name. Returns
    the first match or None.
    """
    return catalog.lookup(callee_name)


# ---------------------------------------------------------------------------
# Boundary map computation (ADR-0016 Phase 1c)
# ---------------------------------------------------------------------------


@dataclass
class IoChain:
    """A call chain from an entry point to an I/O boundary call.

    Attributes:
        boundary: The I/O boundary type (e.g., "fs_read").
        primitive: The matched I/O primitive qualified name.
        io_edge_src: The symbol ID of the caller of the I/O primitive.
        io_edge_dst: The symbol ID of the I/O primitive itself.
        entry_points: Set of entry-point symbol IDs that can reach this I/O call.
    """

    boundary: str
    primitive: str
    io_edge_src: str
    io_edge_dst: str
    entry_points: list[str] = field(default_factory=list)


@dataclass
class BoundaryMapEntry:
    """Aggregated boundary map for one boundary type.

    Attributes:
        boundary: The I/O boundary type.
        chains: Individual I/O chains reaching this boundary.
        entry_points: Deduplicated entry-point symbol IDs across all chains.
        primitives_used: Deduplicated I/O primitive names across all chains.
    """

    boundary: str
    chains: list[IoChain] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    primitives_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict."""
        return {
            "boundary": self.boundary,
            "chain_count": len(self.chains),
            "entry_points": self.entry_points,
            "primitives_used": self.primitives_used,
        }


@dataclass
class BoundaryMap:
    """Complete I/O boundary map for a repository.

    Attributes:
        entries: Mapping from boundary type to aggregated entry.
        total_io_edges: Total number of boundary-tagged edges found.
    """

    entries: dict[str, BoundaryMapEntry] = field(default_factory=dict)
    total_io_edges: int = 0

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict."""
        return {
            "total_io_edges": self.total_io_edges,
            "boundaries": {
                k: v.to_dict() for k, v in sorted(self.entries.items())
            },
        }


def compute_boundary_map(
    edges: list,
    catalogs: dict[str, IoBoundaryCatalog],
) -> BoundaryMap:
    """Compute the I/O boundary map from a set of edges.

    Tags edges with I/O boundary metadata (in-place), then aggregates
    tagged edges by boundary type. Entry-point tracing (reverse slice)
    is deferred to Phase 1c CLI integration — this function provides the
    per-boundary aggregation.

    Args:
        edges: List of Edge objects (mutated: io_boundary metadata stamped).
        catalogs: Language → IoBoundaryCatalog mapping.

    Returns:
        BoundaryMap with per-boundary-type aggregation.
    """
    tagged_count = tag_io_boundaries(edges, catalogs)

    # Aggregate tagged edges by boundary type
    by_boundary: dict[str, list[IoChain]] = {}
    for edge in edges:
        meta = edge.meta
        if meta is None:
            continue
        boundary = meta.get("io_boundary")
        if boundary is None:
            continue
        primitive = meta.get("io_primitive", "")
        chain = IoChain(
            boundary=boundary,
            primitive=primitive,
            io_edge_src=edge.src,
            io_edge_dst=edge.dst,
        )
        by_boundary.setdefault(boundary, []).append(chain)

    # Build boundary map entries
    bmap = BoundaryMap(total_io_edges=tagged_count)
    for boundary, chains in by_boundary.items():
        entry_points_set: set[str] = set()
        primitives_set: set[str] = set()
        for chain in chains:
            primitives_set.add(chain.primitive)
            for ep in chain.entry_points:  # pragma: no cover — populated by reverse-trace (Phase 1c CLI)
                entry_points_set.add(ep)
        bmap.entries[boundary] = BoundaryMapEntry(
            boundary=boundary,
            chains=chains,
            entry_points=sorted(entry_points_set),
            primitives_used=sorted(primitives_set),
        )

    return bmap


# ---------------------------------------------------------------------------
# Boundary-tagging pass (ADR-0016 Phase 1b)
# ---------------------------------------------------------------------------


def _extract_callee_name(edge_dst: str) -> str:
    """Extract a callable name from an edge destination symbol ID.

    Symbol IDs have the format:
        ``language:path:span:name:kind``

    We extract the ``name`` part (4th colon-separated field from the end).
    For method calls the name may be ``ClassName.method_name``.
    """
    parts = edge_dst.split(":")
    if len(parts) >= 2:
        # The name is the second-to-last field before the kind
        # Example: "python:/path/to/file.py:10-12:os.listdir:function"
        # → name = "os.listdir"
        return parts[-2] if len(parts) >= 2 else edge_dst
    return edge_dst


def tag_io_boundaries(
    edges: list,
    catalogs: dict[str, IoBoundaryCatalog],
    *,
    call_types: frozenset[str] = frozenset({
        "calls", "imports",
        # FFI edges — trace I/O boundaries across language boundaries
        "wasm_bridge", "wasm_load", "bridge_invokes",
        "ipc_calls", "ipc_event",
        "grpc_calls", "implements_rpc",
    }),
) -> int:
    """Tag edges that reach I/O primitives with boundary metadata.

    For each call-type edge, extracts the callee name from the destination
    symbol ID, looks it up in the appropriate language catalog, and stamps
    ``io_boundary`` and ``io_primitive`` into ``edge.meta`` if matched.

    Args:
        edges: List of Edge objects to scan (mutated in place).
        catalogs: Language → IoBoundaryCatalog mapping.
        call_types: Edge types to consider. Default includes calls,
            imports, and FFI edge types (wasm_bridge, ipc_calls, etc.)
            so boundary tracing crosses language boundaries.

    Returns:
        Number of edges tagged.
    """
    tagged = 0
    for edge in edges:
        if edge.edge_type not in call_types:
            continue

        # Extract language from dst ID (first colon-delimited segment)
        dst_parts = edge.dst.split(":")
        lang = dst_parts[0]

        catalog = catalogs.get(lang)
        if catalog is None:
            continue

        callee = _extract_callee_name(edge.dst)
        match = catalog.lookup(callee)
        if match is None:
            continue

        if edge.meta is None:
            edge.meta = {}
        edge.meta["io_boundary"] = match.boundary
        edge.meta["io_primitive"] = match.qualified_name
        tagged += 1

    return tagged
