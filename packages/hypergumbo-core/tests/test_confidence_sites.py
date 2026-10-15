# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the static confidence-site audit (WI-vosif, ADR-0039 rulings 6-7)."""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.confidence_sites import (
    CALLER_VALUED_MARKER,
    ConfidenceSiteFinding,
    baseline_document,
    compare_to_baseline,
    load_baseline,
    scan_file,
    scan_tree,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _scan(tmp_path: Path, body: str, rel: str = "pkg/src/analyzer.py") -> list:
    path = tmp_path / "f.py"
    path.write_text("from hypergumbo_core.ir import Edge\n" + body)
    return scan_file(path, rel)


def test_literal_in_band_is_clean(tmp_path: Path) -> None:
    assert _scan(tmp_path, 'Edge.create(src="a", dst="b", edge_type="calls", line=1,\n'
                           '            evidence_type="ast_call", confidence=0.6)\n') == []


def test_literal_above_analyzer_ceiling_is_a_finding(tmp_path: Path) -> None:
    findings = _scan(tmp_path, 'Edge.create(src="a", dst="b", edge_type="calls", line=1,\n'
                               '            evidence_type="ast_call", confidence=0.9)\n')
    assert [(f.kind, f.evidence_types, f.value, f.line) for f in findings] == [
        ("literal_out_of_band", ("ast_call",), "0.9", 2),
    ]


def test_omitted_evidence_type_is_the_create_default(tmp_path: Path) -> None:
    findings = _scan(tmp_path, 'Edge.create(src="a", dst="b", edge_type="calls", line=1,\n'
                               '            confidence=1.0)\n')
    assert findings[0].evidence_types == ("ast_call_direct",)


def test_conditional_literal_branch_out_of_band(tmp_path: Path) -> None:
    findings = _scan(tmp_path, 'x = True\nEdge.create(src="a", dst="b", edge_type="calls", line=1,\n'
                               '    evidence_type="ast_import", confidence=0.5 if x else 1.0)\n')
    assert [f.kind for f in findings] == ["literal_out_of_band"]


def test_conditional_evidence_type_checks_every_branch(tmp_path: Path) -> None:
    # 0.9 is inside ast_new (ceiling 0.95) but over ast_call_direct (0.85).
    findings = _scan(tmp_path, 'k = True\nEdge.create(src="a", dst="b", edge_type="calls", line=1,\n'
                               '    evidence_type="ast_new" if k else "ast_call_direct", confidence=0.9)\n')
    assert findings[0].evidence_types == ("ast_call_direct", "ast_new")


def test_linker_literal_is_judged_by_linker_range(tmp_path: Path) -> None:
    body_ok = ('Edge.create(src="a", dst="b", edge_type="calls", line=1,\n'
               '            evidence_type="ast_call_direct", confidence=0.9)\n')
    assert _scan(tmp_path, body_ok, rel="hypergumbo_core/linkers/x.py") == []
    body_bad = body_ok.replace("0.9", "0.97")
    findings = _scan(tmp_path, body_bad, rel="hypergumbo_core/linkers/x.py")
    assert [f.kind for f in findings] == ["literal_out_of_band"]


def test_computed_analyzer_value_needs_the_marker(tmp_path: Path) -> None:
    body = ('c = 0.5\nEdge.create(src="a", dst="b", edge_type="calls", line=1,\n'
            '            evidence_type="ast_call", confidence=c)\n')
    assert [f.kind for f in _scan(tmp_path, body)] == ["computed_uncommented"]
    marked = body.replace("c = 0.5\n", f"c = 0.5\n{CALLER_VALUED_MARKER} test reason\n")
    assert _scan(tmp_path, marked) == []
    inline = body.replace("confidence=c)", f"confidence=c,  {CALLER_VALUED_MARKER} why\n)")
    assert _scan(tmp_path, inline) == []
    # The marker may open a longer comment block above the call...
    block = body.replace("c = 0.5\n", f"c = 0.5\n{CALLER_VALUED_MARKER} a reason\n# that runs on\n")
    assert _scan(tmp_path, block) == []
    # ...but a marker separated from the call by code does not count.
    detached = body.replace("c = 0.5\n", f"{CALLER_VALUED_MARKER} stale\nc = 0.5\n")
    assert [f.kind for f in _scan(tmp_path, detached)] == ["computed_uncommented"]


def test_conditional_between_literals_is_branch_tier(tmp_path: Path) -> None:
    body = ('k = 1\nEdge.create(src="a", dst="b", edge_type="calls", line=1,\n'
            '    evidence_type="ast_call", confidence=0.8 if k else 0.5)\n')
    assert [f.kind for f in _scan(tmp_path, body)] == ["computed_uncommented"]


def test_computed_values_that_are_not_findings(tmp_path: Path) -> None:
    # Linker computed value: no static band. Unseeded / unknown pathway: no band.
    linker = ('c = 0.5\nEdge.create(src="a", dst="b", edge_type="calls", line=1,\n'
              '            evidence_type="ast_call", confidence=c)\n')
    assert _scan(tmp_path, linker, rel="hypergumbo_core/linkers/x.py") == []
    unseeded = linker.replace('"ast_call"', '"ast_call_namespace"')
    assert _scan(tmp_path, unseeded) == []
    unknown = linker.replace('evidence_type="ast_call"', "evidence_type=c")
    assert _scan(tmp_path, unknown) == []


def test_sites_without_explicit_confidence_are_ignored(tmp_path: Path) -> None:
    body = ('Edge.create(src="a", dst="b", edge_type="calls", line=1, confidence=None)\n'
            'Edge.create(src="a", dst="b", edge_type="calls", line=1, resolution_score=0.9)\n'
            'Other.create(confidence=2.0)\nmake(confidence=2.0)\n')
    assert _scan(tmp_path, body) == []


def test_baseline_round_trip_and_comparison(tmp_path: Path) -> None:
    f1 = ConfidenceSiteFinding("p.py", 3, "literal_out_of_band", ("ast_call",), "0.9")
    f2 = ConfidenceSiteFinding("q.py", 7, "computed_uncommented", ("ast_call",), "c")
    (tmp_path / ".ci").mkdir()
    (tmp_path / ".ci" / "confidence-sites-baseline.json").write_text(baseline_document([f1]))
    baseline = load_baseline(tmp_path)
    assert baseline == [f1.key]
    # A moved line keeps its key.
    moved = ConfidenceSiteFinding("p.py", 30, "literal_out_of_band", ("ast_call",), "0.9")
    assert compare_to_baseline([moved], baseline) == ([], [])
    assert compare_to_baseline([moved, f2], baseline) == ([f2], [])
    assert compare_to_baseline([], baseline) == ([], [f1.key])
    assert "q.py:7: computed_uncommented" in f2.render()


def test_missing_baseline_is_empty(tmp_path: Path) -> None:
    assert load_baseline(tmp_path) == []


def test_live_tree_matches_baseline() -> None:
    """The CI gate: no new out-of-contract site, and no stale baseline entry."""
    findings = scan_tree(_REPO_ROOT)
    new, stale = compare_to_baseline(findings, load_baseline(_REPO_ROOT))
    assert not new, "new out-of-contract confidence sites (ADR-0039 rulings 6-7):\n" + \
        "\n".join(f.render() for f in new)
    assert not stale, (
        "baseline entries no longer occur; remove them from "
        ".ci/confidence-sites-baseline.json (shrink-only):\n" + "\n".join(stale)
    )
