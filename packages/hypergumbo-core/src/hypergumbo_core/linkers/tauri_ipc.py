"""Tauri IPC linker for connecting TypeScript/JavaScript invoke() calls to Rust commands.

This linker creates ipc_calls edges between TypeScript/JavaScript code that
calls Rust functions via Tauri's IPC bridge (``invoke('command_name', ...)``)
and the Rust functions annotated with ``#[tauri::command]``.

How It Works
------------
Two-phase detection:

1. **Rust side**: Iterates all Rust symbols to find functions with
   ``#[tauri::command]`` in their ``meta.annotations``. Builds a command map
   keyed by the exported command name:
   - Default: the Rust function name (snake_case)
   - ``rename_all = "camelCase"``: converts ``get_user_data`` → ``getUserData``
   - ``rename = "customName"``: explicit override

2. **TS/JS side**: Scans source files for invoke patterns with literal command
   names. Handles three invoke function forms:
   - ``invoke('cmd')`` — standard ``@tauri-apps/api`` import
   - ``TAURI_INVOKE("cmd")`` — tauri-specta generated bindings
   - ``__TAURI_INVOKE__('cmd')`` — older specta / internal Tauri API
   All forms support single/double/backtick quotes, TypeScript generics
   (``invoke<T>('cmd')``), and the Tauri plugin pattern
   ``invoke('plugin:name|command')`` (extracts command after ``|``).

After building both maps, the linker creates ipc_calls edges from synthetic
TS/JS-side sources to the matching Rust command functions.

Why This Design
---------------
- Tauri ``invoke()`` uses string literal command names that are not statically
  analyzable by the JS/TS tree-sitter parser as call targets. The JS analyzer
  creates a call edge to ``invoke`` itself, not to the Rust function.
- Source scanning with regex is sufficient: ``invoke('literal')`` is a rigid
  pattern and Tauri apps consistently use it.
- The ``#[tauri::command]`` attribute is captured by the Rust analyzer's
  annotation extraction, so no additional Rust source scanning is needed.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import hashlib

from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
)

PASS_ID = make_pass_id("tauri-ipc-linker")

# Matches invoke(), TAURI_INVOKE(), and __TAURI_INVOKE__() with string
# literal command names. Also handles TypeScript generics like invoke<T>().
#
# Matched patterns:
#   invoke('command')              - standard Tauri API
#   invoke<Type>('command')        - TypeScript generic form
#   TAURI_INVOKE("command")        - tauri-specta generated bindings
#   TAURI_INVOKE<T>("command")     - tauri-specta with generics
#   __TAURI_INVOKE__('command')    - older specta / internal Tauri API
#
# Group 1 captures the command name string.
_INVOKE_PATTERN = re.compile(
    r"""(?:__TAURI_INVOKE__|TAURI_INVOKE|invoke)\s*(?:<[^>]*>)?\s*\(\s*['"`]([a-zA-Z0-9_:|]+)['"`]""",
)


def _snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase.

    Examples:
        get_user_data -> getUserData
        greet -> greet  (no change)
    """
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


@dataclass
class TauriIPCLinkResult:
    """Result of Tauri IPC linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


def _find_tauri_commands(
    rust_symbols: list[Symbol],
) -> dict[str, Symbol]:
    """Build command name → Symbol map from Rust #[tauri::command] functions.

    A single Rust function may be registered under multiple names:
    - The raw function name (default)
    - camelCase-converted name (if rename_all="camelCase")
    - Explicit rename (if rename="customName")
    """
    command_map: dict[str, Symbol] = {}

    for sym in rust_symbols:
        if sym.language != "rust":
            continue
        if sym.kind not in ("function", "method"):
            continue
        if sym.meta is None:
            continue

        annotations = sym.meta.get("annotations")
        if not annotations or not isinstance(annotations, list):
            continue

        is_tauri_command = False
        rename_all = None
        explicit_rename = None

        for ann in annotations:
            if not isinstance(ann, dict):
                continue  # pragma: no cover
            name = ann.get("name", "")
            if name in ("tauri::command", "command"):
                is_tauri_command = True
                kwargs = ann.get("kwargs", {})
                if "rename_all" in kwargs:
                    rename_all = kwargs["rename_all"]
                if "rename" in kwargs:
                    explicit_rename = kwargs["rename"]

        if not is_tauri_command:
            continue

        # Register under the raw function name (always)
        command_map[sym.name] = sym

        # Register under camelCase name if rename_all is set
        if rename_all == "camelCase":
            camel_name = _snake_to_camel(sym.name)
            if camel_name != sym.name:
                command_map[camel_name] = sym

        # Register under explicit rename if set
        if explicit_rename and isinstance(explicit_rename, str):
            command_map[explicit_rename] = sym

    return command_map


def _scan_ts_js_file_for_invoke(
    file_path: Path,
) -> list[str]:
    """Scan a TypeScript/JavaScript file for Tauri invoke() calls.

    Returns a list of command names found in invoke('command_name') calls.
    For plugin invokes like invoke('plugin:name|command'), returns the
    command portion after the pipe.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - defensive for I/O errors
        return []

    commands: list[str] = []
    for match in _INVOKE_PATTERN.finditer(content):
        raw_cmd = match.group(1)
        # Handle plugin pattern: invoke('plugin:name|command')
        if "|" in raw_cmd:
            raw_cmd = raw_cmd.split("|", 1)[1]
        commands.append(raw_cmd)

    return commands


def link_tauri_ipc(
    repo_root: Path,
    ts_js_symbols: list[Symbol],
    rust_symbols: list[Symbol],
) -> TauriIPCLinkResult:
    """Link Tauri invoke() calls to their Rust #[tauri::command] functions.

    Args:
        repo_root: Repository root path.
        ts_js_symbols: TypeScript/JavaScript symbols from analyzers.
        rust_symbols: Rust symbols from analyzers.

    Returns:
        TauriIPCLinkResult with ipc_calls edges.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    result_edges: list[Edge] = []
    result_symbols: list[Symbol] = []
    seen_publisher_ids: set[str] = set()

    # Phase 1: Build command map from Rust symbols
    command_map = _find_tauri_commands(rust_symbols)
    if not command_map:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return TauriIPCLinkResult(edges=[], run=run)

    # Phase 2: Scan TS/JS files for invoke() calls
    # Deduplicate by file path
    seen_paths: set[str] = set()
    ts_js_files: list[Path] = []
    for sym in ts_js_symbols:
        if sym.language not in ("javascript", "typescript"):
            continue
        if sym.path in seen_paths:
            continue
        seen_paths.add(sym.path)

        file_path = Path(sym.path)
        if not file_path.is_absolute():
            file_path = repo_root / file_path
        ts_js_files.append(file_path)

    # Phase 3: Match invoke calls to Rust commands
    seen_edges: set[tuple[str, str]] = set()  # (file_path, command_name)

    for file_path in ts_js_files:
        if not file_path.exists():
            continue

        invoke_commands = _scan_ts_js_file_for_invoke(file_path)

        for cmd_name in invoke_commands:
            target_sym = command_map.get(cmd_name)
            if target_sym is None:
                continue

            dedup_key = (str(file_path), cmd_name)
            if dedup_key in seen_edges:
                continue
            seen_edges.add(dedup_key)

            # Build a synthetic source ID for the TS/JS invoke call site.
            # Use the file path as-is from the symbol to maintain consistency
            # with the rest of the behavior map.
            rel_path = str(file_path)
            # Try to make relative to repo root for cleaner IDs
            try:
                rel_path = str(file_path.relative_to(repo_root))
            except ValueError:
                pass

            src_id = f"typescript:{rel_path}:0-0:{cmd_name}:ipc_publisher"

            # Create synthetic Symbol node for the IPC publisher so the
            # slicer's BFS can traverse through it. Without this node,
            # reverse slices from Rust handlers would dead-end because
            # node_by_id.get(edge.src) returns None.
            if src_id not in seen_publisher_ids:
                seen_publisher_ids.add(src_id)
                result_symbols.append(Symbol(
                    id=src_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=f"invoke('{cmd_name}')",
                    fingerprint=hashlib.sha256(src_id.encode()).hexdigest()[:16],
                    kind="ipc_publisher",
                    name=cmd_name,
                    path=rel_path,
                    language="typescript",
                    span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
                    origin=PASS_ID,
                    meta={"tauri_command": cmd_name},
                ))

            result_edges.append(Edge.create(
                src=src_id,
                dst=target_sym.id,
                edge_type="ipc_calls",
                line=0,
                confidence=0.90,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="tauri_invoke",
            ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return TauriIPCLinkResult(
        edges=result_edges, symbols=result_symbols, run=run,
    )


def _count_js_ts_files(ctx: LinkerContext) -> int:
    """Count JavaScript/TypeScript files."""
    seen_paths: set[str] = set()
    for sym in ctx.symbols:
        if sym.language in ("javascript", "typescript"):
            if sym.path not in seen_paths:
                seen_paths.add(sym.path)
    return len(seen_paths)


def _count_tauri_commands(ctx: LinkerContext) -> int:
    """Count Rust symbols with #[tauri::command] annotation."""
    count = 0
    for sym in ctx.symbols:
        if sym.language != "rust" or sym.kind not in ("function", "method"):
            continue
        if sym.meta is None:
            continue
        annotations = sym.meta.get("annotations")
        if not annotations or not isinstance(annotations, list):
            continue
        for ann in annotations:
            if isinstance(ann, dict) and ann.get("name") in (
                "tauri::command", "command",
            ):
                count += 1
                break
    return count


TAURI_IPC_REQUIREMENTS = [
    LinkerRequirement(
        name="js_ts_files",
        description="JavaScript/TypeScript files (potential Tauri invoke callers)",
        check=_count_js_ts_files,
    ),
    LinkerRequirement(
        name="tauri_command_functions",
        description="Rust #[tauri::command] functions (IPC targets)",
        check=_count_tauri_commands,
    ),
]


@register_linker(
    "tauri_ipc",
    priority=14,  # After NAPI (13), before cgo (15)
    description=(
        "Tauri IPC bridge - links TypeScript/JavaScript invoke() calls "
        "to Rust #[tauri::command] functions"
    ),
    requirements=TAURI_IPC_REQUIREMENTS,
    activation=LinkerActivation(
        frameworks=["tauri"],
        language_pairs=[
            ("typescript", "rust"),
            ("javascript", "rust"),
        ],
    ),
)
def tauri_ipc_linker(ctx: LinkerContext) -> LinkerResult:
    """Tauri IPC linker for registry-based dispatch.

    Wraps link_tauri_ipc() to use the LinkerContext/LinkerResult interface.
    """
    ts_js_symbols = [
        s for s in ctx.symbols if s.language in ("javascript", "typescript")
    ]
    rust_symbols = [s for s in ctx.symbols if s.language == "rust"]

    result = link_tauri_ipc(ctx.repo_root, ts_js_symbols, rust_symbols)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
