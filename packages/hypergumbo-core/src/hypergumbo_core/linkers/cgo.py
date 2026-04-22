# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bridge linker: Cgo for connecting Go C function calls to C/C++ implementations.

This linker creates cgo_bridge edges between Go functions that call C functions
via the ``import "C"`` pseudo-package and the corresponding C/C++ function
implementations found in the repository.

How It Works
------------
1. Find unresolved edges from the Go analyzer with dst pattern ``go:C:0-0:{name}:unresolved``
   (these are created when Go code calls ``C.funcName()`` and the Go analyzer can't resolve them)
2. Build a lookup table from C/C++ function symbols by name
3. Match the unresolved function names to C/C++ function symbols
4. Create cgo_bridge edges linking the Go caller to the C/C++ implementation

Cgo Mechanism
-------------
Go's cgo allows calling C code from Go using a special pseudo-package:

    // #include <stdio.h>
    // #include "mylib.h"
    import "C"

    func main() {
        C.puts(C.CString("hello"))
        C.myFunction(42)
    }

The Go analyzer creates ``go:C:0-0:puts:unresolved`` and
``go:C:0-0:myFunction:unresolved`` edges for these calls. This linker
resolves them to the actual C function symbols.

C++ functions accessible via cgo must use ``extern "C"`` linkage, so they
appear as regular C-style function symbols and are matched the same way.

Dataflow Annotation
-------------------
Bridge edges are annotated with ``access_mode="write"`` (Go caller passes data
to C) and ``dest_access_mode="read"`` (C callee receives data). This is Tier 2
(explicit linker) annotation — Tier 1 automatic annotation cannot work across
language boundaries because no single-language AST spans the FFI call.

Why This Design
---------------
- Follows the same analyze-then-link pattern as JNI linker
- Uses the Go analyzer's existing unresolved edge mechanism (no Go analyzer changes needed)
- Simple name matching: unlike JNI, cgo uses the raw C function name with no encoding
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..ir import AnalysisRun, Edge, PASS_VERSION, Symbol, make_pass_id
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
)

PASS_ID = make_pass_id("cgo-linker")

# The prefix for cgo unresolved edges created by the Go analyzer
CGO_UNRESOLVED_PREFIX = "go:C:0-0:"
CGO_UNRESOLVED_SUFFIX = ":unresolved"


def _count_go_cgo_calls(ctx: LinkerContext) -> int:
    """Count unresolved Go edges that target the C pseudo-package."""
    count = 0
    for edge in ctx.edges:
        if (
            edge.dst.startswith(CGO_UNRESOLVED_PREFIX)
            and edge.dst.endswith(CGO_UNRESOLVED_SUFFIX)
        ):
            count += 1
    return count


def _count_c_cpp_functions(ctx: LinkerContext) -> int:
    """Count C/C++ function symbols available for linking."""
    count = 0
    for sym in ctx.symbols:
        if sym.language in ("c", "cpp") and sym.kind == "function":
            count += 1
    return count


CGO_REQUIREMENTS = [
    LinkerRequirement(
        name="go_cgo_calls",
        description="Go cgo calls (C.funcName() via import \"C\")",
        check=_count_go_cgo_calls,
    ),
    LinkerRequirement(
        name="c_cpp_functions",
        description="C/C++ function implementations",
        check=_count_c_cpp_functions,
    ),
]


@dataclass
class CgoLinkResult:
    """Result of cgo linking."""

    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


def _build_c_function_lookup(c_symbols: list[Symbol]) -> dict[str, Symbol]:
    """Build a lookup table from function name to C/C++ symbol.

    Maps bare function names to their C/C++ symbol definitions.
    For qualified names like ``MyStruct.method``, also indexes the last
    component (``method``) for broader matching.

    Cgo can call functions from both C (.c) and C++ (.cpp) files, as long
    as C++ functions use ``extern "C"`` linkage.
    """
    lookup: dict[str, Symbol] = {}

    for sym in c_symbols:
        if sym.language not in ("c", "cpp") or sym.kind != "function":
            continue

        lookup[sym.name] = sym

    return lookup


def link_cgo(
    go_symbols: list[Symbol],
    c_symbols: list[Symbol],
    edges: list[Edge],
) -> CgoLinkResult:
    """Link Go cgo C function calls to their C/C++ implementations.

    Args:
        go_symbols: Symbols from Go analyzer (used for context, not directly matched)
        c_symbols: Symbols from C and C++ analyzers
        edges: All edges including unresolved cgo calls from Go analyzer

    Returns:
        CgoLinkResult with cgo_bridge edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    result_edges: list[Edge] = []

    # Build lookup for C/C++ functions
    c_lookup = _build_c_function_lookup(c_symbols)

    # Find unresolved cgo edges and resolve them
    for edge in edges:
        # Only process unresolved edges targeting the C pseudo-package
        if not edge.dst.startswith(CGO_UNRESOLVED_PREFIX):
            continue
        if not edge.dst.endswith(CGO_UNRESOLVED_SUFFIX):  # pragma: no cover - defensive
            continue

        # Extract function name from dst: "go:C:0-0:funcName:unresolved"
        # Strip prefix "go:C:0-0:" and suffix ":unresolved"
        func_name = edge.dst[len(CGO_UNRESOLVED_PREFIX):-len(CGO_UNRESOLVED_SUFFIX)]

        if not func_name:
            continue  # pragma: no cover - defensive for malformed edge

        # Match to C/C++ function
        if func_name in c_lookup:
            c_sym = c_lookup[func_name]
            result_edges.append(Edge.create(
                src=edge.src,
                dst=c_sym.id,
                edge_type="cgo_bridge",
                line=edge.line,
                confidence=0.90,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="cgo_call",
                access_mode="write",
                dest_access_mode="read",
            ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return CgoLinkResult(edges=result_edges, run=run)


@register_linker(
    "cgo",
    priority=15,  # After JNI (10), before protocol linkers
    description="Go/C/C++ cgo bridge - links C.funcName() calls to C/C++ implementations",
    requirements=CGO_REQUIREMENTS,
    activation=LinkerActivation(language_pairs=[("go", "c"), ("go", "cpp")]),
)
def cgo_linker(ctx: LinkerContext) -> LinkerResult:
    """Cgo linker for registry-based dispatch.

    Wraps link_cgo() to use the LinkerContext/LinkerResult interface.
    """
    go_symbols = [s for s in ctx.symbols if s.language == "go"]
    c_symbols = [s for s in ctx.symbols if s.language in ("c", "cpp")]

    result = link_cgo(go_symbols, c_symbols, ctx.edges)

    return LinkerResult(
        symbols=[],
        edges=result.edges,
        run=result.run,
    )
