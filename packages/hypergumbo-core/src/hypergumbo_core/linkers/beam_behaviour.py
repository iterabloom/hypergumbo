# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bridge linker: an Elixir ``@behaviour :mod`` reaches the in-repo Erlang behaviour.

Why this exists (WI-hidit)
--------------------------
Elixir adopts an ERLANG behaviour by naming the module as an atom:
``@behaviour :ejabberd_auth``, ``@behaviour :gen_cycle``. The Elixir analyzer
reads the directive and emits ``implements`` to the unresolved target
``elixir:external:0-0:ejabberd_auth:unresolved`` -- it cannot do better, because
it never sees Erlang symbols. When the behaviour IS in the repository (ejabberd
ships ``src/ejabberd_auth.erl`` and ``src/gen_mod.erl`` and implements both in
Elixir under ``lib/``), the relationship is fully knowable once both analyzers
have run, and only a pass that sees both languages can state it.

What it emits
-------------
For each such stub whose atom names an Erlang module in the run:

* ``implements`` from the Elixir module to EVERY Erlang module of that name
  (module names are global on the BEAM, so two same-named modules are two
  candidates the build chooses between, as in the Erlang analyzer);
* ``dispatches_to`` from each of that module's ``-callback`` symbols
  (``name/arity``, ``abstract``) to the Elixir module's public functions named
  ``<Module>.<name>`` whose declared parameters admit the callback's arity
  (``overload_arity.narrow_by_arity``: a function of unknown arity is kept).
  Elixir emits one function per name/arity but names it without the arity
  (``M.start`` for both ``start/0`` and ``start/1``), so the arity check is
  what keeps ``start/1`` from claiming a ``start/0`` helper.

Both edges come from ``hypergumbo_core.behaviours``, the shape the two
analyzers emit for a same-language behaviour, and carry the consumed stub in
``derived_from``. The stub is left in place: this pass holds no supersession
grant (ADR-0057 §14.1), so the two coexist and the resolved edge is the one a
consumer following ``implements`` to an in-repo node finds.

What it does not do
-------------------
* It reads only ATOM-spelled targets: a name whose first character is
  lowercase. An Elixir alias is capitalised and names ``Elixir.X``, which is
  never an Erlang module, so ``@behaviour Foo`` is not bridged to an Erlang
  ``'Foo'`` -- those are different atoms on the BEAM.
* The reverse direction (an Erlang ``-behaviour('Elixir.Foo')``) is not
  bridged; none was found on the local corpus.
"""
from __future__ import annotations

import time

from ..behaviours import behaviour_implements_edge, callback_dispatch_edge
from ..ir import PASS_VERSION, AnalysisRun, Edge, Symbol, make_pass_id
from ..overload_arity import narrow_by_arity
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)

PASS_ID = make_pass_id("beam-behaviour-linker")

_STUB_PREFIX = "elixir:external:0-0:"
_STUB_SUFFIX = ":unresolved"


def _atom_behaviour(edge: Edge) -> str | None:
    """The Erlang module an Elixir ``implements`` stub names, or None."""
    if edge.edge_type != "implements":
        return None
    dst = edge.dst
    if not (dst.startswith(_STUB_PREFIX) and dst.endswith(_STUB_SUFFIX)):
        return None
    name = dst[len(_STUB_PREFIX):-len(_STUB_SUFFIX)]
    return name if name[:1].islower() else None


def _erlang_indexes(
    symbols: list[Symbol],
) -> tuple[dict[str, list[Symbol]], dict[str, list[Symbol]]]:
    """``(modules by name, callbacks by module name)`` for the Erlang symbols.

    A callback belongs to the module declared in its file -- the first
    ``-module`` there, as the Erlang analyzer attributes it.
    """
    modules: dict[str, list[Symbol]] = {}
    module_of_path: dict[str, str] = {}
    for sym in symbols:
        if sym.language == "erlang" and sym.kind == "module":
            modules.setdefault(sym.name, []).append(sym)
            module_of_path.setdefault(sym.path, sym.name)
    callbacks: dict[str, list[Symbol]] = {}
    for sym in symbols:
        if sym.language != "erlang" or "abstract" not in sym.modifiers:
            continue
        owner = module_of_path.get(sym.path)
        if owner is not None:
            callbacks.setdefault(owner, []).append(sym)
    return modules, callbacks


@register_linker(
    "beam-behaviour-linker",
    priority=40,  # Beside otp-linker: both read finished BEAM analyzer output.
    description=(
        "Elixir @behaviour :erlang_mod -> the in-repo Erlang behaviour "
        "(implements + callback dispatch)"
    ),
    activation=LinkerActivation(language_pairs=[("elixir", "erlang")]),
    # CNF: the Elixir directive AND the Erlang behaviour it names.
    depends_on=[["elixir"], ["erlang"]],
)
def beam_behaviour_linker(ctx: LinkerContext) -> LinkerResult:
    """See module docstring for the algorithm."""
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    modules, callbacks = _erlang_indexes(ctx.symbols)
    by_id: dict[str, Symbol] = {s.id: s for s in ctx.symbols}
    elixir_functions: dict[str, list[Symbol]] = {}
    for sym in ctx.symbols:
        if (sym.language == "elixir" and sym.kind == "function"
                and "private" not in sym.modifiers
                and "abstract" not in sym.modifiers):
            elixir_functions.setdefault(sym.name, []).append(sym)

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for stub in ctx.edges:
        behaviour = _atom_behaviour(stub)
        implementer = by_id.get(stub.src)
        if behaviour is None or implementer is None:
            continue
        for target in modules.get(behaviour, []):
            if (implementer.id, target.id) in seen:
                continue
            seen.add((implementer.id, target.id))
            edges.append(behaviour_implements_edge(
                src=implementer.id, lang="elixir", behaviour=behaviour,
                target_id=target.id, line=stub.line, origin=PASS_ID,
                run_id=run.execution_id, derived_from=[stub.id],
            ))
        for callback in callbacks.get(behaviour, []):
            meta = callback.meta or {}
            base = meta.get("base_name")
            arity = meta.get("arity")
            candidates = elixir_functions.get(f"{implementer.name}.{base}", [])
            for impl in narrow_by_arity(
                candidates, arity if isinstance(arity, int) else None,
            ):
                if (callback.id, impl.id) in seen:
                    continue
                seen.add((callback.id, impl.id))
                edges.append(callback_dispatch_edge(
                    callback_id=callback.id, impl_id=impl.id, line=stub.line,
                    origin=PASS_ID, run_id=run.execution_id,
                    derived_from=[stub.id],
                ))

    run.duration_ms = int((time.time() - start_time) * 1000)
    return LinkerResult(edges=edges, run=run)
