"""Framework pattern matching for symbol enrichment (ADR-0003).

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
**Definition-based (v0.8.x):** Match against symbol definition metadata.

- Decorator patterns: Match function/method decorators (e.g., @app.get)
- Base class patterns: Match class inheritance (e.g., BaseModel)
- Annotation patterns: Match Java annotations (e.g., @RequestMapping)
- Parameter type patterns: Match function parameter types (e.g., Depends)

**Usage-based (v1.1.x):** Match against UsageContext records for call-based
frameworks where routing is done via function calls rather than decorators.

- Usage patterns: Match UsageContext kind/name/position (e.g., Django path(),
  Flask add_url_rule(), Go gin.GET())
- Extraction expressions: Pull route paths and methods from UsageContext metadata

Relationship to Route Symbols
-----------------------------
Enrichment adds ``concept: route`` to *handler* symbols (e.g., a Django view
function gets tagged as a route handler). This is complementary to — not a
replacement for — the ``kind="route"`` symbols that language analyzers create.
Route symbols are first-class IR entities representing the route itself, consumed
by the ``route_handler`` linker to create ``routes_to`` edges. Both outputs are
derived from the same UsageContext extraction pass in each analyzer.

Why This Design
---------------
- Separation of concerns: Analyzers extract metadata, patterns add semantics
- Extensibility: New frameworks added by creating YAML files, no code changes
- Maintainability: Framework-specific logic is centralized and declarative
- Testing: Patterns can be validated independently of analyzer code

Usage
-----
    from hypergumbo_core.framework_patterns import (
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

    Path inheritance (v1.3.x):
    - prefix_from_parent: Inherit path prefix from parent class's concept
      Example: NestJS @Get(':id') on method inherits prefix from @Controller('/users')
      on class, resulting in combined path '/users/:id'. The value is the concept
      name to look up on the parent class (e.g., "controller").

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
        modifiers: Regex pattern requiring at least one modifier to match (positive filter)
        modifiers_exclude: Regex pattern to reject symbols with matching modifiers
        symbol_path: Regex pattern to match against symbol's file path (search, not match)
        extract_path: JSONPath-like expression to extract route path from metadata
        extract_method: How to derive HTTP method (decorator_suffix, kwargs.methods, etc.)
        prefix_from_parent: Concept name to look up on parent class for path prefix
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
    prefix_from_parent: str | None = None
    modifiers: str | None = None
    modifiers_exclude: str | None = None
    symbol_path: str | None = None
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
        self._modifiers_re = (
            re.compile(self.modifiers) if self.modifiers else None
        )
        self._modifiers_exclude_re = (
            re.compile(self.modifiers_exclude) if self.modifiers_exclude else None
        )
        self._symbol_path_re = (
            re.compile(self.symbol_path) if self.symbol_path else None
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

        # File path filter: if specified, symbol's file path must match
        # Used for Python __init__.py library export detection
        if self._symbol_path_re:
            if not symbol.path or not self._symbol_path_re.search(symbol.path):
                return None

        # Modifiers positive filter: require at least one modifier to match
        # Used for Python re-exported symbols (re_exported modifier)
        if self._modifiers_re:
            if not symbol.modifiers:
                return None
            if not any(self._modifiers_re.match(m) for m in symbol.modifiers):
                return None

        # Modifiers exclusion filter: reject symbols with any matching modifier
        # Used to exclude private functions (defp in Elixir, etc.)
        if self._modifiers_exclude_re and symbol.modifiers:
            for modifier in symbol.modifiers:
                if self._modifiers_exclude_re.match(modifier):
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
                        # Always set path when extract_path is configured - empty string
                        # for decorators with no path arg (like @Get()) so that
                        # prefix_from_parent can still combine with controller prefix
                        result["path"] = path if path else ""
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
                        # Always set path when extract_path is configured - empty string
                        # for annotations with no path arg so that prefix combination works
                        result["path"] = path if path else ""
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
        # Note: We use search() instead of match() to support end-of-string patterns
        # like "\.mount$" for Elixir qualified names (e.g., "MyApp.Pages.Index.mount")
        if self._symbol_name_re:
            # Check symbol_name (required)
            if not self._symbol_name_re.search(symbol.name):
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

        # Try symbol_kind match (alone, without any other specific match field).
        # If decorator/base_class/annotation/param_type were specified but didn't
        # match above, we must NOT fall through to symbol_kind-only matching —
        # those fields are AND conditions, not independent alternatives.
        if (
            self._symbol_kind_re
            and not self._symbol_name_re
            and not self._parent_base_class_re
            and not self._method_name_re
            and not self._decorator_re
            and not self._base_class_re
            and not self._annotation_re
            and not self._param_type_re
        ):
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
        """Extract a value from metadata using a single path expression.

        Unwraps single-element lists — Java array initializer syntax like
        ``@GetMapping({ "/vets" })`` produces ``args: [["/vets"]]``, so
        ``args[0]`` yields ``["/vets"]`` which must be unwrapped to ``"/vets"``.
        """
        if path.startswith("args["):
            # Extract array index
            try:
                idx = int(path[5:].rstrip("]"))
                args = metadata.get("args", [])
                if idx < len(args):
                    val = args[idx]
                    # Unwrap single-element lists (Java array initializers)
                    if isinstance(val, list) and len(val) == 1:
                        val = val[0]
                    return str(val)
            except (ValueError, IndexError):
                pass
        elif path.startswith("kwargs."):
            key = path[7:]
            kwargs = metadata.get("kwargs", {})
            if key in kwargs:
                val = kwargs[key]
                # Unwrap single-element lists (Java array initializers)
                if isinstance(val, list) and len(val) == 1:
                    val = val[0]
                return str(val)
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
                # Assumes previous was split, take last element.
                # Re-parse to find delimiter from previous split.
                # (We're always inside the " | " branch from line 520,
                # so parts is guaranteed to exist.)
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
            val = metadata[path]
            # List values (e.g., methods=["GET", "POST"]): join with comma
            if isinstance(val, list):
                return ",".join(str(v) for v in val)
            return str(val)

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
                modifiers=p.get("modifiers"),
                modifiers_exclude=p.get("modifiers_exclude"),
                symbol_path=p.get("symbol_path"),
                extract_path=p.get("extract_path"),
                extract_method=p.get("extract_method"),
                prefix_from_parent=p.get("prefix_from_parent"),
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

# Framework alias mapping: maps detected framework names to pattern file names
# Used when multiple frameworks share a single pattern file
_FRAMEWORK_ALIASES: dict[str, str] = {
    # Go web frameworks -> go-web.yaml
    "gin": "go-web",
    "echo": "go-web",
    "fiber": "go-web",
    "chi": "go-web",
    "gorilla": "go-web",
    "buffalo": "go-web",
    "revel": "go-web",
    "beego": "go-web",
    "iris": "go-web",
    "prometheus-common": "go-web",  # github.com/prometheus/common/route (chi-like)
    "xorm": "go-web",  # xorm.io/xorm (Go ORM used by Forgejo/Gitea)
    # Rust web frameworks -> rust-web.yaml
    "actix-web": "rust-web",
    "axum": "rust-web",
    "rocket": "rust-web",
    "warp": "rust-web",
    "tide": "rust-web",
    "gotham": "rust-web",
    "poem": "rust-web",
    "salvo": "rust-web",
    # Jenkins Stapler URL dispatch -> stapler.yaml
    "jenkins": "stapler",
    # Java JAX-RS implementations -> jax-rs.yaml
    "dropwizard": "jax-rs",
    "jersey": "jax-rs",
    "resteasy": "jax-rs",
}


def get_frameworks_dir() -> Path:
    """Get the path to the frameworks directory.

    Returns:
        Path to src/hypergumbo/frameworks/
    """
    return Path(__file__).parent / "frameworks"


def load_framework_patterns(framework_id: str) -> FrameworkPatternDef | None:
    """Load framework patterns from YAML file.

    Supports framework aliases - multiple detected framework names can map
    to a single pattern file (e.g., "chi" -> "go-web.yaml").

    Args:
        framework_id: Framework identifier (e.g., "fastapi", "chi")

    Returns:
        FrameworkPatternDef if found, None otherwise.
    """
    if framework_id in _PATTERN_CACHE:
        return _PATTERN_CACHE[framework_id]

    # Resolve alias if present (e.g., "chi" -> "go-web")
    resolved_id = _FRAMEWORK_ALIASES.get(framework_id, framework_id)

    yaml_path = get_frameworks_dir() / f"{resolved_id}.yaml"
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


def _combine_route_paths(prefix: str | None, suffix: str | None) -> str | None:
    """Combine a route prefix and suffix into a full path.

    Handles normalization:
    - Ensures single leading slash
    - Avoids double slashes at join point
    - Returns None if both are None/empty

    Examples:
        _combine_route_paths("/users", ":id") -> "/users/:id"
        _combine_route_paths("/users", "/:id") -> "/users/:id"
        _combine_route_paths("/users/", ":id") -> "/users/:id"
        _combine_route_paths("users", "profile") -> "/users/profile"
        _combine_route_paths(None, ":id") -> "/:id"
        _combine_route_paths("/users", None) -> "/users"
        _combine_route_paths(None, None) -> None

    Args:
        prefix: Route prefix from parent (e.g., from @Controller)
        suffix: Route suffix from method (e.g., from @Get)

    Returns:
        Combined path, or None if both inputs are None/empty.
    """
    # Normalize prefix: strip leading/trailing slashes
    if prefix:
        prefix = prefix.strip("/")
    else:
        prefix = ""  # pragma: no cover - prefix always has value when called

    # Normalize suffix: strip leading/trailing slashes
    if suffix:
        suffix = suffix.strip("/")
    else:
        suffix = ""  # pragma: no cover - suffix always has value when called

    # Combine with leading slash
    if prefix and suffix:
        return f"/{prefix}/{suffix}"
    elif prefix:  # pragma: no cover - prefix only, no suffix
        return f"/{prefix}"
    elif suffix:  # pragma: no cover - only suffix path
        return f"/{suffix}"
    else:  # pragma: no cover - both empty after strip
        return None


def _build_class_symbol_lookup(
    symbols: list["Symbol"],
) -> dict[tuple[str, str], "Symbol"]:
    """Build a lookup from (file_path, class_name) to class Symbol.

    Used for finding parent class symbols during enrichment.

    Args:
        symbols: All symbols from analysis

    Returns:
        Dict mapping (path, name) to class Symbol
    """
    lookup: dict[tuple[str, str], "Symbol"] = {}
    for sym in symbols:
        if sym.kind == "class":
            lookup[(sym.path, sym.name)] = sym
    return lookup


def _get_parent_class_name(symbol: "Symbol") -> str | None:
    """Extract parent class name from a method symbol.

    Parses the qualified name (e.g., "UserController.findOne" -> "UserController").

    Args:
        symbol: A method symbol

    Returns:
        Parent class name, or None if not a method or can't be determined.
    """
    if symbol.kind not in ("method", "function"):
        return None  # pragma: no cover - only called for method/function symbols

    # Try canonical_name first, fall back to name
    name = symbol.canonical_name or symbol.name
    if "." in name:
        return name.rsplit(".", 1)[0]
    return None


def _get_concept_path_from_symbol(symbol: "Symbol", concept_name: str) -> str | None:
    """Get the path from a specific concept on a symbol.

    Args:
        symbol: The symbol to check
        concept_name: The concept to look for (e.g., "controller")

    Returns:
        The path from the concept, or None if not found.
    """
    if not symbol.meta:  # pragma: no cover - defensive for symbols without meta
        return None

    concepts = symbol.meta.get("concepts", [])
    for concept in concepts:
        if concept.get("concept") == concept_name:
            return concept.get("path")
    return None  # pragma: no cover - concept not found on parent


def _apply_subresource_locator_paths(
    symbols: list["Symbol"],
    class_lookup: dict[tuple[str, str], "Symbol"],
) -> None:
    """Propagate accumulated path prefixes through JAX-RS subresource locator chains.

    Subresource locators are methods with a ``resource_path`` concept (from @Path)
    but NO ``route`` concept (no @GET/@POST).  Their return type references another
    resource class whose route methods should inherit the full accumulated path.

    Algorithm:
    1. Build a class-by-name lookup (cross-file).
    2. Find subresource locator methods (have resource_path, no route, have return_type).
    3. Accumulate the full path: parent class path + method path.
    4. Update the target class's route methods by prepending the accumulated path.

    Handles multi-level chains with cycle detection and depth limit.
    """
    # Build cross-file class name → Symbol lookup
    class_by_name: dict[str, "Symbol"] = {}
    for sym in symbols:
        if sym.kind == "class":
            class_by_name[sym.name] = sym

    # Build method → parent class name lookup
    methods_by_class: dict[str, list["Symbol"]] = {}
    for sym in symbols:
        if sym.kind in ("method", "function") and sym.meta:
            parent_name = _get_parent_class_name(sym)
            if parent_name:
                methods_by_class.setdefault(parent_name, []).append(sym)

    # Find subresource locator methods
    locators: list[tuple["Symbol", str, str]] = []  # (method, accumulated_path, return_type)
    for sym in symbols:
        if sym.kind not in ("method", "function") or not sym.meta:
            continue

        concepts = sym.meta.get("concepts", [])
        has_resource_path = False
        has_route = False
        path_value = None

        for c in concepts:
            if c.get("concept") == "resource_path":
                has_resource_path = True
                path_value = c.get("path", "")
            elif c.get("concept") == "route":
                has_route = True

        if has_resource_path and not has_route:
            return_type = sym.meta.get("return_type")
            # When return type is generic (Object), use inferred type from
            # "return new X(...)" in the method body.  Common in JAX-RS
            # subresource locators (keycloak: token() returns Object but
            # body constructs TokenEndpoint).
            if return_type == "Object":
                return_type = sym.meta.get("inferred_return_type", return_type)
            if return_type and return_type != "Object":
                # Get parent class's resource_path
                parent_name = _get_parent_class_name(sym)
                if parent_name:
                    parent_sym = class_lookup.get((sym.path, parent_name))
                    parent_path = None
                    if parent_sym:
                        parent_path = _get_concept_path_from_symbol(
                            parent_sym, "resource_path",
                        )
                    accumulated = _combine_route_paths(parent_path, path_value)
                    if accumulated:
                        locators.append((sym, accumulated, return_type))

    if not locators:
        return

    # Propagate accumulated paths to target class route methods
    # Handle multi-level chains (depth limit 10)
    # Only seed root-level locators (whose parent class is NOT a target of
    # another locator).  Chain iteration discovers mid-level locators with
    # the correct accumulated prefix from their upstream locator.
    target_classes = {ret for _, _, ret in locators}
    propagated_prefixes: dict[str, str] = {}  # class_name -> accumulated prefix
    for method, accumulated_path, return_type in locators:
        parent_name = _get_parent_class_name(method)
        if parent_name not in target_classes:
            propagated_prefixes[return_type] = accumulated_path

    # Iteratively resolve chained locators (A -> B -> C)
    for _depth in range(10):
        new_prefixes: dict[str, str] = {}
        for class_name, prefix in propagated_prefixes.items():
            class_methods = methods_by_class.get(class_name, [])
            for method in class_methods:
                if not method.meta:  # pragma: no cover - defensive
                    continue
                concepts = method.meta.get("concepts", [])
                for c in concepts:
                    if c.get("concept") == "resource_path" and not any(
                        cc.get("concept") == "route" for cc in concepts
                    ):
                        ret = method.meta.get("return_type")
                        if ret and ret not in propagated_prefixes:
                            method_path = c.get("path", "")
                            new_accumulated = _combine_route_paths(prefix, method_path)
                            if new_accumulated:
                                new_prefixes[ret] = new_accumulated
        if not new_prefixes:
            break
        propagated_prefixes.update(new_prefixes)

    # Now update route concepts on target class methods
    for class_name, prefix in propagated_prefixes.items():
        target_class = class_by_name.get(class_name)
        target_class_path = _get_concept_path_from_symbol(
            target_class, "resource_path",
        ) if target_class else None

        # Combine subresource prefix with target class's own resource_path
        if target_class_path is not None:
            full_class_prefix = _combine_route_paths(prefix, target_class_path)
        else:
            full_class_prefix = prefix

        class_methods = methods_by_class.get(class_name, [])
        for method in class_methods:
            if not method.meta:  # pragma: no cover - defensive
                continue
            concepts = method.meta.get("concepts", [])

            # Get the method's own @Path (resource_path concept), if any
            method_own_path = None
            for c in concepts:
                if c.get("concept") == "resource_path":
                    method_own_path = c.get("path")
                    break

            for c in concepts:
                if c.get("concept") == "route":
                    current_path = c.get("path")
                    if current_path:
                        # Phase 2 already set a path (parent class path + method path)
                        # Replace the target class prefix with the full subresource prefix
                        if target_class_path:
                            stripped = target_class_path.strip("/")
                            current_stripped = current_path.lstrip("/")
                            if current_stripped.startswith(stripped):
                                remainder = current_stripped[len(stripped):]
                                method_segment = remainder.lstrip("/")
                            else:  # pragma: no cover - defensive
                                method_segment = current_path
                        else:  # pragma: no cover - defensive
                            method_segment = current_path
                        # If stripping left nothing but method has its own
                        # @Path, Phase 3 only prepended parent path — we need
                        # to re-attach the method's own path segment.
                        if not method_segment and method_own_path is not None:  # pragma: no cover - Phase 2 now includes method @Path
                            method_segment = method_own_path
                        c["path"] = _combine_route_paths(
                            full_class_prefix, method_segment,
                        )
                    elif method_own_path is not None:  # pragma: no cover - Phase 2 now sets path from method @Path
                        # Route has no path but method has @Path annotation
                        c["path"] = _combine_route_paths(
                            full_class_prefix, method_own_path,
                        )
                    else:
                        # No method path at all; use just the class prefix
                        c["path"] = full_class_prefix


def enrich_symbols(
    symbols: list[Symbol],
    detected_frameworks: set[str],
    usage_contexts: list[UsageContext] | None = None,
) -> list[Symbol]:
    """Enrich symbols with framework concept metadata.

    Four-phase enrichment (v1.3.x):
    1. Language conventions: Match main() functions and other language-level patterns
    2. Definition-based: Match against decorators, base classes, annotations
    3. Parent path inheritance: Combine paths from parent concepts (prefix_from_parent)
    4. Usage-based: Match against UsageContext records for call-based frameworks

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
    # - naming-conventions.yaml: Controller, Handler, Service naming heuristics (0.70)
    # - library-exports.yaml: JS/TS index exports, Go exported symbols, Elixir modules
    # - logging-conventions.yaml: Logger classes, factory methods, log bridges
    for convention_id in (
        "main-functions",
        "test-frameworks",
        "language-conventions",
        "config-conventions",
        "naming-conventions",
        "library-exports",
        "logging-conventions",
    ):
        convention_patterns = load_framework_patterns(convention_id)
        if convention_patterns:
            pattern_defs.append(convention_patterns)

    if not pattern_defs:  # pragma: no cover - main-functions.yaml is always loaded
        return symbols

    # Build lookups for parent resolution and name-based fallback
    symbol_by_id: dict[str, Symbol] = {s.id: s for s in symbols}
    # Build name-based lookup for fallback resolution (INV-002 fix)
    # Uses simple name as key; for qualified names, use the last component
    symbol_by_name: dict[str, Symbol] = {}
    for s in symbols:
        if s.name:
            # Use simple name (last component after dots)
            simple_name = s.name.rsplit(".", 1)[-1]
            # Prefer callable symbols (functions/methods) over classes/variables
            if simple_name not in symbol_by_name or s.kind in ("function", "method"):
                symbol_by_name[simple_name] = s
    class_lookup = _build_class_symbol_lookup(symbols)

    # Collect patterns that use prefix_from_parent (for phase 3)
    patterns_with_prefix: list[tuple[Pattern, str]] = []  # (pattern, framework_id)
    for pattern_def in pattern_defs:
        for pattern in pattern_def.patterns:
            if pattern.prefix_from_parent:
                patterns_with_prefix.append((pattern, pattern_def.id))

    # Phase 1: Definition-based matching (decorators, base classes, annotations)
    for symbol in symbols:
        matches = match_patterns(symbol, pattern_defs)
        if matches:
            # Add matched concepts to symbol metadata
            if symbol.meta is None:  # pragma: no cover - patterns require meta to match
                symbol.meta = {}
            symbol.meta["concepts"] = matches

    # Phase 2: Parent path inheritance (v1.3.x prefix_from_parent)
    # After all symbols have their concepts, resolve parent prefixes
    if patterns_with_prefix:
        for symbol in symbols:
            if not symbol.meta or "concepts" not in symbol.meta:  # pragma: no cover
                continue

            for concept in symbol.meta["concepts"]:
                # Find the pattern that produced this concept
                matched_pattern = None
                for pattern, fw_id in patterns_with_prefix:
                    if (
                        pattern.concept == concept.get("concept")
                        and fw_id == concept.get("framework")
                    ):
                        matched_pattern = pattern
                        break

                if not matched_pattern or not matched_pattern.prefix_from_parent:
                    continue

                # Find parent class symbol
                parent_class_name = _get_parent_class_name(symbol)
                if not parent_class_name:
                    continue

                parent_symbol = class_lookup.get((symbol.path, parent_class_name))
                if not parent_symbol:
                    continue  # pragma: no cover - parent class not found in file

                # Get the path from the parent's matching concept
                parent_path = _get_concept_path_from_symbol(
                    parent_symbol, matched_pattern.prefix_from_parent
                )

                # Always combine paths when we have a parent - this ensures
                # leading slash normalization even when parent path is empty.
                # Examples:
                #   @Controller('users') + @Get(':id') -> /users/:id
                #   @Controller() + @Get('test') -> /test
                #   @Controller('users') + @Get() -> /users
                method_path = concept.get("path")
                # JAX-RS: route concept from @GET has no path, but the method
                # may have its own @Path("/{id}") producing a resource_path
                # concept.  Combine: class @Path + method @Path + @GET.
                if not method_path and matched_pattern.prefix_from_parent:
                    own_resource_path = _get_concept_path_from_symbol(
                        symbol, matched_pattern.prefix_from_parent
                    )
                    if own_resource_path:
                        method_path = own_resource_path
                # Only skip if parent_path is None (no concept) vs "" (empty concept)
                if parent_path is not None:
                    combined_path = _combine_route_paths(parent_path, method_path)
                    if combined_path:
                        concept["path"] = combined_path
                elif method_path:
                    # No parent class path, but method has its own @Path
                    concept["path"] = _combine_route_paths(None, method_path)

    # Phase 2b: JAX-RS subresource locator path chaining
    # Methods with @Path (resource_path concept) and a return_type that matches
    # a class with resource_path or route methods are subresource locators.
    # Propagate the accumulated path prefix to the target class's route methods.
    _apply_subresource_locator_paths(symbols, class_lookup)

    # Phase 3: Usage-based matching (v1.1.x)
    if usage_contexts:
        for ctx in usage_contexts:
            symbol: Symbol | None = None

            # Try direct symbol_ref lookup first
            if ctx.symbol_ref:
                symbol = symbol_by_id.get(ctx.symbol_ref)

            # Fallback: try name-based resolution from metadata (INV-002 fix)
            # This handles cases where view_name exists but symbol_ref wasn't set
            # because the symbol was in a different file during analysis
            if symbol is None and ctx.metadata:
                view_name = ctx.metadata.get("view_name")
                if view_name:
                    # Try simple name lookup
                    simple_name = view_name.rsplit(".", 1)[-1]
                    symbol = symbol_by_name.get(simple_name)

            if not symbol:
                continue

            # Match against usage patterns
            matches = match_usage_patterns(ctx, pattern_defs)
            if matches:
                if symbol.meta is None:
                    symbol.meta = {}

                # Append to existing concepts, deduplicating.
                # Both definition-based (Phase 1) and usage-based (Phase 3)
                # can produce the same concept (e.g., Go route handlers
                # matched by both decorator and UsageContext patterns).
                existing = symbol.meta.get("concepts", [])
                existing_keys = {
                    tuple(sorted(c.items())) for c in existing
                }
                for m in matches:
                    if tuple(sorted(m.items())) not in existing_keys:
                        existing.append(m)
                        existing_keys.add(tuple(sorted(m.items())))
                symbol.meta["concepts"] = existing

    return symbols


@dataclass
class DeferredResolutionStats:
    """Statistics from deferred symbol resolution phase.

    Tracks how many UsageContexts were resolved and by which strategy,
    enabling observability into the resolution process.
    """

    total_unresolved: int = 0
    resolved_exact: int = 0
    resolved_suffix: int = 0
    resolved_path_hint: int = 0
    resolved_ambiguous: int = 0
    still_unresolved: int = 0

    @property
    def total_resolved(self) -> int:
        """Total number of successfully resolved UsageContexts."""
        return (
            self.resolved_exact
            + self.resolved_suffix
            + self.resolved_path_hint
            + self.resolved_ambiguous
        )


def resolve_deferred_symbol_refs(
    symbols: list["Symbol"],
    usage_contexts: list[UsageContext],
) -> DeferredResolutionStats:
    """Resolve symbol_ref for UsageContexts that couldn't be resolved during analysis.

    This is the proper fix for INV-002: Usage patterns extracted by analyzers should
    become concepts on nodes, but this requires resolving string-based handler
    references to actual Symbol IDs.

    The problem: When an analyzer extracts a UsageContext (e.g., Django URL pattern
    referencing 'views.list_users'), the target symbol may be in a different file
    that hasn't been analyzed yet, so symbol_ref is None.

    The solution: After ALL analyzers have run, we have a complete symbol table.
    This function uses that table to resolve deferred references using multiple
    strategies:

    1. Exact match: view_name matches a symbol's name exactly
    2. Suffix match: view_name "list_users" matches "myapp.views.list_users"
    3. Path hint: When ambiguous, prefer symbols in paths matching module hints

    Args:
        symbols: All symbols from all analyzers (complete symbol table)
        usage_contexts: All UsageContexts, some with symbol_ref=None

    Returns:
        DeferredResolutionStats with resolution metrics

    Side Effects:
        Mutates UsageContext objects: sets symbol_ref when resolved
    """
    from .symbol_resolution import NameResolver

    stats = DeferredResolutionStats()

    # Build lookup indices
    symbol_by_id: dict[str, "Symbol"] = {s.id: s for s in symbols}
    symbol_by_name: dict[str, "Symbol"] = {}

    for s in symbols:
        # Index by simple name
        symbol_by_name[s.name] = s
        # Also index by qualified name if available (e.g., "Class.method")
        if s.meta and s.meta.get("qualified_name"):
            symbol_by_name[s.meta["qualified_name"]] = s

    # Create NameResolver for suffix matching
    resolver = NameResolver(symbol_by_name)

    # Process UsageContexts with unresolved symbol_ref
    for ctx in usage_contexts:
        # Skip if already resolved
        if ctx.symbol_ref and ctx.symbol_ref in symbol_by_id:
            continue

        # This one needs resolution
        stats.total_unresolved += 1

        # Extract resolution hints from metadata
        # Different analyzers use different keys, so we check multiple
        resolution_hints = _extract_resolution_hints(ctx)

        if not resolution_hints["name"]:
            stats.still_unresolved += 1
            continue

        # Build path hint from module info if available
        path_hint = resolution_hints.get("module_path")

        # Attempt resolution
        result = resolver.lookup(
            resolution_hints["name"],
            allow_suffix=True,
            path_hint=path_hint,
        )

        if result.found:
            # Update the UsageContext with resolved symbol_ref
            ctx.symbol_ref = result.symbol.id

            # Track which strategy worked
            match_type = result.match_type or "exact"
            if match_type == "exact" or result.confidence >= 1.0:
                stats.resolved_exact += 1
            elif match_type == "path_hint":
                stats.resolved_path_hint += 1
            elif match_type == "suffix_ambiguous":
                stats.resolved_ambiguous += 1
            else:
                stats.resolved_suffix += 1
        else:
            stats.still_unresolved += 1

    return stats


def _extract_resolution_hints(ctx: UsageContext) -> dict[str, str | None]:
    """Extract resolution hints from UsageContext metadata.

    Different analyzers store handler names in different metadata keys.
    This function normalizes the extraction.

    Returns:
        Dict with 'name' (required for resolution) and optional hints:
        - name: The symbol name to resolve (e.g., "list_users")
        - module_path: Path hint for disambiguation (e.g., "views")
        - qualified_name: Full qualified name if available
    """
    hints: dict[str, str | None] = {
        "name": None,
        "module_path": None,
        "qualified_name": None,
    }

    if not ctx.metadata:
        return hints

    # Priority order for name extraction:
    # 1. view_name (Django, Rails)
    # 2. handler (Express, Fastify)
    # 3. handler_name (generic)
    # 4. callback (event-based)
    # 5. function_name (generic)
    name_keys = ["view_name", "handler", "handler_name", "callback", "function_name"]
    for key in name_keys:
        if ctx.metadata.get(key):
            raw_name = ctx.metadata[key]
            # Handle dotted names like "views.list_users"
            if "." in raw_name:
                parts = raw_name.rsplit(".", 1)
                hints["module_path"] = parts[0]
                hints["name"] = parts[1]
                hints["qualified_name"] = raw_name
            else:
                hints["name"] = raw_name
            break

    # Additional path hint from module_name if name doesn't have dots
    if hints["name"] and not hints["module_path"]:
        module_hint = ctx.metadata.get("module_name") or ctx.metadata.get("module")
        if module_hint:
            hints["module_path"] = module_hint

    return hints


def materialize_route_symbols(symbols: list[Symbol]) -> list[Symbol]:
    """Create kind='route' symbols for enriched route handler methods.

    After ``enrich_symbols()`` tags handler methods with ``concept: route``,
    this function creates corresponding route IR nodes that the
    ``route_handler`` linker can use to produce ``routes_to`` edges.

    This is needed for annotation-based frameworks (JAX-RS, Spring MVC,
    ASP.NET) where route info is distributed across class/method annotations
    rather than centralized in a routing DSL.  Language analyzers for Go
    and JS/TS already create route symbols directly; this function fills the
    gap for frameworks that rely on YAML pattern enrichment.

    Args:
        symbols: All symbols (already enriched by ``enrich_symbols``).

    Returns:
        List of new route Symbol objects to extend the symbol list.
        Does NOT modify the input list.
    """
    from .analyze.base import make_route_stable_id
    from .ir import Span, Symbol as SymbolCls, make_pass_id

    pass_id = make_pass_id("route-materializer")
    new_route_symbols: list[SymbolCls] = []
    seen_routes: set[str] = set()  # Dedupe by (method, path)

    for sym in symbols:
        if not sym.meta:
            continue
        concepts = sym.meta.get("concepts", [])
        if not concepts:
            continue

        for concept in concepts:
            if not isinstance(concept, dict):  # pragma: no cover
                continue
            if concept.get("concept") != "route":
                continue

            # Already has a kind="route" symbol (e.g., Go/JS analyzers created one)
            if sym.kind == "route":
                continue

            method = (concept.get("method") or "").upper()
            path = concept.get("path") or ""

            # Stapler convention: doXxx → POST /xxx, getXxx → GET /xxx
            # When the YAML pattern matches method_name but doesn't set
            # extract_method, we can derive method + path from the name.
            if not method and sym.name:
                short_name = sym.name.split(".")[-1]
                if short_name.startswith("do") and len(short_name) > 2 and short_name[2].isupper():
                    method = "POST"
                    path = "/" + short_name[2].lower() + short_name[3:]
                elif short_name.startswith("get") and len(short_name) > 3 and short_name[3].isupper():
                    method = "GET"
                    path = "/" + short_name[3].lower() + short_name[4:]

            # Need at least a method to create a meaningful route node
            if not method:
                continue

            # Dedupe: same (method, path) pair already materialized
            route_key = f"{method}:{path}"
            if route_key in seen_routes:
                continue
            seen_routes.add(route_key)

            # Build route name
            if path:
                route_name = f"{method} {path}"
            else:
                route_name = f"{method} route"

            stable_id = make_route_stable_id(method, path) if path else None

            # Create route symbol at the same location as the handler
            route_id = (
                f"{sym.language}:{sym.path}:{sym.span.start_line}-"
                f"{sym.span.end_line}:{route_name}:route"
            )
            route_sym = SymbolCls(
                id=route_id,
                name=route_name,
                kind="route",
                language=sym.language,
                path=sym.path,
                span=Span(
                    start_line=sym.span.start_line,
                    end_line=sym.span.end_line,
                    start_col=sym.span.start_col,
                    end_col=sym.span.end_col,
                ),
                origin=pass_id,
                stable_id=stable_id,
                meta={
                    "route_path": path,
                    "http_method": method,
                    "handler_ref": sym.name,
                    "materialized_from": sym.id,
                },
            )
            new_route_symbols.append(route_sym)

    return new_route_symbols


def clear_pattern_cache() -> None:
    """Clear the pattern cache. For testing only."""
    _PATTERN_CACHE.clear()
