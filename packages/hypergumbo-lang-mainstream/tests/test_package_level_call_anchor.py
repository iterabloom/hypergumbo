# SPDX-License-Identifier: AGPL-3.0-or-later
"""A call at module / package level emits a calls edge, anchored on the file.

INV-bamij (INV-nopoh's family). Every call site an analyzer parses must emit a
calls edge anchored on SOME symbol (INV-foluz). In nine tree-sitter analyzers a
call inside a module-level function literal or initializer emitted nothing: the
enclosing-scope walk looks for a function or method, returns None at the file
root, and the call arm drops the call. The same call inside a named function
emits in every one of them, which is each test's control.

The anchor of last resort is the file's pseudo-symbol (``make_file_id``), the
convention python and php already follow for module-level code. Ruby and Lua
are where module-level code is the norm (scripts, Rakefiles, config/*.rb,
every ``require``-d Lua module), so they come first.

Fixtures are INV-bamij's own sweep fixtures
(~/hypergumbo_lab_notebook/nopoh_pkglit_09032026/sweep/), one line per shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.lua import analyze_lua
from hypergumbo_lang_mainstream.ruby import analyze_ruby

_RUBY = (
    'HANDLER = -> { File.read("/etc/x") }   # PKG_LITERAL\n'
    'CMD = { run: proc { File.read("/etc/y") } }   # PKG_FIELD_LITERAL\n'
    "def named\n"
    '  inner = -> { File.read("z") }   # IN_NAMED\n'
    "  inner.call\n"
    "end\n"
    'File.write("t", "x")   # TOP_LEVEL_STATEMENT\n'
    "class Widget\n"
    '  CONFIG = File.read("c")   # CLASS_BODY\n'
    "end\n"
)

_LUA = (
    'local handler = function() io.open("/etc/x") end   -- PKG_LITERAL\n'
    'local cmd = { run = function() io.open("/etc/y") end }   -- PKG_FIELD_LITERAL\n'
    'function named() local inner = function() io.open("z") end; inner() end   -- IN_NAMED\n'
    'print("top level")   -- TOP_LEVEL_STATEMENT\n'
)


def _anchors(tmp_path: Path, name: str, text: str, analyze) -> dict[int, set[str]]:
    (tmp_path / name).write_text(text)
    analysis = analyze(tmp_path)
    kinds = {s.id: s.kind for s in analysis.symbols}
    out: dict[int, set[str]] = {}
    for e in analysis.edges:
        if e.edge_type == "calls" and e.line is not None:
            # The anchor's kind: a named function, or the file pseudo-symbol.
            kind = kinds.get(e.src) or e.src.rsplit(":", 1)[-1]
            out.setdefault(e.line, set()).add(kind)
    return out


@pytest.mark.parametrize(("name", "text", "analyze"), [
    ("m.rb", _RUBY, analyze_ruby),
    ("m.lua", _LUA, analyze_lua),
], ids=["ruby", "lua"])
def test_module_level_calls_emit_on_the_file(
    tmp_path: Path, name: str, text: str, analyze,
) -> None:
    anchors = _anchors(tmp_path, name, text, analyze)
    for line in (1, 2, 4 if name.endswith(".lua") else 7):
        assert "file" in anchors.get(line, set()), (line, anchors)


@pytest.mark.parametrize(("name", "text", "analyze", "line"), [
    ("m.rb", _RUBY, analyze_ruby, 4),
    ("m.lua", _LUA, analyze_lua, 3),
], ids=["ruby", "lua"])
def test_a_call_inside_a_named_function_keeps_its_function(
    tmp_path: Path, name: str, text: str, analyze, line: int,
) -> None:
    """The control: a literal inside a named function is still attributed to
    that function, never moved to the file."""
    anchors = _anchors(tmp_path, name, text, analyze)
    assert anchors.get(line) and "file" not in anchors[line], anchors


def test_a_ruby_class_body_call_is_anchored_on_its_class(tmp_path: Path) -> None:
    """Class-body code (a constant initialiser here; ``include``, a Rails
    ``has_many`` in real code) is outside every method but inside a class; the
    class is the nearer anchor. The receiverless DSL calls themselves emit no
    edge even inside a method when they resolve nowhere -- a separate defect
    (INV-foluz's shape in ruby), filed rather than folded in."""
    anchors = _anchors(tmp_path, "m.rb", _RUBY, analyze_ruby)
    assert anchors.get(9) == {"class"}, anchors
