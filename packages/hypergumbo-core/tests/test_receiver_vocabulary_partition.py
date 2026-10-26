# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gate: every ``Edge.meta['receiver']`` value a producer writes is placed.

WI-sibup. The magnet detector decides whether a call's target type was known
from this one key. It used to read a hand-written subset of the values, and
the subset did not grow with the vocabulary: C++'s ``declared_type``
(WI-lamij) was never added, so ``Logger lg; lg.Close();`` read as an untyped
call and finalize replaced a correct bind with an unresolved stub. A value
missing from a subset is silent; a value missing from a partition that every
value must be in fails here.

AST-based for the reason ``test_skip_reason_code_declaration`` gives: docstrings
quote ``meta["receiver"]="typed_field"`` and a grep would count them.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hypergumbo_core.receiver_blind_magnets import RECEIVER_IDENTIFIES_TARGET

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_DIRS = [
    REPO_ROOT / "packages" / pkg / "src"
    for pkg in (
        "hypergumbo-core",
        "hypergumbo-lang-common",
        "hypergumbo-lang-mainstream",
        "hypergumbo-lang-extended1",
        "hypergumbo-lang-rust-analyzer",
    )
]


def _is_receiver_key(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == "receiver"


def _string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _receiver_values_in(source: str) -> set[str]:
    """The string literals written under a ``"receiver"`` key, as a dict
    entry (``{"receiver": "x"}``) or a subscript store (``m["receiver"] = "x"``).
    A non-literal value is not a vocabulary value (route-extractor usage
    contexts store the receiver's NAME there), so it is skipped."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if _is_receiver_key(key) and _string(value) is not None:
                    found.add(_string(value))
        elif isinstance(node, ast.Assign) and _string(node.value) is not None:
            for target in node.targets:
                if isinstance(target, ast.Subscript) and _is_receiver_key(target.slice):
                    found.add(_string(node.value))
    return found


def _live_values() -> set[str]:
    values: set[str] = set()
    for src in SRC_DIRS:
        for path in sorted(src.rglob("*.py")):
            values |= _receiver_values_in(path.read_text())
    return values


def test_every_written_receiver_value_is_placed() -> None:
    unplaced = _live_values() - set(RECEIVER_IDENTIFIES_TARGET)
    assert not unplaced, (
        "These meta['receiver'] values are written by a producer but not placed "
        "in RECEIVER_IDENTIFIES_TARGET, so the magnet detector reads them as "
        f"receiver-blind by default: {sorted(unplaced)}"
    )


def test_every_placed_value_is_still_written() -> None:
    stale = set(RECEIVER_IDENTIFIES_TARGET) - _live_values()
    assert not stale, f"placed but no longer written by any producer: {sorted(stale)}"


def test_gate_can_fail() -> None:
    """Positive control (LIVE.md §1.6): a walker that has stopped finding
    values would also report nothing unplaced."""
    src = (
        "def f(edges):\n"
        "    edges.append(Edge.create(meta={'receiver': 'brand_new'}))\n"
        "    m = {}\n"
        "    m['receiver'] = 'also_new'\n"
        "    ctx = {'receiver': receiver_name}\n"
    )
    assert _receiver_values_in(src) == {"brand_new", "also_new"}


@pytest.mark.parametrize("src_dir", SRC_DIRS, ids=lambda p: p.parts[-2])
def test_source_root_exists(src_dir: Path) -> None:
    assert src_dir.is_dir(), f"source root missing: {src_dir}"
