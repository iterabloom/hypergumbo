# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/spec_validator.py
"""The validator's check that no node id is held by two nodes.

A node id is a location key (ADR-0036): edges name their endpoints by it, so
two nodes under one id are two records an edge cannot tell apart. The check
reports each shared id once, at ``error`` in ``cross_field`` like its
uniqueness neighbours (per-file stable ids, one file node per path). File
pseudo-symbols are left to ``one_file_node_per_path``, which already counts
two records of one file id.
"""

import dataclasses
from pathlib import Path

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.spec_validator import (
    _WIRED_CHECKS,
    _check_node_id_uniqueness,
    validate_ir,
)
from hypergumbo_lang_common.vue import analyze_vue

#: Two <span v-if> on one line: two directives, one node id (the id's span
#: slot holds lines and its name slot the element and directive).
_SAME_LINE = """<template>
  <div>
    <span v-if="a">A</span><span v-if="b">B</span>
    <p v-if="c">C</p>
  </div>
</template>
"""


def _sym(sym_id: str, kind: str = "function", path: str = "a.py", **extra: object) -> Symbol:
    return Symbol(
        id=sym_id, name=sym_id.rsplit(":", 2)[1], kind=kind, language="python",
        path=path, span=Span(1, 1, 0, 0), **extra,
    )


def test_real_analyzer_same_line_twins_are_told_apart_and_pass(tmp_path: Path) -> None:
    """Two ``<span v-if>`` on one line shared an id until ADR-0036 Ruling 1
    was amended (2026-11-23): the second carries ``@<column>`` and nothing
    is flagged."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "A.vue").write_text(_SAME_LINE)
    symbols = analyze_vue(tmp_path).symbols
    ids = [s.id for s in symbols]
    # Reach: both spans and the <p> on its own line are emitted.
    assert ids.count("vue:src/A.vue:3-3:span.v-if:directive") == 1
    assert ids.count("vue:src/A.vue:3-3:span.v-if@27:directive") == 1
    assert ids.count("vue:src/A.vue:4-4:p.v-if:directive") == 1
    assert _check_node_id_uniqueness(symbols) == []


def test_real_analyzer_output_with_a_shared_id_is_flagged(tmp_path: Path) -> None:
    """One node emitted twice (the nested-template double walk, WI-supar):
    real analyzer output with one of its nodes repeated at the same place."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "A.vue").write_text(_SAME_LINE)
    symbols = analyze_vue(tmp_path).symbols
    shared = "vue:src/A.vue:4-4:p.v-if:directive"
    (again,) = [s for s in symbols if s.id == shared]
    symbols = [*symbols, dataclasses.replace(again)]

    violations = _check_node_id_uniqueness(symbols)
    assert len(violations) == 1
    (violation,) = violations
    assert (violation.severity, violation.validator_class) == ("error", "cross_field")
    assert violation.record_id == shared
    assert "2 nodes" in violation.message
    assert "vue" in (violation.observed or "")


def test_distinct_ids_and_idless_records_pass() -> None:
    # Control: same name, different lines; and a record without an id.
    assert _check_node_id_uniqueness([
        _sym("python:a.py:1-1:f:function"),
        _sym("python:a.py:2-2:f:function"),
        _sym("python:b.py:1-1:f:function", path="b.py"),
        Symbol(id="", name="x", kind="function", language="python", path="a.py",
               span=Span(1, 1, 0, 0)),
    ]) == []


def test_file_pseudo_symbols_are_left_to_their_own_check() -> None:
    # Two records of one file id are one_file_node_per_path's violation;
    # reporting them here too would count one defect twice.
    file_id = "python:a.py:1-1:file:file"
    twins = [_sym(file_id, kind="file"), _sym(file_id, kind="file")]
    assert _check_node_id_uniqueness(twins) == []
    assert any("file pseudo-symbols" in v.message for v in validate_ir(twins, [], []))


def test_one_violation_per_shared_id_in_id_order() -> None:
    a = "python:a.py:3-3:g:function"
    b = "python:a.py:1-1:f:function"
    violations = _check_node_id_uniqueness([_sym(a), _sym(b), _sym(a), _sym(b), _sym(b)])
    assert [v.record_id for v in violations] == [b, a]
    assert "3 nodes" in violations[0].message


def test_wired_into_validate_ir_and_disclosed() -> None:
    shared = "python:a.py:1-1:f:function"
    flagged = [
        v for v in validate_ir([_sym(shared), _sym(shared)], [], [])
        if v.record_id == shared and "node id" in v.message
    ]
    assert len(flagged) == 1
    (entry,) = [c for c in _WIRED_CHECKS if c["check"] == "node_id_uniqueness"]
    assert entry["validator_class"] == "cross_field"
