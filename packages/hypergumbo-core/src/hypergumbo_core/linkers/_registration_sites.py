# SPDX-License-Identifier: AGPL-3.0-or-later
"""Infrastructure linker helper: the function a registration site hands over.

The IPC and WebSocket linkers find a registration by its text
(``ipcMain.handle('ch', h)``, ``io.on('connection', h)``) and mint a node
for the site (``ipc:receive:ch``, ``ws:connection``), but the function the
call hands over was not linked to the node: the handler's only incoming
edge was the ``references`` edge the analyzer emits for a function passed
as an argument (``callback_argument_reference``), and no edge joined the
channel to the code that serves it (WI-pahom, WI-movur).

:func:`site_handlers` joins them through that edge. The handler of a site
is a function a ``callback_argument_reference`` from the site's FILE, at
the site's LINE, points at. A line can pass several functions
(``ipcMain.handle('ch', () => xs.map(f))`` passes the callback and, inside
it, ``f``), so two facts narrow it:

* a function carrying the site's concept (``ipc_handler``,
  ``websocket_handler``; the framework file's registration row stamped it
  on an argument of a call it names) is the handler;
* with no such function, a line that passes exactly one function names it,
  and a line that passes several names none.

Before either, a function passed from inside another candidate (a callback
the handler itself registers, written on the same line:
``io.on('connection', (s) => { s.on('ping', h); })``) is dropped: the
site's handler is the outermost.

A line holding two sites links none: which function serves which channel
is not known from a line. Site paths are made repo-relative the way the
orchestrator normalizes them (prefix strip, forward slashes), because the
linkers mint their nodes from absolute file paths while the analyzers'
symbols are already relative when linkers run.

:func:`registered_function` answers the same question for a registration
that names the function rather than passing it, in C or C++
(``rb_define_method(c, "run", run, 0)``, ``napi_create_function(env, "f",
NAPI_AUTO_LENGTH, impl, ...)``), by name: of the functions so named, the one
the REGISTERING file defines is the one the name reaches there (C scoping: a
``static`` function is visible only in its own file, and a second external
definition of the name would not link). Ruby C extensions define ``static
VALUE run`` or ``initialize`` in many files, so resolving the name tree-wide
sent every registration to one file's function (WI-supar).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterator

from ..ir import Edge, Symbol
from ..paths import normalize_path
from ._concept_utils import has_concept


def _relative(path: str, root_prefix: str) -> str:
    normed = normalize_path(path)
    return normed[len(root_prefix):] if normed.startswith(root_prefix) else normed


def site_handlers(
    sites: list[Symbol],
    symbols: list[Symbol],
    edges: list[Edge],
    concept: str,
    repo_root: Path,
) -> Iterator[tuple[Symbol, Symbol, Edge]]:
    """``(site, handler, reference edge)`` for each site whose handler is known."""
    root_prefix = normalize_path(repo_root).rstrip("/") + "/"
    by_id: dict[str, Symbol] = {s.id: s for s in symbols}
    passed: dict[tuple[str, int], dict[str, tuple[Symbol, Edge]]] = defaultdict(dict)
    for edge in edges:
        if edge.edge_type != "references" or edge.evidence_type != "callback_argument_reference":
            continue
        src, dst = by_id.get(edge.src), by_id.get(edge.dst)
        if src is not None and dst is not None and dst.kind in ("function", "method"):
            passed[(_relative(src.path, root_prefix), edge.line)].setdefault(dst.id, (dst, edge))
    lines = [
        (site, (_relative(site.path, root_prefix), site.span.start_line))
        for site in sites if site.span is not None
    ]
    per_line = Counter(key for _, key in lines)
    for site, key in lines:
        if per_line[key] != 1:
            continue
        candidates = list(passed.get(key, {}).values())
        # A function passed from INSIDE another candidate (a callback
        # registered in the handler's own body, on the same line) is not
        # the site's: keep the outermost.
        inner = {h.id for h, _ in candidates}
        candidates = [(h, e) for h, e in candidates if e.src not in inner]
        tagged = [(h, e) for h, e in candidates if has_concept(h, concept)]
        for handler, edge in tagged or (candidates if len(candidates) == 1 else []):
            yield site, handler, edge


def site_dispatch_edges(
    sites: list[Symbol],
    symbols: list[Symbol],
    edges: list[Edge],
    concept: str,
    repo_root: Path,
    channel_kind: str,
    origin: str,
    origin_run_id: str,
) -> list[Edge]:
    """A ``dispatches_to`` edge from each site to the function it hands over.

    The edge carries ``meta.dispatch_kind="listener"`` and the site's
    ``channel_kind`` (``ipc`` / ``websocket``), and names the reference edge
    it was read from in ``derived_from``.
    """
    return [
        Edge.create(
            src=site.id,
            dst=handler.id,
            edge_type="dispatches_to",
            line=reference.line,
            evidence_type="dispatch_pattern",
            origin=origin,
            origin_run_id=origin_run_id,
            meta={"dispatch_kind": "listener", "channel_kind": channel_kind},
            derived_from=[site.id, handler.id, reference.id],
        )
        for site, handler, reference in site_handlers(
            sites, symbols, edges, concept, repo_root,
        )
    ]


def registered_function(
    candidates: list[Symbol], site_path: str,
) -> tuple[Symbol, bool]:
    """The function a C/C++ registration in ``site_path`` names, and whether
    it is a guess.

    ``candidates`` are the in-tree functions carrying the registered name;
    ``site_path`` is the registering file, spelled as the symbols spell their
    ``path``. One candidate is the answer. Of several, the one ``site_path``
    defines is the answer: the name resolves inside its own translation unit.
    Otherwise the target is not known from the registration -- several other
    files define the name, none of them this one -- and the lowest id is
    returned with ``True``, for the caller to downgrade under INV-zuhub (the
    same pick the callers made before; two definitions in the registering
    file, overloads in C++, are also that case).
    """
    if len(candidates) == 1:
        return candidates[0], False
    own = [c for c in candidates if c.path == site_path]
    if len(own) == 1:
        return own[0], False
    return min(own or candidates, key=lambda s: s.id), True
