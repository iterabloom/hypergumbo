# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: ORM query for detecting ORM model references in application code.

Detects ORM query patterns in Python source files and creates model_reference
edges from the enclosing function to the Model symbol. This increases the
in-degree centrality of Model classes, improving their ranking in behavior maps.

How It Works
------------
1. Find all symbols with concept "model" (from YAML framework patterns)
2. Build a regex from model class names matching ORM accessor patterns
3. Scan Python source files for matches: ModelName.objects.<method> (Django)
   or ModelName.query.<method> (Flask-SQLAlchemy)
4. For each match, find the enclosing function symbol via LinkerContext
5. Create model_reference edges from the enclosing function to the Model

Why This Design
---------------
Django/Flask-SQLAlchemy ORM calls use attribute chains (e.g., User.objects.filter())
that the Python AST analyzer cannot resolve because it only handles one level of
attribute chaining. This linker fills the gap by scanning source files for known
model names with ORM accessor patterns, creating edges that make Models visible
in the call graph.

Supported Patterns
------------------
- Django: User.objects.filter(), User.objects.get(), User.objects.all(), etc.
- Flask-SQLAlchemy: User.query.filter_by(), User.query.first(), etc.

Impact
------
Without this linker, Django Models typically have degree 1-5 and rank in the
thousands (e.g., rank 2156 for Django Model). With this linker, Models gain
in-degree from every view/function that queries them, boosting their centrality
to match developer intuition about their importance.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, PASS_VERSION, Symbol, make_pass_id
from ._concept_utils import has_concept
from .registry import LinkerContext, LinkerResult, register_linker
from ._text_filters import read_masked_source

PASS_ID = make_pass_id("orm-linker")


@dataclass
class OrmReference:
    """A detected ORM query reference in source code."""

    model_name: str
    accessor: str  # "objects" (Django) or "query" (Flask-SQLAlchemy)
    line: int
    file_path: str


@dataclass
class OrmLinkResult:
    """Result of ORM query linking."""

    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


def _build_model_lookup(symbols: list[Symbol]) -> dict[str, list[Symbol]]:
    """Build a lookup from model class name to candidate Symbols.

    Finds all symbols with concept "model" in their metadata (set by YAML
    framework patterns for Django, Flask-SQLAlchemy, etc.). When multiple
    symbols share the same short name (cross-package collision), all
    candidates are returned in insertion order; :func:`_resolve_model_with_fallback`
    applies the INV-zuhub same-file-preferred / deterministic-fallback
    rule at edge-creation time.

    Args:
        symbols: All symbols from analysis.

    Returns:
        Dict mapping model class name to list of candidate Symbols.
    """
    lookup: dict[str, list[Symbol]] = {}
    for sym in symbols:
        if not has_concept(sym, "model"):
            continue
        # Use short name (last component after any dots)
        short_name = sym.name.split(".")[-1] if "." in sym.name else sym.name
        lookup.setdefault(short_name, []).append(sym)
    return lookup


def _resolve_model_with_fallback(
    candidates: list[Symbol],
    referring_file: str,
) -> tuple[Symbol, bool] | None:
    """Pick the best model Symbol for a reference site, with INV-zuhub provenance.

    Resolution cascade per INV-zuhub:

    1. Zero candidates → return ``None`` (no edge to emit).
    2. Single candidate → precision resolution; return ``(sym, is_fallback=False)``.
    3. Multiple candidates with exactly one in the referring file → same-file
       precision win; return ``(same_file_sym, False)``.
    4. Multiple candidates, none / multiple in the referring file →
       deterministic-by-id fallback; return ``(min_by_id_sym, True)``.

    The boolean is the *is_fallback* signal that the caller threads into
    edge creation: when True, the edge carries ``confidence <= 0.5`` and
    ``meta['disambiguation_fallback'] = True`` per INV-zuhub.
    """
    if not candidates:  # pragma: no cover - guarded by caller (ref.model_name from lookup keys)
        return None
    if len(candidates) == 1:
        return (candidates[0], False)
    same_file = [c for c in candidates if c.path == referring_file]
    if len(same_file) == 1:
        return (same_file[0], False)
    pool = same_file if len(same_file) > 1 else candidates
    return (min(pool, key=lambda c: c.id), True)


def _build_orm_pattern(model_names: list[str]) -> re.Pattern | None:
    """Build a regex pattern that matches ORM accessor calls on model names.

    Pattern matches: ModelName.objects.<method> or ModelName.query.<method>
    with word boundaries to prevent partial matches.

    Args:
        model_names: List of model class names to match.

    Returns:
        Compiled regex pattern, or None if model_names is empty.
    """
    if not model_names:
        return None

    # Escape model names for regex safety and join with alternation
    escaped = [re.escape(name) for name in model_names]
    names_group = "|".join(escaped)
    return re.compile(rf"\b({names_group})\.(objects|query)\.\w+")


def _scan_orm_references(
    file_path: Path,
    content: str,
    pattern: re.Pattern,
) -> list[OrmReference]:
    """Scan a Python source file for ORM query references.

    Args:
        file_path: Path to the source file.
        content: File content.
        pattern: Compiled regex pattern from _build_orm_pattern.

    Returns:
        List of detected ORM references.
    """
    if file_path.suffix != ".py":
        return []

    refs: list[OrmReference] = []
    seen: set[tuple[str, int]] = set()  # (model_name, line) for deduplication

    for match in pattern.finditer(content):
        model_name = match.group(1)
        accessor = match.group(2)
        line = content[:match.start()].count("\n") + 1

        key = (model_name, line)
        if key in seen:
            continue
        seen.add(key)

        refs.append(OrmReference(
            model_name=model_name,
            accessor=accessor,
            line=line,
            file_path=str(file_path),
        ))

    return refs


def _find_python_files(root: Path) -> Iterator[Path]:
    """Find Python source files for ORM scanning."""
    for path in find_files(root, ["**/*.py"]):
        yield path


def link_orm_queries(
    root: Path,
    symbols: list[Symbol],
) -> OrmLinkResult:
    """Link ORM query patterns to model symbols.

    Scans Python source files for ORM accessor patterns (e.g., User.objects.filter)
    and creates model_reference edges from the enclosing function to the Model
    symbol.

    Args:
        root: Repository root path.
        symbols: All symbols from analysis (including model symbols with concepts).

    Returns:
        OrmLinkResult with edges linking functions to models.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Step 1: Find model symbols
    model_lookup = _build_model_lookup(symbols)
    if not model_lookup:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return OrmLinkResult(run=run)

    # Step 2: Build regex pattern from model names
    # model_lookup is non-empty here (guarded above), so pattern is always non-None
    pattern = _build_orm_pattern(list(model_lookup.keys()))
    assert pattern is not None  # for type checker — model_lookup is non-empty

    # Step 3: Build a LinkerContext for enclosing symbol lookup
    ctx = LinkerContext(repo_root=root, symbols=symbols)

    # Step 4: Scan Python source files
    all_refs: list[OrmReference] = []
    files_scanned = 0

    for file_path in _find_python_files(root):
        try:
            content = read_masked_source(file_path, encoding="utf-8", errors="ignore")
            files_scanned += 1
            refs = _scan_orm_references(file_path, content, pattern)
            all_refs.extend(refs)
        except (OSError, IOError):  # pragma: no cover
            pass

    # Step 5: Create edges from enclosing functions to models
    edges: list[Edge] = []
    seen_pairs: set[tuple[str, str]] = set()  # (src_id, dst_id) for deduplication

    for ref in all_refs:
        resolved = _resolve_model_with_fallback(
            model_lookup.get(ref.model_name, []), ref.file_path,
        )
        if resolved is None:
            continue  # pragma: no cover — model_name came from model_lookup keys
        model_sym, is_fallback = resolved

        # Find the enclosing function/method (INV-hojus: include file-kind
        # so Python's file-canonical pseudo-node is reachable as fallback
        # container for module-level model references)
        enclosing = ctx.find_enclosing_symbol(
            ref.file_path,
            ref.line,
            kinds=("function", "method", "class", "module", "file"),
        )
        if enclosing is None:
            continue

        # Deduplicate (same function referencing same model)
        pair = (enclosing.id, model_sym.id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        # INV-zuhub: simple-name fallback edges carry conf <= 0.5 and
        # the disambiguation_fallback flag so consumers can filter the
        # fallback population from the precision-resolved one.
        confidence = 0.5 if is_fallback else 0.85
        edge_meta: dict[str, object] = {
            "model_name": ref.model_name,
            "accessor": ref.accessor,
            "framework_dispatch": "orm_accessor",
        }
        if is_fallback:
            edge_meta["disambiguation_fallback"] = True

        edge = Edge.create(
            src=enclosing.id,
            dst=model_sym.id,
            edge_type="references",
            line=ref.line,
            confidence=confidence,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="ast_call_direct",
            meta=edge_meta,
        )
        edges.append(edge)

    run.duration_ms = int((time.time() - start_time) * 1000)
    run.files_analyzed = files_scanned

    return OrmLinkResult(edges=edges, run=run)


# =============================================================================
# Linker Registry Integration
# =============================================================================



@register_linker(
    "orm",
    priority=75,  # Run after framework patterns have enriched symbols
    description="ORM query linking (model accessor patterns to Model symbols)",
)
def orm_linker(ctx: LinkerContext) -> LinkerResult:
    """ORM linker for registry-based dispatch.

    Wraps link_orm_queries() to use the LinkerContext/LinkerResult interface.
    """
    result = link_orm_queries(ctx.repo_root, ctx.symbols)

    return LinkerResult(
        edges=result.edges,
        run=result.run,
    )
