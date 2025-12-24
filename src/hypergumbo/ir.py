"""Internal Representation (IR) for code analysis.

Parsers emit Symbol and Edge objects to this IR layer. The IR is then
compiled to output views (e.g., behavior_map JSON).
"""
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


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


@dataclass
class AnalysisRun:
    """Provenance tracking for an analysis pass execution.

    Tracks which pass ran, when, and what it analyzed.
    """

    execution_id: str
    pass_id: str
    version: str
    files_analyzed: int = 0
    files_skipped: int = 0
    started_at: str = ""
    duration_ms: int = 0

    @classmethod
    def create(cls, pass_id: str, version: str) -> "AnalysisRun":
        """Create a new AnalysisRun with a unique execution_id."""
        return cls(
            execution_id=f"uuid:{uuid.uuid4()}",
            pass_id=pass_id,
            version=version,
            started_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "pass": self.pass_id,
            "version": self.version,
            "files_analyzed": self.files_analyzed,
            "files_skipped": self.files_skipped,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
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
        }


@dataclass
class Edge:
    """A relationship between two symbols (e.g., function calls).

    Attributes:
        id: Unique identifier for this edge
        src: ID of the source symbol (e.g., the caller)
        dst: ID of the target symbol (e.g., the callee)
        edge_type: Type of relationship (calls, imports, inherits, etc.)
        line: Line number where the relationship occurs
        confidence: Confidence score (0.0-1.0)
        origin: Which analysis pass created this edge
        origin_run_id: Unique execution ID of the analysis run
        evidence_type: Type of evidence (e.g., ast_call_direct)
    """

    id: str
    src: str
    dst: str
    edge_type: str
    line: int
    confidence: float = 0.85
    origin: str = ""
    origin_run_id: str = ""
    origin_run_signature: Optional[str] = None
    evidence_type: str = "ast_call_direct"

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
    ) -> "Edge":
        """Create an Edge with auto-generated ID."""
        # Generate deterministic edge ID from src, dst, type
        edge_hash = hashlib.sha256(f"{src}:{dst}:{edge_type}".encode()).hexdigest()[:16]
        return cls(
            id=f"edge:sha256:{edge_hash}",
            src=src,
            dst=dst,
            edge_type=edge_type,
            line=line,
            confidence=confidence,
            origin=origin,
            origin_run_id=origin_run_id,
            evidence_type=evidence_type,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "src": self.src,
            "dst": self.dst,
            "type": self.edge_type,
            "line": self.line,
            "confidence": self.confidence,
            "origin": self.origin,
            "origin_run_id": self.origin_run_id,
            "origin_run_signature": self.origin_run_signature,
            "meta": {
                "evidence_type": self.evidence_type,
            },
        }
