# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every import / include endpoint these analyzers write is a node id (WI-tatop).

Gleam, Odin and V wrote an import edge as ``file:<path>`` ->
``<lang>:import:<module>``; Twig wrote ``twig:template:<name>``; Pony drew a
call outside any declaration from ``pony:<path>``. None is an id: finalize
parsed each with its fewer-than-five-slot fallback, and every import of a
language landed on one ``<unknown>`` node. Each case runs the analyzer's
production entry point over a fixture and asserts the strict gate
(``edge_target.non_id_edge_endpoints``) is empty and what the endpoints are.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import make_file_id, make_module_import_dst
from hypergumbo_core.analyze.edge_target import non_id_edge_endpoints
from hypergumbo_lang_extended1.gleam import analyze_gleam
from hypergumbo_lang_extended1.odin import analyze_odin
from hypergumbo_lang_extended1.twig import analyze_twig
from hypergumbo_lang_extended1.v_lang import analyze_v


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


_IMPORTS: dict[str, tuple[Callable, str, str, list[str]]] = {
    "gleam": (
        analyze_gleam, "app.gleam",
        "import gleam/io\nimport gleam/list\n\npub fn main() {\n  io.println(\"hi\")\n}\n",
        ["gleam/io", "gleam/list"],
    ),
    "odin": (
        analyze_odin, "main.odin",
        'package main\n\nimport "core:fmt"\nimport "core:os"\n\n'
        'main :: proc() {\n\tfmt.println("hi")\n}\n',
        ["core:fmt", "core:os"],
    ),
    "v": (
        analyze_v, "main.v",
        "module main\n\nimport os\nimport json\n\nfn main() {\n\tprintln(os.args)\n}\n",
        ["os", "json"],
    ),
}


@pytest.mark.parametrize("lang", sorted(_IMPORTS))
def test_imports_start_at_the_file_and_end_on_a_module_stand_in(
    tmp_path: Path, lang: str,
) -> None:
    analyze, name, text, modules = _IMPORTS[lang]
    result = analyze(_write(tmp_path, {name: text}))
    imports = [e for e in result.edges if e.edge_type == "imports"]
    assert non_id_edge_endpoints(result.symbols, imports) == []
    assert {e.src for e in imports} == {make_file_id(lang, name)}
    assert sorted(e.dst for e in imports) == sorted(
        make_module_import_dst(lang, m) for m in modules
    )


def test_twig_extends_and_includes_end_on_template_stand_ins(tmp_path: Path) -> None:
    result = analyze_twig(_write(tmp_path, {
        "tpl/page.twig": (
            "{% extends 'base.twig' %}\n{% include 'partial.twig' %}\n"
            "{{ include('card.twig') }}\n"
            "{% block body %}{{ name }}{% endblock %}\n"
        ),
        "tpl/base.twig": "<html>{% block body %}{% endblock %}</html>\n",
    }))
    refs = [e for e in result.edges if e.edge_type in ("extends", "includes")]
    assert non_id_edge_endpoints(result.symbols, refs) == []
    assert sorted(e.dst for e in refs) == sorted(
        make_module_import_dst("twig", t)
        for t in ("base.twig", "partial.twig", "card.twig")
    )
    assert {e.meta["template"] for e in refs} == {"base.twig", "partial.twig", "card.twig"}
