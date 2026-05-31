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
  and edge_key for deduplication across passes. Edges carry a structured
  ``dst_ref: Optional[ExternalRef]`` sibling alongside the legacy ``dst``
  colon-encoded id; consumers prefer ``dst_ref`` and fall back to
  colon-splitting ``dst`` for pre-0.7.2 cached JSON.
- **ExternalRef**: Frozen ``(lang, module_path, name)`` triple naming a
  call target outside the producer's translation unit. Aliased imports
  bind ``name`` to the imported symbol, not the local alias.
- **UsageContext**: Per-call-site discrimination for resolved call edges
  (e.g., direct vs reflective, decorator-wrapped, framework-mediated).

Provenance Fields
-----------------
- execution_id: Unique per run (uuid)
- run_signature: Deterministic hash of (pass_id, version, config_fingerprint, toolchain)
- repo_fingerprint: Hash of git state for cache invalidation
- origin_run_signature: *Removed in 0.9.x (WI-gapin); never stamped by any producer.*
"""
import hashlib
import inspect
import platform
import types
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

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

LEGACY_DESERIALIZED_SENTINEL: str = "<legacy-deserialized>"
"""WI-higap deserialization sentinel.

``Edge.__post_init__`` hard-raises on empty ``origin`` / ``origin_run_id``,
which protects fresh-construction paths from silently dropping provenance.
But legacy behavior-map JSON predating WI-higap producer fixes carries empty
strings for these fields on the 67 production sites that hadn't yet been
migrated. ``Edge.from_dict`` swaps in this sentinel during deserialization
so loading old artifacts off disk (caches, ``hypergumbo explain``, sketch
comparisons) doesn't crash.

A property test asserts that production hypergumbo runs never emit this
sentinel — it surfaces only when reading older data, never on the
producer side.
"""


def make_pass_id(name: str) -> str:
    """Return the canonical pass ID for an analyzer or linker.

    Analyzers: ``make_pass_id("go")`` → ``"go"``
    Linkers:   ``make_pass_id("containment-linker")`` → ``"containment-linker"``

    INV-morag PR 2 dropped the legacy ``-v1`` suffix. Pass identity now
    comes from a stable opaque name (the registration ``name`` argument);
    versioning lives in :data:`AnalysisRun.pass_version` (a code-hash via
    :func:`compute_pass_version`); backend identity lives in
    :data:`RegisteredAnalyzer.backend` / :data:`RegisteredLinker.backend`;
    display labels live in ``pass_label``. ``make_pass_id`` is preserved
    as the canonical accessor so any future format change has one
    well-known migration point.
    """
    return name


def compute_pass_version(target: types.ModuleType | Callable[..., Any]) -> str:
    """Compute a stable per-pass version derived from the pass module source.

    Args:
        target: A module or a callable whose module is hashed. When given a
                callable, the callable's defining module is hashed (so
                hashing a registration site's analyzer function yields the
                same result as hashing the module that defines it).

    Returns:
        A string of the form ``"sha256:<64 hex chars>"`` that changes
        whenever the pass module's source code changes, and does NOT change
        when unrelated package code (e.g., a sibling analyzer in a different
        module, or a docstring edit elsewhere) is bumped.

    Rationale (INV-morag option A):
        The legacy pass-ID suffix ``-v1`` was a fake-versioning artifact —
        it bumped with the package release whether or not the pass logic
        changed, so caches and reproducibility comparisons couldn't tell
        "this analyzer's behavior changed" from "this analyzer's package
        version bumped." Hashing the module source replaces that fake
        signal with a real one.

        Module-level hashing was chosen over function-level for two reasons:
        (1) most analyzers depend on helper functions and module-level
        constants in the same file; (2) hashing just the registered function
        misses changes in those helpers, leading to stale cache hits.
        Cross-module helper changes (e.g., to ``analyze.base``) are still
        missed — those are covered by the surrounding package version,
        which lives in ``AnalysisRun.version``.
    """
    if inspect.ismodule(target):
        module = target
    else:
        module = inspect.getmodule(target)
        if module is None:  # pragma: no cover — defensive
            raise ValueError(  # pragma: no cover
                f"Cannot resolve module for {target!r} — pass_version "
                "requires a module-bound callable."
            )
    source = inspect.getsource(module)
    return "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


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

    execution_id: str  # axis: identity
    pass_id: str  # axis: pass-id
    version: str  # axis: identity
    run_signature: str = ""  # axis: identity
    repo_fingerprint: Optional[str] = None  # axis: identity
    toolchain: Dict[str, str] = field(default_factory=dict)
    config_fingerprint: str = ""  # axis: identity
    files_analyzed: int = 0
    files_skipped: int = 0
    skipped_passes: List[Dict[str, str]] = field(default_factory=list)
    failed_files: List[Dict[str, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    started_at: str = ""  # axis: free-text — ISO-8601 UTC timestamp; consumers display, never branch on the value.
    duration_ms: int = 0
    # Code-hash of the pass module (via compute_pass_version). Analyzers
    # set it from self.pass_version; linkers are stamped by _stamp_pass_version
    # in run_all_linkers. Distinct from ``version`` (the package version).
    pass_version: str = ""  # axis: identity

    def __post_init__(self) -> None:
        if not self.config_fingerprint:
            self.config_fingerprint = _default_config_fingerprint()

    def record_failed_file(self, path: str, reason: str) -> None:
        """Record a per-file failure for later drain into limits.failed_files.

        The `analyzer` field on the resulting FailedFile is auto-stamped from
        self.pass_id at drain time (see all_analyzers.collect_analyzer_result),
        so producer sites only need to supply path + reason — they cannot get
        the analyzer name wrong by accident.
        """
        self.failed_files.append({"path": path, "reason": reason})

    @classmethod
    def create(  # nosec B107 — pass_version is a code-hash, not a password; bandit B107 false-positives on any "pass*" name with default ""
        cls,
        pass_id: str,
        version: str,
        config_fingerprint: Optional[str] = None,
        toolchain: Optional[Dict[str, str]] = None,
        repo_fingerprint: Optional[str] = None,
        pass_version: str = "",
    ) -> "AnalysisRun":
        """Create a new AnalysisRun with a unique execution_id.

        Args:
            pass_id: Identifier for the analysis pass (e.g., 'python-ast-v1')
            version: Hypergumbo package version (``PASS_VERSION``). All
                producers pass the same value (INV-kohat).
            config_fingerprint: Hash of effective config (defaults to empty config hash)
            toolchain: Runtime info dict (defaults to current Python runtime)
            repo_fingerprint: Hash of repo state for cache keying (optional)
            pass_version: Code-hash of the pass module (via
                :func:`compute_pass_version`). Analyzers set this from
                ``self.pass_version``; linkers are stamped centrally by
                ``_stamp_pass_version`` in ``run_all_linkers``.
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
            pass_version=pass_version,
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
            "failed_files": self.failed_files,
            "warnings": self.warnings,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "pass_version": self.pass_version,
        }


# Supply chain tier names for JSON output
_TIER_NAMES = {
    1: "first_party",
    2: "internal_dep",
    3: "external_dep",
    4: "derived",
}


def _normalize_origin(raw: object) -> List[str]:
    """Coerce scalar/list/missing origin to list[str] (INV-jidat migration)."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        return [raw] if raw else []
    return []  # pragma: no cover


@dataclass
class Symbol:
    """A code symbol (function, class, etc.) detected by analysis.

    Attributes:
        id: Location-based identifier in format {lang}:{file}:{start}-{end}:{name}:{kind}
        name: The symbol's name (e.g., function name, class name)
        kind: Type of symbol (function, class, etc.)
        language: Programming language (python, javascript, etc.). Optional per
            ADR-0031 — Symbols representing source-code declarations carry the
            host language; synthetic-stand-in Symbols emitted by linkers for
            protocol/framework patterns (Kafka topics, WASM modules, IPC
            channels, etc.) leave this ``None`` and populate ``protocol_origin``
            instead. Pre-ADR-0031 emits will continue passing a string for the
            full ADR-0031 §"Phase 1 Producer migration" window.
        path: File path where the symbol is defined
        span: Source location with lines and columns
        origin: Provenance list (INV-jidat). Each element is a pass ID that
            contributed to this Symbol's existence, ordered chronologically
            (originating pass first). Single-element lists are the common case.
            Auto-normalized from scalar str for backward compat.
        origin_run_id: Unique execution ID of the analysis run
        stable_id: Semantic identity hash (survives renames/moves)
        shape_id: Structural implementation fingerprint
        canonical_name: Set only when ``name`` is unqualified but a
            fully-qualified path is known (e.g., proto RPCs, nested capnp
            messages, niche-language symbols). For mainstream-analyzer
            languages where ``name`` already encodes the parent
            (Python's ``ClassName.method``, Java's ``Class.method``, etc.),
            this field is deliberately ``None`` and consumers should fall
            back to ``name`` for fully-qualified identifiers. Populated by
            niche-language analyzers (nix, r_lang, hlsl, asm, capnp, ada,
            fish, verilog, powershell, css, wgsl), the yjs_crdt and
            wasm_bindgen linkers, and the external-boundary synthesis path.
        fingerprint: Content hash of source bytes (sha256)
        quality: Score and reason dict for quality assessment
        meta: Optional metadata dict for language-specific information
        supply_chain_tier: Position in dependency graph (1=first_party, 2=internal_dep,
            3=external_dep, 4=derived). See §14 of spec.
        supply_chain_reason: Why this tier was assigned (e.g., "matches ^src/")
        is_test_file: True if the file holds test code. Independent
            of tier — co-located test files can be tier 1.
        is_example_file: True if the file is example/demo/sample/tutorial code.
            Set when the path matches an EXAMPLE_PATTERN.
        is_config_file: True if the file is a dependency/build manifest such as
            ``pyproject.toml`` / ``package.json`` / ``Cargo.toml``. Within
            tier 2, ``is_test_file`` / ``is_example_file`` / ``is_config_file``
            are mutually exclusive — at most one is True per Symbol.
        is_generated_file: True if the file is generated code. Independent of
            the role flags above.
        is_exported: True if the symbol is part of the package's public API.
        cyclomatic_complexity: McCabe cyclomatic complexity (decision points + 1).
            Counts if/elif/else, for, while, except, with, and/or, match/case.
        lines_of_code: Number of source lines in the symbol body (end_line - start_line + 1).
        signature: Function/method signature string, e.g., "(x: int, y: str) -> bool".
            Only populated for callable symbols (functions, methods). None for classes, etc.
        docstring: First-line summary of doc comment (truncated to 80 chars).
        modifiers: List of semantic modifiers (e.g., ["native", "public", "static"]).
            Used by linkers for cross-language matching (e.g., JNI needs 'native').
        discovery_language: ADR-0031 typed sibling field. Names the host source
            language where the linker discovered the pattern that produced this
            Symbol. Populated by Class-B linker emits (synthetic stand-ins
            discovered in real source files). ``None`` for real-source
            declarations emitted by analyzers (``language`` already names that
            information). Shares the ``language`` axis catalog with
            ``Symbol.language``; the cross-language-detection consumer sites in
            ``event_sourcing.py`` / ``database_query.py`` / ``message_queue.py``
            / ``graphql_resolver.py`` read this rather than ``language``.
        protocol_origin: ADR-0031 typed sibling field. Names the protocol or
            framework family (kafka, websocket, ipc, wasm, openapi, grpc,
            graphql, etc.) for synthetic stand-ins emitted by linkers fabricating
            protocol identity from source patterns. Catalog at
            :mod:`hypergumbo_core.protocol_origins`. ``None`` for real-source
            declarations and for synthetic stand-ins that don't belong to a
            recognized protocol family.
    """

    id: str  # axis: identity
    name: str  # axis: free-text — language identifier from source; consumers display/store/lookup, never branch on the value itself.
    kind: str  # axis: symbol-kind
    language: Optional[str]  # axis: language
    path: str  # axis: free-text — filesystem path; consumers display/sort/group, never branch on the value itself.
    span: Span
    origin: List[str] = field(default_factory=list)  # axis: pass-id
    origin_run_id: str = ""  # axis: identity
    stable_id: Optional[str] = None  # axis: identity
    shape_id: Optional[str] = None  # axis: identity
    canonical_name: Optional[str] = None  # axis: free-text — fully-qualified name from source; consumers display, never branch on the value itself.
    fingerprint: Optional[str] = None  # axis: identity
    quality: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None
    supply_chain_tier: int = 1  # Default to first_party
    supply_chain_reason: str = ""  # axis: free-text — natural-language explanation of the assigned tier; consumers display, never branch on the value itself.
    is_test_file: bool = False  # WI-rigun: independent of tier
    is_example_file: bool = False  # WI-jobuj: example/demo/sample/tutorial code
    is_config_file: bool = False  # WI-jobuj: dependency/build manifest
    is_generated_file: bool = False  # WI-tizij: generated code flag
    is_exported: bool = False  # WI-zimum: public API / externally reachable
    cyclomatic_complexity: Optional[int] = None
    lines_of_code: Optional[int] = None
    signature: Optional[str] = None  # axis: free-text — callable signature string in source-language grammar; consumers display, never branch on the value itself.
    docstring: Optional[str] = None  # axis: free-text — natural-language summary from the source comment; consumers display/log/hash, never branch on the value itself.
    modifiers: List[str] = field(default_factory=list)
    discovery_language: Optional[str] = None  # axis: language
    protocol_origin: Optional[str] = None  # axis: protocol-origin

    def __post_init__(self) -> None:
        if isinstance(self.origin, str):
            self.origin = [self.origin] if self.origin else []

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
                "is_example_file": self.is_example_file,
                "is_config_file": self.is_config_file,
                "is_generated_file": self.is_generated_file,
                "is_exported": self.is_exported,
            },
            "cyclomatic_complexity": self.cyclomatic_complexity,
            "lines_of_code": self.lines_of_code,
            "signature": self.signature,
            "docstring": self.docstring,
            "modifiers": self.modifiers,
            "discovery_language": self.discovery_language,
            "protocol_origin": self.protocol_origin,
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
            origin=_normalize_origin(d.get("origin", "")),
            origin_run_id=d.get("origin_run_id", ""),
            stable_id=d.get("stable_id"),
            shape_id=d.get("shape_id"),
            canonical_name=d.get("canonical_name"),
            fingerprint=d.get("fingerprint"),
            quality=d.get("quality"),
            meta=d.get("meta"),
            supply_chain_tier=supply_chain.get("tier", 1),
            supply_chain_reason=supply_chain.get("reason", ""),
            is_test_file=supply_chain.get("is_test_file", False),
            is_example_file=supply_chain.get("is_example_file", False),
            is_config_file=supply_chain.get("is_config_file", False),
            is_generated_file=supply_chain.get("is_generated_file", False),
            is_exported=supply_chain.get("is_exported", False),
            cyclomatic_complexity=d.get("cyclomatic_complexity"),
            lines_of_code=d.get("lines_of_code"),
            signature=d.get("signature"),
            docstring=d.get("docstring"),
            modifiers=d.get("modifiers", []),
            discovery_language=d.get("discovery_language"),
            protocol_origin=d.get("protocol_origin"),
        )


def _compute_edge_key(src: str, dst: str, edge_type: str) -> str:
    """Compute canonical edge_key for deduplication across passes."""
    data = f"{edge_type}:{src}:{dst}"
    return f"edgekey:sha256:{hashlib.sha256(data.encode()).hexdigest()[:16]}"


@dataclass(frozen=True)
class ExternalRef:
    """Structured identity for an external-target edge endpoint (WI-tihup).

    Replaces the per-analyzer string-formatted ``Edge.dst`` for edges that
    point at symbols outside the analyzed repo (stdlib, dependencies,
    unresolved externals). Each analyzer composes one of these directly
    rather than building a colon-delimited identity string with its own
    convention; consumers query the structured fields rather than parsing
    the legacy string. The legacy ``Edge.dst`` string is still populated
    alongside ``Edge.dst_ref`` for back-compat with the ~34 consumer
    sites that haven't migrated.

    Fields:
        lang: Language identifier (``"python"``, ``"rust"``, ``"go"``, ...).
        module_path: Canonical module path in the language's import
            vocabulary (e.g. ``"urllib.request"``, ``"std::fs"``,
            ``"node:fs/promises"``, ``"java.util.Collections"``). Use
            the value that would appear in a clean ``from X import Y``
            (or equivalent) statement — NOT the in-scope alias.
        name: Canonical name of the imported symbol AT ITS DEFINITION
            site (e.g. ``"urlopen"``, ``"read_to_string"``,
            ``"singletonList"``). For aliased imports
            (``from X import Y as Z`` or ``use ... as alias``), this is
            ``Y``, not ``Z`` — the alias is a property of the call site,
            not of the target.
    """

    lang: str  # axis: language
    module_path: str  # axis: free-text — module import path in source-language grammar; consumers display/lookup, never branch on the value itself.
    name: str  # axis: free-text — symbol name at the definition site; consumers display/lookup, never branch on the value itself.

    def to_dict(self) -> Dict[str, str]:
        """Nested-dict form for JSON serialization."""
        return {"lang": self.lang, "module_path": self.module_path, "name": self.name}

    @classmethod
    def from_dict(cls, d: Dict[str, str]) -> "ExternalRef":
        """Reconstruct from the nested-dict form."""
        return cls(lang=d["lang"], module_path=d["module_path"], name=d["name"])


def format_legacy_dst(ref: ExternalRef) -> str:
    """Uniform 5-seg legacy ``Edge.dst`` string built from an ``ExternalRef``.

    Shape: ``{lang}:{module_path}:0-0:{name}:unresolved``. Every analyzer
    composes its external-target legacy dst from this helper so the
    format is uniform across languages (closes the WI-mafik audit's
    "Rust 6-seg outlier" and "Java class-embedded-in-module" anomalies
    by routing every producer through one builder).
    """
    return f"{ref.lang}:{ref.module_path}:0-0:{ref.name}:unresolved"


# RCT-pinned surface — see tests/test_rct_public_api_pinned.py before changing field names, types, or defaults.
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
        origin: Pass IDs that contributed to this edge (INV-jidat). Auto-normalized from scalar str.
        origin_run_id: Unique execution ID of the analysis run
        evidence_type: Type of evidence (e.g., ast_call_direct)
        evidence_lang: Language for confidence scoring
        evidence_spans: Structured locations of evidence
        is_resolved: Whether the dst symbol was resolved at analysis time. Default True (the ~90% case); set to False for unresolved external targets per ADR-0028.
        dst_ref: Structured identity for the dst endpoint, populated when the dst points at an external symbol (stdlib / dependency / unresolved external). Canonical source of truth for external dsts — the legacy `dst` string is built from the same `ExternalRef` and stays populated alongside for back-compat. None for in-repo dsts whose `dst` is a real Symbol ID.
        derived_from: Symbol (or Edge) IDs the producer consumed to construct this Edge (INV-rukor). Populated by linkers; None for analyzer-originated edges.
        quality: Score and reason dict for quality assessment
        meta: Optional metadata dict. Dataflow edges (ADR-0015) store access_mode, dest_access_mode, and channel here.
    """

    id: str  # axis: identity
    src: str  # axis: identity
    dst: str  # axis: identity
    edge_type: str  # axis: edge-type
    line: int
    edge_key: Optional[str] = None  # axis: identity
    confidence: float = 0.85
    origin: List[str] = field(default_factory=list)  # axis: pass-id
    origin_run_id: str = ""  # axis: identity
    evidence_type: str = "ast_call_direct"  # axis: evidence-type
    evidence_lang: Optional[str] = None  # axis: language
    evidence_spans: Optional[List[Dict[str, Any]]] = None
    is_resolved: bool = True
    dst_ref: Optional[ExternalRef] = None
    derived_from: Optional[List[str]] = None  # axis: identity
    quality: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if isinstance(self.origin, str):
            self.origin = [self.origin] if self.origin else []
        if not self.origin:
            raise ValueError(
                f"Edge.origin must be non-empty (edge_type={self.edge_type!r}, "
                f"src={self.src!r}, dst={self.dst!r}). Producers must stamp "
                "their pass_id; see WI-higap and the existing AnalysisRun "
                "pattern in linkers/analyzers.",
            )
        if not self.origin_run_id:
            raise ValueError(
                f"Edge.origin_run_id must be non-empty (origin={self.origin!r}, "
                f"edge_type={self.edge_type!r}). Stamp from AnalysisRun.create()"
                "'s execution_id at the producer; see WI-higap.",
            )

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
        is_resolved: bool = True,
        dst_ref: Optional[ExternalRef] = None,
        derived_from: Optional[List[str]] = None,
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
            is_resolved=is_resolved,
            dst_ref=dst_ref,
            derived_from=derived_from,
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

        out: Dict[str, Any] = {
            "id": self.id,
            "edge_key": self.edge_key,
            "src": self.src,
            "dst": self.dst,
            "type": self.edge_type,
            "line": self.line,
            "confidence": self.confidence,
            "origin": self.origin,
            "origin_run_id": self.origin_run_id,
            "is_resolved": self.is_resolved,
            "quality": self.quality,
            "meta": meta,
        }
        if self.dst_ref is not None:
            out["dst_ref"] = self.dst_ref.to_dict()
        if self.derived_from is not None:
            out["derived_from"] = self.derived_from
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        """Reconstruct an Edge from its dict representation (e.g., from cached results).

        WI-higap: ``__post_init__`` hard-raises on empty ``origin`` /
        ``origin_run_id``. Deserialization of legacy behavior-map JSON that
        predates WI-higap-era producer fixes must not crash — we inject a
        sentinel so the Edge is constructable. A property test
        (``tests/test_edge_provenance_invariant.py``) asserts that
        production hypergumbo runs never *emit* the sentinel; it appears
        only when reading older artifacts off disk.
        """
        meta = d.get("meta", {})
        dst_ref_raw = d.get("dst_ref")
        return cls(
            id=d.get("id", ""),
            src=d.get("src", ""),
            dst=d.get("dst", ""),
            edge_type=d.get("type", "calls"),
            line=d.get("line", 0),
            edge_key=d.get("edge_key"),
            confidence=d.get("confidence", 0.85),
            origin=_normalize_origin(d.get("origin")) or [LEGACY_DESERIALIZED_SENTINEL],
            origin_run_id=d.get("origin_run_id") or LEGACY_DESERIALIZED_SENTINEL,
            evidence_type=meta.get("evidence_type", "ast_call_direct"),
            evidence_lang=meta.get("evidence_lang"),
            evidence_spans=meta.get("evidence_spans"),
            is_resolved=d.get("is_resolved", True),
            dst_ref=ExternalRef.from_dict(dst_ref_raw) if dst_ref_raw else None,
            derived_from=d.get("derived_from"),
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
        kind: How the surrounding source code references this symbol — either
            as a syntactic construct (``call``, ``macro``) or as a semantic
            role (``data_value``, ``export``). The field currently mixes two
            axes; the union is small enough (4 values) to be tractable, but a
            fifth value that doesn't fit either axis should trigger a split
            into separate fields (e.g., ``usage_construct`` + ``usage_role``).
            Re-evaluation triggers: (a) a fifth value that fits neither
            construct nor role; (b) a consumer needing per-axis filtering;
            (c) a bug where the single picked value hides a cross-axis signal
            a consumer needs. See the WI-gatuh-ruvar audit thread.
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

    id: str  # axis: identity
    kind: Literal["call", "data_value", "export", "macro"]  # axis: bounded-enum
    context_name: str  # axis: free-text — name of the function call or export name from source; consumers display/lookup, never branch on the value itself.
    symbol_ref: Optional[str]  # axis: identity  (None for inline handlers — lambdas, blocks)
    position: str  # axis: free-text — positional descriptor like "args[1]", ":get", "default"; consumer-facing display string parsed by humans, not branched on by code.
    metadata: Dict[str, Any]
    path: str  # axis: free-text — filesystem path; consumers display/sort/group, never branch on the value itself.
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


_SYNTHETIC_SPAN = "0-0"


def validate_symbol_id_format(symbol_id: str) -> Optional[str]:
    """Validate a symbol id against the dual-shape format spec.

    Returns ``None`` if the id is well-formed, or a string describing
    the first violation. Used by callers that want to assert IDs they
    receive are well-formed (load-time checks) and by property tests
    that exercise analyzer output.

    The two valid shapes are documented in ``docs/hypergumbo-spec.md``
    §6 Identity field semantics:

    - **File-path shape** ``{lang}:{file}:{N-M}:{name}:{kind}`` — slot 2
      is a literal repo-relative path. Hyphens, slashes, and arbitrary
      directory names are all permitted because they reflect on-disk
      reality. Span is non-``0-0`` (real ``start-end`` lines, or the
      whole-file sentinel ``1-1``).
    - **Module-hint shape** ``{lang}:{module_hint}:0-0:{name}:{kind}``
      — slot 2 is a dotted-module-form qualifier in the language's
      import vocabulary. Span is ``0-0``.

    A ``0-0``-span id whose slot-2 qualifier carries filesystem
    segments (``packages.``, ``.src.``) or — for Python — an embedded
    hyphen indicates an analyzer derivation bug: the producer fell
    through from "resolve to a real source root" to "stringify the
    file path as if it were a module name." This is the WI-davan bug
    class, and is the only class of malformation this validator
    rejects; other ID irregularities pass through silently.

    Slot 2 may itself contain colons (e.g. ``dart:dart:io:0-0:…``
    where the path is ``dart:io``); the parse uses the trailing
    span/name/kind triple to identify the boundary, matching
    :func:`_parse_dangling_id`.
    """
    parts = symbol_id.split(":")
    if len(parts) < 5:
        return None
    span = parts[-3]
    if span != _SYNTHETIC_SPAN:
        return None
    language = parts[0]
    slot2 = ":".join(parts[1:-3])
    if "packages." in slot2 or ".src." in slot2:
        return (
            f"module-hint qualifier {slot2!r} in {symbol_id!r} contains "
            f"filesystem segments ('packages.' or '.src.') — derivation bug "
            f"(see WI-davan)"
        )
    if language == "python" and "-" in slot2:
        return (
            f"python module-hint qualifier {slot2!r} in {symbol_id!r} contains "
            f"a hyphen — invalid Python identifier (see WI-davan)"
        )
    return None


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
    # Collect unique dangling targets (both src and dst). Also build a
    # ``dangling_id → ExternalRef`` lookup so the parsing step below can
    # prefer the WI-tihup structured form over the legacy colon-split
    # heuristic. Producers that populate ``Edge.dst_ref`` short-circuit
    # the parsing-bug class behind the historical 6-seg Rust outlier
    # (``rust:external:0-0:fs::read_to_string:unresolved`` parsed as
    # ``path="external:0-0:fs"`` and re-emitted with a fabricated extra
    # ``0-0`` slot).
    dangling_ids: set = set()
    dangling_refs: Dict[str, ExternalRef] = {}
    for edge in edges:
        if edge.src not in symbol_ids:
            dangling_ids.add(edge.src)
        if edge.dst not in symbol_ids:
            dangling_ids.add(edge.dst)
            if edge.dst_ref is not None:
                # First writer wins — multiple edges can share a dst id
                # but the ref is identity, so any consistent ref is fine.
                dangling_refs.setdefault(edge.dst, edge.dst_ref)

    if not dangling_ids:
        return [], {}

    # Group dangling ids by dedupe key. The key collapses file-id
    # pseudo-symbols per language; other kinds keep full identity.
    groups: Dict[tuple[str, str, str, str], List[str]] = {}
    for dangling_id in dangling_ids:
        ref = dangling_refs.get(dangling_id)
        if ref is not None:
            # WI-tihup: structured ref bypasses the colon-split heuristic.
            # ``kind`` is fixed at "unresolved" for ExternalRef-bearing
            # edges (the producer convention), which matches the dst's
            # kind slot for the canonical 5-seg shape.
            language, path, name, kind = (
                ref.lang, ref.module_path, ref.name, "unresolved",
            )
        else:
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
