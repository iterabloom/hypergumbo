# SPDX-License-Identifier: AGPL-3.0-or-later
"""The exposure record the §3a walk's withhold rule reads (WI-buzup, ADR-0017 §7b).

``cfg.exposed_definitions`` records, per walked function, the definitions a
name the walk does not follow may reach: an in-place store (``v[i] = x``,
``v.f = x``, ``*v = x``), a definition that reads its own target, an address
taken, a closure capture. The store and address-of forms come from the
``store_target`` / ``address_of`` keys of ``cfg_nodes/<lang>.yaml``; the
capture form from ``nested_function``.

ONE ROW PER YAML ENTRY. Each entry of those three keys has a row below with a
snippet that only that entry makes visible, so deleting the entry fails its
row; and ``test_every_yaml_entry_has_a_row`` fails when an entry is added with
no row. The one exception is named in ``_UNTESTABLE``.

Built through ``build_repo_ddg`` on a real file, not a hand-made CFG, so the
row also proves the record reaches ``RepoDdg.exposure``.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hypergumbo_core import cfg as cfg_mod
from hypergumbo_core.cfg import (
    EXPOSURE_ADDRESS_TAKEN,
    EXPOSURE_CAPTURED,
    EXPOSURE_IN_PLACE_STORE,
    EXPOSURE_SELF_REFERENCE,
    DefinitionExposure,
)
from hypergumbo_core.dataflow_scope import ensure_def_use_extractors_registered
from hypergumbo_core.ddg_build import build_repo_ddg

#: language -> (file name, template). ``{body}`` is replaced by the row's
#: snippet; the template defines and uses ``x`` so the function has DDG edges
#: (the record is kept only for walkable functions).
_TEMPLATES: dict[str, tuple[str, str]] = {
    "go": ("m.go", """\
package m

type T struct{ F int }

func f(t []int, b *T, p *int) {
	x := 1
{body}
	_ = x
}
"""),
    "python": ("m.py", """\
def f(t, b):
    x = 1
{body}
    print(x)
"""),
    "typescript": ("m.ts", """\
function f(t: any, b: any): void {
  let x = 1;
{body}
  console.log(x);
}
"""),
    "javascript": ("m.js", """\
function f(t, b) {
  let x = 1;
{body}
  console.log(x);
}
"""),
    "java": ("A.java", """\
class A {
  String fld;
  void f(String[] t, A b) {
    String x = "1";
{body}
    System.out.println(x);
  }
}
"""),
    "c": ("m.c", """\
struct T { int f; };
void f(int *t, struct T b, int *p) {
    int x = 1;
{body}
    g(x);
}
"""),
    "rust": ("src/m.rs", """\
struct T { f: i32 }
fn f(t: &mut Vec<i32>, b: &mut T, p: &mut i32) {
    let x = 1;
{body}
    g(x);
}
"""),
}

_INDENT = {"go": "\t", "python": "    ", "typescript": "  ", "javascript": "  ",
           "java": "    ", "c": "    ", "rust": "    "}

#: The YAML files, by stem (``javascript`` reads ``typescript.yaml``).
_YAML_STEMS = ("go", "python", "typescript", "java", "c", "rust")

#: (yaml file, key, node type[, operator]) -> (language, snippet, kind, name).
#: ``kind`` is the exposure the snippet must produce for ``name``; a store is
#: also checked to sit on the snippet's own line.
_ROWS: dict[tuple[str, str, str], tuple[str, str, str, str]] = {
    # --- go.yaml
    ("go", "assignment", "assignment_statement"): ("go", "t[0] = x", EXPOSURE_IN_PLACE_STORE, "t"),
    ("go", "target", "index_expression"): ("go", "t[0] = x", EXPOSURE_IN_PLACE_STORE, "t"),
    ("go", "target", "selector_expression"): ("go", "b.F = x", EXPOSURE_IN_PLACE_STORE, "b"),
    ("go", "target", "unary_expression"): ("go", "*p = x", EXPOSURE_IN_PLACE_STORE, "p"),
    ("go", "address_of", "unary_expression"): ("go", "q := &x\n_ = q", EXPOSURE_ADDRESS_TAKEN, "x"),
    ("go", "nested_function", "func_literal"): ("go", "g := func() int { return x }\n_ = g", EXPOSURE_CAPTURED, "x"),
    # --- python.yaml
    ("python", "assignment", "assignment"): ("python", "t[0] = x", EXPOSURE_IN_PLACE_STORE, "t"),
    ("python", "assignment", "augmented_assignment"): ("python", "t[0] += x", EXPOSURE_IN_PLACE_STORE, "t"),
    ("python", "target", "subscript"): ("python", "t[0] = x", EXPOSURE_IN_PLACE_STORE, "t"),
    ("python", "target", "attribute"): ("python", "b.f = x", EXPOSURE_IN_PLACE_STORE, "b"),
    ("python", "nested_function", "function_definition"): ("python", "def h():\n    return x", EXPOSURE_CAPTURED, "x"),
    ("python", "nested_function", "class_definition"): ("python", "class K:\n    y = x", EXPOSURE_CAPTURED, "x"),
    ("python", "capture_scope", "lambda"): ("python", "g = lambda: x", EXPOSURE_CAPTURED, "x"),
    ("python", "capture_scope", "generator_expression"): ("python", "g = (x for _ in range(1))", EXPOSURE_CAPTURED, "x"),
    # --- typescript.yaml (javascript reads it too)
    ("typescript", "assignment", "assignment_expression"): ("typescript", "t[0] = x;", EXPOSURE_IN_PLACE_STORE, "t"),
    ("typescript", "assignment", "augmented_assignment_expression"): ("typescript", "t[0] += x;", EXPOSURE_IN_PLACE_STORE, "t"),
    ("typescript", "target", "subscript_expression"): ("typescript", "t[0] = x;", EXPOSURE_IN_PLACE_STORE, "t"),
    ("typescript", "target", "member_expression"): ("typescript", "b.f = x;", EXPOSURE_IN_PLACE_STORE, "b"),
    ("typescript", "nested_function", "arrow_function"): ("typescript", "const g = () => x;", EXPOSURE_CAPTURED, "x"),
    ("typescript", "nested_function", "function_expression"): ("typescript", "const g = function () { return x; };", EXPOSURE_CAPTURED, "x"),
    ("typescript", "nested_function", "generator_function"): ("typescript", "const g = function* () { yield x; };", EXPOSURE_CAPTURED, "x"),
    ("typescript", "nested_function", "function_declaration"): ("typescript", "function h() { return x; }", EXPOSURE_CAPTURED, "x"),
    ("typescript", "nested_function", "generator_function_declaration"): ("typescript", "function* h() { yield x; }", EXPOSURE_CAPTURED, "x"),
    ("typescript", "nested_function", "method_definition"): ("typescript", "const o = { m() { return x; } };", EXPOSURE_CAPTURED, "x"),
    ("typescript", "capture_scope", "public_field_definition"): ("typescript", "class K { v = x; }", EXPOSURE_CAPTURED, "x"),
    ("typescript", "capture_scope", "field_definition"): ("javascript", "class K { v = x; }", EXPOSURE_CAPTURED, "x"),
    # --- java.yaml
    ("java", "assignment", "assignment_expression"): ("java", "t[0] = x;", EXPOSURE_IN_PLACE_STORE, "t"),
    ("java", "target", "array_access"): ("java", "t[0] = x;", EXPOSURE_IN_PLACE_STORE, "t"),
    ("java", "target", "field_access"): ("java", "b.fld = x;", EXPOSURE_IN_PLACE_STORE, "b"),
    ("java", "nested_function", "lambda_expression"): ("java", "Runnable g = () -> System.out.println(x);", EXPOSURE_CAPTURED, "x"),
    ("java", "nested_function", "method_declaration"): ("java", "Object o = new Object() { public String toString() { return x; } };", EXPOSURE_CAPTURED, "x"),
    ("java", "nested_function", "constructor_declaration"): ("java", "class L { L() { String y = x; } }", EXPOSURE_CAPTURED, "x"),
    # --- c.yaml
    ("c", "assignment", "assignment_expression"): ("c", "t[0] = x;", EXPOSURE_IN_PLACE_STORE, "t"),
    ("c", "target", "subscript_expression"): ("c", "t[0] = x;", EXPOSURE_IN_PLACE_STORE, "t"),
    ("c", "target", "field_expression"): ("c", "b.f = x;", EXPOSURE_IN_PLACE_STORE, "b"),
    ("c", "target", "pointer_expression"): ("c", "*p = x;", EXPOSURE_IN_PLACE_STORE, "p"),
    ("c", "address_of", "pointer_expression"): ("c", "int *q = &x;\ng(q);", EXPOSURE_ADDRESS_TAKEN, "x"),
    # --- rust.yaml
    ("rust", "assignment", "assignment_expression"): ("rust", "t[0] = x;", EXPOSURE_IN_PLACE_STORE, "t"),
    ("rust", "assignment", "compound_assignment_expr"): ("rust", "t[0] += x;", EXPOSURE_IN_PLACE_STORE, "t"),
    ("rust", "target", "index_expression"): ("rust", "t[0] = x;", EXPOSURE_IN_PLACE_STORE, "t"),
    ("rust", "target", "field_expression"): ("rust", "b.f = x;", EXPOSURE_IN_PLACE_STORE, "b"),
    ("rust", "target", "unary_expression"): ("rust", "*p = x;", EXPOSURE_IN_PLACE_STORE, "p"),
    ("rust", "address_of", "reference_expression"): ("rust", "let q = &x;\ng(q);", EXPOSURE_ADDRESS_TAKEN, "x"),
    ("rust", "nested_function", "closure_expression"): ("rust", "let c = || x;\ng(c);", EXPOSURE_CAPTURED, "x"),
    ("rust", "nested_function", "function_item"): ("rust", "fn h() -> i32 { let y = x; y }", EXPOSURE_CAPTURED, "x"),
}

#: Entries no snippet can exercise, with the reason.
_UNTESTABLE = {
    # ``function`` is the older tree-sitter-javascript spelling of
    # ``function_expression`` (typescript.yaml's own comment). In the grammars
    # this tree ships, ``function`` is only the anonymous KEYWORD token, which
    # has no children, so the entry captures nothing and removing it changes
    # nothing this file can observe.
    ("typescript", "nested_function", "function"),
}


@pytest.fixture(scope="module", autouse=True)
def _registered() -> None:
    assert ensure_def_use_extractors_registered()


def _exposure(tmp_path: Path, language: str, snippet: str) -> tuple[DefinitionExposure, int]:
    """The record for ``f`` in a file built from ``snippet``, and its first line."""
    name, template = _TEMPLATES[language]
    head = template.split("{body}")[0]
    body = textwrap.indent(snippet, _INDENT[language])
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template.replace("{body}", body))
    result = build_repo_ddg(tmp_path, (language,))
    syms = [s for s in result.exposure if s.split(":")[-2] in ("f", "A.f")]
    assert len(syms) == 1, sorted(result.exposure)
    return result.exposure[syms[0]], head.count("\n") + 1


def _has(exposure: DefinitionExposure, kind: str, name: str, line: int) -> bool:
    if kind == EXPOSURE_IN_PLACE_STORE:
        return (name, line) in exposure.stores
    if kind == EXPOSURE_ADDRESS_TAKEN:
        return name in exposure.address_taken
    return name in exposure.captured


@pytest.mark.parametrize("entry", sorted(_ROWS), ids=lambda e: "-".join(e))
def test_each_yaml_entry_is_read(tmp_path: Path, entry: tuple[str, str, str]) -> None:
    language, snippet, kind, name = _ROWS[entry]
    exposure, line = _exposure(tmp_path, language, snippet)
    assert _has(exposure, kind, name, line), (entry, exposure)


def _yaml_entries() -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for stem in _YAML_STEMS:
        mapping = cfg_mod.load_cfg_mapping(stem)
        assert mapping is not None
        out |= {(stem, "assignment", a.node_type) for a in mapping.store_assignments}
        out |= {(stem, "target", t.node_type) for t in mapping.store_targets}
        out |= {(stem, "address_of", a.node_type) for a in mapping.address_of or []}
        out |= {(stem, "nested_function", n.node_type) for n in mapping.nested_functions}
        out |= {(stem, "capture_scope", c) for c in mapping.capture_scopes or []}
    return out


def test_every_yaml_entry_has_a_row() -> None:
    """An entry added with no row would be an untested list entry."""
    entries = _yaml_entries()
    assert entries
    assert entries - set(_ROWS) == _UNTESTABLE
    assert set(_ROWS) <= entries


#: Late-evaluation candidates checked by mechanism and NOT added to
#: ``capture_scope``, with the verdict observed for the leak fixture
#: (``x = ""``; the form mentions ``x``; ``x = <secret>``; the sink reads the
#: form) on the branch that added the key. Kept as data so adding one later
#: means reading why it was left out.
_CAPTURE_FORMS_NOT_ADDED = {
    ("python", "list_comprehension"): "evaluated where it stands: the leak "
        "fixture's sink receives the empty string, so its removal is correct "
        "(confirmed_with_caveats, removed 1); adding it would mark every name a "
        "comprehension mentions as captured",
    ("typescript", "class_static_block"): "runs once, at class definition, "
        "where it is written: the fixture's removal is correct (confirmed, "
        "removed 1)",
    ("rust", "async_block"): "lazy, but no Rust walk refutes this shape: the "
        "leak fixture reads violated (the walk escapes) on both arms",
    ("java", "class_body"): "a Java capture must be effectively final, so a "
        "rebinding after capture does not compile and only an in-place store "
        "(already a store) can be seen; the anonymous-class leak fixture reads "
        "violated (the walk escapes) on both arms",
    ("go", "method_value"): "``get := t.Get`` copies a value receiver when "
        "evaluated, so the fixture's removal is correct (confirmed, removed 1); "
        "a pointer receiver is reached through a store, already a store",
    ("c", "none"): "C has no closure or late-evaluation form",
}


@pytest.mark.parametrize("entry", sorted(_CAPTURE_FORMS_NOT_ADDED), ids=lambda e: "-".join(e))
def test_a_form_left_out_is_not_a_capture_scope(entry: tuple[str, str]) -> None:
    """Adding one of these must come back through the record above."""
    stem, node_type = entry
    mapping = cfg_mod.load_cfg_mapping(stem)
    assert mapping is not None and mapping.capture_scopes is not None
    assert node_type not in mapping.capture_scopes


@pytest.mark.parametrize("language", sorted(_TEMPLATES))
def test_a_definition_that_reads_its_own_target_is_a_self_reference(
    tmp_path: Path, language: str,
) -> None:
    """Clause 2 comes from the def/use record, so every extractor is checked."""
    snippet = {
        "go": "x = x + 1", "python": "x = x + 1", "typescript": "x = x + 1;",
        "javascript": "x = x + 1;",
        "java": 'x = x + "1";', "c": "x = x + 1;", "rust": "let x = x + 1;",
    }[language]
    exposure, line = _exposure(tmp_path, language, snippet)
    assert ("x", line) in exposure.self_references, exposure


@pytest.mark.parametrize("language", sorted(_TEMPLATES))
def test_a_plain_rebinding_exposes_nothing(tmp_path: Path, language: str) -> None:
    """The case the rule must leave alone: a fresh local, rebound and read."""
    snippet = {
        "go": "y := x\n_ = y", "python": "y = x\nprint(y)",
        "typescript": "const y = x;\nconsole.log(y);", "javascript": "const y = x;\nconsole.log(y);",
        "java": "String y = x;\nSystem.out.println(y);",
        "c": "int y = x;\ng(y);", "rust": "let y = x;\ng(y);",
    }[language]
    exposure, _line = _exposure(tmp_path, language, snippet)
    assert exposure == DefinitionExposure(), exposure


def test_a_go_unary_minus_is_not_a_dereference(tmp_path: Path) -> None:
    """``unary_expression`` is a store target only with the ``*`` operator."""
    exposure, _line = _exposure(tmp_path, "go", "y := -x\n_ = y")
    assert exposure.stores == frozenset()
    assert exposure.address_taken == frozenset()


def test_a_go_tuple_target_reads_each_element(tmp_path: Path) -> None:
    exposure, line = _exposure(tmp_path, "go", "t[0], b.F = x, x")
    assert {("t", line), ("b", line)} <= exposure.stores


class TestTheRecord:
    def test_kind_for_reports_in_declared_order(self) -> None:
        e = DefinitionExposure(
            stores=frozenset({("v", 3)}),
            self_references=frozenset({("v", 3), ("w", 4)}),
            address_taken=frozenset({"v", "w", "a"}),
            captured=frozenset({"v", "w", "a", "c"}),
        )
        assert e.kind_for("v", 3) == EXPOSURE_IN_PLACE_STORE
        assert e.kind_for("w", 4) == EXPOSURE_SELF_REFERENCE
        assert e.kind_for("a", 9) == EXPOSURE_ADDRESS_TAKEN
        assert e.kind_for("c", 9) == EXPOSURE_CAPTURED
        assert e.kind_for("z", 1) is None
        assert e.kind_for("v", 4) == EXPOSURE_ADDRESS_TAKEN

    def test_merged_is_a_union(self) -> None:
        a = DefinitionExposure(stores=frozenset({("a", 1)}), address_taken=frozenset({"p"}))
        b = DefinitionExposure(self_references=frozenset({("b", 2)}), captured=frozenset({"c"}))
        m = a.merged(b)
        assert m == DefinitionExposure(
            stores=frozenset({("a", 1)}), self_references=frozenset({("b", 2)}),
            address_taken=frozenset({"p"}), captured=frozenset({"c"}),
        )

    def test_a_target_rooted_in_a_literal_names_nothing(self, tmp_path: Path) -> None:
        """``_root_name`` returns a name only for a leaf it can name."""
        from hypergumbo_core.cfg import _root_name
        from tree_sitter_language_pack import get_parser

        src = b"y = [][0]\n"
        tree = get_parser("python").parse(src)
        stack, node = [tree.root_node], None
        while stack and node is None:
            cur = stack.pop()
            node = cur if cur.type == "subscript" else None
            stack.extend(cur.children)
        assert node is not None
        # the descent stops at the empty ``list``, which has only bracket
        # tokens: not a leaf that names anything
        assert _root_name(node, src) is None
