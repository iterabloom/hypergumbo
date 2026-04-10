# SPDX-License-Identifier: AGPL-3.0-or-later
"""Base classes and utilities for language analyzers.

This module provides shared infrastructure for all language analyzers,
eliminating duplication across 65+ analyzer files.

Shared Components
-----------------
- **AnalysisResult**: Universal result type returned by all analyzers
- **FileAnalysis**: Intermediate per-file analysis result
- **Tree-sitter helpers**: node_text, find_child_by_type, find_child_by_field
- **ID generation**: make_symbol_id, make_file_id
- **Availability checking**: is_grammar_available

Why This Design
---------------
Previously, each analyzer duplicated these components. This led to:
- 65+ copies of identical dataclasses
- Inconsistent helper implementations
- High maintenance burden when adding new analyzers

Now, analyzers import from this module and focus only on
language-specific parsing logic.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re as _re
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, ClassVar, Iterator, Optional

from ..dataflow import annotate_dataflow, get_dataflow_config
from ..discovery import find_files
from ..ir import PASS_VERSION, AnalysisRun, Edge, Span, Symbol, UsageContext, make_pass_id
from ..symbol_resolution import NameResolver

# ---------------------------------------------------------------------------
# Memory safety: abort analysis before OOM crashes the machine
# ---------------------------------------------------------------------------

# Minimum available system memory (MB) before aborting an in-progress analysis.
# When available memory drops below this threshold between files, the analysis
# raises MemoryPressureError instead of continuing and risking OOM/swap thrash.
# The threshold is low (512 MB) because it's a last-resort safety net — the
# bakeoff scripts and smart-test have their own higher thresholds.
_MIN_AVAILABLE_MB = int(os.environ.get("HYPERGUMBO_MIN_MEMORY_MB", "512"))


class MemoryPressureError(MemoryError):
    """Raised when available system memory is critically low during analysis.

    This is a graceful abort — the analysis stops between files (not mid-parse)
    and returns partial results or raises to the caller.  Prevents the OS from
    thrashing swap or killing the process via OOM killer.
    """


def _check_memory_pressure() -> None:
    """Raise MemoryPressureError if available memory is critically low.

    Reads MemAvailable from /proc/meminfo (Linux).  No-op on non-Linux.
    Called between files during analysis to catch memory exhaustion early.
    """
    if _MIN_AVAILABLE_MB <= 0:
        return  # Disabled via env var
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    available_mb = int(line.split()[1]) // 1024
                    if available_mb < _MIN_AVAILABLE_MB:
                        raise MemoryPressureError(
                            f"Available memory ({available_mb} MB) below "
                            f"threshold ({_MIN_AVAILABLE_MB} MB). Aborting "
                            f"analysis to prevent OOM. Set "
                            f"HYPERGUMBO_MIN_MEMORY_MB=0 to disable."
                        )
                    return
    except (OSError, ValueError):
        pass  # Non-Linux or /proc not available — skip check


if TYPE_CHECKING:
    import tree_sitter


@dataclass
class AnalysisResult:
    """Universal result type for all language analyzers.

    This replaces the per-language XxxAnalysisResult dataclasses
    (GoAnalysisResult, RustAnalysisResult, etc.) which were all identical.

    Attributes:
        symbols: List of detected symbols (functions, classes, etc.)
        edges: List of relationships between symbols (calls, imports, etc.)
        usage_contexts: List of usage contexts for call-based pattern matching (v1.1.x)
        run: Provenance tracking for the analysis pass
        skipped: Whether the analysis was skipped (e.g., missing dependency)
        skip_reason: Human-readable reason for skipping
    """

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    usage_contexts: list[UsageContext] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""
    dependency_manifest: object | None = None
    """Optional DependencyManifest from supply_chain.py.

    Language analyzers that parse dependency manifests (go.mod, package.json,
    Cargo.toml) can populate this field. Manifests are merged after all
    analyzers run and passed to ``create_boundary_nodes`` for tier
    classification of external references.
    """


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single source file.

    Used during two-pass analysis: first pass collects symbols,
    second pass resolves cross-file references using the symbol registry.

    Attributes:
        symbols: Symbols detected in this file
        symbol_by_name: Quick lookup by symbol name for edge resolution
        import_aliases: Mapping of import alias → import path (Go, etc.)
        node_for_symbol: Mapping of symbol ID → tree-sitter node for
            automatic shape_id computation (ADR-0014 §1). Analyzers that
            populate this get shape_id computed by the base class.
    """

    symbols: list[Symbol] = field(default_factory=list)
    symbol_by_name: dict[str, Symbol] = field(default_factory=dict)
    import_aliases: dict[str, str] = field(default_factory=dict)
    node_for_symbol: dict[str, "tree_sitter.Node"] = field(default_factory=dict)
    class_field_types: dict[str, dict[str, str]] = field(default_factory=dict)
    """Maps class/struct name → {field_name → type_name}.

    Populated by subclasses in ``extract_symbols_from_file()``.
    Aggregated by ``analyze()`` into ``_field_type_registry`` for Pass 2.
    Name-based (not Symbol-based) because the target type's Symbol
    may not exist yet during Pass 1 (it might be in another file).
    """

    interface_method_sets: dict[str, set[tuple[str, int, int]]] = field(
        default_factory=dict,
    )
    """Maps interface name → set of (method_name, param_count, return_count).

    Used for cross-file structural interface matching: a struct satisfies
    an interface if its method set (including arities) is a superset of
    the interface's.  Arity matching prevents false positives from
    coincidental method name collisions (e.g., Close() vs Close(ctx)).
    """

    struct_method_sets: dict[str, set[tuple[str, int, int]]] = field(
        default_factory=dict,
    )
    """Maps struct name → set of (method_name, param_count, return_count).

    Used alongside ``interface_method_sets`` for cross-file structural
    interface matching in Go.
    """

    method_return_types: dict[str, str] = field(default_factory=dict)
    """Maps qualified method name → return type name.

    INV-dihos / WI-kuroj return-type registry: when a call site does
    ``x := obj.Method()`` and ``obj``'s type is known, the return type
    of ``{ObjType}.{MethodName}`` can be looked up here and assigned
    to ``x`` in var_types, enabling chained receiver-type resolution.

    Populated during Pass 1 (symbol extraction) by language analyzers
    that parse method/function signatures.  Aggregated across files
    by ``analyze()`` into ``_method_return_type_registry`` for Pass 2.

    Key format is language-specific:
    - Go: ``ReceiverType.MethodName`` (e.g. ``Engine.Query``)
    - Java: ``ClassName.methodName`` (e.g. ``Engine.query``)
    - Functions without receivers: ``FuncName`` (e.g. ``NewEngine``)
    """


@dataclass(frozen=True)
class ArityFlags:
    """Parameter arity classification for stable_id computation (ADR-0014 §2).

    Captures the structural shape of a function's parameter list without
    recording names or types.  Two functions with the same ArityFlags and
    kind will produce the same untyped-tier stable_id (by design — they
    have the same *interface shape*).

    Attributes:
        param_count: Number of regular parameters (excludes self/cls/receiver).
        has_defaults: Whether any parameter has a default value.
        has_varargs: Whether variadic positional args exist (``*args``, ``...``).
        has_kwargs: Whether variadic keyword args exist (``**kwargs``).
    """

    param_count: int
    has_defaults: bool
    has_varargs: bool
    has_kwargs: bool

    def as_flags_str(self) -> str:
        """Return the canonical string form used in stable_id hashing."""
        return f"{self.has_defaults},{self.has_varargs},{self.has_kwargs}"


# ---------------------------------------------------------------------------
# Tree-sitter helper functions
# ---------------------------------------------------------------------------


def node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text content for a tree-sitter node.

    Args:
        node: A tree-sitter node
        source: Source file bytes

    Returns:
        The text content of the node, decoded as UTF-8.
    """
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def find_child_by_type(
    node: "tree_sitter.Node", type_name: str
) -> Optional["tree_sitter.Node"]:
    """Find the first child node of a given type.

    Args:
        node: Parent tree-sitter node
        type_name: The node type to search for

    Returns:
        The first matching child, or None if not found.
    """
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def find_child_by_field(
    node: "tree_sitter.Node", field_name: str
) -> Optional["tree_sitter.Node"]:
    """Find a child node by field name.

    Args:
        node: Parent tree-sitter node
        field_name: The field name to look up

    Returns:
        The child at that field, or None if not found.
    """
    return node.child_by_field_name(field_name)


# ---------------------------------------------------------------------------
# ID generation helpers
# ---------------------------------------------------------------------------


def make_symbol_id(
    lang: str, path: str, start_line: int, end_line: int, name: str, kind: str
) -> str:
    """Generate a location-based symbol ID.

    Format: {lang}:{path}:{start}-{end}:{name}:{kind}

    Args:
        lang: Language identifier (e.g., "go", "rust", "python")
        path: File path
        start_line: Starting line number
        end_line: Ending line number
        name: Symbol name
        kind: Symbol kind (function, class, etc.)

    Returns:
        A unique, location-based symbol ID.
    """
    return f"{lang}:{path}:{start_line}-{end_line}:{name}:{kind}"


def make_file_id(lang: str, path: str) -> str:
    """Generate an ID for a file node (used as import edge source).

    Args:
        lang: Language identifier
        path: File path

    Returns:
        A file-level symbol ID.
    """
    return f"{lang}:{path}:1-1:file:file"


def make_unresolved_edge(
    lang: str,
    src_id: str,
    callee_name: str,
    line: int,
    pass_id: str,
    run_id: str,
    *,
    module_hint: str = "external",
) -> Edge:
    """Create an unresolved-external call edge for a callee not in the project.

    All language analyzers use this when a function/method call cannot be
    resolved to a project symbol.  The resulting edge has confidence 0.50
    and a standardized dst ID format:  {lang}:{module_hint}:0-0:{name}:unresolved

    Args:
        lang: Language identifier (e.g., "c", "java", "rust")
        src_id: Symbol ID of the caller
        callee_name: Name of the called function/method
        line: Source line number of the call
        pass_id: Analyzer pass ID
        run_id: Execution run ID
        module_hint: Module/package context when known (default "external")
    """
    dst_id = f"{lang}:{module_hint}:0-0:{callee_name}:unresolved"
    return Edge.create(
        src=src_id,
        dst=dst_id,
        edge_type="calls",
        line=line,
        confidence=0.50,
        origin=pass_id,
        origin_run_id=run_id,
        evidence_type="unresolved_external_call",
    )


def make_route_stable_id(method: str, path: str) -> str:
    """Compute a collision-free stable_id for route symbols.

    Uses sha256("route:{method}:{path}") per ADR-0014 §4.  The previous
    approach set stable_id to bare HTTP methods (e.g. "GET"), causing every
    same-method route to collide.

    Args:
        method: HTTP method (e.g. "GET", "POST", "ANY"). Case-insensitive —
            the value is upper-cased internally for consistency.
        path: Route path (e.g. "/users", "/posts/:id").

    Returns:
        A hex-digest string that uniquely identifies the (method, path) pair.
    """
    # Normalize empty paths to "/" — empty strings from annotation extraction
    # (e.g., @GetMapping("") or sub-resource locators without @Path) should
    # hash identically to the root path (INV-nimik).
    normalized = path if path else "/"
    key = f"route:{method.upper()}:{normalized}"
    return hashlib.sha256(key.encode()).hexdigest()


def make_entry_stable_id(entry_type: str, name: str) -> str:
    """Compute a collision-free stable_id for entry-point symbols.

    Uses sha256("entry:{entry_type}:{name}") per ADR-0014 §4.  Used for
    symbols like WGSL shader stages (@vertex, @fragment, @compute) where the
    previous approach set stable_id to the bare entry type string, causing
    same-type entry points to collide.

    Args:
        entry_type: Entry point category (e.g. "vertex", "fragment", "compute").
        name: Symbol name (e.g. the function name).

    Returns:
        A hex-digest string that uniquely identifies the (entry_type, name) pair.
    """
    key = f"entry:{entry_type}:{name}"
    return hashlib.sha256(key.encode()).hexdigest()


def make_typed_stable_id(
    kind: str,
    normalized_signature: str,
    visibility: str = "",
    containing_stable_id: str = "",
    decorators: str = "",
) -> str:
    """Compute a typed-tier stable_id from a normalized signature.

    Uses the formula from ADR-0014 §3::

        sha256({kind}:{normalized_signature}:{visibility}:{decorators}:{containing_stable_id})

    The typed tier is preferred when type information is available (e.g. Java,
    C#, Kotlin, TypeScript, Dart, Go, Python with annotations).  It produces
    higher-quality identity than the untyped tier because it captures the full
    interface shape including parameter and return types.

    Decorators/annotations are included because they change runtime behavior
    (e.g. ``@staticmethod``, ``@lru_cache``, ``@Override``).  Two functions
    with identical signatures but different decorators are semantically
    distinct and must receive different stable_ids.

    Args:
        kind: Symbol kind (``"function"``, ``"method"``, ``"class"``).
        normalized_signature: Output of a ``normalize_*_signature()`` function
            (e.g. ``"(String,int)User"``).  Must already be normalized.
        visibility: Access modifier (``"public"``, ``"private"``, ``"protected"``).
            Empty string for languages without access modifiers (Python, Go).
        containing_stable_id: Stable ID of the enclosing scope (class or
            module).  Empty string for top-level definitions.
        decorators: Sorted, comma-joined decorator/annotation names (e.g.
            ``"Override,Test"``).  Empty string when no decorators are present.

    Returns:
        Stable ID in ``sha256:{16-hex-chars}`` format.
    """
    sig = (
        f"{kind}:{normalized_signature}:{visibility}"
        f":{decorators}:{containing_stable_id}"
    )
    hash_val = hashlib.sha256(sig.encode()).hexdigest()[:16]
    return f"sha256:{hash_val}"


_VISIBILITY_MODIFIERS = frozenset({"public", "private", "protected", "internal"})


def visibility_from_modifiers(modifiers: list[str] | None) -> str:
    """Extract the visibility modifier from a list of modifiers.

    Returns the first visibility keyword found (``"public"``, ``"private"``,
    ``"protected"``, ``"internal"``), or an empty string if none is present.
    Languages without visibility modifiers (Python, Go) will have empty
    modifier lists or lists without visibility keywords.
    """
    if not modifiers:
        return ""
    for m in modifiers:
        if m in _VISIBILITY_MODIFIERS:
            return m
    return ""


# ---------------------------------------------------------------------------
# Doc comment extraction
# ---------------------------------------------------------------------------

_DOC_COMMENT_TYPES = frozenset({
    "comment",
    "block_comment",
    "line_comment",
    "multiline_comment",
})

# Prefixes to strip from comment text, in order of specificity.
_COMMENT_STRIP_RE = _re.compile(
    r"^\s*(?:"
    r"/\*\*\s*"   # /** (Javadoc/JSDoc/KDoc/PHPDoc opener)
    r"|\*/\s*"    # */ (block closer)
    r"|\*\s?"     # * (block continuation line)
    r"|///\s?"    # /// (Rust/C#/Swift)
    r"|//!\s?"    # //! (Rust inner doc)
    r"|//\s?"     # // (Go)
    r"|%+\s?"     # % or %% (Erlang)
    r"|#\s?"      # # (Ruby/Python/Elixir)
    r")"
)

# Tag lines to skip (Javadoc @param, @returns, etc.)
_DOC_TAG_RE = _re.compile(r"^\s*@\w+")


def extract_doc_comment(
    node: "tree_sitter.Node",
    source: bytes,
    max_len: int = 80,
) -> str | None:
    """Extract first-line summary of the doc comment preceding a declaration node.

    Walks backwards through prev_named_sibling collecting consecutive comment
    nodes with no blank-line gap.  Cleans comment delimiters and returns the
    first non-empty content line, truncated to *max_len*.

    Works across languages: handles ``/** */`` (Java/Kotlin/JS/PHP),
    ``///`` (Rust/C#/Swift), ``//`` (Go), and ``#`` (Ruby) comment styles.
    """
    # Collect comment nodes walking backwards from the declaration
    comment_nodes: list["tree_sitter.Node"] = []
    prev = getattr(node, "prev_named_sibling", None)
    while prev is not None and prev.type in _DOC_COMMENT_TYPES:
        # Stop on blank-line gap (more than 1 line between consecutive nodes)
        if comment_nodes:
            last_collected = comment_nodes[-1]
            if last_collected.start_point[0] - prev.end_point[0] > 1:
                break
        comment_nodes.append(prev)
        prev = prev.prev_named_sibling

    if not comment_nodes:
        return None

    # Reverse so we process top-to-bottom
    comment_nodes.reverse()

    # Decode and clean comment text
    for cnode in comment_nodes:
        raw = source[cnode.start_byte:cnode.end_byte].decode("utf-8", errors="replace")
        for raw_line in raw.split("\n"):
            line = _COMMENT_STRIP_RE.sub("", raw_line)
            # Strip trailing block-comment closer
            if line.rstrip().endswith("*/"):
                line = line.rstrip()[:-2]
            line = line.strip()
            # Skip empty lines, closing delimiters, and tag lines
            if not line or line == "/" or _DOC_TAG_RE.match(line):
                continue
            # Found the first content line — truncate and return
            if len(line) > max_len:
                return line[: max_len - 1] + "\u2026"
            return line

    return None


def populate_docstrings_from_tree(
    root_node: "tree_sitter.Node",
    source: bytes,
    symbols: list[Symbol],
) -> None:
    """Populate docstrings for symbols by finding their tree-sitter nodes via position.

    For each symbol that lacks a docstring, uses the symbol's span to locate
    the corresponding tree-sitter node via ``named_descendant_for_point_range``,
    then extracts the doc comment preceding that node.

    This enables docstring extraction for analyzers that don't populate
    ``node_for_symbol`` — the span's start position is enough to reverse-lookup
    the declaration node, since all analyzers set ``start_col`` from
    ``node.start_point[1]``.

    When the lookup returns a container node (e.g. a class body) rather than
    the declaration itself, drills down through named children that start at
    the target position until the actual declaration node is found.  This
    handles languages like Scala, Groovy, Ruby, and Elixir where the tree
    structure nests declarations inside body nodes.
    """
    for sym in symbols:
        if sym.docstring is not None or sym.span is None:
            continue
        start = (sym.span.start_line - 1, sym.span.start_col)
        node = root_node.named_descendant_for_point_range(start, start)
        if node is None:
            continue
        # Drill down: when the lookup returns a container node (e.g.
        # template_body in Scala) instead of the declaration itself,
        # find the named child whose start point matches the target.
        while True:
            refined = None
            for child in node.named_children:
                if child.start_point == start:
                    refined = child
                    break
            if refined is None:
                break
            node = refined
        # Walk up: when drill-down lands on a leaf (e.g. primitive_type
        # in C/C++), walk up through ancestors on the same line to find
        # the declaration node whose prev_named_sibling is a comment.
        candidate = node
        while candidate is not None:
            doc = extract_doc_comment(candidate, source)
            if doc is not None:
                sym.docstring = doc
                break
            parent = candidate.parent
            if parent is None or parent.start_point[0] != start[0]:
                break
            candidate = parent


# ---------------------------------------------------------------------------
# Signature normalization utilities (ADR-0014 §3)
# ---------------------------------------------------------------------------


def strip_fqn_prefix(type_name: str) -> str:
    """Strip fully-qualified name prefix, keeping just the simple name.

    Handles dotted paths like ``java.lang.String`` → ``String``
    and ``System.Collections.Generic.List`` → ``List``.
    Preserves generic parameters: ``java.util.List<String>`` → ``List<String>``.

    Does NOT strip if the name contains no dots (already simple).
    """
    # Split at first '<' to preserve generic params
    if "<" in type_name:
        base, rest = type_name.split("<", 1)
        return strip_fqn_prefix(base) + "<" + rest
    if "[" in type_name:
        # Go/Scala square-bracket generics
        base, rest = type_name.split("[", 1)
        return strip_fqn_prefix(base) + "[" + rest
    if "." in type_name:
        return type_name.rsplit(".", 1)[-1]
    return type_name


# Regex to match type parameter names in generic brackets.
# Matches single uppercase letter or conventional names like T1, TKey, etc.
_TYPE_PARAM_RE = _re.compile(r"\b([A-Z][A-Za-z0-9]*)\b")


def normalize_generic_params(
    text: str,
    type_params: list[str],
) -> str:
    """Replace declared type parameter names with positional markers.

    ``T, U`` → ``$0, $1`` within the given text.  Only replaces
    names that exactly match a declared type parameter — concrete
    type names like ``String`` or ``Integer`` are untouched.

    Args:
        text: The signature text to transform.
        type_params: Ordered list of declared type parameter names
            (e.g., ``["T", "U", "V"]``).

    Returns:
        Transformed text with type params replaced by positional markers.
    """
    if not type_params:
        return text
    mapping = {tp: f"${i}" for i, tp in enumerate(type_params)}

    def _replace(m: _re.Match) -> str:
        name = m.group(1)
        return mapping.get(name, name)

    return _TYPE_PARAM_RE.sub(_replace, text)


def split_params_top_level(params_str: str) -> list[str]:
    """Split a parameter list by commas, respecting generic nesting.

    ``"Map<String, Integer>, int"`` → ``["Map<String, Integer>", "int"]``

    Handles ``<>``, ``[]``, and ``()`` nesting.  Leading/trailing
    whitespace on each part is stripped.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in params_str:
        if ch in ("<", "[", "("):
            depth += 1
            current.append(ch)
        elif ch in (">", "]", ")"):
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _normalize_type(raw_type: str, type_params: list[str] | None) -> str:
    """Apply FQN stripping and generic-param normalization to a single type."""
    t = strip_fqn_prefix(raw_type.strip())
    if type_params:
        t = normalize_generic_params(t, type_params)
    return t


def normalize_signature_types_first(
    signature: str | None,
    type_params: list[str] | None = None,
    *,
    skip_void_return: bool = True,
    return_sep: str = "",
) -> str | None:
    """Normalize a signature where params are ``Type name`` (Java, C#, Dart, Groovy).

    Input format: ``(Type name, Type name) ReturnType``
    or with return_sep ``":"``: ``(Type name, Type name): ReturnType``
    Output format: ``(Type,Type)ReturnType``

    Strips parameter names, FQN prefixes, and normalizes generic type
    parameters by position.  Used by Java, C#, Dart, Groovy, Objective-C.
    """
    if not signature:
        return None

    # Split into params part and return type
    paren_close = _find_matching_paren(signature, 0)
    if paren_close < 0:
        return None

    params_str = signature[1:paren_close]
    rest = signature[paren_close + 1:].strip()

    # Extract return type, stripping separator if present
    if return_sep and rest.startswith(return_sep):
        return_str = rest[len(return_sep):].strip()
    else:
        return_str = rest

    # Parse params: "Type name" → extract Type
    raw_params = split_params_top_level(params_str)
    types: list[str] = []
    for p in raw_params:
        p = p.strip()
        if not p:
            continue
        # Handle varargs: "Type... name"
        if "..." in p:
            p = p.replace("...", " ").strip()
        # Type comes first, name is last space-separated token
        # But type itself may have spaces (e.g., "unsigned int x" in C)
        # For most languages, split on last space
        parts = p.rsplit(None, 1)
        if len(parts) == 2:
            types.append(_normalize_type(parts[0], type_params))
        else:
            # Single token — it's either just a type or just a name
            types.append(_normalize_type(parts[0], type_params))

    norm_params = ",".join(types)

    if return_str and not (skip_void_return and return_str.lower() == "void"):
        norm_return = _normalize_type(return_str, type_params)
        return f"({norm_params}){norm_return}"
    return f"({norm_params})"


def normalize_signature_names_first(
    signature: str | None,
    type_params: list[str] | None = None,
    *,
    return_sep: str = ":",
    skip_self: bool = False,
) -> str | None:
    """Normalize a signature where params are ``name: Type`` (Kotlin, Scala, TS, Swift, Rust, Python).

    Input format: ``(name: Type, name: Type): ReturnType`` or
                  ``(name: Type, name: Type) -> ReturnType``
    Output format: ``(Type,Type)ReturnType``

    Args:
        signature: The raw signature string.
        type_params: Declared generic type parameter names.
        return_sep: Separator before return type (``":"`` or ``"->"``)
        skip_self: If True, skip ``self``/``cls`` parameters (Python/Rust).
    """
    if not signature:
        return None

    # Split into params part and return type
    paren_close = _find_matching_paren(signature, 0)
    if paren_close < 0:
        return None

    params_str = signature[1:paren_close]
    rest = signature[paren_close + 1:].strip()

    # Extract return type
    return_str = ""
    if rest.startswith(return_sep):
        return_str = rest[len(return_sep):].strip()
    elif rest.startswith("->"):
        return_str = rest[2:].strip()
    elif rest:
        return_str = rest.strip()

    # Parse params: "name: Type" → extract Type
    raw_params = split_params_top_level(params_str)
    types: list[str] = []
    for p in raw_params:
        p = p.strip()
        if not p:
            continue
        # Skip self/cls parameters
        if skip_self and p in ("self", "cls", "&self", "&mut self"):
            continue
        # Find the colon separator — "name: Type"
        colon_idx = p.find(":")
        if colon_idx >= 0:
            type_part = p[colon_idx + 1:].strip()
            types.append(_normalize_type(type_part, type_params))
        else:
            # No type annotation (e.g., bare name in Python)
            # Include as-is for untyped params
            types.append(p.strip())

    norm_params = ",".join(types)

    if return_str:
        norm_return = _normalize_type(return_str, type_params)
        return f"({norm_params}){norm_return}"
    return f"({norm_params})"


def normalize_signature_php(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a PHP signature: ``(Type $name, Type $name): ReturnType``.

    Output format: ``(Type,Type)ReturnType``
    """
    if not signature:
        return None

    paren_close = _find_matching_paren(signature, 0)
    if paren_close < 0:
        return None

    params_str = signature[1:paren_close]
    rest = signature[paren_close + 1:].strip()

    return_str = ""
    if rest.startswith(":"):
        return_str = rest[1:].strip()

    raw_params = split_params_top_level(params_str)
    types: list[str] = []
    for p in raw_params:
        p = p.strip()
        if not p:
            continue
        # PHP format: "Type $name" or "Type $name = ..." or "$name" (untyped)
        dollar_idx = p.find("$")
        if dollar_idx > 0:
            type_part = p[:dollar_idx].strip()
            types.append(_normalize_type(type_part, type_params))
        else:
            # Untyped param: "$name" or just name
            types.append(p.strip())

    norm_params = ",".join(types)

    if return_str and return_str.lower() != "void":
        norm_return = _normalize_type(return_str, type_params)
        return f"({norm_params}){norm_return}"
    return f"({norm_params})"


def normalize_signature_go(
    signature: str | None,
    type_params: list[str] | None = None,
) -> str | None:
    """Normalize a Go signature: ``(name Type, name Type) ReturnType``.

    Go has a unique convention where multiple names can share a type:
    ``(a, b int)`` means both are ``int``.  The return type may be
    a tuple: ``(int, error)``.

    Output format: ``(Type,Type)ReturnType``
    """
    if not signature:
        return None

    paren_close = _find_matching_paren(signature, 0)
    if paren_close < 0:
        return None

    params_str = signature[1:paren_close]
    rest = signature[paren_close + 1:].strip()

    # Return type in Go — could be "(Type, Type)" or "Type"
    return_str = rest.strip()

    # Parse params — Go format: "name Type" where Type is the last token
    raw_params = split_params_top_level(params_str)
    types: list[str] = []
    for p in raw_params:
        p = p.strip()
        if not p:
            continue
        # Strip pointer: "*Type" → "Type"
        parts = p.split()
        if len(parts) >= 2:
            # Last token is the type
            raw_type = parts[-1]
            if raw_type.startswith("*"):
                raw_type = raw_type[1:]
            types.append(_normalize_type(raw_type, type_params))
        else:
            # Single token — might be a type with no name
            raw_type = parts[0]
            if raw_type.startswith("*"):
                raw_type = raw_type[1:]
            types.append(_normalize_type(raw_type, type_params))

    norm_params = ",".join(types)

    if return_str:
        # Normalize return type (strip pointer prefixes, FQN)
        ret = return_str
        if ret.startswith("(") and ret.endswith(")"):
            # Tuple return: "(int, error)" → normalize each
            inner = ret[1:-1]
            ret_parts = split_params_top_level(inner)
            norm_ret = "(" + ",".join(
                _normalize_type(r.strip().lstrip("*"), type_params)
                for r in ret_parts
            ) + ")"
            return f"({norm_params}){norm_ret}"
        if ret.startswith("*"):
            ret = ret[1:]
        norm_return = _normalize_type(ret, type_params)
        return f"({norm_params}){norm_return}"
    return f"({norm_params})"


def _find_matching_paren(s: str, start: int) -> int:
    """Find the index of the closing paren matching the opening paren at *start*.

    Returns -1 if not found or *s[start]* is not ``(``.
    """
    if start >= len(s) or s[start] != "(":
        return -1
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


# ---------------------------------------------------------------------------
# Grammar availability checking
# ---------------------------------------------------------------------------


def is_grammar_available(grammar_module: str) -> bool:
    """Check if a tree-sitter grammar is available.

    Args:
        grammar_module: The grammar module name (e.g., "tree_sitter_go")

    Returns:
        True if both tree_sitter and the grammar module are importable.
    """
    if importlib.util.find_spec("tree_sitter") is None:
        return False
    if importlib.util.find_spec(grammar_module) is None:
        return False
    return True


# ---------------------------------------------------------------------------
# Iterative tree traversal (avoids RecursionError on deeply nested code)
# ---------------------------------------------------------------------------


def iter_tree(root: "tree_sitter.Node") -> Iterator["tree_sitter.Node"]:
    """Iterate over all nodes in a tree-sitter tree without recursion.

    Uses an explicit stack to avoid RecursionError on deeply nested code
    (e.g., TensorFlow has files exceeding Python's 1000-level limit).

    Args:
        root: The root node of the tree to traverse

    Yields:
        Each node in depth-first order.

    Example:
        for node in iter_tree(tree.root_node):
            if node.type == "function_definition":
                # process function...
    """
    stack: list["tree_sitter.Node"] = [root]
    while stack:
        node = stack.pop()
        yield node
        # Add children in reverse order so leftmost is processed first
        stack.extend(reversed(node.children))


def iter_tree_with_context(
    root: "tree_sitter.Node",
    context_types: set[str],
) -> Iterator[tuple["tree_sitter.Node", Optional["tree_sitter.Node"]]]:
    """Iterate over nodes with parent context tracking.

    Useful for edge extraction where we need to know the enclosing
    function/method when processing call expressions.

    Args:
        root: The root node of the tree to traverse
        context_types: Node types that establish context (e.g., {"function_definition"})

    Yields:
        Tuples of (node, context_node) where context_node is the nearest
        ancestor matching one of context_types, or None if outside any context.

    Example:
        for node, func_ctx in iter_tree_with_context(tree.root_node, {"function_definition"}):
            if node.type == "call_expression" and func_ctx:
                # We know which function contains this call
    """
    # Stack entries: (node, current_context)
    stack: list[tuple["tree_sitter.Node", Optional["tree_sitter.Node"]]] = [
        (root, None)
    ]
    while stack:
        node, context = stack.pop()

        # Update context if this node is a context type
        new_context = node if node.type in context_types else context

        yield node, context

        # Add children with updated context
        for child in reversed(node.children):
            stack.append((child, new_context))


# ---------------------------------------------------------------------------
# File discovery helpers
# ---------------------------------------------------------------------------


def make_file_finder(patterns: list[str]) -> Callable[[Path], Iterator[Path]]:  # pragma: no cover
    """Create a file finder function for specific patterns.

    Args:
        patterns: Glob patterns to match (e.g., ["*.go"], ["*.rs"])

    Returns:
        A function that yields matching files from a repo root.
    """

    def finder(repo_root: Path) -> Iterator[Path]:
        yield from find_files(repo_root, patterns)

    return finder


# ---------------------------------------------------------------------------
# TreeSitterAnalyzer base class
# ---------------------------------------------------------------------------


class TreeSitterAnalyzer:
    """Base class for tree-sitter-based language analyzers.

    Encapsulates the universal two-pass architecture used by 100+ analyzers:
      Pass 1: Discover files, parse with tree-sitter, extract symbols
      Pass 2: Re-walk ASTs, resolve calls/imports against global symbol registry

    Subclasses configure via class attributes and override template methods
    for language-specific extraction logic.

    How It Works
    ------------
    1. Check grammar availability (``_check_grammar_available``)
    2. Initialize parser and AnalysisRun
    3. Pass 1: ``extract_symbols_from_file()`` for each source file
    4. Build global symbol registry via ``register_symbol()``
    5. Pass 2: ``extract_edges_from_file()`` for each file
    6. Pass 2b: ``extract_usage_contexts_from_file()`` for each file
    7. ``post_process()`` hook for cross-cutting concerns
    8. Assemble and return AnalysisResult

    Why This Design
    ---------------
    Previously, each analyzer duplicated this two-pass loop (~100 lines).
    The base class captures the scaffolding so subclasses focus solely on
    language-specific extraction logic. Analyzers can override any template
    method, or override ``analyze()`` entirely for full control.

    Grammar Modes
    -------------
    Two ways to specify the grammar:

    - ``grammar_module = "tree_sitter_go"`` — direct package import
    - ``language_pack_name = "nim"`` — uses tree_sitter_language_pack

    Exactly one should be set. The base class handles availability checking
    and parser creation for both modes.

    Example (simple analyzer)::

        class NimAnalyzer(TreeSitterAnalyzer):
            lang = "nim"
            file_patterns = ["*.nim", "*.nims"]
            language_pack_name = "nim"

            def extract_symbols_from_file(self, tree, source, file_path,
                                          rel_path, run):
                analysis = FileAnalysis()
                for node in iter_tree(tree.root_node):
                    if node.type == "proc_declaration":
                        # ... extract symbol
                return analysis

            def extract_edges_from_file(self, ...):
                # ... extract edges
                return edges

        _analyzer = NimAnalyzer()

        @register_analyzer("nim")
        def analyze_nim(repo_root, max_files=None):
            return _analyzer.analyze(repo_root, max_files)
    """

    # -- Required configuration (set by subclass) --------------------------
    lang: str = ""
    """Language identifier (e.g., "go", "rust", "python")."""

    pass_id: str = ""
    """Pass identifier (e.g., "go-v1", "rust-v1")."""

    pass_version: str = ""
    """Version string (e.g., "hypergumbo-0.1.0")."""

    file_patterns: ClassVar[list[str]] = []
    """Glob patterns for source files (e.g., ["*.go"], ["*.rs"])."""

    # -- Grammar source: exactly one of these should be set ----------------
    grammar_module: Optional[str] = None
    """Direct grammar package name (e.g., "tree_sitter_go")."""

    language_pack_name: Optional[str] = None
    """Language-pack grammar name (e.g., "nim")."""

    # -- Optional configuration --------------------------------------------
    resolver_class: type = NameResolver
    """Resolver class for symbol lookup during Pass 2."""

    create_file_symbols: bool = False
    """Whether to emit file-level symbols for each source file."""

    supports_max_files: bool = False
    """Whether analyze() should respect the max_files parameter."""

    self_keywords: ClassVar[frozenset[str]] = frozenset({"self"})
    """Tokens that refer to the current instance in this language.

    Python/Ruby/Rust/Swift use ``self``, Java/C#/Kotlin use ``this``.
    Subclasses override for their language.  Used by
    ``resolve_receiver_type()`` to anchor field-chain resolution.
    """

    # -- Template methods: grammar setup -----------------------------------

    def _check_grammar_available(self) -> bool:
        """Check if the tree-sitter grammar is available.

        Default implementation handles both grammar_module and
        language_pack_name modes. Override for custom availability logic.

        Returns:
            True if grammar is importable and usable.
        """
        if self.grammar_module is not None:
            return is_grammar_available(self.grammar_module)
        if self.language_pack_name is not None:
            if importlib.util.find_spec("tree_sitter") is None:
                return False  # pragma: no cover
            if importlib.util.find_spec("tree_sitter_language_pack") is None:
                return False  # pragma: no cover
            try:
                from tree_sitter_language_pack import get_language

                get_language(self.language_pack_name)
                return True
            except Exception:  # pragma: no cover
                return False
        return False  # pragma: no cover - no grammar configured

    def _create_parser(self) -> "tree_sitter.Parser":
        """Create and return a tree-sitter parser.

        Default implementation handles both grammar_module and
        language_pack_name modes. Override for custom parser setup.

        Returns:
            A configured tree-sitter Parser instance.
        """
        import tree_sitter

        if self.grammar_module is not None:
            mod = importlib.import_module(self.grammar_module)
            lang = tree_sitter.Language(mod.language())
            return tree_sitter.Parser(lang)

        # language_pack_name mode
        from tree_sitter_language_pack import get_language

        lang = get_language(self.language_pack_name)
        return tree_sitter.Parser(lang)

    # -- Template methods: symbol extraction (Pass 1) ----------------------

    def extract_symbols_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        run: AnalysisRun,
    ) -> FileAnalysis:
        """Extract symbols from a single parsed file.

        Override this method with language-specific symbol extraction.
        Default returns empty FileAnalysis.

        Args:
            tree: Parsed tree-sitter tree
            source: Raw source bytes
            file_path: Absolute path to the file
            rel_path: Path relative to repo root
            run: Current AnalysisRun for provenance

        Returns:
            FileAnalysis with symbols and symbol_by_name populated.
        """
        return FileAnalysis()  # pragma: no cover

    def get_import_aliases(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
    ) -> dict[str, str]:
        """Extract import alias to module path mappings.

        Used during Pass 2 for call disambiguation (e.g., "np" -> "numpy").
        Default returns empty dict.

        Args:
            tree: Parsed tree-sitter tree
            source: Raw source bytes

        Returns:
            Mapping of alias name to full module path.
        """
        return {}

    # -- Template methods: edge extraction (Pass 2) ------------------------

    def extract_edges_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        rel_path: str,
        local_symbols: dict[str, Symbol],
        global_symbols: dict,
        run: AnalysisRun,
        import_aliases: dict[str, str],
        resolver: NameResolver,
    ) -> list[Edge]:
        """Extract edges from a single parsed file.

        Override for language-specific edge extraction.
        Default returns empty list.

        Args:
            tree: Parsed tree-sitter tree
            source: Raw source bytes
            file_path: Absolute path to the file
            rel_path: Path relative to repo root
            local_symbols: Symbol-by-name dict for this file
            global_symbols: All symbols across all files
            run: Current AnalysisRun for provenance
            import_aliases: Import alias mappings from get_import_aliases
            resolver: Configured name resolver for symbol lookup

        Returns:
            List of Edge instances.
        """
        return []  # pragma: no cover

    def extract_usage_contexts_from_file(
        self,
        tree: "tree_sitter.Tree",
        source: bytes,
        file_path: Path,
        symbol_by_name: dict[str, Symbol],
    ) -> list[UsageContext]:
        """Extract UsageContext records for framework pattern matching.

        Default returns empty list. Override for route-emitting analyzers.

        Args:
            tree: Parsed tree-sitter tree
            source: Raw source bytes
            file_path: Absolute path to the file
            symbol_by_name: Symbol-by-name dict for this file

        Returns:
            List of UsageContext instances.
        """
        return []

    # -- Field type resolution helpers --------------------------------------

    def resolve_receiver_type(
        self,
        value_node: "tree_sitter.Node",
        source: bytes,
        enclosing_type: str | None,
    ) -> str | None:
        """Resolve a self.field receiver chain to its type name.

        Uses ``_field_type_registry`` (set by ``analyze()`` before Pass 2).
        Walks nested ``field_expression`` / ``member_expression`` nodes to
        decompose a receiver chain like ``self.app`` into segments, then
        iteratively resolves through the registry.

        Only resolves chains rooted at a ``self_keywords`` token.
        Non-self receivers return None.

        Args:
            value_node: The receiver node of a method call (e.g. the
                ``self.app`` part of ``self.app.run()``).
            source: Source bytes for extracting node text.
            enclosing_type: The type that contains this method (e.g.
                the impl target in Rust, the class name in Python).

        Returns:
            The resolved type name, or None if resolution fails at any step.
        """
        if enclosing_type is None:
            return None

        registry = getattr(self, "_field_type_registry", {})
        if not registry:
            return None

        # Decompose the receiver chain into segments.
        # e.g. self.inner.app → ["self", "inner", "app"]
        # C++: this->inner->app (field_expression uses "argument" not "value")
        segments: list[str] = []
        node = value_node
        while node.type in ("field_expression", "member_expression"):
            field_node = node.child_by_field_name("field")
            if field_node is None:
                return None
            segments.append(node_text(field_node, source))
            node = (
                node.child_by_field_name("value")
                or node.child_by_field_name("argument")
            )
            if node is None:
                return None

        # Root must be a self keyword
        root_text = node_text(node, source)
        if root_text not in self.self_keywords:
            return None

        # Segments were collected leaf-to-root; reverse to walk root-to-leaf.
        segments.reverse()

        # Walk the chain: start from enclosing_type, resolve each field step.
        current_type = enclosing_type
        for field_name in segments:
            fields = registry.get(current_type)
            if fields is None:
                return None
            next_type = fields.get(field_name)
            if next_type is None:
                return None
            current_type = next_type

        return current_type

    # -- Template methods: global symbol registry --------------------------

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict,
    ) -> None:
        """Add a symbol to the global registry for cross-file resolution.

        Default stores by qualified name only. The ``NameResolver`` suffix
        index handles short-name lookups (e.g., ``"compute"`` →
        ``"Diff::compute"``) so individual analyzers should NOT register
        short names — doing so causes false exact matches when multiple
        types share a method name.

        Override for language-specific indexing (e.g., Go stores lists).

        Args:
            symbol: Symbol to register
            global_symbols: Mutable global registry dict
        """
        global_symbols[symbol.name] = symbol

    # -- Template methods: file discovery ------------------------------------

    def _find_source_files(self, repo_root: Path) -> Iterator[Path]:
        """Yield source files to analyze.

        Default uses ``find_files(repo_root, self.file_patterns)``.
        Override for custom filtering (e.g., F# skips Forth .fs files).

        Args:
            repo_root: Root directory of the repository.

        Yields:
            Paths to source files.
        """
        yield from find_files(repo_root, self.file_patterns)

    # -- Template methods: post-processing ---------------------------------

    def post_process(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        usage_contexts: list[UsageContext],
        run: AnalysisRun,
    ) -> tuple[list[Symbol], list[Edge], list[UsageContext]]:
        """Optional post-processing after both passes complete.

        Use for route extraction, annotation edges, or other
        cross-cutting concerns. Default is identity.

        Args:
            symbols: All symbols from Pass 1
            edges: All edges from Pass 2
            usage_contexts: All usage contexts from Pass 2
            run: Current AnalysisRun

        Returns:
            Tuple of (symbols, edges, usage_contexts), possibly modified.
        """
        return symbols, edges, usage_contexts

    # -- shape_id computation (ADR-0014 §1) ---------------------------------

    _SHAPE_SKIP_TYPES: ClassVar[frozenset[str]] = frozenset(
        {"comment", "line_comment", "block_comment", "ERROR", "MISSING"}
    )

    def compute_shape_id(self, node: "tree_sitter.Node") -> str:
        """Compute shape_id by hashing the structural skeleton of a CST subtree.

        Walks the tree-sitter concrete syntax tree non-recursively, strips
        identifiers, literals, comments, and punctuation, then hashes the
        resulting S-expression.  This captures the structural "shape" of code
        independent of naming and formatting.

        Filtering strategy (ADR-0014 §1):
        - Anonymous nodes (punctuation like ``{``, ``;``, ``(``) are skipped
        - Comment, ERROR, and MISSING nodes are skipped
        - Named leaf nodes (identifiers, literals) emit only their type name
        - Named non-leaf nodes emit ``(type child1 child2 ...)`` structure

        Returns:
            Shape hash in ``sha256:{16-hex-chars}`` format matching
            the Python analyzer's convention.
        """
        structure = self._cst_structure(node)
        hash_val = hashlib.sha256(structure.encode()).hexdigest()[:16]
        return f"sha256:{hash_val}"

    def _cst_structure(self, node: "tree_sitter.Node") -> str:
        """Build an S-expression skeleton of a CST subtree.

        Uses an explicit stack to avoid RecursionError on deeply nested code
        (same rationale as ``iter_tree()``).  The output is a parenthesised
        S-expression where only *named* structural nodes contribute, with
        leaf nodes (identifiers, literals, keywords) represented by their
        type name alone.

        Example output for ``def foo(x): return x + 1``::

            (function_definition identifier (parameters identifier)
             (block (return_statement (binary_operator identifier integer))))
        """
        parts: list[str] = []
        skip = self._SHAPE_SKIP_TYPES
        # Stack entries: (node | None, phase)
        #   phase 0 → process / open the node
        #   phase 1 → close the node (append ")")
        stack: list[tuple["tree_sitter.Node | None", int]] = [(node, 0)]

        while stack:
            current, phase = stack.pop()

            if phase == 1:
                parts.append(")")
                continue

            assert current is not None  # pragma: no cover - type narrowing

            # Skip anonymous nodes (punctuation) and filtered types
            if not current.is_named or current.type in skip:
                continue

            # Collect named, non-skip children
            named_children = [
                c for c in current.children
                if c.is_named and c.type not in skip
            ]

            if not named_children:
                # Leaf: just emit the type name (covers identifiers, literals, etc.)
                parts.append(current.type)
            else:
                # Non-leaf: open S-expression, push closing marker + children
                parts.append(f"({current.type}")
                stack.append((None, 1))
                for child in reversed(named_children):
                    stack.append((child, 0))

        return " ".join(parts)

    # -- Parameter classification (ADR-0014 §2) ----------------------------

    # Node types that indicate variadic positional params, by grammar.
    # Languages may add to this set via _VARARGS_NODE_TYPES class attribute.
    _VARARGS_NODE_TYPES: ClassVar[frozenset[str]] = frozenset({
        "rest_pattern",          # JS/TS
        "rest_element",          # JS/TS (alternative)
        "spread_parameter",      # Java
        "splat_parameter",       # Ruby
        "variadic_parameter",    # C/C++, PHP
    })

    # Node types that indicate variadic keyword params.
    _KWARGS_NODE_TYPES: ClassVar[frozenset[str]] = frozenset({
        "hash_splat_parameter",  # Ruby
        "dictionary_splat_pattern",  # Python (tree-sitter)
    })

    # Node types that indicate default parameter values.
    _DEFAULT_NODE_TYPES: ClassVar[frozenset[str]] = frozenset({
        "assignment_pattern",    # JS/TS
        "optional_parameter",    # Ruby, TypeScript
        "default_parameter",     # Python (tree-sitter)
    })

    def classify_parameter_flags(
        self, params_node: "tree_sitter.Node",
    ) -> ArityFlags:
        """Classify a function's parameter list into ArityFlags.

        Examines the children of a tree-sitter parameter-list node to
        determine parameter count, defaults, varargs, and kwargs.

        The default implementation uses heuristic node-type matching that
        covers most C-family and scripting languages.  Override for
        languages with unusual parameter models.

        Args:
            params_node: A tree-sitter node representing the parameter list
                (e.g., ``formal_parameters``, ``parameters``, ``parameter_list``).

        Returns:
            ArityFlags with the classified values.
        """
        param_count = 0
        has_defaults = False
        has_varargs = False
        has_kwargs = False

        for child in params_node.children:
            if not child.is_named:
                continue  # skip punctuation

            node_type = child.type

            # Check for varargs/kwargs/defaults
            if node_type in self._VARARGS_NODE_TYPES:
                has_varargs = True
                param_count += 1
            elif node_type in self._KWARGS_NODE_TYPES:
                has_kwargs = True
                param_count += 1
            elif node_type in self._DEFAULT_NODE_TYPES:
                has_defaults = True
                param_count += 1
            else:
                # Regular parameter (identifier, typed_parameter, etc.)
                param_count += 1
                # Check if any child has a default value (= expr)
                for sub in child.children:
                    if not sub.is_named:
                        continue
                    if sub.type in self._DEFAULT_NODE_TYPES or sub.type == "default_value":
                        has_defaults = True
                        break

        return ArityFlags(
            param_count=param_count,
            has_defaults=has_defaults,
            has_varargs=has_varargs,
            has_kwargs=has_kwargs,
        )

    # -- stable_id computation (ADR-0014 §2) ---------------------------------

    # Node types for decorator/annotation wrappers, by grammar.
    _DECORATOR_NODE_TYPES: ClassVar[frozenset[str]] = frozenset({
        "decorator",             # Python, JS/TS
        "annotation",            # Java, Kotlin
        "attribute",             # C#, Rust
        "attribute_item",        # Rust inner
    })

    # Node types for parameter-list containers.
    _PARAMS_NODE_TYPES: ClassVar[frozenset[str]] = frozenset({
        "parameters",            # Python, Ruby
        "formal_parameters",     # Java, JS/TS
        "parameter_list",        # C, C++, C#
        "function_parameters",   # Rust
    })

    def compute_stable_id(
        self,
        node: "tree_sitter.Node",
        kind: str,
        containing_stable_id: str = "",
    ) -> str:
        """Compute an untyped-tier stable_id for a function/class/method node.

        Uses the formula from ADR-0014 §2::

            sha256({kind}:{param_count}:{arity_flags}:{decorators}:{containing_stable_id})

        The result survives renames and file moves because it captures
        only the *interface shape*, not names or locations.

        Args:
            node: Tree-sitter node for the symbol's definition.
            kind: Symbol kind (``"function"``, ``"method"``, ``"class"``).
            containing_stable_id: Stable ID of the enclosing scope (class or
                module).  Empty string for top-level definitions.

        Returns:
            Stable ID in ``sha256:{16-hex-chars}`` format.
        """
        # 1. Extract parameter arity
        params_node = self._find_params_node(node)
        if params_node is not None:
            flags = self.classify_parameter_flags(params_node)
        else:
            flags = ArityFlags(
                param_count=0,
                has_defaults=False,
                has_varargs=False,
                has_kwargs=False,
            )

        # 2. Extract decorator/annotation names
        decorators = self._extract_decorator_names(node)
        decorators_str = ",".join(sorted(decorators))

        # 3. Build signature string and hash
        sig = (
            f"{kind}:{flags.param_count}:{flags.as_flags_str()}"
            f":{decorators_str}:{containing_stable_id}"
        )
        hash_val = hashlib.sha256(sig.encode()).hexdigest()[:16]
        return f"sha256:{hash_val}"

    def _find_params_node(
        self, node: "tree_sitter.Node",
    ) -> "tree_sitter.Node | None":
        """Find the parameter-list child of a definition node.

        Searches immediate children for a node whose type is in
        ``_PARAMS_NODE_TYPES``.  Returns ``None`` for class definitions
        or nodes without parameter lists.
        """
        for child in node.children:
            if child.type in self._PARAMS_NODE_TYPES:
                return child
        return None

    def _extract_decorator_names(
        self, node: "tree_sitter.Node",
    ) -> list[str]:
        """Extract decorator/annotation names from a definition node.

        Looks at the node's parent (if available) or siblings for
        decorator nodes.  Returns a list of plain decorator names
        (without arguments or module paths).

        The default implementation looks for decorator children preceding
        the definition node.  Override for grammars where decorators are
        structured differently.
        """
        names: list[str] = []
        # Some grammars nest decorators as children of the definition node
        # (e.g., Python's decorated_definition → decorator* + definition).
        # Others make them siblings. Check children first.
        for child in node.children:
            if child.type in self._DECORATOR_NODE_TYPES:
                name = self._decorator_node_name(child)
                if name:
                    names.append(name)
        return names

    def _decorator_node_name(
        self, decorator_node: "tree_sitter.Node",
    ) -> str:
        """Extract the plain name from a single decorator/annotation node.

        Walks the decorator's children to find the identifier.  Handles
        both simple (``@foo``) and call (``@foo(arg)``) forms.
        """
        for child in decorator_node.children:
            if child.type == "identifier":
                return child.text.decode("utf-8", errors="replace") if child.text else ""
            # Dotted path: @module.decorator → take last segment
            if child.type in ("attribute", "dotted_name"):
                # Find the last identifier in the chain
                last_ident = ""
                for sub in child.children:
                    if sub.type == "identifier":
                        last_ident = sub.text.decode("utf-8", errors="replace") if sub.text else ""
                if last_ident:
                    return last_ident
            # Call form: @decorator(args) → look inside the call
            if child.type == "call":
                return self._decorator_node_name(child)
        return ""

    # -- Main analysis method (the two-pass loop) --------------------------

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run the full two-pass analysis.

        This method orchestrates the entire analysis pipeline:
        1. Check grammar availability
        2. Initialize parser and AnalysisRun
        3. Pass 1: extract symbols from each file
        4. Build global symbol registry
        5. Pass 2: extract edges from each file
        6. Pass 2b: extract usage contexts
        7. Post-process
        8. Assemble and return AnalysisResult

        Args:
            repo_root: Root directory of the repository
            max_files: Optional limit on files to process

        Returns:
            AnalysisResult with symbols, edges, usage_contexts, and run.
        """
        start_time = time.time()
        effective_pass_id = self.pass_id or make_pass_id(self.lang)
        effective_pass_version = self.pass_version or PASS_VERSION
        run = AnalysisRun.create(pass_id=effective_pass_id, version=effective_pass_version)

        # 1. Check grammar availability
        if not self._check_grammar_available():
            warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            run.duration_ms = int((time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        # 2. Initialize parser
        parser = self._create_parser()

        # 3. Pass 1: Extract symbols from all files
        # Cache source bytes per file so Pass 2 can reuse them without
        # re-reading from disk.  The tuple stores (analysis, aliases, source).
        file_analyses: dict[Path, tuple[FileAnalysis, dict[str, str], bytes]] = {}
        files_analyzed = 0
        files_skipped = 0

        for source_file in self._find_source_files(repo_root):
            if max_files is not None and files_analyzed >= max_files:
                break

            # Memory safety: check between files to catch pressure early
            _check_memory_pressure()

            try:
                source = source_file.read_bytes()
            except OSError:
                files_skipped += 1
                continue

            tree = parser.parse(source)
            rel_path = str(source_file.relative_to(repo_root))

            analysis = self.extract_symbols_from_file(
                tree, source, source_file, rel_path, run
            )

            # Optional: create file-level symbol
            if self.create_file_symbols:
                file_sym = Symbol(
                    id=make_file_id(self.lang, rel_path),
                    name=rel_path,
                    kind="file",
                    language=self.lang,
                    path=rel_path,
                    span=Span(start_line=1, start_col=0, end_line=1, end_col=0),
                    origin=effective_pass_id,
                    origin_run_id=run.execution_id,
                )
                analysis.symbols.insert(0, file_sym)

            # Auto-compute shape_id and docstring for symbols with nodes (ADR-0014 §1)
            if analysis.node_for_symbol:
                sym_by_id = {s.id: s for s in analysis.symbols}
                for sym_id, ts_node in analysis.node_for_symbol.items():
                    sym = sym_by_id.get(sym_id)
                    if sym is not None:
                        if sym.shape_id is None:
                            sym.shape_id = self.compute_shape_id(ts_node)
                        if sym.docstring is None:
                            sym.docstring = extract_doc_comment(ts_node, source)

            # Fallback: populate docstrings for symbols without node_for_symbol
            # by finding their tree-sitter nodes via position matching.
            populate_docstrings_from_tree(tree.root_node, source, analysis.symbols)

            # Extract import aliases for Pass 2
            import_aliases = self.get_import_aliases(tree, source)

            file_analyses[source_file] = (analysis, import_aliases, source)
            files_analyzed += 1

        # 4. Build global symbol registry
        global_symbols: dict = {}
        for analysis, _, _source in file_analyses.values():
            for symbol in analysis.symbols:
                self.register_symbol(symbol, global_symbols)

        # 4b. Aggregate class field types from all files for Pass 2.
        # Subclasses populate FileAnalysis.class_field_types during Pass 1;
        # here we merge them into a single registry keyed by class/struct name.
        field_type_registry: dict[str, dict[str, str]] = {}
        for analysis, _, _source in file_analyses.values():
            for class_name, fields in analysis.class_field_types.items():
                if class_name not in field_type_registry:
                    field_type_registry[class_name] = {}
                for fname, ftype in fields.items():
                    field_type_registry[class_name].setdefault(fname, ftype)
        self._field_type_registry = field_type_registry

        # 5. Pass 2: Extract edges and usage contexts
        # Reuses cached source bytes from Pass 1 to avoid re-reading files.
        all_symbols: list[Symbol] = []
        all_edges: list[Edge] = []
        all_contexts: list[UsageContext] = []
        resolver = self.resolver_class(global_symbols)

        for source_file, (analysis, import_aliases, source) in file_analyses.items():
            _check_memory_pressure()  # Between-file memory safety check
            all_symbols.extend(analysis.symbols)

            tree = parser.parse(source)
            rel_path = str(source_file.relative_to(repo_root))

            edges = self.extract_edges_from_file(
                tree, source, source_file, rel_path,
                analysis.symbol_by_name, global_symbols, run,
                import_aliases, resolver,
            )
            # ADR-0015 Tier 1: automatic dataflow annotation from AST context
            df_config = get_dataflow_config(self.lang)
            if df_config is not None:
                edges = annotate_dataflow(edges, tree, source, df_config)
            all_edges.extend(edges)

            # 6. Usage contexts
            contexts = self.extract_usage_contexts_from_file(
                tree, source, source_file, analysis.symbol_by_name,
            )
            all_contexts.extend(contexts)

        # Clear field type registry to avoid stale data across runs.
        self._field_type_registry = {}

        # 7. Post-process
        all_symbols, all_edges, all_contexts = self.post_process(
            all_symbols, all_edges, all_contexts, run,
        )

        # 8. Assemble result
        run.files_analyzed = files_analyzed
        run.files_skipped = files_skipped
        run.duration_ms = int((time.time() - start_time) * 1000)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            usage_contexts=all_contexts,
            run=run,
        )

    # -- Registration helper -----------------------------------------------

    def as_registered_analyzer(self) -> Callable:
        """Return a function suitable for use with @register_analyzer.

        Returns a function with signature
        ``(repo_root: Path, max_files: int | None = None) -> AnalysisResult``
        that delegates to ``self.analyze()``.

        Example::

            _analyzer = GoAnalyzer()

            @register_analyzer("go", priority=50)
            def analyze_go(repo_root, max_files=None):
                return _analyzer.analyze(repo_root, max_files)

            # Or equivalently:
            analyze_go = register_analyzer("go")(_analyzer.as_registered_analyzer())

        Returns:
            A callable that wraps ``self.analyze()``.
        """

        def analyze_fn(
            repo_root: Path, max_files: Optional[int] = None
        ) -> AnalysisResult:
            return self.analyze(repo_root, max_files)

        return analyze_fn
