# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the L3 producer-side axis-coherence linter.

The L3 linter walks producer call sites (Edge.create / Edge( origin="test", origin_run_id="test") /
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

import json
from pathlib import Path

from hypergumbo_core.producer_coherence import (
    find_edge_type_producer_violations,
    find_emitted_edge_types,
    find_emitted_evidence_types,
    find_emitted_literal_values,
    find_emitted_symbol_kinds,
    find_evidence_type_producer_violations,
    find_producer_coherence_violations,
    find_symbol_kind_producer_violations,
    ratchet_diff,
    unregistered_edge_types,
    unregistered_emitted_values,
    unregistered_evidence_types,
    unregistered_symbol_kinds,
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


def test_ast_call_method_not_emitted_after_cluster_d_fold():
    """WI-nibis substrate-wide negative close-check: no producer still emits
    the folded-out ``ast_call_method`` evidence type. The py.py:3811 default
    leak is folded to ``ast_call`` + ``meta['call_construct']='method'`` per
    audit-findings 0012 (Cluster 28D apex/peer collapse)."""
    emitted = find_emitted_evidence_types(REPO_ROOT)
    assert "ast_call_method" not in emitted


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
        'evidence_type="ast_call_direct", origin="test", origin_run_id="test")\n',
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
        'evidence_type="brand_new_label", origin="test", origin_run_id="test")\n',
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
        'evidence_type=f"{name}_dispatch", origin="test", origin_run_id="test")\n',
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
        'evidence_type=f"ast_{edge_type}", origin="test", origin_run_id="test")\n',
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
        'evidence_type=_MY_EVIDENCE, origin="test", origin_run_id="test")\n',
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
        'line=1, evidence_type=some_arg, origin="test", origin_run_id="test")\n',
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
        'evidence_type="brand_new_label", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()


def test_bare_constructor_call_matches(tmp_path: Path):
    """Bare ``Edge(..., origin="test", origin_run_id="test")`` (no `.create`) should also be inspected."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'Edge(id="x", src="a", dst="b", edge_type="calls", line=1, '
        'evidence_type="brand_new_label", origin="test", origin_run_id="test")\n',
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
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()


# --- WI-nubuv extension A: assignment-form trace ---
#
# These cases mirror the WI-nitil empirical leak shape:
#   kind = "<literal>"
#   ...
#   Symbol(kind=kind, ...)
# The literal-kwarg-only matcher caught NONE of them; the WI-nubuv
# extension walks back simple assignment patterns within the enclosing
# function to surface the literal candidates.


def test_assignment_form_literal_in_registry_is_clean(tmp_path: Path):
    """Function-local ``kind = "literal"`` followed by ``Symbol(kind=kind)``
    where the literal IS in the registry should not produce a violation."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make():\n'
        '    label = "ast_call_direct"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()
    assert result.advisory_dynamic_emits == ()


def test_assignment_form_literal_not_in_registry_is_strict(tmp_path: Path):
    """The WI-nitil shape: function-local single-literal assignment
    whose value is NOT in the registry should fail strictly."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make():\n'
        '    label = "brand_new_label"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert len(result.strict_violations) == 1
    assert "brand_new_label" in result.strict_violations[0]


def test_assignment_form_ternary_both_in_registry_is_clean(tmp_path: Path):
    """Ternary ``kind = "a" if cond else "b"`` where both are in
    registry should be clean."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(x):\n'
        '    label = "ast_call_direct" if x else "naming_convention"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct", "naming_convention"}),
    )
    assert result.strict_violations == ()


def test_assignment_form_ternary_one_unregistered_is_strict(tmp_path: Path):
    """Ternary with one branch outside registry should flag that branch."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(x):\n'
        '    label = "ast_call_direct" if x else "brand_new_label"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert len(result.strict_violations) == 1
    assert "brand_new_label" in result.strict_violations[0]


def test_assignment_form_if_else_chain_one_unregistered_is_strict(tmp_path: Path):
    """If/else chain assigning the same name to literals — both branches
    are inspected."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(x):\n'
        '    if x:\n'
        '        label = "ast_call_direct"\n'
        '    else:\n'
        '        label = "brand_new_label"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert len(result.strict_violations) == 1
    assert "brand_new_label" in result.strict_violations[0]


def test_assignment_form_unresolvable_rhs_is_silent(tmp_path: Path):
    """Conservative: if any RHS in scope is unresolvable (function call,
    arithmetic), the whole resolution falls back to silent-skip rather
    than partially flagging — the variable could be anything at runtime."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(x):\n'
        '    label = compute_label(x)\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()
    assert result.advisory_dynamic_emits == ()


def test_assignment_form_nested_function_scope_isolated(tmp_path: Path):
    """An assignment inside a nested function must not pollute the
    outer function's scope — Python lexical scoping; ``inner()`` has its
    own ``label`` binding."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def outer():\n'
        '    def inner():\n'
        '        label = "brand_new_label"\n'
        '        return label\n'
        '    label = "ast_call_direct"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()


def test_assignment_form_nested_if_block_resolves(tmp_path: Path):
    """A literal assignment nested inside an ``if`` block (no else,
    or one-arm) is still in the enclosing function's scope and must be
    resolvable."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(x):\n'
        '    if x:\n'
        '        label = "brand_new_label"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert len(result.strict_violations) == 1
    assert "brand_new_label" in result.strict_violations[0]


def test_assignment_form_annassign_resolves(tmp_path: Path):
    """``label: str = "literal"`` (annotated assignment) should resolve
    the same way as plain assignment."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make():\n'
        '    label: str = "brand_new_label"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert len(result.strict_violations) == 1
    assert "brand_new_label" in result.strict_violations[0]


def test_assignment_form_function_param_is_silent(tmp_path: Path):
    """A function parameter (no in-scope assignment of the name) keeps
    the existing silent-skip behaviour. Regression guard."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(label):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()
    assert result.advisory_dynamic_emits == ()


def test_assignment_form_ternary_one_branch_unresolvable_is_silent(tmp_path: Path):
    """Conservative posture also applies inside ternaries: if one branch
    is a function call (unresolvable), the whole resolution falls back
    to silent-skip rather than partial-flagging the resolvable branch."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(x):\n'
        '    label = "ast_call_direct" if x else compute_label(x)\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()
    assert result.advisory_dynamic_emits == ()


# --- WI-zipis: transitive helper-sink + positional-arg descent ---
#
# Everything above only sees the DIRECT constructor call with the axis
# KEYWORD present. A producer that routes the value through a module-local
# helper and passes it POSITIONALLY is invisible — the proto rpc/service
# shape:
#
#     def _make_proto_symbol(..., name, kind, ...):
#         return Symbol(id=..., name=name, kind=kind, ...)
#     _make_proto_symbol(..., service_name, "service")   # kind arg #6, positional
#
# The literal-kwarg matcher never descends into ``_make_proto_symbol``
# and never binds the positional ``"service"``/``"rpc"`` to the ``kind``
# param, so these two unregistered kinds report ``strict=0`` (the false
# RESOLVED audit-0013 / INV-numat named). ``descend_helpers=True``
# discovers helper functions whose parameter flows into a known sink
# (fixpoint) and binds positional AND keyword arguments at their call
# sites.

_SYM = {
    "constructor_names": frozenset({"Symbol", "Symbol.create"}),
    "keyword_arg": "kind",
    "registry_names": frozenset({"class", "function"}),
}


def test_helper_positional_literal_is_invisible_by_default(tmp_path: Path):
    """The proto shape: the DEFAULT (non-transitive) linter is blind to a
    literal routed positionally through a helper. This pins the false
    RESOLVED that WI-zipis closes."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _make(name, kind):\n'
        '    return Symbol(id="i", name=name, kind=kind)\n'
        '_make("Svc", "service")\n',
    )
    result = find_producer_coherence_violations(tmp_path, **_SYM)
    assert result.strict_violations == ()


def test_helper_positional_literal_not_in_registry_is_strict(tmp_path: Path):
    """With descent, the positional literal bound to the helper's sink
    param surfaces as a strict violation (proto rpc/service shape)."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _make(name, kind):\n'
        '    return Symbol(id="i", name=name, kind=kind)\n'
        '_make("Svc", "service")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    assert len(result.strict_violations) == 1
    assert "service" in result.strict_violations[0]


def test_helper_keyword_literal_not_in_registry_is_strict(tmp_path: Path):
    """Descent also binds the KEYWORD form at the helper call site."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _make(name, kind):\n'
        '    return Symbol(id="i", name=name, kind=kind)\n'
        '_make("Svc", kind="service")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    assert len(result.strict_violations) == 1
    assert "service" in result.strict_violations[0]


def test_helper_positional_literal_in_registry_is_clean(tmp_path: Path):
    """A registered kind routed through the helper produces no violation."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _make(name, kind):\n'
        '    return Symbol(id="i", name=name, kind=kind)\n'
        '_make("Widget", "class")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    assert result.strict_violations == ()


def test_helper_param_not_flowing_to_sink_is_not_a_sink(tmp_path: Path):
    """A helper param that never reaches the constructor's sink slot must
    NOT be treated as a sink — no false positive on its call sites."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _make(bogus):\n'
        '    return Symbol(id="i", name=bogus, kind="class")\n'
        '_make("service")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    assert result.strict_violations == ()


def test_helper_multi_hop_fixpoint(tmp_path: Path):
    """Two-hop indirection: _outer -> _inner -> Symbol(kind=). The fixpoint
    must promote BOTH helpers to sinks and flag the outer call site."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _inner(k):\n'
        '    return Symbol(id="i", name="n", kind=k)\n'
        'def _outer(kk):\n'
        '    return _inner(kk)\n'
        '_outer("service")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    assert len(result.strict_violations) == 1
    assert "service" in result.strict_violations[0]


def test_helper_call_with_function_local_name_resolves(tmp_path: Path):
    """A helper called with a Name bound to a function-local literal
    resolves via the caller's scope, same as the direct path."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _make(name, kind):\n'
        '    return Symbol(id="i", name=name, kind=kind)\n'
        'def caller():\n'
        '    k = "service"\n'
        '    return _make("Svc", k)\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    assert len(result.strict_violations) == 1
    assert "service" in result.strict_violations[0]


def test_helper_starred_positional_arg_does_not_crash(tmp_path: Path):
    """A starred call arg (``*args``) makes positional index binding
    ambiguous; the linter skips it rather than mis-binding."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _make(name, kind):\n'
        '    return Symbol(id="i", name=name, kind=kind)\n'
        'args = ("Svc", "service")\n'
        '_make(*args)\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    assert result.strict_violations == ()


def test_helper_call_missing_sink_arg_is_skipped(tmp_path: Path):
    """A helper call that omits the sink arg entirely (relies on a
    default) has nothing to bind."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _make(name, kind="class"):\n'
        '    return Symbol(id="i", name=name, kind=kind)\n'
        '_make("Widget")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    assert result.strict_violations == ()


def test_descend_helpers_preserves_direct_constructor_path(tmp_path: Path):
    """With descent ON, the direct-constructor keyword path still works
    (a direct unregistered literal is still flagged, exactly once)."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'Symbol(id="i", name="n", kind="brand_new_kind")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    assert len(result.strict_violations) == 1
    assert "brand_new_kind" in result.strict_violations[0]


def test_helper_with_dotted_constructor(tmp_path: Path):
    """A helper whose sink is the dotted ``Symbol.create`` form is
    discovered (exercises the Attribute constructor-match path) and the
    ``Symbol.create`` call site itself is not double-reported."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _make(name, kind):\n'
        '    return Symbol.create(id="i", name=name, kind=kind)\n'
        '_make("Svc", "service")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    assert len(result.strict_violations) == 1
    assert "service" in result.strict_violations[0]


def test_helper_body_unrelated_call_ignored(tmp_path: Path):
    """A non-sink call in a helper body (neither a constructor nor a
    known helper) is skipped during discovery without derailing the
    promotion of the real sink."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _make(name, kind):\n'
        '    log("making")\n'
        '    return Symbol(id="i", name=name, kind=kind)\n'
        '_make("Svc", "service")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    assert len(result.strict_violations) == 1
    assert "service" in result.strict_violations[0]


def test_helper_constructor_without_sink_kwarg_is_not_a_sink(tmp_path: Path):
    """A helper whose only constructor call omits the sink keyword (and
    supplies no positional index for it) is never promoted — the arg
    binding returns None."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _make(bogus):\n'
        '    return Symbol(id="i", name=bogus)\n'
        '_make("service")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    assert result.strict_violations == ()


def test_helper_nested_closure_is_discovered(tmp_path: Path):
    """The dominant emit-helper shape across analyzers is a NESTED closure
    (``def make_symbol(...)`` inside ``analyze(...)``) — thrift, ocaml, dart,
    haskell, solidity, and ~half a dozen more. The fixpoint must descend into
    nested function defs, not just module-level ones, or the census silently
    undercounts (the INV-numat trap)."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def analyze():\n'
        '    def make_symbol(name, kind):\n'
        '        return Symbol(id="i", name=name, kind=kind)\n'
        '    return make_symbol("Svc", "service")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    # Exactly one: the nested make_symbol call site. The enclosing analyze()
    # must NOT be falsely promoted (it passes literals, not its own param).
    assert len(result.strict_violations) == 1
    assert "service" in result.strict_violations[0]


def test_helper_nested_closure_registered_kind_is_clean(tmp_path: Path):
    """A nested helper emitting a registered kind produces no violation."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def analyze():\n'
        '    def make_symbol(name, kind):\n'
        '        return Symbol(id="i", name=name, kind=kind)\n'
        '    return make_symbol("Widget", "class")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path, descend_helpers=True, **_SYM,
    )
    assert result.strict_violations == ()


# --- WI-zipis: ratchet gate (descend-aware enumerator + baseline diff) ---


def test_find_emitted_literal_values_descend_covers_helper(tmp_path: Path):
    """The value enumerator gains ``descend_helpers`` so a helper-routed
    literal is enumerated (feeds the ratchet's live set)."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _make(name, kind):\n'
        '    return Symbol(id="i", name=name, kind=kind)\n'
        '_make("Svc", "wonkytype")\n',
    )
    ctors = frozenset({"Symbol", "Symbol.create"})
    shallow = find_emitted_literal_values(
        tmp_path, constructor_names=ctors, keyword_arg="kind",
    )
    assert "wonkytype" not in shallow
    deep = find_emitted_literal_values(
        tmp_path, constructor_names=ctors, keyword_arg="kind",
        descend_helpers=True,
    )
    assert "wonkytype" in deep


def test_unregistered_emitted_values_filters_registry(tmp_path: Path):
    """Only values absent from the registry are returned (descend-aware)."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def _make(name, kind):\n'
        '    return Symbol(id="i", name=name, kind=kind)\n'
        '_make("A", "class")\n'
        '_make("B", "wonkytype")\n',
    )
    result = unregistered_emitted_values(
        tmp_path, constructor_names=frozenset({"Symbol", "Symbol.create"}),
        keyword_arg="kind", registry_names=frozenset({"class"}),
    )
    assert set(result) == {"wonkytype"}
    assert "demo.py" in result["wonkytype"][0]


def test_ratchet_diff_new_and_stale():
    """New leaks = live not in baseline; stale = baseline not in live."""
    new_leaks, stale = ratchet_diff({"a", "b", "c"}, {"b", "c", "d"})
    assert new_leaks == ["a"]
    assert stale == ["d"]


def test_ratchet_diff_clean():
    new_leaks, stale = ratchet_diff({"a", "b"}, {"a", "b"})
    assert new_leaks == []
    assert stale == []


def test_live_tree_producer_axis_ratchet():
    """THE producer-side closure gate for INV-numat: the live tree's
    unregistered helper-descend emitted values, per axis, must exactly
    equal the committed baseline. A NEW unregistered value fails
    (regression); a baselined value since drained must be removed
    (shrink-only). Replaces per-cohort violation-counting."""
    baseline = json.loads(
        (REPO_ROOT / ".ci" / "producer-axis-coherence-baseline.json").read_text()
    )
    for axis, finder in (
        ("Symbol.kind", unregistered_symbol_kinds),
        ("Edge.evidence_type", unregistered_evidence_types),
        ("Edge.edge_type", unregistered_edge_types),
    ):
        live = set(finder(REPO_ROOT))
        new_leaks, stale = ratchet_diff(live, set(baseline.get(axis, [])))
        assert not new_leaks, (
            f"{axis}: NEW unregistered producer value(s) {new_leaks} — "
            f"register the value (ADR-0027) or fold the producer (WI-zipis)."
        )
        assert not stale, (
            f"{axis}: baselined value(s) {stale} no longer emitted — remove "
            f"from .ci/producer-axis-coherence-baseline.json (shrink-only)."
        )


def test_assignment_form_annassign_without_value_is_skipped(tmp_path: Path):
    """``label: str`` is a type-annotation declaration with no RHS;
    it does not bind a value and must be skipped during the walk-back.
    A subsequent real assignment (``label = "literal"``) is still
    resolved normally."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make():\n'
        '    label: str\n'
        '    label = "brand_new_label"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert len(result.strict_violations) == 1
    assert "brand_new_label" in result.strict_violations[0]


# --- find_emitted_literal_values + per-axis wrappers ---


def _write_producer(tmp_path: Path, body: str) -> None:
    _write(tmp_path / "packages" / "demo" / "src" / "demo.py", body)


def test_emitted_symbol_kinds_returns_literal_kwarg_emit_sites(tmp_path: Path):
    """find_emitted_symbol_kinds maps {kind_value: (file:line, ...)} and
    catches literal-kwarg emits at Symbol(...) call sites."""
    _write_producer(
        tmp_path,
        'from foo import Symbol\n'
        'def make():\n'
        '    return Symbol(kind="custom_kind", id="x")\n',
    )
    sites = find_emitted_symbol_kinds(tmp_path)
    assert "custom_kind" in sites
    assert any("demo.py:3" in s for s in sites["custom_kind"])


def test_emitted_evidence_types_returns_literal_kwarg_emit_sites(tmp_path: Path):
    """find_emitted_evidence_types maps {evidence_type: (file:line, ...)}
    and catches literal-kwarg emits at Edge(..., origin="test", origin_run_id="test") call sites."""
    _write_producer(
        tmp_path,
        'from foo import Edge\n'
        'def link():\n'
        '    return Edge(src="a", dst="b", evidence_type="brand_inference", '
        'edge_type="calls", origin="test", origin_run_id="test")\n',
    )
    sites = find_emitted_evidence_types(tmp_path)
    assert "brand_inference" in sites
    assert any("demo.py:3" in s for s in sites["brand_inference"])


def test_emitted_edge_types_returns_literal_kwarg_emit_sites(tmp_path: Path):
    """find_emitted_edge_types maps {edge_type: (file:line, ...)} and
    catches literal-kwarg emits at Edge(..., origin="test", origin_run_id="test") call sites."""
    _write_producer(
        tmp_path,
        'from foo import Edge\n'
        'def link():\n'
        '    return Edge(src="a", dst="b", edge_type="brand_relation", origin="test", origin_run_id="test")\n',
    )
    sites = find_emitted_edge_types(tmp_path)
    assert "brand_relation" in sites
    assert any("demo.py:3" in s for s in sites["brand_relation"])


def test_emitted_literal_values_resolves_assignment_form_to_name(tmp_path: Path):
    """The literal enumerator picks up assignment-form-to-Name shapes
    (WI-nubuv ext A scope), not just the literal-kwarg shape."""
    _write_producer(
        tmp_path,
        'from foo import Symbol\n'
        'def make():\n'
        '    k = "via_assignment"\n'
        '    return Symbol(kind=k, id="x")\n',
    )
    sites = find_emitted_literal_values(
        tmp_path,
        constructor_names=frozenset({"Symbol"}),
        keyword_arg="kind",
    )
    assert "via_assignment" in sites


def test_emitted_literal_values_resolves_ternary_assignment(tmp_path: Path):
    """A ternary assignment with both branches resolvable contributes
    both literal candidates to the emit map (ext A frozenset path)."""
    _write_producer(
        tmp_path,
        'from foo import Symbol\n'
        'def make(cond):\n'
        '    k = "branch_a" if cond else "branch_b"\n'
        '    return Symbol(kind=k, id="x")\n',
    )
    sites = find_emitted_literal_values(
        tmp_path,
        constructor_names=frozenset({"Symbol"}),
        keyword_arg="kind",
    )
    assert "branch_a" in sites
    assert "branch_b" in sites


def test_emitted_literal_values_skips_fstring_and_unresolvable(tmp_path: Path):
    """f-string and unresolvable-Name shapes contribute no literal
    values to the emit map. They're flagged as advisories by the gate
    function, not enumerated as emit candidates."""
    _write_producer(
        tmp_path,
        'from foo import Symbol\n'
        'def dynamic(prefix, foo):\n'
        '    return Symbol(kind=f"prefix_{foo}", id="x")\n'
        'def name_unresolvable(other_var):\n'
        '    return Symbol(kind=other_var, id="y")\n',
    )
    sites = find_emitted_literal_values(
        tmp_path,
        constructor_names=frozenset({"Symbol"}),
        keyword_arg="kind",
    )
    assert sites == {}


def test_emitted_literal_values_skips_missing_search_root(tmp_path: Path):
    """Missing search-root directories are silently skipped — the empty
    map is the correct result for a tmp_path with no packages/."""
    sites = find_emitted_literal_values(
        tmp_path,
        constructor_names=frozenset({"Symbol"}),
        keyword_arg="kind",
    )
    assert sites == {}


def test_assignment_form_module_constant_still_takes_precedence(tmp_path: Path):
    """When a module-level constant exists with the same name as a
    function-local variable, the function-local resolution still uses
    its own assignment (Python LEGB scoping). Sanity check that the
    function-local path doesn't accidentally pull in the module-level
    constant's value."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'label = "ast_call_direct"\n'
        'def make():\n'
        '    label = "brand_new_label"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert len(result.strict_violations) == 1
    assert "brand_new_label" in result.strict_violations[0]


# --- WI-nubuv: inline IfExp + non-string-constant skip ---
#
# The pre-ext-B classifier only recognized inline ternary at the
# function-local-assignment path, not at the kwarg site itself. And the
# function-local walker poisoned on ``= None`` sentinels even when the
# real string assignments followed (the ``edge_type = None`` shape at
# linkers/inheritance.py:209 vs the resolvable string assignments at
# :218 / :227). Both gaps closed in this PR.


def test_inline_ternary_resolves_when_both_branches_in_registry(tmp_path: Path):
    """Inline ``evidence_type="a" if cond else "b"`` at the kwarg site
    resolves to the union of both branches via _resolve_simple_rhs.
    Pre-fix, this was classified ``unresolvable=IfExp`` and silently
    skipped — a real producer-side leak shape (ipc.py:546,
    message_queue.py:516)."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(cond):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type="ast_call_direct" if cond else "naming_convention", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct", "naming_convention"}),
    )
    assert result.strict_violations == ()


def test_inline_ternary_one_branch_outside_registry_is_strict(tmp_path: Path):
    """Inline ternary with one branch missing from registry → strict per
    offending branch."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(cond):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type="ast_call_direct" if cond else "brand_new_label", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert len(result.strict_violations) == 1
    assert "brand_new_label" in result.strict_violations[0]


def test_inline_ternary_both_branches_same_literal_resolves_as_literal(tmp_path: Path):
    """Edge case: ``f(kind="x" if cond else "x")`` collapses to a single
    literal via the union-of-frozensets path."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(cond):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type="ast_call_direct" if cond else "ast_call_direct", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()
    assert result.advisory_dynamic_emits == ()


def test_emitted_literal_values_excludes_test_files(tmp_path: Path):
    """The emit enumerator must skip ``/tests/`` paths by default — same
    posture as the gate function — so synthetic kwargs in test fixtures
    don't pollute the emit map."""
    from hypergumbo_core.producer_coherence import find_emitted_literal_values
    _write(
        tmp_path / "packages" / "demo" / "tests" / "test_demo.py",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'evidence_type="test_only_value", origin="test", origin_run_id="test")\n',
    )
    sites = find_emitted_literal_values(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
    )
    assert sites == {}


def test_inline_ternary_one_branch_unresolvable_falls_to_unresolvable(tmp_path: Path):
    """Conservative: if either branch is unresolvable (function call),
    classifier returns ``unresolvable=IfExp`` and the kwarg is silently
    skipped (variable_form_mode default)."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(cond):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type="ast_call_direct" if cond else helper(), origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()
    assert result.advisory_dynamic_emits == ()


def test_assignment_form_none_sentinel_then_literal_resolves(tmp_path: Path):
    """The inheritance.py:209 shape: ``edge_type = None`` sentinel before
    real string assignments. Pre-fix, _resolve_simple_rhs returned None
    for ``= None`` and poisoned the whole resolution; now non-string
    Constants contribute empty set, letting the real string assignments
    populate candidates normally."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(cond):\n'
        '    label = None\n'
        '    if cond:\n'
        '        label = "ast_call_direct"\n'
        '    else:\n'
        '        label = "naming_convention"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct", "naming_convention"}),
    )
    assert result.strict_violations == ()


def test_assignment_form_only_non_string_constants_falls_to_unresolvable(tmp_path: Path):
    """If every assignment to the name is a non-string Constant (None,
    int, bool), _resolve_function_local returns the empty frozenset and
    _classify_value falls through to module-constant lookup, then to
    unresolvable. Silent under default variable_form_mode='silent'."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make():\n'
        '    label = None\n'
        '    label = 42\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()
    assert result.advisory_dynamic_emits == ()


# --- WI-nubuv extension B: f-string expansion via function-local trace ---


def test_fstring_expand_mode_silently_accepts_resolvable(tmp_path: Path):
    """The acceptance criterion from the WI: an f-string with a literal
    prefix concatenated with a function-locally-resolvable Name expands
    to the prefix-concatenated set of candidates; if every candidate is
    in the registry, the site is silently accepted (no advisory)."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(cond):\n'
        '    edge_type = "extends" if cond else "implements"\n'
        '    return Edge.create(src="a", dst="b", edge_type=edge_type, '
        'line=1, evidence_type=f"ast_{edge_type}", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_extends", "ast_implements"}),
        fstring_mode="expand",
    )
    assert result.strict_violations == ()
    assert result.advisory_dynamic_emits == ()


def test_fstring_expand_mode_strict_when_expansion_outside_registry(tmp_path: Path):
    """In expand mode, an f-string whose expansion includes a value
    outside the registry should fail strictly per offending expansion."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(cond):\n'
        '    edge_type = "extends" if cond else "brand_new_relation"\n'
        '    return Edge.create(src="a", dst="b", edge_type=edge_type, '
        'line=1, evidence_type=f"ast_{edge_type}", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_extends"}),
        fstring_mode="expand",
    )
    assert len(result.strict_violations) == 1
    assert "ast_brand_new_relation" in result.strict_violations[0]


def test_fstring_expand_mode_falls_back_to_advisory_when_unexpandable(tmp_path: Path):
    """If any FormattedValue segment can't be resolved (function param,
    for-loop unpack, function call), expand mode falls back to the
    advisory path rather than failing strictly. Strict mode is the
    aggressive alternative — see next test."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(prefix):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=f"{prefix}_call", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
        fstring_mode="expand",
    )
    assert result.strict_violations == ()
    assert len(result.advisory_dynamic_emits) == 1
    assert "f-string" in result.advisory_dynamic_emits[0]


def test_fstring_strict_mode_flags_unexpandable_as_strict(tmp_path: Path):
    """Strict mode promotes unexpandable f-strings to strict violations.
    For axes where every producer should be either a literal or a fully
    enumerable f-string."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(prefix):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=f"{prefix}_call", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
        fstring_mode="strict",
    )
    assert len(result.strict_violations) == 1
    assert "unexpandable" in result.strict_violations[0]


def test_fstring_strict_mode_silently_accepts_resolvable_in_registry(tmp_path: Path):
    """Strict mode still silently accepts f-strings whose expansion is
    fully resolvable and all in registry."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make():\n'
        '    edge_type = "extends"\n'
        '    return Edge.create(src="a", dst="b", edge_type=edge_type, '
        'line=1, evidence_type=f"ast_{edge_type}", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_extends"}),
        fstring_mode="strict",
    )
    assert result.strict_violations == ()
    assert result.advisory_dynamic_emits == ()


def test_fstring_expand_with_format_spec_falls_through(tmp_path: Path):
    """``f"{x:>5}"`` (format spec) is treated as unexpandable because
    formatting mutates the string. Expand mode falls back to advisory."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make():\n'
        '    x = "foo"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=f"{x:>5}", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
        fstring_mode="expand",
    )
    assert result.strict_violations == ()
    assert len(result.advisory_dynamic_emits) == 1


def test_fstring_expand_with_conversion_falls_through(tmp_path: Path):
    """``f"{x!r}"`` (repr conversion) is treated as unexpandable."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make():\n'
        '    x = "foo"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=f"{x!r}", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
        fstring_mode="expand",
    )
    assert result.strict_violations == ()
    assert len(result.advisory_dynamic_emits) == 1


def test_fstring_expand_cartesian_cap_bails_to_advisory(tmp_path: Path):
    """When the Cartesian product of segment candidates would exceed the
    expansion cap, expand mode bails to advisory rather than materializing
    a huge set."""
    # Two segments x 8 candidates each = 64 expansions > _FSTRING_EXPANSION_CAP=32.
    branches_a = "\n    ".join(
        f'if cond == {i}: a = "v{i}"' for i in range(8)
    )
    branches_b = "\n    ".join(
        f'if cond == {i}: b = "w{i}"' for i in range(8)
    )
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(cond):\n'
        f'    {branches_a}\n'
        f'    {branches_b}\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=f"{a}_{b}", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
        fstring_mode="expand",
    )
    # Cap exceeded → fallback to advisory, not strict.
    assert result.strict_violations == ()
    assert len(result.advisory_dynamic_emits) == 1


def test_fstring_advisory_mode_preserves_pre_ext_b_behavior(tmp_path: Path):
    """In advisory mode (the base-function default), f-strings still
    surface as advisories regardless of expandability — preserves the
    pre-WI-nubuv-ext-B contract."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make():\n'
        '    edge_type = "extends"\n'
        '    return Edge.create(src="a", dst="b", edge_type=edge_type, '
        'line=1, evidence_type=f"ast_{edge_type}", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_extends"}),
        fstring_mode="advisory",
    )
    assert result.strict_violations == ()
    assert len(result.advisory_dynamic_emits) == 1


def test_fstring_expand_with_constant_inner_resolves(tmp_path: Path):
    """An f-string with a literal-Constant FormattedValue
    (``f"{1}_call"``) resolves to a single expansion via
    _resolve_simple_rhs's non-string-Constant handling (empty frozenset)
    — which makes the whole f-string unexpandable. Expand mode falls
    back to advisory."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make():\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=f"{1}_call", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
        fstring_mode="expand",
    )
    # Constant(1) is non-string → empty frozenset → unexpandable.
    assert result.strict_violations == ()
    assert len(result.advisory_dynamic_emits) == 1


# --- WI-nubuv extension C: variable-form structural backstop ---


def test_variable_form_silent_default_preserves_existing_behavior(tmp_path: Path):
    """Default ``variable_form_mode='silent'`` keeps the pre-ext-C
    contract: unresolvable Names (function params, for-loop unpacks,
    function calls) surface as neither strict nor advisory."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(label):\n'  # function param — unresolvable
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
    )
    assert result.strict_violations == ()
    assert result.advisory_dynamic_emits == ()


def test_variable_form_advisory_mode_surfaces_function_param(tmp_path: Path):
    """Advisory mode reports unresolvable Names as advisories — visible
    audit signal without blocking the commit."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(label):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
        variable_form_mode="advisory",
    )
    assert result.strict_violations == ()
    assert len(result.advisory_dynamic_emits) == 1
    assert "variable-form producer" in result.advisory_dynamic_emits[0]
    assert "Name(label)" in result.advisory_dynamic_emits[0]


def test_variable_form_strict_mode_flags_function_param(tmp_path: Path):
    """Strict mode bans the variable form: every unresolvable Name
    becomes a strict violation. This is the structural backstop for the
    four blind-spot shapes (literal-kwarg, assignment-form, f-string,
    dict-subscript-target) — when the value can't be statically gated,
    refuse to ship rather than rely on runtime checks."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make(label):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
        variable_form_mode="strict",
    )
    assert len(result.strict_violations) == 1
    assert "variable form banned" in result.strict_violations[0]
    assert "Name(label)" in result.strict_violations[0]


def test_variable_form_strict_mode_flags_function_call_result(tmp_path: Path):
    """Strict mode also flags ``evidence_type=compute()`` — a function
    call return is one of the four blind-spot shapes the ext-C backstop
    is designed to ban."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make():\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=compute(), origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
        variable_form_mode="strict",
    )
    assert len(result.strict_violations) == 1
    assert "variable form banned" in result.strict_violations[0]
    assert "Call" in result.strict_violations[0]


def test_variable_form_strict_mode_does_not_flag_literal(tmp_path: Path):
    """Strict mode must not false-positive on literal kwargs — those
    have nothing to ban."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'evidence_type="ast_call_direct", origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
        variable_form_mode="strict",
    )
    assert result.strict_violations == ()


def test_variable_form_strict_does_not_flag_resolvable_assignment(tmp_path: Path):
    """Strict mode must not false-positive on assignment-form Names
    that ext A resolves — those aren't 'variable form', they're
    statically gated."""
    _write(
        tmp_path / "packages" / "demo" / "src" / "demo.py",
        'def make():\n'
        '    label = "ast_call_direct"\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, evidence_type=label, origin="test", origin_run_id="test")\n',
    )
    result = find_producer_coherence_violations(
        tmp_path,
        constructor_names=frozenset({"Edge", "Edge.create"}),
        keyword_arg="evidence_type",
        registry_names=frozenset({"ast_call_direct"}),
        variable_form_mode="strict",
    )
    assert result.strict_violations == ()


# --- Wrapper signature: per-axis opt-in flags ---


def test_evidence_type_wrapper_accepts_mode_kwargs():
    """The per-axis wrappers expose fstring_mode and variable_form_mode
    so callers can opt into stricter gates without rebuilding the
    constructor_names / keyword_arg / registry_names triple by hand."""
    # We use a tmp_path that doesn't exist to keep the result trivially
    # empty — the relevant test is that the kwargs are accepted.
    from pathlib import Path as _P
    nonexistent = _P("/nonexistent-for-wrapper-signature-test")
    r = find_evidence_type_producer_violations(
        nonexistent, fstring_mode="strict", variable_form_mode="strict",
    )
    assert r.strict_violations == ()


def test_symbol_kind_wrapper_accepts_mode_kwargs():
    from pathlib import Path as _P
    nonexistent = _P("/nonexistent-for-wrapper-signature-test")
    r = find_symbol_kind_producer_violations(
        nonexistent, fstring_mode="strict", variable_form_mode="strict",
    )
    assert r.strict_violations == ()


def test_edge_type_wrapper_accepts_mode_kwargs():
    from pathlib import Path as _P
    nonexistent = _P("/nonexistent-for-wrapper-signature-test")
    r = find_edge_type_producer_violations(
        nonexistent, fstring_mode="strict", variable_form_mode="strict",
    )
    assert r.strict_violations == ()
