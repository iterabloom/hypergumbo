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
3. Search symbols for a ``main`` function in that file
4. Create a ``calls`` edge: build_target → main()

Why a Linker
------------
The TOML analyzer runs before language analyzers and doesn't know main()'s
node ID. This linker runs after all analyzers, when both the Cargo binary
symbol and the Rust main() symbol exist.
"""
from __future__ import annotations

import time

from ..ir import AnalysisRun, Edge, PASS_VERSION, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker

PASS_ID = make_pass_id("build-target-linker")


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
    for sym in ctx.symbols:
        symbols_by_path.setdefault(sym.path, []).append(sym)

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()

    for edge in ctx.edges:
        if edge.edge_type != "defines_target":
            continue

        target_path = edge.dst
        candidates = symbols_by_path.get(target_path, [])

        # Prefer main() function; fall back to any function named main
        main_fn = None
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
