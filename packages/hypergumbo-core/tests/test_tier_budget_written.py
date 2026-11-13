# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tiered survey file is within its token budget ON DISK (INV-nukuk).

A tier is written with ``indent=2, sort_keys=True``
(``safety_zones.user_out_open_json_dump``). The budget used to be checked
against ``json.dumps(map)`` with default separators, a form about a quarter
smaller than the file, so a tier could pass the check and exceed its budget
once written. And the check could only remove nodes: a map whose other
sections alone exceed the budget was written over budget and said nothing.

Every size assertion here is on the bytes of a file written by the real
writer, never on an estimate of them.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core import compact
from hypergumbo_core.compact import format_tiered_behavior_map
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.safety_zones import user_out_open_json_dump
from hypergumbo_core.selection.token_budget import (
    CHARS_PER_TOKEN,
    estimate_json_tokens,
    estimate_written_json_tokens,
    written_json_text,
)


def _symbol(name: str, path: str = "src/main.py") -> Symbol:
    return Symbol(
        id=f"python:{path}:1-10:{name}:function", name=name, kind="function",
        language="python", path=path,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
    )


def _edge(src: Symbol, dst: Symbol) -> Edge:
    return Edge(id=f"edge:{src.id}->{dst.id}", src=src.id, dst=dst.id,
                edge_type="calls", line=1, confidence=0.9, origin="test", origin_run_id="test")


def _dense_map(nodes: int = 60, fan_out: int = 8, **extra: Any) -> tuple[dict, list, list]:
    symbols = [_symbol(f"func_{i}") for i in range(nodes)]
    edges = [_edge(symbols[i], symbols[(i + j) % nodes])
             for i in range(nodes) for j in range(1, fan_out + 1)]
    behavior_map = {
        "nodes": [s.to_dict() for s in symbols],
        "edges": [e.to_dict() for e in edges],
        "entrypoints": [{"symbol_id": s.id, "kind": "main_function", "confidence": 0.9}
                        for s in symbols[:5]],
        **extra,
    }
    return behavior_map, symbols, edges


def _tokens_on_disk(tiered: dict, tmp_path: Path) -> int:
    out = tmp_path / "survey.tier.json"
    user_out_open_json_dump(out, tiered)
    return out.stat().st_size // CHARS_PER_TOKEN


# ── the written form has one definition ──────────────────────────────────────

def test_written_json_text_is_what_the_writer_writes(tmp_path: Path) -> None:
    """If the writer's layout changes and this helper does not, every budget
    check is measuring a file that is not written."""
    data = {"b": [1, {"y": None, "x": "é"}], "a": {"k": [[], {}]}, "c": "text"}
    out = tmp_path / "x.json"
    user_out_open_json_dump(out, data)
    assert out.read_text() == written_json_text(data)
    assert estimate_written_json_tokens(data) == out.stat().st_size // CHARS_PER_TOKEN


def test_the_written_form_is_larger_than_the_default_dump() -> None:
    """Reach: the two forms differ, so a check on one says nothing of the other."""
    behavior_map, _symbols, _edges = _dense_map()
    assert estimate_written_json_tokens(behavior_map) > estimate_json_tokens(behavior_map)


# ── a tier fits its budget as written ────────────────────────────────────────

@pytest.mark.parametrize("budget", [4000, 8000, 16000])
def test_a_tier_file_is_within_its_budget_on_disk(tmp_path: Path, budget: int) -> None:
    behavior_map, symbols, edges = _dense_map()
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, budget)
    # Reach: the budget binds (nodes were left out) and the tier is not degenerate.
    assert 1 < len(tiered["nodes"]) < len(symbols)
    assert _tokens_on_disk(tiered, tmp_path) <= budget


def test_a_budget_the_whole_map_fits_removes_nothing(tmp_path: Path) -> None:
    behavior_map, symbols, edges = _dense_map(nodes=6, fan_out=2)
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, 64000)
    assert len(tiered["nodes"]) == len(symbols)
    assert _tokens_on_disk(tiered, tmp_path) <= 64000


# ── a tier says what it reached ──────────────────────────────────────────────

@pytest.mark.parametrize("budget", [4000, 16000, 64000])
def test_a_tier_records_the_size_it_was_written_at(tmp_path: Path, budget: int) -> None:
    behavior_map, symbols, edges = _dense_map()
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, budget)
    assert tiered["tier_tokens"] == budget
    assert tiered["tier_tokens_used"] == _tokens_on_disk(tiered, tmp_path)


def test_a_budget_the_fixed_sections_exceed_is_reported_not_hidden(tmp_path: Path) -> None:
    """The shrink can only remove nodes. Sections that do not depend on the
    selection (here ``limits``) can exceed the budget on their own; the file
    is then over budget, and it must say so in a field a reader can check."""
    fixed = {"skipped_passes": [{"pass": f"analyzer-{i}", "reason": "no files matched"}
                                for i in range(400)]}
    behavior_map, symbols, edges = _dense_map(limits=fixed)
    budget = 4000
    assert estimate_written_json_tokens({"limits": fixed}) > budget  # reach
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, budget)
    assert len(tiered["nodes"]) == 1
    on_disk = _tokens_on_disk(tiered, tmp_path)
    assert on_disk > budget
    assert tiered["tier_tokens_used"] == on_disk
    assert tiered["tier_tokens_used"] > tiered["tier_tokens"]


# ── the running size the shrink loop keeps is exact ─────────────────────────

def _random_record(rng: random.Random, depth: int = 0) -> Any:
    kinds = ["str", "int", "none", "bool", "float"]
    if depth < 3:
        kinds += ["dict", "list", "dict", "list"]
    kind = rng.choice(kinds)
    if kind == "str":
        return "".join(rng.choice('ab "\\\n\té/:{}[],') for _ in range(rng.randint(0, 12)))
    if kind == "int":
        return rng.randint(-10**6, 10**6)
    if kind == "none":
        return None
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "float":
        return round(rng.random() * 100, 4)
    if kind == "dict":
        return {f"k{i}": _random_record(rng, depth + 1) for i in range(rng.randint(0, 4))}
    return [_random_record(rng, depth + 1) for _ in range(rng.randint(0, 4))]


@pytest.mark.parametrize("seed", range(40))
def test_the_running_size_equals_the_written_size(seed: int) -> None:
    """Records added and removed in any order: the running size is the length
    of the text the writer would produce for exactly what is left."""
    rng = random.Random(seed)  # noqa: S311 # nosec B311 - a reproducible fixture, not a secret
    arrays = {name: [{"id": f"{name}-{i}", "v": _random_record(rng)}
                     for i in range(rng.randint(0, 6))]
              for name in compact._WrittenTierSize.ARRAYS}
    shell = {"view": "tiered", "limits": _random_record(rng), "zz": [1, 2]}
    size = compact._WrittenTierSize(shell)
    for name, records in arrays.items():
        for record in records:
            size.add(name, record)
    assert size.chars == len(written_json_text({**shell, **arrays}))
    for name, records in arrays.items():
        while records and rng.random() < 0.6:
            size.remove(name, records.pop(rng.randrange(len(records))))
            assert size.chars == len(written_json_text({**shell, **arrays}))
    assert size.tokens == estimate_written_json_tokens({**shell, **arrays})


def test_the_running_size_ignores_arrays_already_in_the_shell() -> None:
    """The shell is measured with the three arrays empty, whatever it was
    handed, so a caller cannot count a record twice."""
    shell = {"view": "tiered", "nodes": [{"id": "stale"}], "edges": [], "entrypoints": []}
    size = compact._WrittenTierSize(shell)
    assert size.chars == len(written_json_text({**shell, "nodes": []}))


# ── the finished map is re-checked ───────────────────────────────────────────

def test_a_finished_map_larger_than_the_running_size_is_shrunk_again(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The summary is re-derived after the shrink and can be larger than the
    scratch copy the loop measured. The budget is checked on the finished map."""
    real = compact.recompute_view_summary

    def bulky(view_map: dict, *args: Any, **kwargs: Any) -> None:
        real(view_map, *args, **kwargs)
        view_map["nodes_summary"]["padding"] = "x" * 6000

    behavior_map, symbols, edges = _dense_map()
    budget = 8000
    plain = format_tiered_behavior_map(behavior_map, symbols, edges, budget)
    monkeypatch.setattr(compact, "recompute_view_summary", bulky)
    padded = format_tiered_behavior_map(behavior_map, symbols, edges, budget)
    assert 1 < len(padded["nodes"]) < len(plain["nodes"])  # reach: it had to give nodes up
    assert _tokens_on_disk(padded, tmp_path) <= budget
    assert padded["tier_tokens_used"] == _tokens_on_disk(padded, tmp_path)


def test_the_arrays_agree_with_each_other_after_the_shrink() -> None:
    behavior_map, symbols, edges = _dense_map()
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, 4000)
    ids = {n["id"] for n in tiered["nodes"]}
    assert all(e["src"] in ids and e["dst"] in ids for e in tiered["edges"])
    assert all(ep["symbol_id"] in ids for ep in tiered["entrypoints"])
    assert tiered["nodes_summary"]["included"]["count"] == len(ids)
    assert json.loads(written_json_text(tiered)) == tiered
