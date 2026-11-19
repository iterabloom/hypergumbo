# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Swift identifier with a ``$`` after its first character is read whole.

Swift accepts ``$`` inside an identifier (the Observation macros generate
``_$observationRegistrar``; swift-composable-architecture declares
``_$isIdentityEqual`` and ``_$willModify``). tree-sitter-swift accepts ``$``
only at the START of an identifier (``$0``, ``$binding``), so it reads
``_$isIdentityEqual`` as ``_`` followed by an ERROR node: the function was
emitted with the name ``_`` (WI-jujog), ``struct A$B`` as ``A``, ``case a$b``
and ``let a$b`` as ``a``, ``var _$reg`` not at all (a wildcard pattern), and
a call ``_$m()`` as a call to ``_``, which bound to whichever ``_`` function
the file declared.

The Swift analyzer now parses a copy of each file in which every ``$`` that
follows an identifier character is spelled ``_`` (same length, so every span
is unchanged) and reads every name from the real bytes, so the grammar sees
one identifier and the analyzer reports it as written. Pass 2 re-parses
through the same hook, so calls are read from the same tree.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.swift import analyze_swift

_SRC = (
    "struct A$B { func m() {} }\n"
    "enum E { case a$b, c }\n"
    "struct S {\n"
    "    var _$reg: Int = 0\n"
    "    let a$b = 1\n"
    "    func trail$() {}\n"
    "    func run() { _$m(); self._$m() }\n"
    "    func _$m() {}\n"
    "}\n"
    "func _$isIdentityEqual(_ a: Int) -> Bool { true }\n"
    "func _$willModify() {}\n"
    "func $lead() {}\n"
    "let g$v = 2\n"
    "func caller() { _$willModify() }\n"
)


def _analyze(tmp_path: Path):  # type: ignore[no-untyped-def]
    (tmp_path / "x.swift").write_text(_SRC)
    return analyze_swift(tmp_path)


def test_every_dollar_name_is_read_whole(tmp_path: Path) -> None:
    names = {(s.name, s.kind) for s in _analyze(tmp_path).symbols}
    for expected in [
        ("A$B", "struct"), ("A$B.m", "method"), ("E.a$b", "field"), ("E.c", "field"),
        ("S._$reg", "field"), ("S.a$b", "field"), ("S.trail$", "method"),
        ("S._$m", "method"), ("_$isIdentityEqual", "function"),
        ("_$willModify", "function"), ("$lead", "function"), ("g$v", "variable"),
    ]:
        assert expected in names, sorted(names)
    assert not {n for n, _ in names if n in ("_", "A", "S._", "S.a", "E.a", "g", "S.trail")}, sorted(names)


def test_the_item_repro_two_dollar_functions_hash_apart(tmp_path: Path) -> None:
    ids = {s.name: s.stable_id for s in _analyze(tmp_path).symbols}
    assert ids["_$isIdentityEqual"] != ids["_$willModify"]


def test_spans_and_signatures_point_at_the_real_text(tmp_path: Path) -> None:
    syms = {s.name: s for s in _analyze(tmp_path).symbols}
    f = syms["_$isIdentityEqual"]
    assert f.span is not None
    assert (f.span.start_line, f.span.start_col) == (10, 0)
    assert f.signature == "(_: Int) -> Bool"
    line = _SRC.splitlines()[f.span.start_line - 1]
    assert line[f.span.start_col:f.span.end_col] == line


def test_a_call_to_a_dollar_name_binds_to_it(tmp_path: Path) -> None:
    res = _analyze(tmp_path)
    by_id = {s.id: s for s in res.symbols}
    calls = {(by_id[e.src].name, e.dst) for e in res.edges
             if e.edge_type == "calls" and e.src in by_id}
    target = {s.name: s.id for s in res.symbols}
    assert ("S.run", target["S._$m"]) in calls, calls
    assert ("caller", target["_$willModify"]) in calls, calls
    # nothing binds to a truncated ``_`` any more
    assert not any(d.endswith(":_:unresolved") or d.endswith(":_:function") for _, d in calls), calls


def test_a_dollar_in_a_string_or_at_a_name_start_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / "y.swift").write_text(
        'func price() -> String { "US$5" }\n'
        "func use(_ xs: [Int]) { xs.map { $0 } }\n"
    )
    res = analyze_swift(tmp_path)
    names = {s.name for s in res.symbols}
    assert {"price", "use"} <= names


def test_a_dollar_after_a_non_ascii_letter_is_read_whole(tmp_path: Path) -> None:
    (tmp_path / "u.swift").write_text("func café$x() {}\n")
    assert "café$x" in {s.name for s in analyze_swift(tmp_path).symbols}


def test_a_file_with_a_raw_identifier_and_a_dollar_name(tmp_path: Path) -> None:
    """Both respellings at once: the raw identifier's spaces (INV-bisok) and
    the inner ``$``; the re-parse after the first keeps the second."""
    (tmp_path / "r.swift").write_text(
        "struct T {\n    func `a b`() {}\n    func _$m() {}\n    func run() { _$m() }\n}\n")
    res = analyze_swift(tmp_path)
    syms = {s.name: s for s in res.symbols}
    assert {"T.`a_b`", "T._$m"} <= set(syms), sorted(syms)
    assert any(e.edge_type == "calls" and e.dst == syms["T._$m"].id for e in res.edges)
