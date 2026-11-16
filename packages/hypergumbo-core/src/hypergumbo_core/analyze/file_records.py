# SPDX-License-Identifier: AGPL-3.0-or-later
"""One file pseudo-symbol per path, whoever names the file.

THE RULE. A path has exactly one FILE PSEUDO-SYMBOL: the ``kind="file"``
Symbol whose id is ``make_file_id(lang, path)`` (ADR-0036 Ruling 3's
``1-1:file:file`` triple). A manifest entry that also carries ``kind="file"``
(an npm script or ``main``, a pyproject script; ``meta["entry_role"]``,
audit-findings 0005) has another id shape and is not one.

WHY IT NEEDS A PASS. ``make_file_id`` takes a language, and every producer
that names a file picks that language itself: the JavaScript analyzer reads
the ``<script>`` block of a Svelte or Vue component and mints
``typescript:src/App.vue`` for it; the Vue analyzer anchors the same file's
template edges on ``vue:src/App.vue``, which the orchestrator then
synthesizes; the PHP analyzer mints ``php:x.blade.php`` and the Blade
analyzer anchors on ``blade:x.blade.php``. Id-equality dedup sees two
different ids, so both nodes persist and the file's edges are split between
them: a consumer walking from either node sees part of the file. No producer
is wrong about the file existing, so the reconciliation is here, in one
place, and not in each producer (ADR-0057: two producers of one declaration
yield one record).

WHERE IT RUNS. :func:`unify_file_pseudo_symbols` runs AFTER the linkers
(a linker can mint a file node too) and before ``deduplicate_edges``. Where a
path has several file pseudo-symbols, they are several producers' records of
one file and become one.

WHICH RECORD SURVIVES. The one in the FILE's own language: the language
``taxonomy.get_language`` gives the path, the same reading the orchestrator's
own anchors use (ADR-0031: ``Symbol.language`` is the source language of the
declaration, and the declaration here is the file). A Vue component is a
``vue`` file whatever language its ``<script>`` is in; the members of that
block keep their own ``typescript`` / ``javascript``. When no member is in
the file's own language (the taxonomy does not know the extension, or the
only producers were other-language ones), the first emitted survives:
analyzers precede the synthesis pass, which precedes the linkers.

WHAT THE SURVIVOR CARRIES. Its own attributes, with ``origin`` the union of
the members' pass ids (its own first), ``meta`` the union (its own value
wins a key both set) and a ``span`` covering every member's. Each dropped
member's id is rewritten to the survivor's in every edge ``src``, ``dst`` and
``derived_from`` and in every usage context's ``symbol_ref``; no edge is
dropped here, and two edges that become identical are folded by the caller's
``deduplicate_edges``, which keeps every call site.

WHAT IT DOES NOT DO. It never CREATES a file Symbol and never changes the
language slot of a path's only one: a component whose single file node the
JavaScript analyzer minted stays ``typescript:`` / ``javascript:``. Changing
that slot changes the node's ``stable_id`` (``make_file_stable_id`` hashes
the language), which is a stable-id scheme change and not a merge.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..ir import Edge, Span, Symbol, UsageContext
from ..taxonomy import get_language

_FILE_ID_SUFFIX = ":1-1:file:file"


def is_file_pseudo_symbol(symbol: Symbol) -> bool:
    """True for THE file node of a path, false for a manifest entry of kind ``file``."""
    return symbol.kind == "file" and symbol.id.endswith(_FILE_ID_SUFFIX)


def _as_list(origin: "str | List[str]") -> List[str]:
    return list(origin) if isinstance(origin, list) else ([origin] if origin else [])


def _survivor(path: str, members: Sequence[Symbol]) -> Symbol:
    """The member in the file's own language, else the first emitted."""
    own_language = get_language(Path(path))
    if own_language is not None:
        for member in members:
            if member.language == own_language:
                return member
    return members[0]


def _absorb(survivor: Symbol, member: Symbol) -> None:
    """Fold a dropped member's provenance, meta and extent into the survivor."""
    origins = _as_list(survivor.origin)
    origins.extend(o for o in _as_list(member.origin) if o not in origins)
    survivor.origin = origins
    if member.meta:
        meta = dict(member.meta)
        meta.update(survivor.meta or {})
        survivor.meta = meta
    if member.span is not None:
        if survivor.span is None:
            survivor.span = member.span
        else:
            survivor.span = Span(
                start_line=min(survivor.span.start_line, member.span.start_line),
                start_col=0,
                end_line=max(survivor.span.end_line, member.span.end_line),
                end_col=0,
            )
        survivor.line_span = survivor.span.end_line - survivor.span.start_line + 1


def rewire_endpoints(
    edges: List[Edge],
    remap: Dict[str, str],
    usage_contexts: Optional[List[UsageContext]] = None,
) -> int:
    """Rewrite every reference to a remapped Symbol id; return the edges changed.

    ``edge_key`` is reset on a changed edge so ``deduplicate_edges`` recomputes
    it from the new endpoints (the merge pass's convention, ADR-0057 §11).
    """
    changed_edges = 0
    for edge in edges:
        changed = False
        if edge.src in remap:
            edge.src = remap[edge.src]
            changed = True
        if edge.dst in remap:
            edge.dst = remap[edge.dst]
            changed = True
        if edge.derived_from and any(ref in remap for ref in edge.derived_from):
            edge.derived_from = [remap.get(ref, ref) for ref in edge.derived_from]
        if changed:
            edge.edge_key = None
            changed_edges += 1
    for context in usage_contexts or []:
        if context.symbol_ref in remap:
            context.symbol_ref = remap[context.symbol_ref]
    return changed_edges


def unify_file_pseudo_symbols(
    symbols: List[Symbol],
    edges: List[Edge],
    usage_contexts: Optional[List[UsageContext]] = None,
) -> Dict[str, str]:
    """Leave each path exactly one file pseudo-symbol; return ``dropped id -> survivor id``.

    Mutates ``symbols`` (the dropped members removed, in place), the survivor
    (see the module docstring) and ``edges`` / ``usage_contexts`` (rewired).
    Paths must already be repo-relative, so it runs after the linkers' path
    normalisation. A graph with one file pseudo-symbol per path is returned
    untouched, with an empty map.
    """
    by_path: Dict[str, List[Symbol]] = {}
    for symbol in symbols:
        if is_file_pseudo_symbol(symbol) and symbol.path:
            members = by_path.setdefault(symbol.path, [])
            # One id emitted twice is one record already; id-keyed consumers
            # see one node, and there is nothing to rewire.
            if all(member.id != symbol.id for member in members):
                members.append(symbol)

    remap: Dict[str, str] = {}
    for path, members in by_path.items():
        if len(members) < 2:
            continue
        survivor = _survivor(path, members)
        for member in members:
            if member is not survivor:
                _absorb(survivor, member)
                remap[member.id] = survivor.id
    if not remap:
        return remap

    symbols[:] = [s for s in symbols if not (s.id in remap and is_file_pseudo_symbol(s))]
    rewire_endpoints(edges, remap, usage_contexts)
    return remap
