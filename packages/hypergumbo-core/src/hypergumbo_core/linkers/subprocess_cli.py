# SPDX-License-Identifier: AGPL-3.0-or-later
"""Protocol linker: subprocess-to-CLI for detecting cross-process CLI invocations.

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
The linker identifies this project's CLI by unioning, across EVERY
``pyproject.toml`` and ``setup.py`` the discovery walk finds (not just the
repo root — that was WI-gadus, which made the linker structurally blind on
any monorepo whose root manifest declares no scripts):
1. [project.scripts] entries from each pyproject.toml
2. [project.name] as fallback
3. console_scripts / entry_points from each setup.py
4. Matching both hyphenated and underscored variants

The union over-selects rather than under-selects: a workspace that vendors a
package named after a common binary will claim that binary's call sites.

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

from ..analyze.base import make_symbol_id, sanitize_id_name_segment
from ..discovery import find_files
from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from ._concept_utils import has_concept
from ._text_filters import language_from_path
from .registry import LinkerContext, LinkerResult, LinkerRequirement, register_linker
from ._text_filters import read_masked_source

PASS_ID = make_pass_id("subprocess-linker")


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


def _leading_constant_args(args_str: str) -> list[str]:
    """Return the LEADING run of constant elements of a list/tuple display.

    WI-gadus B2. The previous implementation ran ``ast.literal_eval`` over the
    WHOLE captured argv, so a single non-literal element discarded the entire
    call site — and real self-invocations are almost never fully literal. The
    live case is ``scripts/hypergumbo_diag.py``::

        subprocess.run(["hypergumbo", "slice", "--input", in_path, ...])

    where the executable and subcommand ARE plain literals in positions 0 and 1
    and everything the linker needs sits in front of the first variable. Parsing
    element-wise and stopping at the first non-constant recovers exactly that
    prefix, and stopping (rather than skipping) is deliberate: once an element
    is opaque, positional reasoning about what follows it is unsound — a
    variable could expand to a flag, a subcommand, or nothing at all.

    Returns ``[]`` when the string does not parse, is not a list/tuple display,
    or begins with a non-constant.
    """
    try:
        node = ast.parse(args_str.strip(), mode="eval").body
    except (ValueError, SyntaxError):
        return []
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    out: list[str] = []
    for elt in node.elts:
        if not isinstance(elt, ast.Constant):
            break
        out.append(str(elt.value))
    return out


def _extract_command_info(args_str: str) -> tuple[str | None, str | None, bool]:
    """Extract executable, subcommand, and python-m flag from argument string.

    Args:
        args_str: The string representation of the command list,
            e.g., '["myapp", "serve", "--port", "8080"]'

    Returns:
        Tuple of (executable, subcommand, is_python_m)
    """
    args = _leading_constant_args(args_str)
    if not args:
        return None, None, False

    # Check for python -m pattern
    if args[0] in ("python", "python3", "python3.10", "python3.11", "python3.12"):
        if len(args) >= 2 and args[1] == "-m":
            # python -m package [subcommand]. The package name is the
            # executable, so a run that stops being literal BEFORE index 2
            # cannot be resolved at all — fail safe rather than reporting
            # "python" as the executable, which would join nothing anyway.
            if len(args) < 3:
                return None, None, False
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


def _cli_names_from_setup_py(setup_path: Path) -> set[str]:
    """Extract distribution + console_script names from a ``setup.py``.

    WI-gadus B1, second half. ``setup.py`` was never read at all, so a
    setuptools project that predates PEP 621 was invisible to this linker no
    matter where its manifest sat. Parsed with ``ast`` rather than executed:
    the call is located structurally and only literal keyword values are read,
    so a computed ``name=`` or a dynamically-built ``entry_points`` fails safe
    to contributing nothing rather than to a wrong name.
    """
    names: set[str] = set()
    try:
        tree = ast.parse(read_masked_source(setup_path, encoding="utf-8"))
    except (OSError, IOError, SyntaxError, ValueError):
        return names
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        fname = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else None
        )
        if fname != "setup":
            continue
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    names.add(kw.value.value)
                    names.add(kw.value.value.replace("-", "_"))
            elif kw.arg == "entry_points" and isinstance(kw.value, ast.Dict):
                for key, val in zip(kw.value.keys, kw.value.values, strict=True):
                    if not (
                        isinstance(key, ast.Constant)
                        and key.value == "console_scripts"
                        and isinstance(val, (ast.List, ast.Tuple))
                    ):
                        continue
                    for elt in val.elts:
                        if isinstance(elt, ast.Constant) and isinstance(
                            elt.value, str
                        ) and "=" in elt.value:
                            names.add(elt.value.split("=")[0].strip())
    return names


def _detect_project_cli_name(repo_root: Path) -> set[str]:
    """Detect the project's CLI executable names, ANYWHERE in the tree.

    Unions, across every ``pyproject.toml`` and ``setup.py`` the discovery
    excludes admit:
    1. ``[project.scripts]`` entries (explicit CLI names)
    2. ``[project.name]`` (plus its underscore variant) as fallback
    3. ``setup(name=...)`` and ``entry_points={"console_scripts": [...]}``

    WI-gadus B1. This previously read ONLY ``<repo_root>/pyproject.toml``, which
    made the linker structurally blind on any monorepo whose root manifest is
    tool-config-only — including hypergumbo itself, whose root has no
    ``[project]`` table at all while the ``hypergumbo`` console script is
    declared in ``packages/hypergumbo/pyproject.toml``. The consequence was not
    a degraded join but the total absence of one: ``_detect_project_cli_name``
    returned an empty set, so the ``call.executable in project_cli_names`` guard
    in :func:`link_subprocess` could never pass for any call in the repository,
    and every downstream mechanism — including the WI-lubap argparse join, which
    resolves its handlers correctly — was dead code behind it.

    Unioning across nested manifests deliberately over-selects rather than
    under-selects. A workspace that vendors a package named after a common
    binary could admit a false executable name; that costs a spurious edge,
    whereas the previous behavior cost every real one, and vendor directories
    are already excluded by the shared discovery rules.

    Returns:
        Set of possible CLI names for this project.
    """
    names: set[str] = set()
    for setup_path in find_files(repo_root, ["**/setup.py"]):
        names |= _cli_names_from_setup_py(setup_path)
    for pyproject_path in find_files(repo_root, ["**/pyproject.toml"]):
        names |= _cli_names_from_pyproject(pyproject_path)
    return names


def _cli_names_from_pyproject(pyproject_path: Path) -> set[str]:
    """Extract distribution + script names from ONE ``pyproject.toml``."""
    names: set[str] = set()

    try:
        content = read_masked_source(pyproject_path, encoding="utf-8")

        # Resolve a TOML loader: tomllib (Python 3.11+) preferred, tomli as fallback.
        try:
            import tomllib  # pragma: no cover
        except ImportError:  # pragma: no cover
            try:
                import tomli as tomllib
            except ImportError:
                tomllib = None

        data: dict | None = None
        if tomllib is not None:
            try:
                data = tomllib.loads(content)  # pragma: no cover
            except (ValueError, OSError):  # pragma: no cover  # malformed TOML
                data = None

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
    """Find Python files that might contain subprocess calls.

    Deliberately does NOT use the linker-evidence-gating:F1 test-file gate:
    unlike the pattern-string linkers (message-queue topics, SQL literals),
    a ``subprocess.run([...])`` call in an integration test is a *real* CLI
    invocation, and detecting test-suite invocations of the project's own CLI
    is this linker's intended behavior (see test_subprocess_linker).
    """
    for path in find_files(root, ["**/*.py"]):
        yield path


def _has_command_concept(symbol: Symbol) -> bool:
    """Check if symbol has a command concept (CLI command)."""
    return has_concept(symbol, "command")


def _create_call_symbol(call: SubprocessCall, root: Path) -> Symbol:
    """Create a symbol for a subprocess call site."""
    rel_path = Path(call.file_path).relative_to(root) if root else Path(call.file_path)

    name_parts = []
    if call.executable:
        name_parts.append(call.executable)
    if call.subcommand:
        name_parts.append(call.subcommand)
    name = " ".join(name_parts) if name_parts else "subprocess"

    # ADR-0031 Class B: synthetic stand-in for a subprocess invocation.
    # Was LITERAL-HOST ("python"); discovery_language now derives from
    # the host file (subprocess.Popen calls can surface from non-Python
    # bindings or from polyglot fixtures).
    return Symbol(
        id=make_symbol_id(language_from_path(Path(call.file_path)), str(rel_path), call.line, call.line, sanitize_id_name_segment(name), "call_site"),
        name=name,
        kind="call_site",
        path=call.file_path,
        span=Span(
            start_line=call.line,
            start_col=0,
            end_line=call.line,
            end_col=0,
        ),
        language=None,
        discovery_language=language_from_path(Path(call.file_path)),
        protocol_origin="subprocess_cli",
        meta={
            "executable": call.executable,
            "subcommand": call.subcommand,
            "call_type": call.call_type,
            "is_python_m": call.is_python_m,
            "call_kind": "subprocess",
        },
    )


def _argparse_add_parser_name(call: "ast.Call") -> str | None:
    """If *call* is ``<x>.add_parser("name", ...)``, return the subcommand
    string ``"name"``; otherwise ``None`` (WI-lubap)."""
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "add_parser"
        and call.args
    ):
        arg0 = call.args[0]
        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
            return arg0.value
    return None


def _argparse_set_defaults_handler(call: "ast.Call") -> tuple[str, str] | None:
    """If *call* is ``<var>.set_defaults(func=<handler>)``, return
    ``(parser_var, handler_name)``; otherwise ``None`` (WI-lubap)."""
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "set_defaults"
        and isinstance(func.value, ast.Name)
    ):
        for kw in call.keywords:
            if kw.arg == "func" and isinstance(kw.value, ast.Name):
                return (func.value.id, kw.value.id)
    return None


@dataclass(frozen=True)
class _CliScanFacts:
    """The FILE-DERIVED half of both CLI scans, from one walk of the tree.

    Split out because it is a pure function of the files on disk: only the
    RESOLUTION half of each scan (handler name → Symbol, class → its methods)
    depends on the symbol table, and the two scans are handed different symbol
    lists by different callers. Keeping the file facts symbol-free is what lets
    one walk serve all four call sites.

    ``argparse_pairs`` is (rel_path, subcommand, handler_name) — the resolved
    add_parser/set_defaults pairing, before any symbol lookup.
    ``fire_targets`` is (rel_path, class_name) from ``fire.Fire(<Name>)``.
    """

    argparse_pairs: tuple[tuple[str, str, str], ...]
    fire_targets: frozenset[tuple[str, str]]


# Single-slot memo of the walk above. SINGLE slot, not a growing dict: the
# access pattern is "the same root, four times in a row" (the linker and the
# requirement-check diagnostic), so one entry captures all of it while making an
# unbounded-growth leak impossible in a long-lived process such as a test run.
_CLI_SCAN_MEMO: tuple[str, _CliScanFacts] | None = None


def _reset_cli_scan_cache() -> None:
    """Drop the memo. For tests that mutate a tree between scans."""
    global _CLI_SCAN_MEMO
    _CLI_SCAN_MEMO = None


def _cli_scan_key(root: Path, files: list[Path]) -> str:
    """Identity of the tree being scanned: path + (size, mtime) of every file.

    WHY NOT JUST ``root``. A memo keyed on the directory alone is a STALE-HIT
    generator the moment anything edits a file between two scans — the caller
    silently receives facts for a tree that no longer exists, with nothing in
    the output saying so. That is the same failure mode WI-madut names for
    persistent per-file memoization, and it is worth pre-empting here even
    though a single survey never mutates its own tree mid-run: test suites do,
    constantly.

    Size+mtime rather than content digests: this runs before the parse, so
    hashing every file's bytes would reintroduce most of the I/O the memo
    exists to avoid. The residual hole is a same-size edit within one mtime
    tick, which ``_reset_cli_scan_cache`` covers for tests that need certainty.
    """
    parts = [str(root)]
    for path in files:
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover - file vanished between walk and stat
            parts.append(f"{path}:gone")
            continue
        parts.append(f"{path}:{stat.st_size}:{stat.st_mtime_ns}")
    return "\x1f".join(parts)


def _scan_python_cli_facts(root: Path) -> _CliScanFacts:
    """Walk every python file ONCE and extract both scans' file-derived facts.

    Replaces two independent walks that each parsed the whole tree, each of
    which ran twice per survey (the linker and ``_count_cli_command_symbols``):
    four full parse-and-walk passes over every python file, measured at ~16s on
    this monorepo with ~12s of it re-parsing content already parsed.
    """
    global _CLI_SCAN_MEMO

    files = list(_find_python_files(root))
    key = _cli_scan_key(root, files)
    memo = _CLI_SCAN_MEMO
    if memo is not None and memo[0] == key:
        return memo[1]

    argparse_pairs: list[tuple[str, str, str]] = []
    fire_targets: set[tuple[str, str]] = set()
    for file_path in files:
        try:
            content = read_masked_source(
                file_path, encoding="utf-8", errors="ignore"
            )
            tree = ast.parse(content)
        except (OSError, IOError, SyntaxError, ValueError):  # pragma: no cover - IO/parse errors hard to force
            continue
        rel_path = str(file_path.relative_to(root))
        # parser-variable → subcommand name (from ``v = X.add_parser("name")``)
        var_to_subcmd: dict[str, str] = {}
        # parser-variable → handler name (from ``v.set_defaults(func=handler)``)
        var_to_handler: dict[str, str] = {}
        for node in ast.walk(tree):
            # The fire probe sees EVERY Call node, exactly as its own loop did.
            # It must NOT hang off the argparse if/elif below: a Call that is an
            # Assign's value (``cli = fire.Fire(Service)``) takes the first
            # branch there and would never be offered to the fire probe —
            # silently dropping a target shape the separate loop had matched.
            if isinstance(node, ast.Call):
                target = _fire_target_name(node)
                if target is not None:
                    fire_targets.add((rel_path, target))
            # The argparse branches keep their original if/elif relationship, so
            # `_argparse_set_defaults_handler` sees exactly the node set it saw
            # before (bare Calls only, never an Assign's value).
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                sub = _argparse_add_parser_name(node.value)
                if sub is not None:
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            var_to_subcmd[tgt.id] = sub
            elif isinstance(node, ast.Call):
                pair = _argparse_set_defaults_handler(node)
                if pair is not None:
                    var_to_handler[pair[0]] = pair[1]
        for var, subcmd in var_to_subcmd.items():
            handler_name = var_to_handler.get(var)
            if handler_name:
                argparse_pairs.append((rel_path, subcmd, handler_name))

    facts = _CliScanFacts(
        argparse_pairs=tuple(argparse_pairs),
        fire_targets=frozenset(fire_targets),
    )
    _CLI_SCAN_MEMO = (key, facts)
    return facts


def _scan_argparse_commands(
    root: Path, all_symbols: list[Symbol]
) -> dict[str, list[Symbol]]:
    """Detect argparse subcommand definitions and resolve their handlers.

    argparse exposes its command surface as a pair of calls on a subparser
    variable — ``<p> = <sub>.add_parser("name", ...)`` registers the subcommand
    *string*, and ``<p>.set_defaults(func=<handler>)`` binds its handler. Neither
    the subcommand name (a string literal) nor the parser is a Symbol, so the
    subprocess linker cannot join them the way it joins decorator-based
    ``concept="command"`` symbols the framework-patterns pass emits (WI-lubap).
    This scans each Python file's AST for the idiom, pairs add_parser /
    set_defaults by parser-variable name, and resolves the ``func=`` handler to
    its module symbol.

    Returns ``{subcommand_name: [handler_symbol, ...]}`` for merging into the
    linker's ``command_by_name`` index. A subcommand whose handler cannot be
    resolved (no ``set_defaults(func=...)``, or a handler name with no matching
    symbol) contributes nothing — there is no join target.
    """
    symbols_by_name: dict[str, list[Symbol]] = {}
    symbols_by_path_name: dict[tuple[str, str], list[Symbol]] = {}
    for sym in all_symbols:
        # WI-gadus B3: an ``external_symbol`` is a boundary PLACEHOLDER, never a
        # real handler body, and it carries the ``<external>`` path sentinel —
        # which sorts before every real path, so the INV-zuhub min-by-id
        # tie-break below would actively PREFER it. On hypergumbo's own tree the
        # subcommand ``survey`` resolved to both the real ``cmd_run`` and such a
        # placeholder, and the placeholder won.
        if sym.kind == "external_symbol":
            continue
        symbols_by_name.setdefault(sym.name, []).append(sym)
        symbols_by_path_name.setdefault((sym.path, sym.name), []).append(sym)

    commands: dict[str, list[Symbol]] = {}
    for rel_path, subcmd, handler_name in _scan_python_cli_facts(root).argparse_pairs:
        # WI-gadus B3: prefer the handler DEFINED in the same file as the
        # add_parser/set_defaults pair. ``set_defaults(func=X)`` names a
        # binding in that module's scope, so a same-file definition is the
        # binding by construction, and any same-named symbol elsewhere is a
        # coincidence. Unlike _scan_fire_commands, this FALLS BACK to a
        # name-only match rather than requiring same-file: the handler is
        # legitimately imported from a sibling module often enough
        # (``from .commands import cmd_serve``) that requiring same-file
        # would trade one wrong-edge class for a recall hole.
        scoped = symbols_by_path_name.get((rel_path, handler_name))
        candidates = scoped if scoped else symbols_by_name.get(handler_name)
        if candidates:
            commands.setdefault(subcmd, []).extend(candidates)
    return commands


def _fire_target_name(call: "ast.Call") -> str | None:
    """If *call* is ``fire.Fire(<Name>)``, return the target ``"<Name>"``;
    otherwise ``None`` (WI-sigam).

    python-fire exposes a class/module's public members as CLI subcommands
    reflectively — there is no ``add_parser``/``set_defaults`` idiom to scan.
    Only the ``fire.Fire(<bare Name>)`` shape is resolved here. Deliberately
    unmatched (documented deferrals, all fail SAFE to no join — never a wrong
    edge): the *arg* forms ``fire.Fire(build())`` / ``fire.Fire(Calc())`` (an
    instance) / ``fire.Fire({...})`` / ``fire.Fire()`` (module dispatch), and
    the *func* forms ``from fire import Fire; Fire(Calc)`` (bare ``Fire``) and
    ``import fire as f; f.Fire(Calc)`` (aliased module). The dominant
    ``import fire; fire.Fire(Cls)`` idiom is covered.
    """
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "Fire"
        and isinstance(func.value, ast.Name)
        and func.value.id == "fire"
        and call.args
        and isinstance(call.args[0], ast.Name)
    ):
        return call.args[0].id
    return None


def _scan_fire_commands(
    root: Path, all_symbols: list[Symbol]
) -> dict[str, list[Symbol]]:
    """Detect ``fire.Fire(<Class>)`` reflective CLIs and resolve their
    subcommands to the class's public methods (WI-sigam).

    python-fire turns each PUBLIC method of the object passed to ``fire.Fire``
    into a CLI subcommand whose name IS the method name — there is no
    add_parser/set_defaults idiom (WI-lubap) to scan and no per-subcommand
    Symbol, so the linker derives the surface from the class's method symbols.

    INV-fahub scoping: a ``fire.Fire(<Name>)`` target is tied to the FILE its
    call lives in, and resolves only to a ``kind="class"`` Symbol **defined in
    that same file** — a same-named class in another module is never harvested
    (a name-only match would mint confidently-wrong edges to an unrelated
    class's methods). Each ``<Name>.<method>`` method Symbol of that file's
    fired class whose method is public (not underscore-prefixed, exactly how
    python-fire hides members) joins the subcommand ``<method>``.

    Returns ``{method_name: [method_symbol, ...]}`` for merging into
    ``command_by_name``. A fire target with no same-file class Symbol (an
    instance / dict / module target, or a class IMPORTED from another module —
    the deferred fire forms) contributes nothing (fails safe to no join).
    """
    # (fire-file repo-relative path, target class name) pairs, from the shared
    # walk — see _scan_python_cli_facts for why this is no longer a private one.
    fire_targets = _scan_python_cli_facts(root).fire_targets
    if not fire_targets:
        return {}
    # Keep only targets whose class is DEFINED in the fire.Fire() call's own
    # file, indexed by that file's path for the method scan below.
    class_paths_names = {
        (s.path, s.name) for s in all_symbols if s.kind == "class"
    }
    targets_by_path: dict[str, set[str]] = {}
    for rel_path, name in fire_targets:
        if (rel_path, name) in class_paths_names:
            targets_by_path.setdefault(rel_path, set()).add(name)
    commands: dict[str, list[Symbol]] = {}
    for sym in all_symbols:
        if sym.kind != "method" or "." not in sym.name:
            continue
        cls, _, method = sym.name.rpartition(".")
        if (
            cls in targets_by_path.get(sym.path, ())
            and not method.startswith("_")
        ):
            commands.setdefault(method, []).append(sym)
    return commands


def link_subprocess(
    root: Path,
    cli_symbols: list[Symbol],
    all_symbols: list[Symbol] | None = None,
) -> SubprocessLinkResult:
    """Link subprocess calls to CLI command handlers.

    Args:
        root: Repository root path.
        cli_symbols: CLI command symbols (those with concept="command", from the
            framework-patterns pass — Click/Typer/Django/etc.).
        all_symbols: All repository symbols. When provided, argparse subcommand
            handlers are additionally resolved (WI-lubap) — argparse CLIs (incl.
            hypergumbo's own) have no concept="command" symbols, so without this
            they are dark to the linker.

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

    # Build index of CLI commands by name. INV-zuhub: when multiple
    # commands share a short name (cross-binary collision in projects
    # whose pyproject.toml declares multiple [project.scripts] entries,
    # each with its own command tree), all candidates are tracked; the
    # fallback rule picks at edge-creation time. Pre-fix single-value
    # dict silently overwrote — see WI-jifiv (BUG-04/05 shape).
    command_by_name: dict[str, list[Symbol]] = {}
    for sym in cli_symbols:
        if _has_command_concept(sym):
            command_by_name.setdefault(sym.name, []).append(sym)

    # WI-lubap: argparse CLIs expose commands as add_parser / set_defaults call
    # pairs, not concept="command" symbols, so they are otherwise dark to this
    # linker. Merge the resolved argparse subcommand→handler map (keyed by the
    # subcommand string, same as command_by_name) so subprocess subcommands can
    # join their argparse handlers too.
    if all_symbols:
        argparse_commands = _scan_argparse_commands(root, all_symbols)
        for subcmd, handlers in argparse_commands.items():
            command_by_name.setdefault(subcmd, []).extend(handlers)
        # WI-sigam: python-fire reflective CLIs expose a class's public methods
        # as subcommands with no add_parser/set_defaults idiom — merge the
        # resolved fire method→subcommand map the same way. Sharing
        # command_by_name means it inherits the INV-zuhub multi-candidate
        # collision fallback at edge-creation time.
        fire_commands = _scan_fire_commands(root, all_symbols)
        for subcmd, handlers in fire_commands.items():
            command_by_name.setdefault(subcmd, []).extend(handlers)

    # Collect all subprocess calls
    all_calls: list[SubprocessCall] = []

    for file_path in _find_python_files(root):
        try:
            content = read_masked_source(file_path, encoding="utf-8", errors="ignore")
            files_scanned += 1
            calls = _scan_python_file(file_path, content)
            all_calls.extend(calls)
        except (OSError, IOError):  # pragma: no cover
            pass

    # Create symbols and edges for each call
    for call in all_calls:
        # Create symbol for the call site
        call_symbol = _create_call_symbol(call, root)
        call_symbol.origin = [PASS_ID]
        call_symbol.origin_run_id = run.execution_id
        symbols.append(call_symbol)

        # Check if this is a call to this project's CLI
        if call.executable and call.executable in project_cli_names:
            # Try to match subcommand to a CLI command symbol
            if call.subcommand and call.subcommand in command_by_name:
                candidates = command_by_name[call.subcommand]
                # INV-zuhub: cross-binary subcommand collision is
                # unresolvable from the subprocess invocation alone
                # (the linker has no per-symbol binary attribution).
                # Pick deterministic-by-id when ambiguous.
                is_fallback = len(candidates) > 1
                target_symbol = (
                    candidates[0] if not is_fallback
                    else min(candidates, key=lambda s: s.id)
                )

                # Determine confidence based on call type
                if call.call_type == "variable":
                    base_confidence = 0.70
                elif call.is_python_m:
                    base_confidence = 0.80
                else:
                    base_confidence = 0.85
                # INV-zuhub: fallback caps at 0.5 (base_confidence is
                # always >= 0.70 here, so the literal is equivalent to
                # min(base_confidence, 0.5) but L4-walker-resolvable).
                confidence = 0.5 if is_fallback else base_confidence
                edge_meta = (
                    {
                        "executable": call.executable,
                        "subcommand": call.subcommand,
                        "call_type": call.call_type,
                        "is_python_m": call.is_python_m,
                        "detection_pattern": "subprocess_cli",
                        "disambiguation_fallback": True,
                    }
                    if is_fallback
                    else {
                        "executable": call.executable,
                        "subcommand": call.subcommand,
                        "call_type": call.call_type,
                        "is_python_m": call.is_python_m,
                        "detection_pattern": "subprocess_cli",
                    }
                )
                edges.append(Edge.create(
                    src=call_symbol.id,
                    dst=target_symbol.id,
                    edge_type="subprocess_calls",
                    line=call.line,
                    confidence=confidence,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_call_direct",
                    meta=edge_meta,
                    derived_from=[call_symbol.id, target_symbol.id],
                ))

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
    """Count the linker's joinable command sources for the requirement check.

    Three producers feed ``command_by_name``: framework-pattern
    ``concept=command`` symbols (Click/Typer/Django/…), resolvable argparse
    subcommand handlers (WI-lubap), and ``fire.Fire(<Class>)`` public methods
    (WI-sigam). Counting all three keeps the requirement diagnostic honest on an
    argparse-only or fire-only project — otherwise it reports zero command
    sources while the linker is in fact joining handlers.
    """
    count = len(_get_cli_command_symbols(ctx))
    argparse_commands = _scan_argparse_commands(ctx.repo_root, ctx.symbols)
    count += sum(len(handlers) for handlers in argparse_commands.values())
    fire_commands = _scan_fire_commands(ctx.repo_root, ctx.symbols)
    count += sum(len(handlers) for handlers in fire_commands.values())
    return count


SUBPROCESS_REQUIREMENTS = [
    LinkerRequirement(
        name="cli_command_symbols",
        description=(
            "CLI command sources: concept=command symbols (framework patterns), "
            "resolvable argparse subcommand handlers (WI-lubap), and "
            "fire.Fire(<Class>) public methods (WI-sigam)"
        ),
        check=_count_cli_command_symbols,
    ),
]


@register_linker(
    "subprocess-linker",
    priority=65,  # Run after framework patterns have identified CLI commands
    description="Subprocess-to-CLI linking (subprocess.run to Click/Typer commands)",
    requirements=SUBPROCESS_REQUIREMENTS,
    # CNF: subprocess invocations appear in any language with shell-out APIs.
    # CLI handler resolution targets Python (Click/Typer/argparse), JS/TS
    # (commander/yargs), Go (cobra/flag), Java (picocli), Rust (clap), etc.
    depends_on=[["python", "javascript", "ruby", "java", "go", "csharp", "rust", "kotlin", "elixir"]],
)
def subprocess_linker(ctx: LinkerContext) -> LinkerResult:
    """Subprocess linker for registry-based dispatch.

    Links subprocess calls to CLI command handlers in the same project.
    """
    cli_symbols = _get_cli_command_symbols(ctx)
    result = link_subprocess(ctx.repo_root, cli_symbols, all_symbols=ctx.symbols)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
