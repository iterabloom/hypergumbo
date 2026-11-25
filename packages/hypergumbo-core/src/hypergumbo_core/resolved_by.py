# SPDX-License-Identifier: AGPL-3.0-or-later
"""The declared vocabulary of ``Edge.meta["resolved_by"]``: what scoped the choice of a call's target.

THE AXIOM. ``resolved_by`` names the evidence that scoped the choice of a
call-family edge's in-repository target (``calls`` and ``instantiates``, the
scope ``call_construct`` declares: a constructor call names a type, and "which
type of that name" is the same question). It answers one thing: why this
declaration and not another of the same name. A resolved call-family edge
carries exactly one value; an unresolved one carries none, because nothing was
chosen.

WHY IT EXISTS. The question used to be answered in parts, in eight places
(``docs/audits/0030-call-target-resolution.md``): a receiver-flavoured
``evidence_type`` in some analyzers, ``meta["resolution_quality"]`` or
``meta["receiver"]`` in others, a grade folded into ``confidence``, a shared
resolver's ``match_type`` that never left the resolver. No one of them was
total, and the largest population answered with nothing: a call bound through
an import and a call bound to the only function of that name in the repository
were the same record. One key with one vocabulary makes "how many resolved
calls rest on a name alone" a count.

WHAT THE KEY MUST NOT CARRY.

* A grade. How good the match was is ``Edge.confidence`` (ADR-0039 ruling 6).
* The number of candidates. It is derivable from the nodes: ``same_file`` with
  two same-named declarations in the file is a pick by declaration order,
  ``name`` with two in the repository is a pick among them, and a census
  counts both without a value for either.
* Where the target lives. That is ``Edge.is_resolved`` (ADR-0037).
* The construct at the call site (``call_construct``) or the shape of the
  receiver expression (``receiver``).

THE RULE FOR CHOOSING A VALUE. Name what scoped the SEARCH, not a check made
on its result. A name looked up among every declaration of the repository and
then admitted because the match was unique is ``name``. One exception, in one
helper (:func:`hypergumbo_core.analyze.base.resolved_by_for_bare_call`): a
bare call whose candidate is a method of the caller's own enclosing type is
``own_type`` whatever lookup produced the candidate, because that is the
language's implicit-receiver rule. A call on an explicit receiver that is not
``self`` / ``this`` never gets that treatment: found by name on an untyped
receiver, it is ``name`` even when the method belongs to the caller's type.

A nearness heuristic with no language rule behind it (same directory, same
npm package, nearest line) scopes nothing: the value is ``name``.

WHY AN ALLOWLIST. ``call_construct`` is the precedent
(:mod:`hypergumbo_core.call_constructs`): a key whose values are declared
nowhere admits a new spelling of an old fact with no review. A value is added
HERE, with its statement, before a producer may pass it; ``Edge.create``
raises on any other.

NOTHING READS THIS KEY TO DECIDE ANYTHING YET. It is written and counted. The
readers that reconstruct "was the target identified" from other fields
(``receiver_blind_magnets``) are unchanged until the shared resolution pass.
"""

from __future__ import annotations

from typing import Final

#: The ``Edge.meta`` key. One constant, so a move to a dataclass field (an
#: open question of audit 0030) changes no producer.
RESOLVED_BY_KEY: Final = "resolved_by"

RECEIVER_TYPE: Final = "receiver_type"
OWN_TYPE: Final = "own_type"
QUALIFIED: Final = "qualified"
IMPORT: Final = "import"
LEXICAL_SCOPE: Final = "lexical_scope"
SAME_FILE: Final = "same_file"
SAME_MODULE: Final = "same_module"
NAME: Final = "name"
CLASS_HINT: Final = "class_hint"
KEY_MATCH: Final = "key_match"
SEMANTIC_BACKEND: Final = "semantic_backend"

#: value -> the target was chosen because ...
RESOLVED_BY_VALUES: Final[dict[str, str]] = {
    RECEIVER_TYPE: (
        "the receiver's type was established (an annotation, a constructor, a "
        "parameter, a field, a declared return type) and the method was "
        "looked up on that type or its ancestors"
    ),
    OWN_TYPE: (
        "the receiver is the enclosing object (self, this, implicit) and the "
        "method was looked up on the enclosing type or its ancestors"
    ),
    QUALIFIED: (
        "the call site names the owner type or module, matched to the "
        "declaration's owner by that name"
    ),
    IMPORT: (
        "an import in the caller's file binds the called name, or its "
        "qualifier, to the declaring file or module"
    ),
    LEXICAL_SCOPE: (
        "a scope-aware binder found the declaration the call site sees (a "
        "nested function, a local binding resolved by position)"
    ),
    SAME_FILE: (
        "the name was looked up in a table of the caller's own file's "
        "declarations, with no scope rule behind it"
    ),
    SAME_MODULE: (
        "the language makes the declarations of the caller's own package, "
        "namespace or module visible without an import, and the name was "
        "looked up among them"
    ),
    NAME: (
        "the name was looked up among the declarations of the whole "
        "repository: nothing scoped it"
    ),
    CLASS_HINT: (
        "the caller constructs or names a class elsewhere in its body and "
        "that class has a member of this name"
    ),
    KEY_MATCH: (
        "a linker matched a key both sides declare (a route, a topic, an "
        "exported symbol, a build target), not a name in the caller's language"
    ),
    SEMANTIC_BACKEND: (
        "a compiler-grade backend supplied the target"
    ),
}


def all_resolved_by_values() -> frozenset[str]:
    """Every value a producer may pass as ``resolved_by``."""
    return frozenset(RESOLVED_BY_VALUES)


def require_declared(value: str) -> str:
    """Return ``value`` if it is declared; raise ``ValueError`` if not."""
    if value not in RESOLVED_BY_VALUES:
        raise ValueError(
            f"resolved_by={value!r} is not declared in "
            "hypergumbo_core.resolved_by.RESOLVED_BY_VALUES; declare it there, "
            "with the evidence it names, before passing it"
        )
    return value
