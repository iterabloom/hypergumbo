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
package that DEFINES such a lookup, from the source rather than a hand-written
list, and requires each to be classified:
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
    "groovy": ("unverified", "WI-mapor"),
    "objc": ("unverified", "WI-mapor"),
    "perl": ("unverified", "WI-mapor"),
    "php": ("unverified", "WI-mapor"),
    "powershell": ("unverified", "WI-mapor"),
}


def _modules_with_a_lookup() -> set[str]:
    return {p.stem for p in _SRC.glob("*.py") if _LOOKUP.search(p.read_text(encoding="utf-8"))}


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
