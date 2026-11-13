# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kafor: a qualified call binds a project symbol only by its exact name.

`_handle_dot_call` binds `Mod.f(...)` to `Mod.f`, or to `Expanded.f` after
alias expansion, and to nothing else. It never binds by BARE NAME (`f`) behind
a test of whether the project "owns" `Mod`, because a bare-name bind lets
`Logger.error(...)` land on any project function named `error`, and no
ownership test short of an exact one is safe:

  SUFFIX ARTIFACT, matching mid-component. `Logger.` is a substring of
  `Phoenix.Logger.install`; `Path.` of `...StaticPath.path`; `Supervisor.` of
  `Phoenix.Endpoint.Supervisor.init`. A component-aware test rejects these.

  NAMESPACE PREFIX, a real component-boundary match that is still wrong. A
  project that defines modules under `Mix.` matches `Mix.Tasks.Local.Phx` on
  any component test -- and `Mix.raise/1` is still Elixir's, not the
  project's.

That is Elixir's own semantics -- `Mod.f()` can reach only `Mod`. A call that
does not bind exactly falls to the unresolved edge, which names the
(alias-expanded) module so the catalogue and the linkers can match it.
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
        """`Logger.` is a substring of `Phoenix.Logger.`."""
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

        A bare-name bind would land `Path.join(project_path, ...)` on a
        project Channel's `join/3`.
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
    """Control: refusing bare-name binds must never lose a real binding."""

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
        """`alias Phx.New.{Project}` — ordinary Elixir, read from its tuple.

        The brace form puts the prefix under a `dot` node, so there is no
        `alias` child of `arguments`; `_extract_alias_hints` must read each
        member from the tuple or register NOTHING. Resolution is exact: the
        project defines `Phx.New.Project`, not `Project`, so without these
        hints `Project.ecto?` goes unresolved.
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
        graft the remainder onto the aliased head.
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
        """A module the project defines exactly still binds.

        `Helper` is defined exactly and `pick` is multi-clause; the exact
        `Helper.pick` qualified-name lookup is what binds the call, with no
        bare-name fallback involved.
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
    than registering none, because a module hint REWRITES the module every
    matching call names, sending it to the wrong first-party symbol or the
    wrong unresolved module.
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
    """`Macro` / `Enum` / `String`, with no project presence, stay unresolved."""

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
