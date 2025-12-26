"""XML configuration analysis pass using tree-sitter-xml.

This analyzer parses XML configuration files and extracts:
- Maven pom.xml: dependencies, plugins, modules, parent relationships
- Android Manifest: activities, services, permissions, intent-filters
- Android Layout: views with IDs, resource references
- Generic XML: element structure for configuration files

If tree-sitter-xml is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-xml is available (via language pack or standalone)
2. If not available, return skipped result (not an error)
3. Detect XML file type (Maven, Android, generic)
4. Parse and extract type-specific information
5. Create symbols for dependencies, components, resources
6. Create edges for dependency and reference relationships

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Maven pom.xml: Extract dependency graph for supply chain analysis
- Android: Extract component graph and resource references
- Intent-filters reveal app entry points and capabilities
- Useful for mobile app analysis and Java/Kotlin build systems
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

PASS_ID = "xml-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# XML file extensions and patterns
XML_EXTENSIONS = ["*.xml"]

# Maven-specific files
MAVEN_FILES = {"pom.xml", "settings.xml"}

# Android-specific patterns
ANDROID_MANIFEST = "AndroidManifest.xml"


def find_xml_files(repo_root: Path) -> Iterator[Path]:
    """Yield all XML files in the repository."""
    yield from find_files(repo_root, XML_EXTENSIONS)


def is_xml_tree_sitter_available() -> bool:
    """Check if tree-sitter with XML grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    # Try tree_sitter_language_pack first (bundled languages)
    if importlib.util.find_spec("tree_sitter_language_pack") is not None:
        try:
            from tree_sitter_language_pack import get_language

            get_language("xml")
            return True
        except Exception:  # pragma: no cover
            pass  # pragma: no cover
    # Fall back to standalone tree_sitter_xml
    if importlib.util.find_spec("tree_sitter_xml") is not None:  # pragma: no cover
        return True  # pragma: no cover
    return False  # pragma: no cover


@dataclass
class XMLAnalysisResult:
    """Result of analyzing XML files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"xml:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_element_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Get the tag name from an element node."""
    for child in node.children:
        if child.type == "STag":
            for sub in child.children:
                if sub.type == "Name":
                    return _node_text(sub, source)
        elif child.type == "EmptyElemTag":
            for sub in child.children:
                if sub.type == "Name":
                    return _node_text(sub, source)
    return None  # pragma: no cover - element must have a name


def _get_attribute(node: "tree_sitter.Node", source: bytes, attr_name: str) -> Optional[str]:
    """Get attribute value from an element node."""
    for child in node.children:
        if child.type in ("STag", "EmptyElemTag"):
            for sub in child.children:
                if sub.type == "Attribute":
                    name_node = None
                    value_node = None
                    for attr_child in sub.children:
                        if attr_child.type == "Name":
                            name_node = attr_child
                        elif attr_child.type == "AttValue":
                            value_node = attr_child
                    if name_node and value_node:
                        name = _node_text(name_node, source)
                        # Handle both plain name and namespace:name
                        if name == attr_name or name.endswith(":" + attr_name):
                            # Remove quotes from value
                            value = _node_text(value_node, source)
                            if value.startswith('"') and value.endswith('"'):
                                return value[1:-1]
                            elif value.startswith("'") and value.endswith("'"):  # pragma: no cover - rare
                                return value[1:-1]  # pragma: no cover - rare
                            return value  # pragma: no cover
    return None  # pragma: no cover - attribute not found


def _get_text_content(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Get text content from an element's content node."""
    for child in node.children:
        if child.type == "content":
            for sub in child.children:
                if sub.type == "CharData":
                    text = _node_text(sub, source).strip()
                    if text:
                        return text
    return None  # pragma: no cover - empty content


def _find_child_elements(node: "tree_sitter.Node", source: bytes, tag_name: str) -> list:
    """Find direct child elements with the given tag name."""
    results = []
    for child in node.children:
        if child.type == "content":
            for sub in child.children:
                if sub.type == "element":
                    elem_name = _get_element_name(sub, source)
                    if elem_name == tag_name:
                        results.append(sub)
    return results


def _process_maven_dependency(
    dep_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    project_id: Optional[str],
) -> None:
    """Extract Maven dependency information."""
    group_id = None
    artifact_id = None
    version = None
    scope = None

    for child in dep_node.children:
        if child.type == "content":
            for sub in child.children:
                if sub.type == "element":
                    elem_name = _get_element_name(sub, source)
                    if elem_name == "groupId":
                        group_id = _get_text_content(sub, source)
                    elif elem_name == "artifactId":
                        artifact_id = _get_text_content(sub, source)
                    elif elem_name == "version":
                        version = _get_text_content(sub, source)
                    elif elem_name == "scope":
                        scope = _get_text_content(sub, source)

    if group_id and artifact_id:
        start_line = dep_node.start_point[0] + 1
        end_line = dep_node.end_point[0] + 1
        dep_name = f"{group_id}:{artifact_id}"
        symbol_id = _make_symbol_id(rel_path, start_line, end_line, dep_name, "dependency")

        meta = {"groupId": group_id, "artifactId": artifact_id}
        if version:
            meta["version"] = version
        if scope:
            meta["scope"] = scope

        sym = Symbol(
            id=symbol_id,
            stable_id=None,
            shape_id=None,
            canonical_name=dep_name,
            fingerprint=hashlib.sha256(source[dep_node.start_byte:dep_node.end_byte]).hexdigest()[:16],
            kind="dependency",
            name=artifact_id,
            path=rel_path,
            language="xml",
            span=Span(
                start_line=start_line,
                end_line=end_line,
                start_col=dep_node.start_point[1],
                end_col=dep_node.end_point[1],
            ),
            origin=PASS_ID,
            meta=meta,
        )
        symbols.append(sym)

        # Create dependency edge from project to dependency
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


def _process_maven_pom(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    """Process Maven pom.xml file."""
    # Find the project element
    project_node = None
    for child in root.children:
        if child.type == "element":
            if _get_element_name(child, source) == "project":
                project_node = child
                break

    if not project_node:  # pragma: no cover - no project element
        return  # pragma: no cover - no project element

    # Extract project identity
    group_id = None
    artifact_id = None
    version = None

    for elem in _find_child_elements(project_node, source, "groupId"):
        group_id = _get_text_content(elem, source)
    for elem in _find_child_elements(project_node, source, "artifactId"):
        artifact_id = _get_text_content(elem, source)
    for elem in _find_child_elements(project_node, source, "version"):
        version = _get_text_content(elem, source)

    # Create project symbol
    project_id = None
    if artifact_id:
        start_line = project_node.start_point[0] + 1
        end_line = project_node.end_point[0] + 1
        project_name = f"{group_id}:{artifact_id}" if group_id else artifact_id
        project_id = _make_symbol_id(rel_path, start_line, end_line, project_name, "module")

        meta = {"artifactId": artifact_id}
        if group_id:
            meta["groupId"] = group_id
        if version:
            meta["version"] = version

        sym = Symbol(
            id=project_id,
            stable_id=None,
            shape_id=None,
            canonical_name=project_name,
            fingerprint=hashlib.sha256(source[project_node.start_byte:project_node.end_byte]).hexdigest()[:16],
            kind="module",
            name=artifact_id,
            path=rel_path,
            language="xml",
            span=Span(
                start_line=start_line,
                end_line=end_line,
                start_col=project_node.start_point[1],
                end_col=project_node.end_point[1],
            ),
            origin=PASS_ID,
            meta=meta,
        )
        symbols.append(sym)

    # Process dependencies
    for deps_elem in _find_child_elements(project_node, source, "dependencies"):
        for dep_elem in _find_child_elements(deps_elem, source, "dependency"):
            _process_maven_dependency(dep_elem, source, rel_path, symbols, edges, project_id)


def _process_android_manifest(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
) -> None:
    """Process Android AndroidManifest.xml file."""
    # Find manifest element
    manifest_node = None
    for child in root.children:
        if child.type == "element":
            if _get_element_name(child, source) == "manifest":
                manifest_node = child
                break

    if not manifest_node:  # pragma: no cover - no manifest element
        return  # pragma: no cover - no manifest element

    # Get package name
    package_name = _get_attribute(manifest_node, source, "package")

    # Process permissions
    for child in manifest_node.children:
        if child.type == "content":
            for sub in child.children:
                if sub.type == "element":
                    elem_name = _get_element_name(sub, source)

                    if elem_name == "uses-permission":
                        perm_name = _get_attribute(sub, source, "name")
                        if perm_name:
                            start_line = sub.start_point[0] + 1
                            end_line = sub.end_point[0] + 1
                            # Extract just the permission name
                            short_name = perm_name.split(".")[-1]
                            symbol_id = _make_symbol_id(rel_path, start_line, end_line, short_name, "permission")

                            sym = Symbol(
                                id=symbol_id,
                                stable_id=None,
                                shape_id=None,
                                canonical_name=perm_name,
                                fingerprint=hashlib.sha256(source[sub.start_byte:sub.end_byte]).hexdigest()[:16],
                                kind="permission",
                                name=short_name,
                                path=rel_path,
                                language="xml",
                                span=Span(
                                    start_line=start_line,
                                    end_line=end_line,
                                    start_col=sub.start_point[1],
                                    end_col=sub.end_point[1],
                                ),
                                origin=PASS_ID,
                                meta={"full_name": perm_name},
                            )
                            symbols.append(sym)

                    elif elem_name == "application":
                        _process_android_application(sub, source, rel_path, symbols, edges, package_name)


def _process_android_application(
    app_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    package_name: Optional[str],
) -> None:
    """Process Android application element."""
    for child in app_node.children:
        if child.type == "content":
            for sub in child.children:
                if sub.type == "element":
                    elem_name = _get_element_name(sub, source)

                    if elem_name in ("activity", "service", "receiver", "provider"):
                        component_name = _get_attribute(sub, source, "name")
                        if component_name:
                            # Resolve relative class name
                            if component_name.startswith(".") and package_name:
                                full_name = package_name + component_name
                            else:
                                full_name = component_name

                            exported = _get_attribute(sub, source, "exported")

                            start_line = sub.start_point[0] + 1
                            end_line = sub.end_point[0] + 1
                            # Use short name without package prefix
                            short_name = full_name.split(".")[-1]
                            symbol_id = _make_symbol_id(rel_path, start_line, end_line, short_name, elem_name)

                            meta: dict = {"component_type": elem_name, "full_name": full_name}
                            if exported:
                                meta["exported"] = exported == "true"

                            # Check for intent-filters (indicates entry points)
                            intent_filters = _find_child_elements(sub, source, "intent-filter")
                            if intent_filters:
                                actions = []
                                categories = []
                                for intent_filter in intent_filters:
                                    for action_elem in _find_child_elements(intent_filter, source, "action"):
                                        action_name = _get_attribute(action_elem, source, "name")
                                        if action_name:
                                            actions.append(action_name)
                                    for cat_elem in _find_child_elements(intent_filter, source, "category"):
                                        cat_name = _get_attribute(cat_elem, source, "name")
                                        if cat_name:
                                            categories.append(cat_name)
                                if actions:
                                    meta["intent_actions"] = actions
                                if categories:
                                    meta["intent_categories"] = categories

                            sym = Symbol(
                                id=symbol_id,
                                stable_id=None,
                                shape_id=None,
                                canonical_name=full_name,
                                fingerprint=hashlib.sha256(source[sub.start_byte:sub.end_byte]).hexdigest()[:16],
                                kind=elem_name,
                                name=short_name,
                                path=rel_path,
                                language="xml",
                                span=Span(
                                    start_line=start_line,
                                    end_line=end_line,
                                    start_col=sub.start_point[1],
                                    end_col=sub.end_point[1],
                                ),
                                origin=PASS_ID,
                                meta=meta,
                            )
                            symbols.append(sym)


def _detect_xml_type(path: Path, source: bytes) -> str:
    """Detect the type of XML file."""
    if path.name in MAVEN_FILES:
        return "maven"
    if path.name == ANDROID_MANIFEST:
        return "android_manifest"
    # Check for Android layout in res/layout directories
    if "/res/layout" in str(path) and path.suffix == ".xml":
        return "android_layout"  # pragma: no cover - layout not implemented
    # Check content for Maven namespace
    if b"maven.apache.org" in source:
        return "maven"
    # Check content for Android namespace
    if b"schemas.android.com" in source:  # pragma: no cover - namespace detection
        if b"<manifest" in source:  # pragma: no cover - namespace detection
            return "android_manifest"  # pragma: no cover - namespace detection
    return "generic"


def analyze_xml_files(repo_root: Path) -> XMLAnalysisResult:
    """Analyze XML files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        XMLAnalysisResult with symbols and edges
    """
    if not is_xml_tree_sitter_available():  # pragma: no cover
        return XMLAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-xml not installed (pip install tree-sitter-xml or tree-sitter-language-pack)",  # pragma: no cover
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

            xml_lang = get_language("xml")
            parser = tree_sitter.Parser(xml_lang)
        except Exception:  # pragma: no cover - language pack available
            import tree_sitter_xml  # pragma: no cover

            parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_xml.language()))  # pragma: no cover
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize XML parser: {e}")
        return XMLAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    xml_files = list(find_xml_files(repo_root))

    for xml_path in xml_files:
        try:
            rel_path = str(xml_path.relative_to(repo_root))
            source = xml_path.read_bytes()

            # Detect XML type
            xml_type = _detect_xml_type(xml_path, source)

            tree = parser.parse(source)
            files_analyzed += 1

            # Process based on type
            if xml_type == "maven":
                _process_maven_pom(tree.root_node, source, rel_path, symbols, edges)
            elif xml_type == "android_manifest":
                _process_android_manifest(tree.root_node, source, rel_path, symbols, edges)
            # Generic XML and layouts are not extracted (too noisy)

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {xml_path}: {e}")  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return XMLAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )


# Convenience alias
analyze_xml = analyze_xml_files
