# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/cuda.py
"""A CUDA or HLSL member function's cv- and ref-qualifiers are identity
input (WI-pohom-turot part 1).

Until scheme v20 a CUDA member ``g() const`` and its ``g()`` twin, and
``h() &`` / ``h() &&``, tied before the occurrence split; in HLSL, which
emits no struct member but does emit its locals, the locals of ``g() const``
and ``g()`` tied (their scope label, ``c_family_callable_label``, was the
bare name). The analyzers run on each fixture; ties are counted on their
own output.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from hypergumbo_core.ir import Symbol
from hypergumbo_lang_common.cuda import analyze_cuda_files
from hypergumbo_lang_common.hlsl import analyze_hlsl


def _tied(symbols: list[Symbol]) -> list[tuple[str, int]]:
    counts = Counter(s.stable_id for s in symbols if s.kind != "file")
    return sorted((s.name, s.span.start_line) for s in symbols
                  if s.kind != "file" and counts[s.stable_id] > 1)


def test_cuda_qualified_twins_do_not_tie(tmp_path: Path) -> None:
    (tmp_path / "k.cu").write_text(
        "struct T {\n"
        "  __device__ int g() const { return 1; }\n"
        "  __device__ int g() { return 2; }\n"
        "  __device__ int h() && { return 3; }\n"
        "  __device__ int h() & { return 4; }\n"
        "};\n"
        "__device__ int free_fn() { return 5; }\n"
    )
    symbols = analyze_cuda_files(tmp_path).symbols
    assert Counter(s.name.split("::")[-1] for s in symbols if s.kind != "file") == {
        "g": 2, "h": 2, "free_fn": 1,
    }  # reach
    assert _tied(symbols) == []


def test_hlsl_locals_of_qualified_twins_do_not_tie(tmp_path: Path) -> None:
    (tmp_path / "s.hlsl").write_text(
        "struct S {\n"
        "  float v;\n"
        "  float g() const { float y; y = v; return y; }\n"
        "  float g() { float y; y = v; return y; }\n"
        "};\n"
    )
    symbols = analyze_hlsl(tmp_path).symbols
    assert [s.span.start_line for s in symbols if s.name == "y"] == [3, 4]  # reach
    assert _tied(symbols) == []


def test_an_unqualified_hlsl_function_keeps_its_value(tmp_path: Path) -> None:
    """The control: a function with no qualifier hashes what it hashed, with
    or without a qualified member beside it."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "s.hlsl").write_text("float f(float x) { return x; }\n")
    (tmp_path / "b" / "s.hlsl").write_text(
        "float f(float x) { return x; }\nstruct S { float g() const { float y; return y; } };\n"
    )
    (alone,) = [s for s in analyze_hlsl(tmp_path / "a").symbols if s.name == "f"]
    (beside,) = [s for s in analyze_hlsl(tmp_path / "b").symbols if s.name == "f"]
    assert alone.stable_id == beside.stable_id
