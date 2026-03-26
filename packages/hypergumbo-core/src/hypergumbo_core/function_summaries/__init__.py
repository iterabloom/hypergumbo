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
        param_to_return={i: True for i in range(10)},
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
