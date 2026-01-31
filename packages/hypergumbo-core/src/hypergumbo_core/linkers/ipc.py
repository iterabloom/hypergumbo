"""IPC linker for detecting inter-process communication patterns.

This linker detects IPC patterns in JavaScript/TypeScript code and creates
message_send and message_receive edges for cross-process communication.

Detected Patterns
-----------------
Electron IPC:
- ipcRenderer.send('channel', data) -> message_send
- ipcRenderer.invoke('channel', data) -> message_send
- ipcRenderer.send(channelVar, data) -> message_send (variable channel)
- ipcMain.on('channel', handler) -> message_receive
- ipcMain.handle('channel', handler) -> message_receive
- ipcMain.handle(channelVar, handler) -> message_receive (variable channel)

Web Workers / postMessage:
- worker.postMessage(data) -> message_send
- window.postMessage(data, origin) -> message_send
- addEventListener('message', handler) -> message_receive

Channel Detection Strategy
--------------------------
Patterns can use either string literals or variables for channel names:
- Literal: ipcRenderer.send('open-file', data) -> exact channel 'open-file'
- Variable: ipcRenderer.send(CHANNEL, data) -> variable name 'CHANNEL'

For variable-based channels, we use heuristic matching:
- If sender uses `OPEN_CHANNEL` and receiver uses `OPEN_CHANNEL`, link them
- Confidence is lower for variable-based matches (0.65 vs 0.85)

How It Works
------------
1. Find all JavaScript/TypeScript files in the repository
2. Scan each file for IPC patterns using regex
3. Extract channel names (literals) or variable names from patterns
4. Create edges linking files with matching channels/variables

Why This Design
---------------
- Regex-based detection is fast and doesn't require tree-sitter
- Channel-based matching enables cross-file IPC graph construction
- Variable detection catches patterns missed by literal-only matching
- Separate linker keeps language analyzers focused on their language
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .registry import (
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
)

PASS_ID = "ipc-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class IpcPattern:
    """Represents a detected IPC pattern."""

    type: str  # 'send' or 'receive'
    channel: str  # Channel name (literal value or variable name, may be empty for postMessage)
    line: int  # Line number in source
    file_path: str  # Source file path
    pattern_type: str  # 'electron', 'postmessage', 'worker'
    channel_type: str = "literal"  # 'literal' or 'variable'


@dataclass
class IpcLinkResult:
    """Result of IPC linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# ============================================================================
# Common patterns for variable detection (shared with MQ linker pattern)
# ============================================================================

# Identifier pattern: matches variable names, constants, and simple attribute access
_IDENTIFIER = r"[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*"

# Channel argument pattern: matches either a string literal OR an identifier
# Group 1: string literal content (if literal)
# Group 2: identifier/variable name (if variable)
_CHANNEL_ARG = rf"(?:['\"]([^'\"]+)['\"]|({_IDENTIFIER}))"


# Regex patterns for IPC detection
# Electron IPC send patterns - matches both literals and variables
ELECTRON_SEND_PATTERN = re.compile(
    rf"ipcRenderer\s*\.\s*(send|invoke)\s*\(\s*{_CHANNEL_ARG}",
    re.MULTILINE,
)

# Electron IPC receive patterns - matches both literals and variables
ELECTRON_RECEIVE_PATTERN = re.compile(
    rf"ipcMain\s*\.\s*(on|handle)\s*\(\s*{_CHANNEL_ARG}",
    re.MULTILINE,
)

# postMessage patterns (window, worker, etc.)
POSTMESSAGE_PATTERN = re.compile(
    r"(\w+)\s*\.\s*postMessage\s*\(",
    re.MULTILINE,
)

# addEventListener('message', ...) pattern
MESSAGE_LISTENER_PATTERN = re.compile(
    r"addEventListener\s*\(\s*['\"]message['\"]",
    re.MULTILINE,
)


def _extract_channel_from_match(match: re.Match, literal_group: int, var_group: int) -> tuple[str, str]:
    """Extract channel and channel_type from a regex match.

    Args:
        match: Regex match object
        literal_group: Group index for string literal content
        var_group: Group index for variable name

    Returns:
        tuple of (channel_value, channel_type) where channel_type is 'literal' or 'variable'
    """
    literal = match.group(literal_group)
    variable = match.group(var_group)

    if literal:
        return (literal, "literal")
    elif variable:
        return (variable, "variable")
    else:
        return ("unknown", "variable")  # pragma: no cover


def detect_ipc_patterns(source: bytes, language: str) -> list[dict]:
    """Detect IPC patterns in source code.

    Args:
        source: Source code bytes
        language: Programming language ('javascript', 'typescript', etc.)

    Returns:
        List of detected patterns with type, channel, channel_type, and line info.
    """
    # Only process JavaScript/TypeScript
    if language not in ("javascript", "typescript"):
        return []

    patterns: list[dict] = []
    text = source.decode("utf-8", errors="replace")

    # Detect Electron ipcRenderer.send/invoke
    # Groups: 1=method, 2=literal channel, 3=variable channel
    for match in ELECTRON_SEND_PATTERN.finditer(text):
        method = match.group(1)  # 'send' or 'invoke'
        channel, channel_type = _extract_channel_from_match(match, 2, 3)
        line = text[:match.start()].count("\n") + 1
        patterns.append({
            "type": "send",
            "channel": channel,
            "channel_type": channel_type,
            "line": line,
            "pattern_type": "electron",
            "method": method,
        })

    # Detect Electron ipcMain.on/handle
    # Groups: 1=method, 2=literal channel, 3=variable channel
    for match in ELECTRON_RECEIVE_PATTERN.finditer(text):
        method = match.group(1)  # 'on' or 'handle'
        channel, channel_type = _extract_channel_from_match(match, 2, 3)
        line = text[:match.start()].count("\n") + 1
        patterns.append({
            "type": "receive",
            "channel": channel,
            "channel_type": channel_type,
            "line": line,
            "pattern_type": "electron",
            "method": method,
        })

    # Detect postMessage
    for match in POSTMESSAGE_PATTERN.finditer(text):
        obj = match.group(1)  # 'window', 'worker', variable name
        line = text[:match.start()].count("\n") + 1
        patterns.append({
            "type": "send",
            "channel": "",  # postMessage doesn't use named channels
            "channel_type": "literal",  # Not applicable but consistent
            "line": line,
            "pattern_type": "postmessage",
            "object": obj,
        })

    # Detect message event listeners
    for match in MESSAGE_LISTENER_PATTERN.finditer(text):
        line = text[:match.start()].count("\n") + 1
        patterns.append({
            "type": "receive",
            "channel": "",  # message events don't use named channels
            "channel_type": "literal",  # Not applicable but consistent
            "line": line,
            "pattern_type": "postmessage",
        })

    return patterns


def _find_js_files(repo_root: Path) -> Iterator[Path]:
    """Find all JavaScript/TypeScript files in the repository."""
    yield from find_files(repo_root, ["*.js", "*.ts", "*.jsx", "*.tsx", "*.mjs"])


def _get_language(file_path: Path) -> str:
    """Get language from file extension."""
    suffix = file_path.suffix.lower()
    if suffix in (".ts", ".tsx"):
        return "typescript"
    return "javascript"


def link_ipc(repo_root: Path) -> IpcLinkResult:
    """Link IPC patterns across files in a repository.

    Scans JavaScript/TypeScript files for IPC patterns and creates
    message_send and message_receive edges for matching channels.

    Args:
        repo_root: Root directory of the repository

    Returns:
        IpcLinkResult with edges and run metadata.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_patterns: list[IpcPattern] = []
    files_analyzed = 0
    files_skipped = 0

    # Scan all JS/TS files for IPC patterns
    for file_path in _find_js_files(repo_root):
        try:
            source = file_path.read_bytes()
            language = _get_language(file_path)
            patterns = detect_ipc_patterns(source, language)

            for p in patterns:
                all_patterns.append(IpcPattern(
                    type=p["type"],
                    channel=p["channel"],
                    line=p["line"],
                    file_path=str(file_path),
                    pattern_type=p["pattern_type"],
                    channel_type=p.get("channel_type", "literal"),
                ))

            files_analyzed += 1
        except (OSError, IOError):
            files_skipped += 1

    # Group patterns by channel
    send_by_channel: dict[str, list[IpcPattern]] = {}
    receive_by_channel: dict[str, list[IpcPattern]] = {}

    for p in all_patterns:
        if p.type == "send":
            if p.channel not in send_by_channel:
                send_by_channel[p.channel] = []
            send_by_channel[p.channel].append(p)
        else:
            if p.channel not in receive_by_channel:
                receive_by_channel[p.channel] = []
            receive_by_channel[p.channel].append(p)

    # Create symbols and edges for matching channels
    edges: list[Edge] = []
    symbols: list[Symbol] = []
    created_symbol_ids: set[str] = set()

    def _make_symbol_id(pattern: IpcPattern, channel: str) -> str:
        return f"ipc:{pattern.file_path}:{pattern.line}:{pattern.type}:{channel}"

    def _ensure_symbol(pattern: IpcPattern, channel: str) -> str:
        """Create symbol for IPC endpoint if not already created."""
        sym_id = _make_symbol_id(pattern, channel)
        if sym_id not in created_symbol_ids:
            symbols.append(Symbol(
                id=sym_id,
                name=f"ipc:{pattern.type}:{channel}",
                kind=f"ipc_{pattern.type}",
                language="javascript",
                path=pattern.file_path,
                span=Span(
                    start_line=pattern.line,
                    end_line=pattern.line,
                    start_col=0,
                    end_col=0,
                ),
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                meta={
                    "channel": channel,
                    "channel_type": pattern.channel_type,
                    "pattern_type": pattern.pattern_type,
                },
            ))
            created_symbol_ids.add(sym_id)
        return sym_id

    for channel, senders in send_by_channel.items():
        if not channel:  # Skip empty channel (postMessage without named channel)
            continue

        receivers = receive_by_channel.get(channel, [])
        for sender in senders:
            src_id = _ensure_symbol(sender, channel)
            for receiver in receivers:
                dst_id = _ensure_symbol(receiver, channel)
                # Confidence depends on whether channels are literal or variable
                is_variable_match = (
                    sender.channel_type == "variable" or receiver.channel_type == "variable"
                )
                confidence = 0.65 if is_variable_match else 0.85
                # Create edge from sender to receiver
                edge = Edge.create(
                    src=src_id,
                    dst=dst_id,
                    edge_type="message_send",
                    line=sender.line,
                    confidence=confidence,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="variable_match" if is_variable_match else "ipc_channel_match",
                )
                edge.meta = {
                    "channel": channel,
                    "channel_type": "variable" if is_variable_match else "literal",
                }
                edges.append(edge)

    # Also create edges for the receive side
    for channel, receivers in receive_by_channel.items():
        if not channel:
            continue

        senders = send_by_channel.get(channel, [])
        for receiver in receivers:
            src_id = _ensure_symbol(receiver, channel)
            for sender in senders:
                dst_id = _ensure_symbol(sender, channel)
                is_variable_match = (
                    sender.channel_type == "variable" or receiver.channel_type == "variable"
                )
                confidence = 0.65 if is_variable_match else 0.85
                edge = Edge.create(
                    src=src_id,
                    dst=dst_id,
                    edge_type="message_receive",
                    line=receiver.line,
                    confidence=confidence,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="variable_match" if is_variable_match else "ipc_channel_match",
                )
                edge.meta = {
                    "channel": channel,
                    "channel_type": "variable" if is_variable_match else "literal",
                }
                edges.append(edge)

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return IpcLinkResult(edges=edges, symbols=symbols, run=run)


# ---------------------------------------------------------------------------
# Linker Registry Integration
# ---------------------------------------------------------------------------


def _count_js_ts_files(ctx: LinkerContext) -> int:
    """Count JavaScript/TypeScript files in the repository."""
    count = 0
    for _ in _find_js_files(ctx.repo_root):
        count += 1
    return count


def _count_electron_patterns_in_code(ctx: LinkerContext) -> int:
    """Count files that might contain Electron IPC patterns.

    Looks for ipcRenderer or ipcMain in JS/TS symbols from analyzers.
    """
    count = 0
    for sym in ctx.symbols:
        if sym.language in ("javascript", "typescript"):
            # Look for Electron-related patterns
            name_lower = sym.name.lower()
            if "ipc" in name_lower or "electron" in name_lower:
                count += 1
    return count


IPC_REQUIREMENTS = [
    LinkerRequirement(
        name="js_ts_files",
        description="JavaScript/TypeScript files",
        check=_count_js_ts_files,
    ),
    LinkerRequirement(
        name="electron_patterns",
        description="Electron IPC patterns in code",
        check=_count_electron_patterns_in_code,
    ),
]


@register_linker(
    "ipc",
    priority=40,  # Run after analyzers
    description="Electron IPC and postMessage pattern linking",
    requirements=IPC_REQUIREMENTS,
)
def ipc_linker(ctx: LinkerContext) -> LinkerResult:
    """IPC linker for registry-based dispatch.

    This wraps link_ipc() to use the LinkerContext/LinkerResult interface.
    """
    result = link_ipc(ctx.repo_root)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
