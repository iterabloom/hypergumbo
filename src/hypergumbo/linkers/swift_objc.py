"""Swift/Objective-C bridging linker.

This linker detects interoperability patterns between Swift and Objective-C:
- @objc annotations exposing Swift code to Objective-C
- NSObject subclasses (automatically bridged)
- Bridging header imports
- #selector() references
- Swift code referencing Objective-C classes

Patterns detected:
- @objc class/func declarations in Swift -> objc_bridge symbols
- NSObject inheritance in Swift -> objc_bridge symbols
- *-Bridging-Header.h files -> import edges
- #selector(methodName) in Swift -> selector_ref symbols
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..ir import AnalysisRun, Edge, Span, Symbol
from .registry import LinkerActivation, LinkerContext, LinkerResult, register_linker

PASS_ID = "swift-objc-linker-v1"
PASS_VERSION = "1.0.0"


@dataclass
class SwiftObjCLinkerResult:
    """Result of running the Swift/Objective-C linker."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


def _make_symbol_id(path: str, line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"swift-objc:{path}:{line}:{name}:{kind}"


def _find_swift_files(root: Path) -> list[Path]:
    """Find all Swift files."""
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
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, IOError):  # pragma: no cover
        return symbols, edges

    lines = content.split("\n")

    # Find @objc annotations
    for match in OBJC_ANNOTATION_PATTERN.finditer(content):
        name = match.group(1)
        # Calculate line number
        line_num = content[:match.start()].count("\n") + 1
        symbol_id = _make_symbol_id(rel_path, line_num, name, "objc_bridge")

        symbols.append(Symbol(
            id=symbol_id,
            name=name,
            kind="objc_bridge",
            language="swift",
            path=rel_path,
            span=Span(line_num, line_num, 0, len(lines[line_num - 1]) if line_num <= len(lines) else 0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
        ))

    # Find NSObject subclasses
    for match in NSOBJECT_INHERIT_PATTERN.finditer(content):
        name = match.group(1)
        line_num = content[:match.start()].count("\n") + 1
        symbol_id = _make_symbol_id(rel_path, line_num, name, "objc_bridge")

        # Avoid duplicates (if already has @objc)
        if not any(s.name == name and s.kind == "objc_bridge" for s in symbols):
            symbols.append(Symbol(
                id=symbol_id,
                name=name,
                kind="objc_bridge",
                language="swift",
                path=rel_path,
                span=Span(line_num, line_num, 0, len(lines[line_num - 1]) if line_num <= len(lines) else 0),
                origin=PASS_ID,
                origin_run_id=run.execution_id,
            ))

    # Find #selector references
    for match in SELECTOR_PATTERN.finditer(content):
        name = match.group(1)
        line_num = content[:match.start()].count("\n") + 1
        symbol_id = _make_symbol_id(rel_path, line_num, name, "selector_ref")

        symbols.append(Symbol(
            id=symbol_id,
            name=name,
            kind="selector_ref",
            language="swift",
            path=rel_path,
            span=Span(line_num, line_num, 0, 0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
        ))

    return symbols, edges


def _extract_bridging_header_imports(
    file_path: Path, run: AnalysisRun
) -> tuple[list[Symbol], list[Edge]]:
    """Extract imports from a bridging header file."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    rel_path = str(file_path)
    file_id = f"swift-objc:{rel_path}:1:file:file"

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, IOError):  # pragma: no cover
        return symbols, edges

    for match in IMPORT_PATTERN.finditer(content):
        import_path = match.group(1)
        line_num = content[:match.start()].count("\n") + 1

        edges.append(Edge.create(
            src=file_id,
            dst=import_path,
            edge_type="imports",
            line=line_num,
            evidence_type="bridging_header_import",
            confidence=0.95,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
        ))

    return symbols, edges


def link_swift_objc(root: Path) -> SwiftObjCLinkerResult:
    """Run the Swift/Objective-C bridging linker.

    Detects:
    - @objc annotations in Swift
    - NSObject subclasses in Swift
    - Bridging header imports
    - #selector() references
    """
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    # Process Swift files
    for swift_file in _find_swift_files(root):
        symbols, edges = _extract_swift_objc_patterns(swift_file, run)
        all_symbols.extend(symbols)
        all_edges.extend(edges)

    # Process bridging headers
    for header_file in _find_bridging_headers(root):
        symbols, edges = _extract_bridging_header_imports(header_file, run)
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
    "swift_objc",
    priority=30,  # Run early, interop bridging is foundational
    description="Swift/Objective-C bridging (@objc, NSObject, bridging headers)",
    activation=LinkerActivation(language_pairs=[("swift", "objc")]),
)
def swift_objc_linker(ctx: LinkerContext) -> LinkerResult:
    """Swift/Objective-C linker for registry-based dispatch.

    This wraps link_swift_objc() to use the LinkerContext/LinkerResult interface.
    """
    result = link_swift_objc(ctx.repo_root)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
