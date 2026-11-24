# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/nix.py
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/vue.py
"""Two nodes with one name and one kind on one line get two node ids.

A node id is ``language:path:lines:name:kind``. A Nix binding and the
attribute inside its value (servant's ``tutorial = mkDevShell { tutorial =
true; };``) and two ``<span v-if>`` on one template line had ONE id, so an
edge could not tell them apart. ADR-0036 Ruling 1 (amended 2026-11-23) lets
the name slot carry ``@<column>``: the first keeps the plain id, a twin gets
its own start column.

Each test has a control that must stay unsuffixed.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_common.nix import analyze_nix_files
from hypergumbo_lang_common.vue import analyze_vue


def _slot(symbol) -> str:
    return symbol.id.rsplit(":", 2)[1]


def test_nix_binding_and_inner_attribute_get_two_ids(tmp_path: Path) -> None:
    (tmp_path / "flake.nix").write_text(
        "{\n"
        "  outputs = { self }: {\n"
        "    devShells = {\n"
        "      tutorial = mkDevShell { tutorial = true; };\n"
        "      other = mkDevShell {\n"
        "        other = true;\n"
        "      };\n"
        "    };\n"
        "  };\n"
        "}\n"
    )
    symbols = analyze_nix_files(tmp_path).symbols
    twins = [s for s in symbols if s.name == "tutorial"]
    assert [(s.span.start_col, _slot(s)) for s in twins] == [
        (6, "tutorial"), (30, "tutorial@30"),
    ]
    ids = [s.id for s in symbols]
    assert len(ids) == len(set(ids))
    # control: the same shape over two lines needs no suffix
    assert [_slot(s) for s in symbols if s.name == "other"] == ["other", "other"]
    assert [_slot(s) for s in symbols if "@" in s.id] == ["tutorial@30"]


def test_vue_same_line_directives_get_two_ids(tmp_path: Path) -> None:
    (tmp_path / "A.vue").write_text(
        "<template>\n"
        '  <div><span v-if="a"></span><span v-if="b"></span></div>\n'
        '  <p v-if="c"></p>\n'
        '  <p v-if="d"></p>\n'
        "</template>\n"
    )
    symbols = analyze_vue(tmp_path).symbols
    spans = [s for s in symbols if s.name == "span:v-if"]
    assert [(s.span.start_col, _slot(s)) for s in spans] == [
        (7, "span.v-if"), (29, "span.v-if@29"),
    ]
    ids = [s.id for s in symbols if s.kind != "file"]
    assert len(ids) == len(set(ids))
    # control: one directive per line
    assert [_slot(s) for s in symbols if s.name == "p:v-if"] == ["p.v-if", "p.v-if"]


def test_vue_twins_in_two_files_do_not_touch_each_other(tmp_path: Path) -> None:
    """The holder table is per file: the same line in another file holds
    another id (the path is in it) and stays plain."""
    for name in ("A.vue", "B.vue"):
        (tmp_path / name).write_text('<template>\n  <span v-if="a"></span>\n</template>\n')
    symbols = [s for s in analyze_vue(tmp_path).symbols if s.kind == "directive"]
    assert len(symbols) == 2
    assert not any("@" in s.id for s in symbols)
