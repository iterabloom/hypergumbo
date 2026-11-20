# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Java anonymous class, an enum constant's body and an overload each label
the scope chain a stable_id hashes (``_java_hash_scope``).

Until scheme v17 the chain held types and method NAMES only. An anonymous
class added nothing, nor did the field or variable it initialises, nor an
enum constant's body; and every overload of a method gave the same label.
So the ``build`` of ``A = new Builder<String>() {...}`` and of ``B = new
Builder<String>() {...}`` (jedis ``BuilderFactory``), ``run`` in ``new
Runnable() {...}`` and in ``new Thread() {...}`` in one method, ``apply`` in
``PLUS { ... }`` and ``MINUS { ... }`` and the enum's own abstract ``apply``,
and the members of a local class in ``o(int)`` and in ``o(String)`` tied;
the run's within-file occurrence split numbered them by order, and deleting
the first moved the second's id onto the first's.

Each case is the analyzer run directly, paths made repo-relative and the
backstop's table formula applied as the pipeline does: no two symbols of the
file share a stable_id, and blanking the first form (its lines kept) moves
no survivor's id. Survivors are matched by (kind, name, line, column).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import populate_kind_stable_ids
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.java import _java_hash_scope, analyze_java


def _symbols(root: Path, text: str) -> list[Symbol]:
    rel = "src/A.java"
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    symbols = [
        s for s in analyze_java(root).symbols
        if s.kind != "file" and s.path.endswith(rel)
    ]
    for sym in symbols:
        sym.path = rel
    populate_kind_stable_ids(symbols)
    return symbols


def _position(sym: Symbol) -> tuple[str, str, int, int]:
    return (sym.kind, sym.name, sym.span.start_line, sym.span.start_col)


_HEAD = (
    "package p;\n"
    "public class A {\n"
    "  interface Builder<T> { T build(Object data); }\n"
)
_TAIL = "  void submit(Runnable r) {}\n}\n"

#: (head, earlier, rest): ``earlier`` holds the first form, ``rest`` its
#: twin (and closes what ``head`` opened); blanking ``earlier`` leaves valid Java.
CASES: dict[str, tuple[str, str, str]] = {
    "fields-same-type": (
        "",
        "  static final Builder<String> X = new Builder<String>() {\n"
        "    public String build(Object data) { return \"x\"; }\n  };\n",
        "  static final Builder<String> Y = new Builder<String>() {\n"
        "    public String build(Object data) { return \"y\"; }\n  };\n",
    ),
    "declarators-of-one-field": (
        "  Runnable\n",
        "    r1 = new Runnable() { public void run() {} },\n",
        "    r2 = new Runnable() { public void run() {} };\n",
    ),
    "locals-same-type": (
        "  void m() {\n",
        "    Runnable x = new Runnable() { public void run() {} };\n",
        "    Runnable y = new Runnable() { public void run() {} };\n  }\n",
    ),
    "arguments-different-types": (
        "  void m() {\n",
        "    submit(new Runnable() { public void run() {} });\n",
        "    submit(new Thread() { public void run() {} });\n  }\n",
    ),
    "overloads-anonymous": (
        "",
        "  void o(int a) { Runnable z = new Runnable() { public void run() {} }; }\n",
        "  void o(String a) { Runnable z = new Runnable() { public void run() {} }; }\n",
    ),
    "overloads-local-class": (
        "",
        "  int o(int a) { class L { void go() {} } return 1; }\n",
        "  int o(String a) { class L { void go() {} } return 2; }\n",
    ),
    "constructor-overloads": (
        "",
        "  A(int a) { Runnable z = new Runnable() { public void run() {} }; }\n",
        "  A(String a) { Runnable z = new Runnable() { public void run() {} }; }\n",
    ),
    "nested-anonymous-in-field": (
        "",
        "  Runnable outer1 = new Runnable() { public void run() {\n"
        "    Runnable in = new Runnable() { public void run() {} }; } };\n",
        "  Runnable outer2 = new Runnable() { public void run() {\n"
        "    Runnable in = new Runnable() { public void run() {} }; } };\n",
    ),
    "anonymous-field-member": (
        "",
        "  Object f1 = new Object() { int n = 1; };\n",
        "  Object f2 = new Object() { int n = 1; };\n",
    ),
    "enum-constant-bodies": (
        "  enum Op {\n",
        "    PLUS { int apply(int a) { return a; } },\n",
        "    MINUS { int apply(int a) { return -a; } };\n"
        "    abstract int apply(int a);\n  }\n",
    ),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_twin_forms_get_distinct_ids_and_survive_deletion(tmp_path: Path, case: str) -> None:
    head, earlier, rest = CASES[case]
    text = _HEAD + head + earlier + rest + _TAIL
    with_sibling = _symbols(tmp_path / "with", text)
    ids = [s.stable_id for s in with_sibling]
    assert None not in ids, [s.name for s in with_sibling if s.stable_id is None]
    assert len(set(ids)) == len(ids), sorted(
        (s.kind, s.name, s.span.start_line) for s in with_sibling if ids.count(s.stable_id) > 1
    )
    before: dict[tuple[str, str, int, int], list[str | None]] = {}
    for s in with_sibling:
        before.setdefault(_position(s), []).append(s.stable_id)
    edited = _HEAD + head + "\n" * earlier.count("\n") + rest + _TAIL
    after = _symbols(tmp_path / "without", edited)
    assert len(after) < len(with_sibling)
    for s in after:
        assert s.stable_id in before.get(_position(s), []), (s.kind, s.name, s.span.start_line)


def _scopes_of(text: str, node_type: str) -> list[list[str]]:
    import tree_sitter
    import tree_sitter_java

    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_java.language()))
    src = text.encode()
    tree = parser.parse(src)
    found: list[tree_sitter.Node] = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == node_type:
            found.append(n)
        stack.extend(n.children)
    found.sort(key=lambda n: n.start_byte)
    return [_java_hash_scope(n, src) for n in found]


def test_scope_labels_spell_member_type_constant_and_signature() -> None:
    """The labels as hashed: a field or variable by its name, an anonymous
    class by ``new`` and the type it instantiates (layout dropped), an enum
    constant by its name, an overload by name and hashed signature; a
    callable whose hashed text is ``()`` by its bare name. A creation without
    a body (``new Thread(...)`` around an anonymous ``Runnable``) adds
    nothing."""
    text = (
        "class A {\n"
        "  Builder<Map<String,  Long>> X = new Builder<Map<String,\n Long>>() { void build() {} };\n"
        "  void m() { Runnable y = new Runnable() { public void run() {} }; }\n"
        "  int o(int a) { submit(new Thread() { public void run() {} }); return 1; }\n"
        "  A(String s) { Object q = new Object() { void f() {} }; }\n"
        "  enum Op { PLUS { void apply() {} } }\n"
        "  void k() { Thread t = new Thread(new Runnable() { public void run() {} }); }\n"
        "}\n"
    )
    assert _scopes_of(text, "method_declaration") == [
        ["A", "X", "new Builder<Map<String, Long>>"],
        ["A"],
        ["A", "m", "y", "new Runnable"],
        ["A"],
        ["A", "o(int)int", "new Thread"],
        ["A", "A(String)", "q", "new Object"],
        ["A", "Op", "PLUS"],
        ["A"],
        ["A", "k", "t", "new Runnable"],
    ]


def test_member_outside_an_initializer_keeps_its_chain() -> None:
    """A field, a method and a named nested type of a class hash the chain
    they hashed before scheme v17: no new label lies above them."""
    text = (
        "class A {\n  int f = 1;\n  void g(int x) {}\n"
        "  class B { void h(String s) {} }\n}\n"
    )
    assert _scopes_of(text, "method_declaration") == [["A"], ["A", "B"]]
    assert _scopes_of(text, "field_declaration") == [["A"]]
