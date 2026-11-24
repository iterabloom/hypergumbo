# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/cuda.py
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/hlsl.py
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/glsl.py
"""CUDA, HLSL and GLSL run the C preprocessor, and their stable ids carry the
preprocessor branches a declaration sits in, as C and C++ do (WI-sifar,
``c_family_open_guards``).

Two definitions of one name under ``#ifdef FAST`` / ``#else`` tied until
scheme v19 and were told apart only by occurrence order. Each language is
checked on the real analyzer: the alternatives get distinct ids; an unguarded
twin hashes what it hashes in a file with no directive; a whole-file include
guard moves no id, whatever its macro is called, while the same ``#ifndef``
that does not wrap the whole file is a branch. WGSL has no preprocessor and is
checked to need nothing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, NamedTuple

import pytest

from hypergumbo_core.analyze.base import populate_kind_stable_ids
from hypergumbo_lang_common.cuda import analyze_cuda_files
from hypergumbo_lang_common.glsl import analyze_glsl_files
from hypergumbo_lang_common.hlsl import analyze_hlsl
from hypergumbo_lang_common.wgsl import analyze_wgsl_files


class Lang(NamedTuple):
    analyze: Callable[[Path], object]
    source: str  # a file the analyzer reads
    header: str  # an included file the analyzer reads
    one: str  # a declaration named ``f``
    two: str  # another declaration named ``f`` (a different body)


LANGS = {
    "cuda": Lang(analyze_cuda_files, "k.cu", "k.cuh",
                 "__global__ void f(float *x) { x[0] = 1; }\n",
                 "__global__ void f(float *x) { x[0] = 2; }\n"),
    "hlsl": Lang(analyze_hlsl, "s.hlsl", "s.hlsli",
                 "float4 f(float4 c) { return c; }\n",
                 "float4 f(float4 c) { return -c; }\n"),
    "glsl": Lang(analyze_glsl_files, "s.frag", "s.glsl",
                 "float f(float c) { return c; }\n",
                 "float f(float c) { return -c; }\n"),
    "glsl-uniform": Lang(analyze_glsl_files, "u.frag", "u.glsl",
                         "uniform float f;\n", "uniform vec2 f;\n"),
    "glsl-struct": Lang(analyze_glsl_files, "t.frag", "t.glsl",
                        "struct f { float a; };\n", "struct f { vec2 a; };\n"),
}


def _ids(lang: Lang, root: Path, rel: str, text: str) -> list[tuple[str, int, str | None]]:
    root.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)
    result = lang.analyze(root)
    symbols = [
        s for s in result.symbols  # type: ignore[attr-defined]
        if s.kind != "file" and s.name == "f"
    ]
    populate_kind_stable_ids(symbols)  # the run's backstop, for a table kind
    return sorted((s.name, s.span.start_line, s.stable_id) for s in symbols)


@pytest.mark.parametrize("name", sorted(LANGS))
def test_alternatives_get_distinct_ids(tmp_path: Path, name: str) -> None:
    lang = LANGS[name]
    found = _ids(lang, tmp_path, lang.source, f"#ifdef FAST\n{lang.one}#else\n{lang.two}#endif\n")
    assert len(found) == 2  # reach: both alternatives are emitted
    assert None not in [sid for _n, _l, sid in found]
    assert found[0][2] != found[1][2]


@pytest.mark.parametrize("name", sorted(LANGS))
def test_unguarded_twin_keeps_its_id(tmp_path: Path, name: str) -> None:
    """A declaration outside every branch hashes what it hashes with no
    directive in the file, beside a guarded twin."""
    lang = LANGS[name]
    plain = _ids(lang, tmp_path / "p", lang.source, f"\n\n\n{lang.two}")
    twin = _ids(lang, tmp_path / "t", lang.source, f"#if 0\n{lang.one}#endif\n{lang.two}")
    assert len(plain) == 1 and len(twin) == 2  # reach
    assert twin[1][2] == plain[0][2]
    assert twin[0][2] != plain[0][2]


@pytest.mark.parametrize("name", sorted(LANGS))
def test_include_guard_moves_no_id(tmp_path: Path, name: str) -> None:
    lang = LANGS[name]
    guarded = _ids(lang, tmp_path / "g", lang.header, f"#ifndef FOO_H\n#define FOO_H\n{lang.one}#endif\n")
    renamed = _ids(lang, tmp_path / "r", lang.header, f"#ifndef BAR_H\n#define BAR_H\n{lang.one}#endif\n")
    bare = _ids(lang, tmp_path / "b", lang.header, f"\n\n{lang.one}\n")
    assert len(guarded) == 1  # reach
    assert guarded == renamed == bare
    partial = _ids(lang, tmp_path / "x", lang.header,
                   f"#ifndef FOO_H\n#define FOO_H\n{lang.one}#endif\n{lang.two}")
    assert partial[0][2] != guarded[0][2]


def test_wgsl_has_no_preprocessor(tmp_path: Path) -> None:
    """WGSL has no ``#if``: a ``#``-line is not WGSL, and two same-named
    functions in one module are a WGSL error, so there is no alternative to
    tell apart. Its ids are the ones it had."""
    (tmp_path / "a.wgsl").write_text("fn f(x: f32) -> f32 { return x; }\n")
    result = analyze_wgsl_files(tmp_path)
    found = [s for s in result.symbols if s.name == "f"]
    assert len(found) == 1 and found[0].stable_id is not None


def test_hlsl_locals_of_two_overloads_get_distinct_ids(tmp_path: Path) -> None:
    """WI-finam: an HLSL local is scoped by its function's name and parameter
    types, so the ``y`` of ``f(float)`` and of ``f(float2)`` do not tie; a
    function with no parameter keeps its bare name in the chain."""
    def ys(rel: str, text: str) -> list[str | None]:
        (tmp_path / rel).write_text(text)
        return [s.stable_id for s in analyze_hlsl(tmp_path).symbols
                if s.name == "y" and s.path.endswith(rel)]

    two = ys("a.hlsl", "float f(float x) { float y; y = x; return y; }\n"
                       "float f(float2 x) { float y; y = 1; return y; }\n")
    assert len(two) == 2  # reach: both locals are emitted
    assert two[0] != two[1]
    bare = ys("b.hlsl", "float g() { float y; y = 1; return y; }\n")
    assert len(bare) == 1 and bare[0] is not None
