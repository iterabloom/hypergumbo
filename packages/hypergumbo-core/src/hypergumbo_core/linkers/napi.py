"""Node.js N-API linker for connecting JavaScript/TypeScript calls to C/C++ addon functions.

This linker creates napi_bridge edges between JavaScript/TypeScript code that
calls into native addons and the C/C++ functions that register those exports
via the N-API or node-addon-api interfaces.

How It Works
------------
Two-phase source scanning of C/C++ files:

1. **N-API (C)**: Scans for ``napi_create_function(env, "funcName", ...)``
   which registers a C function under a string name. The ``napi_set_named_property``
   call exports that function on the addon's exports object. The linker maps
   the registered string name to the C callback function and the export name
   to the JS call target.

2. **node-addon-api (C++)**: Scans for ``Napi::Function::New(env, FuncRef)``
   inside ``exports.Set("name", ...)`` calls, and ``InstanceMethod("name", &Class::Method)``
   declarations. These register C++ functions under JS-visible names.

After building a map of {js_export_name -> C/C++ symbol}, the linker iterates
unresolved JS/TS call edges and resolves them against the export map.

Why This Design
---------------
- N-API addons expose C/C++ functions under string names chosen at registration
  time, not at the function definition. Source scanning is required because the
  string names appear only in the init function, not in the function signatures.
- Simple regex matching is sufficient: the patterns are syntactically rigid
  (N-API macros, node-addon-api template calls).
- Follows the source-scanning pattern established by pyffi and ruby_ffi linkers.
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

PASS_ID = make_pass_id("napi-linker")

# --- N-API C patterns ---
# napi_create_function(env, "funcName", NAPI_AUTO_LENGTH, CCallback, NULL, &result)
# Captures: group(1) = JS export name, group(2) = C callback function name
_NAPI_CREATE_FUNCTION_RE = re.compile(
    r'napi_create_function\s*\(\s*\w+\s*,\s*"(\w+)"\s*,'
    r'\s*\w+\s*,\s*(\w+)',
)

# napi_set_named_property(env, exports, "name", value)
# Captures: group(1) = export name
_NAPI_SET_NAMED_PROPERTY_RE = re.compile(
    r'napi_set_named_property\s*\(\s*\w+\s*,\s*\w+\s*,\s*"(\w+)"',
)

# --- node-addon-api C++ patterns ---
# exports.Set("name", Napi::Function::New(env, CppFunc))
# Captures: group(1) = JS export name, group(2) = C++ function reference
_NAPI_EXPORTS_SET_RE = re.compile(
    r'(?:exports|target)\s*\.\s*Set\s*\(\s*"(\w+)"\s*,\s*'
    r'Napi::Function::New\s*\(\s*\w+\s*,\s*(\w+)',
)

# InstanceMethod("jsName", &ClassName::MethodName)
# Captures: group(1) = JS method name, group(2) = C++ class::method
_INSTANCE_METHOD_RE = re.compile(
    r'InstanceMethod\s*\(\s*"(\w+)"\s*,\s*&(\w+::\w+)',
)

# StaticMethod("jsName", &ClassName::MethodName) — same pattern
_STATIC_METHOD_RE = re.compile(
    r'StaticMethod\s*\(\s*"(\w+)"\s*,\s*&(\w+::\w+)',
)


@dataclass
class NAPILinkResult:
    """Result of N-API linking."""

    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


def _scan_c_cpp_file_for_napi_exports(
    file_path: Path,
) -> list[tuple[str, str, str]]:
    """Scan a C/C++ file for N-API export registrations.

    Returns a list of (js_name, c_cpp_name, evidence_type) tuples where:
    - js_name: The name exported to JavaScript
    - c_cpp_name: The C/C++ function/method name implementing it
    - evidence_type: 'napi_create_function' or 'napi_addon_api'
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive for I/O errors
        return []

    results: list[tuple[str, str, str]] = []

    # Phase 1: N-API C patterns
    for match in _NAPI_CREATE_FUNCTION_RE.finditer(content):
        js_name = match.group(1)
        c_callback = match.group(2)
        results.append((js_name, c_callback, "napi_create_function"))

    # Phase 2: node-addon-api C++ patterns
    for match in _NAPI_EXPORTS_SET_RE.finditer(content):
        js_name = match.group(1)
        cpp_func = match.group(2)
        results.append((js_name, cpp_func, "napi_addon_api"))

    for match in _INSTANCE_METHOD_RE.finditer(content):
        js_name = match.group(1)
        cpp_method = match.group(2)
        results.append((js_name, cpp_method, "napi_addon_api"))

    for match in _STATIC_METHOD_RE.finditer(content):
        js_name = match.group(1)
        cpp_method = match.group(2)
        results.append((js_name, cpp_method, "napi_addon_api"))

    return results


def link_napi(
    repo_root: Path,
    js_symbols: list[Symbol],
    c_cpp_symbols: list[Symbol],
    edges: list[Edge],
) -> NAPILinkResult:
    """Link N-API exports to their JavaScript/TypeScript callers.

    Args:
        repo_root: Repository root path.
        js_symbols: JavaScript/TypeScript symbols from analyzers.
        c_cpp_symbols: C and C++ symbols from analyzers.
        edges: All existing edges (used for unresolved call matching).

    Returns:
        NAPILinkResult with napi_bridge edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    result_edges: list[Edge] = []
    seen_edges: set[tuple[str, str]] = set()  # (src_id, js_name) dedup

    # Build lookup for C/C++ functions by name
    c_cpp_lookup: dict[str, Symbol] = {}
    for sym in c_cpp_symbols:
        if sym.kind in ("function", "method"):
            c_cpp_lookup[sym.name] = sym

    # Scan C/C++ files for N-API export registrations
    # Build map: js_export_name -> (c_cpp_symbol, evidence_type)
    export_map: dict[str, tuple[Symbol, str]] = {}

    c_cpp_files: set[str] = set()
    for sym in c_cpp_symbols:
        if sym.language in ("c", "cpp") and sym.kind in ("function", "method"):
            c_cpp_files.add(sym.path)

    for c_path_str in c_cpp_files:
        c_path = Path(c_path_str)
        if not c_path.is_absolute():
            c_path = repo_root / c_path

        if not c_path.exists():
            continue

        napi_exports = _scan_c_cpp_file_for_napi_exports(c_path)

        for js_name, c_cpp_name, evidence_type in napi_exports:
            # Look up the C/C++ symbol by function name
            target_sym = c_cpp_lookup.get(c_cpp_name)
            if target_sym is not None:
                export_map[js_name] = (target_sym, evidence_type)

    # Match unresolved JS/TS edges against the export map
    for edge in edges:
        if not edge.dst.endswith(":unresolved"):
            continue

        # Extract the function name from the unresolved dst
        # Format: "javascript:path:0-0:name:unresolved"
        parts = edge.dst.split(":")
        if len(parts) < 5:
            continue
        call_name = parts[-2]  # second to last

        if call_name not in export_map:
            continue

        target_sym, evidence_type = export_map[call_name]

        dedup_key = (edge.src, call_name)
        if dedup_key in seen_edges:
            continue
        seen_edges.add(dedup_key)

        result_edges.append(Edge.create(
            src=edge.src,
            dst=target_sym.id,
            edge_type="napi_bridge",
            line=edge.line,
            confidence=0.85,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type=evidence_type,
        ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return NAPILinkResult(edges=result_edges, run=run)


def _count_js_ts_files(ctx: LinkerContext) -> int:
    """Count JavaScript/TypeScript files that might import native addons."""
    count = 0
    seen_paths: set[str] = set()
    for sym in ctx.symbols:
        if sym.language in ("javascript", "typescript"):
            if sym.path not in seen_paths:
                seen_paths.add(sym.path)
                count += 1
    return count


def _count_c_cpp_functions(ctx: LinkerContext) -> int:
    """Count C/C++ function symbols available for N-API linking."""
    count = 0
    for sym in ctx.symbols:
        if sym.language in ("c", "cpp") and sym.kind in ("function", "method"):
            count += 1
    return count


NAPI_REQUIREMENTS = [
    LinkerRequirement(
        name="js_ts_files",
        description="JavaScript/TypeScript files (potential native addon callers)",
        check=_count_js_ts_files,
    ),
    LinkerRequirement(
        name="c_cpp_functions",
        description="C/C++ function implementations (potential N-API addon sources)",
        check=_count_c_cpp_functions,
    ),
]


@register_linker(
    "napi",
    priority=13,  # After JNI (10), before cgo (15)
    description="Node.js N-API bridge - links JavaScript/TypeScript calls to C/C++ native addon functions",
    requirements=NAPI_REQUIREMENTS,
    activation=LinkerActivation(
        language_pairs=[
            ("javascript", "c"),
            ("javascript", "cpp"),
            ("typescript", "c"),
            ("typescript", "cpp"),
        ],
    ),
)
def napi_linker(ctx: LinkerContext) -> LinkerResult:
    """N-API linker for registry-based dispatch.

    Wraps link_napi() to use the LinkerContext/LinkerResult interface.
    """
    js_symbols = [
        s for s in ctx.symbols if s.language in ("javascript", "typescript")
    ]
    c_cpp_symbols = [s for s in ctx.symbols if s.language in ("c", "cpp")]

    result = link_napi(ctx.repo_root, js_symbols, c_cpp_symbols, ctx.edges)

    return LinkerResult(
        symbols=[],
        edges=result.edges,
        run=result.run,
    )
