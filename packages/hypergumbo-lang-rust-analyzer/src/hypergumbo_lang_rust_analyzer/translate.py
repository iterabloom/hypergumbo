# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIP bytes → hypergumbo ``(Symbol, Edge)`` translation with rust.py parity.

How It Works
------------
``rust-analyzer scip`` emits a Protobuf-encoded ``scip.Index`` file
describing every defined symbol, every reference, and every
relationship in a Rust workspace. This module wraps the hypergumbo-core
SCIP shim (``hypergumbo_core.scip.*``) with three things the generic
shim cannot do on its own:

1. **Stable-ID parity with rust.py.** The core shim fills
   ``Symbol.stable_id`` with ``scip_stable_id``, a sha256 of the defining
   Document's relative path and the SCIP moniker (INV-kutid); the raw
   moniker stays in ``meta['scip_symbol']``. That id is correct for
   cross-SCIP-pass dedup but does not match the
   signature-derived stable_id rust.py computes for the same function
   in the same source file. Without reassignment, enabling this
   backend on a cached analysis double-counts every Rust symbol.
   :func:`reassign_rust_stable_ids` replaces the SCIP-derived
   ``stable_id`` with the rust.py-equivalent one whenever a Rust
   function span can be located in the caller-provided source reader,
   using
   :func:`hypergumbo_lang_mainstream.rust_scip.compute_rust_stable_id_from_source`.
   Non-Rust Symbols (SCIP can carry multiple languages in one index),
   Rust Symbols that are neither ``function`` nor ``method`` (structs,
   modules, constants — rust.py has no signature-level parity for these),
   and Symbols whose source cannot be read pass through unchanged.

   **What this achieves in production: nothing, for any multi-line
   function (INV-dolud).** The span handed to the helper is the SCIP
   Definition-occurrence range, and rust-analyzer's is the identifier
   token; the helper requires the item range, so it abstains and the
   path-and-moniker id (``scip_stable_id``) stays. The two Rust arms
   therefore do not share a ``stable_id`` for such functions, so the
   double-counting the paragraph above describes is the shipped state, not
   the averted one. The pass is kept as readiness for WI-gojum, which owns
   whether the arms should share an identity; see ``rust_scip.py``'s
   docstring for why it is not simply "fixed" here.

2. **One-shot translate.** :func:`translate_scip_to_hg` bundles the
   three core shim calls (``scip_index_to_symbols``,
   ``scip_index_to_edges``, ``scip_index_to_call_edges``) with the
   parity reassignment so the backend's analyzer wrapper can consume a
   single entry point. It also stamps ``run_id`` onto what it emits,
   falling back to a locally-constructed ``AnalysisRun`` when the caller
   supplies none (WI-higap).

3. **Symbol resolution, and the deliberate dropping of external edges.**
   The translate builds a ``MonikerResolver`` (``hypergumbo_core.scip.
   resolve``) over each Symbol's ``meta['scip_symbol']`` and passes it to
   both edge builders as ``resolve_symbol=``. It is keyed per Document, not
   per moniker (INV-sipod). rust-analyzer gives a package's lib, bin and
   build-script roots one ``crate/`` moniker, so a reference resolves in its
   own Document first, then within its crate target (``crate_units``: the
   ``mod`` tree from each root), and is otherwise refused and counted, with
   one line to ``log``. It returns ``None`` for a symbol outside the indexed
   workspace, and the shims skip the edge rather than emitting it.

   This is load-bearing, not an oversight: with raw SCIP descriptor
   strings as endpoints every scip edge dangles, and ``finalize`` silently
   discards the entire scip call graph. The residual consequence is that a
   Rust call into a dependency or the stdlib yields no edge under this
   backend, where the tree-sitter analyzer would emit an ``:unresolved``
   one (WI-gojum).

Why This Design
---------------
rust-analyzer leaves ``SymbolInformation.relationships`` empty
(WI-zakub); the primary edge source is
therefore non-Definition ``Occurrence``s routed through
``scip_index_to_call_edges``. Both ``scip_index_to_edges`` and
``scip_index_to_call_edges`` are still called here — the former is
zero-cost when the relationship set is empty, and keeping both in the
pipeline lets the translator also work on SCIP indexes produced by
other tools (scip-python, scip-java) that do populate relationships.

The ``source_reader`` callable is a caller-owned I/O boundary:
production callers pass a function that reads ``path`` bytes from the
indexed workspace; tests pass a dict-backed fake so the whole
translate path can be exercised without filesystem access. Reader
failures degrade to "skip this symbol's reassignment" rather than
aborting the whole translate — the SCIP-derived ``stable_id`` remains as
a best-effort fallback. The caught set is exactly ``(OSError, ValueError)``
(``FileNotFoundError`` is an ``OSError``); anything else propagates and
does abort the translate.
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional

from hypergumbo_core.ir import Edge, Symbol
from hypergumbo_core.scip._generated import scip_pb2
from hypergumbo_core.scip.calls import scip_index_to_call_edges
from hypergumbo_core.scip.edges import scip_index_to_edges
from hypergumbo_core.scip.index import scip_index_to_symbols
from hypergumbo_core.scip.resolve import MonikerResolver
from hypergumbo_lang_mainstream.rust_scip import (
    compute_rust_stable_id_from_source,
)

from .crate_units import crate_owners, same_crate


SourceReader = Callable[[str], Optional[bytes]]


def _read_source(source_reader: SourceReader, path: str) -> Optional[bytes]:
    """Invoke *source_reader*, swallowing I/O errors as ``None``.

    Reader exceptions (FileNotFoundError, OSError, or anything the
    caller's implementation chooses to raise) map to "source
    unavailable, skip reassignment" — the translate pass still emits
    the Symbol with the SCIP-derived stable_id rather than dropping it.
    """
    try:
        return source_reader(path)
    except (OSError, ValueError):
        return None


def reassign_rust_stable_ids(
    symbols: Iterable[Symbol],
    source_reader: SourceReader,
) -> List[Symbol]:
    """Return *symbols* with Rust function ``stable_id``s rust.py-compatible.

    For each input Symbol, the reassignment applies only when:

    - ``language == "rust"``
    - ``kind == "function"`` or ``kind == "method"``
    - ``span`` is present (SCIP Definition Occurrences always carry one)
    - ``source_reader(path)`` returns bytes
    - ``compute_rust_stable_id_from_source`` finds the function at the
      span and returns a non-None stable_id

    All other Symbols (non-Rust, non-function, span-less, source-less,
    or parity-lookup-failed) pass through with their original
    ``stable_id`` untouched. The return value is a new list — inputs
    are not mutated.
    """
    reassigned: List[Symbol] = []
    source_cache: dict[str, Optional[bytes]] = {}
    for sym in symbols:
        new_sym = sym
        if (
            sym.language == "rust"
            and sym.kind in {"function", "method"}
            and sym.span is not None
            and sym.path
        ):
            if sym.path not in source_cache:
                source_cache[sym.path] = _read_source(source_reader, sym.path)
            source = source_cache[sym.path]
            if source is not None:
                parity = compute_rust_stable_id_from_source(
                    source, sym.span.start_line, sym.span.end_line,
                    rel_path=sym.path,
                )
                if parity is not None:
                    new_sym = Symbol(
                        id=sym.id,
                        name=sym.name,
                        kind=sym.kind,
                        language=sym.language,
                        path=sym.path,
                        span=sym.span,
                        origin=sym.origin,
                        stable_id=parity,
                        signature=sym.signature,
                        meta=sym.meta,
                    )
        reassigned.append(new_sym)
    return reassigned


def translate_scip_to_hg(
    scip_bytes: bytes,
    source_reader: SourceReader,
    *,
    run_id: str = "",
    log: Optional[Callable[[str], None]] = None,
) -> tuple[List[Symbol], List[Edge]]:
    """Parse *scip_bytes* and return ``(symbols, edges)`` with parity.

    ``log`` receives one line when edge endpoints were refused as ambiguous
    (INV-sipod; :class:`~hypergumbo_core.scip.resolve.MonikerResolver`).

    The three core SCIP shim passes and the parity pass run in order:

    1. :func:`scip_index_to_symbols` to build the Symbol list.
    2. :func:`reassign_rust_stable_ids` to overwrite Rust function
       ``stable_id`` fields with rust.py-compatible values.
    3. :func:`scip_index_to_edges` for Relationship-derived edges.
    4. :func:`scip_index_to_call_edges` for Occurrence-ref edges via
       span-enclosure resolution.

    The Edge list is the concatenation of the two edge sources; callers
    expecting a deduplicated edge set should run the standard
    ``deduplicate_edges`` pass themselves (not done here — translate
    preserves the shim outputs verbatim so downstream passes can see
    the raw signal and ``deduplicate_edges`` is the only place the
    dedup rule lives).
    """
    index = scip_pb2.Index()
    index.ParseFromString(scip_bytes)
    symbols = reassign_rust_stable_ids(
        scip_index_to_symbols(index), source_reader,
    )
    # If no run_id is supplied (a library caller), create a local run so
    # Edge.__post_init__ enforcement passes. Its id is a well-formed UUID that
    # resolves to nothing: the local run is constructed, harvested for its id
    # and discarded. Production passes a real run (the analyzer threads one
    # through ``try_analyze_with_rust_analyzer``), and any caller that wants
    # provenance to flow into a parent run must do the same. Note the
    # asymmetry: ``Edge.__post_init__`` hard-raises on an empty value and
    # ``Symbol`` has no such guard, so without the stamping below the guarded
    # half would carry a confident wrong id while the unguarded half stayed
    # empty. (history: WI-higap, WI-didag)
    if not run_id:
        from hypergumbo_core.ir import AnalysisRun, PASS_VERSION, make_pass_id
        _scip_run = AnalysisRun.create(
            pass_id=make_pass_id("rust-scip-translate"),
            version=PASS_VERSION,
        )
        run_id = _scip_run.execution_id
    # Stamp SYMBOLS from the same id as the edges. This does not make a
    # fabricated id resolvable — nothing serializes the local run either way —
    # but it makes the two halves agree, so a dangling artifact is ONE fact a
    # reader can chase rather than two unrelated-looking ones. When the caller
    # supplied a real run (the production path) this stamps the symbols at the
    # source instead of relying on the orchestrator's empty-only backstop,
    # which a library caller bypassing the orchestrator never gets. Pure fill:
    # a producer that already stamped keeps its value. (history: WI-zabus,
    # WI-mosil)
    for _sym in symbols:
        if not _sym.origin_run_id:
            _sym.origin_run_id = run_id
    # Map each SCIP symbol string to the hypergumbo ``Symbol.id`` the shim
    # assigned it (the shim preserves the raw string in ``meta["scip_symbol"]``),
    # so edge endpoints reference real Symbols instead of raw SCIP descriptor
    # strings. Without this ``resolve_symbol`` map the edge builders default to
    # the raw string endpoint (``occ.symbol``), so every scip edge is dangling
    # (its src/dst match no ``Symbol.id``) — and the survey's endpoint-integrity
    # finalize drops the ENTIRE scip call graph while the scip Symbols survive,
    # a silent half-merge that leaves the rust-analyzer backend emitting zero
    # edges via ``hypergumbo survey``.
    #
    # INV-sipod: the map is per Document, not per moniker. rust-analyzer gives a
    # package's lib, bin and build-script roots one ``crate/`` moniker, and a
    # flat dict would keep the last record, crediting main.rs code to lib.rs.
    # ``None`` still tells the shim to skip the edge: a symbol no record
    # carries is external (a dependency / stdlib symbol, absent from the Symbol
    # set), and an ambiguous one is refused and counted.
    resolve = MonikerResolver(
        symbols,
        same_unit=same_crate(
            crate_owners(index, lambda p: _read_source(source_reader, p)),
        ),
    )

    edges: List[Edge] = []
    edges.extend(
        scip_index_to_edges(index, run_id=run_id, resolve_symbol=resolve)
    )
    edges.extend(
        scip_index_to_call_edges(index, run_id=run_id, resolve_symbol=resolve)
    )
    report = resolve.ambiguity_report()
    if report is not None and log is not None:
        log(report)
    return symbols, edges
