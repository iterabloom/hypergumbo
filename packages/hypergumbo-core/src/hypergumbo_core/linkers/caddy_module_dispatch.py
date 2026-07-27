# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Caddy module-registry dispatch (RegisterModule → LoadModule).

How It Works
------------
Caddy's plugin system registers modules into a global registry at ``init``
time via ``caddy.RegisterModule(SomeModule{})`` and later instantiates and
drives them by string ID through reflection: ``LoadModuleByID`` looks the ID
up in the registry, calls the registered ``New()`` constructor, and then
invokes lifecycle hooks (``Provision`` / ``Validate`` / ...) and — for HTTP
handlers — ``ServeHTTP``. Because the register→load coupling flows through a
runtime string ID resolved from JSON config, the static call graph never
connects a load site to a concrete module, so every module's handler methods
have zero incoming edges and look like dead code (the forward slice from
``main()`` misses all the HTTP handlers — WI-zizig, found on zoxide's sibling
Caddy in the DEEP cohort).

This linker recovers the structural half of that dispatch without modeling
Caddy's runtime. It identifies a Caddy module as a Go ``struct`` that owns a
``CaddyModule() caddy.ModuleInfo`` method (the ``caddy.Module`` interface
marker — the one contract every registered module satisfies), and emits a
``dispatches_to`` edge from that **marker method** to each of the module's
framework-called lifecycle/handler methods in :data:`CADDY_DISPATCHED_METHODS`.
Downstream slice/dead-code analysis then walks those edges (``dispatches_to``
is a reachability edge type) and reaches the handlers.

The marker method is the source (not the struct) on purpose: it is the one
method on the module the static graph already reaches —
``init → RegisterModule`` calls ``m.CaddyModule()``, which resolves to
``Module.CaddyModule`` and then to the concrete ``T.CaddyModule`` via the
implements/interface-dispatch linker — so anchoring here puts the handlers on
a forward slice from the registration site. The struct is only reached by
``contains`` edges, which forward-slice does not traverse, and Caddy's struct
instantiation is not linked (see residuals).

Scope
-----
Go-only Framework-subcategory linker. The register→load string-ID coupling is
resolved from JSON config at runtime and is **not** statically recoverable, so
this pass deliberately does not attempt to match a specific ``LoadModule`` call
site to a specific module — it recovers the marker-method → own-handler-method
edges, which is what lifts the handlers out of the dead-code candidate pool
(the same tractable structural strategy the Kafka Streams and decorator
dispatch linkers use). Two documented residuals: module-namespace-specific
handler methods beyond the universal interface hooks (e.g. an encoder's
``NewEncoder``, a matcher's ``Match``) are not yet in the dispatched-method
set; and full ``main()``-reachability additionally depends on the
``init → RegisterModule → Module.CaddyModule → T.CaddyModule`` chain resolving
(present when the implements-linker connects the interface method to the impl).
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..ir import PASS_VERSION, AnalysisRun, Edge, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker

if TYPE_CHECKING:
    from ..ir import Symbol

PASS_ID = make_pass_id("caddy-module-dispatch-linker")

# The ``caddy.Module`` interface marker method every registered module defines
# (``func (T) CaddyModule() caddy.ModuleInfo``). A struct owning a method with
# this short name is a Caddy module, and this marker method is the linker's
# EDGE SOURCE: it is the one method on the module that the static call graph
# already reaches (``init → RegisterModule → Module.CaddyModule`` resolves to
# the concrete ``T.CaddyModule`` via the implements/interface-dispatch linker),
# so anchoring the dispatch edges here puts the handler methods on a forward
# slice from the registration site. The module STRUCT is a poor anchor: it is
# only reached by ``contains`` edges, which forward-slice does not traverse
# (reachability edge types are calls / dispatches_to / wraps), and Caddy's
# struct instantiation — the ``RegisterModule(Gzip{})`` literal argument — is
# not currently linked to the struct.
CADDY_MODULE_MARKER = "CaddyModule"

# Framework-called lifecycle / handler methods that Caddy invokes reflectively
# after ``LoadModuleByID`` constructs a module (so they have zero static
# incoming edges). Sources: caddy.Provisioner (Provision), caddy.Validator
# (Validate), caddy.CleanerUpper (Cleanup), caddy.App (Start/Stop),
# caddyhttp.Handler / net/http.Handler (ServeHTTP). ``CaddyModule`` itself is
# the edge SOURCE (the reachable marker), not a dispatch target.
CADDY_DISPATCHED_METHODS: frozenset[str] = frozenset({
    "Provision",
    "Validate",
    "Cleanup",
    "Start",
    "Stop",
    "ServeHTTP",
})


def _short_name(qualified_name: str) -> str:
    """Return the method's short name, stripping any ``Receiver.`` prefix."""
    return qualified_name.rsplit(".", 1)[-1]


def _build_go_method_index(
    symbols: list["Symbol"],
) -> dict[tuple[str, str], list["Symbol"]]:
    """Group Go method symbols by ``(path, receiver_type)``.

    The Go analyzer emits method ``Symbol.name`` as ``"Receiver.Method"`` with
    the pointer/value receiver normalized to the bare type name, so all methods
    of a struct — regardless of value vs pointer receiver — land under the same
    ``(path, receiver_type)`` key. Non-Go and receiverless (top-level function)
    symbols are skipped.
    """
    index: dict[tuple[str, str], list["Symbol"]] = {}
    for sym in symbols:
        if sym.kind != "method" or sym.language != "go":
            continue
        if "." not in sym.name:
            continue
        receiver, _method = sym.name.rsplit(".", 1)
        index.setdefault((sym.path or "", receiver), []).append(sym)
    return index


@register_linker(
    "caddy-module-dispatch-linker",
    priority=22,
    description=(
        "Emit dispatches_to edges from a Caddy module struct (CaddyModule "
        "marker) to its framework-called handler methods (WI-zizig)"
    ),
    # CNF: Caddy modules are Go.
    depends_on=[["go"]],
)
def link_caddy_module_dispatch(ctx: LinkerContext) -> LinkerResult:
    """Recover Caddy module-registry dispatch edges.

    See the module docstring for detection criteria and edge semantics.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    method_index = _build_go_method_index(ctx.symbols)
    existing_keys: set[tuple[str, str, str]] = {
        (e.src, e.dst, e.edge_type)
        for e in ctx.edges
        if e.edge_type == "dispatches_to"
    }

    edges: list[Edge] = []
    for sym in ctx.symbols:
        if sym.kind != "struct" or sym.language != "go":
            continue
        methods = method_index.get((sym.path or "", sym.name), [])
        # A Caddy module is a struct that owns a ``CaddyModule`` marker method;
        # that marker method is the (reachable) edge source.
        marker = next(
            (m for m in methods if _short_name(m.name) == CADDY_MODULE_MARKER),
            None,
        )
        if marker is None:
            continue
        for method in methods:
            if _short_name(method.name) not in CADDY_DISPATCHED_METHODS:
                continue
            edge_key = (marker.id, method.id, "dispatches_to")
            if edge_key in existing_keys:
                continue
            existing_keys.add(edge_key)
            edges.append(
                Edge.create(
                    src=marker.id,
                    dst=method.id,
                    edge_type="dispatches_to",
                    line=marker.span.start_line if marker.span else 0,
                    confidence=0.90,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_call_direct",
                    meta={"framework_dispatch": "caddy_module"},
                    derived_from=[marker.id, method.id],
                ),
            )

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(symbols=[], edges=edges, run=run)
