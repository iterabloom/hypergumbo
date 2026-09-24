# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag, ruby and c#: a call edge's src is the method whose span contains it.

Both looked the enclosing method up by its short name. Ruby lets a file define a
method twice (a reopened class, a redefinition: postal's MessageInspection#scan).
C# shares a leaf name across classes of one file (livebook's ElixirKit.cs:
``API.Stop`` / ``Release.Stop``). Found by the declaration's position now.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.csharp import analyze_csharp
from hypergumbo_lang_mainstream.ruby import analyze_ruby


def _anchors(result) -> list[tuple[str, int, int, int]]:
    by_id = {s.id: s for s in result.symbols}
    return [
        (by_id[e.src].name, by_id[e.src].span.start_line, by_id[e.src].span.end_line, e.line)
        for e in result.edges if e.edge_type == "calls" and e.line and e.src in by_id
    ]


def test_ruby_a_method_defined_twice(tmp_path: Path) -> None:
    (tmp_path / "inspection.rb").write_text("""\
def one; 1; end
def two; 2; end

class MessageInspection
  def scan
    one
  end
end

class MessageInspection
  def scan
    two
  end
end
""")
    anchors = _anchors(analyze_ruby(tmp_path))
    assert {a[3] for a in anchors} >= {6, 12}, anchors  # reach: both calls
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)


def test_csharp_one_leaf_name_in_two_classes(tmp_path: Path) -> None:
    (tmp_path / "ElixirKit.cs").write_text("""\
public static class Helpers
{
    public static void One() {}
    public static void Two() {}
}

public class API
{
    public void Stop()
    {
        Helpers.One();
    }
}

public class Release
{
    public void Stop()
    {
        Helpers.Two();
    }
}
""")
    anchors = _anchors(analyze_csharp(tmp_path))
    assert {a[3] for a in anchors} >= {11, 19}, anchors  # reach: both calls
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)
