# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer stable_ids for the extended1 package's non-callable kinds (WI-tusad).

These analyzers computed no ``stable_id`` for the kinds below, which rode the
ADR-0035 backstop floor key ``(kind, language, path, name)``: every gnuplot
``plot`` (all named ``plot``), two VHDL ``architecture rtl`` of different
entities, ``Alice`` in two Mermaid diagrams of one file, each shared the key
and was left null. Each now mints ``overload_arity.scoped_stable_id`` with the
scope the grammar nests the symbol in.

Pinned per analyzer, on its own output: every symbol of a migrated kind holds a
non-null id that is not the floor key (matched by kind, name and position),
the exact value for a scoped symbol, and the delete-an-earlier-sibling
property (the deleted lines are kept as blank lines, so each survivor matches
itself by position).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from hypergumbo_core.analyze.base import AnalysisResult, floor_stable_id_key
from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import scoped_stable_id
from hypergumbo_lang_extended1.blade import analyze_blade
from hypergumbo_lang_extended1.gnuplot import analyze_gnuplot
from hypergumbo_lang_extended1.mermaid import analyze_mermaid
from hypergumbo_lang_extended1.vhdl import analyze_vhdl_files

Analyze = Callable[[Path], AnalysisResult]
Key = tuple[str, str, int, int]


def _symbols(analyze: Analyze, root: Path, name: str, text: str) -> list[Symbol]:
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    return list(analyze(root).symbols)


def _at(symbols: list[Symbol], kinds: set[str]) -> dict[Key, Symbol]:
    picked = [s for s in symbols if s.kind in kinds]
    by_pos = {(s.kind, s.name, s.span.start_line, s.span.start_col): s for s in picked}
    assert len(by_pos) == len(picked), "two symbols of one kind and name at one position"
    return by_pos


def _assert_deletion_moves_nothing(
    analyze: Analyze, tmp_path: Path, name: str, text: str, deleted: str,
    kinds: set[str], lang: str,
) -> dict[Key, Symbol]:
    """Delete ``deleted`` (replaced by as many blank lines) and compare."""
    assert text.count(deleted) == 1
    edited = text.replace(deleted, "\n" * deleted.count("\n"))
    before = _at(_symbols(analyze, tmp_path / "r1", name, text), kinds)
    after = _at(_symbols(analyze, tmp_path / "r2", name, edited), kinds)
    assert 0 < len(after) < len(before)  # reach
    for key, sym in before.items():
        assert sym.stable_id is not None, key
        assert sym.stable_id != floor_stable_id_key(sym.kind, lang, name, sym.name), key
    for key, sym in after.items():
        assert sym.stable_id == before[key].stable_id, key
    return before


def test_gnuplot_plots_are_scoped_by_what_they_plot(tmp_path: Path) -> None:
    text = "plot sin(x)\nplot   cos(x) with lines\nsplot x*y\nreplot\n"
    before = _assert_deletion_moves_nothing(
        analyze_gnuplot, tmp_path, "fig.gp", text, "plot sin(x)\n", {"plot"}, "gnuplot",
    )
    assert before[("plot", "plot", 2, 0)].stable_id == scoped_stable_id(
        kind="plot", lang="gnuplot", path="fig.gp", name="plot",
        scope=("cos(x) with lines",),
    )


def test_blade_sections_yields_and_components_take_producer_ids(tmp_path: Path) -> None:
    text = (
        "@section('sidebar')\n<p>side</p>\n@endsection\n"
        "@section('content')\n<p>body</p>\n@endsection\n@yield('footer')\n"
        "@component('alert')\n@endcomponent\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_blade, tmp_path, "views/page.blade.php", text,
        "@section('sidebar')\n<p>side</p>\n@endsection\n",
        {"section", "yield", "component"}, "blade",
    )
    assert before[("yield", "footer", 7, 0)].stable_id == scoped_stable_id(
        kind="yield", lang="blade", path="views/page.blade.php", name="footer",
    )


def test_vhdl_architectures_and_components_are_scoped(tmp_path: Path) -> None:
    text = (
        "library ieee;\n"
        "entity a is\nend entity a;\narchitecture rtl of a is\nbegin\nend architecture rtl;\n"
        "entity b is\nend entity b;\narchitecture rtl of b is\n"
        "  component sub is port (clk : in bit); end component;\nbegin\nend architecture rtl;\n"
        "package pkg is\nend package pkg;\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_vhdl_files, tmp_path, "rtl/top.vhd", text,
        "entity a is\nend entity a;\narchitecture rtl of a is\nbegin\nend architecture rtl;\n",
        {"entity", "architecture", "component", "package"}, "vhdl",
    )
    assert before[("architecture", "rtl", 9, 0)].stable_id == scoped_stable_id(
        kind="architecture", lang="vhdl", path="rtl/top.vhd", name="rtl", scope=("b",),
    )
    assert before[("component", "sub", 10, 2)].stable_id == scoped_stable_id(
        kind="component", lang="vhdl", path="rtl/top.vhd", name="sub", scope=("b/rtl",),
    )


def test_mermaid_symbols_are_scoped_by_their_diagram(tmp_path: Path) -> None:
    text = (
        "sequenceDiagram\n  participant Alice\n"
        "flowchart TD\n  Alice[Start]\n  B{Check}\n"
        "stateDiagram\n  state \"Idle\" as S1\nclassDiagram\n  class Shape\n"
    )
    before = _assert_deletion_moves_nothing(
        analyze_mermaid, tmp_path, "docs/flow.mmd", text,
        "sequenceDiagram\n  participant Alice\n",
        {"diagram", "participant", "node", "state"}, "mermaid",
    )
    assert before[("node", "Alice", 4, 0)].stable_id == scoped_stable_id(
        kind="node", lang="mermaid", path="docs/flow.mmd", name="Alice",
        scope=("flowchart",),
    )
