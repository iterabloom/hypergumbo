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
- :func:`non_id_edge_endpoints` -- the gate producer tests assert is empty on
  the production path (``analyze_*`` over a fixture). It runs on ONE analyzer's
  result, before finalize, because the ``spec_validator`` sees only the
  ``<unknown>`` node finalize made of the string, not which producer wrote it.
"""
from __future__ import annotations

import posixpath
from pathlib import Path
from typing import TYPE_CHECKING, AbstractSet, Iterable, Optional

from ..ir import ExternalRef
from ..taxonomy import get_language
from .base import make_file_id, make_symbol_id
from .edge_source import unemitted_edge_sources

if TYPE_CHECKING:
    from ..ir import Edge, Symbol

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


def in_tree_file_id(
    file_path: Path, rel_path: str, candidates: Iterable[str],
) -> Optional[str]:
    """The file node of the first candidate that is a file of the repository.

    ``file_path`` is the absolute path of the file being analysed and
    ``rel_path`` its repository-relative path; the repository root is what is
    left of the first when the second is taken off. Each candidate is a
    repository-relative path (the caller applies its language's own rule for
    what a written path is relative to). A candidate that leaves the
    repository, is absent, or has no language in the taxonomy is skipped.
    Returns ``None`` when none is left: the target is outside the tree.
    """
    repo_root = file_path.parents[len(Path(rel_path).parts) - 1]
    for candidate in candidates:
        norm = posixpath.normpath(candidate.replace("\\", "/"))
        if norm == "." or norm == ".." or norm.startswith(("../", "/")):
            continue
        lang = get_language(Path(norm))
        if lang is not None and (repo_root / norm).is_file():
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


def _is_stand_in(endpoint: str, tokens: AbstractSet[str]) -> bool:
    """Whether ``endpoint`` parses, anchored from both ends (ADR-0036 Ruling
    1), as a five-slot id with span ``0-0``, a name and a stand-in last slot."""
    lang, sep, rest = endpoint.partition(":")
    parts = rest.rsplit(":", 3)
    if not sep or len(parts) != 4:
        return False
    path, span, name, last = parts
    return bool(
        lang.isidentifier() and lang == lang.lower() and path
        and span == _STAND_IN_SPAN and name and last in tokens
    )


def non_id_edge_endpoints(
    symbols: Iterable["Symbol"], edges: Iterable["Edge"],
    *, stand_in_tokens: AbstractSet[str] = STAND_IN_SLOT_TOKENS,
) -> list[tuple[str, "Edge"]]:
    """``("src" | "dst", edge)`` for every endpoint that is no id.

    ``symbols`` and ``edges`` are one analyzer result's. A src passes when it
    is an emitted symbol's id or a file anchor (``unemitted_edge_sources``). A
    dst passes when it is an emitted symbol's id, a file anchor, or a stand-in
    (:func:`_is_stand_in`). An edge whose two endpoints both fail is listed
    twice.
    """
    symbols = list(symbols)
    edges = list(edges)
    ids = {s.id for s in symbols}
    bad: list[tuple[str, "Edge"]] = [
        ("src", e) for e in unemitted_edge_sources(symbols, edges)
    ]
    bad.extend(
        ("dst", e) for e in edges
        if e.dst not in ids
        and not e.dst.endswith(_FILE_ID_SUFFIX)
        and not _is_stand_in(e.dst, stand_in_tokens)
    )
    return bad
