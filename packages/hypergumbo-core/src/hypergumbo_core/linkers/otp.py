"""OTP GenServer dispatch linker for Elixir.

Bridges GenServer.call/cast call sites to their handle_call/handle_cast
handler functions. In OTP (Elixir's concurrency framework), GenServer
dispatches messages at runtime, so static analysis can't see the connection
between a GenServer.call(target, message) and the def handle_call(message, ...)
that actually processes it. This linker closes that gap.

Matching Strategies
-------------------
1. Same-module (variable or __MODULE__ target):
   GenServer.call(pid, :msg) inside a module that also defines handle_call →
   link the calling function to the handler. This is the most common OTP
   pattern (client API + server callbacks in one module).

2. Cross-module (explicit module name target):
   GenServer.call(MyApp.Server, :msg) → look up MyApp.Server.handle_call
   in the symbol table. Lower confidence since the target could be aliased.

3. Alias resolution (suffix-match fallback):
   GenServer.call(UserCache, :msg) where UserCache is an alias for
   MyApp.UserCache → suffix-match finds MyApp.UserCache in the handler index.

How It Works
------------
1. Index existing handler symbols (*.handle_call, *.handle_cast) by module name
2. Scan .ex/.exs files for GenServer.call/cast patterns via regex
3. For each call site, find the enclosing function symbol (ctx.find_enclosing_symbol)
4. Determine target module: same-module for variables/__MODULE__, explicit for UpperCase
5. Create otp_call/otp_cast edges from caller function to handler symbols

Why Not Parse Message Patterns
------------------------------
Elixir's pattern matching means handle_call({:get, id}, ...) vs handle_call(:ping, ...)
dispatch on different messages. Matching specific messages to specific clauses would
require understanding Elixir pattern semantics. Instead, we link to ALL handler
clauses for the target module — this is sound (the runtime WILL dispatch to one of
them) and useful for graph traversal.
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


def _find_elixir_files(repo_root: Path) -> Iterator[Path]:
    """Find all Elixir files in the repository."""
    yield from find_files(repo_root, ["*.ex", "*.exs"])


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


@register_linker(
    "otp",
    priority=40,
    description="OTP GenServer call/cast dispatch (Elixir)",
    activation=LinkerActivation(always=True),
)
def otp_linker(ctx: LinkerContext) -> LinkerResult:
    """Link GenServer.call/cast sites to handler functions.

    Scans Elixir files for GenServer.call/cast patterns and creates
    edges to the corresponding handle_call/handle_cast functions.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    # Early exit if no Elixir detected
    if "elixir" not in ctx.detected_languages:
        run.duration_ms = 0
        return LinkerResult(run=run)

    # Build handler index from existing symbols
    handler_index = _build_handler_index(ctx.symbols)
    if not handler_index:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(run=run)

    edges: list[Edge] = []
    seen_pairs: set[tuple[str, str]] = set()
    files_analyzed = 0
    files_skipped = 0

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

            # Find the enclosing function symbol for this call site
            enclosing = ctx.find_enclosing_symbol(str(file_path), line)
            if enclosing is None:
                continue

            # Determine target module(s)
            handler_suffix, edge_type = CALL_TYPE_MAP[call_type]
            target_modules: list[str] = []
            confidence: float

            if is_module:
                # Explicit module target: GenServer.call(MyApp.Server, ...)
                target_modules.append(target)
                confidence = 0.80
            elif target == "__MODULE__":
                # Same module: GenServer.call(__MODULE__, ...)
                caller_module = _extract_module(enclosing.name)
                if caller_module:
                    target_modules.append(caller_module)
                confidence = 0.90
            else:
                # Variable target: GenServer.call(pid, ...)
                # Assume same module (common OTP pattern)
                caller_module = _extract_module(enclosing.name)
                if caller_module:
                    target_modules.append(caller_module)
                confidence = 0.85

            # Create edges to matching handlers, resolving aliases
            for module in target_modules:
                resolved = _resolve_handler_module(module, handler_index)
                if resolved is None:
                    continue
                handlers = handler_index[resolved].get(handler_suffix, [])
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
