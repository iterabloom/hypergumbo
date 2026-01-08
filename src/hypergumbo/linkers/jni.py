"""JNI linker for connecting Java native methods to C/C++ implementations.

This linker creates native_bridge edges between Java native method declarations
and their corresponding C/C++ JNI function implementations.

How It Works
------------
1. Find all Java method symbols marked as native (via modifiers field)
2. Find all C/C++ function symbols with JNI naming pattern (Java_Package_Class_Method)
3. Parse JNI function names to extract package, class, and method components
4. Match Java native methods to C/C++ JNI functions by fully qualified name
5. Create native_bridge edges for matched pairs

JNI implementations can be written in either C (.c) or C++ (.cpp) files.
Android NDK projects commonly use C++ for their JNI implementations.

JNI Naming Convention
---------------------
Java class: com.example.MyClass
Java native method: processData
C/C++ function: Java_com_example_MyClass_processData

Special encodings in JNI names:
- Underscore (_) in Java names becomes _1 in C
- Semicolon (;) becomes _2
- Left bracket ([) becomes _3
- Unicode chars become _0xxxx

Why This Design
---------------
- Separate linker keeps language analyzers focused on their own language
- Two-phase approach (analyze, then link) allows for flexible composition
- Same pattern can be used for other cross-language bridges (e.g., Python C extensions)
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from ..ir import AnalysisRun, Edge, Symbol
from .registry import (
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
)

PASS_ID = "jni-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


# Requirement check functions for the linker contract
def _count_java_native_methods(ctx: LinkerContext) -> int:
    """Count Java methods with native modifier."""
    count = 0
    for sym in ctx.symbols:
        if sym.language != "java" or sym.kind != "method":
            continue
        is_native_via_modifiers = "native" in sym.modifiers
        is_native_via_meta = sym.meta.get("is_native", False) if sym.meta else False
        if is_native_via_modifiers or is_native_via_meta:
            count += 1
    return count


def _count_c_cpp_jni_functions(ctx: LinkerContext) -> int:
    """Count C/C++ functions with JNI naming convention (Java_...).

    JNI implementations can be written in either C or C++ files. In practice,
    many Android NDK projects use .cpp files for their JNI implementations.
    """
    count = 0
    for sym in ctx.symbols:
        if sym.language in ("c", "cpp") and sym.kind == "function" and sym.name.startswith("Java_"):
            count += 1
    return count


# Define the linker requirements contract
JNI_REQUIREMENTS = [
    LinkerRequirement(
        name="java_native_methods",
        description="Java native method declarations",
        check=_count_java_native_methods,
    ),
    LinkerRequirement(
        name="c_cpp_jni_functions",
        description="C/C++ JNI implementation functions (Java_*)",
        check=_count_c_cpp_jni_functions,
    ),
]


@dataclass
class JniLinkResult:
    """Result of JNI linking."""

    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None


def parse_jni_function_name(name: str) -> Optional[dict[str, str]]:
    """Parse a JNI function name into package, class, and method components.

    Args:
        name: C function name, e.g., "Java_com_example_MyClass_processData"

    Returns:
        Dict with keys 'package', 'class', 'method', or None if not a JNI function.
    """
    if not name.startswith("Java_"):
        return None

    # Remove Java_ prefix
    rest = name[5:]  # "com_example_MyClass_processData"

    if not rest:
        return None

    # Handle overload suffix (e.g., __I for int parameter)
    # Overload suffixes start with __ followed by signature
    overload_match = re.search(r"__[A-Z\d_]+$", rest)
    if overload_match:
        rest = rest[:overload_match.start()]

    # Split by underscore
    parts = rest.split("_")

    if len(parts) < 2:
        return None

    # Handle _1 encoding (underscore in original Java name)
    # Reconstruct by joining _1 sequences
    decoded_parts: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        # Check if next part is "1" (indicates underscore encoding)
        if i + 1 < len(parts) and parts[i + 1] == "1":
            # This is an encoded underscore
            decoded_parts.append(part + "_")
            i += 2
            # Continue accumulating if more _1 sequences
            while i < len(parts) and parts[i] == "1":
                decoded_parts[-1] += "_"
                i += 1
            if i < len(parts):
                decoded_parts[-1] += parts[i]
                i += 1
        else:
            decoded_parts.append(part)
            i += 1

    if len(decoded_parts) < 2:
        return None

    # Last part is the method name
    method = decoded_parts[-1]

    # Second to last is the class name
    class_name = decoded_parts[-2]

    # Everything before that is the package
    package_parts = decoded_parts[:-2]
    package = ".".join(package_parts)

    return {
        "package": package,
        "class": class_name,
        "method": method,
    }


def _build_jni_lookup(native_symbols: list[Symbol]) -> dict[str, Symbol]:
    """Build a lookup table from JNI-style names to C/C++ symbols.

    Maps Java method names to their C/C++ implementations. Creates entries for both
    fully qualified names (com.example.MyClass.method) and short names
    (MyClass.method) to support matching regardless of whether the Java analyzer
    includes package information.

    JNI implementations can be in .c or .cpp files - Android NDK commonly uses C++.
    """
    lookup: dict[str, Symbol] = {}

    for sym in native_symbols:
        if sym.language not in ("c", "cpp") or sym.kind != "function":
            continue

        parsed = parse_jni_function_name(sym.name)
        if parsed is None:
            continue

        # Build the short name (ClassName.method)
        short_name = f"{parsed['class']}.{parsed['method']}"
        lookup[short_name] = sym

        # Also add fully qualified name if package is present
        if parsed["package"]:
            fq_name = f"{parsed['package']}.{parsed['class']}.{parsed['method']}"
            lookup[fq_name] = sym

    return lookup


def link_jni(java_symbols: list[Symbol], native_symbols: list[Symbol]) -> JniLinkResult:
    """Link Java native methods to their C/C++ JNI implementations.

    Args:
        java_symbols: Symbols from Java analyzer
        native_symbols: Symbols from C and C++ analyzers (JNI can use either)

    Returns:
        JniLinkResult with native_bridge edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    edges: list[Edge] = []

    # Build lookup table for C/C++ JNI functions
    jni_lookup = _build_jni_lookup(native_symbols)

    # Find Java native methods and link to C/C++ implementations
    for sym in java_symbols:
        if sym.language != "java":
            continue

        if sym.kind != "method":
            continue

        # Check if this is a native method (via modifiers field or legacy meta.is_native)
        is_native_via_modifiers = "native" in sym.modifiers
        is_native_via_meta = sym.meta.get("is_native", False) if sym.meta else False
        if not (is_native_via_modifiers or is_native_via_meta):
            continue

        # Look up the corresponding C/C++ function
        # sym.name is like "MyClass.processData" or "com.example.MyClass.processData"
        if sym.name in jni_lookup:
            native_sym = jni_lookup[sym.name]
            edge = Edge.create(
                src=sym.id,
                dst=native_sym.id,
                edge_type="native_bridge",
                line=sym.span.start_line if sym.span else 0,
                confidence=0.95,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="jni_naming_convention",
            )
            edges.append(edge)

    run.duration_ms = int((time.time() - start_time) * 1000)

    return JniLinkResult(edges=edges, run=run)


# Register the linker with the registry for unified dispatch
@register_linker(
    "jni",
    priority=10,  # Early priority - JNI linking should happen before other linkers
    description="Java/C/C++ JNI bridge - links native method declarations to C/C++ implementations",
    requirements=JNI_REQUIREMENTS,
)
def jni_linker(ctx: LinkerContext) -> LinkerResult:
    """JNI linker for registry-based dispatch.

    This wraps link_jni() to use the LinkerContext/LinkerResult interface.
    JNI implementations can be in either C (.c) or C++ (.cpp) files.
    """
    # Separate Java and C/C++ symbols
    java_symbols = [s for s in ctx.symbols if s.language == "java"]
    native_symbols = [s for s in ctx.symbols if s.language in ("c", "cpp")]

    result = link_jni(java_symbols, native_symbols)

    return LinkerResult(
        symbols=[],
        edges=result.edges,
        run=result.run,
    )
