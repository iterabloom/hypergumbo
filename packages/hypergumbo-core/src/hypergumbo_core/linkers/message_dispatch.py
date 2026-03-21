# SPDX-License-Identifier: AGPL-3.0-or-later
"""Message dispatch linker for typed wire protocol message patterns.

Detects custom wire protocol dispatch patterns where message types flow
between sender and handler code. Creates ``message_dispatch`` edges
between send sites and receive/dispatch sites matched by message type name.

Two API surfaces are covered:

**JavaScript/TypeScript (string-discriminated messages):**
- Write (send): ``{ type: 'MSG_TYPE', ... }`` or ``{ action: 'MSG_TYPE', ... }``
  in object literals (typically passed to ``.send()`` or ``JSON.stringify()``)
- Read (dispatch): ``case 'MSG_TYPE':`` in switch statements, or
  ``msg.type === 'MSG_TYPE'`` / ``msg.type == 'MSG_TYPE'`` comparisons

**Rust (serde-tagged enums):**
- Write (type def): ``#[serde(rename = "msg_type")]`` on enum variants
  inside ``#[serde(tag = "...")]`` enums
- Read (match): ``EnumName::Variant { ... } =>`` match arms

Why This Design
---------------
Custom wire protocols (WebRTC signaling, Yjs sync, game state, etc.) use
typed messages that are structurally identical to pub/sub patterns: a sender
produces a message with a discriminator field, and a receiver dispatches on
that field. Standard call-graph analysis misses these because the coupling
is data-mediated (through the message type string), not through direct calls.
This linker matches senders to handlers by message type name.
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

PASS_ID = make_pass_id("message-dispatch-linker")

_DISPATCH_LANGUAGES = ("javascript", "typescript", "rust")

# ---- JS/TS patterns ----

# Write: object literal with type/action discriminator field.
# Matches: { type: 'MSG', ... } or { action: 'MSG', ... }
# Group 1: the message type string
_JS_SEND_PATTERN = re.compile(
    r"""(?:type|action)\s*:\s*['"]([a-zA-Z0-9_.\-:]+)['"]""",
)

# Read: case 'MSG_TYPE': in switch statements.
# Group 1: the message type string
_JS_CASE_PATTERN = re.compile(
    r"""case\s+['"]([a-zA-Z0-9_.\-:]+)['"]\s*:""",
)

# Read: msg.type === 'MSG_TYPE' or msg.type == 'MSG_TYPE'
# (Also matches .action ===)
# Group 1: the message type string
_JS_COMPARE_PATTERN = re.compile(
    r"""\.(?:type|action)\s*===?\s*['"]([a-zA-Z0-9_.\-:]+)['"]""",
)

# ---- Rust patterns ----

# Write: #[serde(rename = "msg_type")] on enum variants
# Group 1: the renamed message type string
_RUST_SERDE_RENAME_PATTERN = re.compile(
    r"""#\[serde\(rename\s*=\s*"([a-zA-Z0-9_.\-:]+)"\)\]""",
)

# Read: EnumName::Variant { ... } => or EnumName::Variant =>
# Group 1: the variant name (used as channel)
_RUST_MATCH_ARM_PATTERN = re.compile(
    r"""\b[A-Z][a-zA-Z0-9]*::([A-Z][a-zA-Z0-9]*)\b""",
)

# Quick bailout keywords
_BAILOUT_JS = ("type:", "action:", "case '", 'case "', ".type ===", ".type ==", ".action ===", ".action ==")
_BAILOUT_RUST = ("serde(rename", "Message::", "Msg::", "Command::", "Event::", "Request::", "Response::")


@dataclass
class DispatchSite:
    """A source location where a message dispatch pattern was detected."""

    kind: str       # "write" or "read"
    channel: str    # message type name
    file_path: str  # relative path
    line: int       # 1-indexed
    api: str        # "js_dispatch" or "rust_dispatch"


def _scan_file_for_dispatch_patterns(
    file_path: Path,
    rel_path: str,
    language: str,
) -> list[DispatchSite]:
    """Scan a source file for typed message dispatch patterns.

    Detects send-side object literals with type/action fields (JS/TS),
    switch/case dispatch (JS/TS), and serde-tagged enum patterns (Rust).
    """
    try:
        content = file_path.read_text(errors="replace")
    except OSError:  # pragma: no cover
        return []

    is_js_ts = language in ("javascript", "typescript")
    is_rust = language == "rust"

    # Quick bailout
    if is_js_ts and not any(kw in content for kw in _BAILOUT_JS):
        return []
    if is_rust and not any(kw in content for kw in _BAILOUT_RUST):
        return []

    sites: list[DispatchSite] = []
    lines = content.split("\n")

    for i, line_text in enumerate(lines):
        line_num = i + 1

        if is_js_ts:
            # Send-side: { type: 'MSG', ... }
            for m in _JS_SEND_PATTERN.finditer(line_text):
                sites.append(DispatchSite(
                    kind="write", channel=m.group(1),
                    file_path=rel_path, line=line_num, api="js_dispatch",
                ))

            # Receive-side: case 'MSG':
            for m in _JS_CASE_PATTERN.finditer(line_text):
                sites.append(DispatchSite(
                    kind="read", channel=m.group(1),
                    file_path=rel_path, line=line_num, api="js_dispatch",
                ))

            # Receive-side: msg.type === 'MSG'
            for m in _JS_COMPARE_PATTERN.finditer(line_text):
                sites.append(DispatchSite(
                    kind="read", channel=m.group(1),
                    file_path=rel_path, line=line_num, api="js_dispatch",
                ))

        elif is_rust:
            # Write: #[serde(rename = "msg_type")]
            for m in _RUST_SERDE_RENAME_PATTERN.finditer(line_text):
                sites.append(DispatchSite(
                    kind="write", channel=m.group(1),
                    file_path=rel_path, line=line_num, api="rust_dispatch",
                ))

            # Read: EnumName::Variant => (match arms)
            for m in _RUST_MATCH_ARM_PATTERN.finditer(line_text):
                # Only count as dispatch read if it looks like a match arm
                # (has => after it, or is preceded by match-like context)
                if "=>" in line_text:
                    sites.append(DispatchSite(
                        kind="read", channel=m.group(1),
                        file_path=rel_path, line=line_num, api="rust_dispatch",
                    ))

    return sites


def link_message_dispatch(
    repo_root: Path,
    symbols: list[Symbol],
) -> LinkerResult:
    """Link typed message senders to dispatch handlers across files.

    Matches send sites (writes) to handler sites (reads) by message type
    name within the same API surface (JS↔JS or Rust↔Rust).

    Args:
        repo_root: Repository root path.
        symbols: All symbols from all analyzers.

    Returns:
        LinkerResult with message_dispatch edges and synthetic symbols.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    result_edges: list[Edge] = []
    result_symbols: list[Symbol] = []

    # Collect unique file paths
    seen_paths: set[str] = set()
    file_paths: list[tuple[Path, str, str]] = []
    for sym in symbols:
        if sym.language not in _DISPATCH_LANGUAGES:
            continue
        if sym.path in seen_paths:  # pragma: no cover
            continue
        seen_paths.add(sym.path)
        abs_path = Path(sym.path)
        if not abs_path.is_absolute():
            abs_path = repo_root / sym.path
        file_paths.append((abs_path, sym.path, sym.language))

    # Scan all files
    all_writes: list[DispatchSite] = []
    all_reads: list[DispatchSite] = []

    for abs_path, rel_path, language in file_paths:
        if not abs_path.exists():
            continue
        sites = _scan_file_for_dispatch_patterns(abs_path, rel_path, language)
        for site in sites:
            if site.kind == "write":
                all_writes.append(site)
            else:
                all_reads.append(site)

    if not all_writes or not all_reads:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(edges=[], symbols=[], run=run)

    # Build read index by (api, channel) for efficient matching
    read_index: dict[tuple[str, str], list[DispatchSite]] = {}
    for read in all_reads:
        key = (read.api, read.channel)
        read_index.setdefault(key, []).append(read)

    seen_edges: set[tuple[str, int, str, int]] = set()
    seen_sym_ids: set[str] = set()

    for write in all_writes:
        matching_reads = read_index.get((write.api, write.channel), [])
        for read in matching_reads:
            if write.file_path == read.file_path:
                continue

            dedup = (write.file_path, write.line, read.file_path, read.line)
            if dedup in seen_edges:  # pragma: no cover
                continue
            seen_edges.add(dedup)

            lang = "typescript" if write.api == "js_dispatch" else "rust"
            pub_id = f"{lang}:{write.file_path}:{write.line}:0:{write.channel}:message_sender"
            sub_id = f"{lang}:{read.file_path}:{read.line}:0:{read.channel}:message_handler"

            if pub_id not in seen_sym_ids:
                seen_sym_ids.add(pub_id)
                result_symbols.append(Symbol(
                    id=pub_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=f"dispatch.send({write.channel})",
                    fingerprint=hashlib.sha256(pub_id.encode()).hexdigest()[:16],
                    kind="message_sender",
                    name=write.channel,
                    path=write.file_path,
                    language=lang,
                    span=Span(
                        start_line=write.line, end_line=write.line,
                        start_col=0, end_col=0,
                    ),
                    origin=PASS_ID,
                    meta={"dispatch_api": write.api, "channel": write.channel},
                    supply_chain_tier=2,
                    supply_chain_reason="message dispatch sender",
                ))

            if sub_id not in seen_sym_ids:
                seen_sym_ids.add(sub_id)
                result_symbols.append(Symbol(
                    id=sub_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=f"dispatch.handle({read.channel})",
                    fingerprint=hashlib.sha256(sub_id.encode()).hexdigest()[:16],
                    kind="message_handler",
                    name=read.channel,
                    path=read.file_path,
                    language=lang,
                    span=Span(
                        start_line=read.line, end_line=read.line,
                        start_col=0, end_col=0,
                    ),
                    origin=PASS_ID,
                    meta={"dispatch_api": read.api, "channel": read.channel},
                    supply_chain_tier=2,
                    supply_chain_reason="message dispatch handler",
                ))

            result_edges.append(Edge.create(
                src=pub_id,
                dst=sub_id,
                edge_type="message_dispatch",
                line=write.line,
                confidence=0.70,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="dispatch_pattern",
                access_mode="write",
                dest_access_mode="read",
                channel=write.channel,
            ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(
        edges=result_edges, symbols=result_symbols, run=run,
    )


@register_linker(
    "message-dispatch",
    priority=87,  # Near Yjs and crypto-flow linkers
    activation=LinkerActivation(always=True),
    requirements=[],
)
def message_dispatch_linker(ctx: LinkerContext) -> LinkerResult:
    """Run the message dispatch linker."""
    return link_message_dispatch(ctx.repo_root, ctx.symbols)
