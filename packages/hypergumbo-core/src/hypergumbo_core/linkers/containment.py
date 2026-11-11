# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker: containment for creating `contains` edges between containers and members.

This linker connects container symbols (classes, interfaces, protocols, structs,
traits, enums, modules, messages, records, files and the rest of
``CONTAINER_KINDS``) to their member symbols (methods, constructors, getters,
setters, fields, subscripts, top-level functions and variables, RPCs, nested messages,
and nested containers) based on naming conventions and span nesting, creating
`contains` edges. Without these edges, members are
orphaned in the graph — disconnected from their parent types — which inflates
orphan rates and hides hierarchical structure from slice traversal.

How It Works
------------
Three phases, tried in order of precedence:

**Phase 1 — Naming convention** (confidence=0.85, ADR-0039-derived from the
``naming_convention`` evidence seed — a name-parse heuristic, so BELOW the
structurally-certain span_overlap 0.90):
Extracts the parent name from the symbol's ``name`` field using
language-specific separators (``.``, ``#``, ``::``).  For example,
``User.save`` → parent ``User``.
When several containers share that name, ``_find_parent`` prefers the one
in the child's file (disambiguating same-file duplicates by the enclosing
span, and refusing when none encloses the child), then one in the same
language; when the child's language is known it never crosses a language
boundary.

**Phase 1.5 — qualified_name fallback** (confidence=0.95; the owner sits in
the child's file and its own qualified name, when it has one, is the prefix,
since a prefix can be a namespace named like a class: C# ``Demo.DemoMain`` is
not a member of a ``class Demo`` whose qualified name is ``Demo.Demo``, nor of
a global ``class Demo`` in another file):
When ``sym.name`` has no separator (e.g., proto RPCs where
``name="BidiHello"``), falls back to ``sym.qualified_name`` (the
ADR-0032 typed sibling field that carries fully-qualified scoped
identifiers, e.g., ``hello.HelloService.BidiHello``). This handles
proto service→rpc containment and nested message containment.
It applies to any symbol with an unqualified ``name`` and a
separator-bearing ``qualified_name`` — the shape proto, capnp, fish
and similar analyzers emit. Several mainstream analyzers also populate
``qualified_name`` (Java and Rust package- or module-qualify it;
Python sets it to the bare name), but a symbol whose ``name`` already
has a separator belongs to Phase 1 and Phase 1.5 skips it.

**Phase 2 — Span-based fallback** (confidence=0.9):
For every symbol whose ``name`` has no separator, and every dotted name
that no earlier phase and no edge already in the graph placed, checks if
any container symbol in the same file has a span that fully encloses it,
and prefers the tightest (smallest span) enclosing container. Because
``file`` is a container kind, this is also what roots a file's top-level
functions, variables and classes at its ``kind="file"`` anchor. In no
phase is a file a child: its name is a dotted path, and a module spanning
the whole file encloses it. A dotted name whose prefix is no symbol (a Lean ``A.S`` in ``namespace A``, an F#
``A.run`` in ``module A``, a dotted Clojure or Agda module, a C++
out-of-line ``Token::get`` whose class is not emitted) gets this fallback
rather than staying an orphan; a dotted name Phase 1 placed is not given
a second parent. For a bare name only an identical parent→child pair
already emitted is deduplicated.

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

from ..member_names import member_owner
from ..ir import PASS_VERSION, AnalysisRun, Edge, make_pass_id
from ..symbol_kinds import container_kind_names
from .registry import LinkerContext, LinkerResult, register_linker, always_on_unreviewed

if TYPE_CHECKING:
    from ..ir import Span, Symbol

PASS_ID = make_pass_id("containment-linker")

# Symbol kinds that can be "contained" by a ``CONTAINER_KINDS`` symbol.
#
# Membership is by kind alone; no ``framework_role`` check is needed. An RPC
# is ``method`` + ``meta["framework_role"]="rpc"`` (ADR-0027 Cluster D), and
# ``method`` is in this set. ``message`` is a CANONICAL kind (audit-findings
# 0007) and is in this set as itself. No live schema producer emits ``rpc`` /
# ``service`` as a kind: proto.py emits ``method`` with
# ``framework_role="rpc"`` under an ``interface`` with
# ``framework_role="service"``, thrift.py emits ``function`` members of such
# an ``interface``, and capnp.py emits plain ``interface`` / ``method`` with
# no role. They pass the kind through positional helpers that the L3
# producer-coherence linter cannot statically gate (WI-jukav). The
# framework-linker producers don't reach this filter (their synthetic-node
# names lack class-qualifier separators, so the parent-extraction step in
# :func:`link_containment` skips them before kind matters).
#
# ``function`` and ``variable`` are graph-structural members of their
# enclosing file/module (dispatch:F4; INV-pohik). They are reached
# almost entirely through Phase-2 span_overlap — a top-level ``def`` or module
# ``var`` carries a bare name, so Phase-1 naming-convention cannot match it —
# attaching them to the ``kind="file"`` anchor that encloses their span
# (file-anchor:F1) so the file's ``contains`` tree is rooted at its top-level
# members and ``slice`` can expand "this file's symbols". A *dotted* function
# name (e.g. the JS ``userController.createUser`` route-handler idiom) can still
# match a same-file container via Phase 1 — that is accepted: the dotted name
# genuinely qualifies a real container when one exists in the file; when none
# does, Phase 2 roots it at the container whose span encloses it (the file).
#
# Reachability: ``contains`` is not a call edge, but reachability walks derive
# walk-only hops from it -- member -> container and file -> its type-like symbols
# (``anchor_execution.py::anchor_execution_edges``, used by dead-code-maybe),
# and class -> initializer for a class some edge constructs (``construction.py``).
# So a new ``contains`` edge can make more code reachable, which can only remove
# a dead-code-maybe claim. Apart from that initializer, it never makes a
# contained function reachable from its container.
CONTAINABLE_KINDS = frozenset(
    # ``field`` (class-body attributes, emitted with dotted names like
    # ``Widget.size``; emission-parity F5) roots at its class exactly like
    # ``method`` — without it, class-body fields are orphans. Its
    # reachability effect is the one in the note above. (WI-zajaz)
    #
    # ``subscript`` (Swift subscript members, emitted with dotted names
    # like ``JSON.subscript(key:)`` — the parenthesized ``(key:)`` suffix carries
    # no name separator, so parent-name extraction already yields ``JSON``) roots
    # at its enclosing type exactly like ``method``. Without it, struct-body
    # subscripts get no ``contains`` edge while the type's fields and methods
    # do, so accessors like ``json[...]`` look like orphaned members of their
    # type. Its reachability effect is the one in the note above. (The
    # separate gap where a ``json["key"]`` subscript-CALL site does not
    # resolve to the subscript node is a Swift-analyzer resolution facet, out
    # of scope here.) (WI-fokag)
    #
    # ``constructor`` (java, dart, csharp, swift, pony, apex, pascal and
    # solidity emit their initializers with this kind) roots at its class like
    # ``method``. Without it a class holds no ``contains`` edge to its
    # constructor, so a construction landing on the class (java/dart
    # ``instantiates``) reaches no initializer in construction.py's hop.
    # (WI-lozop)
    {
        "method", "getter", "setter", "message", "function", "variable", "field",
        "subscript", "constructor",
    }
)

# Symbol kinds that can "contain" other symbols.
# Includes struct/trait/enum for Rust (and Go/C/Zig structs) and message for
# proto (nested messages). NOT `service`: a service is ``interface`` (or
# ``class``) + ``meta["framework_role"]="service"`` (ADR-0027 Cluster D), and
# proto.py emits exactly that — so a `service` entry here would be dead, and
# both ``interface`` and ``class`` are already in this set (WI-jukav).
# Framework-linker producers don't reach this filter for the same reason as
# ``CONTAINABLE_KINDS`` above (synthetic-node names lack class-qualifier
# separators).
# One set, derived from the registry (``container_kind_names``) and shared
# with slice, so the two cannot drift (WI-totam). Each member is either a
# nominal type in the registry or in ``SCOPE_CONTAINER_KINDS``, so
# registering a type kind (pony's ``actor`` / ``primitive``, WI-higik) makes
# it a container here too.
CONTAINER_KINDS = container_kind_names()

# Separator vocabulary lives in ``member_names``, the one place every
# consumer reads it from, so they cannot disagree about it (INV-tihim).


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
    return member_owner(method_name)


def _find_parent(
    parent_name: str,
    child_path: str,
    container_by_name: dict[str, list[Symbol]],
    child_language: str | None = None,
    child_span: Span | None = None,
) -> Symbol | None:
    """Find the best parent container for a given parent name.

    When multiple containers share a name (e.g., Django's 238 Model classes),
    uses a priority cascade:
      1. Same file (most specific). When more than one same-name container
         lives in the child's file, disambiguate by SPAN — the true parent is
         the one whose span encloses the child (tightest wins). If none
         encloses the child, the naming match is to the wrong same-name class,
         so refuse rather than emit a provably-false containment (WI-vakuh).
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
    same_file = [c for c in candidates if c.path == child_path]
    if len(same_file) == 1:
        return same_file[0]
    if len(same_file) > 1:
        # Multiple same-name containers in one file (e.g. a real class plus a
        # same-named test stub). The first-match heuristic could bind a method
        # to the wrong class, whose span does not even enclose it. Disambiguate
        # by span: pick the enclosing container, tightest first.
        if child_span is not None:
            enclosing = [
                (c.span.end_line - c.span.start_line, c)
                for c in same_file
                if c.span is not None
                and c.span.start_line <= child_span.start_line
                and child_span.end_line <= c.span.end_line
            ]
            if enclosing:
                return min(enclosing, key=lambda item: item[0])[1]
            # No same-name container in this file encloses the child → the
            # name match is to the wrong class; refuse (Phase 2's span_overlap
            # fallback then roots the child at whatever does enclose it).
            return None
        # No span to disambiguate with: take the first match.
        return same_file[0]

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


def _may_be_contained(sym: Symbol) -> bool:
    """A member or nested container kind, and not a file: a file roots its
    own tree. Its name is a dotted path (``Demo.cs`` named a parent ``Demo``
    to Phase 1), and a module spanning the whole file encloses its span."""
    return sym.kind != "file" and (
        sym.kind in CONTAINABLE_KINDS or sym.kind in CONTAINER_KINDS
    )


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
    activation=always_on_unreviewed(),
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
        # Process CONTAINABLE_KINDS members and nested containers (not files)
        if not _may_be_contained(sym):
            continue

        # Extract parent name from qualified name (e.g., "User.save" → "User")
        # For top-level classes with no separator, parent_name is None → skip
        parent_name = _extract_parent_name(sym.name)
        if parent_name is None:
            continue

        parent_sym = _find_parent(
            parent_name, sym.path, container_by_name, sym.language,
            child_span=sym.span,
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
            # Confidence derives from the naming_convention registry seed
            # (0.85): a name-parse heuristic ranks below the certain
            # span_overlap 0.90 and stays under the 0.95 band ceiling.
            # (ADR-0039 R1; WI-vakuh / WI-lutad)
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="naming_convention",
            # derived-from endpoints: the child's qualified-name prefix names the parent; nothing
            #   else is read
            derived_from=[parent_sym.id, sym.id],
        ))
        # Track to avoid duplicates within this run
        existing_contains.add(pair)

    # --- Phase 1.5: qualified_name fallback ---
    # Handles cases where sym.name is unqualified (no separator) but
    # sym.qualified_name is fully qualified (e.g., proto RPCs where
    # name="BidiHello" but qualified_name="hello.HelloService.BidiHello").
    for sym in ctx.symbols:
        if not _may_be_contained(sym):
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

        # A qualified name's prefix may be a NAMESPACE that shares its name
        # with a class (C# ``namespace Demo;`` beside a ``class Demo``): the
        # owner must sit in the child's own file (a nested declaration is
        # declared inside its owner; C#'s partial class re-declares it there)
        # and its own qualified name, if it has one, must BE the prefix (that
        # class's is ``Demo.Demo``).
        owners = {parent_name: [
            c for c in container_by_name.get(parent_name, ())
            if c.path == sym.path and c.qualified_name in (None, parent_name)
        ]}
        parent_sym = _find_parent(
            parent_name, sym.path, owners, sym.language, child_span=sym.span,
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
            # derived-from endpoints: the child's qualified_name prefix names the parent; nothing
            #   else is read
            derived_from=[parent_sym.id, sym.id],
        ))
        existing_contains.add(pair)

    # --- Phase 2: Span-based fallback ---
    # Handles cases where analyzers emit unqualified names (e.g., Ruby
    # analyzer emits "HTTP" instead of "Postal::HTTP"), and dotted names
    # nothing above placed.  For each such symbol, check if any container
    # in the same file has a span that fully encloses it.
    containers_by_file: dict[str, list[Symbol]] = {}
    for sym in ctx.symbols:
        if sym.kind in CONTAINER_KINDS and sym.span is not None:
            containers_by_file.setdefault(sym.path, []).append(sym)
    contained = {child for _, child in existing_contains}

    for sym in ctx.symbols:
        if not _may_be_contained(sym):
            continue
        if sym.span is None:
            continue

        # A dotted name is served only when nothing placed it: Phase 1 /
        # Phase 1.5 found no owner (Lean ``A.S`` in ``namespace A``, an F#
        # ``A.run`` in ``module A``: the namespace or module is no symbol)
        # and no other pass gave it a ``contains`` edge. A bare name keeps
        # its fallback unconditionally (only an identical pair is skipped).
        if _extract_parent_name(sym.name) is not None and sym.id in contained:
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
            # derived-from endpoints: span nesting of the two endpoint symbols decides it
            derived_from=[best.id, sym.id],
        ))
        existing_contains.add(pair)

    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(
        symbols=[],
        edges=edges,
        run=run,
    )
