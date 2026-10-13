# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer stable_ids for the typed-overload languages Julia, CUDA, GLSL, Fortran (WI-tusad).

None of these analyzers computed a ``stable_id`` for its callables, so they
rode the ADR-0035 backstop floor key ``(kind, language, path, name)``. Their
languages overload a name in one file -- Julia's multiple dispatch
(``f(x::Int)`` / ``f(x::String)``), CUDA's C++ overloads and GLSL's
(``float f(float)`` / ``vec2 f(vec2)``), a Fortran ``run`` in two modules --
and two same-name callables share that key, so the floor left both null.
Each now records its parameters (types included) in ``meta["parameters"]`` and
mints the producer formula through ``overload_arity.declared_arity_stable_id``
(typed tier as soon as a parameter declares a type), with its scope chain
(Julia modules, CUDA namespaces/classes, Fortran units) as the qualified name.

What is pinned, per language, on the analyzer's own output:

* every same-name callable gets its own non-null id (checked symbol by symbol,
  never through a dict keyed on name);
* the exact value (the shared helper over the inputs the module docstring
  names), so a producer that drifted onto other inputs fails here;
* the delete-an-earlier-sibling property: deleting an earlier same-name
  callable leaves every survivor's id unchanged (the deleted lines are kept
  as blank lines so each survivor matches itself by line);
* the non-callable kinds migrated with them (Julia abstract/const, GLSL
  uniforms and in/out, Fortran programs and components).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, declared_arity, declared_arity_stable_id, parameter_entry,
)
from hypergumbo_lang_common.cuda import analyze_cuda_files
from hypergumbo_lang_common.fortran import analyze_fortran_files
from hypergumbo_lang_common.glsl import analyze_glsl_files
from hypergumbo_lang_common.julia import analyze_julia

Analyze = Callable[[Path], AnalysisResult]


def _symbols(analyze: Analyze, root: Path, name: str, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    return list(analyze(root).symbols)


def _by_line(symbols: list[Symbol], kinds: set[str]) -> dict[int, Symbol]:
    picked = [s for s in symbols if s.kind in kinds and s.span is not None]
    by_line = {s.span.start_line: s for s in picked}  # type: ignore[union-attr]
    assert len(by_line) == len(picked), "two symbols on one line"
    return by_line


def _assert_deletion_moves_nothing(
    analyze: Analyze, tmp_path: Path, name: str, text: str, deleted: str,
    kinds: set[str], survivors: int,
) -> None:
    """Delete ``deleted`` (replaced by as many blank lines) and compare."""
    assert deleted in text
    edited = text.replace(deleted, "\n" * deleted.count("\n"))
    before = _by_line(_symbols(analyze, tmp_path / "r1", name, text), kinds)
    after = _by_line(_symbols(analyze, tmp_path / "r2", name, edited), kinds)
    assert len(after) == survivors < len(before)  # reach
    for line, sym in after.items():
        assert sym.stable_id is not None, (line, sym.name)
        assert sym.stable_id == before[line].stable_id, (line, sym.name)


def _expected(
    kind: str, lang: str, path: str, name: str, qualified_name: str,
    meta: Optional[dict[str, Any]] = None,
) -> str:
    return declared_arity_stable_id(
        kind=kind, lang=lang, path=path, name=name,
        qualified_name=qualified_name, arity=declared_arity(meta),
    )


def _named(symbols: list[Symbol], kind: str, name: str) -> list[Symbol]:
    return [s for s in symbols if s.kind == kind and s.name == name]


# -- Julia ----------------------------------------------------------------

_JULIA = (
    "module M\n"
    "f(x::Int) = x\n"
    "f(x::String, y=2; z::Int=3) = x\n"
    "function g(a::Vector{T}, b, c::Int...; k=1) where T\n"
    "    a\n"
    "end\n"
    "h(::Int, (p, q), xs...) = 1\n"
    "f(x::T) where {T<:Real} = x\n"
    "k(x)::Int = 1\n"
    "function Base.show(io::IO, x::Int)\n"
    "end\n"
    "function stub end\n"
    "macro m(ex, y)\n"
    "    ex\n"
    "end\n"
    "abstract type Shape end\n"
    "const C = 1\n"
    "end\n"
)


def test_julia_methods_of_one_name_get_distinct_typed_ids(tmp_path: Path) -> None:
    syms = _symbols(analyze_julia, tmp_path, "a.jl", _JULIA)
    fs = _named(syms, "function", "f")
    # f(x::Int), f(x::String, y=2), and the parametric f(x::T) where T
    assert [s.span.start_line for s in fs] == [2, 3, 8]  # type: ignore[union-attr]
    for s in fs:
        assert s.stable_id is not None
        assert s.stable_id == _expected(
            "function", "julia", "a.jl", "f", "M.f", s.meta,
        )
    assert len({s.stable_id for s in fs}) == 3
    # positional parameters only: z after ``;`` takes no part in dispatch
    assert fs[1].meta == {PARAMETERS_KEY: [
        parameter_entry("x", "String"), parameter_entry("y", default=True),
    ]}


def test_julia_parameter_shapes(tmp_path: Path) -> None:
    syms = _symbols(analyze_julia, tmp_path, "a.jl", _JULIA)
    params = {s.name: (s.meta or {}).get(PARAMETERS_KEY) for s in syms
              if s.kind in ("function", "macro") and s.name != "f"}
    assert params == {
        "g": [
            parameter_entry("a", "Vector{T}"), parameter_entry("b"),
            parameter_entry("c", "Int", variadic=True),
        ],
        "h": [
            parameter_entry("", "Int"), parameter_entry("(p, q)"),
            parameter_entry("xs", variadic=True),
        ],
        "k": [parameter_entry("x")],
        "m": [parameter_entry("ex"), parameter_entry("y")],
        "Base.show": [parameter_entry("io", "IO"), parameter_entry("x", "Int")],
        "stub": None,
    }


def test_julia_where_forms_base_extensions_and_generics_are_emitted(tmp_path: Path) -> None:
    """A ``where`` clause no longer hides a method; nor (B8a) does a field-
    expression name, ``Base.show`` (a method of another module's function),
    nor the zero-method ``function stub end``, which declares the generic and
    gives it no method (no parameters are recorded for it)."""
    syms = _symbols(analyze_julia, tmp_path, "a.jl", _JULIA)
    names = sorted(s.name for s in syms if s.kind == "function")
    assert names == ["Base.show", "f", "f", "f", "g", "h", "k", "stub"]
    (g,) = _named(syms, "function", "g")
    assert g.signature == "(a::Vector{T}, b)"
    (k,) = _named(syms, "function", "k")
    assert k.signature == "(x)"


def test_julia_macro_abstract_and_const_ids(tmp_path: Path) -> None:
    syms = _symbols(analyze_julia, tmp_path, "a.jl", _JULIA)
    (m,) = _named(syms, "macro", "m")
    assert m.stable_id == _expected("macro", "julia", "a.jl", "m", "M.m", m.meta)
    (shape,) = _named(syms, "abstract", "Shape")
    assert shape.stable_id == _expected("abstract", "julia", "a.jl", "Shape", "M.Shape")
    (c,) = _named(syms, "const", "C")
    assert c.stable_id == _expected("const", "julia", "a.jl", "C", "M.C")


def test_julia_deleting_an_earlier_method_moves_nothing(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_julia, tmp_path, "a.jl", _JULIA, "f(x::Int) = x\n",
        {"function", "macro", "abstract", "const"}, survivors=10,
    )


# -- CUDA -----------------------------------------------------------------

_CUDA = (
    "__device__ int f(int x) { return x; }\n"
    "__device__ float f(float x, ...) { return x; }\n"
    "namespace ns {\n"
    "struct A { __device__ int size(int x) { return x; } };\n"
    "struct B { __device__ int size(int x) { return x; } };\n"
    "}\n"
    "__global__ void kern(int *a) { a[0] = f(1); }\n"
)


def test_cuda_overloads_and_members_get_distinct_typed_ids(tmp_path: Path) -> None:
    syms = _symbols(analyze_cuda_files, tmp_path, "a.cu", _CUDA)
    fs = _named(syms, "function", "f")
    assert [s.meta[PARAMETERS_KEY] for s in fs if s.meta] == [  # type: ignore[index]
        [parameter_entry("x", "int")],
        [parameter_entry("x", "float"), parameter_entry("...", variadic=True)],
    ]
    for s in fs:
        assert s.stable_id == _expected("function", "cuda", "a.cu", "f", "f", s.meta)
    sizes = _named(syms, "function", "size")
    assert [s.stable_id for s in sizes] == [
        _expected("function", "cuda", "a.cu", "size", "ns::A::size", sizes[0].meta),
        _expected("function", "cuda", "a.cu", "size", "ns::B::size", sizes[1].meta),
    ]
    assert len({s.stable_id for s in fs + sizes}) == 4
    # the execution space still rides beside the parameters
    (kern,) = _named(syms, "function", "kern")
    assert kern.meta is not None and kern.meta["cuda_execution_space"] == "global"


def test_cuda_deleting_an_earlier_overload_moves_nothing(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_cuda_files, tmp_path, "a.cu", _CUDA,
        "__device__ int f(int x) { return x; }\n", {"function"}, survivors=4,
    )


# -- GLSL -----------------------------------------------------------------

_GLSL = (
    "#version 330\n"
    "uniform sampler2D tex;\n"
    "in vec2 uv;\n"
    "out vec4 color;\n"
    "float f(float x) { return x; }\n"
    "vec2 f(in vec2 x) { return x; }\n"
    "void main() { color = vec4(f(1.0)); }\n"
)


def test_glsl_overloads_get_distinct_typed_ids(tmp_path: Path) -> None:
    syms = _symbols(analyze_glsl_files, tmp_path, "a.frag", _GLSL)
    fs = _named(syms, "function", "f")
    assert [(s.meta or {}).get(PARAMETERS_KEY) for s in fs] == [
        [parameter_entry("x", "float")], [parameter_entry("x", "in vec2")],
    ]
    for s in fs:
        assert s.stable_id == _expected("function", "glsl", "a.frag", "f", "f", s.meta)
    assert len({s.stable_id for s in fs}) == 2
    (main,) = _named(syms, "function", "main")
    assert main.meta == {PARAMETERS_KEY: []}


def test_glsl_shader_globals_get_producer_ids(tmp_path: Path) -> None:
    syms = _symbols(analyze_glsl_files, tmp_path, "a.frag", _GLSL)
    got = {(s.kind, s.name): s.stable_id for s in syms if s.kind != "function"}
    assert got == {
        (kind, name): _expected(kind, "glsl", "a.frag", name, name)
        for kind, name in (("uniform", "tex"), ("input", "uv"), ("output", "color"))
    }


def test_glsl_deleting_an_earlier_overload_moves_nothing(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_glsl_files, tmp_path, "a.frag", _GLSL,
        "float f(float x) { return x; }\n",
        {"function", "uniform", "input", "output"}, survivors=5,
    )


# -- Fortran --------------------------------------------------------------

_FORTRAN = (
    "module a\n"
    "  type :: point\n"
    "    real :: x\n"
    "  end type point\n"
    "contains\n"
    "  integer function run(x, y)\n"
    "    integer, intent(in) :: x\n"
    "    real, optional :: y\n"
    "    run = x\n"
    "  end function run\n"
    "end module a\n"
    "module b\n"
    "contains\n"
    "  subroutine run(q, n)\n"
    "    TYPE(Point) :: q\n"
    "  contains\n"
    "    subroutine inner()\n"
    "    end subroutine inner\n"
    "  end subroutine run\n"
    "end module b\n"
    "program p\n"
    "end program p\n"
)


def test_fortran_same_name_procedures_in_two_modules_get_distinct_ids(tmp_path: Path) -> None:
    syms = _symbols(analyze_fortran_files, tmp_path, "a.f90", _FORTRAN)
    (fn,) = _named(syms, "function", "run")
    (sub,) = _named(syms, "subroutine", "run")
    assert fn.meta == {PARAMETERS_KEY: [
        parameter_entry("x", "integer"), parameter_entry("y", "real", default=True),
    ]}
    # an undeclared dummy (implicit typing) has no type; case is folded
    assert sub.meta == {PARAMETERS_KEY: [
        parameter_entry("q", "type(point)"), parameter_entry("n"),
    ]}
    assert fn.stable_id == _expected("function", "fortran", "a.f90", "run", "a.run", fn.meta)
    assert sub.stable_id == _expected(
        "subroutine", "fortran", "a.f90", "run", "b.run", sub.meta,
    )
    (inner,) = _named(syms, "subroutine", "inner")
    assert inner.stable_id == _expected(
        "subroutine", "fortran", "a.f90", "inner", "b.run.inner", inner.meta,
    )


def test_fortran_program_and_component_ids(tmp_path: Path) -> None:
    syms = _symbols(analyze_fortran_files, tmp_path, "a.f90", _FORTRAN)
    (prog,) = _named(syms, "program", "p")
    assert prog.stable_id == _expected("program", "fortran", "a.f90", "p", "p")
    (comp,) = _named(syms, "field", "point.x")
    assert comp.stable_id == _expected("field", "fortran", "a.f90", "point.x", "a.point.x")


def test_fortran_deleting_an_earlier_procedure_moves_nothing(tmp_path: Path) -> None:
    deleted = (
        "  integer function run(x, y)\n"
        "    integer, intent(in) :: x\n"
        "    real, optional :: y\n"
        "    run = x\n"
        "  end function run\n"
    )
    _assert_deletion_moves_nothing(
        analyze_fortran_files, tmp_path, "a.f90", _FORTRAN, deleted,
        {"function", "subroutine", "program", "field"}, survivors=4,
    )


# -- HLSL (scheme bump: these ids were producer-computed with param_count 0) --

_HLSL = (
    "float f(float x) { return x; }\n"
    "float2 f(float2 x : TEXCOORD0, uniform float y = 1.0) { return x; }\n"
    "float4 main() : SV_Target { return 0; }\n"
)


def test_hlsl_overloads_get_distinct_typed_ids(tmp_path: Path) -> None:
    from hypergumbo_lang_common.hlsl import analyze_hlsl

    syms = _symbols(analyze_hlsl, tmp_path, "a.hlsl", _HLSL)
    fs = _named(syms, "function", "f")
    assert [(s.meta or {}).get(PARAMETERS_KEY) for s in fs] == [
        [parameter_entry("x", "float")],
        [parameter_entry("x", "float2"), parameter_entry("y", "uniform float", default=True)],
    ]
    assert len({s.stable_id for s in fs}) == 2 and None not in {s.stable_id for s in fs}
    (main,) = _named(syms, "function", "main")
    assert main.meta == {PARAMETERS_KEY: []}


def test_hlsl_deleting_an_earlier_overload_moves_nothing(tmp_path: Path) -> None:
    from hypergumbo_lang_common.hlsl import analyze_hlsl

    _assert_deletion_moves_nothing(
        analyze_hlsl, tmp_path, "a.hlsl", _HLSL,
        "float f(float x) { return x; }\n", {"function"}, survivors=2,
    )
