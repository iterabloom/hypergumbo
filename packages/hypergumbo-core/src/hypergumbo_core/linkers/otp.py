# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: OTP GenServer dispatch for Elixir and Erlang.

Bridges GenServer.call/cast call sites to their handle_call/handle_cast
handler functions. In OTP, GenServer dispatches messages at runtime, so
static analysis can't see the connection between a call site and the
handler that processes it. This linker closes that gap.

Supported Languages
-------------------
- **Elixir:** GenServer.call(target, message) / GenServer.cast(target, message)
- **Erlang:** gen_server:call(Target, Message) / gen_server:cast(Target, Message)

Matching Strategies
-------------------
1. Same-module (variable or __MODULE__/?MODULE target):
   GenServer.call(pid, :msg) / gen_server:call(Pid, msg) inside a module
   that also defines handle_call → link the calling function to the handler.

2. Cross-module (explicit module name target):
   GenServer.call(MyApp.Server, :msg) / gen_server:call(data_server, msg) →
   look up the target module's handle_call in the symbol table.

3. Alias resolution (Elixir suffix-match fallback):
   GenServer.call(UserCache, :msg) where UserCache is an alias for
   MyApp.UserCache → suffix-match finds MyApp.UserCache in the handler index.

How It Works
------------
1. Index existing handler symbols by module name
2. Scan .ex/.exs/.erl files for GenServer/gen_server call/cast patterns via regex
3. For each call site, find the enclosing function symbol (ctx.find_enclosing_symbol)
4. Determine target module from call site target expression
5. Create otp_call/otp_cast edges from caller function to handler symbols

Why Not Parse Message Patterns
------------------------------
Pattern matching means handle_call({:get, id}, ...) vs handle_call(:ping, ...)
dispatch on different messages. Matching specific messages to specific clauses
would require understanding pattern semantics. Instead, we link to ALL handler
clauses for the target module — this is sound (the runtime WILL dispatch to one
of them) and useful for graph traversal.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterator

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, PASS_VERSION, Symbol, make_pass_id
from .registry import LinkerActivation, LinkerContext, LinkerResult, register_linker

PASS_ID = make_pass_id("otp-linker")

# Handler function suffixes that indicate OTP callbacks
HANDLER_SUFFIXES = (".handle_call", ".handle_cast", ".handle_info")

# Maps GenServer call type to handler suffix and edge type
CALL_TYPE_MAP = {
    "call": (".handle_call", "otp_call"),
    "cast": (".handle_cast", "otp_cast"),
}

# Regex patterns for GenServer.call/cast detection
# Captures the target argument (module name, variable, or __MODULE__)
GENSERVER_CALL_PATTERN = re.compile(
    r"GenServer\.call\s*\(\s*"
    r"([A-Z]\w*(?:\.\w+)*|__MODULE__|\w+)",
    re.MULTILINE,
)

GENSERVER_CAST_PATTERN = re.compile(
    r"GenServer\.cast\s*\(\s*"
    r"([A-Z]\w*(?:\.\w+)*|__MODULE__|\w+)",
    re.MULTILINE,
)

# Erlang patterns for gen_server:call/cast
# Erlang conventions: variables start with uppercase (Pid), atoms with lowercase
# (my_server), macros with ? (?MODULE, ?SERVER)
ERLANG_GEN_SERVER_CALL_PATTERN = re.compile(
    r"gen_server:call\s*\(\s*"
    r"(\?[A-Z]\w*|[A-Z]\w*|[a-z]\w*)",
    re.MULTILINE,
)

ERLANG_GEN_SERVER_CAST_PATTERN = re.compile(
    r"gen_server:cast\s*\(\s*"
    r"(\?[A-Z]\w*|[A-Z]\w*|[a-z]\w*)",
    re.MULTILINE,
)

# Erlang handler name patterns (name/arity format)
ERLANG_HANDLER_SUFFIXES = ("handle_call/3", "handle_cast/2", "handle_info/2")

ERLANG_CALL_TYPE_MAP = {
    "call": ("handle_call/3", "otp_call"),
    "cast": ("handle_cast/2", "otp_cast"),
}


def detect_otp_call_sites(source: bytes) -> list[dict]:
    """Detect GenServer.call/cast patterns in Elixir source code.

    Args:
        source: Source code bytes

    Returns:
        List of dicts with keys: call_type, target, is_module, line
    """
    text = source.decode("utf-8", errors="replace")
    sites: list[dict] = []

    for match in GENSERVER_CALL_PATTERN.finditer(text):
        target = match.group(1)
        line = text[: match.start()].count("\n") + 1
        sites.append({
            "call_type": "call",
            "target": target,
            "is_module": target[0].isupper() and target != "__MODULE__",
            "line": line,
        })

    for match in GENSERVER_CAST_PATTERN.finditer(text):
        target = match.group(1)
        line = text[: match.start()].count("\n") + 1
        sites.append({
            "call_type": "cast",
            "target": target,
            "is_module": target[0].isupper() and target != "__MODULE__",
            "line": line,
        })

    return sites


def detect_erlang_otp_call_sites(source: bytes) -> list[dict]:
    """Detect gen_server:call/cast patterns in Erlang source code.

    Erlang conventions:
    - Variables start with uppercase: Pid, ServerRef → not a module target
    - Atoms start with lowercase: my_server, data_server → module target
    - Macros start with ?: ?MODULE, ?SERVER → ?MODULE is self-reference

    Args:
        source: Source code bytes

    Returns:
        List of dicts with keys: call_type, target, is_module, line
    """
    text = source.decode("utf-8", errors="replace")
    sites: list[dict] = []

    for match in ERLANG_GEN_SERVER_CALL_PATTERN.finditer(text):
        target = match.group(1)
        line = text[: match.start()].count("\n") + 1
        # In Erlang, lowercase atoms are module names; uppercase are variables
        is_module = target[0].islower()
        sites.append({
            "call_type": "call",
            "target": target,
            "is_module": is_module,
            "line": line,
        })

    for match in ERLANG_GEN_SERVER_CAST_PATTERN.finditer(text):
        target = match.group(1)
        line = text[: match.start()].count("\n") + 1
        is_module = target[0].islower()
        sites.append({
            "call_type": "cast",
            "target": target,
            "is_module": is_module,
            "line": line,
        })

    return sites


def _find_elixir_files(repo_root: Path) -> Iterator[Path]:
    """Find all Elixir files in the repository."""
    yield from find_files(repo_root, ["*.ex", "*.exs"])


def _find_erlang_files(repo_root: Path) -> Iterator[Path]:
    """Find all Erlang files in the repository."""
    yield from find_files(repo_root, ["*.erl"])


def _build_erlang_handler_index(
    symbols: list[Symbol],
) -> dict[str, dict[str, list[Symbol]]]:
    """Build index of Erlang handler symbols grouped by module.

    Erlang functions are named ``handle_call/3`` (no module prefix). We
    determine the module by finding the module symbol in the same file.

    Returns:
        {module_name: {"handle_call/3": [sym1], "handle_cast/2": [sym2]}}
    """
    # First pass: build file_path → module_name mapping from module symbols
    file_to_module: dict[str, str] = {}
    for sym in symbols:
        if sym.language == "erlang" and sym.kind == "module":
            file_to_module[sym.path] = sym.name

    # Second pass: index handler functions by their module
    index: dict[str, dict[str, list[Symbol]]] = {}
    for sym in symbols:
        if sym.language != "erlang" or sym.kind != "function":
            continue
        if sym.name not in ERLANG_HANDLER_SUFFIXES:
            continue
        module = file_to_module.get(sym.path)
        if module is None:
            continue
        if module not in index:
            index[module] = {}
        if sym.name not in index[module]:
            index[module][sym.name] = []
        index[module][sym.name].append(sym)

    return index


def _extract_module(symbol_name: str) -> str:
    """Extract module name from a fully-qualified Elixir symbol name.

    'MyApp.Server.handle_call' → 'MyApp.Server'
    'MyApp.Worker.update' → 'MyApp.Worker'
    """
    parts = symbol_name.rsplit(".", 1)
    return parts[0] if len(parts) > 1 else ""


def _build_handler_index(
    symbols: list[Symbol],
) -> dict[str, dict[str, list[Symbol]]]:
    """Build index of handler symbols grouped by module and handler type.

    Returns:
        {module_name: {".handle_call": [sym1, sym2], ".handle_cast": [sym3]}}
    """
    index: dict[str, dict[str, list[Symbol]]] = {}

    for sym in symbols:
        if sym.language != "elixir" or sym.kind != "function":
            continue

        for suffix in HANDLER_SUFFIXES:
            if sym.name.endswith(suffix):
                module = sym.name[: -len(suffix)]
                if module not in index:
                    index[module] = {}
                if suffix not in index[module]:
                    index[module][suffix] = []
                index[module][suffix].append(sym)
                break

    return index


def _resolve_handler_module(
    target: str,
    handler_index: dict[str, dict[str, list[Symbol]]],
) -> str | None:
    """Resolve a target module name to a handler_index key.

    Handles Elixir alias patterns where GenServer.call(UserCache, :msg) uses
    the short alias name ``UserCache`` but the handler_index key is the
    fully-qualified ``MyApp.UserCache``.

    Resolution cascade:
    1. Exact match: target is already a key in handler_index
    2. Suffix match: a key ends with ``.{target}`` (handles standard aliases)

    Returns the resolved module key, or None if no match found.
    """
    # 1. Exact match
    if target in handler_index:
        return target

    # 2. Suffix match: alias MyApp.UserCache → UserCache resolves to MyApp.UserCache
    suffix = f".{target}"
    for module_key in handler_index:
        if module_key.endswith(suffix):
            return module_key

    return None


def _extract_erlang_caller_module(
    enclosing: Symbol,
    file_to_module: dict[str, str],
) -> str | None:
    """Get the Erlang module name for a function symbol.

    Erlang function names don't include the module (they're just ``name/arity``),
    so we look up the module from the file-to-module mapping.
    """
    return file_to_module.get(enclosing.path)


@register_linker(
    "otp",
    priority=40,
    description="OTP GenServer call/cast dispatch (Elixir/Erlang)",
    activation=LinkerActivation(always=True),
)
def otp_linker(ctx: LinkerContext) -> LinkerResult:
    """Link GenServer.call/cast sites to handler functions.

    Scans Elixir and Erlang files for GenServer/gen_server call/cast patterns
    and creates edges to the corresponding handle_call/handle_cast functions.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    has_elixir = "elixir" in ctx.detected_languages
    has_erlang = "erlang" in ctx.detected_languages

    # Early exit if neither Elixir nor Erlang detected
    if not has_elixir and not has_erlang:
        run.duration_ms = 0
        return LinkerResult(run=run)

    edges: list[Edge] = []
    seen_pairs: set[tuple[str, str]] = set()
    files_analyzed = 0
    files_skipped = 0

    # --- Elixir phase ---
    if has_elixir:
        handler_index = _build_handler_index(ctx.symbols)
        if handler_index:
            for file_path in _find_elixir_files(ctx.repo_root):
                try:
                    source = file_path.read_bytes()
                except (OSError, IOError):
                    files_skipped += 1
                    continue

                sites = detect_otp_call_sites(source)
                if not sites:
                    files_analyzed += 1
                    continue

                for site in sites:
                    call_type = site["call_type"]
                    target = site["target"]
                    line = site["line"]
                    is_module = site["is_module"]

                    enclosing = ctx.find_enclosing_symbol(str(file_path), line)
                    if enclosing is None:
                        continue

                    handler_suffix, edge_type = CALL_TYPE_MAP[call_type]
                    target_modules: list[str] = []
                    confidence: float

                    if is_module:
                        target_modules.append(target)
                        confidence = 0.80
                    elif target == "__MODULE__":
                        caller_module = _extract_module(enclosing.name)
                        if caller_module:
                            target_modules.append(caller_module)
                        confidence = 0.90
                    else:
                        caller_module = _extract_module(enclosing.name)
                        if caller_module:
                            target_modules.append(caller_module)
                        confidence = 0.85

                    for module in target_modules:
                        resolved = _resolve_handler_module(module, handler_index)
                        if resolved is None:
                            continue
                        handlers = handler_index[resolved].get(
                            handler_suffix, [],
                        )
                        for handler in handlers:
                            pair = (enclosing.id, handler.id)
                            if pair in seen_pairs:
                                continue
                            seen_pairs.add(pair)

                            edge = Edge.create(
                                src=enclosing.id,
                                dst=handler.id,
                                edge_type=edge_type,
                                line=line,
                                confidence=confidence,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="otp_genserver_dispatch",
                            )
                            edge.meta = {
                                "evidence_type": "otp_genserver_dispatch",
                                "call_type": call_type,
                                "target": target,
                            }
                            edges.append(edge)

                files_analyzed += 1

    # --- Erlang phase ---
    if has_erlang:
        erlang_handler_index = _build_erlang_handler_index(ctx.symbols)
        # Build file→module mapping for caller module resolution
        file_to_module: dict[str, str] = {}
        for sym in ctx.symbols:
            if sym.language == "erlang" and sym.kind == "module":
                file_to_module[sym.path] = sym.name

        if erlang_handler_index:
            for file_path in _find_erlang_files(ctx.repo_root):
                try:
                    source = file_path.read_bytes()
                except (OSError, IOError):
                    files_skipped += 1
                    continue

                sites = detect_erlang_otp_call_sites(source)
                if not sites:
                    files_analyzed += 1
                    continue

                for site in sites:
                    call_type = site["call_type"]
                    target = site["target"]
                    line = site["line"]
                    is_module = site["is_module"]

                    enclosing = ctx.find_enclosing_symbol(str(file_path), line)
                    if enclosing is None:
                        continue

                    handler_name, edge_type = ERLANG_CALL_TYPE_MAP[call_type]
                    target_modules: list[str] = []
                    confidence: float

                    if is_module:
                        # Explicit module atom: gen_server:call(data_server, ...)
                        target_modules.append(target)
                        confidence = 0.80
                    elif target == "?MODULE":
                        # Same module macro: gen_server:call(?MODULE, ...)
                        caller_module = _extract_erlang_caller_module(
                            enclosing, file_to_module,
                        )
                        if caller_module:
                            target_modules.append(caller_module)
                        confidence = 0.90
                    else:
                        # Variable target: gen_server:call(Pid, ...)
                        # Assume same module (common OTP pattern)
                        caller_module = _extract_erlang_caller_module(
                            enclosing, file_to_module,
                        )
                        if caller_module:
                            target_modules.append(caller_module)
                        confidence = 0.85

                    for module in target_modules:
                        if module not in erlang_handler_index:
                            continue
                        handlers = erlang_handler_index[module].get(
                            handler_name, [],
                        )
                        for handler in handlers:
                            pair = (enclosing.id, handler.id)
                            if pair in seen_pairs:
                                continue
                            seen_pairs.add(pair)

                            edge = Edge.create(
                                src=enclosing.id,
                                dst=handler.id,
                                edge_type=edge_type,
                                line=line,
                                confidence=confidence,
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                                evidence_type="otp_genserver_dispatch",
                            )
                            edge.meta = {
                                "evidence_type": "otp_genserver_dispatch",
                                "call_type": call_type,
                                "target": target,
                            }
                            edges.append(edge)

                files_analyzed += 1

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(edges=edges, run=run)
