# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag, javascript/typescript: a call edge's src is the function whose
span contains it.

For an arrow function or function expression, the enclosing lookup tries the
expression's own symbol, found by position, before the enclosing
``var``/``const`` NAME. Looking the name up first, through the name-keyed
global registry, anchors calls to the wrong function in two shapes:
- one variable name bound twice in a file (``var server = ...`` in two test
  blocks);
- an IIFE bound to the same name as the constructor it returns
  (``var Typeahead = (function () { function Typeahead() {} ... })()``), whose
  prototype methods have no symbol of their own.
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

function setup() {
  const handlers = {
    info: (m) => two(),
  };
  return handlers;
}
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
    assert {a[3] for a in anchors} >= {6, 12, 18, 21, 27, 30, 34, 40}, anchors  # reach
    # A call in an object-literal arrow with no symbol of its own is credited to
    # the module variable that contains it.
    assert [a[0] for a in anchors if a[3] == 34] == ["logger"], anchors
    # A LOCAL object has no symbol and neither has its arrow: the walk moves on
    # past the declarator to the function that contains it.
    assert [a[0] for a in anchors if a[3] == 40] == ["setup"], anchors
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)


# A .vue <script> block is positioned by a line offset, and the position
# lookup must apply it (WI-pizan). Lines 6-12 hold one-line callbacks. Without
# the offset, the callback at line 13 would be looked up at "line 8", where
# another callback really starts, and the one at line 18 at "line 13", the
# callback above: both would credit their calls to a neighbour. The callback
# at line 23 would find nothing at "line 18" and fall through to the file.
_VUE = """\
<template>
  <slot />
</template>

<script setup lang="ts">
on('a', () => one())
on('b', () => two())
on('c', () => one())
on('d', () => two())
on('e', () => one())
on('f', () => two())
on('g', () => one())
on('h', () => {
  two()
  one()
})

on('i', () => {
  two()
})


on('j', () => {
  one()
})

function one() {}
function two() {}
</script>
"""


def test_a_vue_callback_is_credited_with_its_own_calls(tmp_path: Path) -> None:
    (tmp_path / "A.vue").write_text(_VUE)
    result = analyze_javascript(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    anchors = {
        e.line: by_id[e.src]
        for e in result.edges
        if e.edge_type == "calls" and e.line and e.src in by_id
    }
    assert set(anchors) >= {6, 12, 14, 15, 19, 24}, sorted(anchors)  # reach
    for line in (6, 12, 14, 15, 19, 24):
        src = anchors[line]
        assert src.name == "_cb_on", (line, src.name)
        assert src.span.start_line <= line <= src.span.end_line, (line, src.span)
