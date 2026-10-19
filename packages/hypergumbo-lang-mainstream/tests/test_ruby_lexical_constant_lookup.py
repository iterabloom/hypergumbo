# SPDX-License-Identifier: AGPL-3.0-or-later
"""A constant is looked up through the call site's lexical nesting (WI-gomup).

Ruby resolves ``Orchestrator`` inside ``module TestData`` by trying
``Module.nesting`` first (``TestData::Orchestrator``), then the top level. The
analyzer tried only the name as written, so a target declared compactly
(``class TestData::Orchestrator``), which is registered under its full name,
was never found and the in-repo call was reported external (chatwoot
``lib/test_data.rb``). A class nested by blocks (``module Outer; class
Helper``) already resolved, because it is also registered under its short
name.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.ruby import analyze_ruby


def _calls(tmp_path: Path, files: dict[str, str]) -> dict[str, list[str]]:
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    result = analyze_ruby(tmp_path)
    names = {s.id: s.name for s in result.symbols}
    out: dict[str, list[str]] = {}
    for e in result.edges:
        if e.edge_type in ("calls", "instantiates") and e.src in names:
            out.setdefault(names[e.src], []).append(names.get(e.dst, e.dst))
    return out


def test_a_constant_resolves_through_the_enclosing_module(tmp_path: Path) -> None:
    calls = _calls(tmp_path, {
        "lib/test_data.rb": (
            "module TestData\n"
            "  def self.generate\n"
            "    Orchestrator.call\n"
            "    Builder.new\n"
            "  end\n"
            "end\n"
        ),
        "lib/test_data/orchestrator.rb": (
            "class TestData::Orchestrator\n"
            "  def self.call\n"
            "  end\n"
            "end\n"
        ),
        "lib/test_data/builder.rb": (
            "class TestData::Builder\n"
            "  def initialize\n"
            "  end\n"
            "end\n"
        ),
    })
    assert "TestData::Orchestrator.call" in calls["TestData.generate"]
    assert "TestData::Builder#initialize" in calls["TestData.generate"]


def test_the_innermost_nesting_wins_and_a_compact_class_adds_one_level(tmp_path: Path) -> None:
    """``module A; module B; X.run`` tries ``A::B::X`` before ``A::X``. Inside a
    compact ``class A::C`` the nesting is ``[A::C]`` only -- Ruby does not add
    ``A`` -- so ``Y.run`` there does NOT find ``A::Y``. (Block-nested
    declarations are registered under their short names, so the targets here
    are compact, the shape the lexical lookup is for.)"""
    calls = _calls(tmp_path, {
        "a.rb": (
            "module A\n"
            "  module B\n"
            "    def self.go\n      X.run\n    end\n"
            "  end\n"
            "end\n"
        ),
        "x.rb": (
            "class A::X\n  def self.run\n  end\nend\n"
            "class A::B::X\n  def self.run\n  end\nend\n"
            "class A::Y\n  def self.run\n  end\nend\n"
        ),
        "c.rb": (
            "class A::C\n"
            "  def self.go\n    Y.run\n  end\n"
            "end\n"
        ),
    })
    assert calls["B.go"] == ["A::B::X.run"]
    assert "A::Y.run" not in calls.get("A::C.go", [])
