# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the PHP analyzer binds itself and what it reports (ADR-0064, batch 5a).

The analyzer binds a call only on PHP's own evidence in hand: ``$this->m()``,
``self::m()`` and ``static::m()`` whose ``m`` the enclosing type declares, both
found by position in the file (``own_type``). Every other call goes out as an
unresolved stub with the facts the call site has, and the shared linkers
decide through the file's import table:

* ``f()`` / ``\\A\\f()``: the callee as written, no fact;
* ``$x->m()``: ``receiver_type_hint`` when the file writes ``$x``'s type
  (``_PhpTypes``: a parameter, ``new``, a return type of this file, ``catch``,
  a typed or promoted property), else nothing, and ``stdlib_member_name`` when
  PHP's own classes define ``m``;
* ``$this->m()`` / ``self::m()`` the type does not declare: ``enclosing_class``;
  ``parent::m()``: ``enclosing_class`` and ``super_receiver``;
* ``Name::m()``: ``qualifier`` as written; ``new Name()``: an ``instantiates``
  stub of the type as written.

The first part pins each route at the analyzer; the second runs the whole
pipeline with a NAMESAKE in another namespace beside each target, so that a
bind by name would show as wrong or as a tie.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/php.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.php import (
    _PHP_LIBRARY_METHOD_NAMES,
    _get_php_parser,
    _php_uses,
    analyze_php,
)


def _analyze(root: Path, files: dict[str, str]):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return analyze_php(root)


def _calls(root: Path, text: str) -> list[Edge]:
    """The call-family edges of one file ``a.php``."""
    result = _analyze(root, {"a.php": text})
    return [e for e in result.edges if e.edge_type in ("calls", "instantiates")]


def _stub(edges: list[Edge], callee: str, line: int | None = None) -> dict:
    """The facts of the one unresolved stub of ``callee`` (on ``line``)."""
    found = [
        e for e in edges if not e.is_resolved and (e.meta or {}).get("callee_name") == callee
        and (line is None or e.line == line)
    ]
    assert len(found) == 1, [(e.line, e.dst, e.meta) for e in edges]
    return {k: v for k, v in found[0].meta.items()
            if k not in ("callee_name", "evidence_lang", "evidence_type", "access_mode")}


# --- the table --------------------------------------------------------------------


def _uses(text: str):
    source = text.encode()
    parser = _get_php_parser()
    assert parser is not None
    return _php_uses(parser.parse(source), source)


def test_every_kind_of_use_is_in_the_table_in_the_declared_name_form() -> None:
    uses = _uses(
        "<?php\nnamespace App\\Http;\n"
        "use A\\B\\C;\nuse A\\B\\D as E;\nuse function A\\b\\f;\n"
        "use function A\\b\\g as gg;\nuse const A\\B\\K;\n"
        "use X\\{Y, Z as Q, function h, const L};\nuse function P\\{i, j};\n"
        "use Foo;\nuse Bar as Baz;\n"
    )
    named = {
        local: (uses.scope.named(local).module, uses.scope.named(local).name)
        for local in ("C", "E", "f", "gg", "Y", "Q", "h", "i", "j", "Foo", "Baz")
    }
    assert named == {
        "C": ("A\\B", "C"), "E": ("A\\B", "D"), "f": ("A\\b", "f"), "gg": ("A\\b", "g"),
        "Y": ("X", "Y"), "Q": ("X", "Z"), "h": ("X", "h"), "i": ("P", "i"), "j": ("P", "j"),
        "Foo": ("", "Foo"), "Baz": ("", "Bar"),
    }
    # A constant is never called: ``use const`` is left out of the table, and
    # stays in the aliases (the symbol pass and the module slot read those).
    assert uses.scope.named("K") is None and uses.scope.named("L") is None
    assert uses.aliases["K"] == "A\\B\\K"
    assert "Foo" not in uses.aliases
    assert uses.scope.wildcard_modules == ()
    # The single-holder name step stays off for PHP (see ``_PhpUses``).
    assert uses.scope.library_names_screened is False


def test_every_file_hands_over_a_table_keyed_like_its_symbols(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {
        "a.php": "<?php\nnamespace App;\nuse Lib\\Conn;\nfunction f() {}\n",
        "b.php": "<?php\nfunction g() {}\n",
    })
    paths = {s.path for s in result.symbols}
    assert set(result.import_scopes) == paths
    a_table = result.import_scopes[str(tmp_path / "a.php")]
    assert a_table.named("Conn").module == "Lib"
    assert result.import_scopes[str(tmp_path / "b.php")].named("Conn") is None


# --- calls on the enclosing object ------------------------------------------------------


def test_this_and_self_bind_the_enclosing_types_own_method_found_by_position(tmp_path: Path) -> None:
    edges = _calls(tmp_path, """<?php
class Box {
    public function a() {}
    public static function s() {}
    public function go() {
        $this->a();
        self::s();
        static::s();
        $this->inherited();
        self::fromParent();
        parent::go();
    }
}
""")
    bound = {(e.line, e.dst.split(":")[-2], e.evidence_type, e.meta["resolved_by"])
             for e in edges if e.is_resolved}
    assert bound == {
        (6, "Box.a", "ast_method_this", "own_type"),
        (7, "Box.s", "ast_static_call", "own_type"),
        (8, "Box.s", "ast_static_call", "own_type"),
    }
    assert _stub(edges, "inherited") == {"enclosing_class": "Box", "call_construct": "method"}
    assert _stub(edges, "fromParent") == {"enclosing_class": "Box", "call_construct": "method"}
    assert _stub(edges, "go") == {
        "enclosing_class": "Box", "call_construct": "method", "super_receiver": True,
    }


def test_the_own_method_is_the_one_of_the_type_written_around_the_call(tmp_path: Path) -> None:
    """Two classes of one name in two namespaces of one file: each ``$this->a()``
    binds its own class's ``a``, not the last one written."""
    edges = _calls(tmp_path, """<?php
namespace One {
    class Box {
        public function a() {}
        public function go() { $this->a(); }
    }
}
namespace Two {
    class Box {
        public function a() {}
        public function go() { $this->a(); }
    }
}
""")
    pairs = {(e.src.split(":")[2], e.dst.split(":")[2]) for e in edges if e.is_resolved}
    assert pairs == {("5-5", "4-4"), ("11-11", "10-10")}


# --- calls that name their owner, and constructions ---------------------------------------


def test_a_static_call_names_its_owner_as_written(tmp_path: Path) -> None:
    edges = _calls(tmp_path, """<?php
namespace App;
use Lib\\Util;
function go() {
    Util::make();
    \\Other\\Util::make();
    Sub\\Tool::run();
}
""")
    assert _stub(edges, "make", 5) == {"qualifier": "Util", "call_construct": "method"}
    assert _stub(edges, "make", 6) == {"qualifier": "\\Other\\Util", "call_construct": "method"}
    assert _stub(edges, "run") == {"qualifier": "Sub\\Tool", "call_construct": "method"}
    assert not [e for e in edges if e.is_resolved]


def test_new_is_a_construction_stub_of_the_type_as_written(tmp_path: Path) -> None:
    edges = _calls(tmp_path, """<?php
class Box {
    public function go($cls) {
        new Conn();
        new \\Lib\\Pool();
        new self();
        new static();
        new $cls();
        new class {};
    }
}
""")
    stubs = sorted((e.line, (e.meta or {})["callee_name"], e.evidence_type)
                   for e in edges if e.edge_type == "instantiates")
    assert stubs == [(4, "Conn", "ast_new"), (5, "\\Lib\\Pool", "ast_new")]
    assert not [e for e in edges if e.is_resolved]


def test_a_bare_call_goes_out_with_no_fact_and_its_use_path_as_its_module(tmp_path: Path) -> None:
    """A bare ``f()`` in PHP is never a method: no ``enclosing_class``."""
    edges = _calls(tmp_path, """<?php
namespace App;
use function Lib\\helper;
class Box {
    public function go() {
        helper();
        local();
        \\Lib\\other();
    }
}
""")
    assert _stub(edges, "helper") == {}
    assert _stub(edges, "local") == {}
    assert _stub(edges, "\\Lib\\other") == {}
    imported = next(e for e in edges if (e.meta or {}).get("callee_name") == "helper")
    assert imported.dst_ref is not None and imported.dst_ref.module_path == "Lib\\helper"


# --- the type a variable's declaration writes ---------------------------------------------


def _hint(edges: list[Edge], callee: str, line: int | None = None) -> object:
    return _stub(edges, callee, line).get("receiver_type_hint")


def test_a_parameter_types_its_variable(tmp_path: Path) -> None:
    edges = _calls(tmp_path, """<?php
class Box {
    public function go(Conn $a, ?Pool $b, Pool|null $c, A|B $d, int $e, $f, \\Lib\\Conn $g, self $h, Conn ...$i, mixed $j) {
        $a->m1();
        $b->m2();
        $c->m3();
        $d->m4();
        $e->m5();
        $f->m6();
        $g->m7();
        $h->m8();
        $i->m9();
        $j->m10();
    }
}
""")
    hints = {f"m{n}": _hint(edges, f"m{n}") for n in range(1, 11)}
    assert hints == {
        "m1": "Conn", "m2": "Pool", "m3": "Pool", "m4": None, "m5": None, "m6": None,
        "m7": "\\Lib\\Conn", "m8": "Box", "m9": None, "m10": None,
    }
    assert _stub(edges, "m6") == {"call_construct": "method"}


def test_a_construction_a_return_type_and_a_catch_type_their_variable(tmp_path: Path) -> None:
    edges = _calls(tmp_path, """<?php
function make(): Conn { return new Conn(); }
function maybe(): ?Pool { return null; }
class Box {
    public function own(): static { return $this; }
    public function go() {
        $a = new Conn();
        $a->m1();
        $b = make();
        $b->m2();
        $c = maybe();
        $c->m3();
        $d = $this->own();
        $d->m4();
        $e = unknown();
        $e->m5();
        make()->m6();
        (new Pool())->m7();
        try {} catch (BadThing $f) { $f->m8(); }
        try {} catch (One | Two $g) { $g->m9(); }
        $h = $a;
        $h->m10();
    }
}
""")
    hints = {f"m{n}": _hint(edges, f"m{n}") for n in range(1, 11)}
    assert hints == {
        "m1": "Conn", "m2": "Conn", "m3": "Pool", "m4": "Box", "m5": None, "m6": "Conn",
        "m7": "Pool", "m8": "BadThing", "m9": None, "m10": "Conn",
    }


def test_what_a_value_writes_and_what_it_does_not(tmp_path: Path) -> None:
    edges = _calls(tmp_path, """<?php
function make(): Conn { return new Conn(); }
class Box extends Base {
    private Conn $conn;
    public function own(): Pool { return new Pool(); }
    public function go($other, $cls, $fn) {
        $a = new \\Lib\\Pool();
        $a->m1();
        $b = new self();
        $b->m2();
        $c = new parent();
        $c->m3();
        $d = new $cls();
        $d->m4();
        $e = \\Lib\\make();
        $e->m5();
        $f = $fn();
        $f->m6();
        $g = $other->own();
        $g->m7();
        $h = 5;
        $h->m8();
        $i = $this?->own();
        $i->m9();
        $j = $this?->conn;
        $j->m10();
        $fn();
    }
}
""")
    hints = [_hint(edges, f"m{n}") for n in range(1, 11)]
    assert hints == ["\\Lib\\Pool", "Box", None, None, None, None, None, None, "Pool", "Conn"]
    # ``$fn()``: a call of a value names no function, nothing is reported.
    assert not [e for e in edges if (e.meta or {}).get("callee_name") == "$fn"]


def test_a_write_that_writes_no_type_hides_the_earlier_one(tmp_path: Path) -> None:
    edges = _calls(tmp_path, """<?php
function go(Conn $a, Conn $b, Conn $c, Conn $d, Conn $e, $y) {
    $a = &$y;
    $a->m1();
    $b .= 'x';
    $b->m2();
    [$c, $k] = $y;
    $c->m3();
    global $d;
    $d->m4();
    static $e;
    $e->m5();
}
""")
    assert [_hint(edges, f"m{n}") for n in range(1, 6)] == [None] * 5


def test_the_last_write_before_the_use_types_the_variable(tmp_path: Path) -> None:
    edges = _calls(tmp_path, """<?php
function go(Conn $x, $list) {
    $x->m1();
    $x = new Pool();
    $x->m2();
    $x = unknown();
    $x->m3();
    foreach ($list as $x) { $x->m4(); }
    $y = new Conn();
    $y = $y->next();
    $y->m5();
}
""")
    assert [_hint(edges, f"m{n}") for n in range(1, 6)] == ["Conn", "Pool", None, None, None]


def test_a_closure_sees_an_outer_variable_through_use_and_an_arrow_function_always(
    tmp_path: Path,
) -> None:
    edges = _calls(tmp_path, """<?php
function go() {
    $x = new Conn();
    $f = function () use ($x) { $x->m1(); };
    $g = function () { $x->m2(); };
    $h = fn() => $x->m3();
    $k = function (Pool $x) { $x->m4(); };
    $l = function () { $y = new Pool(); };
    $y->m5();
}
""")
    assert [_hint(edges, f"m{n}") for n in range(1, 6)] == ["Conn", None, "Conn", "Pool", None]


def test_a_property_is_typed_by_its_declaration(tmp_path: Path) -> None:
    edges = _calls(tmp_path, """<?php
class Box {
    private ?Conn $conn;
    private static Pool $pool;
    private $loose;
    public function __construct(private Repo $repo, int $n) {}
    public function go() {
        $this->conn->m1();
        self::$pool->m2();
        $this->repo->m3();
        $this->loose->m4();
        $this->missing->m5();
        $this->conn?->m6();
    }
}
""")
    assert [_hint(edges, f"m{n}") for n in range(1, 7)] == [
        "Conn", "Pool", "Repo", None, None, "Conn",
    ]


def test_a_value_of_unknown_type_marks_the_names_phps_own_classes_define(tmp_path: Path) -> None:
    edges = _calls(tmp_path, """<?php
function go($a, Conn $b, $c, $m) {
    $a->format('Y');
    $b->format('Y');
    $a->renderPage();
    $c::getMessage();
    $a->$m();
    $a?->rollback();
}
""")
    assert _stub(edges, "format", 3) == {"call_construct": "method", "stdlib_member_name": True}
    assert _stub(edges, "format", 4) == {"call_construct": "method", "receiver_type_hint": "Conn"}
    assert _stub(edges, "renderPage") == {"call_construct": "method"}
    assert _stub(edges, "getMessage") == {"call_construct": "method", "stdlib_member_name": True}
    assert _stub(edges, "rollback") == {"call_construct": "method", "stdlib_member_name": True}
    # ``$a->$m()``: the method's name is a value, nothing is reported.
    assert not [e for e in edges if (e.meta or {}).get("callee_name") == "$m"]
    assert {"getmessage", "format", "rollback", "offsetget", "count"} <= _PHP_LIBRARY_METHOD_NAMES


# --- the caller ----------------------------------------------------------------------------


def test_the_caller_is_found_by_position_not_by_its_name(tmp_path: Path) -> None:
    """Two files declare ``Job::run``: the caller lookup by name in a run-wide
    table took the one written last and dropped the other file's calls. An
    anonymous class's method is a caller too."""
    files = {
        f"{side}.php": f"<?php\nclass Job {{\n    public function run() {{ $this->{side}(); }}\n"
        f"    public function {side}() {{}}\n}}\n"
        for side in ("a", "b")
    }
    files["m.php"] = "<?php\nreturn new class {\n    public function up() { $this->down(); }\n" \
                     "    public function down() {}\n};\n"
    result = _analyze(tmp_path, files)
    names = {s.id: (Path(s.path).name, s.name) for s in result.symbols}
    pairs = {(names[e.src], e.dst.split(":")[-2]) for e in result.edges
             if e.edge_type == "calls" and e.is_resolved}
    assert pairs == {(("a.php", "Job.run"), "Job.a"), (("b.php", "Job.run"), "Job.b"),
                     (("m.php", "up"), "down")}


def test_this_in_an_anonymous_class_is_the_anonymous_object(tmp_path: Path) -> None:
    """Inside ``new class { ... }`` written in ``Outer::make``, ``$this->a()`` is
    the anonymous class's ``a`` or nothing the analyzer can name: never
    ``Outer::a``. The anonymous class has no name to hand over."""
    edges = _calls(tmp_path, """<?php
class Outer {
    public function a() {}
    public function make() {
        return new class extends Base {
            public function b() {}
            public function run() {
                $this->b();
                $this->a();
                parent::boot();
                self::c();
            }
        };
    }
}
""")
    bound = {(e.line, e.dst.split(":")[1:3][1]) for e in edges if e.is_resolved}
    assert bound == {(8, "6-6")}
    assert _stub(edges, "a") == {"call_construct": "method"}
    assert _stub(edges, "boot") == {"call_construct": "method", "super_receiver": True}
    assert _stub(edges, "c") == {"call_construct": "method"}


# --- through the whole pipeline, with a namesake beside every target ---------------------------


def _map(root: Path, files: dict[str, str]) -> tuple[dict[str, dict], list[dict]]:
    for rel, text in files.items():
        path = root / "repo" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    out = root / "out.json"
    run_behavior_map(repo_root=root / "repo", out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    return {n["id"]: n for n in data["nodes"]}, data["edges"]


def _resolved(nodes: dict[str, dict], edges: list[dict], edge_type: str = "calls") -> set[tuple]:
    """(caller, line, target's qualified name, mark) of each resolved PHP edge."""
    return {
        (nodes[e["src"]]["qualified_name"], e["line"], nodes[e["dst"]]["qualified_name"],
         (e.get("meta") or {}).get("resolved_by"))
        for e in edges
        if e["type"] == edge_type and e.get("is_resolved") and e["src"].startswith("php:")
    }


_CONN = "<?php\nnamespace {ns};\nclass Conn {{\n    public function query() {{}}\n" \
        "    public static function open() {{}}\n}}\n"


def test_a_typed_receiver_reaches_the_imported_class_not_its_namesake(tmp_path: Path) -> None:
    nodes, edges = _map(tmp_path, {
        "Db/Conn.php": _CONN.format(ns="App\\Db"),
        "Legacy/Conn.php": _CONN.format(ns="Legacy"),
        "Http/Ctl.php": "<?php\nnamespace App\\Http;\nuse App\\Db\\Conn;\n"
                        "function run(Conn $c) {\n    $c->query();\n}\n"
                        "function fresh() {\n    $d = new Conn();\n    $d->query();\n}\n",
    })
    calls = _resolved(nodes, edges)
    assert calls == {
        ("App\\Http\\run", 5, "App\\Db\\Conn::query", "receiver_type"),
        ("App\\Http\\fresh", 9, "App\\Db\\Conn::query", "receiver_type"),
    }


def test_a_construction_of_an_imported_type_and_of_a_same_namespace_type(tmp_path: Path) -> None:
    nodes, edges = _map(tmp_path, {
        "Db/Conn.php": _CONN.format(ns="App\\Db"),
        "Legacy/Conn.php": _CONN.format(ns="Legacy"),
        "Http/Pool.php": "<?php\nnamespace App\\Http;\nclass Pool {}\n",
        "Legacy/Pool.php": "<?php\nnamespace Legacy;\nclass Pool {}\n",
        "Http/Ctl.php": "<?php\nnamespace App\\Http;\nuse App\\Db\\Conn;\n"
                        "function run() {\n    new Conn();\n    new Pool();\n    new \\Legacy\\Pool();\n"
                        "    new Missing();\n}\n",
    })
    assert _resolved(nodes, edges, "instantiates") == {
        ("App\\Http\\run", 5, "App\\Db\\Conn", "import"),
        ("App\\Http\\run", 6, "App\\Http\\Pool", "same_module"),
        ("App\\Http\\run", 7, "Legacy\\Pool", "qualified"),
    }


def test_a_static_call_reaches_the_class_its_owner_names(tmp_path: Path) -> None:
    nodes, edges = _map(tmp_path, {
        "Db/Conn.php": _CONN.format(ns="App\\Db"),
        "Legacy/Conn.php": _CONN.format(ns="Legacy"),
        "Http/Ctl.php": "<?php\nnamespace App\\Http;\nuse App\\Db\\Conn;\nuse Vendor\\Facade;\n"
                        "function run() {\n    Conn::open();\n    \\Legacy\\Conn::open();\n"
                        "    Facade::open();\n}\n",
    })
    assert _resolved(nodes, edges) == {
        ("App\\Http\\run", 6, "App\\Db\\Conn::open", "qualified"),
        ("App\\Http\\run", 7, "Legacy\\Conn::open", "qualified"),
    }


def test_parent_reaches_the_parent_and_this_reaches_an_inherited_method(tmp_path: Path) -> None:
    base = "<?php\nnamespace {ns};\nclass Base {{\n    public function save() {{}}\n" \
           "    public function load() {{}}\n}}\n"
    nodes, edges = _map(tmp_path, {
        "App/Base.php": base.format(ns="App"),
        "Legacy/Base.php": base.format(ns="Legacy"),
        "App/Child.php": "<?php\nnamespace App;\nclass Child extends Base {\n"
                         "    public function save() {\n        parent::save();\n"
                         "        $this->load();\n    }\n}\n",
    })
    assert _resolved(nodes, edges) == {
        ("App\\Child::save", 5, "App\\Base::save", "own_type"),
        ("App\\Child::save", 6, "App\\Base::load", "own_type"),
    }


def test_an_untyped_receiver_stays_unresolved(tmp_path: Path) -> None:
    nodes, edges = _map(tmp_path, {
        "Db/Conn.php": _CONN.format(ns="App\\Db"),
        "Legacy/Conn.php": _CONN.format(ns="Legacy"),
        "Http/Ctl.php": "<?php\nnamespace App\\Http;\nfunction run($c) {\n    $c->query();\n}\n",
    })
    assert _resolved(nodes, edges) == set()
    stub = next(e for e in edges if e["type"] == "calls" and e["line"] == 4)
    assert stub["is_resolved"] is False


def test_a_bare_call_means_the_namespaces_function_then_the_global_one(tmp_path: Path) -> None:
    """PHP looks an unqualified function up in the caller's namespace, then in the
    global one; a ``use function`` binds it; a library function (``count``) is
    not a repository function of another namespace that shares its name."""
    helper = "<?php\nnamespace {ns};\nfunction helper() {{}}\nfunction count($a) {{}}\n"
    nodes, edges = _map(tmp_path, {
        "Util/helpers.php": helper.format(ns="App\\Util"),
        "Other/helpers.php": helper.format(ns="Other"),
        "global.php": "<?php\nfunction create_user() {}\n",
        "Http/Ctl.php": "<?php\nnamespace App\\Http;\nuse function Other\\helper;\n"
                        "function run() {\n    helper();\n    create_user();\n    count([]);\n"
                        "    \\App\\Util\\helper();\n}\n",
    })
    assert _resolved(nodes, edges) == {
        ("App\\Http\\run", 5, "Other\\helper", "import"),
        ("App\\Http\\run", 6, "create_user", "same_module"),
        ("App\\Http\\run", 8, "App\\Util\\helper", "qualified"),
    }


def test_a_leading_backslash_names_from_the_global_namespace(tmp_path: Path) -> None:
    r"""``\Util\helper()`` and ``new \Lib\Conn`` are full names: the caller's
    namespace, which declares namesakes under itself, does not reinterpret
    them; the same names without the backslash are relative to it."""
    nodes, edges = _map(tmp_path, {
        "Util/helpers.php": "<?php\nnamespace Util;\nfunction helper() {}\n",
        "App/Util/helpers.php": "<?php\nnamespace App\\Util;\nfunction helper() {}\n",
        "Lib/Conn.php": _CONN.format(ns="Lib"),
        "App/Lib/Conn.php": _CONN.format(ns="App\\Lib"),
        "App/main.php": "<?php\nnamespace App;\nfunction run() {\n    \\Util\\helper();\n"
                        "    Util\\helper();\n    \\Lib\\Conn::open();\n    Lib\\Conn::open();\n}\n",
    })
    assert _resolved(nodes, edges) == {
        ("App\\run", 4, "Util\\helper", "qualified"),
        ("App\\run", 5, "App\\Util\\helper", "qualified"),
        ("App\\run", 6, "Lib\\Conn::open", "qualified"),
        ("App\\run", 7, "App\\Lib\\Conn::open", "qualified"),
    }


def test_a_script_calls_its_own_function_not_another_scripts_namesake(tmp_path: Path) -> None:
    """Two scripts each define ``check`` and are never loaded together: each
    one's call is to its own."""
    script = "<?php\nfunction check($s) {}\nfunction main() {\n    check(1);\n}\n"
    nodes, edges = _map(tmp_path, {"interop/client.php": script, "qps/client.php": script})
    own = {
        (nodes[e["src"]]["path"], nodes[e["dst"]]["path"])
        for e in edges if e["type"] == "calls" and e.get("is_resolved")
    }
    assert {(Path(a).parent.name, Path(b).parent.name) for a, b in own} == {
        ("interop", "interop"), ("qps", "qps"),
    }
