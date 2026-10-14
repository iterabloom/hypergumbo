# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-napaf: a receiverless ruby call that resolves nowhere is RECORDED.

``puts "x"``, ``has_many :posts``, ``validates :name``, ``before_action :boot``,
``system("ls")``, ``raise E, "m"`` -- a receiverless ``call`` whose name no
project symbol carries -- emitted NO edge of any type, in a method body as in a
class body or at file level. That is INV-foluz's shape in ruby (python fixed it
with the ``external`` residual, javascript with WI-fahod): an absent edge reads
as "this method calls nothing", and the ADR-0017 walk records an escape where a
call stands. It now emits ``ruby:external:0-0:<name>:unresolved``.

Ruby's syntax decides call-vs-variable for us: a receiverless ``call`` node (one
with arguments, parentheses or a block) is ALWAYS a method send to ``self``,
even when a local of that name is in scope (``x = 1; x(2)`` raises
NoMethodError). So, unlike python's LEGB refusal and javascript's scope walk,
no local-binding check applies to this arm. A bare ``identifier`` IS ambiguous
(``x`` vs ``puts`` with no arguments) and keeps its own, unchanged arm.

THE CATALOGUE QUESTION (WI-javaf's hazard, answered for ruby). The ``external``
slot reaches ``gate_named_entry``, which matches FUNCTION-kind rows by short
name. In javascript that is never right, because every function row is reached
through an import. In ruby it IS right for the Kernel methods: ``system``,
``exec``, ``puts`` and ``open`` are ambient on every object and need no require.
What must NOT reach the row is a project method SHADOWING the Kernel name, and
it does not: a name some project symbol carries resolves first (or, when three
or more classes declare it, is withheld by the AMB-METHOD guard), so the
placeholder is only ever emitted for a name the repository does not define.
Both sides are pinned below.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.io_boundary import classify_call, load_overlay_catalog
from hypergumbo_lang_mainstream.ruby import analyze_ruby

_APP = """class App < ActiveRecord::Base
  has_many :posts
  validates :name, presence: true
  before_action :boot

  def boot(blk)
    puts "x"
    has_many :y
    system("ls")
    raise ArgumentError, "bad"
    Integer("3")
    blk(2)
    loop do
      break
    end
  end
end

puts "top"
"""


def _calls_at(result, line: int) -> list:
    return [
        e for e in result.edges
        if e.edge_type == "calls" and e.line == line
    ]


def _by_name(result) -> dict[str, str]:
    return {s.name: s.id for s in result.symbols}


def test_a_receiverless_call_in_a_method_is_recorded(tmp_path: Path) -> None:
    (tmp_path / "app.rb").write_text(_APP)
    result = analyze_ruby(tmp_path)
    boot = _by_name(result)["App#boot"]
    expected = {
        7: "puts", 8: "has_many", 9: "system", 10: "raise",
        11: "Integer", 12: "blk", 13: "loop",
    }
    for line, name in expected.items():
        at = _calls_at(result, line)
        assert [e.dst for e in at] == [f"ruby:external:0-0:{name}:unresolved"], (
            line, [(e.src, e.dst) for e in at])
        edge = at[0]
        assert edge.src == boot
        assert edge.is_resolved is False
        assert edge.meta["callee_name"] == name
        # A receiverless call is a bare call: no ``method`` construct, which
        # would make both taint gates refuse a catalogued Kernel row.
        assert "call_construct" not in edge.meta
        assert edge.dst_ref is None  # the owner is genuinely unknown


def test_class_body_dsl_and_top_level_calls_are_recorded(tmp_path: Path) -> None:
    (tmp_path / "app.rb").write_text(_APP)
    result = analyze_ruby(tmp_path)
    app = _by_name(result)["App"]
    for line, name in {2: "has_many", 3: "validates", 4: "before_action"}.items():
        at = [e for e in _calls_at(result, line) if e.dst.startswith("ruby:external:")]
        assert [(e.src, e.dst) for e in at] == [
            (app, f"ruby:external:0-0:{name}:unresolved")], line
    top = _calls_at(result, 19)
    assert [e.dst for e in top] == ["ruby:external:0-0:puts:unresolved"]
    assert top[0].src.startswith("ruby:app.rb:") and top[0].src.endswith(":file")


def test_a_call_the_repository_defines_is_not_an_external_placeholder(
    tmp_path: Path,
) -> None:
    """The shadowing side of the catalogue question: a project ``system``."""
    (tmp_path / "app.rb").write_text(
        "class Runner\n"
        "  def system(cmd)\n"
        "    cmd\n"
        "  end\n"
        "\n"
        "  def go\n"
        "    system(\"ls\")\n"
        "  end\n"
        "end\n"
    )
    result = analyze_ruby(tmp_path)
    at = _calls_at(result, 7)
    assert [e.dst for e in at] == [_by_name(result)["Runner#system"]]


def test_an_ambiguous_project_name_gets_no_external_placeholder(
    tmp_path: Path,
) -> None:
    """Three classes declare ``run``: the AMB-METHOD guard withholds the bind,
    and the name IS the project's, so ``external`` would be a false claim (and
    would short-name-match a Kernel row of that name)."""
    (tmp_path / "a.rb").write_text(
        "class A\n  def run(x)\n  end\nend\n"
        "class B\n  def run(x)\n  end\nend\n"
        "class C\n  def run(x)\n  end\nend\n"
        "class D\n  def go\n    run(1)\n  end\nend\n"
    )
    result = analyze_ruby(tmp_path)
    assert not [e for e in result.edges if e.dst.startswith("ruby:external:")]


def test_super_and_a_local_read_get_no_placeholder(tmp_path: Path) -> None:
    """``super(1)`` is the keyword, not a method named ``super``; ``foo`` after
    ``foo = 1`` is a variable read, not a call."""
    (tmp_path / "k.rb").write_text(
        "class K < Base\n"
        "  def go(a)\n"
        "    super(1)\n"
        "    foo = 1\n"
        "    foo\n"
        "  end\n"
        "end\n"
    )
    result = analyze_ruby(tmp_path)
    assert not [e for e in result.edges if e.dst.startswith("ruby:external:")]


def test_the_placeholder_reaches_a_kernel_row_and_the_shadow_does_not(
    tmp_path: Path,
) -> None:
    """WI-javaf's hazard in ruby, by the shipped classifier: the ambient Kernel
    call reaches a function-kind overlay row; a project ``system`` does not."""
    overlay = tmp_path / "kernel.yaml"
    overlay.write_text(
        "language: ruby\nstatus: overlay\n"
        "subprocess:\n  - module: Kernel\n    functions: [system]\n"
    )
    catalogs = {"ruby": load_overlay_catalog(overlay)}

    ambient = tmp_path / "ambient"
    ambient.mkdir()
    (ambient / "a.rb").write_text("class W\n  def go\n    system(\"ls\")\n  end\nend\n")
    [edge] = _calls_at(analyze_ruby(ambient), 3)
    hit = classify_call(catalogs, edge.dst, edge.meta, dst_ref=edge.dst_ref)
    assert hit is not None and (hit.boundary, hit.module, hit.name) == (
        "subprocess", "Kernel", "system")

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "a.rb").write_text(
        "class W\n  def system(c)\n  end\n\n  def go\n    system(\"ls\")\n  end\nend\n"
    )
    [edge] = _calls_at(analyze_ruby(shadow), 6)
    assert edge.is_resolved is not False
    assert classify_call(catalogs, edge.dst, edge.meta, dst_ref=edge.dst_ref) is None
