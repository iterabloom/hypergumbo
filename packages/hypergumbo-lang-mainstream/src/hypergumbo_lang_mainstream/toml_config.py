# SPDX-License-Identifier: AGPL-3.0-or-later
"""TOML configuration file analyzer using tree-sitter-toml.

This module parses TOML configuration files (Cargo.toml, pyproject.toml, etc.)
to extract structure information useful for understanding project configuration.

How It Works
------------
Uses tree-sitter-toml to parse TOML files and extract:
- Table definitions (sections like [package], [dependencies])
- Array of tables (like [[bin]], [[test]])
- Dependencies (from Cargo.toml and pyproject.toml)

The analyzer produces Symbols for:
- Tables and nested tables
- Dependencies (Rust crates, Python packages) as ``kind="dependency"``.
  Cargo dev / build dependencies fold to the same kind, with
  ``meta["dependency_scope"]`` discriminating (per WI-limas /
  ADR-0027 audit-findings 0006). A ``pyproject.toml`` declares dependencies
  in many tables (PEP 621, PEP 735, ``[build-system]``, Poetry, PDM, uv,
  Hatch); see "pyproject dependency tables" below.
- Cargo array-of-tables targets: ``[[bin]]``, ``[[test]]``,
  ``[[example]]``, ``[[bench]]``
- Library configuration ([lib])
- Workspaces ([workspace])
- Project metadata ([project] / [package])
- Script entry points (pyproject ``[project.scripts]`` /
  ``[project.gui-scripts]``), emitted as ``kind="file"`` +
  ``meta["entry_role"]="script"``

The analyzer also emits ``defines_target`` edges from each
``[[bin]]`` / ``[[test]]`` / ``[[example]]`` / ``[[bench]]`` target
symbol to its explicit ``path`` (only when one is given), and from each
pyproject script symbol to an unresolved Python id for its function.

Why This Design
---------------
- TOML is used extensively for Rust (Cargo.toml), Python (pyproject.toml),
  and other configuration files
- Extracting dependencies (with scope metadata) helps understand
  project structure and supply-chain surface
- Cargo target extraction surfaces build outputs and test entry points
- ``defines_target`` edges feed cross-language entry-point linking
- Workspace detection identifies monorepo structures
- Identity (ADR-0035, WI-tusad): a table or array-of-tables symbol whose kind
  the backstop's table does not own (``table``, ``workspace``, ``library``,
  ``package``, ``table_array``, ``binary``, ``test``, ``example``,
  ``benchmark``) mints ``overload_arity.scoped_stable_id`` over its name, the
  table's dotted path (or the target's ``name``), which TOML makes unique per
  file for a table. The elements of one array of tables (``[[tool.x]]``
  twice) are a same-scope tie, which the per-file split pass orders by
  position: their array index is what tells them apart. ``project`` keeps the
  backstop's table formula (:func:`_toml_stable_id`); a Cargo dependency
  mints ``overload_arity.scoped_table_stable_id`` scoped by its table
  (``dev``, ``build``, a ``target.'cfg(..)'`` table), the backstop's value for
  ``[dependencies]`` (:func:`_extract_cargo_dependencies`; INV-punig), and a
  pyproject dependency is scoped by its table too, as described next.

pyproject dependency tables
---------------------------
The tables read are ``py_deps.PYPROJECT_DEPENDENCY_TABLES``, the list
``py_deps`` builds the import-directness manifest from, so the node reader and
the manifest reader name the same tables. Each entry of each table is one
node (:func:`_extract_pyproject_dependencies`):

- The key path of every pair is computed from the syntax tree, so a table is
  found however it is spelled: ``[project.optional-dependencies]`` with
  ``docs = [...]``, ``optional-dependencies.docs = [...]`` under
  ``[project]``, or ``optional-dependencies = {docs = [...]}``.
- The role of the table is ``meta["dependency_scope"]``: absent for a runtime
  dependency (``[project].dependencies``, ``[tool.poetry.dependencies]``),
  ``optional`` for an extra and for a Poetry entry with ``optional = true``,
  ``dev`` for a PEP 735 / Poetry / PDM / uv group and a Hatch environment,
  ``build`` for ``[build-system].requires``. ``dev`` and ``build`` are the
  values the Cargo tables and ``json_config``'s ``devDependencies`` use;
  ``optional`` is new with these tables.
- Identity: ``requests`` in ``[project].dependencies`` and in an extra are two
  declarations, each with its own span and constraint (ADR-0035 §4), so they
  are two nodes, and the table is the scope that tells them apart (ADR-0035
  §1; the rule Cargo's and package.json's groups follow, INV-punig). The
  stable_id is ``overload_arity.scoped_table_stable_id`` scoped by the
  table's key path (``project/optional-dependencies/docs``). One table is not
  scoped: ``[project].dependencies``, whose nodes take the backstop's
  unscoped value, the one they had when it was the only table read. Leaving
  the other tables unscoped would tie them with it, and the per-file split
  pass gives the unsuffixed id to whichever tied node comes first in the
  file: a ``[build-system]`` or ``[dependency-groups]`` table above
  ``[project]`` would take the runtime node's id.
- The name of a ``[project].dependencies`` node is still cut at the first
  ``.`` (``zope.interface`` is named ``zope``); that name is in the node's
  stable_id, so correcting it moves the id. Every other table uses
  ``py_deps.pep508_name``, which keeps the whole name.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, NodeId, PASS_VERSION, Span, StableId, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    is_table_kind,
    iter_tree,
    make_manifest_target_dst,
    make_symbol_id,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.overload_arity import scoped_stable_id, scoped_table_stable_id
from hypergumbo_core.pass_silence import DEPENDENCY_UNAVAILABLE

from hypergumbo_lang_mainstream.py_deps import (
    PYPROJECT_DEPENDENCY_TABLES,
    SHAPE_REQUIREMENTS,
    is_interpreter_constraint,
    pep508_name,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import tree_sitter


def _make_toml_symbol_id(
    path: str, start_line: int, end_line: int, name: str, kind: str,
) -> NodeId:
    """Generate a location-based symbol ID for TOML symbols.

    Uses the standard ``lang:path:start-end:name:kind`` format so that
    symbol IDs are human-readable and resolvable by ``slice --entry``
    (a hash would make compact output IDs opaque).
    """
    return make_symbol_id("toml", path, start_line, end_line, name, kind)


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate a unique edge ID."""
    key = f"toml:{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(key.encode()).hexdigest()[:16]}"


PASS_ID = make_pass_id("toml")


def find_toml_files(root: Path) -> Iterator[Path]:
    """Find all TOML files in a directory tree, excluding vendor dirs."""
    yield from find_files(root, ["*.toml"])


def _get_key_text(node: "tree_sitter.Node") -> str:
    """Extract key text from a key node.

    tree-sitter-toml nests a dotted key of three or more parts
    (``a.b.c`` is ``dotted_key(dotted_key(a, b), c)``), so the inner
    ``dotted_key`` is joined too; skipping it would name both
    ``[tool.poetry.dependencies]`` and ``[target.'cfg(windows)'.dependencies]``
    ``dependencies``, one name under two different parents, and their ids
    would tie (INV-punig).
    """
    if node.type == "bare_key":
        return node.text.decode("utf-8", errors="replace") if node.text else ""
    elif node.type == "quoted_key":
        text = node.text.decode("utf-8", errors="replace") if node.text else ""
        return text.strip('"').strip("'")
    elif node.type == "dotted_key":
        # Join dotted key parts
        parts = []
        for child in node.children:
            if child.type in ("bare_key", "quoted_key", "dotted_key"):
                parts.append(_get_key_text(child))
        return ".".join(parts)
    return node.text.decode("utf-8", errors="replace") if node.text else ""  # pragma: no cover


_KEY_NODE_TYPES = ("bare_key", "quoted_key", "dotted_key")


def _key_parts(node: "tree_sitter.Node") -> list[str]:
    """The parts of a key node, one per segment.

    ``a."b.c".d`` is three parts; :func:`_get_key_text` would join them into
    a string that reads as four.
    """
    if node.type != "dotted_key":
        return [_get_key_text(node)]
    parts: list[str] = []
    for child in node.children:
        if child.type in _KEY_NODE_TYPES:
            parts.extend(_key_parts(child))
    return parts


def _header_key_parts(node: "tree_sitter.Node") -> tuple[str, ...]:
    """The key path a ``table`` or ``table_array_element`` header names."""
    parts: list[str] = []
    for child in node.children:
        if child.type in _KEY_NODE_TYPES:
            parts.extend(_key_parts(child))
    return tuple(parts)


def _get_string_value(node: "tree_sitter.Node | None") -> str:
    """Extract string value from a string node."""
    if node is None:  # pragma: no cover - defensive
        return ""  # pragma: no cover - defensive
    text = node.text.decode("utf-8", errors="replace") if node.text else ""
    # Strip quotes - various quote styles supported by TOML
    if text.startswith('"""') and text.endswith('"""'):  # pragma: no cover - multi-line
        return text[3:-3]  # pragma: no cover - multi-line
    elif text.startswith("'''") and text.endswith("'''"):  # pragma: no cover - multi-line
        return text[3:-3]  # pragma: no cover - multi-line
    elif text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    elif text.startswith("'") and text.endswith("'"):  # pragma: no cover - literal
        return text[1:-1]  # pragma: no cover - literal
    return text  # pragma: no cover - fallback


def _extract_table_name(node: "tree_sitter.Node") -> str:
    """Extract the full table name from a table or table_array_element."""
    # Find the key nodes between [ ] or [[ ]]
    parts = []
    for child in node.children:
        if child.type in ("bare_key", "quoted_key", "dotted_key"):
            parts.append(_get_key_text(child))
    return ".".join(parts)


def _find_pair_value(table_node: "tree_sitter.Node", key: str) -> str | None:
    """Find the value of a specific key in a table."""
    for child in table_node.children:
        if child.type == "pair":
            pair_key = None
            pair_value = None
            for pair_child in child.children:
                if pair_child.type in ("bare_key", "quoted_key", "dotted_key"):
                    pair_key = _get_key_text(pair_child)
                elif pair_child.type == "string":
                    pair_value = _get_string_value(pair_child)
            if pair_key == key and pair_value:
                return pair_value
    return None  # pragma: no cover - key not found


def _toml_stable_id(kind: str, name: str, rel_path: str) -> Optional[StableId]:
    """The producer stable_id of a table-like symbol, or None for a table kind."""
    if is_table_kind(kind):
        return None
    return scoped_stable_id(kind=kind, lang="toml", path=rel_path, name=name)


def _process_toml_tree(
    root: "tree_sitter.Node",
    symbols: list[Symbol],
    edges: list[Edge],
    rel_path: str,
    content: str,
    is_cargo: bool,
    is_pyproject: bool,
    *,
    run_id: str,
) -> None:
    """Process a TOML AST tree iteratively and extract symbols and edges."""
    # The (table, name) pairs of the name-keyed pyproject tables already
    # emitted for this file (see _extract_pyproject_dependencies).
    declared_names: set[tuple[tuple[str, ...], str]] = set()
    for node in iter_tree(root):
        if node.type == "document" and is_pyproject:
            # Pairs above the first table header (``dependency-groups.test = [..]``).
            _extract_pyproject_dependencies(node, rel_path, symbols, declared_names)

        elif node.type == "table":
            table_name = _extract_table_name(node)

            # Determine kind based on table name
            kind = "table"
            name = table_name

            if table_name == "workspace":
                kind = "workspace"
            elif table_name == "lib":
                kind = "library"
                # Try to get library name
                lib_name = _find_pair_value(node, "name")
                if lib_name:
                    name = lib_name
            elif table_name == "project" and is_pyproject:
                kind = "project"
                proj_name = _find_pair_value(node, "name")
                if proj_name:
                    name = proj_name
            elif table_name == "package" and is_cargo:
                kind = "package"
                pkg_name = _find_pair_value(node, "name")
                if pkg_name:
                    name = pkg_name

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbol_id = _make_toml_symbol_id(rel_path, start_line, end_line, name, kind)

            symbols.append(
                Symbol(
                    id=symbol_id,
                    stable_id=_toml_stable_id(kind, name, rel_path),
                    shape_id=None,
                    # No canonical_name (name= carries it); the central post-pass stamps the fingerprint (ADR-0032).
                    kind=kind,
                    name=name,
                    path=rel_path,
                    language="toml",
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
            )

            # Process dependencies if this is a dependency table.
            # dev-dependencies and build-dependencies use kind="dependency"
            # with meta["dependency_scope"] = "dev" / "build"; the symbol-kind
            # registry has no separate dev-/build-dependency kinds
            # (WI-limas, ADR-0027).
            if is_cargo and table_name in (
                "dependencies",
                "dev-dependencies",
                "build-dependencies",
            ):
                _DEP_SCOPE = {
                    "dependencies": None,
                    "dev-dependencies": "dev",
                    "build-dependencies": "build",
                }
                _extract_cargo_dependencies(
                    node, rel_path, symbols, content, _DEP_SCOPE[table_name],
                )
            elif is_cargo and table_name.endswith(".dependencies"):
                # ``[target.'cfg(windows)'.dependencies]``: the target table
                # scopes the identity (not the meta, which names the
                # dependency_scope axis's dev/build values only).
                _extract_cargo_dependencies(
                    node, rel_path, symbols, content, None,
                    id_scope=table_name[: -len(".dependencies")],
                )
            elif is_pyproject:
                _extract_pyproject_dependencies(node, rel_path, symbols, declared_names)
                # Process pyproject.toml [project.scripts] and [project.gui-scripts]
                if table_name in ("project.scripts", "project.gui-scripts"):
                    _extract_pyproject_scripts(
                        node, rel_path, symbols, edges, content, run_id=run_id)

        elif node.type == "table_array_element":
            table_name = _extract_table_name(node)

            # Determine kind based on table array name
            kind = "table_array"
            name = table_name
            target_path = None  # Source file path for build targets

            if table_name == "bin" and is_cargo:
                kind = "binary"
                bin_name = _find_pair_value(node, "name")
                if bin_name:
                    name = bin_name
                target_path = _find_pair_value(node, "path")
            elif table_name == "test" and is_cargo:
                kind = "test"
                test_name = _find_pair_value(node, "name")
                if test_name:
                    name = test_name
                target_path = _find_pair_value(node, "path")
            elif table_name == "example" and is_cargo:
                kind = "example"
                ex_name = _find_pair_value(node, "name")
                if ex_name:
                    name = ex_name
                target_path = _find_pair_value(node, "path")
            elif table_name == "bench" and is_cargo:
                kind = "benchmark"
                bench_name = _find_pair_value(node, "name")
                if bench_name:
                    name = bench_name
                target_path = _find_pair_value(node, "path")

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            symbol_id = _make_toml_symbol_id(rel_path, start_line, end_line, name, kind)

            # Build meta with path if present
            meta = None
            if target_path:
                meta = {"path": target_path}

            symbols.append(
                Symbol(
                    id=symbol_id,
                    stable_id=_toml_stable_id(kind, name, rel_path),
                    shape_id=None,
                    # No canonical_name (name= carries it); the central post-pass stamps the fingerprint (ADR-0032).
                    kind=kind,
                    name=name,
                    path=rel_path,
                    language="toml",
                    span=Span(
                        start_line=start_line,
                        start_col=node.start_point[1],
                        end_line=end_line,
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta=meta,
                )
            )

            if is_pyproject:
                # ``[[tool.poetry.dependencies.foo]]``: one constraint of several.
                _extract_pyproject_dependencies(node, rel_path, symbols, declared_names)

            # Create edge from build target to source file.
            #
            # The edge declares the build_target_main pathway: a Cargo
            # [[bin]]/[[test]] target naming its source path is not a call
            # site, so Edge.create's ``ast_call_direct`` default would judge
            # it against the call-site band. No confidence is passed; the
            # ir.py chokepoint derives it from the pathway seed, since no
            # detection method is certain (INV-zatug).
            if target_path:
                edges.append(
                    Edge.create(
                        src=symbol_id,
                        # The target file's node id; the linker reads the
                        # path from meta["target_path"] (WI-bufar-pokam).
                        dst=make_manifest_target_dst(rel_path, target_path, "rust"),
                        edge_type="defines_target",
                        line=start_line,
                        evidence_type="build_target_main",
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        meta={"target_path": target_path},
                    )
                )


class TomlAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based TOML configuration analyzer.

    Uses tree-sitter-toml to parse Cargo.toml, pyproject.toml, and other
    TOML configuration files. Extracts tables, dependencies, build targets,
    workspaces, and script entry points.

    Overrides ``analyze`` because TOML uses a single-pass approach: both
    symbols and edges (e.g. build target source paths) are extracted together
    since file-type-specific processing (Cargo vs pyproject) needs to happen
    inline.
    """

    lang = "toml"
    file_patterns: ClassVar[list[str]] = ["*.toml"]
    grammar_module = "tree_sitter_toml"

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run TOML analysis with single-pass symbol+edge extraction.

        TOML analysis detects file type (Cargo.toml, pyproject.toml) per-file
        and extracts symbols and edges together in a single pass through
        ``_process_toml_tree``.
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

        symbols: list[Symbol] = []
        edges: list[Edge] = []
        files_analyzed = 0
        files_skipped = 0
        warnings_list: list[str] = []

        toml_files = list(set(find_toml_files(repo_root)))

        for toml_file in toml_files:
            if max_files is not None and files_analyzed >= max_files:
                break  # pragma: no cover

            try:
                content = toml_file.read_text(encoding="utf-8", errors="replace")
                tree = parser.parse(bytes(content, "utf-8"))
                files_analyzed += 1

                rel_path = str(toml_file.relative_to(repo_root))
                is_cargo = toml_file.name == "Cargo.toml"
                is_pyproject = toml_file.name == "pyproject.toml"

                _process_toml_tree(
                    tree.root_node, symbols, edges, rel_path, content,
                    is_cargo, is_pyproject,

                    run_id=run.execution_id,
                )

            except (OSError, IOError) as e:  # pragma: no cover
                files_skipped += 1  # pragma: no cover
                run.record_failed_file(str(toml_file.relative_to(repo_root)), f"{type(e).__name__}: {e}")  # pragma: no cover
                continue  # pragma: no cover

        run.files_analyzed = files_analyzed
        run.files_skipped = files_skipped
        run.duration_ms = int((_time.time() - start_time) * 1000)
        run.warnings = warnings_list

        return AnalysisResult(
            symbols=symbols,
            edges=edges,
            run=run,
        )


_analyzer = TomlAnalyzer()


def is_toml_tree_sitter_available() -> bool:
    """Check if tree-sitter-toml is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("toml")
def analyze_toml_files(root: Path) -> AnalysisResult:
    """Analyze TOML files in a directory.

    Args:
        root: Directory to analyze (can be a file path for single file)

    Returns:
        AnalysisResult containing symbols and edges
    """
    return _analyzer.analyze(root)


def _extract_cargo_dependencies(
    table_node: "tree_sitter.Node", rel_path: str, symbols: list[Symbol],
    content: str, scope: str | None = None, id_scope: str | None = None,
) -> None:
    """Extract dependencies from a Cargo.toml dependencies table.

    ``scope`` becomes ``meta["dependency_scope"]``, naming the table the
    entry lives in (``dev``, ``build``, or absent for production
    dependencies). Every Cargo dependency has kind="dependency", so the
    ``config-conventions.yaml`` rules tell dev and build dependencies apart
    by this meta value via ``meta_match`` (WI-limas).

    The stable_id is ``overload_arity.scoped_table_stable_id`` scoped by the
    table the entry lives in (INV-punig, scheme v9): ``scope`` for
    ``[dev-dependencies]`` / ``[build-dependencies]``, ``id_scope`` for a
    target table (``target.'cfg(windows)'``), so ``serde`` in two of them is
    two identities. A ``[dependencies]`` entry has neither and keeps the
    backstop's table value.
    """
    for child in table_node.children:
        if child.type == "pair":
            dep_name = None
            for pair_child in child.children:
                if pair_child.type in ("bare_key", "quoted_key"):
                    dep_name = _get_key_text(pair_child)
                    break

            if dep_name:
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                symbol_id = _make_toml_symbol_id(rel_path, start_line, end_line, dep_name, "dependency")

                meta = {"dependency_scope": scope} if scope else None
                table = scope or id_scope
                symbols.append(
                    Symbol(
                        id=symbol_id,
                        stable_id=scoped_table_stable_id(
                            kind="dependency", lang="toml", path=rel_path, name=dep_name,
                            scope=(table,) if table else (),
                        ),
                        shape_id=None,
                        # No canonical_name (name= carries it); the central post-pass stamps the fingerprint (ADR-0032).
                        kind="dependency",
                        name=dep_name,
                        path=rel_path,
                        language="toml",
                        span=Span(
                            start_line=start_line,
                            start_col=child.start_point[1],
                            end_line=end_line,
                            end_col=child.end_point[1],
                        ),
                        origin=PASS_ID,
                        meta=meta,
                    )
                )


def _parse_pyproject_dependency(dep_str: str) -> str:
    """Parse a PEP 508 dependency string and extract the package name.

    Examples:
        "requests>=2.0" -> "requests"
        "click" -> "click"
        "pytest[testing]>=7.0" -> "pytest"
        "numpy>=1.0,<2.0" -> "numpy"
    """
    import re
    # PEP 508: package name is alphanumeric, underscores, hyphens
    # followed by optional extras, version specifiers, etc.
    match = re.match(r"^([a-zA-Z0-9_-]+)", dep_str.strip())
    if match:
        return match.group(1)
    return dep_str.strip()  # pragma: no cover - fallback


#: The one pyproject table whose nodes keep the backstop's unscoped stable_id
#: and the name parse they had when it was the only table read (module
#: docstring, "pyproject dependency tables").
_UNSCOPED_PYPROJECT_TABLE = ("project", "dependencies")

#: One step of the way from a table header down to a pair: the length of the
#: key path once this node's keys are added, and the node.
_PathStep = tuple[int, "tree_sitter.Node"]

#: A pair (or a table header) of a pyproject: its key path from the document
#: root, its value node (None for a header), and the steps that lead to it.
_PyprojectEntry = tuple[
    tuple[str, ...], Optional["tree_sitter.Node"], tuple[_PathStep, ...],
]


@dataclass
class _DeclaredDependency:
    """One entry of a pyproject dependency table, before it becomes a node."""

    name: str
    node: "tree_sitter.Node"
    #: Key path of the declaring table (``project/optional-dependencies/docs``).
    table_path: tuple[str, ...]
    #: ``meta["dependency_scope"]``; None for a runtime dependency.
    scope: Optional[str]


def _iter_pyproject_entries(
    container: "tree_sitter.Node", path: tuple[str, ...], steps: tuple[_PathStep, ...],
) -> Iterator[_PyprojectEntry]:
    """Every pair under ``container`` as ``(key path, value node, steps)``.

    ``path`` is the key path of ``container`` itself. An inline table is
    walked into, so ``optional-dependencies = {docs = [...]}`` yields the pair
    and then ``docs`` under it, with the same key path the
    ``[project.optional-dependencies]`` spelling gives.
    """
    for child in container.children:
        if child.type != "pair":
            continue
        key_parts: list[str] = []
        value = None
        for pair_child in child.children:
            if pair_child.type in _KEY_NODE_TYPES:
                key_parts = _key_parts(pair_child)
            elif pair_child.is_named:
                value = pair_child
        full = (*path, *key_parts)
        here = (*steps, (len(full), child))
        yield full, value, here
        if value is not None and value.type == "inline_table":
            yield from _iter_pyproject_entries(value, full, here)


def _extract_pyproject_dependencies(
    container: "tree_sitter.Node", rel_path: str, symbols: list[Symbol],
    declared_names: set[tuple[tuple[str, ...], str]],
) -> None:
    """Emit one dependency node per entry a pyproject table declares.

    ``container`` is a ``table``, a ``table_array_element`` or the document
    (for the pairs above the first header). Its pairs are matched by key path
    against ``py_deps.PYPROJECT_DEPENDENCY_TABLES``:

    - a requirements table (an array of PEP 508 strings) emits a node per
      string element, spanning the element; an element that is not a string
      (``{include-group = "x"}``) or names no package is skipped;
    - a name-keyed table (Poetry) emits a node per key, spanning the node that
      introduces the key: the pair ``foo = ...``, or the table
      ``[tool.poetry.dependencies.foo]``. ``declared_names`` keeps the keys
      already emitted for this file, because one key can be reached more than
      once (``foo.version`` and ``foo.extras``; the elements of
      ``[[tool.poetry.dependencies.foo]]``). ``python`` is the interpreter
      constraint and is skipped.

    ``meta["dependency_scope"]`` and the stable_id's scope are described in
    the module docstring.
    """
    header = _header_key_parts(container)
    # The header is an entry too: ``[tool.poetry.dependencies.foo]`` names
    # ``foo`` before any of its pairs does (and when it has none).
    header_steps: tuple[_PathStep, ...] = ((len(header), container),)
    entries: list[_PyprojectEntry] = [
        (header, None, header_steps),
        *_iter_pyproject_entries(container, header, header_steps),
    ]

    declared: list[_DeclaredDependency] = []  # in file order
    by_name: dict[tuple[tuple[str, ...], str], _DeclaredDependency] = {}
    for path, value, steps in entries:
        table = next((t for t in PYPROJECT_DEPENDENCY_TABLES if t.match(path)), None)
        if table is None:
            continue
        depth = len(table.path)
        if table.shape == SHAPE_REQUIREMENTS:
            if len(path) != depth or value is None or value.type != "array":
                continue
            for elem in value.children:
                if elem.type != "string":
                    continue
                requirement = _get_string_value(elem)
                name = (
                    _parse_pyproject_dependency(requirement)
                    if path == _UNSCOPED_PYPROJECT_TABLE
                    else pep508_name(requirement)
                )
                if name is not None:
                    declared.append(_DeclaredDependency(name, elem, path, table.scope))
        elif len(path) > depth and not is_interpreter_constraint(path[depth]):
            key = (path[:depth], path[depth])
            entry = by_name.get(key)
            if entry is None and key not in declared_names:
                declared_names.add(key)
                node = next(n for length, n in steps if length > depth)
                entry = by_name[key] = _DeclaredDependency(
                    path[depth], node, path[:depth], table.scope)
                declared.append(entry)
            # Poetry: ``optional = true`` on a main-table entry makes it an
            # extra's dependency, whichever way the entry is spelled.
            if (
                entry is not None
                and table.scope is None
                and path[depth + 1:] == ("optional",)
                and value is not None
                and value.type == "boolean"
                and value.text == b"true"
            ):
                entry.scope = "optional"

    for dep in declared:
        name, node, table_path, scope = dep.name, dep.node, dep.table_path, dep.scope
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        symbols.append(
            Symbol(
                id=_make_toml_symbol_id(rel_path, start_line, end_line, name, "dependency"),
                stable_id=(
                    None  # the backstop's table value, unscoped
                    if table_path == _UNSCOPED_PYPROJECT_TABLE
                    else scoped_table_stable_id(
                        kind="dependency", lang="toml", path=rel_path, name=name,
                        scope=table_path,
                    )
                ),
                shape_id=None,
                # No canonical_name (name= carries it); the central post-pass stamps the fingerprint (ADR-0032).
                kind="dependency",
                name=name,
                path=rel_path,
                language="toml",
                span=Span(
                    start_line=start_line,
                    start_col=node.start_point[1],
                    end_line=end_line,
                    end_col=node.end_point[1],
                ),
                origin=PASS_ID,
                meta={"dependency_scope": scope} if scope else None,
            )
        )


def _python_entry_point_to_path(entry_point: str) -> tuple[str, str] | None:
    """Convert a Python entry point string to (file_path, function_name).

    Entry point format: ``module.path:function_name``
    e.g. ``mypackage.cli:main`` → ``("mypackage/cli.py", "main")``

    Returns None if the format is not recognized.
    """
    if ":" not in entry_point:
        return None
    module_part, func_name = entry_point.rsplit(":", 1)
    if not module_part or not func_name:
        return None
    file_path = module_part.replace(".", "/") + ".py"
    return file_path, func_name


def _extract_pyproject_scripts(
    table_node: "tree_sitter.Node", rel_path: str, symbols: list[Symbol],
    edges: list[Edge], content: str,
    *,
    run_id: str,
):
    """Extract CLI entry points from a pyproject.toml scripts table.

    Handles both ``[project.scripts]`` (console scripts) and
    ``[project.gui-scripts]`` (GUI scripts). Each entry like
    ``my-cli = "mypackage.cli:main"`` produces:

    1. A ``kind="file"`` symbol for the CLI command
    2. A ``defines_target`` edge pointing to the resolved file path
       (``mypackage/cli.py``), with ``target_function`` in meta so the
       build-target linker can find the specific function
    """
    for child in table_node.children:
        if child.type == "pair":
            script_name = None
            entry_point = None
            for pair_child in child.children:
                if pair_child.type in ("bare_key", "quoted_key"):
                    script_name = _get_key_text(pair_child)
                elif pair_child.type == "string":
                    entry_point = _get_string_value(pair_child)

            if script_name:
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                # INV-dulah: the id kind-slot carries the symbol's own kind, not
                # its role — the role's home is meta["entry_role"] below.
                symbol_id = _make_toml_symbol_id(rel_path, start_line, end_line, script_name, "file")

                meta: dict = {"entry_role": "script"}
                if entry_point:
                    meta["entry_point"] = entry_point

                symbols.append(
                    Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        # No canonical_name (name= carries it); the central post-pass stamps the fingerprint (ADR-0032).
                        kind="file",
                        name=script_name,
                        path=rel_path,
                        language="toml",
                        span=Span(
                            start_line=start_line,
                            start_col=child.start_point[1],
                            end_line=end_line,
                            end_col=child.end_point[1],
                        ),
                        origin=PASS_ID,
                        meta=meta,
                    )
                )

                # Create defines_target edge for build-target linker.
                # The dst is a well-formed Python id of the entry-point
                # function (lang:module:span:name:kind), so
                # ir.create_boundary_nodes synthesises a clean boundary
                # node instead of stuffing the path into the language
                # slot via _parse_dangling_id's len(parts)<5 fallback
                # (INV-nodij). The actual file path lives in
                # meta.target_path so the build-target linker can still
                # resolve via path lookup; meta.target_function carries
                # the entry-point function name.
                if entry_point:
                    parsed = _python_entry_point_to_path(entry_point)
                    if parsed:
                        target_path, target_func = parsed
                        module_dotted = entry_point.rsplit(":", 1)[0]
                        dst_id = f"python:{module_dotted}:0-0:{target_func}:unresolved"
                        # A [project.scripts] entry is a build target's main
                        # entry point, so the edge declares build_target_main
                        # rather than the ast_call_direct default. Confidence
                        # derives at the ir.py chokepoint (INV-zatug).
                        edges.append(
                            Edge.create(
                                src=symbol_id,
                                dst=dst_id,
                                edge_type="defines_target",
                                line=start_line,
                                evidence_type="build_target_main",
                                origin=PASS_ID,
                                meta={
                                    "target_function": target_func,
                                    "target_path": target_path,
                                },
                                origin_run_id=run_id,
                            )
                        )
