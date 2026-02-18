"""Lua FFI linker for connecting LuaJIT FFI calls to C function implementations.

This linker creates ffi_bridge edges between Lua code that uses LuaJIT's FFI
interface and the C function implementations those calls resolve to.

How It Works
------------
Source scanning of Lua files for two FFI call patterns:

1. **ffi.C.funcname()**: Direct calls to the default C namespace. The linker
   finds ``ffi.C.<name>(`` patterns and matches ``<name>`` against C function
   symbols. This covers the common case where ``ffi.cdef`` declares functions
   that are then called via ``ffi.C``.

2. **ffi.load("libname")**: Loads a shared library into a local variable
   (``local lib = ffi.load("mylib")``). The linker tracks the variable name
   and finds ``lib.<name>(`` calls, matching ``<name>`` against C symbols.

After scanning, the linker also checks unresolved Lua call edges and resolves
any whose name matches a C function symbol (for cases where the Lua analyzer
produced an unresolved edge for an FFI call).

Why This Design
---------------
- LuaJIT FFI uses string-based function names that only appear in ffi.cdef or
  ffi.C.name call sites, not in function signatures. Source scanning is needed.
- The ffi.cdef content is a C header string — we don't parse it, we just track
  that it exists and match ffi.C.<name> calls against actual C symbols.
- Simple regex matching is sufficient: ``ffi.C.name(`` and ``var.name(`` are
  syntactically rigid patterns.
- Follows the source-scanning pattern established by pyffi and ruby_ffi linkers.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..ir import AnalysisRun, Edge, Symbol
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
)

PASS_ID = "lua-ffi-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# ffi.C.funcname( — direct call to the default C namespace
_FFI_C_CALL_RE = re.compile(
    r'ffi\.C\.(\w+)\s*\(',
)

# local lib = ffi.load("libname") or lib = ffi.load('libname')
# Captures: group(1) = variable name, group(2) = library name
_FFI_LOAD_RE = re.compile(
    r'(?:local\s+)?(\w+)\s*=\s*ffi\.load\s*\(\s*["\'](\w+)["\']\s*\)',
)

# Template for attribute calls on a loaded library variable: var.funcname(
_LIB_CALL_RE_TEMPLATE = r'{var}\.(\w+)\s*\('


@dataclass
class LuaFFILinkResult:
    """Result of Lua FFI linking."""

    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


def _scan_lua_file_for_ffi_calls(
    file_path: Path,
) -> list[tuple[str, str, int]]:
    """Scan a Lua file for LuaJIT FFI calls.

    Returns a list of (func_name, evidence_type, line_number) tuples.
    evidence_type is 'luajit_ffi_c' or 'luajit_ffi_load'.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive for I/O errors
        return []

    lines = content.splitlines()
    results: list[tuple[str, str, int]] = []

    # Track ffi.load variable names
    lib_vars: set[str] = set()

    for line_num, line in enumerate(lines, start=1):
        # Detect ffi.load("libname")
        load_match = _FFI_LOAD_RE.search(line)
        if load_match:
            var_name = load_match.group(1)
            lib_vars.add(var_name)

        # Detect ffi.C.funcname() calls
        for match in _FFI_C_CALL_RE.finditer(line):
            func_name = match.group(1)
            if func_name.startswith("_"):
                continue
            results.append((func_name, "luajit_ffi_c", line_num))

        # Detect lib.funcname() calls on loaded libraries
        for var_name in lib_vars:
            lib_re = re.compile(_LIB_CALL_RE_TEMPLATE.format(var=re.escape(var_name)))
            for match in lib_re.finditer(line):
                func_name = match.group(1)
                if func_name.startswith("_"):
                    continue
                results.append((func_name, "luajit_ffi_load", line_num))

    return results


def link_lua_ffi(
    repo_root: Path,
    lua_symbols: list[Symbol],
    c_symbols: list[Symbol],
    edges: list[Edge],
) -> LuaFFILinkResult:
    """Link Lua FFI calls to their C function implementations.

    Args:
        repo_root: Repository root path.
        lua_symbols: Lua symbols from the analyzer.
        c_symbols: C and C++ symbols from analyzers.
        edges: All existing edges (used for unresolved call matching).

    Returns:
        LuaFFILinkResult with ffi_bridge edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    result_edges: list[Edge] = []
    seen_edges: set[tuple[str, str]] = set()  # (src_id, func_name) dedup

    # Build lookup for C/C++ functions by name
    c_lookup: dict[str, Symbol] = {}
    for sym in c_symbols:
        if sym.kind in ("function", "method"):
            c_lookup[sym.name] = sym

    # --- Phase 1: Scan Lua files for ffi.C / ffi.load calls ---
    lua_files: set[str] = set()
    for sym in lua_symbols:
        if sym.language == "lua":
            lua_files.add(sym.path)

    for lua_path_str in lua_files:
        lua_path = Path(lua_path_str)
        if not lua_path.is_absolute():
            lua_path = repo_root / lua_path

        if not lua_path.exists():
            continue

        ffi_calls = _scan_lua_file_for_ffi_calls(lua_path)

        for func_name, evidence_type, line_num in ffi_calls:
            if func_name not in c_lookup:
                continue

            # Find the enclosing Lua symbol for this file
            src_sym = None
            for sym in lua_symbols:
                if sym.path == lua_path_str:
                    if src_sym is None:
                        src_sym = sym
                    elif sym.span and src_sym.span:
                        # Prefer the symbol that encloses this line
                        if (
                            sym.span.start_line <= line_num <= sym.span.end_line
                            and (
                                src_sym.span.start_line > line_num
                                or src_sym.span.end_line < line_num
                                or (sym.span.end_line - sym.span.start_line)
                                < (src_sym.span.end_line - src_sym.span.start_line)
                            )
                        ):
                            src_sym = sym

            if src_sym is None:  # pragma: no cover - defensive (path in set implies symbol exists)
                continue

            dedup_key = (src_sym.id, func_name)
            if dedup_key in seen_edges:
                continue
            seen_edges.add(dedup_key)

            c_sym = c_lookup[func_name]
            result_edges.append(Edge.create(
                src=src_sym.id,
                dst=c_sym.id,
                edge_type="ffi_bridge",
                line=line_num,
                confidence=0.85,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type=evidence_type,
            ))

    # --- Phase 2: Match unresolved Lua call edges against C symbols ---
    for edge in edges:
        if not edge.dst.endswith(":unresolved"):
            continue

        parts = edge.dst.split(":")
        if len(parts) < 5:
            continue
        call_name = parts[-2]  # second to last

        if call_name not in c_lookup:
            continue

        dedup_key = (edge.src, call_name)
        if dedup_key in seen_edges:
            continue
        seen_edges.add(dedup_key)

        c_sym = c_lookup[call_name]
        result_edges.append(Edge.create(
            src=edge.src,
            dst=c_sym.id,
            edge_type="ffi_bridge",
            line=edge.line,
            confidence=0.85,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="luajit_ffi_unresolved",
        ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return LuaFFILinkResult(edges=result_edges, run=run)


def _count_lua_files(ctx: LinkerContext) -> int:
    """Count Lua files that might use FFI."""
    count = 0
    seen_paths: set[str] = set()
    for sym in ctx.symbols:
        if sym.language == "lua":
            if sym.path not in seen_paths:
                seen_paths.add(sym.path)
                count += 1
    return count


def _count_c_functions(ctx: LinkerContext) -> int:
    """Count C/C++ function symbols available for FFI linking."""
    count = 0
    for sym in ctx.symbols:
        if sym.language in ("c", "cpp") and sym.kind in ("function", "method"):
            count += 1
    return count


LUA_FFI_REQUIREMENTS = [
    LinkerRequirement(
        name="lua_files",
        description="Lua files (potential LuaJIT FFI callers)",
        check=_count_lua_files,
    ),
    LinkerRequirement(
        name="c_functions",
        description="C/C++ function implementations (FFI targets)",
        check=_count_c_functions,
    ),
]


@register_linker(
    "lua_ffi",
    priority=17,  # After pyffi (16), before protocol linkers
    description="Lua FFI bridge - links LuaJIT ffi.C/ffi.load calls to C function implementations",
    requirements=LUA_FFI_REQUIREMENTS,
    activation=LinkerActivation(
        language_pairs=[("lua", "c"), ("lua", "cpp")],
    ),
)
def lua_ffi_linker(ctx: LinkerContext) -> LinkerResult:
    """Lua FFI linker for registry-based dispatch.

    Wraps link_lua_ffi() to use the LinkerContext/LinkerResult interface.
    """
    lua_symbols = [s for s in ctx.symbols if s.language == "lua"]
    c_symbols = [s for s in ctx.symbols if s.language in ("c", "cpp")]

    result = link_lua_ffi(ctx.repo_root, lua_symbols, c_symbols, ctx.edges)

    return LinkerResult(
        symbols=[],
        edges=result.edges,
        run=result.run,
    )
