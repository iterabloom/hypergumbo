# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag, c: a call edge's src is the function whose span contains it.

An ``#ifdef``/``#else`` pair defines one function name twice in one file
(tmux's compat/closefrom.c). The name-keyed lookup kept one of them, so calls in
the other were anchored to it: 35 of 11,011 c call edges on a 26-repo run.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.edge_source import (
    SRC_STANDS_IN_FOR,
    UNNAMED_DEFINITION,
    unemitted_edge_sources,
)
from hypergumbo_lang_mainstream.c import analyze_c


def test_ifdef_alternatives_are_told_apart(tmp_path: Path) -> None:
    (tmp_path / "closefrom.c").write_text("""\
void one(void);
void two(void);

#ifdef HAVE_PROC
void closefrom(int lowfd)
{
    one();
}
#else
void closefrom(int lowfd)
{
    two();
}
#endif
""")
    result = analyze_c(tmp_path)
    assert unemitted_edge_sources(result.symbols, result.edges) == []
    by_id = {s.id: s for s in result.symbols}
    anchors = [
        (by_id[e.src].name, by_id[e.src].span.start_line, by_id[e.src].span.end_line, e.line)
        for e in result.edges if e.edge_type == "calls" and e.line and e.src in by_id
    ]
    assert {a[3] for a in anchors} >= {7, 12}, anchors  # reach: both calls
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)


def _anchors(result) -> list[tuple[str, int, int, int]]:
    by_id = {s.id: s for s in result.symbols}
    return [
        (by_id[e.src].name, by_id[e.src].span.start_line, by_id[e.src].span.end_line, e.line)
        for e in result.edges if e.edge_type == "calls" and e.line and e.src in by_id
    ]


def test_every_declarator_shape_names_its_function(tmp_path: Path) -> None:
    """WI-saduj. A definition whose name sits deeper than ONE ``pointer_declarator``
    got no symbol, so every call in it was left unemitted: ``char **f()`` (crun's
    ``read_dir_entries`` and ``dup_array``), ``T ***f()``, a const-qualified pointer
    level, a parenthesised name, and a function returning a function pointer, whose
    own name sits under a ``parenthesized_declarator`` inside the declarator of the
    pointer it returns."""
    (tmp_path / "m.c").write_text("""\
#include <stdlib.h>
char *one_ptr(int n) { return malloc(n); }
char **two_ptr(int n) { return malloc(n); }
static char ***three_ptr(int n) { return malloc(n); }
int plain(int n) { malloc(n); return 0; }
int (*getfn(void))(int) { malloc(1); return 0; }
char * const *cptr(int n) { return malloc(n); }
int (paren)(int n) { malloc(n); return 0; }
""")
    result = analyze_c(tmp_path)
    names = {s.name for s in result.symbols if s.kind == "function"}
    assert names >= {"one_ptr", "two_ptr", "three_ptr", "plain", "getfn", "cptr", "paren"}, names
    anchors = _anchors(result)
    assert {a[3] for a in anchors} >= set(range(2, 9)), anchors  # reach: every call
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)
    sigs = {s.name: s.signature for s in result.symbols if s.kind == "function"}
    assert sigs["two_ptr"] == "(int n) char**", sigs
    assert sigs["three_ptr"] == "(int n) static char***", sigs
    # A function returning a function pointer: its own parameters, and NO return
    # type rather than a wrong one (``int`` or ``int*`` would both be false).
    assert sigs["getfn"] == "(void)", sigs


def test_dispatch_table_reference_is_anchored_by_position(tmp_path: Path) -> None:
    """The dispatch-table body scan found the referring function by NAME, so of two
    ``#ifdef`` alternatives the reference in the first was attributed to the second."""
    (tmp_path / "d.c").write_text("""\
int a(int n) { return n; }
struct cmd { const char *name; int (*fn)(int); };
static struct cmd table[] = { { "a", a } };
#ifdef X
int lookup(int i)
{
    return table[i].fn(i);
}
#else
int lookup(int i)
{
    return table[0].fn(i);
}
#endif
""")
    result = analyze_c(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    refs = [
        (by_id[e.src].span.start_line, by_id[e.src].span.end_line, e.line)
        for e in result.edges
        if e.edge_type == "references" and e.src in by_id
        and (e.meta or {}).get("ref_construct") == "dispatch_table"
    ]
    assert {r[2] for r in refs} == {7, 12}, refs  # reach: both references
    for start, end, line in refs:
        assert start <= line <= end, (start, end, line)


def test_a_macro_named_definition_mints_no_symbol(tmp_path: Path) -> None:
    """A ``function_declarator`` directly inside another is a macro tree-sitter
    could not expand (x265's ``PFX(name)(args)``, lua's ``LUALIB_API int (f)
    (args)`` read as a function named ``int``). Naming the inner one would mint a
    symbol called ``PFX`` or ``int`` and anchor real calls on it; the definition
    stays unnamed, and its calls are drawn from the file, marked as standing in
    (WI-tikop; ``test_a_call_in_an_unnamed_definition_stands_in``)."""
    (tmp_path / "m.c").write_text("""\
void foo(void);
int PFX(cpu_test)(void) { foo(); return 0; }
LUALIB_API int (luaL_loadstring) (const char *s) { foo(); return 0; }
int ok(void) { foo(); return 0; }
""")
    result = analyze_c(tmp_path)
    names = {s.name for s in result.symbols if s.kind == "function"}
    assert "ok" in names, names  # reach: the plain definition is named
    assert not names & {"PFX", "int", "cpu_test", "luaL_loadstring"}, names
    assert 4 in {a[3] for a in _anchors(result)}, _anchors(result)
    standing = {
        e.line for e in result.edges
        if e.edge_type == "calls" and e.src.endswith(":1-1:file:file")
        and (e.meta or {}).get(SRC_STANDS_IN_FOR) == UNNAMED_DEFINITION
    }
    assert standing == {2, 3}, standing


def test_a_call_in_an_unnamed_definition_stands_in(tmp_path: Path) -> None:
    """WI-tikop. A definition whose name comes from a macro tree-sitter cannot
    expand -- jemalloc's ``TEST_BEGIN(test_x) {..}`` (2,598 redis call sites),
    ``PFX(f)(args)`` -- has no symbol and no honest name. Every edge drawn from
    inside it -- a call, a callback argument, ``&f``, a dispatch-table use --
    comes from the nearest enclosing record that has a symbol (an outer
    definition, else the file) and carries ``src_stands_in_for``. Before, all of
    them were left unemitted, except inside a named outer definition, where they
    were drawn from it with nothing to say so."""
    (tmp_path / "t.c").write_text("""\
#include <pthread.h>
int helper(int x) { return x; }
static int (*cmds[])(int) = { helper };
int lookup(int i);
TEST_BEGIN(test_x) {
  helper(1);
  int (*f)(int) = &helper;
  pthread_create(0, 0, helper, 0);
  return cmds[0](1);
}
TEST_END
int outer(void) {
  int MAC(inner)(void) { return helper(2); }
  return helper(3);
}
int plain(void) { return helper(4); }
static int tbl[] = { helper };
int MAC2(user)(int i) { return tbl[i]; }
""")
    result = analyze_c(tmp_path)
    # The one dangling src is the dispatch-table VARIABLE (``tbl``), which c.py
    # does not emit as a symbol: a residual of its own (see the report), pinned
    # by equality so the fix has to update this line.
    assert {
        (e.edge_type, e.line) for e in unemitted_edge_sources(result.symbols, result.edges)
    } == {("dispatches_to", 17)}
    names = {s.name for s in result.symbols if s.kind == "function"}
    assert not names & {"TEST_BEGIN", "test_x", "MAC", "inner", "MAC2", "user"}, names
    by_id = {s.id: s for s in result.symbols}

    def src(e) -> str:
        return by_id[e.src].name if e.src in by_id else "file"

    got = {
        (e.edge_type, e.line, src(e), (e.meta or {}).get(SRC_STANDS_IN_FOR))
        for e in result.edges
        if e.edge_type in ("calls", "references") and e.line
        and e.dst.split(":")[-2] in ("helper", "pthread_create", "tbl")
    }
    assert got >= {
        ("calls", 6, "file", UNNAMED_DEFINITION),
        ("references", 7, "file", UNNAMED_DEFINITION),
        ("calls", 8, "file", UNNAMED_DEFINITION),
        ("calls", 13, "outer", UNNAMED_DEFINITION),
        ("calls", 14, "outer", None),
        ("calls", 16, "plain", None),
    }, sorted(got, key=str)
    # The dispatch-table use inside a macro-named definition stands in too.
    table_refs = [
        (e.line, src(e), (e.meta or {}).get(SRC_STANDS_IN_FOR))
        for e in result.edges
        if e.edge_type == "references"
        and (e.meta or {}).get("ref_construct") == "dispatch_table"
    ]
    assert (18, "file", UNNAMED_DEFINITION) in table_refs, table_refs
