# SPDX-License-Identifier: AGPL-3.0-or-later
"""A nested Ruby declaration is registered under its full lexical path (INV-dosoz).

``module A; class X`` used to be registered under ``X`` and its methods under
``X.run`` / ``X#go``, while a compact ``class A::X`` kept its path. Two classes
of one name in different namespaces therefore shared a registry key, the later
registration took every reference to either, and a reference that spelled the
path out (``A::X.run``) was answered by its last segment.

Each test names its targets by ``qualified_name`` (``A::B::X::run``), which
already told the namesakes apart, and asserts which symbol an edge reaches.
``Symbol.name`` and ``qualified_name`` are asserted unchanged: both are inputs
of ``stable_id``, so the fix lives in the registry keys only.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_lang_mainstream.ruby import (
    _analyzer,
    _declaration_nesting,
    _lexical_path,
    _namespaces,
    _owner_path,
    _registry_key,
    analyze_ruby,
)

Edges = dict[tuple[str, str], list[str]]


def _edges(tmp_path: Path, files: dict[str, str]) -> Edges:
    """``{(edge_type, src qualified_name): [dst qualified_name or raw dst id]}``.

    Only the analyzer's own edges: no linker runs here.
    """
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    result = analyze_ruby(tmp_path)
    qualified = {s.id: str(s.qualified_name) for s in result.symbols}
    out: Edges = {}
    for e in result.edges:
        if e.src in qualified:
            out.setdefault((e.edge_type, qualified[e.src]), []).append(
                qualified.get(e.dst, e.dst))
    return out


_X_OUTER = "  class X\n    def self.run\n    end\n    def go\n    end\n  end\n"
_B_WITH_X = (
    "  module B\n"
    "    class X\n      def self.run\n      end\n      def go\n      end\n    end\n"
    "    def self.go\n      X.run\n    end\n"
    "  end\n"
)
_A_TOP = "  def self.top\n    X.run\n  end\n"
_USERS = (
    "class Z\n"
    "  def self.go\n    A::X.run\n  end\n"
    "  def self.deep\n    A::B::X.run\n  end\n"
    "  def self.root\n    ::A::B::X.run\n  end\n"
    "  def self.chain\n    A::X.new.go\n  end\n"
    "  def self.typed\n    x = A::B::X.new\n    x.go\n  end\n"
    "end\n"
    "class Q < A::B::X\nend\n"
    "class P < A::X\nend\n"
)


@pytest.fixture(params=["outer_first", "inner_first"])
def namesakes(request: pytest.FixtureRequest, tmp_path: Path) -> Edges:
    """``A::X`` and ``A::B::X``, declared in either order: which one registered
    last used to decide every bind."""
    body = (
        _X_OUTER + _B_WITH_X if request.param == "outer_first"
        else _B_WITH_X + _X_OUTER
    )
    return _edges(tmp_path, {"a.rb": f"module A\n{body}{_A_TOP}end\n{_USERS}"})


def test_a_relative_constant_binds_the_innermost_namesake(namesakes: Edges) -> None:
    assert namesakes["calls", "A::B::go"] == ["A::B::X::run"]
    assert namesakes["calls", "A::top"] == ["A::X::run"]


def test_a_written_path_binds_the_class_at_that_path(namesakes: Edges) -> None:
    assert namesakes["calls", "Z::go"] == ["A::X::run"]
    assert namesakes["calls", "Z::deep"] == ["A::B::X::run"]
    assert namesakes["calls", "Z::root"] == ["A::B::X::run"]


def test_a_chained_and_a_typed_receiver_bind_the_class_at_the_path(
    namesakes: Edges,
) -> None:
    """``A::X.new.go`` and ``x = A::B::X.new; x.go``. Neither class declares
    ``initialize``, so ``.new`` stays one unresolved edge naming its class."""
    assert "A::X::go" in namesakes["calls", "Z::chain"]
    assert "A::B::X::go" in namesakes["calls", "Z::typed"]
    assert "A::X::go" not in namesakes["calls", "Z::typed"]


def test_a_superclass_binds_the_class_at_its_path(namesakes: Edges) -> None:
    assert namesakes["extends", "Q"] == ["A::B::X"]
    assert namesakes["extends", "P"] == ["A::X"]


def test_names_that_feed_the_stable_id_are_unchanged(tmp_path: Path) -> None:
    """The registry is re-keyed; the symbols are not renamed."""
    (tmp_path / "a.rb").write_text(
        "module A\n" + _X_OUTER + _B_WITH_X + "end\n")
    result = analyze_ruby(tmp_path)
    named = sorted(
        (s.kind, s.name, s.qualified_name) for s in result.symbols
        if s.kind in ("class", "method") and s.name.startswith("X")
    )
    assert named == [
        ("class", "X", "A::B::X"),
        ("class", "X", "A::X"),
        ("method", "X#go", "A::B::X::go"),
        ("method", "X#go", "A::X::go"),
        ("method", "X.run", "A::B::X::run"),
        ("method", "X.run", "A::X::run"),
    ]
    assert len({s.stable_id for s in result.symbols}) == len(result.symbols)


def test_block_and_compact_declarations_share_one_key(tmp_path: Path) -> None:
    """``class A::C`` in one file and ``module A; class C`` in another are one
    class: a reference reaches the methods of both, by either spelling, and
    ``C.new`` names ONE class (it used to emit an unresolved edge per
    spelling)."""
    edges = _edges(tmp_path, {
        "compact.rb": "class A::C\n  def self.go\n  end\nend\n",
        "block.rb": (
            "module A\n"
            "  class C\n    def helper\n    end\n  end\n"
            "  class D\n"
            "    def work\n      c = C.new\n      c.helper\n      C.go\n    end\n"
            "  end\n"
            "end\n"
        ),
        "user.rb": (
            "class U\n"
            "  def one\n    A::C.go\n  end\n"
            "  def two\n    A::C.new.helper\n  end\n"
            "end\n"
        ),
    })
    work = edges["calls", "A::D::work"]
    assert "A::C::helper" in work and "A::C::go" in work
    assert len([d for d in work if d.endswith(":initialize:unresolved")]) == 1
    assert edges["calls", "U::one"] == ["A::C::go"]
    assert "A::C::helper" in edges["calls", "U::two"]


def test_a_class_reopened_in_another_file_keeps_its_key(tmp_path: Path) -> None:
    edges = _edges(tmp_path, {
        "one.rb": "module A\n  class X\n    def self.run\n    end\n  end\nend\n",
        "two.rb": "module A\n  class X\n    def self.more\n    end\n  end\nend\n",
        "other.rb": "module B\n  class X\n    def self.more\n    end\n  end\nend\n",
        "user.rb": (
            "module A\n  def self.go\n    X.run\n    X.more\n  end\nend\n"
        ),
    })
    assert edges["calls", "A::go"] == ["A::X::run", "A::X::more"]


def test_a_nested_class_is_not_reachable_by_its_last_segment(tmp_path: Path) -> None:
    """Ruby finds ``X`` at the top level only if a top-level ``X`` exists, and
    ``Other::X`` only at that path. Neither may fall back to ``A::X``; the
    short name is no longer a registry key. A project's own ``A::File`` no
    longer answers the top level's ``File.read``."""
    edges = _edges(tmp_path, {
        "a.rb": (
            "module A\n"
            "  class X\n    def self.run\n    end\n  end\n"
            "  class File\n    def self.read\n    end\n  end\n"
            "end\n"
            "class Z\n"
            "  def self.bare\n    X.run\n  end\n"
            "  def self.elsewhere\n    Other::X.run\n  end\n"
            "  def self.rooted\n    ::X.run\n  end\n"
            "  def self.io\n    File.read\n  end\n"
            "end\n"
        ),
    })
    assert edges["calls", "Z::bare"] == ["ruby:X:0-0:run:unresolved"]
    assert edges["calls", "Z::elsewhere"] == ["ruby:Other::X:0-0:run:unresolved"]
    assert edges["calls", "Z::rooted"] == ["ruby:X:0-0:run:unresolved"]
    assert edges["calls", "Z::io"] == ["ruby:File:0-0:read:unresolved"]


def test_the_innermost_class_decides_even_when_it_lacks_the_method(
    tmp_path: Path,
) -> None:
    """Ruby resolves the constant first, then sends: inside ``A::B``, ``X`` is
    ``A::B::X``, and ``X.run`` is not answered by ``A::X.run`` when ``A::B::X``
    declares no ``run``. The call stays unresolved with its receiver's type,
    for the inherited-calls linker."""
    for name, text in {
        "a.rb": (
            "module A\n"
            "  class X\n    def self.run\n    end\n  end\n"
            "  module B\n"
            "    class X\n    end\n"
            "    def self.go\n      X.run\n    end\n"
            "  end\n"
            "end\n"
        ),
    }.items():
        (tmp_path / name).write_text(text)
    result = analyze_ruby(tmp_path)
    go = next(s for s in result.symbols if s.qualified_name == "A::B::go")
    (edge,) = [e for e in result.edges if e.src == go.id]
    assert edge.dst == "ruby:X:0-0:run:unresolved"
    assert edge.meta["receiver_type_hint"] == "X"


def test_a_constant_is_reached_through_an_included_module_and_a_superclass(
    tmp_path: Path,
) -> None:
    """After the lexical scopes Ruby searches the ancestors of the innermost
    one: ``UserExists`` in a class that includes ``Errors::Account``, ``Config``
    in a class whose superclass's superclass declares it. A class with neither
    ancestor does not reach them: the short name alone is not a key."""
    edges = _edges(tmp_path, {
        "errors.rb": (
            "module Errors\n  module Account\n"
            "    class UserExists\n      def initialize\n      end\n    end\n"
            "  end\nend\n"
        ),
        "base.rb": (
            "class Base\n"
            "  class Config\n    def self.load\n    end\n  end\n"
            "end\n"
            "class Mid < Base\nend\n"
        ),
        "use.rb": (
            "class Builder\n"
            "  include Errors::Account\n"
            "  def go\n    UserExists.new\n  end\n"
            "end\n"
            "class Kid < Mid\n"
            "  def go\n    Config.load\n  end\n"
            "  def typed\n    c = Config.new\n    c.go\n  end\n"
            "end\n"
            "class Loner\n"
            "  def go\n    Config.load\n    UserExists.new\n  end\n"
            "end\n"
        ),
    })
    assert edges["instantiates", "Builder::go"] == [
        "Errors::Account::UserExists::initialize"]
    assert edges["calls", "Kid::go"] == ["Base::Config::load"]
    assert "ruby:Config:0-0:initialize:unresolved" in edges["calls", "Kid::typed"]
    assert edges["calls", "Loner::go"] == [
        "ruby:Config:0-0:load:unresolved", "ruby:UserExists:0-0:new:unresolved"]


def test_lexical_scopes_come_before_ancestors_and_ancestors_before_the_top_level(
    tmp_path: Path,
) -> None:
    edges = _edges(tmp_path, {
        "a.rb": (
            "class Config\n  def self.load\n  end\nend\n"
            "class Base\n"
            "  class Config\n    def self.load\n    end\n  end\n"
            "end\n"
            "module Scope\n"
            "  class Config\n    def self.load\n    end\n  end\n"
            "  class Kid < Base\n    def go\n      Config.load\n    end\n  end\n"
            "end\n"
            "class Plain < Base\n  def go\n    Config.load\n  end\nend\n"
            "module Api\n"
            "  class Base < Base\n    def go\n      Config.load\n    end\n  end\n"
            "end\n"
        ),
    })
    assert edges["calls", "Scope::Kid::go"] == ["Scope::Config::load"]
    assert edges["calls", "Plain::go"] == ["Base::Config::load"]
    assert edges["calls", "Api::Base::go"] == ["Base::Config::load"]


def test_the_ancestor_walk_survives_cycles_diamonds_and_reopenings(
    tmp_path: Path,
) -> None:
    edges = _edges(tmp_path, {
        "mods.rb": (
            "module D\n  class Tool\n    def self.run\n    end\n  end\nend\n"
            "module B\n  include D\n  include C\nend\n"
            "module C\n  include D\n  include B\nend\n"
        ),
        "one.rb": "class K\n  include B\n  include C\nend\n",
        "two.rb": "class K\n  include B\n  def go\n    Tool.run\n  end\nend\n",
    })
    assert edges["calls", "K::go"] == ["D::Tool::run"]


def test_a_superclass_is_looked_up_from_where_the_class_is_declared(
    tmp_path: Path,
) -> None:
    """``class Kid < Base`` inside ``module Admin`` is ``Admin::Base`` when that
    exists; the compact ``class Admin::Other < Base`` sits at the top level,
    where ``Base`` is the top-level one; ``class Base < Base`` inside ``module
    Api`` cannot mean itself; ``< Gem::Base`` names a path no project class has
    and binds nothing (it used to take any class whose last segment matched);
    ``< ::Base`` skips the nesting."""
    edges = _edges(tmp_path, {
        "base.rb": "class Base\nend\n",
        "admin.rb": (
            "module Admin\n"
            "  class Base\n  end\n"
            "  class Kid < Base\n  end\n"
            "  class Rooted < ::Base\n  end\n"
            "  class Ext < Gem::Base\n  end\n"
            "end\n"
            "class Admin::Other < Base\nend\n"
        ),
        "api.rb": "module Api\n  class Base < Base\n  end\nend\n",
    })
    assert edges["extends", "Admin::Kid"] == ["Admin::Base"]
    assert edges["extends", "Admin::Rooted"] == ["Base"]
    assert edges["extends", "Admin::Other"] == ["Base"]
    assert edges["extends", "Api::Base"] == ["Base"]
    assert ("extends", "Admin::Ext") not in edges


def test_a_superclass_declared_in_several_files_still_prefers_the_required_one(
    tmp_path: Path,
) -> None:
    """Several declarations of ONE path (test stubs named ``Model``) are still
    told apart by the child's ``require_relative``, now under a path key."""
    edges = _edges(tmp_path, {
        "stubs/one/model.rb": "module Stub\n  class Model\n  end\nend\n",
        "stubs/two/model.rb": "module Stub\n  class Model\n  end\nend\n",
        "kid.rb": (
            "require_relative 'stubs/two/model'\n"
            "class Kid < Stub::Model\nend\n"
        ),
    })
    result = analyze_ruby(tmp_path)
    (extends,) = [e for e in result.edges if e.edge_type == "extends"]
    assert "stubs/two/model.rb" in extends.dst
    assert edges["extends", "Kid"] == ["Stub::Model"]


def test_a_bare_and_a_self_send_stay_inside_the_callers_own_class(
    tmp_path: Path,
) -> None:
    """``helper``, ``self.helper`` and ``method(:helper)`` in ``A::X#run`` are
    ``A::X#helper``, not the ``helper`` of the ``A::B::X`` declared after it in
    the same file."""
    body = (
        "    def run\n      helper\n    end\n"
        "    def own\n      self.helper\n    end\n"
        "    def ref\n      method(:helper)\n    end\n"
        "    def helper\n    end\n"
    )
    edges = _edges(tmp_path, {
        "a.rb": (
            "module A\n"
            f"  class X\n{body}  end\n"
            "  module B\n"
            f"  class X\n{body}  end\n"
            "  end\n"
            "end\n"
        ),
    })
    for outer in ("A::X", "A::B::X"):
        assert edges["calls", f"{outer}::run"] == [f"{outer}::helper"]
        assert edges["calls", f"{outer}::own"] == [f"{outer}::helper"]
        assert edges["references", f"{outer}::ref"] == [f"{outer}::helper"]


def test_a_method_reference_in_a_class_body_binds_that_class(tmp_path: Path) -> None:
    edges = _edges(tmp_path, {
        "a.rb": (
            "module A\n"
            "  class X\n    HOOK = method(:helper)\n    def helper\n    end\n  end\n"
            "end\n"
            "module B\n"
            "  class X\n    def helper\n    end\n  end\n"
            "end\n"
        ),
    })
    assert edges["references", "A::X"] == ["A::X::helper"]


def test_rails_callbacks_pair_each_controller_with_its_own_method(
    tmp_path: Path,
) -> None:
    body = (
        "    before_action :auth\n"
        "    after_commit do\n      audit\n    end\n"
        "    def auth\n    end\n"
        "    def audit\n    end\n"
    )
    edges = _edges(tmp_path, {
        "controllers.rb": (
            f"module Admin\n  class UsersController\n{body}  end\nend\n"
            f"module Api\n  class UsersController\n{body}  end\nend\n"
        ),
    })
    for ns in ("Admin", "Api"):
        assert sorted(edges["dispatches_to", f"{ns}::UsersController"]) == [
            f"{ns}::UsersController::audit", f"{ns}::UsersController::auth",
        ]


def test_an_association_and_a_delegate_walk_the_models_namespaces(
    tmp_path: Path,
) -> None:
    """``has_many :comments`` in ``Admin::Post`` is ``Admin::Comment`` when that
    exists (Rails tries the model's namespaces, innermost first), else the
    top-level ``Comment``; ``delegate ..., to: :account`` likewise."""
    edges = _edges(tmp_path, {
        "comment.rb": "class Comment\nend\n",
        "account.rb": "class Account\n  def plan\n  end\nend\n",
        "admin.rb": (
            "module Admin\n"
            "  class Comment\n  end\n"
            "  class Account\n    def plan\n    end\n  end\n"
            "  class Post\n"
            "    has_many :comments\n"
            "    delegate :plan, to: :account\n"
            "  end\n"
            "end\n"
        ),
        "post.rb": (
            "class Post\n"
            "  has_many :comments\n"
            "  delegate :plan, to: :account\n"
            "end\n"
        ),
    })
    assert edges["references", "Admin::Post"] == [
        "Admin::Comment", "Admin::Account::plan"]
    assert edges["references", "Post"] == ["Comment", "Account::plan"]


def test_a_job_is_enqueued_on_the_class_at_its_path(tmp_path: Path) -> None:
    edges = _edges(tmp_path, {
        "jobs.rb": (
            "module A\n  class SyncJob\n    def perform\n    end\n  end\nend\n"
            "module B\n  class SyncJob\n    def perform\n    end\n  end\n"
            "  class Bare\n  end\n"
            "  def self.go\n    SyncJob.perform_later\n    Bare.perform_later\n  end\n"
            "end\n"
            "class U\n"
            "  def one\n    A::SyncJob.perform_later\n  end\n"
            "  def two\n    SyncJob.perform_later\n  end\n"
            "end\n"
        ),
    })
    assert edges["event_publishes", "B::go"] == ["B::SyncJob::perform", "B::Bare"]
    assert edges["event_publishes", "U::one"] == ["A::SyncJob::perform"]
    assert edges["event_publishes", "U::two"] == ["ruby:?:0-0:SyncJob:unresolved"]


def test_a_rooted_compact_declaration_drops_the_scope_it_is_written_in(
    tmp_path: Path,
) -> None:
    """``module M; class ::Root::Thing`` declares ``Root::Thing``."""
    edges = _edges(tmp_path, {
        "a.rb": (
            "module M\n"
            "  class ::Root::Thing\n    def self.run\n    end\n  end\n"
            "end\n"
            "class U\n  def go\n    Root::Thing.run\n    ::Root::Thing.run\n  end\nend\n"
        ),
    })
    assert edges["calls", "U::go"] == ["M::::Root::Thing::run"] * 2


def _sym(kind: str, name: str, qualified: str | None) -> Symbol:
    return Symbol(
        id=f"ruby:a.rb:1-1:{name}:{kind}", name=name, kind=kind,
        language="ruby", path="a.rb",
        span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        origin="test", origin_run_id="r", qualified_name=qualified,
    )


@pytest.mark.parametrize(("kind", "name", "qualified", "key"), [
    ("class", "X", "A::B::X", "A::B::X"),
    ("class", "A::B::X", "A::B::X", "A::B::X"),
    ("class", "::Root::Thing", "M::::Root::Thing", "Root::Thing"),
    ("class", "::Top", "::Top", "Top"),
    ("module", "B", "A::B", "A::B"),
    ("class", "Bare", None, "Bare"),
    ("method", "X#go", "A::B::X::go", "A::B::X#go"),
    ("method", "X.run", "A::B::X::run", "A::B::X.run"),
    ("method", "A::C.go", "A::C::go", "A::C.go"),
    ("method", "top_level", "top_level", "top_level"),
    ("method", "loose", None, "loose"),
])
def test_registry_key(kind: str, name: str, qualified: str | None, key: str) -> None:
    symbol = _sym(kind, name, qualified)
    assert _registry_key(symbol) == key
    registry: dict[str, Symbol] = {}
    _analyzer.register_symbol(symbol, registry)
    assert registry == {key: symbol}


def test_path_helpers() -> None:
    assert _lexical_path("A::B") == "A::B"
    assert _lexical_path("M::::Root::Thing") == "Root::Thing"
    assert _namespaces("A::B::C") == ["A::B::C", "A::B", "A"]
    assert _namespaces("") == []
    assert _owner_path(_sym("method", "X#go", "A::X::go")) == "A::X"
    assert _owner_path(_sym("method", "go", "go")) is None
    assert _owner_path(_sym("class", "X", "A::X")) is None
    assert _declaration_nesting(_sym("class", "Q", "A::B::Q")) == ["A::B", "A"]
    assert _declaration_nesting(_sym("class", "A::B::Q", "A::B::Q")) == []
    assert _declaration_nesting(_sym("class", "::Q", "M::::Q")) == ["M"]
    assert _declaration_nesting(_sym("class", "Q", None)) == []
