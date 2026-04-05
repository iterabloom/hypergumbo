# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ruby FFI linker for connecting Ruby FFI gem calls and C extension registrations
to C/C++ function implementations.

This linker creates ffi_bridge edges for two Ruby-C/C++ interop mechanisms:

1. **FFI gem**: Scans Ruby files for ``attach_function`` declarations within modules
   that ``extend FFI::Library``. Extracts the C function name and matches it to
   C/C++ function symbols.

2. **C extensions**: Scans C/C++ files for ``rb_define_method``,
   ``rb_define_module_function``, and ``rb_define_singleton_method`` calls, which
   register C functions as Ruby methods. Matches the C function argument to
   C/C++ function symbols.

How It Works
------------
Phase 1 (Ruby FFI gem):
  Scans ``.rb`` files for ``attach_function :name, ...`` or
  ``attach_function :ruby_name, :c_name, ...`` patterns. When the C function
  name matches a C/C++ function symbol, creates an ``ffi_bridge`` edge from
  the enclosing Ruby symbol to the C function.

Phase 2 (C extensions):
  Scans ``.c`` and ``.cpp`` files for ``rb_define_method(klass, "name", c_func, argc)``
  and variants. When the C function argument matches a C/C++ function symbol,
  creates an ``ffi_bridge`` edge.

Why This Design
---------------
- Follows the same source-scanning approach as the Python FFI linker
- Two distinct FFI patterns (gem vs C extension) are common in Ruby ecosystems
- Gems like nokogiri, ffi, pg, and bcrypt use one or both patterns
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

PASS_ID = make_pass_id("ruby-ffi-linker")

# Pseudo-namespace prefix for FFI calls to external shared libraries.
# Follows the cgo/pyffi pattern so the io_boundary tagger can redirect
# to the C catalog via _resolve_ffi_catalog().
RUBY_FFI_STDLIB_PREFIX = "ruby:C_ffi:0-0:"

# Regex for attach_function declarations in Ruby FFI gem
# Matches:
#   attach_function :name, [params], :return
#   attach_function :ruby_name, :c_name, [params], :return
#   attach_function 'name', [params], :return
#   attach_function 'ruby_name', 'c_name', [params], :return
_ATTACH_FUNCTION_RE = re.compile(
    r"attach_function\s+"
    r"['\"]?:?(\w+)['\"]?"           # first arg (Ruby name or C name)
    r"(?:\s*,\s*['\"]?:?(\w+)['\"]?)?"  # optional second arg (C name if different)
    r"\s*,"                           # comma before param list
)

# Regex for rb_define_method and variants in C extension code
# Matches:
#   rb_define_method(klass, "method_name", c_function, argc)
#   rb_define_module_function(mod, "method_name", c_function, argc)
#   rb_define_singleton_method(klass, "method_name", c_function, argc)
_RB_DEFINE_RE = re.compile(
    r'rb_define_(?:method|module_function|singleton_method)\s*\(\s*'
    r'\w+\s*,\s*'                     # klass/mod variable
    r'"(\w+)"\s*,\s*'                 # "method_name"
    r'(\w+)\s*,'                      # c_function
)


@dataclass
class RubyFFILinkResult:
    """Result of Ruby FFI linking."""

    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


def _scan_ruby_file_for_ffi(
    file_path: Path,
) -> list[tuple[str, int]]:
    """Scan a Ruby file for attach_function declarations.

    Returns a list of (c_func_name, line_number) tuples.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive for I/O errors
        return []

    results: list[tuple[str, int]] = []

    for line_num, line in enumerate(content.splitlines(), start=1):
        match = _ATTACH_FUNCTION_RE.search(line)
        if match:
            first_name = match.group(1)
            second_name = match.group(2)
            # If second_name exists, it's the C function name
            # If only first_name, it's both the Ruby and C name
            c_name = second_name if second_name else first_name
            results.append((c_name, line_num))

    return results


def _scan_c_file_for_rb_define(
    file_path: Path,
    c_lookup: dict[str, Symbol],
) -> list[tuple[Symbol, int]]:
    """Scan a C file for rb_define_method/module_function/singleton_method calls.

    Returns a list of (c_symbol, line_number) tuples for matched functions.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive for I/O errors
        return []

    results: list[tuple[Symbol, int]] = []

    for line_num, line in enumerate(content.splitlines(), start=1):
        match = _RB_DEFINE_RE.search(line)
        if match:
            c_func_name = match.group(2)
            if c_func_name in c_lookup:
                results.append((c_lookup[c_func_name], line_num))

    return results


def link_ruby_ffi(
    repo_root: Path,
    ruby_symbols: list[Symbol],
    c_symbols: list[Symbol],
    edges: list[Edge],
) -> RubyFFILinkResult:
    """Link Ruby FFI calls and C extension registrations to C/C++ implementations.

    Args:
        repo_root: Repository root path
        ruby_symbols: Ruby symbols from the analyzer
        c_symbols: C and C++ symbols from analyzers
        edges: All existing edges (not used currently, kept for interface consistency)

    Returns:
        RubyFFILinkResult with ffi_bridge edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    result_edges: list[Edge] = []
    seen_edges: set[tuple[str, str]] = set()  # (src_id, func_name) dedup

    # Build lookup for C/C++ functions by name
    c_lookup: dict[str, Symbol] = {}
    for sym in c_symbols:
        if sym.kind == "function":
            c_lookup[sym.name] = sym

    # --- Phase 1: Scan Ruby files for FFI gem attach_function ---
    ruby_files: set[str] = set()
    for sym in ruby_symbols:
        if sym.language == "ruby" and sym.path.endswith(".rb"):
            ruby_files.add(sym.path)

    for rb_path_str in ruby_files:
        rb_path = Path(rb_path_str)
        if not rb_path.is_absolute():
            rb_path = repo_root / rb_path

        if not rb_path.exists():
            continue

        ffi_calls = _scan_ruby_file_for_ffi(rb_path)

        for func_name, line_num in ffi_calls:
            # Find the enclosing Ruby symbol for this file
            src_sym = None
            for sym in ruby_symbols:
                if sym.path == rb_path_str:
                    if src_sym is None:
                        src_sym = sym
                    elif sym.span and src_sym.span:
                        if (
                            sym.span.start_line <= line_num <= sym.span.end_line
                            and (sym.span.end_line - sym.span.start_line)
                            < (src_sym.span.end_line - src_sym.span.start_line)
                        ):
                            src_sym = sym

            if src_sym is None:  # pragma: no cover - defensive
                continue

            dedup_key = (src_sym.id, func_name)
            if dedup_key in seen_edges:
                continue
            seen_edges.add(dedup_key)

            if func_name in c_lookup:
                # Resolved: repo-local C symbol
                c_sym = c_lookup[func_name]
                result_edges.append(Edge.create(
                    src=src_sym.id,
                    dst=c_sym.id,
                    edge_type="ffi_bridge",
                    line=line_num,
                    confidence=0.90,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ruby_ffi_attach",
                    access_mode="write",
                    dest_access_mode="read",
                ))
            else:
                # Unresolved: external library (e.g., libzmq, libc)
                dst = f"{RUBY_FFI_STDLIB_PREFIX}{func_name}:unresolved"
                result_edges.append(Edge.create(
                    src=src_sym.id,
                    dst=dst,
                    edge_type="ffi_bridge",
                    line=line_num,
                    confidence=0.75,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ruby_ffi_attach_unresolved",
                    access_mode="write",
                    dest_access_mode="read",
                ))

    # --- Phase 2: Scan C files for rb_define_method patterns ---
    c_files: set[str] = set()
    for sym in c_symbols:
        if sym.language in ("c", "cpp") and (
            sym.path.endswith(".c") or sym.path.endswith(".cpp")
        ):
            c_files.add(sym.path)

    for c_path_str in c_files:
        c_path = Path(c_path_str)
        if not c_path.is_absolute():
            c_path = repo_root / c_path

        if not c_path.exists():
            continue

        rb_defines = _scan_c_file_for_rb_define(c_path, c_lookup)

        for c_sym, line_num in rb_defines:
            dedup_key = ("rb_define", c_sym.name)
            if dedup_key in seen_edges:
                continue  # pragma: no cover - rare dedup
            seen_edges.add(dedup_key)

            # For C extension patterns, the "src" is a synthetic registration
            # and the "dst" is the C function. We use the C function's own
            # enclosing file as source context.
            result_edges.append(Edge.create(
                src=c_sym.id,
                dst=c_sym.id,
                edge_type="ffi_bridge",
                line=line_num,
                confidence=0.85,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="ruby_c_extension",
                access_mode="write",
                dest_access_mode="read",
            ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return RubyFFILinkResult(edges=result_edges, run=run)


def _count_ruby_files(ctx: LinkerContext) -> int:
    """Count Ruby files that might contain FFI calls."""
    seen: set[str] = set()
    for sym in ctx.symbols:
        if sym.language == "ruby" and sym.path.endswith(".rb"):
            if sym.path not in seen:
                seen.add(sym.path)
    return len(seen)


def _count_c_cpp_functions(ctx: LinkerContext) -> int:
    """Count C/C++ function symbols available for linking."""
    count = 0
    for sym in ctx.symbols:
        if sym.language in ("c", "cpp") and sym.kind == "function":
            count += 1
    return count


RUBY_FFI_REQUIREMENTS = [
    LinkerRequirement(
        name="ruby_files",
        description="Ruby files (potential FFI gem or C extension callers)",
        check=_count_ruby_files,
    ),
    LinkerRequirement(
        name="c_cpp_functions",
        description="C/C++ function implementations",
        check=_count_c_cpp_functions,
    ),
]


@register_linker(
    "ruby_ffi",
    priority=17,  # After pyffi (16), before protocol linkers
    description="Ruby/C/C++ FFI bridge - links FFI gem attach_function and C extension rb_define_method to native implementations",
    requirements=RUBY_FFI_REQUIREMENTS,
    activation=LinkerActivation(
        language_pairs=[("ruby", "c"), ("ruby", "cpp")],
    ),
)
def ruby_ffi_linker(ctx: LinkerContext) -> LinkerResult:
    """Ruby FFI linker for registry-based dispatch.

    Wraps link_ruby_ffi() to use the LinkerContext/LinkerResult interface.
    """
    ruby_symbols = [s for s in ctx.symbols if s.language == "ruby"]
    c_symbols = [s for s in ctx.symbols if s.language in ("c", "cpp")]

    result = link_ruby_ffi(ctx.repo_root, ruby_symbols, c_symbols, ctx.edges)

    return LinkerResult(
        symbols=[],
        edges=result.edges,
        run=result.run,
    )
