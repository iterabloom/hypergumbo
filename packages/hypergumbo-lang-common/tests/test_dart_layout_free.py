# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Dart declaration reformatted by ``dart format`` keeps its stable id.

``dart format`` splits a parameter list that does not fit on one line, one
parameter per line, and adds a trailing comma; it joins it again (and drops
the comma) when it fits. Through stable-id scheme v15 the hashed text kept
that layout: ``normalize_dart_signature("({\\n  required String text,\\n
int sid,\\n})")`` was ``({\\n  required String,int,})`` where the one-line
form gave ``({required String,int)`` (the last parameter's name is dropped
with the group's closing bracket only when they touch), so reformatting a
file moved ids (WI-kumoh).

``normalize_dart_signature`` now reads the text in its one-line form
(``_dart_one_line``), and the typed formula hashes it layout-free
(``make_typed_stable_id``). The displayed ``signature`` keeps its layout.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_common.dart import _dart_one_line, analyze_dart, normalize_dart_signature

# (case, one-line text, the same reflowed)
_REFLOWED = [
    ("named-group", "({required String text, int sid})",
     "({\n  required String text,\n  int sid,\n})"),
    ("name-on-its-own-line", "({required String text, int sid})",
     "({\n    required String\n        text,\n    int sid,\n  })"),
    ("optional-group", "(int a, [String? b]) List<Map<String, int>>",
     "(int a, [\n    String?   b,\n  ]) List<\n      Map<String,\n          int>>"),
    ("default", "(Map<String, int> x, {int y = ...}) Map<String, int>",
     "(Map< String , int >   x, {\n    int y = ...,\n  }) Map<String, int>"),
    ("plain-list", "(int a, int b)", "(\n  int a,\n  int b,\n)"),
]


@pytest.mark.parametrize(("case", "one_line", "reflowed"), _REFLOWED,
                         ids=[c[0] for c in _REFLOWED])
def test_a_reflowed_signature_reads_as_its_one_line_form(
    case: str, one_line: str, reflowed: str,
) -> None:
    assert _dart_one_line(reflowed) == _dart_one_line(one_line) == one_line
    assert normalize_dart_signature(reflowed) == normalize_dart_signature(one_line)


_ONE_LINE = """\
class K {
  void f({required String text, int sid}) {}
  List<Map<String, int>> g(int a, [String? b]) => [];
  Map<String, int> m(Map<String, int> x, {int y = 1}) => x;
  Map<String, int> fld = {};
  K(this.fld, {int? z});
}
int top(int a, String b) => a;
"""

_DART_FORMATTED = """\
class K {
  void f({
    required String text,
    int sid,
  }) {}
  List<
      Map<String,
          int>> g(
    int a, [
    String? b,
  ]) => [];
  Map<String, int> m(
    Map<String, int> x, {
    int y = 1,
  }) => x;
  Map<String,
      int> fld = {};
  K(
    this.fld, {
    int? z,
  });
}
int top(
  int a,
  String b,
) => a;
"""


def _ids(tmp_path: Path, text: str) -> dict[tuple[str, str], str | None]:
    (tmp_path / "k.dart").write_text(text)
    return {(s.kind, s.name): s.stable_id for s in analyze_dart(tmp_path).symbols
            if s.kind not in ("file", "module")}


def test_a_formatted_file_keeps_every_stable_id(tmp_path: Path) -> None:
    one_line = _ids(tmp_path, _ONE_LINE)
    formatted = _ids(tmp_path, _DART_FORMATTED)
    assert {k for k in one_line if k[0] in ("method", "function", "field")} >= {
        ("method", "K.f"), ("method", "K.g"), ("method", "K.m"), ("field", "K.fld"),
        ("function", "top"),
    }
    assert formatted == one_line


def test_the_displayed_signature_keeps_its_layout(tmp_path: Path) -> None:
    (tmp_path / "k.dart").write_text(_DART_FORMATTED)
    shown = {s.name: s.signature for s in analyze_dart(tmp_path).symbols}
    assert shown["K.f"] == "({\n    required String text,\n    int sid,\n  })"
