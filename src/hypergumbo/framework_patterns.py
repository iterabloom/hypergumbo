"""Framework pattern matching for symbol enrichment (ADR-0003 v0.8.x).

This module provides data-driven framework detection using YAML pattern files.
Instead of hardcoding framework-specific logic in analyzers, patterns are
externalized to YAML files that match against symbol metadata.

How It Works
------------
1. Each framework has a YAML file in src/hypergumbo/frameworks/ (e.g., fastapi.yaml)
2. Patterns match against symbol metadata (decorators, base_classes, annotations)
3. When a pattern matches, the symbol is enriched with a "concept" (route, model, etc.)
4. Linkers use concepts to understand symbol semantics without framework knowledge

Pattern Types
-------------
- Decorator patterns: Match function/method decorators (e.g., @app.get)
- Base class patterns: Match class inheritance (e.g., BaseModel)
- Annotation patterns: Match Java annotations (e.g., @RequestMapping)
- Parameter type patterns: Match function parameter types (e.g., Depends)

Why This Design
---------------
- Separation of concerns: Analyzers extract metadata, patterns add semantics
- Extensibility: New frameworks added by creating YAML files, no code changes
- Maintainability: Framework-specific logic is centralized and declarative
- Testing: Patterns can be validated independently of analyzer code

Usage
-----
    from hypergumbo.framework_patterns import (
        load_framework_patterns,
        match_patterns,
        enrich_symbols,
    )

    # Load patterns for detected frameworks
    patterns = [load_framework_patterns(fw) for fw in detected_frameworks]

    # Enrich symbols with matched concepts
    enriched = enrich_symbols(symbols, patterns)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .ir import Symbol


@dataclass
class Pattern:
    """A single pattern to match against symbol metadata.

    Patterns are OR'd within a concept - if any pattern matches, the concept
    is assigned to the symbol.

    Attributes:
        concept: The concept type this pattern identifies (route, model, task, etc.)
        decorator: Regex pattern to match against decorator names
        base_class: Regex pattern to match against base class names
        annotation: Regex pattern to match against Java annotations
        parameter_type: Regex pattern to match against parameter types
        extract_path: JSONPath-like expression to extract route path from metadata
        extract_method: How to derive HTTP method (decorator_suffix, kwargs.methods, etc.)
    """

    concept: str
    decorator: str | None = None
    base_class: str | None = None
    annotation: str | None = None
    parameter_type: str | None = None
    extract_path: str | None = None
    extract_method: str | None = None

    def __post_init__(self) -> None:
        """Compile regex patterns for efficiency."""
        self._decorator_re = re.compile(self.decorator) if self.decorator else None
        self._base_class_re = re.compile(self.base_class) if self.base_class else None
        self._annotation_re = re.compile(self.annotation) if self.annotation else None
        self._param_type_re = (
            re.compile(self.parameter_type) if self.parameter_type else None
        )

    def matches(self, symbol: Symbol) -> dict[str, Any] | None:
        """Check if this pattern matches the given symbol.

        Args:
            symbol: The symbol to check against this pattern

        Returns:
            Dict with extracted data if matched, None otherwise.
            The dict always includes 'concept' and may include 'path', 'method', etc.
        """
        # Get symbol metadata for matching
        decorators = symbol.meta.get("decorators", []) if symbol.meta else []
        base_classes = symbol.meta.get("base_classes", []) if symbol.meta else []
        annotations = symbol.meta.get("annotations", []) if symbol.meta else []
        parameters = symbol.meta.get("parameters", []) if symbol.meta else []

        result: dict[str, Any] = {"concept": self.concept}

        # Try decorator match
        if self._decorator_re:
            for dec in decorators:
                dec_name = dec.get("name", "") if isinstance(dec, dict) else str(dec)
                match = self._decorator_re.match(dec_name)
                if match:
                    result["matched_decorator"] = dec_name
                    if self.extract_path and isinstance(dec, dict):
                        path = self._extract_value(dec, self.extract_path)
                        if path:
                            result["path"] = path
                    if self.extract_method:
                        method = self._extract_http_method(dec, match, dec_name)
                        if method:
                            result["method"] = method
                    return result

        # Try base class match
        if self._base_class_re:
            for base in base_classes:
                if self._base_class_re.match(base):
                    result["matched_base_class"] = base
                    return result

        # Try annotation match (Java)
        if self._annotation_re:
            for ann in annotations:
                ann_name = ann.get("name", "") if isinstance(ann, dict) else str(ann)
                match = self._annotation_re.match(ann_name)
                if match:
                    result["matched_annotation"] = ann_name
                    if self.extract_path and isinstance(ann, dict):
                        path = self._extract_value(ann, self.extract_path)
                        if path:
                            result["path"] = path
                    if self.extract_method:
                        method = self._extract_http_method_from_annotation(ann, match, ann_name)
                        if method:
                            result["method"] = method
                    return result

        # Try parameter type match
        if self._param_type_re:
            for param in parameters:
                param_type = (
                    param.get("type") or "" if isinstance(param, dict) else str(param)
                )
                if param_type and self._param_type_re.match(param_type):
                    result["matched_parameter_type"] = param_type
                    return result

        return None

    def _extract_value(self, metadata: dict[str, Any], path: str) -> str | None:
        """Extract a value from metadata using a simple path expression.

        Supports:
        - "args[0]" - first positional argument
        - "kwargs.key" - keyword argument by name
        - "value" - direct attribute

        Args:
            metadata: Decorator/annotation metadata dict
            path: Path expression (e.g., "args[0]", "kwargs.methods")

        Returns:
            Extracted value as string, or None if not found.
        """
        if path.startswith("args["):
            # Extract array index
            try:
                idx = int(path[5:].rstrip("]"))
                args = metadata.get("args", [])
                if idx < len(args):
                    return str(args[idx])
            except (ValueError, IndexError):
                pass
        elif path.startswith("kwargs."):
            key = path[7:]
            kwargs = metadata.get("kwargs", {})
            if key in kwargs:
                return str(kwargs[key])
        else:
            if path in metadata:
                return str(metadata[path])

        return None

    def _extract_http_method(
        self, metadata: dict[str, Any] | str, match: re.Match, dec_name: str
    ) -> str | None:
        """Extract HTTP method from decorator match.

        Args:
            metadata: Decorator metadata
            match: Regex match object from decorator name
            dec_name: The matched decorator name (e.g., "Get", "app.get")

        Returns:
            HTTP method string (GET, POST, etc.) or None.
        """
        if self.extract_method == "decorator_suffix":
            # Extract method from decorator name suffix (e.g., app.get -> GET)
            groups = match.groups()
            if groups:
                return groups[-1].upper()
        elif self.extract_method == "decorator_name_upper":
            # Use the decorator name directly as the method (e.g., Get -> GET)
            # This is useful for NestJS-style decorators where @Get() = GET method
            return dec_name.upper()
        elif self.extract_method and self.extract_method.startswith("kwargs."):
            # Extract from kwargs
            if isinstance(metadata, dict):
                key = self.extract_method[7:]
                kwargs = metadata.get("kwargs", {})
                methods = kwargs.get(key)
                if isinstance(methods, list) and methods:
                    return str(methods[0]).upper()
                elif methods:
                    return str(methods).upper()

        return None

    def _extract_http_method_from_annotation(
        self, metadata: dict[str, Any] | str, match: re.Match, ann_name: str
    ) -> str | None:
        """Extract HTTP method from annotation match.

        Args:
            metadata: Annotation metadata
            match: Regex match object from annotation name
            ann_name: The matched annotation name (e.g., "@GetMapping")

        Returns:
            HTTP method string (GET, POST, etc.) or None.
        """
        if self.extract_method == "annotation_prefix":
            # Extract method from the first regex capture group
            # e.g., @GetMapping -> "Get" capture group -> "GET"
            groups = match.groups()
            if groups:
                return groups[0].upper()
        elif self.extract_method == "annotation_name_upper":
            # Use the annotation name directly (strip @ prefix)
            if ann_name.startswith("@"):
                return ann_name[1:].upper()
            return ann_name.upper()

        return None


@dataclass
class FrameworkPatternDef:
    """Framework pattern definition loaded from YAML.

    Attributes:
        id: Unique framework identifier (e.g., "fastapi", "spring")
        language: Primary language for this framework
        patterns: List of patterns to match
        linkers: Linkers that should be activated when this framework is detected
    """

    id: str
    language: str
    patterns: list[Pattern] = field(default_factory=list)
    linkers: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FrameworkPatternDef:
        """Create a FrameworkPatternDef from a dict (parsed YAML).

        Args:
            data: Dict with framework pattern data

        Returns:
            FrameworkPatternDef instance
        """
        patterns = []
        for p in data.get("patterns", []):
            patterns.append(Pattern(
                concept=p.get("concept", "unknown"),
                decorator=p.get("decorator"),
                base_class=p.get("base_class"),
                annotation=p.get("annotation"),
                parameter_type=p.get("parameter_type"),
                extract_path=p.get("extract_path"),
                extract_method=p.get("extract_method"),
            ))

        return cls(
            id=data.get("id", "unknown"),
            language=data.get("language", "unknown"),
            patterns=patterns,
            linkers=data.get("linkers", []),
        )


# Cache for loaded framework patterns
_PATTERN_CACHE: dict[str, FrameworkPatternDef | None] = {}


def get_frameworks_dir() -> Path:
    """Get the path to the frameworks directory.

    Returns:
        Path to src/hypergumbo/frameworks/
    """
    return Path(__file__).parent / "frameworks"


def load_framework_patterns(framework_id: str) -> FrameworkPatternDef | None:
    """Load framework patterns from YAML file.

    Args:
        framework_id: Framework identifier (e.g., "fastapi")

    Returns:
        FrameworkPatternDef if found, None otherwise.
    """
    if framework_id in _PATTERN_CACHE:
        return _PATTERN_CACHE[framework_id]

    yaml_path = get_frameworks_dir() / f"{framework_id}.yaml"
    if not yaml_path.exists():
        _PATTERN_CACHE[framework_id] = None
        return None

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    pattern_def = FrameworkPatternDef.from_dict(data)
    _PATTERN_CACHE[framework_id] = pattern_def
    return pattern_def


def match_patterns(
    symbol: Symbol,
    pattern_defs: list[FrameworkPatternDef],
) -> list[dict[str, Any]]:
    """Match a symbol against framework patterns.

    Args:
        symbol: Symbol to match
        pattern_defs: List of framework pattern definitions to try

    Returns:
        List of match results (concept dicts). Empty if no matches.
    """
    results = []
    for pattern_def in pattern_defs:
        for pattern in pattern_def.patterns:
            match = pattern.matches(symbol)
            if match:
                match["framework"] = pattern_def.id
                results.append(match)

    return results


def enrich_symbols(
    symbols: list[Symbol],
    detected_frameworks: set[str],
) -> list[Symbol]:
    """Enrich symbols with framework concept metadata.

    Args:
        symbols: Symbols to enrich
        detected_frameworks: Set of detected framework IDs

    Returns:
        Same symbols, possibly with updated metadata.
        Note: Modifies symbols in place and returns same list.
    """
    # Load patterns for detected frameworks
    pattern_defs = []
    for fw_id in detected_frameworks:
        pattern_def = load_framework_patterns(fw_id)
        if pattern_def:
            pattern_defs.append(pattern_def)

    if not pattern_defs:
        return symbols

    # Match each symbol against patterns
    for symbol in symbols:
        matches = match_patterns(symbol, pattern_defs)
        if matches:
            # Add matched concepts to symbol metadata
            if symbol.meta is None:  # pragma: no cover - patterns require meta to match
                symbol.meta = {}
            symbol.meta["concepts"] = matches

    return symbols


def clear_pattern_cache() -> None:
    """Clear the pattern cache. For testing only."""
    _PATTERN_CACHE.clear()
