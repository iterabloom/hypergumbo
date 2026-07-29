# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSON configuration analysis pass using tree-sitter-json.

This analyzer parses JSON configuration files and extracts:
- package.json: dependencies, devDependencies, scripts, ``bin``,
  ``main``, and ``exports`` entry points (emitted as
  ``defines_target`` edges)
- tsconfig.json: project ``references``
- composer.json: PHP dependencies

Generic JSON files (tool configs like ``.eslintrc.json``,
``.prettierrc``, etc.) are intentionally not extracted — too noisy.

If tree-sitter-json is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-json is available (via language pack or standalone)
2. If not available, return skipped result (not an error)
3. Detect JSON file type (package.json, tsconfig, composer, etc.)
4. Parse and extract type-specific information
5. Create symbols for dependencies, scripts, configurations
6. Create edges for dependency relationships

Why This Design
---------------
- Optional dependency keeps base install lightweight
- package.json: Extract npm/yarn dependency graph
- tsconfig.json: Extract TypeScript project references
- composer.json: Extract PHP Composer dependencies
- Useful for frontend/Node.js and PHP ecosystem analysis
"""
from __future__ import annotations

import hashlib
import time
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult, TreeSitterAnalyzer, make_symbol_id
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("json")

# JSON file extensions
JSON_EXTENSIONS = ["*.json"]

# Known configuration files
PACKAGE_JSON = "package.json"
TSCONFIG_FILES = {"tsconfig.json", "tsconfig.base.json", "tsconfig.build.json"}
COMPOSER_JSON = "composer.json"


def find_json_files(repo_root: Path) -> Iterator[Path]:
    """Yield all JSON files in the repository."""
    yield from find_files(repo_root, JSON_EXTENSIONS)


class JsonTreeSitterAnalyzer(TreeSitterAnalyzer):
    """TreeSitterAnalyzer wrapper for JSON configuration files.

    Overrides ``analyze()`` entirely because JSON analysis uses custom
    type detection (package.json, tsconfig, composer) and file-specific
    processing. The base class provides grammar availability checking
    via ``_check_grammar_available()``.
    """

    lang = "json"
    file_patterns: ClassVar[list[str]] = ["*.json"]
    language_pack_name = "json"

    def analyze(self, repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        """Run the JSON analysis using the existing analyze logic."""
        return _analyze_json_impl(repo_root)


_json_analyzer = JsonTreeSitterAnalyzer()


def is_json_tree_sitter_available() -> bool:
    """Check if tree-sitter with JSON grammar is available."""
    return _json_analyzer._check_grammar_available()


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate a location-based ID via the shared ADR-0036 minter.

    Delegates rather than re-implementing the grammar as an f-string: this
    module was the last of the three manifest analyzers holding its own copy,
    which meant it silently opted out of the minter's name-slot sanitization
    (INV-dulah).
    """
    return make_symbol_id("json", path, start_line, end_line, name, kind)


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_string_content(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Get content from a string node (without quotes)."""
    if node.type == "string":
        for child in node.children:
            if child.type == "string_content":
                return _node_text(child, source)
        # Fallback: strip quotes manually
        text = _node_text(node, source)  # pragma: no cover
        if text.startswith('"') and text.endswith('"'):  # pragma: no cover
            return text[1:-1]  # pragma: no cover
    return None  # pragma: no cover


def _get_pair_key(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Get the key from a pair node."""
    if node.type == "pair":
        for child in node.children:
            if child.type == "string":
                return _get_string_content(child, source)
    return None  # pragma: no cover


def _get_pair_value(node: "tree_sitter.Node") -> Optional["tree_sitter.Node"]:
    """Get the value node from a pair node."""
    if node.type == "pair":
        found_colon = False
        for child in node.children:
            if child.type == ":":
                found_colon = True
            elif found_colon:
                return child
    return None  # pragma: no cover


def _find_object_key(
    obj_node: "tree_sitter.Node", source: bytes, key_name: str
) -> Optional["tree_sitter.Node"]:
    """Find a key-value pair in an object node."""
    if obj_node.type != "object":
        return None  # pragma: no cover
    for child in obj_node.children:
        if child.type == "pair":
            key = _get_pair_key(child, source)
            if key == key_name:
                return _get_pair_value(child)
    return None


def _process_dependencies(
    deps_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    project_id: Optional[str],
    run_id: str,
    dep_type: str = "dependency",
) -> None:
    """Extract npm dependencies from a dependencies object."""
    if deps_node.type != "object":
        return  # pragma: no cover

    for child in deps_node.children:
        if child.type == "pair":
            pkg_name = _get_pair_key(child, source)
            version_node = _get_pair_value(child)
            version = None
            if version_node:
                version = _get_string_content(version_node, source)

            if pkg_name:
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1

                meta: dict = {"package": pkg_name}
                if version:
                    meta["version"] = version
                # Wave 6 PR 5 fold per audit-findings 0006: devDependency
                # → kind="dependency" + meta["dependency_scope"]="dev".
                # Phase 6 PR6 (ADR-0034 id_format closure): the ID's kind
                # segment now uses the post-fold ``kind_value`` so the
                # canonical 5-segment shape uses lowercase identifiers
                # only (``devDependency`` violates the kind-segment regex
                # ``^[a-z][a-z0-9_]*$``).
                if dep_type == "devDependency":
                    meta["dependency_scope"] = "dev"
                kind_value = "dependency" if dep_type == "devDependency" else dep_type

                symbol_id = _make_symbol_id(rel_path, start_line, end_line, pkg_name, kind_value)

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    # ADR-0032: canonical_name dropped (was redundant with name=); fingerprint stamped by central post-pass.
                    kind=kind_value,
                    name=pkg_name,
                    path=rel_path,
                    language="json",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=child.start_point[1],
                        end_col=child.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta=meta,
                )
                symbols.append(sym)

                # Create dependency edge
                if project_id:
                    edge = Edge.create(
                        src=project_id,
                        dst=symbol_id,
                        edge_type="depends_on",
                        line=start_line,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        evidence_type="static",
                    )
                    edges.append(edge)


def _process_scripts(
    scripts_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
) -> None:
    """Extract npm scripts from a scripts object."""
    if scripts_node.type != "object":
        return  # pragma: no cover

    for child in scripts_node.children:
        if child.type == "pair":
            script_name = _get_pair_key(child, source)
            command_node = _get_pair_value(child)
            command = None
            if command_node:
                command = _get_string_content(command_node, source)

            if script_name:
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                # INV-dulah: the id kind-slot carries the symbol's own kind, not
                # its role — the role's home is meta["entry_role"] below.
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, script_name, "file")

                meta: dict = {
                    "script_name": script_name,
                    "entry_role": "script",
                }
                if command:
                    meta["command"] = command

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    display_label=f"npm run {script_name}",
                    # ADR-0032: fingerprint stamped by central post-pass.
                    kind="file",
                    name=script_name,
                    path=rel_path,
                    language="json",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=child.start_point[1],
                        end_col=child.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta=meta,
                )
                symbols.append(sym)


def _process_bin(
    bin_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    pkg_name: Optional[str],
    run_id: str,
    edges: Optional[list[Edge]] = None,
) -> None:
    """Extract npm bin entries from a bin object or string.

    The "bin" field can be:
    - String: "./cli.js" (uses package name as command)
    - Object: {"my-cli": "./bin/cli.js", ...}

    These define CLI entry points - executables that npm installs globally.
    Each bin entry also creates a ``defines_target`` edge pointing to the
    target file path, mirroring Cargo ``[[bin]]`` behavior.
    """
    if bin_node.type == "string":
        # String form: single binary using package name
        bin_path = _get_string_content(bin_node, source)
        if bin_path and pkg_name:
            start_line = bin_node.start_point[0] + 1
            end_line = bin_node.end_point[0] + 1
            symbol_id = _make_symbol_id(rel_path, start_line, end_line, pkg_name, "bin")

            sym = Symbol(
                id=symbol_id,
                stable_id=None,
                shape_id=None,
                # ADR-0032: canonical_name dropped (was redundant with name=); fingerprint stamped by central post-pass.
                kind="bin",
                name=pkg_name,
                path=rel_path,
                language="json",
                span=Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=bin_node.start_point[1],
                    end_col=bin_node.end_point[1],
                ),
                origin=PASS_ID,
                meta={"path": bin_path},
            )
            symbols.append(sym)
            if edges is not None:
                target = bin_path.lstrip("./")
                edges.append(
                    Edge.create(
                        src=symbol_id,
                        dst=target,
                        edge_type="defines_target",
                        line=start_line,
                        confidence=1.0,
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                )
        return

    if bin_node.type != "object":
        return  # pragma: no cover - unexpected type

    # Object form: multiple binaries
    for child in bin_node.children:
        if child.type == "pair":
            bin_name = _get_pair_key(child, source)
            path_node = _get_pair_value(child)
            bin_path = None
            if path_node:
                bin_path = _get_string_content(path_node, source)

            if bin_name:
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, bin_name, "bin")

                meta: dict = {}
                if bin_path:
                    meta["path"] = bin_path

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    # ADR-0032: canonical_name dropped (was redundant with name=); fingerprint stamped by central post-pass.
                    kind="bin",
                    name=bin_name,
                    path=rel_path,
                    language="json",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=child.start_point[1],
                        end_col=child.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta=meta,
                )
                symbols.append(sym)
                if edges is not None and bin_path:
                    target = bin_path.lstrip("./")
                    edges.append(
                        Edge.create(
                            src=symbol_id,
                            dst=target,
                            edge_type="defines_target",
                            line=start_line,
                            confidence=1.0,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        )
                    )


def _process_main_entry(
    main_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    pkg_name: Optional[str],
    run_id: str,
) -> None:
    """Extract the package.json "main" field as a defines_target edge.

    The "main" field specifies the primary entry point for Node.js modules
    and Electron apps (e.g., ``"main": "./src/main.js"``). This creates a
    ``main_entry`` symbol and a ``defines_target`` edge to the target file,
    enabling the build-target linker to connect it to the module's main()
    function or module symbol.
    """
    if main_node.type != "string":
        return  # pragma: no cover - main should always be a string

    main_path = _get_string_content(main_node, source)
    if not main_path:
        return  # pragma: no cover - empty string

    entry_name = pkg_name or "main"
    start_line = main_node.start_point[0] + 1
    end_line = main_node.end_point[0] + 1
    # INV-dulah: the id kind-slot carries the symbol's own kind, not its role —
    # the role's home is meta["entry_role"] = "main" below.
    symbol_id = _make_symbol_id(rel_path, start_line, end_line, entry_name, "file")

    sym = Symbol(
        id=symbol_id,
        stable_id=None,
        shape_id=None,
        # ADR-0032: canonical_name dropped (was redundant with name=); fingerprint stamped by central post-pass.
        kind="file",
        name=entry_name,
        path=rel_path,
        language="json",
        span=Span(
            start_line=start_line,
            end_line=end_line,
            start_col=main_node.start_point[1],
            end_col=main_node.end_point[1],
        ),
        origin=PASS_ID,
        meta={"path": main_path, "entry_role": "main"},
    )
    symbols.append(sym)

    target = main_path.lstrip("./")
    edges.append(
        Edge.create(
            src=symbol_id,
            dst=target,
            edge_type="defines_target",
            line=start_line,
            confidence=1.0,
            origin=PASS_ID,
            origin_run_id=run_id,
        )
    )


# Priority order for resolving conditional export paths.
# "source" is preferred (points to unbuilt source), then "import" (ESM),
# then "require" (CJS), then "default" (fallback).
_EXPORT_CONDITION_PRIORITY = ("source", "import", "require", "default")


def _resolve_conditional_export(
    value_node: "tree_sitter.Node", source: bytes,
) -> Optional[str]:
    """Resolve a conditional export value to a single file path.

    Conditional exports look like:
    {
      "types": "./dist/types/index.d.ts",
      "import": "./dist/esm/index.js",
      "require": "./dist/cjs/index.js"
    }

    We pick the first available condition from _EXPORT_CONDITION_PRIORITY.
    Falls back to first non-types string value if no priority condition matches.
    """
    if value_node.type == "string":
        return _get_string_content(value_node, source)

    if value_node.type != "object":
        return None  # pragma: no cover

    # Build a map of condition -> path
    conditions: dict[str, str] = {}
    first_path: Optional[str] = None
    for child in value_node.children:
        if child.type == "pair":
            key = _get_pair_key(child, source)
            val = _get_pair_value(child)
            if key and val:
                path = _get_string_content(val, source)
                if path:
                    conditions[key] = path
                    if first_path is None and key != "types":
                        first_path = path

    # Pick by priority
    for cond in _EXPORT_CONDITION_PRIORITY:
        if cond in conditions:
            return conditions[cond]

    return first_path


def _process_exports(
    exports_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    pkg_name: Optional[str],
    run_id: str,
) -> None:
    """Extract package.json "exports" field as defines_target edges.

    The "exports" field maps subpath patterns to source files, providing
    entry points for library consumers. It can be:
    - String: "./src/index.js" (single main export)
    - Object with subpath keys: {".": "./src/index.js", "./sync": "./src/sync.js"}
    - Object with conditional values: {".": {"import": "./dist/esm.js", "require": "./dist/cjs.js"}}

    Each resolved export path creates an ``export_entry`` symbol and a
    ``defines_target`` edge to the target file.
    """
    if exports_node.type == "string":
        # Simple string form: single main export
        export_path = _get_string_content(exports_node, source)
        if export_path:
            entry_name = "."
            start_line = exports_node.start_point[0] + 1
            end_line = exports_node.end_point[0] + 1
            symbol_id = _make_symbol_id(
                rel_path, start_line, end_line, entry_name, "export_entry",
            )
            sym = Symbol(
                id=symbol_id,
                stable_id=None,
                shape_id=None,
                qualified_name=f"{pkg_name or 'pkg'}:{entry_name}",
                # ADR-0032: fingerprint stamped by central post-pass.
                kind="export",
                name=entry_name,
                path=rel_path,
                language="json",
                span=Span(
                    start_line=start_line,
                    end_line=end_line,
                    start_col=exports_node.start_point[1],
                    end_col=exports_node.end_point[1],
                ),
                origin=PASS_ID,
                meta={
                    "path": export_path,
                    "subpath": entry_name,
                    "export_source": "package_exports_map",
                },
            )
            symbols.append(sym)
            target = export_path.lstrip("./")
            edges.append(
                Edge.create(
                    src=symbol_id,
                    dst=target,
                    edge_type="defines_target",
                    line=start_line,
                    confidence=1.0,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
            )
        return

    if exports_node.type != "object":
        return  # pragma: no cover

    # Object form: iterate subpath keys
    for child in exports_node.children:
        if child.type != "pair":
            continue
        subpath = _get_pair_key(child, source)
        value_node = _get_pair_value(child)
        if not subpath or not value_node:
            continue  # pragma: no cover

        # Resolve the export target (handles both string and conditional)
        export_path = _resolve_conditional_export(value_node, source)
        if not export_path:
            continue  # pragma: no cover

        start_line = child.start_point[0] + 1
        end_line = child.end_point[0] + 1
        symbol_id = _make_symbol_id(
            rel_path, start_line, end_line, subpath, "export_entry",
        )
        sym = Symbol(
            id=symbol_id,
            stable_id=None,
            shape_id=None,
            qualified_name=f"{pkg_name or 'pkg'}:{subpath}",
            # ADR-0032: fingerprint stamped by central post-pass.
            kind="export",
            name=subpath,
            path=rel_path,
            language="json",
            span=Span(
                start_line=start_line,
                end_line=end_line,
                start_col=child.start_point[1],
                end_col=child.end_point[1],
            ),
            origin=PASS_ID,
            meta={
                "path": export_path,
                "subpath": subpath,
                "export_source": "package_exports_map",
            },
        )
        symbols.append(sym)
        target = export_path.lstrip("./")
        edges.append(
            Edge.create(
                src=symbol_id,
                dst=target,
                edge_type="defines_target",
                line=start_line,
                confidence=1.0,
                origin=PASS_ID,
                origin_run_id=run_id,
            )
        )


def _process_package_json(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    run_id: str,
) -> None:
    """Process package.json file."""
    # Find the root object
    obj_node = None
    for child in root.children:
        if child.type == "object":
            obj_node = child
            break

    if not obj_node:
        return  # pragma: no cover - invalid JSON

    # Get package name and version
    name_node = _find_object_key(obj_node, source, "name")
    pkg_name = None
    if name_node:
        pkg_name = _get_string_content(name_node, source)

    version_node = _find_object_key(obj_node, source, "version")
    pkg_version = None
    if version_node:
        pkg_version = _get_string_content(version_node, source)

    # Create project symbol
    project_id = None
    if pkg_name:
        start_line = obj_node.start_point[0] + 1
        end_line = obj_node.end_point[0] + 1
        project_id = _make_symbol_id(rel_path, start_line, end_line, pkg_name, "package")

        meta = {"name": pkg_name}
        if pkg_version:
            meta["version"] = pkg_version

        sym = Symbol(
            id=project_id,
            stable_id=None,
            shape_id=None,
            # ADR-0032: canonical_name dropped (was redundant with name=); fingerprint stamped by central post-pass.
            kind="package",
            name=pkg_name,
            path=rel_path,
            language="json",
            span=Span(
                start_line=start_line,
                end_line=end_line,
                start_col=obj_node.start_point[1],
                end_col=obj_node.end_point[1],
            ),
            origin=PASS_ID,
            meta=meta,
        )
        symbols.append(sym)

    # Process dependencies
    deps_node = _find_object_key(obj_node, source, "dependencies")
    if deps_node:
        _process_dependencies(deps_node, source, rel_path, symbols, edges, project_id, run_id, "dependency")

    # Process devDependencies
    dev_deps_node = _find_object_key(obj_node, source, "devDependencies")
    if dev_deps_node:
        _process_dependencies(dev_deps_node, source, rel_path, symbols, edges, project_id, run_id, "devDependency")

    # Process scripts
    scripts_node = _find_object_key(obj_node, source, "scripts")
    if scripts_node:
        _process_scripts(scripts_node, source, rel_path, symbols)

    # Process bin entries (CLI executables)
    bin_node = _find_object_key(obj_node, source, "bin")
    if bin_node:
        _process_bin(bin_node, source, rel_path, symbols, pkg_name, run_id, edges)

    # Process main entry (Node.js/Electron app entry point)
    main_node = _find_object_key(obj_node, source, "main")
    if main_node:
        _process_main_entry(main_node, source, rel_path, symbols, edges, pkg_name, run_id)

    # Process exports (library entry points)
    exports_node = _find_object_key(obj_node, source, "exports")
    if exports_node:
        _process_exports(exports_node, source, rel_path, symbols, edges, pkg_name, run_id)


def _process_tsconfig(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    run_id: str,
) -> None:
    """Process tsconfig.json file."""
    # Find the root object
    obj_node = None
    for child in root.children:
        if child.type == "object":
            obj_node = child
            break

    if not obj_node:
        return  # pragma: no cover - invalid JSON

    start_line = obj_node.start_point[0] + 1
    end_line = obj_node.end_point[0] + 1

    # Create tsconfig symbol. Wave 6 PR 3 DEPRECATE-NO-FOLD per
    # audit-findings 0005: drop the kind="tsconfig" specialisation;
    # consumer queries is_config_file=True + meta["config_format"]=
    # "tsconfig" instead. ADR-0036 Ruling 2 completes the fold: the id
    # kind-slot is the Symbol's own kind ("file"), not the "tsconfig"
    # fold-source (which survives only on meta["config_format"]).
    config_id = _make_symbol_id(rel_path, start_line, end_line, rel_path, "file")

    sym = Symbol(
        id=config_id,
        stable_id=None,
        shape_id=None,
        display_label=rel_path,
        # ADR-0032: fingerprint stamped by central post-pass.
        kind="file",
        is_config_file=True,
        name=Path(rel_path).name,
        path=rel_path,
        language="json",
        span=Span(
            start_line=start_line,
            end_line=end_line,
            start_col=obj_node.start_point[1],
            end_col=obj_node.end_point[1],
        ),
        origin=PASS_ID,
        meta={"config_format": "tsconfig"},
    )
    symbols.append(sym)

    # Process references (project references)
    # Cluster E sub-case (b) per audit-findings 0010: per-reference Symbol
    # was redundant with the references Edge. Edge dst is now the referenced
    # tsconfig file id (5-part well-formed dangling id; IR's boundary
    # materialization handles it).
    refs_node = _find_object_key(obj_node, source, "references")
    if refs_node and refs_node.type == "array":
        for child in refs_node.children:
            if child.type == "object":
                path_node = _find_object_key(child, source, "path")
                if path_node:
                    ref_path = _get_string_content(path_node, source)
                    if ref_path:
                        ref_start = child.start_point[0] + 1
                        ref_dst = f"json:{ref_path}:1-1:{Path(ref_path).name}:tsconfig"
                        edge = Edge.create(
                            src=config_id,
                            dst=ref_dst,
                            edge_type="references",
                            line=ref_start,
                            origin=PASS_ID,
                            origin_run_id=run_id,
                            evidence_type="static",
                            meta={"reference_path": ref_path},
                        )
                        edges.append(edge)


def _process_composer_json(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    run_id: str,
) -> None:
    """Process composer.json file."""
    # Find the root object
    obj_node = None
    for child in root.children:
        if child.type == "object":
            obj_node = child
            break

    if not obj_node:
        return  # pragma: no cover - invalid JSON

    # Get package name
    name_node = _find_object_key(obj_node, source, "name")
    pkg_name = None
    if name_node:
        pkg_name = _get_string_content(name_node, source)

    # Create project symbol
    project_id = None
    if pkg_name:
        start_line = obj_node.start_point[0] + 1
        end_line = obj_node.end_point[0] + 1
        # INV-dulah: the id kind-slot carries the symbol's own kind, not its
        # ecosystem — that lives in meta["package_ecosystem"] = "composer".
        project_id = _make_symbol_id(rel_path, start_line, end_line, pkg_name, "package")

        sym = Symbol(
            id=project_id,
            stable_id=None,
            shape_id=None,
            # ADR-0032: canonical_name dropped (was redundant with name=); fingerprint stamped by central post-pass.
            kind="package",
            name=pkg_name,
            path=rel_path,
            language="json",
            span=Span(
                start_line=start_line,
                end_line=end_line,
                start_col=obj_node.start_point[1],
                end_col=obj_node.end_point[1],
            ),
            origin=PASS_ID,
            meta={"package_ecosystem": "composer"},
        )
        symbols.append(sym)

    # Process require (production dependencies)
    require_node = _find_object_key(obj_node, source, "require")
    if require_node:
        _process_dependencies(require_node, source, rel_path, symbols, edges, project_id, run_id, "dependency")

    # Process require-dev (dev dependencies)
    require_dev_node = _find_object_key(obj_node, source, "require-dev")
    if require_dev_node:
        _process_dependencies(require_dev_node, source, rel_path, symbols, edges, project_id, run_id, "devDependency")


def _detect_json_type(path: Path) -> str:
    """Detect the type of JSON file."""
    filename = path.name
    if filename == PACKAGE_JSON:
        return "package_json"
    if filename in TSCONFIG_FILES or filename.startswith("tsconfig."):
        return "tsconfig"
    if filename == COMPOSER_JSON:
        return "composer"
    return "generic"


@register_analyzer("json")
def analyze_json_files(repo_root: Path) -> AnalysisResult:
    """Analyze JSON files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult with symbols and edges
    """
    return _json_analyzer.analyze(repo_root)


def _analyze_json_impl(repo_root: Path) -> AnalysisResult:
    """Internal implementation of JSON analysis.

    Called by JsonTreeSitterAnalyzer.analyze() after grammar availability
    has been checked by the base class.
    """
    if not _json_analyzer._check_grammar_available():  # pragma: no cover
        return AnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="json tree-sitter grammar not available",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []
    failed_files_list: list[dict[str, str]] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Create parser - try language pack first, then standalone
    try:
        try:
            from tree_sitter_language_pack import get_language

            json_lang = get_language("json")
            parser = tree_sitter.Parser(json_lang)
        except Exception:  # pragma: no cover - language pack available
            import tree_sitter_json  # pragma: no cover

            parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_json.language()))  # pragma: no cover
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize JSON parser: {e}")
        return AnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    # Create run before processing so Edge construction can stamp
    # origin_run_id (WI-higap: enforced by Edge.__post_init__).
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    json_files = list(find_json_files(repo_root))

    for json_path in json_files:
        try:
            rel_path = str(json_path.relative_to(repo_root))
            source = json_path.read_bytes()

            # Detect JSON type
            json_type = _detect_json_type(json_path)

            tree = parser.parse(source)
            files_analyzed += 1

            # Process based on type
            if json_type == "package_json":
                _process_package_json(tree.root_node, source, rel_path, symbols, edges, run.execution_id)
            elif json_type == "tsconfig":
                _process_tsconfig(tree.root_node, source, rel_path, symbols, edges, run.execution_id)
            elif json_type == "composer":
                _process_composer_json(tree.root_node, source, rel_path, symbols, edges, run.execution_id)
            # Generic JSON files are not extracted (too noisy)

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {json_path}: {e}")  # pragma: no cover
            failed_files_list.append({"path": str(json_path.relative_to(repo_root)), "reason": f"{type(e).__name__}: {e}"})  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list
    run.failed_files = failed_files_list

    return AnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )


# Convenience alias
analyze_json = analyze_json_files
