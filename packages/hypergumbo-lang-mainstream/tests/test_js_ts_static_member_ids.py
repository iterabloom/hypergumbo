# SPDX-License-Identifier: AGPL-3.0-or-later
"""A JS/TS class member's stable_id hashes ``static``.

A class may declare ``static error(m: string)`` beside ``error(m: string)``
(nest's ``Logger`` does, for error / warn / log / debug / verbose / fatal), a
``static get x()`` beside ``get x()``, and a ``static count`` beside
``count``. Until scheme v17 nothing a member's typed stable_id hashed told
them apart, so each pair hashed one text and tied; the run's within-file
occurrence split (``split_within_file_stable_id_collisions``) numbered them by
order, and deleting the first moved the second's id onto the first's. Every
symbol declared INSIDE such a pair tied the same way: the scope chain
(``_jsts_scope``) labelled both methods by their bare name.

The properties pinned here, for TypeScript and JavaScript (one analyzer):
each static/instance pair gets two ids; deleting the instance member moves no
id of the static one; the decorators slot is ``static`` for a static member
and ``""`` for an instance member, so an instance member's id is the one it
had; and a symbol nested in a static method is scoped ``static <name>``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import (
    make_file_stable_id,
    make_typed_stable_id,
    split_within_file_stable_id_collisions,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.js_ts import (
    _jsts_member_decorators,
    _jsts_scope,
    analyze_javascript,
    normalize_jsts_signature,
)


def _symbols(root: Path, rel: str, text: str) -> list[Symbol]:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return [
        s for s in analyze_javascript(root).symbols
        if s.kind != "file" and s.path.endswith(rel)
    ]


def _ids_at(
    root: Path, rel: str, text: str, *, split: bool = True,
) -> dict[tuple[int, str], str]:
    """``{(line, kind): stable_id}``; with ``split``, numbered as the run's
    within-file occurrence split numbers a tie."""
    syms = _symbols(root, rel, text)
    if split:
        split_within_file_stable_id_collisions(syms)
    out: dict[tuple[int, str], str] = {}
    for s in syms:
        key = (s.span.start_line, s.kind)
        assert key not in out, s.name
        assert s.stable_id is not None, s.name
        out[key] = s.stable_id
    return out


#: (extension, instance member, static member, kind): one pair per case, the
#: instance member on line 2 and the static one on line 3 of ``class C``.
_PAIRS: dict[str, tuple[str, str, str, str]] = {
    "ts-method": ("ts", "  error(m: string): void {}\n",
                  "  static error(m: string): void {}\n", "method"),
    "ts-untyped-method": ("ts", "  run() {}\n", "  static run() {}\n", "method"),
    "ts-async-method": ("ts", "  async load(x: number): Promise<void> {}\n",
                        "  static async load(x: number): Promise<void> {}\n",
                        "method"),
    "ts-generator": ("ts", "  *gen(): Generator<number> { yield 1; }\n",
                     "  static *gen(): Generator<number> { yield 1; }\n",
                     "method"),
    "ts-getter": ("ts", "  get level(): number { return 1; }\n",
                  "  static get level(): number { return 2; }\n", "getter"),
    "ts-setter": ("ts", "  set level(v: number) {}\n",
                  "  static set level(v: number) {}\n", "setter"),
    "ts-typed-field": ("ts", "  count: number = 0;\n",
                       "  static count: number = 0;\n", "field"),
    "ts-untyped-field": ("ts", "  name = 'a';\n", "  static name = 'b';\n",
                         "field"),
    "ts-arrow-field": ("ts", "  handler = (x: number): number => x;\n",
                       "  static handler = (x: number): number => x;\n",
                       "field"),
    "ts-modified-field": ("ts", "  private readonly k: number = 1;\n",
                          "  private static readonly k: number = 1;\n",
                          "field"),
    "ts-abstract-class": ("ts", "  run2(x: number): void {}\n",
                          "  static run2(x: number): void {}\n", "method"),
    "js-method": ("js", "  error(m) {}\n", "  static error(m) {}\n", "method"),
    "js-getter": ("js", "  get level() { return 1; }\n",
                  "  static get level() { return 2; }\n", "getter"),
    "js-field": ("js", "  count = 0;\n", "  static count = 0;\n", "field"),
}


def _class(case: str, instance: bool = True, static: bool = True) -> str:
    ext, inst, stat, _kind = _PAIRS[case]
    head = "export abstract class C {\n" if case == "ts-abstract-class" else "export class C {\n"
    return head + (inst if instance else "\n") + (stat if static else "\n") + "}\n"


@pytest.mark.parametrize("case", sorted(_PAIRS))
def test_static_and_instance_member_get_two_ids(tmp_path: Path, case: str) -> None:
    ext, _i, _s, kind = _PAIRS[case]
    ids = _ids_at(tmp_path, f"a.{ext}", _class(case), split=False)
    assert ids[(2, kind)] != ids[(3, kind)]


@pytest.mark.parametrize("case", sorted(_PAIRS))
def test_deleting_the_instance_member_keeps_the_static_id(tmp_path: Path, case: str) -> None:
    ext, _i, _s, kind = _PAIRS[case]
    both = _ids_at(tmp_path / "both", f"a.{ext}", _class(case))
    alone = _ids_at(tmp_path / "alone", f"a.{ext}", _class(case, instance=False))
    assert alone[(3, kind)] == both[(3, kind)]


@pytest.mark.parametrize("case", sorted(_PAIRS))
def test_deleting_the_static_member_keeps_the_instance_id(tmp_path: Path, case: str) -> None:
    ext, _i, _s, kind = _PAIRS[case]
    both = _ids_at(tmp_path / "both", f"a.{ext}", _class(case))
    alone = _ids_at(tmp_path / "alone", f"a.{ext}", _class(case, static=False))
    assert alone[(2, kind)] == both[(2, kind)]


def test_instance_method_hashes_an_empty_decorators_slot(tmp_path: Path) -> None:
    """The formula with ``decorators=""`` is the id an instance member had
    before scheme v17, so instance ids do not move; the static member's id is
    the same formula with ``decorators="static"``."""
    rel = "a.ts"
    syms = _symbols(tmp_path, rel, _class("ts-method"))
    fid = make_file_stable_id("typescript", rel)
    by_line = {s.span.start_line: s for s in syms if s.kind == "method"}
    for line, word in ((2, ""), (3, "static")):
        expected = make_typed_stable_id(
            "method", normalize_jsts_signature("(m: string): void"), decorators=word,
            name="error", qualified_name="C.error", file_stable_id=fid,
        )
        assert by_line[line].stable_id == expected


def test_member_decorators_reads_only_static(tmp_path: Path) -> None:
    """``static`` is the one hashed word: ``readonly``, ``override``, an
    accessibility modifier or ``async`` beside it add nothing, and an instance
    member hashes ``""``."""
    import tree_sitter
    import tree_sitter_typescript

    parser = tree_sitter.Parser(
        tree_sitter.Language(tree_sitter_typescript.language_typescript())
    )
    src = (
        b"class C {\n"
        b"  private static readonly a: number = 1;\n"
        b"  override async b(): Promise<void> {}\n"
        b"  static async c() {}\n"
        b"  public d = 1;\n"
        b"}\n"
    )
    body = parser.parse(src).root_node.children[0].child_by_field_name("body")
    assert body is not None
    words = [_jsts_member_decorators(m) for m in body.named_children]
    assert words == ["static", "", "static", ""]


_NESTED = (
    "export class N {\n"
    "  run(p: Promise<number>): void { p.then((v: number) => v + 1); }\n"
    "  static run(p: Promise<number>): void { p.then((v: number) => v + 1); }\n"
    "}\n"
)


def test_callbacks_in_static_and_instance_methods_get_two_ids(tmp_path: Path) -> None:
    syms = _symbols(tmp_path, "n.ts", _NESTED)
    cbs = {s.span.start_line: s.stable_id for s in syms if s.kind == "function"}
    assert set(cbs) == {2, 3}
    assert cbs[2] != cbs[3]


def test_deleting_the_instance_method_keeps_the_static_callback_id(tmp_path: Path) -> None:
    alone_text = _NESTED.replace(
        "  run(p: Promise<number>): void { p.then((v: number) => v + 1); }\n", "\n",
    )
    both = _symbols(tmp_path / "both", "n.ts", _NESTED)
    alone = _symbols(tmp_path / "alone", "n.ts", alone_text)
    split_within_file_stable_id_collisions(both)
    split_within_file_stable_id_collisions(alone)

    def cb(syms: list[Symbol]) -> str | None:
        return next(s.stable_id for s in syms if s.kind == "function" and s.span.start_line == 3)

    assert cb(alone) == cb(both)


def test_scope_label_of_a_static_method_names_static(tmp_path: Path) -> None:
    """A symbol nested in ``static run`` is scoped ``static run``; one nested in
    the instance ``run`` keeps the bare name, so its id does not move."""
    import tree_sitter
    import tree_sitter_typescript

    parser = tree_sitter.Parser(
        tree_sitter.Language(tree_sitter_typescript.language_typescript())
    )
    src = _NESTED.encode()
    tree = parser.parse(src)
    arrows: list[tree_sitter.Node] = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "arrow_function":
            arrows.append(n)
        stack.extend(n.children)
    scopes = sorted(
        (a.start_point[0], _jsts_scope(a, src)) for a in arrows
    )
    assert [s for _line, s in scopes] == [["N", "run"], ["N", "static run"]]


def test_static_method_with_a_computed_name_labels_nothing() -> None:
    """A computed-name method (``static [k]()``) has no name to label, static
    or not; it adds nothing to the chain, as an instance one does."""
    import tree_sitter
    import tree_sitter_typescript

    parser = tree_sitter.Parser(
        tree_sitter.Language(tree_sitter_typescript.language_typescript())
    )
    src = b"class C {\n  static [k]() { f(() => 1); }\n  [j]() { f(() => 1); }\n}\n"
    tree = parser.parse(src)
    arrows: list[tree_sitter.Node] = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "arrow_function":
            arrows.append(n)
        stack.extend(n.children)
    assert [_jsts_scope(a, src) for a in arrows] == [["C"], ["C"]]
