"""Entrypoint detection heuristics for code analysis.

Detects common entrypoint patterns:
- HTTP routes (FastAPI, Flask decorators)
- CLI entrypoints (main guard, Click commands)
- Electron app entry points (electron.js, preload.js)
- Django views (functions imported by urls.py)

How It Works
------------
Entrypoint detection uses heuristics to identify likely "entry points"
into a codebase - places where execution typically starts or where
external requests arrive. This enables `--entry auto` in the slicer.

Detection is based on:

1. **Decorators** (high confidence ~0.95): Functions decorated with
   `@get`, `@post`, `@route`, `@command` etc. are almost certainly
   entrypoints. We extract decorator names from the Symbol's stable_id
   field during analysis.

2. **Name patterns** (lower confidence ~0.70): Functions named `main`,
   `cli`, `run` are *probably* entrypoints but could be false positives.
   The lower confidence lets callers filter if desired.

3. **File patterns** (medium confidence ~0.85): For Electron apps,
   files named `electron.js`, `preload.js` indicate entry points.
   Generic names like `renderer.js` and `index.js` are NOT matched
   to avoid false positives (many frameworks use these names).

4. **Import patterns** (high confidence ~0.90): For Django, functions
   imported by urls.py files are likely views. This leverages the fact
   that Django's URL configuration explicitly references view functions.

Confidence Scores
-----------------
- 0.95: Decorator-based (very reliable)
- 0.90: Django urls.py imports (explicit URL mappings)
- 0.85: File-pattern-based (specific Electron files)
- 0.70: Name-pattern-based (heuristic, may have false positives)

Current Limitations
-------------------
- Decorator detection relies on stable_id containing decorator names,
  which is a temporary hack. Proper decorator storage in IR is needed.
- No Express.js detection yet (requires JS analysis).
- Django detection doesn't catch views defined inline in urls.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List

from .ir import Symbol, Edge


class EntrypointKind(Enum):
    """Types of entrypoints that can be detected."""

    HTTP_ROUTE = "http_route"
    CLI_MAIN = "cli_main"
    CLI_COMMAND = "cli_command"
    ELECTRON_MAIN = "electron_main"
    ELECTRON_PRELOAD = "electron_preload"
    ELECTRON_RENDERER = "electron_renderer"
    DJANGO_VIEW = "django_view"


@dataclass
class Entrypoint:
    """A detected entrypoint in the codebase.

    Attributes:
        symbol_id: ID of the symbol that is an entrypoint.
        kind: Type of entrypoint detected.
        confidence: Confidence score (0.0-1.0).
        label: Human-readable label for the entrypoint.
    """

    symbol_id: str
    kind: EntrypointKind
    confidence: float
    label: str

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "symbol_id": self.symbol_id,
            "kind": self.kind.value,
            "confidence": self.confidence,
            "label": self.label,
        }


# HTTP route decorator patterns (high confidence)
HTTP_ROUTE_DECORATORS = {
    "get", "post", "put", "delete", "patch", "head", "options",
    "route", "api_route",
}

# CLI-related decorators
CLI_DECORATORS = {
    "command", "group", "click.command", "click.group",
    "app.command", "typer.command",
}

# CLI function name patterns (lower confidence)
CLI_NAME_PATTERNS = {
    "main", "cli", "run", "execute", "start",
}

# Electron file patterns (only specific patterns to avoid false positives)
# Note: renderer.js/ts and index.js are too generic - many frameworks use these names
ELECTRON_MAIN_FILES = {"electron.js", "electron.ts", "electron-main.js", "electron-main.ts"}
ELECTRON_PRELOAD_FILES = {"preload.js", "preload.ts", "electron-preload.js", "electron-preload.ts"}


def _get_decorators(symbol: Symbol) -> set[str]:
    """Extract decorator names from symbol.

    The stable_id field is used to store comma-separated decorator names
    during analysis (this is a temporary solution until we have proper
    decorator storage in the IR).
    """
    if symbol.stable_id and not symbol.stable_id.startswith("sha256:"):
        return set(symbol.stable_id.split(","))
    return set()


def _get_filename(path: str) -> str:
    """Extract filename from path."""
    return path.rsplit("/", 1)[-1] if "/" in path else path


def _detect_http_routes(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect HTTP route entrypoints from decorators."""
    entrypoints = []

    for sym in symbols:
        decorators = _get_decorators(sym)
        matching = decorators & HTTP_ROUTE_DECORATORS

        if matching:
            # High confidence for decorator-based detection
            label = f"HTTP {list(matching)[0].upper()}"
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.HTTP_ROUTE,
                confidence=0.95,
                label=label,
            ))

    return entrypoints


def _detect_cli_entrypoints(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect CLI entrypoints from decorators and name patterns."""
    entrypoints = []

    for sym in symbols:
        decorators = _get_decorators(sym)

        # Check for CLI decorators (high confidence)
        if decorators & CLI_DECORATORS:
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.CLI_COMMAND,
                confidence=0.95,
                label="CLI command",
            ))
            continue

        # Check for name patterns (lower confidence)
        if sym.name.lower() in CLI_NAME_PATTERNS:
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.CLI_MAIN,
                confidence=0.70,
                label="CLI main",
            ))

    return entrypoints


def _detect_electron_entrypoints(symbols: List[Symbol]) -> List[Entrypoint]:
    """Detect Electron app entrypoints from file names.

    Only matches specific Electron file patterns to minimize false positives.
    Tracks files already seen to emit one entry point per file, not per symbol.
    """
    entrypoints = []
    seen_files: set[str] = set()

    for sym in symbols:
        if sym.language not in ("javascript", "typescript"):
            continue

        # Only emit one entry point per file
        if sym.path in seen_files:
            continue

        filename = _get_filename(sym.path)

        if filename in ELECTRON_MAIN_FILES:
            seen_files.add(sym.path)
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.ELECTRON_MAIN,
                confidence=0.85,
                label="Electron main",
            ))
        elif filename in ELECTRON_PRELOAD_FILES:
            seen_files.add(sym.path)
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.ELECTRON_PRELOAD,
                confidence=0.85,
                label="Electron preload",
            ))

    return entrypoints


def _detect_django_views(
    symbols: List[Symbol],
    edges: List[Edge],
) -> List[Entrypoint]:
    """Detect Django view entrypoints from urls.py imports.

    Django uses path() and url() calls in urls.py files to map URLs to views.
    Rather than parsing the Python AST for these calls, we use a simpler heuristic:
    any function imported by a urls.py file is likely a Django view.

    This has high precision (urls.py imports are intentional) but may miss
    views defined inline or in the same file.
    """
    entrypoints = []

    # Find all urls.py file nodes
    urls_files = {
        sym.id for sym in symbols
        if sym.path.endswith("urls.py") and sym.kind == "file"
    }

    if not urls_files:
        return entrypoints

    # Find all imports from urls.py files
    for edge in edges:
        if edge.src in urls_files and edge.edge_type == "imports":
            # The destination is a symbol imported by urls.py - likely a view
            entrypoints.append(Entrypoint(
                symbol_id=edge.dst,
                kind=EntrypointKind.DJANGO_VIEW,
                confidence=0.90,
                label="Django view",
            ))

    return entrypoints


def detect_entrypoints(
    nodes: List[Symbol],
    edges: List[Edge],
) -> List[Entrypoint]:
    """Detect entrypoints in the codebase.

    Uses heuristics to find:
    - HTTP routes (FastAPI, Flask decorators)
    - CLI entrypoints (main guard, Click commands)
    - Electron entry points (main, preload, renderer files)

    Args:
        nodes: All symbols in the codebase.
        edges: All edges (currently unused, for future IPC detection).

    Returns:
        List of detected entrypoints with confidence scores.
    """
    entrypoints: List[Entrypoint] = []

    # Detect different types of entrypoints
    entrypoints.extend(_detect_http_routes(nodes))
    entrypoints.extend(_detect_cli_entrypoints(nodes))
    entrypoints.extend(_detect_electron_entrypoints(nodes))
    entrypoints.extend(_detect_django_views(nodes, edges))

    # Remove duplicates (same symbol detected by multiple heuristics)
    seen_ids: set[str] = set()
    unique_entrypoints: List[Entrypoint] = []
    for ep in entrypoints:
        if ep.symbol_id not in seen_ids:
            seen_ids.add(ep.symbol_id)
            unique_entrypoints.append(ep)

    return unique_entrypoints
