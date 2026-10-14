# SPDX-License-Identifier: AGPL-3.0-or-later
"""The declared vocabulary of ``Edge.meta["call_construct"]``, and what each value reaches.

THE AXIOM. ``call_construct`` names the source-language construct that
produced a call-family edge (``calls`` / ``instantiates``). It is not "the
syntax of a call": two declared values are constructs that are not calls at
all. ``constructor`` produces ``calls`` in ruby and ``instantiates`` in
dart/csharp for the same source construct (INV-kahig), and ``assignment`` is a
handler registration by property assignment (``ws.onmessage = h``, WI-dosuh)
that the catalogue matches as a call into ``WebSocket.onmessage``. The key's
registry entry in :mod:`hypergumbo_core.axis_meta_keys` states the same axiom.

WHAT THE KEY MUST NOT CARRY. Each value names a construct observed in source.
It does not name the inference pathway (that is ``evidence_type``: erlang's
``?LOG_INFO`` expansion carries ``evidence_type="macro_expansion"`` and no
construct, because tree-sitter expanded nothing at the site), and it does not
name the detector that found the call (rust's macro-token-tree scan used to
stamp ``macro_body``, which said where the call was found rather than what it
is). It also does not name resolution status or mechanism: four such values
were ejected earlier (INV-tadup) and are pinned out of this table by
``test_axis_meta_keys.py``.

WHY AN ALLOWLIST. The guard that preceded this table was a DENYLIST of the
ejected values. A denylist cannot stop a value it has never seen, and
``assignment`` shipped through every gate in the repository with no review of
whether it belonged on this axis (docs/surveys/call-construct-value-family-audit.md).
A new value now has to be added HERE, with a statement of what it reaches,
before a producer can emit it: ``test_axis_meta_keys.py`` fails on a literal
emit of an undeclared value, and :func:`require_declared` raises for a value
that flows through ``make_unresolved_edge``.

WHY EACH VALUE DECLARES A ROW KIND. ``verify_claims.method_starved_modules``
asks whether a call edge into a catalogued module carries a construct the
catalogue can match. It used to answer by testing the construct string for
membership in the module's set of ``IoPrimitive.kind`` values. Those two
vocabularies are different axes (the io-primitive-kind axis is ADR-0059's
``function`` / ``method`` / ``attribute``), and the test worked only where two
tokens coincide. On a JS fixture that registers ``ws.onmessage`` and calls
nothing else on ``ws``, the classifier matched the edge (``net_recv``) while
the gate reported ``WebSocket`` structurally invisible, because ``assignment``
is not a kind (WI-zohuk's measurement, WI-bobuf's contradiction). The
crosswalk below is what the gate reads instead: :func:`reached_row_kind` maps
a construct to the ADR-0059 kind of the row it reaches, or ``None`` when the
construct does not determine one.

THE CROSSWALK, value by value, against ADR-0059's axiom (``method`` iff called
on an INSTANCE of the row's module; ``function`` iff called on the module
itself or with no owner):

* ``method`` -> method: a call on a receiver expression.
* ``function`` -> function: a call with no receiver, or on a named owner
  (java's ``Files.readAllBytes(p)``, WI-fuvaj).
* ``assignment`` -> method: the JS emitter stamps it only when the receiver is
  typed to a catalogue module AND the property is a METHOD-kind row of that
  module (``js_ts._derive_js_assignable_rows``), so the edge always reaches a
  method row through an instance.
* ``protocol`` -> method: python's implicit dunder (``for x in qs`` runs
  ``qs.__iter__()``) on a receiver typed to ``django.db.models``, whose dunder
  rows are method-kind.
* ``application``, ``pipe``, ``remote``, ``local``, ``monadic_action`` ->
  function: haskell/ocaml juxtaposition, elixir ``|>``, erlang ``Mod:f()`` and
  ``f()``, haskell's bare action in a ``do`` block. Each calls a function by
  name, on a module or with no owner; none of these languages has an
  instance-receiver call form here. NO VERDICT CAN MOVE THROUGH THIS today:
  elixir, erlang, haskell and ocaml catalogue no method-kind module, so the
  gate skips them before it reads a construct.
* ``constructor`` -> None. A construction reaches the TYPE, not a row of it.
  Whether a row names the constructor is a question about the NAME, which the
  gate's second route answers (``Command::new`` is a function-kind row). Mapping
  it to ``function`` would let Kotlin's ``File(path)`` satisfy ``java.io.File``,
  a module that also declares function rows, and that constructor-but-no-
  method-call shape is exactly the blind-language signal the gate exists for.

A value missing from the table maps to ``None`` too: an undeclared construct is
no evidence the catalogue could match, so it cannot satisfy the gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Optional

from .io_primitive_kinds import KIND_FUNCTION, KIND_METHOD


@dataclass(frozen=True)
class CallConstructValue:
    """One declared ``call_construct`` value.

    ``row_kind`` is the io-primitive kind (ADR-0059) of the catalogue row an
    edge with this construct reaches, or ``None`` when the construct does not
    determine one. ``languages`` names the analyzers that emit it, for a reader
    deciding whether a new value duplicates an old one; it is documentation,
    not a gate.
    """

    name: str  # axis: identity
    row_kind: Optional[str]  # axis: bounded-enum
    languages: tuple[str, ...]
    description: str  # axis: free-text — a reader's gloss; no consumer branches on it


CALL_CONSTRUCT_VALUES: Final[tuple[CallConstructValue, ...]] = (
    CallConstructValue(
        "method", KIND_METHOD, ("many",),
        "A call on a receiver expression: `r.f(x)`.",
    ),
    CallConstructValue(
        "function", KIND_FUNCTION, ("many",),
        "A call with no receiver, or on a named owner: `f(x)`, `Mod.f(x)`, "
        "a java static `Files.readAllBytes(p)`.",
    ),
    CallConstructValue(
        "constructor", None, ("dart", "csharp", "ruby", "core"),
        "Object creation. Emitted on `instantiates` (dart, csharp) and on "
        "`calls` (ruby, and core's construction edges to an initializer) for "
        "the same source construct (INV-kahig).",
    ),
    CallConstructValue(
        "assignment", KIND_METHOD, ("javascript", "typescript"),
        "A handler registration by property assignment on a typed receiver: "
        "`ws.onmessage = h` (WI-dosuh). Not a call; it produces a call-family "
        "edge because the catalogue matches the registered row.",
    ),
    CallConstructValue(
        "protocol", KIND_METHOD, ("python",),
        "A dunder the language invokes on behalf of syntax: `for x in qs` "
        "runs `qs.__iter__()` (WI-fasap).",
    ),
    CallConstructValue(
        "application", KIND_FUNCTION, ("haskell", "ocaml"),
        "Function application by juxtaposition: `f x`.",
    ),
    CallConstructValue(
        "pipe", KIND_FUNCTION, ("elixir",),
        "A pipeline stage: `x |> f()`; the pipeline supplies the first "
        "argument.",
    ),
    CallConstructValue(
        "remote", KIND_FUNCTION, ("erlang",),
        "A module-qualified call: `Mod:f(x)`.",
    ),
    CallConstructValue(
        "local", KIND_FUNCTION, ("erlang",),
        "An unqualified call: `f(x)`.",
    ),
    CallConstructValue(
        "monadic_action", KIND_FUNCTION, ("haskell",),
        "A bare identifier executed in a `do` block, with no application "
        "syntax at all (INV-fofoj).",
    ),
)

_BY_NAME: Final[dict[str, CallConstructValue]] = {
    v.name: v for v in CALL_CONSTRUCT_VALUES
}


def all_call_construct_values() -> frozenset[str]:
    """Every value a producer may stamp on ``call_construct``."""
    return frozenset(_BY_NAME)


def reached_row_kind(construct: Optional[str]) -> Optional[str]:
    """The ADR-0059 kind of the row an edge with ``construct`` reaches.

    ``None`` for an absent construct, for ``constructor``, and for any value
    this table does not declare: none of those is evidence of which kind of
    row the call could match.
    """
    if construct is None:
        return None
    spec = _BY_NAME.get(construct)
    return spec.row_kind if spec is not None else None


def require_declared(construct: str) -> str:
    """Return ``construct`` if it is declared; raise ``ValueError`` if not.

    Called by ``make_unresolved_edge``, the one helper every analyzer routes an
    unresolved call through, so a value computed at run time (java's
    ``_java_call_construct``, cpp's member-call test) is checked where the
    static scan in ``test_axis_meta_keys.py`` cannot see it.
    """
    if construct not in _BY_NAME:
        raise ValueError(
            f"call_construct={construct!r} is not declared in "
            "hypergumbo_core.call_constructs.CALL_CONSTRUCT_VALUES; declare it "
            "there, with the row kind it reaches, before emitting it"
        )
    return construct
