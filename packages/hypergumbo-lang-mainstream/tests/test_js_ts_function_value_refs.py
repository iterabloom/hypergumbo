# SPDX-License-Identifier: AGPL-3.0-or-later
"""A JS/TS function VALUE in a binding or an array names its function (META-mino).

js_ts.py recorded a function value in an object literal (``{run: h}``,
``{h}``), a property write (``obj.h = h``, WI-vubal) and a call argument. Three
value positions had no edge, so a function used only there had no caller:

- a declaration: ``const f = handler`` -- at module level or in a function;
- a plain assignment: ``f = handler``;
- an array element: ``[a, b]``, the dispatch table.

Each is a ``references`` edge with ``evidence_type="function_reference"``,
which is not a callback registration (``edge_types.is_callback_registration``),
so taint does not walk it: storing a function is not calling it, the same
reason ``module.exports = {parse}`` carries the non-crossable object-literal
stamp.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.edge_types import is_callback_registration
from hypergumbo_lang_mainstream.js_ts import analyze_javascript

_TARGETS = "export function a1() { return 1; }\nexport function a2() { return 2; }\n"


def _refs(
    root: Path, files: dict[str, str], names: tuple[str, ...] = ("a1", "a2"),
) -> list[tuple[str, str, str]]:
    """(src name, dst name, evidence) of every ``references`` edge to ``names``."""
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    result = analyze_javascript(root)
    by_id = {s.id: s for s in result.symbols}
    out = []
    for e in result.edges:
        dst = by_id.get(e.dst)
        if e.edge_type == "references" and dst is not None and dst.name in names:
            assert not is_callback_registration(e.edge_type, e.meta)
            out.append((by_id[e.src].name, dst.name, e.evidence_type))
    return sorted(out)


def test_a_local_declaration_names_its_function(tmp_path: Path) -> None:
    assert _refs(tmp_path, {
        "t.js": _TARGETS,
        "use.js": (
            "import { a1 } from './t.js';\n"
            "export function use() { const f = a1; return f(); }\n"
        ),
    }) == [("use", "a1", "function_reference")]


def test_a_module_level_binding_is_anchored_on_the_module(tmp_path: Path) -> None:
    refs = _refs(tmp_path, {
        "t.js": _TARGETS,
        "use.js": "import { a1 } from './t.js';\nexport const run = a1;\n",
    })
    assert [(dst, ev) for _src, dst, ev in refs] == [("a1", "function_reference")]


def test_a_plain_assignment_names_its_function(tmp_path: Path) -> None:
    assert _refs(tmp_path, {
        "t.js": _TARGETS,
        "use.js": (
            "import { a1 } from './t.js';\n"
            "export function use() { let f; f = a1; return f(); }\n"
        ),
    }) == [("use", "a1", "function_reference")]


def test_an_array_element_names_its_function(tmp_path: Path) -> None:
    assert _refs(tmp_path, {
        "t.ts": _TARGETS,
        "use.ts": (
            "import { a1, a2 } from './t';\n"
            "export function use(): number { const t = [a1, a2]; return t[0](); }\n"
        ),
    }) == [("use", "a1", "function_reference"), ("use", "a2", "function_reference")]


def test_a_local_name_is_not_the_imported_function(tmp_path: Path) -> None:
    assert _refs(tmp_path, {
        "t.js": _TARGETS,
        "use.js": (
            "export function use(a1) { const f = a1; const t = [a1]; return f() + t[0](); }\n"
        ),
    }) == []


def test_values_that_are_not_functions_name_nothing(tmp_path: Path) -> None:
    assert _refs(tmp_path, {
        "t.js": _TARGETS + "export const limit = 3;\nexport class K {}\n",
        "use.js": (
            "import { limit, K, a1 } from './t.js';\n"
            "export function use() { const n = limit; const C = K; const xs = [limit];"
            " const r = a1(); return n + xs[0] + r; }\n"
        ),
    }, names=("a1", "limit", "K")) == []


def test_object_literal_values_keep_their_own_evidence(tmp_path: Path) -> None:
    """The refactor moved the pair and shorthand arms onto the shared helper;
    their stamp is unchanged."""
    assert _refs(tmp_path, {
        "t.js": _TARGETS,
        "use.js": (
            "import { a1, a2 } from './t.js';\n"
            "export function use() { return { run: a1, a2 }; }\n"
        ),
    }) == [("use", "a1", "object_field_reference"), ("use", "a2", "object_field_reference")]
