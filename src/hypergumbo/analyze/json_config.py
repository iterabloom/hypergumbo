"""JSON configuration analysis pass using tree-sitter-json.

This analyzer parses JSON configuration files and extracts:
- package.json: dependencies, devDependencies, scripts, workspaces
- tsconfig.json: compiler references, paths
- composer.json: PHP dependencies
- Various tool configs (.eslintrc.json, .prettierrc, etc.)

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
import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "json-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# JSON file extensions
JSON_EXTENSIONS = ["*.json"]

# Known configuration files
PACKAGE_JSON = "package.json"
TSCONFIG_FILES = {"tsconfig.json", "tsconfig.base.json", "tsconfig.build.json"}
COMPOSER_JSON = "composer.json"


def find_json_files(repo_root: Path) -> Iterator[Path]:
    """Yield all JSON files in the repository."""
    yield from find_files(repo_root, JSON_EXTENSIONS)


def is_json_tree_sitter_available() -> bool:
    """Check if tree-sitter with JSON grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    # Try tree_sitter_language_pack first (bundled languages)
    if importlib.util.find_spec("tree_sitter_language_pack") is not None:
        try:
            from tree_sitter_language_pack import get_language

            get_language("json")
            return True
        except Exception:  # pragma: no cover
            pass  # pragma: no cover
    # Fall back to standalone tree_sitter_json
    if importlib.util.find_spec("tree_sitter_json") is not None:  # pragma: no cover
        return True  # pragma: no cover
    return False  # pragma: no cover


@dataclass
class JSONAnalysisResult:
    """Result of analyzing JSON files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"json:{path}:{start_line}-{end_line}:{name}:{kind}"


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
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, pkg_name, dep_type)

                meta: dict = {"package": pkg_name}
                if version:
                    meta["version"] = version

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=pkg_name,
                    fingerprint=hashlib.sha256(source[child.start_byte:child.end_byte]).hexdigest()[:16],
                    kind=dep_type,
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
                    edge = Edge(
                        id=_make_edge_id(project_id, symbol_id, "depends_on"),
                        src=project_id,
                        dst=symbol_id,
                        edge_type="depends_on",
                        line=start_line,
                        confidence=0.95,
                        origin=PASS_ID,
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
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, script_name, "script")

                meta: dict = {"script_name": script_name}
                if command:
                    meta["command"] = command

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=f"npm run {script_name}",
                    fingerprint=hashlib.sha256(source[child.start_byte:child.end_byte]).hexdigest()[:16],
                    kind="script",
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


def _process_package_json(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
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
            canonical_name=pkg_name,
            fingerprint=hashlib.sha256(source[obj_node.start_byte:obj_node.end_byte]).hexdigest()[:16],
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
        _process_dependencies(deps_node, source, rel_path, symbols, edges, project_id, "dependency")

    # Process devDependencies
    dev_deps_node = _find_object_key(obj_node, source, "devDependencies")
    if dev_deps_node:
        _process_dependencies(dev_deps_node, source, rel_path, symbols, edges, project_id, "devDependency")

    # Process scripts
    scripts_node = _find_object_key(obj_node, source, "scripts")
    if scripts_node:
        _process_scripts(scripts_node, source, rel_path, symbols)


def _process_tsconfig(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
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

    # Create tsconfig symbol
    config_id = _make_symbol_id(rel_path, start_line, end_line, rel_path, "tsconfig")

    sym = Symbol(
        id=config_id,
        stable_id=None,
        shape_id=None,
        canonical_name=rel_path,
        fingerprint=hashlib.sha256(source[obj_node.start_byte:obj_node.end_byte]).hexdigest()[:16],
        kind="tsconfig",
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
    )
    symbols.append(sym)

    # Process references (project references)
    refs_node = _find_object_key(obj_node, source, "references")
    if refs_node and refs_node.type == "array":
        for child in refs_node.children:
            if child.type == "object":
                path_node = _find_object_key(child, source, "path")
                if path_node:
                    ref_path = _get_string_content(path_node, source)
                    if ref_path:
                        ref_start = child.start_point[0] + 1
                        ref_end = child.end_point[0] + 1
                        ref_id = _make_symbol_id(rel_path, ref_start, ref_end, ref_path, "reference")

                        ref_sym = Symbol(
                            id=ref_id,
                            stable_id=None,
                            shape_id=None,
                            canonical_name=ref_path,
                            fingerprint=hashlib.sha256(source[child.start_byte:child.end_byte]).hexdigest()[:16],
                            kind="reference",
                            name=ref_path,
                            path=rel_path,
                            language="json",
                            span=Span(
                                start_line=ref_start,
                                end_line=ref_end,
                                start_col=child.start_point[1],
                                end_col=child.end_point[1],
                            ),
                            origin=PASS_ID,
                            meta={"reference_path": ref_path},
                        )
                        symbols.append(ref_sym)

                        # Create reference edge
                        edge = Edge(
                            id=_make_edge_id(config_id, ref_id, "references"),
                            src=config_id,
                            dst=ref_id,
                            edge_type="references",
                            line=ref_start,
                            confidence=0.95,
                            origin=PASS_ID,
                            evidence_type="static",
                        )
                        edges.append(edge)


def _process_composer_json(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
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
        project_id = _make_symbol_id(rel_path, start_line, end_line, pkg_name, "composer_package")

        sym = Symbol(
            id=project_id,
            stable_id=None,
            shape_id=None,
            canonical_name=pkg_name,
            fingerprint=hashlib.sha256(source[obj_node.start_byte:obj_node.end_byte]).hexdigest()[:16],
            kind="composer_package",
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
        )
        symbols.append(sym)

    # Process require (production dependencies)
    require_node = _find_object_key(obj_node, source, "require")
    if require_node:
        _process_dependencies(require_node, source, rel_path, symbols, edges, project_id, "dependency")

    # Process require-dev (dev dependencies)
    require_dev_node = _find_object_key(obj_node, source, "require-dev")
    if require_dev_node:
        _process_dependencies(require_dev_node, source, rel_path, symbols, edges, project_id, "devDependency")


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


def analyze_json_files(repo_root: Path) -> JSONAnalysisResult:
    """Analyze JSON files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        JSONAnalysisResult with symbols and edges
    """
    if not is_json_tree_sitter_available():  # pragma: no cover
        return JSONAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-json not installed (pip install tree-sitter-json or tree-sitter-language-pack)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

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
        return JSONAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

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
                _process_package_json(tree.root_node, source, rel_path, symbols, edges)
            elif json_type == "tsconfig":
                _process_tsconfig(tree.root_node, source, rel_path, symbols, edges)
            elif json_type == "composer":
                _process_composer_json(tree.root_node, source, rel_path, symbols, edges)
            # Generic JSON files are not extracted (too noisy)

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {json_path}: {e}")  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return JSONAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )


# Convenience alias
analyze_json = analyze_json_files
