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


def create_boundary_nodes(
    symbols: List[Symbol],
    edges: List[Edge],
) -> List[Symbol]:
    """Create lightweight boundary nodes for dangling edge endpoints.

    After all analyzers and linkers have run, some edges point to IDs that
    don't exist as symbols (e.g., calls to Go stdlib functions, imports of
    npm packages, or references to Java standard library classes).  Rather
    than leaving these as dangling edges that break slice traversal, this
    function creates synthetic "boundary" nodes that mark where the analyzed
    codebase ends and external dependencies begin.

    Boundary nodes are:
    - kind="external_symbol" (generic) or inferred from the ID format
    - supply_chain_tier=3 (external dependency)
    - Flagged with meta.external_boundary=True
    - Zero-span (no source location)

    Args:
        symbols: All extracted symbols from analyzers and linkers.
        edges: All edges (after deduplication).

    Returns:
        List of new boundary Symbol objects to extend the symbol list.
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
        return []

    boundary_nodes: List[Symbol] = []
    zero_span = Span(start_line=0, end_line=0, start_col=0, end_col=0)

    for dangling_id in sorted(dangling_ids):
        # Parse the ID to extract language and name.
        # Common formats:
        #   {lang}:unresolved:0-0:{name}:unresolved
        #   {lang}:external:0-0:{name}:unresolved
        #   {lang}:{path}:0-0:{name}:{kind}
        #   {lang}:{path}:{start}-{end}:{name}:{kind}  (rare edge case)
        parts = dangling_id.split(":")
        language = parts[0] if parts else "unknown"
        # Extract name: second-to-last part in the colon-separated ID
        name = parts[-2] if len(parts) >= 2 else dangling_id

        sym = Symbol(
            id=dangling_id,
            name=name,
            kind="external_symbol",
            language=language,
            path="<external>",
            span=zero_span,
            meta={"external_boundary": True},
            supply_chain_tier=3,
            supply_chain_reason="unresolved external reference",
        )
        boundary_nodes.append(sym)

    return boundary_nodes
