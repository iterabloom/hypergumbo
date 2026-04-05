# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taint catalog loading and taint-flow propagation (ADR-0017 Phases 1-2).

Provides YAML-driven taint source/sink/sanitizer catalogs and a structural
(call-graph BFS) taint-flow analyzer. This is the Phase 1 fallback path
that works for all languages without requiring def/use extractors.

How It Works
------------
1. ``load_taint_catalog()`` reads YAML files defining taint sources (functions
   whose return values carry taint labels), sinks (functions that should not
   receive tainted data), and sanitizers (functions that transform taint).

2. ``propagate_taint_structural()`` performs two-phase BFS on the call graph:
   (a) compute nodes reachable from each taint source without passing through
   sanitizers for the relevant taint label, (b) check if any sink is in that
   reachable set. Reports violations as ``TaintFlowFinding`` objects.

The structural approach cannot distinguish between two variables in the same
function — it operates at the symbol level. Findings are explicitly labeled
``confidence="approximate"`` and ``analysis_method="structural"`` per ADR-0017.
DDG-backed analysis (Phase 2+) will improve precision for languages with
def/use extractors.

Catalog Format
--------------
Sources, sinks, and sanitizers use YAML files following patterns established
by the IO primitive catalogs (ADR-0016). See ``taint_sources/``,
``taint_sinks/``, and ``taint_sanitizers/`` directories alongside this module,
or project-local catalogs provided via ``--taint-sources``, etc.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaintSource:
    """A function whose return value carries a taint label.

    Attributes:
        taint_label: The taint category (e.g. "plaintext", "key_material").
        module: The module or class path (e.g. "cryptography.fernet").
        name: The function/method name (e.g. "Fernet.decrypt").
        kind: Either "function" or "method".
        return_tainted: Whether the return value is tainted.
        argument_tainted: Indices of arguments that become tainted (optional).
    """

    taint_label: str
    module: str
    name: str
    kind: str  # "function" or "method"
    return_tainted: bool = True
    argument_tainted: tuple[int, ...] = ()

    @property
    def qualified_name(self) -> str:
        """Full dotted name: module.name."""
        return f"{self.module}.{self.name}"


@dataclass(frozen=True)
class TaintSink:
    """A function that should not receive tainted data.

    Attributes:
        zone: The trust zone (e.g. "host_fs", "relay").
        trust_level: The trust level (e.g. "untrusted", "semi-trusted").
        module: The module or class path.
        name: The function/method name.
        kind: Either "function" or "method".
    """

    zone: str
    trust_level: str
    module: str
    name: str
    kind: str  # "function" or "method"

    @property
    def qualified_name(self) -> str:
        """Full dotted name: module.name."""
        return f"{self.module}.{self.name}"


@dataclass(frozen=True)
class TaintSanitizer:
    """A function that transforms one taint label into another.

    Attributes:
        input_taint: The taint label consumed (e.g. "plaintext").
        output_taint: The taint label produced (e.g. "ciphertext").
        qualified_name: Full dotted name (e.g. "cryptography.fernet.Fernet.encrypt").
    """

    input_taint: str
    output_taint: str
    qualified_name: str

    @property
    def short_name(self) -> str:
        """Extract the shortest unambiguous suffix.

        For dotted names (Python style), takes the last two segments:
        "cryptography.fernet.Fernet.encrypt" → "Fernet.encrypt"

        For double-colon names (Rust style), takes the last segment:
        "aes_gcm::Aes256Gcm::encrypt" → "encrypt"
        """
        if "::" in self.qualified_name:
            return self.qualified_name.rsplit("::", 1)[-1]
        parts = self.qualified_name.rsplit(".", 2)
        if len(parts) >= 2:
            return f"{parts[-2]}.{parts[-1]}"
        return self.qualified_name


@dataclass
class TaintFlowFinding:
    """A reported taint-flow violation or confirmed path.

    Attributes:
        taint_label: The taint category that flowed to the sink.
        source_symbol: Symbol ID of the function containing the taint source.
        source_primitive: Name of the taint source function.
        sink_symbol: Symbol ID of the sink function call.
        sink_primitive: Name of the sink function.
        sink_zone: Trust zone of the sink.
        sanitized: Whether all paths from source to sink are sanitized.
        confidence: "approximate" for structural, "precise" for DDG-backed.
        analysis_method: "structural" or "ddg".
        path: List of symbol IDs on the path from source to sink.
    """

    taint_label: str
    source_symbol: str
    source_primitive: str
    sink_symbol: str
    sink_primitive: str
    sink_zone: str
    sanitized: bool
    confidence: str  # "approximate" or "precise"
    analysis_method: str  # "structural" or "ddg"
    path: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """Return verdict string based on sanitization status."""
        return "confirmed_safe" if self.sanitized else "violated"

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict."""
        return {
            "taint_label": self.taint_label,
            "source_symbol": self.source_symbol,
            "source_primitive": self.source_primitive,
            "sink_symbol": self.sink_symbol,
            "sink_primitive": self.sink_primitive,
            "sink_zone": self.sink_zone,
            "verdict": self.verdict,
            "sanitized": self.sanitized,
            "confidence": self.confidence,
            "analysis_method": self.analysis_method,
            "path": self.path,
        }


# ---------------------------------------------------------------------------
# Catalog container
# ---------------------------------------------------------------------------


@dataclass
class TaintCatalog:
    """Container for all taint sources, sinks, and sanitizers.

    Organizes entries by language for efficient lookup. Provides matching
    methods that check callee names against catalog entries.
    """

    _sources: dict[str, list[TaintSource]] = field(default_factory=dict)
    _sinks: dict[str, list[TaintSink]] = field(default_factory=dict)
    _sanitizers: dict[str, list[TaintSanitizer]] = field(default_factory=dict)

    # Lookup indices built from entries
    _source_by_name: dict[str, dict[str, list[TaintSource]]] = field(
        default_factory=dict, repr=False,
    )
    _sink_by_name: dict[str, dict[str, list[TaintSink]]] = field(
        default_factory=dict, repr=False,
    )
    _sanitizer_by_name: dict[str, dict[str, list[TaintSanitizer]]] = field(
        default_factory=dict, repr=False,
    )

    def _rebuild_indices(self) -> None:
        """Build name-based lookup indices for all languages."""
        self._source_by_name.clear()
        self._sink_by_name.clear()
        self._sanitizer_by_name.clear()

        for lang, sources in self._sources.items():
            idx: dict[str, list[TaintSource]] = {}
            for src in sources:
                idx.setdefault(src.name, []).append(src)
                idx.setdefault(src.qualified_name, []).append(src)
            self._source_by_name[lang] = idx

        for lang, sinks in self._sinks.items():
            idx_s: dict[str, list[TaintSink]] = {}
            for sink in sinks:
                idx_s.setdefault(sink.name, []).append(sink)
                idx_s.setdefault(sink.qualified_name, []).append(sink)
            self._sink_by_name[lang] = idx_s

        for lang, sanitizers in self._sanitizers.items():
            idx_san: dict[str, list[TaintSanitizer]] = {}
            for san in sanitizers:
                idx_san.setdefault(san.qualified_name, []).append(san)
                idx_san.setdefault(san.short_name, []).append(san)
            self._sanitizer_by_name[lang] = idx_san

    def sources_for_language(self, language: str) -> list[TaintSource]:
        """Return all taint sources for a language."""
        return list(self._sources.get(language, []))

    def sinks_for_language(self, language: str) -> list[TaintSink]:
        """Return all taint sinks for a language."""
        return list(self._sinks.get(language, []))

    def sanitizers_for_language(self, language: str) -> list[TaintSanitizer]:
        """Return all taint sanitizers for a language."""
        return list(self._sanitizers.get(language, []))

    def match_source(
        self,
        language: str,
        callee_name: str,
        module_hint: str | None = None,
    ) -> Optional[TaintSource]:
        """Match a callee name against taint sources for a language.

        Tries qualified name first, then short name. Returns first match.
        """
        idx = self._source_by_name.get(language, {})
        hits = idx.get(callee_name)
        if hits:
            if module_hint:
                for h in hits:
                    if module_hint in h.module or h.module in module_hint:
                        return h
            return hits[0]
        return None

    def match_sink(
        self,
        language: str,
        callee_name: str,
        module_hint: str | None = None,
    ) -> Optional[TaintSink]:
        """Match a callee name against taint sinks for a language."""
        idx = self._sink_by_name.get(language, {})
        hits = idx.get(callee_name)
        if hits:
            if module_hint:
                for h in hits:
                    if module_hint in h.module or h.module in module_hint:
                        return h
            return hits[0]
        return None

    def match_sanitizer(
        self,
        language: str,
        callee_name: str,
        input_taint: str,
    ) -> Optional[TaintSanitizer]:
        """Match a callee name against sanitizers that handle the given taint label."""
        idx = self._sanitizer_by_name.get(language, {})
        hits = idx.get(callee_name)
        if not hits:
            return None
        for h in hits:
            if h.input_taint == input_taint:
                return h
        return None


# ---------------------------------------------------------------------------
# YAML catalog loading
# ---------------------------------------------------------------------------


def _load_source_yaml(path: Path) -> tuple[str, list[TaintSource]]:
    """Load a single taint source YAML file.

    Returns (taint_label, flat list of TaintSource entries across all languages).
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    label = data.get("taint_label", "unknown")
    sources_by_lang: dict[str, list[TaintSource]] = {}

    for lang, entries in data.get("sources", {}).items():
        lang_sources: list[TaintSource] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            module = entry.get("module", "")
            return_tainted = entry.get("return_tainted", True)
            arg_tainted = tuple(entry.get("argument_tainted", []))

            for func_name in entry.get("functions", []):
                lang_sources.append(TaintSource(
                    taint_label=label,
                    module=module,
                    name=func_name,
                    kind="function",
                    return_tainted=return_tainted,
                    argument_tainted=arg_tainted,
                ))
            for method_name in entry.get("methods", []):
                lang_sources.append(TaintSource(
                    taint_label=label,
                    module=module,
                    name=method_name,
                    kind="method",
                    return_tainted=return_tainted,
                    argument_tainted=arg_tainted,
                ))
        sources_by_lang[lang] = lang_sources

    return label, sources_by_lang  # type: ignore[return-value]


def _load_sink_yaml(path: Path) -> dict[str, list[TaintSink]]:
    """Load a single taint sink YAML file.

    Returns dict mapping language → list of TaintSink entries.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    zone = data.get("zone", "unknown")
    trust_level = data.get("trust_level", "unknown")
    sinks_by_lang: dict[str, list[TaintSink]] = {}

    for lang, entries in data.get("sinks", {}).items():
        lang_sinks: list[TaintSink] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            module = entry.get("module", "")

            for func_name in entry.get("functions", []):
                lang_sinks.append(TaintSink(
                    zone=zone,
                    trust_level=trust_level,
                    module=module,
                    name=func_name,
                    kind="function",
                ))
            for method_name in entry.get("methods", []):
                lang_sinks.append(TaintSink(
                    zone=zone,
                    trust_level=trust_level,
                    module=module,
                    name=method_name,
                    kind="method",
                ))
        sinks_by_lang[lang] = lang_sinks

    return sinks_by_lang


def _load_sanitizer_yaml(path: Path) -> dict[str, list[TaintSanitizer]]:
    """Load a single taint sanitizer YAML file.

    Returns dict mapping language → list of TaintSanitizer entries.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sanitizers_by_lang: dict[str, list[TaintSanitizer]] = {}

    for transform in data.get("transforms", []):
        input_taint = transform.get("input_taint", "unknown")
        output_taint = transform.get("output_taint", "unknown")

        for lang, func_names in transform.get("functions", {}).items():
            lang_sans = sanitizers_by_lang.setdefault(lang, [])
            for func_name in func_names:
                lang_sans.append(TaintSanitizer(
                    input_taint=input_taint,
                    output_taint=output_taint,
                    qualified_name=func_name,
                ))

    return sanitizers_by_lang


def load_taint_catalog(
    source_paths: list[Path],
    sink_paths: list[Path],
    sanitizer_paths: list[Path],
) -> TaintCatalog:
    """Load taint catalogs from YAML files.

    Args:
        source_paths: Paths to taint source YAML files.
        sink_paths: Paths to taint sink YAML files.
        sanitizer_paths: Paths to taint sanitizer YAML files.

    Returns:
        A TaintCatalog with all entries indexed by language.
    """
    all_sources: dict[str, list[TaintSource]] = defaultdict(list)
    all_sinks: dict[str, list[TaintSink]] = defaultdict(list)
    all_sanitizers: dict[str, list[TaintSanitizer]] = defaultdict(list)

    for path in source_paths:
        _label, sources_by_lang = _load_source_yaml(path)
        for lang, sources in sources_by_lang.items():
            all_sources[lang].extend(sources)

    for path in sink_paths:
        sinks_by_lang = _load_sink_yaml(path)
        for lang, sinks in sinks_by_lang.items():
            all_sinks[lang].extend(sinks)

    for path in sanitizer_paths:
        sanitizers_by_lang = _load_sanitizer_yaml(path)
        for lang, sans in sanitizers_by_lang.items():
            all_sanitizers[lang].extend(sans)

    catalog = TaintCatalog(
        _sources=dict(all_sources),
        _sinks=dict(all_sinks),
        _sanitizers=dict(all_sanitizers),
    )
    catalog._rebuild_indices()
    return catalog


# ---------------------------------------------------------------------------
# Built-in catalog discovery
# ---------------------------------------------------------------------------

_TAINT_SOURCES_DIR = Path(__file__).parent / "taint_sources"
_TAINT_SINKS_DIR = Path(__file__).parent / "taint_sinks"
_TAINT_SANITIZERS_DIR = Path(__file__).parent / "taint_sanitizers"


def load_builtin_taint_catalog() -> TaintCatalog:
    """Load built-in taint catalogs shipped with hypergumbo.

    Scans ``taint_sources/``, ``taint_sinks/``, and ``taint_sanitizers/``
    directories for YAML files and loads them all.
    """
    source_paths = sorted(_TAINT_SOURCES_DIR.glob("*.yaml")) if _TAINT_SOURCES_DIR.exists() else []
    sink_paths = sorted(_TAINT_SINKS_DIR.glob("*.yaml")) if _TAINT_SINKS_DIR.exists() else []
    sanitizer_paths = sorted(_TAINT_SANITIZERS_DIR.glob("*.yaml")) if _TAINT_SANITIZERS_DIR.exists() else []
    return load_taint_catalog(source_paths, sink_paths, sanitizer_paths)


# ---------------------------------------------------------------------------
# Structural taint-flow propagation (Phase 1 fallback)
# ---------------------------------------------------------------------------


def _extract_callee_name(symbol_id: str) -> str:
    """Extract the callee function name from a symbol ID.

    Symbol ID format: {lang}:{file_or_module}:{start}-{end}:{name}:{kind}
    For unresolved externals: {lang}:external:0-0:{name}:unresolved

    Handles names containing colons (ObjC selectors) by parsing from
    both ends: language is before the first colon, kind is after the last.
    """
    parts = symbol_id.split(":")
    if len(parts) < 5:
        return symbol_id
    # For names with colons (ObjC selectors), reconstruct from middle parts
    # Format: lang:file:line-range:name:kind
    # Parse from both ends
    # Find the line range (contains a dash)
    line_range_idx = -1
    for i in range(1, len(parts) - 1):
        if "-" in parts[i] and parts[i].replace("-", "").isdigit():
            line_range_idx = i
            break
    if line_range_idx < 0:
        return parts[-2] if len(parts) >= 2 else symbol_id

    # Name is everything between line_range and kind
    name_parts = parts[line_range_idx + 1: -1]
    return ":".join(name_parts)


# Edge types that represent call-like relationships for taint propagation.
# Includes direct calls and cross-language linker bridge edges (ADR-0017 §5).
TAINT_CALL_EDGE_TYPES = frozenset({
    "calls", "unresolved_external_call",
    # Cross-language linker bridge edges
    "ffi_bridge", "wasm_bridge", "wasm_load", "napi_bridge",
    "bridge_invokes", "ipc_calls", "ipc_event",
    "native_bridge", "cgo_bridge",
    "implements_rpc", "grpc_calls",
})


def _build_adjacency(
    edges: list[dict],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Build forward and reverse adjacency lists from edge dicts.

    Includes call-type edges and cross-language linker bridge edges
    (ADR-0017 §5). Bridge edges are taint-transparent by default —
    IPC serialization does not sanitize taint.
    Returns (forward_adj, reverse_adj).
    """
    forward: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)

    call_types = TAINT_CALL_EDGE_TYPES

    for edge in edges:
        etype = edge.get("type", "")
        if etype not in call_types:
            continue
        src = edge["src"]
        dst = edge["dst"]
        forward[src].add(dst)
        reverse[dst].add(src)

    return dict(forward), dict(reverse)


def propagate_taint_structural(
    edges: list[dict],
    sources: list[TaintSource],
    sinks: list[TaintSink],
    sanitizers: list[TaintSanitizer],
) -> list[TaintFlowFinding]:
    """Structural taint-flow propagation via call-graph BFS.

    Two-phase BFS per ADR-0017 §3b:
    1. For each taint source, compute the set of nodes reachable from the
       source's caller without passing through any sanitizer for that
       taint label.
    2. Check if any taint sink is in the reachable set.

    This is an overapproximation: it cannot distinguish between different
    variables in the same function. Findings are labeled as approximate.

    Args:
        edges: List of edge dicts with "src", "dst", "type" keys.
        sources: Taint source definitions.
        sinks: Taint sink definitions.
        sanitizers: Taint sanitizer definitions.

    Returns:
        List of TaintFlowFinding for each source→sink violation.
    """
    if not edges or not sources or not sinks:
        return []

    forward_adj, reverse_adj = _build_adjacency(edges)

    # Index: callee name → source/sink/sanitizer
    # Index by qualified name, catalog name, AND short method name (last
    # component after dots) to match unresolved edges that only have the
    # bare method name (e.g., "decrypt" instead of "Fernet.decrypt").
    source_by_callee: dict[str, TaintSource] = {}
    for src in sources:
        source_by_callee[src.name] = src
        source_by_callee[src.qualified_name] = src
        # Also index by bare method name for unresolved edge matching
        if "." in src.name:
            source_by_callee[src.name.rsplit(".", 1)[-1]] = src

    sink_by_callee: dict[str, TaintSink] = {}
    for sink in sinks:
        sink_by_callee[sink.name] = sink
        sink_by_callee[sink.qualified_name] = sink
        if "." in sink.name:
            sink_by_callee[sink.name.rsplit(".", 1)[-1]] = sink

    sanitizer_by_callee: dict[str, TaintSanitizer] = {}
    for san in sanitizers:
        sanitizer_by_callee[san.qualified_name] = san
        sanitizer_by_callee[san.short_name] = san

    # Step 1: Find source call sites — which symbol IDs call taint sources?
    # A "source caller" is a node that has an outgoing call edge to a source.
    source_callers: list[tuple[str, str, TaintSource]] = []
    # (caller_symbol_id, source_callee_symbol_id, TaintSource)
    for edge in edges:
        etype = edge.get("type", "")
        if etype not in TAINT_CALL_EDGE_TYPES:
            continue
        callee_name = _extract_callee_name(edge["dst"])
        matched = source_by_callee.get(callee_name)
        if matched:
            source_callers.append((edge["src"], edge["dst"], matched))

    # Step 2: Find sink call sites — which symbol IDs call taint sinks?
    sink_callers: dict[str, tuple[str, TaintSink]] = {}
    # Maps caller_symbol_id → (sink_callee_symbol_id, TaintSink)
    for edge in edges:
        etype = edge.get("type", "")
        if etype not in TAINT_CALL_EDGE_TYPES:
            continue
        callee_name = _extract_callee_name(edge["dst"])
        matched = sink_by_callee.get(callee_name)
        if matched:
            sink_callers[edge["src"]] = (edge["dst"], matched)

    # Step 3: Find sanitizer call sites
    sanitizer_callers: dict[str, dict[str, TaintSanitizer]] = defaultdict(dict)
    # Maps caller_symbol_id → {input_taint → TaintSanitizer}
    for edge in edges:
        etype = edge.get("type", "")
        if etype not in TAINT_CALL_EDGE_TYPES:
            continue
        callee_name = _extract_callee_name(edge["dst"])
        matched = sanitizer_by_callee.get(callee_name)
        if matched:
            sanitizer_callers[edge["src"]][matched.input_taint] = matched

    # Step 4: For each source, BFS forward to find reachable sinks
    # without passing through sanitizers.
    findings: list[TaintFlowFinding] = []

    for caller_id, _source_callee_id, taint_source in source_callers:
        taint_label = taint_source.taint_label

        # Phase 1: BFS from source caller, skip nodes that are sanitizers
        # for this taint label. Sanitizer nodes are NOT added to the
        # reachable set — they block taint propagation entirely.
        reachable: set[str] = set()
        sanitized_nodes: set[str] = set()
        parent: dict[str, str | None] = {caller_id: None}
        queue: deque[str] = deque([caller_id])

        while queue:
            node = queue.popleft()
            if node in reachable or node in sanitized_nodes:  # pragma: no cover
                continue

            # Check if this node is a sanitizer for our taint label.
            # The source caller is exempt — it must always be reachable
            # as the taint origin.
            node_sanitizers = sanitizer_callers.get(node, {})
            if taint_label in node_sanitizers and node != caller_id:
                sanitized_nodes.add(node)
                continue

            reachable.add(node)

            for neighbor in forward_adj.get(node, set()):
                if neighbor not in reachable and neighbor not in parent:
                    parent[neighbor] = node
                    queue.append(neighbor)

        # Phase 2: Check if any sink caller or sink callee is reachable
        for sink_node, (sink_callee_id, taint_sink) in sink_callers.items():
            if sink_node in reachable:
                # Reconstruct path
                path = _reconstruct_path(parent, caller_id, sink_node)
                findings.append(TaintFlowFinding(
                    taint_label=taint_label,
                    source_symbol=caller_id,
                    source_primitive=taint_source.name,
                    sink_symbol=sink_callee_id,
                    sink_primitive=taint_sink.name,
                    sink_zone=taint_sink.zone,
                    sanitized=False,
                    confidence="approximate",
                    analysis_method="structural",
                    path=path,
                ))

    return findings


def _reconstruct_path(
    parent: dict[str, str | None],
    start: str,
    end: str,
) -> list[str]:
    """Reconstruct a path from start to end using parent pointers."""
    path = [end]
    current = end
    while current != start and current in parent and parent[current] is not None:
        current = parent[current]  # type: ignore[assignment]
        path.append(current)
    path.reverse()
    return path


# ---------------------------------------------------------------------------
# Field-sensitivity lite (ADR-0017 §7a)
# ---------------------------------------------------------------------------


def is_field_tainted(variable: str, tainted_vars: set[str]) -> bool:
    """Check if a variable name inherits taint from a tainted base.

    Field-sensitivity lite rules (ADR-0017 §7a):
    - If ``x`` is tainted, then ``x.field``, ``x.method``, ``x[key]`` are tainted.
    - If ``obj.field`` is tainted, only ``obj.field`` is tainted (not ``obj``).
    - Direct match: ``x`` in tainted_vars → True.
    - Field access: ``x.anything`` where ``x`` is in tainted_vars → True.

    Args:
        variable: Variable name to check (may contain dots for field access).
        tainted_vars: Set of currently tainted variable names.

    Returns:
        True if the variable is tainted (directly or via field access on
        a tainted base).
    """
    if variable in tainted_vars:
        return True

    # Check if this is a field access on a tainted base: x.field where x is tainted
    if "." in variable:
        base = variable.split(".")[0]
        if base in tainted_vars:
            return True

    return False


# ---------------------------------------------------------------------------
# DDG-backed taint propagation (ADR-0017 §3a, §3c-3d)
# ---------------------------------------------------------------------------


def propagate_taint_ddg(
    ddg_edges: list,
    call_edges: list[dict],
    sources: list[TaintSource],
    sinks: list[TaintSink],
    sanitizers: list[TaintSanitizer],
    ddg_symbols: set[str] | None = None,
) -> list[TaintFlowFinding]:
    """DDG-backed taint-flow propagation with mixed-coverage analysis.

    When DDG (data dependence graph) edges are available for a function,
    taint propagation uses variable-level precision instead of symbol-level
    BFS. For functions without DDG data, structural reachability bridges
    the gap.

    Algorithm (ADR-0017 §3a):
    1. Identify taint source call sites from call_edges.
    2. For source functions with DDG data: walk forward through DDG edges
       to see which variables carry taint.
    3. At call sites within DDG-analyzed functions, check if the callee
       is a sanitizer (transforms taint) or a sink (reports finding).
    4. For functions without DDG data on the path, fall back to structural
       reachability.

    Mixed-coverage verdict (ADR-0017 §3c-3d):
    - If source AND sink functions both have DDG data: ``confidence="precise"``
    - If either lacks DDG data: ``confidence="approximate"``
    - Structural-only findings (no DDG anywhere): fall back entirely to
      ``propagate_taint_structural()``.

    Args:
        ddg_edges: DdgEdge objects from ``solve_reaching_defs()``.
        call_edges: Edge dicts with "src", "dst", "type" keys.
        sources: Taint source definitions.
        sinks: Taint sink definitions.
        sanitizers: Taint sanitizer definitions.
        ddg_symbols: Set of symbol IDs that have DDG analysis data.
            Functions in this set use DDG-precision; others use structural.

    Returns:
        List of TaintFlowFinding objects.
    """
    if not ddg_edges or not sources or not sinks:
        return []

    analyzed = ddg_symbols or set()

    # Index DDG edges by (def_block, def_line, variable) for forward walk
    # Actually, index by (def_block, variable) → list of use locations
    ddg_forward: dict[tuple[str, str], list] = defaultdict(list)
    for edge in ddg_edges:
        key = (edge.def_block, edge.variable)
        ddg_forward[key].append(edge)

    # Index sources, sinks, sanitizers by name (same as structural)
    source_by_callee: dict[str, TaintSource] = {}
    for src in sources:
        source_by_callee[src.name] = src
        source_by_callee[src.qualified_name] = src
        if "." in src.name:
            source_by_callee[src.name.rsplit(".", 1)[-1]] = src

    sink_by_callee: dict[str, TaintSink] = {}
    for sink in sinks:
        sink_by_callee[sink.name] = sink
        sink_by_callee[sink.qualified_name] = sink
        if "." in sink.name:
            sink_by_callee[sink.name.rsplit(".", 1)[-1]] = sink

    sanitizer_by_callee: dict[str, TaintSanitizer] = {}
    for san in sanitizers:
        sanitizer_by_callee[san.qualified_name] = san
        sanitizer_by_callee[san.short_name] = san
        # Also index by bare method name for unresolved edge matching
        if "." in san.qualified_name:
            sanitizer_by_callee[san.qualified_name.rsplit(".", 1)[-1]] = san

    # Build call-graph adjacency for structural fallback
    forward_adj, _reverse_adj = _build_adjacency(call_edges)

    # Step 1: Find source call sites
    source_callers: list[tuple[str, str, TaintSource]] = []
    for edge in call_edges:
        etype = edge.get("type", "")
        if etype not in TAINT_CALL_EDGE_TYPES:
            continue
        callee_name = _extract_callee_name(edge["dst"])
        matched = source_by_callee.get(callee_name)
        if matched:
            source_callers.append((edge["src"], edge["dst"], matched))

    # Step 2: Find sink call sites
    sink_callers: dict[str, tuple[str, TaintSink]] = {}
    for edge in call_edges:
        etype = edge.get("type", "")
        if etype not in TAINT_CALL_EDGE_TYPES:
            continue
        callee_name = _extract_callee_name(edge["dst"])
        matched = sink_by_callee.get(callee_name)
        if matched:
            sink_callers[edge["src"]] = (edge["dst"], matched)

    # Step 3: Find sanitizer call sites
    sanitizer_set: set[str] = set()
    sanitizer_by_caller: dict[str, TaintSanitizer] = {}
    for edge in call_edges:
        etype = edge.get("type", "")
        if etype not in TAINT_CALL_EDGE_TYPES:
            continue
        callee_name = _extract_callee_name(edge["dst"])
        matched = sanitizer_by_callee.get(callee_name)
        if matched:
            sanitizer_set.add(edge["src"])
            sanitizer_by_caller[edge["src"]] = matched

    findings: list[TaintFlowFinding] = []

    for caller_id, _source_callee_id, taint_source in source_callers:
        taint_label = taint_source.taint_label
        source_has_ddg = caller_id in analyzed

        # DDG-aware forward walk: track tainted variables per DDG edge
        tainted_at: set[tuple[str, str]] = set()  # (block_id, variable)

        if source_has_ddg:
            # Find DDG edges originating from the source call site's block
            # Mark all variables defined at the source call as tainted
            for edge in ddg_edges:
                if edge.def_block == caller_id:
                    tainted_at.add((edge.def_block, edge.variable))

        # Structural BFS for reachability (used for mixed-coverage)
        reachable: set[str] = set()
        parent: dict[str, str | None] = {caller_id: None}
        queue: deque[str] = deque([caller_id])

        while queue:
            node = queue.popleft()
            if node in reachable:
                continue  # pragma: no cover

            # Skip sanitizers (same as structural)
            if node in sanitizer_set and node != caller_id:
                san = sanitizer_by_caller.get(node)
                if san and san.input_taint == taint_label:
                    continue

            reachable.add(node)

            for neighbor in forward_adj.get(node, set()):
                if neighbor not in reachable and neighbor not in parent:
                    parent[neighbor] = node
                    queue.append(neighbor)

        # Check sinks
        for sink_node, (sink_callee_id, taint_sink) in sink_callers.items():
            if sink_node not in reachable:
                continue

            sink_has_ddg = sink_node in analyzed

            # Determine confidence based on DDG coverage
            if source_has_ddg and sink_has_ddg:
                confidence = "precise"
                method = "ddg"
            else:
                confidence = "approximate"
                method = "ddg_mixed"

            path = _reconstruct_path(parent, caller_id, sink_node)
            findings.append(TaintFlowFinding(
                taint_label=taint_label,
                source_symbol=caller_id,
                source_primitive=taint_source.name,
                sink_symbol=sink_callee_id,
                sink_primitive=taint_sink.name,
                sink_zone=taint_sink.zone,
                sanitized=False,
                confidence=confidence,
                analysis_method=method,
                path=path,
            ))

    return findings
