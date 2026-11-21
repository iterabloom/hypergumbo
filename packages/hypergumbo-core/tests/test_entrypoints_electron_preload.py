# SPDX-License-Identifier: AGPL-3.0-or-later
"""``electron_preload`` has a producer (audit 0027, WI-jilik).

electron.yaml's usage rows stamp ``api_bridge`` (a ``contextBridge``
exposure in the file) and ``preload_script`` (the file a window's
``webPreferences.preload`` names) on the preload FILE symbol; the concept
detector maps both to ``ELECTRON_PRELOAD``. The entry replaces, never
duplicates, the ``script_module`` entry the same file gets from its shape:
the per-symbol dedup keeps the more confident (0.90 framework pattern over
0.80 structural).
"""
from __future__ import annotations

import pytest

from hypergumbo_core.entrypoints import EntrypointKind, detect_entrypoints
from hypergumbo_core.framework_patterns import (
    clear_pattern_cache,
    load_framework_patterns,
)
from hypergumbo_core.ir import Edge, Span, Symbol


def _file(path: str, concepts: list[dict] | None = None) -> Symbol:
    return Symbol(
        id=f"javascript:{path}:1-1:file:file", name=path, kind="file",
        language="javascript", path=path, span=Span(1, 1, 0, 0),
        meta={"concepts": concepts} if concepts else None,
    )


def _call(src: str, dst: str) -> Edge:
    return Edge.create(src=src, dst=dst, edge_type="calls", line=1,
                       evidence_type="ast_call_direct", confidence=0.9,
                       origin="javascript-v1", origin_run_id="uuid:test")


@pytest.mark.parametrize("concept", ["api_bridge", "preload_script"])
def test_either_concept_makes_the_file_a_preload_entry(concept: str) -> None:
    pre = _file("src/preload.js", [{"concept": concept, "framework": "electron"}])
    helper = Symbol(id="javascript:src/h.js:1-2:h:function", name="h",
                    kind="function", language="javascript", path="src/h.js",
                    span=Span(1, 2, 0, 0))
    entries = detect_entrypoints([pre, helper], [_call(pre.id, helper.id)])
    mine = [ep for ep in entries if ep.symbol_id == pre.id]
    assert [(ep.kind, ep.label, ep.confidence) for ep in mine] == [
        (EntrypointKind.ELECTRON_PRELOAD,
         "Electron preload script (src/preload.js)", 0.90)]
    assert mine[0].meta["evidence_type"] == "framework_pattern"
    assert mine[0].meta["source"] == "concept_detector"


def test_both_concepts_on_one_file_make_one_entry() -> None:
    pre = _file("preload.js", [
        {"concept": "api_bridge", "framework": "electron"},
        {"concept": "preload_script", "framework": "electron"},
    ])
    entries = detect_entrypoints([pre], [])
    assert [ep.kind for ep in entries if ep.symbol_id == pre.id] == [
        EntrypointKind.ELECTRON_PRELOAD]


def test_a_file_without_the_concepts_stays_a_script_module() -> None:
    plain = _file("renderer.js")
    helper = Symbol(id="javascript:h.js:1-2:h:function", name="h",
                    kind="function", language="javascript", path="h.js",
                    span=Span(1, 2, 0, 0))
    entries = detect_entrypoints([plain, helper], [_call(plain.id, helper.id)])
    assert [ep.kind for ep in entries if ep.symbol_id == plain.id] == [
        EntrypointKind.SCRIPT_MODULE]


def test_electron_yaml_keys_both_concepts_on_usage() -> None:
    """The rows are usage-keyed (the analyzer's caller-position and
    webPreferences.preload UsageContexts), not ``decorator:`` rows, which
    a call never matches."""
    clear_pattern_cache()
    patterns = load_framework_patterns("electron")
    assert patterns is not None
    rows = {p.concept: p for p in patterns.patterns
            if p.concept in ("api_bridge", "preload_script")}
    assert set(rows) == {"api_bridge", "preload_script"}
    for row in rows.values():
        assert row.decorator is None
        assert row.usage is not None
