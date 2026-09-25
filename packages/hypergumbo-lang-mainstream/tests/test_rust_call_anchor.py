# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag, rust: a call edge's src is the function whose span contains it.

rust already looked the enclosing function up by span, but built that index from
``global_symbols``, keyed by QUALIFIED name. Two ``impl IntoIterator`` blocks for
``AttributeSet`` and ``&AttributeSet`` (just's attribute_set.rs) both define
``AttributeSet::into_iter``, so one vanished from the index and its calls fell
back to the name lookup: 5 of 8,842 rust call edges on a 26-repo run.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.rust import analyze_rust


def test_two_impls_of_one_method(tmp_path: Path) -> None:
    (tmp_path / "lib.rs").write_text("""\
pub struct AttributeSet { items: Vec<u32> }

fn one() -> u32 { 1 }
fn two() -> u32 { 2 }

impl IntoIterator for AttributeSet {
    type Item = u32;
    type IntoIter = std::vec::IntoIter<u32>;
    fn into_iter(self) -> Self::IntoIter {
        one();
        self.items.into_iter()
    }
}

impl<'a> IntoIterator for &'a AttributeSet {
    type Item = &'a u32;
    type IntoIter = std::slice::Iter<'a, u32>;
    fn into_iter(self) -> Self::IntoIter {
        two();
        self.items.iter()
    }
}
""")
    result = analyze_rust(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    anchors = [
        (by_id[e.src].name, by_id[e.src].span.start_line, by_id[e.src].span.end_line, e.line)
        for e in result.edges if e.edge_type == "calls" and e.line and e.src in by_id
    ]
    assert {a[3] for a in anchors} >= {10, 19}, anchors  # reach: both calls
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)
