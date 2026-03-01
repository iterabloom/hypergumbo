"""ORM query linker for detecting ORM model references in application code.

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
from .registry import LinkerContext, LinkerResult, register_linker

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


def _build_model_lookup(symbols: list[Symbol]) -> dict[str, Symbol]:
    """Build a lookup from model class name to Symbol.

    Finds all symbols with concept "model" in their metadata (set by YAML
    framework patterns for Django, Flask-SQLAlchemy, etc.).

    Args:
        symbols: All symbols from analysis.

    Returns:
        Dict mapping model class name to Symbol. First symbol wins on duplicates.
    """
    lookup: dict[str, Symbol] = {}
    for sym in symbols:
        if sym.meta is None:
            continue
        concepts = sym.meta.get("concepts", [])
        for concept in concepts:
            if isinstance(concept, dict) and concept.get("concept") == "model":
                # Use short name (last component after any dots)
                short_name = sym.name.split(".")[-1] if "." in sym.name else sym.name
                if short_name not in lookup:
                    lookup[short_name] = sym
                break
    return lookup


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
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            files_scanned += 1
            refs = _scan_orm_references(file_path, content, pattern)
            all_refs.extend(refs)
        except (OSError, IOError):  # pragma: no cover
            pass

    # Step 5: Create edges from enclosing functions to models
    edges: list[Edge] = []
    seen_pairs: set[tuple[str, str]] = set()  # (src_id, dst_id) for deduplication

    for ref in all_refs:
        model_sym = model_lookup.get(ref.model_name)
        if model_sym is None:
            continue  # pragma: no cover — model_name came from model_lookup keys

        # Find the enclosing function/method
        enclosing = ctx.find_enclosing_symbol(
            ref.file_path,
            ref.line,
            kinds=("function", "method", "class", "module"),
        )
        if enclosing is None:
            continue

        # Deduplicate (same function referencing same model)
        pair = (enclosing.id, model_sym.id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        edge = Edge.create(
            src=enclosing.id,
            dst=model_sym.id,
            edge_type="model_reference",
            line=ref.line,
            confidence=0.85,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="orm_accessor_pattern",
        )
        edge.meta = {
            "model_name": ref.model_name,
            "accessor": ref.accessor,
        }
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
