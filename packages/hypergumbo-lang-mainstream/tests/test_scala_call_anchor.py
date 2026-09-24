# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag, scala: a call edge's src is the function whose span contains it.

The enclosing lookup keyed on the short name, which the ``apply`` of every
companion in a file shares, as does ``show`` across tapir's EndpointIO.scala
classes. On a pinned 26-repo run, 964 of 16,531 scala call edges named a src
whose span does not contain the call. Found by position now, like kotlin and
elixir.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.scala import analyze_scala


def _anchors(tmp_path: Path, source: str) -> list[tuple[str, int, int, int]]:
    (tmp_path / "A.scala").write_text(source)
    result = analyze_scala(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    return [
        (by_id[e.src].name, by_id[e.src].span.start_line, by_id[e.src].span.end_line, e.line)
        for e in result.edges
        if e.edge_type == "calls" and e.line and e.src in by_id
    ]


def test_same_named_methods_in_two_classes(tmp_path: Path) -> None:
    anchors = _anchors(tmp_path, """\
object Helpers {
  def one(): Int = 1
  def two(): Int = 2
}

class OneOf {
  def show(): Int = {
    Helpers.one()
  }
}

class Mapped {
  def show(): Int = {
    Helpers.two()
  }
}
""")
    assert len([a for a in anchors if a[3] in (8, 14)]) == 2, anchors  # reach
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)


def test_every_companion_apply(tmp_path: Path) -> None:
    anchors = _anchors(tmp_path, """\
object Invalid {
  def apply(x: Int): Int = helper(x)
}

object Valid {
  def apply(x: Int): Int = helper(x + 1)
}

object Top {
  def helper(x: Int): Int = x
}
""")
    assert anchors, "reach"
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)
