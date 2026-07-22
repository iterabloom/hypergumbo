# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the kind-conditioned writer-contract population contract.

The record-class-level writer-contract sub-patterns (default-only initializer,
schema-declares-no-writer) partition only by ``(record_class, field)``. They
miss a *kind-conditioned* NULL: a field populated on ``function`` symbols but
100% NULL on every ``method`` symbol never trips a Symbol-level check, because
at least one Symbol (the function) carries it -- this is the 194-of-312 NULL
(kind,field) cell class WI-libib names. Rather than enforce the full matrix
(rejected by the item's own author as over-broad), the mechanism is a
*registered* positive population contract: each entry asserts a specific
(language, kind, field) cell stays populated, and a 100%-NULL partition on that
cell is a writer regression. The first entries are ``qualified_name`` on Python
function/method/class (WI-hudug; the producer half is done -- WI-fagab /
ADR-0032 -- so these currently emit no violation and act as a regression guard).

These tests lock both the mechanism (partition by language+kind, skip an absent
partition, fire only on a 100%-NULL registered cell) and WI-hudug's concrete
registered entries.
"""
from __future__ import annotations

from hypergumbo_core.spec_validator import (
    _WRITER_CONTRACT_KIND_MUST_POPULATE,
    _check_kind_conditioned_population,
    validate_ir,
)


class _Rec:
    """Attribute-bearing stand-in; the validator reads via getattr/_read."""

    def __init__(self, **kw: object) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def _run() -> _Rec:
    """A minimal AnalysisRun -- the writer_contract validator (like its
    sub-pattern-1 sibling) is gated on >=1 run being present."""
    return _Rec(execution_id="run:1", pass_id="py_v1", config_fingerprint="sha256:x")


def _pysym(kind: str, qualified_name: object, sym_id: str) -> _Rec:
    return _Rec(id=sym_id, kind=kind, language="python", qualified_name=qualified_name)


def test_registration_covers_python_callable_and_class_qualified_name() -> None:
    """WI-hudug's concrete entry is present: qualified_name on Python
    function/method/class."""
    registered = {
        (lang, kind, field)
        for (lang, kind, field, _msg) in _WRITER_CONTRACT_KIND_MUST_POPULATE
    }
    for kind in ("function", "method", "class"):
        assert ("python", kind, "qualified_name") in registered


def test_flags_100pct_null_qualified_name_on_python_functions() -> None:
    """A registered (python, function, qualified_name) cell that is 100% NULL
    across its non-empty kind partition emits ONE writer_contract warning
    pointing at the first offending symbol."""
    syms = [_pysym("function", None, f"s:{i}") for i in range(3)]
    violations = _check_kind_conditioned_population(syms)
    matched = [
        v for v in violations
        if v.validator_class == "writer_contract"
        and v.field_name == "Symbol[python/function].qualified_name"
    ]
    assert len(matched) == 1
    assert matched[0].severity == "warning"
    assert matched[0].record_id == "s:0"
    assert "unpopulated" in matched[0].message


def test_silent_when_qualified_name_populated() -> None:
    """When the field is populated across the kind partition, no violation --
    the writer IS wiring the slot (the steady state post-WI-fagab)."""
    syms = [_pysym("function", "pkg.fn", f"s:{i}") for i in range(3)]
    violations = _check_kind_conditioned_population(syms)
    assert not any(
        v.field_name == "Symbol[python/function].qualified_name"
        for v in violations
    )


def test_silent_when_at_least_one_populated() -> None:
    """A single populated symbol satisfies the population contract: the writer
    IS reaching the slot, so a residual per-record gap is a different (finer)
    concern, not a 100%-NULL writer regression."""
    syms = [
        _pysym("function", None, "s:0"),
        _pysym("function", "pkg.fn", "s:1"),
    ]
    violations = _check_kind_conditioned_population(syms)
    assert not any(
        v.field_name == "Symbol[python/function].qualified_name"
        for v in violations
    )


def test_absent_kind_partition_is_not_a_violation() -> None:
    """A registered kind with zero symbols on this substrate is skipped:
    absence of the kind is not a population regression."""
    syms = [_pysym("variable", None, "s:0")]  # no function/method/class
    violations = _check_kind_conditioned_population(syms)
    assert violations == []


def test_partition_is_language_and_kind_scoped() -> None:
    """A non-Python function with NULL qualified_name does NOT trip the
    python/function entry -- the partition keys on language as well as kind."""
    syms = [_Rec(id="s:0", kind="function", language="go", qualified_name=None)]
    violations = _check_kind_conditioned_population(syms)
    assert not any(
        v.field_name == "Symbol[python/function].qualified_name"
        for v in violations
    )


def test_end_to_end_through_validate_ir_with_a_run() -> None:
    """The kind-conditioned check runs inside the writer_contract validator and
    surfaces through the public validate_ir entrypoint when >=1 run is present
    (the same run-gating sub-pattern-1 uses)."""
    syms = [_pysym("method", None, f"s:{i}") for i in range(2)]
    violations = validate_ir(syms, [], [_run()])
    assert any(
        v.validator_class == "writer_contract"
        and v.field_name == "Symbol[python/method].qualified_name"
        for v in violations
    )


def test_no_run_skips_the_check() -> None:
    """With zero runs the writer_contract validator returns early (matching the
    sub-pattern-1 precedent), so no kind-conditioned violation is emitted even
    on a 100%-NULL registered cell."""
    syms = [_pysym("method", None, "s:0")]
    violations = validate_ir(syms, [], [])
    assert not any(v.validator_class == "writer_contract" for v in violations)
