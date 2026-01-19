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

# Import UsageContext at runtime since it's used by matches_usage and extract_usage_value
from .ir import UsageContext


@dataclass
class UsagePatternSpec:
    """Specification for matching usage contexts.

    Usage patterns match against UsageContext records emitted by analyzers
    for call-based frameworks like Django, Express, Go Gin.

    Attributes:
        kind: Regex pattern to match context kind (call, data_value, export, macro)
        name: Regex pattern to match context_name (function called, var defined, etc.)
        position: Regex pattern to match position (args[1], :get, default, etc.)
    """

    kind: str | None = None
    name: str | None = None
    position: str | None = None

    # Compiled regex patterns
    _kind_re: re.Pattern | None = field(default=None, repr=False, compare=False)
    _name_re: re.Pattern | None = field(default=None, repr=False, compare=False)
    _position_re: re.Pattern | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Compile regex patterns for efficiency."""
        self._kind_re = re.compile(self.kind) if self.kind else None
        self._name_re = re.compile(self.name) if self.name else None
        self._position_re = re.compile(self.position) if self.position else None

    def matches(self, ctx: "UsageContext") -> bool:
        """Check if this spec matches the given usage context.

        All specified patterns must match for a match to succeed.
        Unspecified patterns (None) match anything.

        Args:
            ctx: The UsageContext to check

        Returns:
            True if all specified patterns match, False otherwise.
        """
        if self._kind_re and not self._kind_re.search(ctx.kind):
            return False
        if self._name_re and not self._name_re.search(ctx.context_name):
            return False
        if self._position_re and not self._position_re.search(ctx.position):
            return False
        return True


@dataclass
class Pattern:
    """A single pattern to match against symbol metadata or usage contexts.

    Patterns are OR'd within a concept - if any pattern matches, the concept
    is assigned to the symbol.

    Definition-based patterns (v1.0.x):
    - decorator, base_class, annotation, parameter_type, symbol_kind

    Definition-based patterns (v1.1.x):
    - parent_base_class: Match methods whose parent class extends a specific base
    - method_name: Match methods by name (the last part of qualified name)

    Language convention patterns (v1.2.x):
    - symbol_name: Match symbol name directly (for main() detection, etc.)
    - language: Filter by symbol's language (AND'd with other conditions)
    When symbol_name + symbol_kind are both specified, they're AND'd together.

    Usage-based patterns (v1.1.x):
    - usage: UsagePatternSpec to match against UsageContext records
    - extract: Dict of extraction expressions for extracting values from metadata

    Attributes:
        concept: The concept type this pattern identifies (route, model, task, etc.)
        decorator: Regex pattern to match against decorator names
        base_class: Regex pattern to match against base class names
        parent_base_class: Regex pattern to match parent class's base classes (for methods)
        method_name: Regex pattern to match method name (last part of qualified name)
        symbol_name: Regex pattern to match symbol name directly
        language: Regex pattern to filter by symbol's language
        annotation: Regex pattern to match against Java annotations
        parameter_type: Regex pattern to match against parameter types
        symbol_kind: Regex pattern to match against symbol kind field
        extract_path: JSONPath-like expression to extract route path from metadata
        extract_method: How to derive HTTP method (decorator_suffix, kwargs.methods, etc.)
        usage: UsagePatternSpec for matching against UsageContext (v1.1.x)
        extract: Dict of extraction expressions for usage-based patterns (v1.1.x)
    """

    concept: str
    decorator: str | None = None
    base_class: str | None = None
    parent_base_class: str | None = None
    method_name: str | None = None
    symbol_name: str | None = None
    language: str | None = None
    annotation: str | None = None
    parameter_type: str | None = None
    symbol_kind: str | None = None
    extract_path: str | None = None
    extract_method: str | None = None
    usage: UsagePatternSpec | None = None
    extract: dict[str, str] | None = None

    def __post_init__(self) -> None:
        """Compile regex patterns for efficiency."""
        self._decorator_re = re.compile(self.decorator) if self.decorator else None
        self._base_class_re = re.compile(self.base_class) if self.base_class else None
        self._parent_base_class_re = (
            re.compile(self.parent_base_class) if self.parent_base_class else None
        )
        self._method_name_re = (
            re.compile(self.method_name) if self.method_name else None
        )
        self._symbol_name_re = (
            re.compile(self.symbol_name) if self.symbol_name else None
        )
        self._language_re = re.compile(self.language) if self.language else None
        self._annotation_re = re.compile(self.annotation) if self.annotation else None
        self._param_type_re = (
            re.compile(self.parameter_type) if self.parameter_type else None
        )
        self._symbol_kind_re = (
            re.compile(self.symbol_kind) if self.symbol_kind else None
        )

    def matches(self, symbol: Symbol) -> dict[str, Any] | None:
        """Check if this pattern matches the given symbol.

        Args:
            symbol: The symbol to check against this pattern

        Returns:
            Dict with extracted data if matched, None otherwise.
            The dict always includes 'concept' and may include 'path', 'method', etc.
        """
        # Language filter: if specified, symbol's language must match (AND'd with other conditions)
        if self._language_re:
            if not symbol.language or not self._language_re.match(symbol.language):
                return None

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

        # Try symbol_name + symbol_kind combined match (for language conventions like main())
        # When both are specified, both must match (AND semantics)
        # When only symbol_name is specified, only it must match
        if self._symbol_name_re:
            # Check symbol_name (required)
            if not self._symbol_name_re.match(symbol.name):
                # symbol_name specified but doesn't match - don't match this pattern
                pass  # Fall through to other pattern types
            else:
                # symbol_name matches
                result["matched_symbol_name"] = symbol.name

                # Check symbol_kind if also specified (AND condition)
                if self._symbol_kind_re:
                    if self._symbol_kind_re.match(symbol.kind):
                        result["matched_symbol_kind"] = symbol.kind
                        return result
                    # symbol_kind specified but doesn't match
                    # Don't match this pattern
                else:
                    # Only symbol_name specified, and it matches
                    return result

        # Try symbol_kind match (alone, without symbol_name or parent_base_class/method_name)
        if self._symbol_kind_re and not self._symbol_name_re and not self._parent_base_class_re and not self._method_name_re:
            if self._symbol_kind_re.match(symbol.kind):
                result["matched_symbol_kind"] = symbol.kind
                return result

        # Try parent_base_class + method_name combined match (for lifecycle hooks)
        # Both conditions must match when both are specified
        if self._parent_base_class_re or self._method_name_re:
            parent_base_classes = (
                symbol.meta.get("parent_base_classes", []) if symbol.meta else []
            )

            # Check parent_base_class if specified
            parent_match = False
            matched_parent_base = None
            if self._parent_base_class_re:
                for base in parent_base_classes:
                    if self._parent_base_class_re.match(base):
                        parent_match = True
                        matched_parent_base = base
                        break
            else:
                # No parent_base_class constraint, so it passes
                parent_match = True

            # Check method_name if specified
            method_match = False
            matched_method = None
            if self._method_name_re:
                # Extract method name from qualified name (e.g., "MainActivity.onCreate" -> "onCreate")
                name_parts = symbol.name.rsplit(".", 1)
                method_name = name_parts[-1] if name_parts else symbol.name
                if self._method_name_re.match(method_name):
                    method_match = True
                    matched_method = method_name
            else:
                # No method_name constraint, so it passes
                method_match = True

            # Both must pass for a match
            if parent_match and method_match:
                if matched_parent_base:
                    result["matched_parent_base_class"] = matched_parent_base
                if matched_method:
                    result["matched_method_name"] = matched_method
                return result

        return None

    def _extract_value(self, metadata: dict[str, Any], path: str) -> str | None:
        """Extract a value from metadata using a simple path expression.

        Supports:
        - "args[0]" - first positional argument
        - "kwargs.key" - keyword argument by name
        - "value" - direct attribute
        - Multiple paths separated by "|" (tries each until one succeeds)

        Args:
            metadata: Decorator/annotation metadata dict
            path: Path expression (e.g., "args[0]", "kwargs.value", "args[0]|kwargs.value")

        Returns:
            Extracted value as string, or None if not found.
        """
        # Support multiple paths separated by "|" (try each in order)
        if "|" in path:
            for single_path in path.split("|"):
                result = self._extract_single_value(metadata, single_path.strip())
                if result:
                    return result
            return None
        return self._extract_single_value(metadata, path)

    def _extract_single_value(self, metadata: dict[str, Any], path: str) -> str | None:
        """Extract a value from metadata using a single path expression."""
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
                    method_str = str(methods[0])
                elif methods:
                    method_str = str(methods)
                else:
                    return None
                # Handle enum-style values like "RequestMethod.GET" -> "GET"
                if "." in method_str:
                    method_str = method_str.split(".")[-1]
                return method_str.upper()

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

    def matches_usage(self, ctx: "UsageContext") -> dict[str, Any] | None:
        """Check if this pattern matches the given usage context (v1.1.x).

        Usage patterns enable YAML-driven route detection for call-based frameworks
        like Django URL patterns, Express routes, and Go Gin handlers.

        Args:
            ctx: The UsageContext to check against this pattern

        Returns:
            Dict with extracted data if matched, None otherwise.
            The dict always includes 'concept' and may include 'path', 'method', etc.
        """
        if not self.usage:
            return None

        if not self.usage.matches(ctx):
            return None

        result: dict[str, Any] = {"concept": self.concept}

        # Extract values using the extract DSL
        if self.extract:
            for key, expr in self.extract.items():
                value = extract_usage_value(ctx, expr)
                if value is not None:
                    result[key] = value

        return result


def extract_usage_value(ctx: "UsageContext", expr: str) -> str | None:
    """Extract a value from a UsageContext using an extraction expression.

    Supported expressions:
    - "literal:VALUE" - constant value (e.g., "literal:GET")
    - "metadata.PATH" - dot-notation path into metadata dict (e.g., "metadata.args[0]")
    - "context_name" - the context_name field
    - Transformations: "expr | uppercase", "expr | lowercase", "expr | split:DELIM | last"

    Args:
        ctx: The UsageContext to extract from
        expr: Extraction expression

    Returns:
        Extracted value as string, or None if not found/applicable.
    """
    # Handle pipe transformations
    if " | " in expr:
        parts = expr.split(" | ")
        value = extract_usage_value(ctx, parts[0].strip())
        if value is None:
            return None
        for transform in parts[1:]:
            transform = transform.strip()
            if transform == "uppercase":
                value = value.upper()
            elif transform == "lowercase":
                value = value.lower()
            elif transform.startswith("split:"):
                delim = transform[6:]
                parts_split = value.split(delim)
                value = delim.join(parts_split)  # Keep value for next transform
            elif transform == "last":
                # Assumes previous was split, take last element
                if " | " in expr:
                    # Re-parse to find delimiter from previous split
                    for prev in reversed(parts[:parts.index(transform)]):
                        if prev.strip().startswith("split:"):
                            delim = prev.strip()[6:]
                            parts_split = value.split(delim)
                            value = parts_split[-1] if parts_split else value
                            break
        return value

    # Handle literal values
    if expr.startswith("literal:"):
        return expr[8:]

    # Handle context_name field
    if expr == "context_name":
        return ctx.context_name

    # Handle metadata paths
    if expr.startswith("metadata."):
        path = expr[9:]
        return _extract_from_metadata(ctx.metadata, path)

    # Handle position field
    if expr == "position":
        return ctx.position

    return None


def _extract_from_metadata(metadata: dict[str, Any], path: str) -> str | None:
    """Extract a value from metadata dict using a path expression.

    Supports:
    - "args[0]" - array index access
    - "kwargs.key" - nested dict access
    - "key" - direct key access

    Args:
        metadata: The metadata dict
        path: Path expression

    Returns:
        Extracted value as string, or None if not found.
    """
    if path.startswith("args["):
        try:
            idx = int(path[5:].split("]")[0])
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
            # Parse usage pattern spec if present (v1.1.x)
            usage_spec = None
            if "usage" in p:
                usage_data = p["usage"]
                usage_spec = UsagePatternSpec(
                    kind=usage_data.get("kind"),
                    name=usage_data.get("name"),
                    position=usage_data.get("position"),
                )

            patterns.append(Pattern(
                concept=p.get("concept", "unknown"),
                decorator=p.get("decorator"),
                base_class=p.get("base_class"),
                parent_base_class=p.get("parent_base_class"),
                method_name=p.get("method_name"),
                symbol_name=p.get("symbol_name"),
                language=p.get("language"),
                annotation=p.get("annotation"),
                parameter_type=p.get("parameter_type"),
                symbol_kind=p.get("symbol_kind"),
                extract_path=p.get("extract_path"),
                extract_method=p.get("extract_method"),
                usage=usage_spec,
                extract=p.get("extract"),
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


def match_usage_patterns(
    ctx: UsageContext,
    pattern_defs: list[FrameworkPatternDef],
) -> list[dict[str, Any]]:
    """Match a usage context against framework patterns (v1.1.x).

    Args:
        ctx: UsageContext to match
        pattern_defs: List of framework pattern definitions to try

    Returns:
        List of match results (concept dicts). Empty if no matches.
    """
    results = []
    for pattern_def in pattern_defs:
        for pattern in pattern_def.patterns:
            match = pattern.matches_usage(ctx)
            if match:
                match["framework"] = pattern_def.id
                results.append(match)

    return results


def enrich_symbols(
    symbols: list[Symbol],
    detected_frameworks: set[str],
    usage_contexts: list[UsageContext] | None = None,
) -> list[Symbol]:
    """Enrich symbols with framework concept metadata.

    Three-phase enrichment (v1.2.x):
    1. Language conventions: Match main() functions and other language-level patterns
    2. Definition-based: Match against decorators, base classes, annotations
    3. Usage-based: Match against UsageContext records for call-based frameworks

    Args:
        symbols: Symbols to enrich
        detected_frameworks: Set of detected framework IDs
        usage_contexts: List of UsageContext records for usage-based matching (v1.1.x)

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

    # Always load language convention patterns
    # These are applied regardless of framework detection:
    # - main-functions.yaml: main() entry points across languages
    # - test-frameworks.yaml: test function detection across frameworks
    # - language-conventions.yaml: CUDA, WGSL, COBOL, LaTeX, Starlark patterns
    # - config-conventions.yaml: NPM, Maven, Cargo dependency patterns
    for convention_id in ("main-functions", "test-frameworks", "language-conventions", "config-conventions"):
        convention_patterns = load_framework_patterns(convention_id)
        if convention_patterns:
            pattern_defs.append(convention_patterns)

    if not pattern_defs:  # pragma: no cover - main-functions.yaml is always loaded
        return symbols

    # Build symbol lookup by ID for usage-based matching
    symbol_by_id: dict[str, Symbol] = {s.id: s for s in symbols}

    # Phase 1: Definition-based matching (decorators, base classes, annotations)
    for symbol in symbols:
        matches = match_patterns(symbol, pattern_defs)
        if matches:
            # Add matched concepts to symbol metadata
            if symbol.meta is None:  # pragma: no cover - patterns require meta to match
                symbol.meta = {}
            symbol.meta["concepts"] = matches

    # Phase 2: Usage-based matching (v1.1.x)
    if usage_contexts:
        for ctx in usage_contexts:
            # Skip if no symbol reference (inline handlers not yet supported)
            if not ctx.symbol_ref:
                continue

            # Find the referenced symbol
            symbol = symbol_by_id.get(ctx.symbol_ref)
            if not symbol:
                continue

            # Match against usage patterns
            matches = match_usage_patterns(ctx, pattern_defs)
            if matches:
                if symbol.meta is None:
                    symbol.meta = {}

                # Append to existing concepts or create new list
                existing = symbol.meta.get("concepts", [])
                symbol.meta["concepts"] = existing + matches

    return symbols


def clear_pattern_cache() -> None:
    """Clear the pattern cache. For testing only."""
    _PATTERN_CACHE.clear()
