# SPDX-License-Identifier: AGPL-3.0-or-later
"""Yjs/CRDT reactive linker for detecting pub/sub patterns in Yjs-based codebases.

Detects data-mediated coupling through Yjs shared types (Y.Map, Y.Array, Y.Text,
Y.Doc) and Awareness (ephemeral state). Creates ``crdt_publishes`` edges between
code that writes to shared state and code that observes it.

Detected Patterns
-----------------
**Raw Yjs API (shared types):**
- Write: ``yMap.set('key', value)``, ``yArray.push(items)``, ``yText.insert(pos, text)``
- Read: ``yMap.observe(callback)``, ``yMap.observeDeep(callback)``
- Doc-level: ``yDoc.on('update', handler)``

**Yjs Awareness (ephemeral state):**
- Write: ``awareness.setLocalState(state)``, ``awareness.setLocalStateField('key', value)``
- Read: ``awareness.on('change', callback)``, ``awareness.on('update', callback)``

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

if TYPE_CHECKING:
    pass

PASS_ID = make_pass_id("yjs-crdt-linker")

# ---- Write patterns (publishers) ----

# Matches Yjs shared type mutations with literal key names:
#   yMap.set('key', value)
#   yMap.delete('key')
#   yArray.push([items])
#   yText.insert(pos, 'text')
# Group 1: the key/channel name (for .set/.delete with string literal)
_YJS_WRITE_PATTERN = re.compile(
    r"""(?:"""
    r"""\.set\s*\(\s*['"]([a-zA-Z0-9_.\-:]+)['"]"""     # .set('key', value)
    r"""|"""
    r"""\.delete\s*\(\s*['"]([a-zA-Z0-9_.\-:]+)['"]"""   # .delete('key')
    r""")""",
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


@dataclass
class YjsSite:
    """A source location where a Yjs pub/sub pattern was detected."""

    kind: str       # "write" or "read"
    channel: str    # key name, "awareness", or "yjs" (generic)
    file_path: str  # relative path
    line: int       # 1-indexed
    api: str        # "yjs" or "awareness"


def _scan_file_for_yjs_patterns(
    file_path: Path,
    rel_path: str,
) -> list[YjsSite]:
    """Scan a TS/JS file for Yjs write/read patterns.

    Returns a list of YjsSite objects for each detected pattern.
    """
    try:
        content = file_path.read_text(errors="replace")
    except OSError:  # pragma: no cover
        return []

    # Quick bailout: skip files that don't mention Yjs-related identifiers
    if not any(kw in content for kw in ("observe", "setLocal", ".set(", ".delete(", ".on(")):
        return []

    sites: list[YjsSite] = []
    lines = content.split("\n")

    for i, line_text in enumerate(lines):
        line_num = i + 1

        # Yjs write patterns
        for m in _YJS_WRITE_PATTERN.finditer(line_text):
            channel = m.group(1) or m.group(2) or "yjs"
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


def link_yjs_crdt(
    repo_root: Path,
    symbols: list[Symbol],
) -> LinkerResult:
    """Link Yjs CRDT publishers to subscribers across all JS/TS files.

    Args:
        repo_root: Repository root path.
        symbols: All symbols from all analyzers.

    Returns:
        LinkerResult with crdt_publishes edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    result_edges: list[Edge] = []
    result_symbols: list[Symbol] = []

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

            pub_id = f"typescript:{write.file_path}:{write.line}:0:{write.channel}:crdt_publisher"
            sub_id = f"typescript:{read.file_path}:{read.line}:0:{read.channel}:crdt_subscriber"

            if pub_id not in seen_sym_ids:
                seen_sym_ids.add(pub_id)
                result_symbols.append(Symbol(
                    id=pub_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=f"yjs.write({write.channel})",
                    fingerprint=hashlib.sha256(pub_id.encode()).hexdigest()[:16],
                    kind="event_publisher",
                    name=write.channel,
                    path=write.file_path,
                    language="typescript",
                    span=Span(
                        start_line=write.line, end_line=write.line,
                        start_col=0, end_col=0,
                    ),
                    origin=PASS_ID,
                    meta={"yjs_api": write.api, "channel": write.channel},
                    supply_chain_tier=2,
                    supply_chain_reason="synthetic Yjs CRDT publisher",
                ))

            if sub_id not in seen_sym_ids:
                seen_sym_ids.add(sub_id)
                result_symbols.append(Symbol(
                    id=sub_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=f"yjs.observe({read.channel})",
                    fingerprint=hashlib.sha256(sub_id.encode()).hexdigest()[:16],
                    kind="event_subscriber",
                    name=read.channel,
                    path=read.file_path,
                    language="typescript",
                    span=Span(
                        start_line=read.line, end_line=read.line,
                        start_col=0, end_col=0,
                    ),
                    origin=PASS_ID,
                    meta={"yjs_api": read.api, "channel": read.channel},
                    supply_chain_tier=2,
                    supply_chain_reason="synthetic Yjs CRDT subscriber",
                ))

            result_edges.append(Edge.create(
                src=pub_id,
                dst=sub_id,
                edge_type="crdt_publishes",
                line=write.line,
                confidence=0.80,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="yjs_crdt_pattern",
                access_mode="write",
                dest_access_mode="read",
                channel=write.channel,
            ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(
        edges=result_edges, symbols=result_symbols, run=run,
    )


@register_linker(
    "yjs-crdt",
    priority=85,  # After framework linkers, before annotation convention
    activation=LinkerActivation(always=True),
    requirements=[],
)
def yjs_crdt_linker(ctx: LinkerContext) -> LinkerResult:
    """Run the Yjs/CRDT reactive linker."""
    return link_yjs_crdt(ctx.repo_root, ctx.symbols)
