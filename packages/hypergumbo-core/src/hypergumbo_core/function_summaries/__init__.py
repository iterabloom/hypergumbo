# SPDX-License-Identifier: AGPL-3.0-or-later
"""YAML-declared function summaries for interprocedural taint propagation (ADR-0017 §4b).

Function summaries describe how data flows through a function's interface
(parameters → return value, parameters → other parameters, parameters → calls).
They bridge intraprocedural DDG analysis and interprocedural taint tracking.

How It Works
------------
1. ``load_function_summaries()`` reads YAML files declaring taint-flow summaries
   for stdlib and framework functions whose source code is not analyzed.

2. ``FunctionSummary`` objects describe parameter-to-return flow, parameter
   mutation flow, sanitization effects, and callback propagation.

3. The taint solver uses summaries at call sites: when a tainted variable
   is passed as an argument, the summary determines whether the taint
   propagates to the return value, mutates another parameter, or is
   sanitized.

4. Functions without summaries use **default-conservative** behavior:
   all parameters are assumed to flow to the return value.

YAML Format
-----------
See ``function_summaries/*.yaml`` files and ADR-0017 §4b for the schema.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class CallbackFlow:
    """Describes how data flows through a callback parameter.

    Used for higher-order functions like ``Array.map``, ``Promise.then``.
    """

    param_index: int
    caller_to_callback_args: dict[str, list[int]] = field(default_factory=dict)
    callback_return_to_outer_return: bool = False


@dataclass
class SanitizeEffect:
    """Describes a sanitization effect on a parameter.

    When the function is called with a tainted argument at position
    ``param_index``, the taint label is transformed from ``from_taint``
    to ``to_taint``.
    """

    param_index: int
    from_taint: str
    to_taint: str


@dataclass
class FunctionSummary:
    """How a function propagates taint between its interface points.

    Describes data flow from parameters to return values, other parameters,
    and call arguments. Used by the taint solver at call sites.

    Attributes:
        function: Qualified function name (e.g., "JSON.stringify").
        param_to_return: Maps param index → True if param flows to return.
        param_to_self: Maps param index → True if param flows to self/receiver.
        mutates_self: Whether the function mutates its receiver.
        side_effect: Whether the function has side effects (I/O, logging).
        sanitizes: List of sanitization effects.
        callback: Optional callback flow description.
    """

    function: str
    param_to_return: dict[int, bool] = field(default_factory=dict)
    param_to_self: dict[int, bool] = field(default_factory=dict)
    mutates_self: bool = False
    side_effect: bool = False
    sanitizes: list[SanitizeEffect] = field(default_factory=list)
    callback: Optional[CallbackFlow] = None


# Conservative default: all params flow to return
_DEFAULT_SUMMARY = FunctionSummary(function="<default>")


def get_default_summary(function_name: str) -> FunctionSummary:
    """Return a conservative default summary for an undeclared function.

    Conservative assumption: every parameter flows to the return value.
    """
    return FunctionSummary(
        function=function_name,
        param_to_return=dict.fromkeys(range(10), True),
    )


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

_SUMMARY_CACHE: dict[str, dict[str, FunctionSummary]] = {}


def get_summaries_dir() -> Path:
    """Return the path to the function_summaries/ directory."""
    return Path(__file__).parent


def load_function_summaries(
    search_dir: Optional[Path] = None,
) -> dict[str, FunctionSummary]:
    """Load all function summaries from YAML files in a directory.

    Returns a dict mapping function name → FunctionSummary.
    Caches results to avoid repeated disk I/O.
    """
    search = search_dir or get_summaries_dir()
    cache_key = str(search)
    if cache_key in _SUMMARY_CACHE:
        return _SUMMARY_CACHE[cache_key]

    result: dict[str, FunctionSummary] = {}
    if not search.is_dir():
        _SUMMARY_CACHE[cache_key] = result
        return result

    for yaml_path in sorted(search.glob("*.yaml")):
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or "summaries" not in data:
            continue
        for entry in data["summaries"]:
            summary = _parse_summary(entry)
            result[summary.function] = summary
            # Also index by short name (last component)
            if "." in summary.function:
                short = summary.function.rsplit(".", 1)[-1]
                result.setdefault(short, summary)

    _SUMMARY_CACHE[cache_key] = result
    return result


def clear_summary_cache() -> None:
    """Clear the summary cache (useful for tests)."""
    _SUMMARY_CACHE.clear()


def _parse_summary(data: dict[str, Any]) -> FunctionSummary:
    """Parse a single function summary from YAML data."""
    sanitizes: list[SanitizeEffect] = []
    for idx_str, san_data in data.get("sanitizes", {}).items():
        sanitizes.append(SanitizeEffect(
            param_index=int(idx_str),
            from_taint=san_data["from"],
            to_taint=san_data["to"],
        ))

    callback = None
    cb_data = data.get("callback")
    if cb_data:
        callback = CallbackFlow(
            param_index=cb_data["param_index"],
            caller_to_callback_args={
                str(k): v for k, v in cb_data.get("caller_to_callback_args", {}).items()
            },
            callback_return_to_outer_return=cb_data.get("callback_return_to_outer_return", False),
        )

    return FunctionSummary(
        function=data["function"],
        param_to_return={int(k): v for k, v in data.get("param_to_return", {}).items()},
        param_to_self={int(k): v for k, v in data.get("param_to_self", {}).items()},
        mutates_self=data.get("mutates_self", False),
        side_effect=data.get("side_effect", False),
        sanitizes=sanitizes,
        callback=callback,
    )


# ---------------------------------------------------------------------------
# Summary inference from DDG (ADR-0017 §4a)
# ---------------------------------------------------------------------------


def infer_summary(
    function_name: str,
    param_names: list[str],
    ddg_edges: list,
    cfg: Any,
) -> FunctionSummary:
    """Infer a FunctionSummary from DDG edges and a FunctionCfg.

    Walks forward through DDG edges from each parameter to determine
    which params flow to the return value. This is the automated
    complement to YAML-declared summaries.

    Algorithm (ADR-0017 §4a):
    1. For each parameter P, find DDG edges where P is defined.
    2. Walk forward through DDG edges: if ``def_var == P`` at some block/line,
       find all uses at other blocks/lines. Continue transitively.
    3. If any use reaches a block containing a return statement,
       record ``param_to_return[P] = True``.
    4. ``return_sources`` is the list of param indices that flow to return.

    Args:
        function_name: The function's qualified name for the summary.
        param_names: Ordered list of parameter variable names.
        ddg_edges: DdgEdge objects from ``solve_reaching_defs()``.
        cfg: A FunctionCfg with populated blocks.

    Returns:
        An inferred FunctionSummary.
    """
    # Find blocks that contain return statements
    return_blocks: set[str] = set()
    for block in cfg.blocks.values():
        for stmt in block.statements:
            if stmt.node_type in ("return_statement", "return_expression"):
                return_blocks.add(block.id)

    # Build DDG forward map: (block, variable) → list of (use_block, use_line, variable)
    ddg_forward: dict[tuple[str, str], list[tuple[str, int, str]]] = {}
    for edge in ddg_edges:
        key = (edge.def_block, edge.variable)
        ddg_forward.setdefault(key, []).append(
            (edge.use_block, edge.use_line, edge.variable)
        )

    param_to_return: dict[int, bool] = {}
    return_sources: list[int] = []

    for idx, param in enumerate(param_names):
        # Walk forward from this parameter through DDG edges
        if _param_reaches_return(param, ddg_edges, ddg_forward, return_blocks):
            param_to_return[idx] = True
            return_sources.append(idx)

    return FunctionSummary(
        function=function_name,
        param_to_return=param_to_return,
    )


def _param_reaches_return(
    param_name: str,
    ddg_edges: list,
    ddg_forward: dict[tuple[str, str], list[tuple[str, int, str]]],
    return_blocks: set[str],
) -> bool:
    """Check if a parameter reaches a return statement via DDG edges.

    BFS through DDG edges starting from all definitions of ``param_name``.
    Follows transitive data flow: if ``x`` flows to a use site where ``y``
    is defined (e.g., ``y = x``), continues tracking ``y``.
    """
    # Build a map from (use_block, use_line) → variables defined at that location
    # This enables transitive flow tracking: x used at line 2 → y defined at line 2
    defs_at_location: dict[tuple[str, int], list[str]] = {}
    for edge in ddg_edges:
        defs_at_location.setdefault((edge.def_block, edge.def_line), []).append(edge.variable)

    # Start BFS from all definitions of the parameter
    visited: set[tuple[str, str]] = set()
    queue: list[tuple[str, str]] = []

    for edge in ddg_edges:
        if edge.variable == param_name:
            key = (edge.def_block, edge.variable)
            if key not in visited:
                visited.add(key)
                queue.append(key)

    while queue:
        current = queue.pop(0)

        # Check all uses reachable from this definition
        for use_block, use_line, _use_var in ddg_forward.get(current, []):
            if use_block in return_blocks:
                return True

            # Find variables defined at the use site (transitive flow)
            for defined_var in defs_at_location.get((use_block, use_line), []):
                next_key = (use_block, defined_var)
                if next_key not in visited:
                    visited.add(next_key)
                    queue.append(next_key)

    return False
