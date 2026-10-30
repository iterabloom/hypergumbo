# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker: build target for connecting manifest entries to main() functions.

Manifest analyzers (TOML for Cargo.toml, JSON for package.json, plus XML
and ``manifest_targets``) create
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
2. For each, collect the target file path (``meta["target_path"]`` when
   present, else the edge's ``dst``)
3. If no symbols live at that path, resolve it against the manifest file's
   directory (critical for monorepos where Cargo.toml/package.json are in
   subdirectories)
   - a Python module (a console script's ``tool.cli`` made ``tool/cli.py``)
     is also looked for where Python would import it from: the manifest's
     ``src/``, the package form ``tool/cli/__init__.py``, else the ONE
     in-tree file whose path ends in the module's path (two name nothing)
     -- see ``_python_module_file`` (WI-lipal)
4. Search symbols in that file for the function named by
   ``meta["target_function"]`` when present, else for ``main``; a named
   function that is not there gets no edge, never some other ``main``
5. Create a ``calls`` edge: build_target → that function, naming the
   ``defines_target`` edge it consumed in ``derived_from``

Supersession
------------
The consumed ``defines_target`` placeholder stays in the graph. This pass
holds ADR-0057 §14.1's permission (granted on ``docs/audits/0024``), so
finalize demotes it and stamps ``meta["superseded_by"]`` with this pass's
edge: a console script's ``python:tool.cli:0-0:main`` stand-in then says
which in-tree function answered it, rather than reading as external code
(INV-nuzas carve-out (c)).

Why a Linker
------------
The TOML analyzer runs before language analyzers and doesn't know main()'s
node ID. This linker runs after all analyzers, when both the Cargo binary
symbol and the Rust main() symbol exist.
"""
from __future__ import annotations

import posixpath

from ..ir import AnalysisRun, Edge, PASS_VERSION, Symbol, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker, always_on_unreviewed

PASS_ID = make_pass_id("build-target-linker")


def _resolve_target_path(
    target_path: str,
    src_id: str,
    symbols_by_id: dict[str, Symbol],
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


def _python_module_file(
    target_path: str,
    manifest_path: str,
    symbols_by_path: dict[str, list[Symbol]],
) -> str | None:
    """The in-tree file a console script's Python module is, or None (WI-lipal).

    ``target_path`` is the module made a path (``tool/cli.py`` for
    ``tool.cli``); the module may also be a package (``tool/cli/__init__.py``).
    Python imports it from wherever it sits on ``sys.path``, which is rarely
    the manifest's own directory alone: under the manifest's ``src/`` (the src
    layout), or in ANOTHER workspace package (the meta-package's ``hypergumbo
    = "hypergumbo_core.cli:main"``). In that order of preference -- the
    manifest's directory, its ``src/``, then the one in-tree file anywhere
    whose path ends in the module's path, by whole segments. Two such files
    and no way to choose name nothing: the module path is the target's
    identity, and a guess between two files would be a wrong edge.
    """
    stem = target_path[: -len(".py")]
    forms = (target_path, f"{stem}/__init__.py")
    manifest_dir = posixpath.dirname(manifest_path)
    for base in (manifest_dir, posixpath.join(manifest_dir, "src") if manifest_dir else "src"):
        for form in forms:
            path = posixpath.normpath(posixpath.join(base, form))
            if path in symbols_by_path:
                return path
    found = {
        path for path in symbols_by_path
        for form in forms if path == form or path.endswith("/" + form)
    }
    return found.pop() if len(found) == 1 else None


@register_linker(
    "build-target-linker",
    priority=15,
    # CNF: the analyzers that emit the ``defines_target`` edges this linker
    # walks, enumerated from their sources (WI-rasal). ``xml`` and
    # ``manifest_targets`` were missing — Haskell repositories falsified the
    # old ``["toml", "json"]`` through ``manifest_targets`` on 25 surveys.
    #
    # The former entry-point conjunct (``["rust", "javascript", "go", "python",
    # "java"]``) is GONE, and deliberately not replaced with a wider list. The
    # linker matches a function or method symbol literally named ``main``, or
    # the manifest's ``target_function`` — which EVERY analyzer that emits
    # functions can supply, so the honest enumeration is all 118 and any narrow
    # one is false (yesod falsified it from Haskell). A conjunct satisfied by
    # everything states nothing that the empty list does not.
    depends_on=[
        ["json", "manifest_targets", "toml", "xml"],
    ],
    activation=always_on_unreviewed(),
    # ADR-0057 §14.1: the placeholder this pass consumed (named in each edge's
    # derived_from) is demoted and stamped superseded_by. Granted on
    # docs/audits/0024: 16 of 16 edges correct over 12 repositories, and the
    # pass makes no choice -- an exact file and the exact named function, or
    # no edge.
    supersedes_consumed_stub=True,
)
def link_build_targets(ctx: LinkerContext) -> LinkerResult:
    """Connect defines_target edges to main() functions.

    Scans for defines_target edges, finds main() in the target file,
    and creates a calls edge from the build target node to main().
    """
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Index symbols by file path for lookup
    symbols_by_path: dict[str, list] = {}
    symbols_by_id: dict[str, Symbol] = {}
    for sym in ctx.symbols:
        symbols_by_path.setdefault(sym.path, []).append(sym)
        symbols_by_id[sym.id] = sym

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()

    for edge in ctx.edges:
        if edge.edge_type != "defines_target":
            continue

        # Producers may carry the file path in meta when the dst is a
        # well-formed symbol id (INV-nodij — toml_config's pyproject
        # scripts emitter). Older producers continue to put the path
        # directly in dst.
        target_path = (
            (edge.meta or {}).get("target_path")
            or edge.dst
        )
        candidates = symbols_by_path.get(target_path, [])

        # If no candidates found at the raw path, try resolving relative
        # to the manifest file's directory (monorepo case)
        if not candidates:
            resolved = _resolve_target_path(target_path, edge.src, symbols_by_id)
            if resolved != target_path:
                candidates = symbols_by_path.get(resolved, [])

        # A Python module is wherever Python would import it from, not only
        # beside the manifest (WI-lipal).
        src_sym = symbols_by_id.get(edge.src)
        if not candidates and target_path.endswith(".py") and src_sym is not None:
            module_file = _python_module_file(target_path, src_sym.path, symbols_by_path)
            if module_file is not None:
                candidates = symbols_by_path[module_file]

        # Check for target_function in edge meta (Python entry points
        # specify the function name, e.g., "run_app" from "pkg.cli:run_app")
        target_func_name = (
            edge.meta.get("target_function") if edge.meta else None
        )

        # Find the target function: the one the manifest names, else main().
        # A manifest that NAMES its function is taken at its word: a missing
        # name is not some other main() (WI-lipal; usually a re-export).
        main_fn = None
        if target_func_name:
            for sym in candidates:
                if sym.name == target_func_name and sym.kind in ("function", "method"):
                    main_fn = sym
                    break
        else:
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
            derived_from=[edge.src, main_fn.id, edge.id],
        ))

    return LinkerResult(edges=edges, run=run)
