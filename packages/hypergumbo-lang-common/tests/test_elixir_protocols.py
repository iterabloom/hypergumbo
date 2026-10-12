# SPDX-License-Identifier: AGPL-3.0-or-later
"""Elixir protocols: ``defprotocol`` / ``defimpl`` identity and edges.

Before this, neither form was modelled. ``defprotocol Size do def size(data)
end`` emitted a bare ``function size`` with no module; ``defimpl Size, for:
Map`` emitted another bare ``size``; a ``defimpl`` nested in ``defmodule
Outer`` was attributed to ``Outer.size`` though its module is ``Size.Outer``.
No module symbol existed for the protocol or an impl, and nothing linked them.

What these tests pin:

- IDENTITY: ``defprotocol P`` is a module ``P`` (nested like ``defmodule``);
  its ``def`` heads are callback-shaped declarations (kind ``function``,
  ``modifiers=["abstract"]``, ``meta["arity"]``); ``defimpl P, for: T`` is
  the module ``P.T`` owning its defs, for every target of ``for: [A, B]``, for
  a ``for:``-less impl nested in ``defmodule T``, for ``__MODULE__``, and with
  P and T alias-expanded. An impl whose name cannot be read owns nothing --
  never a bare name.
- EDGES: ``implements`` ``P.T -> P`` (in-repo, else the unresolved external
  target) and ``dispatches_to`` protocol function -> impl function, matched by
  name AND arity.
- ROOTS: an impl of a protocol outside the repo (``String.Chars``,
  ``Jason.Encoder``) has its public functions dispatched to by its module, as
  the framework callback table roots a GenServer's callbacks.
- A protocol function IS a call target (``Size.size(x)`` dispatches at run
  time), unlike a behaviour ``@callback``.
"""

from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_lang_common.elixir import analyze_elixir

_REPRO = """defprotocol Size do
  def size(data)
end
defimpl Size, for: Map do
  def size(map), do: map_size(map)
end
defmodule Outer do
  defimpl Size, for: Outer do
    def size(_), do: 0
  end
end
"""


def _write(tmp_path: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        (tmp_path / name).write_text(body)


def _names(result, kind: str) -> list[str]:
    return sorted(s.name for s in result.symbols if s.kind == kind)


def _by_name(result, name: str, kind: str | None = None):
    found = [
        s for s in result.symbols
        if s.name == name and (kind is None or s.kind == kind)
    ]
    assert len(found) == 1, f"expected one {name!r} ({kind}), got {found}"
    return found[0]


def _pairs(result, edge_type: str) -> list[tuple[str, str]]:
    name = {s.id: s.name for s in result.symbols}
    return sorted(
        (name.get(e.src, e.src), name.get(e.dst, e.dst))
        for e in result.edges if e.edge_type == edge_type
    )


class TestIdentity:
    """Every protocol and impl function has its real module."""

    def test_filed_repro(self, tmp_path: Path) -> None:
        _write(tmp_path, {"size.ex": _REPRO})
        result = analyze_elixir(tmp_path)
        assert _names(result, "module") == ["Outer", "Size", "Size.Map", "Size.Outer"]
        assert _names(result, "function") == [
            "Size.Map.size", "Size.Outer.size", "Size.size",
        ]
        proto = _by_name(result, "Size.size")
        assert proto.modifiers == ["abstract"]
        assert (proto.meta or {}).get("arity") == 1
        assert proto.signature == "(data)"
        assert _by_name(result, "Size.Map.size").modifiers == []

    def test_multi_target_impl_is_one_module_per_target(self, tmp_path: Path) -> None:
        _write(tmp_path, {"s.ex": """defimpl Size, for: [List, Tuple] do
  def size(x), do: helper(x)
  defp helper(_), do: 1
end
"""})
        result = analyze_elixir(tmp_path)
        assert _names(result, "module") == ["Size.List", "Size.Tuple"]
        assert _names(result, "function") == [
            "Size.List.helper", "Size.List.size",
            "Size.Tuple.helper", "Size.Tuple.size",
        ]

    def test_target_from_module_attribute_forms(self, tmp_path: Path) -> None:
        """No ``for:`` inside ``defmodule T``, ``for: __MODULE__`` and
        ``for: __MODULE__.Sub`` all name the enclosing module."""
        _write(tmp_path, {"s.ex": """defmodule App.User do
  defimpl Size do
    def size(_), do: 1
  end
  defimpl Inspect, for: __MODULE__ do
    def inspect(_, _), do: "u"
  end
  defimpl String.Chars, for: __MODULE__.Sub do
    def to_string(_), do: "s"
  end
end
"""})
        result = analyze_elixir(tmp_path)
        assert _names(result, "module") == [
            "App.User", "Inspect.App.User", "Size.App.User",
            "String.Chars.App.User.Sub",
        ]
        assert _names(result, "function") == [
            "Inspect.App.User.inspect", "Size.App.User.size",
            "String.Chars.App.User.Sub.to_string",
        ]

    def test_protocol_and_target_are_alias_expanded(self, tmp_path: Path) -> None:
        _write(tmp_path, {"s.ex": """defmodule App do
  alias App.Proto.Sizer
  defmodule Box do
  end
  defimpl Sizer, for: Box do
    def size(_), do: 1
  end
  defimpl Sizer.Deep, for: Any do
    def size(_), do: 0
  end
end
"""})
        result = analyze_elixir(tmp_path)
        assert "App.Proto.Sizer.App.Box" in _names(result, "module")
        assert "App.Proto.Sizer.Deep.Any" in _names(result, "module")
        assert "App.Proto.Sizer.App.Box.size" in _names(result, "function")

    def test_nested_protocol_composes_like_a_module(self, tmp_path: Path) -> None:
        _write(tmp_path, {"s.ex": """defmodule App do
  defprotocol Render do
    @fallback_to_any true
    def render(t, opts)
  end
end
"""})
        result = analyze_elixir(tmp_path)
        assert _names(result, "module") == ["App", "App.Render"]
        render = _by_name(result, "App.Render.render")
        assert render.modifiers == ["abstract"]
        assert render.meta == {"arity": 2}

    def test_unreadable_impl_owns_nothing(self, tmp_path: Path) -> None:
        """A target the analyzer cannot read (``unquote``), or no ``for:`` and
        no enclosing module, mints no impl module and no bare-named function."""
        _write(tmp_path, {"s.ex": """defimpl Size, for: some_var do
  def size(_), do: 1
end
defimpl Size do
  def size(_), do: 2
end
defimpl unquote(p), for: Map do
  def size(_), do: 3
end
defimpl do
  def size(_), do: 4
end
defimpl Size, for: other_var do
  defmodule Helper do
    def help, do: 5
  end
end
"""})
        result = analyze_elixir(tmp_path)
        assert _names(result, "module") == []
        assert _names(result, "function") == []

    def test_unreadable_list_member_and_other_options(self, tmp_path: Path) -> None:
        """An unreadable member of ``for: [...]`` is dropped, the readable one
        kept; a keyword list without ``for:`` takes the enclosing module."""
        _write(tmp_path, {"s.ex": """defimpl Size, for: [Map, unquote(t)] do
  def size(_), do: 1
end
defmodule Box do
  defimpl Size, deprecated: true do
    def size(_), do: 2
  end
end
"""})
        result = analyze_elixir(tmp_path)
        assert _names(result, "module") == ["Box", "Size.Box", "Size.Map"]

    def test_impl_inside_quote_is_a_template(self, tmp_path: Path) -> None:
        _write(tmp_path, {"s.ex": """defmodule Deriver do
  defmacro __using__(_) do
    quote do
      defimpl Size, for: Thing do
        def size(_), do: 1
      end
    end
  end
end
"""})
        result = analyze_elixir(tmp_path)
        assert _names(result, "module") == ["Deriver"]
        assert _names(result, "function") == []

    def test_every_symbol_keeps_a_unique_stable_id(self, tmp_path: Path) -> None:
        from hypergumbo_core.analyze.base import populate_kind_stable_ids

        _write(tmp_path, {"size.ex": _REPRO + """defimpl Size, for: [List, Tuple] do
  def size(x), do: x
end
"""})
        result = analyze_elixir(tmp_path)
        symbols = list(result.symbols)
        populate_kind_stable_ids(symbols)
        ids = [s.stable_id for s in symbols]
        assert None not in ids
        assert len(set(ids)) == len(ids)


class TestEdges:
    """``implements`` and protocol-function -> impl dispatch."""

    def test_filed_repro_edges(self, tmp_path: Path) -> None:
        _write(tmp_path, {"size.ex": _REPRO})
        result = analyze_elixir(tmp_path)
        assert _pairs(result, "implements") == [
            ("Size.Map", "Size"), ("Size.Outer", "Size"),
        ]
        assert _pairs(result, "dispatches_to") == [
            ("Size.size", "Size.Map.size"), ("Size.size", "Size.Outer.size"),
        ]
        edge = next(e for e in result.edges if e.edge_type == "dispatches_to")
        assert edge.evidence_type == "behaviour_callback"

    def test_dispatch_matches_arity_and_skips_private(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "p.ex": "defprotocol P do\n  def f(a)\n  def g(a, b)\nend\n",
            "i.ex": """defimpl P, for: Map do
  def f(m), do: m
  def f(m, extra), do: {m, extra}
  def g(a, b), do: {a, b}
  defp g(a), do: a
end
""",
        })
        result = analyze_elixir(tmp_path)
        line = {s.id: s.span.start_line for s in result.symbols}
        got = sorted(
            (e.src, line[e.dst]) for e in result.edges
            if e.edge_type == "dispatches_to"
        )
        f_id = _by_name(result, "P.f").id
        g_id = _by_name(result, "P.g").id
        assert sorted(got) == sorted([(f_id, 2), (g_id, 4)])

    def test_external_protocol_is_an_unresolved_target_and_roots_its_impl(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, {"u.ex": """defmodule User do
  defstruct [:name]
  defimpl String.Chars do
    def to_string(u), do: fmt(u)
    defp fmt(u), do: u.name
  end
end
"""})
        result = analyze_elixir(tmp_path)
        assert _pairs(result, "implements") == [
            ("String.Chars.User", "elixir:external:0-0:String.Chars:unresolved"),
        ]
        assert _pairs(result, "dispatches_to") == [
            ("String.Chars.User", "String.Chars.User.to_string"),
        ]

    def test_in_repo_protocol_impl_is_not_rooted_by_its_module(
        self, tmp_path: Path,
    ) -> None:
        """An in-repo protocol's impl is reached through the protocol
        function, never from its own module."""
        _write(tmp_path, {"size.ex": _REPRO})
        result = analyze_elixir(tmp_path)
        assert not [
            p for p in _pairs(result, "dispatches_to") if p[0] != "Size.size"
        ]

    def test_protocol_function_is_a_qualified_call_target(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "size.ex": _REPRO,
            "use.ex": """defmodule Caller do
  alias Size, as: S
  def go(x), do: Size.size(x) + S.size(x)
  def size(x), do: x
end
""",
        })
        result = analyze_elixir(tmp_path)
        assert ("Caller.go", "Size.size") in _pairs(result, "calls")
        # Only by its qualified name: a bare ``size`` in the protocol's own
        # file never reaches the bodiless head.
        assert ("Size.Map.size", "Size.size") not in _pairs(result, "calls")

    def test_call_in_impl_body_anchors_on_the_impl_module(self, tmp_path: Path) -> None:
        _write(tmp_path, {"s.ex": """defimpl Size, for: Map do
  @doc Helper.doc()
  def size(m), do: m
end
"""})
        result = analyze_elixir(tmp_path)
        assert ("Size.Map", "elixir:Helper:0-0:doc:unresolved") in _pairs(result, "calls")


class TestBehaviourCallbackArity:
    """INV-dital: a ``@callback`` dispatches only to clauses of its arity."""

    def test_callback_reaches_only_its_arity(self, tmp_path: Path) -> None:
        _write(tmp_path, {"b.ex": """defmodule B do
  @callback f(term) :: term
end
defmodule M do
  @behaviour B
  def f(a), do: a
  def f(a, b), do: {a, b}
end
"""})
        result = analyze_elixir(tmp_path)
        line = {s.id: s.span.start_line for s in result.symbols}
        got = [
            line[e.dst] for e in result.edges
            if e.edge_type == "dispatches_to"
            and e.src == _by_name(result, "B.f").id
        ]
        assert got == [6]
        assert _by_name(result, "B.f").meta == {"arity": 1}


class TestPipeline:
    """End to end: the behavior map carries the protocol shape."""

    def test_behavior_map_has_protocol_dispatch(self, tmp_path: Path) -> None:
        from hypergumbo_core.cli import run_behavior_map

        repo = tmp_path / "repo"
        repo.mkdir()
        _write(repo, {"size.ex": _REPRO})
        out = tmp_path / "bm.json"
        run_behavior_map(
            repo_root=repo, out_path=out,
            include_sketch_precomputed=False, progress=False,
        )
        bm = json.loads(out.read_text())
        name = {n["id"]: n["name"] for n in bm["nodes"]}
        got = sorted(
            (e["type"], name[e["src"]], name[e["dst"]])
            for e in bm["edges"]
            if e["type"] in ("implements", "dispatches_to")
            and e["src"] in name and e["dst"] in name
        )
        assert got == [
            ("dispatches_to", "Size.size", "Size.Map.size"),
            ("dispatches_to", "Size.size", "Size.Outer.size"),
            ("implements", "Size.Map", "Size"),
            ("implements", "Size.Outer", "Size"),
        ]
