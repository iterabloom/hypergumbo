# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/swift.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
"""Two declarations with one name on one line get two node ids.

A node id is ``language:path:lines:name:kind``: two Swift ``init``s on one
line (vapor's ``init() {}; init() where K == BasicCodingKey {}``), or two
overloads of one function, had ONE id, and the second's syntax node replaced
the first's in ``FileAnalysis.node_for_symbol``. ADR-0036 Ruling 1 (amended
2026-11-23) lets the name slot carry ``@<column>``: the first declaration
keeps the plain id, a twin gets its own start column.

JavaScript anonymous callbacks always carry the column (their names are
synthetic); pinned here so the suffix stays the node's start column, which is
what the validator checks.

Each test has a control that must stay unsuffixed.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.js_ts import analyze_javascript
from hypergumbo_lang_mainstream.swift import analyze_swift

_SWIFT = (
    "struct FailureEncoder<K: CodingKey> {\n"
    "    init() {}; init() where K == BasicCodingKey {}\n"
    "    func f(_ a: Int) {}; func f(_ a: String) {}\n"
    "    func alone() {}\n"
    "}\n"
    "struct Apart {\n"
    "    init() {}\n"
    "    init(x: Int) {}\n"
    "}\n"
)


def _swift_symbols(tmp_path: Path):
    (tmp_path / "A.swift").write_text(_SWIFT)
    result = analyze_swift(tmp_path)
    return [s for s in result.symbols if s.kind != "file"]


def test_swift_same_line_initialisers_get_two_ids(tmp_path: Path) -> None:
    inits = [s for s in _swift_symbols(tmp_path) if s.name == "FailureEncoder.init"]
    assert [(s.span.start_col, s.id.rsplit(":", 2)[1]) for s in inits] == [
        (4, "FailureEncoder.init"),
        (15, "FailureEncoder.init@15"),
    ]


def test_swift_same_line_overloads_get_two_ids(tmp_path: Path) -> None:
    overloads = [s for s in _swift_symbols(tmp_path) if s.name == "FailureEncoder.f"]
    assert [s.id.rsplit(":", 2)[1] for s in overloads] == [
        "FailureEncoder.f", f"FailureEncoder.f@{overloads[1].span.start_col}",
    ]
    assert overloads[0].span.start_col != overloads[1].span.start_col


def test_swift_no_id_is_shared_and_no_other_id_is_suffixed(tmp_path: Path) -> None:
    symbols = _swift_symbols(tmp_path)
    ids = [s.id for s in symbols]
    assert len(ids) == len(set(ids))
    suffixed = sorted(s.id.rsplit(":", 2)[1] for s in symbols if "@" in s.id)
    assert suffixed == ["FailureEncoder.f@25", "FailureEncoder.init@15"]
    # controls: declarations on lines of their own, one name, are untouched
    apart = [s.id.rsplit(":", 2)[1] for s in symbols if s.name == "Apart.init"]
    assert apart == ["Apart.init", "Apart.init"]


def test_swift_each_twin_keeps_its_own_shape(tmp_path: Path) -> None:
    """``node_for_symbol`` is keyed by id: with one id the second twin's node
    replaced the first's, and both got the second's shape."""
    by_slot = {s.id.rsplit(":", 2)[1]: s for s in _swift_symbols(tmp_path)}
    plain, twin = by_slot["FailureEncoder.init"], by_slot["FailureEncoder.init@15"]
    assert plain.shape_id is not None and twin.shape_id is not None
    alone = by_slot["FailureEncoder.alone"]
    assert alone.shape_id is not None


def test_javascript_callback_suffix_is_its_start_column(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text(
        "function main(p) {\n  p.then(a => a, e => e);\n}\n"
    )
    result = analyze_javascript(tmp_path)
    callbacks = [s for s in result.symbols if s.name.startswith("_cb_")]
    assert len(callbacks) == 2
    assert [s.id.rsplit(":", 2)[1] for s in callbacks] == [
        f"{s.name}@{s.span.start_col}" for s in callbacks
    ]
    assert len({s.id for s in callbacks}) == 2
    named = [s for s in result.symbols if s.name == "main"]
    assert named and "@" not in named[0].id
