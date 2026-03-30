# SPDX-License-Identifier: AGPL-3.0-or-later
"""YAML-driven dataflow classification for edges (ADR-0015).

Provides declarative dataflow annotation on edges using YAML pattern files.
Instead of hardcoding read/write classification in each analyzer, patterns
are externalized to YAML files that map tree-sitter AST node types to
access modes (read, write, mutate, delete).

How It Works
------------
1. Each language has a dataflow YAML (e.g., dataflow/python.yaml) defining
   which AST node types represent assignments, deletions, and calls.
2. ``annotate_dataflow()`` takes a batch of edges and a parsed AST tree,
   finds the AST node at each edge's line, looks up the node type in the
   language config, and stamps ``access_mode`` into the edge's ``meta`` dict.
3. Edges that already have ``access_mode`` set (by a linker — Tier 2) are
   skipped, implementing the precedence rule: explicit beats automatic.
4. ``scan_library_patterns()`` matches regex patterns from the
   ``library_patterns`` YAML section against source text, returning
   structured DataflowSite objects for library-specific read/write sites.

Two-Tier Model (ADR-0015 §5)
-----------------------------
- **Tier 1 (automatic):** Intra-language edges annotated via AST node lookup.
  One integration point in ``analyze/base.py`` covers all 104 tree-sitter
  analyzers automatically.
- **Tier 2 (explicit):** Cross-language linker edges annotated by the linker
  itself via ``Edge.create(access_mode=..., channel=...)``. These are skipped
  by ``annotate_dataflow()`` to avoid double-counting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .ir import Edge


@dataclass
class DataflowSite:
    """A source location where a dataflow access (read/write/mutate/delete) occurs.

    Produced by ``scan_library_patterns()`` for library-specific patterns
    that can't be detected from AST structure alone (e.g., Yjs .set(),
    Kafka producer.send()).
    """

    access_mode: str
    line: int
    channel: Optional[str] = None


@dataclass
class DataflowConfig:
    """Parsed dataflow YAML configuration for a single language.

    Each section maps tree-sitter node types to access mode rules.
    The ``library_patterns`` section defines regex-based patterns for
    library-specific dataflow detection.  The ``returns`` section maps
    return/yield node types to ``"read"`` (the function reads/produces
    a value).
    """

    language: str
    assignments: List[Dict[str, Any]] = field(default_factory=list)
    calls: List[Dict[str, Any]] = field(default_factory=list)
    deletions: List[Dict[str, Any]] = field(default_factory=list)
    borrows: List[Dict[str, Any]] = field(default_factory=list)
    returns: List[Dict[str, Any]] = field(default_factory=list)
    library_patterns: List[Dict[str, Any]] = field(default_factory=list)

    def build_node_type_map(self) -> Dict[str, str]:
        """Build a simple lookup from tree-sitter node_type to access_mode.

        Returns a dict mapping node_type strings to the primary access mode
        for that node type.  For assignment rules with both write and read
        sides, this returns the write/mutate side (use
        ``build_positional_map`` for position-aware classification).
        """
        result: Dict[str, str] = {}
        for rule in self.assignments:
            node_type = rule.get("node_type", "")
            if "write" in rule:
                result[node_type] = "write"
            elif "mutate" in rule:
                result[node_type] = "mutate"
            elif "read" in rule:
                result[node_type] = "read"
        for rule in self.deletions:
            node_type = rule.get("node_type", "")
            result[node_type] = "delete"
        for rule in self.returns:
            node_type = rule.get("node_type", "")
            if node_type and node_type not in result:
                result[node_type] = "read"
        for rule in self.calls:
            node_type = rule.get("node_type", "")
            if node_type and node_type not in result:
                result[node_type] = "read"
        for rule in self.borrows:
            node_type = rule.get("node_type", "")
            if "mutate_if" in rule:
                result[node_type] = "mutate"
            elif "read_if" in rule:
                result[node_type] = "read"
        return result

    def build_positional_map(self) -> Dict[str, Dict[str, str]]:
        """Build a lookup from node_type to child-field-based access modes.

        Assignment rules with both write and read sides (e.g.,
        ``{node_type: assignment, write: left, read: right}``) produce
        positional maps like ``{"assignment": {"left": "write", "right": "read"}}``.

        Non-positional rules (deletions, returns, borrows, calls) produce
        a single ``_default`` key: ``{"delete_statement": {"_default": "delete"}}``.
        """
        result: Dict[str, Dict[str, str]] = {}
        for rule in self.assignments:
            node_type = rule.get("node_type", "")
            if not node_type:
                continue
            child_modes: Dict[str, str] = {}
            for key, val in rule.items():
                if key == "node_type":
                    continue
                # key is the access mode, val is the child field name
                # e.g., {"write": "left", "read": "right"}
                if key in ("write", "read", "mutate", "delete"):
                    child_modes[val] = key
            if child_modes:
                result[node_type] = child_modes
        for rule in self.deletions:
            node_type = rule.get("node_type", "")
            if node_type:
                result[node_type] = {"_default": "delete"}
        for rule in self.returns:
            node_type = rule.get("node_type", "")
            if node_type:
                result[node_type] = {"_default": "read"}
        for rule in self.calls:
            node_type = rule.get("node_type", "")
            if node_type and node_type not in result:
                result[node_type] = {"_default": "read"}
        for rule in self.borrows:
            node_type = rule.get("node_type", "")
            if not node_type:
                continue
            if "mutate_if" in rule:
                result[node_type] = {"_default": "mutate"}
            elif "read_if" in rule:
                result[node_type] = {"_default": "read"}
        return result


def load_dataflow_config(yaml_path: Path) -> DataflowConfig:
    """Load a dataflow YAML file and return a DataflowConfig.

    Args:
        yaml_path: Path to a dataflow YAML file.

    Returns:
        Parsed DataflowConfig with all sections populated.

    Raises:
        ValueError: If the YAML is missing the required 'language' field.
    """
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    language = data.get("language")
    if not language:
        raise ValueError(f"Dataflow YAML {yaml_path} missing required 'language' field")

    return DataflowConfig(
        language=language,
        assignments=data.get("assignments", []),
        calls=data.get("calls", []),
        deletions=data.get("deletions", []),
        borrows=data.get("borrows", []),
        returns=data.get("returns", []),
        library_patterns=data.get("library_patterns", []),
    )


# Module-level cache for loaded configs, keyed by language name.
_config_cache: Dict[str, Optional[DataflowConfig]] = {}

# Default directory for built-in dataflow YAMLs (sibling to frameworks/).
# Named dataflow_patterns/ (not dataflow/) to avoid shadowing this module.
_DATAFLOW_DIR = Path(__file__).parent / "dataflow_patterns"


def get_dataflow_config(language: str) -> Optional[DataflowConfig]:
    """Get the dataflow config for a language, loading from YAML if needed.

    Looks for a YAML file at ``<package>/dataflow/<language>.yaml``.
    Returns None if no config exists for the language. Caches results
    so each YAML is loaded at most once per process.

    Args:
        language: Language name (e.g., "python", "javascript", "rust").

    Returns:
        DataflowConfig if a YAML exists, None otherwise.
    """
    if language in _config_cache:
        return _config_cache[language]

    yaml_path = _DATAFLOW_DIR / f"{language}.yaml"
    if yaml_path.is_file():
        config = load_dataflow_config(yaml_path)
        _config_cache[language] = config
    else:
        _config_cache[language] = None

    return _config_cache[language]


def _build_line_index(root_node: Any) -> Dict[int, Any]:
    """Build an index mapping 0-indexed line numbers to deepest AST nodes.

    Walks the entire tree once and records the deepest (last-seen) node
    for each start line. This enables O(1) lookup by line number instead
    of O(depth) per-edge in annotate_dataflow().

    For a file with N edges, this reduces total work from O(N * depth) to
    O(tree_size + N). Profiling shows _find_node_at_line accounts for ~15%
    of Java analysis time on large codebases (37K calls for killbill).
    """
    index: Dict[int, Any] = {}
    stack = [root_node]
    while stack:
        node = stack.pop()
        # Record this node for its start line (deeper nodes overwrite shallower)
        index[node.start_point[0]] = node
        # Push children in reverse order so left-to-right processing
        # means later (deeper) children overwrite earlier ones
        for child in reversed(node.children):
            stack.append(child)
    return index


def _classify_by_position(
    node: Any,
    parent: Any,
    child_modes: Dict[str, str],
) -> Optional[str]:
    """Classify an edge's node by its position within a parent AST node.

    Uses byte-range containment to determine which named child field of
    *parent* contains *node*, then returns the access mode for that child.

    Args:
        node: The deepest AST node at the edge's line.
        parent: The matching ancestor node (e.g., an assignment node).
        child_modes: Mapping of child-field-name → access_mode from the
            positional map.

    Returns:
        Access mode string if position resolved, None otherwise.
    """
    if "_default" in child_modes:
        return child_modes["_default"]

    # Resolve position via tree-sitter child_by_field_name
    child_by_field = getattr(parent, "child_by_field_name", None)
    if child_by_field is None:
        return None

    node_start = getattr(node, "start_byte", None)
    node_end = getattr(node, "end_byte", None)
    if node_start is None or node_end is None:
        return None

    for field_name, mode in child_modes.items():
        child = child_by_field(field_name)
        if child is None:
            continue
        child_start = getattr(child, "start_byte", None)
        child_end = getattr(child, "end_byte", None)
        if child_start is None or child_end is None:
            continue
        if child_start <= node_start and node_end <= child_end:
            return mode

    return None


def annotate_dataflow(
    edges: List["Edge"],
    tree: Any,
    source: bytes,
    config: DataflowConfig,
) -> List["Edge"]:
    """Batch-annotate edges with access_mode from AST context (Tier 1).

    For each edge, finds the AST node at the edge's line number, walks up
    the AST to find a node type that matches the language's dataflow config,
    then classifies the access mode.  For assignment nodes with positional
    rules (e.g., ``write: left, read: right``), the mode depends on which
    child subtree of the assignment the edge's node falls in.

    Edges that already have ``access_mode`` in their meta are skipped
    (Tier 2 precedence rule: explicit linker annotations beat automatic).

    Uses a pre-built line→node index for O(1) per-edge lookup instead of
    walking the AST for each edge.

    Args:
        edges: List of Edge objects to annotate.
        tree: Parsed tree-sitter Tree object.
        source: Source file bytes (for context, currently unused).
        config: DataflowConfig for the language.

    Returns:
        The same list of edges, with access_mode added to meta where applicable.
        Edges are modified in place and also returned for convenience.
    """
    if not edges:
        return edges

    positional_map = config.build_positional_map()
    if not positional_map:
        return edges

    # Build line index once for O(1) per-edge lookup
    line_index = _build_line_index(tree.root_node)

    for edge in edges:
        # Skip edges that already have access_mode (Tier 2 precedence)
        if edge.meta is not None and "access_mode" in edge.meta:
            continue

        # Look up node at this edge's line (O(1) via index)
        target_line = edge.line - 1  # tree-sitter uses 0-indexed lines
        node = line_index.get(target_line)
        if node is None:
            continue

        # Walk up from the deepest node to find a matching node type
        current = node
        access_mode = None
        while current is not None:
            if current.type in positional_map:
                child_modes = positional_map[current.type]
                access_mode = _classify_by_position(node, current, child_modes)
                break
            current = getattr(current, "parent", None)

        if access_mode is not None:
            if edge.meta is None:
                edge.meta = {}
            edge.meta["access_mode"] = access_mode

    return edges


def scan_library_patterns(
    content: str,
    config: DataflowConfig,
) -> List[DataflowSite]:
    """Match library-specific regex patterns against source text.

    Scans the source content for patterns defined in the config's
    ``library_patterns`` section. Each match produces a DataflowSite
    with the access mode, line number, and optional channel.

    Args:
        content: Source file content as string.
        config: DataflowConfig with library_patterns.

    Returns:
        List of DataflowSite objects for each pattern match.
    """
    if not config.library_patterns:
        return []

    sites: List[DataflowSite] = []
    lines = content.split("\n")

    for pattern_def in config.library_patterns:
        match_str = pattern_def.get("match", "")
        access_mode = pattern_def.get("access_mode", "read")
        channel = pattern_def.get("channel")
        channel_from = pattern_def.get("channel_from")

        if not match_str:
            continue

        # Use the match string as a regex pattern
        try:
            regex = re.compile(re.escape(match_str) if not _looks_like_regex(match_str) else match_str)
        except re.error:
            # Fall back to literal matching if regex is invalid
            regex = re.compile(re.escape(match_str))

        for i, line_text in enumerate(lines):
            m = regex.search(line_text)
            if m:
                site_channel = channel
                # Extract channel from capture group if channel_from is an int
                if channel_from is not None and isinstance(channel_from, int):
                    try:
                        site_channel = m.group(channel_from)
                    except (IndexError, re.error):
                        pass

                sites.append(DataflowSite(
                    access_mode=access_mode,
                    line=i + 1,  # 1-indexed
                    channel=site_channel,
                ))

    return sites


def annotate_dataflow_ast(
    edges: List["Edge"],
    tree: Any,
) -> List["Edge"]:
    """Annotate edges with access_mode using Python's ast module (Tier 1 for py.py).

    Walks the Python AST and builds a line-to-access-mode map from assignment,
    augmented assignment, delete, return, and yield statements.  For assignment
    lines, call edges are classified as ``"read"`` because the call produces a
    value consumed by the assignment (RHS), regardless of whether the
    assignment itself is a write or mutate.

    Skips edges that already have access_mode (Tier 2 precedence).

    Args:
        edges: List of Edge objects to annotate.
        tree: Python ast.Module node.

    Returns:
        The same list of edges with access_mode added where applicable.
    """
    import ast

    if not edges or tree is None:
        return edges

    # Build line -> access_mode map from AST
    line_modes: Dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            line_modes[node.lineno] = "write"
        elif isinstance(node, ast.AugAssign):
            line_modes[node.lineno] = "mutate"
        elif isinstance(node, ast.AnnAssign):
            line_modes[node.lineno] = "write"
        elif isinstance(node, ast.Delete):
            line_modes[node.lineno] = "delete"
        elif isinstance(node, ast.Return):
            line_modes[node.lineno] = "read"
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            line_modes[node.lineno] = "write"  # iteration variable
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            line_modes[node.lineno] = "write"  # context variable
        elif isinstance(node, ast.Yield) or isinstance(node, ast.YieldFrom):
            line_modes[node.lineno] = "read"

    if not line_modes:
        return edges

    for edge in edges:
        if edge.meta is not None and "access_mode" in edge.meta:
            continue
        mode = line_modes.get(edge.line)
        if mode is not None:
            # Call edges on assignment/augmented-assignment lines are reads:
            # the call produces a value consumed by the assignment (RHS).
            # Even in `foo().bar = x`, the call reads the object.
            if mode in ("write", "mutate") and edge.edge_type == "calls":
                mode = "read"
            if edge.meta is None:
                edge.meta = {}
            edge.meta["access_mode"] = mode

    return edges


def _looks_like_regex(s: str) -> bool:
    """Heuristic: does this string look like it contains regex metacharacters?"""
    # If it has unescaped regex metacharacters, treat as regex
    return bool(re.search(r'(?<!\\)[\\()\[\]{}+*?|^$]', s))
