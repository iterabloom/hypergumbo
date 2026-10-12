# SPDX-License-Identifier: AGPL-3.0-or-later
"""Elixir behaviours as a type relationship (WI-vitas).

A behaviour is Elixir's interface mechanism: ``@callback`` declares a function
an implementing module must define, and ``@behaviour X`` (or a ``use X`` whose
``__using__`` sets it) declares that the module implements ``X``. Before this
item neither was modelled:

1. ``@callback area() :: integer`` produced NO symbol, so the required function
   did not exist and there was nothing to dispatch FROM;
2. ``@behaviour Shape`` produced NO edge, so nothing linked the implementing
   module to its behaviour. Only the twelve framework behaviours in
   ``BEHAVIOUR_CALLBACKS`` got anything, and that was module -> callback
   ``dispatches_to``, never a type relationship.

What these tests pin:

- a callback symbol (kind ``function``, or ``macro`` for ``@macrocallback``,
  ``modifiers=["abstract"]``) owned by its module, for every head shape the
  grammar produces, and NONE inside ``quote`` or outside a module;
- the WI-dokib rule is kept: a typespec body still emits no call edge, and a
  callback symbol is never a CALL target (bare, same-file, or qualified);
- ``implements`` from the module to its behaviour: resolved to the in-repo
  module, else an unresolved ``elixir:external:0-0:<Behaviour>:unresolved``
  target; ``use X`` only where X's ``__using__`` is known to set the behaviour;
- ``dispatches_to`` from each callback to the implementing function (its
  clauses folded into one symbol per name/arity), same-file and cross-file, kind-matched (a ``@callback`` never links to a
  same-named ``defmacro``).
"""

from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_lang_common.elixir import analyze_elixir

_SHAPE = """defmodule Shape do
  @callback area() :: integer
  @callback scale(shape :: t, factor :: number) :: t when t: term
  @macrocallback mk(arg :: term) :: Macro.t
  @callback simple :: :ok
  @optional_callbacks scale: 2
end
"""


def _write(tmp_path: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        (tmp_path / name).write_text(body)


def _by_name(result, name: str, kind: str | None = None):
    found = [
        s for s in result.symbols
        if s.name == name and (kind is None or s.kind == kind)
    ]
    assert len(found) == 1, f"expected one {name!r} ({kind}), got {found}"
    return found[0]


def _edges(result, edge_type: str):
    return [e for e in result.edges if e.edge_type == edge_type]


class TestCallbackSymbols:
    """``@callback`` / ``@macrocallback`` mint a symbol on their module."""

    def test_every_head_shape_is_a_symbol(self, tmp_path: Path) -> None:
        _write(tmp_path, {"shape.ex": _SHAPE})
        result = analyze_elixir(tmp_path)
        area = _by_name(result, "Shape.area")
        assert area.kind == "function"
        assert area.modifiers == ["abstract"]
        assert area.signature == "() :: integer"
        assert area.span.start_line == 2
        scale = _by_name(result, "Shape.scale")
        assert scale.kind == "function"
        assert scale.signature == "(shape :: t, factor :: number) :: t when t: term"
        simple = _by_name(result, "Shape.simple")
        assert simple.signature == "() :: :ok"

    def test_macrocallback_is_a_macro(self, tmp_path: Path) -> None:
        _write(tmp_path, {"shape.ex": _SHAPE})
        result = analyze_elixir(tmp_path)
        mk = _by_name(result, "Shape.mk")
        assert mk.kind == "macro"
        assert mk.modifiers == ["abstract"]

    def test_other_attributes_are_not_callbacks(self, tmp_path: Path) -> None:
        """``@optional_callbacks`` / ``@spec`` / ``@type`` name no callback."""
        _write(tmp_path, {"m.ex": """defmodule M do
  @optional_callbacks foo: 1
  @spec run(integer) :: :ok
  @type t :: map
  @doc "x"
  def run(_), do: :ok
end
"""})
        result = analyze_elixir(tmp_path)
        assert not [s for s in result.symbols if "abstract" in s.modifiers]
        assert {s.name for s in result.symbols} == {"M", "M.run"}

    def test_nested_module_owns_its_callback(self, tmp_path: Path) -> None:
        _write(tmp_path, {"m.ex": """defmodule Outer do
  defmodule Inner do
    @callback go() :: :ok
  end
end
"""})
        result = analyze_elixir(tmp_path)
        assert _by_name(result, "Outer.Inner.go").kind == "function"

    def test_callback_inside_quote_is_a_template(self, tmp_path: Path) -> None:
        """INV-sinah: a ``quote`` body belongs to the module that runs ``use``."""
        _write(tmp_path, {"m.ex": """defmodule Tmpl do
  defmacro __using__(_) do
    quote do
      @callback injected() :: :ok
    end
  end
end
"""})
        result = analyze_elixir(tmp_path)
        assert not [s for s in result.symbols if s.name.endswith("injected")]

    def test_heads_that_name_no_function_emit_nothing(self, tmp_path: Path) -> None:
        """Parseable but nameless: an empty ``@callback()``, a dotted head, a
        literal head; and argument-less ``@behaviour`` / ``use`` blocks."""
        _write(tmp_path, {"odd.ex": """defmodule Odd do
  @callback()
  @callback Mod.foo() :: t
  @callback 1 :: :ok
  @behaviour do
  end
  use do
  end
end
"""})
        result = analyze_elixir(tmp_path)
        assert {s.name for s in result.symbols} == {"Odd"}
        assert _edges(result, "implements") == []

    def test_callback_outside_any_module_emits_nothing(self, tmp_path: Path) -> None:
        _write(tmp_path, {"loose.exs": "@callback orphan() :: :ok\n"})
        result = analyze_elixir(tmp_path)
        assert not [s for s in result.symbols if "orphan" in s.name]

    def test_typespec_body_still_emits_no_call(self, tmp_path: Path) -> None:
        """WI-dokib: the symbol is new, the silence of its body is not."""
        _write(tmp_path, {"m.ex": """defmodule M do
  @callback child_spec(keyword) :: :supervisor.child_spec()
  @callback other() :: Remote.t()
end
"""})
        result = analyze_elixir(tmp_path)
        assert _by_name(result, "M.child_spec").kind == "function"
        assert _edges(result, "calls") == []

    def test_a_callback_is_never_a_call_target(self, tmp_path: Path) -> None:
        """Bare, same-file and qualified calls do not bind to a declaration.

        ``area()`` inside ``Shape`` and ``Shape.area()`` from anywhere are not
        calls of the callback: a behaviour does not define its callbacks.
        """
        _write(tmp_path, {
            "shape.ex": """defmodule Shape do
  @callback area() :: integer
  def describe, do: area()
end
""",
            "user.ex": """defmodule User do
  import Shape
  def go, do: Shape.area()
  def bare, do: area()
end
""",
        })
        result = analyze_elixir(tmp_path)
        area = _by_name(result, "Shape.area")
        assert not [e for e in result.edges if e.dst == area.id]


class TestImplementsEdge:
    """``@behaviour X`` (and ``use X`` for known behaviours) -> ``implements``."""

    def test_in_repo_behaviour_resolves(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "shape.ex": _SHAPE,
            "square.ex": """defmodule Square do
  @behaviour Shape
  def area, do: 1
end
""",
        })
        result = analyze_elixir(tmp_path)
        square = _by_name(result, "Square", "module")
        shape = _by_name(result, "Shape", "module")
        impl = _edges(result, "implements")
        assert [(e.src, e.dst) for e in impl] == [(square.id, shape.id)]
        assert impl[0].evidence_type == "ast_implements"
        assert impl[0].is_resolved is True

    def test_alias_expanded_behaviour_resolves(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "shape.ex": """defmodule MyApp.Shape do
  @callback area() :: integer
end
""",
            "square.ex": """defmodule MyApp.Square do
  alias MyApp.Shape
  @behaviour Shape
  def area, do: 1
end
""",
        })
        result = analyze_elixir(tmp_path)
        shape = _by_name(result, "MyApp.Shape", "module")
        assert [e.dst for e in _edges(result, "implements")] == [shape.id]

    def test_external_behaviour_is_an_unresolved_target(self, tmp_path: Path) -> None:
        _write(tmp_path, {"p.ex": """defmodule MyPlug do
  @behaviour Plug
  @behaviour NexAI.Middleware
  def init(o), do: o
  def call(c, _), do: c
end
"""})
        result = analyze_elixir(tmp_path)
        impl = _edges(result, "implements")
        assert sorted(e.dst for e in impl) == [
            "elixir:external:0-0:NexAI.Middleware:unresolved",
            "elixir:external:0-0:Plug:unresolved",
        ]
        assert all(e.is_resolved is False for e in impl)

    def test_use_implies_only_the_behaviour_its_using_sets(self, tmp_path: Path) -> None:
        """Verified against each library's ``__using__``: Plug.Builder sets
        ``@behaviour Plug``; Ecto.Repo and HTTPoison.Base set themselves;
        Agent, Task and Phoenix.Component set none."""
        _write(tmp_path, {"u.ex": """defmodule A do
  use GenServer
end
defmodule B do
  use Plug.Builder
end
defmodule C do
  use Plug.Router
end
defmodule D do
  use Agent
  use Task
  use Phoenix.Component
  use ExUnit.Case
end
defmodule E do
  use Phoenix.LiveView
  use Phoenix.LiveComponent
  use Supervisor
  use WebSockex
end
defmodule F do
  use Ecto.Repo, otp_app: :app, adapter: Ecto.Adapters.Postgres
end
defmodule G do
  use HTTPoison.Base
end
"""})
        result = analyze_elixir(tmp_path)
        names = {s.id: s.name for s in result.symbols}
        got = sorted((names[e.src], e.dst) for e in _edges(result, "implements"))
        ext = "elixir:external:0-0:{}:unresolved".format
        assert got == [
            ("A", ext("GenServer")),
            ("B", ext("Plug")),
            ("C", ext("Plug")),
            ("E", ext("Phoenix.LiveComponent")),
            ("E", ext("Phoenix.LiveView")),
            ("E", ext("Supervisor")),
            ("E", ext("WebSockex")),
            ("F", ext("Ecto.Repo")),
            ("G", ext("HTTPoison.Base")),
        ]

    def test_use_and_behaviour_of_the_same_module_give_one_edge(self, tmp_path: Path) -> None:
        _write(tmp_path, {"s.ex": """defmodule S do
  use GenServer
  @behaviour GenServer
  def init(x), do: {:ok, x}
end
"""})
        result = analyze_elixir(tmp_path)
        assert len(_edges(result, "implements")) == 1

    def test_behaviour_inside_quote_is_not_the_defining_modules(self, tmp_path: Path) -> None:
        """``quote do @behaviour Cache end`` in ``Cache.__using__`` is the USING
        module's declaration; attributing it to ``Cache`` is a self-edge."""
        _write(tmp_path, {"c.ex": """defmodule Cache do
  @callback get(term) :: term
  defmacro __using__(_) do
    quote do
      @behaviour Cache
      use GenServer
    end
  end
end
"""})
        result = analyze_elixir(tmp_path)
        assert _edges(result, "implements") == []
        assert not [e for e in _edges(result, "dispatches_to")
                    if e.src.endswith("Cache.get:function")]

    def test_no_behaviour_no_edge(self, tmp_path: Path) -> None:
        _write(tmp_path, {"m.ex": "defmodule M do\n  def f, do: 1\nend\n"})
        result = analyze_elixir(tmp_path)
        assert _edges(result, "implements") == []


class TestUsingTemplates:
    """``use M`` adopts what M's ``__using__`` template declares.

    This is how an in-repo behaviour is usually adopted (phoenix's
    ``use Phoenix.Channel``, plausible's ``use Plausible.Cache``): the module
    never writes ``@behaviour`` itself.
    """

    _CACHE = """defmodule MyApp.Cache do
  alias MyApp.Other
  @callback get(term) :: term
  defmacro __using__(_) do
    quote do
      @behaviour unquote(__MODULE__)
      @behaviour Other
      use GenServer
    end
  end
end
defmodule MyApp.Other do
  @callback ping() :: :pong
end
defmodule MyApp.Wrapper do
  defmacro __using__(_) do
    quote do
      use MyApp.Cache
    end
  end
end
"""

    def _implements(self, result) -> list[tuple[str, str]]:
        names = {s.id: s.name for s in result.symbols}
        return sorted(
            (names[e.src], names.get(e.dst, e.dst))
            for e in _edges(result, "implements")
        )

    def test_use_adopts_the_templates_behaviours(self, tmp_path: Path) -> None:
        """``unquote(__MODULE__)`` is the defining module; an alias in the
        quote is expanded with the DEFINING file's aliases."""
        _write(tmp_path, {
            "cache.ex": self._CACHE,
            "impl.ex": """defmodule MyApp.UserCache do
  use MyApp.Cache
  def get(k), do: k
  def ping, do: :pong
end
""",
        })
        result = analyze_elixir(tmp_path)
        assert self._implements(result) == [
            ("MyApp.UserCache", "MyApp.Cache"),
            ("MyApp.UserCache", "MyApp.Other"),
            ("MyApp.UserCache", "elixir:external:0-0:GenServer:unresolved"),
        ]
        sym = {s.id: s.name for s in result.symbols}
        assert sorted(
            (sym[e.src], sym[e.dst]) for e in _edges(result, "dispatches_to")
            if e.src in sym and e.dst in sym
        ) == [
            ("MyApp.Cache.get", "MyApp.UserCache.get"),
            ("MyApp.Other.ping", "MyApp.UserCache.ping"),
        ]

    def test_a_template_that_uses_a_template_is_followed(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "cache.ex": self._CACHE,
            "impl.ex": """defmodule MyApp.UserCache do
  alias MyApp.Wrapper
  use Wrapper
end
""",
        })
        result = analyze_elixir(tmp_path)
        assert [d for _s, d in self._implements(result)] == [
            "MyApp.Cache", "MyApp.Other",
            "elixir:external:0-0:GenServer:unresolved",
        ]

    def test_a_cycle_of_templates_terminates(self, tmp_path: Path) -> None:
        _write(tmp_path, {"c.ex": """defmodule A do
  defmacro __using__(_) do
    quote do
      @behaviour A
      use B
    end
  end
end
defmodule B do
  defmacro __using__(_) do
    quote do
      @behaviour B
      use A
    end
  end
end
defmodule User do
  use A
end
"""})
        result = analyze_elixir(tmp_path)
        assert self._implements(result) == [("User", "A"), ("User", "B")]

    def test_quotes_outside_using_and_unowned_templates_declare_nothing(
        self, tmp_path: Path,
    ) -> None:
        """A ``quote`` in an ordinary function is not ``__using__``; a
        ``__using__`` in an atom-named or absent module has no owner; an
        ``unquote(__MODULE__)`` outside any quote names nothing."""
        _write(tmp_path, {
            "m.ex": """defmodule Gen do
  def template, do: quote(do: @behaviour Gen)
end
defmodule :weird do
  defmacro __using__(_) do
    quote do
      @behaviour Weird
    end
  end
end
defmodule Bad do
  @behaviour unquote(__MODULE__)
end
defmodule User do
  use Gen
end
""",
            "loose.exs": """defmacro __using__(_) do
  quote do
    @behaviour Loose
  end
end
""",
        })
        result = analyze_elixir(tmp_path)
        assert self._implements(result) == []


class TestCallbackDispatch:
    """Each callback ``dispatches_to`` the implementing module's function (one
    symbol per name/arity, its clauses folded in)."""

    def _dispatch_pairs(self, result) -> list[tuple[str, str, int]]:
        sym = {s.id: s for s in result.symbols}
        return sorted(
            (sym[e.src].name, sym[e.dst].name, sym[e.dst].span.start_line)
            for e in _edges(result, "dispatches_to")
            if e.src in sym and "abstract" in sym[e.src].modifiers
        )

    def test_same_file_parity_fixture(self, tmp_path: Path) -> None:
        """The interface-dispatch parity column's exact fixture."""
        _write(tmp_path, {"s.ex": """defmodule Shape do
  @callback area() :: integer
end

defmodule Square do
  @behaviour Shape
  def area, do: 1
end
"""})
        result = analyze_elixir(tmp_path)
        assert self._dispatch_pairs(result) == [("Shape.area", "Square.area", 7)]
        edge = next(e for e in _edges(result, "dispatches_to"))
        assert edge.evidence_type == "behaviour_callback"
        assert (edge.meta or {}).get("mechanism") == "callback"

    def test_cross_file_folded_function_and_only_callbacks(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "shape.ex": _SHAPE,
            "square.ex": """defmodule Square do
  @behaviour Shape
  def area, do: 1
  def scale(s, 0), do: s
  def scale(s, f), do: {s, f}
  defmacro mk(a), do: a
  def unrelated, do: :ok
end
""",
        })
        result = analyze_elixir(tmp_path)
        assert self._dispatch_pairs(result) == [
            ("Shape.area", "Square.area", 3),
            ("Shape.mk", "Square.mk", 6),
            ("Shape.scale", "Square.scale", 4),
        ]

    def test_kind_and_visibility_must_match(self, tmp_path: Path) -> None:
        """A ``@callback`` is implemented by a public ``def``: never by a
        ``defmacro`` and never by a ``defp``, which no other module can call."""
        _write(tmp_path, {"s.ex": """defmodule B do
  @callback run() :: :ok
  @macrocallback gen() :: Macro.t
  @callback hidden() :: :ok
end
defmodule I do
  @behaviour B
  defmacro run, do: :ok
  def gen, do: :ok
  defp hidden, do: :ok
end
"""})
        result = analyze_elixir(tmp_path)
        assert self._dispatch_pairs(result) == []

    def test_a_module_without_the_directive_gets_nothing(self, tmp_path: Path) -> None:
        _write(tmp_path, {"s.ex": """defmodule Shape do
  @callback area() :: integer
end
defmodule Circle do
  def area, do: 3
end
"""})
        result = analyze_elixir(tmp_path)
        assert self._dispatch_pairs(result) == []

    def test_framework_table_edges_are_unchanged(self, tmp_path: Path) -> None:
        """The module -> callback edges of the 12 table behaviours stay."""
        _write(tmp_path, {"p.ex": """defmodule MyPlug do
  @behaviour Plug
  def init(o), do: o
  def call(c, _), do: c
end
"""})
        result = analyze_elixir(tmp_path)
        mod = _by_name(result, "MyPlug", "module")
        sym = {s.id: s.name for s in result.symbols}
        assert sorted(
            sym[e.dst] for e in _edges(result, "dispatches_to") if e.src == mod.id
        ) == ["MyPlug.call", "MyPlug.init"]


class TestFrameworkTableScope:
    """The framework table roots only the directive's OWN module's functions."""

    def test_a_sibling_modules_same_named_function_is_not_rooted(
        self, tmp_path: Path,
    ) -> None:
        """Two modules in one file: ``use GenServer`` in ``A`` roots nothing
        of ``B``, whose ``init`` merely shares the callback's name."""
        _write(tmp_path, {"two.ex": """defmodule A do
  use GenServer
end
defmodule B do
  def init(x), do: x
end
"""})
        result = analyze_elixir(tmp_path)
        assert [
            e for e in _edges(result, "dispatches_to")
            if (e.meta or {}).get("mechanism") == "callback"
        ] == []


class TestPipeline:
    """End to end: the emitted behavior map carries the parity shape."""

    def test_behavior_map_has_callback_to_impl_dispatch(self, tmp_path: Path) -> None:
        from hypergumbo_core.cli import run_behavior_map

        repo = tmp_path / "repo"
        repo.mkdir()
        _write(repo, {
            "shape.ex": "defmodule Shape do\n  @callback area() :: integer\nend\n",
            "square.ex": "defmodule Square do\n  @behaviour Shape\n  def area, do: 1\nend\n",
        })
        out = tmp_path / "bm.json"
        run_behavior_map(
            repo_root=repo, out_path=out,
            include_sketch_precomputed=False, progress=False,
        )
        bm = json.loads(out.read_text())
        name = {n["id"]: (n["name"], n["kind"]) for n in bm["nodes"]}
        got = sorted(
            (e["type"], name[e["src"]], name[e["dst"]])
            for e in bm["edges"]
            if e["type"] in ("implements", "dispatches_to")
            and e["src"] in name and e["dst"] in name
        )
        assert got == [
            ("dispatches_to", ("Shape.area", "function"), ("Square.area", "function")),
            ("implements", ("Square", "module"), ("Shape", "module")),
        ]


class TestWebModuleDispatcher:
    """``use AppWeb, :live_view`` adopts what ``AppWeb.live_view``'s quote does.

    The Phoenix generator's web module dispatches ``use AppWeb, :which`` to
    ``apply(__MODULE__, which, [])``: the quote that runs in the user lives in
    a public ``def which``, not in ``__using__``. That template is read like a
    ``__using__`` one (behaviours, and every ``use`` followed), and it feeds
    BOTH the ``implements`` edges and the framework callback table, so a
    LiveView's ``mount`` / ``render`` / ``handle_event`` are rooted.
    """

    _WEB = """defmodule MyAppWeb do
  alias MyAppWeb.Flash

  def live_view(opts \\\\ []) do
    quote do
      use Phoenix.LiveView, layout: {MyAppWeb.Layouts, :app}
      use Flash
    end
  end

  def live_component do
    quote do
      use Phoenix.LiveComponent
    end
  end

  defp helpers do
    quote do
      use Phoenix.LiveView
    end
  end

  defmacro __using__(which) when is_atom(which) do
    apply(__MODULE__, which, [])
  end

  defmacro __using__([{which, opts}]) when is_atom(which) do
    apply(__MODULE__, which, [List.wrap(opts)])
  end

  def mount(_p, _s, socket), do: {:ok, socket}
  def update(a, s), do: {a, s}
end
defmodule MyAppWeb.Flash do
  @callback flash() :: :ok
  defmacro __using__(_) do
    quote do
      @behaviour MyAppWeb.Flash
    end
  end
end
"""

    _USERS = """defmodule MyAppWeb.SitesLive do
  use MyAppWeb, :live_view
  def mount(_p, _s, socket), do: {:ok, socket}
  def render(assigns), do: assigns
  def handle_event(_e, _p, s), do: {:noreply, s}
  def flash, do: :ok
end
defmodule MyAppWeb.Modal do
  use MyAppWeb, :live_component
  def update(_a, s), do: {:ok, s}
  def render(assigns), do: assigns
end
defmodule MyAppWeb.WithOpts do
  use MyAppWeb, live_view: [:no_sentry]
  def mount(_p, _s, socket), do: {:ok, socket}
end
defmodule MyAppWeb.Private do
  use MyAppWeb, :helpers
  def mount(_p, _s, socket), do: {:ok, socket}
end
defmodule MyAppWeb.Dynamic do
  use MyAppWeb, kind
  def mount(_p, _s, socket), do: {:ok, socket}
end
"""

    def _implements(self, result) -> list[tuple[str, str]]:
        names = {s.id: s.name for s in result.symbols}
        return sorted(
            (names[e.src], names.get(e.dst, e.dst))
            for e in _edges(result, "implements")
        )

    def _rooted(self, result) -> list[tuple[str, str]]:
        """Framework-table edges: module -> its callback functions."""
        sym = {s.id: s for s in result.symbols}
        return sorted(
            (sym[e.src].name, sym[e.dst].name)
            for e in _edges(result, "dispatches_to")
            if e.src in sym and e.dst in sym and sym[e.src].kind == "module"
        )

    def test_dispatched_template_declares_its_behaviours(self, tmp_path: Path) -> None:
        """``:live_view`` / ``:live_component`` / ``live_view: opts`` each adopt
        their def's quote, followed through its nested in-repo ``use``; a
        ``defp`` is never ``apply``-able, and a non-literal name names no
        function: both declare nothing."""
        _write(tmp_path, {"web.ex": self._WEB, "users.ex": self._USERS})
        result = analyze_elixir(tmp_path)
        assert self._implements(result) == [
            ("MyAppWeb.Modal", "elixir:external:0-0:Phoenix.LiveComponent:unresolved"),
            ("MyAppWeb.SitesLive", "MyAppWeb.Flash"),
            ("MyAppWeb.SitesLive", "elixir:external:0-0:Phoenix.LiveView:unresolved"),
            ("MyAppWeb.WithOpts", "MyAppWeb.Flash"),
            ("MyAppWeb.WithOpts", "elixir:external:0-0:Phoenix.LiveView:unresolved"),
        ]
        sym = {s.id: s.name for s in result.symbols}
        assert ("MyAppWeb.Flash.flash", "MyAppWeb.SitesLive.flash") in [
            (sym.get(e.src), sym.get(e.dst)) for e in _edges(result, "dispatches_to")
        ]

    def test_dispatched_template_roots_the_framework_callbacks(
        self, tmp_path: Path,
    ) -> None:
        """The callback table fires for the USER, and never for the web
        module, whose own ``mount`` / ``update`` are not callbacks of the
        ``use Phoenix.LiveView`` written inside its quote."""
        _write(tmp_path, {"web.ex": self._WEB, "users.ex": self._USERS})
        result = analyze_elixir(tmp_path)
        assert self._rooted(result) == [
            ("MyAppWeb.Modal", "MyAppWeb.Modal.render"),
            ("MyAppWeb.Modal", "MyAppWeb.Modal.update"),
            ("MyAppWeb.SitesLive", "MyAppWeb.SitesLive.handle_event"),
            ("MyAppWeb.SitesLive", "MyAppWeb.SitesLive.mount"),
            ("MyAppWeb.SitesLive", "MyAppWeb.SitesLive.render"),
            ("MyAppWeb.WithOpts", "MyAppWeb.WithOpts.mount"),
        ]

    def test_only_an_apply_dispatcher_exposes_its_defs(self, tmp_path: Path) -> None:
        """A module whose ``__using__`` is an ordinary template, or applies
        some OTHER module, or applies a name that is not its parameter, is no
        dispatcher: ``use M, :live_view`` there reads only ``__using__``."""
        _write(tmp_path, {"m.ex": """defmodule Plain do
  def live_view do
    quote do
      use Phoenix.LiveView
    end
  end
  defmacro __using__(_opts) do
    quote do
      import Plain
    end
  end
end
defmodule Elsewhere do
  def live_view do
    quote do
      use Phoenix.LiveView
    end
  end
  defmacro __using__(which), do: apply(Other, which, [])
end
defmodule Fixed do
  def live_view do
    quote do
      use Phoenix.LiveView
    end
  end
  defmacro __using__(which), do: apply(__MODULE__, :live_view, [which])
end
defmodule Unrelated do
  def live_view do
    quote do
      use Phoenix.LiveView
    end
  end
  defmacro __using__(which), do: apply(__MODULE__, other, [which])
end
defmodule A do
  use Plain, :live_view
  def mount(_p, _s, socket), do: {:ok, socket}
end
defmodule B do
  use Elsewhere, :live_view
  def mount(_p, _s, socket), do: {:ok, socket}
end
defmodule C do
  use Fixed, :live_view
  def mount(_p, _s, socket), do: {:ok, socket}
end
defmodule D do
  use Unrelated, :live_view
  def mount(_p, _s, socket), do: {:ok, socket}
end
"""})
        result = analyze_elixir(tmp_path)
        assert self._implements(result) == []
        assert self._rooted(result) == []

    def test_a_using_template_roots_the_user_not_its_definer(
        self, tmp_path: Path,
    ) -> None:
        """The second half: ``use GenServer`` inside a ``__using__`` quote
        roots the callbacks of the module that runs ``use``, not those of the
        module that writes the template."""
        _write(tmp_path, {"c.ex": """defmodule MyApp.Base do
  defmacro __using__(_) do
    quote do
      use GenServer
    end
  end
  def init(x), do: x
end
defmodule MyApp.Worker do
  use MyApp.Base
  def init(x), do: {:ok, x}
  def handle_call(_m, _f, s), do: {:reply, s, s}
end
"""})
        result = analyze_elixir(tmp_path)
        assert self._rooted(result) == [
            ("MyApp.Worker", "MyApp.Worker.handle_call"),
            ("MyApp.Worker", "MyApp.Worker.init"),
        ]


class TestAtomBehaviour:
    """WI-hidit: ``@behaviour :gen_cycle`` names an ERLANG module by its atom.

    It used to be read not at all -- only an ``alias`` argument was a
    directive -- so plausible's two ``:gen_cycle`` workers and its
    ``:otel_sampler``, and livebook's ``:gen_statem`` connection, implemented
    nothing. The atom is the module's full name (no alias expansion applies)
    and is spelled without the colon, as the atom call form spells it.
    Resolving it to an in-repo Erlang module is the BEAM behaviour bridge
    linker's job; this analyzer cannot see Erlang symbols.
    """

    _WORKER = """defmodule Plausible.Cache.Warmer do
  @behaviour :gen_cycle
  def init_cycle(opts), do: {:ok, opts}
end
"""

    def _implements(self, result) -> list[str]:
        return [e.dst for e in _edges(result, "implements")]

    def test_atom_behaviour_is_an_unresolved_target(self, tmp_path: Path) -> None:
        _write(tmp_path, {"w.ex": self._WORKER})
        result = analyze_elixir(tmp_path)
        implements = _edges(result, "implements")
        assert self._implements(result) == ["elixir:external:0-0:gen_cycle:unresolved"]
        assert implements[0].is_resolved is False
        assert implements[0].src == _by_name(result, "Plausible.Cache.Warmer").id
        assert implements[0].line == 2

    def test_otp_atom_behaviour_roots_its_callbacks(self, tmp_path: Path) -> None:
        """``@behaviour :gen_statem`` roots OTP's callbacks, as Erlang does."""
        _write(tmp_path, {"c.ex": """defmodule Livebook.Teams.Connection do
  @behaviour :gen_statem
  def callback_mode, do: :handle_event_function
  def init(arg), do: {:ok, :idle, arg}
  def other, do: :ok
end
"""})
        result = analyze_elixir(tmp_path)
        module = _by_name(result, "Livebook.Teams.Connection")
        by_id = {s.id: s for s in result.symbols}
        rooted = sorted(
            by_id[e.dst].name for e in _edges(result, "dispatches_to")
            if e.src == module.id
        )
        assert rooted == [
            "Livebook.Teams.Connection.callback_mode",
            "Livebook.Teams.Connection.init",
        ]
        assert self._implements(result) == ["elixir:external:0-0:gen_statem:unresolved"]

    def test_atom_behaviour_in_a_using_template_is_adopted(self, tmp_path: Path) -> None:
        _write(tmp_path, {"t.ex": """defmodule Tmpl do
  defmacro __using__(_) do
    quote do
      @behaviour :gen_cycle
    end
  end
end
defmodule User do
  use Tmpl
end
"""})
        result = analyze_elixir(tmp_path)
        user = _by_name(result, "User")
        assert [(e.src, e.dst) for e in _edges(result, "implements")] == [
            (user.id, "elixir:external:0-0:gen_cycle:unresolved"),
        ]

    def test_quoted_atom_and_use_of_an_atom_are_not_read(self, tmp_path: Path) -> None:
        """``:"Elixir.Foo"`` is not an Erlang module name, and ``use :mod``
        needs a ``__using__`` macro, which an Erlang module cannot define."""
        _write(tmp_path, {"q.ex": """defmodule Q do
  @behaviour :"Elixir.Foo"
  use :gen_cycle
end
"""})
        result = analyze_elixir(tmp_path)
        assert _edges(result, "implements") == []


class TestCallbackStableId:
    """WI-hidit analogue: a callback beside a same-named ``def`` in its own
    module shared the backstop floor key with it, and both stable_ids were
    nulled. The callback's id is now minted by the analyzer."""

    def test_callback_and_same_named_def_both_keep_an_identity(
        self, tmp_path: Path,
    ) -> None:
        from hypergumbo_core.analyze.base import populate_kind_stable_ids

        _write(tmp_path, {"s.ex": """defmodule Shape do
  @callback area() :: integer
  @callback area(t) :: integer
  def area, do: 0
end
"""})
        result = analyze_elixir(tmp_path)
        symbols = list(result.symbols)
        populate_kind_stable_ids(symbols)
        areas = [s for s in symbols if s.name == "Shape.area"]
        assert len(areas) == 3
        ids = [s.stable_id for s in areas]
        assert None not in ids
        assert len(set(ids)) == 3
