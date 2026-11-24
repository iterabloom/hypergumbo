# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/analyze/base.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/spec_validator.py
"""The ``@<column>`` suffix of a node id's name slot (ADR-0036 Ruling 1, as amended).

A node id is ``language:path:lines:name:kind``. Two different nodes of one
file with one name and one kind on one line would share it, and an edge
could not tell them apart. The owner's ruling (2026-11-23): the name slot may
carry ``@<column>``, the node's start column, after the sanitized name.

What is pinned here:

* the factory writes the suffix (``make_symbol_id(..., column=)``,
  ``id_with_column``);
* ``distinguish_same_line_id`` leaves the first holder of an id alone, gives
  a later holder at ANOTHER column the suffix, and leaves a later holder at
  the SAME column alone (one thing emitted twice is not two things: the
  uniqueness check reports it);
* the file pseudo-symbol is never suffixed;
* ``FileAnalysis.add_symbol`` applies the rule and keys ``node_for_symbol``
  by the id the symbol ends with;
* the validator accepts the suffix only when it IS the node's start column,
  and counts the suffixed ids (``name_slot_stats.column_suffixed``).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

from hypergumbo_core.analyze.base import (
    FileAnalysis,
    distinguish_same_line_id,
    distinguish_same_line_ids,
    id_with_column,
    make_file_id,
    make_symbol_id,
)
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.spec_validator import (
    KNOWN_NAME_SLOT_HOLES,
    _check_name_slot_roundtrip,
    _check_node_id_uniqueness,
    _name_slot_mismatch,
    compute_name_slot_stats,
)


def _symbol(name: str, col: int, line: int = 2, kind: str = "function",
            path: str = "a.swift", symbol_id: Optional[str] = None) -> Symbol:
    return Symbol(
        id=symbol_id or make_symbol_id("swift", path, line, line, name, kind),
        name=name, kind=kind, language="swift", path=path,
        span=Span(start_line=line, end_line=line, start_col=col, end_col=col + 5),
        origin="swift",
    )


# --- the factory ---------------------------------------------------------


def test_factory_writes_the_column_after_the_sanitized_name() -> None:
    assert make_symbol_id("swift", "a.swift", 2, 2, "T.init", "constructor", column=15) == (
        "swift:a.swift:2-2:T.init@15:constructor"
    )
    assert make_symbol_id("objc", "a.m", 1, 1, "at:error:", "method", column=0) == (
        "objc:a.m:1-1:at.error.@0:method"
    )


def test_factory_without_a_column_is_unchanged() -> None:
    assert make_symbol_id("swift", "a.swift", 2, 2, "T.init", "constructor") == (
        "swift:a.swift:2-2:T.init:constructor"
    )


def test_id_with_column_keeps_a_colon_bearing_path() -> None:
    assert id_with_column("rust:std::cmp:3-3:max:function", 7) == "rust:std::cmp:3-3:max@7:function"


def test_id_with_column_writes_what_the_factory_writes() -> None:
    """The two ways to a suffixed id give one string, sanitized name included."""
    for name in ("T.init", "at:error:", "operator<<", "user@42"):
        plain = make_symbol_id("swift", "a.swift", 2, 9, name, "method")
        assert id_with_column(plain, 15) == make_symbol_id(
            "swift", "a.swift", 2, 9, name, "method", column=15
        )


# --- telling same-line twins apart ---------------------------------------


def test_the_first_holder_keeps_the_plain_id_and_a_twin_gets_its_column() -> None:
    first, twin, third = _symbol("T.init", 4), _symbol("T.init", 15), _symbol("T.init", 40)
    distinguish_same_line_ids([first, twin, third])
    assert [s.id for s in (first, twin, third)] == [
        "swift:a.swift:2-2:T.init:function",
        "swift:a.swift:2-2:T.init@15:function",
        "swift:a.swift:2-2:T.init@40:function",
    ]


def test_the_same_position_emitted_twice_is_not_two_things() -> None:
    """A producer that emits one node twice keeps one id: the uniqueness
    check reports it, and a suffix would hide it as a false twin."""
    first, again = _symbol("T.init", 4), _symbol("T.init", 4)
    distinguish_same_line_ids([first, again])
    assert first.id == again.id == "swift:a.swift:2-2:T.init:function"
    assert len(_check_node_id_uniqueness([first, again])) == 1


def test_a_twin_emitted_twice_keeps_one_suffixed_id() -> None:
    syms = [_symbol("T.init", 4), _symbol("T.init", 15), _symbol("T.init", 15)]
    distinguish_same_line_ids(syms)
    assert [s.id.rsplit(":", 2)[1] for s in syms] == ["T.init", "T.init@15", "T.init@15"]


def test_other_lines_names_and_kinds_are_left_alone() -> None:
    syms = [_symbol("f", 4), _symbol("f", 4, line=3), _symbol("g", 9), _symbol("f", 20, kind="method")]
    before = [s.id for s in syms]
    distinguish_same_line_ids(syms)
    assert [s.id for s in syms] == before
    assert not any("@" in i for i in before)


def test_a_symbol_without_a_span_is_left_alone() -> None:
    first, spanless = _symbol("f", 4), _symbol("f", 9)
    spanless.span = None
    distinguish_same_line_ids([first, spanless])
    assert spanless.id == first.id


def test_the_file_pseudo_symbol_is_never_suffixed() -> None:
    file_id = make_file_id("swift", "a.swift")
    one = _symbol("a.swift", 0, line=1, kind="file", symbol_id=file_id)
    two = _symbol("a.swift", 3, line=1, kind="file", symbol_id=file_id)
    distinguish_same_line_ids([one, two])
    assert one.id == two.id == file_id


def test_one_holder_table_serves_symbols_added_one_at_a_time() -> None:
    holders: dict[str, int] = {}
    first, twin = _symbol("f", 4), _symbol("f", 25)
    distinguish_same_line_id(first, holders)
    distinguish_same_line_id(twin, holders)
    assert (first.id, twin.id) == ("swift:a.swift:2-2:f:function", "swift:a.swift:2-2:f@25:function")


def test_file_analysis_add_symbol_keys_the_node_by_the_final_id() -> None:
    analysis = FileAnalysis()
    first, twin = _symbol("f", 4), _symbol("f", 25)
    node_a, node_b = object(), object()
    assert analysis.add_symbol(first, node_a) is first
    assert analysis.add_symbol(twin, node_b) is twin
    assert analysis.symbols == [first, twin]
    assert analysis.node_for_symbol == {
        "swift:a.swift:2-2:f:function": node_a,
        "swift:a.swift:2-2:f@25:function": node_b,
    }


def test_file_analysis_add_symbol_without_a_node_registers_none() -> None:
    analysis = FileAnalysis()
    analysis.add_symbol(_symbol("f", 4))
    assert analysis.node_for_symbol == {}


# --- the validator -------------------------------------------------------


def _twins() -> list[Symbol]:
    syms = [_symbol("T.init", 4), _symbol("T.init", 15)]
    distinguish_same_line_ids(syms)
    return syms


def test_the_validator_accepts_the_suffix_that_is_the_start_column() -> None:
    syms = _twins()
    assert [_name_slot_mismatch(s) for s in syms] == [None, None]
    assert _check_name_slot_roundtrip(syms, [object()], holes={}) == []
    assert _check_node_id_uniqueness(syms) == []


def test_the_validator_rejects_a_suffix_that_is_not_the_start_column() -> None:
    wrong = _symbol("T.init", 15, symbol_id="swift:a.swift:2-2:T.init@16:function")
    assert _name_slot_mismatch(wrong) == ("swift", "function")
    found = _check_name_slot_roundtrip([wrong], [object()], holes={})
    assert [(v.observed, v.expected) for v in found] == [("T.init@16", "T.init or T.init@15")]


def test_the_validator_rejects_a_suffix_on_a_node_without_a_span() -> None:
    spanless = SimpleNamespace(
        id="javascript:a.js:2-4:_cb_map@16:function", name="_cb_map", kind="function",
        origin=["javascript"], language="javascript",
    )
    assert _name_slot_mismatch(spanless) == ("javascript", "function")
    found = _check_name_slot_roundtrip([spanless], [object()], holes={})
    assert found[0].expected == "_cb_map"


def test_a_suffix_of_another_shape_is_a_mismatch() -> None:
    for slot in ("T.init@", "T.init@x", "T.init@-1", "T.init@15@15", "@15"):
        sym = _symbol("T.init", 15, symbol_id=f"swift:a.swift:2-2:{slot}:function")
        assert _name_slot_mismatch(sym) is not None, slot


def test_a_name_that_ends_in_at_digits_round_trips_as_itself() -> None:
    sym = _symbol("user@42", 7)
    assert sym.id.endswith(":user@42:function")
    assert _name_slot_mismatch(sym) is None
    assert compute_name_slot_stats([sym])["column_suffixed"] == 0


def test_the_statistic_counts_the_suffixed_ids() -> None:
    wrong = _symbol("g", 15, symbol_id="swift:a.swift:2-2:g@16:function")
    stats = compute_name_slot_stats([*_twins(), _symbol("h", 1), wrong])
    assert (stats["ids_checked"], stats["column_suffixed"], stats["mismatched"]) == (4, 1, 1)


def test_no_name_slot_hole_is_declared() -> None:
    """The JavaScript anonymous callback was the last one: its ``@<column>``
    is now the ruled grammar, checked like every other id."""
    assert KNOWN_NAME_SLOT_HOLES == {}
