# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIP → hypergumbo IR for scip-python output (WI-nanom).

Three passes of the shared importer, plus two things scip-python's output
needs that rust-analyzer's did not — measured on hypergumbo-core's own tree
before this module was written (lab notebook 2026-09-19 12:15):

1. **Drop what the syntax arm never models.** scip-python emits a global
   symbol for every PARAMETER (``f().(x)`` — 4,634 on 179 files) and one
   META ``__init__:`` declaration per module. The ``python`` analyzer emits
   neither, so they could pair with nothing and would sit in the artifact
   as thousands of one-sided records. The module is already the file node.

2. **Keep the receiver's module on external callees.** The shared edge
   builders drop a reference whose target is not an in-repo Symbol. That is
   right for rust-analyzer, which resolved nothing outside the crate; it
   throws away exactly what pyright is for. Of the syntax arm's 4,133
   method-call stubs whose receiver module it could not determine,
   scip-python resolved 3,973 (96.1%) — 3,840 of them to the stdlib. Those
   become external ``calls`` edges here, on the id shape the syntax arm
   mints when IT knows the module (``python:pathlib.Path:0-0:glob:unresolved``,
   ``ExternalRef(module_path="pathlib.Path", name="glob")``, via
   :func:`hypergumbo_core.ir.format_legacy_dst`), so the two arms are
   comparable at every call site and a consumer keying on the module —
   the I/O-boundary catalogue behind ``verify-claims`` — can read it.

Only METHOD-descriptor externals (callables) are kept; an external
``references`` edge (a type annotation naming ``Path``) is noise for a call
graph and the syntax arm emits nothing to pair it with. ``call_construct``
is stamped ``method`` when the descriptor has a class parent and
``function`` otherwise, the same vocabulary the syntax arm uses.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from hypergumbo_core.ir import Edge, ExternalRef, Symbol, format_legacy_dst
from hypergumbo_core.scip._generated import scip_pb2
from hypergumbo_core.scip.calls import scip_index_to_call_edges
from hypergumbo_core.scip.descriptor import DescriptorKind, parse_scip_symbol
from hypergumbo_core.scip.edges import scip_index_to_edges
from hypergumbo_core.scip.index import scip_index_to_symbols

#: Symbol kinds the importer mints for descriptors the syntax arm never models.
DROPPED_KINDS = frozenset({"parameter", "declaration"})

#: The generated protobuf module carries no type information; read it as Any
#: (the same accommodation the Rust translation makes).
_generated: Any = scip_pb2


def _external_ref(scip_symbol: str) -> Optional[Tuple[ExternalRef, str]]:
    """``(ExternalRef, call_construct)`` for an external METHOD descriptor, else None.

    ``scip-python python python-stdlib 3.11 pathlib/Path#glob().`` →
    ``ExternalRef("python", "pathlib.Path", "glob")``, ``"method"``;
    ``scip-python python python-stdlib 3.11 re/compile().`` →
    ``ExternalRef("python", "re", "compile")``, ``"function"``.
    """
    try:
        parsed = parse_scip_symbol(scip_symbol)
    except ValueError:
        return None
    descriptors = parsed.descriptors
    if not descriptors or descriptors[-1].kind is not DescriptorKind.METHOD:
        return None
    module_path = ".".join(d.name for d in descriptors[:-1])
    if not module_path:
        return None
    has_class_parent = any(d.kind is DescriptorKind.TYPE for d in descriptors[:-1])
    return (
        ExternalRef(lang="python", module_path=module_path, name=descriptors[-1].name),
        "method" if has_class_parent else "function",
    )


def translate_scip_python_to_hg(
    scip_bytes: bytes, *, run_id: str,
) -> Tuple[List[Symbol], List[Edge]]:
    """Parse ``scip_bytes`` and return ``(symbols, edges)``.

    ``run_id`` is the analyzer's own run: every Symbol and Edge names it, so
    the artifact's provenance resolves to a serialized run (WI-didag).
    """
    index = _generated.Index()
    index.ParseFromString(scip_bytes)
    symbols = [s for s in scip_index_to_symbols(index) if s.kind not in DROPPED_KINDS]
    for symbol in symbols:
        symbol.origin_run_id = run_id

    id_by_scip_symbol: Dict[str, str] = {
        (sym.meta or {}).get("scip_symbol", ""): sym.id
        for sym in symbols
        if sym.meta and sym.meta.get("scip_symbol")
    }
    external: Dict[str, Tuple[ExternalRef, str]] = {}

    def _resolve(scip_symbol: str) -> Optional[str]:
        # In-repo: the defining Symbol's id. External callable: the syntax
        # arm's module-qualified unresolved id, remembered so the edge can be
        # given its structured dst_ref below. Anything else: skip the edge.
        internal = id_by_scip_symbol.get(scip_symbol)
        if internal is not None:
            return internal
        if scip_symbol in external:
            return format_legacy_dst(external[scip_symbol][0])
        ref = _external_ref(scip_symbol)
        if ref is None:
            return None
        external[scip_symbol] = ref
        return format_legacy_dst(ref[0])

    edges: List[Edge] = []
    edges.extend(scip_index_to_edges(index, run_id=run_id, resolve_symbol=_resolve))
    edges.extend(scip_index_to_call_edges(index, run_id=run_id, resolve_symbol=_resolve))
    by_dst = {format_legacy_dst(ref): (ref, construct) for ref, construct in external.values()}
    kept: List[Edge] = []
    for edge in edges:
        hit = by_dst.get(edge.dst)
        if hit is None:
            kept.append(edge)
            continue
        if edge.edge_type != "calls":
            continue  # an external non-call reference: nothing to pair, nothing to type
        ref, construct = hit
        edge.dst_ref = ref
        edge.is_resolved = False
        edge.meta = {**(edge.meta or {}), "call_construct": construct}
        kept.append(edge)
    return symbols, kept


SourceReader = Callable[[str], Optional[bytes]]
