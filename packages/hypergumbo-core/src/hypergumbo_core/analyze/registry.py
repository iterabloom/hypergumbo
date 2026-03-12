# SPDX-License-Identifier: AGPL-3.0-or-later
"""Analyzer registry for decorator-based dynamic dispatch.

This module provides the canonical registration system for language analyzers,
mirroring the proven pattern from linkers/registry.py. Analyzers self-register
via the @register_analyzer decorator at import time; entry-point-based plugin
discovery triggers the imports.

How It Works
------------
1. Each analyzer module decorates its entry function with @register_analyzer()
2. Language packages export ANALYZER_MODULES lists via entry-points
3. ensure_discovered() loads entry-points, imports listed modules (triggering decorators)
4. run_all_analyzers() / get_all_analyzers() iterate the populated registry

Why This Design
---------------
- Self-registration: the analyzer file is self-describing (decorator on the function)
- Plugin extensibility: entry-points enable external language packages
- Rich metadata: priority, supports_max_files, capture_symbols_as, requires_symbols
- Consistency: mirrors the linker registry pattern (ADR-0012 Step 1)

Usage
-----
In an analyzer module:

    from hypergumbo_core.analyze.registry import register_analyzer

    @register_analyzer("go", priority=50)
    def analyze_go(repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        ...

Discovery happens automatically via entry-points when ensure_discovered() is called.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from .base import AnalysisResult

logger = logging.getLogger(__name__)

# Type alias for analyzer functions
AnalyzerFunc = Callable[..., AnalysisResult]


@dataclass
class RegisteredAnalyzer:
    """Metadata for a registered analyzer.

    Attributes:
        name: Unique identifier (e.g., "go", "rust", "python")
        func: The analyzer function (direct reference from decorator)
        module_path: Module containing the function (for test patchability)
        func_name: Function name in the module (for test patchability)
        priority: Execution order (lower = earlier). Default 50.
        requires_symbols: List of analyzer names whose symbols this
            analyzer needs (for future multi-pass support)
        supports_max_files: Whether this analyzer accepts a max_files parameter
        capture_symbols_as: If set, store this analyzer's symbols under
            this key in captured_symbols dict for linkers (e.g., "java" for JNI)
    """

    name: str
    func: AnalyzerFunc
    module_path: str | None = None
    func_name: str | None = None
    priority: int = 50
    requires_symbols: list[str] | None = None
    supports_max_files: bool = False
    capture_symbols_as: str | None = None

    def get_func(self) -> AnalyzerFunc:
        """Get the analyzer function, resolving from module for patchability.

        When module_path and func_name are set, looks up the function from
        the module at call time rather than using the stored reference. This
        enables test patching (e.g., ``patch("module.func", ...)``) to work
        correctly, since the module attribute is checked at each call.

        Falls back to the stored function reference if the module-level
        lookup fails (e.g., for functions defined inside test methods that
        aren't module-level attributes).
        """
        if self.module_path and self.func_name:
            try:
                module = importlib.import_module(self.module_path)
                return getattr(module, self.func_name)
            except (ImportError, AttributeError):
                return self.func
        return self.func


# Global registry of analyzers
_ANALYZER_REGISTRY: dict[str, RegisteredAnalyzer] = {}

# Discovery flag — ensures entry-point loading happens only once
_discovered: bool = False


def register_analyzer(
    name: str,
    priority: int = 50,
    requires_symbols: list[str] | None = None,
    supports_max_files: bool = False,
    capture_symbols_as: str | None = None,
) -> Callable[[AnalyzerFunc], AnalyzerFunc]:
    """Decorator to register an analyzer function.

    Args:
        name: Unique identifier for this analyzer (e.g., "go", "rust")
        priority: Execution order (lower = earlier). Default 50.
        requires_symbols: Other analyzers whose symbols this one needs.
        supports_max_files: Whether the analyzer accepts max_files parameter.
        capture_symbols_as: Key for storing symbols in captured_symbols dict.

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
            module_path=func.__module__,
            func_name=func.__name__,
            priority=priority,
            requires_symbols=requires_symbols,
            supports_max_files=supports_max_files,
            capture_symbols_as=capture_symbols_as,
        )
        return func

    return decorator


def get_analyzer(name: str) -> RegisteredAnalyzer | None:
    """Get a registered analyzer by name.

    Args:
        name: The analyzer identifier

    Returns:
        The RegisteredAnalyzer, or None if not found.
    """
    return _ANALYZER_REGISTRY.get(name)


def get_all_analyzers() -> Iterator[RegisteredAnalyzer]:
    """Get all registered analyzers in priority order.

    Yields:
        RegisteredAnalyzer objects, sorted by priority (ascending).
    """
    for analyzer in sorted(_ANALYZER_REGISTRY.values(), key=lambda a: a.priority):
        yield analyzer


def run_analyzer(
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
        available = ", ".join(sorted(_ANALYZER_REGISTRY)) or "none registered"
        raise KeyError(f"Unknown analyzer: {name!r}. Available: {available}")
    return analyzer.get_func()(repo_root, **kwargs)


def run_all_analyzers(
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
        result = analyzer.get_func()(repo_root, **kwargs)
        results.append((analyzer.name, result))
    return results


def _load_entry_point_modules() -> None:
    """Load analyzer modules discovered via entry-points.

    Handles two formats for the 'hypergumbo.analyzers' entry-point group:

    1. **Module list (target format):** A list of module path strings.
       Importing each module triggers @register_analyzer decorators.
       Example: ["hypergumbo_lang_mainstream.bash", "hypergumbo_lang_mainstream.go"]

    2. **AnalyzerSpec list (transition format):** A list of AnalyzerSpec
       NamedTuples. Each spec is converted to a RegisteredAnalyzer and added
       to the registry directly. This allows gradual migration from the old
       AnalyzerSpec system — packages can switch one at a time.

    Format detection: if the first element is a string, treat as module list;
    if it has a 'module_path' attribute, treat as AnalyzerSpec list.
    """
    try:
        eps = importlib.metadata.entry_points(group="hypergumbo.analyzers")
    except Exception:  # pragma: no cover
        logger.debug("Failed to load entry_points for hypergumbo.analyzers")
        return

    for ep in eps:
        try:
            entry_list = ep.load()
        except Exception:
            logger.debug("Failed to load entry point %s", ep.name)
            continue

        if not isinstance(entry_list, list) or not entry_list:
            logger.debug("Entry point %s did not return a non-empty list, skipping", ep.name)
            continue

        # Detect format from first element
        first = entry_list[0]
        if isinstance(first, str):
            # New format: list of module paths to import
            _import_module_list(entry_list)
        elif hasattr(first, "module_path"):
            # Transition format: list of AnalyzerSpec NamedTuples
            _register_from_specs(entry_list)
        else:
            logger.debug(
                "Entry point %s returned unknown format, skipping", ep.name
            )


def _import_module_list(module_paths: list[str]) -> None:
    """Import a list of module paths to trigger @register_analyzer decorators."""
    for module_path in module_paths:
        try:
            importlib.import_module(module_path)
        except Exception:
            logger.debug("Failed to import analyzer module %s", module_path)


def _register_from_specs(specs: list) -> None:
    """Register analyzers from AnalyzerSpec NamedTuples (transition support).

    Converts each AnalyzerSpec to a RegisteredAnalyzer by lazily loading
    the analyzer function from the spec's module_path and func_name.
    """
    for spec in specs:
        if spec.name in _ANALYZER_REGISTRY:
            continue  # Already registered (e.g., via decorator)
        try:
            module = importlib.import_module(spec.module_path)
            func = getattr(module, spec.func_name)
            _ANALYZER_REGISTRY[spec.name] = RegisteredAnalyzer(
                name=spec.name,
                func=func,
                module_path=spec.module_path,
                func_name=spec.func_name,
                supports_max_files=getattr(spec, "supports_max_files", False),
                capture_symbols_as=getattr(spec, "capture_symbols_as", None),
            )
        except Exception:
            logger.debug(
                "Failed to register analyzer %s from spec", spec.name
            )


def ensure_discovered() -> None:
    """Ensure all analyzer entry-points have been loaded.

    On first call, loads entry-points from the 'hypergumbo.analyzers' group
    and imports the listed modules (triggering @register_analyzer decorators).
    Subsequent calls are no-ops until clear_registry() resets the flag.
    """
    global _discovered
    if _discovered:
        return
    _discovered = True
    _load_entry_point_modules()


def clear_registry() -> None:
    """Clear the analyzer registry and reset discovery. For testing only."""
    global _discovered
    _ANALYZER_REGISTRY.clear()
    _discovered = False


def list_registered() -> list[str]:
    """List all registered analyzer names. For debugging."""
    return list(_ANALYZER_REGISTRY.keys())
