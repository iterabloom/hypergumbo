# SPDX-License-Identifier: AGPL-3.0-or-later
"""Protocol linker: IPC for detecting inter-process communication patterns.

This linker detects IPC patterns in JavaScript/TypeScript code and emits
canonical ``event_publishes`` edges (with ``meta["channel_kind"]="ipc"``)
for the send-side of cross-process communication, plus ``calls`` edges
(with ``meta["bridge_kind"]="context_bridge"``) for Electron contextBridge
mediation. The receive side is intentionally not emitted — see audit-
findings 0002.

Post-Phase 3 IPC fold (WI-hahap-farid; pre-fold edge_types were
``message_send`` / ``message_receive`` / ``bridge_invokes``):

Detected Patterns
-----------------
Electron IPC (renderer → main):
- ipcRenderer.send('channel', data)        -> event_publishes (channel_kind='ipc')
- ipcRenderer.invoke('channel', data)      -> event_publishes (channel_kind='ipc')
- ipcRenderer.sendSync('channel', data)    -> event_publishes (channel_kind='ipc')
- ipcRenderer.send(channelVar, data)       -> event_publishes (channel_kind='ipc'; variable)

Electron IPC (main → renderer):
- webContents.send('channel', data)        -> event_publishes (channel_kind='ipc')
- event.sender.send('channel', data)       -> event_publishes (channel_kind='ipc')

Electron IPC (receive side — main and renderer):
- ipcMain.on / .once / .handle /           -> detected and used as the DST of the forward
  .handleOnce
- ipcRenderer.on / .once                      edge; a receive symbol is emitted only for a
                                              receiver some sender joins (an unpaired
                                              ipcMain.handle gets no node). Only the
                                              converse-direction edge is dropped
                                              (DEPRECATE-NO-FOLD per audit-findings 0002:
                                              forward event_publishes captures the
                                              relationship, and slice walks reverse
                                              direction natively)

Electron contextBridge (preload → renderer):
- contextBridge.exposeInMainWorld('ns', { method: () => ipcRenderer.invoke('ch') })
  Detects bridge definitions, then scans for window.ns.method() calls in renderer
  files. Emits canonical ``calls`` edges with ``meta["bridge_kind"]="context_bridge"``
  from renderer call sites to the IPC send symbols in the preload file (post WI-mifor-
  vabul Phase 3 bridge/FFI; pre-fold edge_type was ``bridge_invokes``), enabling
  end-to-end traceability from UI code through the preload bridge into the main process
  handler. The edge's dst IS the send site's own node -- same (file, line, channel),
  so same id -- the one the channel join's ``event_publishes`` edge leaves, so
  renderer -> preload -> main is one connected path (WI-kokuv; it used to target a
  separate ``0-0`` stand-in with no outgoing edge). A bridge send through a custom
  ``ipcInvoke('ch')`` wrapper, which no ``ipcRenderer.*`` pattern detects, is added
  to the senders before the join and publishes to the receivers itself.

Electron IPC wrappers (WI-pumit):
- A same-repo function or method whose body forwards its first PARAMETER as
  the channel of any call above is a wrapper: podman-desktop's main-process
  ``ipcHandle(channel, l) { ipcMain.handle(channel, ...) }``, its preload's
  ``ipcInvoke(channel, ...a) { ipcRenderer.invoke(channel, ...a) }``, vscode's
  ``ValidatedIpcMain.on(channel, l) { electron.ipcMain.on(channel, ...) }``.
  Every call shape is one ``_ELECTRON_SITE_RULES`` row, read by both the site
  scan and the wrapper scan, so the two directions share one mechanism.
- A call to a wrapper, with a literal or same-file-constant channel, is a site
  of the forwarded call's direction at the call's line (``this.ipcHandle('ch',
  ...)`` is a receive site). The forwarding call inside the wrapper names a
  parameter, not a channel, and is no site: it used to join every other
  wrapper's same-named parameter on the identifier (``variable_match``).
- Scope: names match repo-wide, but a call must show it can reach a wrapper
  of that name -- a bare ``f('ch')`` in the wrapper's file or a file that
  imports ``f``; ``this.f('ch')`` inside the wrapper's class or in a class that
  declares an ``f`` member (dependency injection, podman's ``IPCHandle``);
  ``x.f('ch')`` where ``x`` is bound to ``new C()`` of the wrapper's class
  (vscode's ``validatedIpcMain``). A non-literal channel, a call in a comment
  or string, and a wrapper of a wrapper (not followed: one level) give no
  site. The scan is regex plus bracket matching over comment- and
  string-masked text, not a parse: an object-literal method wrapper's
  forwards are dropped, but no call form resolves to it.
- The contextBridge function-exposure path still accepts ``ipcInvoke('ch')`` /
  ``ipcSend('ch')`` by NAME (``_FUNC_BODY_IPC_PATTERN``), for a wrapper whose
  definition is not in the repo; where the definition is, both paths mint the
  same (file, line, channel) send node.

Web Workers / postMessage:
- worker.postMessage(data)                 -> detected, but NO edge and NO symbol: the
- window.postMessage(data, origin)            pattern names no channel, and the edge loop
                                              skips a call whose channel is empty. Pairing
                                              an unnamed postMessage with its listener needs
                                              a channel the syntax does not carry.
- addEventListener('message', handler)     -> detected, but NO edge and NO symbol, for
                                              the same reason as postMessage: it names
                                              no channel

Channel Detection Strategy
--------------------------
A channel is written as a string literal or as an identifier. The shared
``_name_args`` helper (WI-misod) classifies each site:
- Literal: ipcRenderer.send('open-file', data) -> channel 'open-file' (``literal``)
- Constant: const OPEN = 'open-file'; ipcRenderer.send(OPEN, data) -> channel
  'open-file' (``constant``; same-file module-scope string constant,
  ``channel_identifier`` keeps 'OPEN')
- Unresolved: ipcRenderer.send(config.channel, data) -> channel NOT known
  (``unresolved``; meta ``channel`` is None, ``channel_identifier`` keeps the text)

Senders join receivers on the channel VALUE (confidence 0.85) when both are
known; when either side is unresolved they join only on an equal IDENTIFIER
(``variable_match``, confidence 0.65). Edge ``channel_type`` labels the JOIN,
symbol ``channel_type`` the SITE.

How It Works
------------
1. Find the non-test JavaScript/TypeScript files in the repository
   (``find_non_test_files``); IPC sites in test files are not scanned
2. Scan each file for IPC patterns using regex
3. Extract channel literals, resolving identifiers to same-file string constants
4. Create edges linking sites with matching channel values (or, when a value
   is unknown, matching identifiers)
5. Detect contextBridge.exposeInMainWorld() wrappers in preload scripts
   (in the same scan as step 2, recording each exposure's send line, so its
   send site joins the step-4 channel match), then scan the files for
   window.namespace.method() calls and create canonical ``calls`` edges
   (``meta["bridge_kind"]="context_bridge"``; the pre-fold edge type was
   ``bridge_invokes``) linking those calls to the IPC send symbols. The same
   path also covers individually-exposed functions and custom ``ipcInvoke`` /
   ``ipcSend`` wrappers
6. Between steps 2 and 4, detect IPC wrappers per file, drop their forwarding
   calls, and add every call that can see a wrapper as a site (repo-wide; see
   "Electron IPC wrappers" above)

Why This Design
---------------
- Regex-based detection is fast and doesn't require tree-sitter
- Channel-based matching enables cross-file IPC graph construction
- Variable detection catches patterns missed by literal-only matching
- contextBridge resolution closes the "last mile" gap where renderer code
  calls a bridge method but the linker can't see through to the IPC channel
- Separate linker keeps language analyzers focused on their language
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..discovery import find_non_test_files
from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from ..taxonomy import JS_TS_LANGUAGES, extension_globs
from ._text_filters import js_ts_language_from_path, read_source_bytes
from ._name_args import (
    KIND_LITERAL,
    NAME_ARG_RE,
    ConstantResolver,
    NameArg,
    name_arg_from_match,
    pair_by_name,
)
from .registry import (
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
    always_on_unreviewed,
)
from ..pass_silence import silence_reason_for_candidates

PASS_ID = make_pass_id("ipc-linker")


#: ``IpcPattern.type`` -> the ``framework_role`` its stand-in carries. A table
#: rather than ``f"ipc_{type}"`` so the values are literal where the enclosure
#: pass's partition test can read them (WI-pokuz); phoenix_ipc shares it.
IPC_FRAMEWORK_ROLES: dict[str, str] = {"send": "ipc_send", "receive": "ipc_receive"}


@dataclass
class IpcPattern:
    """Represents a detected IPC pattern."""

    type: str  # 'send' or 'receive'
    channel: str  # Channel value when known, else the identifier written (empty for postMessage)
    line: int  # Line number in source
    file_path: str  # Source file path
    pattern_type: str  # 'electron', 'postmessage', 'worker'
    channel_type: str = KIND_LITERAL  # 'literal' | 'constant' | 'unresolved'
    channel_identifier: str | None = None  # identifier written at the site, if any

    @property
    def arg(self) -> NameArg:
        """The site's channel as a :class:`NameArg` (WI-misod)."""
        return NameArg(self.channel, self.channel_type, self.channel_identifier)


@dataclass
class IpcLinkResult:
    """Result of IPC linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# Channel argument pattern (shared, ``_name_args``): a string literal OR an
# identifier, as two consecutive capture groups.
_CHANNEL_ARG = NAME_ARG_RE


# Regex patterns for IPC detection
# Electron IPC send patterns - matches both literals and variables
# Covers: ipcRenderer.send, ipcRenderer.invoke, ipcRenderer.sendSync
ELECTRON_SEND_PATTERN = re.compile(
    rf"ipcRenderer\s*\.\s*(send|invoke|sendSync)\s*\(\s*{_CHANNEL_ARG}",
    re.MULTILINE,
)

# Electron IPC receive patterns - matches both literals and variables
# Covers: ipcMain.on, ipcMain.once, ipcMain.handle, ipcMain.handleOnce
ELECTRON_RECEIVE_PATTERN = re.compile(
    rf"ipcMain\s*\.\s*(on|once|handle|handleOnce)\s*\(\s*{_CHANNEL_ARG}",
    re.MULTILINE,
)

# Electron main-to-renderer push: webContents.send('channel', data)
# Covers: win.webContents.send, event.sender.send, <expr>.webContents.send
WEBCONTENTS_SEND_PATTERN = re.compile(
    rf"(?:webContents|sender)\s*\.\s*send\s*\(\s*{_CHANNEL_ARG}",
    re.MULTILINE,
)

# Electron renderer-side receive: ipcRenderer.on('channel', handler)
ELECTRON_RENDERER_RECEIVE_PATTERN = re.compile(
    rf"ipcRenderer\s*\.\s*(on|once)\s*\(\s*{_CHANNEL_ARG}",
    re.MULTILINE,
)


@dataclass(frozen=True)
class _SiteRule:
    """One Electron IPC call shape: its regex, direction and channel groups."""

    regex: re.Pattern[str]
    type: str  # 'send' | 'receive'
    literal_group: int
    identifier_group: int
    method: str | None = None  # fixed method label; None reads regex group 1

    def method_of(self, match: re.Match[str]) -> str:
        return self.method or match.group(1)


#: Every Electron IPC call shape, in detection order. The site scan
#: (:func:`detect_ipc_patterns`) and the wrapper scan (:func:`_ipc_wrappers`)
#: read the same table, so a call shape is a site exactly when a wrapper that
#: forwards its channel parameter to that shape is a wrapper.
_ELECTRON_SITE_RULES: tuple[_SiteRule, ...] = (
    _SiteRule(ELECTRON_SEND_PATTERN, "send", 2, 3),
    _SiteRule(ELECTRON_RECEIVE_PATTERN, "receive", 2, 3),
    _SiteRule(WEBCONTENTS_SEND_PATTERN, "send", 1, 2, method="webcontents_send"),
    _SiteRule(ELECTRON_RENDERER_RECEIVE_PATTERN, "receive", 2, 3),
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

# contextBridge.exposeInMainWorld('namespace', { ... }) pattern
# Captures the namespace (group 1) and the entire object body (group 2).
# Uses DOTALL so the object body can span multiple lines.
_CONTEXT_BRIDGE_PATTERN = re.compile(
    r"""contextBridge\s*\.\s*exposeInMainWorld\s*\(\s*"""
    r"""['"](\w+)['"]\s*,\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}""",
    re.DOTALL,
)

# Inside a bridge object body, match method definitions that call ipcRenderer:
#   methodName: (...) => ipcRenderer.invoke('channel', ...)
#   methodName(...) { return ipcRenderer.send('channel', ...) }
_BRIDGE_METHOD_PATTERN = re.compile(
    r"""(?P<method>\w+)\s*(?::\s*(?:\([^)]*\)\s*=>|function\s*\([^)]*\)\s*\{)|"""
    r"""\([^)]*\)\s*\{)\s*"""
    r"""(?:return\s+)?(?P<ipc>ipcRenderer)\s*\.\s*(?P<ipc_method>send|invoke|sendSync)"""
    r"""\s*\(\s*['"](?P<channel>[^'"]+)['"]""",
)


@dataclass(frozen=True)
class _BridgeSend:
    """The IPC send a contextBridge exposure forwards to, at its real site.

    ``line`` is the line of the token that starts the IPC call
    (``ipcRenderer`` or an ``ipcInvoke``-style wrapper) -- the same line
    :func:`detect_ipc_patterns` records for an ``ipcRenderer.*`` send there,
    so the bridge's send node and the detected one share an id (WI-kokuv).
    """

    channel: str
    ipc_method: str
    line: int


def _line_at(text: str, offset: int) -> int:
    """1-based line number of ``offset`` in ``text``."""
    return text.count("\n", 0, offset) + 1


def _bridge_wrapper_sends(text: str) -> list[tuple[str, dict[str, _BridgeSend]]]:
    """Object-literal contextBridge exposures, with each method's send site."""
    results: list[tuple[str, dict[str, _BridgeSend]]] = []
    for bridge_match in _CONTEXT_BRIDGE_PATTERN.finditer(text):
        body_start = bridge_match.start(2)
        methods: dict[str, _BridgeSend] = {}
        for method_match in _BRIDGE_METHOD_PATTERN.finditer(bridge_match.group(2)):
            methods[method_match.group("method")] = _BridgeSend(
                channel=method_match.group("channel"),
                ipc_method=method_match.group("ipc_method"),
                line=_line_at(text, body_start + method_match.start("ipc")),
            )
        if methods:
            results.append((bridge_match.group(1), methods))
    return results


def detect_context_bridge_wrappers(
    source: bytes,
) -> list[tuple[str, dict[str, tuple[str, str]]]]:
    """Detect contextBridge.exposeInMainWorld wrapper definitions.

    Parses preload scripts for patterns like::

        contextBridge.exposeInMainWorld('api', {
            openFile: (...args) => ipcRenderer.invoke('open-file', ...args),
            saveFile: (data) => ipcRenderer.send('save-file', data),
        });

    Args:
        source: Source code bytes.

    Returns:
        List of (namespace, methods_dict) tuples, where methods_dict maps
        method_name -> (channel_name, ipc_method).
    """
    text = source.decode("utf-8", errors="replace")
    return [
        (namespace, {name: (b.channel, b.ipc_method) for name, b in methods.items()})
        for namespace, methods in _bridge_wrapper_sends(text)
    ]


# contextBridge.exposeInMainWorld('funcName', async (...) => { ... ipcRenderer.invoke('channel') ... })
# Also matches custom wrappers like ipcInvoke('channel') which internally call ipcRenderer.invoke.
# Captures: (1) function name, (2) function body containing the IPC call, (3) channel name.
_CONTEXT_BRIDGE_FUNC_PATTERN = re.compile(
    r"""contextBridge\s*\.\s*exposeInMainWorld\s*\(\s*"""
    r"""['"](\w+)['"]\s*,\s*"""
    # Match function expression (arrow or regular), NOT an object literal
    r"""(?:async\s+)?(?:\([^)]*\)(?:\s*:\s*[^=]*?)?\s*=>|function\s*\([^)]*\)\s*)\s*"""
    r"""\{([^}]*(?:\{[^}]*\}[^}]*)*)\}""",
    re.DOTALL,
)

# Inside a function body, match ipcRenderer.invoke/send/sendSync('channel') or
# ipcInvoke('channel') / ipcSend('channel') custom wrappers.
_FUNC_BODY_IPC_PATTERN = re.compile(
    r"""(?:ipcRenderer\s*\.\s*(send|invoke|sendSync)|"""
    r"""ipc(Invoke|Send|SendSync))\s*\(\s*['"]([^'"]+)['"]""",
)


def detect_context_bridge_functions(
    source: bytes,
) -> list[tuple[str, str, str]]:
    """Detect individual function exposures via contextBridge.exposeInMainWorld.

    Some Electron apps expose individual functions rather than method objects::

        contextBridge.exposeInMainWorld('listContainers', async () => {
            return ipcInvoke('container-provider-registry:listContainers');
        });

    The renderer then calls ``window.listContainers()``.

    Also detects custom wrappers like ``ipcInvoke('channel')`` which are
    common in codebases that wrap ``ipcRenderer.invoke`` with error handling.

    Args:
        source: Source code bytes.

    Returns:
        List of (func_name, channel, ipc_method) tuples.
    """
    text = source.decode("utf-8", errors="replace")
    return [
        (func_name, b.channel, b.ipc_method)
        for func_name, b in _bridge_function_sends(text)
    ]


def _bridge_function_sends(text: str) -> list[tuple[str, _BridgeSend]]:
    """Single-function contextBridge exposures, with each one's send site."""
    results: list[tuple[str, _BridgeSend]] = []
    for match in _CONTEXT_BRIDGE_FUNC_PATTERN.finditer(text):
        ipc_match = _FUNC_BODY_IPC_PATTERN.search(match.group(2))
        if ipc_match:
            # ipcRenderer.method or ipcXxx wrapper
            if ipc_match.group(1):
                ipc_method = ipc_match.group(1)
            else:
                # Custom wrapper: ipcInvoke → invoke, ipcSend → send, etc.
                wrapper_name = ipc_match.group(2)
                ipc_method = wrapper_name[0].lower() + wrapper_name[1:]
            results.append((match.group(1), _BridgeSend(
                channel=ipc_match.group(3),
                ipc_method=ipc_method,
                line=_line_at(text, match.start(2) + ipc_match.start()),
            )))
    return results


def _channel_site(arg: NameArg) -> dict[str, str | None]:
    """The channel fields of a detected-pattern dict, from a :class:`NameArg`."""
    return {
        "channel": arg.label,
        "channel_type": arg.kind,
        "channel_identifier": arg.identifier,
    }


def detect_ipc_patterns(source: bytes, language: str) -> list[dict]:
    """Detect IPC patterns in source code.

    Args:
        source: Source code bytes
        language: Programming language ('javascript', 'typescript', etc.)

    Returns:
        List of detected patterns with type, channel, channel_type,
        channel_identifier and line info. ``channel`` is the value when known,
        else the identifier written at the site (``channel_type`` says which).
    """
    # Only process JavaScript/TypeScript
    if language not in ("javascript", "typescript"):
        return []

    patterns: list[dict] = []
    text = source.decode("utf-8", errors="replace")
    resolver = ConstantResolver(text, language)

    # Electron sites, one table for every direction (send / receive).
    for rule in _ELECTRON_SITE_RULES:
        for match in rule.regex.finditer(text):
            arg = name_arg_from_match(
                match, rule.literal_group, rule.identifier_group, resolver,
            )
            patterns.append({
                "type": rule.type,
                **_channel_site(arg),
                "line": _line_at(text, match.start()),
                "pattern_type": "electron",
                "method": rule.method_of(match),
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


# ---------------------------------------------------------------------------
# IPC wrappers (WI-pumit)
# ---------------------------------------------------------------------------
#
# A wrapper is a same-repo function or method whose body forwards its first
# PARAMETER as the channel of an Electron IPC call (any ``_ELECTRON_SITE_RULES``
# shape): podman-desktop's ``ipcHandle(channel, l) { ipcMain.handle(channel, ...) }``,
# its preload's ``ipcInvoke(channel, ...a) { ipcRenderer.invoke(channel, ...a) }``,
# vscode's ``ValidatedIpcMain.on(channel, l) { electron.ipcMain.on(channel, ...) }``.
# A call to a wrapper that can see it, with a literal or same-file-constant
# channel, is a site of the forwarded call's direction at the call's line; the
# forwarding call itself names a parameter, not a channel, and is no site.

_JS_IDENT = r"[A-Za-z_$][\w$]*"
_BRACKET = re.compile(r"[()\[\]{}]")
#: An arrow's expression body ends at a depth-0 ``;`` / ``,`` / closer, or at a
#: depth-0 newline that no continuation (``.x``, ``? :``, an operator) follows.
_EXPR_END = re.compile(r"[()\[\]{};,]|\n(?![ \t]*[.?:+\-*/&|])")
_NOT_A_DEFINITION = frozenset({
    "if", "for", "while", "switch", "catch", "with", "return", "function",
    "typeof", "await", "new", "super", "import", "export", "delete", "void",
    "yield", "constructor",
})
_CLASS_HEAD = re.compile(rf"\bclass\s+(?P<name>{_JS_IDENT})[^{{;]*\{{")
_FUNCTION_HEAD = re.compile(
    rf"\bfunction\b\s*\*?\s*(?P<name>{_JS_IDENT})\s*(?:<[^<>()]*>)?\s*\(",
)
_METHOD_HEAD = re.compile(
    r"^[ \t]*(?:(?:public|private|protected|static|async|override)\s+)*"
    rf"(?P<name>{_JS_IDENT})\s*(?:<[^<>()]*>)?\s*\(",
    re.MULTILINE,
)
#: ``const f = (`` / ``const f = function (`` / class field ``f = async (``,
#: or a bare-parameter arrow ``const f = channel =>`` (``param`` group).
_ASSIGN_HEAD = re.compile(
    rf"(?:\b(?P<decl>const|let|var)\s+|^[ \t]*(?:(?:public|private|protected"
    rf"|static|readonly)\s+)*)(?P<name>{_JS_IDENT})\s*(?::[^=;\n]*)?=\s*"
    rf"(?:async\s+)?(?:(?P<fn>function\b\s*\*?\s*(?:{_JS_IDENT})?\s*)?"
    rf"(?:<[^<>()]*>)?\s*\(|(?P<param>{_JS_IDENT})\s*=>)",
    re.MULTILINE,
)
_FIRST_PARAM = re.compile(rf"\s*(?:\.\.\.)?(?P<p>{_JS_IDENT})")
_BLOCK_AFTER_PARAMS = re.compile(r"\s*(?::[^{;=]*?)?\s*\{")
_ARROW_AFTER_PARAMS = re.compile(r"\s*(?::[^;{=]*?)?\s*=>\s*")


def _mask_js(text: str) -> str:
    """``text`` with comment and string-literal contents blanked to spaces.

    Same length and the same newlines, so offsets and line numbers carry
    over; quote characters are kept. The bracket scans below read the masked
    text, so a brace written inside a string or a comment is no delimiter.
    A template literal is blanked whole, ``${...}`` included; a regex literal
    is not recognised (its characters stay code).
    """
    out = list(text)
    i, n = 0, len(text)

    def blank(start: int, end: int) -> None:
        for k in range(start, end):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = text[i]
        if c == "/" and text.startswith(("//", "/*"), i):
            close = text.find("\n" if text[i + 1] == "/" else "*/", i + 2)
            end = n if close == -1 else close + (0 if text[i + 1] == "/" else 2)
            blank(i, end)
            i = end
        elif c in "'\"`":
            j = i + 1
            while j < n and text[j] != c and not (text[j] == "\n" and c != "`"):
                j += 2 if text[j] == "\\" else 1
            blank(i + 1, min(j, n))
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _close_of(masked: str, open_index: int) -> int:
    """Index of the bracket closing the one at ``open_index`` (len if none)."""
    depth = 0
    for m in _BRACKET.finditer(masked, open_index):
        depth += 1 if m.group() in "([{" else -1
        if depth == 0:
            return m.start()
    return len(masked)


def _expression_end(masked: str, start: int) -> int:
    """End of an arrow's expression body that starts at ``start``."""
    depth = 0
    for m in _EXPR_END.finditer(masked, start):
        token = m.group()
        if token in "([{":
            depth += 1
        elif depth == 0:
            return m.start()
        elif token in ")]}":
            depth -= 1
    return len(masked)


@dataclass(frozen=True)
class _IpcWrapper:
    """A function or method that forwards its channel parameter to IPC.

    ``owner`` says which call forms can reach it (see
    :func:`_wrapper_call_sites`): ``function`` (a bare ``f('ch')``),
    ``method`` (``this.f('ch')`` / ``instance.f('ch')``; ``class_name`` and
    ``class_span`` locate the class), or ``object`` (an object-literal method:
    no call form is resolved, but its forwards are still no sites).
    """

    name: str
    file_path: str
    owner: str  # 'function' | 'method' | 'object'
    param: str
    forwards: tuple[tuple[str, int], ...]  # (site type, line of the forwarding call)
    class_name: str | None = None
    class_span: tuple[int, int] = (0, 0)


def _ipc_wrappers(text: str, file_path: str) -> list[_IpcWrapper]:
    """The IPC wrappers ``text`` defines (WI-pumit)."""
    candidates = [
        (rule, m) for rule in _ELECTRON_SITE_RULES
        for m in rule.regex.finditer(text) if m.group(rule.identifier_group)
    ]
    if not candidates:
        return []
    masked = _mask_js(text)
    classes = [
        (m.group("name"), m.end() - 1, _close_of(masked, m.end() - 1))
        for m in _CLASS_HEAD.finditer(masked)
    ]

    def enclosing_class(offset: int) -> tuple[str, int, int] | None:
        inside = [c for c in classes if c[1] < offset < c[2]]
        return max(inside, key=lambda c: c[1]) if inside else None

    # (name, header offset, '(' offset -- or, for a bare-parameter arrow, the
    # offset after its '=>' --, bare param, owner hint, arrow?)
    heads: list[tuple[str, int, int, str | None, str, bool]] = []
    for m in _FUNCTION_HEAD.finditer(masked):
        heads.append((m.group("name"), m.start(), m.end() - 1, None, "function", False))
    for m in _METHOD_HEAD.finditer(masked):
        if m.group("name") not in _NOT_A_DEFINITION:
            heads.append((m.group("name"), m.start(), m.end() - 1, None, "member", False))
    for m in _ASSIGN_HEAD.finditer(masked):
        hint = "function" if m.group("decl") else "field"
        if m.group("param"):
            heads.append((m.group("name"), m.start(), m.end(), m.group("param"), hint, True))
        else:
            heads.append((m.group("name"), m.start(), m.end() - 1, None, hint,
                          not m.group("fn")))

    wrappers: list[_IpcWrapper] = []
    for name, head_start, open_index, bare_param, hint, arrow in heads:
        if bare_param is not None:
            param: str | None = bare_param
            after = open_index
        else:
            close = _close_of(masked, open_index)
            first = _FIRST_PARAM.match(masked, open_index + 1, close)
            param = first.group("p") if first else None
            joint = (_ARROW_AFTER_PARAMS if arrow else _BLOCK_AFTER_PARAMS).match(
                masked, close + 1,
            )
            if joint is None:
                continue  # a call, a declaration without a body, or an overload
            after = joint.end() - (0 if arrow else 1)
        if not param or param == "this":
            continue
        while after < len(masked) and masked[after] in " \t\r\n":
            after += 1
        if after < len(masked) and masked[after] == "{":
            body = (after, _close_of(masked, after))
        else:
            body = (after, _expression_end(masked, after))
        forwards = tuple(
            (rule.type, _line_at(text, m.start()))
            for rule, m in candidates
            if body[0] <= m.start() < body[1]
            and m.group(rule.identifier_group) == param
            and masked[m.start()] != " "
        )
        if not forwards:
            continue
        cls = enclosing_class(head_start)
        if hint == "function" or (hint == "field" and cls is None):
            owner = "function"
        elif cls is None:
            owner = "object"
        else:
            owner = "method"
        wrappers.append(_IpcWrapper(
            name=name, file_path=file_path, owner=owner, param=param,
            forwards=forwards,
            class_name=cls[0] if owner == "method" and cls else None,
            class_span=(cls[1], cls[2]) if owner == "method" and cls else (0, 0),
        ))
    return wrappers


def _imports_name(content: str, name: str) -> bool:
    """Whether ``content`` imports or requires a binding spelled ``name``.

    Name-level, not module-level: which module the binding comes from is not
    checked against the wrapper's file.
    """
    n = re.escape(name)
    return re.search(
        rf"\bimport\s+(?:type\s+)?(?:[\w$]+\s*,\s*)?(?:{n}\b|\{{[^}}]*\b{n}\b)"
        rf"|\{{[^}}]*\b{n}\b[^}}]*\}}\s*=\s*require\s*\(|\b{n}\s*=\s*require\s*\(",
        content,
    ) is not None


def _declares_member(content: str, name: str) -> bool:
    """Whether a class in ``content`` declares a field or parameter property
    ``name`` (``private readonly ipcHandle: IPCHandle``) -- the shape through
    which a dependency-injected wrapper reaches ``this.name(...)``."""
    n = re.escape(name)
    return re.search(
        rf"\b(?:private|public|protected|readonly)\s+(?:readonly\s+)?{n}\s*[?!]?\s*:",
        content,
    ) is not None


def _wrapper_call_sites(
    file_contents: dict[str, str], wrappers: list[_IpcWrapper],
) -> list[IpcPattern]:
    """Repo-wide: each call that can see a wrapper is a site (WI-pumit).

    Scope -- which calls "see" a wrapper. Names match repo-wide, because a
    main process routinely reaches its wrapper through dependency injection
    from another file (podman's ``this.ipcHandle`` in a class handed the
    method by an ``IPCHandle`` token), but each call form must show it can
    reach a wrapper of that name:

    * ``f('ch')`` -- ``f`` is a function wrapper defined in this file, or a
      binding this file imports as ``f``;
    * ``this.f('ch')`` -- ``f`` is a method wrapper and the call is inside
      its class, or this file declares a member ``f`` (the injected shape);
    * ``x.f('ch')`` -- ``f`` is a method of class ``C`` and ``x`` is bound
      to ``new C(...)`` somewhere in the repo.

    So ``emitter.on('data')`` stays no site even where a wrapper method is
    named ``on``, and a file's own same-named non-forwarding function is not
    mistaken for an imported wrapper. The channel must be a literal or a
    same-file constant: a parameter or dotted expression names no channel,
    and it is what a wrapper-of-a-wrapper forwards (not followed: one level).
    A call inside a comment or a string is no site.
    """
    callable_wrappers = [w for w in wrappers if w.owner != "object"]
    if not callable_wrappers:
        return []
    by_name: dict[str, list[_IpcWrapper]] = {}
    for w in callable_wrappers:
        by_name.setdefault(w.name, []).append(w)
    names = "|".join(re.escape(n) for n in sorted(by_name, key=len, reverse=True))
    call_re = re.compile(
        rf"(?:(?<![\w$.])(?P<recv>{_JS_IDENT})\s*\.\s*|(?<![\w$.]))"
        rf"(?P<name>{names})(?![\w$])\s*(?:<[^<>()]*>)?\s*\(\s*{NAME_ARG_RE}",
    )
    literal_group, identifier_group = call_re.groups - 1, call_re.groups

    instances: dict[str, set[str]] = {}
    class_names = {w.class_name for w in callable_wrappers if w.class_name}
    if class_names:
        alt = "|".join(re.escape(c) for c in sorted(class_names))
        new_re = re.compile(
            rf"(?<![\w$.])(?P<var>{_JS_IDENT})\s*(?::[^=;\n]*)?=\s*new\s+(?P<cls>{alt})\b",
        )
        for content in file_contents.values():
            for m in new_re.finditer(content):
                instances.setdefault(m.group("cls"), set()).add(m.group("var"))

    def visible(w: _IpcWrapper, recv: str | None, file_str: str, offset: int,
                content: str) -> bool:
        if recv is None:
            return w.owner == "function" and (
                w.file_path == file_str or _imports_name(content, w.name)
            )
        if w.owner != "method":
            return False
        if recv == "this":
            in_class = w.file_path == file_str and (
                w.class_span[0] < offset < w.class_span[1]
            )
            return in_class or _declares_member(content, w.name)
        return recv in instances.get(w.class_name or "", set())

    sites: list[IpcPattern] = []
    for file_str, content in file_contents.items():
        matches = list(call_re.finditer(content))
        if not matches:
            continue
        masked = _mask_js(content)
        language = _get_language(Path(file_str))
        resolver = ConstantResolver(content, language)
        seen: set[tuple[int, str, str]] = set()
        for m in matches:
            name_at = m.start("name")
            if masked[name_at] == " ":
                continue  # inside a comment or a string
            arg = name_arg_from_match(m, literal_group, identifier_group, resolver)
            if arg.value is None:
                continue
            line = _line_at(content, name_at)
            for w in by_name[m.group("name")]:
                if not visible(w, m.group("recv"), file_str, name_at, content):
                    continue
                for site_type, _forward_line in w.forwards:
                    if (line, site_type, arg.label) in seen:
                        continue
                    seen.add((line, site_type, arg.label))
                    sites.append(IpcPattern(
                        type=site_type,
                        channel=arg.label,
                        line=line,
                        file_path=file_str,
                        pattern_type="electron",
                        channel_type=arg.kind,
                        channel_identifier=arg.identifier,
                    ))
    return sites


def _find_js_files(repo_root: Path) -> Iterator[Path]:
    """Find the non-test JavaScript/TypeScript files in the repository."""
    yield from find_non_test_files(repo_root, extension_globs(*JS_TS_LANGUAGES))  # WI-hizon


def _get_language(file_path: Path) -> str:
    """Get language from file extension (INV-tofun: analyzer-parity JS/TS tag).

    Thin wrapper over the shared :func:`js_ts_language_from_path` helper, kept
    for call-site readability within this module.
    """
    return js_ts_language_from_path(file_path)


def link_ipc(repo_root: Path) -> IpcLinkResult:
    """Link IPC patterns across files in a repository.

    Scans JavaScript/TypeScript files for IPC patterns and creates
    ``event_publishes`` edges for matching channels (the pre-fold
    ``message_send`` / ``message_receive`` pair).

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

    # contextBridge exposures, collected in the same scan (one read per file)
    # because their send sites must be in ``senders`` BEFORE the channel join.
    # namespace -> { method_name: (preload_file, send) }
    bridge_methods: dict[str, dict[str, tuple[str, _BridgeSend]]] = {}
    # func_name -> (preload_file, send)
    bridge_funcs: dict[str, tuple[str, _BridgeSend]] = {}
    file_contents: dict[str, str] = {}  # file_path -> content (for Phase 3)

    # Scan all JS/TS files for IPC patterns
    for file_path in _find_js_files(repo_root):
        try:
            source = read_source_bytes(file_path)
            language = _get_language(file_path)
            patterns = detect_ipc_patterns(source, language)
            file_str = str(file_path)
            content = source.decode("utf-8", errors="replace")
            file_contents[file_str] = content
            for namespace, methods in _bridge_wrapper_sends(content):
                for method_name, send in methods.items():
                    bridge_methods.setdefault(namespace, {})[method_name] = (file_str, send)
            for func_name, send in _bridge_function_sends(content):
                bridge_funcs[func_name] = (file_str, send)

            for p in patterns:
                all_patterns.append(IpcPattern(
                    type=p["type"],
                    channel=p["channel"],
                    line=p["line"],
                    file_path=str(file_path),
                    pattern_type=p["pattern_type"],
                    channel_type=p.get("channel_type", KIND_LITERAL),
                    channel_identifier=p.get("channel_identifier"),
                ))

            files_analyzed += 1
        except (OSError, IOError) as e:
            files_skipped += 1
            run.record_failed_file(
                str(file_path.relative_to(repo_root)),
                f"{type(e).__name__}: {e}",
            )

    # WI-pumit: a wrapper's forwarding call names its parameter, not a
    # channel -- no site; each call that can see the wrapper is the site.
    wrappers = [
        w for file_str, content in file_contents.items()
        for w in _ipc_wrappers(content, file_str)
    ]
    forwarding = {
        (w.file_path, line, site_type, w.param)
        for w in wrappers for site_type, line in w.forwards
    }
    all_patterns = [
        p for p in all_patterns
        if (p.file_path, p.line, p.type, p.channel_identifier) not in forwarding
    ]
    detected = {(p.file_path, p.line, p.type, p.channel) for p in all_patterns}
    all_patterns.extend(
        site for site in _wrapper_call_sites(file_contents, wrappers)
        if (site.file_path, site.line, site.type, site.channel) not in detected
    )

    # Named-channel sites only: postMessage / 'message' listeners carry no
    # channel, and the join skips them (see the module docstring).
    senders = [pat for pat in all_patterns if pat.type == "send" and pat.channel]
    receivers = [pat for pat in all_patterns if pat.type != "send" and pat.channel]

    # Create symbols and edges for matching channels
    edges: list[Edge] = []
    symbols: list[Symbol] = []
    created_symbol_ids: set[str] = set()

    def _make_symbol_id(pattern: IpcPattern, channel: str) -> str:
        return (
            f"ipc:{pattern.file_path}:{pattern.line}-{pattern.line}"
            f":{pattern.type}:{channel}"
        )

    def _ensure_symbol(pattern: IpcPattern, channel: str) -> str:
        """Create symbol for IPC endpoint if not already created."""
        sym_id = _make_symbol_id(pattern, channel)
        if sym_id not in created_symbol_ids:
            # ADR-0027 Phase 3 / audit-findings 0013: f-string Symbol.kind
            # framework-role leak (was kind=f"ipc_{pattern.type}", expanding
            # to "ipc_send" or "ipc_receive"). Fold to canonical
            # kind="function" + meta["framework_role"] capturing the same
            # information dynamically. The framework_role meta key is
            # open-form per ADR-0024 §"Fold-residue discipline".
            # ADR-0031 Class B: synthetic stand-in for an IPC send/receive
            # site. Was LITERAL-HOST ("javascript"); now derives the host
            # language from the file extension.
            symbols.append(Symbol(
                id=sym_id,
                name=f"ipc:{pattern.type}:{channel}",
                kind="function",
                language=None,
                discovery_language=_get_language(Path(pattern.file_path)),
                protocol_origin="ipc",
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
                    # WI-misod: the channel VALUE -- None when the identifier
                    # written at the site could not be resolved.
                    "channel": pattern.arg.value,
                    "channel_type": pattern.channel_type,
                    "channel_identifier": pattern.channel_identifier,
                    "pattern_type": pattern.pattern_type,
                    "framework_role": IPC_FRAMEWORK_ROLES[pattern.type],
                },
            ))
            created_symbol_ids.add(sym_id)
        return sym_id

    # WI-kokuv: a bridge's send IS the send site the scan detected there --
    # the same (file, line, channel) gives the same id, so the bridge ``calls``
    # edge and the join's ``event_publishes`` edge meet on ONE node and
    # renderer -> preload -> main is a connected path. A bridge send no
    # ``ipcRenderer.*`` pattern detected (a custom ``ipcInvoke('ch')`` wrapper)
    # joins ``senders`` itself, so it, too, publishes to the receivers.
    sender_ids = {_make_symbol_id(s, s.channel) for s in senders}

    def _bridge_send_id(preload_file: str, send: _BridgeSend) -> str:
        site = IpcPattern(
            type="send",
            channel=send.channel,
            line=send.line,
            file_path=preload_file,
            pattern_type="electron",
            channel_type=KIND_LITERAL,
        )
        sym_id = _make_symbol_id(site, send.channel)
        if sym_id not in sender_ids:
            sender_ids.add(sym_id)
            senders.append(site)
            all_patterns.append(site)
        return sym_id

    # namespace -> { method_name: (channel, send symbol id) }
    bridge_maps: dict[str, dict[str, tuple[str, str]]] = {
        namespace: {
            method_name: (send.channel, _bridge_send_id(preload_file, send))
            for method_name, (preload_file, send) in methods.items()
        }
        for namespace, methods in bridge_methods.items()
    }
    # func_name -> (channel, send symbol id)
    bridge_func_map: dict[str, tuple[str, str]] = {
        func_name: (send.channel, _bridge_send_id(preload_file, send))
        for func_name, (preload_file, send) in bridge_funcs.items()
    }

    # Every named send site gets a symbol, joined or not.
    for sender in senders:
        _ensure_symbol(sender, sender.channel)

    # WI-misod: the shared ``pair_by_name`` joins on the channel VALUE when
    # both are known and on the IDENTIFIER only when one side is unresolved --
    # never an identifier's text against a channel string.
    for sender, receiver, join in pair_by_name(
        senders, receivers, lambda p: p.arg, lambda p: p.arg,
    ):
        src_id = _ensure_symbol(sender, sender.channel)
        dst_id = _ensure_symbol(receiver, receiver.channel)
        # Confidence depends on what the join compared: values (exact) or
        # identifiers (heuristic).
        is_variable_match = not join.on_value
        confidence = 0.65 if is_variable_match else 0.85
        # ADR-0023 §6 Phase 3 / audit-findings 0002 (WI-hahap-farid):
        # Electron renderer→main message exchange is the
        # publish-family shape; "ipc" is the channel kind.
        # Canonical 'event_publishes' + meta['channel_kind']='ipc'.
        # Pass meta via kwarg so Edge.create merges with dataflow
        # fields — assigning edge.meta afterward would wipe out
        # the dataflow meta fields (INV-forim).
        edge = Edge.create(
            src=src_id,
            dst=dst_id,
            edge_type="event_publishes",
            line=sender.line,
            confidence=confidence,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="variable_match" if is_variable_match else "ipc_channel_match",
            access_mode="write",
            channel=join.channel,
            meta={
                "channel_kind": "ipc",
                "channel_type": join.kind,
                "channel_identifier": join.identifier,
            },
            # derived-from consumed-none: both ends are minted from a file scan and joined
            #   on the channel
            derived_from=[],
        )
        edges.append(edge)

    # audit-findings 0002 (WI-hahap-farid): the converse-direction
    # message_receive edges are dropped — the forward
    # event_publishes edges above already capture the relationship,
    # and hypergumbo's slice handles reverse traversal natively
    # (no per-edge converse-direction emit needed). Same verdict
    # as audit-findings 0001's event_subscribes shape problem.

    # ---- Phase 3: contextBridge.exposeInMainWorld wrapper resolution ----
    # For each preload file with bridge definitions, scan other files for
    # window.<namespace>.<method>() calls and create calls edges
    # (meta.bridge_kind="context_bridge").

    # The exposures were collected in the scan above and their send ids
    # resolved before the join (``bridge_maps`` / ``bridge_func_map``).

    # Scan all files for window.<namespace>.<method>() calls
    seen_bridge_edges: set[tuple[str, str, str]] = set()  # (file, namespace, method)

    for file_str, content in file_contents.items():
        for namespace, method_map in bridge_maps.items():
            # Build pattern for this namespace: window.api.methodName(
            ns_call_pattern = re.compile(
                rf"window\s*\.\s*{re.escape(namespace)}\s*\.\s*(\w+)\s*\(",
            )
            for call_match in ns_call_pattern.finditer(content):
                method_name = call_match.group(1)
                if method_name not in method_map:
                    continue

                dedup_key = (file_str, namespace, method_name)
                if dedup_key in seen_bridge_edges:
                    continue
                seen_bridge_edges.add(dedup_key)

                channel, preload_send_id = method_map[method_name]
                call_line = content[:call_match.start()].count("\n") + 1

                # Relativize file path
                rel_path = file_str
                try:
                    rel_path = str(Path(file_str).relative_to(repo_root))
                except ValueError:  # pragma: no cover - files always under repo_root
                    pass

                # Create synthetic bridge caller symbol
                caller_id = (
                    f"ipc:{rel_path}:{call_line}-{call_line}"
                    f":{namespace}.{method_name}:bridge_caller"
                )
                if caller_id not in created_symbol_ids:
                    # ADR-0027 Phase 3 / audit-findings 0013: framework-role
                    # leak.
                    # ADR-0031 Class B: synthetic stand-in for an IPC bridge
                    # caller (window.<namespace>.<method>).
                    symbols.append(Symbol(
                        id=caller_id,
                        name=f"window.{namespace}.{method_name}",
                        kind="function",
                        language=None,
                        discovery_language=_get_language(Path(file_str)),
                        protocol_origin="ipc",
                        path=rel_path,
                        span=Span(
                            start_line=call_line,
                            end_line=call_line,
                            start_col=0,
                            end_col=0,
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        meta={
                            "namespace": namespace,
                            "bridge_method": method_name,
                            "channel": channel,
                            "framework_role": "ipc_bridge_caller",
                        },
                    ))
                    created_symbol_ids.add(caller_id)

                # dst: the preload send site's own node (WI-kokuv), which
                # every sender got above.

                # ADR-0023 §6 Phase 3 (WI-mifor-vabul): canonical
                # 'calls' + meta['bridge_kind']='context_bridge' for
                # Electron's contextBridge mediation.
                #
                # ADR-0028 Phase 3 / audit-findings 0014: framework-dispatch
                # leak; meta["framework_dispatch"]="electron_context_bridge".
                edge = Edge.create(
                    src=caller_id,
                    dst=preload_send_id,
                    edge_type="calls",
                    line=call_line,
                    confidence=0.80,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_call_direct",
                    data_direction="src_to_dst",
                    channel=channel,
                    meta={
                        "bridge_kind": "context_bridge",
                        "method": method_name,
                        "namespace": namespace,
                        "framework_dispatch": "electron_context_bridge",
                    },
                    # derived-from consumed-none: both ends are minted from a file scan and joined
                    #   on namespace/method
                    derived_from=[],
                )
                edges.append(edge)

    # Phase 3b: Scan for window.<funcName>() calls from individual function exposures
    seen_func_edges: set[tuple[str, str]] = set()  # (file, func_name)

    for file_str, content in file_contents.items():
        for func_name, (channel, preload_send_id) in bridge_func_map.items():
            # Match window.funcName( but NOT window.funcName.something(
            func_call_pattern = re.compile(
                rf"window\s*\.\s*{re.escape(func_name)}\s*\(",
            )
            for call_match in func_call_pattern.finditer(content):
                dedup_key = (file_str, func_name)
                if dedup_key in seen_func_edges:
                    continue
                seen_func_edges.add(dedup_key)

                call_line = content[:call_match.start()].count("\n") + 1

                rel_path = file_str
                try:
                    rel_path = str(Path(file_str).relative_to(repo_root))
                except ValueError:  # pragma: no cover
                    pass

                caller_id = (
                    f"ipc:{rel_path}:{call_line}-{call_line}"
                    f":{func_name}:bridge_caller"
                )
                if caller_id not in created_symbol_ids:
                    # ADR-0027 Phase 3 / audit-findings 0013: framework-role
                    # leak.
                    # ADR-0031 Class B: synthetic stand-in for an IPC bridge
                    # caller (window.<func>).
                    symbols.append(Symbol(
                        id=caller_id,
                        name=f"window.{func_name}",
                        kind="function",
                        language=None,
                        discovery_language=_get_language(Path(file_str)),
                        protocol_origin="ipc",
                        path=rel_path,
                        span=Span(
                            start_line=call_line,
                            end_line=call_line,
                            start_col=0,
                            end_col=0,
                        ),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        meta={
                            "bridge_function": func_name,
                            "channel": channel,
                            "framework_role": "ipc_bridge_caller",
                        },
                    ))
                    created_symbol_ids.add(caller_id)

                # ADR-0023 §6 Phase 3 (WI-mifor-vabul): canonical
                # 'calls' + meta['bridge_kind']='context_bridge'.
                #
                # ADR-0028 Phase 3 / audit-findings 0014: framework-dispatch
                # leak; meta["framework_dispatch"]="electron_context_bridge".
                edge = Edge.create(
                    src=caller_id,
                    dst=preload_send_id,
                    edge_type="calls",
                    line=call_line,
                    confidence=0.80,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_call_direct",
                    data_direction="src_to_dst",
                    channel=channel,
                    meta={
                        "bridge_kind": "context_bridge",
                        "function": func_name,
                        "framework_dispatch": "electron_context_bridge",
                    },
                    # derived-from consumed-none: both ends are minted from a file scan and joined
                    #   on the function name
                    derived_from=[],
                )
                edges.append(edge)

    run.silence_reason = silence_reason_for_candidates(all_patterns)
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
        languages=("javascript", "typescript"),
    ),
    LinkerRequirement(
        name="electron_patterns",
        description="Electron IPC patterns in code",
        check=_count_electron_patterns_in_code,
        languages=("javascript", "typescript"),
    ),
]


@register_linker(
    "ipc-linker",
    priority=40,  # Run after analyzers
    description="Electron IPC and postMessage pattern linking",
    requirements=IPC_REQUIREMENTS,
    # CNF: Electron IPC and browser postMessage are JS/TS-only.
    depends_on=[["javascript"]],
    activation=always_on_unreviewed(),
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
