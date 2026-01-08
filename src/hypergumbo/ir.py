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
from typing import Any, Dict, List, Optional


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
            3=external_dep, 4=derived). See §8.6 of spec.
        supply_chain_reason: Why this tier was assigned (e.g., "matches ^src/")
        cyclomatic_complexity: McCabe cyclomatic complexity (decision points + 1).
            Counts if/elif/else, for, while, except, with, and/or, match/case.
        lines_of_code: Number of source lines in the symbol body (end_line - start_line + 1).
        signature: Function/method signature string, e.g., "(x: int, y: str) -> bool".
            Only populated for callable symbols (functions, methods). None for classes, etc.
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
    cyclomatic_complexity: Optional[int] = None
    lines_of_code: Optional[int] = None
    signature: Optional[str] = None
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
            },
            "cyclomatic_complexity": self.cyclomatic_complexity,
            "lines_of_code": self.lines_of_code,
            "signature": self.signature,
            "modifiers": self.modifiers,
        }


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
    ) -> "Edge":
        """Create an Edge with auto-generated ID and edge_key."""
        # Generate deterministic edge ID from src, dst, type
        edge_hash = hashlib.sha256(f"{src}:{dst}:{edge_type}".encode()).hexdigest()[:16]
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
