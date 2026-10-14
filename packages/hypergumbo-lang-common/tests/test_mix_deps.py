# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Mix (mix.exs / mix.lock) dependency manifest reader (WI-juzaj)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_common.mix_deps import (
    _strip_comments,
    parse_mix_dependencies,
    parse_mix_exs,
    parse_mix_lock,
)

MIX_EXS = '''defmodule Shop.MixProject do
  use Mix.Project

  def project do
    [app: :shop, version: "0.1.0", deps: deps()]
  end

  # {:commented_out, "~> 1.0"}
  defp deps do
    [
      {:phoenix, "~> 1.7.14"},
      {:phoenix_live_view, "~> 1.0"},  # trailing comment
      {:credo, "~> 1.7", only: [:dev, :test], runtime: false},
      {:esbuild, "~> 0.8", runtime: Mix.env() == :dev},
      {:heroicons,
       github: "tailwindlabs/heroicons#main",
       tag: "v2.1.1",
       app: false},
      {:shop_core, path: "../shop_core"},
      {:shop_web, in_umbrella: true},
      {:weird, "]"}
    ]
  end
end
'''

MIX_LOCK = '''%{
  "phoenix": {:hex, :phoenix, "1.7.14", "abc", [:mix], [{:plug, "~> 1.14", [hex: :plug]}], "hexpm", "def"},
  "plug": {:hex, :plug, "1.16.1", "abc", [:mix], [], "hexpm", "def"},
  "decimal": {:hex, :decimal, "2.1.1", "abc", [:mix], [], "hexpm", "def"},
  "heroicons": {:git, "https://github.com/tailwindlabs/heroicons.git", "88ab", [tag: "v2.1.1"]},
}
'''


class TestStripComments:
    def test_keeps_hash_in_string(self) -> None:
        assert _strip_comments('x = "a#b" # c\ny') == 'x = "a#b" \ny'

    def test_escaped_quote_in_string(self) -> None:
        assert _strip_comments('"a\\"#b" # c') == '"a\\"#b" '


class TestParseMixExs:
    def test_deps_function(self) -> None:
        direct, workspace, own = parse_mix_exs(MIX_EXS)
        assert direct == {
            "phoenix", "phoenix_live_view", "credo", "esbuild", "heroicons", "weird",
        }
        assert workspace == {"shop_core", "shop_web"}
        assert own == {"shop"}

    def test_commented_dep_ignored(self) -> None:
        direct, _, _ = parse_mix_exs(MIX_EXS)
        assert "commented_out" not in direct

    def test_inline_deps_keyword_and_one_liner(self) -> None:
        src = 'def project, do: [app: :x, deps: [{:jason, "~> 1.4"}, {:a, ">= 0"}]]\n'
        direct, _, own = parse_mix_exs(src)
        assert direct == {"jason", "a"}
        assert own == {"x"}

    def test_defp_deps_do_keyword_form(self) -> None:
        src = 'defp deps, do: [{:oban, "~> 2.18"}]\n'
        assert parse_mix_exs(src)[0] == {"oban"}

    def test_no_deps(self) -> None:
        assert parse_mix_exs("defmodule X do\nend\n") == (set(), set(), set())

    def test_unclosed_list_is_ignored(self) -> None:
        assert parse_mix_exs('defp deps do\n  [{:a, "1"}\n')[0] == set()

    def test_deps_fn_without_bracket(self) -> None:
        assert parse_mix_exs("defp deps do\n  base()\nend\n")[0] == set()

    def test_escaped_quote_inside_dep_string(self) -> None:
        src = 'defp deps do\n  [{:a, "x\\"]"}, {:b, "1"}]\nend\n'
        assert parse_mix_exs(src)[0] == {"a", "b"}

    def test_non_atom_tuple_skipped(self) -> None:
        src = 'defp deps do\n  [{"str", "1"}, {:ok, "1"}]\nend\n'
        assert parse_mix_exs(src)[0] == {"ok"}


class TestParseMixLock:
    def test_keys(self) -> None:
        assert parse_mix_lock(MIX_LOCK) == {"phoenix", "plug", "decimal", "heroicons"}


class TestParseMixDependencies:
    def test_direct_transitive_and_workspace(self, tmp_path: Path) -> None:
        (tmp_path / "mix.exs").write_text(MIX_EXS)
        (tmp_path / "mix.lock").write_text(MIX_LOCK)
        m = parse_mix_dependencies(tmp_path)
        hexes = m.scoped["hex"]
        assert hexes["phoenix"] == {"direct": True}
        assert hexes["heroicons"] == {"direct": True}
        assert hexes["plug"] == {"direct": False}
        assert hexes["decimal"] == {"direct": False}
        for name in ("shop", "shop_core", "shop_web"):
            assert name not in hexes
        assert m.entries == {}

    def test_umbrella_and_deps_dir_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "mix.exs").write_text(
            'defp deps do\n  [{:shop_web, in_umbrella: true}]\nend\n'
        )
        app = tmp_path / "apps" / "shop_web"
        app.mkdir(parents=True)
        (app / "mix.exs").write_text(
            'def project, do: [app: :shop_web]\ndefp deps do\n  [{:jason, "~> 1.4"}]\nend\n'
        )
        # A fetched dependency's own mix.exs must not declare deps for us.
        dep = tmp_path / "deps" / "jason"
        dep.mkdir(parents=True)
        (dep / "mix.exs").write_text('defp deps do\n  [{:decimal, "~> 2.0"}]\nend\n')
        (tmp_path / "mix.lock").write_text('%{\n  "jason": {:hex, :jason, "1.4.4"},\n}\n')
        m = parse_mix_dependencies(tmp_path)
        assert m.scoped["hex"] == {"jason": {"direct": True}}

    def test_no_mix_project(self, tmp_path: Path) -> None:
        m = parse_mix_dependencies(tmp_path)
        assert m.scoped == {} and m.entries == {}

    def test_unreadable_files_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "mix.exs").write_text(MIX_EXS)
        (tmp_path / "mix.lock").write_text(MIX_LOCK)
        with patch.object(Path, "read_text", side_effect=OSError("denied")):
            m = parse_mix_dependencies(tmp_path)
        assert m.scoped == {}


class TestElixirBoundaryDirectnessEndToEnd:
    def test_survey_stamps_elixir_boundaries(self, tmp_path: Path) -> None:
        """Through the real CLI pipeline: declared and locked Hex packages get
        directness + third_party; stdlib and in-repo modules get no guess."""
        (tmp_path / "mix.exs").write_text(MIX_EXS)
        (tmp_path / "mix.lock").write_text(MIX_LOCK)
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "shop.ex").write_text(
            "defmodule Shop.Page do\n"
            "  def show(conn) do\n"
            "    Phoenix.LiveView.push_event(conn, \"x\", %{})\n"
            "    Plug.Conn.send_resp(conn, 200, \"ok\")\n"
            "    Decimal.new(1)\n"
            "    Enum.map([1], fn x -> x end)\n"
            "    Shop.Missing.thing()\n"
            "  end\n"
            "end\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out,
                         include_sketch_precomputed=False)
        data = json.loads(out.read_text())
        boundary = {
            n["display_label"].split(":")[1]: n.get("meta") or {}
            for n in data["nodes"]
            if n.get("language") == "elixir"
            and (n.get("meta") or {}).get("external_boundary")
        }
        assert boundary["Phoenix.LiveView"]["directness"] == "direct"
        assert boundary["Phoenix.LiveView"]["ecosystem"] == "third_party"
        assert boundary["Plug.Conn"]["directness"] == "transitive"
        assert boundary["Decimal"]["directness"] == "transitive"
        assert "directness" not in boundary["Enum"]
        assert "directness" not in boundary["Shop.Missing"]
