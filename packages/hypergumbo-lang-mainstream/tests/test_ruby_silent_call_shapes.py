# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ruby call shapes that emitted no edge of any type (WI-misir).

WI-napaf gave a receiverless ``call`` node that resolves nowhere the
``external`` placeholder. These siblings stayed silent:

1. a call on a variable receiver of unknown type (``obj.write``);
2-4. a no-argument implicit-self call, which tree-sitter-ruby parses as a bare
   ``identifier`` like a local-variable read (``current_user``, ``params``,
   ``puts``) -- told apart by Ruby's own lexical rule: a name is a local iff a
   parameter, block parameter, rescue variable or assignment binds it earlier
   in the same scope;
5. a bare call to a name 3+ project classes define (AMB-METHOD);
6. ``super`` / ``super(...)``;
7. direct recursion;
8. ``self.helper(...)``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/ruby.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.ruby import analyze_ruby

_SRC = """\
class Base
  def go(obj, a)
  end
end

class P
  def run(x)
  end
end

class Q
  def run(x)
  end
end

class R
  def run(x)
  end
end

class K < Base
  def go(obj, a)
    obj.write(a)
    current_user.name
    params[:id]
    puts
    x = 1
    x
    a
    [1].each { |y| y + z }
    run(1)
    super(1)
    helper(2)
    self.helper(3)
  rescue => e
    e
  end

  def again
    super
  end

  def foo(n)
    foo(n - 1)
  end

  def helper(v)
  end
end
"""


def _edges_from(tmp_path: Path, method: str) -> list[tuple[str, dict]]:
    (tmp_path / "k.rb").write_text(_SRC)
    result = analyze_ruby(tmp_path)
    names = {s.id: s.name for s in result.symbols}
    return [
        (names.get(e.dst, e.dst), dict(e.meta or {}))
        for e in result.edges
        if e.edge_type == "calls" and names.get(e.src) == method
    ]


def _targets(tmp_path: Path, method: str) -> list[str]:
    return [dst for dst, _ in _edges_from(tmp_path, method)]


def test_unknown_receiver_and_implicit_self_calls_get_the_external_placeholder(tmp_path: Path) -> None:
    targets = _targets(tmp_path, "K#go")
    for name in ("write", "current_user", "name", "params", "puts", "z"):
        assert f"ruby:external:0-0:{name}:unresolved" in targets, (name, targets)
    meta = dict(_edges_from(tmp_path, "K#go"))
    assert meta["ruby:external:0-0:write:unresolved"].get("call_construct") == "method"


def test_locals_are_not_calls(tmp_path: Path) -> None:
    """An assigned local, a parameter, a block parameter and a rescue variable
    are reads, not sends."""
    targets = _targets(tmp_path, "K#go")
    for name in ("x", "a", "y", "e", "obj"):
        assert f"ruby:external:0-0:{name}:unresolved" not in targets, (name, targets)


def test_an_ambiguous_bare_call_keeps_its_enclosing_class(tmp_path: Path) -> None:
    edges = dict(_edges_from(tmp_path, "K#go"))
    # Not "external": the name is the project's (WI-napaf's rule).
    run = edges["ruby:K:0-0:run:unresolved"]
    assert run.get("enclosing_class") == "K"


def test_super_is_a_call_to_the_parent_s_method(tmp_path: Path) -> None:
    edges = _edges_from(tmp_path, "K#go") + _edges_from(tmp_path, "K#again")
    supers = [m for dst, m in edges if dst in (
        "ruby:Base:0-0:go:unresolved", "ruby:Base:0-0:again:unresolved")]
    assert [m.get("enclosing_class") for m in supers] == ["Base", "Base"]


def test_super_resolves_through_the_inherited_calls_linker(tmp_path: Path) -> None:
    (tmp_path / "k.rb").write_text(_SRC)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    names = {n["id"]: n["name"] for n in data["nodes"]}
    pairs = {(names.get(e["src"]), names.get(e["dst"])) for e in data["edges"]
             if e["type"] == "calls"}
    assert ("K#go", "Base#go") in pairs


def test_direct_recursion_and_self_receiver(tmp_path: Path) -> None:
    assert "K#foo" in _targets(tmp_path, "K#foo"), "python and java emit the self edge too"
    assert _targets(tmp_path, "K#go").count("K#helper") == 2, "helper(2) and self.helper(3)"


def test_more_binding_and_owner_shapes(tmp_path: Path) -> None:
    """A ``for`` variable is a local; a top-level ``super`` has no owner and
    no superclass; a bare ambiguous identifier with no candidate in its own
    file is recorded with its enclosing class."""
    (tmp_path / "others.rb").write_text(
        "class P\n  def run\n  end\nend\n"
        "class Q\n  def run\n  end\nend\n"
        "class R\n  def run\n  end\nend\n"
    )
    (tmp_path / "k.rb").write_text(
        "def top\n  super\nend\n"
        "class K\n"
        "  def go(xs)\n"
        "    for item in xs\n      item\n    end\n"
        "    run\n"
        "  end\n"
        "end\n"
    )
    result = analyze_ruby(tmp_path)
    names = {s.id: s.name for s in result.symbols}
    by_src: dict[str, list[tuple[str, dict]]] = {}
    for e in result.edges:
        if e.edge_type == "calls" and e.src in names:
            by_src.setdefault(names[e.src], []).append((e.dst, dict(e.meta or {})))
    go = dict(by_src["K#go"])
    assert "ruby:external:0-0:item:unresolved" not in go, "a for variable is a local"
    assert go["ruby:K:0-0:run:unresolved"].get("enclosing_class") == "K"
    top = dict(by_src["top"])
    assert top["ruby:external:0-0:top:unresolved"].get("enclosing_class") is None
