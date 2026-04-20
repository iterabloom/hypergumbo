# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIP bytes → hypergumbo ``(Symbol, Edge)`` translation with rust.py parity.

How It Works
------------
``rust-analyzer scip`` emits a Protobuf-encoded ``scip.Index`` file
describing every defined symbol, every reference, and every
relationship in a Rust workspace. This module wraps the hypergumbo-core
SCIP shim (``hypergumbo_core.scip.*``) with two things the generic
shim cannot do on its own:

1. **Stable-ID parity with rust.py.** The core shim fills
   ``Symbol.stable_id`` with the raw SCIP symbol string. That string is
   correct for cross-SCIP-pass dedup but does not match the
   signature-derived stable_id rust.py computes for the same function
   in the same source file. Without reassignment, enabling this
   backend on a cached analysis double-counts every Rust symbol.
   :func:`reassign_rust_stable_ids` replaces the SCIP-derived
   ``stable_id`` with the rust.py-equivalent one whenever a Rust
   function span can be located in the caller-provided source reader,
   using
   :func:`hypergumbo_lang_mainstream.rust_scip.compute_rust_stable_id_from_source`.
   Non-Rust Symbols (SCIP can carry multiple languages in one index),
   non-function Rust Symbols (structs, modules, constants — rust.py
   has no signature-level parity for these), and Symbols whose source
   cannot be read pass through unchanged.

2. **One-shot translate.** :func:`translate_scip_to_hg` bundles the
   three core shim calls (``scip_index_to_symbols``,
   ``scip_index_to_edges``, ``scip_index_to_call_edges``) with the
   parity reassignment so Slice-B's analyzer wrapper can consume a
   single entry point.

Why This Design
---------------
WI-zakub (2026-04-17 mini trial) established that rust-analyzer leaves
``SymbolInformation.relationships`` empty; the primary edge source is
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
failures (``OSError``, ``FileNotFoundError``, arbitrary exceptions)
degrade to "skip this symbol's reassignment" rather than aborting the
whole translate — the SCIP-derived ``stable_id`` remains as a
best-effort fallback.
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional

from hypergumbo_core.ir import Edge, Symbol
from hypergumbo_core.scip._generated import scip_pb2
from hypergumbo_core.scip.calls import scip_index_to_call_edges
from hypergumbo_core.scip.edges import scip_index_to_edges
from hypergumbo_core.scip.index import scip_index_to_symbols
from hypergumbo_lang_mainstream.rust_scip import (
    compute_rust_stable_id_from_source,
)


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
) -> tuple[List[Symbol], List[Edge]]:
    """Parse *scip_bytes* and return ``(symbols, edges)`` with parity.

    The three core SCIP shim passes are invoked in order:

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
    edges: List[Edge] = []
    edges.extend(scip_index_to_edges(index))
    edges.extend(scip_index_to_call_edges(index))
    return symbols, edges
