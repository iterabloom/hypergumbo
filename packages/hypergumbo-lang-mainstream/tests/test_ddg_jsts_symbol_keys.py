# SPDX-License-Identifier: AGPL-3.0-or-later
"""The javascript / typescript DDG stores each callable under the key the taint
walk asks for (WI-sakir, WI-jopuf).

``_ddg_taint_reaches`` gates on ``source_fn in ddg_symbols``, where
``source_fn`` is the ANALYZER's symbol id. Both ``ts_def_use`` registrations
walked only ``function_declaration`` and supplied neither ``name_for`` nor
``kind_for``. WI-sakir was filed on the WI-ripas shape (a method stored under
an unreachable key); probing the analyzer showed the defect is one step
earlier: a class method is ``method_definition`` and was NEVER WALKED at all.
Measured on dash.js before the fix: 0 of 766 methods, 0 of 54 getters.

``js_ts.py`` decides a method's name and kind slot inline -- ``Class.method``
from the nearest enclosing ``class_declaration``, and ``method`` / ``getter``
/ ``setter`` from a ``get``/``set`` child. WI-jopuf's objection to widening
the node types was exactly that re-deriving that decision in the spec would
be a second copy of a production judgement; the fix extracts it into
``jsts_method_name`` / ``jsts_method_kind`` and both the analyzer and the
spec call them.

Plain ``function_declaration`` keys already matched (a nested function is
named bare by BOTH producers) and are pinned here so they stay that way.

THE PATH SLOT IS NORMALISED, DELIBERATELY AND VISIBLY, as in the python
sibling: the two producers can be handed different root semantics.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core import dataflow_scope
from hypergumbo_core.ddg_build import build_repo_ddg
from hypergumbo_lang_mainstream.js_ts import analyze_javascript

_SRC = """
function top(a) {
  const x = a + 1;
  function inner(b) {
    const y = b + x;
    return y;
  }
  return inner(x);
}
class Outer {
  meth(p) {
    const q = p + 1;
    return q;
  }
  get g() {
    const r = this.v;
    return r;
  }
  set g(val) {
    const s = val;
    this.v = s;
  }
  static st(z) {
    const w = z;
    return w;
  }
}
function wrap() {
  class Inner {
    deep(d) {
      const e = d;
      return e;
    }
  }
  return Inner;
}
"""

_CALLABLE_KINDS = ("function", "method", "getter", "setter")


def _basename_path_slot(symbol_id: str) -> str:
    parts = symbol_id.split(":")
    parts[1] = parts[1].rsplit("/", 1)[-1]
    return ":".join(parts)


def _both_arms(tmp_path: Path, ext: str, language: str) -> tuple[set[str], set[str]]:
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"n.{ext}").write_text(_SRC)
    # INV-zunik: def/use extractors register by import side-effect; an empty
    # registry would make every assertion below vacuously true.
    dataflow_scope.ensure_def_use_extractors_registered()
    ddg = build_repo_ddg(root, languages=(language,))
    analyzer = analyze_javascript(root)
    return (
        {_basename_path_slot(k) for k in ddg.ddg_symbols},
        {
            _basename_path_slot(s.id)
            for s in analyzer.symbols
            if s.kind in _CALLABLE_KINDS and s.language == language
        },
    )


_ARMS = pytest.mark.parametrize(
    ("ext", "language"), [("js", "javascript"), ("ts", "typescript")],
)


@_ARMS
def test_the_fixture_reaches_the_machinery(tmp_path: Path, ext: str, language: str) -> None:
    ddg_keys, analyzer_ids = _both_arms(tmp_path, ext, language)
    assert ddg_keys, "DDG stored nothing — the assertions would be vacuous"
    assert analyzer_ids, "analyzer produced no callables"


@_ARMS
def test_every_callable_is_stored_under_the_key_the_walk_asks(
    tmp_path: Path, ext: str, language: str,
) -> None:
    ddg_keys, analyzer_ids = _both_arms(tmp_path, ext, language)
    assert sorted(analyzer_ids - ddg_keys) == []


@_ARMS
def test_the_ddg_invents_no_key_the_analyzer_never_produces(
    tmp_path: Path, ext: str, language: str,
) -> None:
    ddg_keys, analyzer_ids = _both_arms(tmp_path, ext, language)
    assert sorted(ddg_keys - analyzer_ids) == []


@_ARMS
def test_the_slots_themselves(tmp_path: Path, ext: str, language: str) -> None:
    ddg_keys, _ = _both_arms(tmp_path, ext, language)
    suffixes = {k.split(":", 3)[3] for k in ddg_keys}
    assert {
        "top:function", "inner:function", "wrap:function",
        "Outer.meth:method", "Outer.g:getter", "Outer.g:setter",
        "Outer.st:method", "Inner.deep:method",
    } <= suffixes


def test_a_computed_method_name_has_no_key_and_is_skipped(tmp_path: Path) -> None:
    """``[Symbol.iterator]() {}`` carries no identifier. The shared namer answers
    ``None`` and the walk skips the node, as a missing ``name`` field is by the
    default, rather than minting a key the analyzer never emits."""
    import tree_sitter
    import tree_sitter_javascript

    from hypergumbo_lang_mainstream.js_ts import jsts_method_name

    src = b"class A { [Symbol.iterator]() { const x = 1; return x; } }"
    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_javascript.language()))
    root = parser.parse(src).root_node
    stack, method = [root], None
    while stack:
        node = stack.pop()
        if node.type == "method_definition":
            method = node
            break
        stack.extend(node.children)
    assert method is not None
    assert jsts_method_name(method, src) is None

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "c.js").write_text(src.decode())
    dataflow_scope.ensure_def_use_extractors_registered()
    assert build_repo_ddg(proj, languages=("javascript",)).ddg_symbols == set()
