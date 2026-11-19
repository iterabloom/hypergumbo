# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every edge endpoint these analyzers write is a node id (WI-tatop).

Each analyzer below spelled an endpoint by hand: an import's src as
``nix:<path>:<line>:import`` or ``robot:<path>``, a dst as
``nix:external:<target>``, ``robot:resource:<file>``, ``meson:subdir:<dir>``,
``fortran:external:<module>``. None is an id, so finalize parsed each with its
fewer-than-five-slot fallback into one ``<lang>:<unknown>:0-0:<token>``
node per language, and every edge of that spelling landed on it.

Each case runs the analyzer's production entry point over a fixture and
asserts the strict gate (``edge_target.non_id_edge_endpoints``) is empty,
then what the endpoints ARE: the file's node, a file of the tree, a stand-in
in the id grammar.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from hypergumbo_core.analyze.base import make_file_id, make_module_import_dst
from hypergumbo_core.analyze.edge_target import external_target, non_id_edge_endpoints
from hypergumbo_core.ir import Edge
from hypergumbo_lang_common.fortran import analyze_fortran_files
from hypergumbo_lang_common.hlsl import analyze_hlsl
from hypergumbo_lang_common.matlab import analyze_matlab
from hypergumbo_lang_common.meson import analyze_meson
from hypergumbo_lang_common.nix import analyze_nix_files
from hypergumbo_lang_common.puppet import analyze_puppet
from hypergumbo_lang_common.purescript import analyze_purescript
from hypergumbo_lang_common.r_lang import analyze_r_files
from hypergumbo_lang_common.racket import analyze_racket
from hypergumbo_lang_common.robot import analyze_robot
from hypergumbo_lang_common.scheme import analyze_scheme


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _run(analyze: Callable, root: Path, files: dict[str, str]) -> tuple[list, list[Edge]]:
    result = analyze(_write(root, files))
    assert result.edges, "the fixture must produce edges"
    return result.symbols, result.edges


_NIX = {
    "shell.nix": (
        "{ pkgs ? import <nixpkgs> {} }:\n"
        "let lib = import ./lib.nix; sub = import ./sub; in\n"
        "pkgs.mkShell { }\n"
    ),
    "lib.nix": "{ x = 1; }\n",
    "sub/default.nix": "{ y = 2; }\n",
}


def test_nix_imports_start_at_the_file_and_end_on_ids(tmp_path: Path) -> None:
    symbols, edges = _run(analyze_nix_files, tmp_path, _NIX)
    assert non_id_edge_endpoints(symbols, edges) == []
    imports = [e for e in edges if e.edge_type == "imports"]
    assert {e.src for e in imports} == {make_file_id("nix", "shell.nix")}
    # ``import <nixpkgs> {}`` is two nested applications and yields the same
    # edge (one id) twice; finalize keeps one.
    assert {e.dst for e in imports} == {
        make_module_import_dst("nix", "<nixpkgs>"),
        make_file_id("nix", "lib.nix"),
        make_file_id("nix", "sub/default.nix"),
    }
    resolved = {e.dst: e.is_resolved for e in imports}
    assert resolved[make_file_id("nix", "lib.nix")]
    assert not resolved[make_module_import_dst("nix", "<nixpkgs>")]


_ROBOT = {
    "tests/suite.robot": (
        "*** Settings ***\n"
        "Resource    common.robot\n"
        "Resource    ../nowhere.resource\n"
        "\n*** Test Cases ***\n"
        "Example\n"
        "    My Keyword\n"
    ),
    "tests/common.robot": "*** Keywords ***\nMy Keyword\n    Log    hi\n",
    "tests/zz_other.robot": "*** Test Cases ***\nExample\n    Log    x\n",
}


def test_robot_imports_start_at_the_file_and_end_on_ids(tmp_path: Path) -> None:
    symbols, edges = _run(analyze_robot, tmp_path, _ROBOT)
    assert non_id_edge_endpoints(symbols, edges) == []
    imports = [e for e in edges if e.edge_type == "imports"]
    assert {e.src for e in imports} == {make_file_id("robot", "tests/suite.robot")}
    assert sorted(e.dst for e in imports) == sorted([
        make_file_id("robot", "tests/common.robot"),
        make_module_import_dst("robot", "../nowhere.resource"),
    ])


def test_robot_calls_start_at_the_definition_that_holds_them(tmp_path: Path) -> None:
    """Two files declare a test ``Example``; the call is drawn from this
    file's, not from whichever the name table kept."""
    symbols, edges = _run(analyze_robot, tmp_path, _ROBOT)
    [own] = [s for s in symbols if s.name == "Example" and s.path == "tests/suite.robot"]
    calls = [e for e in edges if e.edge_type == "calls"]
    assert [e.src for e in calls] == [own.id]


def test_meson_subdir_ends_on_the_subdirectory_build_file(tmp_path: Path) -> None:
    symbols, edges = _run(analyze_meson, tmp_path, {
        "meson.build": "project('demo', 'c')\nsubdir('src')\nsubdir('gone')\n",
        "src/meson.build": "x = 1\n",
    })
    includes = [e for e in edges if e.edge_type == "includes"]
    assert non_id_edge_endpoints(symbols, includes) == []
    assert sorted(e.dst for e in includes) == sorted([
        make_file_id("meson", "src/meson.build"),
        make_module_import_dst("meson", "gone"),
    ])


def test_fortran_use_of_a_module_outside_the_tree_is_a_module_stand_in(
    tmp_path: Path,
) -> None:
    symbols, edges = _run(analyze_fortran_files, tmp_path, {
        "calc.f90": (
            "program calc\n  use iso_fortran_env\n  implicit none\n"
            "end program calc\n"
        ),
    })
    imports = [e for e in edges if e.edge_type == "imports"]
    assert non_id_edge_endpoints(symbols, imports) == []
    assert [e.dst for e in imports] == [
        make_module_import_dst("fortran", "iso_fortran_env"),
    ]


# -- a name the tree does not declare ends on a stand-in in the id grammar --
#
# Each analyzer below wrote such a dst as ``<lang>:unresolved:<name>``,
# ``<lang>:external:<name>[:function]``, ``r:builtin:<name>``,
# ``r:external:<pkg>::<name>`` or ``puppet:resource:<text>``: three or four
# slots, or a ``::`` in the span slot. Finalize folded every one of a
# language into ONE ``<lang>:<unknown>:0-0:<token>`` node (``unresolved``,
# ``external``, ``builtin``): all of a repository's unresolved Racket calls
# were one callee. Now each is ``edge_target.external_target``.

_NAMES: dict[str, tuple[Callable, dict[str, str], list[str]]] = {
    "fortran": (analyze_fortran_files, {
        "calc.f90": "program calc\n  implicit none\n  call external_sub(1)\nend program calc\n",
    }, ["external_sub"]),
    "hlsl": (analyze_hlsl, {
        "shade.hlsl": (
            "float4 helper(float4 c) { return c; }\n"
            "float4 main(float4 c : COLOR) : SV_Target {\n"
            "    return extfunc(helper(c));\n}\n"
        ),
    }, ["extfunc"]),
    "matlab": (analyze_matlab, {
        "run.m": "function y = run(x)\n  y = unknown_fn(x);\nend\n",
    }, ["unknown_fn"]),
    "nix": (analyze_nix_files, {
        "lib.nix": "{ f = x: builtins.toString (missingFn x); }\n",
    }, ["missingFn"]),
    "purescript": (analyze_purescript, {
        "Main.purs": 'module Main where\n\nmain = do\n  log "hi"\n  undefinedThing 1\n',
    }, ["undefinedThing"]),  # ``log`` is on the skip-list
    "racket": (analyze_racket, {"a.rkt": "#lang racket\n(define (f x) (g x))\n"}, ["g"]),
    "scheme": (analyze_scheme, {"b.scm": "(define (f x) (g x))\n"}, ["g"]),
    "robot": (analyze_robot, {
        "t.robot": "*** Test Cases ***\nT\n    Other Missing\n",
    }, ["Other Missing"]),
}


@pytest.mark.parametrize("lang", sorted(_NAMES))
def test_a_name_the_tree_lacks_ends_on_a_stand_in(tmp_path: Path, lang: str) -> None:
    analyze, files, names = _NAMES[lang]
    symbols, edges = _run(analyze, tmp_path, files)
    misses = [e for e in edges if e.edge_type == "calls" and not e.is_resolved]
    assert non_id_edge_endpoints(symbols, misses) == []
    assert sorted(e.dst for e in misses) == sorted(
        external_target(lang, n)[0] for n in names
    )


def test_r_qualified_and_plain_calls_end_on_stand_ins(tmp_path: Path) -> None:
    symbols, edges = _run(analyze_r_files, tmp_path, {
        "app.R": "f <- function(x) {\n  y <- dplyr::filter(x)\n  paste(y)\n}\n",
    })
    calls = [e for e in edges if e.edge_type == "calls"]
    assert non_id_edge_endpoints(symbols, calls) == []
    by_dst = {e.dst: e for e in calls}
    qualified, ref = external_target("r", "filter", "dplyr")
    assert by_dst[qualified].dst_ref == ref
    assert external_target("r", "paste")[0] in by_dst


def test_puppet_require_keeps_the_reference_text(tmp_path: Path) -> None:
    symbols, edges = _run(analyze_puppet, tmp_path, {
        "site.pp": (
            "class web {\n  package { 'nginx': ensure => installed }\n"
            "  service { 'nginx':\n    ensure  => running,\n"
            "    require => Package['nginx'],\n  }\n}\n"
        ),
    })
    deps = [e for e in edges if e.edge_type == "depends_on"]
    assert non_id_edge_endpoints(symbols, deps) == []
    dst, ref = external_target("puppet", "Package['nginx']", "resource")
    assert [(e.dst, e.dst_ref) for e in deps] == [(dst, ref)]
