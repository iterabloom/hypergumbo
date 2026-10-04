# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-fohuh / WI-tikop, cuda: every definition shape that holds calls is a symbol,
calls are drawn from it by POSITION, and a call in a definition with no honest
name is drawn from the nearest enclosing record and says so.

``_get_function_name`` looked for a ``function_declarator`` as a DIRECT child of
the definition, so every function returning a pointer (``float *f()``) got no
symbol and every call in it was dropped; a member (``field_identifier``),
qualified (``A::run``), operator or template-specialisation leaf got none either.
The caller was found by NAME (``local_symbols``, one per name), so of two
same-named definitions every call was drawn from the last. The shared C-family
walk (``hypergumbo_core.analyze.c_family``) and the position index cure both.

Every test checks that no edge's src is unemitted (``unemitted_edge_sources``)
and asserts REACH (an edge at the line) before CONTAINMENT.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.edge_source import SRC_STANDS_IN_FOR, unemitted_edge_sources
from hypergumbo_lang_common.cuda import analyze_cuda_files


def _calls(result) -> dict[int, tuple[str, dict]]:
    """line -> (src name or "file", meta) for every call edge; asserts no src
    dangles and a symbol src's span holds the line."""
    assert unemitted_edge_sources(result.symbols, result.edges) == []
    by_id = {s.id: s for s in result.symbols}
    out: dict[int, tuple[str, dict]] = {}
    for e in result.edges:
        if e.edge_type != "calls":
            continue
        src = by_id.get(e.src)
        if src is None:
            out[e.line] = ("file", e.meta or {})
            continue
        assert src.span.start_line <= e.line <= src.span.end_line, (e.line, src.name)
        out[e.line] = (src.name, e.meta or {})
    return out


def test_a_pointer_returning_function_is_a_symbol_and_its_calls_are_drawn_from_it(
    tmp_path: Path,
) -> None:
    """WI-fohuh's filed repro, plus a function returning a function pointer."""
    (tmp_path / "k.cu").write_text("""\
__device__ int helper(int x) { return x; }
__device__ int plain(int n) { return helper(n); }
__device__ float *one_ptr(float *p) { helper(1); return p; }
__device__ float **two_ptr(float **p) { helper(2); return p; }
int (*getfn(void))(int) { helper(3); return 0; }
""")
    result = analyze_cuda_files(tmp_path)
    sigs = {s.name: s.signature for s in result.symbols}
    assert set(sigs) == {"helper", "plain", "one_ptr", "two_ptr", "getfn"}
    assert sigs["two_ptr"] == "(float **p) float**"
    assert sigs["getfn"] == "(void)"
    calls = _calls(result)
    assert {line: name for line, (name, _) in calls.items()} == {
        2: "plain", 3: "one_ptr", 4: "two_ptr", 5: "getfn",
    }


def test_every_cpp_leaf_names_its_definition(tmp_path: Path) -> None:
    """A functor's ``operator()``, a qualified out-of-line method, a member
    defined in its struct, a template kernel and an explicit specialisation."""
    (tmp_path / "k.cu").write_text("""\
__device__ float helper(float x) { return x; }
struct Op { __device__ float operator()(float x) const { return helper(x); } };
void A::run() { helper(4); }
struct B { __device__ void step() { helper(5); } };
template <typename T> __global__ void kern(T *x) { helper(6); }
template <> __global__ void kern<int>(int *x) { helper(7); }
""")
    result = analyze_cuda_files(tmp_path)
    calls = _calls(result)
    assert {line: name for line, (name, _) in calls.items()} == {
        2: "operator()", 3: "A::run", 4: "step", 5: "kern", 6: "kern",
    }


def test_same_named_definitions_are_told_apart_by_position(tmp_path: Path) -> None:
    """Two ``#ifdef`` alternatives of one name: each call is drawn from the
    definition that holds it (the name lookup drew both from the last)."""
    (tmp_path / "k.cu").write_text("""\
__device__ int one(int x) { return x; }
__device__ int two(int x) { return x; }
#ifdef FAST
__device__ int run(int x) {
  return one(x);
}
#else
__device__ int run(int x) {
  return two(x);
}
#endif
""")
    result = analyze_cuda_files(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    anchors = {
        e.line: by_id[e.src].span.start_line
        for e in result.edges if e.edge_type == "calls"
    }
    assert anchors == {5: 4, 9: 8}


def test_a_macro_named_definition_stands_in_on_the_nearest_record(tmp_path: Path) -> None:
    """WI-tikop's rule in cuda: ``PFX(f)(args)`` mints no symbol called ``PFX``.
    Its call is drawn from the file (top level) or the named definition around it,
    and the edge says it stands in. A call in no definition stays unemitted."""
    (tmp_path / "k.cu").write_text("""\
__device__ int helper(int x) { return x; }
int PFX(cpu_test)(void) { return helper(6); }
__device__ int outer(void) {
  int MAC(inner)(void) { return helper(7); }
  return helper(8);
}
int g = helper(9);
""")
    result = analyze_cuda_files(tmp_path)
    assert not {s.name for s in result.symbols} & {"PFX", "MAC", "cpu_test", "inner"}
    calls = _calls(result)
    assert calls[2] == ("file", {SRC_STANDS_IN_FOR: "unnamed_definition"})
    assert calls[4] == ("outer", {SRC_STANDS_IN_FOR: "unnamed_definition"})
    assert calls[5][0] == "outer" and SRC_STANDS_IN_FOR not in calls[5][1]
    assert 7 not in calls
