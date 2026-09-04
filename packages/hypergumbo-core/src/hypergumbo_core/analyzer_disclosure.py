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
    "SUPPRESSED_METHOD_NAMES",
    "MethodCallEdgeDeclaration",
    "emits_external_method_call_edges",
    "method_call_blind_languages",
    "suppressed_catalogued_sinks",
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
        "javascript", True, "2026-09-03",
        "WI-nasuf / INV-misup, closed: an instance-method call on an untyped "
        "receiver emits the `external` placeholder with `call_construct: "
        "method`, and a `new`-constructed receiver (`ws = new WebSocket(u); "
        "ws.send(x)`, `new net.Socket().write(d)`) carries the constructor's "
        "catalogue module. Measured on the same fixture that recorded the "
        "2026-08-23 blindness, plus mobx / plausible / workadventure "
        "io-boundaries and per-site churn.",
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


#: Method names an analyzer DELIBERATELY declines to model, per language.
#:
#: THIS IS THE ANALYZER'S OWN DENYLIST, MOVED HERE RATHER THAN COPIED. rust.py
#: imports ``SUPPRESSED_METHOD_NAMES["rust"]`` as its
#: ``_RUST_GENERIC_TRAIT_METHODS``; there is one object, not two. A restated
#: copy would be a second home for one fact and the copy is what goes stale
#: (LIVE.md rule 7) — and here the two readers want OPPOSITE things from it,
#: which is exactly the condition under which two homes diverge unnoticed: the
#: analyzer asks "may I resolve this name?" and ``verify_claims`` asks "what did
#: I therefore never look at?".
#:
#: WHY THE DENYLIST IS RIGHT AND STAYS. Without receiver types, resolving
#: ``x.load()`` by name binds it to an arbitrary concrete ``load`` — WI-bakak
#: measured 22 of 29 false callers on ``JoltDevice::load`` from
#: ``AtomicU64.load(Ordering::Relaxed)``. Name-only resolution with conservative
#: guards is the intended design, and receiver-type discrimination is a
#: fidelity question (WI-nanom / rust-analyzer), not a denylist question.
#:
#: WHY IT NONETHELESS COSTS A DISCLOSURE. Ten of these names are methods
#: ``io_primitives/rust.yaml`` declares as I/O SINKS, so a call to one of them
#: on an untypable receiver produced no edge and a clean boundary verdict had
#: nothing to disclose — INV-polad. The overlap is computed from the shipped
#: catalogue by :func:`suppressed_catalogued_sinks` rather than listed here, so
#: adding a catalogue row for a denylisted name extends the disclosure without
#: anyone remembering to.
#:
#: WHY NOT SIMPLY EMIT THE SUPPRESSED CALLS, measured before being rejected:
#: emitting them as ordinary unresolved-external edges took total edges +33% /
#: +67% and the external-edge population +115% / +207% on two real crates
#: (bellman, tiktoken-rs), because the same set contains ``clone`` / ``unwrap``
#: / ``map``. Paying that in centrality, dead-code, slice and supply-chain
#: output on every Rust repository to make ten names disclosable is the wrong
#: trade; declaring the gap costs nothing and says the same true thing.
SUPPRESSED_METHOD_NAMES: dict[str, frozenset[str]] = {
    "rust": frozenset({
    # core::convert
    "into", "from", "try_into", "try_from",
    # core::fmt / Display / ToString
    "fmt", "to_string",
    # core::default
    "default",
    # core::clone
    "clone", "clone_from",
    # core::cmp / core::hash
    "eq", "ne", "partial_cmp", "cmp", "hash",
    # core::ops
    "deref", "deref_mut", "drop",
    # core::iter — combinators are on Iterator, Option, Result
    "next", "into_iter", "map", "filter", "fold", "collect", "flat_map",
    "find", "any", "all", "for_each",
    # core::convert (ref)
    "as_ref", "as_mut",
    # std collection methods (ambiguous without receiver type)
    "len", "is_empty", "push", "pop", "get", "insert", "remove", "contains",
    "iter", "iter_mut", "extend",
    # Option / Result combinators
    "and_then", "or_else", "map_err", "unwrap_or", "unwrap_or_else",
    "ok", "err", "expect", "ok_or", "ok_or_else",
    # serde
    "serialize", "deserialize",
    # core::str / parsing
    "from_str", "parse", "unwrap",
    # std::sync::atomic — .load()/.store() on AtomicU64, AtomicU8, etc.
    # Without receiver type info, x.load() conflates AtomicU64.load()
    # with domain-specific load() methods (WI-bakak: 22 false positives).
    "load", "store", "fetch_add", "fetch_sub", "compare_exchange", "swap",
    # std::io — Read/Write trait methods
    "read", "write", "flush",
    # Constructor / builder — ubiquitous across types
    "new", "build",
    # Channel / async
    "send", "recv",
    # Command — .output() conflates with test utilities (ripgrep bakeoff)
    "output", "status", "spawn",
    # Logging — ubiquitous across log/tracing crates
    "warn", "error", "info", "debug", "trace",
}),
}


def suppressed_catalogued_sinks(language: str, catalog: object) -> set[str]:
    """Names this language's analyzer declines to model that its I/O catalogue
    declares as METHOD-kind sinks.

    METHOD-KIND ONLY, and the restriction is load-bearing rather than tidy: the
    denylist governs the instance-method call path, so an associated-function
    call of the same name is unaffected. ``std::process::Command.new`` is the
    worked example — ``new`` is on the denylist, yet a crate calling
    ``Command::new("sh").status()`` still returns ``violated`` with the launch
    site named, because that is a ``Type::method`` call on a different path. A
    first reading of this overlap claimed all four subprocess rows were
    unreachable; a live control refuted it.
    """
    names = {
        p.name for p in getattr(catalog, "primitives", ())
        if getattr(p, "kind", None) == "method"
    }
    return names & SUPPRESSED_METHOD_NAMES.get(language, frozenset())
