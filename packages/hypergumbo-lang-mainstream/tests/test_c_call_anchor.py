# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag, c: a call edge's src is the function whose span contains it.

An ``#ifdef``/``#else`` pair defines one function name twice in one file
(tmux's compat/closefrom.c). The name-keyed lookup kept one of them, so calls in
the other were anchored to it: 35 of 11,011 c call edges on a 26-repo run.
"""
from __future__ import annotations

from pathlib import Path

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
