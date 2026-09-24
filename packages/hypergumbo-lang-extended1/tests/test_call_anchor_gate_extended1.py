# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag's cross-language gate for hypergumbo-lang-extended1: every analyzer that finds a
call's enclosing function is classified, so none can drift silently.

The same gate as hypergumbo-lang-mainstream's test_call_anchor_gate.py; see its
docstring for the statement and the mechanism. It enumerates every module in
this package that DEFINES an enclosing-function lookup. Each must be VERIFIED
(named containment test, which runs the analyzer on a same-named-declarations
fixture) or UNVERIFIED (the residual row). The UNVERIFIED half is bookkeeping,
not a check: it records that nobody has looked, so a new analyzer cannot arrive
unclassified and read as covered.
"""
from __future__ import annotations

import re
from pathlib import Path


_SRC = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_lang_extended1"
_LOOKUP = re.compile(r"^def _(?:get|find)_enclosing_(?:function|method|callable)\w*\(", re.M)

#: module -> ("verified", "test_module::test_name") | ("unverified", row id)
CLASSIFIED: dict[str, tuple[str, str]] = {
    "d_lang": ("verified", "test_call_anchor_gate_extended1::test_d_lang_same_named_callables"),
    "fennel": ("verified", "test_call_anchor_gate_extended1::test_fennel_same_named_callables"),
    "fish": ("verified", "test_call_anchor_gate_extended1::test_fish_same_named_callables"),
    "gdscript": ("verified", "test_call_anchor_gate_extended1::test_gdscript_same_named_callables"),
    "gleam": ("verified", "test_call_anchor_gate_extended1::test_gleam_same_named_callables"),
    "hack": ("verified", "test_call_anchor_gate_extended1::test_hack_same_named_callables"),
    "janet": ("verified", "test_call_anchor_gate_extended1::test_janet_same_named_callables"),
    "llvm_ir": ("verified", "test_call_anchor_gate_extended1::test_llvm_ir_same_named_callables"),
    "odin": ("verified", "test_call_anchor_gate_extended1::test_odin_same_named_callables"),
    "pascal": ("verified", "test_call_anchor_gate_extended1::test_pascal_same_named_callables"),
    "solidity": ("verified", "test_call_anchor_gate_extended1::test_solidity_same_named_callables"),
    "v_lang": ("verified", "test_call_anchor_gate_extended1::test_v_lang_same_named_callables"),
}


def _modules_with_a_lookup() -> set[str]:
    return {p.stem for p in _SRC.glob("*.py") if _LOOKUP.search(p.read_text(encoding="utf-8"))}


def test_every_enclosing_lookup_is_classified() -> None:
    found = _modules_with_a_lookup()
    assert "solidity" in found, "reach: the scan must see a known lookup"
    assert found == set(CLASSIFIED), (
        f"unclassified: {sorted(found - set(CLASSIFIED))}; "
        f"stale: {sorted(set(CLASSIFIED) - found)}")


def _assert_contained(result, calls: list[int]) -> None:
    """Reach first: a call edge credited to a callable (not the file) at every
    expected line. Then containment: every call edge's src span holds its line."""
    by_id = {s.id: s for s in result.symbols}
    anchors = [
        (by_id[e.src].name, by_id[e.src].span.start_line, by_id[e.src].span.end_line, e.line)
        for e in result.edges
        if e.edge_type == "calls" and e.line and e.src in by_id and by_id[e.src].kind != "file"
    ]
    assert {a[3] for a in anchors} >= set(calls), anchors  # reach
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)


def test_d_lang_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: an overload pair, and one method name in two classes that is also
    a free function's. Its calls went to the last same-named declaration until the
    lookup keyed on position (WI-mapor)."""
    from hypergumbo_lang_extended1.d_lang import analyze_d

    (tmp_path / "a.d").write_text("""\
void one() {}
void two() {}
void run(int x) {
    one();
}
void run(string s) {
    two();
}
class A {
    void three() {}
    void run() {
        one();
    }
}
class B {
    void four() {}
    void run() {
        two();
    }
}
""")
    _assert_contained(analyze_d(tmp_path), [4, 7, 12, 18])


def test_fennel_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: a top-level ``fn`` repeated. It measured correct when first
    checked (WI-mapor); this pins it."""
    from hypergumbo_lang_extended1.fennel import analyze_fennel

    (tmp_path / "a.fnl").write_text("""\
(fn one [] 1)
(fn two [] 2)
(fn run []
  (one))
(fn run []
  (two))
""")
    _assert_contained(analyze_fennel(tmp_path), [4, 6])


def test_fish_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: a function defined twice (the later definition wins when it runs).
    Its calls went to the last same-named declaration until the lookup keyed on position
    (WI-mapor)."""
    from hypergumbo_lang_extended1.fish import analyze_fish

    (tmp_path / "a.fish").write_text("""\
function one
end
function two
end
function run
    one
end
function run
    if true
        two
    end
end
""")
    # The second body nests its call in an ``if``, so the walk climbs past a
    # node that is not a function before it reaches one.
    _assert_contained(analyze_fish(tmp_path), [6, 10])


def test_gdscript_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: one method name in two inner classes. Its calls went to the last
    same-named declaration until the lookup keyed on position (WI-mapor)."""
    from hypergumbo_lang_extended1.gdscript import analyze_gdscript

    (tmp_path / "a.gd").write_text("""\
extends Node

class A:
\tfunc one():
\t\tpass
\tfunc run():
\t\tone()

class B:
\tfunc two():
\t\tpass
\tfunc run():
\t\ttwo()
""")
    _assert_contained(analyze_gdscript(tmp_path), [7, 13])


def test_gleam_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: none: a function name is unique per module, so this pins
    containment. It measured correct when first checked (WI-mapor); this pins it."""
    from hypergumbo_lang_extended1.gleam import analyze_gleam

    (tmp_path / "a.gleam").write_text("""\
pub fn one() { 1 }
pub fn two() { 2 }
pub fn run() {
  one()
  two()
}
""")
    _assert_contained(analyze_gleam(tmp_path), [4, 5])


def test_hack_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: one method name in two classes. It measured correct when first
    checked (WI-mapor); this pins it."""
    from hypergumbo_lang_extended1.hack import analyze_hack

    (tmp_path / "a.hack").write_text("""\
<?hh
class A {
  public function one(): void {}
  public function run(): void {
    $this->one();
  }
}
class B {
  public function two(): void {}
  public function run(): void {
    $this->two();
  }
}
""")
    _assert_contained(analyze_hack(tmp_path), [5, 11])


def test_janet_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: a top-level ``defn`` repeated. It measured correct when first
    checked (WI-mapor); this pins it."""
    from hypergumbo_lang_extended1.janet import analyze_janet

    (tmp_path / "a.janet").write_text("""\
(defn one [] 1)
(defn two [] 2)
(defn run []
  (one))
(defn run []
  (two))
""")
    _assert_contained(analyze_janet(tmp_path), [4, 6])


def test_llvm_ir_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: none: a global name is unique per module, so this pins
    containment. It measured correct when first checked (WI-mapor); this pins it."""
    from hypergumbo_lang_extended1.llvm_ir import analyze_llvm_ir

    (tmp_path / "a.ll").write_text("""\
define void @one() {
  ret void
}
define void @two() {
  ret void
}
define void @run() {
  call void @one()
  call void @two()
  ret void
}
""")
    _assert_contained(analyze_llvm_ir(tmp_path), [8, 9])


def test_odin_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: a nested procedure of one name in two procedures. It measured
    correct when first checked (WI-mapor); this pins it."""
    from hypergumbo_lang_extended1.odin import analyze_odin

    (tmp_path / "a.odin").write_text("""\
package main

one :: proc() {}
two :: proc() {}

outer1 :: proc() {
\tinner :: proc() {
\t\tone()
\t}
\tinner()
}

outer2 :: proc() {
\tinner :: proc() {
\t\ttwo()
\t}
\tinner()
}
""")
    _assert_contained(analyze_odin(tmp_path), [8, 15])


def test_pascal_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: an ``overload`` pair. It measured correct when first checked (WI-
    mapor); this pins it."""
    from hypergumbo_lang_extended1.pascal import analyze_pascal

    (tmp_path / "a.pas").write_text("""\
program P;

procedure One; begin end;
procedure Two; begin end;

procedure Run(X: Integer); overload;
begin
  One();
end;

procedure Run(S: String); overload;
begin
  Two();
end;

begin
end.
""")
    _assert_contained(analyze_pascal(tmp_path), [8, 13])


def test_solidity_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: one function name in two contracts. It measured correct when first
    checked (WI-mapor); this pins it."""
    from hypergumbo_lang_extended1.solidity import analyze_solidity

    (tmp_path / "a.sol").write_text("""\
pragma solidity ^0.8.0;
contract A {
    function one() internal {}
    function run() public {
        one();
    }
}
contract B {
    function two() internal {}
    function run() public {
        two();
    }
}
""")
    _assert_contained(analyze_solidity(tmp_path), [5, 11])


def test_v_lang_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: one method name on two structs. It measured correct when first
    checked (WI-mapor); this pins it."""
    from hypergumbo_lang_extended1.v_lang import analyze_v

    (tmp_path / "a.v").write_text("""\
module main

struct A {}
struct B {}

fn one() {}
fn two() {}

fn (a A) run() {
\tone()
}

fn (b B) run() {
\ttwo()
}
""")
    _assert_contained(analyze_v(tmp_path), [10, 14])
