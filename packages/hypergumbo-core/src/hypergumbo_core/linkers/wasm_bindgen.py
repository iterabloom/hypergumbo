# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bridge linker: wasm_bindgen for connecting JS/TS imports to Rust #[wasm_bindgen] exports.

This linker creates ``calls`` edges (tagged ``meta.bridge_kind="wasm"``)
between JavaScript/TypeScript code that imports functions from
wasm-pack-generated packages and the Rust functions annotated with
``#[wasm_bindgen]``. (The bespoke ``wasm_bridge`` edge type was folded onto
the canonical ``calls`` per the audit-findings 0002 relationship-axis
consolidation.)

How It Works
------------
Three-phase detection:

1. **Rust side**: Iterates all Rust symbols to find functions with
   ``#[wasm_bindgen]`` in their ``meta.annotations``. Builds an export map
   keyed by the JavaScript-visible name:
   - Default: the Rust function name (snake_case preserved)
   - ``js_name = "customName"``: explicit JavaScript name override

2. **JS/TS side**: Scans source files for import statements that reference
   wasm-bindgen output directories or files. Detects:
   - ``import { func } from './pkg/module'`` (wasm-pack convention)
   - ``import { func } from './module_bg.wasm'`` (direct wasm import)
   - ``import init, { func } from './pkg/module'`` (default + named)
   - Aliased imports: ``import { func as alias }`` extracts ``func``
   - Filters out ``import type { ... }`` (TypeScript type-only imports)

3. **Dynamic WASM loading**: Scans JS/TS for ``WebAssembly.instantiate`` /
   ``instantiateStreaming``, ``.wasm`` URL imports, and Emscripten ``Module()``
   patterns, creating synthetic ``kind="module"`` WASM nodes
   (``meta.compilation_target="wasm"``) and ``imports`` edges from the JS file
   to them.

After building both maps, the linker creates ``calls`` edges
(``meta.bridge_kind="wasm"``) from synthetic JS/TS-side sources to the
matching Rust wasm_bindgen functions.

Why This Design
---------------
- wasm-pack generates a ``pkg/`` directory with JS bindings that re-export
  Rust functions under their ``#[wasm_bindgen]``-declared names. The JS
  analyzer sees these as normal imports, but doesn't know they target Rust.
- The ``#[wasm_bindgen]`` attribute is captured by the Rust analyzer's
  annotation extraction, so no additional Rust source scanning is needed.
- Source scanning with regex is sufficient: ``import { name } from './pkg/...'``
  is a rigid pattern that wasm-pack projects consistently use.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
)
from ._text_filters import js_ts_language_from_path, read_masked_source

PASS_ID = make_pass_id("wasm-bindgen-linker")

# Matches named imports from wasm-related paths:
#   import { func1, func2 } from './pkg/module'
#   import { func } from './module_bg.wasm'
#   import init, { func } from './pkg/module'
# Does NOT match: import type { ... } from './pkg/...'
# Group 1: the import specifier list (e.g., "func1, func2 as alias")
# Group 2: the import path
_WASM_IMPORT_PATTERN = re.compile(
    r"""import\s+"""
    r"""(?!type\s)"""  # negative lookahead: skip `import type`
    r"""(?:\w+\s*,\s*)?"""  # optional default import (e.g., "init, ")
    r"""\{\s*([^}]+)\}\s*"""  # named imports in braces
    r"""from\s+['"]([^'"]+)['"]""",
)

# Paths that indicate wasm-bindgen output
_WASM_PATH_INDICATORS = ("/pkg/", "_bg.wasm", ".wasm")


def _is_wasm_import_path(path: str) -> bool:
    """Check if an import path looks like a wasm-bindgen output."""
    return any(indicator in path for indicator in _WASM_PATH_INDICATORS)


def _parse_import_names(specifier_list: str) -> list[str]:
    """Parse import names from a specifier list like 'func1, func2 as alias'.

    Returns the original names (before 'as' alias), since those correspond
    to the Rust export names.
    """
    names: list[str] = []
    for spec in specifier_list.split(","):
        spec = spec.strip()
        if not spec:
            continue
        # Handle "name as alias" — we want the original name
        parts = spec.split()
        if parts:
            names.append(parts[0])
    return names


def _find_wasm_bindgen_exports(
    rust_symbols: list[Symbol],
) -> dict[str, Symbol]:
    """Build export name -> Symbol map from Rust #[wasm_bindgen] functions.

    A single Rust function may be registered under multiple names:
    - The raw function name (default)
    - Explicit js_name override (if js_name="customName")
    """
    export_map: dict[str, Symbol] = {}

    for sym in rust_symbols:
        if sym.language != "rust":
            continue
        if sym.kind not in ("function", "method"):
            continue
        if sym.meta is None:
            continue

        annotations = sym.meta.get("annotations")
        if not annotations or not isinstance(annotations, list):
            continue

        is_wasm_bindgen = False
        js_name = None

        for ann in annotations:
            if not isinstance(ann, dict):
                continue  # pragma: no cover
            name = ann.get("name", "")
            if name == "wasm_bindgen":
                is_wasm_bindgen = True
                kwargs = ann.get("kwargs", {})
                raw_js_name = kwargs.get("js_name")
                if isinstance(raw_js_name, str):
                    js_name = raw_js_name

        if not is_wasm_bindgen:
            continue

        # Register under original name (always)
        export_map[sym.name] = sym

        # Register under js_name if specified
        if js_name:
            export_map[js_name] = sym

    return export_map


def _scan_js_ts_for_wasm_imports(
    file_path: Path,
) -> list[str]:
    """Scan a JS/TS file for imports from wasm-bindgen packages.

    Returns a list of imported function names from wasm-related modules.
    """
    try:
        content = read_masked_source(file_path, encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - defensive for I/O errors
        return []

    names: list[str] = []
    for match in _WASM_IMPORT_PATTERN.finditer(content):
        import_path = match.group(2)
        if not _is_wasm_import_path(import_path):
            continue
        specifiers = match.group(1)
        names.extend(_parse_import_names(specifiers))

    return names


@dataclass
class WasmBindgenLinkResult:
    """Result of wasm_bindgen linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


def link_wasm_bindgen(
    repo_root: Path,
    ts_js_symbols: list[Symbol],
    rust_symbols: list[Symbol],
) -> WasmBindgenLinkResult:
    """Link wasm-bindgen imports to their Rust #[wasm_bindgen] exports.

    Args:
        repo_root: Repository root path.
        ts_js_symbols: JavaScript/TypeScript symbols from analyzers.
        rust_symbols: Rust symbols from analyzers.

    Returns:
        WasmBindgenLinkResult with wasm_bridge edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    result_edges: list[Edge] = []
    result_symbols: list[Symbol] = []
    seen_import_ids: set[str] = set()

    # Phase 1: Build export map from Rust symbols
    export_map = _find_wasm_bindgen_exports(rust_symbols)
    if not export_map:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return WasmBindgenLinkResult(edges=[], run=run)

    # Phase 2: Collect unique JS/TS file paths
    seen_paths: set[str] = set()
    ts_js_files: list[Path] = []
    for sym in ts_js_symbols:
        if sym.language not in ("javascript", "typescript"):
            continue
        if sym.path in seen_paths:
            continue
        seen_paths.add(sym.path)

        file_path = Path(sym.path)
        if not file_path.is_absolute():
            file_path = repo_root / file_path
        ts_js_files.append(file_path)

    # Phase 3: Match imports to Rust exports
    seen_edges: set[tuple[str, str]] = set()  # (file_path, import_name)

    for file_path in ts_js_files:
        if not file_path.exists():
            continue

        import_names = _scan_js_ts_for_wasm_imports(file_path)

        for import_name in import_names:
            target_sym = export_map.get(import_name)
            if target_sym is None:
                continue

            dedup_key = (str(file_path), import_name)
            if dedup_key in seen_edges:
                continue
            seen_edges.add(dedup_key)

            # Build synthetic source ID
            rel_path = str(file_path)
            try:
                rel_path = str(file_path.relative_to(repo_root))
            except ValueError:
                pass

            # WI-dovog: the importing file's language, not a literal.
            host_language = js_ts_language_from_path(Path(rel_path))
            src_id = f"{host_language}:{rel_path}:0-0:{import_name}:wasm_import"

            # Create synthetic Symbol node for the wasm import so the
            # slicer's BFS can traverse through it. Without this node,
            # reverse slices from Rust exports would dead-end because
            # node_by_id.get(edge.src) returns None.
            if src_id not in seen_import_ids:
                seen_import_ids.add(src_id)
                # ADR-0031 Class B: synthetic stand-in for a WASM import
                # on the TS side. Was LITERAL ("typescript").
                result_symbols.append(Symbol(
                    id=src_id,
                    stable_id=None,
                    shape_id=None,
                    display_label=f"import {{ {import_name} }}",  # ADR-0032
                    # ADR-0032: producer-side Format 1 fingerprint dropped; central post-pass stamps Format 2.
                    kind="import",
                    name=import_name,
                    path=rel_path,
                    language=None,
                    discovery_language=host_language,
                    protocol_origin="wasm",
                    span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={
                        "wasm_export": import_name,
                        "compilation_target": "wasm",
                    },
                    # INV-bonup / ADR-0041 §1: NO supply_chain_tier stamp — this
                    # node used to borrow tier 2 purely to make _classify_symbols
                    # skip host-path reclassification (e.g. generated pkg/ files →
                    # tier 4 → filtered out), leaking a skip mechanism into the
                    # distance axis. The skip now keys on this node's protocol_origin
                    # marker, so it keeps its honest first-party default distance.
                    supply_chain_reason="synthetic WASM bridge node",
                ))

            # ADR-0023 §6 Phase 3 (WI-mifor-vabul): canonical 'calls'
            # + meta['bridge_kind']='wasm'.
            result_edges.append(Edge.create(
                src=src_id,
                dst=target_sym.id,
                edge_type="calls",
                line=0,
                confidence=0.85,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="ast_import",
                data_direction="src_to_dst",
                meta={"bridge_kind": "wasm", "framework_dispatch": "wasm_bindgen_import"},
                derived_from=[src_id, target_sym.id],
            ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return WasmBindgenLinkResult(
        edges=result_edges, symbols=result_symbols, run=run,
    )


# Pattern for WebAssembly.instantiate / WebAssembly.instantiateStreaming calls
# Captures the URL/path of the .wasm file being loaded
_WASM_INSTANTIATE_PATTERN = re.compile(
    r"""WebAssembly\.instantiate(?:Streaming)?\s*\("""
    r"""[^)]*?['"]([^'"]*\.wasm)['"]""",
)

# Pattern for import URL of .wasm files (bundler URL imports)
_WASM_URL_IMPORT_PATTERN = re.compile(
    r"""import\s+\w+\s+from\s+['"](?:url:)?([^'"]*\.wasm)['"]""",
)

# Pattern for Emscripten Module() factory or loadModule() calls
_EMSCRIPTEN_MODULE_PATTERN = re.compile(
    r"""(?:new\s+Module|Module\s*\(|loadModule\s*\()""",
)


def _scan_js_ts_for_wasm_loading(
    file_path: Path,
) -> list[str]:
    """Scan a JS/TS file for dynamic WASM loading patterns.

    Detects:
    - WebAssembly.instantiate('path.wasm')
    - WebAssembly.instantiateStreaming(fetch('path.wasm'))
    - import wasmUrl from 'url:codecs/rotate/rotate.wasm'
    - Emscripten Module() factory patterns

    Returns a list of .wasm file references found.
    """
    try:
        content = read_masked_source(file_path, encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - defensive for I/O errors
        return []

    wasm_refs: list[str] = []

    for match in _WASM_INSTANTIATE_PATTERN.finditer(content):
        wasm_refs.append(match.group(1))

    for match in _WASM_URL_IMPORT_PATTERN.finditer(content):
        wasm_refs.append(match.group(1))

    # Check for Emscripten patterns (no specific .wasm path captured)
    if _EMSCRIPTEN_MODULE_PATTERN.search(content):
        wasm_refs.append("__emscripten_module__")

    return wasm_refs


def _create_wasm_load_edges(
    repo_root: Path,
    ts_js_symbols: list[Symbol],
    run: AnalysisRun,
) -> tuple[list[Edge], list[Symbol]]:
    """Create ``imports`` edges for dynamic WASM loading patterns.

    Scans JS/TS files for WebAssembly.instantiate, URL imports of .wasm files,
    and Emscripten Module patterns. Creates synthetic WASM module symbols and
    ``imports`` edges (``meta.compilation_target="wasm"``) from the JS file to
    the WASM module.
    """
    edges: list[Edge] = []
    symbols: list[Symbol] = []
    seen_paths: set[str] = set()
    seen_wasm_refs: set[tuple[str, str]] = set()

    for sym in ts_js_symbols:
        if sym.language not in ("javascript", "typescript"):
            continue
        if sym.path in seen_paths:
            continue
        seen_paths.add(sym.path)

        file_path = Path(sym.path)
        if not file_path.is_absolute():
            file_path = repo_root / file_path
        if not file_path.exists():
            continue

        wasm_refs = _scan_js_ts_for_wasm_loading(file_path)
        for wasm_ref in wasm_refs:
            dedup_key = (sym.path, wasm_ref)
            if dedup_key in seen_wasm_refs:
                continue
            seen_wasm_refs.add(dedup_key)

            # Create synthetic WASM module symbol
            wasm_module_id = f"wasm:{wasm_ref}:0-0:module:wasm_module"

            if wasm_module_id not in {s.id for s in symbols}:
                # ADR-0031 Class B: WASM module synthetic — has no host
                # discovery context (it's referenced by URL/path). Was
                # LITERAL-SENTINEL ("wasm" — not a real language tag).
                symbols.append(Symbol(
                    id=wasm_module_id,
                    stable_id=None,
                    shape_id=None,
                    display_label=f"WASM module: {wasm_ref}",  # ADR-0032
                    # ADR-0032: producer-side Format 1 fingerprint dropped; central post-pass stamps Format 2.
                    kind="module",
                    name=wasm_ref,
                    path=wasm_ref,
                    language=None,
                    protocol_origin="wasm",
                    span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={"compilation_target": "wasm"},
                    supply_chain_reason="WASM module loaded dynamically",
                ))

            # Create wasm_load edge from JS file to WASM module
            rel_path = sym.path
            try:
                rel_path = str(Path(sym.path).relative_to(repo_root))
            except ValueError:
                pass

            src_id = f"{sym.language}:{rel_path}:0-0:file:file"
            # ADR-0023 §6 Phase 3 (WI-mifor-vabul): wasm_load is a
            # file→module relationship (not a call), parallel to module
            # imports. Fold to canonical 'imports'; the dst's
            # ``kind == 'module'`` + ``meta['compilation_target']='wasm'``
            # carries the WASM-specificity (Wave 6 PR 3 fold).
            edges.append(Edge.create(
                src=src_id,
                dst=wasm_module_id,
                edge_type="imports",
                line=0,
                confidence=0.80,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="ast_call_direct",
                meta={"framework_dispatch": "wasm_instantiate"},
                derived_from=[src_id, wasm_module_id],
            ))

    return edges, symbols


def _count_js_ts_files(ctx: LinkerContext) -> int:
    """Count JavaScript/TypeScript files."""
    seen_paths: set[str] = set()
    for sym in ctx.symbols:
        if sym.language in ("javascript", "typescript"):
            if sym.path not in seen_paths:
                seen_paths.add(sym.path)
    return len(seen_paths)


def _count_wasm_bindgen_functions(ctx: LinkerContext) -> int:
    """Count Rust symbols with #[wasm_bindgen] annotation."""
    count = 0
    for sym in ctx.symbols:
        if sym.language != "rust" or sym.kind not in ("function", "method"):
            continue
        if sym.meta is None:
            continue
        annotations = sym.meta.get("annotations")
        if not annotations or not isinstance(annotations, list):
            continue
        for ann in annotations:
            if isinstance(ann, dict) and ann.get("name") == "wasm_bindgen":
                count += 1
                break
    return count


WASM_BINDGEN_REQUIREMENTS = [
    LinkerRequirement(
        name="js_ts_files",
        description="JavaScript/TypeScript files (potential wasm import callers)",
        check=_count_js_ts_files,
    ),
    LinkerRequirement(
        name="wasm_bindgen_functions",
        description="Rust #[wasm_bindgen] functions (WASM exports)",
        check=_count_wasm_bindgen_functions,
    ),
]


@register_linker(
    "wasm-bindgen-linker",
    priority=14,  # Same tier as Tauri IPC
    description=(
        "wasm-bindgen bridge - links JavaScript/TypeScript imports "
        "to Rust #[wasm_bindgen] exported functions"
    ),
    requirements=WASM_BINDGEN_REQUIREMENTS,
    activation=LinkerActivation(
        language_pairs=[
            ("typescript", "rust"),
            ("javascript", "rust"),
        ],
    ),
    # CNF: javascript (JS/TS share the analyzer pass id) AND rust — both
    # required for wasm-bindgen's import ↔ #[wasm_bindgen] bridge.
    depends_on=[["javascript"], ["rust"]],
)
def wasm_bindgen_linker(ctx: LinkerContext) -> LinkerResult:
    """wasm_bindgen linker for registry-based dispatch.

    Wraps link_wasm_bindgen() and adds dynamic WASM loading detection
    (WebAssembly.instantiate, URL imports, Emscripten patterns).
    """
    ts_js_symbols = [
        s for s in ctx.symbols if s.language in ("javascript", "typescript")
    ]
    rust_symbols = [s for s in ctx.symbols if s.language == "rust"]

    result = link_wasm_bindgen(ctx.repo_root, ts_js_symbols, rust_symbols)

    # Also detect dynamic WASM loading patterns
    load_edges, load_symbols = _create_wasm_load_edges(
        ctx.repo_root, ts_js_symbols, result.run or AnalysisRun.create(
            pass_id=PASS_ID, version=PASS_VERSION,
        ),
    )

    return LinkerResult(
        symbols=result.symbols + load_symbols,
        edges=result.edges + load_edges,
        run=result.run,
    )
