# SPDX-License-Identifier: AGPL-3.0-or-later
"""Internal Representation (IR) for code analysis.

Parsers emit Symbol and Edge objects to this IR layer. The IR is then
compiled to output views (e.g., behavior_map JSON).

Key IR Classes
--------------
- **Span**: Source location with line/column info
- **AnalysisRun**: Provenance for an analysis pass execution, including
  run_signature for cache keying and repo_fingerprint for invalidation
- **Symbol**: Code elements (functions, classes) with location, identity hashes
  (stable_id, shape_id), and quality scores
- **Edge**: Relationships between symbols with confidence, evidence tracking,
  and edge_key for deduplication across passes

Provenance Fields
-----------------
- execution_id: Unique per run (uuid)
- run_signature: Deterministic hash of (pass_id, version, config_fingerprint, toolchain)
- repo_fingerprint: Hash of git state for cache invalidation
- origin_run_signature: Links nodes/edges to their creating run's signature
"""
import hashlib
import platform
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from . import __version__

VALID_ACCESS_MODES: frozenset[str] = frozenset({"read", "write", "mutate", "delete"})
"""ADR-0015 access mode vocabulary for dataflow edges.

- read: observe value without changing it
- write: replace value entirely
- mutate: modify in place (implies read + write; ordering matters)
- delete: remove binding/key/entry (can cause subsequent reads to fail)
"""

PASS_VERSION: str = __version__
"""Canonical pass version derived from the package version.

All analyzers and linkers use this as their version string, ensuring
cache signatures correctly invalidate on release.  Single source of truth.
"""


def make_pass_id(name: str) -> str:
    """Return the canonical pass ID for an analyzer or linker.

    Analyzers: ``make_pass_id("go")`` → ``"go-v1"``
    Linkers:   ``make_pass_id("containment-linker")`` → ``"containment-linker-v1"``

    The ``-v1`` suffix is backend-neutral and provides an escape hatch
    for future versioning if an analyzer's output format changes.
    """
    return f"{name}-v1"


@dataclass
class Span:
    """Source code location with line and column info."""

    start_line: int
    end_line: int
    start_col: int
    end_col: int

    def to_dict(self) -> dict:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_col": self.start_col,
            "end_col": self.end_col,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Span":
        return cls(
            start_line=d.get("start_line", 0),
            end_line=d.get("end_line", 0),
            start_col=d.get("start_col", 0),
            end_col=d.get("end_col", 0),
        )


def _compute_run_signature(
    pass_id: str, version: str, config_fingerprint: str, toolchain: Dict[str, str]
) -> str:
    """Compute deterministic run_signature from pass configuration."""
    data = f"{pass_id}:{version}:{config_fingerprint}:{toolchain.get('name', '')}:{toolchain.get('version', '')}"
    return f"sha256:{hashlib.sha256(data.encode()).hexdigest()[:16]}"


def _get_python_toolchain() -> Dict[str, str]:
    """Get current Python runtime info for toolchain field."""
    return {
        "name": "python",
        "version": platform.python_version(),
    }


def _default_config_fingerprint() -> str:
    """Return default config fingerprint (empty config)."""
    return f"sha256:{hashlib.sha256(b'{}').hexdigest()[:16]}"


@dataclass
class AnalysisRun:
    """Provenance tracking for an analysis pass execution.

    Tracks which pass ran, when, and what it analyzed. Includes fields
    for cache keying (run_signature, repo_fingerprint) and runtime info
    (toolchain).
    """

    execution_id: str
    pass_id: str
    version: str
    run_signature: str = ""
    repo_fingerprint: Optional[str] = None
    toolchain: Dict[str, str] = field(default_factory=dict)
    config_fingerprint: str = ""
    files_analyzed: int = 0
    files_skipped: int = 0
    skipped_passes: List[Dict[str, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    started_at: str = ""
    duration_ms: int = 0

    @classmethod
    def create(
        cls,
        pass_id: str,
        version: str,
        config_fingerprint: Optional[str] = None,
        toolchain: Optional[Dict[str, str]] = None,
        repo_fingerprint: Optional[str] = None,
    ) -> "AnalysisRun":
        """Create a new AnalysisRun with a unique execution_id.

        Args:
            pass_id: Identifier for the analysis pass (e.g., 'python-ast-v1')
            version: Hypergumbo version (e.g., '0.5.0')
            config_fingerprint: Hash of effective config (defaults to empty config hash)
            toolchain: Runtime info dict (defaults to current Python runtime)
            repo_fingerprint: Hash of repo state for cache keying (optional)
        """
        tc = toolchain if toolchain is not None else _get_python_toolchain()
        cfg_fp = config_fingerprint if config_fingerprint else _default_config_fingerprint()
        run_sig = _compute_run_signature(pass_id, version, cfg_fp, tc)

        return cls(
            execution_id=f"uuid:{uuid.uuid4()}",
            pass_id=pass_id,
            version=version,
            run_signature=run_sig,
            repo_fingerprint=repo_fingerprint,
            toolchain=tc,
            config_fingerprint=cfg_fp,
            skipped_passes=[],
            warnings=[],
            started_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "run_signature": self.run_signature,
            "repo_fingerprint": self.repo_fingerprint,
            "pass": self.pass_id,
            "version": self.version,
            "toolchain": self.toolchain,
            "config_fingerprint": self.config_fingerprint,
            "files_analyzed": self.files_analyzed,
            "files_skipped": self.files_skipped,
            "skipped_passes": self.skipped_passes,
            "warnings": self.warnings,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
        }


# Supply chain tier names for JSON output
_TIER_NAMES = {
    1: "first_party",
    2: "internal_dep",
    3: "external_dep",
    4: "derived",
}


@dataclass
class Symbol:
    """A code symbol (function, class, etc.) detected by analysis.

    Attributes:
        id: Location-based identifier in format {lang}:{file}:{start}-{end}:{name}:{kind}
        name: The symbol's name (e.g., function name, class name)
        kind: Type of symbol (function, class, etc.)
        language: Programming language (python, javascript, etc.)
        path: File path where the symbol is defined
        span: Source location with lines and columns
        origin: Which analysis pass created this symbol
        origin_run_id: Unique execution ID of the analysis run
        origin_run_signature: Run signature for grouping by analyzer config
        stable_id: Semantic identity hash (survives renames/moves)
        shape_id: Structural implementation fingerprint
        canonical_name: Fully qualified name (e.g., 'mymodule.MyClass.method')
        fingerprint: Content hash of source bytes (sha256)
        quality: Score and reason dict for quality assessment
        meta: Optional metadata dict for language-specific information
        supply_chain_tier: Position in dependency graph (1=first_party, 2=internal_dep,
            3=external_dep, 4=derived). See §14 of spec.
        supply_chain_reason: Why this tier was assigned (e.g., "matches ^src/")
        cyclomatic_complexity: McCabe cyclomatic complexity (decision points + 1).
            Counts if/elif/else, for, while, except, with, and/or, match/case.
        lines_of_code: Number of source lines in the symbol body (end_line - start_line + 1).
        signature: Function/method signature string, e.g., "(x: int, y: str) -> bool".
            Only populated for callable symbols (functions, methods). None for classes, etc.
        docstring: First-line summary of doc comment (truncated to 80 chars).
        modifiers: List of semantic modifiers (e.g., ["native", "public", "static"]).
            Used by linkers for cross-language matching (e.g., JNI needs 'native').
    """

    id: str
    name: str
    kind: str
    language: str
    path: str
    span: Span
    origin: str = ""
    origin_run_id: str = ""
    origin_run_signature: Optional[str] = None
    stable_id: Optional[str] = None
    shape_id: Optional[str] = None
    canonical_name: Optional[str] = None
    fingerprint: Optional[str] = None
    quality: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None
    supply_chain_tier: int = 1  # Default to first_party
    supply_chain_reason: str = ""
    is_test_file: bool = False  # WI-rigun: independent of tier
    is_generated_file: bool = False  # WI-tizij: generated code flag
    is_exported: bool = False  # WI-zimum: public API / externally reachable
    cyclomatic_complexity: Optional[int] = None
    lines_of_code: Optional[int] = None
    signature: Optional[str] = None
    docstring: Optional[str] = None
    modifiers: List[str] = field(default_factory=list)

    # Keep line/end_line for backwards compatibility during transition
    @property
    def line(self) -> int:
        return self.span.start_line

    @property
    def end_line(self) -> int:
        return self.span.end_line

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "language": self.language,
            "path": self.path,
            "span": self.span.to_dict(),
            "origin": self.origin,
            "origin_run_id": self.origin_run_id,
            "origin_run_signature": self.origin_run_signature,
            "stable_id": self.stable_id,
            "shape_id": self.shape_id,
            "canonical_name": self.canonical_name,
            "fingerprint": self.fingerprint,
            "quality": self.quality,
            "meta": self.meta,
            "supply_chain": {
                "tier": self.supply_chain_tier,
                "tier_name": _TIER_NAMES.get(self.supply_chain_tier, "first_party"),
                "reason": self.supply_chain_reason,
                "is_test_file": self.is_test_file,
                "is_generated_file": self.is_generated_file,
                "is_exported": self.is_exported,
            },
            "cyclomatic_complexity": self.cyclomatic_complexity,
            "lines_of_code": self.lines_of_code,
            "signature": self.signature,
            "docstring": self.docstring,
            "modifiers": self.modifiers,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Symbol":
        """Reconstruct a Symbol from its dict representation (e.g., from cached results)."""
        span_data = d.get("span", {})
        supply_chain = d.get("supply_chain", {})
        return cls(
            id=d["id"],
            name=d["name"],
            kind=d["kind"],
            language=d["language"],
            path=d["path"],
            span=Span.from_dict(span_data),
            origin=d.get("origin", ""),
            origin_run_id=d.get("origin_run_id", ""),
            origin_run_signature=d.get("origin_run_signature"),
            stable_id=d.get("stable_id"),
            shape_id=d.get("shape_id"),
            canonical_name=d.get("canonical_name"),
            fingerprint=d.get("fingerprint"),
            quality=d.get("quality"),
            meta=d.get("meta"),
            supply_chain_tier=supply_chain.get("tier", 1),
            supply_chain_reason=supply_chain.get("reason", ""),
            is_test_file=supply_chain.get("is_test_file", False),
            is_generated_file=supply_chain.get("is_generated_file", False),
            is_exported=supply_chain.get("is_exported", False),
            cyclomatic_complexity=d.get("cyclomatic_complexity"),
            lines_of_code=d.get("lines_of_code"),
            signature=d.get("signature"),
            docstring=d.get("docstring"),
            modifiers=d.get("modifiers", []),
        )


def _compute_edge_key(src: str, dst: str, edge_type: str) -> str:
    """Compute canonical edge_key for deduplication across passes."""
    data = f"{edge_type}:{src}:{dst}"
    return f"edgekey:sha256:{hashlib.sha256(data.encode()).hexdigest()[:16]}"


@dataclass
class Edge:
    """A relationship between two symbols (e.g., function calls).

    Attributes:
        id: Unique identifier for this edge instance
        edge_key: Canonical identity for deduplication across passes
        src: ID of the source symbol (e.g., the caller)
        dst: ID of the target symbol (e.g., the callee)
        edge_type: Type of relationship (calls, imports, inherits, etc.)
        line: Line number where the relationship occurs
        confidence: Confidence score (0.0-1.0)
        origin: Which analysis pass created this edge
        origin_run_id: Unique execution ID of the analysis run
        origin_run_signature: Run signature for grouping
        evidence_type: Type of evidence (e.g., ast_call_direct)
        evidence_lang: Language for confidence scoring
        evidence_spans: Structured locations of evidence
        quality: Score and reason dict for quality assessment
        meta: Optional metadata dict. Dataflow edges (ADR-0015) store access_mode, dest_access_mode, and channel here.
    """

    id: str
    src: str
    dst: str
    edge_type: str
    line: int
    edge_key: Optional[str] = None
    confidence: float = 0.85
    origin: str = ""
    origin_run_id: str = ""
    origin_run_signature: Optional[str] = None
    evidence_type: str = "ast_call_direct"
    evidence_lang: Optional[str] = None
    evidence_spans: Optional[List[Dict[str, Any]]] = None
    quality: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None

    @classmethod
    def create(
        cls,
        src: str,
        dst: str,
        edge_type: str,
        line: int,
        origin: str = "",
        origin_run_id: str = "",
        evidence_type: str = "ast_call_direct",
        confidence: float = 0.85,
        evidence_lang: Optional[str] = None,
        evidence_spans: Optional[List[Dict[str, Any]]] = None,
        meta: Optional[Dict[str, Any]] = None,
        access_mode: Optional[str] = None,
        dest_access_mode: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> "Edge":
        """Create an Edge with auto-generated ID and edge_key.

        ADR-0015 dataflow kwargs (access_mode, dest_access_mode, channel)
        are merged into the meta dict when non-None.
        """
        if access_mode is not None and access_mode not in VALID_ACCESS_MODES:
            raise ValueError(
                f"access_mode={access_mode!r} not in {sorted(VALID_ACCESS_MODES)}"
            )
        if dest_access_mode is not None and dest_access_mode not in VALID_ACCESS_MODES:
            raise ValueError(
                f"dest_access_mode={dest_access_mode!r} not in {sorted(VALID_ACCESS_MODES)}"
            )
        # Merge dataflow kwargs into meta
        dataflow_meta: Dict[str, str] = {}
        if access_mode is not None:
            dataflow_meta["access_mode"] = access_mode
        if dest_access_mode is not None:
            dataflow_meta["dest_access_mode"] = dest_access_mode
        if channel is not None:
            dataflow_meta["channel"] = channel
        if dataflow_meta:
            if meta is not None:
                merged = dict(meta)
                merged.update(dataflow_meta)
                meta = merged
            else:
                meta = dataflow_meta
        # Generate deterministic edge ID from src, dst, type, AND line
        # Line is included to ensure uniqueness for multiple call sites
        edge_hash = hashlib.sha256(f"{src}:{dst}:{edge_type}:{line}".encode()).hexdigest()[:16]
        # edge_key excludes line for deduplication across passes
        edge_key = _compute_edge_key(src, dst, edge_type)
        return cls(
            id=f"edge:sha256:{edge_hash}",
            edge_key=edge_key,
            src=src,
            dst=dst,
            edge_type=edge_type,
            line=line,
            confidence=confidence,
            origin=origin,
            origin_run_id=origin_run_id,
            evidence_type=evidence_type,
            evidence_lang=evidence_lang,
            evidence_spans=evidence_spans,
            meta=meta,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        meta: Dict[str, Any] = {
            "evidence_type": self.evidence_type,
        }
        if self.evidence_lang is not None:
            meta["evidence_lang"] = self.evidence_lang
        if self.evidence_spans is not None:
            meta["evidence_spans"] = self.evidence_spans
        # Merge any additional metadata (e.g., channel for IPC edges)
        if self.meta is not None:
            meta.update(self.meta)

        return {
            "id": self.id,
            "edge_key": self.edge_key,
            "src": self.src,
            "dst": self.dst,
            "type": self.edge_type,
            "line": self.line,
            "confidence": self.confidence,
            "origin": self.origin,
            "origin_run_id": self.origin_run_id,
            "origin_run_signature": self.origin_run_signature,
            "quality": self.quality,
            "meta": meta,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        """Reconstruct an Edge from its dict representation (e.g., from cached results)."""
        meta = d.get("meta", {})
        return cls(
            id=d.get("id", ""),
            src=d.get("src", ""),
            dst=d.get("dst", ""),
            edge_type=d.get("type", "calls"),
            line=d.get("line", 0),
            edge_key=d.get("edge_key"),
            confidence=d.get("confidence", 0.85),
            origin=d.get("origin", ""),
            origin_run_id=d.get("origin_run_id", ""),
            origin_run_signature=d.get("origin_run_signature"),
            evidence_type=meta.get("evidence_type", "ast_call_direct"),
            evidence_lang=meta.get("evidence_lang"),
            evidence_spans=meta.get("evidence_spans"),
            quality=d.get("quality"),
            meta=meta,
        )


def deduplicate_edges(
    edges: list[Edge],
    *,
    remove_self_loops: bool = False,
) -> list[Edge]:
    """Deduplicate edges by edge_key (src + dst + edge_type, ignoring line).

    Multiple call sites from the same function to the same target produce
    edges with distinct ``id`` values (line-sensitive) but identical
    ``edge_key`` values (line-insensitive).  For a call graph, one edge
    per (src, dst, type) relationship is the correct model.

    When *remove_self_loops* is True, also drops edges where src == dst.
    Self-loops inflate centrality without adding useful connectivity;
    common sources include visitor patterns and name collisions.

    Preserves encounter order: the first edge for each key is kept.
    """
    seen: set[str] = set()
    result: list[Edge] = []
    for edge in edges:
        key = edge.edge_key
        # Compute edge_key on-the-fly when missing (None).  Many analyzers
        # and linkers use the Edge() constructor directly instead of
        # Edge.create(), leaving edge_key unset.  Without this fallback
        # all None-keyed edges collapse to one — silently dropping edges.
        if key is None:
            key = _compute_edge_key(edge.src, edge.dst, edge.edge_type)
        if key in seen:
            continue
        if remove_self_loops and edge.src == edge.dst:
            continue
        seen.add(key)
        result.append(edge)
    return result


def _compute_usage_context_id(
    path: str, start_line: int, context_name: str, position: str
) -> str:
    """Compute unique ID for a UsageContext."""
    data = f"{path}:{start_line}:{context_name}:{position}"
    return f"usage:sha256:{hashlib.sha256(data.encode()).hexdigest()[:16]}"


@dataclass
class UsageContext:
    """A context that gives semantic meaning to a symbol through its usage.

    Captures how a symbol is used (passed to a function, stored in a data structure,
    exported from a file) rather than how it's defined (decorators, base classes).

    This enables YAML pattern matching for call-based frameworks (Django, Express, Go)
    where route handlers are registered via function calls rather than decorators.

    Attributes:
        id: Unique identifier for this usage context
        kind: Type of usage context (call, data_value, export, macro)
        context_name: Name of the function called, var defined, file exported from, etc.
        symbol_ref: ID of the symbol being used (None if inline/anonymous handler)
        position: Where in the context the symbol appears (e.g., "args[1]", ":get", "default")
        metadata: Context-specific data (args, kwargs, receiver, etc.)
        path: File where this usage occurs
        span: Source location of the usage

    Example (Django URL pattern):
        UsageContext(
            kind="call",
            context_name="path",
            symbol_ref="python:views.py:10-15:list_users:function",
            position="args[1]",
            metadata={"args": ["/users/", "views.list_users"]},
            ...
        )
    """

    id: str
    kind: Literal["call", "data_value", "export", "macro"]
    context_name: str
    symbol_ref: Optional[str]  # None for inline handlers (lambdas, blocks)
    position: str
    metadata: Dict[str, Any]
    path: str
    span: Span

    @classmethod
    def create(
        cls,
        kind: Literal["call", "data_value", "export", "macro"],
        context_name: str,
        position: str,
        path: str,
        span: Span,
        symbol_ref: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "UsageContext":
        """Create a UsageContext with auto-generated ID."""
        ctx_id = _compute_usage_context_id(path, span.start_line, context_name, position)
        return cls(
            id=ctx_id,
            kind=kind,
            context_name=context_name,
            symbol_ref=symbol_ref,
            position=position,
            metadata=metadata if metadata is not None else {},
            path=path,
            span=span,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "kind": self.kind,
            "context_name": self.context_name,
            "symbol_ref": self.symbol_ref,
            "position": self.position,
            "metadata": self.metadata,
            "path": self.path,
            "span": self.span.to_dict(),
        }


def is_external_boundary(symbol_or_dict: Any) -> bool:
    """True iff *symbol_or_dict* is a synthetic external-boundary node.

    Boundary nodes are minted by :func:`create_boundary_nodes` for every
    edge endpoint that doesn't resolve to a real Symbol (stdlib calls,
    npm imports, third-party constructors). They carry
    ``meta.external_boundary == True`` regardless of how they were
    serialized — so this helper accepts either a live :class:`Symbol`
    instance (in-memory pipeline) or a JSON-loaded dict (consumers that
    rehydrate ``behavior_map["nodes"]`` from disk).

    Centralized here so consumers (sketch / compact / search /
    dead-code-maybe / explain) all use the same predicate; previously
    the check was duplicated ad-hoc as
    ``not (s.meta and s.meta.get("external_boundary"))``.
    """
    if isinstance(symbol_or_dict, Symbol):
        meta = symbol_or_dict.meta
    else:
        meta = symbol_or_dict.get("meta") if isinstance(symbol_or_dict, dict) else None
    return bool(meta and meta.get("external_boundary"))


# Cap for ``Edge.meta.referring_paths`` — the per-reference-site path
# slots preserved when src-side dedupe collapses N edges into one. 50 is
# arbitrary but large enough to retain attribution on virtually any
# real-world repo (the largest hypergumbo collapse target was 732 file
# externals → 1 boundary, but per-edge collapse depth is much lower).
_REFERRING_PATHS_CAP = 50


def _canonical_external_id(language: str, path: str, name: str, kind: str) -> str:
    """Canonical id for a deduplicated boundary Symbol (WI-fozoh).

    Format mirrors :func:`make_symbol_id` so downstream tooling parses
    it consistently. The path slot is preserved for kinds where it
    carries semantic identity (e.g. module name for ``kind="module"``,
    qualified path for ``kind="unresolved"``); for ``kind="file"``
    pseudo-IDs the path slot is replaced with ``<external>`` and all
    per-reference variants collapse into one canonical Symbol per
    language.
    """
    return f"{language}:{path}:0-0:{name}:{kind}"


def _canonical_external_stable_id(
    language: str, path: str, name: str, kind: str,
) -> str:
    """Stable cross-run identity for a boundary Symbol.

    Identity is a function of the dedupe key — ``(language, name, kind)``
    for collapsed file-id groups (path absent), or
    ``(language, path, name, kind)`` for full-identity externals. Two
    runs against equivalent code produce the same stable_id for the
    same logical boundary.
    """
    payload = f"external:{language}:{path}:{name}:{kind}"
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def _extract_path_slot(symbol_id: str) -> Optional[str]:
    """Extract the ``path`` slot from a ``{lang}:{path}:{span}:{name}:{kind}`` id.

    Returns ``None`` if the id doesn't have at least 5 colon-separated parts.
    Used to record the original referring-site path on edges whose src
    was collapsed by :func:`apply_external_id_remap`.
    """
    parts = symbol_id.split(":")
    if len(parts) >= 5:
        return parts[1]
    return None


def _parse_dangling_id(dangling_id: str) -> tuple[str, str, str, str]:
    """Parse ``{lang}:{path}:{span}:{name}:{kind}`` into its components.

    The path slot may itself contain colons (e.g. dart imports like
    ``dart:dart:io:0-0:module:module`` where path = ``dart:io``), so the
    parse uses the LAST three colon-separated tokens as span / name /
    kind, joining everything between ``lang`` and that suffix as the
    path.

    Returns ``(language, path, name, kind)``. Falls back to safe defaults
    when the id has fewer than 5 colon-separated parts.
    """
    parts = dangling_id.split(":")
    if len(parts) < 5:
        # Non-standard id — keep whatever we can.
        language = parts[0] if parts else "unknown"
        name = parts[-2] if len(parts) >= 2 else dangling_id
        kind = parts[-1] if parts else "external_symbol"
        return language, "<unknown>", name, kind
    language = parts[0]
    kind = parts[-1]
    name = parts[-2]
    # parts[-3] is the span; everything between lang and span is the path
    # (which may contain colons).
    path = ":".join(parts[1:-3])
    return language, path, name, kind


def _dedupe_key(
    language: str, path: str, name: str, kind: str,
) -> tuple[str, str, str, str]:
    """Compute the dedupe key for an external boundary group.

    For ``kind="file"`` pseudo-IDs (produced by ``make_file_id`` in
    every Python file's import-edge src), the path slot is a
    per-reference filesystem path with no semantic identity — collapse
    all such ids per language into one canonical "file" boundary by
    using ``"<external>"`` in place of the path. For every other kind,
    the path slot is meaningful (module name for imports, qualified
    submodule for unresolved calls, etc.) and is kept in the key so
    distinct logical externals stay distinct.
    """
    if kind == "file":
        return (language, "<external>", name, kind)
    return (language, path, name, kind)


def create_boundary_nodes(
    symbols: List[Symbol],
    edges: List[Edge],
    dependency_manifest: Any = None,
) -> tuple[List[Symbol], Dict[str, str]]:
    """Create boundary nodes for dangling edge endpoints, with cross-run identity.

    After all analyzers and linkers have run, some edges point to IDs
    that don't exist as symbols (calls to Go stdlib functions, imports
    of npm packages, references to Java standard library classes,
    every Python file's ``make_file_id`` import-edge src…). Rather than
    leaving these as dangling edges that break slice traversal, this
    function creates synthetic "boundary" nodes that mark where the
    analyzed codebase ends.

    Two universal effects (WI-fozoh):

    * **Cross-run identity.** Every boundary Symbol gets a non-null
      ``stable_id`` and ``canonical_name`` derived from its dedupe key,
      so consumers (sketch / slice / cross-run diff) can group and
      compare boundary nodes the same way ADR-0014 stable_ids work for
      first-party symbols.
    * **Targeted dedupe of file-id pseudo-symbols.** For
      ``kind="file"`` boundary ids — the per-Python-file
      ``make_file_id`` synthetic ids that are dangling because the
      module Symbol uses a different id format — all per-reference
      variants per language collapse into one canonical "file"
      boundary. Other externals (module imports, unresolved calls)
      preserve their full path-slot identity, so two distinct modules
      with the same exported name (e.g. ``urllib.request.urlopen`` vs
      ``urllib.parse.urlopen``) stay distinct boundaries.

    The structural mismatch driving file-id externals (``_make_file_id``
    in analyzers ↔ module-Symbol id format) is tracked separately and
    fixed at the producer side per the Plan B / file-id-emit-symbol
    invariant.

    Tier classification is **tier-min** across the collapsed group: if
    *any* referring site classifies as tier-2 via the dependency
    manifest, the canonical node is tier-2. One tier-2 signal means
    "this external IS declared somewhere" — we don't want a second
    tier-3 referring site to silently demote it.

    Args:
        symbols: All extracted symbols from analyzers and linkers.
        edges: All edges (after deduplication).
        dependency_manifest: Optional DependencyManifest from
            supply_chain.py. When provided, boundary nodes for
            languages with a manifest parser (go, java, kotlin, python)
            are classified tier-2 vs tier-3 based on declared deps.

    Returns:
        Tuple ``(boundary_symbols, id_remap)``.

        * ``boundary_symbols``: list of new boundary Symbols (one per
          dedupe-key group, sorted by id for determinism).
        * ``id_remap``: ``{original_dangling_id: canonical_id}`` mapping.
          Callers MUST apply this to every edge's ``src`` / ``dst`` via
          :func:`apply_external_id_remap` before serialization, or the
          graph will contain edges pointing at the original (now-absent)
          dangling ids. For most ids the remap is a no-op (canonical id
          equals original); the file-id collapse case is the one that
          actually changes ids.

        Does NOT modify the input lists.
    """
    symbol_ids = {sym.id for sym in symbols}
    # Collect unique dangling targets (both src and dst)
    dangling_ids: set = set()
    for edge in edges:
        if edge.src not in symbol_ids:
            dangling_ids.add(edge.src)
        if edge.dst not in symbol_ids:
            dangling_ids.add(edge.dst)

    if not dangling_ids:
        return [], {}

    # Group dangling ids by dedupe key. The key collapses file-id
    # pseudo-symbols per language; other kinds keep full identity.
    groups: Dict[tuple[str, str, str, str], List[str]] = {}
    for dangling_id in dangling_ids:
        language, path, name, kind = _parse_dangling_id(dangling_id)
        key = _dedupe_key(language, path, name, kind)
        groups.setdefault(key, []).append(dangling_id)

    boundary_nodes: List[Symbol] = []
    id_remap: Dict[str, str] = {}
    zero_span = Span(start_line=0, end_line=0, start_col=0, end_col=0)

    # Iterate groups in sorted order so the output is deterministic.
    for (language, key_path, name, kind), members in sorted(groups.items()):
        canonical_id = _canonical_external_id(language, key_path, name, kind)

        # Tier-min selection: if ANY referring site classifies as tier-2
        # via the manifest, the canonical node is tier-2.
        best_tier = 3
        best_reason = "unresolved external reference"
        if (
            dependency_manifest is not None
            and language in ("go", "java", "kotlin", "python")
        ):
            for member_id in members:
                _, member_path, _, _ = _parse_dangling_id(member_id)
                if not member_path or member_path == "<unknown>":
                    continue  # pragma: no cover  # defensive — generated ids all have 5 parts
                manifest_tier = dependency_manifest.classify_import(member_path)
                if manifest_tier.value < best_tier:
                    best_tier = manifest_tier.value
                    if best_tier == 2:
                        if language == "go":
                            best_reason = "direct dependency (go.mod)"
                        elif language == "python":
                            best_reason = "direct dependency (pyproject.toml)"
                        else:
                            best_reason = "direct dependency (build manifest)"
                    if best_tier == 1:  # pragma: no cover - manifests don't return tier-1
                        break

        sym = Symbol(
            id=canonical_id,
            stable_id=_canonical_external_stable_id(language, key_path, name, kind),
            canonical_name=f"{language}:{key_path}:{name}:{kind}",
            name=name,
            kind="external_symbol",
            language=language,
            path="<external>",
            span=zero_span,
            meta={"external_boundary": True},
            supply_chain_tier=best_tier,
            supply_chain_reason=best_reason,
        )
        boundary_nodes.append(sym)
        for member_id in members:
            # Only register the remap when canonicalization actually
            # changes the id — saves a no-op rewrite pass and lets
            # callers cheaply detect "nothing collapsed."
            if member_id != canonical_id:
                id_remap[member_id] = canonical_id

    return boundary_nodes, id_remap


def apply_external_id_remap(
    edges: List[Edge],
    id_remap: Dict[str, str],
) -> List[Edge]:
    """Rewrite edges' ``src`` / ``dst`` per the remap from
    :func:`create_boundary_nodes`, dedupe collapsed edges, and capture
    per-reference attribution on collisions.

    The 732 ``file``-named externals on hypergumbo self-analysis collapse
    to one canonical boundary Symbol. Every Python file's import edges
    therefore acquire the same canonical ``src`` and need to dedupe
    against each other — without preserving attribution, "which files
    import click?" becomes unanswerable from the graph alone.

    For every edge whose ``src`` is remapped, the original ``src`` id's
    path slot is captured into ``edge.meta.referring_paths`` (capped at
    :data:`_REFERRING_PATHS_CAP`). When multiple edges collapse onto the
    same ``(canonical_src, canonical_dst, edge_type)``, the
    first-encountered edge wins (matching :func:`deduplicate_edges`'s
    convention) and the colliding edges' original src paths union into
    its ``referring_paths``.

    Mutates edges in place. Returns the surviving edge list (subset of
    input).
    """
    if not id_remap:
        return edges

    seen: Dict[str, Edge] = {}
    out: List[Edge] = []
    for edge in edges:
        new_src = id_remap.get(edge.src, edge.src)
        new_dst = id_remap.get(edge.dst, edge.dst)
        orig_src_path: Optional[str] = None
        if new_src != edge.src:
            orig_src_path = _extract_path_slot(edge.src)
        edge.src = new_src
        edge.dst = new_dst
        edge.edge_key = _compute_edge_key(new_src, new_dst, edge.edge_type)

        kept = seen.get(edge.edge_key)
        if kept is None:
            if orig_src_path:
                edge.meta = dict(edge.meta or {})
                edge.meta["referring_paths"] = [orig_src_path]
            seen[edge.edge_key] = edge
            out.append(edge)
            continue

        # Collapse case — union referring_paths into the kept edge.
        if orig_src_path:
            kept.meta = dict(kept.meta or {})
            existing = list(kept.meta.get("referring_paths") or [])
            if orig_src_path not in existing and len(existing) < _REFERRING_PATHS_CAP:
                existing.append(orig_src_path)
            kept.meta["referring_paths"] = existing

    return out
