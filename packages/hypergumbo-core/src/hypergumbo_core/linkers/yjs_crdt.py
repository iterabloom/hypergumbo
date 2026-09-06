# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: Yjs/CRDT reactive for detecting pub/sub patterns in Yjs-based codebases.

Detects data-mediated coupling through Yjs shared types (Y.Map, Y.Array, Y.Text,
Y.Doc) and Awareness (ephemeral state). Creates ``event_publishes`` edges
(tagged ``meta.channel_kind="crdt"``, ``meta.framework_dispatch="yjs_crdt"``)
between code that writes to shared state and code that observes it. (The
bespoke ``crdt_publishes`` type was folded onto ``event_publishes`` per the
audit-findings 0001/0014 consolidation.)

Three API surfaces are covered:

Detected Patterns
-----------------
**Raw Yjs API (shared types):**
- Write: ``yMap.set('key', value)``, ``yMap.delete('key')``
- Shared type access: ``doc.getMap('name')``, ``doc.getArray('name')``,
  ``doc.getText('name')``, ``doc.getXmlFragment('name')``
- Read: ``yMap.observe(callback)``, ``yMap.observeDeep(callback)``
- Doc-level: ``yDoc.on('update', handler)``, ``yDoc.on('subdocs', handler)``,
  ``yDoc.on('destroy', handler)``

**Yjs Awareness (ephemeral state):**
- Write: ``awareness.setLocalState(state)``, ``awareness.setLocalStateField('key', value)``
- Read: ``awareness.on('change', callback)``, ``awareness.on('update', callback)``

**BlockSuite (document model abstraction over Yjs):**
- Write: ``store.addBlock('flavour', ...)``, ``store.deleteBlock()``,
  ``store.transact(fn)``, ``defineBlockSchema({ flavour: 'x' })``
- Read: ``store.slots.blockUpdated.subscribe()``,
  ``store.slots.rootAdded.subscribe()``, ``store.slots.rootDeleted.subscribe()``,
  ``store.slots.yBlockUpdated.subscribe()``, ``store.slots.ready.subscribe()``,
  ``model.propsUpdated.subscribe()``

BlockSuite is an abstraction layer over Yjs used by editors like AFFiNE. Block
mutations map to Yjs operations internally, so tracing through BlockSuite's API
captures the same data flow as raw Yjs but at the application level.

Why This Design
---------------
Yjs CRDTs are the primary coupling mechanism in collaborative apps — virtually
all shared state flows through observation patterns, not direct function calls.
Standard call-graph analysis misses these dependencies. This linker creates
explicit edges for the data flow, enabling ``slice --dataflow`` to trace through
reactive CRDT state.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)
from ._text_filters import js_ts_language_from_path, read_masked_source

if TYPE_CHECKING:
    pass

PASS_ID = make_pass_id("yjs-crdt-linker")

# ---- Write patterns (publishers) ----

# Matches Yjs shared type mutations with literal key names:
#   yMap.set('key', value)
#   yMap.delete('key')
# Group 1: the key/channel name (for .set/.delete with string literal)
_YJS_WRITE_PATTERN = re.compile(
    r"""(?:"""
    r"""\.set\s*\(\s*['"]([a-zA-Z0-9_.\-:]+)['"]"""     # .set('key', value)
    r"""|"""
    r"""\.delete\s*\(\s*['"]([a-zA-Z0-9_.\-:]+)['"]"""   # .delete('key')
    r""")""",
)

# Matches Yjs doc-level shared type accessor calls:
#   doc.getMap('name')
#   doc.getArray('name')
#   doc.getText('name')
#   doc.getXmlFragment('name')
# These bind a named shared type from a Y.Doc — the name acts as the channel.
# Group 1: the shared type name
_YJS_SHARED_TYPE_PATTERN = re.compile(
    r"""\.get(?:Map|Array|Text|XmlFragment)\s*\(\s*['"]([a-zA-Z0-9_.\-:]+)['"]""",
)

# Matches Yjs awareness write patterns:
#   awareness.setLocalState(state)
#   awareness.setLocalStateField('key', value)
# Group 1: key name from setLocalStateField (None for setLocalState)
_AWARENESS_WRITE_PATTERN = re.compile(
    r"""(?:"""
    r"""\.setLocalStateField\s*\(\s*['"]([a-zA-Z0-9_.\-:]+)['"]"""
    r"""|"""
    r"""\.setLocalState\s*\("""
    r""")""",
)

# ---- BlockSuite patterns ----

# Matches BlockSuite block mutations:
#   store.addBlock('affine:paragraph', ...)
#   store.deleteBlock(model)
#   store.transact(() => { ... })
# Group 1: flavour string from addBlock (None for deleteBlock/transact)
_BLOCKSUITE_WRITE_PATTERN = re.compile(
    r"""(?:"""
    r"""\.addBlock\s*\(\s*['"]([a-zA-Z0-9_.\-:]+)['"]"""   # addBlock('flavour', ...)
    r"""|"""
    r"""\.deleteBlock\s*\("""                                # deleteBlock(model)
    r"""|"""
    r"""\.transact\s*\("""                                   # transact(() => { ... })
    r""")""",
)

# Matches defineBlockSchema({ flavour: 'x' }) — block schema definition.
# Group 1: the flavour string
_BLOCKSUITE_SCHEMA_PATTERN = re.compile(
    r"""defineBlockSchema\s*\(\s*\{[^}]*?"""
    r"""flavour\s*:\s*['"]([a-zA-Z0-9_.\-:]+)['"]""",
    re.DOTALL,
)

# Matches BlockSuite slot subscriptions (RxJS Subject-based):
#   store.slots.blockUpdated.subscribe(handler)
#   store.slots.rootAdded.subscribe(handler)
#   store.slots.rootDeleted.subscribe(handler)
#   model.propsUpdated.subscribe(handler)
_BLOCKSUITE_READ_PATTERN = re.compile(
    r"""(?:"""
    r"""\.slots\.(?:blockUpdated|rootAdded|rootDeleted|yBlockUpdated|ready)"""
    r"""\.subscribe\s*\("""
    r"""|"""
    r"""\.propsUpdated\.subscribe\s*\("""
    r""")""",
)

# ---- Read patterns (subscribers) ----

# Matches Yjs shared type observation:
#   yMap.observe(callback)
#   yMap.observeDeep(callback)
#   yDoc.on('update', handler)
_YJS_READ_PATTERN = re.compile(
    r"""(?:"""
    r"""\.observe(?:Deep)?\s*\("""
    r"""|"""
    r"""\.on\s*\(\s*['"](?:update|subdocs|destroy)['"]"""
    r""")""",
)

# Matches Yjs awareness observation:
#   awareness.on('change', callback)
#   awareness.on('update', callback)
_AWARENESS_READ_PATTERN = re.compile(
    r"""\.on\s*\(\s*['"](?:change|update)['"]""",
)


# Keywords for quick bailout — avoids scanning files without any Yjs/BlockSuite API.
_BAILOUT_KEYWORDS = (
    "observe", "setLocal", ".set(", ".delete(", ".on(",
    "getMap", "getArray", "getText", "getXmlFragment",
    "addBlock", "deleteBlock", "transact", "defineBlockSchema",
    ".slots.", "propsUpdated",
)


@dataclass
class YjsSite:
    """A source location where a Yjs pub/sub pattern was detected."""

    kind: str       # "write" or "read"
    channel: str    # key name, "awareness", "yjs", or "blocksuite"
    file_path: str  # relative path
    line: int       # 1-indexed
    api: str        # "yjs", "awareness", or "blocksuite"


def _scan_file_for_yjs_patterns(
    file_path: Path,
    rel_path: str,
) -> list[YjsSite]:
    """Scan a TS/JS file for Yjs write/read patterns.

    Returns a list of YjsSite objects for each detected pattern.
    """
    try:
        content = read_masked_source(file_path, errors="replace")
    except OSError:  # pragma: no cover
        return []

    # Quick bailout: skip files that don't mention Yjs/BlockSuite identifiers
    if not any(kw in content for kw in _BAILOUT_KEYWORDS):
        return []

    sites: list[YjsSite] = []
    lines = content.split("\n")

    # BlockSuite schema definitions can span multiple lines — scan full content
    for m in _BLOCKSUITE_SCHEMA_PATTERN.finditer(content):
        flavour = m.group(1)
        line_num = content[:m.start()].count("\n") + 1
        sites.append(YjsSite(
            kind="write", channel=flavour, file_path=rel_path,
            line=line_num, api="blocksuite",
        ))

    for i, line_text in enumerate(lines):
        line_num = i + 1

        # Yjs write patterns
        for m in _YJS_WRITE_PATTERN.finditer(line_text):
            channel = m.group(1) or m.group(2) or "yjs"
            sites.append(YjsSite(
                kind="write", channel=channel, file_path=rel_path,
                line=line_num, api="yjs",
            ))

        # Yjs shared type accessor patterns (doc.getMap, doc.getArray, etc.)
        for m in _YJS_SHARED_TYPE_PATTERN.finditer(line_text):
            channel = m.group(1)
            sites.append(YjsSite(
                kind="write", channel=channel, file_path=rel_path,
                line=line_num, api="yjs",
            ))

        # Awareness write patterns
        for m in _AWARENESS_WRITE_PATTERN.finditer(line_text):
            channel = m.group(1) or "awareness"
            sites.append(YjsSite(
                kind="write", channel=f"awareness.{channel}",
                file_path=rel_path, line=line_num, api="awareness",
            ))

        # BlockSuite write patterns (addBlock, deleteBlock, transact)
        for m in _BLOCKSUITE_WRITE_PATTERN.finditer(line_text):
            channel = m.group(1) or "blocksuite"
            sites.append(YjsSite(
                kind="write", channel=channel, file_path=rel_path,
                line=line_num, api="blocksuite",
            ))

        # BlockSuite read patterns (slots, propsUpdated)
        if _BLOCKSUITE_READ_PATTERN.search(line_text):
            sites.append(YjsSite(
                kind="read", channel="blocksuite", file_path=rel_path,
                line=line_num, api="blocksuite",
            ))

        # Yjs read patterns
        if _YJS_READ_PATTERN.search(line_text):
            sites.append(YjsSite(
                kind="read", channel="yjs", file_path=rel_path,
                line=line_num, api="yjs",
            ))

        # Awareness read patterns
        if _AWARENESS_READ_PATTERN.search(line_text):
            # Only count as awareness read if it looks like awareness context
            if "awareness" in line_text.lower():
                sites.append(YjsSite(
                    kind="read", channel="awareness",
                    file_path=rel_path, line=line_num, api="awareness",
                ))

    return sites


YJS_DEPENDENCY_NAMES: frozenset[str] = frozenset({
    "yjs", "y-protocols", "y-websocket",
})
"""Yjs ecosystem npm packages whose presence in package.json activates this
linker. A repo using Yjs MUST depend on ``yjs`` (the core package); the
other two are listed because they're the canonical standalone packages
that drive Yjs awareness/networking, and a real-world Yjs setup commonly
declares them directly. Other ``y-*`` ecosystem packages (y-monaco,
y-prosemirror, y-quill, y-codemirror, …) transitively depend on yjs,
so checking for yjs core is sufficient — but the additional two names
are kept here to be explicit and to match the WI-vurig prescription."""


def _repo_has_yjs_dependency(symbols: list[Symbol]) -> bool:
    """Manifest-presence gate (WI-vurig). True iff at least one symbol
    is an npm package.json dependency whose name is in
    ``YJS_DEPENDENCY_NAMES``.

    The json_config analyzer emits each package.json dependency as a
    ``Symbol(kind="dependency", language="json", name=<pkg>)`` (see
    ``hypergumbo_lang_mainstream.json_config._extract_dependencies``).
    A Rails+Vue customer-engagement app like chatwoot has many
    ``json`` symbols but none whose ``name`` matches the yjs ecosystem,
    so the gate fires and the linker skips text-pattern scanning entirely
    — eliminating the 68 false-positive crdt_publishes edges observed in
    DEEP cohort 1 reflect (2026-05-10).
    """
    for sym in symbols:
        if (
            sym.kind == "dependency"
            and sym.language == "json"
            and sym.name in YJS_DEPENDENCY_NAMES
        ):
            return True
    return False


def link_yjs_crdt(
    repo_root: Path,
    symbols: list[Symbol],
) -> LinkerResult:
    """Link Yjs CRDT publishers to subscribers across all JS/TS files.

    Args:
        repo_root: Repository root path.
        symbols: All symbols from all analyzers.

    Returns:
        LinkerResult with event_publishes edges (mechanism-tagged CRDT pub/sub).
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    result_edges: list[Edge] = []
    result_symbols: list[Symbol] = []

    # WI-vurig manifest-presence gate. Before the gate, the linker scanned
    # every .js / .ts file in the repo for text patterns like ".set(" /
    # ".observe(" / ".on(" and emitted crdt_publishes edges based on
    # write/read API matching. Those patterns are common Vue/Rails/Express
    # vocabulary; on chatwoot (no Yjs dependency) the scan produced 68
    # false-positive edges. Gating on a real yjs npm dependency cuts the
    # false positives to zero on non-Yjs repos while leaving Yjs repos
    # unaffected.
    if not _repo_has_yjs_dependency(symbols):
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(edges=[], symbols=[], run=run)

    # Collect unique JS/TS file paths
    seen_paths: set[str] = set()
    file_paths: list[tuple[Path, str]] = []
    for sym in symbols:
        if sym.language not in ("javascript", "typescript"):
            continue
        if sym.path in seen_paths:  # pragma: no cover
            continue
        seen_paths.add(sym.path)
        abs_path = Path(sym.path)
        if not abs_path.is_absolute():
            abs_path = repo_root / sym.path
        file_paths.append((abs_path, sym.path))

    # Scan all files for Yjs patterns
    all_writes: list[YjsSite] = []
    all_reads: list[YjsSite] = []

    for abs_path, rel_path in file_paths:
        if not abs_path.exists():
            continue
        sites = _scan_file_for_yjs_patterns(abs_path, rel_path)
        for site in sites:
            if site.kind == "write":
                all_writes.append(site)
            else:
                all_reads.append(site)

    if not all_writes or not all_reads:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(edges=[], symbols=[], run=run)

    # Match writers to readers by API surface.
    # For Yjs: specific key writes match generic yjs reads (observe covers all keys).
    # For awareness: specific field writes match generic awareness reads.
    seen_edges: set[tuple[str, int, str, int]] = set()
    seen_sym_ids: set[str] = set()

    for write in all_writes:
        for read in all_reads:
            # Same file writes/reads are not cross-component coupling
            if write.file_path == read.file_path:
                continue

            # Match by API surface: yjs writes match yjs reads,
            # awareness writes match awareness reads
            if write.api != read.api:
                continue

            dedup = (write.file_path, write.line, read.file_path, read.line)
            if dedup in seen_edges:  # pragma: no cover
                continue
            seen_edges.add(dedup)

            # WI-dovog: each site carries ITS OWN file's language (ADR-0031
            # Class B via js_ts_language_from_path), not a literal.
            pub_lang = js_ts_language_from_path(Path(write.file_path))
            sub_lang = js_ts_language_from_path(Path(read.file_path))
            pub_id = f"{pub_lang}:{write.file_path}:{write.line}:0:{write.channel}:crdt_publisher"
            sub_id = f"{sub_lang}:{read.file_path}:{read.line}:0:{read.channel}:crdt_subscriber"

            if pub_id not in seen_sym_ids:
                seen_sym_ids.add(pub_id)
                # ADR-0031 Class B: synthetic stand-in for a Yjs CRDT write.
                result_symbols.append(Symbol(
                    id=pub_id,
                    stable_id=None,
                    shape_id=None,
                    display_label=f"yjs.write({write.channel})",  # ADR-0032
                    fingerprint=hashlib.sha256(pub_id.encode()).hexdigest()[:16],
                    # ADR-0027 Phase 3 / audit-findings 0013: framework-role
                    # leak.
                    kind="function",
                    name=write.channel,
                    path=write.file_path,
                    language=None,
                    discovery_language=pub_lang,
                    protocol_origin="yjs_crdt",
                    span=Span(
                        start_line=write.line, end_line=write.line,
                        start_col=0, end_col=0,
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={
                        "yjs_api": write.api,
                        "channel": write.channel,
                        "framework_role": "event_publisher",
                    },
                    supply_chain_reason="synthetic Yjs CRDT publisher",
                ))

            if sub_id not in seen_sym_ids:
                seen_sym_ids.add(sub_id)
                # ADR-0031 Class B: synthetic stand-in for a Yjs CRDT observe.
                result_symbols.append(Symbol(
                    id=sub_id,
                    stable_id=None,
                    shape_id=None,
                    display_label=f"yjs.observe({read.channel})",  # ADR-0032
                    fingerprint=hashlib.sha256(sub_id.encode()).hexdigest()[:16],
                    # ADR-0027 Phase 3 / audit-findings 0013: framework-role
                    # leak.
                    kind="function",
                    name=read.channel,
                    path=read.file_path,
                    language=None,
                    discovery_language=sub_lang,
                    protocol_origin="yjs_crdt",
                    span=Span(
                        start_line=read.line, end_line=read.line,
                        start_col=0, end_col=0,
                    ),
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    meta={
                        "yjs_api": read.api,
                        "channel": read.channel,
                        "framework_role": "event_subscriber",
                    },
                    supply_chain_reason="synthetic Yjs CRDT subscriber",
                ))

            # ADR-0023 §6 Phase 3 / audit-findings 0001 (WI-vasik-jofiv):
            # CRDT publish IS publish; "crdt" is the channel kind.
            # Canonical 'event_publishes' + meta['channel_kind']='crdt'.
            #
            # ADR-0028 Phase 3 / audit-findings 0014: framework-dispatch leak.
            # Fold evidence_type to ast_call_direct + meta key.
            result_edges.append(Edge.create(
                src=pub_id,
                dst=sub_id,
                edge_type="event_publishes",
                line=write.line,
                confidence=0.80,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="ast_call_direct",
                access_mode="write",
                channel=write.channel,
                meta={
                    "channel_kind": "crdt",
                    "framework_dispatch": "yjs_crdt",
                },
                derived_from=[pub_id, sub_id],
            ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(
        edges=result_edges, symbols=result_symbols, run=run,
    )


@register_linker(
    "yjs-crdt-linker",
    priority=85,  # After framework linkers, before annotation convention
    activation=LinkerActivation(always=True),
    requirements=[],
    # CNF: Yjs is a JS/TS CRDT library.
    depends_on=[["javascript"]],
)
def yjs_crdt_linker(ctx: LinkerContext) -> LinkerResult:
    """Run the Yjs/CRDT reactive linker."""
    return link_yjs_crdt(ctx.repo_root, ctx.symbols)
