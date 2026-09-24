# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag, javascript/typescript: a call edge's src is the function whose
span contains it.

For an arrow function or function expression, the enclosing lookup tried the
enclosing ``var``/``const`` NAME first, through the name-keyed global registry,
and only then the expression's own symbol by position. Two shapes collided:
- one variable name bound twice in a file (``var server = ...`` in two test
  blocks of express's app.listen.js);
- an IIFE bound to the same name as the constructor it returns
  (``var Typeahead = (function () { function Typeahead() {} ... })()``), whose
  prototype methods have no symbol of their own.
135 javascript and 33 typescript call edges on a 26-repo run were anchored to
the wrong function. The expression's own symbol, found by position, is tried
first now.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.js_ts import analyze_javascript

_SOURCE = """\
function one() {}
function two() {}

describe('a', function () {
  var server = makeServer(function () {
    one();
  });
});

describe('b', function () {
  var server = makeServer(function () {
    two();
  });
});

var Typeahead = (function () {
  function Typeahead(o) {
    one();
  }
  Typeahead.prototype.open = function open() {
    two();
  };
  return Typeahead;
})();

const handler = () => {
  one();
};

const picked = [1, 2].filter((x) => two());

const logger = {
  info: (msg) => {
    one();
  },
};
"""


def test_every_call_is_inside_its_src(tmp_path: Path) -> None:
    (tmp_path / "a.js").write_text(_SOURCE)
    result = analyze_javascript(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    anchors = [
        (by_id[e.src].name, by_id[e.src].span.start_line, by_id[e.src].span.end_line, e.line)
        for e in result.edges
        if e.edge_type == "calls" and e.line and e.src in by_id and by_id[e.src].kind != "file"
    ]
    assert {a[3] for a in anchors} >= {6, 12, 18, 21, 27, 30, 34}, anchors  # reach
    # A call in an object-literal arrow with no symbol of its own is credited to
    # the module variable that contains it, as the name lookup did before.
    assert [a[0] for a in anchors if a[3] == 34] == ["logger"], anchors
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)
