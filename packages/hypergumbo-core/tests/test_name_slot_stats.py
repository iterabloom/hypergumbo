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


# ----------------------------------------------------------------------
# The check: an undeclared mismatch is an id_format error
# ----------------------------------------------------------------------

_RUNS = [SimpleNamespace(execution_id="run-1")]


def test_the_check_reports_an_undeclared_mismatch_as_an_id_format_error() -> None:
    from hypergumbo_core.spec_validator import _check_name_slot_roundtrip

    violations = _check_name_slot_roundtrip(_shapes(), _RUNS, holes={})
    # the four mismatching shapes; round-trips, the file node and the short id
    # raise nothing
    assert sorted(v.record_id for v in violations) == sorted([
        "javascript:app.js:2-4:_cb_forEach@16:function",
        "javascript:app.js:9-9:results:focus:function",
        "json:package.json:3-3:main.js:file",
        "vue:x.vue:10-13:viz-node.v-bind.boxHeight:directive",
    ])
    assert {(v.severity, v.validator_class, v.field_name) for v in violations} == {
        ("error", "id_format", "Symbol.id"),
    }
    (cb,) = [v for v in violations if v.record_id.endswith("@16:function")]
    assert (cb.observed, cb.expected) == ("_cb_forEach@16", "_cb_forEach")


def test_a_declared_hole_silences_its_own_pair_only() -> None:
    from hypergumbo_core.spec_validator import _check_name_slot_roundtrip

    holes = {("javascript", "function"): ("callbacks", "WI-bafaz")}
    violations = _check_name_slot_roundtrip(_shapes(), _RUNS, holes=holes)
    assert sorted(v.record_id.split(":", 1)[0] for v in violations) == [
        "javascript", "json", "vue",
    ]
    # the event-sourcing node is javascript-language but another producer
    assert any("results:focus" in v.record_id for v in violations)


def test_the_check_is_gated_on_a_run_set() -> None:
    """Unit fixtures call validate_ir with no runs and hand-written ids."""
    from hypergumbo_core.spec_validator import _check_name_slot_roundtrip

    assert _check_name_slot_roundtrip(_shapes(), [], holes={}) == []


def test_validate_ir_runs_the_check_with_the_declared_holes() -> None:
    from hypergumbo_core.spec_validator import (
        KNOWN_NAME_SLOT_HOLES,
        _check_name_slot_roundtrip,
        validate_ir,
    )

    assert ("javascript", "function") in KNOWN_NAME_SLOT_HOLES
    found = sorted(v.record_id for v in validate_ir(_shapes(), [], _RUNS)
                   if "Ruling 1" in v.message)
    # the callback is a declared hole; the event-sourcing node is not
    assert "javascript:app.js:9-9:results:focus:function" in found
    assert not any("_cb_forEach" in r for r in found)
    assert found == sorted(v.record_id for v in _check_name_slot_roundtrip(_shapes(), _RUNS))
