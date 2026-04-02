# SPDX-License-Identifier: AGPL-3.0-or-later
"""Python FFI linker for connecting Python ctypes/cffi calls to C/C++ implementations
and PyO3 Rust functions to their Python callers.

This linker creates ffi_bridge edges between Python code that calls native functions
via ctypes or cffi and the corresponding C/C++ function implementations, as well as
between Python code that imports PyO3-annotated Rust functions.

How It Works
------------
Three FFI mechanisms are detected by scanning Python source files:

1. **ctypes**: Scans for ``ctypes.CDLL`` / ``ctypes.cdll.LoadLibrary`` patterns,
   then finds ``lib.funcname()`` attribute calls on the loaded library variable.
   Matches funcname against C/C++ function symbols.

2. **cffi**: Scans for ``ffi.dlopen()`` / ``ffi.verify()`` patterns, then finds
   ``lib.funcname()`` attribute calls. Same name-matching as ctypes.

3. **PyO3**: Finds Rust symbols with ``pyfunction`` or ``pymethods`` in their
   decorators metadata. When Python code has unresolved call edges whose name
   matches a PyO3-annotated Rust function, creates ffi_bridge edges.

Why This Design
---------------
- Follows the analyze-then-link pattern established by JNI and cgo linkers
- Source scanning for ctypes/cffi is necessary because Python's dynamic nature
  means these calls don't produce typed call edges from the analyzer
- PyO3 detection piggybacks on the Rust analyzer's decorator metadata
- Simple name matching is sufficient: ctypes/cffi use the raw C function name
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

PASS_ID = make_pass_id("pyffi-linker")

# Regex patterns for detecting ctypes library loading
# Matches: ctypes.CDLL(...), ctypes.cdll.LoadLibrary(...), ctypes.WinDLL(...),
#          ctypes.OleDLL(...), ctypes.PyDLL(...)
_CTYPES_LOAD_RE = re.compile(
    r'(\w+)\s*=\s*ctypes\.(?:CDLL|cdll\.LoadLibrary|WinDLL|OleDLL|PyDLL)\s*\(',
)

# Regex for detecting cffi library loading
# Matches: lib = ffi.dlopen(...), lib = ffi.verify(...)
_CFFI_LOAD_RE = re.compile(
    r'(\w+)\s*=\s*\w+\.(?:dlopen|verify)\s*\(',
)

# Regex for detecting cffi import (from cffi import FFI)
_CFFI_IMPORT_RE = re.compile(
    r'(?:from\s+cffi\s+import|import\s+cffi)',
)

# Regex for detecting attribute calls on a variable: var.funcname(
_ATTR_CALL_RE_TEMPLATE = r'{var}\.(\w+)\s*\('

# PyO3 attribute names that indicate Python-exposed Rust code.
# The Rust analyzer stores the outer attribute crate name ("pyo3") rather
# than the specific attribute ("pyfunction", "pymethods"), so we match both.
_PYO3_ANNOTATIONS = frozenset({"pyfunction", "pymethods", "pyclass", "pyo3"})


@dataclass
class PyFFILinkResult:
    """Result of Python FFI linking."""

    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


def _scan_python_file_for_ffi_calls(
    file_path: Path,
) -> list[tuple[str, str, int]]:
    """Scan a Python file for ctypes/cffi attribute calls on loaded libraries.

    Returns a list of (func_name, evidence_type, line_number) tuples.
    evidence_type is 'ctypes_call' or 'cffi_call'.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive for I/O errors
        return []

    lines = content.splitlines()
    results: list[tuple[str, str, int]] = []

    # Track library variable names and their FFI type
    lib_vars: dict[str, str] = {}  # var_name -> evidence_type

    # Check for cffi import presence (needed to disambiguate ffi.dlopen from other .dlopen)
    has_cffi = bool(_CFFI_IMPORT_RE.search(content))

    for line_num, line in enumerate(lines, start=1):
        # Detect ctypes library loading
        ctypes_match = _CTYPES_LOAD_RE.search(line)
        if ctypes_match:
            var_name = ctypes_match.group(1)
            lib_vars[var_name] = "ctypes_call"
            continue

        # Detect cffi library loading (only if cffi is imported)
        if has_cffi:
            cffi_match = _CFFI_LOAD_RE.search(line)
            if cffi_match:
                var_name = cffi_match.group(1)
                lib_vars[var_name] = "cffi_call"
                continue

        # Detect attribute calls on known library variables
        for var_name, evidence_type in lib_vars.items():
            attr_re = re.compile(_ATTR_CALL_RE_TEMPLATE.format(var=re.escape(var_name)))
            for match in attr_re.finditer(line):
                func_name = match.group(1)
                # Skip dunder methods and common non-FFI attributes
                if func_name.startswith("_"):
                    continue
                results.append((func_name, evidence_type, line_num))

    return results


def _find_pyo3_symbols(rust_symbols: list[Symbol]) -> dict[str, Symbol]:
    """Find Rust symbols annotated with PyO3 attributes (#[pyfunction], #[pymethods]).

    The Rust analyzer stores attributes in meta["annotations"] as a list of dicts
    with "name", "args", "kwargs" keys (matching tree-sitter extraction format).

    Returns a dict mapping function name (last component) to Symbol.
    """
    pyo3_lookup: dict[str, Symbol] = {}

    for sym in rust_symbols:
        if sym.kind not in ("function", "method"):
            continue
        if sym.meta is None:
            continue
        annotations = sym.meta.get("annotations", [])
        if not isinstance(annotations, list):  # pragma: no cover - defensive for malformed meta
            continue
        if any(
            isinstance(a, dict) and a.get("name") in _PYO3_ANNOTATIONS
            for a in annotations
        ):
            # Register by multiple name variants for flexible matching:
            # "PyTokenizer::encode" → keys: full name, short name (after ::),
            # and Python-style name (strip Py prefix, replace :: with .)
            full_name = sym.name
            # Short name: last component after :: or .
            if "::" in full_name:
                short_name = full_name.rsplit("::", 1)[-1]
            elif "." in full_name:
                short_name = full_name.rsplit(".", 1)[-1]
            else:
                short_name = full_name
            pyo3_lookup[full_name] = sym
            pyo3_lookup[short_name] = sym
            # Python-style: strip Py prefix from class, use .method
            # PyTokenizer::encode → Tokenizer.encode
            if "::" in full_name:
                parts = full_name.split("::")
                cls = parts[0]
                if cls.startswith("Py"):
                    cls = cls[2:]
                py_style = f"{cls}.{parts[-1]}" if len(parts) > 1 else cls
                pyo3_lookup[py_style] = sym

    return pyo3_lookup


def link_pyffi(
    repo_root: Path,
    python_symbols: list[Symbol],
    c_symbols: list[Symbol],
    rust_symbols: list[Symbol],
    edges: list[Edge],
) -> PyFFILinkResult:
    """Link Python FFI calls to their native implementations.

    Args:
        repo_root: Repository root path
        python_symbols: Python symbols from the analyzer
        c_symbols: C and C++ symbols from analyzers
        rust_symbols: Rust symbols from the analyzer
        edges: All existing edges (used for PyO3 unresolved call matching)

    Returns:
        PyFFILinkResult with ffi_bridge edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    result_edges: list[Edge] = []
    seen_edges: set[tuple[str, str]] = set()  # (src_path, func_name) dedup

    # Build lookup for C/C++ functions by name
    c_lookup: dict[str, Symbol] = {}
    for sym in c_symbols:
        if sym.kind == "function":
            c_lookup[sym.name] = sym

    # --- Phase 1: Scan Python files for ctypes/cffi calls ---
    python_files: set[str] = set()
    for sym in python_symbols:
        if sym.language == "python" and sym.path.endswith(".py"):
            python_files.add(sym.path)

    for py_path_str in python_files:
        py_path = Path(py_path_str)
        if not py_path.is_absolute():
            py_path = repo_root / py_path

        if not py_path.exists():
            continue

        ffi_calls = _scan_python_file_for_ffi_calls(py_path)

        for func_name, evidence_type, line_num in ffi_calls:
            if func_name not in c_lookup:
                continue

            # Find the enclosing Python symbol for this file
            src_sym = None
            for sym in python_symbols:
                if sym.path == py_path_str:
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
                access_mode="write",
                dest_access_mode="read",
            ))

    # --- Phase 2: Match PyO3 Rust symbols to Python unresolved calls ---
    pyo3_lookup = _find_pyo3_symbols(rust_symbols)

    if pyo3_lookup:
        for edge in edges:
            if not edge.dst.endswith(":unresolved"):
                continue
            # Extract the function name from the unresolved dst
            # Format: "python:path:0-0:name:unresolved"
            parts = edge.dst.split(":")
            if len(parts) < 5:
                continue
            call_name = parts[-2]  # second to last

            # Check both the full name and the short name
            short_name = call_name.split(".")[-1] if "." in call_name else call_name

            rust_sym = pyo3_lookup.get(call_name) or pyo3_lookup.get(short_name)
            if rust_sym is None:
                continue

            dedup_key = (edge.src, call_name)
            if dedup_key in seen_edges:
                continue
            seen_edges.add(dedup_key)

            result_edges.append(Edge.create(
                src=edge.src,
                dst=rust_sym.id,
                edge_type="ffi_bridge",
                line=edge.line,
                confidence=0.85,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="pyo3_bridge",
                access_mode="write",
                dest_access_mode="read",
            ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return PyFFILinkResult(edges=result_edges, run=run)


def _count_python_ffi_files(ctx: LinkerContext) -> int:
    """Count Python files that might contain ctypes/cffi/PyO3 calls."""
    count = 0
    seen_paths: set[str] = set()
    for sym in ctx.symbols:
        if sym.language == "python" and sym.path.endswith(".py"):
            if sym.path not in seen_paths:
                seen_paths.add(sym.path)
                count += 1
    return count


def _count_c_cpp_rust_functions(ctx: LinkerContext) -> int:
    """Count C/C++/Rust function symbols available for linking."""
    count = 0
    for sym in ctx.symbols:
        if sym.language in ("c", "cpp", "rust") and sym.kind in ("function", "method"):
            count += 1
    return count


PYFFI_REQUIREMENTS = [
    LinkerRequirement(
        name="python_ffi_files",
        description="Python files (potential ctypes/cffi/PyO3 callers)",
        check=_count_python_ffi_files,
    ),
    LinkerRequirement(
        name="c_cpp_rust_functions",
        description="C/C++/Rust function implementations",
        check=_count_c_cpp_rust_functions,
    ),
]


@register_linker(
    "pyffi",
    priority=16,  # After cgo (15), before protocol linkers
    description="Python/C/C++/Rust FFI bridge - links ctypes/cffi/PyO3 calls to native implementations",
    requirements=PYFFI_REQUIREMENTS,
    activation=LinkerActivation(
        language_pairs=[("python", "c"), ("python", "cpp"), ("python", "rust")],
    ),
)
def pyffi_linker(ctx: LinkerContext) -> LinkerResult:
    """Python FFI linker for registry-based dispatch.

    Wraps link_pyffi() to use the LinkerContext/LinkerResult interface.
    """
    python_symbols = [s for s in ctx.symbols if s.language == "python"]
    c_symbols = [s for s in ctx.symbols if s.language in ("c", "cpp")]
    rust_symbols = [s for s in ctx.symbols if s.language == "rust"]

    result = link_pyffi(ctx.repo_root, python_symbols, c_symbols, rust_symbols, ctx.edges)

    return LinkerResult(
        symbols=[],
        edges=result.edges,
        run=result.run,
    )
