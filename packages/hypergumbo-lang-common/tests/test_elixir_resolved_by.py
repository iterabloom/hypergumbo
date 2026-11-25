# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on Elixir's call edges (audit 0030, WI-hirol).

One fixture per route:

* a bare call (or a pipe) found in the caller's FILE's table is
  ``same_file``: a file may define several modules, and the table holds
  them all, so the lookup was not restricted to the caller's module;
* a bare call (or a pipe) found among the functions of the modules the file
  ``import``s is ``import``; with no import list (a direct call of the
  extractor) the same lookup ran over the whole run and is ``name``;
* ``Util.util_fn()`` found under the module the call site writes is
  ``qualified``; ``Svc.call()`` whose ``Svc`` an ``alias`` directive expands
  is ``import``.
"""
from pathlib import Path

from hypergumbo_core.ir import make_pass_id
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_lang_common.elixir import (
    _analyzer,
    _extract_edges_from_tree,
    _extract_symbols_from_tree,
    analyze_elixir,
)


def _edge(result_edges, src: str, dst: str):
    found = [
        e for e in result_edges
        if e.edge_type == "calls" and f":{src}:" in e.src and f":{dst}:" in e.dst
    ]
    assert len(found) == 1, f"calls {src} -> {dst}: {len(found)} edge(s)"
    return found[0]


def test_elixir_routes(tmp_path: Path) -> None:
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "util.ex").write_text("defmodule Util do\n  def util_fn(x), do: x\nend\n")
    (lib / "svc.ex").write_text("defmodule MyApp.Svc do\n  def call(x), do: x\nend\n")
    (lib / "a.ex").write_text(
        "defmodule A do\n"
        "  import Util\n"
        "  alias MyApp.Svc\n\n"
        "  def run(x), do: helper(x)\n"
        "  def helper(x), do: x\n"
        "  def go(x), do: util_fn(x)\n"
        "  def p(x), do: x |> helper\n"
        "  def q(x), do: x |> util_fn\n"
        "  def r(x), do: Util.util_fn(x)\n"
        "  def s(x), do: Svc.call(x)\n"
        "end\n"
    )
    edges = analyze_elixir(tmp_path).edges
    assert _edge(edges, "A.run", "A.helper").meta["resolved_by"] == "same_file"
    assert _edge(edges, "A.go", "Util.util_fn").meta["resolved_by"] == "import"
    assert _edge(edges, "A.p", "A.helper").meta["resolved_by"] == "same_file"
    assert _edge(edges, "A.q", "Util.util_fn").meta["resolved_by"] == "import"
    assert _edge(edges, "A.r", "Util.util_fn").meta["resolved_by"] == "qualified"
    assert _edge(edges, "A.s", "MyApp.Svc.call").meta["resolved_by"] == "import"


def test_no_import_list_is_a_name_lookup() -> None:
    """The extractor called without ``imported_modules``: nothing gated the
    run-wide multi-arity table, so the call and the pipe are ``name``."""
    util_src = b"defmodule Util do\n  def util_fn(x), do: x\nend\n"
    a_src = b"defmodule A do\n  def go(x), do: util_fn(x)\n  def q(x), do: x |> util_fn\nend\n"
    parser = _analyzer._create_parser()
    run_id = make_pass_id("elixir-test")
    util_syms, _, _ = _extract_symbols_from_tree(parser.parse(util_src), util_src, "util.ex", run_id)
    a_tree = parser.parse(a_src)
    a_syms, a_by_name, _ = _extract_symbols_from_tree(a_tree, a_src, "a.ex", run_id)
    multi: dict = {}
    for sym in [*util_syms, *a_syms]:
        if sym.kind == "function":
            multi.setdefault(sym.name.rsplit(".", 1)[-1], []).append(sym)
    local_multi = {
        name: [s for s in syms if s.path == "a.ex"]
        for name, syms in multi.items() if any(s.path == "a.ex" for s in syms)
    }
    edges = _extract_edges_from_tree(
        a_tree, a_src, "a.ex", a_by_name, {}, run_id,
        NameResolver({s.name: s for s in [*util_syms, *a_syms]}),
        local_symbols_multi=local_multi, global_symbols_multi=multi,
        file_symbols=a_syms,
    )
    assert _edge(edges, "A.go", "Util.util_fn").meta["resolved_by"] == "name"
    assert _edge(edges, "A.q", "Util.util_fn").meta["resolved_by"] == "name"
