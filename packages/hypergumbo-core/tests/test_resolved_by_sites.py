# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the static check of the resolution mark (audit 0030, WI-hirol)."""

from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.resolved_by_sites import (
    BASELINE_PATH,
    UnmarkedSite,
    baseline_document,
    compare_to_baseline,
    load_baseline,
    old_spelling_counts,
    scan_tree,
    unmarked_sites,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HEAD = "from hypergumbo_core.ir import Edge\n"


def _unmarked(body: str) -> list[UnmarkedSite]:
    return unmarked_sites(_HEAD + body, "pkg/src/a.py")


# --- every call-family creation site passes the mark ---------------------------


def test_a_calls_site_without_the_mark_is_listed() -> None:
    (site,) = _unmarked('Edge.create(src="a", dst="b", edge_type="calls", line=1)\n')
    assert site == UnmarkedSite("pkg/src/a.py", 2, "calls")
    assert site.render() == "pkg/src/a.py:2: calls edge created without resolved_by="


def test_a_constructor_site_and_a_positional_edge_type_are_judged() -> None:
    assert [s.edge_type for s in _unmarked(
        'Edge.create(src="a", dst="b", edge_type="instantiates", line=1)\n'
        'Edge.create("a", "b", "calls", 1)\n'
        'Edge(id="i", src="a", dst="b", edge_type="calls", line=1)\n'
    )] == ["instantiates", "calls", "calls"]


def test_a_conditional_between_literals_is_judged_by_its_call_family_branch() -> None:
    (site,) = _unmarked(
        'Edge.create(src="a", dst="b", line=1,\n'
        '            edge_type="instantiates" if ctor else "references")\n'
    )
    assert site.edge_type == "instantiates"


def test_what_is_not_judged() -> None:
    assert _unmarked(
        # the mark is passed (its value is Edge.create's business)
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, resolved_by=by)\n'
        # the edge leaves the repository by construction
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, is_resolved=False)\n'
        # another edge type
        'Edge.create(src="a", dst="b", edge_type="imports", line=1)\n'
        # an edge type that is not a literal: the census judges those at run time
        'Edge.create(src="a", dst="b", edge_type=kind, line=1)\n'
        'Edge.create(src="a", dst="b", edge_type="calls" if x else kind, line=1)\n'
        # not an Edge
        'Symbol.create(src="a", dst="b", edge_type="calls", line=1)\n'
        'run.create(edge_type="calls")\n'
    ) == []


def test_a_computed_is_resolved_does_not_exempt_a_site() -> None:
    assert len(_unmarked(
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, is_resolved=found)\n'
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, is_resolved=True)\n'
    )) == 2


# --- the older spellings are counted where they are written --------------------


def test_the_three_written_forms_are_counted() -> None:
    assert old_spelling_counts(
        'Edge.create(evidence_type="ast_call_type_inferred")\n'
        'meta = {"call_construct": "method", "resolution_quality": "typed_receiver"}\n'
        'meta["receiver"] = "typed_var"\n'
        'meta["resolution_quality"] = "ambiguous" if tie else "typed"\n'
    ) == {
        "evidence_type=ast_call_type_inferred": 1,
        "resolution_quality=typed_receiver": 1,
        "receiver=typed_var": 1,
        "resolution_quality=ambiguous": 1,
        "resolution_quality=typed": 1,
    }


def test_prose_and_values_that_do_not_fold_are_not_counted() -> None:
    assert old_spelling_counts(
        '"""A docstring that says resolution_quality="typed" and "receiver": "self"."""\n'
        '# evidence_type="ast_method_inferred" in a comment\n'
        'Edge.create(evidence_type="ast_call")\n'           # the apex stays
        'meta = {"receiver": "field_chain"}\n'              # expression shape stays
        'meta["resolution_quality"] = "accessor_name"\n'    # ADR-0053 stays
        'meta["resolution_quality"] = computed\n'           # not a literal
        'other[index] = "typed"\n'                          # not the key
        'f(**{1: "typed"})\n'                               # not a string key
    ) == {}


# --- the pins ------------------------------------------------------------------


def test_a_pin_may_only_be_met() -> None:
    pinned = {"a.py": {"receiver=self": 2}, "gone.py": {"receiver=self": 1}}
    current = {"a.py": {"receiver=self": 3}, "new.py": {"resolution_quality=typed": 1}}
    grown, shrunk = compare_to_baseline(current, pinned)
    assert grown == [
        "a.py: receiver=self is written 3 time(s), pinned at 2",
        "new.py: resolution_quality=typed is written 1 time(s), pinned at 0",
    ]
    assert shrunk == ["gone.py: receiver=self is written 0 time(s), pinned at 1"]
    assert compare_to_baseline(pinned, pinned) == ([], [])


def test_baseline_round_trip(tmp_path: Path) -> None:
    assert load_baseline(tmp_path) == {}
    (tmp_path / ".ci").mkdir()
    current = {"a.py": {"receiver=self": 2}}
    (tmp_path / BASELINE_PATH).write_text(baseline_document(current))
    assert load_baseline(tmp_path) == current
    assert "may only go down" in json.loads((tmp_path / BASELINE_PATH).read_text())["description"]


def test_scan_tree_reads_package_sources_only(tmp_path: Path) -> None:
    src = tmp_path / "packages" / "p" / "src" / "p"
    src.mkdir(parents=True)
    (src / "a.py").write_text(
        _HEAD + 'Edge.create(src="a", dst="b", edge_type="calls", line=1,\n'
                '            evidence_type="ast_call_static")\n'
    )
    (src / "clean.py").write_text("x = 1\n")
    tests = tmp_path / "packages" / "p" / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text(_HEAD + 'Edge.create("a", "b", "calls", 1)\n')
    unmarked, old = scan_tree(tmp_path)
    assert [s.render() for s in unmarked] == [
        "packages/p/src/p/a.py:2: calls edge created without resolved_by=",
    ]
    assert old == {"packages/p/src/p/a.py": {"evidence_type=ast_call_static": 1}}


def test_live_tree_is_clean() -> None:
    """The gate: every call-family creation site is marked, and no old spelling moved off its pin."""
    unmarked, current = scan_tree(_REPO_ROOT)
    assert not unmarked, (
        "call-family edges created without resolved_by= (hypergumbo_core.resolved_by "
        "says which value to pass):\n" + "\n".join(s.render() for s in unmarked)
    )
    grown, shrunk = compare_to_baseline(current, load_baseline(_REPO_ROOT))
    assert not grown, (
        "an old spelling of 'how a call target was chosen' gained a producer; pass "
        "resolved_by instead (docs/audits/0030-call-target-resolution.md):\n" + "\n".join(grown)
    )
    assert not shrunk, (
        "an old spelling lost a producer; lower its pin with "
        "scripts/check-resolved-by-sites --update-baseline:\n" + "\n".join(shrunk)
    )
