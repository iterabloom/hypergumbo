# SPDX-License-Identifier: AGPL-3.0-or-later
"""A C / C++ function named as a value reaches the map (META-mino).

Read through the full survey, because an edge whose src names no node is
dropped there and looks exactly like no edge.

C linked a function value everywhere but at FILE SCOPE: ``int (*G)(void) =
glob;`` and ``= &adr;`` emitted nothing, so a function reachable only through a
global pointer looked dead. C++ linked only ``&f`` and call arguments: a
designated field (``{ .run = fld }``), a pointer variable (``int (*f)(void) =
var;``), an assignment (``h = asg;``) and an array of function pointers had no
edge, and a static dispatch table's ``dispatches_to`` edges started at a table
symbol C++ never emitted (C's WI-dirij, unported), so they were dropped.
"""
from __future__ import annotations

import pytest

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map

_DECLS = (
    "struct cmd { const char *name; int (*run)(void); };\n"
    "int fld(void) { return 1; }\nint var(void) { return 2; }\n"
    "int glob(void) { return 3; }\nint arr(void) { return 4; }\n"
    "int tbl(void) { return 5; }\nint asg(void) { return 6; }\n"
    "int adr(void) { return 7; }\nint ret(void) { return 8; }\n"
)
_FILE_SCOPE = (
    "int (*G)(void) = glob;\n"
    "int (*GA)(void) = &adr;\n"
    "static struct cmd table[] = { { \"t\", tbl } };\n"
)
_BODY = (
    "int (*pick(void))(void) { return ret; }\n"
    "int main(void) {\n"
    "    struct cmd c = { .name = \"x\", .run = fld };\n"
    "    int (*f)(void) = var;\n"
    "    int (*t[])(void) = { arr };\n"
    "    int (*h)(void);\n"
    "    h = asg;\n"
    "    return c.run() + f() + t[0]() + h() + table[0].run() + pick()();\n"
    "}\n"
)
_TARGETS = ("fld", "var", "glob", "arr", "tbl", "asg", "adr", "ret")


def _incoming(tmp_path: Path, name: str, text: str) -> dict[str, list[str]]:
    """Each target -> sorted ``edge_type`` of its non-contains incoming edges."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / name).write_text(text)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    names = {n["id"]: n["name"] for n in data["nodes"]}
    got: dict[str, list[str]] = {t: [] for t in _TARGETS}
    for e in data["edges"]:
        if e["type"] != "contains" and names.get(e["dst"]) in got:
            assert e["src"] in names, f"dangling src {e['src']}"
            got[names[e["dst"]]].append(e["type"])
    return {k: sorted(v) for k, v in got.items()}


def test_c_every_function_value_has_an_incoming_edge(tmp_path: Path) -> None:
    got = _incoming(tmp_path, "m.c", _DECLS + _FILE_SCOPE + _BODY)
    assert all(got[t] for t in _TARGETS), got
    # The two file-scope initialisers are references from the file.
    assert got["glob"] == ["references"]
    assert got["adr"] == ["references"]


def test_cpp_every_function_value_has_an_incoming_edge(tmp_path: Path) -> None:
    got = _incoming(tmp_path, "m.cpp", _DECLS + _FILE_SCOPE + _BODY)
    assert all(got[t] for t in _TARGETS), got
    assert got["tbl"] == ["dispatches_to"]
    assert got["arr"] == ["dispatches_to"]
    for t in ("fld", "var", "glob", "asg", "adr", "ret"):
        assert got[t] == ["references"], (t, got[t])


def test_cpp_values_that_are_not_functions_name_nothing(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "m.cpp").write_text(
        "int limit = 3;\nint f(void) { return 1; }\n"
        "int main(void) { int n = limit; int xs[] = { limit, n }; n = limit; return f() + xs[0]; }\n"
    )
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    refs = [e for e in data["edges"] if e["type"] == "references"]
    assert refs == [], refs


def test_cpp_a_local_shadowing_a_function_is_the_local(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "m.cpp").write_text(
        "int var(void) { return 1; }\n"
        "int main(int var) { int (*f)(void) = 0; int x = var; return x; }\n"
    )
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    assert [e for e in data["edges"] if e["type"] == "references"] == []


def test_c_a_value_in_an_unnamed_definition_stands_in(tmp_path: Path) -> None:
    """In a macro-named definition (``TEST_BEGIN(t) {..}``) the value
    reference is drawn from the file and says it stands in, as a call is."""
    from hypergumbo_core.analyze.edge_source import SRC_STANDS_IN_FOR
    from hypergumbo_lang_mainstream.c import analyze_c

    (tmp_path / "t.c").write_text(
        "int helper(void) { return 1; }\n"
        "TEST_BEGIN(test_x) {\n  int (*f)(void) = helper;\n  return f();\n}\n"
    )
    result = analyze_c(tmp_path)
    refs = [
        e for e in result.edges
        if e.evidence_type == "function_reference" and e.dst.split(":")[-2] == "helper"
    ]
    assert len(refs) == 1 and (refs[0].meta or {}).get(SRC_STANDS_IN_FOR), refs


def test_cpp_an_aggregate_assigned_after_declaration_is_a_value(tmp_path: Path) -> None:
    """``c = { "x", f };`` is no array table: the element is a value."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "m.cpp").write_text(
        "struct cmd { const char *n; int (*run)(void); };\n"
        "int f(void) { return 1; }\n"
        "int main(void) { struct cmd c; c = { \"x\", f }; return c.run(); }\n"
    )
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    names = {n["id"]: n["name"] for n in data["nodes"]}
    refs = [
        (names.get(e["src"]), names.get(e["dst"])) for e in data["edges"]
        if e["type"] == "references"
    ]
    assert refs == [("main", "f")], refs


def test_cpp_a_function_in_another_file_resolves(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.cpp").write_text("int handler(void) { return 1; }\n")
    (root / "b.cpp").write_text(
        "int handler(void);\nint (*wire(void))(void) { return handler; }\n"
    )
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    paths = {n["id"]: n.get("path", "") for n in data["nodes"]}
    refs = [
        paths.get(e["dst"]) for e in data["edges"]
        if e["type"] == "references" and e.get("evidence_type") != "x"
    ]
    assert refs and all(p and p.endswith("a.cpp") for p in refs), (refs, paths)


def test_cpp_a_bare_name_is_not_a_member_and_a_template_param_is_local(
    tmp_path: Path,
) -> None:
    """x265: ``dst += size`` (template parameter ``size``) had bound
    ``PicList::size``."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "m.cpp").write_text(
        "struct PicList { int size() { return 0; } };\n"
        "template<int size> void copy(short *dst) { short *p = dst; p += size; int n = size; dst[0] = n; }\n"
        "int use(void) { int (*f)(void) = 0; return f == 0; }\n"
    )
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    assert [e for e in data["edges"] if e["type"] == "references"] == []


def test_cpp_a_bare_value_is_never_a_member_even_one_registered_bare(
    tmp_path: Path,
) -> None:
    """apt: members of ``class pkgCache::RlsFileIterator { ... }`` are
    registered bare as functions (INV-tohuj); ``return Origin;`` in another
    class reads a data member, not that inline ``Origin()``."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.h").write_text(
        "class pkgCache { public: class RlsFileIterator; };\n"
        "class pkgCache::RlsFileIterator {\n"
        "  public:\n  inline const char *Origin() const { return 0; }\n};\n"
    )
    (root / "b.cpp").write_text(
        "#include \"a.h\"\n"
        "#include <string>\n"
        "class metaIndex { std::string Origin;\n"
        "  public: std::string GetOrigin() const { return Origin; } };\n"
    )
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    assert [e for e in data["edges"] if e["type"] == "references"] == []


def _refs_in(tmp_path: Path, files: dict[str, str]) -> list[tuple[str, str]]:
    root = tmp_path / "repo"
    root.mkdir()
    for rel, text in files.items():
        (root / rel).write_text(text)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    names = {n["id"]: n["name"] for n in data["nodes"]}
    return sorted(
        (names.get(e["src"], "?"), names.get(e["dst"], "?"))
        for e in data["edges"] if e["type"] == "references"
    )


def test_cpp_in_a_member_function_only_this_file_s_free_function_is_taken(
    tmp_path: Path,
) -> None:
    """apt: ``metaIndex::GetOrigin() { return Origin; }`` reads a data member;
    a same-named function another file declares (there: an inline accessor of
    a class tree-sitter could not parse, registered as a free function) is not
    it. x265: ``CUData::initialize`` storing its own file's ``bcast64`` is
    kept."""
    assert _refs_in(tmp_path, {
        "other.cpp": "const char *Origin(void) { return 0; }\n",
        "meta.cpp": (
            "static void bcast64(void) {}\n"
            "struct metaIndex { const char *Origin; void (*set)(void);\n"
            "  const char *GetOrigin() const; void init(); };\n"
            "const char *metaIndex::GetOrigin() const { return Origin; }\n"
            "void metaIndex::init() { set = bcast64; }\n"
        ),
    }) == [("metaIndex::init", "bcast64")]


def test_cpp_a_class_misread_as_a_function_is_member_context() -> None:
    """apt: an export macro before a class (``class APT_PUBLIC pkgCacheFile``
    after a forward declaration) parses the class as a FUNCTION whose inline
    members are nested definitions -- the chain apt's cachefile.h:75 has. A
    bare ``return Cache;`` there is member context, so another file's
    ``Cache()`` is not taken."""
    import tree_sitter_language_pack as tlp

    from hypergumbo_core.ir import Span, Symbol
    from hypergumbo_lang_mainstream.cpp import _cpp_bare_value_is_a_member

    code = (
        b"#ifndef X\n#define X\nclass pkgPolicy;\nclass APT_PUBLIC pkgCacheFile\n{\n"
        b"  struct Private;\n  pkgCache *Cache;\n public:\n"
        b"  inline operator pkgCache *() const {return Cache;};\n"
        b"  inline pkgCache *Get() { return Cache; };\n};\n#endif\n"
    )
    tree = tlp.get_parser("cpp").parse(code)
    stack, node = [tree.root_node], None
    while stack:
        cur = stack.pop()
        if (
            cur.type == "identifier" and cur.text == b"Cache"
            and cur.parent is not None and cur.parent.type == "return_statement"
        ):
            node = cur
        stack.extend(cur.children)
    assert node is not None
    chain, cur = [], node.parent
    while cur is not None:
        chain.append(cur.type)
        cur = cur.parent
    assert chain.count("function_definition") == 2 and "ERROR" not in chain, chain

    def sym(name: str, path: str) -> Symbol:
        return Symbol(
            id=f"cpp:{path}:1-1:{name}:function", name=name, kind="function",
            language="cpp", path=path, span=Span(1, 1, 0, 0),
            origin="t", origin_run_id="t",
        )

    assert _cpp_bare_value_is_a_member(
        node, sym("Cache", "other.cpp"), sym("Get", "file.h"), "file.h",
    ) is True



@pytest.mark.parametrize(
    ("code", "ident"),
    [
        # an access label read as a statement label in a "function"
        (b"class APT_PUBLIC D\n{\n public:\n  struct Fn { int (*f)(void); } x = { .f = InRootSet };\n};\n", b"InRootSet"),
        # a class body read as a file-scope block
        (b"#ifndef X\n#define X\n{\n  for (int i = 0; i < 2; i++) { if (i) return value; }\n}\n#endif\n", b"value"),
        # an ERROR region
        # an ERROR with no well-formed definition between it and the use
        (b"x = ) { for (iter v = begin; v; ) {} }\n", b"begin"),
    ],
)
def test_cpp_a_misread_region_takes_no_bare_value(code: bytes, ident: bytes) -> None:
    import tree_sitter_language_pack as tlp

    from hypergumbo_lang_mainstream.cpp import _cpp_in_a_misread_region

    tree = tlp.get_parser("cpp").parse(code)
    stack, hits = [tree.root_node], []
    while stack:
        cur = stack.pop()
        if cur.type == "identifier" and cur.text == ident:
            hits.append(cur)
        stack.extend(cur.children)
    assert hits and all(_cpp_in_a_misread_region(n) for n in hits), [
        n.parent.type for n in hits
    ]


def test_cpp_an_error_beyond_a_clean_definition_is_no_misread_region() -> None:
    """x265: a file-level ERROR (macros around a namespace) above functions
    that parsed cleanly; their ``p.idct = idct8;`` is a value."""
    import tree_sitter_language_pack as tlp

    from hypergumbo_lang_mainstream.cpp import _cpp_in_a_misread_region

    code = b"struct S { int a; ) void setup(P &p) { p.idct = idct8; }\n"
    tree = tlp.get_parser("cpp").parse(code)
    stack, hits = [tree.root_node], []
    while stack:
        cur = stack.pop()
        if cur.type == "identifier" and cur.text == b"idct8":
            hits.append(cur)
        stack.extend(cur.children)
    chain, cur = [], hits[0].parent
    while cur is not None:
        chain.append(cur.type)
        cur = cur.parent
    assert "ERROR" in chain and "function_definition" in chain, chain
    assert not _cpp_in_a_misread_region(hits[0])


def test_cpp_well_formed_code_is_no_misread_region() -> None:
    import tree_sitter_language_pack as tlp

    from hypergumbo_lang_mainstream.cpp import _cpp_in_a_misread_region

    code = b"int f(void);\nint g(void) { int (*h)(void) = f; return h(); }\n"
    tree = tlp.get_parser("cpp").parse(code)
    stack, hits = [tree.root_node], []
    while stack:
        cur = stack.pop()
        if cur.type == "identifier" and cur.text == b"f" and cur.parent.type == "init_declarator":
            hits.append(cur)
        stack.extend(cur.children)
    assert hits and not any(_cpp_in_a_misread_region(n) for n in hits)
