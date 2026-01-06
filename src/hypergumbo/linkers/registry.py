"""Linker registry for dynamic dispatch.

This module provides a registration system for cross-language linkers,
enabling loop-based dispatch in run_behavior_map() instead of
many repetitive code blocks.

How It Works
------------
1. Each linker module calls `register_linker()` at import time
2. The registry stores linker functions by name
3. `run_behavior_map()` iterates over `get_all_linkers()`
4. Each linker is called uniformly via `run_linker()` with LinkerContext

Why This Design
---------------
- Adding a new linker requires only creating the linker file
- No need to edit cli.py imports or run_behavior_map()
- Linkers can specify their own ordering priority
- Consistent interface for all linkers despite different needs

LinkerContext
-------------
Linkers have heterogeneous input needs (some need repo_root only,
others need filtered symbols, captured symbols, etc.). LinkerContext
provides all possible inputs, and each linker takes what it needs.

Usage
-----
In a linker module:

    from .registry import register_linker, LinkerContext, LinkerResult

    @register_linker("ipc", priority=50)
    def link_ipc(ctx: LinkerContext) -> LinkerResult:
        repo_root = ctx.repo_root
        # ... do linking ...
        return LinkerResult(symbols=symbols, edges=edges, run=run)

In cli.py:

    from .linkers.registry import get_all_linkers, run_all_linkers, LinkerContext

    ctx = LinkerContext(repo_root=repo_root, symbols=all_symbols, ...)
    results = run_all_linkers(ctx)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

if TYPE_CHECKING:
    from ..ir import AnalysisRun, Edge, Symbol


@dataclass
class LinkerContext:
    """Context passed to all linkers.

    Contains all possible inputs a linker might need. Each linker
    picks what it needs from this context.

    Attributes:
        repo_root: Repository root path
        symbols: All symbols collected so far
        edges: All edges collected so far
        captured_symbols: Symbols captured by specific analyzers (for JNI, etc.)
            Maps analyzer name to list of symbols (e.g., {"c": [...], "java": [...]})
    """

    repo_root: Path
    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    captured_symbols: dict[str, list[Symbol]] = field(default_factory=dict)


@dataclass
class LinkerResult:
    """Result from running a linker.

    Attributes:
        symbols: New symbols created by the linker
        edges: New edges created by the linker
        run: AnalysisRun metadata (optional)
    """

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


# Type alias for linker functions
LinkerFunc = Callable[[LinkerContext], LinkerResult]


@dataclass
class RegisteredLinker:
    """Metadata for a registered linker.

    Attributes:
        name: Unique identifier (e.g., "jni", "http", "ipc")
        func: The linker function
        priority: Execution order (lower = earlier). Default 50.
            Early linkers (JNI) run first; late linkers (dependency) run last.
        description: Human-readable description
    """

    name: str
    func: LinkerFunc
    priority: int = 50
    description: str = ""


# Global registry of linkers
_LINKER_REGISTRY: dict[str, RegisteredLinker] = {}


def register_linker(
    name: str,
    priority: int = 50,
    description: str = "",
) -> Callable[[LinkerFunc], LinkerFunc]:
    """Decorator to register a linker function.

    Args:
        name: Unique identifier for this linker (e.g., "jni", "http")
        priority: Execution order (lower = earlier).
        description: Human-readable description of what the linker does.

    Returns:
        Decorator that registers the function and returns it unchanged.

    Example:
        @register_linker("ipc", priority=50, description="IPC patterns")
        def link_ipc(ctx: LinkerContext) -> LinkerResult:
            ...
    """

    def decorator(func: LinkerFunc) -> LinkerFunc:
        _LINKER_REGISTRY[name] = RegisteredLinker(
            name=name,
            func=func,
            priority=priority,
            description=description,
        )
        return func

    return decorator


def get_linker(name: str) -> RegisteredLinker | None:
    """Get a registered linker by name.

    Args:
        name: The linker identifier

    Returns:
        The RegisteredLinker, or None if not found.
    """
    return _LINKER_REGISTRY.get(name)


def get_all_linkers() -> Iterator[RegisteredLinker]:
    """Get all registered linkers in priority order.

    Yields:
        RegisteredLinker objects, sorted by priority (ascending).
    """
    for linker in sorted(_LINKER_REGISTRY.values(), key=lambda lnk: lnk.priority):
        yield linker


def run_linker(
    name: str,
    ctx: LinkerContext,
) -> LinkerResult:
    """Run a specific linker by name.

    Args:
        name: The linker identifier
        ctx: LinkerContext with all inputs

    Returns:
        LinkerResult from the linker

    Raises:
        KeyError: If the linker is not registered.
    """
    linker = _LINKER_REGISTRY.get(name)
    if linker is None:
        raise KeyError(f"Unknown linker: {name}")
    return linker.func(ctx)


def run_all_linkers(ctx: LinkerContext) -> list[tuple[str, LinkerResult]]:
    """Run all registered linkers in priority order.

    Args:
        ctx: LinkerContext with all inputs

    Returns:
        List of (name, result) tuples in execution order.
    """
    results = []
    for linker in get_all_linkers():
        result = linker.func(ctx)
        results.append((linker.name, result))
    return results


def clear_registry() -> None:
    """Clear the linker registry. For testing only."""
    _LINKER_REGISTRY.clear()


def list_registered() -> list[str]:
    """List all registered linker names. For debugging."""
    return list(_LINKER_REGISTRY.keys())
