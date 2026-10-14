# SPDX-License-Identifier: AGPL-3.0-or-later
"""A BEAM behaviour's shared shape: callback identity and the two edges it implies.

A behaviour is the BEAM's interface mechanism. A behaviour module declares
callbacks (Erlang ``-callback area() -> integer().``, Elixir
``@callback area() :: integer``) and an implementing module adopts it
(``-behaviour(shape).`` / ``@behaviour Shape``). Hypergumbo models that as
the type relationship every OO language gets (WI-vitas, WI-hidit):

* ``implements`` from the implementing module to the behaviour -- the
  in-repo behaviour module when there is one, else the unresolved target
  ``<lang>:external:0-0:<behaviour>:unresolved``, the ``external`` sentinel
  the inheritance linker uses for an out-of-tree base;
* ``dispatches_to`` from each callback (a symbol carrying the ``abstract``
  modifier) to the function implementing it, evidence ``behaviour_callback``
  -- the abstract-member -> implementation shape ``linkers/type_hierarchy``
  builds for an interface method.

A callback symbol carries ``CALLBACK_MODIFIER`` and a stable_id minted by
:func:`callback_stable_id` rather than left to the backstop floor. The floor
keys on ``(kind, language, path, name)``, which a callback shares with a
same-named function in its own module (a behaviour may define a default
``area`` beside its ``area`` callback), and a floor collision nulls BOTH
(ADR-0035 §1). The modifier fills the v6 formula's decorators slot, so the
two hash apart.

WHY A SHARED MODULE. Three producers emit these edges: the Erlang analyzer
(``-behaviour``), the Elixir analyzer (``@behaviour``, and ``defimpl``: an
Elixir protocol is a behaviour whose ``def`` heads are its callbacks,
WI-forof) and the BEAM behaviour bridge linker (an Elixir module whose ``@behaviour :name`` names an in-repo
ERLANG module, which neither analyzer can see). They must agree on edge type,
evidence, the unresolved-target spelling and the meta, or a consumer reading
one language's behaviours misreads the other's. HOW EACH PRODUCER FINDS the
behaviour and the implementation stays with it: Erlang matches a callback to
its implementation by ``name/arity`` exactly, Elixir and the bridge by name
within the implementing module plus arity bounds (``meta["arity"]`` against
the clause's ``meta["parameters"]``, ``overload_arity.narrow_by_arity``).
"""
from __future__ import annotations

from typing import Optional

from .ir import Edge
from .overload_arity import ARITY_KEY, declared_arity, declared_arity_stable_id

#: The modifier on a callback declaration -- the marking cpp and kotlin give
#: an abstract member. A symbol bearing it is a required function with no
#: body: never a caller, and never a call target -- except an Elixir protocol
#: function, which is the protocol's dispatch function (``Size.size(x)``).
CALLBACK_MODIFIER = "abstract"


def callback_stable_id(
    lang: str, path: str, name: str, arity: int, kind: str = "function",
) -> str:
    """The stable_id of callback ``name`` (``arity`` parameters) declared in ``path``.

    ``kind`` is the symbol kind the callback requires: ``function``, or
    ``macro`` for an Elixir ``@macrocallback``.

    The ADR-0035 v6 formula, anchored to the declaring file the way
    ``TreeSitterAnalyzer.compute_stable_id`` anchors a top-level symbol, with
    ``CALLBACK_MODIFIER`` in the decorators slot. It is minted through the
    shared ``overload_arity`` helper every producer without a parameter-list
    node uses: the callback knows its parameter count (``meta["arity"]``),
    not its parameters, so the flags slot is empty.
    """
    return declared_arity_stable_id(
        kind=kind, lang=lang, path=path, name=name, qualified_name=name,
        arity=declared_arity({ARITY_KEY: arity}), decorators=CALLBACK_MODIFIER,
    )


def behaviour_implements_edge(
    *,
    src: str,
    lang: str,
    behaviour: str,
    target_id: Optional[str],
    line: int,
    origin: str,
    run_id: str,
    derived_from: Optional[list[str]] = None,
) -> Edge:
    """``implements`` from module ``src`` to its behaviour.

    ``target_id`` is the in-repo behaviour module's id, or None for a
    behaviour defined outside the analyzed tree; the edge then points at
    ``<lang>:external:0-0:<behaviour>:unresolved`` and says it is unresolved.
    """
    if target_id is not None:
        return Edge.create(
            src=src,
            dst=target_id,
            edge_type="implements",
            line=line,
            evidence_type="ast_implements",
            origin=origin,
            origin_run_id=run_id,
            derived_from=derived_from,
        )
    return Edge.create(
        src=src,
        dst=f"{lang}:external:0-0:{behaviour}:unresolved",
        edge_type="implements",
        line=line,
        evidence_type="ast_implements",
        is_resolved=False,
        origin=origin,
        origin_run_id=run_id,
    )


def callback_dispatch_edge(
    *,
    callback_id: str,
    impl_id: str,
    line: int,
    origin: str,
    run_id: str,
    derived_from: Optional[list[str]] = None,
) -> Edge:
    """``dispatches_to`` from a behaviour callback to one implementation."""
    return Edge.create(
        src=callback_id,
        dst=impl_id,
        edge_type="dispatches_to",
        line=line,
        evidence_type="behaviour_callback",
        origin=origin,
        origin_run_id=run_id,
        derived_from=derived_from,
        meta={"mechanism": "callback"},
    )
