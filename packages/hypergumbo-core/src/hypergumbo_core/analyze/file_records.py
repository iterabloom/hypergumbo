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

WHO IS A PRODUCER, AND WHO ONLY REFERS. A producer speaks AS the file: it
emits the file Symbol, or it anchors an edge's ``src`` on the file id (which
:func:`~.base.synthesize_file_symbols_for_dangling_edges` turns into a Symbol
on its behalf). An edge's ``dst`` naming a file id only REFERS to the file.
The two are treated differently:

* :func:`repoint_file_references` runs BEFORE the synthesis pass. A ``dst``
  that names a file id no Symbol has, on a path that already has a file
  Symbol and that no edge anchors a ``src`` on, is re-pointed at the Symbol
  the path has. A reference therefore never mints a second node for a path:
  an analyzer may resolve an import to ``make_file_id(<target's language>,
  <target path>)`` without knowing which producer owns the target's record.
* :func:`unify_file_pseudo_symbols` runs AFTER the linkers. Where a path
  still has several file pseudo-symbols, they are several producers' records
  of one file and become one.

:func:`file_reference_dst` is what such an analyzer calls: it resolves a
relative import against the importing file's directory and, when the file is
in the tree, returns its file id, so the edge ends on the file's node and not
on an ``external_symbol`` stand-in for a file that sits in the repository.

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

WHAT A FOLD MUST NOT LOSE. ``deduplicate_edges`` keeps the first of two
identical edges and the call sites of the second, nothing else. The rewiring
here makes a component's two views of one import identical: the template
analyzer's edge (``App.vue`` uses ``<Kid>``, with the tag's directives in its
meta) and the js-module linker's (the script's ``import Kid``, whose
``derived_from`` names the placeholder edge it resolved, which is how that
placeholder comes to be demoted, ADR-0057 section 14.1). So before the fold
the edge that will survive takes the other's ``origin``, and its
``derived_from`` when it states none of its own. One edge then says both
passes found the relation, and the placeholder is still demoted. It never
adds a second pass's ``derived_from`` to an edge that has one: which pass
consumed which placeholder stays readable.

WHAT IT DOES NOT DO. It never CREATES a file Symbol and never changes the
language slot of a path's only one: a component whose single file node the
JavaScript analyzer minted stays ``typescript:`` / ``javascript:``. Changing
that slot changes the node's ``stable_id`` (``make_file_stable_id`` hashes
the language), which is a stable-id scheme change and not a merge.
"""
from __future__ import annotations

import os
import posixpath
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..ir import Edge, Span, Symbol, UsageContext
from ..paths import normalize_path
from ..taxonomy import get_language
from .base import make_file_id

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
) -> List[Edge]:
    """Rewrite every reference to a remapped Symbol id; return the edges changed.

    ``edge_key`` is reset on a changed edge so ``deduplicate_edges`` recomputes
    it from the new endpoints (the merge pass's convention, ADR-0057 §11).
    """
    changed_edges: List[Edge] = []
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
            changed_edges.append(edge)
    for context in usage_contexts or []:
        if context.symbol_ref in remap:
            context.symbol_ref = remap[context.symbol_ref]
    return changed_edges


def _carry_provenance_across_folds(edges: List[Edge], rewired: List[Edge]) -> None:
    """Give each soon-to-survive edge what the fold would drop (module docstring).

    Only relations a rewired edge takes part in are looked at: every other
    fold is ``deduplicate_edges``' as before.
    """
    touched = {(e.src, e.dst, e.edge_type) for e in rewired}
    first: Dict[tuple[str, str, str], Edge] = {}
    for edge in edges:
        key = (edge.src, edge.dst, edge.edge_type)
        if key not in touched:
            continue
        survivor = first.setdefault(key, edge)
        if survivor is edge:
            continue
        origins = _as_list(survivor.origin)
        origins.extend(o for o in _as_list(edge.origin) if o not in origins)
        survivor.origin = origins
        if not survivor.derived_from and edge.derived_from:
            survivor.derived_from = list(edge.derived_from)


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
    _carry_provenance_across_folds(edges, rewire_endpoints(edges, remap, usage_contexts))
    return remap


def repoint_file_references(
    symbols: Sequence[Symbol],
    edges: List[Edge],
    repo_root: Optional[Path] = None,
) -> Dict[str, str]:
    """Land a reference to a file on the file Symbol its path already has.

    For every edge ``dst`` of ``make_file_id`` shape that no Symbol carries
    and no edge uses as ``src``: when the path it names already has a file
    pseudo-symbol (in whatever language slot), the ``dst`` is rewritten to
    that Symbol's id. Returns ``referenced id -> existing id``. See the module
    docstring for why a ``src`` is left alone.

    Symbol paths and ids may still be absolute here (this runs before the
    orchestrator's path normalisation), so paths are compared repo-relative.
    """
    root_prefix: Optional[str] = None
    if repo_root is not None:
        root_prefix = normalize_path(str(repo_root)).rstrip("/") + "/"

    def _relative(path: str) -> str:
        normed = normalize_path(path)
        if root_prefix is not None and normed.startswith(root_prefix):
            return normed[len(root_prefix):]
        return normed

    existing_ids = {s.id for s in symbols}
    file_symbol_of: Dict[str, Symbol] = {}
    for symbol in symbols:
        if is_file_pseudo_symbol(symbol) and symbol.path:
            file_symbol_of.setdefault(_relative(symbol.path), symbol)
    if not file_symbol_of:
        return {}

    anchored = {edge.src for edge in edges}
    remap: Dict[str, str] = {}
    for edge in edges:
        dst = edge.dst
        if dst in remap or not dst.endswith(_FILE_ID_SUFFIX):
            continue
        if dst in existing_ids or dst in anchored:
            continue
        # ``{lang}:{path}:1-1:file:file``; the path slot may hold colons.
        path = dst[: -len(_FILE_ID_SUFFIX)].partition(":")[2]
        target = file_symbol_of.get(_relative(path))
        if target is not None:
            remap[dst] = target.id
    if remap:
        for edge in edges:
            if edge.dst in remap:
                edge.dst = remap[edge.dst]
                edge.edge_key = None
    return remap


def file_reference_dst(
    importer_lang: str,
    importer_file: Path,
    importer_rel_path: str,
    import_path: str,
) -> Optional[str]:
    """The file id a RELATIVE import names, when that file is in the tree.

    ``import_path`` is resolved against the importing file's directory.
    ``importer_file`` is that file on disk and ``importer_rel_path`` its
    repo-relative POSIX path, both as the analyzer was handed them: the
    target's id path is computed from the second, lexically, so no root is
    re-derived here, and a path that normalizes above the repo root never
    resolves. Only ``./`` and ``../`` specifiers are relative; a bare or
    aliased one (``@/components/X.vue``, ``$lib/X.svelte``) is configuration
    this function does not read, and gets None, as does a path naming no
    existing file. The caller keeps its placeholder dst for those.

    The id's language is the TARGET's own (``taxonomy.get_language``), else
    ``importer_lang`` for a format the taxonomy does not list: the analyzers
    that call this record same-format component imports. Whether the target's
    node carries that slot is not this function's concern:
    :func:`repoint_file_references` lands the edge on the node the path has.
    """
    if not import_path.startswith(("./", "../")):
        return None
    rel = posixpath.normpath(
        posixpath.join(posixpath.dirname(importer_rel_path), import_path)
    )
    if rel == ".." or rel.startswith("../"):
        return None
    target = Path(os.path.normpath(importer_file.parent / import_path))
    if not target.is_file():
        return None
    return make_file_id(get_language(target) or importer_lang, rel)
