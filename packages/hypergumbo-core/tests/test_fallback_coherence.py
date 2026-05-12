# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the INV-zuhub fallback-coherence linter.

The linter asserts: every ``Edge.create(...)`` call site in
``packages/.../linkers/`` that sets
``meta["disambiguation_fallback"] = True`` must also set
``confidence`` to a statically-resolvable value <= 0.5.

The live-tree invariant test
:func:`test_no_fallback_coherence_violations_in_linkers` runs the
finder against the actual linker tree at every test session — INV-zuhub
acceptance item 2 ("AST-walking property test that asserts every
fallback-emitting linker code path sets conf<=0.5 + meta flag") landed
in this PR uses this test as its enforcement surface.

Synthetic-fixture tests cover the AST walker's positive and negative
classification paths (no live linker code is exercised by these — they
write small Python source trees into ``tmp_path`` and feed them
through the finder).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.fallback_coherence import (
    CONFIDENCE_CEILING,
    CONSTRUCTOR_NAMES,
    DEFAULT_LINKER_ROOTS,
    FALLBACK_META_KEY,
    FallbackCoherenceViolation,
    find_fallback_coherence_violations,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


# --- Live-tree invariant: every linker source conforms ---


def test_no_fallback_coherence_violations_in_linkers():
    """INV-zuhub item 2 enforcement. The single live-tree assertion
    that gates every commit: if any linker source under
    ``packages/.../linkers/`` sets ``meta[disambiguation_fallback]=True``
    on an Edge.create call without matching ``confidence <= 0.5`` on
    the same call, this test fails with a CI-friendly diagnostic listing
    every offending site."""
    violations = find_fallback_coherence_violations(REPO_ROOT)
    assert not violations, (
        "INV-zuhub fallback-coherence violations:\n"
        + "\n".join(v.format() for v in violations)
    )


# --- Constants exported for cross-module use ---


def test_constructor_names_includes_edge_and_dotted_create():
    """Both ``Edge(...)`` and ``Edge.create(...)`` are recognized."""
    assert "Edge" in CONSTRUCTOR_NAMES
    assert "Edge.create" in CONSTRUCTOR_NAMES


def test_fallback_meta_key_matches_axis_meta_keys_registry():
    """The flag name lives in two places — this module and
    :mod:`hypergumbo_core.axis_meta_keys`. The single source of truth
    is the registry; this test pins the string match so a rename in
    the registry without a corresponding update here fails loudly."""
    from hypergumbo_core.axis_meta_keys import all_meta_key_names
    assert FALLBACK_META_KEY in all_meta_key_names()


def test_confidence_ceiling_matches_inv_zuhub_statement():
    """INV-zuhub statement: ``confidence <= 0.5``."""
    assert CONFIDENCE_CEILING == 0.5


def test_default_linker_roots_points_to_core_linkers():
    """Default scan root is the core linkers package — narrower than
    producer_coherence's three-root surface because the
    disambiguation_fallback discipline is a Tier 2 linker concern."""
    assert any(
        "linkers" in root and "hypergumbo-core" in root
        for root in DEFAULT_LINKER_ROOTS
    )


def test_violation_format_is_one_line_diagnostic():
    """Output shape matches the ``path:line: reason`` pre-commit
    convention so the violation list pastes cleanly into a hook."""
    v = FallbackCoherenceViolation(
        path="packages/demo/linker.py",
        lineno=42,
        reason="confidence > 0.5",
    )
    assert v.format() == "packages/demo/linker.py:42: confidence > 0.5"


# --- Synthetic-fixture tests for the AST walker ---


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


# The synthetic fixtures write into
# ``packages/hypergumbo-core/src/hypergumbo_core/linkers/<name>.py``
# so they fall under DEFAULT_LINKER_ROOTS without an override.
LINKER_DIR = Path("packages/hypergumbo-core/src/hypergumbo_core/linkers")


def _linker(tmp_path: Path, name: str, body: str) -> None:
    _write(tmp_path / LINKER_DIR / f"{name}.py", body)


def test_returns_empty_when_linker_root_missing(tmp_path: Path):
    """Missing linker dir → no violations (don't crash on isolated
    package layouts that lack the linkers subtree)."""
    assert find_fallback_coherence_violations(tmp_path) == ()


def test_call_without_meta_flag_is_clean(tmp_path: Path):
    """``Edge.create`` without the fallback meta flag is out of scope —
    the linter only enforces sites that *declare* themselves as
    fallback-emit sites."""
    _linker(
        tmp_path,
        "demo",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'confidence=0.95)\n',
    )
    assert find_fallback_coherence_violations(tmp_path) == ()


def test_literal_low_confidence_with_flag_is_clean(tmp_path: Path):
    """The canonical conforming shape: literal ``confidence=0.5`` with
    the meta flag set inline."""
    _linker(
        tmp_path,
        "demo",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'confidence=0.5, meta={"disambiguation_fallback": True})\n',
    )
    assert find_fallback_coherence_violations(tmp_path) == ()


def test_literal_high_confidence_with_flag_is_violation(tmp_path: Path):
    """``confidence=0.9`` with the flag set → violation per the
    ceiling check."""
    _linker(
        tmp_path,
        "demo",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'confidence=0.9, meta={"disambiguation_fallback": True})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1
    assert "exceed ceiling" in violations[0].reason


def test_missing_confidence_with_flag_is_violation(tmp_path: Path):
    """Setting the flag without a confidence kwarg at all → violation;
    the contract requires both fields."""
    _linker(
        tmp_path,
        "demo",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'meta={"disambiguation_fallback": True})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1
    assert "does not set confidence" in violations[0].reason


def test_function_local_ternary_resolves_through_assignment(tmp_path: Path):
    """The canonical inheritance.py shape: ``confidence`` is a Name
    bound to a ternary in the enclosing function, both branches
    statically resolvable."""
    _linker(
        tmp_path,
        "demo",
        'def make(is_fallback):\n'
        '    confidence = 0.5 if is_fallback else 0.95\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, confidence=confidence, '
        'meta={"disambiguation_fallback": True} if is_fallback else None)\n',
    )
    # The True branch of the meta ternary sets the flag; the True
    # branch of the confidence ternary is 0.5; the contract is
    # satisfied. The walker only enforces the FLAG-set branch.
    violations = find_fallback_coherence_violations(tmp_path)
    assert violations == ()


def test_function_local_ternary_with_high_confidence_branch_is_violation(tmp_path: Path):
    """If the function-local ternary's True branch exceeds the
    ceiling, that's a violation (the flag is set but conf isn't
    statically <= 0.5 in the relevant branch — well, in *any* branch
    actually, because the walker is conservative)."""
    _linker(
        tmp_path,
        "demo",
        'def make(is_fallback):\n'
        '    confidence = 0.7 if is_fallback else 0.95\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, confidence=confidence, '
        'meta={"disambiguation_fallback": True} if is_fallback else None)\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1
    assert "exceed ceiling" in violations[0].reason


def test_unresolvable_confidence_name_is_violation(tmp_path: Path):
    """Function parameter ``confidence=conf_param`` with no in-scope
    binding to a literal → not statically resolvable → violation
    (the static gate cannot verify runtime values)."""
    _linker(
        tmp_path,
        "demo",
        'def make(conf_param):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, confidence=conf_param, '
        'meta={"disambiguation_fallback": True})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1
    assert "not statically resolvable" in violations[0].reason


def test_call_via_function_call_in_confidence_is_violation(tmp_path: Path):
    """``confidence=compute()`` is structurally unresolvable; same
    violation class as a function-param Name."""
    _linker(
        tmp_path,
        "demo",
        'def make():\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, confidence=compute(), '
        'meta={"disambiguation_fallback": True})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1
    assert "not statically resolvable" in violations[0].reason


def test_bare_edge_constructor_is_also_checked(tmp_path: Path):
    """``Edge(...)`` (no ``.create``) is also a constructor call site
    per :data:`CONSTRUCTOR_NAMES`."""
    _linker(
        tmp_path,
        "demo",
        'Edge(id="x", src="a", dst="b", edge_type="calls", line=1, '
        'confidence=0.9, meta={"disambiguation_fallback": True})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1


def test_unrelated_constructor_is_skipped(tmp_path: Path):
    """A different class's constructor isn't gated — the contract is
    Edge-specific."""
    _linker(
        tmp_path,
        "demo",
        'OtherClass.create(src="a", dst="b", confidence=0.9, '
        'meta={"disambiguation_fallback": True})\n',
    )
    assert find_fallback_coherence_violations(tmp_path) == ()


def test_meta_via_name_reference_resolves_to_flag_dict(tmp_path: Path):
    """``meta=edge_meta`` where ``edge_meta`` has a single function-local
    assignment to a flag-bearing dict gets resolved through; the call
    is treated as fallback-emit and the confidence is checked. This
    catches the canonical inheritance.py shape post-refactor (both
    fields bound to local Names rather than inline)."""
    _linker(
        tmp_path,
        "demo",
        'def make():\n'
        '    edge_meta = {"disambiguation_fallback": True}\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, confidence=0.9, meta=edge_meta)\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1
    assert "exceed ceiling" in violations[0].reason


def test_meta_via_name_reference_unresolvable_is_silent(tmp_path: Path):
    """``meta=some_dict_var`` where ``some_dict_var`` is a function
    parameter (no in-scope assignment) cannot be resolved → silent.
    The conservative posture for genuinely unresolvable shapes; the
    flagless-fallback case is caught by per-linker property tests
    instead (INV-zuhub item 1)."""
    _linker(
        tmp_path,
        "demo",
        'def make(some_dict_var):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, confidence=0.9, meta=some_dict_var)\n',
    )
    assert find_fallback_coherence_violations(tmp_path) == ()


def test_meta_dict_with_other_keys_alongside_flag_is_checked(tmp_path: Path):
    """Inline dict with the flag set + other keys (e.g.,
    ``protocol``) still triggers the gate — the contract is about the
    flag's presence, not the dict's exclusive content."""
    _linker(
        tmp_path,
        "demo",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'confidence=0.9, '
        'meta={"disambiguation_fallback": True, "protocol": "grpc"})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1


def test_meta_flag_set_to_false_is_silent(tmp_path: Path):
    """``"disambiguation_fallback": False`` is NOT a fallback-emit
    declaration — the contract only applies when the flag is True."""
    _linker(
        tmp_path,
        "demo",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'confidence=0.9, meta={"disambiguation_fallback": False})\n',
    )
    assert find_fallback_coherence_violations(tmp_path) == ()


def test_meta_inline_ternary_with_flag_in_true_branch(tmp_path: Path):
    """``meta={...flag...} if cond else None`` is a recognized
    fallback-emit declaration shape. The walker descends into the True
    branch and recognizes the flag-bearing dict."""
    _linker(
        tmp_path,
        "demo",
        'def make(is_fallback):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, confidence=0.5, '
        'meta={"disambiguation_fallback": True} if is_fallback else None)\n',
    )
    assert find_fallback_coherence_violations(tmp_path) == ()


def test_negative_literal_confidence_resolves(tmp_path: Path):
    """``confidence=-0.5`` (synthetic edge case) resolves via
    UnaryOp(USub, Constant). Below the ceiling → clean."""
    _linker(
        tmp_path,
        "demo",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'confidence=-0.5, meta={"disambiguation_fallback": True})\n',
    )
    assert find_fallback_coherence_violations(tmp_path) == ()


def test_negative_unresolvable_unaryop_is_unresolvable(tmp_path: Path):
    """``confidence=-compute()`` — the UnaryOp wraps an unresolvable
    inner expression, propagating the unresolvable verdict."""
    _linker(
        tmp_path,
        "demo",
        'def make():\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, confidence=-compute(), '
        'meta={"disambiguation_fallback": True})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1
    assert "not statically resolvable" in violations[0].reason


def test_integer_confidence_one_resolves(tmp_path: Path):
    """``confidence=1`` (integer literal) — above ceiling, violation."""
    _linker(
        tmp_path,
        "demo",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'confidence=1, meta={"disambiguation_fallback": True})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1


def test_boolean_confidence_does_not_resolve_to_float(tmp_path: Path):
    """``confidence=True`` would coerce to 1.0 at runtime but is
    semantically a type error — the walker rejects bool-typed
    Constants to keep the static gate strict."""
    _linker(
        tmp_path,
        "demo",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'confidence=True, meta={"disambiguation_fallback": True})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1
    assert "not statically resolvable" in violations[0].reason


def test_string_confidence_does_not_resolve_to_float(tmp_path: Path):
    """A non-numeric constant is not resolvable to a float."""
    _linker(
        tmp_path,
        "demo",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'confidence="high", meta={"disambiguation_fallback": True})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1
    assert "not statically resolvable" in violations[0].reason


def test_multiple_violations_in_same_file_are_all_reported(tmp_path: Path):
    """Property test surface: a file with two violations yields two
    diagnostics (callers consume the full list)."""
    _linker(
        tmp_path,
        "demo",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'confidence=0.9, meta={"disambiguation_fallback": True})\n'
        'Edge.create(src="c", dst="d", edge_type="references", line=2, '
        'confidence=0.7, meta={"disambiguation_fallback": True})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 2


def test_function_local_assignment_with_multiple_writes_is_unresolvable(tmp_path: Path):
    """When the confidence Name has more than one binding in scope,
    the walker can't pick one and falls back to unresolvable. This
    keeps the gate strict in the presence of code that rebinds the
    name mid-function."""
    _linker(
        tmp_path,
        "demo",
        'def make(is_fallback):\n'
        '    confidence = 0.5\n'
        '    if is_fallback:\n'
        '        confidence = 0.3\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, confidence=confidence, '
        'meta={"disambiguation_fallback": True})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1
    assert "not statically resolvable" in violations[0].reason


def test_meta_dict_value_non_bool_constant_is_not_a_flag_set(tmp_path: Path):
    """``"disambiguation_fallback": "yes"`` (string, not True) is
    not a recognized flag set — the contract requires the literal
    True constant."""
    _linker(
        tmp_path,
        "demo",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'confidence=0.9, meta={"disambiguation_fallback": "yes"})\n',
    )
    assert find_fallback_coherence_violations(tmp_path) == ()


def test_attribute_call_bare_attr_matches_constructor_name(tmp_path: Path):
    """``module.Edge(...)`` — the attribute call's final ``attr`` is
    ``Edge`` which is in the constructor set's bare-attribute path.
    Exercises the ``func.attr in CONSTRUCTOR_NAMES`` branch (line 136)
    which triggers regardless of the receiver. Distinct from
    :func:`test_bare_edge_constructor_is_also_checked`, which exercises
    the ``ast.Name`` branch — this one needs the receiver to be
    something complex enough that we go through the Attribute branch
    instead."""
    _linker(
        tmp_path,
        "demo",
        'imported_module.Edge(id="x", src="a", dst="b", '
        'edge_type="calls", line=1, confidence=0.9, '
        'meta={"disambiguation_fallback": True})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1


def test_ifexp_with_non_float_branch_is_unresolvable(tmp_path: Path):
    """Inline ternary where one branch is non-numeric (e.g. a string)
    propagates unresolvable. Exercises the False return inside
    :func:`_expr_to_floats`'s IfExp arm."""
    _linker(
        tmp_path,
        "demo",
        'Edge.create(src="a", dst="b", edge_type="calls", line=1, '
        'confidence=0.5 if True else "high", '
        'meta={"disambiguation_fallback": True})\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1
    assert "not statically resolvable" in violations[0].reason


def test_inline_conditional_confidence_with_matching_predicate(tmp_path: Path):
    """Inline confidence ternary that matches the meta's predicate
    structurally — only the True branch is checked. Exercises the
    inline-IfExp branch of ``_confidence_values_for_meta_shape``
    (without resolving through a Name)."""
    _linker(
        tmp_path,
        "demo",
        'def make(is_fallback):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, confidence=0.5 if is_fallback else 0.95, '
        'meta={"disambiguation_fallback": True} if is_fallback else None)\n',
    )
    assert find_fallback_coherence_violations(tmp_path) == ()


def test_inline_conditional_confidence_mismatched_predicate_checks_all(tmp_path: Path):
    """When the inline confidence ternary's predicate doesn't match
    the meta's predicate, the walker falls back to checking all
    candidates (conservative — can't prove they correlate)."""
    _linker(
        tmp_path,
        "demo",
        'def make(is_fallback, other_flag):\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, confidence=0.5 if other_flag else 0.95, '
        'meta={"disambiguation_fallback": True} if is_fallback else None)\n',
    )
    violations = find_fallback_coherence_violations(tmp_path)
    assert len(violations) == 1
    assert "exceed ceiling" in violations[0].reason


def test_name_resolved_to_non_ifexp_under_conditional_meta(tmp_path: Path):
    """When meta is conditional but the confidence Name resolves to a
    non-IfExp literal (e.g. a single Constant), the walker checks the
    literal directly. Exercises the fall-through path in
    ``_confidence_values_for_meta_shape`` where the resolved RHS isn't
    an IfExp."""
    _linker(
        tmp_path,
        "demo",
        'def make(is_fallback):\n'
        '    confidence = 0.5\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, confidence=confidence, '
        'meta={"disambiguation_fallback": True} if is_fallback else None)\n',
    )
    assert find_fallback_coherence_violations(tmp_path) == ()


def test_nested_function_scope_isolated(tmp_path: Path):
    """A nested function's confidence binding doesn't leak into the
    outer function's resolution — Python lexical scoping discipline."""
    _linker(
        tmp_path,
        "demo",
        'def outer():\n'
        '    def inner():\n'
        '        confidence = 0.9\n'
        '        return confidence\n'
        '    confidence = 0.3\n'
        '    return Edge.create(src="a", dst="b", edge_type="calls", '
        'line=1, confidence=confidence, '
        'meta={"disambiguation_fallback": True})\n',
    )
    # outer's confidence resolves to 0.3 from its own assignment.
    assert find_fallback_coherence_violations(tmp_path) == ()
