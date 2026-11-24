# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/cuda.py
"""CUDA, HLSL and GLSL overloads that differ only in a parameter's
declarator marks do not share a stable id (WI-pohom-turot part 2).

The three read their parameters through the shared C-family reader
(``hypergumbo_core.analyze.c_family.c_family_parameters``), whose type was
the text before the declarator until scheme v20, so ``f(float)`` /
``f(float*)`` (CUDA) and ``f(float a[2])`` / ``f(float a)`` (HLSL, GLSL)
tied before the occurrence split. Each analyzer runs on its fixture; ties
are counted on its own output.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from hypergumbo_core.ir import Symbol
from hypergumbo_lang_common.cuda import analyze_cuda_files
from hypergumbo_lang_common.glsl import analyze_glsl_files
from hypergumbo_lang_common.hlsl import analyze_hlsl


def _functions(symbols: list[Symbol], name: str) -> list[Symbol]:
    return [s for s in symbols if s.kind != "file" and s.name.split("::")[-1] == name]


def _tied(symbols: list[Symbol]) -> list[str]:
    counts = Counter(s.stable_id for s in symbols if s.kind != "file")
    return sorted(s.name for s in symbols if s.kind != "file" and counts[s.stable_id] > 1)


def test_cuda_pointer_and_reference_overloads(tmp_path: Path) -> None:
    (tmp_path / "k.cu").write_text(
        "__device__ float f(float a) { return a; }\n"
        "__device__ float f(float* a) { return *a; }\n"
        "__device__ float g(float& a) { return a; }\n"
        "__device__ float g(float&& a) { return a; }\n"
    )
    symbols = analyze_cuda_files(tmp_path).symbols
    assert len(_functions(symbols, "f")) == 2 and len(_functions(symbols, "g")) == 2
    assert _tied(symbols) == []


def test_hlsl_array_overloads_and_their_locals(tmp_path: Path) -> None:
    (tmp_path / "s.hlsl").write_text(
        "float f(float a[2]) { float y; y = a[0]; return y; }\n"
        "float f(float a) { float y; y = a; return y; }\n"
    )
    symbols = analyze_hlsl(tmp_path).symbols
    assert len(_functions(symbols, "f")) == 2 and len(_functions(symbols, "y")) == 2
    assert _tied(symbols) == []


def test_glsl_array_overloads(tmp_path: Path) -> None:
    (tmp_path / "s.frag").write_text(
        "float f(in float a[2]) { return a[0]; }\n"
        "float f(in float a) { return a; }\n"
    )
    symbols = analyze_glsl_files(tmp_path).symbols
    assert len(_functions(symbols, "f")) == 2
    assert _tied(symbols) == []


def test_a_plain_glsl_signature_keeps_its_id_beside_a_marked_one(tmp_path: Path) -> None:
    """The control: ``f(in float a)``'s id is the same with or without the
    array overload in the file."""
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    (tmp_path / "one" / "s.frag").write_text("float f(in float a) { return a; }\n")
    (tmp_path / "two" / "s.frag").write_text(
        "float f(in float a[2]) { return a[0]; }\nfloat f(in float a) { return a; }\n"
    )
    (alone,) = _functions(analyze_glsl_files(tmp_path / "one").symbols, "f")
    pair = _functions(analyze_glsl_files(tmp_path / "two").symbols, "f")
    assert alone.stable_id in {s.stable_id for s in pair}
