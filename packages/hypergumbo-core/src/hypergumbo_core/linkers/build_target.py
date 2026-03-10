"""Build target linker for connecting manifest entries to main() functions.

Manifest analyzers (TOML for Cargo.toml, JSON for package.json) create
``defines_target`` edges that point from build target symbols (Cargo
``[[bin]]``, npm ``bin``) to file paths. These file paths are bare strings
(e.g., ``src/main.rs``), not valid node IDs, so the slicer cannot follow
them.

This linker resolves the gap by finding the ``main()`` function (or
equivalent entry point) in each target file and creating a ``calls`` edge
from the build target node directly to the main function node. This makes
forward slices from Cargo binary entrypoints traverse into the actual
application code.

How It Works
------------
1. Scan all edges for ``defines_target`` type
2. For each, collect the destination file path
3. Resolve relative paths against the manifest file's directory (critical
   for monorepos where Cargo.toml/package.json are in subdirectories)
4. Search symbols for a ``main`` function in that file
5. Create a ``calls`` edge: build_target → main()

Why a Linker
------------
The TOML analyzer runs before language analyzers and doesn't know main()'s
node ID. This linker runs after all analyzers, when both the Cargo binary
symbol and the Rust main() symbol exist.
"""
from __future__ import annotations

import posixpath
import time

from ..ir import AnalysisRun, Edge, PASS_VERSION, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker

PASS_ID = make_pass_id("build-target-linker")


def _resolve_target_path(
    target_path: str,
    src_id: str,
    symbols_by_id: dict[str, object],
) -> str:
    """Resolve a defines_target destination path against the manifest directory.

    In monorepos, defines_target destinations are relative to the manifest
    file (e.g., ``src/main.rs`` relative to ``crates/myapp/Cargo.toml``).
    This function resolves them to repo-root-relative paths by extracting
    the manifest directory from the source node.

    Args:
        target_path: The raw destination path from the defines_target edge
        src_id: The source node ID (build target symbol)
        symbols_by_id: Index of all symbols by ID

    Returns:
        Resolved repo-root-relative path
    """
    src_sym = symbols_by_id.get(src_id)
    if src_sym is None:
        return target_path

    # Get manifest directory from source symbol's path
    # e.g., "crates/myapp/Cargo.toml" → "crates/myapp"
    manifest_dir = posixpath.dirname(src_sym.path)
    if not manifest_dir:
        # Manifest is at repo root, no resolution needed
        return target_path

    # Strip ./ prefix if present
    cleaned = target_path.lstrip("./") if target_path.startswith("./") else target_path

    # Join and normalize: "crates/myapp" + "src/main.rs" → "crates/myapp/src/main.rs"
    resolved = posixpath.normpath(posixpath.join(manifest_dir, cleaned))
    return resolved


@register_linker("build-target", priority=15)
def link_build_targets(ctx: LinkerContext) -> LinkerResult:
    """Connect defines_target edges to main() functions.

    Scans for defines_target edges, finds main() in the target file,
    and creates a calls edge from the build target node to main().
    """
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    t0 = time.monotonic()

    # Index symbols by file path for lookup
    symbols_by_path: dict[str, list] = {}
    symbols_by_id: dict[str, object] = {}
    for sym in ctx.symbols:
        symbols_by_path.setdefault(sym.path, []).append(sym)
        symbols_by_id[sym.id] = sym

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()

    for edge in ctx.edges:
        if edge.edge_type != "defines_target":
            continue

        target_path = edge.dst
        candidates = symbols_by_path.get(target_path, [])

        # If no candidates found at the raw path, try resolving relative
        # to the manifest file's directory (monorepo case)
        if not candidates:
            resolved = _resolve_target_path(target_path, edge.src, symbols_by_id)
            if resolved != target_path:
                candidates = symbols_by_path.get(resolved, [])

        # Check for target_function in edge meta (Python entry points
        # specify the function name, e.g., "run_app" from "pkg.cli:run_app")
        target_func_name = (
            edge.meta.get("target_function") if edge.meta else None
        )

        # Find the target function: prefer target_function if specified,
        # fall back to main()
        main_fn = None
        if target_func_name:
            for sym in candidates:
                if sym.name == target_func_name and sym.kind in ("function", "method"):
                    main_fn = sym
                    break
        if main_fn is None:
            # Fall back to main() regardless of target_function
            for sym in candidates:
                if sym.name == "main" and sym.kind in ("function", "method"):
                    main_fn = sym
                    break

        if main_fn is None:
            continue

        pair = (edge.src, main_fn.id)
        if pair in seen:
            continue
        seen.add(pair)

        edges.append(Edge.create(
            src=edge.src,
            dst=main_fn.id,
            edge_type="calls",
            line=edge.line,
            evidence_type="build_target_main",
            confidence=0.95,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
        ))

    run.wall_time = time.monotonic() - t0
    return LinkerResult(edges=edges, run=run)
