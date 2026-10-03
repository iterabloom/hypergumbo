# SPDX-License-Identifier: AGPL-3.0-or-later
"""A function identifier assigned to a property emits a ``references`` edge
from the assigning function (WI-vubal).

``ws.onmessage = handle`` produced no edge of any type from the registering
function to ``handle``: the registration was invisible, so a forward slice from
``setup`` could not show that ``handle`` runs and a reverse slice from
``handle`` could not show who registered it. ``js_ts.py`` already emitted a
``references`` edge for the same relationship in two sibling positions -- an
object-literal value (``{onClick: handleClick}``) and a callback argument --
and this is the third spelling.

THE EVIDENCE TYPE SAYS WHICH RELATIONSHIP IT IS, because one of them is read
by taint and the other must not be (``edge_types.is_callback_registration``):

* ``callback_argument_reference`` -- the REGISTRATION shape, stamped only when
  the assignment is one the WI-dosuh branch already classifies as a catalogued
  handler registration (``ws.onmessage = handle`` on a typed receiver). That is
  the same relationship as ``ws.addEventListener('message', handle)``, which
  carries the same stamp, so taint now enters a named handler registered by
  assignment exactly as it enters one registered by argument.
* ``object_field_reference`` -- every other property write
  (``obj.handler = fn``, ``el.onclick = fn``, ``module.exports = fn``). It is
  the object-literal sibling's stamp, and it is deliberately NOT crossable:
  ``module.exports = parse`` must not flow a module-level source into every
  exported function (the INV-putug error class the predicate's docstring
  names).

The target is resolved by the scope binder (WI-sofuh) like every other bare
identifier: a parameter or local value names nothing and emits nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.edge_types import is_callback_registration
from hypergumbo_core.ir import Edge, Symbol
from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _run(root: Path, files: dict[str, str]) -> tuple[list[Edge], dict[str, Symbol]]:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text)
    result = analyze_javascript(root)
    return result.edges, {s.id: s for s in result.symbols}


def _refs(edges: list[Edge], by_id: dict[str, Symbol]) -> list[tuple[str, str, str]]:
    """``(src name, dst name, evidence_type)`` of each references edge into a
    function symbol."""
    return sorted(
        (by_id[e.src].name, by_id[e.dst].name, e.evidence_type)
        for e in edges
        if e.edge_type == "references" and e.dst in by_id and e.src in by_id
        and by_id[e.dst].kind == "function"
    )


_FILED = (
    "const { exec } = require('child_process');\n"
    "function handle(ev) { exec('ls ' + ev.data); }\n"
    "function setup(url) {\n"
    "  const ws = new WebSocket(url);\n"
    "  ws.onmessage = handle;\n"
    "}\n"
)


class TestTheFiledSpelling:
    def test_setup_references_handle(self, tmp_path: Path) -> None:
        edges, by_id = _run(tmp_path, {"app.js": _FILED})
        assert _refs(edges, by_id) == [
            ("setup", "handle", "callback_argument_reference"),
        ]

    def test_it_is_a_callback_registration_for_taint(self, tmp_path: Path) -> None:
        """The coupling WI-nisud's note asked to pin: the predicate the taint
        walk reads accepts this edge, so the handler is ENTERED."""
        edges, by_id = _run(tmp_path, {"app.js": _FILED})
        [edge] = [
            e for e in edges
            if e.edge_type == "references" and e.dst in by_id
            and by_id[e.dst].name == "handle"
        ]
        assert is_callback_registration(
            edge.edge_type, {"evidence_type": edge.evidence_type},
        )

    def test_the_registration_row_edge_is_unchanged(self, tmp_path: Path) -> None:
        edges, _ = _run(tmp_path, {"app.js": _FILED})
        assert "javascript:WebSocket:0-0:onmessage:unresolved" in {
            e.dst for e in edges if e.edge_type == "calls"
        }


class TestOtherPropertyWrites:
    @pytest.mark.parametrize("stmt", [
        "obj.handler = handle;",
        "el.onclick = handle;",
        "module.exports = handle;",
        "exports.handle = handle;",
        "table['go'] = handle;",
        "obj.handler ||= handle;",
    ])
    def test_object_field_reference(self, tmp_path: Path, stmt: str) -> None:
        edges, by_id = _run(tmp_path, {"app.js": (
            "function handle(ev) { return ev; }\n"
            f"function setup(obj, el, table) {{ {stmt} }}\n"
        )})
        assert _refs(edges, by_id) == [
            ("setup", "handle", "object_field_reference"),
        ]

    def test_object_field_reference_is_not_a_registration(
        self, tmp_path: Path,
    ) -> None:
        assert not is_callback_registration(
            "references", {"evidence_type": "object_field_reference"},
        )

    def test_module_scope_assignment_is_anchored_to_the_file(
        self, tmp_path: Path,
    ) -> None:
        edges, by_id = _run(tmp_path, {"app.js": (
            "function handle(ev) { return ev; }\n"
            "module.exports = handle;\n"
        )})
        hits = [
            (by_id[e.src].kind, by_id[e.dst].name)
            for e in edges
            if e.edge_type == "references" and e.dst in by_id and e.src in by_id
        ]
        assert hits == [("file", "handle")], hits


class TestWhatMustNotEmit:
    @pytest.mark.parametrize("rhs", ["h", "local", "notDeclaredAnywhere", "x.y"])
    def test_no_function_symbol_no_edge(self, tmp_path: Path, rhs: str) -> None:
        edges, by_id = _run(tmp_path, {"app.js": (
            "function handle(ev) { return ev; }\n"
            f"function setup(obj, h) {{ const local = 1; obj.handler = {rhs}; }}\n"
        )})
        assert _refs(edges, by_id) == []

    def test_a_plain_variable_assignment_is_not_a_property(
        self, tmp_path: Path,
    ) -> None:
        edges, by_id = _run(tmp_path, {"app.js": (
            "function handle(ev) { return ev; }\n"
            "function setup() { let h; h = handle; return h; }\n"
        )})
        assert _refs(edges, by_id) == []

    def test_own_file_handler_wins_over_another_files(self, tmp_path: Path) -> None:
        edges, by_id = _run(tmp_path, {
            "a.js": "function handle(ev) { return ev; }\nfunction setup(o) { o.h = handle; }\n",
            "b.js": "function handle(ev) { return ev; }\n",
        })
        [edge] = [e for e in edges if e.edge_type == "references" and e.dst in by_id]
        assert Path(by_id[edge.dst].path).name == "a.js"
