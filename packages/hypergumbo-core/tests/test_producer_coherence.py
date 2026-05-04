# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the L3 producer-side axis-coherence linter.

The L3 linter walks producer call sites (Edge.create / Edge() /
Symbol.create / Symbol()) and verifies that literal-string keyword
arguments to axis-bearing parameters are in the corresponding
canonical registry. F-string arguments are advisory (Phase-3 fold
candidates), not strict failures.

The live-tree invariant tests assert zero strict violations across
all three sibling axes — Edge.evidence_type, Symbol.kind,
Edge.edge_type — which is the structural guarantee Phase 1 of ADR-0028
§"Path B" delivers.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.producer_coherence import (
    find_edge_type_producer_violations,
    find_evidence_type_producer_violations,
    find_producer_coherence_violations,
    find_symbol_kind_producer_violations,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


# --- Live-tree invariants: zero strict violations across all axes ---

def test_no_strict_producer_violations_for_evidence_type():
    result = find_evidence_type_producer_violations(REPO_ROOT)
    assert not result.strict_violations, (
        "Edge.evidence_type producer literals not in canonical registry:\n"
        + "\n".join(result.strict_violations)
        + "\n\nAdd the value to "
        "packages/hypergumbo-core/src/hypergumbo_core/evidence_types.py "
        "with an axis classification per ADR-0028."
    )


def test_no_strict_producer_violations_for_symbol_kind():
    result = find_symbol_kind_producer_violations(REPO_ROOT)
    assert not result.strict_violations, (
        "Symbol.kind producer literals not in canonical registry:\n"
        + "\n".join(result.strict_violations)
    )


def test_no_strict_producer_violations_for_edge_type():
    result = find_edge_type_producer_violations(REPO_ROOT)
    assert not result.strict_violations, (
        "Edge.edge_type producer literals not in canonical registry:\n"
        + "\n".join(result.strict_violations)
    )


# --- Synthetic-fixture tests for the field-agnostic linter ---

def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_returns_empty_when_no_search_root_exists(tmp_path: Path):
    """Repo with no packages/scripts dirs returns no violations."""
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()
    assert result.advisory_dynamic_emits == ()


def test_literal_in_registry_is_clean(tmp_path: Path):
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'evidence_type="ast_call_direct")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()
    assert result.advisory_dynamic_emits == ()


def test_literal_not_in_registry_is_strict_violation(tmp_path: Path):
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'evidence_type="brand_new_label")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert len(result.strict_violations) == 1
    assert "brand_new_label" in result.strict_violations[0]
    assert result.advisory_dynamic_emits == ()


def test_fstring_emit_is_advisory_not_strict(tmp_path: Path):
    """F-strings are surfaced as Phase-3 fold candidates, not failures."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'name = "django"\n'
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'evidence_type=f"{name}_dispatch")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()
    assert len(result.advisory_dynamic_emits) == 1
    assert "f-string" in result.advisory_dynamic_emits[0]


def test_fstring_with_literal_prefix_surfaces_prefix(tmp_path: Path):
    """F-strings with a leading literal should surface the prefix in
    the advisory so reviewers can spot which Phase-3 cluster the
    site belongs to."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'edge_type = "extends"\n'
        'Edge.create(src="a", dst="b", edge_type=edge_type, line=1, '
        'evidence_type=f"ast_{edge_type}")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert len(result.advisory_dynamic_emits) == 1
    assert "ast_" in result.advisory_dynamic_emits[0]


def test_module_constant_resolution(tmp_path: Path):
    """A keyword arg referencing a module-level string constant should
    resolve to the constant's value and be checked against the
    registry. This catches the scip_relationship case (a real producer
    pattern in scip/edges.py)."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        '_MY_EVIDENCE = "brand_new_label"\n'
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'evidence_type=_MY_EVIDENCE)\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert len(result.strict_violations) == 1
    assert "brand_new_label" in result.strict_violations[0]


def test_unresolvable_name_is_silently_skipped(tmp_path: Path):
    """A keyword arg referencing a function param or local variable
    whose value is unknown is out of scope for the static linter
    (would be caught by a runtime-coherence check). It should NOT
    produce a strict violation OR an advisory."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make_edge(some_arg):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=some_arg)\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()
    assert result.advisory_dynamic_emits == ()


def test_test_files_are_excluded_by_default(tmp_path: Path):
    """Test files legitimately construct synthetic axis values; they
    should be skipped by default."""
    _write(
        tmp_path / "packages" / "demo" / "tests" / "test_demo.py",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'evidence_type="brand_new_label")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()


def test_bare_constructor_call_matches(tmp_path: Path):
    """Bare ``Edge(...)`` (no `.create`) should also be inspected."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'Edge(id="x", src="a", dst="b", edge_type="calls", line=1, '
        'evidence_type="brand_new_label")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert len(result.strict_violations) == 1


def test_non_matching_constructor_is_skipped(tmp_path: Path):
    """A call to a different class's constructor must not match."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'OtherClass.create(src="a", dst="b", '
        'evidence_type="brand_new_label")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()


def test_bare_attribute_name_matches_when_caller_opts_in(tmp_path: Path):
    """When the caller's ``constructor_names`` includes the bare
    attribute (e.g. ``{"create"}``), ANY ``.create(...)`` call site
    matches regardless of the receiver. This is the broad-match path
    on the field-agnostic API; the wrappers in this module use the
    tighter ``{"Edge", "Edge.create"}`` shape so receiver typos don't
    pull in unrelated `.create()` calls. Provided for callers who want
    a wider net (e.g. a hypothetical future linter that gates ALL
    constructor-style calls regardless of receiver class)."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'AnyReceiver.create(name="x", evidence_type="brand_new_label")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert len(result.strict_violations) == 1


def test_call_without_keyword_is_skipped(tmp_path: Path):
    """A constructor call that omits the inspected keyword has nothing
    to validate."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1)\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()
