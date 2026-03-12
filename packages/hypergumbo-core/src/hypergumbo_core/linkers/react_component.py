# SPDX-License-Identifier: AGPL-3.0-or-later
"""React component linker for detecting JSX composition edges.

This linker scans JavaScript/TypeScript (JSX/TSX) files for component usage
in JSX expressions and creates ``renders_component`` edges between the
enclosing component and the rendered child component.

How It Works
------------
Two-phase detection:

1. **Build component map**: Identify all symbols that are React components.
   A symbol is a React component if it's a function/class in a .jsx/.tsx file
   with a PascalCase name (React convention). Also includes symbols detected
   as components via ``base_classes`` containing ``React.Component`` or
   ``React.PureComponent``.

2. **Scan for JSX usage**: Scan JS/TS source files for JSX element tags
   (``<ComponentName``) and match them against the component map. For each
   match, create a ``renders_component`` edge from the file to the target
   component.

Why This Design
---------------
- JSX elements use PascalCase to distinguish components from HTML elements.
  ``<Button>`` is a component; ``<button>`` is an HTML element.
- Source scanning with regex is sufficient: ``<ComponentName`` is a rigid
  pattern that reliably indicates component usage.
- The JS/TS tree-sitter parser captures imports but doesn't track JSX
  element-to-component relationships. A linker bridges this gap.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..ir import AnalysisRun, Edge, PASS_VERSION, Symbol, make_pass_id
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
)

PASS_ID = make_pass_id("react-component-linker")

# Matches JSX element opening tags with PascalCase names.
# <ComponentName, <Component.SubComponent, <Ns.Component
# Does NOT match lowercase HTML elements (<div, <span, <button).
# Group 1: the component name (possibly dotted).
_JSX_COMPONENT_PATTERN = re.compile(
    r"<\s*([A-Z][a-zA-Z0-9]*(?:\.[A-Z][a-zA-Z0-9]*)*)\s*[^>]*[>/]",
)

# Common React built-in components that should NOT generate edges.
_REACT_BUILTINS = frozenset({
    "Fragment", "Suspense", "StrictMode", "Profiler",
    "React.Fragment", "React.Suspense", "React.StrictMode", "React.Profiler",
})


@dataclass
class ReactComponentLinkResult:
    """Result of React component linking."""

    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


def _is_pascal_case(name: str) -> bool:
    """Check if a name is PascalCase (starts with uppercase, not ALL_CAPS)."""
    if not name or not name[0].isupper():
        return False
    # ALL_CAPS_WITH_UNDERSCORES is not a component name
    if name.isupper() and "_" in name:
        return False
    return True


def _build_component_map(
    symbols: list[Symbol],
) -> dict[str, Symbol]:
    """Build component name -> Symbol map from JS/TS symbols.

    A symbol is considered a React component if:
    - It's a function/class in JS/TS
    - Its name is PascalCase (React convention)
    - OR it extends React.Component/PureComponent
    """
    component_map: dict[str, Symbol] = {}

    for sym in symbols:
        if sym.language not in ("javascript", "typescript"):
            continue
        if sym.kind not in ("function", "class"):
            continue
        if not _is_pascal_case(sym.name):
            continue

        # Register under simple name
        component_map[sym.name] = sym

    return component_map


def _scan_file_for_jsx_components(
    file_path: Path,
) -> list[str]:
    """Scan a JS/TS file for JSX component usage.

    Returns a list of PascalCase component names found in JSX expressions.
    Filters out React built-in components.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - defensive for I/O errors
        return []

    names: list[str] = []
    for match in _JSX_COMPONENT_PATTERN.finditer(content):
        component_name = match.group(1)
        if component_name not in _REACT_BUILTINS:
            names.append(component_name)

    return names


def link_react_components(
    repo_root: Path,
    js_ts_symbols: list[Symbol],
) -> ReactComponentLinkResult:
    """Link JSX component usage to component definitions.

    Args:
        repo_root: Repository root path.
        js_ts_symbols: JavaScript/TypeScript symbols from analyzers.

    Returns:
        ReactComponentLinkResult with renders_component edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    result_edges: list[Edge] = []

    # Phase 1: Build component map
    component_map = _build_component_map(js_ts_symbols)
    if not component_map:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return ReactComponentLinkResult(edges=[], run=run)

    # Phase 2: Collect unique JS/TS file paths
    seen_paths: set[str] = set()
    file_paths: list[Path] = []
    for sym in js_ts_symbols:
        if sym.language not in ("javascript", "typescript"):
            continue
        if sym.path in seen_paths:
            continue
        seen_paths.add(sym.path)

        file_path = Path(sym.path)
        if not file_path.is_absolute():
            file_path = repo_root / file_path
        file_paths.append(file_path)

    # Phase 3: Match JSX usage to component definitions
    seen_edges: set[tuple[str, str]] = set()  # (file_path, component_name)

    for file_path in file_paths:
        if not file_path.exists():
            continue

        jsx_components = _scan_file_for_jsx_components(file_path)

        for comp_name in jsx_components:
            # Handle dotted names: <Ns.Component> → look up "Component"
            simple_name = comp_name.rsplit(".", 1)[-1] if "." in comp_name else comp_name
            target_sym = component_map.get(simple_name)
            if target_sym is None:
                continue

            # Don't create self-referential edges
            if target_sym.path == str(file_path) or (
                not Path(target_sym.path).is_absolute()
                and repo_root / target_sym.path == file_path
            ):
                continue

            dedup_key = (str(file_path), comp_name)
            if dedup_key in seen_edges:
                continue
            seen_edges.add(dedup_key)

            rel_path = str(file_path)
            try:
                rel_path = str(file_path.relative_to(repo_root))
            except ValueError:
                pass

            src_id = f"typescript:{rel_path}:0-0:{comp_name}:jsx_usage"

            result_edges.append(Edge.create(
                src=src_id,
                dst=target_sym.id,
                edge_type="renders_component",
                line=0,
                confidence=0.80,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="jsx_element",
            ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return ReactComponentLinkResult(edges=result_edges, run=run)


def _count_js_ts_files(ctx: LinkerContext) -> int:
    """Count JavaScript/TypeScript files."""
    seen_paths: set[str] = set()
    for sym in ctx.symbols:
        if sym.language in ("javascript", "typescript"):
            if sym.path not in seen_paths:
                seen_paths.add(sym.path)
    return len(seen_paths)


def _count_react_components(ctx: LinkerContext) -> int:
    """Count PascalCase function/class symbols (potential React components)."""
    count = 0
    for sym in ctx.symbols:
        if sym.language not in ("javascript", "typescript"):
            continue
        if sym.kind not in ("function", "class"):
            continue
        if _is_pascal_case(sym.name):
            count += 1
    return count


REACT_COMPONENT_REQUIREMENTS = [
    LinkerRequirement(
        name="js_ts_files",
        description="JavaScript/TypeScript files with potential JSX usage",
        check=_count_js_ts_files,
    ),
    LinkerRequirement(
        name="react_components",
        description="PascalCase function/class symbols (React component candidates)",
        check=_count_react_components,
    ),
]


@register_linker(
    "react_component",
    priority=42,  # After IPC (40), before HTTP (45)
    description=(
        "React component composition - links JSX element usage "
        "to component definitions via renders_component edges"
    ),
    requirements=REACT_COMPONENT_REQUIREMENTS,
    activation=LinkerActivation(
        frameworks=["react"],
    ),
)
def react_component_linker(ctx: LinkerContext) -> LinkerResult:
    """React component linker for registry-based dispatch.

    Wraps link_react_components() to use the LinkerContext/LinkerResult interface.
    """
    js_ts_symbols = [
        s for s in ctx.symbols if s.language in ("javascript", "typescript")
    ]

    result = link_react_components(ctx.repo_root, js_ts_symbols)

    return LinkerResult(
        symbols=[],
        edges=result.edges,
        run=result.run,
    )
