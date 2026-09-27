# SPDX-License-Identifier: AGPL-3.0-or-later
"""An Elixir call outside a def emits an edge, anchored on its module.

WI-dokib (INV-bamij's elixir twin). ``_handle_dot_call`` and the bare-call
branch began by resolving the enclosing ``def`` and returned when there was
none, so a call in a MODULE BODY (``for path <- :code.get_path()`` in
mix.exs) or in a ``test`` / ``setup`` MACRO BLOCK (``:ssl.listen`` inside
``test "..." do``) emitted nothing: 101 of 225 atom call sites on phoenix. The
alias path lost exactly the same sites. It is now anchored on the enclosing
``defmodule``'s symbol, else the file.

WHAT MUST STAY SILENT, per the item: a typespec. ``@spec``, ``@type``,
``@callback`` and friends are module-level too, and ``:inet.ip_address()`` or
``:supervisor.child_spec()`` inside one is a TYPE, not a call. The naive "emit
outside a def" would mint call edges for every typespec, which is worse than
the gap; a fixture pins that it does not.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_common.elixir import analyze_elixir

_SOURCE = """defmodule MyApp.Cache do
  @table :ets.new(:cache, [:set])
  @callback child_spec(keyword) :: :supervisor.child_spec()
  @spec addr() :: :inet.ip_address()
  Logger.info("loading")

  def put(k, v) do
    :ets.insert(:cache, {k, v})
  end
end

defmodule MyApp.CacheTest do
  use ExUnit.Case
  test "roundtrip" do
    :ets.insert(:cache, {:a, 1})
    Logger.error("x")
  end
end
"""


def _calls(tmp_path: Path) -> dict[int, list[tuple[str, str]]]:
    (tmp_path / "cache.ex").write_text(_SOURCE)
    analysis = analyze_elixir(tmp_path)
    kinds = {s.id: s.kind for s in analysis.symbols}
    out: dict[int, list[tuple[str, str]]] = {}
    for e in analysis.edges:
        if e.edge_type == "calls" and e.line is not None:
            out.setdefault(e.line, []).append((kinds.get(e.src, e.src.rsplit(":", 1)[-1]), e.dst))
    return out


def test_module_body_calls_emit_on_the_module(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    for line in (2, 5):
        assert calls.get(line) and {k for k, _ in calls[line]} == {"module"}, (line, calls)


def test_a_test_block_call_emits_on_the_module(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    for line in (15, 16):
        assert calls.get(line) and {k for k, _ in calls[line]} == {"module"}, (line, calls)


def test_a_def_body_call_keeps_its_function(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    assert calls.get(8) and "module" not in {k for k, _ in calls[8]}, calls


def test_typespec_type_references_emit_nothing(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    assert 3 not in calls and 4 not in calls, {k: v for k, v in calls.items() if k in (3, 4)}
