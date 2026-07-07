# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical route accessor — one place to answer "is this symbol a route?"
and to extract its normalized ``{path, method, framework, protocol}``.

WI-tosul / target-D. Route-ness is surfaced two ways at the analyzer /
enrichment layer:

- the **ADR-0027 route marker** — ``meta['framework_role'] == 'route'`` with
  ``meta['route_path']`` / ``meta['http_method']`` on the symbol itself
  (analyzer-level; fires with no manifest, e.g. Go ``net/http``); and
- **framework-YAML concept enrichment** — a ``meta['concepts']`` entry with
  ``concept == 'route'`` carrying ``path`` / ``method`` / ``framework``
  (manifest-gated, e.g. Flask ``@app.route`` once ``flask`` is a declared dep).

Multiple consumers — ``cmd_routes``, the handler-slice/reverse-slice family,
supply-chain tier promotion, entrypoint detection, and the ``http`` / ``openapi``
linkers — each re-implemented the OR of these two mechanisms (the ``http`` and
``openapi`` linkers carried verbatim-duplicate ``_get_route_info_from_concept`` /
``_get_route_symbols`` pairs, each with a *concept-first* precedence that
disagreed with the marker-first accessor and silently dropped dual-carry /
marker-only routes). :func:`route_of` / :func:`is_route` consolidate them behind
one chokepoint; those linker sites now delegate here.

**Precedence is marker-first.** The ADR-0027 route marker is the producer-side
authoritative signal; the concept entry is the manifest-gated upstream
projection (decision-register 2026-07-05). **Normalization:** the ``'WS'``
sentinel that older producers smuggled into ``http_method`` is lifted out into
a dedicated ``protocol`` field (``'http'`` | ``'websocket'``); a caller that
needs the historical raw method reconstructs it from ``protocol``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .ir import Symbol


def _route_concept(meta: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return the symbol's ``concept == 'route'`` dict, or ``None``.

    Mirrors ``linkers/_concept_utils.get_concept`` but is inlined here so the
    top-level accessor carries no dependency on the linker package.
    """
    for c in meta.get("concepts", []) or []:
        if isinstance(c, dict) and c.get("concept") == "route":
            return c
    return None


def route_of(symbol: Symbol) -> Optional[dict[str, Any]]:
    """Return ``{path, method, framework, protocol}`` for a route symbol, else None.

    Marker-first for path/method: if the symbol carries the ADR-0027 route
    marker (``framework_role == 'route'``) its own ``route_path`` /
    ``http_method`` win; otherwise a ``concept == 'route'`` entry supplies path /
    method / framework. A symbol that is neither returns ``None``.

    **The framework is UNIONED, never dropped** (WI-tosul Phase-1b-alpha, BUG-1).
    A marker carries no framework of its own, so the marker branch falls through
    to the canonical ``route_framework`` key, then the legacy ``meta['framework']``
    (Starlette), then a *co-resident* ``concept == 'route'`` — which is how
    Rails / Phoenix / Sinatra / Laravel dual-carry markers (analyzer marker +
    the framework-YAML ``framework_role: '^route$'`` concept) still surface their
    framework. ``framework`` is ``None`` only when no source carries one.

    ``method`` is ``None`` when ``protocol == 'websocket'`` (the ``'WS'``
    sentinel is normalized out); a producer-persisted ``route_protocol`` key
    wins over that derivation (additive, forward-compatible with INV-tibap's
    LIVE/RPC transport fold). ``path`` may be ``None`` for a marker symbol that
    has no ``route_path`` (still a route by ``is_route``, just without a
    resolvable path — the caller decides whether to require one).
    """
    meta = symbol.meta or {}
    concept = _route_concept(meta)
    if meta.get("framework_role") == "route":
        path = meta.get("route_path")
        method = meta.get("http_method")
        framework: Any = (
            meta.get("route_framework")
            or meta.get("framework")
            or (concept.get("framework") if concept else None)
        )
    else:
        if concept is None:
            return None
        path = concept.get("path")
        method = concept.get("method")
        framework = concept.get("framework")

    raw_method = str(method) if method is not None else None
    protocol = str(
        meta.get("route_protocol") or ("websocket" if raw_method == "WS" else "http")
    )
    method_out = None if protocol == "websocket" else raw_method
    return {
        "path": str(path) if path is not None else None,
        "method": method_out,
        "framework": str(framework) if framework is not None else None,
        "protocol": protocol,
    }


def is_route(symbol: Symbol) -> bool:
    """Return True iff *symbol* is a route (marker OR ``concept == 'route'``)."""
    return route_of(symbol) is not None
