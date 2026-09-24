# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag, kotlin: a call edge's src is the function whose span contains it.

The enclosing lookup keyed on the declaration's SHORT name, which several
functions in one file share: ``HttpUrl.parse`` and ``Builder.parse`` in okhttp's
HttpUrl.kt, or one test name under two ``describe`` blocks in detekt. It
returned whichever registered last. On a pinned 26-repo run, 2,014 of 54,037
kotlin call edges (3.7%) named a src whose span does not contain the call.
The cure INV-mozas shipped for elixir applies unchanged: the enclosing
declaration node IS the symbol's node, so its start position identifies it.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.kotlin import analyze_kotlin


def _anchors(tmp_path: Path, source: str) -> list[tuple[str, int, int, int]]:
    """``(src name, src start, src end, call line)`` for every call edge."""
    (tmp_path / "A.kt").write_text(source)
    result = analyze_kotlin(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    out = []
    for edge in result.edges:
        if edge.edge_type == "calls" and edge.line and edge.src in by_id:
            src = by_id[edge.src]
            out.append((src.name, src.span.start_line, src.span.end_line, edge.line))
    return out


_TWO_CLASSES = """\
class HttpUrl {
    fun parse(s: String): Int {
        return helperOne(s)
    }
}

class Builder {
    fun parse(s: String): Int {
        return helperTwo(s)
    }
}

fun helperOne(s: String): Int = 1
fun helperTwo(s: String): Int = 2
"""


def test_a_call_is_anchored_to_the_method_that_contains_it(tmp_path: Path) -> None:
    anchors = _anchors(tmp_path, _TWO_CLASSES)
    assert len(anchors) >= 2, anchors  # reach: both calls were emitted
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)
    by_line = {line: name for name, _, _, line in anchors}
    assert by_line[3] == "HttpUrl.parse"
    assert by_line[9] == "Builder.parse"


def test_the_same_test_name_under_two_blocks(tmp_path: Path) -> None:
    """detekt's shape: one leaf name in two enclosing objects."""
    anchors = _anchors(tmp_path, """\
object StringProperty {
    fun usesDefault() { check(1) }
}

object BooleanProperty {
    fun usesDefault() { check(2) }
}

fun check(x: Int) {}
""")
    assert anchors, "reach"
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)
