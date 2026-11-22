# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/profile.py
"""Ruby CLI frameworks that were not detected, and classes marked by what they adopt.

Two detection gaps (WI-movur triage, group ruby):

- dry-cli's gem is ``dry-cli`` and its require is ``dry/cli``. The demote
  phase looks for a production import of the framework's gem name, found
  none, and moved ``cli-ruby`` to ``dev_frameworks``: on a dry-cli project
  the cli-ruby rows never loaded. ``IMPORT_OVERRIDES`` now maps the gem to
  its require.
- Commander (``gem 'commander'``) was not one of cli-ruby's gems at all.

And two rows with ``adopts:`` (``meta["included_modules"]``, which Ruby
writes for ``include`` / ``extend``): the GLI app class (``extend
GLI::App``) and a Commander program (``include Commander::Methods``) are
commands; a class that includes ``Sidekiq::Worker`` / ``Sidekiq::Job`` is a
background task, as is one including ``Sidekiq::IterableJob``
(rails.yaml had only the ``base_class`` form, which a Sidekiq worker never
matches: nothing is inherited).

Every test runs the real Ruby analyzer; detection runs ``detect_profile``
and ``refine_frameworks`` on its output, as the pipeline does.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Symbol
from hypergumbo_core.profile import detect_profile, refine_frameworks
from hypergumbo_lang_mainstream.ruby import analyze_ruby


def _write(tmp_path: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def _frameworks(tmp_path: Path, files: dict[str, str]) -> tuple[list[str], list[str]]:
    _write(tmp_path, files)
    result = analyze_ruby(tmp_path)
    symbols = list(result.symbols)
    for symbol in symbols:
        if Path(symbol.path).is_absolute():
            symbol.path = Path(symbol.path).relative_to(tmp_path).as_posix()
    profile = refine_frameworks(detect_profile(tmp_path), list(result.edges), symbols)
    return list(profile.frameworks), list(profile.dev_frameworks)


def _enriched(tmp_path: Path, source: str, framework: str) -> dict[str, Symbol]:
    _write(tmp_path, {"lib/app.rb": source})
    symbols = list(analyze_ruby(tmp_path).symbols)
    clear_pattern_cache()
    enrich_symbols(symbols, {framework})
    return {s.name: s for s in symbols}


def _concepts(symbol: Symbol) -> set[str]:
    return {
        c["concept"] for c in (symbol.meta or {}).get("concepts", []) if isinstance(c, dict)
    }


DRY_CLI_LIB = """\
require 'dry/cli'

module Foo
  class Version < Dry::CLI::Command
    def call(*); end
  end
end
"""


class TestDetection:
    def test_dry_cli_is_confirmed_by_its_require(self, tmp_path: Path) -> None:
        frameworks, dev = _frameworks(tmp_path, {
            "Gemfile": "source 'https://rubygems.org'\ngem 'dry-cli'\n",
            "lib/foo.rb": DRY_CLI_LIB,
        })
        assert "cli-ruby" in frameworks
        assert "cli-ruby" not in dev

    def test_commander_is_a_ruby_cli_framework(self, tmp_path: Path) -> None:
        frameworks, _ = _frameworks(tmp_path, {
            "Gemfile": "source 'https://rubygems.org'\ngem 'commander'\n",
            "bin/greet.rb": (
                "require 'commander'\n\nclass GreetCLI\n"
                "  include Commander::Methods\n  def run; end\nend\n"
            ),
        })
        assert "cli-ruby" in frameworks

    def test_a_project_without_those_gems_gets_no_cli_framework(self, tmp_path: Path) -> None:
        frameworks, dev = _frameworks(tmp_path, {
            "Gemfile": "source 'https://rubygems.org'\ngem 'rake'\n",
            "lib/foo.rb": "require 'json'\n\nmodule Foo\n  def self.go; end\nend\n",
        })
        assert "cli-ruby" not in frameworks + dev


class TestAdoptedModules:
    def test_the_gli_and_commander_app_classes_are_commands(self, tmp_path: Path) -> None:
        by_name = _enriched(tmp_path, """\
class App
  extend GLI::App
end

class GreetCLI
  include Commander::Methods
end

# control: another module adopted
class Plain
  include Comparable
end
""", "cli-ruby")
        assert "command" in _concepts(by_name["App"])
        assert "command" in _concepts(by_name["GreetCLI"])
        assert not _concepts(by_name["Plain"])

    def test_a_sidekiq_worker_is_a_task(self, tmp_path: Path) -> None:
        by_name = _enriched(tmp_path, """\
class MailWorker
  include Sidekiq::Worker
  def perform(id); end
end

class NewStyleJob
  include Sidekiq::Job
  def perform; end
end

class SweepJob
  include Sidekiq::IterableJob
  def each_iteration(item); end
end

# control: a module whose name starts the same
class Batcher
  include Sidekiq::WorkerExtras
end
""", "rails")
        assert "task" in _concepts(by_name["MailWorker"])
        assert "task" in _concepts(by_name["NewStyleJob"])
        assert "task" in _concepts(by_name["SweepJob"])
        assert not _concepts(by_name["Batcher"])
