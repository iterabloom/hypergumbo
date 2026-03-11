"""Tauri IPC linker for connecting TypeScript/JavaScript invoke() calls to Rust commands.

This linker creates ipc_calls edges between TypeScript/JavaScript code that
calls Rust functions via Tauri's IPC bridge (``invoke('command_name', ...)``)
and the Rust functions annotated with ``#[tauri::command]``.

How It Works
------------
Three-phase detection:

1. **Rust side**: Iterates all Rust symbols to find functions with
   ``#[tauri::command]`` in their ``meta.annotations``. Builds a command map
   keyed by the exported command name:
   - Default: the Rust function name (snake_case)
   - ``rename_all = "camelCase"``: converts ``get_user_data`` → ``getUserData``
   - ``rename = "customName"``: explicit override

2. **TS/JS side (direct invokes)**: Scans source files for invoke patterns
   with literal command names. Handles three invoke function forms:
   - ``invoke('cmd')`` — standard ``@tauri-apps/api`` import
   - ``TAURI_INVOKE("cmd")`` — tauri-specta generated bindings
   - ``__TAURI_INVOKE__('cmd')`` — older specta / internal Tauri API

3. **TS/JS side (specta wrapper resolution)**: Detects tauri-specta generated
   wrapper files — files that export functions wrapping TAURI_INVOKE calls
   (e.g., ``export function takeScreenshot() { return TAURI_INVOKE("take_screenshot") }``).
   Scans other TS/JS files for imports from these wrapper files, and creates
   ``caller_invokes`` edges from the import site to the synthetic
   ``ipc_publisher`` node. This closes the "last mile" gap: TS components
   calling ``commands.startRecording()`` are now linked through to Rust
   handlers.

After building both maps, the linker creates ipc_calls edges from synthetic
TS/JS-side sources to the matching Rust command functions, and caller_invokes
edges from TS/JS files that import specta wrappers to the ipc_publisher nodes.

Why This Design
---------------
- Tauri ``invoke()`` uses string literal command names that are not statically
  analyzable by the JS/TS tree-sitter parser as call targets. The JS analyzer
  creates a call edge to ``invoke`` itself, not to the Rust function.
- Source scanning with regex is sufficient: ``invoke('literal')`` is a rigid
  pattern and Tauri apps consistently use it.
- The ``#[tauri::command]`` attribute is captured by the Rust analyzer's
  annotation extraction, so no additional Rust source scanning is needed.
- Specta wrapper resolution uses a two-pass approach: first identify wrapper
  files (files containing TAURI_INVOKE), then scan imports pointing at those
  files and resolve imported names to command names.
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


# Matches `export function funcName(...)` followed by TAURI_INVOKE("cmd")
# within the same function body. Used to build wrapper-name → command-name maps
# for specta-generated binding files.
#
# Group 1: the exported function name
# Group 2: the command name string passed to TAURI_INVOKE/invoke
_SPECTA_WRAPPER_PATTERN = re.compile(
    r"""export\s+(?:async\s+)?function\s+(\w+)\s*\([^)]*\)\s*"""
    r"""(?::\s*[^{]*)?\{[^}]*?"""
    r"""(?:__TAURI_INVOKE__|TAURI_INVOKE|invoke)\s*(?:<[^>]*>)?\s*\(\s*['"`]([a-zA-Z0-9_:|]+)['"`]""",
)

# Matches import statements from a relative path:
#   import { func1, func2 } from './bindings'
#   import * as commands from './bindings'
#   import { func } from './bindings.ts'
# Group 1 (named imports): the specifier list inside braces, or None
# Group 2 (namespace import): the namespace alias after "* as", or None
# Group 3: the import path
_TS_IMPORT_PATTERN = re.compile(
    r"""import\s+(?:"""
    r"""(?!type\s)"""  # skip `import type`
    r"""(?:"""
    r"""\{([^}]+)\}"""  # named imports: { func1, func2 }
    r"""|"""
    r"""\*\s+as\s+(\w+)"""  # namespace import: * as commands
    r""")"""
    r"""\s+from\s+['"]([^'"]+)['"])""",
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


def _scan_specta_wrappers(
    file_path: Path,
) -> dict[str, str]:
    """Scan a specta-generated file for wrapper-name → command-name mappings.

    Detects patterns like:
        export function takeScreenshot() { return TAURI_INVOKE("take_screenshot") }

    Returns a dict mapping the JS/TS function name to the Rust command name,
    e.g., {"takeScreenshot": "take_screenshot"}.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - defensive for I/O errors
        return {}

    wrappers: dict[str, str] = {}
    for match in _SPECTA_WRAPPER_PATTERN.finditer(content):
        func_name = match.group(1)
        raw_cmd = match.group(2)
        # Handle plugin pattern: invoke('plugin:name|command')
        if "|" in raw_cmd:
            raw_cmd = raw_cmd.split("|", 1)[1]
        wrappers[func_name] = raw_cmd

    return wrappers


def _resolve_import_path(
    import_path: str,
    importer_dir: Path,
) -> Path | None:
    """Resolve a relative import path to an absolute file path.

    Tries the path as-is first, then with common TS/JS extensions.
    Returns None if unresolvable or not a relative import.
    """
    if not import_path.startswith("."):
        return None

    base = importer_dir / import_path
    # Try exact path first (e.g., import from './bindings.ts')
    if base.exists() and base.is_file():
        return base

    # Try appending common extensions (for extensionless imports)
    for ext in (".ts", ".tsx", ".js", ".jsx"):
        candidate = Path(str(base) + ext)
        if candidate.exists() and candidate.is_file():
            return candidate

    return None


def _scan_imports_from_wrapper(
    file_path: Path,
    wrapper_paths: set[str],
    wrapper_maps: dict[str, dict[str, str]],
) -> list[tuple[str, str]]:
    """Scan a TS/JS file for imports from known specta wrapper files.

    Args:
        file_path: The file to scan for import statements.
        wrapper_paths: Set of absolute path strings for known wrapper files.
        wrapper_maps: Maps absolute wrapper path → {func_name: cmd_name}.

    Returns:
        List of (wrapper_func_name, command_name) pairs imported in this file.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - defensive for I/O errors
        return []

    results: list[tuple[str, str]] = []
    importer_dir = file_path.parent

    for match in _TS_IMPORT_PATTERN.finditer(content):
        named_specs = match.group(1)  # e.g., "getUser, saveUser as save"
        namespace = match.group(2)     # e.g., "commands"
        import_path = match.group(3)   # e.g., "./bindings"

        resolved = _resolve_import_path(import_path, importer_dir)
        if resolved is None:
            continue

        abs_resolved = str(resolved.resolve())
        if abs_resolved not in wrapper_paths:
            continue

        wrapper_map = wrapper_maps.get(abs_resolved, {})
        if not wrapper_map:
            continue  # pragma: no cover

        if named_specs:
            # Named imports: { getUser, saveUser as save }
            for spec in named_specs.split(","):
                spec = spec.strip()
                if not spec:
                    continue
                # Handle "name as alias" — we want the original name
                parts = spec.split()
                original_name = parts[0] if parts else ""
                if original_name in wrapper_map:
                    results.append((original_name, wrapper_map[original_name]))

        elif namespace:
            # Namespace import: * as commands
            # All wrapper functions are accessible via commands.funcName()
            for func_name, cmd_name in wrapper_map.items():
                results.append((func_name, cmd_name))

    return results


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
                    # Tier 2 prevents _classify_symbols from reclassifying
                    # based on the host file path (e.g., tauri.ts detected
                    # as "minified/generated" → tier 4 → filtered out).
                    supply_chain_tier=2,
                    supply_chain_reason="synthetic IPC bridge node",
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

    # Phase 4: Specta wrapper resolution
    # Files that contain TAURI_INVOKE are wrapper files. Build wrapper maps,
    # then scan other files for imports from those wrappers.
    wrapper_file_paths: set[str] = set()  # absolute paths of wrapper files
    wrapper_maps: dict[str, dict[str, str]] = {}  # abs_path → {func: cmd}

    for file_path in ts_js_files:
        if not file_path.exists():
            continue
        wrappers = _scan_specta_wrappers(file_path)
        if wrappers:
            abs_key = str(file_path.resolve())
            wrapper_file_paths.add(abs_key)
            wrapper_maps[abs_key] = wrappers

    if wrapper_file_paths:
        # Build publisher_id lookup: cmd_name → ipc_publisher src_id
        publisher_id_by_cmd: dict[str, str] = {}
        for sym in result_symbols:
            if sym.kind == "ipc_publisher":
                cmd = sym.meta.get("tauri_command", "") if sym.meta else ""
                if cmd:
                    publisher_id_by_cmd[cmd] = sym.id

        seen_caller_edges: set[tuple[str, str]] = set()
        seen_caller_ids: set[str] = set()

        for file_path in ts_js_files:
            if not file_path.exists():
                continue
            # Skip wrapper files themselves
            if str(file_path.resolve()) in wrapper_file_paths:
                continue

            imported = _scan_imports_from_wrapper(
                file_path, wrapper_file_paths, wrapper_maps,
            )
            if not imported:
                continue

            rel_path = str(file_path)
            try:
                rel_path = str(file_path.relative_to(repo_root))
            except ValueError:
                pass

            for func_name, cmd_name in imported:
                publisher_id = publisher_id_by_cmd.get(cmd_name)
                if publisher_id is None:
                    continue

                dedup_key = (rel_path, cmd_name)
                if dedup_key in seen_caller_edges:
                    continue
                seen_caller_edges.add(dedup_key)

                caller_id = (
                    f"typescript:{rel_path}:0-0:{func_name}:ipc_caller"
                )

                if caller_id not in seen_caller_ids:
                    seen_caller_ids.add(caller_id)
                    result_symbols.append(Symbol(
                        id=caller_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=f"{func_name}()",
                        fingerprint=hashlib.sha256(
                            caller_id.encode(),
                        ).hexdigest()[:16],
                        kind="ipc_caller",
                        name=func_name,
                        path=rel_path,
                        language="typescript",
                        span=Span(
                            start_line=0, end_line=0,
                            start_col=0, end_col=0,
                        ),
                        origin=PASS_ID,
                        meta={"tauri_command": cmd_name},
                        supply_chain_tier=2,
                        supply_chain_reason="synthetic IPC caller node",
                    ))

                result_edges.append(Edge.create(
                    src=caller_id,
                    dst=publisher_id,
                    edge_type="caller_invokes",
                    line=0,
                    confidence=0.80,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="specta_wrapper_import",
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
