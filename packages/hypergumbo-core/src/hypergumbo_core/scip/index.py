# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIP ``Index`` → hypergumbo ``Symbol`` translation shim.

This module turns a parsed Sourcegraph SCIP ``Index`` protobuf into a list
of hypergumbo :class:`~hypergumbo_core.ir.Symbol` objects. It is
intentionally narrow: no I/O, no byte decoding, no edge emission. The
caller is expected to have already decoded wire bytes with
``scip_pb2.Index().ParseFromString(buf)`` (or to have built an Index
object directly, as the tests do). Edge emission lives in two sibling
modules: :mod:`.edges` turns ``Relationship`` entries into
``implements`` / ``references``, and :mod:`.calls` turns
``Occurrence`` refs into ``calls`` or ``references``.

Why this is a thin glue layer:

* The non-trivial SCIP work — parsing the ``<scheme> <manager> <package>
  <version> <descriptor>+`` symbol string including rust-analyzer's trait-
  dispatch shape ``impl#[T][Trait]method().`` — lives in
  :mod:`hypergumbo_core.scip.descriptor`, which this module reuses.
* The hypergumbo ``Symbol`` dataclass is already the downstream IR for
  every analyzer; this module just fills the same fields.
* Kind comes from the producer's ``SymbolInformation.kind`` when it maps to
  a registered kind (``_SCIP_KIND_MAP``), else from the descriptor chain
  (WI-gapup / ADR-0057 §7; see the comment block above
  ``symbol_information_kind_values``). An empty ``Document.language`` falls
  back to the file extension (WI-nanom).
* A SCIP ``Document`` is self-contained (its ``symbols`` list names
  everything the ``occurrences`` list refers to), so the translator is
  one linear pass over ``index.documents`` with a tiny
  ``symbol_string → Definition occurrence`` map kept per document.

Offset and indexing conventions pinned here:

* ``Occurrence.range`` is ``repeated int32`` and SCIP encodes it either
  as ``[start_line, start_col, end_col]`` (single-line span) or
  ``[start_line, start_col, end_line, end_col]`` (multi-line span).
  Any other length is a malformed input and raises ``ValueError``.
* ``Symbol.span`` is the Definition occurrence's ``range`` — the NAME
  token, which is the anchor both SCIP backends declare
  (``SPAN_ROLE_TOKEN``) and the merge pass pairs on. When the emitter also
  gives an ``enclosing_range`` that differs (rust-analyzer: the whole
  item), that extent rides in ``meta["item_span"]`` (1-indexed, same
  fields as a ``Span``). Re-spanning here would break the pairing; the
  merge pass instead applies it to a record it does not fold, and drops
  it from one it does (INV-lodum). A malformed ``enclosing_range`` counts
  as absent, not as fatal: unlike ``range``, nothing is lost without it.
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
  ``external_symbol`` boundary nodes for dangling edge endpoints.

  On that edge side, the Rust backend's ``translate.py`` passes a
  ``resolve_symbol`` resolver (a ``MonikerResolver``) that returns ``None``
  for a SCIP symbol with no in-workspace definition (and for an ambiguous
  one), which *drops the edge* rather than letting it dangle into a
  boundary node. That is deliberate and load-bearing: an endpoint left as
  a raw SCIP descriptor string dangles, and finalize's endpoint-integrity
  step silently discards such edges, so unresolved endpoints would lose
  the whole scip call graph while the scip Symbols survive. Letting
  externals dangle is not free: it needs a canonical
  ``{lang}:{path}:{span}:{name}:{kind}`` id minted from the SCIP symbol,
  not the raw descriptor.

  Consequence, stated plainly: unlike the tree-sitter analyzers, which
  emit an ``:unresolved`` dst for such a call, a Rust call into a
  dependency or the stdlib produces no edge at all under this backend
  (open: WI-gojum, "external calls recorded accurately", pending ADR-0012
  Steps 2-3).
* Malformed SCIP symbol strings (``parse_scip_symbol`` raises) — skipped
  with no warning. A buggy upstream emitter must not abort the whole
  translation pass; downstream analyses will simply see missing symbols
  rather than a crash.
* ``SymbolInformation`` whose descriptor chain is empty (e.g. header
  with no trailing descriptor) — skipped. :func:`parse_scip_symbol`
  raises on this input, so the try/except handles it uniformly with the
  malformed case above.
* Local symbols (``local <id>``) — skipped, and deliberately. A SCIP local
  id is an index into its DOCUMENT, not an identifier: ``local 0`` is a
  different binding in every file, and rust-analyzer emits one per
  ``let``, parameter and pattern binding. Minting them would give each
  Symbol a bare-index name (``"1"``, ``"102"``) and the ``stable_id``
  ``sha256("local 1")`` shared by every file's ``local 1``; an edge keyed
  on the raw moniker would point at another file's binding, asserting a
  reference that cannot exist; and, carrying the registry's Terraform
  ``local`` kind, they would pass the key-symbol predicate and outrank
  real functions in the sketch. This follows every other backend:
  py.py, kotlin.py, go.py and the tree-sitter rust.py all
  READ function-local bindings (for receiver typing and constant
  resolution) and mint nothing for them, because a binding no other file
  can name is not part of a behavior map. The sibling shims
  :mod:`.calls` and :mod:`.edges` apply the same predicate
  (:func:`~hypergumbo_core.scip.descriptor.is_local_symbol`) to edge
  endpoints, so the rule holds for a library caller that supplies no
  resolver. Its parity-side consequence — the remaining SCIP Symbols
  carry a moniker-derived identity the tree-sitter arm never shares — is
  WI-gojum's, not this module's. (history: WI-jikok, INV-kukiz)

Identity (``stable_id``, INV-kutid):

* ``stable_id = sha256("scip:<Document.relative_path>:<moniker>")``,
  truncated like every other id (:func:`scip_stable_id`). A moniker alone
  is only as scoped as its emitter makes it, and rust-analyzer's names the
  cargo PACKAGE (``rust-analyzer cargo aardvark-dns 2.0.0-dev crate/``),
  not the crate TARGET: a package's lib (``src/lib.rs``), bin
  (``src/main.rs``) and build script (``build.rs``) each define
  ``crate/`` -- and any same-named item at their roots -- under one
  string, which rust-analyzer itself logs as "Duplicate symbol". Hashed
  alone, the three roots would share one id across three files, a group
  the within-file collision splitter never sees. The defining Document's
  path is the one per-Document input that tells targets apart, so it goes
  into the hash, which is also what ADR-0035 §4 does for every tree-sitter
  producer. Recovering the TARGET from cargo's conventional paths instead
  would be wrong for ``[lib] path =`` and ``[[bin]] path =`` and would
  re-implement cargo's target discovery.
* The cost is the tree-sitter arm's: renaming a file re-mints its ids.
  A file compiled into two targets (``mod util;`` in both lib and bin) is
  one path and one moniker, so its items stay one id -- the same code.
* Only SCIP-only records keep this value. A record the merge pass folds
  takes the incumbent's id (ADR-0057) and keeps this one as an
  alternative.
* Edge endpoints are a separate question: they resolve by the moniker
  (``meta["scip_symbol"]``) read in the occurrence's Document, and a moniker
  several targets share is decided by that Document, then by its crate
  target, or refused and counted (:mod:`.resolve`, INV-sipod).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

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
    # SCIP means here. RE-EVALUATION TRIGGER: no META descriptor has been
    # seen in rust-analyzer output. The first observed META instance should
    # be inspected and given a specific kind if it has one.
    DescriptorKind.META: "declaration",
}

# ---------------------------------------------------------------------------
# WI-gapup / ADR-0057 §7: kind from the producer's declaration, else the chain
# ---------------------------------------------------------------------------
#
# ``_KIND_MAP`` above reads only the LEAF descriptor's suffix, and SCIP's
# ``().`` suffix (DescriptorKind.METHOD) covers a free function as well as a
# method — so the leaf alone calls every free function ``kind="method"``,
# and every enum variant, a ``#`` TYPE nested in a TYPE, a ``class``. Two
# better sources, in order:
#
# 1. ``SymbolInformation.kind``. rust-analyzer sets it on its global
#    definitions (Function, Method / StaticMethod / TraitMethod, Field,
#    EnumMember, Struct, Enum, Trait, TypeAlias, Constant, StaticVariable,
#    Module). That is the
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
#    reading the immediate parent would call such methods free functions.
#    A nested TYPE stays ``class``: in scip-python ``Outer#Inner#``
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
        # The abstract-type family is mapped WHOLE. SCIP declares Protocol
        # beside Trait and Interface, and omitting it would keep a Swift or
        # Objective-C protocol indexed by SCIP from ever reaching the
        # `protocol` kind -- the partial-family enumeration that
        # test_no_language_agnostic_module_hand_rolls_a_partial_abstract_family
        # catches.
        ("Trait", "trait"),
        ("Interface", "interface"),
        ("Protocol", "protocol"),
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


def scip_stable_id(relative_path: str, moniker: str) -> str:
    """The ``stable_id`` of a SCIP Definition (module docstring, "Identity").

    ``relative_path`` is the defining ``Document.relative_path`` (repo-
    relative, as SCIP emits it); ``moniker`` is the global SCIP symbol
    string. Both are hashed because the moniker alone does not name the
    crate target (INV-kutid).
    """
    return _short_sha256(f"scip:{relative_path}:{moniker}")


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


def _item_span(occ: Any, token: Span) -> Optional[Dict[str, int]]:
    """The item extent to carry in ``meta["item_span"]``, or None.

    None when the emitter gave no ``enclosing_range``, a malformed one, or
    one equal to the token span (rust-analyzer's file-level modules: the
    "name" already spans the file). ``occ`` is typed ``Any`` for the same
    reason as in :mod:`.calls` (no stubs for the generated module).
    """
    if not len(occ.enclosing_range):
        return None
    try:
        item = _span_from_range(list(occ.enclosing_range))
    except ValueError:
        return None
    if item == token:
        return None
    return {
        "start_line": item.start_line, "end_line": item.end_line,
        "start_col": item.start_col, "end_col": item.end_col,
    }


def _resolve_language(doc: scip_pb2.Document) -> str:
    """Normalize ``Document.language`` to a lowercase hypergumbo language.

    SCIP emitters set ``Document.language`` to strings like ``"Rust"``
    (rust-analyzer) or a lowercase form; hypergumbo analyzers use lowercase
    identifiers across the board, so we flatten here. A producer may also
    leave the field EMPTY — scip-python 0.6.6 does, on every document
    (WI-nanom) — and without this fallback every one of its records would
    land in language ``"unknown"``, which the merge pass keys on, so
    nothing could pair with the ``python`` arm. The file extension is
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
    knows it, else the descriptor chain's placement (see the comment block
    above the map for why the leaf suffix alone is wrong for free functions
    and enum variants).

    Local symbols never reach here: :func:`scip_index_to_symbols` skips
    them on the raw string (see the module docstring). A local handed to it
    has no descriptors and takes the defensive branch.
    """
    if not scip_sym.descriptors:  # pragma: no cover
        # Defensive: parse_scip_symbol already rejects a header with
        # zero descriptors, and the caller filters locals (the other
        # descriptor-less shape) before parsing. Kept as a guard against
        # a future parser regression. It RAISES rather than returning a
        # placeholder kind: even a placeholder like ``("", "unknown")`` that
        # the caller's empty-name check would drop is an unregistered
        # Symbol.kind in the producer's source, and the producer gates read
        # tuple returns. (history: WI-nakur)
        raise ValueError(f"SCIP symbol has no descriptors: {scip_sym!r}")
    last = scip_sym.descriptors[-1]
    declared = _SCIP_KIND_MAP.get(declared_kind)
    if declared is not None:
        return last.name, declared
    # The chain rule bottoms out in a direct ``_KIND_MAP[leaf.kind]``
    # subscript, not a ``.get(..., "unknown")`` fallback that would mint an
    # unregistered kind. Such a fallback is unreachable: DescriptorKind is a
    # closed Enum, every producer in ``descriptor.py`` constructs from it,
    # and the map is total over it — a totality held by
    # test_scip_kind_map_conformance. A KeyError on a future ninth member is
    # louder and more truthful than silently minting a kind nothing
    # describes. (history: INV-lagot)
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
                name, kind = _name_and_kind(parsed, sym_info.kind)
            except ValueError:
                continue
            if not name:  # pragma: no cover
                # Defensive: a last descriptor whose name is empty (the
                # parser accepts an empty backtick-quoted name). Unmeasured.
                continue
            span = _span_from_range(list(occ.range))
            meta = _build_meta(sym_info)
            item = _item_span(occ, span)
            if item is not None:
                meta["item_span"] = item
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
                    # INV-hunup: canonical sha256 stable_id; INV-kutid: of the
                    # defining Document's path AND the moniker (module
                    # docstring, "Identity"). The raw moniker is preserved in
                    # meta["scip_symbol"] (_build_meta).
                    stable_id=scip_stable_id(doc.relative_path, sym_info.symbol),
                    meta=meta,
                )
            )
    return out
