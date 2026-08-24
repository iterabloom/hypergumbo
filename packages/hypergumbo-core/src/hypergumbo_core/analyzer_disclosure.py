# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dated per-language declarations of whether an analyzer emits the external
instance-method call edge every downstream disclosure depends on.

WHY A DECLARATION AND NOT A MEASUREMENT AT RUNTIME. ``verify_claims`` reasons
over an edge SET. It can see that a language produced no edge of some shape,
but it cannot tell "this repository happens to contain no external method
calls" from "this analyzer never emits them" — and those are opposite facts
about a security verdict. The first earns a clean answer; the second means the
analysis never looked. Nothing in the edge set distinguishes them, so the fact
has to be declared by the only party that knows it: this project, about its own
analyzers, with a date and the measurement behind it.

WHY FAIL-CLOSED. :func:`emits_external_method_call_edges` returns ``False`` for
a language with no entry. A language that arrives with method-kind I/O sinks
and no declaration is therefore treated as BLIND until someone measures it and
says otherwise, which is the direction that cannot silently produce a false
clean verdict. :func:`test_every_language_with_method_sinks_is_declared` turns
that default into a hard gate so the omission is caught at test time rather
than becoming a quiet caveat in production.

THE SHAPE IS DELIBERATELY THE ONE ALREADY RULED FOR STDLIB COMPLETENESS: a
POSITIVE DATED DECLARATION, never a widened gate. ``module_completeness`` says
"this module's I/O surface was audited on this date"; these entries say "this
analyzer's external instance-method call emission was measured on this date".
Both are claims a person made and can be held to, and both fail closed when
absent.

WHY THIS IS NOT IN ``io_primitives/*.yaml``. Those catalogue what a LIBRARY
does. This records what an ANALYZER can see. Filing an analyzer capability in a
library catalogue would put one concept in the wrong home and invite the next
reader to add library facts here.

THE CONSEQUENCE OF A ``False`` ENTRY, so the cost is visible from the
declaration itself: a clean boundary or taint verdict on a repository
containing that language can no longer be a BARE ``confirmed``. It becomes
``confirmed_with_caveats`` (rc 3) carrying
:data:`~hypergumbo_core.verify_claims.CAVEAT_ANALYZER_METHOD_CALL_BLIND`, which
names the language and the size of what went unexamined. It is a QUALIFIED
OPINION rather than a disclaimer (ADR-0016 §4) on purpose: kotlin and
javascript do emit call edges, just not this shape, so "I examined everything I
could see, except this whole construct" is the true sentence — withholding to
``inconclusive`` would say the analysis formed no view at all, which is false
and less useful.

WHEN AN ENTRY FLIPS TO ``True`` (WI-nasuf builds the missing edges), the caveat
stops firing on its own and no invariant changes colour. That is the point of
declaring rather than blocking: under a CAPABILITY-phrased bar, teaching kotlin
to emit these edges would have read as a mass NEW violation, because those
sinks would suddenly become visible-but-untyped. LIVE.md rule 19.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DECLARATIONS",
    "MethodCallEdgeDeclaration",
    "emits_external_method_call_edges",
    "method_call_blind_languages",
]


@dataclass(frozen=True)
class MethodCallEdgeDeclaration:
    """One language's measured answer to "does this analyzer emit an external
    instance-method call edge?", with the date and the evidence.

    ``evidence`` is required rather than optional: an undated, unsourced
    ``emits=True`` is indistinguishable from a guess, and a guess in this
    direction produces a silent clean verdict.
    """

    language: str
    emits: bool
    measured: str
    evidence: str


#: Measured 2026-08-23 with one fixture per language calling a sink from that
#: language's OWN io_primitives catalogue on a receiver the analyzer cannot
#: type. Fixtures, probe and results:
#: ``~/hypergumbo_lab_notebook/disclosure_parity_08232026/``.
#:
#: THE FIXTURE SHAPE IS LOAD-BEARING and a first pass got it wrong: written
#: with a CAST receiver, scala and swift both read as non-emitting. A cast
#: takes a different, bare-call branch in both analyzers. These entries are
#: measured on the plain ``receiver.method()`` shape real code uses.
_MEASUREMENT = "disclosure_parity_08232026, plain receiver.method() fixture"

DECLARATIONS: dict[str, MethodCallEdgeDeclaration] = {
    lang: MethodCallEdgeDeclaration(lang, True, "2026-08-23", _MEASUREMENT)
    for lang in ("python", "go", "scala", "swift", "java", "objc", "rust")
}

DECLARATIONS.update({
    "kotlin": MethodCallEdgeDeclaration(
        "kotlin", False, "2026-08-23",
        "WI-nasuf: emits NO call edge for an external instance-method call in "
        "either the two-step or chained shape, while `File(p)` DOES emit for "
        "the constructor — so the analyzer reaches the expression and declines "
        "to emit for the method. 181 of kotlin's 186 catalogued primitives "
        "(97%) are method-kind and therefore unreachable through the shape "
        "real code uses.",
    ),
    "javascript": MethodCallEdgeDeclaration(
        "javascript", False, "2026-08-23",
        "WI-nasuf: emits NO call edge for `new net.Socket().write(d)` in "
        "either shape. Positive control in the SAME fixture: the "
        "MODULE-qualified `fs.createWriteStream(p)` emits and matches, so the "
        "null is the method shape and not a broken harness. 51 of "
        "javascript's 187 catalogued primitives (27%) are method-kind.",
    ),
})


def emits_external_method_call_edges(language: str) -> bool:
    """Whether ``language``'s analyzer emits an external instance-method call
    edge — FAIL-CLOSED: an undeclared language answers ``False``.

    A missing entry is not "probably fine". It is "nobody measured", and the
    only safe reading of that for a gate whose other branch produces a clean
    security verdict is that the analysis may not have looked.
    """
    declaration = DECLARATIONS.get(language)
    return declaration is not None and declaration.emits


def method_call_blind_languages(
    languages: set[str],
    languages_with_method_sinks: set[str],
) -> list[str]:
    """Languages PRESENT in this analysis whose method-call blindness matters.

    Both conditions are required and each excludes a different false positive.
    Without the first, a declaration would qualify verdicts for a language the
    repository does not contain. Without the second, a language whose catalogue
    declares no method-kind sink at all — ``c``, ``cpp``, ``bash``, ``elixir``,
    ``erlang``, ``haskell`` — would raise a caveat about an inability that
    cannot cost it anything, and a caveat that is always there is discounted by
    its reader.
    """
    return sorted(
        lang for lang in languages
        if lang in languages_with_method_sinks
        and not emits_external_method_call_edges(lang)
    )
