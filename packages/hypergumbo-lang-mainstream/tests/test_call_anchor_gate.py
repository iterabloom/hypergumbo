# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag's cross-language gate: every analyzer that finds a call's
enclosing function is classified, so none can drift silently.

THE STATEMENT. Every analyzer-origin ``calls`` edge with a line lies inside its
src's span. The defect behind every violation found so far is the same: an
enclosing lookup keyed on a NAME that several declarations in one file share.
Two methods ``parse`` in two classes, an ``#ifdef`` pair, overloads, a companion
``apply``, ``func init()`` twice. Each analyzer carries its own
``_get_enclosing_function``-shaped lookup, so the defect had to be found and
cured one language at a time (INV-mozas, INV-midag).

WHAT THIS GATE DOES, and what it does not. It enumerates every module in this
package that DEFINES such a lookup under the conventional name, or EMITS a
``calls`` edge at all, from the source rather than a hand-written list, and
requires each to be classified. (The name pattern alone missed 25 modules whose
lookup is spelled otherwise, cpp and lua among them: WI-tosum.)
Each is:
- VERIFIED names a containment test that exists here. That test runs the real
  analyzer on a same-named-declarations fixture and asserts reach, then
  containment. That half is semantic.
- UNVERIFIED names the residual row. That half is BOOKKEEPING, not a check. It
  records that nobody has looked, so a new analyzer cannot arrive unclassified
  and read as covered. Go is why: it measured 0 out of span on the corpus, and
  a fixture of two ``func init()`` then found the defect.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_lang_mainstream"
_LOOKUP = re.compile(r"^def _(?:get|find)_enclosing_(?:function|method|callable)\w*\(", re.M)
#: A module that emits a ``calls`` edge anchors it somewhere, whatever its lookup
#: is called. The name pattern alone missed 25 such modules (WI-tosum).
_CALLS = re.compile(r"""edge_type=["']calls["']""")

#: module -> ("verified", "test_module::test_name") | ("unverified", row id)
CLASSIFIED: dict[str, tuple[str, str]] = {
    "c": ("verified", "test_c_call_anchor::test_ifdef_alternatives_are_told_apart"),
    "csharp": ("verified", "test_ruby_csharp_call_anchor::test_csharp_one_leaf_name_in_two_classes"),
    "go": ("verified", "test_call_anchor_gate::test_go_two_init_functions"),
    "java": ("verified", "test_call_anchor_gate::test_java_overloads"),
    "js_ts": ("verified", "test_js_call_anchor::test_every_call_is_inside_its_src"),
    "kotlin": ("verified", "test_kotlin_call_anchor::test_a_call_is_anchored_to_the_method_that_contains_it"),
    "ruby": ("verified", "test_ruby_csharp_call_anchor::test_ruby_a_method_defined_twice"),
    "rust": ("verified", "test_rust_call_anchor::test_two_impls_of_one_method"),
    "scala": ("verified", "test_scala_call_anchor::test_same_named_methods_in_two_classes"),
    "swift": ("verified", "test_swift_call_anchor::test_overloads_are_told_apart"),
    "groovy": ("verified", "test_call_anchor_gate::test_groovy_same_named_callables"),
    "objc": ("verified", "test_call_anchor_gate::test_objc_same_named_callables"),
    "perl": ("verified", "test_call_anchor_gate::test_perl_same_named_callables"),
    "php": ("verified", "test_call_anchor_gate::test_php_same_named_callables"),
    "powershell": ("verified", "test_call_anchor_gate::test_powershell_same_named_callables"),
    "bash": ("unverified", "WI-tosum"),
    "cpp": ("unverified", "WI-tosum"),
    "jupyter": ("unverified", "WI-tosum"),
    "lua": ("unverified", "WI-tosum"),
    "py": ("unverified", "WI-tosum"),
}


def _modules_with_a_lookup() -> set[str]:
    """Every module that defines an enclosing lookup by the conventional name, or
    emits a ``calls`` edge. A literal ``edge_type="calls"`` is what the second
    pattern sees; a module that spells the type through a variable is not seen."""
    found = set()
    for p in _SRC.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        if _LOOKUP.search(text) or _CALLS.search(text):
            found.add(p.stem)
    return found


def test_every_enclosing_lookup_is_classified() -> None:
    found = _modules_with_a_lookup()
    assert "kotlin" in found, "reach: the scan must see a known lookup"
    assert found == set(CLASSIFIED), (
        f"unclassified: {sorted(found - set(CLASSIFIED))}; "
        f"stale: {sorted(set(CLASSIFIED) - found)}")


@pytest.mark.parametrize("module", sorted(m for m, (s, _) in CLASSIFIED.items() if s == "verified"))
def test_a_verified_entry_names_a_real_test(module: str) -> None:
    test_module, test_name = CLASSIFIED[module][1].split("::")
    assert callable(getattr(importlib.import_module(test_module), test_name, None)), CLASSIFIED[module]


def _anchors(result) -> list[tuple[str, int, int, int]]:
    by_id = {s.id: s for s in result.symbols}
    return [
        (by_id[e.src].name, by_id[e.src].span.start_line, by_id[e.src].span.end_line, e.line)
        for e in result.edges if e.edge_type == "calls" and e.line and e.src in by_id
    ]


def test_go_two_init_functions(tmp_path: Path) -> None:
    """Go admits ``func init()`` any number of times per file; the name lookup
    anchored every earlier init's calls to the last one."""
    from hypergumbo_lang_mainstream.go import analyze_go

    (tmp_path / "a.go").write_text(
        "package main\n\nfunc one() {}\nfunc two() {}\n\n"
        "func init() {\n\tone()\n}\n\nfunc init() {\n\ttwo()\n}\n")
    anchors = _anchors(analyze_go(tmp_path))
    assert {a[3] for a in anchors} >= {7, 11}, anchors  # reach
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)


def test_java_overloads(tmp_path: Path) -> None:
    """Java looks the declaration up by position already (measured 0 of 86,580
    on the corpus); this pins it on the shape that breaks name lookups."""
    from hypergumbo_lang_mainstream.java import analyze_java

    (tmp_path / "A.java").write_text(
        "class A {\n    void one() {}\n    void two() {}\n\n"
        "    void run(int x) {\n        one();\n    }\n\n"
        "    void run(String s) {\n        two();\n    }\n}\n")
    anchors = _anchors(analyze_java(tmp_path))
    assert {a[3] for a in anchors} >= {6, 10}, anchors  # reach
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)


def test_go_without_an_index_falls_back_to_the_qualified_name(tmp_path: Path) -> None:
    """Callers that pass no position index (the route extractor) keep the
    qualified-name path: ``Type.Method`` distinguishes one method name on two
    receiver types."""
    import tree_sitter
    import tree_sitter_go

    from hypergumbo_core.analyze.base import iter_tree
    from hypergumbo_lang_mainstream.go import _get_enclosing_function, analyze_go

    source = (b"package main\n\nfunc one() {}\n\ntype A struct{}\ntype B struct{}\n\n"
              b"func (a A) Run() {\n\tone()\n}\n\nfunc (b B) Run() {\n\tone()\n}\n")
    (tmp_path / "a.go").write_bytes(source)
    symbols = {s.name: s for s in analyze_go(tmp_path).symbols}
    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_go.language()))
    calls = [n for n in iter_tree(parser.parse(source).root_node) if n.type == "call_expression"]
    assert len(calls) == 2  # reach
    got = [_get_enclosing_function(c, source, symbols).name for c in calls]
    assert got == ["A.Run", "B.Run"], got


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


def test_groovy_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: an overload pair and one method name in two classes. Its calls
    went to the last same-named declaration until the lookup keyed on position (WI-
    mapor)."""
    from hypergumbo_lang_mainstream.groovy import analyze_groovy

    (tmp_path / "A.groovy").write_text("""\
class A {
    void one() {}
    void run(int x) {
        one()
    }
    void run(String s) {
        one()
    }
}
class B {
    void two() {}
    void run() {
        two()
    }
}
""")
    _assert_contained(analyze_groovy(tmp_path), [4, 7, 13])


def test_objc_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: one selector implemented in two ``@implementation`` blocks. Its
    calls went to the last same-named declaration until the lookup keyed on position
    (WI-mapor)."""
    from hypergumbo_lang_mainstream.objc import analyze_objc

    (tmp_path / "A.m").write_text("""\
@implementation A
- (void)one {}
- (void)run {
    [self one];
}
@end
@implementation B
- (void)two {}
- (void)run {
    [self two];
}
@end
""")
    _assert_contained(analyze_objc(tmp_path), [4, 10])


def test_perl_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: ``sub run`` in two packages of one file. Its calls went to the
    last same-named declaration until the lookup keyed on position (WI-mapor)."""
    from hypergumbo_lang_mainstream.perl import analyze_perl

    (tmp_path / "A.pm").write_text("""\
package A;
sub one { }
sub run {
    one();
}
package B;
sub two { }
sub run {
    two();
}
1;
""")
    _assert_contained(analyze_perl(tmp_path), [4, 9])


def test_php_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: one method name in two classes. It measured correct when first
    checked (WI-mapor); this pins it."""
    from hypergumbo_lang_mainstream.php import analyze_php

    (tmp_path / "A.php").write_text("""\
<?php
class A {
    function one() {}
    function run() {
        $this->one();
    }
}
class B {
    function two() {}
    function run() {
        $this->two();
    }
}
""")
    _assert_contained(analyze_php(tmp_path), [5, 11])


def test_powershell_same_named_callables(tmp_path: Path) -> None:
    """Same-named shape: a function defined twice (the later definition wins at run time).
    Its calls went to the last same-named declaration until the lookup keyed on position
    (WI-mapor)."""
    from hypergumbo_lang_mainstream.powershell import analyze_powershell

    (tmp_path / "A.ps1").write_text("""\
function One { }
function Two { }
function Run {
    One
}
function Run {
    Two
}
""")
    _assert_contained(analyze_powershell(tmp_path), [4, 7])
