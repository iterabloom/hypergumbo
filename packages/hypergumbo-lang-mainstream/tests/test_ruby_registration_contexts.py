# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py
"""A class-body call that hands a framework a LOCAL method or class (WI-movur).

``rescue_from NotFound, with: :handle_not_found``, ``field :full_name``,
``subcommand "remote", Remote``, ``register "version", Version``: each names,
by a symbol or a constant, code of the repository that the framework calls
later. Nothing in the repository calls it, so without a record it reads as
dead. The rows that were written for these calls keyed them on
``decorator:``, which Ruby never fills, and were deleted.

The analyzer now records a registration usage context for such a call: kind
``call``, the method name as ``context_name``, the argument's position
(``args[i]`` / ``kwargs[name]``) and the local method or class it names as
``symbol_ref``. It records it only for a name some shipped ``usage:`` row
names (``framework_patterns.usage_row_names_call``), so the rows are the only
list of names; and only when the argument resolves to a symbol of the
repository. Every test runs the real analyzer on source; the row tests then
give its output to the shipped rows (``enrich_symbols``).
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Symbol, UsageContext
from hypergumbo_lang_mainstream.ruby import analyze_ruby


def _analyse(
    tmp_path: Path, files: dict[str, str],
) -> tuple[dict[str, Symbol], list[UsageContext], list[Symbol]]:
    for relative, text in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    result = analyze_ruby(tmp_path)
    symbols = list(result.symbols)
    by_name = {s.name: s for s in symbols}
    return by_name, list(result.usage_contexts), symbols


def _contexts(contexts: list[UsageContext], name: str) -> list[UsageContext]:
    return [c for c in contexts if c.context_name == name]


def _concepts(symbol: Symbol) -> set[tuple[str, str]]:
    return {
        (c["concept"], c.get("framework", ""))
        for c in (symbol.meta or {}).get("concepts", [])
        if isinstance(c, dict)
    }


def _enrich(symbols: list[Symbol], contexts: list[UsageContext], *frameworks: str) -> None:
    clear_pattern_cache()
    enrich_symbols(symbols, set(frameworks), contexts)


GRAPE = """\
module Shop
  class API < Grape::API
    helpers do
      def handle_not_found(e)
        error!('not found', 404)
      end

      def ping
        nil
      end
    end

    rescue_from NotFound, with: :handle_not_found
    rescue_from Gone, :with => :handle_not_found
    # control: a handler the repository does not define
    rescue_from Teapot, with: :no_such_method
    # control: the same shape under a name no usage row has
    handle_with Boom, with: :handle_not_found

    get :ping do
      { ok: true }
    end

    def self.lookup
      # control: inside a method it is ordinary code
      rescue_from Late, with: :handle_not_found
    end
  end
end
"""


class TestRescueFrom:
    def test_with_symbol_names_the_local_handler(self, tmp_path: Path) -> None:
        by_name, contexts, _ = _analyse(tmp_path, {"app/api/shop.rb": GRAPE})
        handler = by_name["API#handle_not_found"]
        found = _contexts(contexts, "rescue_from")
        assert [(c.position, c.symbol_ref, c.span.start_line) for c in found] == [
            ("kwargs[with]", handler.id, 13), ("kwargs[with]", handler.id, 14),
        ]
        assert found[0].kind == "call"
        assert found[0].metadata["scope"] == "Shop::API"
        assert found[0].metadata["scope_bases"] == ["Grape::API"]

    def test_a_name_no_row_has_is_not_recorded(self, tmp_path: Path) -> None:
        _, contexts, _ = _analyse(tmp_path, {"app/api/shop.rb": GRAPE})
        assert any(c.context_name == "rescue_from" for c in contexts)  # reach
        assert _contexts(contexts, "handle_with") == []

    def test_route_calls_stay_with_the_route_extractor(self, tmp_path: Path) -> None:
        # ``get :ping`` names a local method too; the route extractor owns ``get``.
        by_name, contexts, _ = _analyse(tmp_path, {"app/api/shop.rb": GRAPE})
        assert "API#ping" in by_name  # reach
        assert all(c.symbol_ref is None for c in _contexts(contexts, "get"))

    def test_a_dsl_the_repository_defines_is_not_recorded(self, tmp_path: Path) -> None:
        source = """\
class Base
  def self.rescue_from(*args)
    args
  end
end

class Child < Base
  rescue_from Oops, with: :recover
  def recover; end
end
"""
        by_name, contexts, _ = _analyse(tmp_path, {"lib/child.rb": source})
        assert "Child#recover" in by_name  # reach
        assert _contexts(contexts, "rescue_from") == []

    def test_rails_and_grape_rows_mark_the_handler(self, tmp_path: Path) -> None:
        source = """\
class ApplicationController < ActionController::Base
  rescue_from ActiveRecord::RecordNotFound, with: :render_not_found
  # control: a callback is not an entry in this change (no row names it);
  # its target keeps the dispatches_to edge _extract_rails_callbacks writes
  before_action :authenticate_user, only: :show

  def show; end

  private

  def render_not_found
    head :not_found
  end

  def authenticate_user
    true
  end
end
"""
        by_name, contexts, symbols = _analyse(
            tmp_path, {"app/controllers/application_controller.rb": source},
        )
        _enrich(symbols, contexts, "rails")
        assert ("error_handler", "rails") in _concepts(
            by_name["ApplicationController#render_not_found"]
        )
        callback = by_name["ApplicationController#authenticate_user"]
        assert not _concepts(callback)
        assert not _concepts(by_name["ApplicationController#show"])
        assert _contexts(contexts, "before_action") == []
        edges = analyze_ruby(tmp_path).edges
        assert any(
            e.edge_type == "dispatches_to" and e.dst == callback.id for e in edges
        )
        by_name, contexts, symbols = _analyse(tmp_path / "g", {"app/api/shop.rb": GRAPE})
        _enrich(symbols, contexts, "grape")
        assert ("error_handler", "grape") in _concepts(by_name["API#handle_not_found"])


GRAPHQL = """\
module Types
  class UserType < BaseObject
    field :full_name, String, null: false
    def full_name
      "x"
    end

    # no method: graphql-ruby reads the object's attribute
    field :email, String

    field :posts_count, Integer, resolver_method: :count_posts
    def count_posts
      0
    end

    field :search, resolver: Resolvers::Search

    # control: a local TYPE as the second argument is not a resolver
    field :friend, UserType

    def helper_not_a_field
      nil
    end
  end
end

module Resolvers
  class Search < GraphQL::Schema::Resolver
    def resolve
      []
    end
  end
end
"""


class TestGraphqlField:
    def test_field_names_its_resolver(self, tmp_path: Path) -> None:
        by_name, contexts, _ = _analyse(tmp_path, {"app/graphql/types.rb": GRAPHQL})
        got = {(c.position, c.symbol_ref) for c in _contexts(contexts, "field")}
        assert got == {
            ("args[0]", by_name["UserType#full_name"].id),
            ("kwargs[resolver_method]", by_name["UserType#count_posts"].id),
            ("kwargs[resolver]", by_name["Search"].id),
        }

    def test_rows_mark_resolvers_and_not_helpers(self, tmp_path: Path) -> None:
        by_name, contexts, symbols = _analyse(tmp_path, {"app/graphql/types.rb": GRAPHQL})
        _enrich(symbols, contexts, "graphql-ruby")
        for name in ("UserType#full_name", "UserType#count_posts"):
            assert ("graphql_resolver", "graphql-ruby") in _concepts(by_name[name]), name
        assert not _concepts(by_name["UserType#helper_not_a_field"])


THOR = """\
module MyCli
  class Base < Thor
  end

  class Accounts < Base
    desc "create NAME", "create"
    def create(name); end
  end

  class Generator < Thor::Group
    def step; end
  end

  class CLI < Thor
    subcommand "accounts", Accounts
    register(Generator, "generate", "generate NAME", "Generate")
  end
end
"""

DRY_CLI = """\
module Foo
  module Commands
    extend Dry::CLI::Registry

    class Version < Dry::CLI::Command
      def call(*); end
    end

    module Generate
      class Config < Dry::CLI::Command
        def call(*); end
      end
    end

    register 'version', Version, aliases: ['v']
    register 'generate' do |prefix|
      prefix.register 'config', Generate::Config
    end
    # controls: a receiver that is not the block's builder (a constant, a
    # local variable), and a splat argument (not one argument)
    Other.register 'x', Version
    registry = Other
    registry.register 'y', Version
    register 'z', *COMMANDS
  end
end
"""


class TestCommandRegistration:
    def test_thor_subcommand_and_register_name_the_class(self, tmp_path: Path) -> None:
        by_name, contexts, symbols = _analyse(tmp_path, {"lib/cli.rb": THOR})
        assert {(c.context_name, c.position, c.symbol_ref) for c in contexts} >= {
            ("subcommand", "args[1]", by_name["Accounts"].id),
            ("register", "args[0]", by_name["Generator"].id),
        }
        _enrich(symbols, contexts, "cli-ruby")
        # ``Accounts < Base`` has no Thor base row match; the registration marks it.
        assert ("command", "cli-ruby") in _concepts(by_name["Accounts"])
        assert ("command", "cli-ruby") in _concepts(by_name["Generator"])

    def test_dry_cli_register_including_the_block_builder(self, tmp_path: Path) -> None:
        by_name, contexts, _ = _analyse(tmp_path, {"lib/foo/cli.rb": DRY_CLI})
        found = [(c.position, c.symbol_ref, c.metadata.get("receiver"))
                 for c in _contexts(contexts, "register")]
        assert found == [
            ("args[1]", by_name["Version"].id, None),
            ("args[1]", by_name["Config"].id, "prefix"),
        ]


PADRINO = """\
module MyExtension
  def self.registered(app); end
end

module MyHelpers
  def greet; end
end

class AppError < StandardError; end

class App < Padrino::Application
  register MyExtension
  helpers MyHelpers

  # control: the exception class is not the handler (the block is)
  error AppError do
    'boom'
  end

  error 404 do
    'missing'
  end

  # control: a template name that happens to name a local method
  render :greet_page

  def greet_page; end
end
"""

RODA = """\
module LocalPlugin
end

class App < Roda
  # controls: a library plugin's name and a template's name that happen to
  # name local methods
  plugin :render
  plugin LocalPlugin

  route do |r|
    view :index
  end

  def render; end
  def index; end
end
"""


class TestEveryRowTheGateFeeds:
    def test_padrino_marks_registered_modules_not_exception_classes(
        self, tmp_path: Path,
    ) -> None:
        by_name, contexts, symbols = _analyse(tmp_path, {"app/app.rb": PADRINO})
        assert "AppError" in by_name and "App#greet_page" in by_name  # reach
        _enrich(symbols, contexts, "padrino")
        assert "helper" in {c for c, _ in _concepts(by_name["MyExtension"])}
        assert "helper" in {c for c, _ in _concepts(by_name["MyHelpers"])}
        assert not _concepts(by_name["AppError"])
        assert not _concepts(by_name["App#greet_page"])
        assert _contexts(contexts, "error") == []
        assert _contexts(contexts, "render") == []

    def test_roda_plugin_and_view_names_are_not_handlers(self, tmp_path: Path) -> None:
        by_name, contexts, symbols = _analyse(tmp_path, {"app.rb": RODA})
        assert "App#render" in by_name and "App#index" in by_name  # reach
        _enrich(symbols, contexts, "roda")
        assert not _concepts(by_name["App#render"])
        assert not _concepts(by_name["App#index"])
        assert not _concepts(by_name["LocalPlugin"])
        assert [c.context_name for c in contexts if c.symbol_ref] == []

    def test_every_ruby_row_a_bare_call_can_reach_names_a_position(self) -> None:
        """The extractor records a bare (or builder-collapsed) method name,
        so a Ruby usage row whose name pattern has no receiver (no ``.``) can
        receive its contexts. Each such row must say which argument is the
        handler; one whose handler is a block, or whose arguments name no
        code, says so with a position no context has."""
        import yaml

        from hypergumbo_core.framework_patterns import get_frameworks_dir

        unpositioned, checked = [], 0
        for path in sorted(get_frameworks_dir().glob("*.yaml")):
            data = yaml.safe_load(path.read_text())
            language = data.get("language")
            languages = [language] if isinstance(language, str) else list(language or [])
            if languages and not {"ruby", "multi"} & set(languages):
                continue
            for row in data.get("patterns") or []:
                usage = row.get("usage") or {}
                name = usage.get("name")
                if name and "\\." not in name:
                    checked += 1
                    if not usage.get("position"):
                        unpositioned.append(f"{path.name}: {name}")
        assert checked > 20  # reach: grape, padrino, rails, roda, ... were read
        assert unpositioned == []
