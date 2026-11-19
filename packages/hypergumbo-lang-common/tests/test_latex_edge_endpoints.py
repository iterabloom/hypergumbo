# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every LaTeX edge endpoint is a node id (INV-zipaj).

The analyzer wrote ``src=f"{rel_path}:file"`` and ``dst=<raw name>``
(``amsmath``, ``sec:intro``). Neither is an id: finalize parsed the src with
its fewer-than-five-slot fallback (the file path in the language slot), every
``\\ref{sec:...}`` landed on one ``sec`` node, and the id_format validator
reported an error on every LaTeX repository. These tests run the production
path (``analyze_latex`` and ``hypergumbo run``) over a fixture and assert what
each endpoint IS: the file's node, a label symbol, a file of the tree, or a
stand-in in the id grammar that keeps the name as written.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import make_file_id, make_module_import_dst
from hypergumbo_core.analyze.edge_target import non_id_edge_endpoints
from hypergumbo_core.cli import main
from hypergumbo_core.survey_io import load_substrate
from hypergumbo_lang_common.latex import analyze_latex

_DOC = r"""\documentclass{article}
\usepackage[utf8]{amsmath,localstyle}
\begin{document}
\input{sub/part}
\include{missing}
\section{Intro}\label{sec:intro}
See \ref{sec:intro} and \ref{sec:nowhere} and \cite{knuth:1984,lamport94}.
\end{document}
"""


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "doc.tex").write_text(_DOC)
    (repo / "sub" / "part.tex").write_text("\\section{Part}\n")
    (repo / "localstyle.sty").write_text("\\ProvidesPackage{localstyle}\n")
    return repo


def _edges(repo: Path, edge_type: str):
    result = analyze_latex(repo)
    return result, [e for e in result.edges if e.edge_type == edge_type]


def test_no_endpoint_is_outside_the_id_grammar(tree: Path) -> None:
    result = analyze_latex(tree)
    assert result.edges
    assert non_id_edge_endpoints(result.symbols, result.edges) == []


def test_every_src_is_the_file_node(tree: Path) -> None:
    result = analyze_latex(tree)
    assert {e.src for e in result.edges} == {make_file_id("latex", "doc.tex")}


def test_a_ref_to_a_defined_label_ends_on_the_label_symbol(tree: Path) -> None:
    result, refs = _edges(tree, "references")
    [label] = [s for s in result.symbols if s.kind == "label"]
    to_label = [e for e in refs if e.dst == label.id]
    assert len(to_label) == 1
    assert to_label[0].is_resolved


def test_a_ref_to_no_label_keeps_its_colon_in_the_stand_in(tree: Path) -> None:
    _, refs = _edges(tree, "references")
    labels = [e for e in refs if (e.meta or {}).get("ref_type") == "label"]
    unresolved = [e for e in labels if not e.is_resolved]
    assert [e.dst_ref.name for e in unresolved if e.dst_ref] == ["sec:nowhere"]
    # The name slot is sanitized, so the id still parses from both ends.
    assert unresolved[0].dst.split(":")[-2] == "sec.nowhere"
    assert unresolved[0].dst.endswith(":0-0:sec.nowhere:unresolved")


def test_every_key_of_a_citation_list_is_an_edge(tree: Path) -> None:
    _, refs = _edges(tree, "references")
    cites = [e for e in refs if (e.meta or {}).get("ref_type") == "citation"]
    assert sorted(e.dst_ref.name for e in cites if e.dst_ref) == [
        "knuth:1984", "lamport94",
    ]


def test_an_input_of_a_file_of_the_tree_ends_on_its_file_node(tree: Path) -> None:
    _, includes = _edges(tree, "includes")
    dsts = {e.dst: e for e in includes}
    assert make_file_id("latex", "sub/part.tex") in dsts
    assert dsts[make_file_id("latex", "sub/part.tex")].is_resolved
    missing = dsts[make_module_import_dst("latex", "missing")]
    assert not missing.is_resolved


def test_every_package_of_a_usepackage_list_is_an_edge(tree: Path) -> None:
    _, imports = _edges(tree, "imports")
    assert sorted(e.dst for e in imports) == sorted([
        make_module_import_dst("latex", "amsmath"),
        make_file_id("latex", "localstyle.sty"),
    ])


def test_a_ref_list_names_each_label(tmp_path: Path) -> None:
    (tmp_path / "a.tex").write_text(
        "\\label{a}\\label{b}\nSee \\cref{a,b}.\n",
    )
    result, refs = _edges(tmp_path, "references")
    label_ids = {s.id for s in result.symbols if s.kind == "label"}
    assert {e.dst for e in refs} == label_ids
    assert len(label_ids) == 2


def test_a_label_defined_twice_in_other_files_prefers_this_file(tmp_path: Path) -> None:
    (tmp_path / "a.tex").write_text("\\label{x}\nSee \\ref{x}.\n")
    (tmp_path / "b.tex").write_text("\\label{x}\n")
    result, refs = _edges(tmp_path, "references")
    [own] = [s for s in result.symbols if s.kind == "label" and s.path == "a.tex"]
    assert [e.dst for e in refs] == [own.id]


def test_a_label_defined_in_two_other_files_is_not_guessed(tmp_path: Path) -> None:
    (tmp_path / "a.tex").write_text("See \\ref{x}.\n")
    (tmp_path / "b.tex").write_text("\\label{x}\n")
    (tmp_path / "c.tex").write_text("\\label{x}\n")
    _, refs = _edges(tmp_path, "references")
    assert [e.is_resolved for e in refs] == [False]
    assert refs[0].dst_ref is not None and refs[0].dst_ref.name == "x"


def test_a_label_defined_once_in_another_file_is_found(tmp_path: Path) -> None:
    (tmp_path / "a.tex").write_text("See \\ref{x}.\n")
    (tmp_path / "b.tex").write_text("\\label{x}\n")
    result, refs = _edges(tmp_path, "references")
    [label] = [s for s in result.symbols if s.kind == "label"]
    assert [e.dst for e in refs] == [label.id]


def test_the_run_records_no_non_id_endpoint_and_no_id_format_error(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "map.json"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    main([
        "run", str(tree), "--out", str(out),
        "--no-sketch-fan-out", "--no-handler-slices",
    ])
    data = load_substrate(out)
    [latex] = [r for r in data["analysis_runs"] if r["pass"].startswith("latex")]
    assert latex["non_id_edge_endpoints"] == 0
    violations = data["validation_report"]["violations"]
    assert not [v for v in violations if v.get("validator_class") == "id_format"]
    nodes = {n["id"] for n in data["nodes"]}
    assert make_file_id("latex", "doc.tex") in nodes
