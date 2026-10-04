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


# --- rust / swift / kotlin / scala (INV-bamij's second batch) ---------------

_RUST = (
    "use std::sync::LazyLock;\n"
    "static CONFIG: LazyLock<String> = LazyLock::new(|| {\n"
    '    std::fs::read_to_string("/etc/app.toml").unwrap()   // PKG_LITERAL\n'
    "});\n"
    "fn named() {\n"
    '    let inner = || { std::fs::read_to_string("x").unwrap() };   // IN_NAMED\n'
    "    inner();\n"
    "}\n"
)
_SWIFT = (
    "import Foundation\n"
    'let handler = { FileManager.default.contents(atPath: "/etc/x") }   // PKG_LITERAL\n'
    'func named() { let inner = { FileManager.default.contents(atPath: "z") }; inner() }   // IN_NAMED\n'
)
_KOTLIN = (
    "import java.io.File\n"
    'val handler = { File("/etc/x").readText() }   // PKG_LITERAL\n'
    'val cmd = mapOf("run" to { File("/etc/y").readText() })   // PKG_FIELD_LITERAL\n'
    'fun named() { val inner = { File("z").readText() }; inner() }   // IN_NAMED\n'
)
_SCALA = (
    "import scala.io.Source\n"
    "object M {\n"
    '  val handler = () => Source.fromFile("/etc/x").mkString   // PKG_LITERAL\n'
    '  def named(): Unit = { val inner = () => Source.fromFile("z").mkString; inner() }   // IN_NAMED\n'
    "}\n"
)


def _batch2():
    from hypergumbo_lang_mainstream.kotlin import analyze_kotlin
    from hypergumbo_lang_mainstream.rust import analyze_rust
    from hypergumbo_lang_mainstream.scala import analyze_scala
    from hypergumbo_lang_mainstream.swift import analyze_swift

    # (file name, source, analyzer, module-level lines, expected anchor kind, named line)
    return [
        ("src.rs", _RUST, analyze_rust, (3,), "file", 6),
        ("m.swift", _SWIFT, analyze_swift, (2,), "file", 3),
        ("m.kt", _KOTLIN, analyze_kotlin, (2, 3), "file", 4),
        ("M.scala", _SCALA, analyze_scala, (3,), "object", 4),
    ]


@pytest.mark.parametrize("case", range(4), ids=["rust", "swift", "kotlin", "scala"])
def test_batch2_module_level_calls_are_anchored(tmp_path: Path, case: int) -> None:
    name, text, analyze, lines, kind, _named = _batch2()[case]
    anchors = _anchors(tmp_path, name, text, analyze)
    for line in lines:
        assert kind in anchors.get(line, set()), (line, anchors)


@pytest.mark.parametrize("case", range(4), ids=["rust", "swift", "kotlin", "scala"])
def test_batch2_a_named_function_keeps_its_calls(tmp_path: Path, case: int) -> None:
    name, text, analyze, _lines, _kind, named = _batch2()[case]
    anchors = _anchors(tmp_path, name, text, analyze)
    assert anchors.get(named) and not anchors[named] & {"file", "object"}, anchors


def test_swift_and_kotlin_type_bodies_anchor_on_the_type(tmp_path: Path) -> None:
    """A Swift ``init`` (which the analyzer emits no symbol for) and a Kotlin
    class property initialiser are in a type, not at file level."""
    from hypergumbo_lang_mainstream.kotlin import analyze_kotlin
    from hypergumbo_lang_mainstream.swift import analyze_swift

    swift = (tmp_path / "s")
    swift.mkdir()
    sw = _anchors(swift, "a.swift",
                  "struct Builder {\n    var n = 0\n    init() {\n        n = compute()\n    }\n}\n",
                  analyze_swift)
    assert sw.get(4) and "file" not in sw[4], sw
    kt_dir = (tmp_path / "k")
    kt_dir.mkdir()
    kt = _anchors(kt_dir, "a.kt", "class Conf {\n    val feat = FeatureConfig()\n}\n", analyze_kotlin)
    assert kt.get(2) == {"class"}, kt


# --- java / c / cpp (INV-bamij's third batch) --------------------------------

_JAVA = (
    "import java.nio.file.*;\n"
    "public class M {\n"
    '    static final Runnable HANDLER = () -> { Files.readString(Path.of("/etc/x")); };   // PKG_LITERAL\n'
    '    static { Files.readString(Path.of("/etc/s")); }   // STATIC_INIT\n'
    '    void named() { Runnable inner = () -> { Files.readString(Path.of("z")); }; inner.run(); }   // IN_NAMED\n'
    "}\n"
)
_C = (
    "#include <stdio.h>\n"
    'static FILE *log_fp = fopen("/etc/x", "r");   /* PKG_INIT_CALL */\n'
    'void named(void) { fopen("z", "r"); }   /* IN_NAMED */\n'
)
_CPP = (
    "#include <fstream>\n"
    "#include <cstdio>\n"
    'static FILE *log_fp = std::fopen("/etc/x", "r");   // PKG_INIT_CALL\n'
    'static auto handler = []() { std::fopen("/etc/y", "r"); };   // PKG_LITERAL\n'
    'void named() { auto inner = []() { std::fopen("z", "r"); }; inner(); }   // IN_NAMED\n'
)


def _batch3():
    from hypergumbo_lang_mainstream.c import analyze_c
    from hypergumbo_lang_mainstream.cpp import analyze_cpp
    from hypergumbo_lang_mainstream.java import analyze_java

    # (file name, source, analyzer, module-level lines, expected anchor kind, named line)
    return [
        ("M.java", _JAVA, analyze_java, (3, 4), "class", 5),
        ("m.c", _C, analyze_c, (2,), "file", 3),
        ("m.cpp", _CPP, analyze_cpp, (3, 4), "file", 5),
    ]


@pytest.mark.parametrize("case", range(3), ids=["java", "c", "cpp"])
def test_batch3_module_level_calls_are_anchored(tmp_path: Path, case: int) -> None:
    name, text, analyze, lines, kind, _named = _batch3()[case]
    anchors = _anchors(tmp_path, name, text, analyze)
    for line in lines:
        assert kind in anchors.get(line, set()), (line, anchors)


@pytest.mark.parametrize("case", range(3), ids=["java", "c", "cpp"])
def test_batch3_a_named_function_keeps_its_calls(tmp_path: Path, case: int) -> None:
    name, text, analyze, _lines, _kind, named = _batch3()[case]
    anchors = _anchors(tmp_path, name, text, analyze)
    assert anchors.get(named) and not anchors[named] & {"file", "class"}, anchors


def test_cpp_an_extern_declaration_at_file_scope_is_not_a_construction(tmp_path: Path) -> None:
    """Seeding the walk with the file anchor must not let the stack-construction
    arm read ``extern Widget w;`` or a member prototype as a construction."""
    from hypergumbo_lang_mainstream.cpp import analyze_cpp

    (tmp_path / "w.hpp").write_text(
        "class Widget { public: static Widget Create(); };\nextern Widget theWidget;\n"
    )
    analysis = analyze_cpp(tmp_path)
    assert not [e for e in analysis.edges if e.edge_type == "instantiates"], [
        (e.src, e.dst, e.line) for e in analysis.edges if e.edge_type == "instantiates"
    ]


def test_c_a_call_in_a_function_with_no_symbol_stands_in_on_the_file(tmp_path: Path) -> None:
    """The file anchor is for code in NO function. A call inside a function the
    analyzer could not name (a macro-named definition, WI-tikop) is drawn from the
    file too -- dropping it said there was no call -- but never as a plausible
    file-scope edge: it carries ``src_stands_in_for``, so the defect stays
    visible on the edge instead of hidden behind it."""
    from hypergumbo_core.analyze.edge_source import SRC_STANDS_IN_FOR, UNNAMED_DEFINITION
    from hypergumbo_lang_mainstream.c import analyze_c

    (tmp_path / "m.c").write_text(
        '#include <stdio.h>\nint PFX(f)(void) { fopen("z", "r"); return 0; }\n'
    )
    calls = [e for e in analyze_c(tmp_path).edges if e.edge_type == "calls"]
    assert [
        (e.src.endswith(":1-1:file:file"), (e.meta or {}).get(SRC_STANDS_IN_FOR))
        for e in calls
    ] == [(True, UNNAMED_DEFINITION)], [(e.src, e.meta) for e in calls]


@pytest.mark.parametrize("name", ["m.c", "m.cpp"])
def test_attribute_and_preprocessor_syntax_is_not_a_file_scope_call(
    tmp_path: Path, name: str,
) -> None:
    """``__attribute__ ((format (...)))`` and ``#if __has_attribute(x)`` parse as
    calls; at file scope they must not become call edges on the file."""
    from hypergumbo_lang_mainstream.c import analyze_c
    from hypergumbo_lang_mainstream.cpp import analyze_cpp

    (tmp_path / name).write_text(
        "#if __has_attribute(__nonstring__)\nint a;\n#endif\n"
        "int xp (const char *fmt, ...) __attribute__ ((format (printf, 1, 2)));\n"
    )
    analyze = analyze_c if name.endswith(".c") else analyze_cpp
    calls = [e.dst for e in analyze(tmp_path).edges if e.edge_type == "calls"]
    assert calls == [], calls


def test_java_type_lookup_needs_its_position_index_and_a_type(tmp_path: Path) -> None:
    """``_get_enclosing_type_symbol`` answers only from the position index it is
    handed, and None for a node inside no type (the file anchor then applies)."""
    import tree_sitter
    from tree_sitter_language_pack import get_language

    from hypergumbo_lang_mainstream.java import _get_enclosing_type_symbol

    tree = tree_sitter.Parser(get_language("java")).parse(b"import a.B;\n")
    node = tree.root_node.children[0]
    assert _get_enclosing_type_symbol(node, tmp_path / "M.java", None) is None
    assert _get_enclosing_type_symbol(node, None, {("x", 1, 0): None}) is None  # type: ignore[dict-item]
    assert _get_enclosing_type_symbol(node, tmp_path / "M.java", {("x", 9, 9): None}) is None  # type: ignore[dict-item]
