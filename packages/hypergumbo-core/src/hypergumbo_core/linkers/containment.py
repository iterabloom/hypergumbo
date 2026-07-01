# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker: containment for creating `contains` edges between containers and members.

This linker connects container symbols (classes, interfaces, structs, traits,
enums, modules, services, messages) to their member symbols (methods, getters,
setters, RPCs, nested messages, and nested containers) based on naming
conventions, creating `contains` edges. Without these edges, members are
orphaned in the graph — disconnected from their parent types — which inflates
orphan rates and hides hierarchical structure from slice traversal.

How It Works
------------
Three phases, tried in order of decreasing confidence:

**Phase 1 — Naming convention** (confidence=1.0):
Extracts the parent name from the symbol's ``name`` field using
language-specific separators (``.``, ``#``, ``::``).  For example,
``User.save`` → parent ``User``.

**Phase 1.5 — qualified_name fallback** (confidence=0.95):
When ``sym.name`` has no separator (e.g., proto RPCs where
``name="BidiHello"``), falls back to ``sym.qualified_name`` (the
ADR-0032 typed sibling field that carries fully-qualified scoped
identifiers, e.g., ``hello.HelloService.BidiHello``). This handles
proto service→rpc containment and nested message containment.
This phase is reachable only for niche-language analyzers (proto,
capnp, fish, etc.) that emit unqualified ``name`` plus a
``qualified_name``. Mainstream-analyzer languages (Python, JS/TS,
Go, Rust, Java, C++, Ruby) leave ``qualified_name=None`` by design
because ``name`` already encodes the parent, so containment falls
through to span_overlap when naming convention doesn't match.

**Phase 2 — Span-based fallback** (confidence=0.9):
When neither naming convention nor qualified_name produces a parent,
checks if any container symbol in the same file has a span that fully
encloses the unqualified symbol.  Prefers the tightest (smallest span)
enclosing container.

Naming Conventions by Language
------------------------------
- Most languages use ``.``: ClassName.methodName (Python, Java, JS/TS, etc.)
- Ruby instance methods use ``#``: ClassName#method_name
- Ruby/Elixir modules use ``::``: Postal::HTTP, MyApp.Repo
- Rust uses ``::``: ImplTarget::method_name
- Proto uses ``.`` in qualified_name: hello.HelloService.BidiHello

Why a Linker Instead of Per-Analyzer Logic
-----------------------------------------
Same rationale as the inheritance linker: containment is a structural
relationship that works identically across all languages. Analyzers
only need to set qualified names; edge creation is language-agnostic.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Symbol

PASS_ID = make_pass_id("containment-linker")

# Symbol kinds that can be "contained" by a class/interface/service.
#
# ADR-0027 Phase-2 audit (WI-jukav slice 2 verdict): no dual-shape
# predicate needed. The post-fold canonical for Cluster D ``rpc`` is
# ``method`` + ``meta["framework_role"]="rpc"`` — and ``method`` is
# already in this set, so post-fold producers (when proto.py /
# thrift.py / capnp.py eventually migrate, currently legacy-shape)
# continue to match unchanged. Cluster H pending ``message`` resolves
# in Wave 6 and similarly cannot fold without changing this set.
# Live producers today (proto.py, thrift.py, capnp.py) emit the
# legacy kinds directly through positional helpers that the L3
# producer-coherence linter cannot statically gate; the Wave-5
# framework-linker producers don't reach this filter (their
# synthetic-node names lack class-qualifier separators, so the
# parent-extraction step in :func:`link_containment` skips them
# before kind matters).
#
# dispatch:F4 (INV-pohik de-orphan): ``function`` and ``variable`` are
# graph-structural members of their enclosing file/module. They are reached
# almost entirely through Phase-2 span_overlap — a top-level ``def`` or module
# ``var`` carries a bare name, so Phase-1 naming-convention cannot match it —
# attaching them to the ``kind="file"`` anchor that encloses their span
# (file-anchor:F1) so the file's ``contains`` tree is rooted at its top-level
# members and ``slice`` can expand "this file's symbols". A *dotted* function
# name (e.g. the JS ``userController.createUser`` route-handler idiom) can still
# match a same-file container via Phase 1 — that is accepted: the dotted name
# genuinely qualifies a real container when one exists in the file, and no edge
# is emitted when none does. The dead-code reachability BFS does NOT traverse
# ``contains`` (it follows calls/dispatches_to/wraps), so this de-orphaning
# does not move the dead-code-maybe false-positive rate.
CONTAINABLE_KINDS = frozenset(
    # WI-zajaz: ``field`` (class-body attributes, emitted with dotted names like
    # ``Widget.size`` by emission-parity F5) roots at its class exactly like
    # ``method`` — without it, 1862 real field symbols (99.6% of them) were
    # orphans on self-analysis. The dead-code BFS does not traverse ``contains``
    # (see the module note above), so this de-orphaning does not move the
    # dead-code-maybe FP rate; it only gives fields their class-containment edge.
    {"method", "getter", "setter", "message", "function", "variable", "field"}
)

# Symbol kinds that can "contain" other symbols.
# Includes struct/trait/enum for Rust (and Go/C/Zig structs),
# service for proto (contains RPCs), message for proto (nested messages).
#
# ADR-0027 Phase-2 audit (WI-jukav slice 2 verdict): no dual-shape
# predicate needed. The post-fold canonical for Cluster D ``service``
# is ``interface``/``class`` + ``meta["framework_role"]="service"`` —
# both are already in this set. Wave-5 framework-linker producers
# don't reach this filter for the same reason as ``CONTAINABLE_KINDS``
# above (synthetic-node names lack class-qualifier separators).
CONTAINER_KINDS = frozenset({
    "class", "interface", "struct", "trait", "enum", "module",
    "message",
    # INV-hojus: file-kind Symbols are the canonical file representation
    # (orchestrator synthesis + py.py for Python with module-level code,
    # js_module linker for TS, etc.). Including them here lets Phase 2's
    # span-based fallback emit file → top-level-class contains edges,
    # closing the structural-orphan gap noted on INV-hojus.
    "file",
})

# Separators used in method names, ordered by specificity
# Ruby `#` and Rust `::` are checked before `.` to avoid
# mis-splitting on languages that use both
_SEPARATORS = ("#", "::", ".")


def _extract_parent_name(method_name: str) -> str | None:
    """Extract the parent class/type name from a qualified method name.

    Uses language-specific separators to split the name:
    - "User.save" → "User"
    - "User#save" → "User"
    - "User::new" → "User"
    - "com.example.UserService.getUsers" → "com.example.UserService"
    - "Outer.Inner.do_thing" → "Outer.Inner"
    - "save" → None (no parent)

    Args:
        method_name: The qualified method name.

    Returns:
        The parent name, or None if there is no parent.
    """
    for sep in _SEPARATORS:
        if sep in method_name:
            # rsplit with maxsplit=1 to get the immediate parent
            parent, _method = method_name.rsplit(sep, 1)
            if parent:
                return parent
    return None


def _find_parent(
    parent_name: str,
    child_path: str,
    container_by_name: dict[str, list[Symbol]],
    child_language: str | None = None,
) -> Symbol | None:
    """Find the best parent container for a given parent name.

    When multiple containers share a name (e.g., Django's 238 Model classes),
    uses a priority cascade:
      1. Same file (most specific, always correct)
      2. Same language, different file (structural match)
      3. No match (returns None — prevents cross-language false positives)

    A single candidate is returned directly only if it matches the child's
    language (or if child_language is not provided, for backward compat).
    """
    candidates = container_by_name.get(parent_name)
    if not candidates:
        return None

    # Fast path: single candidate, language check if possible
    if len(candidates) == 1:
        if child_language and candidates[0].language != child_language:
            return None
        return candidates[0]

    # Priority 1: same file
    for c in candidates:
        if c.path == child_path:
            return c

    # Priority 2: same language (any file)
    if child_language:
        same_lang = [c for c in candidates if c.language == child_language]
        if len(same_lang) == 1:
            return same_lang[0]
        if len(same_lang) > 1:
            # Multiple same-language candidates: ambiguous, return first
            return same_lang[0]
        # No same-language candidate: refuse to cross language boundary
        return None

    # No language info: fall back to first (backward compat)
    return candidates[0]


@register_linker(
    "containment-linker",
    priority=12,  # After analyzers (0), before inheritance (15)
    description="Creates contains edges from classes/interfaces to their methods",
    # CNF: explicit empty. This linker is graph-structural — it pairs every
    # method symbol with its enclosing class symbol via name-prefix matching.
    # It produces useful output for ANY analyzer's symbol set, with no specific
    # language requirement; the trivial output on an empty symbol set is also
    # the correct answer. Honest declaration: no upstream pass is required.
    depends_on=[],
)
def link_containment(ctx: LinkerContext) -> LinkerResult:
    """Create contains edges from class/interface symbols to their methods.

    For each method symbol with a qualified name (e.g., User.save),
    finds the corresponding class symbol (User) and creates a
    `contains` edge from class → method.

    Args:
        ctx: LinkerContext with all symbols and existing edges.

    Returns:
        LinkerResult with new `contains` edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Build multimap of class/interface names to symbols.
    # Multiple classes can share the same name (e.g., Django has 238 classes
    # named "Model" — 1 real + 237 test stubs). When linking methods, we
    # prefer the class in the same file as the method.
    # Also index by qualified_name (ADR-0032) for proto service→rpc and
    # nested message containment, where name is unqualified but
    # qualified_name is fully qualified (e.g., name="HelloService",
    # qualified_name="hello.HelloService").
    container_by_name: dict[str, list[Symbol]] = {}
    for sym in ctx.symbols:
        if sym.kind in CONTAINER_KINDS:
            container_by_name.setdefault(sym.name, []).append(sym)
            qualified = sym.qualified_name
            if qualified and qualified != sym.name:
                container_by_name.setdefault(qualified, []).append(sym)

    # Build set of existing contains edge keys for deduplication
    existing_contains: set[tuple[str, str]] = {
        (e.src, e.dst) for e in ctx.edges if e.edge_type == "contains"
    }

    edges: list[Edge] = []

    for sym in ctx.symbols:
        # Process methods, getters, setters, and nested classes
        if sym.kind not in CONTAINABLE_KINDS and sym.kind not in CONTAINER_KINDS:
            continue

        # Extract parent name from qualified name (e.g., "User.save" → "User")
        # For top-level classes with no separator, parent_name is None → skip
        parent_name = _extract_parent_name(sym.name)
        if parent_name is None:
            continue

        parent_sym = _find_parent(
            parent_name, sym.path, container_by_name, sym.language,
        )
        if parent_sym is None:
            continue

        # Skip self-containment
        if parent_sym.id == sym.id:
            continue  # pragma: no cover - defensive

        # Skip duplicates
        pair = (parent_sym.id, sym.id)
        if pair in existing_contains:
            continue

        edges.append(Edge.create(
            src=parent_sym.id,
            dst=sym.id,
            edge_type="contains",
            line=sym.span.start_line if sym.span else 0,
            confidence=1.0,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="naming_convention",
            derived_from=[parent_sym.id, sym.id],
        ))
        # Track to avoid duplicates within this run
        existing_contains.add(pair)

    # --- Phase 1.5: qualified_name fallback ---
    # Handles cases where sym.name is unqualified (no separator) but
    # sym.qualified_name is fully qualified (e.g., proto RPCs where
    # name="BidiHello" but qualified_name="hello.HelloService.BidiHello").
    for sym in ctx.symbols:
        if sym.kind not in CONTAINABLE_KINDS and sym.kind not in CONTAINER_KINDS:
            continue

        # Only apply when name has no separator (Phase 1 didn't match)
        if _extract_parent_name(sym.name) is not None:
            continue

        # Need a qualified_name with a separator
        qualified = sym.qualified_name
        if not qualified:
            continue
        parent_name = _extract_parent_name(qualified)
        if parent_name is None:
            continue

        parent_sym = _find_parent(
            parent_name, sym.path, container_by_name, sym.language,
        )
        if parent_sym is None:
            continue

        # Skip self-containment
        if parent_sym.id == sym.id:
            continue  # pragma: no cover - defensive

        pair = (parent_sym.id, sym.id)
        if pair in existing_contains:
            continue

        edges.append(Edge.create(
            src=parent_sym.id,
            dst=sym.id,
            edge_type="contains",
            line=sym.span.start_line if sym.span else 0,
            confidence=0.95,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="canonical_name",
            derived_from=[parent_sym.id, sym.id],
        ))
        existing_contains.add(pair)

    # --- Phase 2: Span-based fallback for unqualified names ---
    # Handles cases where analyzers emit unqualified names (e.g., Ruby
    # analyzer emits "HTTP" instead of "Postal::HTTP").  For each symbol
    # that wasn't connected by naming convention, check if any container
    # in the same file has a span that fully encloses it.
    containers_by_file: dict[str, list[Symbol]] = {}
    for sym in ctx.symbols:
        if sym.kind in CONTAINER_KINDS and sym.span is not None:
            containers_by_file.setdefault(sym.path, []).append(sym)

    for sym in ctx.symbols:
        if sym.kind not in CONTAINABLE_KINDS and sym.kind not in CONTAINER_KINDS:
            continue
        if sym.span is None:
            continue

        # Skip if naming convention already found a parent
        parent_name = _extract_parent_name(sym.name)
        if parent_name is not None:
            continue

        # Find the tightest enclosing container in the same file
        file_containers = containers_by_file.get(sym.path, [])
        best: Symbol | None = None
        best_span_size = float("inf")
        for container in file_containers:
            if container.id == sym.id:
                continue
            assert container.span is not None  # guaranteed by filter above
            if (
                container.span.start_line <= sym.span.start_line
                and sym.span.end_line <= container.span.end_line
            ):
                span_size = container.span.end_line - container.span.start_line
                if span_size < best_span_size:
                    best = container
                    best_span_size = span_size

        if best is None:
            continue

        pair = (best.id, sym.id)
        if pair in existing_contains:
            continue

        edges.append(Edge.create(
            src=best.id,
            dst=sym.id,
            edge_type="contains",
            line=sym.span.start_line,
            confidence=0.9,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="span_overlap",
            derived_from=[best.id, sym.id],
        ))
        existing_contains.add(pair)

    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(
        symbols=[],
        edges=edges,
        run=run,
    )
