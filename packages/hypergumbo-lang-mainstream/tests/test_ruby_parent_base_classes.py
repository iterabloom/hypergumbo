# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py
"""A Ruby method carries its class's bases, so a row can ask for them (WI-movur).

Thor runs every public instance method of a ``Thor`` subclass as a command;
dry-cli runs ``#call`` of a ``Dry::CLI::Command``. A row says that with
``parent_base_class:`` (plus ``method_name:``), which reads
``meta["parent_base_classes"]``. Python, Java and Objective-C wrote the key;
Ruby did not, so no such row could match a Ruby method (WI-marun's gap, in
Ruby).

The key is written on the PUBLIC instance methods a class declares in its
own body: the members a framework that dispatches by method name reaches.
Not on a method after ``private`` / ``protected`` (or named by ``private
:x`` / ``private def x``), not on a ``def self.x``, not on a method declared
inside a block of the class body (Thor's ``no_commands do ... end``, a
concern's ``included do``). Every test runs the real analyzer.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.ruby import analyze_ruby


def _symbols(tmp_path: Path, source: str) -> tuple[dict[str, Symbol], list[Symbol]]:
    target = tmp_path / "lib" / "cli.rb"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    result = analyze_ruby(tmp_path)
    symbols = list(result.symbols)
    clear_pattern_cache()
    enrich_symbols(symbols, {"cli-ruby"}, list(result.usage_contexts))
    return {s.name: s for s in symbols}, symbols


def _bases(symbol: Symbol) -> object:
    return (symbol.meta or {}).get("parent_base_classes")


def _concepts(symbol: Symbol) -> set[str]:
    return {
        c["concept"] for c in (symbol.meta or {}).get("concepts", []) if isinstance(c, dict)
    }


THOR = """\
class CLI < Thor
  desc "hello NAME", "say hello"
  def hello(name)
    greeting(name)
  end

  def undocumented; end

  def self.exit_on_failure?
    true
  end

  no_commands do
    def greeting(name)
      name
    end
  end

  private def inline_private; end

  def later_private; end
  private :later_private

  protected

  def guarded; end

  private

  def secret_helper; end

  public

  def back_in_public; end
end

class Report
  def build; end
end
"""


class TestThorCommands:
    def test_public_instance_methods_carry_the_class_bases(self, tmp_path: Path) -> None:
        by_name, _ = _symbols(tmp_path, THOR)
        for name in ("CLI#hello", "CLI#undocumented", "CLI#back_in_public"):
            assert _bases(by_name[name]) == ["Thor"], name

    def test_others_do_not(self, tmp_path: Path) -> None:
        by_name, _ = _symbols(tmp_path, THOR)
        for name in (
            "CLI.exit_on_failure?", "CLI#greeting", "CLI#inline_private",
            "CLI#later_private", "CLI#guarded", "CLI#secret_helper",
            "Report#build",
        ):
            assert name in by_name, name  # reach
            assert _bases(by_name[name]) is None, name

    def test_the_thor_row_marks_commands_only(self, tmp_path: Path) -> None:
        by_name, _ = _symbols(tmp_path, THOR)
        assert "command" in _concepts(by_name["CLI#hello"])
        assert "command" in _concepts(by_name["CLI#back_in_public"])
        assert not _concepts(by_name["CLI#secret_helper"])
        assert not _concepts(by_name["CLI#greeting"])
        assert not _concepts(by_name["Report#build"])


DRY_CLI = """\
module Foo
  class Version < Dry::CLI::Command
    def call(*)
      format_version
    end

    def format_version
      "1.0"
    end
  end
end
"""


class TestDryCliCall:
    def test_call_is_the_command(self, tmp_path: Path) -> None:
        by_name, _ = _symbols(tmp_path, DRY_CLI)
        assert _bases(by_name["Version#call"]) == ["Dry::CLI::Command"]
        assert "command" in _concepts(by_name["Version#call"])
        # control: a public helper of the command class is not a command
        assert _bases(by_name["Version#format_version"]) == ["Dry::CLI::Command"]
        assert not _concepts(by_name["Version#format_version"])
