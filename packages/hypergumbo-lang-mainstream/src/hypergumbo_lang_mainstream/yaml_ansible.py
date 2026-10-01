# SPDX-License-Identifier: AGPL-3.0-or-later
"""YAML/Ansible analyzer using tree-sitter.

This analyzer extracts playbooks, tasks, handlers, and variables from
Ansible YAML files. It uses tree-sitter-yaml for parsing when available,
falling back gracefully when the grammar is not installed.

Constructs detected:
- Playbooks (- name: X, hosts: Y)
- Tasks (- name: X, module: params)
- Handlers (handlers: section)
- Variables (vars: section)
- Include/import references (include_tasks, import_tasks, include_role,
  import_role)

File discovery (``find_ansible_files``) is evidence-gated (WI-jifog).
``.yml``/``.yaml`` has no Ansible-specific extension, and directory names
such as ``tasks/`` or ``vars/`` are common outside Ansible, so the path
only nominates a candidate; the claim needs Ansible evidence: the file is
itself a playbook, or its tree is rooted by an ``ansible.cfg``, a playbook
or a role entry point. Before this, any root-level YAML (a
``.yamllint.yaml``) and any YAML under an Ansible-named directory was
tagged ``language="ansible"``. Unclaimed YAML falls to the generic
``yaml`` file-anchor analyzer, which subtracts this set.

Each discovered Ansible file also gets a ``kind="file"`` symbol, which
is the ``src`` of its include/import edges.

Single-pass per file: symbols and reference edges (includes,
imports) are extracted together (see ``AnsibleAnalyzer`` class
docstring for rationale).

After all files are extracted, ``AnsibleAnalyzer.analyze`` rewrites
each ``imports`` edge's raw target to a file symbol id.
``_resolve_ansible_path`` handles literal paths (basename match,
preferring the source's directory; ``name=role`` ->
``roles/<role>/tasks/main.yml``). A Jinja-templated path fans out via
``_jinja_fanout_candidates`` to one edge per matching file at
``_JINJA_FANOUT_CONFIDENCE`` (0.30); an unresolvable target keeps its
raw string and drops to confidence 0.50.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional

import yaml

from hypergumbo_core.discovery import is_excluded
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.pass_silence import DEPENDENCY_UNAVAILABLE

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("yaml_ansible")


# Directory names that NOMINATE a YAML file as an Ansible candidate. A name
# match is not evidence on its own: ``tasks/``, ``vars/`` and ``roles/`` are
# ordinary directory names in non-Ansible projects (WI-jifog).
_ANSIBLE_DIRS = frozenset({
    "roles", "tasks", "handlers", "playbooks", "vars", "defaults",
    "group_vars", "host_vars",
})
_YAML_EXTENSIONS = (".yml", ".yaml")
# Keys only a play (an item of a playbook) carries: ``hosts`` targets the
# play; ``import_playbook`` is the playbook-level include.
_PLAY_KEYS = frozenset({"hosts", "import_playbook", "ansible.builtin.import_playbook"})
# A role's entry point is ``<role>/tasks/main.yml`` (or ``.yaml``).
_ROLE_ENTRY_NAMES = frozenset({"main.yml", "main.yaml"})
# Files above this size are not parsed for their shape: a playbook or a
# role's task list is small, and composing a multi-megabyte data file just
# to learn it is not a playbook is wasted work on large repos.
_MAX_SHAPE_BYTES = 1_048_576
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@dataclass(frozen=True)
class _TopShape:
    """Top-level shape of a YAML file's first document.

    ``is_task_list``: a non-empty sequence whose every item is a mapping
    (the shape of a playbook, a task file and a handler file).
    ``is_play_list``: a task list in which some item carries a play key.
    """

    is_play_list: bool = False
    is_task_list: bool = False


_NO_SHAPE = _TopShape()


def _top_level_shape(path: Path) -> _TopShape:
    """Classify the top level of ``path``'s first YAML document.

    Uses ``yaml.compose_all`` (node graph, no object construction), so
    Ansible's ``!vault`` and other application tags do not raise, and only
    the first document is parsed. An unreadable, oversized, empty or
    malformed file has no shape.
    """
    try:
        if path.stat().st_size > _MAX_SHAPE_BYTES:
            return _NO_SHAPE
        with path.open("rb") as fh:
            doc = next(iter(yaml.compose_all(fh, Loader=_YAML_LOADER)), None)
    except (OSError, UnicodeError, yaml.YAMLError):
        return _NO_SHAPE
    if not isinstance(doc, yaml.SequenceNode) or not doc.value:
        return _NO_SHAPE
    if not all(isinstance(item, yaml.MappingNode) for item in doc.value):
        return _NO_SHAPE
    is_play = any(
        isinstance(key, yaml.ScalarNode) and key.value in _PLAY_KEYS
        for item in doc.value
        for key, _value in item.value
    )
    return _TopShape(is_play_list=is_play, is_task_list=True)


def find_ansible_files(root: Path) -> list[Path]:
    """Find Ansible YAML files in a directory tree.

    Two path arms NOMINATE a ``.yml``/``.yaml`` file as a candidate: it is
    at the repository root, or a repo-relative directory component is an
    Ansible directory name (``_ANSIBLE_DIRS``). Path alone is never enough
    (WI-jifog: ``.yamllint.yaml`` and a plain ``web/vars/app.yaml`` were
    both tagged ``language=ansible``). A candidate is claimed when:

    - it is itself a playbook (``_TopShape.is_play_list``) -- either arm; or
    - it is on the directory arm AND lies under an *Ansible tree root*.

    Ansible vars/defaults/group_vars files are plain mappings, so their own
    content cannot identify them; the tree root supplies the evidence. A
    tree root is the directory holding an ``ansible.cfg``, or the path
    prefix before the first Ansible directory name of a playbook or of a
    role entry point (``tasks/main.yml`` holding a task list). Roots are
    scoped: an ``ansible.cfg`` under ``deploy/`` does not make
    ``app/vars/x.yaml`` Ansible. Only repo-relative components count, so a
    checkout that happens to live under ``.../vars/`` is not affected.
    """
    # Use the global FileIndex if available to avoid a redundant walk.
    from hypergumbo_core.discovery import get_file_index
    file_index = get_file_index()
    if file_index is not None and file_index.repo_root == root:
        all_files = file_index.all_files()
    else:
        all_files = [
            p for p in root.rglob("*")
            if p.is_file() and not is_excluded(p, root)
        ]

    tree_roots: set[tuple[str, ...]] = set()
    candidates: list[tuple[Path, tuple[str, ...], Optional[int], _TopShape]] = []
    for path in all_files:
        rel_parts = path.relative_to(root).parts
        dir_parts = rel_parts[:-1]
        if path.name == "ansible.cfg":
            tree_roots.add(dir_parts)
            continue
        if path.suffix not in _YAML_EXTENSIONS:
            continue
        first = next(
            (i for i, part in enumerate(dir_parts) if part in _ANSIBLE_DIRS), None,
        )
        if first is None and dir_parts:
            continue  # neither at the root nor under an Ansible directory
        shape = _top_level_shape(path)
        candidates.append((path, dir_parts, first, shape))
        if first is None:
            if shape.is_play_list:
                tree_roots.add(dir_parts)
        elif shape.is_play_list or (
            shape.is_task_list
            and path.name in _ROLE_ENTRY_NAMES
            and dir_parts[-1] == "tasks"
        ):
            tree_roots.add(dir_parts[:first])

    return [
        path
        for path, dir_parts, first, shape in candidates
        if shape.is_play_list
        or (
            first is not None
            and any(dir_parts[:len(r)] == r for r in tree_roots)
        )
    ]


def _find_all_children_by_type(
    node: "tree_sitter.Node", type_name: str
) -> list["tree_sitter.Node"]:
    """Find all children (recursive) with given type.

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    result: list["tree_sitter.Node"] = []
    for n in iter_tree(node):
        if n.type == type_name:
            result.append(n)
    return result


def _get_scalar_value(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract scalar value from various YAML scalar types."""
    if node.type in ("plain_scalar", "single_quote_scalar", "double_quote_scalar"):
        for child in node.children:
            if child.type in ("string_scalar", "boolean_scalar", "integer_scalar", "float_scalar"):
                return node_text(child, source)
        return node_text(node, source)  # pragma: no cover - fallback
    elif node.type == "flow_node":
        for child in node.children:
            val = _get_scalar_value(child, source)
            if val:
                return val
    return None  # pragma: no cover - defensive fallback


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)


def _extract_mapping_key_value(
    pair_node: "tree_sitter.Node", source: bytes
) -> tuple[str | None, str | None]:
    """Extract key and value from a block_mapping_pair."""
    key: str | None = None
    value: str | None = None

    children = list(pair_node.children)
    for i, child in enumerate(children):
        if child.type == "flow_node" and key is None:
            key = _get_scalar_value(child, source)
        elif child.type == ":" and key is not None:
            # Value comes after the colon
            for j in range(i + 1, len(children)):
                next_child = children[j]
                if next_child.type == "flow_node":
                    value = _get_scalar_value(next_child, source)
                    break
                elif next_child.type == "block_node":  # pragma: no cover - nested value
                    break
            break

    return key, value


def _extract_vars_from_pair(
    pair_node: "tree_sitter.Node",
    source: bytes,
    symbols: list[Symbol],
    rel_path: str,
    run: AnalysisRun,
) -> None:
    """Extract variable definitions from a vars: block_mapping_pair."""
    # Find the block_node value of the vars: key
    for child in pair_node.children:
        if child.type == "block_node":
            # Look for nested block_mapping
            nested_mapping = find_child_by_type(child, "block_mapping")
            if nested_mapping:
                for nested_pair in nested_mapping.children:
                    if nested_pair.type == "block_mapping_pair":
                        var_key, var_value = _extract_mapping_key_value(nested_pair, source)
                        if var_key:
                            line = nested_pair.start_point[0] + 1
                            symbol_id = make_symbol_id("ansible", rel_path, line, line, var_key, "variable")
                            symbols.append(Symbol(
                                id=symbol_id,
                                name=var_key,
                                kind="variable",
                                language="ansible",
                                path=rel_path,
                                span=Span(line, line, 0, 0),
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge]]:
    """Extract symbols and edges from a single Ansible file."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    rel_path = str(file_path)
    file_id = make_file_id("ansible", rel_path)

    try:
        source = file_path.read_bytes()
    except (OSError, IOError):  # pragma: no cover
        return symbols, edges

    tree = parser.parse(source)
    root = tree.root_node

    # Track context
    in_tasks = False
    in_handlers = False
    current_play_name: str | None = None

    def process_mapping_pairs(
        pairs: list["tree_sitter.Node"], context: str
    ) -> None:
        nonlocal in_tasks, in_handlers, current_play_name

        for pair in pairs:
            key, value = _extract_mapping_key_value(pair, source)
            if not key:  # pragma: no cover - malformed YAML
                continue

            line = pair.start_point[0] + 1
            end_line = pair.end_point[0] + 1

            # Detect sections and process nested content
            if key == "tasks":
                in_tasks = True
                in_handlers = False
            elif key == "handlers":
                in_handlers = True
                in_tasks = False
            elif key == "vars":
                # Process nested vars block inline
                _extract_vars_from_pair(pair, source, symbols, rel_path, run)

            # Extract playbook name
            if key == "name" and context == "play":
                current_play_name = value
                if value:
                    symbol_id = make_symbol_id("ansible", rel_path, line, end_line, value, "playbook")
                    symbols.append(Symbol(
                        id=symbol_id,
                        name=value,
                        kind="playbook",
                        language="ansible",
                        path=rel_path,
                        span=Span(line, end_line, 0, 0),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))

            # Extract task/handler name
            elif key == "name" and (in_tasks or in_handlers):
                kind = "handler" if in_handlers else "task"
                if value:
                    symbol_id = make_symbol_id("ansible", rel_path, line, end_line, value, kind)
                    symbols.append(Symbol(
                        id=symbol_id,
                        name=value,
                        kind=kind,
                        language="ansible",
                        path=rel_path,
                        span=Span(line, end_line, 0, 0),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))

            # Detect include/import patterns
            if key in ("include_tasks", "import_tasks", "include_role", "import_role"):
                if value:
                    edges.append(Edge.create(
                        src=file_id,
                        dst=value,
                        edge_type="imports",
                        line=line,
                        evidence_type=key,
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))

    # Find all block_sequence_items (plays or tasks)
    seq_items = _find_all_children_by_type(root, "block_sequence_item")

    for seq_item in seq_items:
        # Get mapping pairs from this item
        mapping = find_child_by_type(seq_item, "block_node")
        if mapping:
            nested_mapping = find_child_by_type(mapping, "block_mapping")
            if nested_mapping:
                pairs = [c for c in nested_mapping.children if c.type == "block_mapping_pair"]

                # Determine context (play level or task level)
                is_play = any(
                    _extract_mapping_key_value(p, source)[0] == "hosts"
                    for p in pairs
                )
                context = "play" if is_play else "task"
                process_mapping_pairs(pairs, context)

    return symbols, edges


def _resolve_ansible_path(
    raw_dst: str,
    src_file_id: str,
    file_basename_map: dict[str, list[str]],
    file_node_ids: dict[str, str],
) -> str | None:
    """Resolve an Ansible include/import path to a file node ID.

    Tries several strategies:
    1. Exact basename match (e.g., "common.yml" → tasks/common.yml)
    2. Relative path from the source file's directory
    3. Role name resolution ("name=rolename" → roles/rolename/tasks/main.yml)

    Returns the file node ID if resolved, None otherwise. Jinja2 template
    expressions (containing "{{") are deferred to ``_jinja_fanout_candidates``
    in the caller — this function only handles literal paths.
    """
    # Jinja2 templates are handled by the fan-out path; this function only
    # resolves literal paths.
    if "{{" in raw_dst:
        return None

    # Handle role references: "name=rolename" → roles/rolename/tasks/main.yml
    if raw_dst.startswith("name="):
        role_name = raw_dst[5:]
        for rel_path, node_id in file_node_ids.items():
            if f"roles/{role_name}/tasks/main.yml" in rel_path:
                return node_id
        return None

    # Strip quotes that may have leaked through
    cleaned = raw_dst.strip("'\"")

    # Try exact basename match
    basename = cleaned.rsplit("/", 1)[-1] if "/" in cleaned else cleaned
    if basename in file_basename_map:
        candidates = file_basename_map[basename]
        if len(candidates) == 1:
            return file_node_ids[candidates[0]]
        # Multiple candidates: try to pick the one in the same directory
        # Extract source directory from src_file_id
        # src_file_id format: ansible:{path}:1-1:file:file
        parts = src_file_id.split(":")
        if len(parts) >= 2:
            src_path = parts[1]
            src_dir = src_path.rsplit("/", 1)[0] if "/" in src_path else ""
            for cand in candidates:
                cand_dir = cand.rsplit("/", 1)[0] if "/" in cand else ""
                if cand_dir == src_dir:
                    return file_node_ids[cand]
        # Fall back to first candidate
        return file_node_ids[candidates[0]]

    return None


_JINJA_RE = re.compile(r"\{\{.*?\}\}")

# Confidence assigned to each fan-out edge produced from a Jinja-templated
# include_tasks/import_tasks directive. The Jinja variable is only resolvable
# at runtime, so we emit one edge per candidate file matching the literal
# portions of the pattern, with reduced confidence to reflect the uncertainty.
_JINJA_FANOUT_CONFIDENCE = 0.30


def _jinja_fanout_candidates(
    raw_dst: str,
    src_file_id: str,
    file_basename_map: dict[str, list[str]],
    file_node_ids: dict[str, str],
) -> list[str]:
    """For a Jinja-templated include path, return candidate file node IDs.

    Two shapes of Jinja-templated path are recognized:

    1. **Basename Jinja** (e.g. ``{{ ansible_os_family }}.yml``): only the
       basename contains a template expression. We treat the ``{{ ... }}``
       as a wildcard and regex-match against sibling files in the source's
       directory. Cross-directory candidates are excluded because role-style
       OS-family dispatch is conventionally co-located.

    2. **Path-prefix Jinja** (e.g. ``{{ tasks_path }}/yumrepos.yml``): the
       basename is literal but the directory portion is templated. We
       fan out to every file in the repo whose basename matches, since
       the templated prefix could resolve to any location at runtime.
       Sorted by repo-relative path for determinism.

    Returns candidate node IDs (possibly empty). The source file itself
    is excluded.
    """
    cleaned = raw_dst.strip("'\"")
    if "{{" not in cleaned:
        return []

    basename = cleaned.rsplit("/", 1)[-1] if "/" in cleaned else cleaned
    # src_file_id format: ansible:{path}:1-1:file:file
    parts = src_file_id.split(":")
    if len(parts) < 2:
        return []  # pragma: no cover — defensive; file IDs always have a path
    src_path = parts[1]

    if "{{" in basename:
        return _basename_jinja_fanout(basename, src_path, file_node_ids)
    # Path-prefix Jinja with literal basename: fan out by basename across repo.
    if basename in file_basename_map:
        matches = [p for p in file_basename_map[basename] if p != src_path]
        matches.sort()
        return [file_node_ids[p] for p in matches]
    return []


def _basename_jinja_fanout(
    basename_pattern: str,
    src_path: str,
    file_node_ids: dict[str, str],
) -> list[str]:
    """Match a Jinja basename pattern against sibling files in src's directory.

    Each ``{{ ... }}`` becomes ``.+`` (must consume at least one char; a
    Jinja var that expands to the empty string would be useless here).
    """
    literals = _JINJA_RE.split(basename_pattern)
    pattern = "^" + ".+".join(re.escape(lit) for lit in literals) + "$"
    matcher = re.compile(pattern)

    src_dir = src_path.rsplit("/", 1)[0] if "/" in src_path else ""
    src_basename = src_path.rsplit("/", 1)[-1]

    matches: list[str] = []
    for rel_path in file_node_ids:
        if rel_path == src_path:
            continue
        cand_basename = rel_path.rsplit("/", 1)[-1]
        cand_dir = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""
        if cand_dir != src_dir:
            continue
        if cand_basename == src_basename:
            continue  # pragma: no cover — same path was already filtered
        if matcher.match(cand_basename):
            matches.append(rel_path)
    matches.sort()
    return [file_node_ids[p] for p in matches]


class AnsibleAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based Ansible YAML analyzer.

    Uses tree-sitter-yaml to parse Ansible playbook, task, handler, and
    variable files. Extracts playbooks, tasks, handlers, variables, and
    include/import reference edges.

    Overrides ``analyze`` because Ansible uses a single-pass approach per
    file (combined symbol+edge extraction) and custom file discovery logic
    that searches Ansible-specific directories rather than simple glob patterns.
    """

    lang = "yaml_ansible"
    file_patterns: ClassVar[list[str]] = ["*.yml", "*.yaml"]
    grammar_module = "tree_sitter_yaml"

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run Ansible analysis with custom file discovery and single-pass extraction.

        Each file is processed with ``_extract_symbols_from_file`` which returns
        both symbols and edges in a single pass.
        """
        import time as _time
        import warnings as _warnings

        start_time = _time.time()
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        if not self._check_grammar_available():
            _warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            run.duration_ms = int((_time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
                skip_reason_code=DEPENDENCY_UNAVAILABLE,
            )

        parser = self._create_parser()

        all_files = find_ansible_files(repo_root)
        if not all_files:
            run.duration_ms = int((_time.time() - start_time) * 1000)
            return AnalysisResult(run=run)

        all_symbols: list[Symbol] = []
        all_edges: list[Edge] = []

        # Build file-level nodes and a lookup map for edge resolution.
        # Maps basename → list of relative paths (multiple files may share a name).
        file_basename_map: dict[str, list[str]] = {}
        file_node_ids: dict[str, str] = {}  # rel_path → file node ID

        for ansible_file in all_files:
            rel_path = str(ansible_file)
            fid = make_file_id("ansible", rel_path)
            file_node_ids[rel_path] = fid

            basename = ansible_file.name
            file_basename_map.setdefault(basename, []).append(rel_path)

            # Create file-level node
            all_symbols.append(Symbol(
                id=fid,
                name=basename,
                kind="file",
                language="ansible",
                path=rel_path,
                span=Span(1, 1, 0, 0),
                origin=PASS_ID,
                origin_run_id=run.execution_id,
            ))

        for ansible_file in all_files:
            if max_files is not None and len(all_symbols) >= max_files:
                break  # pragma: no cover

            symbols, edges = _extract_symbols_from_file(ansible_file, parser, run)
            all_symbols.extend(symbols)
            all_edges.extend(edges)

        # Resolve edge destinations: convert raw filenames to file node IDs.
        # include_tasks/import_tasks use relative filenames; try to match
        # them against known Ansible files by basename or relative path.
        extra_edges: list[Edge] = []
        for edge in all_edges:
            if edge.edge_type != "imports":
                continue  # pragma: no cover — all current edges are imports
            raw_dst = edge.dst
            # Skip if already a valid node ID (shouldn't happen, but defensive)
            if raw_dst in file_node_ids.values():
                continue  # pragma: no cover — dst is always raw filename
            resolved = _resolve_ansible_path(
                raw_dst, edge.src, file_basename_map, file_node_ids,
            )
            if resolved is not None:
                edge.dst = resolved
                continue
            # Unresolved by literal-path strategies. If the destination is a
            # Jinja template like "{{ ansible_os_family }}.yml", fan out to
            # sibling files matching the literal portions.
            candidates = _jinja_fanout_candidates(
                raw_dst, edge.src, file_basename_map, file_node_ids,
            )
            if candidates:
                edge.dst = candidates[0]
                edge.confidence = _JINJA_FANOUT_CONFIDENCE
                for extra_dst in candidates[1:]:
                    extra_edges.append(Edge.create(
                        src=edge.src,
                        dst=extra_dst,
                        edge_type=edge.edge_type,
                        line=edge.line,
                        evidence_type=edge.evidence_type,
                        confidence=_JINJA_FANOUT_CONFIDENCE,
                        origin=edge.origin,
                        origin_run_id=edge.origin_run_id,
                    ))
            else:
                # Unresolvable (Jinja2 template with no matching siblings,
                # missing file, etc.)
                edge.confidence = 0.50
        all_edges.extend(extra_edges)

        run.duration_ms = int((_time.time() - start_time) * 1000)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            run=run,
        )


_analyzer = AnsibleAnalyzer()


def is_yaml_tree_sitter_available() -> bool:
    """Check if tree-sitter and yaml grammar are available."""
    return _analyzer._check_grammar_available()


@register_analyzer("yaml_ansible", languages=["ansible"], language_state="no_taxonomy_spec")  # WI-futin
def analyze_ansible(root: Path) -> AnalysisResult:
    """Analyze Ansible YAML files in a directory.

    Uses tree-sitter-yaml for parsing. Falls back gracefully if not available.
    """
    return _analyzer.analyze(root)
