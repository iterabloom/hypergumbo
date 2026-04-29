# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: multi-language dependency injection resolution.

Creates ``di_resolves`` edges from interface methods to implementation methods
when DI bindings can be determined with high confidence.

Why ``di_resolves`` Instead of ``dispatches_to``
-------------------------------------------------
``dispatches_to`` is in ``_STRUCTURAL_EDGE_TYPES`` (slice.py), so forward BFS
excludes it to avoid fan-out explosion (an interface with 50 implementations
pollutes the slice). ``di_resolves`` is *not* structural, so forward slices
follow it—correct behavior when DI configuration narrows the binding to one
high-confidence implementation.

How It Works
------------
1. **Build interface→implementations map** from ``implements``/``extends`` edges
   and symbol metadata (``base_classes``).
2. **Scan source files** for explicit DI binding patterns per language:
   - Java/Kotlin/Scala: Guice ``bind().to()``, Spring ``@Bean`` methods
   - C#: ``services.Add{Scoped,Transient,Singleton}<I,C>()``
   - TypeScript: NestJS/Angular ``{ provide: X, useClass: Y }``, Inversify ``bind().to()``
   - Python: ``binder.bind(I, to=C)``
   - Java SPI: ``META-INF/services/`` files
3. **Resolution cascade** (highest-confidence wins):
   - Explicit framework binding → 0.90
   - SPI ``META-INF/services/`` → 0.85
   - Naming convention (``DefaultX``/``XImpl``) → 0.75
   - Single implementation → 0.70
4. **Create ``di_resolves`` edges** from interface methods to implementation
   methods (method-level, not class-level).

Supported Frameworks
--------------------
Java: Guice (``bind().to()``, ``@Provides``, ``@ImplementedBy``), Spring
(``@Bean``), CDI (``@Produces``, scope-annotated ``implements``), SPI.
Kotlin: Koin, Guice.  C#: ASP.NET Core DI.
TypeScript: NestJS, Angular, InversifyJS.  Python: injector.  Scala: Guice (same
patterns as Java).
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..discovery import find_files
from ..ir import PASS_VERSION, AnalysisRun, Edge, Symbol, make_pass_id
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)
from ._text_filters import read_masked_source

if TYPE_CHECKING:
    pass

PASS_ID = make_pass_id("di-resolution")

# ---------------------------------------------------------------------------
# Regex patterns for explicit DI binding detection
# ---------------------------------------------------------------------------

# Java/Kotlin/Scala: Guice bind().to() / toProvider()
_GUICE_BIND = re.compile(
    r"bind\s*\(\s*(\w+)\.class\s*\)"
    r"(?:\s*\.\s*annotatedWith\s*\([^()]*(?:\([^()]*\)[^()]*)*\))?"  # optional annotatedWith(...)
    r"\s*\.\s*to(?:Provider)?\s*\(\s*(\w+)\.class\s*\)",
)

# Java: Spring @Bean method — captures return type and new Impl()
_SPRING_BEAN = re.compile(
    r"@Bean\b[^{]*?"                           # @Bean with optional annotations
    r"public\s+(\w+)\s+\w+\s*\([^)]*\)\s*\{"  # return-type method() {
    r"[^}]*?new\s+(\w+)\s*\(",                 # new Impl(
    re.DOTALL,
)

# C#: services.AddScoped<I, C>(), AddTransient<I, C>(), AddSingleton<I, C>()
_CSHARP_DI = re.compile(
    r"\.Add(?:Scoped|Transient|Singleton)\s*<\s*(\w+)\s*,\s*(\w+)\s*>",
)

# TypeScript: { provide: X, useClass: Y }
_TS_PROVIDER = re.compile(
    r"provide\s*:\s*(\w+)\s*,\s*useClass\s*:\s*(\w+)",
)

# TypeScript/NestJS: @Module({ providers: [...], controllers: [...] })
# Captures the array contents for providers and controllers keys.
_NESTJS_MODULE_ARRAY = re.compile(
    r"(?:providers|controllers)\s*:\s*\[([^\]]*)\]",
    re.DOTALL,
)
# Matches the class declaration following @Module({...})
_NESTJS_MODULE_CLASS = re.compile(
    r"@Module\s*\(\s*\{",
)
# Extracts simple PascalCase identifiers from a comma-separated list,
# skipping object literals like { provide: X, useClass: Y }.
_SIMPLE_CLASS_REF = re.compile(r"\b([A-Z]\w+)\b")

# TypeScript: container.bind<I>(token).to(C)
_INVERSIFY_BIND = re.compile(
    r"\.bind\s*<\s*(\w+)\s*>\s*\([^)]*\)\s*\.to\s*\(\s*(\w+)\s*\)",
)

# Python: binder.bind(I, to=C)
_PYTHON_BIND = re.compile(
    r"\.bind\s*\(\s*(\w+)\s*,\s*to\s*=\s*(\w+)\s*\)",
)

# Java/Kotlin/Scala: Guice @Provides method — captures return type and new Impl()
_GUICE_PROVIDES = re.compile(
    r"@Provides\b[^{]*?"                          # @Provides with optional annotations
    r"(?:public\s+|protected\s+|private\s+)?"     # optional access modifier
    r"(\w+)\s+\w+\s*\([^)]*\)\s*\{"              # return-type method() {
    r"[^}]*?new\s+(\w+)\s*\(",                    # new Impl(
    re.DOTALL,
)

# Jakarta CDI: @Produces method — same pattern as Guice @Provides
# @Produces public Foo createFoo() { return new FooImpl(); }
_CDI_PRODUCES = re.compile(
    r"@Produces\b[^{]*?"                            # @Produces with optional annotations
    r"(?:public\s+|protected\s+|private\s+)?"       # optional access modifier
    r"(\w+)\s+\w+\s*\([^)]*\)\s*\{"                # return-type method() {
    r"[^}]*?new\s+(\w+)\s*\(",                      # new Impl(
    re.DOTALL,
)

# Java: @ImplementedBy(Impl.class) on interface declaration
# Group 1 = impl class (from annotation), group 2 = interface name
_GUICE_IMPLEMENTED_BY = re.compile(
    r"@ImplementedBy\s*\(\s*"
    r"(?:[\w.]+\.)?(\w+)\.class"                  # Impl.class (optional FQN prefix)
    r"\s*\)\s*"
    r"(?:public\s+)?"                             # optional public
    r"interface\s+(\w+)",                         # interface Name
    re.DOTALL,
)

# Kotlin: single<IFoo> { FooImpl() }
_KOIN_SINGLE = re.compile(
    r"single\s*<\s*(\w+)\s*>\s*\{[^}]*?(\w+)\s*\(\s*\)",
    re.DOTALL,
)

# Jakarta CDI: Scope-annotated class implements interface
# @ApplicationScoped public class FooImpl implements FooService { ... }
# Group(1) = impl class, group(2) = first interface (reversed pattern)
_CDI_SCOPED_IMPL = re.compile(
    r"@(?:ApplicationScoped|RequestScoped|SessionScoped|Dependent|Singleton)"
    r"\b[^{]*?"
    r"class\s+(\w+)\s+"                             # impl class name
    r"(?:extends\s+\w+\s+)?"                        # optional extends clause
    r"implements\s+(\w+)",                           # first interface
    re.DOTALL,
)

# File extension -> regex patterns to apply
_PATTERNS_BY_EXT: dict[str, list[tuple[re.Pattern[str], float]]] = {
    ".java": [
        (_GUICE_BIND, 0.90),
        (_SPRING_BEAN, 0.90),
        (_GUICE_PROVIDES, 0.90),
        (_CDI_PRODUCES, 0.90),
    ],
    ".kt": [
        (_GUICE_BIND, 0.90),
        (_KOIN_SINGLE, 0.90),
        (_GUICE_PROVIDES, 0.90),
    ],
    ".scala": [
        (_GUICE_BIND, 0.90),
        (_GUICE_PROVIDES, 0.90),
    ],
    ".cs": [
        (_CSHARP_DI, 0.90),
    ],
    ".ts": [
        (_TS_PROVIDER, 0.90),
        (_INVERSIFY_BIND, 0.90),
    ],
    ".tsx": [
        (_TS_PROVIDER, 0.90),
        (_INVERSIFY_BIND, 0.90),
    ],
    ".py": [
        (_PYTHON_BIND, 0.90),
    ],
}

# Patterns where capture groups are reversed: group(1)=impl, group(2)=iface
_REVERSED_PATTERNS_BY_EXT: dict[str, list[tuple[re.Pattern[str], float]]] = {
    ".java": [
        (_GUICE_IMPLEMENTED_BY, 0.85),
        (_CDI_SCOPED_IMPL, 0.85),
    ],
}

# Extensions to scan (union of both pattern dicts)
_SOURCE_EXTENSIONS = list(
    set(_PATTERNS_BY_EXT.keys()) | set(_REVERSED_PATTERNS_BY_EXT.keys()),
)
_SOURCE_GLOBS = [f"*{ext}" for ext in _SOURCE_EXTENSIONS]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DIBinding:
    """A detected DI binding from interface to implementation.

    Attributes:
        interface_name: Short name of the interface/abstract type.
        impl_name: Short name of the implementation class.
        confidence: 0.0-1.0 confidence in the binding.
        source: Where the binding was found (e.g. "guice", "spring", "heuristic").
    """

    interface_name: str
    impl_name: str
    confidence: float
    source: str


# ---------------------------------------------------------------------------
# File scanning for explicit bindings
# ---------------------------------------------------------------------------

def _scan_spi_files(root: Path) -> list[DIBinding]:
    """Scan META-INF/services/ for Java SPI bindings.

    Each file is named after the service interface (fully qualified).  Lines
    inside list implementation classes.
    """
    bindings: list[DIBinding] = []
    spi_dir = root / "META-INF" / "services"
    if not spi_dir.is_dir():
        # Also check src/main/resources/ (Maven layout)
        for candidate in find_files(root, ["META-INF/services/*"]):
            iface_fqn = candidate.name
            iface_short = iface_fqn.rsplit(".", 1)[-1]
            try:
                for line in read_masked_source(candidate, errors="replace").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        impl_short = line.rsplit(".", 1)[-1]
                        bindings.append(DIBinding(
                            interface_name=iface_short,
                            impl_name=impl_short,
                            confidence=0.85,
                            source="spi",
                        ))
            except OSError:  # pragma: no cover - defensive filesystem
                pass
        return bindings

    # Direct META-INF/services/ at repo root
    for spi_file in spi_dir.iterdir():
        if not spi_file.is_file():
            continue  # pragma: no cover - defensive
        iface_fqn = spi_file.name
        iface_short = iface_fqn.rsplit(".", 1)[-1]
        try:
            for line in read_masked_source(spi_file, errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    impl_short = line.rsplit(".", 1)[-1]
                    bindings.append(DIBinding(
                        interface_name=iface_short,
                        impl_name=impl_short,
                        confidence=0.85,
                        source="spi",
                    ))
        except OSError:  # pragma: no cover - defensive filesystem
            pass
    return bindings


def _extract_nestjs_module_bindings(content: str) -> list[DIBinding]:
    """Extract NestJS @Module provider/controller registrations from TS content.

    Parses ``@Module({ providers: [...], controllers: [...] })`` decorators and
    returns DIBinding entries with ``source="nestjs:module"``.  The
    ``interface_name`` field holds the module class name and ``impl_name`` holds
    each registered provider/controller.

    Only simple class references (``PascalCase`` identifiers) are extracted.
    Object literals like ``{ provide: X, useClass: Y }`` are left to
    ``_TS_PROVIDER`` to handle.
    """
    bindings: list[DIBinding] = []

    # Find each @Module({...}) decorator and the class it decorates
    for mod_match in _NESTJS_MODULE_CLASS.finditer(content):
        # Find matching closing brace for the decorator's object arg
        start = mod_match.end()
        brace_depth = 1
        pos = start
        decorator_end = len(content)
        while pos < len(content) and brace_depth > 0:
            ch = content[pos]
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
            pos += 1
        decorator_end = pos

        decorator_body = content[mod_match.start():decorator_end]

        # Find the class name after the decorator — require `class Foo`
        # followed by `{`, `extends`, or `implements` to avoid matching
        # "class" in comments.
        rest = content[decorator_end:]
        class_match = re.search(
            r"(?:export\s+)?class\s+(\w+)\s*(?:[{]|extends|implements)", rest,
        )
        if not class_match:
            continue
        module_name = class_match.group(1)

        # Extract providers and controllers arrays from the decorator body
        for arr_match in _NESTJS_MODULE_ARRAY.finditer(decorator_body):
            arr_content = arr_match.group(1)
            # Skip object literals: split by comma, keep only simple identifiers
            for item in arr_content.split(","):
                item = item.strip()
                if not item or "{" in item or "}" in item:
                    continue
                # Extract the PascalCase class name
                ref_match = _SIMPLE_CLASS_REF.match(item)
                if ref_match:
                    class_ref = ref_match.group(1)
                    bindings.append(DIBinding(
                        interface_name=module_name,
                        impl_name=class_ref,
                        confidence=0.85,
                        source="nestjs:module",
                    ))

    return bindings


def extract_bindings_from_source(root: Path) -> list[DIBinding]:
    """Extract explicit DI bindings from source files in *root*.

    Scans ``.java``, ``.kt``, ``.scala``, ``.cs``, ``.ts``, ``.tsx``, ``.py``
    files and ``META-INF/services/`` directories.  Also detects NestJS
    ``@Module({providers: [...], controllers: [...]})`` registrations.

    Returns:
        List of DIBinding with confidence levels.
    """
    bindings: list[DIBinding] = []

    # Scan source files
    for file_path in find_files(root, _SOURCE_GLOBS):
        ext = file_path.suffix.lower()
        patterns = _PATTERNS_BY_EXT.get(ext, [])
        reversed_patterns = _REVERSED_PATTERNS_BY_EXT.get(ext, [])
        if not patterns and not reversed_patterns:
            continue  # pragma: no cover - defensive

        try:
            content = read_masked_source(file_path, errors="replace")
        except OSError:  # pragma: no cover - defensive filesystem
            continue

        # Standard patterns: group(1)=iface, group(2)=impl
        for regex, confidence in patterns:
            for match in regex.finditer(content):
                iface_name, impl_name = match.group(1), match.group(2)
                if iface_name == impl_name:
                    continue
                bindings.append(DIBinding(
                    interface_name=iface_name,
                    impl_name=impl_name,
                    confidence=confidence,
                    source=f"regex:{ext}",
                ))

        # Reversed patterns: group(1)=impl, group(2)=iface
        for regex, confidence in reversed_patterns:
            for match in regex.finditer(content):
                impl_name, iface_name = match.group(1), match.group(2)
                if iface_name == impl_name:
                    continue
                bindings.append(DIBinding(
                    interface_name=iface_name,
                    impl_name=impl_name,
                    confidence=confidence,
                    source=f"annotation:{ext}",
                ))

        # NestJS @Module registrations (TypeScript only)
        if ext in (".ts", ".tsx"):
            bindings.extend(_extract_nestjs_module_bindings(content))

    # SPI files
    bindings.extend(_scan_spi_files(root))

    return bindings


# ---------------------------------------------------------------------------
# Interface → implementation map from symbol metadata
# ---------------------------------------------------------------------------

def build_interface_impl_map(
    symbols: list[Symbol],
    edges: list[Edge],
) -> dict[str, list[Symbol]]:
    """Build map of interface name → implementing class symbols.

    Uses ``implements`` edges and ``extends`` edges (when the parent is an
    interface-kind symbol) to discover implementations.

    Args:
        symbols: All symbols.
        edges: All edges.

    Returns:
        Dict mapping interface short name to list of implementing class Symbols.
    """
    sym_by_id: dict[str, Symbol] = {s.id: s for s in symbols}
    result: dict[str, list[Symbol]] = defaultdict(list)

    for edge in edges:
        if edge.edge_type == "implements":
            iface_sym = sym_by_id.get(edge.dst)
            impl_sym = sym_by_id.get(edge.src)
            if iface_sym and impl_sym:
                result[iface_sym.name].append(impl_sym)
        elif edge.edge_type == "extends":
            parent_sym = sym_by_id.get(edge.dst)
            child_sym = sym_by_id.get(edge.src)
            if parent_sym and child_sym and parent_sym.kind == "interface":
                result[parent_sym.name].append(child_sym)

    return dict(result)


# ---------------------------------------------------------------------------
# Heuristic resolution
# ---------------------------------------------------------------------------

def _has_naming_convention(iface_name: str, impl_name: str) -> bool:
    """Check if impl name follows Default/Impl naming convention for iface."""
    return (
        impl_name == f"Default{iface_name}"
        or impl_name == f"{iface_name}Impl"
    )


def resolve_bindings(
    symbols: list[Symbol],
    edges: list[Edge],
    explicit_bindings: list[DIBinding],
) -> list[DIBinding]:
    """Apply resolution cascade to produce final bindings.

    Priority:
    1. Explicit framework bindings (confidence 0.90)
    2. Naming convention (``DefaultX``/``XImpl``) with single impl (0.75)
    3. Single implementation (0.70)

    Explicit bindings always win; heuristics only apply when no explicit
    binding exists for an interface.

    Args:
        symbols: All symbols.
        edges: All edges.
        explicit_bindings: Bindings from source scanning and SPI.

    Returns:
        Combined list of bindings (explicit + heuristic).
    """
    # Start with explicit bindings
    all_bindings = list(explicit_bindings)

    # Interfaces already bound explicitly
    explicitly_bound: set[str] = {b.interface_name for b in explicit_bindings}

    # Build interface->impl map from edges
    iface_impl_map = build_interface_impl_map(symbols, edges)

    # Apply heuristics for unbound interfaces
    for iface_name, impls in iface_impl_map.items():
        if iface_name in explicitly_bound:
            continue

        if len(impls) == 1:
            impl = impls[0]
            # Check naming convention for higher confidence
            if _has_naming_convention(iface_name, impl.name):
                confidence = 0.75
                source = "heuristic:naming"
            else:
                confidence = 0.70
                source = "heuristic:single-impl"

            all_bindings.append(DIBinding(
                interface_name=iface_name,
                impl_name=impl.name,
                confidence=confidence,
                source=source,
            ))
        # Multiple impls: check if exactly one has naming convention
        elif len(impls) > 1:
            named = [i for i in impls if _has_naming_convention(iface_name, i.name)]
            if len(named) == 1:
                all_bindings.append(DIBinding(
                    interface_name=iface_name,
                    impl_name=named[0].name,
                    confidence=0.75,
                    source="heuristic:naming",
                ))

    return all_bindings


# ---------------------------------------------------------------------------
# Edge creation: interface methods → implementation methods
# ---------------------------------------------------------------------------

def _get_method_short_name(method_name: str) -> str:
    """Extract short method name from qualified name."""
    if "#" in method_name:
        return method_name.split("#")[-1]
    if "." in method_name:
        return method_name.split(".")[-1]
    return method_name


def _create_di_edges(
    bindings: list[DIBinding],
    symbols: list[Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Create di_resolves edges from interface methods to implementation methods.

    For each binding (interface_name → impl_name), finds methods on both the
    interface and implementation with matching short names, then creates edges.

    Args:
        bindings: Resolved DI bindings.
        symbols: All symbols.
        run: AnalysisRun for provenance.

    Returns:
        List of di_resolves edges.
    """
    # Index: class_name -> list of method symbols
    methods_by_class: dict[str, list[Symbol]] = defaultdict(list)
    for sym in symbols:
        if sym.kind != "method":
            continue
        class_name = sym.meta.get("class") if sym.meta else None
        if class_name:
            methods_by_class[class_name].append(sym)

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()

    for binding in bindings:
        iface_methods = methods_by_class.get(binding.interface_name, [])
        impl_methods = methods_by_class.get(binding.impl_name, [])

        if not iface_methods or not impl_methods:
            continue

        # Build impl method index by short name
        impl_by_short: dict[str, list[Symbol]] = defaultdict(list)
        for m in impl_methods:
            impl_by_short[_get_method_short_name(m.name)].append(m)

        for iface_m in iface_methods:
            short = _get_method_short_name(iface_m.name)
            for impl_m in impl_by_short.get(short, []):
                pair = (iface_m.id, impl_m.id)
                if pair in seen:
                    continue  # pragma: no cover - defensive dedup
                seen.add(pair)

                edges.append(Edge.create(
                    src=iface_m.id,
                    dst=impl_m.id,
                    edge_type="di_resolves",
                    line=iface_m.span.start_line if iface_m.span else 0,
                    confidence=binding.confidence,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type=f"di_binding:{binding.source}",
                ))

    return edges


def _create_di_registers_edges(
    bindings: list[DIBinding],
    symbols: list[Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Create di_registers edges from NestJS module classes to providers.

    For each ``nestjs:module`` binding (module_name → provider_name), finds the
    module and provider class symbols and creates a class-level edge.

    Args:
        bindings: DI bindings (only ``nestjs:module`` source are used).
        symbols: All symbols.
        run: AnalysisRun for provenance.

    Returns:
        List of ``di_registers`` edges.
    """
    module_bindings = [b for b in bindings if b.source == "nestjs:module"]
    if not module_bindings:
        return []

    # Index: class name -> Symbol (prefer class kind)
    sym_by_name: dict[str, Symbol] = {}
    for sym in symbols:
        if sym.kind in ("class", "interface"):
            sym_by_name[sym.name] = sym

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()

    for binding in module_bindings:
        module_sym = sym_by_name.get(binding.interface_name)
        provider_sym = sym_by_name.get(binding.impl_name)
        if not module_sym or not provider_sym:
            continue

        pair = (module_sym.id, provider_sym.id)
        if pair in seen:
            continue
        seen.add(pair)

        edges.append(Edge.create(
            src=module_sym.id,
            dst=provider_sym.id,
            edge_type="di_registers",
            line=module_sym.span.start_line if module_sym.span else 0,
            confidence=binding.confidence,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="nestjs_module_registration",
        ))

    return edges


# ---------------------------------------------------------------------------
# Main linker function
# ---------------------------------------------------------------------------

def link_di_resolution(ctx: LinkerContext) -> LinkerResult:
    """Run the DI resolution linker.

    1. Extract explicit bindings from source files.
    2. Apply resolution cascade (explicit > naming > single-impl).
    3. Create ``di_resolves`` edges at method level.
    4. Create ``di_registers`` edges for NestJS module registrations.

    Args:
        ctx: LinkerContext with symbols, edges, and repo_root.

    Returns:
        LinkerResult with new di_resolves and di_registers edges.
    """
    start = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Step 1: Extract explicit DI bindings from source
    explicit_bindings = extract_bindings_from_source(ctx.repo_root)

    # Step 2: Resolution cascade (for di_resolves method-level edges)
    all_bindings = resolve_bindings(
        ctx.symbols, ctx.edges, explicit_bindings,
    )

    # Step 3: Create di_resolves edges
    new_edges = _create_di_edges(all_bindings, ctx.symbols, run)

    # Step 4: Create di_registers edges for NestJS module registrations
    new_edges.extend(_create_di_registers_edges(
        explicit_bindings, ctx.symbols, run,
    ))

    run.duration_ms = int((time.time() - start) * 1000)
    return LinkerResult(edges=new_edges, run=run)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@register_linker(
    "di_resolution",
    priority=65,
    description=(
        "Creates di_resolves edges from interface methods to DI-bound "
        "implementation methods (Guice bind/provides/implementedBy, "
        "Spring, ASP.NET Core, NestJS, Angular, Inversify, Koin, "
        "Python injector, Java SPI)"
    ),
    activation=LinkerActivation(always=True),
)
def _di_resolution_entry(ctx: LinkerContext) -> LinkerResult:
    """Registry entry point for the DI resolution linker."""
    return link_di_resolution(ctx)
