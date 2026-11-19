# SPDX-License-Identifier: AGPL-3.0-or-later
"""A JS/TS scope label is a name, never a pattern's or a body's source text.

``_jsts_scope`` labels the variable a function or object literal is bound to
(INV-fabih), and read the declarator's ``name`` field as text. That field is a
destructuring ``object_pattern`` / ``array_pattern`` in ``const { a, b } =
useX(cb)``, so ``cb`` (``_cb_useX``) and everything declared in it hashed the
pattern's text: adding a binding or a default to the pattern moved their ids.
In koel's ``AiChatMessage.spec.ts`` tree-sitter-typescript's error recovery
around ``original<typeof import('m')>()`` makes ``describe``'s whole callback
body the "pattern" of a recovered declarator, so every ``it`` callback hashed
the source text of that body, and editing any test moved them all.

A declarator now labels a scope only when it binds one identifier, as the
named-callable path (``const f = () => {}``) already required. Each case
analyses two versions of one file whose edit keeps the checked symbols at their
positions (a binding renamed, or a later test edited); matched by (kind, name,
line, column).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.js_ts import _jsts_scope, analyze_javascript


def _ids(tmp_path: Path, rel: str, text: str) -> dict[tuple, str]:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    out: dict[tuple, str] = {}
    for sym in analyze_javascript(tmp_path).symbols:
        if sym.kind == "file" or not sym.path.endswith(rel):
            continue
        assert sym.stable_id is not None, sym.name
        out[(sym.kind, sym.name, sym.span.start_line, sym.span.start_col)] = sym.stable_id
    return out


_KOEL_HEAD = (
    "vi.mock('m', async original => {\n"
    "  const mod = await original<typeof import('m')>()\n"
    "  return { ...mod, f: vi.fn() }\n"
    "})\n\n"
    "describe('x', () => {\n"
    "  const h = createHarness()\n\n"
    "  it('a', () => {\n    h.render(1)\n  })\n\n"
)

#: (file, version 1, version 2, the symbols that must keep their id).
_CASES = {
    "ts-object-pattern": (
        "src/a.ts",
        "const { select, isSelected } = useListSelection(items, s => {\n"
        "  function inner() { return s }\n  return inner()\n})\n",
        "const { selekt, isSelected } = useListSelection(items, s => {\n"
        "  function inner() { return s }\n  return inner()\n})\n",
        {"_cb_useListSelection", "inner"},
    ),
    "ts-array-pattern": (
        "src/b.ts",
        "const [first, second] = makePair(() => {\n  class Inner {}\n  return Inner\n})\n",
        "const [first, secund] = makePair(() => {\n  class Inner {}\n  return Inner\n})\n",
        {"_cb_makePair", "Inner"},
    ),
    "js-object-pattern": (
        "src/c.js",
        "let { x = 1, y } = wrap(function () {\n  function innerF() {}\n  return innerF\n})\n",
        "let { x = 2, z } = wrap(function () {\n  function innerF() {}\n  return innerF\n})\n",
        {"_cb_wrap", "innerF"},
    ),
    "js-array-pattern": (
        "src/d.js",
        "var [a, b] = pair(() => {\n  const local = () => 1\n  return local\n})\n",
        "var [a, c] = pair(() => {\n  const local = () => 1\n  return local\n})\n",
        {"_cb_pair", "local"},
    ),
    # koel AiChatMessage.spec.ts: the recovered "pattern" is describe's body,
    # so an edit to a LATER test moved the earlier tests' callbacks.
    "ts-recovered-body": (
        "src/e.spec.ts",
        _KOEL_HEAD + "  it('b', () => {\n    h.render(2)\n  })\n})\n",
        _KOEL_HEAD + "  it('b changed', () => {\n    h.render(3)\n    h.render(4)\n  })\n})\n",
        {"_cb_anonymous"},
    ),
}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_editing_a_pattern_or_body_keeps_the_nested_ids(tmp_path: Path, case: str) -> None:
    rel, before, after, names = _CASES[case]
    old = _ids(tmp_path / "v1", rel, before)
    new = _ids(tmp_path / "v2", rel, after)
    checked = [key for key in old if key[1] in names and key in new]
    assert {key[1] for key in checked} == names, sorted(old)
    moved = [key for key in checked if old[key] != new[key]]
    assert not moved, moved


def test_a_named_binding_is_still_a_scope_label(tmp_path: Path) -> None:
    """An identifier declarator keeps labelling its scope (INV-fabih): the
    ``inner`` of ``const b = wrap(cb)`` keeps its id when the same-shaped
    ``const a`` before it is deleted (lines keep their numbers), which an
    unlabelled chain would not (the two would tie and be numbered)."""
    second = "const b = wrap(() => {\n  function inner() {}\n})\n"
    both = _ids(tmp_path / "v1", "src/f.ts",
                "const a = wrap(() => {\n  function inner() {}\n})\n" + second)
    alone = _ids(tmp_path / "v2", "src/f.ts", "\n\n\n" + second)
    key = ("function", "inner", 5, 2)
    assert both[key] == alone[key]
    # The analyzer alone does not number ties (the pipeline does), so an
    # unlabelled chain shows here as the two ids being one.
    assert both[("function", "inner", 2, 2)] != both[key]


def test_scope_labels_are_names(tmp_path: Path) -> None:
    """Every label ``_jsts_scope`` returns in the cases above is a name."""
    import tree_sitter
    import tree_sitter_typescript

    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_typescript.language_typescript()))
    for _rel, before, _after, _names in _CASES.values():
        source = before.encode()
        stack = [parser.parse(source).root_node]
        while stack:
            node = stack.pop()
            stack.extend(node.children)
            for label in _jsts_scope(node, source):
                assert label.isidentifier(), (label, before)
