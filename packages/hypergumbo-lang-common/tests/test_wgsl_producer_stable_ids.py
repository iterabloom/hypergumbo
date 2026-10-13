# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer stable_ids for WGSL functions and buffer bindings (WI-tusad).

``wgsl.py`` minted an id only for an entry point (``@vertex`` / ``@fragment`` /
``@compute``: ``make_entry_stable_id``). Every other function, and every
``var<uniform>`` / ``var<storage>`` binding, rode the ADR-0035 backstop floor
key ``(kind, language, path, name)``. WGSL has no overloading, but shader
sources in the wild are templates: whisper.cpp's ``ggml-webgpu`` shaders define
``store_scale`` once under ``#ifdef INPLACE`` and again under ``#else``, or
``rotate`` in two ``#decl(...)`` blocks. tree-sitter recovers past the
directive lines and emits both definitions, which shared the floor key, so
both were null (56 of the 75 functions of a 22-file sample).

Each symbol now mints the ADR-0035 formula with the template branch it sits in
as its scope: a function through ``compute_stable_id`` (its parameter count;
the ``parameter_list`` is a direct child), a binding through
``overload_arity.declared_arity_stable_id`` with no arity. Entry points keep
their existing id.

What is pinned, on the analyzer's own output: every symbol holds its own
non-null id (matched by kind and position, never through a dict keyed on
name); the exact value; and the delete-an-earlier-sibling property (the deleted
lines are kept as blank lines so each survivor matches itself by line).
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import floor_stable_id_key, make_entry_stable_id
from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import (
    declared_arity_of_entries, declared_arity_stable_id, parameter_entry,
)
from hypergumbo_lang_common.wgsl import _template_scopes, analyze_wgsl_files

PATH = "src/demo.wgsl"
KINDS = {"function", "uniform", "storage"}

_WGSL = (
    "#ifdef INPLACE\n"                                  # 1
    "@group(0) @binding(1)\n"
    "var<uniform> params: Params;\n"
    "fn store(val: f32) {\n"
    "    src[0] = val;\n"                               # 5
    "}\n"
    "#else\n"
    "@group(0) @binding(1)\n"
    "var<storage, read_write> dst: array<f32>;\n"
    "@group(0) @binding(2)\n"                           # 10
    "var<uniform> params: Params;\n"
    "fn store(val: f32) {\n"
    "    dst[0] = val;\n"
    "}\n"
    "#endif\n"                                          # 15
    "#decl(ROTATE)\n"
    "fn rotate(a: u32, b: u32) { }\n"
    "#enddecl(ROTATE)\n"
    "#decl(ROTATE_INPLACE)\n"
    "fn rotate(a: u32, b: u32) { }\n"                   # 20
    "#enddecl(ROTATE_INPLACE)\n"
    "fn helper(x: f32) -> f32 { return x; }\n"
    "@compute @workgroup_size(1)\n"
    "fn main() { }\n"
)


def _symbols(root: Path, text: str) -> dict[tuple[str, int, int], Symbol]:
    (root / PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / PATH).write_text(text)
    picked = [s for s in analyze_wgsl_files(root).symbols if s.kind in KINDS]
    by_pos = {(s.kind, s.span.start_line, s.span.start_col): s for s in picked}
    assert len(by_pos) == len(picked), "two symbols of one kind at one position"
    return by_pos


def _fn(name: str, chain: str, params: list[str]) -> str:
    """``compute_stable_id``'s untyped tier equals the helper's for the same
    untyped parameter count."""
    return declared_arity_stable_id(
        kind="function", lang="wgsl", path=PATH, name=name, qualified_name=chain,
        arity=declared_arity_of_entries([parameter_entry(p) for p in params]),
    )


def _var(kind: str, name: str, chain: str) -> str:
    return declared_arity_stable_id(
        kind=kind, lang="wgsl", path=PATH, name=name, qualified_name=chain,
        arity=None,
    )


def test_template_branches_scope_same_name_definitions(tmp_path: Path) -> None:
    by_pos = _symbols(tmp_path, _WGSL)
    branch = "#ifdef INPLACE #else"
    expected = {
        ("uniform", 2, 0): _var("uniform", "params", "#ifdef INPLACE/params"),
        ("function", 4, 0): _fn("store", "#ifdef INPLACE/store", ["val"]),
        ("storage", 8, 0): _var("storage", "dst", f"{branch}/dst"),
        ("uniform", 10, 0): _var("uniform", "params", f"{branch}/params"),
        ("function", 12, 0): _fn("store", f"{branch}/store", ["val"]),
        ("function", 17, 0): _fn("rotate", "#decl(ROTATE)/rotate", ["a", "b"]),
        ("function", 20, 0): _fn("rotate", "#decl(ROTATE_INPLACE)/rotate", ["a", "b"]),
        ("function", 22, 0): _fn("helper", "helper", ["x"]),
        ("function", 23, 0): make_entry_stable_id("compute", "main", PATH),
    }
    assert set(by_pos) == set(expected)
    for key, value in expected.items():
        sym = by_pos[key]
        assert sym.stable_id == value, key
        assert sym.stable_id != floor_stable_id_key(sym.kind, "wgsl", PATH, sym.name)
    assert len({s.stable_id for s in by_pos.values()}) == len(by_pos)


def test_deleting_an_earlier_branch_moves_no_id(tmp_path: Path) -> None:
    for deleted, survivors in (
        ("@group(0) @binding(1)\nvar<uniform> params: Params;\n"
         "fn store(val: f32) {\n    src[0] = val;\n}\n", 7),
        ("#decl(ROTATE)\nfn rotate(a: u32, b: u32) { }\n#enddecl(ROTATE)\n", 8),
    ):
        assert _WGSL.count(deleted) == 1
        edited = _WGSL.replace(deleted, "\n" * deleted.count("\n"))
        before = _symbols(tmp_path / "r1", _WGSL)
        after = _symbols(tmp_path / "r2", edited)
        assert len(after) == survivors < len(before)
        for key, sym in after.items():
            assert sym.stable_id is not None, key
            assert sym.stable_id == before[key].stable_id, key


def test_template_scopes_track_open_alternative_and_close() -> None:
    src = (
        b"#if A\n"           # 1
        b"x\n"
        b"#elif B\n"
        b"#define(SHADER)\n"
        b"#define N 4\n"     # 5: a C-style macro opens nothing
        b"#end(SHADER)\n"
        b"#endif\n"
        b"#endif\n"          # 8: an unbalanced close is ignored
        b"  # else\n"        # 9: so is an alternative with nothing open
    )
    assert _template_scopes(src) == [
        ("#if A",), ("#if A",), ("#if A #elif B",),
        ("#if A #elif B", "#define(SHADER)"), ("#if A #elif B", "#define(SHADER)"),
        ("#if A #elif B",), (), (), (), (),
    ]
