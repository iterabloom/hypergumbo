# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fahub receiver-blind method-magnet detector (language-agnostic).

**What it detects and why.** INV-fahub's invariant is that *a call whose
receiver/target type cannot be resolved MUST emit an ambiguous/external edge,
not a high-confidence calls edge to an arbitrary same-named internal
definition.* The failure mode ("the magnet") is an untyped-receiver method call
— ``x.method()`` where ``x``'s type is unknowable — that a per-language resolver
binds by short name to the one same-named internal ``method``, so dozens of
unrelated call sites collapse onto one arbitrary ``Owner.method`` and poison its
in-degree / centrality (``.len()`` → ``GlobSet::len``; ``writeln(x)`` →
``ManWriter.writeln``).

**Why this lives in one place.** The prior INV-fahub campaign gated each
analyzer's *bare-identifier* free-call path, but the real funnel is the
*method-selector* path, which resolves through a separate ungated route — and
nothing measured the finalized graph, so the gap survived per-analyzer unit
tests. This module is the single, language-agnostic definition of "a
receiver-blind magnet edge," shared by three consumers so they can never drift:
the ``spec_validator`` content-gated check (whole-graph CI gate), the
per-language fixture-matrix test (``run_analyzer`` output, pre-linker), and the
real-repro survey harness.

**How it distinguishes a magnet from a legitimate bind.** A legitimate
resolution *knows the target type* and stamps it — either a receiver-aware
``evidence_type`` (``ast_call_type_inferred`` / ``ast_call_ufcs`` /
``ast_call_inherited`` — the Site-1/Site-2 recoveries and typed binds), or a
``meta.receiver`` marker (``typed_var`` / ``typed_field`` / ``field_chain`` /
``external`` / ``stdlib`` for a typed/external receiver, or ``qualified`` for an
explicitly-scoped/static ``Type::method`` call whose type the call site named),
or ``meta.resolution_quality`` other than ``"ambiguous"``. So a magnet is
precisely a **resolved** ``calls`` edge to an
**internal ``method``** that carries only the bare ``ast_call`` /
``ast_call_direct`` pathway, **no** receiver marker, is **not a framework route
registration** (a handler bound by reference is a correct dispatch, not a
method call — see ``_is_route_dispatch``), is **cross-owner** (the caller's
class differs from the method's owner — a same-class implicit-``this`` call is
legitimate), and lands at or above ``min_confidence``. Everything the
resolver understood about the receiver is excluded by construction, so genuine
inherited-call recoveries (``ast_call_inherited``) and typed binds never trip.

The reader is shape-tolerant (attribute *or* dict key) so the same function runs
on live ``Symbol``/``Edge`` dataclasses (the validator, the fixture matrix) and
on deserialized ``survey.json`` node/edge dicts (the survey harness).
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional

from .paths import is_test_file

__all__ = [
    "demote_harmful_magnets",
    "find_harmful_magnets",
    "find_receiver_blind_magnets",
    "owner_of",
]

# evidence_type values that are the bare, receiver-blind resolution pathways.
# Everything else (ast_call_type_inferred, ast_call_ufcs, ast_call_inherited,
# interface_dispatch, …) encodes that the receiver WAS characterised, so it is a
# legitimate bind and is excluded simply by not being in this set.
_BARE_EVIDENCE = frozenset({"ast_call", "ast_call_direct"})

# meta.receiver values that mean the call's TARGET TYPE was identified, so the
# bind is NOT receiver-blind: a typed receiver (``typed_var`` / ``typed_field`` /
# ``field_chain``), an external/stdlib receiver, or an explicitly-QUALIFIED
# target (``Type::method`` / ``Type.Method`` — the scoped/static call whose type
# the call site named, which resolves to a method-kind associated-function/static
# symbol and would otherwise be a false positive). The receiver-blind values
# ``'bare'`` (implicit-``this``/``self``) and ``'generic'`` (receiver present but
# unclassified) are deliberately ABSENT — a ``'bare'`` cross-class bind is exactly
# the magnet, and the legitimate same-class implicit-``this`` case is excluded by
# the owner check in ``find_receiver_blind_magnets`` instead.
_RECEIVER_MARKERS = frozenset(
    {
        "typed_var", "typed_field", "field_chain",
        "external", "constant_external", "stdlib",
        "qualified",
    }
)

_OWNER_SEPARATORS = ("::", "#", ".")


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dataclass-like object or a dict, whichever it is."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _set(obj: Any, key: str, value: Any) -> None:
    """Write ``key`` on a dataclass-like object or a dict, whichever it is —
    the write-side twin of ``_get``, so ``demote_harmful_magnets`` mutates live
    ``Edge`` dataclasses (the finalize call) and deserialized survey-JSON dicts
    (the real-repro survey harness) with the same code."""
    if isinstance(obj, dict):
        obj[key] = value
    else:
        setattr(obj, key, value)


def _strip_generics(name: str) -> str:
    """Remove balanced ``<...>`` generic/template-parameter groups from a name.

    A ``this``/``self`` call inside a generic class surfaces as
    ``AP4_Array<T>::method`` on the caller but ``AP4_Array::method`` on the
    callee — the SAME class, differing only by the instantiation notation. Left
    unnormalized, the owner comparison reads them as cross-class and the call is
    a false-positive magnet (bento4 C++ produced ~hundreds of these). Stripping
    balanced ``<...>`` groups (Rust ``Vec<T>``, C++ ``Array<T>``, C#/Java
    ``List<T>``) folds both spellings to the same owner. Unbalanced ``<`` (a
    C++ ``operator<`` / ``operator<<`` tail) simply drops the remainder, which is
    harmless for owner extraction — the class prefix precedes the operator name.
    """
    out = []
    depth = 0
    for ch in name:
        if ch == "<":
            depth += 1
        elif ch == ">":
            if depth > 0:
                depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def owner_of(name: Optional[str]) -> Optional[str]:
    """Return the owning-type prefix of a qualified symbol name, or None.

    ``"GlobSet::len"`` → ``"GlobSet"``; ``"Json.to"`` → ``"Json"``;
    ``"AP4_Array<T>::method"`` → ``"AP4_Array"`` (generics normalized so
    same-class template calls don't read as cross-class); a bare ``"helper"``
    (a free function, no separator) → ``None``.
    """
    if not name:
        return None
    name = _strip_generics(name)
    for sep in _OWNER_SEPARATORS:
        if sep in name:
            return name.rsplit(sep, 1)[0]
    return None


def _evidence_type(edge: Any, meta: "dict[str, Any]") -> Optional[str]:
    """evidence_type is a top-level field on the Edge dataclass but nests under
    ``meta`` in serialized survey JSON — read whichever is present."""
    ev = _get(edge, "evidence_type", None)
    if ev is None:
        ev = meta.get("evidence_type")
    return ev


def _receiver_was_identified(meta: "dict[str, Any]") -> bool:
    """True iff the edge carries any marker that the receiver was characterised
    (so it is a legitimate typed/recovered bind, not a receiver-blind magnet)."""
    rq = meta.get("resolution_quality")
    if rq is not None and rq != "ambiguous":
        return True
    return meta.get("receiver") in _RECEIVER_MARKERS


def _is_route_dispatch(meta: "dict[str, Any]") -> bool:
    """True iff the edge is a framework ROUTE REGISTRATION, not a method call.

    A route registration (``mux.HandleFunc("/x", dr.deprecationHandler)``,
    ``@app.get("/y") def h(): ...``) binds a handler BY REFERENCE to the real
    handler method — a correctly-resolved dispatch, not a receiver-blind
    method-CALL magnet. It trips the cross-owner check only incidentally: the
    call-site receiver token (``dr``) reads as an "owner" that differs from the
    handler method's class. ``meta.dispatch_kind == "route"`` (and the
    ``handler_name`` marker some route linkers stamp instead) puts these edges
    out of the magnet detector's scope. (Real repro: Go alertmanager's 8
    ``dr.deprecationHandler`` route edges — all correct, none a misbind.)
    """
    return meta.get("dispatch_kind") == "route" or "handler_name" in meta


def find_receiver_blind_magnets(
    nodes: Iterable[Any],
    edges: Iterable[Any],
    *,
    min_confidence: float = 0.80,
) -> List[Any]:
    """Return the edges that are receiver-blind cross-class internal-method magnets.

    An edge qualifies iff all hold:

    * ``edge_type`` (dataclass) / ``type`` (JSON) is ``calls`` or ``dispatches_to``;
    * ``is_resolved`` is true;
    * ``evidence_type`` is a bare pathway (``ast_call`` / ``ast_call_direct``);
    * no receiver marker (``meta.resolution_quality`` is absent/``"ambiguous"`` and
      ``meta.receiver`` is not a known-receiver marker);
    * ``confidence`` >= ``min_confidence``;
    * ``dst`` resolves to an in-set node whose ``kind`` is ``method``;
    * cross-owner: the ``src`` node's owner differs from the ``dst`` method's owner
      (a same-class implicit-``this`` call is legitimate and excluded).

    Returns the offending edge objects (per-edge, ungrouped) in input order; the
    caller may group by ``dst`` to rank magnets. Confidence defaults to the
    invariant's literal "high-confidence" band (0.80); pass ``min_confidence=0.0``
    to assert a controlled fixture is clean at any confidence.
    """
    by_id = {}
    for n in nodes:
        nid = _get(n, "id", None)
        if nid is not None:
            by_id[nid] = n

    out: List[Any] = []
    for edge in edges:
        etype = _get(edge, "edge_type", None) or _get(edge, "type", None)
        if etype not in ("calls", "dispatches_to"):
            continue
        if not _get(edge, "is_resolved", False):
            continue
        meta = _get(edge, "meta", None) or {}
        if _evidence_type(edge, meta) not in _BARE_EVIDENCE:
            continue
        if _receiver_was_identified(meta):
            continue
        if _is_route_dispatch(meta):
            continue
        conf = _get(edge, "confidence", 0.0)
        if conf is None or conf < min_confidence:
            continue
        dst = by_id.get(_get(edge, "dst", None))
        if dst is None or _get(dst, "kind", None) != "method":
            continue
        dst_owner = owner_of(_get(dst, "name", None) or _get(dst, "qualified_name", None))
        src = by_id.get(_get(edge, "src", None))
        src_owner = owner_of(_get(src, "name", None) or _get(src, "qualified_name", None))
        if src_owner is not None and src_owner == dst_owner:
            continue  # same-class implicit-this/self — legitimate
        out.append(edge)
    return out


# Well-known standard-library / core-interface method names. An untyped-receiver
# call to one of these overwhelmingly targets the stdlib type, not the arbitrary
# internal same-named method it bound to (``tmpl.Parse()`` → text/template, not a
# local ``Template.Parse``; ``x.Close()`` → io.Closer, not a repo ``Foo.Close``).
# These are the PascalCase (Go / Java / C#) interface-method names — DELIBERATELY
# case-sensitive so the snake_case trait methods that dominate Rust
# (``next`` / ``len`` / ``clone`` / ``into`` / ``channels``) are NOT swept: those
# are the correct-but-unprovable trait-dispatch binds INV-fahub's owner ruling
# KEEPS as ADR-0012 (real-type-resolution) scope, not the stdlib shadow this gate
# demotes. Mirrors ``go.py::_GO_STDLIB_INTERFACE_METHODS`` (a separate emit-time
# guard) but lives here as the finalize-stage, all-language demotion vocabulary.
_STDLIB_INTERFACE_METHODS = frozenset({
    # sync.Locker
    "Lock", "Unlock", "RLock", "RUnlock",
    # io: Reader / Writer / Closer / Seeker
    "Read", "Write", "Close", "Seek", "ReadAt", "WriteAt",
    # fmt.Stringer / error
    "String", "Error",
    # sort.Interface
    "Len", "Less", "Swap",
    # encoding.*Marshaler
    "MarshalJSON", "UnmarshalJSON", "MarshalText", "UnmarshalText",
    # context.Context
    "Deadline", "Done", "Value",
    # net/http Handler / ResponseWriter
    "ServeHTTP", "Header", "WriteHeader",
    # text/template, encoding, flag — Parse is a canonical concrete-type method
    # whose untyped-receiver call overwhelmingly hits the stdlib type.
    "Parse",
})


def _method_short_name(name: Optional[str]) -> Optional[str]:
    """The method segment of a qualified name — the inverse of ``owner_of``.

    ``"GlobSet::len"`` → ``"len"``; ``"Json.to"`` → ``"to"``; a bare ``"helper"``
    (no separator) → ``"helper"``.
    """
    if not name:
        return None
    for sep in _OWNER_SEPARATORS:
        if sep in name:
            return name.rsplit(sep, 1)[1]
    return name


def _harmful_magnet_reason(edge: Any, by_id: "dict[Any, Any]") -> Optional[str]:
    """Which cleanly-harmful sub-class a receiver-blind magnet ``edge`` is, or None.

    The single shared predicate behind ``find_harmful_magnets`` (the durable
    validator gate) and ``demote_harmful_magnets`` (the finalize action) so they
    can never diverge: ``"test_helper"`` (production→test-support misbind),
    ``"stdlib_interface"`` (stdlib-interface method shadow), or ``None`` (a
    correct-but-unprovable bind that both KEEP — ADR-0012 scope).
    """
    dst = by_id.get(_get(edge, "dst", None))
    src = by_id.get(_get(edge, "src", None))
    method = _method_short_name(
        _get(dst, "name", None) or _get(dst, "qualified_name", None)
    )
    dst_is_helper = is_test_file(_get(dst, "path", "") or "")
    src_is_helper = (
        is_test_file(_get(src, "path", "") or "") if src is not None else False
    )
    if dst_is_helper and not src_is_helper:
        return "test_helper"
    if method in _STDLIB_INTERFACE_METHODS:
        return "stdlib_interface"
    return None


def _by_id(nodes: Iterable[Any]) -> "dict[Any, Any]":
    out: "dict[Any, Any]" = {}
    for n in nodes:
        nid = _get(n, "id", None)
        if nid is not None:
            out[nid] = n
    return out


def find_harmful_magnets(
    nodes: Iterable[Any],
    edges: Iterable[Any],
    *,
    min_confidence: float = 0.80,
) -> List[Any]:
    """The receiver-blind magnets that are CLEANLY harmful — a production→
    test-helper misbind or a stdlib-interface-method shadow (see
    ``demote_harmful_magnets`` for the rationale). NON-mutating: the durable
    ``spec_validator`` gate calls this on the FINALIZED graph to assert none
    survived un-demoted (finalize's demotion should have redirected every one to
    external, so a survivor means a demotion-ordering regression, a new
    magnet-producing path, or a detector gap). Returns the offending edge objects
    in input order (empty list if none)."""
    nodes = list(nodes)
    by_id = _by_id(nodes)
    return [
        edge
        for edge in find_receiver_blind_magnets(
            nodes, edges, min_confidence=min_confidence
        )
        if _harmful_magnet_reason(edge, by_id) is not None
    ]


def demote_harmful_magnets(
    nodes: Iterable[Any],
    edges: Iterable[Any],
    *,
    min_confidence: float = 0.80,
) -> List[Any]:
    """Demote the CLEANLY-harmful receiver-blind magnets to unresolved-external.

    INV-fahub's letter says an unresolvable-receiver call must emit an
    ambiguous/external edge, not a high-confidence bind to an arbitrary internal
    def. Its owner ruling refines *which* magnets to act on: only the two
    sub-classes where the bound internal target is almost-certainly WRONG —

    * **production → test-helper**: the ``dst`` def lives in a test-support file
      (``paths.is_test_file`` — the broad heuristic that flags ``testutils/`` /
      ``fixtures/`` / ``mocks/`` / ``benches/`` …) while the ``src`` caller does
      not. Production code binding ``.Add()`` to a *test* ``Collector.Add`` is a
      misbind; the real target is elsewhere/external.
    * **stdlib-interface shadow**: the method's short name is a well-known
      standard-library interface method (``Close`` / ``Parse`` / ``Len`` …), which
      on an untyped receiver overwhelmingly targets the stdlib type, not the
      arbitrary internal same-named method.

    Everything else stays: the snake_case trait-method funnel (Rust ``x.next()``
    → ``Red::next``) is a correct-but-unprovable bind that needs real type
    resolution (ADR-0012), and the owner ruling KEEPS it rather than pay the
    recall cost of a blanket gate. Same-module builder binds (``Args.append``)
    and test→test-helper calls are likewise untouched.

    Each demoted edge has its ``dst`` **redirected** to an
    ``{lang}:external:0-0:{method}:unresolved`` id (so ``finalize``'s ADR-0037
    edge-resolution verdict derives ``is_resolved=False`` — this function never
    hand-sets the flag) and ``meta.resolution_quality='ambiguous'`` stamped to
    record that the receiver was unresolvable. Mutates the offending edge objects
    in place and returns them (empty list if none). Intended to run at the
    finalize chokepoint BEFORE the edge-resolution sub-step, over the whole
    reconciled graph, so it is a single all-language gate rather than a per-analyzer
    sweep.
    """
    nodes = list(nodes)
    by_id = _by_id(nodes)
    demoted: List[Any] = []
    for edge in find_receiver_blind_magnets(
        nodes, edges, min_confidence=min_confidence
    ):
        if _harmful_magnet_reason(edge, by_id) is None:
            continue
        dst = by_id.get(_get(edge, "dst", None))
        src = by_id.get(_get(edge, "src", None))
        method = _method_short_name(
            _get(dst, "name", None) or _get(dst, "qualified_name", None)
        )
        lang = (
            _get(src, "language", None) or _get(dst, "language", None) or "unknown"
        )
        _set(edge, "dst", f"{lang}:external:0-0:{method}:unresolved")
        meta = dict(_get(edge, "meta", None) or {})
        meta["resolution_quality"] = "ambiguous"
        _set(edge, "meta", meta)
        demoted.append(edge)
    return demoted
