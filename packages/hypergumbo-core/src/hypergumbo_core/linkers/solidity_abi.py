# SPDX-License-Identifier: AGPL-3.0-or-later
"""Solidity ABI bridge linker for connecting TS/JS contract calls to Solidity functions.

This linker creates ``abi_call`` edges between TypeScript/JavaScript code that
calls Solidity contract methods (via ethers.js, viem, or similar libraries)
and the corresponding Solidity function definitions.

How It Works
------------
Two-phase detection:

1. **Solidity side**: Collects all Solidity ``function`` symbols and builds
   a name-to-symbols map. Constructor, event, and modifier symbols are
   excluded since they're not callable from TS/JS contract interfaces.

2. **TS/JS side**: Scans source files for two call patterns:
   - **ethers.js method calls**: ``variable.functionName(args)`` where
     ``functionName`` matches a known Solidity function name.
   - **viem config calls**: ``functionName: 'solidityFn'`` inside
     ``readContract``/``writeContract``/``simulateContract`` call objects.

After building both maps, the linker creates ``abi_call`` edges from synthetic
TS/JS-side call nodes to the matching Solidity function symbols. Confidence
is 0.75 (name-based matching is heuristic — no ABI artifact parsing).

Why This Design
---------------
- Solidity smart contract ABIs define a stable function interface. TS/JS code
  interacts with contracts via these function names, whether through ethers.js
  (``contract.transfer()``) or viem (``readContract({ functionName: 'transfer' })``).
- Name-based matching is sufficient because Solidity function names in the ABI
  are unique within a contract (overloads share a name but are disambiguated by
  the compiler, not the caller).
- Source scanning is used rather than ABI artifact parsing to avoid dependency
  on build output. This works even when the project hasn't been compiled.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerRequirement,
    LinkerResult,
    register_linker,
)

PASS_ID = make_pass_id("solidity-abi-linker")

# Pattern 1: ethers.js-style method call — variable.functionName(
# We only match when functionName is a known Solidity function (checked post-regex).
_ETHERS_METHOD_CALL = re.compile(
    r"""\.\s*(\w+)\s*\(""",
)

# Pattern 2: viem-style config — functionName: 'solidityFn' or "solidityFn"
_VIEM_FUNCTION_NAME = re.compile(
    r"""functionName\s*:\s*['"](\w+)['"]""",
)

# File extensions to scan for contract calls
_TS_JS_EXTENSIONS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".mts"})


def _collect_solidity_functions(
    symbols: list[Symbol],
) -> dict[str, list[Symbol]]:
    """Build a map of Solidity function names to their Symbol objects.

    Only includes ``function`` and ``constructor`` kinds. Events, modifiers,
    contracts, interfaces, and libraries are excluded since they're not
    callable through the ABI from TS/JS.
    """
    result: dict[str, list[Symbol]] = defaultdict(list)
    for sym in symbols:
        if sym.language != "solidity":
            continue
        if sym.kind not in ("function", "constructor"):
            continue
        result[sym.name].append(sym)
        # Also index by unqualified name for cross-language matching.
        # Solidity functions may have qualified names (ContractName.function)
        # while TS/JS calls use unqualified names (contract.function).
        if "." in sym.name:
            short_name = sym.name.rsplit(".", 1)[-1]
            if short_name not in result or sym not in result[short_name]:
                result[short_name].append(sym)
    return dict(result)


def _scan_contract_calls(
    repo_root: Path,
    known_functions: set[str],
) -> list[tuple[str, str, int]]:
    """Scan TS/JS files for calls to known Solidity functions.

    Returns a list of (relative_path, function_name, line_number) tuples.
    Only matches method calls where the method name appears in
    ``known_functions`` (the set of Solidity function names).
    """
    results: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()

    for ts_file in repo_root.rglob("*"):
        if not ts_file.is_file():
            continue
        suffix = ts_file.suffix
        if suffix not in _TS_JS_EXTENSIONS:
            continue

        try:
            rel_path = str(ts_file.relative_to(repo_root))
        except ValueError:  # pragma: no cover
            continue

        try:
            content = ts_file.read_text(errors="replace")
        except OSError:  # pragma: no cover
            continue

        for line_num, line in enumerate(content.splitlines(), 1):
            # Pattern 1: ethers.js method call .functionName(
            for m in _ETHERS_METHOD_CALL.finditer(line):
                name = m.group(1)
                if name in known_functions:
                    key = (rel_path, name)
                    if key not in seen:
                        seen.add(key)
                        results.append((rel_path, name, line_num))

            # Pattern 2: viem functionName: 'name'
            for m in _VIEM_FUNCTION_NAME.finditer(line):
                name = m.group(1)
                if name in known_functions:
                    key = (rel_path, name)
                    if key not in seen:
                        seen.add(key)
                        results.append((rel_path, name, line_num))

    return results


@dataclass
class SolidityAbiLinkResult:
    """Result of Solidity ABI bridge linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


def link_solidity_abi(
    repo_root: Path,
    ts_js_symbols: list[Symbol],
    sol_symbols: list[Symbol],
) -> SolidityAbiLinkResult:
    """Link TypeScript/JavaScript contract calls to Solidity function definitions.

    Creates ``abi_call`` edges from synthetic TS/JS call-site nodes to Solidity
    function symbols. A synthetic ``abi_call`` node is created at each TS/JS
    call site to anchor the edge source.

    Args:
        repo_root: Path to the repository root.
        ts_js_symbols: All TypeScript/JavaScript symbols.
        sol_symbols: All Solidity symbols.

    Returns:
        SolidityAbiLinkResult with edges, symbols, and run metadata.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    sol_functions = _collect_solidity_functions(sol_symbols)
    if not sol_functions:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return SolidityAbiLinkResult(edges=[], symbols=[], run=run)

    known_names = set(sol_functions.keys())
    call_sites = _scan_contract_calls(repo_root, known_names)

    result_edges: list[Edge] = []
    result_symbols: list[Symbol] = []

    for rel_path, func_name, line_num in call_sites:
        targets = sol_functions.get(func_name, [])
        if not targets:
            continue  # pragma: no cover - should not happen given known_names filter

        # Create synthetic call-site node
        syn_id_input = f"abi_call:{rel_path}:{line_num}:{func_name}"
        syn_hash = hashlib.sha256(syn_id_input.encode()).hexdigest()[:12]
        syn_id = f"typescript:{rel_path}:{line_num}-{line_num}:abi_call:{func_name}:{syn_hash}"

        syn_sym = Symbol(
            id=syn_id,
            name=f"abi_call:{func_name}",
            kind="abi_call",
            language="typescript",
            path=rel_path,
            span=Span(
                start_line=line_num,
                end_line=line_num,
                start_col=0,
                end_col=0,
            ),
            supply_chain_tier=2,
        )
        result_symbols.append(syn_sym)

        # Create edges to each matching Solidity function
        for target in targets:
            result_edges.append(Edge.create(
                src=syn_id,
                dst=target.id,
                edge_type="abi_call",
                line=line_num,
                confidence=0.75,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="abi_name_match",
            ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return SolidityAbiLinkResult(
        edges=result_edges, symbols=result_symbols, run=run,
    )


def _count_ts_js_files(ctx: LinkerContext) -> int:
    """Count TypeScript/JavaScript files."""
    seen: set[str] = set()
    for sym in ctx.symbols:
        if sym.language in ("javascript", "typescript"):
            if sym.path not in seen:
                seen.add(sym.path)
    return len(seen)


def _count_solidity_functions(ctx: LinkerContext) -> int:
    """Count Solidity function symbols."""
    return sum(
        1 for sym in ctx.symbols
        if sym.language == "solidity" and sym.kind in ("function", "constructor")
    )


SOLIDITY_ABI_REQUIREMENTS = [
    LinkerRequirement(
        name="ts_js_files",
        description="TypeScript/JavaScript files (contract callers)",
        check=_count_ts_js_files,
    ),
    LinkerRequirement(
        name="solidity_functions",
        description="Solidity function definitions (ABI targets)",
        check=_count_solidity_functions,
    ),
]


@register_linker(
    "solidity_abi",
    priority=14,
    description=(
        "Solidity ABI bridge - links TypeScript/JavaScript ethers.js and "
        "viem contract calls to Solidity function definitions"
    ),
    requirements=SOLIDITY_ABI_REQUIREMENTS,
    activation=LinkerActivation(
        language_pairs=[
            ("typescript", "solidity"),
            ("javascript", "solidity"),
        ],
    ),
)
def solidity_abi_linker(ctx: LinkerContext) -> LinkerResult:
    """Solidity ABI linker for registry-based dispatch.

    Wraps link_solidity_abi() to use the LinkerContext/LinkerResult interface.
    """
    ts_js_symbols = [
        s for s in ctx.symbols if s.language in ("javascript", "typescript")
    ]
    sol_symbols = [s for s in ctx.symbols if s.language == "solidity"]

    result = link_solidity_abi(ctx.repo_root, ts_js_symbols, sol_symbols)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
