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
