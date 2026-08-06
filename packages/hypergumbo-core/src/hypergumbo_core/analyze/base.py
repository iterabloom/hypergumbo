# SPDX-License-Identifier: AGPL-3.0-or-later
"""Base classes and utilities for language analyzers.

This module provides shared infrastructure for all language analyzers,
eliminating duplication across the ~128 analyzer files spread across
the four ``hypergumbo-lang-*`` packages.

Shared Components
-----------------
- **AnalysisResult**: Universal result type returned by all analyzers
- **FileAnalysis**: Intermediate per-file analysis result
- **Tree-sitter helpers**: node_text, find_child_by_type, find_child_by_field
- **ID generation**: make_symbol_id, make_file_id
- **Availability checking**: is_grammar_available

Why This Design
---------------
Previously, each analyzer duplicated these components. This led to:
- ~100+ copies of identical dataclasses
- Inconsistent helper implementations
- High maintenance burden when adding new analyzers

Now, analyzers import from this module and focus only on
language-specific parsing logic.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re as _re
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Dict, Iterator, Optional

from ..dataflow import annotate_dataflow, get_dataflow_config
from ..discovery import find_files
from ..paths import normalize_path
from ..ir import (
    PASS_VERSION, AnalysisRun, Edge, ExternalRef, Span, Symbol, UsageContext,
    compute_config_fingerprint, compute_pass_version, make_pass_id,
)
from ..symbol_resolution import NameResolver

# ---------------------------------------------------------------------------
# Memory safety: abort analysis before OOM crashes the machine
# ---------------------------------------------------------------------------

# Minimum available system memory (MB) before aborting an in-progress analysis.
# When available memory drops below this threshold between files, the analysis
# raises MemoryPressureError instead of continuing and risking OOM/swap thrash.
# The threshold is low (512 MB) because it's a last-resort safety net — the
# bakeoff scripts and smart-test have their own higher thresholds.
_MIN_AVAILABLE_MB = int(os.environ.get("HYPERGUMBO_MIN_MEMORY_MB", "512"))


class MemoryPressureError(MemoryError):
    """Raised when available system memory is critically low during analysis.

    This is a graceful abort — the analysis stops between files (not mid-parse)
    and returns partial results or raises to the caller.  Prevents the OS from
    thrashing swap or killing the process via OOM killer.
    """


def _check_memory_pressure() -> None:
    """Raise MemoryPressureError if available memory is critically low.

    Reads MemAvailable from /proc/meminfo (Linux).  No-op on non-Linux.
    Called between files during analysis to catch memory exhaustion early.
    """
    if _MIN_AVAILABLE_MB <= 0:
        return  # Disabled via env var
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    available_mb = int(line.split()[1]) // 1024
                    if available_mb < _MIN_AVAILABLE_MB:
                        raise MemoryPressureError(
                            f"Available memory ({available_mb} MB) below "
                            f"threshold ({_MIN_AVAILABLE_MB} MB). Aborting "
                            f"analysis to prevent OOM. Set "
                            f"HYPERGUMBO_MIN_MEMORY_MB=0 to disable."
                        )
                    return
    except (OSError, ValueError):
        pass  # Non-Linux or /proc not available — skip check


if TYPE_CHECKING:
    import tree_sitter


@dataclass
class AnalysisResult:
    """Universal result type for all language analyzers.

    This replaces the per-language XxxAnalysisResult dataclasses
    (GoAnalysisResult, RustAnalysisResult, etc.) which were all identical.

    Attributes:
        symbols: List of detected symbols (functions, classes, etc.)
        edges: List of relationships between symbols (calls, imports, etc.)
        usage_contexts: List of usage contexts for call-based pattern matching (v1.1.x)
        run: Provenance tracking for the analysis pass
        skipped: Whether the analysis was skipped (e.g., missing dependency)
        skip_reason: Human-readable reason for skipping
    """

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    usage_contexts: list[UsageContext] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""
    dependency_manifest: object | None = None
    """Optional DependencyManifest from supply_chain.py.

    Language analyzers that parse dependency manifests (go.mod, package.json,
    Cargo.toml) can populate this field. Manifests are merged after all
    analyzers run and passed to ``create_boundary_nodes`` for tier
    classification of external references.
    """


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single source file.

    Used during two-pass analysis: first pass collects symbols,
    second pass resolves cross-file references using the symbol registry.

    Attributes:
        symbols: Symbols detected in this file
        symbol_by_name: Quick lookup by symbol name for edge resolution
        import_aliases: Mapping of import alias → import path (Go, etc.)
        node_for_symbol: Mapping of symbol ID → tree-sitter node for
            automatic shape_id computation (ADR-0014 §1). Analyzers that
            populate this get shape_id computed by the base class.
    """

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)
    import_aliases: dict[str, str] = field(default_factory=dict)
    node_for_symbol: dict[str, "tree_sitter.Node"] = field(default_factory=dict)
    class_field_types: dict[str, dict[str, str]] = field(default_factory=dict)
    """Maps class/struct name → {field_name → type_name}.

    Populated by subclasses in ``extract_symbols_from_file()``.
    Aggregated by ``analyze()`` into ``_field_type_registry`` for Pass 2.
    Name-based (not Symbol-based) because the target type's Symbol
    may not exist yet during Pass 1 (it might be in another file).
    """

    interface_method_sets: dict[str, set[tuple[str, int, int]]] = field(
        default_factory=dict,
    )
    """Maps interface name → set of (method_name, param_count, return_count).

    Used for cross-file structural interface matching: a struct satisfies
    an interface if its method set (including arities) is a superset of
    the interface's.  Arity matching prevents false positives from
    coincidental method name collisions (e.g., Close() vs Close(ctx)).
    """

    struct_method_sets: dict[str, set[tuple[str, int, int]]] = field(
        default_factory=dict,
    )
    """Maps struct name → set of (method_name, param_count, return_count).

    Used alongside ``interface_method_sets`` for cross-file structural
    interface matching in Go.
    """

    dot_imports: list[str] = field(default_factory=list)
    """Package paths brought into scope unprefixed (Go ``import . "X"``).

    Powers WI-vovum / WI-mafik dot-import gap fix: a bare-identifier call
    whose name was dot-imported gets an unresolved edge keyed to the source
    package. Populated during Pass 1 by analyzers that recognize dot
    imports; consumed at call-emit time in Pass 2.
    """

    method_return_types: dict[str, str] = field(default_factory=dict)
    """Maps qualified method name → return type name.

    INV-dihos / WI-kuroj return-type registry: when a call site does
    ``x := obj.Method()`` and ``obj``'s type is known, the return type
    of ``{ObjType}.{MethodName}`` can be looked up here and assigned
    to ``x`` in var_types, enabling chained receiver-type resolution.

    Populated during Pass 1 (symbol extraction) by language analyzers
    that parse method/function signatures.  Aggregated across files
    by ``analyze()`` into ``_method_return_type_registry`` for Pass 2.

    Key format is language-specific:
    - Go: ``ReceiverType.MethodName`` (e.g. ``Engine.Query``)
    - Java: ``ClassName.methodName`` (e.g. ``Engine.query``)
    - Functions without receivers: ``FuncName`` (e.g. ``NewEngine``)
    """


@dataclass(frozen=True)
class ArityFlags:
    """Parameter arity classification for stable_id computation (ADR-0014 §2).

    Captures the structural shape of a function's parameter list without
    recording names or types.  Two functions with the same ArityFlags and
    kind will produce the same untyped-tier stable_id (by design — they
    have the same *interface shape*).

    Attributes:
        param_count: Number of regular parameters (excludes self/cls/receiver).
        has_defaults: Whether any parameter has a default value.
        has_varargs: Whether variadic positional args exist (``*args``, ``...``).
        has_kwargs: Whether variadic keyword args exist (``**kwargs``).
    """

    param_count: int
    has_defaults: bool
    has_varargs: bool
    has_kwargs: bool

    def as_flags_str(self) -> str:
        """Return the canonical string form used in stable_id hashing."""
        return f"{self.has_defaults},{self.has_varargs},{self.has_kwargs}"


# ---------------------------------------------------------------------------
# Tree-sitter helper functions
# ---------------------------------------------------------------------------


def node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text content for a tree-sitter node.

    Args:
        node: A tree-sitter node
        source: Source file bytes

    Returns:
        The text content of the node, decoded as UTF-8.
    """
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def node_own_text(node: "tree_sitter.Node") -> str:
    """Decoded ``node.text``, None-safe — the ONE home for this fact.

    ``tree_sitter.Node.text`` is ``bytes | None``, and 35 analyzers carried
    a byte-similar private ``_get_node_text`` copy of this two-liner
    (WI-sarag) — with the None guard present in some copies and absent in
    others, the half-guarded-population tell (WI-vokiz) that means there
    is no contract, only local habit. Analyzers import this under their
    historical local name (``import ... as _get_node_text``); a
    recurrence test fails if a new private copy appears.

    Distinct from :func:`node_text`, which slices the SOURCE by byte
    range — that one exists for callers that hold the file bytes and may
    face nodes from re-parsed sub-trees where ``.text`` is unavailable.
    """
    return (node.text or b"").decode("utf-8", errors="replace")


def find_child_by_type(
    node: "tree_sitter.Node", type_name: str
) -> Optional["tree_sitter.Node"]:
    """Find the first child node of a given type.

    Args:
        node: Parent tree-sitter node
        type_name: The node type to search for

    Returns:
        The first matching child, or None if not found.
    """
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def find_child_by_field(
    node: "tree_sitter.Node", field_name: str
) -> Optional["tree_sitter.Node"]:
    """Find a child node by field name.

    Args:
        node: Parent tree-sitter node
        field_name: The field name to look up

    Returns:
        The child at that field, or None if not found.
    """
    return node.child_by_field_name(field_name)


# ---------------------------------------------------------------------------
# ID generation helpers
# ---------------------------------------------------------------------------


# Node types that mean "a value is produced by calling something", and the
# field each grammar uses for the callee. Shared rather than re-derived per
# analyzer: WI-nopod's whole point is that a fact recorded in N places
# diverges, and the parity column
# (``test_constructed_from_parity``) enforces the semantics uniformly.
_CALL_NODE_CALLEE_FIELDS: "tuple[str, ...]" = (
    "function", "constructor", "callee",
)
# Name-shaped nodes that can stand in for an unnamed callee field.
_CALLEE_NAME_NODE_TYPES: frozenset[str] = frozenset({
    "identifier", "simple_identifier", "scoped_identifier",
    "member_expression", "navigation_expression", "field_expression",
    "qualified_name", "selector_expression",
})
_CALL_NODE_TYPES: frozenset[str] = frozenset({
    "call_expression", "new_expression", "call", "constructor_invocation",
})


def constructed_from_callee(
    value_node: "Optional[tree_sitter.Node]", source: bytes,
) -> Optional[str]:
    """Render the callee of a binding's initializer, or None (WI-nopod).

    ``var app = NewC()`` -> ``"NewC"``; ``let app = pkg.Build()`` ->
    ``"pkg.Build"``. Used to stamp ``Symbol.meta["constructed_from"]`` so a
    framework YAML can key on a *construction site* — the surface used by
    every framework you configure by building an object rather than by
    decorating or subclassing one.

    Three deliberate narrowings, uniform across languages and pinned by the
    parity column:

    * only a call-valued initializer qualifies (``x = 30`` records nothing,
      or the key stops being a useful filter);
    * a constructor and a factory are treated identically, because static
      analysis cannot distinguish them and claiming otherwise would be a
      guess dressed as data;
    * a **computed** callee (``registry[k]()``) records nothing rather than a
      partial name — a YAML matching a fiction fails silently, so absence is
      the honest signal.

    Qualification is KEPT: stripping it would make ``orm.declarative_base``
    indistinguishable from a same-named local, which is unrecoverable
    downstream rather than merely inconvenient.
    """
    if value_node is None or value_node.type not in _CALL_NODE_TYPES:
        return None
    callee = None
    for field_name in _CALL_NODE_CALLEE_FIELDS:
        callee = value_node.child_by_field_name(field_name)
        if callee is not None:
            break
    if callee is None:
        # Not every grammar names the callee. Swift's `call_expression` is
        # simply `(simple_identifier, call_suffix)` with no fields at all, so
        # fall back to a leading name-shaped child. Restricted to name shapes
        # so an argument list or an operator can never be mistaken for a
        # callee — silence is the correct output for anything else.
        first = value_node.children[0] if value_node.children else None
        if first is None or first.type not in _CALLEE_NAME_NODE_TYPES:
            return None
        callee = first
    text = node_text(callee, source).strip()
    if not text:  # pragma: no cover - a callee node always spans source text;
        # the guard exists so a zero-width node from a damaged parse cannot
        # produce an empty key that a YAML regex would then match on.
        return None
    # Accept a static dotted / scoped path only. `a[k].b` and generics are
    # computed or parameterised and cannot be keyed on reliably.
    parts = text.replace("::", ".").split(".")
    if not all(part.isidentifier() for part in parts):
        return None
    return text


def make_symbol_id(
    lang: str, path: str, start_line: int, end_line: int, name: str, kind: str
) -> str:
    """Generate a location-based symbol ID.

    Format: {lang}:{path}:{start}-{end}:{name}:{kind}

    Args:
        lang: Language identifier (e.g., "go", "rust", "python")
        path: File path
        start_line: Starting line number
        end_line: Ending line number
        name: Symbol name
        kind: Symbol kind (function, class, etc.)

    Returns:
        A unique, location-based symbol ID.

    The ``name`` slot is sanitized ``':' -> '.'`` here — the always-on ADR-0036
    Ruling-1 chokepoint (WI-sikar). Why the name slot specifically: the
    canonical parse is anchored from the RIGHT (``span, name, kind =
    parts[-3:]``), so a colon in the name slot shifts every anchor left by one
    and the id stops parsing, while colons in the ``path`` slot are harmless and
    stay verbatim (Rust's ``std::cmp`` module ids depend on that).

    This fold was deferred for a long time on the premise that it would "churn"
    colon-bearing source identifiers such as Objective-C selectors
    (``removeItemAtPath:error:``). Measurement reversed that premise: those ids
    were ALREADY unparseable seven-segment strings, so sanitizing repairs them
    rather than churning them. Selector-style and coordinate-style names are the
    beneficiaries.

    The substitution is documented-lossy (full fidelity lives in ``Symbol.name``)
    and idempotent, so producers that already route through
    :func:`sanitize_id_name_segment` — the synthetic linker stand-ins per
    WI-vuzaf, the Maven manifest sites per INV-dulah — are unaffected. Those
    explicit calls stay: they pin the right *value* into the slot, whereas this
    only guarantees the slot is colon-free.
    """
    return (
        f"{lang}:{path}:{start_line}-{end_line}"
        f":{sanitize_id_name_segment(name)}:{kind}"
    )


def sanitize_id_name_segment(name: str) -> str:
    """Colon-free ``{name}`` slot for a canonical symbol id (ADR-0036 Ruling 1).

    A literal ``':'`` in the name slot would push the id past its five anchored
    segments and defeat the from-both-ends round-trip parser, so colons are
    sanitized ``':' -> '.'`` (the round-trip is documented-lossy — full fidelity
    lives in ``Symbol.name``), e.g. the synthetic linker stand-ins whose name
    folds a protocol address — message-queue ``kafka:publish:topic`` →
    ``kafka.publish.topic`` (WI-vuzaf Pattern A) — and the Maven manifest
    producers whose name folds an ecosystem coordinate,
    ``org.springframework.boot:spring-boot-starter-web`` (INV-dulah).

    :func:`make_symbol_id` applies this to every name slot (WI-sikar), so
    calling it explicitly is no longer required for correctness. Producers keep
    doing so where it documents intent, and the substitution is idempotent.
    """
    return name.replace(":", ".")


def make_file_id(lang: str, path: str) -> str:
    """Generate an ID for a file node (used as import edge source).

    Args:
        lang: Language identifier
        path: File path

    Returns:
        A file-level symbol ID.
    """
    return f"{lang}:{path}:1-1:file:file"


_FILE_ID_SUFFIX = ":1-1:file:file"


def _read_file_end_line(repo_root: "Path", rel_path: str) -> int:
    """Line count (>=1) of ``repo_root/rel_path`` when readable, else 1.

    Shared by the two file-anchor synthesizers (the dangling-edge pass and the
    node-bearing-path pass) to stamp a real ``span.end_line`` on each synthesized
    file Symbol (INV-vaguj). The real span is load-bearing: the containment
    linker's span-based pass roots a file's top-level members at the file node by
    span containment, so a 1-1 span would leave the file an orphan. Unreadable
    files keep the schema-valid sentinel ``1`` (the INV-piroh schema gate forbids
    the negative sentinel).
    """
    try:
        file_text = (repo_root / rel_path).read_text(
            encoding="utf-8", errors="ignore",
        )
    except (OSError, ValueError):
        return 1
    line_count = file_text.count("\n")
    if file_text and not file_text.endswith("\n"):
        line_count += 1
    return line_count if line_count >= 1 else 1


def synthesize_file_symbols_for_dangling_edges(
    symbols: list[Symbol],
    edges: list[Edge],
    repo_root: "Optional[Path]" = None,
    origin_run_id: str = "",
) -> list[Symbol]:
    """Synthesize real file Symbols for any ``make_file_id``-shape dangling edge endpoint.

    WI-ramuv chokepoint: many analyzers emit import / module-level edges
    whose ``src`` (and occasionally ``dst``) is :func:`make_file_id` shape
    (``{lang}:{path}:1-1:file:file``) without emitting a matching producer-
    side ``kind="file"`` Symbol. ``ir.create_boundary_nodes`` then turns
    every such dangling endpoint into an external boundary node, which is
    structurally wrong (the file IS first-party) and forces Plan A
    canonical-id collapsing as a band-aid.

    This helper runs at the orchestrator (after analyzer result
    aggregation, before boundary-node synthesis) and emits one real
    ``kind="file"`` Symbol per distinct dangling ``make_file_id`` id, so
    those edges land on real producer-side Symbols and never enter the
    boundary pipeline.

    INV-vaguj
    ---------
    When ``repo_root`` is provided, two identity claims on each synthesised
    Symbol become honest:

    * **``name``/``path``** — paths under ``repo_root`` strip to repo-relative
      form. Analyzers that leaked absolute paths into the dangling endpoint
      id no longer poison the synthesised ``Symbol.name`` with a leading
      ``/home/.../`` prefix (downstream the ``Symbol.path`` normalisation
      loop in ``all_analyzers`` already handled ``.path``, but never
      ``.name`` — the two values drifted, which is the root cause of
      INV-dihif's user-visible explain leak).
    * **``span.end_line``** — when the file is readable under ``repo_root``,
      reflects the file's actual line count, not the hardcoded ``1``. A
      3179-line ``cli.py`` ships with ``span.end_line=3179`` instead of
      ``span.end_line=1``. Unreadable files retain ``end_line=1`` (a
      schema-valid value; the tracker-proposed sentinel ``-1`` would
      violate the INV-piroh schema gate's ``minimum=0`` constraint on
      ``Span.end_line``).

    When ``repo_root`` is ``None`` (legacy call form retained for unit
    tests that pre-date this fix), the pre-INV-vaguj shape is preserved:
    ``name = path = <whatever-was-in-the-endpoint>`` and ``end_line = 1``.

    Args:
        symbols: All Symbols collected from analyzers (mutated only via
            return value — this function is non-destructive).
        edges: All Edges collected from analyzers.
        repo_root: Repository root. When provided, paths normalise to
            repo-relative and ``span.end_line`` reflects each file's
            actual line count.
        origin_run_id: Execution id of the ``AnalysisRun`` the orchestrator
            emits for this synthesis pass (synthetic:F1). Stamped into each
            synthesized Symbol's ``origin_run_id`` so the node->AnalysisRun
            JOIN resolves. Defaults to ``""`` for legacy/unit-test callers
            that don't supply a run.

    Returns:
        List of new Symbols (one per previously-dangling
        ``make_file_id`` id). The caller appends these to the global
        Symbol list.
    """
    existing_ids = {s.id for s in symbols}
    synthesized: dict[str, Symbol] = {}
    root_prefix: Optional[str] = None
    if repo_root is not None:
        root_prefix = str(repo_root).replace("\\", "/").rstrip("/") + "/"

    for edge in edges:
        for endpoint in (edge.src, edge.dst):
            if not endpoint.endswith(_FILE_ID_SUFFIX):
                continue
            if endpoint in existing_ids or endpoint in synthesized:
                continue
            # Canonical make_file_id shape is "{lang}:{path}:1-1:file:file".
            # path may itself contain colons (e.g. dart "dart:io"), so we
            # split off the language at the first colon and strip the
            # fixed suffix. Anything that doesn't match this shape was
            # filtered above.
            head = endpoint[: -len(_FILE_ID_SUFFIX)]
            colon = head.find(":")
            if colon < 0:  # pragma: no cover
                continue
            language = head[:colon]
            path = head[colon + 1 :]

            # INV-vaguj: strip the repo_root prefix so absolute paths
            # leaked by upstream analyzers don't surface in ``name``.
            end_line = 1
            if root_prefix is not None:
                normed = path.replace("\\", "/")
                if normed.startswith(root_prefix):
                    path = normed[len(root_prefix):]
                # INV-vaguj: stamp the file's real line count when we can
                # read it; otherwise keep the schema-valid sentinel of 1.
                end_line = _read_file_end_line(repo_root, path)

            synthesized[endpoint] = Symbol(
                id=endpoint,
                name=path,
                kind="file",
                language=language,
                path=path,
                span=Span(
                    start_line=1, start_col=0,
                    end_line=end_line, end_col=0,
                ),
                origin="orchestrator_file_symbol_synthesis",
                # synthetic:F1: stamp the orchestrator-emitted AnalysisRun's
                # execution_id so the node->AnalysisRun JOIN resolves (was the
                # empty-string sentinel). Defaults to '' for legacy callers.
                origin_run_id=origin_run_id,
            )

    return list(synthesized.values())


def synthesize_file_anchors_for_node_bearing_paths(
    symbols: list[Symbol],
    repo_root: "Optional[Path]" = None,
    origin_run_id: str = "",
) -> list[Symbol]:
    """Synthesize a ``kind="file"`` anchor for every path that has content
    nodes but no file anchor (WI-dagif; file-anchor:F1, node-bearing slice).

    Sibling of :func:`synthesize_file_symbols_for_dangling_edges`: that pass is
    keyed on dangling ``make_file_id`` EDGE endpoints (WI-ramuv); this one is
    keyed on NODE-BEARING PATHS whose ``make_file_id`` has no producer-side
    ``kind="file"`` Symbol *and* no dangling edge to trigger the other pass —
    e.g. a Java/C#/Rust file with only a class + fields and no imports/calls, or
    a generated module. Without it those paths have a rootless ``contains`` tree
    (content nodes with no file root), so ``slice`` cannot expand "this file's
    symbols" and the file universe is split (WI-rajod).

    Run AFTER the dangling-edge pass so its anchors count as already-present.
    Each anchor carries a real ``span.end_line`` (the file's line count) so the
    containment linker's span-based pass roots the file's top-level members at it
    — no separate ``contains``-edge emission is needed here.

    SAFETY — why this is landable independently of file-anchor:F4 (the centrality
    surface split): these paths already carry content nodes, so they are already
    in the Additional-Files exclusion set (``source_paths`` in cli.py) and minting
    their anchors cannot empty that surface. The nodeless cohort (yaml/markdown/
    config files with zero nodes) is deliberately NOT anchored here — that is the
    full F1+F4 co-release, gated on the WI-rajod-invariant human decision.

    Args:
        symbols: All Symbols collected from analyzers + the dangling-edge pass.
        repo_root: Repository root; when provided, paths normalize to
            repo-relative and ``span.end_line`` reflects each file's line count.
        origin_run_id: Execution id of the orchestrator file-synthesis
            ``AnalysisRun`` (shared with the dangling-edge pass).

    Returns:
        One new ``kind="file"`` Symbol per node-bearing path that lacked one.
    """
    root_prefix: Optional[str] = None
    if repo_root is not None:
        root_prefix = str(repo_root).replace("\\", "/").rstrip("/") + "/"

    def _rel(p: str) -> str:
        if root_prefix is not None:
            normed = p.replace("\\", "/")
            if normed.startswith(root_prefix):
                return normed[len(root_prefix):]
        return p

    anchored: set[str] = {
        _rel(s.path) for s in symbols if s.kind == "file" and s.path
    }
    # path -> representative language (first content node wins; deterministic in
    # input order). A path with no language is skipped — make_file_id needs one.
    needs_anchor: dict[str, str] = {}
    for sym in symbols:
        if sym.kind == "file" or not sym.path or not sym.language:
            continue
        rel = _rel(sym.path)
        if rel in anchored or rel in needs_anchor:
            continue
        needs_anchor[rel] = sym.language

    synthesized: list[Symbol] = []
    for rel, language in needs_anchor.items():
        end_line = _read_file_end_line(repo_root, rel) if repo_root is not None else 1
        synthesized.append(Symbol(
            id=make_file_id(language, rel),
            name=rel,
            kind="file",
            language=language,
            path=rel,
            span=Span(start_line=1, start_col=0, end_line=end_line, end_col=0),
            origin="orchestrator_file_symbol_synthesis",
            origin_run_id=origin_run_id,
        ))
    return synthesized


def synthesize_file_anchors_for_paths(
    symbols: list[Symbol],
    path_to_language: "dict[str, str]",
    repo_root: "Optional[Path]" = None,
    origin_run_id: str = "",
) -> list[Symbol]:
    """Mint a ``kind="file"`` anchor for each ``(rel_path, language)`` whose path
    has no existing Symbol (file-anchor:F1, additional-file-candidate cohort).

    Generic path-keyed minter: the caller (``all_analyzers``) selects the
    candidates via :func:`taxonomy.additional_file_candidates` + ``get_language``
    — selection lives in the caller because ``base`` cannot import the taxonomy/
    discovery/sketch modules that define the candidate filter and its exclude
    lists without a cycle. Minting (the canonical id/span/origin shape, shared
    with the dangling-edge and node-bearing synthesizers) stays here. These
    anchors are leaf nodes (their files — config/docs — carry no content nodes
    to contain), so no containment-linker rooting is needed.

    Args:
        symbols: All Symbols so far (only their ``path`` set is read, for dedup).
        path_to_language: Repo-relative path -> resolved language. Every language
            must be non-``None`` (the candidate filter requires a resolvable one).
        repo_root: Repo root; when provided, ``span.end_line`` reflects the
            file's line count.
        origin_run_id: Execution id of the shared file-synthesis ``AnalysisRun``.

    Returns:
        One new ``kind="file"`` Symbol per path that lacked one.
    """
    # Dedup against already-emitted paths, normalized to repo-relative. Some
    # analyzers (e.g. html) emit file Symbols with ABSOLUTE paths that the
    # pipeline only normalizes downstream (after this chokepoint). The candidate
    # keys here are repo-relative, so an absolute existing path would not match
    # and we would mint a duplicate anchor (the html double-file-node regression).
    existing: set[str] = set()
    for s in symbols:
        if not s.path:
            continue
        sp = s.path
        _p = Path(sp)
        if repo_root is not None and _p.is_absolute() and _p.is_relative_to(repo_root):
            sp = str(_p.relative_to(repo_root))
        existing.add(sp)
    synthesized: list[Symbol] = []
    for rel, language in path_to_language.items():
        if rel in existing:
            continue
        end_line = _read_file_end_line(repo_root, rel) if repo_root is not None else 1
        synthesized.append(Symbol(
            id=make_file_id(language, rel),
            name=rel,
            kind="file",
            language=language,
            path=rel,
            span=Span(start_line=1, start_col=0, end_line=end_line, end_col=0),
            origin="orchestrator_file_symbol_synthesis",
            origin_run_id=origin_run_id,
        ))
    return synthesized


def make_unresolved_edge(
    lang: str,
    src_id: str,
    callee_name: str,
    line: int,
    pass_id: str,
    run_id: str,
    *,
    module_hint: str = "external",
    dst_ref: Optional[ExternalRef] = None,
    enclosing_class: Optional[str] = None,
    receiver_type_hint: Optional[str] = None,
    inherited_field_receiver: Optional[str] = None,
    call_construct: Optional[str] = None,
) -> Edge:
    """Create an unresolved-external call edge for a callee not in the project.

    All language analyzers use this when a function/method call cannot be
    resolved to a project symbol.  The resulting edge has confidence 0.50
    and a standardized dst ID format:  {lang}:{module_hint}:0-0:{name}:unresolved

    Args:
        lang: Language identifier (e.g., "c", "java", "rust")
        src_id: Symbol ID of the caller
        callee_name: Name of the called function/method
        line: Source line number of the call
        pass_id: Analyzer pass ID
        run_id: Execution run ID
        module_hint: Module/package context when known (default "external").
        dst_ref: Optional structured ``ExternalRef`` (WI-tihup). When a
            caller supplies a richer ref it wins; otherwise one is derived
            from ``(lang, module_hint, callee_name)`` (ADR-0037 ruling 2),
            EXCEPT when ``module_hint`` is the ``"external"`` sentinel — then
            the module is unknown and ``dst_ref`` stays ``None`` (WI-huzuv,
            the ADR-0037 "unidentified reference" cell). The legacy ``dst``
            string is built from the same components so the two stay coherent.
        enclosing_class: Owning class short name of the call site (Site 1:
            bare / `this` / `self` calls). Lands on ``Edge.meta`` under
            ``"enclosing_class"`` for the inherited_calls linker (INV-nilud
            campaign, PR-1) to walk the ancestor chain.
        receiver_type_hint: Inferred receiver type short name (Site 2:
            typed-receiver calls). Lands on ``Edge.meta`` under
            ``"receiver_type_hint"``.
        inherited_field_receiver: Receiver identifier when believed to be
            an inherited field (Site 3). Lands on ``Edge.meta`` under
            ``"inherited_field_receiver"``.
        call_construct: Syntactic form of the call site — ``"method"`` for a
            call on a receiver. Lands on ``Edge.meta`` under
            ``"call_construct"``, where BOTH taint gates read it:
            ``io_boundary.gate_named_entry`` (an untyped method call never
            matches a catalogued entry) and ``_register_sanitizer_callers``
            (an untyped method call never registers a barrier). Declaring it
            is what stops a name-shortening improvement — putting a real
            receiver type in ``module_hint`` also shortens ``w.doFinal`` to
            ``doFinal`` — from turning into a PHANTOM BARRIER, since the
            sanitizer path matches on the short name and never reads
            ``module_hint``. Omit it only when the call genuinely has no
            receiver.
    """
    dst_id = f"{lang}:{module_hint}:0-0:{callee_name}:unresolved"
    # ADR-0037 ruling 2: derive dst_ref from the components this helper already holds,
    # instead of leaving it the optional-and-usually-omitted kwarg that left 67.8% of
    # external edges without a structured ref (WI-zuhon). A caller-supplied richer ref
    # still wins. EXCEPTION (WI-huzuv): when module_hint is the "external" sentinel the
    # module is unknown, so dst_ref stays None — the "unidentified reference" cell of the
    # ADR-0037 table (False, None); promoting the literal sentinel to a path would invent
    # false precision. The finalize edge-resolution sub-step backstops bypassing producers.
    if dst_ref is None and module_hint != "external":
        dst_ref = ExternalRef(lang=lang, module_path=module_hint, name=callee_name)
    hint_meta: Dict[str, Any] = {}
    if enclosing_class is not None:
        hint_meta["enclosing_class"] = enclosing_class
    if receiver_type_hint is not None:
        hint_meta["receiver_type_hint"] = receiver_type_hint
    if inherited_field_receiver is not None:
        hint_meta["inherited_field_receiver"] = inherited_field_receiver
    if call_construct is not None:
        hint_meta["call_construct"] = call_construct
    return Edge.create(
        src=src_id,
        dst=dst_id,
        edge_type="calls",
        line=line,
        origin=pass_id,
        origin_run_id=run_id,
        evidence_type="ast_call_direct",
        is_resolved=False,
        dst_ref=dst_ref,
        meta=hint_meta or None,
    )


def defer_bare_method_call(
    candidate_kind: str,
    candidate_name: str,
    match_type: str,
    enclosing_type: Optional[str],
    separator: str = ".",
) -> bool:
    """INV-fahub: should a BARE call that resolved to ``candidate`` be WITHHELD?

    A bare / implicit-``this`` / ``self`` call (``copy(...)``, a chained-receiver
    call whose receiver token was dropped, a same-file short-name hit) may
    legitimately reach a free function / object, or a method of its OWN enclosing
    class (implicit ``this``). It must NOT confidently bind to a DIFFERENT class's
    method on weak short-name evidence — that is the cross-language magnet misbind
    (``copy`` → ``FileCopyTask.copy``, ``delete`` → ``ActivityPubClient.delete``:
    dozens of call sites → one arbitrary def, per real-repro validation). Deferring
    such a call to the shared ``inherited_calls`` **Site-1** walker (by emitting
    ``make_unresolved_edge(..., enclosing_class=<enclosing_type>)``) lets it be
    recovered iff the method is actually on the enclosing class's linearization (a
    genuine *inherited* implicit-``this`` call), and left honestly external when it
    is a cross-class magnet — INV-nogof (withhold, never pick-first) + INV-nilud
    (the linker owns resolution).

    Returns ``True`` to DEFER, ``False`` to bind directly. ``match_type`` is the
    resolver's ``LookupResult.match_type``; pass ``"suffix"`` for a bare same-file
    ``local_symbols`` hit (an exact *short-name* match, but still weak evidence for
    a cross-class target). Owner class is parsed from ``candidate_name``'s
    ``"Owner<sep>method"`` shape; ``separator`` defaults to ``"."`` (Scala / Swift /
    Go / most) but callers whose method names use another separator pass it — e.g.
    Rust / C++ (``"Owner::method"``) pass ``separator="::"``. A name with no
    separator (a free def) never defers.
    """
    if candidate_kind != "method":
        return False
    owner = (
        candidate_name.rsplit(separator, 1)[0]
        if separator in candidate_name else None
    )
    if owner is not None and owner == enclosing_type:
        return False  # same-class implicit ``this``/``self`` — bind directly
    # Cross-class (or owner-unknown) method: only a strong exact / import-scoped
    # match is trustworthy for a bare call; a weak suffix / ambiguous / same-file
    # short-name collision is the magnet — defer to Site-1.
    return match_type not in ("exact", "path_hint")


def make_route_stable_id(method: str, path: str) -> str:
    """Compute a collision-free stable_id for route symbols.

    Uses ``_short_sha256("route:{method}:{path}")`` per ADR-0014 §4 +
    Phase 6 PR1 (INV-hunup). The previous approach set stable_id to
    bare HTTP methods (e.g. "GET"), causing every same-method route to
    collide; that was already fixed. Phase 6 PR1 additionally aligns the
    output shape with the canonical ``sha256:<16hex>`` schema that the
    other ``make_*_stable_id`` factories use, closing the
    ``raw_hex_64`` escape category for ~20 call-site Symbols emitted by
    the HTTP linker and ~N route Symbols emitted by various analyzers.

    Args:
        method: HTTP method (e.g. "GET", "POST", "ANY"). Case-insensitive —
            the value is upper-cased internally for consistency.
        path: Route path (e.g. "/users", "/posts/:id").

    Returns:
        A ``sha256:<16hex>``-shaped string that uniquely identifies the
        (method, path) pair.
    """
    # Normalize empty paths to "/" — empty strings from annotation extraction
    # (e.g., @GetMapping("") or sub-resource locators without @Path) should
    # hash identically to the root path (INV-nimik).
    normalized = path if path else "/"
    return _short_sha256(f"route:{method.upper()}:{normalized}")


def make_route_symbol(
    *,
    language: str,
    path: str,
    span: Span,
    method: str,
    route_path: str,
    origin: str,
    origin_run_id: str,
    framework_role: str = "route",
    handler_ref: Optional[str] = None,
    extra_meta: Optional[dict[str, Any]] = None,
    is_exported: bool = False,
    protocol_origin: Optional[str] = None,
    discovery_language: Optional[str] = None,
) -> Symbol:
    """Mint a route-marker Symbol with canonical identity + provenance (WI-zugob).

    Every route-marker producer previously hand-rolled this record, and they
    drifted apart in three ways the validators catch — an id kind-slot left as
    the literal ``route`` fossil (``route`` is not a registered symbol kind, so
    ``id_format`` fails), an absent ``origin_run_id`` (``cross_field``), and an
    unregistered ``origin`` pass-id (``axis_conformance``). This is the shared
    minting chokepoint the WI-tufil producer scout found did not exist; it
    encapsulates the shape ``framework_patterns.materialize_route_symbols``
    already proved for the go/js/java trio.

    **Naming.** ``Symbol.name`` is ``"{METHOD} {path}"`` and the id name-slot is
    derived from it, so ADR-0036 Ruling 1 (``id name-slot == sanitized(name)``)
    holds by construction. The handler's own name is NOT the record name — it
    lives in ``meta['handler_ref']``, which is what ``linkers/route_handler``
    already resolves against. That direction is forced, not stylistic: a
    multi-method registration (Starlette ``methods=['GET','POST']``, a Django
    class-based view expanded per verb) emits several markers at the SAME span,
    so a handler-derived name-slot would collide their ids, while the
    method+path pair is unique by construction.

    Args:
        language: Producer language for the id lang-slot.
        path: File path the registration appears in.
        span: Registration span (the marker is anchored at the call site).
        method: HTTP verb, or a transport sentinel (``WS``/``LIVE``/``RPC``) —
            split out of the verb field by :func:`routes.transport_meta`.
        route_path: Route path; empty normalizes to ``"/"`` (INV-nimik).
        origin: Producing pass-id — MUST be registered (axis_conformance).
        origin_run_id: ``execution_id`` of the producing AnalysisRun — MUST be
            non-empty and join a real run (cross_field).
        framework_role: ``route`` (default), ``route_mount`` or ``route_include``.
        handler_ref: Handler name, preserved in meta for the route_handler
            linker. Producers whose linker branch keys on a different meta key
            (go reads ``handler_name``) pass theirs via ``extra_meta`` instead.
        extra_meta: Producer-specific meta merged last (e.g. ``view_name``,
            ``wrapper_name``). Callers should NOT re-supply ``route_path`` /
            ``http_method`` / ``framework_role`` here — the factory owns those,
            and re-supplying ``http_method`` would undo the transport split.
        is_exported: Whether the route's handler is part of the public API
            (go derives it from the handler's leading capital).
        protocol_origin: ADR-0031 Class B discriminator. Set it when the marker
            is a SYNTHETIC stand-in fabricated from a protocol definition rather
            than a real source declaration (the grpc linker reading a ``.proto``,
            the annotation linker reading an ``@hg:route`` directive). When set,
            ``Symbol.language`` becomes ``None`` and the ``language`` argument
            moves to ``discovery_language`` — the host source language the
            pattern was discovered in — matching what the other Class-B linkers
            (message_queue / database_query / subprocess_cli) already emit. The
            id lang-slot still uses ``language`` either way, so the id stays a
            parseable five-segment ADR-0036 record for synthetic and real
            markers alike.
        discovery_language: Host source language for a synthetic marker, stated
            EXPLICITLY rather than derived from ``language``. The two are not
            always the same judgement: the grpc linker deliberately leaves it
            ``None`` (a gRPC route is fabricated from a service definition, not
            discovered inside a host file), while the annotation linker sets it
            to the language of the file carrying the directive. Deriving it here
            would silently overturn that per-linker decision, so the caller owns
            it.

    Returns:
        A route-marker ``Symbol`` with ``kind="function"`` (the ADR-0027 Phase-3
        route→function fold; the route signal lives in ``meta.framework_role``).
    """
    from ..routes import transport_meta

    normalized_path = route_path if route_path else "/"
    name = f"{method} {normalized_path}"
    meta: dict[str, Any] = {
        "route_path": normalized_path,
        **transport_meta(method),
        "framework_role": framework_role,
    }
    if handler_ref:
        meta["handler_ref"] = handler_ref
    if extra_meta:
        meta.update(extra_meta)
    return Symbol(
        id=make_symbol_id(
            language, path, span.start_line, span.end_line,
            sanitize_id_name_segment(name), "function",
        ),
        name=name,
        kind="function",
        # ADR-0031 Class B: a synthetic stand-in has no host language of its
        # own, so `language` names where it was DISCOVERED, not what it is.
        language=None if protocol_origin else language,
        discovery_language=discovery_language,
        protocol_origin=protocol_origin,
        path=path,
        span=span,
        origin=origin,
        origin_run_id=origin_run_id,
        stable_id=make_route_stable_id(method, normalized_path),
        meta=meta,
        line_span=span.end_line - span.start_line + 1,
        is_exported=is_exported,
    )


def make_site_stable_id(protocol_origin: str, rel_path: str, target: str) -> str:
    """ADR-0035 §3 SITE-axis identity for ``call_site`` stand-ins (v8, WI-napoh).

    Identity formula: ``sha256("site:{protocol_origin}:{rel_path}:{target}")[:16]``.

    SITE-axis kinds represent a CODE LOCATION — "this file issues this call" — so
    their identity MUST fold the declaring file's repo-relative path (``rel_path``),
    unlike LOGICAL kinds (message_queue / event_sourcing topics, routes) whose
    identity is the external target deduped across reference sites. Before v8 the
    HTTP client ``call_site`` borrowed :func:`make_route_stable_id` and the SQL
    query ``call_site`` borrowed :func:`make_protocol_stable_id` — both LOGICAL and
    file-blind — so two files issuing ``GET /api/users`` (or running ``SELECT
    users``) collided cross-file (the Wave-2 identity-gate residual, INV-tazaj
    corpus limb / WI-napoh). The dedicated ``site:`` namespace also guarantees a
    SITE id can never collide with a LOGICAL id by construction.

    ``target`` is the per-family logical target string (e.g. ``"GET:/api/users"``
    for http, ``"SELECT:users"`` for sql). The within-file occurrence ordinal is
    NOT folded here — :func:`split_within_file_stable_id_collisions` adds the
    ``:occ:<n>`` suffix downstream, completing the §3 ``(path, target, occurrence)``
    SITE key (line numbers stay OUT of the hash, §3).
    """
    return _short_sha256(f"site:{protocol_origin}:{rel_path}:{target}")


def make_entry_stable_id(entry_type: str, name: str, path: str) -> str:
    """Compute a collision-free stable_id for entry-point symbols.

    Uses ``_short_sha256("entry:{entry_type}:{name}")`` per ADR-0014 §4 +
    Phase 6 PR1 (INV-hunup). Used for symbols like WGSL shader stages
    (@vertex, @fragment, @compute) where the previous approach set
    stable_id to the bare entry type string, causing same-type entry
    points to collide. Phase 6 PR1 aligns the output shape with the
    canonical ``sha256:<16hex>`` schema so consumers can match this
    factory's output against the ``_check_stable_id_format`` regex
    without a special case.

    ADR-0035 §4: the declaring file ``path`` is folded in. Shader entry names are file-scoped
    (every shader has a ``main``), so without the path two ``@vertex fn main`` in distinct files
    collapse to one stable_id.

    Args:
        entry_type: Entry point category (e.g. "vertex", "fragment", "compute").
        name: Symbol name (e.g. the function name).
        path: Repo-relative path of the declaring file.

    Returns:
        A ``sha256:<16hex>``-shaped string that uniquely identifies the
        (entry_type, name, path) triple.
    """
    return _short_sha256(f"entry:{entry_type}:{name}:{path}")


def _short_sha256(payload: str) -> str:
    """Return the canonical ``sha256:{16-hex}`` form used by `_compute_stable_id`."""
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def assemble_stable_id(
    kind: str,
    param_count: int,
    arity_flags: str,
    decorators: str,
    containing_stable_id: str,
    name: str,
    qualified_name: str,
    occurrence_index: int = 0,
) -> str:
    """The single stable_id v6 hash formula (ADR-0035 §1), shared by every producer.

    ``sha256(kind:param_count:arity_flags:decorators:containing_stable_id
             :name:qualified_name:occurrence_index)``

    This is the one place the formula lives; both :meth:`BaseAnalyzer.compute_stable_id`
    (the tree-sitter path) and the Python analyzer's ``_compute_stable_id`` (the AST path)
    call it, so they can no longer diverge (the v5 divergence — py.py folded ``body_sig`` and
    omitted ``qualified_name`` — was the WI-gitun / INV-tazaj defect surface).

    The ``qualified_name`` slot carries the FULL enclosing scope chain (enclosing classes →
    enclosing functions → local name), which is what makes two same-local-name symbols in
    distinct scopes hash distinctly (ADR-0035 §1 "unique within run"). ``body_sig`` is
    deliberately ABSENT: structural identity is ``shape_id``'s job, and folding the member set
    here churned the class id on every member add/remove (violating §1 "survives body edits").

    ``occurrence_index`` is the §1 within-scope ordinal — a tiebreaker for two definitions that
    are otherwise hash-identical in one scope (e.g. conditional redefinition). It is ``0`` in
    the v6 carrier (no such collision exists on the measured corpus); the slot is present so
    populating it later never requires another scheme bump (§6 "one atomic bump").
    """
    sig = (
        f"{kind}:{param_count}:{arity_flags}:{decorators}"
        f":{containing_stable_id}:{name}:{qualified_name}:{occurrence_index}"
    )
    return _short_sha256(sig)


def make_file_stable_id(language: str, path: str) -> str:
    """INV-sotiv: stable identity for ``kind="file"`` Symbols.

    Identity formula: ``sha256("file:{language}:{path}")[:16]``. Path is the
    repo-relative file path (set by ``all_analyzers``' normalisation pass).
    Two files at the same path but in different languages (rare — typically
    the language is determined by the file's extension) get distinct
    stable_ids; the path itself is the dominant identity component.
    """
    return _short_sha256(f"file:{language}:{path}")


def make_module_stable_id(language: str, path: str, name: str) -> str:
    """INV-sotiv: stable identity for ``kind="module"`` Symbols.

    Identity formula: ``sha256("module:{language}:{path}:{name}")[:16]``. The
    declaring file ``path`` is folded in (ADR-0035 §4 same-shape audit): module
    names are file-scoped local identifiers (e.g. two Verilog ``module counter``
    in distinct files), so without the path they collapse cross-file. Mirrors
    :func:`make_variable_stable_id` / :func:`make_export_stable_id`.
    """
    return _short_sha256(f"module:{language}:{path}:{name}")


def make_dependency_stable_id(language: str, path: str, name: str) -> str:
    """ADR-0035 §4: stable identity for ``kind="dependency"`` Symbols — one node per
    manifest declaration.

    Identity formula: ``sha256("dependency:{language}:{path}:{name}")[:16]``. The declaring
    manifest ``path`` is folded in (WI-titiz): a package declared in N manifests becomes N
    nodes, each keeping its own version constraint and provenance. ``(ecosystem, name)`` is a
    presentation-time aggregation key, never an identity key. The language namespace still
    separates ``requests`` (PyPI) from ``requests`` (npm). For a synthetic package stand-in
    with no declaring manifest (an unresolved import), callers pass ``path=""``.
    """
    return _short_sha256(f"dependency:{language}:{path}:{name}")


def make_variable_stable_id(language: str, path: str, name: str) -> str:
    """INV-sotiv: stable identity for ``kind="variable"`` Symbols.

    Identity formula: ``sha256("variable:{language}:{path}:{name}")[:16]``.
    Variables of the same name in different files get distinct stable_ids
    because the file path is the dominant identity component for a
    module-level binding.
    """
    return _short_sha256(f"variable:{language}:{path}:{name}")


def make_export_stable_id(language: str, path: str, name: str) -> str:
    """INV-sotiv: stable identity for ``kind="export"`` Symbols.

    Identity formula: ``sha256("export:{language}:{path}:{name}")[:16]``.
    Exports are file-scoped, so ``(path, name)`` is the natural identity.
    Bash function exports, Python ``__all__`` entries, and JS named
    exports all use this shape.
    """
    return _short_sha256(f"export:{language}:{path}:{name}")


def make_doc_stable_id(
    language: str, path: str, kind: str, name: str, start_line: int, end_line: int
) -> str:
    """Stable identity for documentation / config noise-node Symbols (id-format:F2 4a).

    Identity formula:
    ``sha256("doc:{language}:{path}:{kind}:{name}:{start_line}-{end_line}")[:16]``.

    Used by the markdown and gitignore analyzers for their ``section`` /
    ``code_block`` / ``link`` / ``pattern`` Symbols — all ``_NOISE_KINDS``
    (absent from default output). Unlike the name-scoped
    :func:`make_variable_stable_id` family (which deliberately omits the span so
    a binding's identity survives edits), the payload here folds in ``kind`` and
    the ``{start}-{end}`` span: these doc nodes routinely share a ``name``
    (anonymous code blocks are all ``"code"``; a file can repeat a heading), so
    the span is the only disambiguator and dropping it would collapse distinct
    siblings to one stable_id (the INV-dubam / INV-tazaj collision class). This
    preserves the per-node distinctness the previously-reused composite
    ``Symbol.id`` carried, while replacing its non-canonical shape with the
    canonical ``sha256:<16hex>``. Pass the same ``name`` and span that
    :func:`make_symbol_id` received at the call site so the 1:1 distinctness is
    exact. For single-line nodes (e.g. markdown links) pass the same value for
    both ``start_line`` and ``end_line``; the factory hashes the span verbatim
    and does not validate span plausibility.
    """
    return _short_sha256(
        f"doc:{language}:{path}:{kind}:{name}:{start_line}-{end_line}"
    )


def make_doc_symbol_ids(
    language: str, path: str, kind: str, name: str, start_line: int, end_line: int
) -> tuple[str, str]:
    """Mint the ``(node.id, stable_id)`` PAIR for a doc/markup/template Symbol
    together, from one argument set (INV-dulah).

    The doc/markup/template analyzer family (rst/scss/vue/svelte/puppet/robot/
    astro/twig/pony/sparql/kdl) each carried a near-duplicate local
    ``_make_symbol_id`` that built the raw composite ``Symbol.id``, then SEPARATELY
    called :func:`make_doc_stable_id` for the canonical ``stable_id``. Holding the
    two identities in two places is exactly the drift that let WI-rijup ship
    ``stable_id == raw id`` for nine of them. Minting both here, from the same
    ``(language, path, kind, name, start_line, end_line)`` tuple, makes that
    divergence unrepresentable, and dedups the eleven copies onto one definition.

    ``node.id`` is the canonical ADR-0036 grammar, minted by delegating to
    :func:`make_symbol_id` — this helper does NOT re-implement the format. The
    delegation is the point: an f-string copy of the grammar is exactly how
    ``js_ts.py`` and ``json_config.py`` each opted out of the shared minter's
    guarantees, and it is what let the doc family drift for months.

    **This shape changed (INV-dulah, doc-family slot-ORDER limb).** The helper
    previously emitted ``"{language}:{path}:{kind}:{start_line}:{name}"`` — kind
    third, no span, name last — deliberately not the documented grammar, on the
    recorded premise that "full grammar conformance for the doc family would
    re-key every node and is a separate, larger decision." Measurement retired
    that framing rather than the work: against the canonical right-anchored parse
    (``span, name, kind = parts[-3:]``) the *kind word landed in the span slot*,
    so every id from all eleven adopters failed ``id_format`` with
    ``malformed_span_segment`` and could not be parsed back into its slots at
    all. These were not ids in a different-but-workable convention; they were
    unparseable. The re-key is consumer-visible for ``node.id`` and identity-safe:
    ``stable_id`` below is computed from the same argument set either way, so it
    is byte-identical, and no ``*_scheme`` bumps (the scheme-bump principle — a
    bump follows an ALTERED existing computed value, and none is altered here).

    Delegating also inherits the WI-sikar always-on name-slot sanitization
    (``':' -> '.'``), which repairs a real six-segment break the old f-string
    shipped: svelte's ``on:click`` event symbols carried an id name-slot of
    ``button:click``, whose colon shifted every right-anchored slot.

    The markdown/gitignore analyzers were excluded from this helper *because* it
    diverged from the grammar; they already mint the canonical shape, so that
    reason is now spent and folding them on is a clean follow-up.

    ``stable_id`` is the canonical ``sha256:<16hex>`` from
    :func:`make_doc_stable_id` — unchanged for every adopter (they already called
    it with these same args).

    ``path`` is coerced with ``str()`` so callers may pass ``str`` or
    ``pathlib.Path`` (the family is split between the two); on POSIX the f-string
    interpolation the analyzers previously used produced the identical string.
    """
    spath = str(path)
    symbol_id = make_symbol_id(language, spath, start_line, end_line, name, kind)
    stable_id = make_doc_stable_id(language, spath, kind, name, start_line, end_line)
    return symbol_id, stable_id


def make_project_stable_id(name: str) -> str:
    """INV-sotiv: stable identity for ``kind="project"`` Symbols.

    Identity formula: ``sha256("project:{name}")[:16]``. Projects are
    repo-level and language-agnostic, so the bare name suffices.
    """
    return _short_sha256(f"project:{name}")


def make_interface_stable_id(language: str, path: str, name: str) -> str:
    """INV-sotiv: stable identity for ``kind="interface"`` Symbols.

    Identity formula: ``sha256("interface:{language}:{path}:{name}")[:16]``. The declaring file
    ``path`` is folded in (ADR-0035 §4): interface names are file-scoped declarations that
    routinely repeat across files (e.g. every TS file can declare ``interface Props``), so
    without the path they collapse cross-file. The language namespace still separates a C#
    ``IRepository`` from a TypeScript ``IRepository``.
    """
    return _short_sha256(f"interface:{language}:{path}:{name}")


def make_type_stable_id(language: str, path: str, name: str) -> str:
    """INV-sotiv: stable identity for ``kind="type"`` Symbols.

    Identity formula: ``sha256("type:{language}:{path}:{name}")[:16]``. The declaring file
    ``path`` is folded in (ADR-0035 §4): type/type-alias names are file-scoped and commonly
    repeat (``type Id``, ``type User``), so without the path they collapse cross-file. Used for
    named type declarations (Rust ``type`` aliases, TypeScript ``type`` statements, etc.).
    """
    return _short_sha256(f"type:{language}:{path}:{name}")


def make_declaration_stable_id(kind: str, language: str, path: str, name: str) -> str:
    """WI-rihob: stable identity for container/declaration Symbol kinds.

    Identity formula: ``sha256("{kind}:{language}:{path}:{name}")[:16]`` — the
    same file-scoped, name-in-hash shape as :func:`make_interface_stable_id` and
    :func:`make_type_stable_id` (ADR-0035 §4), generalized over ``kind``. Covers
    the named-declaration container kinds — ``class`` / ``struct`` / ``enum`` /
    ``trait`` / ``protocol`` / ``contract`` — that the tree-sitter producers
    (js_ts and its csharp/rust/swift/solidity/wgsl analogues) construct with a
    ``shape_id=`` but no ``stable_id=``, leaving them at ``None`` (measured:
    0/4 TypeScript classes, plus the TS enum, csharp class/struct/enum, rust
    enum/struct/trait, swift class/enum/protocol, solidity contract, wgsl struct).

    The declaring ``path`` is folded in because these names routinely repeat
    across files (every module can declare ``class Config``); ``kind`` is
    threaded into the hash so a ``class Widget`` and a same-named ``struct
    Widget`` in one file stay distinct (mirroring the kind-prefix that separates
    interface/type). ``language`` namespaces a C# ``IRepository`` from a
    TypeScript one.

    Per D3a/D3b (ADR-0035): this survives line drift and body edits (adding a
    method does not churn the class id — members are not in the hash) and churns
    on rename/move (name is in the hash; move-tracking is delegated to
    ``shape_id``/``fingerprint``). It is the backstop shape, not the typed tier:
    a class has no parameter signature, so — like its interface/type siblings —
    it uses the name-scoped key rather than :func:`make_typed_stable_id`.
    """
    return _short_sha256(f"{kind}:{language}:{path}:{name}")


def make_protocol_stable_id(category: str, *parts: str) -> str:
    """Phase 6 PR1 (INV-hunup): canonical-shape stable_id for protocol
    linker stand-ins.

    Identity formula: ``sha256("{category}:{parts joined by colon}")[:16]``.
    Used by linkers that emit ADR-0031 Class B synthetic Symbols where
    the previous code constructed the stable_id with an ad-hoc f-string
    (e.g., ``f"{queue_type}:{topic}"`` from message_queue or
    ``f"{type_name}.{field_name}"`` from graphql_resolver). The escape
    categories addressed:

    * ``raw_hex_64`` — full ``hashlib.sha256(...).hexdigest()`` without
      the ``sha256:`` prefix.
    * ``colon_1`` / ``colon_2`` — composite name with no namespace prefix.
    * ``no_colon`` — bare event/method name with no disambiguator.

    All four shapes now route through this factory, which guarantees the
    canonical ``sha256:<16hex>`` schema the new
    ``_check_stable_id_format`` validator (Phase 6 PR1) enforces.

    Args:
        category: Linker family namespace (e.g. ``"db_query"``,
            ``"message_queue"``, ``"event_sourcing"``,
            ``"graphql_resolver"``). Keeps two linkers' Symbols with
            structurally similar identity tuples from colliding.
        parts: Identity-carrying values. Stringified and concatenated
            with ``:`` separators before hashing. Order matters — the
            caller controls disambiguator priority.

    Returns:
        A ``sha256:<16hex>``-shaped string suitable for direct use as
        ``Symbol.stable_id``.
    """
    key = ":".join((category,) + tuple(parts))
    return _short_sha256(key)


def _declaration_kind_stable_id(sym: Symbol) -> str:
    """Backstop factory for container/declaration kinds (WI-rihob)."""
    return make_declaration_stable_id(sym.kind, sym.language, sym.path, sym.name)


# Mapping from Symbol.kind to the factory function used by
# populate_kind_stable_ids. Kinds NOT present here have producers that compute
# their own stable_id (function, method, route, and Python classes via
# ``_compute_stable_id``) or are absent from the measured-gap set and remain at
# None until a future invariant adds them.
#
# WI-rihob: the container/declaration kinds (class/struct/enum/trait/protocol/
# contract) are backstopped here. Despite the earlier assumption that "class
# computes its own", the tree-sitter producers (js_ts and its csharp/rust/swift/
# solidity/wgsl analogues) construct them with ``shape_id=`` but no
# ``stable_id=``, so they fell through to None; they share interface/type's
# file-scoped name-in-hash shape via :func:`make_declaration_stable_id`.
_KIND_STABLE_ID_FACTORIES = {
    "file": lambda s: make_file_stable_id(s.language, s.path),
    "module": lambda s: make_module_stable_id(s.language, s.path, s.name),
    "dependency": lambda s: make_dependency_stable_id(s.language, s.path, s.name),
    "variable": lambda s: make_variable_stable_id(s.language, s.path, s.name),
    "export": lambda s: make_export_stable_id(s.language, s.path, s.name),
    "project": lambda s: make_project_stable_id(s.name),
    "interface": lambda s: make_interface_stable_id(s.language, s.path, s.name),
    "type": lambda s: make_type_stable_id(s.language, s.path, s.name),
    "class": _declaration_kind_stable_id,
    "struct": _declaration_kind_stable_id,
    "enum": _declaration_kind_stable_id,
    "trait": _declaration_kind_stable_id,
    "protocol": _declaration_kind_stable_id,
    "contract": _declaration_kind_stable_id,
    # META-nomiz: view-template stand-ins (linkers/_view_template_core.py) mint
    # kind="template" with a language but no stable_id, falling through every other
    # backstop. They share the generic kind-scoped formula — kind is folded into the
    # hash, so a "template" node stays distinct from a "file" node at the same path.
    "template": _declaration_kind_stable_id,
}


def populate_kind_stable_ids(symbols: list[Symbol]) -> None:
    """INV-sotiv backstop: fill missing ``Symbol.stable_id`` by kind.

    Runs at the orchestrator chokepoint (``analyze.all_analyzers`` after
    path normalisation) and mutates ``symbols`` in place. For every
    Symbol whose ``stable_id`` is still ``None``, dispatches on
    ``Symbol.kind`` to a kind-specific factory and stamps the result.

    Producers that already computed a ``stable_id`` (functions and methods
    via ``make_typed_stable_id``; Python functions/methods/classes via
    py.py's ``_compute_stable_id``; routes via ``make_route_stable_id``; etc.)
    keep priority — this backstop never overrides a non-``None`` value.

    Kinds not in ``_KIND_STABLE_ID_FACTORIES`` are left untouched. The map
    covers the eight INV-sotiv kinds (file / module / dependency / variable /
    export / project / interface / type) plus the six WI-rihob container/
    declaration kinds (class / struct / enum / trait / protocol / contract) —
    the latter because the tree-sitter producers construct them with a
    ``shape_id=`` but no ``stable_id=`` (unlike Python, whose ``_compute_stable_id``
    covers classes at the producer). New kinds that surface with ``None`` need
    their own factory entry here.
    """
    for sym in symbols:
        if sym.stable_id is not None:
            continue
        factory = _KIND_STABLE_ID_FACTORIES.get(sym.kind)
        if factory is None:
            continue
        sym.stable_id = factory(sym)


def make_synthetic_symbol_identity(
    sym: Symbol, occurrence: int = 0,
) -> tuple[str, str, str]:
    """synthetic:F2 chokepoint resolver: derive ``(stable_id, display_label,
    fingerprint)`` for a Class-B synthetic stand-in Symbol from its already-set
    fields.

    **Injective stable_id (ADR-0035 §1 zero-by-design-collisions).** The key is
    ``(protocol_origin, kind, path, name, occurrence)`` hashed via
    :func:`make_protocol_stable_id`. Every component earns its place:

    * ``kind`` separates a *definition* from a *reference* to the same name
      (e.g. an ``@objc func`` from a ``#selector`` use-site).
    * ``path`` separates same-named stand-ins minted in different files.
    * ``occurrence`` — a deterministic within-``(protocol_origin, kind, path,
      name)`` index assigned by the caller — separates *role-distinct same-name
      siblings* the producing linker leaves otherwise-identical (e.g. a CRDT
      writer and an observer on the same channel, both ``kind="function"`` with
      ``name=channel``; or two call sites to the same target in one file).

    A coarser ``(protocol_origin, name)`` key (the first cut) manufactured
    by-design collisions on exactly those families — turning honest
    ``stable_id=None`` into a *wrong, colliding* value — so the key is the full
    injective tuple uniformly, not a per-kind LOGICAL/SITE branch. **Line
    numbers stay out of the hash** (occurrence, not line — the ADR-0035 §3 SITE
    rule applied uniformly): a freshly-stamped id churns only when same-key
    siblings in a file are added/removed/reordered, not on unrelated edits.

    ADR-0036 (lines 134-138) defers the lang-slot binding for ``language=None``
    synthetic node IDs to "the ``make_synthetic_symbol()`` chokepoint" — bound
    to ``discovery_language or language``. In 5a that binding does NOT enter the
    stable_id hash (``make_protocol_stable_id`` takes a *category*, not a
    language) and ``node.id`` is owned by the producing linker (kind-slot
    re-keying is the deferred 5b migration), so the binding is a recorded rule
    honored here and consumed by 5b — not a 5a hash input.
    """
    stable_id = make_protocol_stable_id(
        sym.protocol_origin, sym.kind, sym.path, sym.name, str(occurrence),
    )
    # display_label (ADR-0032): a human-readable stand-in label. The Symbol's
    # ``name`` already carries the protocol-qualified identity for these nodes,
    # so it is the honest display string. Stamping it closes META-huvuh's
    # producer half (display_label was null on Class-B).
    display_label = sym.name
    # fingerprint: the central ``stamp_symbol_fingerprints`` pass cannot
    # fingerprint a ``language=None`` symbol (no grammar), so stamp here,
    # matching the bare 16-hex shape the already-stamped Class-B linkers
    # (yjs_crdt / crypto_flow) use.
    fingerprint = hashlib.sha256(sym.id.encode()).hexdigest()[:16]
    return stable_id, display_label, fingerprint


def populate_synthetic_class_b_identity(symbols: list[Symbol]) -> None:
    """synthetic:F2 (5a): backstop identity/display stamping for Class-B
    synthetic protocol-synth Symbols.

    A Class-B stand-in (ADR-0031: ``language is None`` AND ``protocol_origin``
    set) minted by a linker may ship without ``stable_id`` / ``display_label`` /
    ``fingerprint`` (the ~7 zero-stable_id protocol linkers — ipc / openapi /
    phoenix_ipc / solidity_abi / swift_objc / websocket / wasm_bindgen — plus
    the unstamped subset of others such as yjs_crdt / crypto_flow). This
    post-*linker* orchestrator pass — a sibling to
    :func:`populate_kind_stable_ids`, run AFTER linkers extend ``symbols`` —
    fills those three fields via the :func:`make_synthetic_symbol_identity`
    chokepoint, each under a per-field skip-if-set guard so it NEVER overrides
    an existing value. Identity-neutral: self-stamping linkers (message_queue /
    event_sourcing / database_query) and any pre-existing value are preserved
    byte-for-byte; only ``None`` fields are filled.

    The stable_id key is injective over ``(protocol_origin, kind, path, name,
    occurrence)`` so two distinct Class-B nodes can never share a stable_id
    (ADR-0035 §1). The within-key ``occurrence`` index is pre-assigned here in a
    deterministic ``(span, id)`` order so it is stable run-to-run regardless of
    symbol-list order.

    WI-lidig: this pass NEVER writes ``supply_chain_tier`` /
    ``supply_chain_reason`` — the annotator re-classifies
    ``(tier==1 AND reason=='')`` nodes via the path classifier, and stamping a
    reason here would break that re-classification.
    """
    # Pre-assign occurrence indices for every Class-B null-stable_id node,
    # grouped by (protocol_origin, kind, path, name) and ordered deterministically
    # by (span, id), so role-distinct same-name siblings (and repeated sites) get
    # distinct, line-independent stable_ids.
    occurrence_of: dict[int, int] = {}
    null_class_b = [
        s for s in symbols
        if s.language is None and s.protocol_origin is not None
        and s.stable_id is None
    ]
    counters: dict[tuple[str | None, str, str, str], int] = {}
    for s in sorted(
        null_class_b,
        key=lambda s: (
            s.protocol_origin, s.kind, s.path, s.name,
            s.span.start_line if s.span else 0,
            s.span.start_col if s.span else 0,
            s.id,
        ),
    ):
        key = (s.protocol_origin, s.kind, s.path, s.name)
        occ = counters.get(key, 0)
        occurrence_of[id(s)] = occ
        counters[key] = occ + 1

    for sym in symbols:
        if sym.language is not None or sym.protocol_origin is None:
            continue  # not a Class-B synthetic stand-in
        stable_id, display_label, fingerprint = make_synthetic_symbol_identity(
            sym, occurrence=occurrence_of.get(id(sym), 0),
        )
        if sym.stable_id is None:
            sym.stable_id = stable_id
        if sym.display_label is None:
            sym.display_label = display_label
        if sym.fingerprint is None:
            sym.fingerprint = fingerprint


# ADR-0035 §3 LOGICAL families: external targets that exist once regardless of
# how many code sites reference them (a message-queue topic, an event channel).
# Their linkers mint one Class-B stand-in per reference SITE — all sharing the
# LOGICAL stable_id — so they dedup to one hub node per logical thing. (SITE-axis
# stand-ins like http / sql call_sites carry a DIFFERENT protocol_origin and are
# occurrence-indexed instead, below.)
#
# graphql is deliberately EXCLUDED. The `graphql` protocol_origin is shared by two
# producers on DIFFERENT identity axes: graphql_resolver.py (resolver fields, a
# canonical sha256 LOGICAL key) AND graphql.py's client call-sites (one stand-in per
# call, keyed on the raw operation_name — SITE-axis). A coarse origin-only dedup
# would silently collapse distinct client call sites (and their per-call metadata)
# across files, so graphql collisions flow to the SITE occurrence-index pass instead;
# the cross-file graphql residue rides the same tracked file-identity follow-up as the
# top-level tree-sitter (INV-zudob) family.
_LOGICAL_DEDUP_PROTOCOL_ORIGINS = frozenset({"message_queue", "event_sourcing"})


def _occurrence_sort_key(sym: Symbol) -> tuple[int, int, int, int, str]:
    """Deterministic within-group order (ADR-0043 §6): span position, then id.

    A span-less Symbol sorts as position zero — identical to the degenerate
    ``Span(0, 0, 0, 0)`` that ``Symbol.from_dict`` used to fabricate for a
    missing span — with ``sym.id`` as the deterministic tie-breaker.
    """
    sp = sym.span
    if sp is None:
        return (0, 0, 0, 0, sym.id)
    return (sp.start_line, sp.start_col, sp.end_line, sp.end_col, sym.id)


def dedup_logical_synthetic_identities(
    symbols: list[Symbol], edges: list[Edge]
) -> int:
    """ADR-0035 §3 LOGICAL dedup: collapse duplicate external-target stand-ins.

    Message-queue / event-channel topics are LOGICAL nodes — "one node per logical
    thing, deduped" (ADR-0035 §3). Their linkers emit one Class-B Symbol per
    reference SITE (each with its own ``id``), all sharing the LOGICAL
    ``stable_id``. (graphql is excluded — see
    :data:`_LOGICAL_DEDUP_PROTOCOL_ORIGINS`.) This collapses each duplicate group to one
    survivor (deterministic by span/id) and rewires EVERY edge endpoint
    (``src`` / ``dst`` / ``derived_from``) that pointed at a dropped node to the
    survivor — so a topic's full publisher/subscriber connectivity lands on the
    one hub node (the caller's ``deduplicate_edges`` then collapses the now-
    identical rewired edges). Returns the count of dropped duplicate Symbols.

    Runs post-linker (AFTER the enclosure post-pass wires per-site ``uses`` edges,
    so they survive on the hub) and BEFORE ``finalize`` (R1: set membership is
    final on finalize entry). Keyed on ``stable_id`` so only true logical
    duplicates collapse; SITE-axis stand-ins (http / sql call_sites, whose
    protocol_origin is not in :data:`_LOGICAL_DEDUP_PROTOCOL_ORIGINS`) are left
    for :func:`split_within_file_stable_id_collisions`.
    """
    groups: dict[str, list[Symbol]] = {}
    for sym in symbols:
        if (
            sym.protocol_origin in _LOGICAL_DEDUP_PROTOCOL_ORIGINS
            and sym.stable_id is not None
        ):
            groups.setdefault(sym.stable_id, []).append(sym)
    id_remap: dict[str, str] = {}
    dropped: set[int] = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        survivor = min(group, key=_occurrence_sort_key)
        for sym in group:
            if sym is survivor:
                continue
            id_remap[sym.id] = survivor.id
            dropped.add(id(sym))
    if not id_remap:
        return 0
    symbols[:] = [s for s in symbols if id(s) not in dropped]
    for edge in edges:
        changed = False
        if edge.src in id_remap:
            edge.src = id_remap[edge.src]
            changed = True
        if edge.dst in id_remap:
            edge.dst = id_remap[edge.dst]
            changed = True
        if edge.derived_from and any(x in id_remap for x in edge.derived_from):
            edge.derived_from = [id_remap.get(x, x) for x in edge.derived_from]
        if changed:
            # Force the caller's deduplicate_edges to recompute the line-
            # insensitive key from the rewritten endpoints.
            edge.edge_key = None
    return len(dropped)


def split_within_file_stable_id_collisions(symbols: list[Symbol]) -> int:
    """ADR-0035 §3 SITE occurrence-indexing: disambiguate within-file collisions.

    Within one file, two emitted Symbols sharing a ``stable_id`` are distinct code
    SITES (repeated SQL/HTTP call sites, repeated shell ``export``s, repeated
    markdown links, manifest script entries, …). v6 leaves them colliding because
    the producers do not populate an occurrence index. This pass groups by
    ``(path, stable_id)`` and, for each colliding group, re-mints the 2nd-and-later
    members (ordered deterministically by span/id) with a ``:occ:<n>`` suffix
    re-hash — the 1st keeps the original id. Line numbers stay OUT of identity
    (§3); the occurrence ordinal goes in. Returns the count of re-minted ids.

    Per ADR-0035 §6 the occurrence-disambiguation slot was reserved precisely so
    this needs no scheme bump (it stays scheme v6); only the colliding symbols'
    values change. LOGICAL stand-ins are already collapsed by
    :func:`dedup_logical_synthetic_identities` (run first), so they present no
    within-file collision here. Pathless / null-stable_id Symbols are out of
    scope (this is the in-a-file guarantee).
    """
    groups: dict[tuple[str, str], list[Symbol]] = {}
    for sym in symbols:
        if sym.stable_id is None or not sym.path:
            continue
        groups.setdefault((sym.path, sym.stable_id), []).append(sym)
    reminted = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        for occurrence, sym in enumerate(sorted(group, key=_occurrence_sort_key)):
            if occurrence == 0:
                continue
            sym.stable_id = _short_sha256(f"{sym.stable_id}:occ:{occurrence}")
            reminted += 1
    return reminted


def _is_route_for_widening(sym: Symbol) -> bool:
    """Identify LOGICAL ``route`` Symbols whose file-blind v6 id needs the v8 fold.

    Route stand-ins are emitted across analyzers/linkers with three non-uniform
    markers (no single one covers every producer, verified against the
    ``make_route_stable_id`` call sites): the ``make_symbol_id`` kind suffix
    ``:route`` / ``:route_mount``; ``meta["framework_role"]`` ==
    ``"route"`` / ``"route_mount"``; or a ``meta`` carrying both ``route_path``
    and ``http_method`` (the synthesized per-route handler ``function`` nodes,
    which use a ``:function`` id suffix and set no framework_role). Their union
    covers every producer (py / go / js_ts / framework materializer / grpc). SITE
    ``call_site`` stand-ins are EXCLUDED — their file-identity fold happens at mint
    via :func:`make_site_stable_id` (WI-napoh), not here.
    """
    if sym.kind == "call_site":
        return False
    meta = sym.meta or {}
    if meta.get("framework_role") in ("route", "route_mount"):
        return True
    if meta.get("route_path") is not None and meta.get("http_method"):
        return True
    sid = sym.id or ""
    return ":" in sid and sid.rsplit(":", 1)[-1] in ("route", "route_mount")


def widen_route_stable_ids(symbols: list[Symbol]) -> int:
    """ADR-0035 §3 + WI-gokiv (v8): fold file identity into LOGICAL ``route`` ids.

    :func:`make_route_stable_id` keys a route on ``route:{method}:{path}`` only —
    file-blind by the ADR-0014 §4 contract carried into v6. The Wave-2 identity
    gate showed this is the dominant corpus-collision class: the same
    ``(method, path)`` declared in different files (express's 33 example apps) or
    different LANGUAGES (gin-Go + express-JS both minting ``route:GET:/``) shares
    one stable_id though the registrations are semantically distinct. The
    2026-06-16 ruling: route stays LOGICAL *in spirit* (within-app dedup is owned
    structurally by the route materializer's ``seen_routes``, keyed on meta — not
    by stable_id), but the IDENTITY widens with the declaring file + language so
    cross-file / cross-language routes no longer collide.

    This is a post-pass (mirroring :func:`split_within_file_stable_id_collisions`)
    rather than a per-producer change because the ~16 ``make_route_stable_id`` call
    sites carry the file path as an ABSOLUTE string at mint, whereas ``sym.path``
    is normalised to the repo-relative form only later (``all_analyzers``' path
    pass). Folding the *normalised* ``sym.path`` here is the single point that is
    both complete (no straggler producer) and location-independent (no absolute
    path baked into identity — the regression WI-bokab's location-independence
    guard forbids). Must run BEFORE :func:`split_within_file_stable_id_collisions`
    so two same-route declarations in one file (now sharing the folded id) get the
    ``:occ:<n>`` ordinal. Returns the count of re-minted ids.
    """
    widened = 0
    for sym in symbols:
        if sym.stable_id is None or not sym.path:
            continue
        if not _is_route_for_widening(sym):
            continue
        sym.stable_id = _short_sha256(
            f"route_site:{sym.language or ''}:{normalize_path(sym.path)}:{sym.stable_id}"
        )
        widened += 1
    return widened


def make_typed_stable_id(
    kind: str,
    normalized_signature: str,
    visibility: str = "",
    containing_stable_id: str = "",
    decorators: str = "",
    *,
    name: str,
    qualified_name: str,
    file_stable_id: str = "",
) -> str:
    """Compute a typed-tier stable_id from a normalized signature.

    v6 formula (ADR-0014 §3 + ADR-0035 §1 WI-zitod ``name``/``qualified_name`` slots)::

        sha256({kind}:{normalized_signature}:{visibility}:{decorators}
               :{containing_stable_id}:{name}:{qualified_name})

    The typed tier is preferred when type information is available (e.g. Java,
    C#, Kotlin, TypeScript, Dart, Go, Python with annotations).  It produces
    higher-quality identity than the untyped tier because it captures the full
    interface shape including parameter and return types.

    ``name`` and ``qualified_name`` are MANDATORY keyword args (ADR-0035 §1 WI-zitod):
    without them two distinct-named symbols with the same normalized signature,
    visibility, decorators, and container collapsed to one id (e.g. js_ts methods at
    16.88% on HTRAC). ``qualified_name`` carries the full enclosing scope chain — the same
    role it plays in :func:`assemble_stable_id`. They have no default so a producer that
    forgets them fails loudly rather than silently re-opening the collision.

    Decorators/annotations are included because they change runtime behavior
    (e.g. ``@staticmethod``, ``@lru_cache``, ``@Override``).  Two functions
    with identical signatures but different decorators are semantically
    distinct and must receive different stable_ids.

    Args:
        kind: Symbol kind (``"function"``, ``"method"``, ``"class"``).
        normalized_signature: Output of a ``normalize_*_signature()`` function
            (e.g. ``"(String,int)User"``).  Must already be normalized.
        visibility: Access modifier (``"public"``, ``"private"``, ``"protected"``).
            Empty string for languages without access modifiers (Python, Go).
        containing_stable_id: Stable ID of the enclosing scope (class or
            module).  Empty string for top-level definitions.
        decorators: Sorted, comma-joined decorator/annotation names (e.g.
            ``"Override,Test"``).  Empty string when no decorators are present.
        name: Symbol's local name (mandatory; the WI-zitod disambiguator).
        qualified_name: Full scope-qualified name (mandatory; carries the scope chain).
        file_stable_id: WI-bokab (v7) file-identity anchor. When ``containing_stable_id``
            is empty (the tree-sitter producers carry scope in ``qualified_name`` rather
            than threading an enclosing-scope id), this value is folded into the
            ``containing_stable_id`` slot so same-``(kind, name, qualified_name)`` symbols
            in different files hash distinctly — mirroring py.py's ``file_containing_id``
            (ADR-0035 §1/§4). Produce it via :meth:`TreeSitterAnalyzer._file_anchor`. An
            explicit non-empty ``containing_stable_id`` always wins.

    Returns:
        Stable ID in ``sha256:{16-hex-chars}`` format.
    """
    # WI-bokab (v7): fold the declaring file's identity for file-resident symbols
    # that don't already carry an enclosing scope. Additive — tree-sitter producers
    # never pass a real containing_stable_id today.
    if not containing_stable_id and file_stable_id:
        containing_stable_id = file_stable_id
    sig = (
        f"{kind}:{normalized_signature}:{visibility}"
        f":{decorators}:{containing_stable_id}:{name}:{qualified_name}"
    )
    return _short_sha256(sig)


_VISIBILITY_MODIFIERS = frozenset({"public", "private", "protected", "internal"})

# WI-zimum: modifiers that denote externally-reachable / public-API symbols
# across languages.
#
# - ``public``:     Java, C#, Kotlin, Scala, Groovy, TypeScript class members
# - ``exported``:   Go naming-convention synthetic modifier (see
#                   ``_go_visibility_modifiers`` — identifiers starting with
#                   an uppercase letter get this tag)
# - ``pub`` and ``pub(...)``:
#                   Rust. The full ``pub(crate)`` / ``pub(super)`` /
#                   ``pub(in path)`` forms all count as "exported" for the
#                   purposes of WI-zimum because they are reachable from at
#                   least one external module — they are public relative to
#                   the file they are defined in, which is what the dead-code
#                   seed set cares about.
_EXPORTED_MODIFIERS_EXACT = frozenset({"public", "exported", "pub"})


def visibility_from_modifiers(modifiers: list[str] | None) -> str:
    """Extract the visibility modifier from a list of modifiers.

    Returns the first visibility keyword found (``"public"``, ``"private"``,
    ``"protected"``, ``"internal"``), or an empty string if none is present.
    Languages without visibility modifiers (Python, Go) will have empty
    modifier lists or lists without visibility keywords.
    """
    if not modifiers:
        return ""
    for m in modifiers:
        if m in _VISIBILITY_MODIFIERS:
            return m
    return ""


def is_exported_from_modifiers(modifiers: list[str] | None) -> bool:
    """Return True if *modifiers* denotes an externally-reachable symbol.

    Recognises the cross-language set from `_EXPORTED_MODIFIERS_EXACT` plus
    Rust's qualified ``pub(...)`` forms (``pub(crate)``, ``pub(super)``,
    ``pub(in ::path)``). A symbol is considered "exported" for the purposes
    of WI-zimum (dead-code seed set) when ANY of its modifiers match.

    Languages without visibility modifiers pass an empty list and this
    returns False — the per-language analyzer is expected to set the
    ``Symbol.is_exported`` field directly when it has its own rule
    (e.g. Python ``__all__``, TypeScript top-level ``export``).
    """
    if not modifiers:
        return False
    for m in modifiers:
        if m in _EXPORTED_MODIFIERS_EXACT:
            return True
        # Rust qualified pub(crate) / pub(super) / pub(in ...)
        if m.startswith("pub("):
            return True
    return False


# ---------------------------------------------------------------------------
# Doc comment extraction
# ---------------------------------------------------------------------------

_DOC_COMMENT_TYPES = frozenset({
    "comment",
    "block_comment",
    "line_comment",
    "multiline_comment",
})

# Prefixes to strip from comment text, in order of specificity.
_COMMENT_STRIP_RE = _re.compile(
    r"^\s*(?:"
    r"/\*\*\s*"   # /** (Javadoc/JSDoc/KDoc/PHPDoc opener)
    r"|\*/\s*"    # */ (block closer)
    r"|\*\s?"     # * (block continuation line)
    r"|///\s?"    # /// (Rust/C#/Swift)
    r"|//!\s?"    # //! (Rust inner doc)
    r"|//\s?"     # // (Go)
    r"|%+\s?"     # % or %% (Erlang)
    r"|#\s?"      # # (Ruby/Python/Elixir)
    r")"
)

# Tag lines to skip (Javadoc @param, @returns, etc.)
_DOC_TAG_RE = _re.compile(r"^\s*@\w+")


def extract_doc_comment(
    node: "tree_sitter.Node",
    source: bytes,
    max_len: int = 80,
) -> str | None:
    """Extract first-line summary of the doc comment preceding a declaration node.

    Walks backwards through prev_named_sibling collecting consecutive comment
    nodes with no blank-line gap.  Cleans comment delimiters and returns the
    first non-empty content line, truncated to *max_len*.

    Works across languages: handles ``/** */`` (Java/Kotlin/JS/PHP),
    ``///`` (Rust/C#/Swift), ``//`` (Go), and ``#`` (Ruby) comment styles.
    """
    # Collect comment nodes walking backwards from the declaration
    comment_nodes: list["tree_sitter.Node"] = []
    prev = getattr(node, "prev_named_sibling", None)
    while prev is not None and prev.type in _DOC_COMMENT_TYPES:
        # Stop on blank-line gap (more than 1 line between consecutive nodes)
        if comment_nodes:
            last_collected = comment_nodes[-1]
            if last_collected.start_point[0] - prev.end_point[0] > 1:
                break
        comment_nodes.append(prev)
        prev = prev.prev_named_sibling

    if not comment_nodes:
        return None

    # Reverse so we process top-to-bottom
    comment_nodes.reverse()

    # Decode and clean comment text
    for cnode in comment_nodes:
        raw = source[cnode.start_byte:cnode.end_byte].decode("utf-8", errors="replace")
        for raw_line in raw.split("\n"):
            line = _COMMENT_STRIP_RE.sub("", raw_line)
            # Strip trailing block-comment closer
            if line.rstrip().endswith("*/"):
                line = line.rstrip()[:-2]
            line = line.strip()
            # Skip empty lines, closing delimiters, and tag lines
            if not line or line == "/" or _DOC_TAG_RE.match(line):
                continue
            # Found the first content line — truncate and return
            if len(line) > max_len:
                return line[: max_len - 1] + "\u2026"
            return line

    return None


def populate_docstrings_from_tree(
    root_node: "tree_sitter.Node",
    source: bytes,
    symbols: list[Symbol],
) -> None:
    """Populate docstrings for symbols by finding their tree-sitter nodes via position.

    For each symbol that lacks a docstring, uses the symbol's span to locate
    the corresponding tree-sitter node via ``named_descendant_for_point_range``,
    then extracts the doc comment preceding that node.

    This enables docstring extraction for analyzers that don't populate
    ``node_for_symbol`` — the span's start position is enough to reverse-lookup
    the declaration node, since all analyzers set ``start_col`` from
    ``node.start_point[1]``.

    When the lookup returns a container node (e.g. a class body) rather than
    the declaration itself, drills down through named children that start at
    the target position until the actual declaration node is found.  This
    handles languages like Scala, Groovy, Ruby, and Elixir where the tree
    structure nests declarations inside body nodes.
    """
    for sym in symbols:
        if sym.docstring is not None or sym.span is None:
            continue
        start = (sym.span.start_line - 1, sym.span.start_col)
        node = root_node.named_descendant_for_point_range(start, start)
        if node is None:
            continue
        # Drill down: when the lookup returns a container node (e.g.
        # template_body in Scala) instead of the declaration itself,
        # find the named child whose start point matches the target.
        while True:
            refined = None
            for child in node.named_children:
                if child.start_point == start:
                    refined = child
                    break
            if refined is None:
                break
            node = refined
        # Walk up: when drill-down lands on a leaf (e.g. primitive_type
        # in C/C++), walk up through ancestors on the same line to find
        # the declaration node whose prev_named_sibling is a comment.
        candidate = node
        while candidate is not None:
            doc = extract_doc_comment(candidate, source)
            if doc is not None:
                sym.docstring = doc
                break
            parent = candidate.parent
            if parent is None or parent.start_point[0] != start[0]:
                break
            candidate = parent


# ---------------------------------------------------------------------------
# Signature normalization utilities (ADR-0014 §3)
# ---------------------------------------------------------------------------


def strip_fqn_prefix(type_name: str) -> str:
    """Strip fully-qualified name prefix, keeping just the simple name.

    Handles dotted paths like ``java.lang.String`` → ``String``
    and ``System.Collections.Generic.List`` → ``List``.
    Preserves generic parameters: ``java.util.List<String>`` → ``List<String>``.

    Does NOT strip if the name contains no dots (already simple).
    """
    # Split at first '<' to preserve generic params
    if "<" in type_name:
        base, rest = type_name.split("<", 1)
        return strip_fqn_prefix(base) + "<" + rest
    if "[" in type_name:
        # Go/Scala square-bracket generics
        base, rest = type_name.split("[", 1)
        return strip_fqn_prefix(base) + "[" + rest
    if "." in type_name:
        return type_name.rsplit(".", 1)[-1]
    return type_name


# Regex to match type parameter names in generic brackets.
# Matches single uppercase letter or conventional names like T1, TKey, etc.
_TYPE_PARAM_RE = _re.compile(r"\b([A-Z][A-Za-z0-9]*)\b")


def normalize_generic_params(
    text: str,
    type_params: list[str],
) -> str:
    """Replace declared type parameter names with positional markers.

    ``T, U`` → ``$0, $1`` within the given text.  Only replaces
    names that exactly match a declared type parameter — concrete
    type names like ``String`` or ``Integer`` are untouched.

    Args:
        text: The signature text to transform.
        type_params: Ordered list of declared type parameter names
            (e.g., ``["T", "U", "V"]``).

    Returns:
        Transformed text with type params replaced by positional markers.
    """
    if not type_params:
        return text
    mapping = {tp: f"${i}" for i, tp in enumerate(type_params)}

    def _replace(m: _re.Match[str]) -> str:
        name = m.group(1)
        return mapping.get(name, name)

    return _TYPE_PARAM_RE.sub(_replace, text)


def split_params_top_level(params_str: str) -> list[str]:
    """Split a parameter list by commas, respecting generic nesting.

    ``"Map<String, Integer>, int"`` → ``["Map<String, Integer>", "int"]``

    Handles ``<>``, ``[]``, and ``()`` nesting.  Leading/trailing
    whitespace on each part is stripped.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in params_str:
        if ch in ("<", "[", "("):
            depth += 1
            current.append(ch)
        elif ch in (">", "]", ")"):
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _normalize_type(raw_type: str, type_params: list[str] | None) -> str:
    """Apply FQN stripping and generic-param normalization to a single type."""
    t = strip_fqn_prefix(raw_type.strip())
    if type_params:
        t = normalize_generic_params(t, type_params)
    return t


def normalize_signature_types_first(
    signature: str | None,
    type_params: list[str] | None = None,
    *,
    skip_void_return: bool = True,
    return_sep: str = "",
) -> str | None:
    """Normalize a signature where params are ``Type name`` (Java, C#, Dart, Groovy).

    Input format: ``(Type name, Type name) ReturnType``
    or with return_sep ``":"``: ``(Type name, Type name): ReturnType``
    Output format: ``(Type,Type)ReturnType``

    Strips parameter names, FQN prefixes, and normalizes generic type
    parameters by position.  Used by Java, C#, Dart, Groovy, Objective-C.
    """
    if not signature:
        return None

    # Split into params part and return type
    paren_close = _find_matching_paren(signature, 0)
    if paren_close < 0:
        return None

    params_str = signature[1:paren_close]
    rest = signature[paren_close + 1:].strip()

    # Extract return type, stripping separator if present
    if return_sep and rest.startswith(return_sep):
        return_str = rest[len(return_sep):].strip()
    else:
        return_str = rest

    # Parse params: "Type name" → extract Type
    raw_params = split_params_top_level(params_str)
    types: list[str] = []
    for p in raw_params:
        p = p.strip()
        if not p:
            continue
        # Handle varargs: "Type... name"
        if "..." in p:
            p = p.replace("...", " ").strip()
        # Type comes first, name is last space-separated token
        # But type itself may have spaces (e.g., "unsigned int x" in C)
        # For most languages, split on last space
        parts = p.rsplit(None, 1)
        if len(parts) == 2:
            types.append(_normalize_type(parts[0], type_params))
        else:
            # Single token — it's either just a type or just a name
            types.append(_normalize_type(parts[0], type_params))

    norm_params = ",".join(types)

    if return_str and not (skip_void_return and return_str.lower() == "void"):
        norm_return = _normalize_type(return_str, type_params)
        return f"({norm_params}){norm_return}"
    return f"({norm_params})"


def normalize_signature_names_first(
    signature: str | None,
    type_params: list[str] | None = None,
    *,
    return_sep: str = ":",
    skip_self: bool = False,
) -> str | None:
    """Normalize a signature where params are ``name: Type`` (Kotlin, Scala, TS, Swift, Rust, Python).

    Input format: ``(name: Type, name: Type): ReturnType`` or
                  ``(name: Type, name: Type) -> ReturnType``
    Output format: ``(Type,Type)ReturnType``

    Args:
        signature: The raw signature string.
        type_params: Declared generic type parameter names.
        return_sep: Separator before return type (``":"`` or ``"->"``)
        skip_self: If True, skip ``self``/``cls`` parameters (Python/Rust).
    """
    if not signature:
        return None

    # Split into params part and return type
    paren_close = _find_matching_paren(signature, 0)
    if paren_close < 0:
        return None

    params_str = signature[1:paren_close]
    rest = signature[paren_close + 1:].strip()

    # Extract return type
    return_str = ""
    if rest.startswith(return_sep):
        return_str = rest[len(return_sep):].strip()
    elif rest.startswith("->"):
        return_str = rest[2:].strip()
    elif rest:
        return_str = rest.strip()

    # Parse params: "name: Type" → extract Type
    raw_params = split_params_top_level(params_str)
    types: list[str] = []
    for p in raw_params:
        p = p.strip()
        if not p:
            continue
        # Skip self/cls parameters
        if skip_self and p in ("self", "cls", "&self", "&mut self"):
            continue
        # Find the colon separator — "name: Type"
        colon_idx = p.find(":")
        if colon_idx >= 0:
            type_part = p[colon_idx + 1:].strip()
            types.append(_normalize_type(type_part, type_params))
        else:
            # No type annotation (e.g., bare name in Python)
            # Include as-is for untyped params
            types.append(p.strip())

    norm_params = ",".join(types)

    if return_str:
        norm_return = _normalize_type(return_str, type_params)
        return f"({norm_params}){norm_return}"
    return f"({norm_params})"


def normalize_signature_php(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a PHP signature: ``(Type $name, Type $name): ReturnType``.

    Output format: ``(Type,Type)ReturnType``
    """
    if not signature:
        return None

    paren_close = _find_matching_paren(signature, 0)
    if paren_close < 0:
        return None

    params_str = signature[1:paren_close]
    rest = signature[paren_close + 1:].strip()

    return_str = ""
    if rest.startswith(":"):
        return_str = rest[1:].strip()

    raw_params = split_params_top_level(params_str)
    types: list[str] = []
    for p in raw_params:
        p = p.strip()
        if not p:
            continue
        # PHP format: "Type $name" or "Type $name = ..." or "$name" (untyped)
        dollar_idx = p.find("$")
        if dollar_idx > 0:
            type_part = p[:dollar_idx].strip()
            types.append(_normalize_type(type_part, type_params))
        else:
            # Untyped param: "$name" or just name
            types.append(p.strip())

    norm_params = ",".join(types)

    if return_str and return_str.lower() != "void":
        norm_return = _normalize_type(return_str, type_params)
        return f"({norm_params}){norm_return}"
    return f"({norm_params})"


def normalize_signature_go(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Go signature: ``(name Type, name Type) ReturnType``.

    Go has a unique convention where multiple names can share a type:
    ``(a, b int)`` means both are ``int``.  The return type may be
    a tuple: ``(int, error)``.

    Output format: ``(Type,Type)ReturnType``
    """
    if not signature:
        return None

    paren_close = _find_matching_paren(signature, 0)
    if paren_close < 0:
        return None

    params_str = signature[1:paren_close]
    rest = signature[paren_close + 1:].strip()

    # Return type in Go — could be "(Type, Type)" or "Type"
    return_str = rest.strip()

    # Parse params — Go format: "name Type" where Type is the last token
    raw_params = split_params_top_level(params_str)
    types: list[str] = []
    for p in raw_params:
        p = p.strip()
        if not p:
            continue
        # Strip pointer: "*Type" → "Type"
        parts = p.split()
        if len(parts) >= 2:
            # Last token is the type
            raw_type = parts[-1]
            if raw_type.startswith("*"):
                raw_type = raw_type[1:]
            types.append(_normalize_type(raw_type, type_params))
        else:
            # Single token — might be a type with no name
            raw_type = parts[0]
            if raw_type.startswith("*"):
                raw_type = raw_type[1:]
            types.append(_normalize_type(raw_type, type_params))

    norm_params = ",".join(types)

    if return_str:
        # Normalize return type (strip pointer prefixes, FQN)
        ret = return_str
        if ret.startswith("(") and ret.endswith(")"):
            # Tuple return: "(int, error)" → normalize each
            inner = ret[1:-1]
            ret_parts = split_params_top_level(inner)
            norm_ret = "(" + ",".join(
                _normalize_type(r.strip().lstrip("*"), type_params)
                for r in ret_parts
            ) + ")"
            return f"({norm_params}){norm_ret}"
        if ret.startswith("*"):
            ret = ret[1:]
        norm_return = _normalize_type(ret, type_params)
        return f"({norm_params}){norm_return}"
    return f"({norm_params})"


def _find_matching_paren(s: str, start: int) -> int:
    """Find the index of the closing paren matching the opening paren at *start*.

    Returns -1 if not found or *s[start]* is not ``(``.
    """
    if start >= len(s) or s[start] != "(":
        return -1
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


# ---------------------------------------------------------------------------
# Grammar availability checking
# ---------------------------------------------------------------------------


def is_grammar_available(grammar_module: str) -> bool:
    """Check if a tree-sitter grammar is available.

    Args:
        grammar_module: The grammar module name (e.g., "tree_sitter_go")

    Returns:
        True if both tree_sitter and the grammar module are importable.
    """
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec(grammar_module) is None:
        return False
    return True


# ---------------------------------------------------------------------------
# Iterative tree traversal (avoids RecursionError on deeply nested code)
# ---------------------------------------------------------------------------


def iter_tree(root: "tree_sitter.Node") -> Iterator["tree_sitter.Node"]:
    """Iterate over all nodes in a tree-sitter tree without recursion.

    Uses an explicit stack to avoid RecursionError on deeply nested code
    (e.g., TensorFlow has files exceeding Python's 1000-level limit).

    Args:
        root: The root node of the tree to traverse

    Yields:
        Each node in depth-first order.

    Example:
        for node in iter_tree(tree.root_node):
            if node.type == "function_definition":
                # process function...
    """
    stack: list["tree_sitter.Node"] = [root]
    while stack:
        node = stack.pop()
        yield node
        # Add children in reverse order so leftmost is processed first
        stack.extend(reversed(node.children))


def iter_tree_with_context(
    root: "tree_sitter.Node",
    context_types: set[str],
) -> Iterator[tuple["tree_sitter.Node", Optional["tree_sitter.Node"]]]:
    """Iterate over nodes with parent context tracking.

    Useful for edge extraction where we need to know the enclosing
    function/method when processing call expressions.

    Args:
        root: The root node of the tree to traverse
        context_types: Node types that establish context (e.g., {"function_definition"})

    Yields:
        Tuples of (node, context_node) where context_node is the nearest
        ancestor matching one of context_types, or None if outside any context.

    Example:
        for node, func_ctx in iter_tree_with_context(tree.root_node, {"function_definition"}):
            if node.type == "call_expression" and func_ctx:
                # We know which function contains this call
    """
    # Stack entries: (node, current_context)
    stack: list[tuple["tree_sitter.Node", Optional["tree_sitter.Node"]]] = [
        (root, None)
    ]
    while stack:
        node, context = stack.pop()

        # Update context if this node is a context type
        new_context = node if node.type in context_types else context

        yield node, context

        # Add children with updated context
        for child in reversed(node.children):
            stack.append((child, new_context))


# ---------------------------------------------------------------------------
# File discovery helpers
# ---------------------------------------------------------------------------


def emit_module_attribute_refs(
    root: "tree_sitter.Node",
    source: bytes,
    imports: dict[str, str],
    caller_symbol: Symbol,
    lang: str,
    edges_out: list[Edge],
    *,
    node_kinds: tuple[str, ...],
    object_field_names: tuple[str, ...],
    property_field_names: tuple[str, ...],
    pass_id: str,
    run_id: str,
    call_node_kinds: tuple[str, ...] = ("call_expression", "call",),
    call_function_field_names: tuple[str, ...] = ("function", "callee",),
    scoped_path: bool = False,
) -> None:
    """Emit ``module_attr_ref`` edges for attribute reads on imported modules.

    This is the tree-sitter counterpart of the per-language helper that
    ships inside ``py.py`` (see WI-guhok).  It targets bare attribute
    accesses like ``process.env.PATH`` (JS), ``System.out`` (Java), or
    ``os.Stdout`` (Go) — an imported-or-global module name followed by
    one or more attribute accesses whose leftmost access is NOT itself
    the callee of a function call.  Callee attribute accesses such as
    ``os.getenv("X")`` or ``System.out.println("x")`` already produce
    ``calls`` edges and would be double-counted if emitted here.

    The emission pairs with ``attributes:`` entries in
    ``io_primitives/*.yaml`` (WI-guhok for Python; this helper extends
    the same mechanism to all tree-sitter-based analyzers — WI-gapam).
    Without an edge to match, ``io-boundaries`` silently under-reports
    ``env_read`` / ``ipc_send`` / ``ipc_recv`` chains on any attribute
    primitive.

    Args:
        root: The tree-sitter node to walk.  Pass the file root for
            module-level reads, or a function body for per-function
            emission.
        source: Raw source bytes used for ``node_text``.
        imports: Map of local-alias name to the fully-qualified module
            name (e.g. ``{"process": "process"}`` in JS,
            ``{"os": "os"}`` in Go).  Base names that are not in this
            map are skipped.
        caller_symbol: The symbol containing the attribute read (used
            as the edge ``src``).
        lang: Language tag for the synthetic edge destination
            (e.g. ``"javascript"`` / ``"java"`` / ``"go"``) — matches
            the ``language`` field of module-attribute sink IDs.
        edges_out: The edge list to append to — mutated in place.
        node_kinds: Tree-sitter node types that represent
            attribute-access nodes in this language.  Tuple because
            some languages use more than one (e.g. JS ``member_expression``
            handles both ``a.b`` and ``a["b"]`` via an ``index_expression``
            sibling that the caller may also want to include).
        object_field_names: Child-field names to try for the base
            (receiver) of the attribute access, in order.  Some grammars
            expose ``object``, others ``value`` or ``operand``.
        property_field_names: Child-field names to try for the attribute
            name.
        call_node_kinds: Tree-sitter node types that represent function
            calls; the helper excludes attribute-access nodes that are
            the callee of such a call so ``calls`` edges are not
            duplicated.
        call_function_field_names: Child-field names to try for the
            callee of a call node.
        scoped_path: When True, switches the helper to a left-recursive
            path-walk model used by languages whose scoped access is
            not a binary ``object`` / ``property`` pair.  Rust's
            ``scoped_identifier`` and C++'s ``qualified_identifier``
            parse ``std::env::consts::OS`` as a nested chain of
            ``path`` + ``name`` children — the helper walks left via
            the path field to find the leftmost identifier, checks it
            against the imports map, and emits edges using
            dot-normalized module paths (``::`` replaced with ``.``)
            so the resulting edge ID survives downstream ``:``-split
            parsing.  Catalog matching still works because
            ``IoBoundaryCatalog`` registers both ``::`` and ``.``
            forms in its qualified-name index.
    """
    # tree-sitter's Python bindings return a fresh wrapper object on each
    # accessor call, so `id()` is unstable across two walks of the same
    # tree.  The underlying node carries a stable integer ``.id`` — use
    # that as the key for the callee-detection set.
    callee_attr_ids: set[int] = set()
    for node in iter_tree(root):
        if node.type not in call_node_kinds:
            continue
        callee = None
        for fname in call_function_field_names:
            callee = node.child_by_field_name(fname)
            if callee is not None:
                break
        if callee is None:
            continue
        if callee.type in node_kinds:
            callee_attr_ids.add(callee.id)

    for node in iter_tree(root):
        if node.type not in node_kinds:
            continue
        if node.id in callee_attr_ids:
            continue
        base = None
        for fname in object_field_names:
            base = node.child_by_field_name(fname)
            if base is not None:
                break
        prop = None
        for fname in property_field_names:
            prop = node.child_by_field_name(fname)
            if prop is not None:
                break
        if base is None or prop is None:
            continue
        base_text = node_text(base, source)
        attr_name = node_text(prop, source)
        if scoped_path:
            # Left-recursive walk: the base may itself be a scoped_identifier
            # (``std::env`` inside ``std::env::consts``).  Walk left via the
            # first object_field_name until we bottom out at a terminal
            # identifier — that's the alias we check against imports.
            leftmost = base
            while leftmost.type in node_kinds:
                inner = None
                for fname in object_field_names:
                    inner = leftmost.child_by_field_name(fname)
                    if inner is not None:
                        break
                if inner is None:  # pragma: no cover
                    # Grammar variation guard: a node whose type is in
                    # node_kinds but whose ``object_field_names`` resolve
                    # to nothing is malformed — the outer node check would
                    # have failed too.  Break to avoid an infinite loop.
                    break
                leftmost = inner
            leftmost_text = node_text(leftmost, source)
            if leftmost_text not in imports:
                continue
            # Replace the leftmost alias with its real module, then
            # dot-normalize ``::`` to ``.`` so the resulting edge ID
            # survives ``:``-split parsing in io_boundary.
            real_leftmost = imports[leftmost_text]
            real_module_raw = real_leftmost + base_text[len(leftmost_text):]
            real_module = real_module_raw.replace("::", ".")
        else:
            if base_text not in imports:
                continue
            real_module = imports[base_text]
        qname = f"{real_module}.{attr_name}"
        edges_out.append(Edge.create(
            src=caller_symbol.id,
            dst=f"{lang}:{real_module}:0-0:{qname}:attribute",
            edge_type="module_attr_ref",
            line=node.start_point[0] + 1,
            origin=pass_id,
            origin_run_id=run_id,
            evidence_type="module_attribute_reference",
        ))


def make_file_finder(patterns: list[str]) -> Callable[[Path], Iterator[Path]]:  # pragma: no cover
    """Create a file finder function for specific patterns.

    Args:
        patterns: Glob patterns to match (e.g., ["*.go"], ["*.rs"])

    Returns:
        A function that yields matching files from a repo root.
    """

    def finder(repo_root: Path) -> Iterator[Path]:
        yield from find_files(repo_root, patterns)

    return finder


# ---------------------------------------------------------------------------
# TreeSitterAnalyzer base class
# ---------------------------------------------------------------------------


class TreeSitterAnalyzer:
    """Base class for tree-sitter-based language analyzers.

    Encapsulates the universal two-pass architecture used by 100+ analyzers:
      Pass 1: Discover files, parse with tree-sitter, extract symbols
      Pass 2: Re-walk ASTs, resolve calls/imports against global symbol registry

    Subclasses configure via class attributes and override template methods
    for language-specific extraction logic.

    How It Works
    ------------
    1. Check grammar availability (``_check_grammar_available``)
    2. Initialize parser and AnalysisRun
    3. Pass 1: ``extract_symbols_from_file()`` for each source file
    4. Build global symbol registry via ``register_symbol()``
    5. Pass 2: ``extract_edges_from_file()`` for each file
    6. Pass 2b: ``extract_usage_contexts_from_file()`` for each file
    7. ``post_process()`` hook for cross-cutting concerns
    8. Assemble and return AnalysisResult

    Why This Design
    ---------------
    Previously, each analyzer duplicated this two-pass loop (~100 lines).
    The base class captures the scaffolding so subclasses focus solely on
    language-specific extraction logic. Analyzers can override any template
    method, or override ``analyze()`` entirely for full control.

    Grammar Modes
    -------------
    Two ways to specify the grammar:

    - ``grammar_module = "tree_sitter_go"`` — direct package import
    - ``language_pack_name = "nim"`` — uses tree_sitter_language_pack

    Exactly one should be set. The base class handles availability checking
    and parser creation for both modes.

    Example (simple analyzer)::

        class NimAnalyzer(TreeSitterAnalyzer):
            lang = "nim"
            file_patterns = ["*.nim", "*.nims"]
            language_pack_name = "nim"

            def extract_symbols_from_file(self, tree, source, file_path,
                                          rel_path, run):
                analysis = FileAnalysis()
                for node in iter_tree(tree.root_node):
                    if node.type == "proc_declaration":
                        # ... extract symbol
                return analysis

            def extract_edges_from_file(self, ...):
                # ... extract edges
                return edges

        _analyzer = NimAnalyzer()

        @register_analyzer("nim")
        def analyze_nim(repo_root, max_files=None):
            return _analyzer.analyze(repo_root, max_files)
    """

    # -- Required configuration (set by subclass) --------------------------
    lang: str = ""
    """Language identifier (e.g., "go", "rust", "python")."""

    pass_id: str = ""
    """Pass identifier (e.g., "go", "rust" — no legacy "-v1" suffix per INV-morag PR 2)."""

    pass_version: str = ""
    """Code-hash of the analyzer module (via compute_pass_version)."""

    file_patterns: ClassVar[list[str]] = []
    """Glob patterns for source files (e.g., ["*.go"], ["*.rs"])."""

    # -- Grammar source: exactly one of these should be set ----------------
    #
    # Read-only PROPERTIES rather than the `Optional[str] = None` class
    # attributes they replace. Subclasses still declare them the same way —
    # `grammar_module = "tree_sitter_go"` in the class body — and nothing about
    # that changes: a subclass class-attribute precedes this base in the MRO, so
    # `self.grammar_module` returns the string, while a subclass that declares
    # neither falls through to the property and gets `None`, exactly as before.
    #
    # Why: 105 analyzer subclasses across the three language packages each
    # narrowed one of these from `str | None` to `str`, which mypy reports as
    # `mutable-override` — a covariant override of a MUTABLE attribute. That was
    # 105 of the ~957 strict errors, 11% of the whole surface, from these two
    # declarations. An attribute that is only ever *read* has no business being
    # declared mutable, so the fix is the declaration, not 105 subclass edits.
    #
    # The tempting alternative — `ClassVar[str] = ""`, matching the `lang` /
    # `pass_id` sentinels a few lines up — is a LIVE BEHAVIOUR CHANGE and was
    # rejected: five `is not None` checks below (`_check_grammar_available`,
    # `_create_parser`) would become permanently true, so every language-pack
    # analyzer would call `importlib.import_module("")`; and `_get_config_dict`
    # feeds `compute_config_fingerprint`, a PERSISTED cache key, so every
    # analyzer's `config_fingerprint` would change. `ClassVar` alone does not
    # silence the error, and `Final` makes it worse (it adds
    # "cannot assign to final name" at all 105 sites).
    #
    # Residual sharp edge, unexercised today and grepped to confirm it: a
    # CLASS-level read (`TreeSitterAnalyzer.grammar_module`) now yields the
    # property object rather than `None`, and `self.grammar_module = ...` would
    # raise. Every read in `packages/*/src/` is `self.`-qualified and there are
    # zero such assignments anywhere in the repo.
    @property
    def grammar_module(self) -> Optional[str]:
        """Direct grammar package name (e.g., "tree_sitter_go")."""
        return None

    @property
    def language_pack_name(self) -> Optional[str]:
        """Language-pack grammar name (e.g., "nim")."""
        return None

    # -- Optional configuration --------------------------------------------
    resolver_class: type = NameResolver
    """Resolver class for symbol lookup during Pass 2."""

    create_file_symbols: bool = False
    """Whether to emit file-level symbols for each source file."""

    supports_max_files: bool = False
    """Whether analyze() should respect the max_files parameter."""

    self_keywords: ClassVar[frozenset[str]] = frozenset({"self"})
    """Tokens that refer to the current instance in this language.

    Python/Ruby/Rust/Swift use ``self``, Java/C#/Kotlin use ``this``.
    Subclasses override for their language.  Used by
    ``resolve_receiver_type()`` to anchor field-chain resolution.
    """

    # -- Template methods: grammar setup -----------------------------------

    def _get_config_dict(self) -> dict[str, Any]:
        """Return the analyzer's effective configuration dict for
        ``config_fingerprint`` derivation.

        INV-lidul / Phase 6 PR2: ``AnalysisRun.config_fingerprint``
        defaulted to ``sha256:44136fa355b3678a`` (sha256 of ``{}``) for
        every analyzer + linker run on self-analysis (84 of 84 runs
        identical), collapsing distinct passes onto the same
        cache-keying fingerprint. The Phase 6 PR2 closure derives a
        per-analyzer fingerprint from this class's identity + grammar
        + file-pattern set — at least making the 84 runs distinct, and
        giving subclasses a one-method override path to thread real
        per-run config (e.g., file globs, language filters).
        """
        return {
            "class": f"{type(self).__module__}.{type(self).__name__}",
            "lang": self.lang,
            "pass_id": self.pass_id or self.lang,
            "file_patterns": list(self.file_patterns),
            "grammar_module": self.grammar_module,
            "language_pack_name": self.language_pack_name,
            "create_file_symbols": self.create_file_symbols,
        }

    def _stamp_config_fingerprint(self, run: AnalysisRun) -> None:
        """Replace the default config_fingerprint with one derived from
        ``self._get_config_dict()``.

        Pre-Phase-6 every run carried the literal ``sha256:44136fa355b3678a``
        default; this method derives a stable fingerprint from the
        analyzer's effective config so distinct analyzers register
        distinct fingerprints and within-analyzer config changes
        propagate via ``compute_pass_version``. Delegates to the shared
        :func:`ir.compute_config_fingerprint` primitive (also used by the
        orchestrator chokepoint, the linker registry, and the synthesis
        passes) so every producer hashes identity the same way.
        """
        run.config_fingerprint = compute_config_fingerprint(self._get_config_dict())

    def _extend_toolchain(self, run: AnalysisRun) -> None:
        """Extend ``run.toolchain`` with grammar/library version info.

        INV-nihug / Phase 6 PR2 closure: ``AnalysisRun.create`` defaults
        ``toolchain`` to ``{"name": "python", "version": <host>}`` via
        ``_get_python_toolchain``. That default is correct for the host
        but doesn't capture the actual dependency chain that produced
        this analysis — every TreeSitter analyzer depends on the
        ``tree_sitter`` library + a grammar (either ``self.grammar_module``
        like ``tree_sitter_go`` or a language-pack name like
        ``self.language_pack_name``). This method appends those into
        ``run.toolchain`` so cache fingerprinting and reproducibility
        comparisons can tell apart two runs that used different grammar
        versions on the same host Python.

        The keys appended:
        - ``tree_sitter_version``: package version of the ``tree_sitter`` lib.
        - ``grammar_module``: name of the grammar source module (e.g.,
          ``tree_sitter_go``), or ``language_pack:<lang>`` for pack-backed
          grammars.
        - ``grammar_version``: when the grammar package exposes
          ``__version__``, capture it.
        """
        # tree_sitter library version (via importlib.metadata since
        # tree_sitter doesn't expose ``__version__`` at the module level).
        try:
            from importlib.metadata import PackageNotFoundError, version as _pkg_version
            try:
                run.toolchain["tree_sitter_version"] = _pkg_version("tree_sitter")
            except PackageNotFoundError:  # pragma: no cover - defensive
                pass
        except ImportError:  # pragma: no cover - defensive
            pass
        # Grammar source identifier (grammar module name or language-pack key)
        if self.grammar_module is not None:
            run.toolchain["grammar_module"] = self.grammar_module
            try:
                from importlib.metadata import (
                    PackageNotFoundError as _PnfE,
                    version as _pv,
                )
                try:
                    run.toolchain["grammar_version"] = _pv(self.grammar_module)
                except _PnfE:  # pragma: no cover - some grammars unpackaged
                    pass
            except ImportError:  # pragma: no cover - defensive
                pass
        elif self.language_pack_name is not None:
            run.toolchain["grammar_module"] = (
                f"language_pack:{self.language_pack_name}"
            )

    def _check_grammar_available(self) -> bool:
        """Check if the tree-sitter grammar is available.

        Default implementation handles both grammar_module and
        language_pack_name modes. Override for custom availability logic.

        Returns:
            True if grammar is importable and usable.
        """
        if self.grammar_module is not None:
            return is_grammar_available(self.grammar_module)
        if self.language_pack_name is not None:
            if importlib.util.find_spec("tree_sitter") is None:
                return False  # pragma: no cover
            if importlib.util.find_spec("tree_sitter_language_pack") is None:
                return False  # pragma: no cover
            try:
                from tree_sitter_language_pack import get_language

                get_language(self.language_pack_name)
                return True
            except Exception:  # pragma: no cover
                return False
        return False  # pragma: no cover - no grammar configured

    def _create_parser(self) -> "tree_sitter.Parser":
        """Create and return a tree-sitter parser.

        Default implementation handles both grammar_module and
        language_pack_name modes. Override for custom parser setup.

        Returns:
            A configured tree-sitter Parser instance.
        """
        import tree_sitter

        if self.grammar_module is not None:
            mod = importlib.import_module(self.grammar_module)
            lang = tree_sitter.Language(mod.language())
            return tree_sitter.Parser(lang)

        # language_pack_name mode
        from tree_sitter_language_pack import get_language

        lang = get_language(self.language_pack_name)
        return tree_sitter.Parser(lang)

    # -- Template methods: symbol extraction (Pass 1) ----------------------

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract symbols from a single parsed file.

        Override this method with language-specific symbol extraction.
        Default returns empty FileAnalysis.

        Args:
            tree: Parsed tree-sitter tree
            source: Raw source bytes
            file_path: Absolute path to the file
            rel_path: Path relative to repo root
            run: Current AnalysisRun for provenance

        Returns:
            FileAnalysis with symbols and symbol_by_name populated.
        """
        return FileAnalysis()  # pragma: no cover

    def get_import_aliases(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
    ) -> dict[str, str]:
        """Extract import alias to module path mappings.

        Used during Pass 2 for call disambiguation (e.g., "np" -> "numpy").
        Default returns empty dict.

        Args:
            tree: Parsed tree-sitter tree
            source: Raw source bytes

        Returns:
            Mapping of alias name to full module path.
        """
        return {}

    # -- Template methods: edge extraction (Pass 2) ------------------------

    def extract_edges_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        local_symbols: dict[str, Symbol],
        global_symbols: dict[str, Symbol],
        run: AnalysisRun,
        import_aliases: dict[str, str],
        resolver: NameResolver,
    ) -> list[Edge]:
        """Extract edges from a single parsed file.

        Override for language-specific edge extraction.
        Default returns empty list.

        Args:
            tree: Parsed tree-sitter tree
            source: Raw source bytes
            file_path: Absolute path to the file
            rel_path: Path relative to repo root
            local_symbols: Symbol-by-name dict for this file
            global_symbols: All symbols across all files
            run: Current AnalysisRun for provenance
            import_aliases: Import alias mappings from get_import_aliases
            resolver: Configured name resolver for symbol lookup

        Returns:
            List of Edge instances.
        """
        return []  # pragma: no cover

    def extract_usage_contexts_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        symbol_by_name: dict[str, Symbol],
    ) -> list[UsageContext]:
        """Extract UsageContext records for framework pattern matching.

        Default returns empty list. Override for route-emitting analyzers.

        Args:
            tree: Parsed tree-sitter tree
            source: Raw source bytes
            file_path: Absolute path to the file
            symbol_by_name: Symbol-by-name dict for this file

        Returns:
            List of UsageContext instances.
        """
        return []

    # -- Field type resolution helpers --------------------------------------

    def resolve_receiver_type(
        self,
        value_node: "tree_sitter.Node",
        source: bytes,
        enclosing_type: str | None,
    ) -> str | None:
        """Resolve a self.field receiver chain to its type name.

        Uses ``_field_type_registry`` (set by ``analyze()`` before Pass 2).
        Walks nested ``field_expression`` / ``member_expression`` nodes to
        decompose a receiver chain like ``self.app`` into segments, then
        iteratively resolves through the registry.

        Only resolves chains rooted at a ``self_keywords`` token.
        Non-self receivers return None.

        Args:
            value_node: The receiver node of a method call (e.g. the
                ``self.app`` part of ``self.app.run()``).
            source: Source bytes for extracting node text.
            enclosing_type: The type that contains this method (e.g.
                the impl target in Rust, the class name in Python).

        Returns:
            The resolved type name, or None if resolution fails at any step.
        """
        if enclosing_type is None:
            return None

        registry = getattr(self, "_field_type_registry", {})
        if not registry:
            return None

        # Decompose the receiver chain into segments.
        # e.g. self.inner.app → ["self", "inner", "app"]
        # C++: this->inner->app (field_expression uses "argument" not "value")
        segments: list[str] = []
        node = value_node
        while node.type in ("field_expression", "member_expression"):
            field_node = node.child_by_field_name("field")
            if field_node is None:
                return None
            segments.append(node_text(field_node, source))
            node = (
                node.child_by_field_name("value")
                or node.child_by_field_name("argument")
            )
            if node is None:
                return None

        # Root must be a self keyword
        root_text = node_text(node, source)
        if root_text not in self.self_keywords:
            return None

        # Segments were collected leaf-to-root; reverse to walk root-to-leaf.
        segments.reverse()

        # Walk the chain: start from enclosing_type, resolve each field step.
        current_type = enclosing_type
        for field_name in segments:
            fields = registry.get(current_type)
            if fields is None:
                return None
            next_type = fields.get(field_name)
            if next_type is None:
                return None
            current_type = next_type

        return current_type

    # -- Template methods: global symbol registry --------------------------

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict[str, Symbol],
    ) -> None:
        """Add a symbol to the global registry for cross-file resolution.

        Default stores by qualified name only. The ``NameResolver`` suffix
        index handles short-name lookups (e.g., ``"compute"`` →
        ``"Diff::compute"``) so individual analyzers should NOT register
        short names — doing so causes false exact matches when multiple
        types share a method name.

        Override for language-specific indexing (e.g., Go stores lists).

        Args:
            symbol: Symbol to register
            global_symbols: Mutable global registry dict
        """
        global_symbols[symbol.name] = symbol

    # -- Template methods: file discovery ------------------------------------

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Yield source files to analyze.

        Default uses ``find_files(repo_root, self.file_patterns)``.
        Override for custom filtering (e.g., F# skips Forth .fs files).

        Args:
            repo_root: Root directory of the repository.

        Yields:
            Paths to source files.
        """
        yield from find_files(repo_root, self.file_patterns)

    # -- Template methods: post-processing ---------------------------------

    def post_process(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        usage_contexts: list[UsageContext],
        run: AnalysisRun,
    ) -> tuple[list[Symbol], list[Edge], list[UsageContext]]:
        """Optional post-processing after both passes complete.

        Use for route extraction, annotation edges, or other
        cross-cutting concerns. Default is identity.

        Args:
            symbols: All symbols from Pass 1
            edges: All edges from Pass 2
            usage_contexts: All usage contexts from Pass 2
            run: Current AnalysisRun

        Returns:
            Tuple of (symbols, edges, usage_contexts), possibly modified.
        """
        return symbols, edges, usage_contexts

    # -- shape_id computation (ADR-0014 §1) ---------------------------------

    _SHAPE_SKIP_TYPES: ClassVar[frozenset[str]] = frozenset(
        {"comment", "line_comment", "block_comment", "ERROR", "MISSING"}
    )

    def compute_shape_id(self, node: "tree_sitter.Node") -> str:
        """Compute shape_id by hashing the structural skeleton of a CST subtree.

        Walks the tree-sitter concrete syntax tree non-recursively, strips
        identifiers, literals, comments, and punctuation, then hashes the
        resulting S-expression.  This captures the structural "shape" of code
        independent of naming and formatting.

        Filtering strategy (ADR-0014 §1):
        - Anonymous nodes (punctuation like ``{``, ``;``, ``(``) are skipped
        - Comment, ERROR, and MISSING nodes are skipped
        - Named leaf nodes (identifiers, literals) emit only their type name
        - Named non-leaf nodes emit ``(type child1 child2 ...)`` structure

        Returns:
            Shape hash in ``sha256:{16-hex-chars}`` format matching
            the Python analyzer's convention.
        """
        structure = self._cst_structure(node)
        hash_val = hashlib.sha256(structure.encode()).hexdigest()[:16]
        return f"sha256:{hash_val}"

    def _cst_structure(self, node: "tree_sitter.Node") -> str:
        """Build an S-expression skeleton of a CST subtree.

        Uses an explicit stack to avoid RecursionError on deeply nested code
        (same rationale as ``iter_tree()``).  The output is a parenthesised
        S-expression where only *named* structural nodes contribute, with
        leaf nodes (identifiers, literals, keywords) represented by their
        type name alone.

        Example output for ``def foo(x): return x + 1``::

            (function_definition identifier (parameters identifier)
             (block (return_statement (binary_operator identifier integer))))
        """
        parts: list[str] = []
        skip = self._SHAPE_SKIP_TYPES
        # Stack entries: (node | None, phase)
        #   phase 0 → process / open the node
        #   phase 1 → close the node (append ")")
        stack: list[tuple["tree_sitter.Node | None", int]] = [(node, 0)]

        while stack:
            current, phase = stack.pop()

            if phase == 1:
                parts.append(")")
                continue

            assert current is not None  # pragma: no cover - type narrowing

            # Skip anonymous nodes (punctuation) and filtered types
            if not current.is_named or current.type in skip:
                continue

            # Collect named, non-skip children
            named_children = [
                c for c in current.children
                if c.is_named and c.type not in skip
            ]

            if not named_children:
                # Leaf: just emit the type name (covers identifiers, literals, etc.)
                parts.append(current.type)
            else:
                # Non-leaf: open S-expression, push closing marker + children
                parts.append(f"({current.type}")
                stack.append((None, 1))
                for child in reversed(named_children):
                    stack.append((child, 0))

        return " ".join(parts)

    # -- Parameter classification (ADR-0014 §2) ----------------------------

    # Node types that indicate variadic positional params, by grammar.
    # Languages may add to this set via _VARARGS_NODE_TYPES class attribute.
    _VARARGS_NODE_TYPES: ClassVar[frozenset[str]] = frozenset({
        "rest_pattern",          # JS/TS
        "rest_element",          # JS/TS (alternative)
        "spread_parameter",      # Java
        "splat_parameter",       # Ruby
        "variadic_parameter",    # C/C++, PHP
    })

    # Node types that indicate variadic keyword params.
    _KWARGS_NODE_TYPES: ClassVar[frozenset[str]] = frozenset({
        "hash_splat_parameter",  # Ruby
        "dictionary_splat_pattern",  # Python (tree-sitter)
    })

    # Node types that indicate default parameter values.
    _DEFAULT_NODE_TYPES: ClassVar[frozenset[str]] = frozenset({
        "assignment_pattern",    # JS/TS
        "optional_parameter",    # Ruby, TypeScript
        "default_parameter",     # Python (tree-sitter)
    })

    def classify_parameter_flags(
        self, params_node: "tree_sitter.Node",
    ) -> ArityFlags:
        """Classify a function's parameter list into ArityFlags.

        Examines the children of a tree-sitter parameter-list node to
        determine parameter count, defaults, varargs, and kwargs.

        The default implementation uses heuristic node-type matching that
        covers most C-family and scripting languages.  Override for
        languages with unusual parameter models.

        Args:
            params_node: A tree-sitter node representing the parameter list
                (e.g., ``formal_parameters``, ``parameters``, ``parameter_list``).

        Returns:
            ArityFlags with the classified values.
        """
        param_count = 0
        has_defaults = False
        has_varargs = False
        has_kwargs = False

        for child in params_node.children:
            if not child.is_named:
                continue  # skip punctuation

            node_type = child.type

            # Check for varargs/kwargs/defaults
            if node_type in self._VARARGS_NODE_TYPES:
                has_varargs = True
                param_count += 1
            elif node_type in self._KWARGS_NODE_TYPES:
                has_kwargs = True
                param_count += 1
            elif node_type in self._DEFAULT_NODE_TYPES:
                has_defaults = True
                param_count += 1
            else:
                # Regular parameter (identifier, typed_parameter, etc.)
                param_count += 1
                # Check if any child has a default value (= expr)
                for sub in child.children:
                    if not sub.is_named:
                        continue
                    if sub.type in self._DEFAULT_NODE_TYPES or sub.type == "default_value":
                        has_defaults = True
                        break

        return ArityFlags(
            param_count=param_count,
            has_defaults=has_defaults,
            has_varargs=has_varargs,
            has_kwargs=has_kwargs,
        )

    # -- stable_id computation (ADR-0014 §2) ---------------------------------

    # Node types for decorator/annotation wrappers, by grammar.
    _DECORATOR_NODE_TYPES: ClassVar[frozenset[str]] = frozenset({
        "decorator",             # Python, JS/TS
        "annotation",            # Java, Kotlin
        "attribute",             # C#, Rust
        "attribute_item",        # Rust inner
    })

    # Node types for parameter-list containers.
    _PARAMS_NODE_TYPES: ClassVar[frozenset[str]] = frozenset({
        "parameters",            # Python, Ruby
        "formal_parameters",     # Java, JS/TS
        "parameter_list",        # C, C++, C#
        "function_parameters",   # Rust
    })

    def _file_anchor(self, rel_path: str) -> str:
        """WI-bokab (v7): the file-identity value folded into ``containing_stable_id``
        for this analyzer's file-resident symbols.

        Returns ``make_file_stable_id(self.lang, normalize_path(rel_path))`` — the same
        value the file Symbol's own stable_id carries (``populate_kind_stable_ids``
        computes it from the normalized repo-relative path), so a child symbol's anchor
        byte-matches its file node. ``rel_path`` MUST be repo-relative; folding an
        absolute path would make the stable_id location-dependent (the regression
        WI-bokab's smoke test guarded against).
        """
        return make_file_stable_id(self.lang, normalize_path(rel_path))

    def compute_stable_id(
        self,
        node: "tree_sitter.Node",
        kind: str,
        containing_stable_id: str = "",
        *,
        name: str = "",
        qualified_name: str = "",
        occurrence_index: int = 0,
        file_stable_id: str = "",
    ) -> str:
        """Compute an untyped-tier stable_id for a function/class/method node.

        Delegates to :func:`assemble_stable_id` — the single v6 formula (ADR-0035 §1)::

            sha256({kind}:{param_count}:{arity_flags}:{decorators}
                   :{containing_stable_id}:{name}:{qualified_name}:{occurrence_index})

        Per INV-tazaj fix shape: the previous interface-shape-only signature
        produced ~60% collisions on the dogfood corpus because shape alone
        does not distinguish 155 zero-parameter bash functions in a single
        file (or 152 tests with empty bodies). Including `name` and
        `qualified_name` gives a structural-identity-within-a-rename-scope
        guarantee: stable_id survives BODY edits but NOT rename or move.
        This is the right tradeoff for a field that must distinguish ~34K
        symbols.

        Args:
            node: Tree-sitter node for the symbol's definition.
            kind: Symbol kind (``"function"``, ``"method"``, ``"class"``).
            containing_stable_id: Stable ID of the enclosing scope (class or
                module).  Empty string for top-level definitions.
            name: Symbol's local name (e.g., ``"poll_ci"``). Defaults to
                empty for back-compat with legacy callers; new sites should
                pass it for uniqueness.
            qualified_name: Dotted full-qualified name (e.g.,
                ``"module.Class.method"``). Defaults to empty when the
                analyzer does not compute one at stable_id time.
            file_stable_id: WI-bokab (v7) file-identity anchor (see
                :meth:`_file_anchor`). When ``containing_stable_id`` is empty,
                this is folded into the containing slot so same-named symbols in
                different files hash distinctly. An explicit ``containing_stable_id``
                wins. Pass ``file_stable_id=self._file_anchor(rel_path)``.

        Returns:
            Stable ID in ``sha256:{16-hex-chars}`` format.
        """
        # WI-bokab (v7): fold the declaring file's identity for file-resident
        # symbols lacking an enclosing-scope containing. Additive — tree-sitter
        # producers carry scope in qualified_name and pass no containing today.
        if not containing_stable_id and file_stable_id:
            containing_stable_id = file_stable_id

        # 1. Extract parameter arity
        params_node = self._find_params_node(node)
        if params_node is not None:
            flags = self.classify_parameter_flags(params_node)
        else:
            flags = ArityFlags(
                param_count=0,
                has_defaults=False,
                has_varargs=False,
                has_kwargs=False,
            )

        # 2. Extract decorator/annotation names
        decorators = self._extract_decorator_names(node)
        decorators_str = ",".join(sorted(decorators))

        # 3. Hash via the shared v6 formula (ADR-0035 §1).
        return assemble_stable_id(
            kind,
            flags.param_count,
            flags.as_flags_str(),
            decorators_str,
            containing_stable_id,
            name,
            qualified_name,
            occurrence_index,
        )

    def _find_params_node(
        self, node: "tree_sitter.Node",
    ) -> "tree_sitter.Node | None":
        """Find the parameter-list child of a definition node.

        Searches immediate children for a node whose type is in
        ``_PARAMS_NODE_TYPES``.  Returns ``None`` for class definitions
        or nodes without parameter lists.
        """
        for child in node.children:
            if child.type in self._PARAMS_NODE_TYPES:
                return child
        return None

    def _extract_decorator_names(
        self, node: "tree_sitter.Node",
    ) -> list[str]:
        """Extract decorator/annotation names from a definition node.

        Looks at the node's parent (if available) or siblings for
        decorator nodes.  Returns a list of plain decorator names
        (without arguments or module paths).

        The default implementation looks for decorator children preceding
        the definition node.  Override for grammars where decorators are
        structured differently.
        """
        names: list[str] = []
        # Some grammars nest decorators as children of the definition node
        # (e.g., Python's decorated_definition → decorator* + definition).
        # Others make them siblings. Check children first.
        for child in node.children:
            if child.type in self._DECORATOR_NODE_TYPES:
                name = self._decorator_node_name(child)
                if name:
                    names.append(name)
        return names

    def _decorator_node_name(
        self, decorator_node: "tree_sitter.Node",
    ) -> str:
        """Extract the plain name from a single decorator/annotation node.

        Walks the decorator's children to find the identifier.  Handles
        both simple (``@foo``) and call (``@foo(arg)``) forms.
        """
        for child in decorator_node.children:
            if child.type == "identifier":
                return child.text.decode("utf-8", errors="replace") if child.text else ""
            # Dotted path: @module.decorator → take last segment
            if child.type in ("attribute", "dotted_name"):
                # Find the last identifier in the chain
                last_ident = ""
                for sub in child.children:
                    if sub.type == "identifier":
                        last_ident = sub.text.decode("utf-8", errors="replace") if sub.text else ""
                if last_ident:
                    return last_ident
            # Call form: @decorator(args) → look inside the call
            if child.type == "call":
                return self._decorator_node_name(child)
        return ""

    # -- Main analysis method (the two-pass loop) --------------------------

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run the full two-pass analysis.

        This method orchestrates the entire analysis pipeline:
        1. Check grammar availability
        2. Initialize parser and AnalysisRun
        3. Pass 1: extract symbols from each file
        4. Build global symbol registry
        5. Pass 2: extract edges from each file
        6. Pass 2b: extract usage contexts
        7. Post-process
        8. Assemble and return AnalysisResult

        Args:
            repo_root: Root directory of the repository
            max_files: Optional limit on files to process

        Returns:
            AnalysisResult with symbols, edges, usage_contexts, and run.
        """
        start_time = time.time()
        effective_pass_id = self.pass_id or make_pass_id(self.lang)
        run = AnalysisRun.create(
            pass_id=effective_pass_id,
            version=PASS_VERSION,
            pass_version=self.pass_version,
        )

        return self._analyze_body(repo_root, max_files, run, start_time)

    def _analyze_body(
        self,
        repo_root: Path,
        max_files: Optional[int],
        run: AnalysisRun,
        start_time: float,
    ) -> AnalysisResult:
        """Inner ``analyze()`` body that does the actual work.

        Kept as a separate method so the outer ``analyze()`` is a thin
        seam — handy for INV-pitab-style future wiring (e.g., per-call
        instrumentation) without re-threading every analyzer."""
        effective_pass_id = self.pass_id or make_pass_id(self.lang)
        # INV-gizik / Phase 6 PR2: stamp pass_version from the concrete
        # analyzer subclass's module hash when the subclass hasn't set
        # one explicitly. Mirrors the linker-side stamping in
        # registry.py:_stamp_pass_version. Without this, every
        # TreeSitterAnalyzer subclass inherits the empty default and
        # the field is unset for 44 of 84 runs on the self-analysis.
        if not run.pass_version:
            try:
                run.pass_version = compute_pass_version(type(self))
            except Exception:  # pragma: no cover - defensive
                pass

        # INV-nihug / Phase 6 PR2: extend the default
        # ``{"name": "python", "version": <host>}`` toolchain with
        # tree-sitter library / grammar-pack version info when this
        # analyzer is grammar-backed. Not every analyzer runs purely on
        # the host Python interpreter; the toolchain field must reflect
        # the actual dependency chain that produced the analysis.
        try:
            self._extend_toolchain(run)
        except Exception:  # pragma: no cover - defensive
            pass

        # INV-lidul / Phase 6 PR2: replace the default empty-config
        # fingerprint with a derived per-analyzer fingerprint. Without
        # this, all 84 self-analysis runs share the literal default
        # ``sha256:44136fa355b3678a`` (sha256 of ``{}``).
        try:
            self._stamp_config_fingerprint(run)
        except Exception:  # pragma: no cover - defensive
            pass

        # 1. Check grammar availability
        if not self._check_grammar_available():
            msg = (
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package."
            )
            # INV-pitab: structurally record the warning on the run so
            # consumers reading the AnalysisRun later see the gap. Also
            # call warnings.warn so existing stderr / pytest.warns
            # consumers continue to see it. No thread-global state
            # mutation (would race in the orchestrator's ThreadPoolExecutor).
            run.warnings.append(f"UserWarning: {msg}")
            warnings.warn(msg, UserWarning, stacklevel=2)
            run.duration_ms = int((time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        # 2. Initialize parser
        parser = self._create_parser()

        # 3. Pass 1: Extract symbols from all files
        # Cache source bytes per file so Pass 2 can reuse them without
        # re-reading from disk.  The tuple stores (analysis, aliases, source).
        file_analyses: dict[Path, tuple[FileAnalysis, dict[str, str], bytes]] = {}
        files_analyzed = 0
        files_skipped = 0

        for source_file in self._find_source_files(repo_root):
            if max_files is not None and files_analyzed >= max_files:
                break

            # Memory safety: check between files to catch pressure early
            _check_memory_pressure()

            try:
                source = source_file.read_bytes()
            except OSError as e:
                files_skipped += 1
                run.record_failed_file(
                    str(source_file.relative_to(repo_root)),
                    f"OSError: {e}",
                )
                continue

            tree = parser.parse(source)
            rel_path = str(source_file.relative_to(repo_root))

            analysis = self.extract_symbols_from_file(
                tree, source, source_file, rel_path, run
            )

            # Optional: create file-level symbol
            if self.create_file_symbols:
                file_sym = Symbol(
                    id=make_file_id(self.lang, rel_path),
                    name=rel_path,
                    kind="file",
                    language=self.lang,
                    path=rel_path,
                    # Full-file span (WI-sijug): mirror bash.py / js_ts.py so the file
                    # node fingerprints its real structure, not just line 1 (a shebang or
                    # comment there filters to empty → a needless null). ``tree`` is parsed
                    # above; end_point[0] is 0-indexed, so +1 gives the last 1-based line.
                    span=Span(
                        start_line=1, start_col=0,
                        end_line=tree.root_node.end_point[0] + 1, end_col=0,
                    ),
                    origin=effective_pass_id,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.insert(0, file_sym)

            # Auto-compute shape_id and docstring for symbols with nodes (ADR-0014 §1)
            if analysis.node_for_symbol:
                sym_by_id = {s.id: s for s in analysis.symbols}
                for sym_id, ts_node in analysis.node_for_symbol.items():
                    sym = sym_by_id.get(sym_id)
                    if sym is not None:
                        if sym.shape_id is None:
                            sym.shape_id = self.compute_shape_id(ts_node)
                        if sym.docstring is None:
                            sym.docstring = extract_doc_comment(ts_node, source)

            # Fallback: populate docstrings for symbols without node_for_symbol
            # by finding their tree-sitter nodes via position matching.
            populate_docstrings_from_tree(tree.root_node, source, analysis.symbols)

            # Extract import aliases for Pass 2
            import_aliases = self.get_import_aliases(tree, source)

            file_analyses[source_file] = (analysis, import_aliases, source)
            files_analyzed += 1

        # 4. Build global symbol registry
        global_symbols: dict[str, Symbol] = {}
        for analysis, _, _source in file_analyses.values():
            for symbol in analysis.symbols:
                self.register_symbol(symbol, global_symbols)

        # 4b. Aggregate class field types from all files for Pass 2.
        # Subclasses populate FileAnalysis.class_field_types during Pass 1;
        # here we merge them into a single registry keyed by class/struct name.
        field_type_registry: dict[str, dict[str, str]] = {}
        for analysis, _, _source in file_analyses.values():
            for class_name, fields in analysis.class_field_types.items():
                if class_name not in field_type_registry:
                    field_type_registry[class_name] = {}
                for fname, ftype in fields.items():
                    field_type_registry[class_name].setdefault(fname, ftype)
        self._field_type_registry = field_type_registry

        # 4c. Aggregate method return-type registry across files
        # (INV-dihos / WI-kuroj / WI-titor). Mirrors field_type_registry:
        # first writer wins. Subclasses opt in by populating
        # FileAnalysis.method_return_types during Pass 1; analyzers that
        # don't (Kotlin/C#'s inline chaining handles the same use case)
        # simply leave the registry empty. Consumed in Pass 2 via
        # ``self._method_return_type_registry``.
        method_return_type_registry: dict[str, str] = {}
        for analysis, _, _source in file_analyses.values():
            for key, ret_type in analysis.method_return_types.items():
                method_return_type_registry.setdefault(key, ret_type)
        self._method_return_type_registry = method_return_type_registry

        # 5. Pass 2: Extract edges and usage contexts
        # Reuses cached source bytes from Pass 1 to avoid re-reading files.
        all_symbols: list[Symbol] = []
        all_edges: list[Edge] = []
        all_contexts: list[UsageContext] = []
        resolver = self.resolver_class(global_symbols)

        for source_file, (analysis, import_aliases, source) in file_analyses.items():
            _check_memory_pressure()  # Between-file memory safety check
            all_symbols.extend(analysis.symbols)

            tree = parser.parse(source)
            rel_path = str(source_file.relative_to(repo_root))

            edges = self.extract_edges_from_file(
                tree, source, source_file, rel_path,
                analysis.symbol_by_name, global_symbols, run,
                import_aliases, resolver,
            )
            # ADR-0015 Tier 1: automatic dataflow annotation from AST context
            df_config = get_dataflow_config(self.lang)
            if df_config is not None:
                edges = annotate_dataflow(edges, tree, source, df_config)
            all_edges.extend(edges)

            # 6. Usage contexts
            contexts = self.extract_usage_contexts_from_file(
                tree, source, source_file, analysis.symbol_by_name,
            )
            all_contexts.extend(contexts)

        # Clear field + method-return-type registries to avoid stale
        # data across runs (mirrors the WI-kuroj cleanup pattern).
        self._field_type_registry = {}
        self._method_return_type_registry = {}

        # 7. Post-process
        all_symbols, all_edges, all_contexts = self.post_process(
            all_symbols, all_edges, all_contexts, run,
        )

        # 8. Assemble result
        run.files_analyzed = files_analyzed
        run.files_skipped = files_skipped
        run.duration_ms = int((time.time() - start_time) * 1000)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            usage_contexts=all_contexts,
            run=run,
        )

    # -- Registration helper -----------------------------------------------

    def as_registered_analyzer(self) -> "Callable[..., AnalysisResult]":
        """Return a function suitable for use with @register_analyzer.

        Returns a function with signature
        ``(repo_root: Path, max_files: int | None = None) -> AnalysisResult``
        that delegates to ``self.analyze()``.

        Example::

            _analyzer = GoAnalyzer()

            @register_analyzer("go", priority=50)
            def analyze_go(repo_root, max_files=None):
                return _analyzer.analyze(repo_root, max_files)

            # Or equivalently:
            analyze_go = register_analyzer("go")(_analyzer.as_registered_analyzer())

        Returns:
            A callable that wraps ``self.analyze()``.
        """

        def analyze_fn(
            repo_root: Path, max_files: Optional[int] = None
        ) -> AnalysisResult:
            return self.analyze(repo_root, max_files)

        return analyze_fn
