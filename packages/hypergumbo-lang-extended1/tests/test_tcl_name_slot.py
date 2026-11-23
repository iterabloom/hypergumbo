# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/tcl.py
"""A Tcl symbol's id name slot is its sanitized name (ADR-0036 Ruling 1).

The id's slot carried every enclosing ``namespace eval`` (``util..helper``)
while ``Symbol.name`` held the proc's word (``helper``) and
``qualified_name`` was empty; a proc declared ``proc ::top`` or under
``namespace eval ::glob`` kept the ``::`` (``..top``, ``..glob..g``). Now
``name`` is the proc's or namespace's own last word, ``qualified_name`` its
absolute name without the leading ``::`` (ADR-0032; Tcl's separator ``::``),
and the slot is ``sanitize_id_name_segment(name)``. The stable id hashes
what it hashed before.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import sanitize_id_name_segment
from hypergumbo_lang_extended1.tcl import analyze_tcl

_SOURCE = """\
namespace eval util {
    proc helper {x} { return $x }
    namespace eval inner {
        proc deep {} {}
    }
}
proc util::other {y} { return $y }
proc ::top {} {}
proc plain {} {}
namespace eval ::glob {
    proc g {} {}
}
namespace eval a::b {
    proc c {} {}
}
"""


def _symbols(tmp_path: Path) -> dict[str, object]:
    (tmp_path / "main.tcl").write_text(_SOURCE)
    result = analyze_tcl(tmp_path)
    return {s.qualified_name: s for s in result.symbols if s.kind in ("function", "namespace")}


def test_name_slot_is_the_sanitized_name_for_every_tcl_symbol(tmp_path: Path) -> None:
    syms = _symbols(tmp_path)
    assert len(syms) == 11  # reach: every proc and namespace, keyed by qualified name
    for qn, s in syms.items():
        slot = s.id.rsplit(":", 3)[-2]
        assert slot == sanitize_id_name_segment(s.name), (qn, s.id, s.name)
        assert ":" not in s.name and not qn.startswith("::")


def test_name_is_the_last_word_and_qualified_name_the_absolute_name(tmp_path: Path) -> None:
    syms = _symbols(tmp_path)
    expected = {
        "util": ("util", "namespace"), "util::helper": ("helper", "function"),
        "util::inner": ("inner", "namespace"), "util::inner::deep": ("deep", "function"),
        "util::other": ("other", "function"), "top": ("top", "function"),
        "plain": ("plain", "function"), "glob": ("glob", "namespace"),
        "glob::g": ("g", "function"), "a::b": ("b", "namespace"),
        "a::b::c": ("c", "function"),
    }
    assert {qn: (s.name, s.kind) for qn, s in syms.items() if qn in expected} == expected


def test_a_call_starts_at_its_procs_own_id(tmp_path: Path) -> None:
    """The caller's id is built by the same rule as the proc's, so a call in
    a namespaced proc starts at an emitted symbol."""
    from hypergumbo_core.analyze.edge_target import non_id_edge_endpoints

    (tmp_path / "main.tcl").write_text(
        "namespace eval util {\n    proc helper {} {}\n    proc run {} { helper }\n}\n"
        "proc ::top {} { util::helper }\n"
    )
    result = analyze_tcl(tmp_path)
    calls = [e for e in result.edges if e.edge_type == "calls"]
    assert len(calls) == 2  # reach
    assert non_id_edge_endpoints(result.symbols, result.edges) == []
