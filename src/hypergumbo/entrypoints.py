"""Entrypoint detection heuristics for code analysis.

Detects common entrypoint patterns:
- HTTP routes (FastAPI, Flask, Express)
- CLI entrypoints (main guard, Click commands)
- Electron app entry points (main, preload, renderer)
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

# Electron file patterns
ELECTRON_MAIN_FILES = {"main.js", "main.ts", "electron.js", "electron.ts"}
ELECTRON_PRELOAD_FILES = {"preload.js", "preload.ts"}
ELECTRON_RENDERER_FILES = {"renderer.js", "renderer.ts", "index.js"}


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
    """Detect Electron app entrypoints from file names."""
    entrypoints = []

    for sym in symbols:
        if sym.language not in ("javascript", "typescript"):
            continue

        filename = _get_filename(sym.path)

        if filename in ELECTRON_MAIN_FILES:
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.ELECTRON_MAIN,
                confidence=0.85,
                label="Electron main",
            ))
        elif filename in ELECTRON_PRELOAD_FILES:
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.ELECTRON_PRELOAD,
                confidence=0.85,
                label="Electron preload",
            ))
        elif filename in ELECTRON_RENDERER_FILES:
            entrypoints.append(Entrypoint(
                symbol_id=sym.id,
                kind=EntrypointKind.ELECTRON_RENDERER,
                confidence=0.80,
                label="Electron renderer",
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

    # Remove duplicates (same symbol detected by multiple heuristics)
    seen_ids: set[str] = set()
    unique_entrypoints: List[Entrypoint] = []
    for ep in entrypoints:
        if ep.symbol_id not in seen_ids:
            seen_ids.add(ep.symbol_id)
            unique_entrypoints.append(ep)

    return unique_entrypoints
