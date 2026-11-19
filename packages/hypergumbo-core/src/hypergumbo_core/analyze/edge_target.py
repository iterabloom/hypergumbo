# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where an edge goes TO: every endpoint is the id of a node, never a hand-built string.

The defect this module exists for
---------------------------------
An analyzer spelled an edge endpoint by hand (``f"fish:{path}:file"``,
``f"cmake:external:{dep}:library"``, ``f"bitbake:{path}"``). Such a string is
the id of no node and is not even shaped like one, so ``ir._parse_dangling_id``
takes its fewer-than-five-slot fallback and boundary synthesis mints
``<lang>:<unknown>:0-0:<some token>:external_symbol``. Every edge of that
language that used the same spelling then lands on that ONE node: all of a
repository's D imports started at ``d:<unknown>:0-0:file:external_symbol``
while each file's real ``:1-1:file:file`` node carried none of them, and three
BitBake ``depends_on`` edges to three packages became one.

``analyze.edge_source`` holds the rule for the src of a call. This is its
counterpart for what an endpoint may BE (ADR-0034, ADR-0036). An endpoint is
one of three things, and the producer has to decide which:

- **this file's node**: ``make_file_id`` (the orchestrator materialises it);
- **another node of the tree**: an id that was LOOKED UP, either a symbol the
  run emitted or, for a file named by path, :func:`in_tree_file_id`. A miss is
  the third case, not a guess;
- **something outside the tree**: a stand-in in the id grammar with span
  ``0-0``: ``make_unresolved_edge`` for a call, ``make_module_import_dst`` for a
  module or file imported by path, :func:`external_target` for anything else
  that has a name.

What this module provides
-------------------------
- :func:`in_tree_file_id` -- the lookup for a file named by path. The node is
  built in the language the file's own analyzer uses (``taxonomy.get_language``),
  so a fish script that sources ``x.sh`` reaches the bash file node rather than
  a fish-language twin of it. A file the taxonomy gives no language has no node
  and is a miss.
- :func:`external_target` -- the stand-in dst and its ``ExternalRef``, for the
  edge types ``make_unresolved_edge`` (calls only) does not build. The name slot
  goes through ``make_symbol_id``, so a colon in the name cannot shift the
  anchored parse.
- :func:`non_id_edge_endpoints` -- the gate, in two modes. It runs on ONE
  pass's result, before finalize, because the ``spec_validator`` sees only the
  ``<unknown>`` node finalize made of the string, not which producer wrote it.
  Its STRICT default is what producer tests assert is empty on the production
  path (``analyze_*`` over a fixture): every endpoint is an emitted symbol, a
  file anchor, or a ``0-0`` stand-in whose last slot is one of
  :data:`STAND_IN_SLOT_TOKENS`.
- :func:`stamp_non_id_edge_endpoints` -- the same gate in production, on every
  run. The analyzer chokepoint (``all_analyzers.collect_analyzer_result``) and
  the linker chokepoint (``linkers.registry._run_linker_with_cache``) call it
  on each pass's own output before the run is serialized. It asks the gate's
  ``grammar_only`` question -- is each endpoint an emitted symbol's id or a
  string that parses as a five-slot id (:func:`_five_slots`, ADR-0036 Ruling
  1)? -- and records the count of those that are not on
  ``AnalysisRun.non_id_edge_endpoints``, with a warning naming the first
  :data:`NON_ID_SAMPLE_SIZE` distinct ones. The strict question would be the
  wrong one there: a well-formed ``0-0`` stand-in whose last slot is a
  use-site word (``package``, ``attribute``, ``target``) is ADR-0036 Ruling
  2's unfinished kind-slot migration, hundreds to thousands per repository,
  and would drown the count; and a linker's edges end on other passes' nodes
  by design, so "emitted by this pass" cannot be required of them. ADR-0034's
  Enforcement section states the check.
"""
from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import TYPE_CHECKING, AbstractSet, Iterable, Optional

from ..ir import ExternalRef, NodeId
from ..discovery import is_excluded
from ..taxonomy import get_language
from .base import make_file_id, make_symbol_id
from .edge_source import unemitted_edge_sources

if TYPE_CHECKING:
    from ..ir import AnalysisRun, Edge, Symbol

#: The ``module_path`` of a stand-in whose module is not known (ADR-0037).
UNKNOWN_MODULE = "external"

#: What the last slot of a stand-in dst may hold. Not a set of symbol kinds:
#: ``unresolved`` (``make_unresolved_edge``, :func:`external_target`) and
#: ``module`` (``make_module_import_dst``) are the use-site reference syntax
#: finalize folds into the boundary node (ADR-0036 Ruling 2), whose own kind,
#: ``external_symbol``, is the third.
STAND_IN_SLOT_TOKENS: frozenset[str] = frozenset({"unresolved", "module", "external_symbol"})

_FILE_ID_SUFFIX = ":1-1:file:file"
_STAND_IN_SPAN = "0-0"
_SPAN = re.compile(r"[0-9]+-[0-9]+")


def in_tree_file_id(
    file_path: Path, rel_path: str, candidates: Iterable[str],
    *, own_lang: Optional[str] = None, own_suffixes: Iterable[str] = (),
) -> Optional[NodeId]:
    """The file node of the first candidate that is a file of the repository.

    ``file_path`` is the absolute path of the file being analysed and
    ``rel_path`` its repository-relative path; the repository root is what is
    left of the first when the second is taken off. Each candidate is a
    repository-relative path (the caller applies its language's own rule for
    what a written path is relative to). A candidate that leaves the
    repository, is absent, or has no language in the taxonomy is skipped.
    So is one that is on disk and that DISCOVERY leaves out (git ignores it
    and does not track it, or a default exclude covers it, ADR-0004): the
    disk is not the analysed tree, and a file id for such a file would have
    the synthesis pass mint a node for a file no analyzer read.
    Returns ``None`` when none is left: the target is outside the tree.

    ``own_lang`` / ``own_suffixes``: the calling analyzer's language and the
    endings of the file names it reads, for an analyzer whose files the
    taxonomy names no language for (Robot ``.resource``, Meson
    ``meson.build``). Such a file is still a node of the run, in the
    analyzer's language; without these it would be a miss. The taxonomy's
    language, when it names one, still wins.
    """
    repo_root = file_path.parents[len(Path(rel_path).parts) - 1]
    endings = tuple(own_suffixes)
    for candidate in candidates:
        norm = posixpath.normpath(candidate.replace("\\", "/"))
        if norm == "." or norm == ".." or norm.startswith(("../", "/")):
            continue
        lang = get_language(Path(norm))
        if lang is None and own_lang is not None and endings and norm.endswith(endings):
            lang = own_lang
        target = repo_root / norm
        if lang is not None and target.is_file() and not is_excluded(target, repo_root):
            return make_file_id(lang, norm)
    return None


def external_target(
    lang: str, name: str, module_path: str = UNKNOWN_MODULE,
) -> tuple[str, Optional[ExternalRef]]:
    """The dst of an edge to ``name`` outside the tree, and its ``ExternalRef``.

    ``<lang>:<module_path>:0-0:<name>:unresolved``, the shape
    ``ir.format_legacy_dst`` gives, with the name slot sanitized. The ref is
    ``None`` when the module is not known (ADR-0037's "unidentified reference"
    cell, WI-huzuv), as ``make_unresolved_edge`` leaves it. Pass both to
    ``Edge.create`` with ``is_resolved=False``.
    """
    dst = make_symbol_id(lang, module_path, 0, 0, name, "unresolved")
    if module_path == UNKNOWN_MODULE:
        return dst, None
    return dst, ExternalRef(lang=lang, module_path=module_path, name=name)


def _is_lower_identifier(slot: str) -> bool:
    """A ``lang`` or ``kind`` slot: an identifier with no upper-case letter."""
    return slot.isidentifier() and slot == slot.lower()


def _five_slots(endpoint: str) -> Optional[tuple[str, str, str, str, str]]:
    """``(lang, path, span, name, kind)`` when ``endpoint`` parses as a node id.

    The anchored parse of ADR-0036 Ruling 1: ``lang`` up to the FIRST ``:``,
    ``span``, ``name`` and ``kind`` the last three ``:``-separated slots, and
    ``path`` (the one colon-tolerant slot) what is left between. It parses
    when ``lang`` and ``kind`` are lowercase identifiers, ``path`` is not
    empty and ``span`` is ``<digits>-<digits>``; ``name`` is colon-free by
    construction and may be empty (the ruling's table). Anything else is
    ``None``, outside the id grammar: finalize either folds it through its
    fewer-than-five-slot fallback into a ``<unknown>``-path node, or, for a
    ``:`` inside the name slot (``std::uint8_t``), recovers a node only by
    its own lenient parse and ``meta.callee_name``.
    """
    lang, sep, rest = endpoint.partition(":")
    parts = rest.rsplit(":", 3)
    if not sep or len(parts) != 4:
        return None
    path, span, name, kind = parts
    if not (
        _is_lower_identifier(lang) and path
        and _SPAN.fullmatch(span) and _is_lower_identifier(kind)
    ):
        return None
    return lang, path, span, name, kind


def _is_stand_in(endpoint: str, tokens: AbstractSet[str]) -> bool:
    """Whether ``endpoint`` parses (:func:`_five_slots`) with span ``0-0``, a
    name and a stand-in last slot."""
    slots = _five_slots(endpoint)
    if slots is None:
        return False
    _lang, _path, span, name, last = slots
    return span == _STAND_IN_SPAN and bool(name) and last in tokens


def non_id_edge_endpoints(
    symbols: Iterable["Symbol"], edges: Iterable["Edge"],
    *, stand_in_tokens: AbstractSet[str] = STAND_IN_SLOT_TOKENS,
    grammar_only: bool = False,
) -> list[tuple[str, "Edge"]]:
    """``("src" | "dst", edge)`` for every endpoint that is no id.

    ``symbols`` and ``edges`` are one pass's result. An edge whose two
    endpoints both fail is listed twice.

    The default is the STRICT gate producer tests assert is empty. A src
    passes when it is an emitted symbol's id or a file anchor
    (``unemitted_edge_sources``). A dst passes when it is an emitted symbol's
    id, a file anchor, or a stand-in (:func:`_is_stand_in`, last slot in
    ``stand_in_tokens``).

    ``grammar_only=True`` asks the narrower question the orchestrator asks of
    every pass in production: is the endpoint IN THE ID GRAMMAR, so finalize
    can read it as a node id at all? An endpoint (src or dst alike) is in the
    grammar when it is an emitted symbol's id, a file anchor, or parses as a
    five-slot id anchored from both ends (:func:`_five_slots`), whatever its
    span and last slot. Only the rest is listed: a string finalize parses
    with its fewer-than-five-slot fallback into one shared
    ``<lang>:<unknown>:0-0:<token>:external_symbol`` node, one whose span or
    kind slot is not a span or a kind, or one with a raw ``:`` in its name
    slot, which shifts the anchored slots (Ruling 1 requires the name slot
    sanitized ``:`` -> ``.``). ``stand_in_tokens`` is not
    read. What this mode does NOT list, on purpose, so that it is not
    drowned by them: a well-formed ``0-0`` stand-in whose last slot is a
    use-site word (``package``, ``attribute``, ``target``, ``doc_link``),
    which is ADR-0036 Ruling 2's unfinished kind-slot migration; and a
    well-formed id of a symbol this pass did not emit, src or dst (the
    strict mode lists the first as ``unemitted_edge_sources`` and the second
    as a dst that is no stand-in), which a linker's edges do legitimately
    and which ``spec_validator``'s dangling-endpoint check sees after
    finalize.
    """
    symbols = list(symbols)
    edges = list(edges)
    ids = {s.id for s in symbols}
    if grammar_only:
        def in_grammar(endpoint: str) -> bool:
            return endpoint in ids or _five_slots(endpoint) is not None

        bad: list[tuple[str, "Edge"]] = []
        for e in edges:
            if not in_grammar(e.src):
                bad.append(("src", e))
            if not in_grammar(e.dst):
                bad.append(("dst", e))
        return bad
    bad = [("src", e) for e in unemitted_edge_sources(symbols, edges)]
    bad.extend(
        ("dst", e) for e in edges
        if e.dst not in ids
        and not e.dst.endswith(_FILE_ID_SUFFIX)
        and not _is_stand_in(e.dst, stand_in_tokens)
    )
    return bad


#: How many distinct offending endpoints the run's warning names.
NON_ID_SAMPLE_SIZE = 3


def stamp_non_id_edge_endpoints(
    run: "AnalysisRun", symbols: Iterable["Symbol"], edges: Iterable["Edge"],
) -> None:
    """Record on ``run`` how many of its pass's edge endpoints are no id.

    The production call of :func:`non_id_edge_endpoints`, in
    ``grammar_only`` mode, made at the two chokepoints every pass's output
    crosses before its run is serialized: ``collect_analyzer_result`` for an
    analyzer, ``_run_linker_with_cache`` for a linker. Sets
    ``run.non_id_edge_endpoints`` to the number of endpoints listed (a
    measured ``0`` when none), and, when there are any, appends one warning
    naming the first :data:`NON_ID_SAMPLE_SIZE` distinct ones in edge order,
    so a reader of ``analysis_runs`` can find the producer and the spelling.
    """
    bad = non_id_edge_endpoints(symbols, edges, grammar_only=True)
    run.non_id_edge_endpoints = len(bad)
    if not bad:
        return
    sample: list[str] = []
    for side, edge in bad:
        endpoint = edge.src if side == "src" else edge.dst
        if endpoint not in sample:
            sample.append(endpoint)
            if len(sample) == NON_ID_SAMPLE_SIZE:
                break
    run.warnings.append(
        f"UserWarning: {len(bad)} edge endpoint(s) of this pass are not "
        f"node ids (no emitted symbol, and not lang:path:start-end:name:kind); "
        f"first {len(sample)} distinct: {', '.join(repr(s) for s in sample)}"
    )
