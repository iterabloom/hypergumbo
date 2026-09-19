# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIP ``Index`` → hypergumbo ``Symbol`` translation shim (WI-mafut Phase 2 Slice B).

This module turns a parsed Sourcegraph SCIP ``Index`` protobuf into a list
of hypergumbo :class:`~hypergumbo_core.ir.Symbol` objects. It is
intentionally narrow: no I/O, no byte decoding, no edge emission. The
caller is expected to have already decoded wire bytes with
``scip_pb2.Index().ParseFromString(buf)`` (or to have built an Index
object directly, as the tests do). Edge emission (``Occurrence`` refs
→ ``calls``, ``Relationship`` → ``implements`` / ``has_type``) lives in
a sibling module and is the subject of Slice C.

Why this is a thin glue layer:

* The non-trivial SCIP work — parsing the ``<scheme> <manager> <package>
  <version> <descriptor>+`` symbol string including WI-zakub's trait-
  dispatch shape ``impl#[T][Trait]method().`` — already lives in Phase 1's
  :mod:`hypergumbo_core.scip.descriptor`. We reuse it unchanged.
* The hypergumbo ``Symbol`` dataclass is already the downstream IR for
  every analyzer; this module just fills the same fields.
* A SCIP ``Document`` is self-contained (its ``symbols`` list names
  everything the ``occurrences`` list refers to), so the translator is
  one linear pass over ``index.documents`` with a tiny
  ``symbol_string → Definition occurrence`` map kept per document.

Offset and indexing conventions pinned here:

* ``Occurrence.range`` is ``repeated int32`` and SCIP encodes it either
  as ``[start_line, start_col, end_col]`` (single-line span) or
  ``[start_line, start_col, end_line, end_col]`` (multi-line span).
  Any other length is a malformed input and raises ``ValueError``.
* SCIP lines are 0-indexed; hypergumbo :class:`~hypergumbo_core.ir.Span`
  lines are 1-indexed. The ``+1`` rewrite happens here so downstream
  sketch/slice/rank code never sees a 0-indexed line number.
* SCIP columns are UTF-8 code-unit offsets by default (WI-zakub §3).
  hypergumbo ``Span`` columns are also offsets, so we pass them through
  unchanged. If a SCIP index ever declares a non-UTF-8
  ``PositionEncoding`` in ``Metadata``, the column values would need
  re-encoding; we don't handle that yet — no in-the-wild emitter uses
  a non-UTF-8 encoding, and adding a conversion pass without a real
  input to test against would be speculative.

Edge cases intentionally handled by skip rather than raise:

* ``SymbolInformation`` with no ``Definition``-role occurrence in the
  document (typically an imported / external symbol) — skipped, and this
  is the RIGHT layer to skip at. This shim emits Symbols, and a symbol
  with no Definition here is a *reference*, not a definition; hypergumbo
  materialises external references from the EDGE side, where
  :func:`~hypergumbo_core.ir.create_boundary_nodes` mints
  ``external_symbol`` boundary nodes for dangling edge endpoints. An
  earlier version of this note said "Slice C will wire those to
  external-tier nodes", which was wrong twice over: Slice C shipped
  without doing it, and the symbol side was never where it belonged.

  Where it actually lives, for anyone following the thread: the Rust
  backend's ``translate.py`` passes a ``resolve_symbol`` map and returns
  ``None`` for a SCIP symbol with no in-workspace definition, which
  *drops the edge* rather than letting it dangle into a boundary node.
  That is deliberate and load-bearing — before commit ``9266762d4b``,
  endpoints were raw SCIP descriptor strings, so EVERY scip edge dangled
  and finalize's endpoint-integrity step silently discarded the entire
  scip call graph while the scip Symbols survived (0 → 739 edges on
  zoxide once endpoints resolved). Letting externals dangle again is not
  free: it needs a canonical ``{lang}:{path}:{span}:{name}:{kind}`` id
  minted from the SCIP symbol, not the raw descriptor.

  Consequence, stated plainly: unlike the ~69 ``:unresolved`` dst sites
  in the tree-sitter analyzers, a Rust call into a dependency or the
  stdlib produces no edge at all under this backend. Tracked as
  WI-gojum sub-component 1 ("external calls recorded accurately"),
  parked by the owner 2026-07-19 pending ADR-0012 Steps 2-3.
* Malformed SCIP symbol strings (``parse_scip_symbol`` raises) — skipped
  with no warning. A buggy upstream emitter must not abort the whole
  translation pass; downstream analyses will simply see missing symbols
  rather than a crash.
* ``SymbolInformation`` whose descriptor chain is empty (e.g. header
  with no trailing descriptor) — skipped. The Phase 1 parser raises on
  this input, so the try/except handles it uniformly with the malformed
  case above.
* Local symbols (``local <id>``) — skipped, and deliberately (WI-jikok /
  INV-kukiz, owner ruling 2026-09-18). A SCIP local id is an index into
  its DOCUMENT, not an identifier: ``local 0`` is a different binding in
  every file, and rust-analyzer emits one per ``let``, parameter and
  pattern binding. Minting them did two wrong things at once. The
  Symbol's name was the bare index (``"1"``, ``"102"``) and its
  ``stable_id`` was ``sha256("local 1")``, so on a 16-file crate 498 of
  1255 Symbols shared an id (39.7%); and the Rust backend's edge resolver
  is one index-wide map keyed on the raw moniker, so a read of ``local 0``
  in one file resolved to the ``local 0`` of whichever file was mapped
  last — 329 of 455 local-pointing edges crossed files, 21.2% of every
  edge in the artifact, each asserting a reference that cannot exist.
  Those 500 nodes were 75% of the backend's output and, carrying the
  registry's Terraform ``local`` kind, passed the key-symbol predicate
  and outranked real functions in the sketch. The ruling follows every
  other backend: py.py, kotlin.py, go.py and the tree-sitter rust.py all
  READ function-local bindings (for receiver typing and constant
  resolution) and mint nothing for them, because a binding no other file
  can name is not part of a behavior map. The sibling shims
  :mod:`.calls` and :mod:`.edges` apply the same predicate
  (:func:`~hypergumbo_core.scip.descriptor.is_local_symbol`) to edge
  endpoints, so the rule holds for a library caller that supplies no
  resolver. Its parity-side consequence — the remaining SCIP Symbols
  carry ``sha256(moniker)``, an identity the tree-sitter arm never
  shares — is WI-gojum's, not this module's.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..analyze.base import _short_sha256, make_symbol_id
from ..ir import Span, Symbol
from ._generated import scip_pb2
from .descriptor import DescriptorKind, is_local_symbol, parse_scip_symbol


_ROLE_DEFINITION = 0x01

_KIND_MAP: Dict[DescriptorKind, str] = {
    DescriptorKind.NAMESPACE: "namespace",
    DescriptorKind.TYPE: "class",
    DescriptorKind.TERM: "variable",
    DescriptorKind.METHOD: "method",
    DescriptorKind.MACRO: "macro",
    DescriptorKind.TYPE_PARAMETER: "type_parameter",
    DescriptorKind.PARAMETER: "parameter",
    # INV-lagot: SCIP's META descriptor (the ``:`` suffix) is an encoding
    # category, not a source-language construct, so ``"meta"`` could never be
    # a registered Symbol.kind under ADR-0027's axiom. It maps to the
    # registry's declared catch-all instead of inventing a value.
    #
    # NOT folded into ``attribute``, which the registry defines as "Attribute
    # declaration (Python class attribute, etc.)" — a data member, not what
    # SCIP means here. RE-EVALUATION TRIGGER: zero META descriptors were
    # observed in rust-analyzer output when this was measured (681
    # SymbolInformation entries on aardvark-dns: 500 local, 80 method, 48
    # term, 25 type, 18 namespace, 10 type_parameter, 0 parameter, 0 meta).
    # The first observed META instance should be inspected and given a
    # specific kind if it has one.
    DescriptorKind.META: "declaration",
}

# ---------------------------------------------------------------------------
# WI-gapup / ADR-0057 §7: kind from the producer's declaration, else the chain
# ---------------------------------------------------------------------------
#
# ``_KIND_MAP`` above reads only the LEAF descriptor's suffix, and SCIP's
# ``().`` suffix (DescriptorKind.METHOD) covers a free function as well as a
# method — so every free function the SCIP arm emitted was ``kind="method"``
# (52 of 52 on aardvark-dns), and every enum variant, a ``#`` TYPE nested in a
# TYPE, was a ``class``. Two sources of truth were being ignored:
#
# 1. ``SymbolInformation.kind``. rust-analyzer sets it on every global
#    definition (169 of 169 on the recorded aardvark-dns index: Function,
#    Method / StaticMethod / TraitMethod, Field, EnumMember, Struct, Enum,
#    Trait, TypeAlias, Constant, StaticVariable, Module). That is the
#    PRODUCER'S OWN claim about the construct and it wins. ``_SCIP_KIND_MAP``
#    maps the kinds we have a registered counterpart for; it is deliberately
#    NOT total over SCIP's 70-odd members — an unmapped kind (Axiom, Lemma,
#    Quasiquoter, ...) falls through to rule 2 rather than minting a guess.
#    ``EnumMember`` lands as ``field``, the tree-sitter Rust arm's deliberate
#    choice for a variant (WI-duguk); whether a registered variant kind should
#    exist is an ADR-0027 question this module does not decide. ``Module``
#    stays ``namespace`` for the same reason (module-vs-namespace is not this
#    map's call). ``Constant`` is the registry's generic ``constant``: SCIP is
#    language-agnostic and so is this table.
#
# 2. The descriptor CHAIN, for an emitter that leaves ``kind`` unset. A METHOD
#    or TERM leaf is placed by its nearest ancestor that is not a
#    TYPE_PARAMETER: under a TYPE it is a method / field, otherwise a
#    function / variable. The type-parameter skip is not a nicety —
#    rust-analyzer spells an impl target as one (``impl#[Counter]increment().``
#    parses as TYPE impl, TYPE_PARAMETER Counter, METHOD increment), and
#    reading the immediate parent would have called 26 of 27 methods free
#    functions. A nested TYPE stays ``class``: in scip-python ``Outer#Inner#``
#    is a nested class, and only a DECLARED EnumMember is a variant.
#
# Both maps are held on the Symbol.kind axis by test_scip_kind_map_conformance.


def symbol_information_kind_values() -> Dict[str, int]:
    """``SymbolInformation.Kind`` member name -> value, via the enum descriptor.

    The generated module has no type stubs, so a direct attribute access is
    a strict-typing error; going through the descriptor's ``keys()`` /
    ``Value()`` is the same enum, typed honestly as ``Any``.
    """
    generated: Any = scip_pb2  # no stubs: every member is Any from here on
    kind_enum = generated.SymbolInformation.Kind
    return {name: int(kind_enum.Value(name)) for name in kind_enum.keys()}


_KIND_VALUE = symbol_information_kind_values()
_SCIP_KIND_MAP: Dict[int, str] = {
    _KIND_VALUE[name]: kind
    for name, kind in (
        ("Function", "function"),
        ("Method", "method"),
        ("StaticMethod", "method"),
        ("TraitMethod", "method"),
        ("AbstractMethod", "method"),
        ("ProtocolMethod", "method"),
        ("PureVirtualMethod", "method"),
        ("Constructor", "constructor"),
        ("Field", "field"),
        ("Property", "property"),
        ("EnumMember", "field"),
        ("Constant", "constant"),
        ("StaticVariable", "variable"),
        ("Variable", "variable"),
        ("Struct", "struct"),
        ("Enum", "enum"),
        ("Trait", "trait"),
        ("Interface", "interface"),
        ("Class", "class"),
        ("TypeAlias", "type_alias"),
        ("Module", "namespace"),
        ("Namespace", "namespace"),
        ("Package", "package"),
        ("Macro", "macro"),
        ("Parameter", "parameter"),
        ("TypeParameter", "type_parameter"),
    )
}


def _kind_from_chain(descriptors: "tuple[Any, ...]") -> str:
    """Rule 2 above: place a METHOD / TERM leaf by its nearest non-type-parameter ancestor."""
    leaf = descriptors[-1]
    ancestor = next(
        (d for d in reversed(descriptors[:-1]) if d.kind is not DescriptorKind.TYPE_PARAMETER),
        None,
    )
    under_type = ancestor is not None and ancestor.kind is DescriptorKind.TYPE
    if leaf.kind is DescriptorKind.METHOD:
        return "method" if under_type else "function"
    if leaf.kind is DescriptorKind.TERM:
        return "field" if under_type else "variable"
    return _KIND_MAP[leaf.kind]


def _span_from_range(range_array: "list[int]") -> Span:
    """Convert a SCIP ``Occurrence.range`` int array into a :class:`Span`.

    SCIP encodes ranges as either a 3-int or 4-int array. 3 ints are a
    single-line span ``[start_line, start_col, end_col]``; 4 ints are a
    multi-line span ``[start_line, start_col, end_line, end_col]``.
    SCIP lines are 0-indexed; we return 1-indexed lines to match the
    rest of hypergumbo's IR.
    """
    n = len(range_array)
    if n == 3:
        start_line, start_col, end_col = range_array
        end_line = start_line
    elif n == 4:
        start_line, start_col, end_line, end_col = range_array
    else:
        raise ValueError(
            f"SCIP range must have 3 or 4 elements; got {n}"
        )
    return Span(
        start_line=int(start_line) + 1,
        end_line=int(end_line) + 1,
        start_col=int(start_col),
        end_col=int(end_col),
    )


def _resolve_language(doc: scip_pb2.Document) -> str:
    """Normalize ``Document.language`` to a lowercase hypergumbo language.

    SCIP emitters set ``Document.language`` to strings like ``"Rust"``
    (rust-analyzer) or a lowercase form; hypergumbo analyzers use lowercase
    identifiers across the board, so we flatten here. A producer may also
    leave the field EMPTY — scip-python 0.6.6 does, on every document
    (WI-nanom) — and until this fallback that landed every one of its
    records in language ``"unknown"``, which the merge pass keys on, so
    nothing could ever pair with the ``python`` arm. The file extension is
    the backend-neutral answer (the taxonomy's :func:`get_language`, the
    same lookup discovery uses); ``"unknown"`` is kept only when neither
    the producer nor the extension says, so ``id`` construction never
    produces a malformed ``:filename:...`` prefix.
    """
    raw = doc.language or ""
    if raw:
        return raw.lower()
    from ..taxonomy import get_language  # local: taxonomy is heavy and this module is not

    return get_language(Path(doc.relative_path)) or "unknown"


def _name_and_kind(scip_sym: Any, declared_kind: int = 0) -> "tuple[str, str]":
    """Pick the (name, kind) pair for a parsed, GLOBAL :class:`ScipSymbol`.

    The name is the last descriptor's — SCIP puts the most specific piece
    last, so for ``module/Class#method().`` it is ``method``. The kind is the
    producer's declared ``SymbolInformation.kind`` when :data:`_SCIP_KIND_MAP`
    knows it, else the descriptor chain's placement (WI-gapup; see the
    comment block above the map for why the leaf suffix alone was wrong on
    52 of 52 free functions and 9 of 9 enum variants).

    Local symbols never reach here: :func:`scip_index_to_symbols` skips
    them on the raw string (see the module docstring). Until WI-jikok this
    function returned ``(local_id, "local")`` for them — a name with no
    lexical content under the registry's Terraform ``local`` kind. A local
    handed to it now has no descriptors and takes the defensive branch.
    """
    if not scip_sym.descriptors:  # pragma: no cover
        # Defensive: parse_scip_symbol already rejects a header with
        # zero descriptors, and the caller filters locals (the other
        # descriptor-less shape) before parsing. Kept as a guard against
        # a future parser regression.
        return "", "unknown"
    last = scip_sym.descriptors[-1]
    declared = _SCIP_KIND_MAP.get(declared_kind)
    if declared is not None:
        return last.name, declared
    # INV-lagot: the chain rule bottoms out in ``_KIND_MAP[leaf.kind]``, which
    # was ``.get(last.kind, "unknown")`` and minted a fourth unregistered
    # kind. The fallback is unreachable and always was: DescriptorKind is a
    # closed Enum, every producer in ``descriptor.py`` constructs from it,
    # and the map is total over it — a totality held by
    # test_scip_kind_map_conformance. A direct subscript is the honest
    # expression of that, and a KeyError on a future ninth member is louder
    # and more truthful than silently minting a kind nothing describes.
    return last.name, _kind_from_chain(scip_sym.descriptors)


def _build_meta(sym_info: scip_pb2.SymbolInformation) -> Dict[str, Any]:
    """Collect the SCIP metadata fields worth preserving on a Symbol.

    We always record the raw SCIP symbol string so later passes can
    re-resolve references without re-parsing the descriptor chain.
    ``display_name`` and SCIP ``Kind`` are preserved when non-default
    so downstream renderers can show the upstream-provided labels.
    """
    meta: Dict[str, Any] = {"scip_symbol": sym_info.symbol}
    if sym_info.display_name:
        meta["display_name"] = sym_info.display_name
    if sym_info.kind:
        meta["scip_kind"] = int(sym_info.kind)
    return meta


def scip_index_to_symbols(index: scip_pb2.Index) -> List[Symbol]:
    """Walk a parsed SCIP ``Index`` and emit hypergumbo ``Symbol`` objects.

    Returns one Symbol per ``SymbolInformation`` that has a
    ``Definition``-role ``Occurrence`` in the same document. External
    symbols (no Definition in this index), malformed SCIP symbol
    strings, and Occurrences with out-of-range int arrays other than
    3/4 elements are handled as described in the module docstring.
    """
    out: List[Symbol] = []
    for doc in index.documents:
        lang = _resolve_language(doc)
        def_occ: Dict[str, scip_pb2.Occurrence] = {}
        for occ in doc.occurrences:
            if occ.symbol_roles & _ROLE_DEFINITION and occ.symbol not in def_occ:
                def_occ[occ.symbol] = occ
        for sym_info in doc.symbols:
            occ = def_occ.get(sym_info.symbol)
            if occ is None:
                continue
            if is_local_symbol(sym_info.symbol):
                # A function-local binding is not part of the map — see
                # the module docstring (WI-jikok / INV-kukiz).
                continue
            try:
                parsed = parse_scip_symbol(sym_info.symbol)
            except ValueError:
                continue
            name, kind = _name_and_kind(parsed, sym_info.kind)
            if not name:  # pragma: no cover
                # Defensive: only reachable if _name_and_kind's empty-
                # descriptor guard fires, which parse_scip_symbol already
                # precludes. Kept for robustness against parser changes.
                continue
            span = _span_from_range(list(occ.range))
            sid = make_symbol_id(
                lang, doc.relative_path, span.start_line, span.end_line, name, kind
            )
            out.append(
                Symbol(
                    id=sid,
                    name=name,
                    kind=kind,
                    language=lang,
                    path=doc.relative_path,
                    span=span,
                    origin="scip",
                    # INV-hunup: canonical sha256 stable_id. A GLOBAL SCIP
                    # moniker (sym_info.symbol) is a stable identity across the
                    # index, so hashing it yields a stable canonical id; the raw
                    # moniker is preserved in meta["scip_symbol"] (_build_meta).
                    # That sentence was false for locals — ``local 0`` recurs in
                    # every document — which is one of the two reasons they are
                    # filtered above rather than hashed here.
                    stable_id=_short_sha256(sym_info.symbol),
                    meta=_build_meta(sym_info),
                )
            )
    return out
