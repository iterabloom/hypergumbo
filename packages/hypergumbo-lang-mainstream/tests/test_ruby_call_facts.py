# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py
"""What the Ruby analyzer binds itself and what it reports (ADR-0064, batch 5b).

The analyzer binds a call only on the evidence Ruby's own lookup starts from,
in hand in the file:

* a send to ``self`` (``self.m``, ``m(x)``, a bare ``m`` that is not a local)
  whose ``m`` the enclosing class declares in THIS file (``own_type``); from a
  top-level method, the file's own top-level ``def`` when no other file
  defines one (``same_file``);
* ``Const.m`` / ``Const.new`` on the class Ruby's constant lookup gives
  ``Const`` (``qualified``), and a call on ``Const.new(...)`` or on a variable
  assigned one (``receiver_type``), when ONE file declares the method.

Every other call goes out as an unresolved stub with its facts, and the shared
linkers decide through the file's table (an empty one, of namespaces):

* a send to ``self`` that the class does not declare here: ``enclosing_class``
  (``super``: with ``super_receiver``);
* a project class that lacks the method, or whose method two files declare:
  ``qualifier`` (``Const.m``) or ``receiver_type_hint`` (an instance) holding
  the class's FULL path;
* a library class's instance: ``receiver_type_hint`` as written;
* any receiver nobody can type: nothing, and ``stdlib_member_name`` when Ruby's
  core or Rails defines the name.

The first part pins each route at the analyzer; the second runs the whole
pipeline with a NAMESAKE in another namespace or file beside each target, so
that a bind by name would show as wrong or as a tie.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.import_scope import MODULES_ARE_NAMESPACES
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.ruby import (
    _RAILS_METHOD_NAMES,
    _RUBY_CORE_METHOD_NAMES,
    _RUBY_KERNEL_NAMES,
    analyze_ruby,
)


def _analyze(root: Path, files: dict[str, str]):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return analyze_ruby(root)


def _calls(root: Path, files: dict[str, str]) -> tuple[dict[str, str], list[Edge]]:
    """Symbol names by id, and the call-family edges."""
    result = _analyze(root, files)
    names = {s.id: s.name for s in result.symbols}
    return names, [e for e in result.edges if e.edge_type in ("calls", "instantiates")]


def _at(edges: list[Edge], line: int, path: str = "a.rb") -> Edge:
    """The one call-family edge written on ``line`` of ``path``."""
    found = [e for e in edges if e.line == line and f":{path}:" in e.src]
    assert len(found) == 1, [(e.line, e.dst, e.meta) for e in edges]
    return found[0]


# --- sends to self ---------------------------------------------------------------------------


def test_a_send_to_self_binds_the_own_class_method_of_this_file(tmp_path: Path) -> None:
    names, edges = _calls(tmp_path, {"a.rb": (
        "class K\n"
        "  def go\n"
        "    self.helper\n"
        "    helper(1)\n"
        "    helper\n"
        "  end\n"
        "\n"
        "  def helper(x = 0)\n"
        "  end\n"
        "end\n"
        "class J\n"
        "  def helper\n"
        "  end\n"
        "end\n"
    )})
    for line, receiver in ((3, "self"), (4, "self"), (5, "bare")):
        edge = _at(edges, line)
        assert (names.get(edge.dst), edge.meta["resolved_by"], edge.meta["receiver"]) == (
            "K#helper", "own_type", receiver)


def test_another_class_s_method_of_the_file_is_not_the_callee(tmp_path: Path) -> None:
    """The file's table of bare names (the last ``helper`` declared) used to
    answer; the same name on another class is not this call's callee."""
    _, edges = _calls(tmp_path, {"a.rb": (
        "class K\n"
        "  def go\n"
        "    helper(1)\n"
        "    helper\n"
        "  end\n"
        "end\n"
        "class J\n"
        "  def helper(x = 0)\n"
        "  end\n"
        "end\n"
    )})
    for line in (3, 4):
        edge = _at(edges, line)
        assert (edge.is_resolved, edge.dst) == (False, "ruby:K:0-0:helper:unresolved")
        assert edge.meta["enclosing_class"] == "K"
        assert edge.meta["call_construct"] == "method"
        assert "stdlib_member_name" not in edge.meta


def test_a_kernel_name_keeps_the_external_slot_and_its_class(tmp_path: Path) -> None:
    _, edges = _calls(tmp_path, {"a.rb": (
        "class K\n"
        "  def go\n"
        "    system(\"ls\")\n"
        "    puts\n"
        "  end\n"
        "end\n"
    )})
    for line, name in ((3, "system"), (4, "puts")):
        edge = _at(edges, line)
        assert edge.dst == f"ruby:external:0-0:{name}:unresolved"
        assert edge.meta["enclosing_class"] == "K"
        assert edge.meta["stdlib_member_name"] is True
        assert "call_construct" not in edge.meta


def test_a_call_in_a_class_body_starts_at_the_class(tmp_path: Path) -> None:
    names, edges = _calls(tmp_path, {"a.rb": (
        "class K\n"
        "  def self.setup\n"
        "  end\n"
        "  setup()\n"
        "  has_many :posts\n"
        "end\n"
    )})
    assert names.get(_at(edges, 4).dst) == "K.setup"
    stub = _at(edges, 5)
    assert stub.dst == "ruby:K:0-0:has_many:unresolved"
    assert stub.meta["enclosing_class"] == "K"


def test_a_top_level_def_of_this_file_answers_a_top_level_send(tmp_path: Path) -> None:
    names, edges = _calls(tmp_path, {
        "a.rb": "def helper\nend\n\ndef go\n  helper\n  other\nend\nhelper()\n",
        "b.rb": "def other\nend\n",
    })
    for line in (5, 8):
        edge = _at(edges, line)
        assert (names.get(edge.dst), edge.meta["resolved_by"]) == ("helper", "same_file")
    other = _at(edges, 6)
    assert (other.is_resolved, other.dst) == (False, "ruby:external:0-0:other:unresolved")
    assert "enclosing_class" not in other.meta


def test_a_top_level_def_two_files_declare_is_not_bound(tmp_path: Path) -> None:
    """Ruby keeps the definition loaded last: the source does not say which."""
    _, edges = _calls(tmp_path, {
        "a.rb": "def helper\nend\n\ndef go\n  helper\nend\n",
        "b.rb": "def helper\nend\n",
    })
    assert _at(edges, 5).dst == "ruby:external:0-0:helper:unresolved"


def test_a_top_level_def_is_not_bound_from_a_class(tmp_path: Path) -> None:
    """From a method of a class Ruby looks the name up in the class's
    ancestors first, which may be a gem's: the call goes to the linker."""
    _, edges = _calls(tmp_path, {"a.rb": (
        "def helper\nend\n"
        "class K\n  def go\n    helper\n  end\nend\n"
    )})
    edge = _at(edges, 5)
    assert (edge.is_resolved, edge.meta["enclosing_class"]) == (False, "K")


def test_super_names_the_class_it_is_written_in(tmp_path: Path) -> None:
    _, edges = _calls(tmp_path, {"a.rb": (
        "module A\n"
        "  class Kid < Base\n"
        "    def go\n"
        "      super\n"
        "    end\n"
        "  end\n"
        "end\n"
        "module M\n"
        "  def go\n"
        "    super()\n"
        "  end\n"
        "end\n"
    )})
    edge = _at(edges, 4)
    assert edge.dst == "ruby:Base:0-0:go:unresolved"
    assert (edge.meta["enclosing_class"], edge.meta["super_receiver"]) == ("Kid", True)
    in_module = _at(edges, 10)
    assert in_module.dst == "ruby:external:0-0:go:unresolved"
    assert "enclosing_class" not in in_module.meta
    assert "super_receiver" not in in_module.meta


# --- constants -------------------------------------------------------------------------------


_SVC = (
    "module A\n"
    "  class Svc\n"
    "    def initialize\n"
    "    end\n"
    "    def run\n"
    "    end\n"
    "    def self.make\n"
    "    end\n"
    "  end\n"
    "  class Bare\n"
    "  end\n"
    "end\n"
)


def test_a_constant_s_method_is_bound_or_qualified_by_its_full_path(tmp_path: Path) -> None:
    names, edges = _calls(tmp_path, {"svc.rb": _SVC, "a.rb": (
        "module A\n"
        "  class Ctl\n"
        "    def go\n"
        "      Svc.make\n"
        "      Svc.new\n"
        "      Bare.make\n"
        "      Bare.new\n"
        "      Svc.new.run\n"
        "      Bare.new.run\n"
        "      Svc.where(1).run\n"
        "      Set.new.add(1)\n"
        "    end\n"
        "  end\n"
        "end\n"
    )})
    make = _at(edges, 4)
    assert (names.get(make.dst), make.meta["resolved_by"]) == ("Svc.make", "qualified")
    new = _at(edges, 5)
    assert (new.edge_type, names.get(new.dst), new.meta["resolved_by"]) == (
        "instantiates", "Svc#initialize", "qualified")
    missing = _at(edges, 6)
    assert (missing.dst, missing.meta["qualifier"]) == ("ruby:Bare:0-0:make:unresolved", "A::Bare")
    assert "receiver_type_hint" not in missing.meta
    init = _at(edges, 7)
    assert (init.edge_type, init.dst) == ("calls", "ruby:Bare:0-0:initialize:unresolved")
    assert init.meta["qualifier"] == "A::Bare"
    (run,) = [e for e in edges if e.line == 8 and e.edge_type == "calls"]
    assert (names.get(run.dst), run.meta["resolved_by"]) == ("Svc#run", "receiver_type")
    (on_bare,) = [e for e in edges if e.line == 9 and e.meta.get("callee_name") == "run"]
    assert (on_bare.is_resolved, on_bare.meta["receiver_type_hint"]) == (False, "A::Bare")
    assert "qualifier" not in on_bare.meta
    # ``where`` returns what the source does not say: no type, no bind.
    chained = [e for e in edges if e.line == 10 and (e.meta or {}).get("callee_name") == "run"]
    assert [(e.is_resolved, e.dst) for e in chained] == [(False, "ruby:external:0-0:run:unresolved")]
    assert not {"receiver_type_hint", "qualifier"} & set(chained[0].meta)
    library = [e for e in edges if e.line == 11 and (e.meta or {}).get("callee_name") == "add"]
    assert [e.meta["receiver_type_hint"] for e in library] == ["Set"]


def test_a_method_two_files_declare_on_one_class_is_not_bound(tmp_path: Path) -> None:
    """A class reopened in two files that each define ``run``: Ruby keeps the
    one loaded last. One file's two definitions are Ruby's own rule: the
    later one is bound."""
    _, edges = _calls(tmp_path, {
        "svc.rb": "class Svc\n  def self.run\n  end\n  def initialize\n  end\nend\n",
        "svc_patch.rb": "class Svc\n  def self.run\n  end\n  def initialize\n  end\nend\n",
        "one.rb": "class One\n  def self.go\n  end\n  def self.go\n  end\nend\n",
        "a.rb": "def main\n  Svc.run\n  Svc.new\n  One.go\nend\n",
    })
    run = _at(edges, 2)
    assert (run.is_resolved, run.meta["qualifier"]) == (False, "Svc")
    new = _at(edges, 3)
    assert (new.edge_type, new.is_resolved, new.meta["qualifier"]) == ("calls", False, "Svc")
    one = _at(edges, 4)
    assert one.is_resolved and one.dst.startswith("ruby:one.rb:4-5:")


def test_a_variable_is_typed_by_a_construction_only(tmp_path: Path) -> None:
    names, edges = _calls(tmp_path, {"svc.rb": _SVC, "a.rb": (
        "module A\n"
        "  class Ctl\n"
        "    def go\n"
        "      s = Svc.new\n"
        "      s.run\n"
        "      b = Bare.new\n"
        "      b.run\n"
        "      f = File.new(p)\n"
        "      f.read\n"
        "      r = Svc.make\n"
        "      r.run\n"
        "    end\n"
        "  end\n"
        "end\n"
    )})
    run = [e for e in edges if e.line == 5 and (e.meta or {}).get("call_construct") == "method"]
    assert [(names.get(e.dst), e.meta["resolved_by"]) for e in run] == [("Svc#run", "receiver_type")]

    def stub(line: int, callee: str) -> dict:
        (edge,) = [e for e in edges if e.line == line and (e.meta or {}).get("callee_name") == callee]
        assert not edge.is_resolved
        return edge.meta

    assert stub(7, "run")["receiver_type_hint"] == "A::Bare"
    assert stub(9, "read")["receiver_type_hint"] == "File"
    assert "receiver_type_hint" not in stub(11, "run")


def test_an_untyped_receiver_carries_the_screen(tmp_path: Path) -> None:
    _, edges = _calls(tmp_path, {"a.rb": (
        "def go(x)\n"
        "  x.each\n"
        "  x.where\n"
        "  x.frobnicate\n"
        "end\n"
    )})
    assert _at(edges, 2).meta["stdlib_member_name"] is True
    assert _at(edges, 3).meta["stdlib_member_name"] is True
    plain = _at(edges, 4)
    assert plain.meta["call_construct"] == "method"
    assert "stdlib_member_name" not in plain.meta


def test_the_screen_is_three_documented_lists() -> None:
    """Kernel's names are core names; Rails adds names the core does not
    define (``where``, ``blank?``)."""
    assert _RUBY_KERNEL_NAMES <= _RUBY_CORE_METHOD_NAMES
    assert {"puts", "require", "system", "respond_to?"} <= _RUBY_KERNEL_NAMES
    assert {"each", "map", "merge", "strip", "read", "call"} <= _RUBY_CORE_METHOD_NAMES
    assert {"where", "find_by", "blank?", "present?"} <= _RAILS_METHOD_NAMES - _RUBY_CORE_METHOD_NAMES


def test_every_ruby_file_hands_over_an_empty_table_of_namespaces(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {
        "a.rb": "require_relative 'b'\nclass K\n  include M\nend\n",
        "lib/b.rb": "# no declarations\nputs 1\n",
    })
    assert set(result.import_scopes) == {"a.rb", "lib/b.rb"}
    for scope in result.import_scopes.values():
        assert scope.module_kind == MODULES_ARE_NAMESPACES
        assert scope.own_namespace is None
        assert scope.library_names_screened is False
        assert scope.named("M") is None and scope.namespace("b") is None
        assert scope.wildcard_modules == ()


# --- through the whole pipeline, with a namesake beside every target ---------------------------


def _map(root: Path, files: dict[str, str]) -> set[tuple]:
    """(caller, line, target's qualified name, mark) of each resolved Ruby call."""
    for rel, text in files.items():
        path = root / "repo" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    out = root / "out.json"
    run_behavior_map(repo_root=root / "repo", out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    nodes = {n["id"]: n for n in data["nodes"]}
    return {
        (nodes[e["src"]]["qualified_name"], e["line"], nodes[e["dst"]]["qualified_name"],
         (e.get("meta") or {}).get("resolved_by"))
        for e in data["edges"]
        if e["type"] in ("calls", "instantiates") and e.get("is_resolved")
        and e["src"].startswith("ruby:")
    }


_BASES = (
    "module {ns}\n"
    "  class Base\n"
    "    def ping\n"
    "    end\n"
    "    def self.make\n"
    "    end\n"
    "  end\n"
    "  class Kid < Base\n"
    "  end\n"
    "end\n"
)


def test_a_qualified_call_reaches_the_class_its_constant_names(tmp_path: Path) -> None:
    """``Kid.make`` inside ``module Store`` is ``Store::Kid``, whose superclass
    declares ``make``; ``Legacy::Kid`` has the same shape."""
    calls = _map(tmp_path, {
        "store.rb": _BASES.format(ns="Store"),
        "legacy.rb": _BASES.format(ns="Legacy"),
        "app.rb": "module Store\n  class App\n    def go\n      Kid.make\n      Legacy::Kid.make\n"
                  "    end\n  end\nend\n",
    })
    assert calls == {
        ("Store::App::go", 4, "Store::Base::make", "qualified"),
        ("Store::App::go", 5, "Legacy::Base::make", "qualified"),
    }


def test_a_typed_receiver_reaches_its_class_s_ancestor_not_a_namesake(tmp_path: Path) -> None:
    calls = _map(tmp_path, {
        "store.rb": _BASES.format(ns="Store"),
        "legacy.rb": _BASES.format(ns="Legacy"),
        "app.rb": "module Store\n  class App\n    def go\n      k = Kid.new\n      k.ping\n"
                  "    end\n  end\nend\n",
    })
    assert calls == {("Store::App::go", 5, "Store::Base::ping", "receiver_type")}


def test_a_send_to_self_reaches_the_class_reopened_elsewhere_and_its_parent(tmp_path: Path) -> None:
    calls = _map(tmp_path, {
        "store.rb": _BASES.format(ns="Store"),
        "legacy.rb": _BASES.format(ns="Legacy"),
        "kid_ext.rb": "module Store\n  class Kid\n    def extra\n    end\n  end\nend\n",
        "kid_go.rb": "module Store\n  class Kid\n    def go\n      extra\n      ping\n      super\n"
                     "    end\n  end\nend\n",
        "other.rb": "class Other\n  def extra\n  end\n  def go\n  end\nend\n",
    })
    assert calls == {
        ("Store::Kid::go", 4, "Store::Kid::extra", "own_type"),
        ("Store::Kid::go", 5, "Store::Base::ping", "own_type"),
    }


def test_a_required_top_level_def_beside_a_namesake_is_not_bound(tmp_path: Path) -> None:
    """``require_relative`` loads a file and binds no name: with a second
    top-level ``helper`` in the tree, neither is the callee by the source."""
    calls = _map(tmp_path, {
        "util.rb": "def helper\n  1\nend\n",
        "decoy.rb": "def helper\n  2\nend\n",
        "main.rb": "require_relative 'util'\n\ndef go\n  helper()\nend\n",
    })
    assert calls == set()


def test_an_untyped_receiver_and_a_library_name_are_not_bound_by_name(tmp_path: Path) -> None:
    """The name step is off for Ruby: ``x.drain`` is unresolved although one
    method in the tree is called ``drain``, and ``items.each`` although the
    tree declares one ``each``."""
    calls = _map(tmp_path, {
        "pool.rb": "class Pool\n  def drain\n  end\n  def each\n  end\nend\n",
        "app.rb": "def go(x, items)\n  x.drain\n  items.each { |i| i }\nend\n",
    })
    assert calls == set()
