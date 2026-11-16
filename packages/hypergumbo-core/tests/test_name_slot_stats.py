# SPDX-License-Identifier: AGPL-3.0-or-later
"""``validation_report.name_slot_stats``: the ADR-0036 Ruling-1 name-slot measurement.

Ruling 1 makes a node id's NAME slot ``sanitize_id_name_segment(Symbol.name)``
(``:`` becomes ``.``, nothing else). The validator reports, per run, how many
ids do not round-trip and which producers mint them, as a statistic rather than
one violation per node: thousands of JavaScript anonymous callbacks
(``_cb_forEach@16`` in the slot, ``_cb_forEach`` in the name) would otherwise
bury every other entry of the report.

The fixtures below are one node per shape the measurement must see: a
round-tripping node, a colon name that round-trips through the sanitizer, the
file pseudo-symbol (exempt: the grammar fixes its name slot to ``file``), a
manifest entry of kind ``file`` (not exempt), a suffixed slot, a raw colon in
the slot, a prefixed slot, an external stand-in, and an id too short to parse.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.spec_validator import (
    _NAME_SLOT_PRODUCER_CAP,
    build_validation_report,
    compute_name_slot_stats,
)


def _sym(sym_id: str, name: Any, kind: str, origin: Any = None,
         language: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=sym_id, name=name, kind=kind, origin=origin, language=language,
    )


def _shapes() -> list[SimpleNamespace]:
    return [
        # round-trips
        _sym("python:a.py:1-2:f:function", "f", "function", ["python"], "python"),
        # a colon name round-trips through the sanitizer
        _sym("python:q.py:3-3:kafka.publish.topic:function", "kafka:publish:topic",
             "function", "message-queue-linker", "python"),
        # an external stand-in: the boundary synthesizer sanitizes the name slot
        _sym("rust:external:0-0:Vec..with_capacity:external_symbol",
             "Vec::with_capacity", "external_symbol",
             ["boundary_external_symbol_synthesis"], "rust"),
        # the file pseudo-symbol: exempt (name slot is the literal ``file``)
        _sym("python:a.py:1-1:file:file", "a.py", "file", ["python"], "python"),
        # a manifest entry of kind ``file``: NOT the pseudo-symbol, so checked
        _sym("json:package.json:3-3:main.js:file", "main", "file", ["json"], "json"),
        # suffixed slot
        _sym("javascript:app.js:2-4:_cb_forEach@16:function", "_cb_forEach",
             "function", ["javascript"], "javascript"),
        # raw colon in the slot (the right-anchored parse reads ``focus``)
        _sym("javascript:app.js:9-9:results:focus:function", "results:focus",
             "function", ["event-sourcing-linker"], "javascript"),
        # prefixed slot, no origin: the producer falls back to the language
        _sym("vue:x.vue:10-13:viz-node.v-bind.boxHeight:directive",
             "v-bind:boxHeight", "directive", None, "vue"),
        # fewer than five slots: owned by the id_format check, not parsed here
        _sym("not-an-id", "x", "function", ["python"], "python"),
    ]


def test_counts_each_shape() -> None:
    stats = compute_name_slot_stats(_shapes())
    assert stats["ids_checked"] == 7
    assert stats["exempt_file_nodes"] == 1
    assert stats["not_parsed"] == 1
    assert stats["mismatched"] == 4
    by = {p["producer"]: p for p in stats["by_producer"]}
    assert set(by) == {"javascript", "event-sourcing-linker", "vue", "json"}
    assert by["javascript"]["mismatched"] == 1
    assert by["javascript"]["checked"] == 1
    assert by["javascript"]["example_id"].endswith(":_cb_forEach@16:function")
    assert by["javascript"]["example_name"] == "_cb_forEach"
    assert by["event-sourcing-linker"]["example_name"] == "results:focus"
    assert by["vue"]["example_name"] == "v-bind:boxHeight"
    assert by["json"]["example_id"] == "json:package.json:3-3:main.js:file"
    assert stats["producers_cut"] == 0


def test_round_tripping_set_reports_zero() -> None:
    stats = compute_name_slot_stats(_shapes()[:4])
    assert stats["mismatched"] == 0
    assert stats["by_producer"] == []
    assert stats["ids_checked"] == 3


def test_name_absent_compares_against_empty_slot() -> None:
    """A missing name is compared as ''; a missing id is not counted at all."""
    stats = compute_name_slot_stats([
        _sym("go:m.go:1-1::function", None, "function", "go", "go"),
        _sym("go:m.go:2-2:g:function", None, "function", None, None),
        _sym(None, "h", "function", "go", "go"),  # no id: nothing to parse
    ])
    assert stats["ids_checked"] == 2
    assert stats["not_parsed"] == 0
    assert stats["mismatched"] == 1
    assert stats["by_producer"][0]["producer"] == "<none>"


def test_producer_list_is_capped_and_the_cut_is_reported() -> None:
    extra = 7
    syms = [
        _sym(f"python:p{i}.py:1-1:slot{i}:function", f"name{i}", "function",
             [f"pass-{i:02d}"], "python")
        for i in range(_NAME_SLOT_PRODUCER_CAP + extra)
    ]
    stats = compute_name_slot_stats(syms)
    assert len(stats["by_producer"]) == _NAME_SLOT_PRODUCER_CAP
    assert stats["producers_cut"] == extra
    assert stats["mismatched"] == _NAME_SLOT_PRODUCER_CAP + extra


def test_order_and_examples_do_not_depend_on_input_order() -> None:
    syms = _shapes() + [
        _sym("javascript:b.js:5-9:_cb_then@3:function", "_cb_then", "function",
             ["javascript"], "javascript"),
        _sym("javascript:a.js:1-2:_cb_map@1:function", "_cb_map", "function",
             ["javascript"], "javascript"),
    ]
    forward = compute_name_slot_stats(syms)
    backward = compute_name_slot_stats(list(reversed(syms)))
    assert forward == backward
    # most mismatches first, ties by producer name
    producers = [p["producer"] for p in forward["by_producer"]]
    assert producers == ["javascript", "event-sourcing-linker", "json", "vue"]
    assert forward["by_producer"][0]["example_id"] == (
        "javascript:a.js:1-2:_cb_map@1:function"
    )


def test_report_carries_the_block() -> None:
    stats = compute_name_slot_stats(_shapes())
    report = build_validation_report([], name_slot_stats=stats)
    assert report["name_slot_stats"] == stats
    assert build_validation_report([])["name_slot_stats"] is None


def test_emitted_map_counts_a_javascript_anonymous_callback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text(
        "function main(items) {\n"
        "  items.forEach(function (x) {\n"
        "    console.log(x);\n"
        "  });\n"
        "}\n"
        "module.exports = { main };\n"
    )
    out = tmp_path / "map.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    bm = json.loads(out.read_text())
    callbacks = [
        n for n in bm["nodes"]
        if n["kind"] == "function" and n["name"].startswith("_cb_")
    ]
    assert callbacks, "reach: the fixture's anonymous callback was not emitted"
    stats = bm["validation_report"]["name_slot_stats"]
    assert (
        stats["ids_checked"] + stats["exempt_file_nodes"] + stats["not_parsed"]
        == len(bm["nodes"])
    )
    assert stats["exempt_file_nodes"] >= 1
    by = {p["producer"]: p for p in stats["by_producer"]}
    assert by["javascript"]["mismatched"] >= len(callbacks)
