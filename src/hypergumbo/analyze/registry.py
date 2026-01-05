"""Analyzer registry for dynamic dispatch.

This module provides a registration system for language analyzers,
enabling loop-based dispatch in run_behavior_map() instead of
50+ repetitive code blocks.

How It Works
------------
1. Each analyzer module calls `register_analyzer()` at import time
2. The registry stores analyzer functions by name
3. `run_behavior_map()` iterates over `get_all_analyzers()`
4. Each analyzer is called uniformly via `run_analyzer()`

Why This Design
---------------
- Adding a new language requires only creating the analyzer file
- No need to edit cli.py imports or run_behavior_map()
- Analyzers can specify their own ordering priority
- Consistent interface for all analyzers

Usage
-----
In an analyzer module:

    from .registry import register_analyzer
    from .base import AnalysisResult

    @register_analyzer("go", priority=50)
    def analyze_go(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        ...

In cli.py:

    from .analyze.registry import get_all_analyzers, run_all_analyzers

    results = run_all_analyzers(repo_root, max_files=max_files)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from .base import AnalysisResult

# Type alias for analyzer functions
AnalyzerFunc = Callable[..., AnalysisResult]


@dataclass
class RegisteredAnalyzer:
    """Metadata for a registered analyzer.

    Attributes:
        name: Unique identifier (e.g., "go", "rust", "python")
        func: The analyzer function
        priority: Execution order (lower = earlier). Default 50.
            Core analyzers (python, html) use priority 10.
            Tree-sitter analyzers use priority 50.
        requires_symbols: List of analyzer names whose symbols this
            analyzer needs (for linkers that need symbols from language analyzers)
    """

    name: str
    func: AnalyzerFunc
    priority: int = 50
    requires_symbols: list[str] | None = None


# Global registry of analyzers
_ANALYZER_REGISTRY: dict[str, RegisteredAnalyzer] = {}


def register_analyzer(
    name: str,
    priority: int = 50,
    requires_symbols: list[str] | None = None,
) -> Callable[[AnalyzerFunc], AnalyzerFunc]:
    """Decorator to register an analyzer function.

    Args:
        name: Unique identifier for this analyzer (e.g., "go", "rust")
        priority: Execution order (lower = earlier). Use 10 for core,
            50 for language analyzers, 90 for linkers.
        requires_symbols: Other analyzers whose symbols this one needs.

    Returns:
        Decorator that registers the function and returns it unchanged.

    Example:
        @register_analyzer("go", priority=50)
        def analyze_go(repo_root: Path) -> AnalysisResult:
            ...
    """

    def decorator(func: AnalyzerFunc) -> AnalyzerFunc:
        _ANALYZER_REGISTRY[name] = RegisteredAnalyzer(
            name=name,
            func=func,
            priority=priority,
            requires_symbols=requires_symbols,
        )
        return func

    return decorator


def get_analyzer(name: str) -> RegisteredAnalyzer | None:  # pragma: no cover
    """Get a registered analyzer by name.

    Args:
        name: The analyzer identifier

    Returns:
        The RegisteredAnalyzer, or None if not found.
    """
    return _ANALYZER_REGISTRY.get(name)


def get_all_analyzers() -> Iterator[RegisteredAnalyzer]:  # pragma: no cover
    """Get all registered analyzers in priority order.

    Yields:
        RegisteredAnalyzer objects, sorted by priority (ascending).
    """
    for analyzer in sorted(_ANALYZER_REGISTRY.values(), key=lambda a: a.priority):
        yield analyzer


def run_analyzer(  # pragma: no cover
    name: str,
    repo_root: Path,
    **kwargs,
) -> AnalysisResult:
    """Run a specific analyzer by name.

    Args:
        name: The analyzer identifier
        repo_root: Repository root path
        **kwargs: Additional arguments passed to the analyzer

    Returns:
        AnalysisResult from the analyzer

    Raises:
        KeyError: If the analyzer is not registered.
    """
    analyzer = _ANALYZER_REGISTRY.get(name)
    if analyzer is None:
        raise KeyError(f"Unknown analyzer: {name}")
    return analyzer.func(repo_root, **kwargs)


def run_all_analyzers(  # pragma: no cover
    repo_root: Path,
    **kwargs,
) -> list[tuple[str, AnalysisResult]]:
    """Run all registered analyzers in priority order.

    Args:
        repo_root: Repository root path
        **kwargs: Additional arguments passed to each analyzer

    Returns:
        List of (name, result) tuples in execution order.
    """
    results = []
    for analyzer in get_all_analyzers():
        result = analyzer.func(repo_root, **kwargs)
        results.append((analyzer.name, result))
    return results


def clear_registry() -> None:  # pragma: no cover
    """Clear the analyzer registry. For testing only."""
    _ANALYZER_REGISTRY.clear()


def list_registered() -> list[str]:  # pragma: no cover
    """List all registered analyzer names. For debugging."""
    return list(_ANALYZER_REGISTRY.keys())
