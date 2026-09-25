# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kafor: which modules count as "this project's" for a qualified call.

`_handle_dot_call`'s last resort before the unresolved fallback asked whether
the project plausibly owns the written module, and asked it as a SUBSTRING
test -- `any(f"{module}." in k for k in global_symbols)` -- though the comment
above it describes a prefix-COMPONENT test. When it passed, the resolver bound
by BARE NAME with only a path hint, so `Logger.error(...)` could land on any
project function named `error`.

TWO SUB-MECHANISMS, instrumented on phoenix-framework rather than assumed, and
the reason a component-aware fix would NOT have been enough:

  SUFFIX ARTIFACT, matching mid-component. `Logger.` is a substring of
  `Phoenix.Logger.install`; `Path.` of `...StaticPath.path`; `Supervisor.` of
  `Phoenix.Endpoint.Supervisor.init`. A component-aware test rejects these.

  NAMESPACE PREFIX, a real component-boundary match that is still wrong.
  phoenix defines 56 modules under `Mix.`, so `Mix.` matches
  `Mix.Tasks.Local.Phx` on any component test -- and `Mix.raise/1` is still
  Elixir's, not phoenix's.

So the rule is neither substring nor component-prefix but EXACT: the project
must define a module whose FULL NAME IS the (alias-expanded) written module.
That is Elixir's own semantics -- `Mod.f()` can reach only `Mod` -- and it
only narrows a fallback path, since a fully-qualified first-party call is
already caught by the `qualified_name in global_symbols` check above it.
"""
from pathlib import Path


def _analyze(tmp_path: Path, files: dict[str, str]):
    from hypergumbo_lang_common.elixir import analyze_elixir

    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return analyze_elixir(tmp_path)


def _calls_from(result, caller_suffix: str) -> list[str]:
    return [
        e.dst for e in result.edges
        if e.edge_type == "calls" and e.src.endswith(caller_suffix)
        and e.src != e.dst
    ]


class TestTheSuffixArtifact:
    """A project module whose name merely ENDS in the written module."""

    def test_logger_error_does_not_bind_a_project_error(
        self, tmp_path: Path,
    ) -> None:
        """The filed case: `Logger.` is a substring of `Phoenix.Logger.`."""
        result = _analyze(tmp_path, {
            "caller.ex": "defmodule Caller do\n"
                         "  def go do\n"
                         '    Logger.error("boom")\n'
                         "  end\nend\n",
            "phx_logger.ex": "defmodule Phoenix.Logger do\n"
                             "  def install, do: :ok\n"
                             "end\n",
            "template.ex": "defmodule Tmpl do\n"
                           "  def error(x), do: x\n"
                           "end\n",
        })
        dsts = _calls_from(result, ":Caller.go:function")

        assert "elixir:Logger:0-0:error:unresolved" in dsts
        assert not [d for d in dsts if "Tmpl" in d]

    def test_path_join_does_not_bind_a_project_join(
        self, tmp_path: Path,
    ) -> None:
        """`Path.` is a substring of `...StaticPath.path` on phoenix.

        On the corpus this bound `Path.join(project_path, ...)` to a Phoenix
        Channel's `join/3` inside an EEx template -- 112 edges.
        """
        result = _analyze(tmp_path, {
            "caller.ex": "defmodule Caller do\n"
                         "  def go(a, b) do\n"
                         "    Path.join(a, b)\n"
                         "  end\nend\n",
            "static.ex": "defmodule My.StaticPath do\n"
                         "  def path, do: :ok\n"
                         "end\n",
            "channel.ex": "defmodule MyChannel do\n"
                          "  def join(t, p, s), do: {:ok, s}\n"
                          "end\n",
        })
        dsts = _calls_from(result, ":Caller.go:function")

        assert "elixir:Path:0-0:join:unresolved" in dsts
        assert not [d for d in dsts if "MyChannel" in d]


class TestTheNamespacePrefix:
    """A component test would pass these; they are still wrong."""

    def test_mix_raise_does_not_bind_a_project_raise(
        self, tmp_path: Path,
    ) -> None:
        """The project owning `Mix.Tasks.*` says nothing about `Mix.raise/1`."""
        result = _analyze(tmp_path, {
            "caller.ex": "defmodule Caller do\n"
                         "  def go do\n"
                         '    Mix.raise("boom")\n'
                         "  end\nend\n",
            "task.ex": "defmodule Mix.Tasks.Local.Phx do\n"
                       "  def run(_), do: :ok\n"
                       "end\n",
            "ctrl.ex": "defmodule My.Controller do\n"
                       "  def raise(x), do: x\n"
                       "end\n",
        })
        dsts = _calls_from(result, ":Caller.go:function")

        assert "elixir:Mix:0-0:raise:unresolved" in dsts
        assert not [d for d in dsts if "My.Controller" in d]


class TestFirstPartyCallsSTILLBind:
    """P3's control: the rule must only narrow, never lose a real binding."""

    def test_an_alias_expanded_project_call_still_binds(
        self, tmp_path: Path,
    ) -> None:
        """`alias My.Deep.Scope` + `Scope.route_prefix()` is first-party.

        This is the case the rule must NOT break: the written module is a
        short alias, and its expansion IS a module the project defines.
        """
        result = _analyze(tmp_path, {
            "caller.ex": "defmodule Caller do\n"
                         "  alias My.Deep.Scope\n"
                         "  def go do\n"
                         "    Scope.route_prefix()\n"
                         "  end\nend\n",
            "scope.ex": "defmodule My.Deep.Scope do\n"
                        "  def route_prefix, do: :ok\n"
                        "end\n",
        })
        dsts = _calls_from(result, ":Caller.go:function")

        assert [d for d in dsts if "My.Deep.Scope.route_prefix" in d], dsts

    def test_the_multi_alias_brace_form_still_binds(self, tmp_path: Path) -> None:
        """`alias Phx.New.{Project}` — ordinary Elixir, and it was unread.

        The brace form puts the prefix under a `dot` node, so
        `_extract_alias_hints` found no `alias` child of `arguments` and
        registered NOTHING. That was invisible while the ownership gate was a
        substring test, because `"Project."` is a substring of
        `Phx.New.Project.ecto?` and the resolver bound these calls correctly
        by accident. An exact gate asks whether the project defines `Project`
        — it defines `Phx.New.Project` — so reading the directive is what
        keeps these edges. 44 real edges on phoenix-framework depend on it.
        """
        result = _analyze(tmp_path, {
            "caller.ex": "defmodule Caller do\n"
                         "  alias My.New.{Project, Other}\n"
                         "  def go(p) do\n"
                         "    Project.ecto?(p)\n"
                         "    Other.run(p)\n"
                         "  end\nend\n",
            "project.ex": "defmodule My.New.Project do\n"
                          "  def ecto?(p), do: p\n"
                          "end\n",
            "other.ex": "defmodule My.New.Other do\n"
                        "  def run(p), do: p\n"
                        "end\n",
        })
        dsts = _calls_from(result, ":Caller.go:function")

        assert [d for d in dsts if "My.New.Project.ecto?" in d], dsts
        assert [d for d in dsts if "My.New.Other.run" in d], dsts

    def test_an_alias_binds_the_FIRST_COMPONENT_not_the_whole_name(
        self, tmp_path: Path,
    ) -> None:
        """`alias Mix.Tasks.Phx.Gen` also makes `Gen.Context` meaningful.

        An alias binds a component, so `Gen.Context.build(...)` is a call to
        `Mix.Tasks.Phx.Gen.Context.build/2`. Looking the whole written name
        `Gen.Context` up in the hint map answers nothing; the expansion has to
        graft the remainder onto the aliased head. 29 real phoenix calls.
        """
        result = _analyze(tmp_path, {
            "caller.ex": "defmodule Caller do\n"
                         "  alias My.Tasks.Gen\n"
                         "  def go(a) do\n"
                         "    Gen.Context.build(a)\n"
                         "  end\nend\n",
            "ctx.ex": "defmodule My.Tasks.Gen.Context do\n"
                      "  def build(a), do: a\n"
                      "end\n",
        })
        dsts = _calls_from(result, ":Caller.go:function")

        assert [d for d in dsts if "My.Tasks.Gen.Context.build" in d], dsts

    def test_an_unaliased_nested_name_still_names_its_expansion(
        self, tmp_path: Path,
    ) -> None:
        """The same expansion reaches the unresolved fallback's module slot.

        `alias My.Tasks.Gen` + `Gen.Missing.run()` names a module the project
        does not define, so no first-party edge is right -- but the edge that
        IS emitted must say `My.Tasks.Gen.Missing`, not the local shorthand.
        """
        result = _analyze(tmp_path, {
            "caller.ex": "defmodule Caller do\n"
                         "  alias My.Tasks.Gen\n"
                         "  def go do\n"
                         "    Gen.Missing.run()\n"
                         "  end\nend\n",
            "gen.ex": "defmodule My.Tasks.Gen do\n  def noop, do: :ok\nend\n",
        })
        dsts = _calls_from(result, ":Caller.go:function")

        assert "elixir:My.Tasks.Gen.Missing:0-0:run:unresolved" in dsts

    def test_a_fully_qualified_project_call_still_binds(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(tmp_path, {
            "caller.ex": "defmodule Caller do\n"
                         "  def go do\n"
                         "    My.Deep.Helper.greet()\n"
                         "  end\nend\n",
            "helper.ex": "defmodule My.Deep.Helper do\n"
                         "  def greet, do: :ok\n"
                         "end\n",
        })
        dsts = _calls_from(result, ":Caller.go:function")

        assert [d for d in dsts if "My.Deep.Helper.greet" in d], dsts

    def test_an_exactly_named_project_module_still_reaches_the_resolver(
        self, tmp_path: Path,
    ) -> None:
        """The narrowed gate still OPENS when the project owns the module.

        `Helper` is defined exactly, and its function is multi-clause, so the
        qualified-name check above does not settle it and the resolver
        fallback is what must bind it.
        """
        result = _analyze(tmp_path, {
            "caller.ex": "defmodule Caller do\n"
                         "  def go do\n"
                         "    Helper.pick(1)\n"
                         "  end\nend\n",
            "helper.ex": "defmodule Helper do\n"
                         "  def pick(1), do: :one\n"
                         "  def pick(_), do: :other\n"
                         "end\n",
        })
        dsts = _calls_from(result, ":Caller.go:function")

        assert [d for d in dsts if "Helper.pick" in d], dsts


class TestAliasFormsThatNameNoModuleStatically:
    """Directives the brace-form reader must step over without guessing.

    Each is ordinary Elixir whose target is not a literal module path, so no
    hint can be registered honestly -- and registering a wrong one is worse
    than registering none, because a module hint KILLS the fallback paths that
    would otherwise still resolve the call.
    """

    def test_an_attribute_alias_registers_no_hint(self, tmp_path: Path) -> None:
        """`alias @base` — the argument is a unary_operator, not a dot."""
        result = _analyze(tmp_path, {
            "caller.ex": "defmodule Caller do\n"
                         "  @base My.Thing\n"
                         "  alias @base\n"
                         "  def go do\n"
                         "    String.upcase(\"x\")\n"
                         "  end\nend\n",
        })
        dsts = _calls_from(result, ":Caller.go:function")

        assert "elixir:String:0-0:upcase:unresolved" in dsts

    def test_a_dot_alias_with_no_tuple_registers_no_hint(
        self, tmp_path: Path,
    ) -> None:
        """`alias __MODULE__.Sub` — a dot, but its tail is an alias not a tuple."""
        result = _analyze(tmp_path, {
            "caller.ex": "defmodule Caller do\n"
                         "  alias __MODULE__.Sub\n"
                         "  def go do\n"
                         "    String.upcase(\"x\")\n"
                         "  end\nend\n",
        })
        dsts = _calls_from(result, ":Caller.go:function")

        assert "elixir:String:0-0:upcase:unresolved" in dsts


class TestModulesWithNoProjectPresenceAreUnchanged:
    """`Macro` / `Enum` / `String` were already refused; keep them refused."""

    def test_an_entirely_external_module_still_goes_unresolved(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(tmp_path, {
            "caller.ex": "defmodule Caller do\n"
                         "  def go(s) do\n"
                         "    String.upcase(s)\n"
                         "  end\nend\n",
        })
        dsts = _calls_from(result, ":Caller.go:function")

        assert "elixir:String:0-0:upcase:unresolved" in dsts
