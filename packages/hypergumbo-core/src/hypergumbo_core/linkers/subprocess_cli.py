"""Subprocess-to-CLI linker for detecting cross-process CLI invocations.

This linker detects subprocess calls (subprocess.run, subprocess.call,
subprocess.Popen) in Python code and links them to CLI command entry points
in the same repository.

Detected Patterns
-----------------
Python subprocess invocations:
- subprocess.run(["myapp", "command", ...])
- subprocess.call(["myapp", "command", ...])
- subprocess.Popen(["myapp", "command", ...])
- subprocess.run(["python", "-m", "mypackage", "command", ...])

Project CLI Detection
---------------------
The linker identifies this project's CLI by:
1. Reading [project.scripts] from pyproject.toml
2. Using [project.name] as fallback
3. Matching both hyphenated and underscored variants

Matching Strategy
-----------------
1. Extract executable and subcommand from subprocess call
2. Check if executable matches this project's CLI name
3. Match subcommand to CLI command symbols (concept="command")
4. Create subprocess_calls edges linking caller to command handler

Confidence Scores
-----------------
- 0.85: Literal command list with matching project CLI and subcommand
- 0.70: Variable command list (can't verify statically)
- 0.65: python -m invocation (slightly less certain)

Why This Design
---------------
- Enables test coverage estimation for CLI-based tests
- Follows same pattern as HTTP linker (client -> server matching)
- Respects project boundaries (only links to same-project CLI)
- Creates symbols for subprocess calls enabling slice traversal
"""
from __future__ import annotations

import ast
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .registry import LinkerContext, LinkerResult, LinkerRequirement, register_linker

PASS_ID = "subprocess-linker-v1"
PASS_VERSION = "hypergumbo-1.0.0"


@dataclass
class SubprocessCall:
    """Represents a detected subprocess call."""

    executable: str | None  # The CLI executable name (e.g., "myapp")
    subcommand: str | None  # The subcommand (e.g., "serve")
    line: int  # Line number in source
    file_path: str  # Source file path
    call_type: str = "literal"  # "literal" or "variable"
    is_python_m: bool = False  # True if invoked via python -m
    raw_args: str = ""  # The raw argument string for debugging


@dataclass
class SubprocessLinkResult:
    """Result of subprocess-CLI linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


def _extract_command_info(args_str: str) -> tuple[str | None, str | None, bool]:
    """Extract executable, subcommand, and python-m flag from argument string.

    Args:
        args_str: The string representation of the command list,
            e.g., '["myapp", "serve", "--port", "8080"]'

    Returns:
        Tuple of (executable, subcommand, is_python_m)
    """
    # Try to parse as a Python list literal
    try:
        # Use ast.literal_eval for safe parsing of list literals
        args = ast.literal_eval(args_str)
        if not isinstance(args, list) or len(args) == 0:
            return None, None, False

        # Convert all to strings
        args = [str(a) for a in args]

        # Check for python -m pattern
        if args[0] in ("python", "python3", "python3.10", "python3.11", "python3.12"):
            if len(args) >= 3 and args[1] == "-m":
                # python -m package [subcommand]
                executable = args[2]
                subcommand = None
                if len(args) >= 4 and not args[3].startswith("-"):
                    subcommand = args[3]
                return executable, subcommand, True

        # Regular command
        executable = args[0]
        subcommand = None

        # Find first non-flag argument as subcommand
        for arg in args[1:]:
            if not arg.startswith("-"):
                subcommand = arg
                break

        return executable, subcommand, False

    except (ValueError, SyntaxError):  # pragma: no cover
        return None, None, False


def _detect_project_cli_name(repo_root: Path) -> set[str]:
    """Detect the project's CLI executable names.

    Reads pyproject.toml to find:
    1. [project.scripts] entries (explicit CLI names)
    2. [project.name] as fallback

    Returns:
        Set of possible CLI names for this project.
    """
    names: set[str] = set()

    pyproject_path = repo_root / "pyproject.toml"
    if not pyproject_path.exists():
        return names

    try:
        content = pyproject_path.read_text(encoding="utf-8")

        # Try to parse with tomllib (Python 3.11+) or tomli
        try:
            import tomllib  # pragma: no cover
            data = tomllib.loads(content)  # pragma: no cover
        except ImportError:  # pragma: no cover
            try:  # pragma: no cover
                import tomli  # pragma: no cover
                data = tomli.loads(content)  # pragma: no cover
            except ImportError:  # pragma: no cover
                # Fall back to regex parsing
                data = None  # pragma: no cover

        if data:
            # Get project name
            project_name = data.get("project", {}).get("name", "")
            if project_name:
                names.add(project_name)
                # Add underscore variant
                names.add(project_name.replace("-", "_"))

            # Get script entry points
            scripts = data.get("project", {}).get("scripts", {})
            for script_name in scripts:
                names.add(script_name)
        else:  # pragma: no cover
            # Regex fallback for name (only used when tomllib unavailable)
            name_match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
            if name_match:
                project_name = name_match.group(1)
                names.add(project_name)
                names.add(project_name.replace("-", "_"))

            # Regex for scripts section
            scripts_match = re.search(
                r'\[project\.scripts\]\s*\n((?:[a-zA-Z_][a-zA-Z0-9_-]*\s*=\s*["\'][^"\']+["\']\s*\n?)+)',
                content
            )
            if scripts_match:
                script_lines = scripts_match.group(1)
                for line in script_lines.strip().split("\n"):
                    if "=" in line:
                        script_name = line.split("=")[0].strip()
                        names.add(script_name)

    except (OSError, IOError):  # pragma: no cover
        pass

    return names


# Patterns for detecting subprocess calls
SUBPROCESS_CALL_PATTERN = re.compile(
    r"""subprocess\.(run|call|Popen)\s*\(\s*
        (\[[^\]]+\])  # Capture the list argument
    """,
    re.VERBOSE,
)

# Pattern for variable-based subprocess calls
SUBPROCESS_VAR_PATTERN = re.compile(
    r"""subprocess\.(run|call|Popen)\s*\(\s*
        ([a-zA-Z_][a-zA-Z0-9_]*)  # Variable name
    """,
    re.VERBOSE,
)


def _scan_python_file(file_path: Path, content: str) -> list[SubprocessCall]:
    """Scan a Python file for subprocess calls.

    Args:
        file_path: Path to the Python file
        content: File content as string

    Returns:
        List of detected SubprocessCall objects.
    """
    calls: list[SubprocessCall] = []

    # Find literal list subprocess calls
    for match in SUBPROCESS_CALL_PATTERN.finditer(content):
        args_str = match.group(2)
        line_num = content[: match.start()].count("\n") + 1

        executable, subcommand, is_python_m = _extract_command_info(args_str)

        if executable:
            calls.append(
                SubprocessCall(
                    executable=executable,
                    subcommand=subcommand,
                    line=line_num,
                    file_path=str(file_path),
                    call_type="literal",
                    is_python_m=is_python_m,
                    raw_args=args_str,
                )
            )

    # Find variable-based subprocess calls
    literal_lines = {c.line for c in calls}
    for match in SUBPROCESS_VAR_PATTERN.finditer(content):
        line_num = content[: match.start()].count("\n") + 1
        # Skip if we already captured this as a literal
        if line_num in literal_lines:
            continue

        var_name = match.group(2)
        # Try to find the variable definition and extract command info
        # Look for: var_name = ["...", "..."]
        var_pattern = re.compile(
            rf'{var_name}\s*=\s*(\[[^\]]+\])',
            re.MULTILINE
        )
        var_match = var_pattern.search(content)

        if var_match:
            args_str = var_match.group(1)
            executable, subcommand, is_python_m = _extract_command_info(args_str)

            if executable:
                calls.append(
                    SubprocessCall(
                        executable=executable,
                        subcommand=subcommand,
                        line=line_num,
                        file_path=str(file_path),
                        call_type="variable",
                        is_python_m=is_python_m,
                        raw_args=args_str,
                    )
                )
        else:
            # Variable not found, create call with unknown executable
            calls.append(
                SubprocessCall(
                    executable=None,
                    subcommand=None,
                    line=line_num,
                    file_path=str(file_path),
                    call_type="variable",
                    is_python_m=False,
                    raw_args=var_name,
                )
            )

    return calls


def _find_python_files(root: Path) -> Iterator[Path]:
    """Find Python files that might contain subprocess calls."""
    for path in find_files(root, ["**/*.py"]):
        yield path


def _has_command_concept(symbol: Symbol) -> bool:
    """Check if symbol has a command concept (CLI command)."""
    if not symbol.meta:
        return False
    concepts = symbol.meta.get("concepts", [])
    return any(
        c.get("concept") == "command"
        for c in concepts
        if isinstance(c, dict)
    )


def _create_call_symbol(call: SubprocessCall, root: Path) -> Symbol:
    """Create a symbol for a subprocess call site."""
    rel_path = Path(call.file_path).relative_to(root) if root else Path(call.file_path)

    name_parts = []
    if call.executable:
        name_parts.append(call.executable)
    if call.subcommand:
        name_parts.append(call.subcommand)
    name = " ".join(name_parts) if name_parts else "subprocess"

    return Symbol(
        id=f"{rel_path}::subprocess_call::{call.line}",
        name=name,
        kind="subprocess_call",
        path=call.file_path,
        span=Span(
            start_line=call.line,
            start_col=0,
            end_line=call.line,
            end_col=0,
        ),
        language="python",
        meta={
            "executable": call.executable,
            "subcommand": call.subcommand,
            "call_type": call.call_type,
            "is_python_m": call.is_python_m,
        },
    )


def link_subprocess(root: Path, cli_symbols: list[Symbol]) -> SubprocessLinkResult:
    """Link subprocess calls to CLI command handlers.

    Args:
        root: Repository root path.
        cli_symbols: CLI command symbols (those with concept="command").

    Returns:
        SubprocessLinkResult with edges and symbols.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    edges: list[Edge] = []
    symbols: list[Symbol] = []
    files_scanned = 0

    # Detect this project's CLI names
    project_cli_names = _detect_project_cli_name(root)

    # Build index of CLI commands by name
    command_by_name: dict[str, Symbol] = {}
    for sym in cli_symbols:
        if _has_command_concept(sym):
            command_by_name[sym.name] = sym

    # Collect all subprocess calls
    all_calls: list[SubprocessCall] = []

    for file_path in _find_python_files(root):
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            files_scanned += 1
            calls = _scan_python_file(file_path, content)
            all_calls.extend(calls)
        except (OSError, IOError):  # pragma: no cover
            pass

    # Create symbols and edges for each call
    for call in all_calls:
        # Create symbol for the call site
        call_symbol = _create_call_symbol(call, root)
        symbols.append(call_symbol)

        # Check if this is a call to this project's CLI
        if call.executable and call.executable in project_cli_names:
            # Try to match subcommand to a CLI command symbol
            if call.subcommand and call.subcommand in command_by_name:
                target_symbol = command_by_name[call.subcommand]

                # Determine confidence based on call type
                if call.call_type == "variable":
                    confidence = 0.70
                elif call.is_python_m:
                    confidence = 0.80
                else:
                    confidence = 0.85

                edge = Edge.create(
                    src=call_symbol.id,
                    dst=target_symbol.id,
                    edge_type="subprocess_calls",
                    line=call.line,
                    confidence=confidence,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="subprocess_cli_match",
                )
                edge.meta = {
                    "executable": call.executable,
                    "subcommand": call.subcommand,
                    "call_type": call.call_type,
                    "is_python_m": call.is_python_m,
                }
                edges.append(edge)

    run.duration_ms = int((time.time() - start_time) * 1000)
    run.files_analyzed = files_scanned

    return SubprocessLinkResult(edges=edges, symbols=symbols, run=run)


# =============================================================================
# Linker Registry Integration
# =============================================================================


def _get_cli_command_symbols(ctx: LinkerContext) -> list[Symbol]:
    """Extract CLI command symbols from context."""
    return [s for s in ctx.symbols if _has_command_concept(s)]


def _count_cli_command_symbols(ctx: LinkerContext) -> int:
    """Count CLI command symbols for requirement check."""
    return len(_get_cli_command_symbols(ctx))


SUBPROCESS_REQUIREMENTS = [
    LinkerRequirement(
        name="cli_command_symbols",
        description="CLI command symbols (concept=command from framework patterns)",
        check=_count_cli_command_symbols,
    ),
]


@register_linker(
    "subprocess",
    priority=65,  # Run after framework patterns have identified CLI commands
    description="Subprocess-to-CLI linking (subprocess.run to Click/Typer commands)",
    requirements=SUBPROCESS_REQUIREMENTS,
)
def subprocess_linker(ctx: LinkerContext) -> LinkerResult:
    """Subprocess linker for registry-based dispatch.

    Links subprocess calls to CLI command handlers in the same project.
    """
    cli_symbols = _get_cli_command_symbols(ctx)
    result = link_subprocess(ctx.repo_root, cli_symbols)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
