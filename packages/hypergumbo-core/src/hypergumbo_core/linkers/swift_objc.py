# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bridge linker: Swift/Objective-C bridging.

This linker detects interoperability patterns between Swift and Objective-C:
- @objc annotations exposing Swift code to Objective-C
- NSObject subclasses (automatically bridged)
- Bridging header imports
- #selector() references

Patterns detected:
- ``@objc`` immediately followed by ``class``/``func``/``var``/``enum`` in
  Swift (``@objc(name)``, ``@objcMembers`` and forms with an intervening
  modifier such as ``@objc public func`` are not matched) ->
  ``kind="function"`` symbols with ``meta["framework_role"] = "objc_bridge"``
- NSObject inheritance in Swift -> ``kind="function"`` symbols with
  ``meta["framework_role"] = "objc_bridge"``
- *-Bridging-Header.h files -> import edges
- #selector(methodName) in Swift -> ``kind="reference"`` symbols with
  ``meta["framework_role"] = "selector_ref"``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..analyze.base import make_file_id, make_symbol_id
from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from .registry import LinkerActivation, LinkerContext, LinkerResult, register_linker
from ._text_filters import read_masked_source

PASS_ID = make_pass_id("swift-objc-linker")


@dataclass
class SwiftObjCLinkerResult:
    """Result of running the Swift/Objective-C linker."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


#: The registered symbol kind each framework role folds to (ADR-0027 Phase 3).
_ROLE_FOLDS: dict[str, str] = {"objc_bridge": "function", "selector_ref": "reference"}


def _make_symbol_id(path: str, line: int, name: str, role: str) -> str:
    """The node id of a symbol this linker mints from a Swift file.

    ADR-0036 Ruling 2: the kind slot is ``node.kind`` and the language slot
    ``discovery_language or language`` -- ``swift`` for these Class B
    stand-ins. The pass name and the framework role used to sit in those two
    slots. The role is kept as a name-segment suffix, as the websocket linker
    does (INV-dulah), so a bridge and a selector at one line stay distinct.
    """
    return make_symbol_id("swift", path, line, line, f"{name}-{role}", _ROLE_FOLDS[role])


def _find_swift_files(root: Path) -> list[Path]:
    """Find all Swift files."""
    from hypergumbo_core.discovery import get_file_index
    file_index = get_file_index()
    if file_index is not None and file_index.repo_root == root:  # pragma: no cover - only via run_behavior_map
        return [
            p for p in file_index.by_extension(".swift")
            if not any(part in (".build", "Build", "DerivedData") for part in p.parts)
        ]
    swift_files: list[Path] = []
    for path in root.rglob("*.swift"):
        if path.is_file():
            # Skip build directories
            if any(part in (".build", "Build", "DerivedData") for part in path.parts):
                continue  # pragma: no cover - test dirs clean
            swift_files.append(path)
    return swift_files


def _find_bridging_headers(root: Path) -> list[Path]:
    """Find bridging header files (*-Bridging-Header.h)."""
    from hypergumbo_core.discovery import get_file_index
    file_index = get_file_index()
    if file_index is not None and file_index.repo_root == root:  # pragma: no cover - only via run_behavior_map
        return list(file_index.by_glob("*-Bridging-Header.h"))
    bridging_headers: list[Path] = []
    for path in root.rglob("*-Bridging-Header.h"):
        if path.is_file():
            bridging_headers.append(path)
    return bridging_headers


# Patterns for Swift analysis
OBJC_ANNOTATION_PATTERN = re.compile(
    r"@objc\s+(?:class|func|var|enum)\s+(\w+)", re.MULTILINE
)
NSOBJECT_INHERIT_PATTERN = re.compile(
    r"class\s+(\w+)\s*:\s*NSObject", re.MULTILINE
)
SELECTOR_PATTERN = re.compile(
    r"#selector\s*\(\s*(\w+)", re.MULTILINE
)

# Patterns for bridging header analysis
IMPORT_PATTERN = re.compile(
    r'#import\s+[<"]([^>"]+)[>"]', re.MULTILINE
)


def _extract_swift_objc_patterns(
    file_path: Path, run: AnalysisRun
) -> tuple[list[Symbol], list[Edge]]:
    """Extract @objc and NSObject patterns from a Swift file."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    rel_path = str(file_path)

    try:
        content = read_masked_source(file_path, encoding="utf-8", errors="replace")
    except (OSError, IOError):  # pragma: no cover
        return symbols, edges

    lines = content.split("\n")

    # Find @objc annotations
    for match in OBJC_ANNOTATION_PATTERN.finditer(content):
        name = match.group(1)
        # Calculate line number
        line_num = content[:match.start()].count("\n") + 1
        symbol_id = _make_symbol_id(rel_path, line_num, name, "objc_bridge")

        # ADR-0027 Phase 3 / audit-findings 0013: framework-role leak.
        # ADR-0031 Class B: synthetic stand-in for a Swift<->Objc bridge
        # crossing. Was LITERAL-HOST ("swift") — Objc-bridge is the protocol
        # identity; the host language is Swift (the discovery file extension).
        symbols.append(Symbol(
            id=symbol_id,
            name=name,
            kind="function",
            language=None,
            discovery_language="swift",
            protocol_origin="objc_bridge",
            path=rel_path,
            span=Span(line_num, line_num, 0, len(lines[line_num - 1]) if line_num <= len(lines) else 0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            meta={"framework_role": "objc_bridge"},
        ))

    # Find NSObject subclasses
    for match in NSOBJECT_INHERIT_PATTERN.finditer(content):
        name = match.group(1)
        line_num = content[:match.start()].count("\n") + 1
        symbol_id = _make_symbol_id(rel_path, line_num, name, "objc_bridge")

        # Avoid duplicates (if already has @objc).
        # Post-fold: filter on meta["framework_role"] since kind is now "function".
        if not any(
            s.name == name
            and (s.meta or {}).get("framework_role") == "objc_bridge"
            for s in symbols
        ):
            symbols.append(Symbol(
                id=symbol_id,
                name=name,
                kind="function",
                language=None,
                discovery_language="swift",
                protocol_origin="objc_bridge",
                path=rel_path,
                span=Span(line_num, line_num, 0, len(lines[line_num - 1]) if line_num <= len(lines) else 0),
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                meta={"framework_role": "objc_bridge"},
            ))

    # Find #selector references
    for match in SELECTOR_PATTERN.finditer(content):
        name = match.group(1)
        line_num = content[:match.start()].count("\n") + 1
        symbol_id = _make_symbol_id(rel_path, line_num, name, "selector_ref")

        # ADR-0027 Phase 3 / audit-findings 0013: framework-role leak.
        # Per audit, selector_ref folds to canonical kind="reference" (not
        # function/method) — the _ref suffix names a use-site, not a
        # definition.
        # ADR-0031 Class B: same protocol-origin family as the bridge above.
        symbols.append(Symbol(
            id=symbol_id,
            name=name,
            kind="reference",
            language=None,
            discovery_language="swift",
            protocol_origin="objc_bridge",
            path=rel_path,
            span=Span(line_num, line_num, 0, 0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            meta={"framework_role": "selector_ref"},
        ))

    return symbols, edges


def _extract_bridging_header_imports(
    file_path: Path, run: AnalysisRun,
    file_ids_by_path: dict[str, str] | None = None,
) -> tuple[list[Symbol], list[Edge]]:
    """Extract imports from a bridging header file.

    WI-mofih-datih: both endpoints take the shapes the ObjC analyzer gives its
    own ``#import`` edges, so the edge meets the graph instead of dangling.
    The src is the header's file node -- the one the run already holds for
    this path when an analyzer emitted one (a header may be parsed as C), else
    ``make_file_id("objc", path)``; it used to carry the PASS NAME in the
    language slot and match no node. The dst is the analyzer's header
    placeholder ``objc:<header>:0-0:header:header`` (INV-dulah), not the raw
    ``#import`` string, which is no id at all.
    """
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    rel_path = str(file_path)
    file_id = (file_ids_by_path or {}).get(rel_path) or make_file_id("objc", rel_path)

    try:
        content = read_masked_source(file_path, encoding="utf-8", errors="replace")
    except (OSError, IOError):  # pragma: no cover
        return symbols, edges

    for match in IMPORT_PATTERN.finditer(content):
        import_path = match.group(1)
        line_num = content[:match.start()].count("\n") + 1

        edges.append(Edge.create(
            src=file_id,
            dst=f"objc:{import_path}:0-0:header:header",
            edge_type="imports",
            line=line_num,
            evidence_type="bridging_header_import",
            confidence=0.95,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            # derived-from consumed-none: a bridging-header text scan; the src is the header's file
            #   id and the dst the analyzer's header placeholder, neither decided by a consumed record
            derived_from=[],
        ))

    return symbols, edges


def link_swift_objc(
    root: Path, graph_symbols: list[Symbol] | None = None,
) -> SwiftObjCLinkerResult:
    """Run the Swift/Objective-C bridging linker.

    Detects:
    - @objc annotations in Swift
    - NSObject subclasses in Swift
    - Bridging header imports
    - #selector() references

    ``graph_symbols`` are the run's symbols so far; a bridging header's existing
    file node is the src of its import edges.
    """
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    file_ids_by_path = {
        sym.path: sym.id for sym in (graph_symbols or ()) if sym.kind == "file"
    }

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    # Process Swift files
    for swift_file in _find_swift_files(root):
        symbols, edges = _extract_swift_objc_patterns(swift_file, run)
        all_symbols.extend(symbols)
        all_edges.extend(edges)

    # Process bridging headers
    for header_file in _find_bridging_headers(root):
        symbols, edges = _extract_bridging_header_imports(
            header_file, run, file_ids_by_path,
        )
        all_symbols.extend(symbols)
        all_edges.extend(edges)

    return SwiftObjCLinkerResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )


# =============================================================================
# Linker Registry Integration
# =============================================================================


@register_linker(
    "swift-objc-linker",
    priority=30,  # Run early, interop bridging is foundational
    description="Swift/Objective-C bridging (@objc, NSObject, bridging headers)",
    activation=LinkerActivation(language_pairs=[("swift", "objc")]),
    # CNF: swift AND objc — both required for the @objc / NSObject /
    # bridging-header bridge.
    depends_on=[["swift"], ["objc"]],
)
def swift_objc_linker(ctx: LinkerContext) -> LinkerResult:
    """Swift/Objective-C linker for registry-based dispatch.

    This wraps link_swift_objc() to use the LinkerContext/LinkerResult interface.
    """
    result = link_swift_objc(ctx.repo_root, ctx.symbols)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
