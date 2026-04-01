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
# High-risk primitives
# ---------------------------------------------------------------------------

HIGH_RISK_PRIMITIVES: frozenset[str] = frozenset({
    # Destructive filesystem
    "shutil.rmtree", "os.rmdir", "os.remove", "os.unlink",
    "pathlib.Path.unlink", "pathlib.Path.rmdir",
    # Subprocess / code execution — Python
    "subprocess.Popen", "subprocess.run", "subprocess.call",
    "subprocess.check_call", "subprocess.check_output",
    "os.system", "os.popen", "os.execv", "os.execve", "os.execvp",
    "os.execvpe", "os.execl", "os.execle", "os.execlp", "os.execlpe",
    "os.fork", "os.forkpty",
    "os.spawnl", "os.spawnle", "os.spawnlp", "os.spawnlpe",
    "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe",
    # Network outbound — Python
    "urllib.request.urlopen", "urllib.request.Request",
    "socket.socket.connect", "socket.socket.send", "socket.socket.sendall",
    # Go
    "os/exec.Command", "os/exec.CommandContext",
    # Java
    "java.lang.ProcessBuilder.start", "java.lang.Runtime.exec",
    # Rust
    "std::process::Command.spawn", "std::process::Command.output",
    "std::process::Command.status",
    # JavaScript / Node
    "child_process.exec", "child_process.execSync",
    "child_process.spawn", "child_process.spawnSync",
    # C
    "unistd.exec", "unistd.execl", "unistd.fork",
    "stdlib.system", "stdio.popen",
})


def is_high_risk(primitive_name: str) -> bool:
    """Check whether a primitive is classified as high-risk.

    High-risk primitives include destructive filesystem operations
    (rmtree, unlink), subprocess/code execution (Popen, exec*), and
    outbound network calls (urlopen, socket.send). The classification
    covers Python, Go, Java, Rust, JavaScript, and C primitives.
    """
    return primitive_name in HIGH_RISK_PRIMITIVES


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
    ambiguous_names: frozenset[str] = field(default_factory=frozenset)
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

    def lookup_with_module(
        self, name: str, module_hint: str | None = None,
    ) -> Optional[IoPrimitive]:
        """Look up a primitive with optional module context for disambiguation.

        When ``module_hint`` is provided and is not ``"external"``, filters
        short-name matches to only those whose ``module`` field is contained
        in the hint (or vice versa).  This prevents false positives like
        ``crypto/rand.Read`` matching ``net.Conn.Read``.

        Falls back to unfiltered short-name matching when:
        - ``module_hint`` is None or ``"external"`` (no module info available)
        - No filtered match is found (defensive fallback)
        """
        # Qualified-name match always wins (exact)
        hit = self._by_qualified.get(name)
        if hit is not None:
            return hit

        hits = self._by_short.get(name)
        if not hits:
            return None

        # If we have module context, filter matches
        if module_hint and module_hint != "external":
            filtered = [
                p for p in hits
                if _module_matches(p.module, module_hint)
            ]
            if filtered:
                return filtered[0]
            # No match with module filtering — this is likely NOT an IO
            # primitive (e.g., crypto/rand.Read is not net.Conn.Read)
            return None

        # No module context — fall back to first match unless ambiguous
        if self.ambiguous_names and name in self.ambiguous_names:
            return None
        return hits[0]

    def merge(self, parent: IoBoundaryCatalog) -> IoBoundaryCatalog:
        """Merge a parent catalog into this one. Self's entries take precedence.

        Used for language inheritance (e.g. Scala inherits from Java):
        Scala-specific entries override Java entries with the same qualified
        name, while Java entries not present in Scala are added.
        """
        existing_qnames = {p.qualified_name for p in self.primitives}
        merged_primitives = list(self.primitives) + [
            p for p in parent.primitives
            if p.qualified_name not in existing_qnames
        ]
        merged_ambiguous = self.ambiguous_names | parent.ambiguous_names
        return IoBoundaryCatalog(
            language=self.language,
            primitives=merged_primitives,
            ambiguous_names=merged_ambiguous,
        )

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
            "ipc_recv", "ipc_send", "env_read", "env_write",
            "subprocess", "db_read", "db_write",
            "process_send", "logging",
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

        ambiguous = frozenset(data.get("ambiguous_names", []))
        catalog = cls(
            language=language,
            primitives=primitives,
            ambiguous_names=ambiguous,
        )
        return catalog


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

_CATALOG_DIR = Path(__file__).parent / "io_primitives"

# Languages that share an IO primitive catalog.  C++ uses C stdlib IO
# functions (fopen, fread, fwrite, popen, etc.) so it falls back to the
# C catalog.  TypeScript shares the JavaScript catalog.
_CATALOG_ALIASES: dict[str, str] = {
    "cpp": "c",
    "typescript": "javascript",
    # JVM languages that lack their own catalog share the Java IO catalog
    "kotlin": "java",
    "groovy": "java",
    # Objective-C nodes have language="objective-c" but edge prefixes use "objc"
    "objective-c": "objc",
}

# Languages with their own catalog that also inherit from a parent.
# The child catalog takes precedence; parent entries fill in the gaps.
_CATALOG_PARENTS: dict[str, str] = {
    "scala": "java",
}


def load_catalog(language: str) -> IoBoundaryCatalog:
    """Load the I/O primitive catalog for a language.

    Looks for ``io_primitives/<language>.yaml`` relative to this module.
    Falls back to language aliases (e.g. cpp → c) if no exact match.
    When a language has a parent catalog (e.g. scala → java), the child
    catalog is loaded first and then merged with the parent so that
    child entries take precedence while parent entries fill in gaps.
    Returns an empty catalog if no catalog is found.
    """
    path = _CATALOG_DIR / f"{language}.yaml"
    if not path.exists():
        alias = _CATALOG_ALIASES.get(language)
        if alias:
            path = _CATALOG_DIR / f"{alias}.yaml"
    if not path.exists():
        return IoBoundaryCatalog(language=language)
    catalog = IoBoundaryCatalog.from_yaml(path)

    # Merge parent catalog if defined (e.g. scala inherits java entries)
    parent_lang = _CATALOG_PARENTS.get(language)
    if parent_lang:
        parent_path = _CATALOG_DIR / f"{parent_lang}.yaml"
        if parent_path.exists():
            parent_catalog = IoBoundaryCatalog.from_yaml(parent_path)
            catalog = catalog.merge(parent_catalog)

    return catalog


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

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict including high-risk flag."""
        return {
            "boundary": self.boundary,
            "primitive": self.primitive,
            "io_edge_src": self.io_edge_src,
            "io_edge_dst": self.io_edge_dst,
            "entry_points": self.entry_points,
            "high_risk": is_high_risk(self.primitive),
        }


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
        """Serialize to JSON-friendly dict.

        Includes per-primitive counts, per-chain detail, and a
        high-risk flag indicating whether any chain uses a high-risk
        primitive (destructive fs, subprocess, outbound network).
        """
        prim_counts: dict[str, int] = {}
        for chain in self.chains:
            prim_counts[chain.primitive] = prim_counts.get(chain.primitive, 0) + 1
        return {
            "boundary": self.boundary,
            "chain_count": len(self.chains),
            "entry_points": self.entry_points,
            "primitives_used": self.primitives_used,
            "primitive_counts": prim_counts,
            "chains": [c.to_dict() for c in self.chains],
            "has_high_risk": any(
                is_high_risk(c.primitive) for c in self.chains
            ),
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


def _trace_entry_points(
    edges: list,
    entrypoint_ids: set[str],
) -> dict[str, set[str]]:
    """Reverse-trace from IO-tagged edges back to entrypoints.

    Builds a reverse call graph (callee → callers) and performs BFS from
    each IO edge's source symbol backward through the graph until reaching
    entrypoint symbols.

    Returns a mapping from IO edge source symbol ID to the set of
    entrypoint IDs that can reach it.
    """
    # Build reverse adjacency list: dst → set of src symbols.
    # Include FFI bridge edges so entry-point traces cross language boundaries
    # (e.g., Java native method → C JNI function → fopen).
    _TRACEABLE_TYPES = frozenset({
        "calls", "instantiates", "dispatches_to", "references",
        # FFI bridge edges
        "native_bridge", "wasm_bridge", "wasm_load", "bridge_invokes",
        "cgo_bridge", "ffi_bridge",
        "ipc_calls", "ipc_event", "grpc_calls", "implements_rpc",
    })
    reverse_graph: dict[str, set[str]] = {}
    for edge in edges:
        if edge.edge_type in _TRACEABLE_TYPES:
            reverse_graph.setdefault(edge.dst, set()).add(edge.src)

    # For each IO-tagged edge, BFS backward to find reachable entrypoints
    io_sources: set[str] = set()
    for edge in edges:
        if edge.meta and edge.meta.get("io_boundary"):
            io_sources.add(edge.src)

    result: dict[str, set[str]] = {}
    for io_src in io_sources:
        reachable_eps: set[str] = set()
        visited: set[str] = set()
        queue = [io_src]
        while queue:
            current = queue.pop(0)
            if current not in visited:
                visited.add(current)
                if current in entrypoint_ids:
                    reachable_eps.add(current)
                for caller in reverse_graph.get(current, ()):
                    if caller not in visited:
                        queue.append(caller)
        result[io_src] = reachable_eps

    return result


def compute_boundary_map(
    edges: list,
    catalogs: dict[str, IoBoundaryCatalog],
    *,
    entrypoint_ids: set[str] | None = None,
) -> BoundaryMap:
    """Compute the I/O boundary map from a set of edges.

    Tags edges with I/O boundary metadata (in-place), then aggregates
    tagged edges by boundary type. When ``entrypoint_ids`` is provided,
    traces backward from each IO edge through the call graph to find
    which entrypoints can reach each IO call.

    Args:
        edges: List of Edge objects (mutated: io_boundary metadata stamped).
        catalogs: Language → IoBoundaryCatalog mapping.
        entrypoint_ids: Optional set of entrypoint symbol IDs. When
            provided, populates ``entry_points`` on each IoChain and
            BoundaryMapEntry.

    Returns:
        BoundaryMap with per-boundary-type aggregation.
    """
    tagged_count = tag_io_boundaries(edges, catalogs)

    # Reverse-trace from IO edges to entrypoints (Phase 1c)
    ep_map: dict[str, set[str]] = {}
    if entrypoint_ids:
        ep_map = _trace_entry_points(edges, entrypoint_ids)

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
        chain_eps = sorted(ep_map.get(edge.src, set()))
        chain = IoChain(
            boundary=boundary,
            primitive=primitive,
            io_edge_src=edge.src,
            io_edge_dst=edge.dst,
            entry_points=chain_eps,
        )
        by_boundary.setdefault(boundary, []).append(chain)

    # Build boundary map entries
    bmap = BoundaryMap(total_io_edges=tagged_count)
    for boundary, chains in by_boundary.items():
        entry_points_set: set[str] = set()
        primitives_set: set[str] = set()
        for chain in chains:
            primitives_set.add(chain.primitive)
            for ep in chain.entry_points:
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


def _module_matches(catalog_module: str, edge_module_hint: str) -> bool:
    """Check if a catalog entry's module matches the edge's module hint.

    Uses case-insensitive substring matching in both directions to handle
    different naming conventions:
    - Go: catalog has ``net.Conn``, edge has ``net.Conn`` → match
    - Go: catalog has ``os``, edge has ``os`` → match
    - Go: catalog has ``net.Conn``, edge has ``crypto/rand`` → no match
    - Rust: catalog has ``std::fs``, edge has ``std::fs::File`` → match
    - Java: catalog has ``java.io``, edge has ``java.io.FileInputStream`` → match
    - Swift: catalog has ``Channel``, edge has ``channel`` → match
    - Swift: catalog has ``ChannelHandlerContext``, edge has ``context`` → match
    - Swift: catalog has ``NonBlockingFileIO``, edge has ``fileIO`` → match

    Case-insensitive comparison is necessary because Swift's tree-sitter
    analyzer extracts receiver variable names (camelCase) as module hints,
    while the catalog uses PascalCase type names.
    """
    # Normalize: treat :: and / as . for uniform comparison, casefold for
    # cross-convention matching (Swift camelCase vars vs PascalCase types)
    cm = catalog_module.replace("::", ".").replace("/", ".").casefold()
    em = edge_module_hint.replace("::", ".").replace("/", ".").casefold()
    return cm in em or em in cm


def _extract_module_hint(edge_dst: str) -> str | None:
    """Extract the module hint from an edge destination symbol ID.

    For unresolved edges with format ``{lang}:{module_hint}:0-0:{name}:unresolved``,
    returns the module_hint part (2nd colon-separated field).

    For resolved edges (file paths in position 2), returns None since the
    path is not a useful module hint.
    """
    parts = edge_dst.split(":")
    if len(parts) >= 5:
        candidate = parts[1]
        # Heuristic: file paths start with / or contain .py/.java/.go etc.
        # Module hints are identifiers like "external", "net.Conn", "os"
        if candidate.startswith("/") or candidate.startswith("\\"):
            return None
        return candidate
    return None


def _extract_callee_name(edge_dst: str) -> str:
    """Extract a callable name from an edge destination symbol ID.

    Symbol IDs have the format ``language:path:span:name:kind``.  The *name*
    field may itself contain colons (e.g., Objective-C selectors like
    ``removeItemAtPath:error:``).

    Strategy: split off the *kind* (last field) from the right, then take
    everything after the first three fields (lang, path, span) as the name.
    """
    # Split off kind from the right
    last_colon = edge_dst.rfind(":")
    if last_colon < 0:
        return edge_dst
    rest = edge_dst[:last_colon]

    # rest = "lang:path:span:name_possibly_with_colons"
    # Split into at most 4 parts: lang, path, span, name(remainder)
    parts = rest.split(":", 3)
    if len(parts) >= 4:
        return parts[3]
    # Fewer fields — return the last segment (handles minimal IDs like "a:b")
    return parts[-1] if parts else edge_dst


def _resolve_ffi_catalog(
    lang: str,
    module_hint: str | None,
    catalogs: dict[str, "IoBoundaryCatalog"],
) -> tuple["IoBoundaryCatalog | None", str | None]:
    """Redirect FFI pseudo-namespace lookups to the actual target catalog.

    Go's cgo pseudo-package ``C`` produces edges like
    ``go:C:0-0:fopen:unresolved`` when calling C stdlib functions.  The
    ``go`` catalog contains Go-native IO (``os.Open``, ``net.Listen``),
    not C stdlib entries.  This function detects the ``go:C:`` prefix
    and redirects to the ``c`` catalog, dropping the module hint because
    ``"C"`` is Go's import alias, not a C header/module name.

    Returns:
        (catalog, adjusted_module_hint) — the catalog to use for lookup
        and the module hint (``None`` when the pseudo-namespace module
        is not a real module in the target language).
    """
    # Go cgo → C stdlib: go:C:0-0:<name>:unresolved
    if lang == "go" and module_hint == "C":
        return catalogs.get("c"), None

    return catalogs.get(lang), module_hint


def tag_io_boundaries(
    edges: list,
    catalogs: dict[str, IoBoundaryCatalog],
    *,
    call_types: frozenset[str] = frozenset({
        "calls", "imports",
        # FFI edges — trace I/O boundaries across language boundaries
        "wasm_bridge", "wasm_load", "bridge_invokes",
        "cgo_bridge", "ffi_bridge",
        "ipc_calls", "ipc_event",
        "grpc_calls", "implements_rpc",
    }),
) -> int:
    """Tag edges that reach I/O primitives with boundary metadata.

    For each call-type edge, extracts the callee name from the destination
    symbol ID, looks it up in the appropriate language catalog, and stamps
    ``io_boundary`` and ``io_primitive`` into ``edge.meta`` if matched.

    When the destination belongs to an FFI pseudo-namespace (e.g.,
    ``go:C:0-0:fopen:unresolved`` for cgo calls), the lookup is
    redirected to the actual target-language catalog (``c`` in this case)
    so C stdlib IO primitives are recognized even when the cgo linker
    could not resolve the call to a repo-local C symbol.

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

        callee = _extract_callee_name(edge.dst)
        module_hint = _extract_module_hint(edge.dst)

        # Try FFI pseudo-namespace redirect first (e.g., go:C: → c catalog),
        # then fall back to the primary language catalog.
        catalog, adjusted_hint = _resolve_ffi_catalog(
            lang, module_hint, catalogs,
        )
        if catalog is None:
            continue

        match = catalog.lookup_with_module(callee, adjusted_hint)
        if match is None:
            continue

        if edge.meta is None:
            edge.meta = {}
        edge.meta["io_boundary"] = match.boundary
        edge.meta["io_primitive"] = match.qualified_name
        tagged += 1

    return tagged
