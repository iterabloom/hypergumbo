"""Dependency linker for connecting manifest dependencies to code imports.

This linker creates depends_on_manifest edges between code import statements
and their corresponding dependency declarations in manifest files (Cargo.toml,
pyproject.toml).

How It Works
------------
1. Build a lookup table of dependencies from TOML symbols (kind="dependency")
2. Iterate through import edges from code analyzers
3. Extract the package/crate name from the import path
4. Match imports to dependencies, handling naming conventions:
   - Rust: hyphens in Cargo.toml become underscores in code (my-crate -> my_crate)
   - Python: match root package name for submodule imports (requests.adapters -> requests)
5. Create depends_on_manifest edges linking importing file to dependency declaration

Why This Design
---------------
- Separate linker keeps language analyzers focused on their own language
- Post-hoc linking allows correlating data from multiple analysis passes
- Same pattern as JNI linker for cross-language/cross-file bridges
- Enables traceability from code usage back to manifest declarations
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..ir import AnalysisRun, Edge, Symbol
from .registry import LinkerContext, LinkerResult, LinkerRequirement, register_linker

PASS_ID = "dependency-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class DependencyLinkResult:
    """Result of dependency linking."""

    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


def _normalize_crate_name(name: str) -> str:
    """Normalize a Rust crate name for matching.

    Cargo.toml uses hyphens (my-crate) but Rust code uses underscores (my_crate).
    """
    return name.replace("-", "_")


def _extract_root_package(import_path: str) -> str | None:
    """Extract the root package name from an import path.

    Examples:
        rust:serde::Serialize:0-0:module:module -> serde
        python:requests.adapters:0-0:module:module -> requests
        python:flask:0-0:module:module -> flask
    """
    # Remove language prefix and metadata
    if import_path.startswith("rust:"):
        rest = import_path[5:]  # Remove "rust:"
        # Get the first component (crate name) before ::
        if "::" in rest:
            return rest.split("::")[0]
        # If no ::, take until the first :
        if ":" in rest:
            return rest.split(":")[0]
        return rest
    elif import_path.startswith("python:"):
        rest = import_path[7:]  # Remove "python:"
        # Get the first component before . or :
        if "." in rest:
            root = rest.split(".")[0]
        elif ":" in rest:
            root = rest.split(":")[0]
        else:
            root = rest
        return root
    return None


def _build_dependency_lookup(toml_symbols: list[Symbol]) -> dict[str, Symbol]:
    """Build a lookup table from dependency names to TOML symbols.

    Returns a dict mapping normalized dependency names to their Symbol.
    Both original and normalized names are added for Rust crates.
    """
    lookup: dict[str, Symbol] = {}

    for sym in toml_symbols:
        if sym.kind != "dependency":
            continue

        # Add original name
        lookup[sym.name] = sym

        # For Rust crates, also add normalized name (hyphen -> underscore)
        if sym.path.endswith("Cargo.toml"):
            normalized = _normalize_crate_name(sym.name)
            if normalized != sym.name:
                lookup[normalized] = sym

    return lookup


def link_dependencies(
    toml_symbols: list[Symbol],
    code_edges: list[Edge],
    code_symbols: list[Symbol],
) -> DependencyLinkResult:
    """Link code imports to manifest dependency declarations.

    Args:
        toml_symbols: Symbols from TOML analyzer (dependencies, packages, etc.)
        code_edges: Edges from code analyzers (imports, calls, etc.)
        code_symbols: Symbols from code analyzers (reserved for future use)

    Returns:
        DependencyLinkResult with depends_on_manifest edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    edges: list[Edge] = []
    seen_pairs: set[tuple[str, str]] = set()

    # Build lookup table for dependencies
    dep_lookup = _build_dependency_lookup(toml_symbols)

    if not dep_lookup:
        # No dependencies to link
        run.duration_ms = int((time.time() - start_time) * 1000)
        return DependencyLinkResult(edges=edges, run=run)

    # Process import edges
    for edge in code_edges:
        if edge.edge_type != "imports":
            continue

        # Extract the root package from the import target
        root_pkg = _extract_root_package(edge.dst)
        if root_pkg is None:
            continue

        # Look up in dependency table
        dep_sym = dep_lookup.get(root_pkg)
        if dep_sym is None:
            continue

        # Avoid duplicate edges for same file -> same dependency
        pair = (edge.src, dep_sym.id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        # Create the link edge
        link_edge = Edge.create(
            src=edge.src,  # The file that imports
            dst=dep_sym.id,  # The dependency declaration in manifest
            edge_type="depends_on_manifest",
            line=edge.line,
            confidence=0.9,  # High confidence but not certain
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="import_to_manifest",
        )
        edges.append(link_edge)

    run.duration_ms = int((time.time() - start_time) * 1000)

    return DependencyLinkResult(edges=edges, run=run)


# =============================================================================
# Linker Registry Integration
# =============================================================================


def _get_toml_symbols(ctx: LinkerContext) -> list[Symbol]:
    """Extract TOML symbols (dependencies) from context."""
    return [s for s in ctx.symbols if s.language == "toml"]


def _count_toml_dependencies(ctx: LinkerContext) -> int:
    """Count available TOML dependency symbols for requirement check."""
    return sum(1 for s in ctx.symbols if s.language == "toml" and s.kind == "dependency")


def _count_import_edges(ctx: LinkerContext) -> int:
    """Count available import edges for requirement check."""
    return sum(1 for e in ctx.edges if e.edge_type == "imports")


DEPENDENCY_REQUIREMENTS = [
    LinkerRequirement(
        name="toml_dependencies",
        description="TOML dependency declarations (Cargo.toml, pyproject.toml)",
        check=_count_toml_dependencies,
    ),
    LinkerRequirement(
        name="import_edges",
        description="Import edges from code analyzers",
        check=_count_import_edges,
    ),
]


@register_linker(
    "dependency",
    priority=80,  # Run late, after all imports have been collected
    description="Dependency linking (imports to manifest declarations)",
    requirements=DEPENDENCY_REQUIREMENTS,
)
def dependency_linker(ctx: LinkerContext) -> LinkerResult:
    """Dependency linker for registry-based dispatch.

    This wraps link_dependencies() to use the LinkerContext/LinkerResult interface.
    Extracts TOML symbols and edges from ctx and delegates to core linking.
    """
    toml_symbols = _get_toml_symbols(ctx)
    result = link_dependencies(
        toml_symbols=toml_symbols,
        code_edges=ctx.edges,
        code_symbols=ctx.symbols,
    )

    return LinkerResult(
        symbols=[],  # dependency linker doesn't create symbols
        edges=result.edges,
        run=result.run,
    )
