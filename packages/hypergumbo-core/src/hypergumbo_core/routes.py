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


# INV-tibap: the transport-protocol sentinels older producers smuggle into the
# ``http_method`` verb field (a mechanism-vs-category leak). ``route_of`` lifts
# them out into the ``protocol`` field; ``method_token`` rebuilds the sentinel
# for method-bucketing / slice-filename consumers that expect the legacy token.
_TRANSPORT_SENTINELS: dict[str, str] = {"WS": "websocket", "LIVE": "liveview", "RPC": "grpc"}
_PROTOCOL_METHOD_TOKENS: dict[str, str] = {v: k for k, v in _TRANSPORT_SENTINELS.items()}


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

    ``method`` is ``None`` for a transport protocol (``websocket`` / ``liveview``
    / ``grpc``): the ``'WS'`` / ``'LIVE'`` / ``'RPC'`` sentinels older producers
    smuggled into ``http_method`` are normalized out into ``protocol`` (INV-tibap).
    A producer-persisted ``route_protocol`` key wins over that derivation;
    :func:`method_token` rebuilds the sentinel for consumers that bucket by it.
    ``path`` may be ``None`` for a marker symbol that has no ``route_path`` (still
    a route by ``is_route``, just without a resolvable path — the caller decides).
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
        meta.get("route_protocol") or _TRANSPORT_SENTINELS.get(raw_method or "", "http")
    )
    method_out = None if protocol in _PROTOCOL_METHOD_TOKENS else raw_method
    return {
        "path": str(path) if path is not None else None,
        "method": method_out,
        "framework": str(framework) if framework is not None else None,
        "protocol": protocol,
    }


def is_route(symbol: Symbol) -> bool:
    """Return True iff *symbol* is a route (marker OR ``concept == 'route'``)."""
    return route_of(symbol) is not None


def method_token(route_info: dict[str, Any]) -> Optional[str]:
    """Rebuild the legacy method/transport token from a :func:`route_of` result.

    For a transport protocol (``websocket`` / ``liveview`` / ``grpc``) returns the
    sentinel (``WS`` / ``LIVE`` / ``RPC``) that method-bucketing and slice-filename
    consumers expect; otherwise returns the plain HTTP method. INV-tibap: the one
    place that rebuilds the token, so the transport round-trips through
    ``protocol`` instead of living in the ``http_method`` field.
    """
    return _PROTOCOL_METHOD_TOKENS.get(route_info["protocol"], route_info["method"])


def protocol_method_token(route_protocol: Optional[str]) -> Optional[str]:
    """Map a ``route_protocol`` value back to its legacy method token (``WS`` /
    ``LIVE`` / ``RPC``), or ``None`` for a plain HTTP protocol.

    The dict-level counterpart to :func:`method_token` for consumers (``cmd_routes``)
    that read raw behavior-map node dicts rather than :func:`route_of` results, so
    a transport route still renders / sorts / dedups under its legacy token even
    though the transport now lives in ``route_protocol`` not ``http_method``. INV-tibap.
    """
    return _PROTOCOL_METHOD_TOKENS.get(str(route_protocol or ""))


def transport_meta(method: Optional[str]) -> dict[str, Optional[str]]:
    """Return the ``http_method`` / ``route_protocol`` meta fragment for a route
    marker, splitting a transport sentinel (``WS`` / ``LIVE`` / ``RPC``) out of the
    verb field.

    A transport sentinel becomes ``{'http_method': None, 'route_protocol': <p>}``
    so producers stop smuggling transport into the verb field; a real HTTP verb
    becomes ``{'http_method': <verb>}``. Producers unpack it into the route
    marker's meta (``meta={..., **transport_meta(m), ...}``) and keep the raw
    sentinel locally for name / stable_id (identity is unchanged). INV-tibap.
    """
    proto = _TRANSPORT_SENTINELS.get(method or "")
    return {"http_method": None, "route_protocol": proto} if proto else {"http_method": method}
