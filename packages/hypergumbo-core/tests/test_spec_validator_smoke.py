# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phase-0 smoke test for the spec_validator stub (ADR-0033, INV-sugat).

Phase 0 lands the validator-stage scaffolding with no check classes enabled.
This test verifies that:

* ``validate_ir`` returns an empty violations list for arbitrary inputs.
* ``build_validation_report`` produces the expected shape (schema_version,
  empty violations, all-zero violations_by_class counters).
* ``emit_stderr_summary`` is silent on empty input and prints one line
  per non-empty class otherwise.

Phase-3 PRs (axis-conformance, writer-contract, cross-field coherence,
verdict-enum completeness) will each grow this file with class-specific
fixtures. The smoke-level assertions here remain as the floor that any
future change has to preserve.
"""
from __future__ import annotations

from hypergumbo_core.spec_validator import (
    VALIDATION_REPORT_SCHEMA_VERSION,
    ValidationViolation,
    build_validation_report,
    emit_stderr_summary,
    validate_ir,
)


def test_validate_ir_phase0_returns_empty() -> None:
    """Phase-0 stub: no checks enabled; must return ``[]`` for any input.

    The stub accepts ``Iterable[Any]`` deliberately — the test passes
    Python lists with placeholder objects to confirm the validator does
    not depend on the IR module's dataclass shapes.
    """
    violations = validate_ir([], [], [])
    assert violations == []

    # Even with non-empty inputs, the stub returns []. When Phase-3 PR1
    # lands, this assertion will need a fixture whose values are
    # catalog-conformant; for now, anything goes.
    violations = validate_ir([object()], [object()], [object()])
    assert violations == []


def test_build_validation_report_empty_is_clean() -> None:
    """Empty violations produces all-zero counters + empty violations list.

    Codifies the Phase-0 contract: a clean run writes a discoverable but
    empty ``validation_report`` into the artifact. Downstream consumers
    (and CI gates) can rely on the section being present even when there's
    nothing to report.
    """
    report = build_validation_report([])
    assert report["schema_version"] == VALIDATION_REPORT_SCHEMA_VERSION
    assert report["violations"] == []
    assert report["violations_by_class"] == {
        "axis_conformance": 0,
        "writer_contract": 0,
        "cross_field": 0,
        "verdict_enum": 0,
    }


def test_build_validation_report_counts_by_class() -> None:
    """Non-empty violations get tallied per ``validator_class``.

    Verifies the counter logic. Future Phase-3 work that emits real
    violations relies on this counter shape for the stderr summary and
    for CI gate readability.
    """
    violations = [
        ValidationViolation(
            severity="error",
            validator_class="axis_conformance",
            message="example axis violation",
        ),
        ValidationViolation(
            severity="error",
            validator_class="axis_conformance",
            message="second axis violation",
        ),
        ValidationViolation(
            severity="warning",
            validator_class="writer_contract",
            message="example writer-contract violation",
        ),
    ]
    report = build_validation_report(violations)
    assert report["violations_by_class"] == {
        "axis_conformance": 2,
        "writer_contract": 1,
        "cross_field": 0,
        "verdict_enum": 0,
    }
    assert len(report["violations"]) == 3
    # Round-trip through asdict — every violation surfaces as a dict
    # with the same keys (severity, validator_class, message, axis,
    # field_name, record_id, observed, expected).
    expected_keys = {
        "severity",
        "validator_class",
        "message",
        "axis",
        "field_name",
        "record_id",
        "observed",
        "expected",
    }
    for emitted in report["violations"]:
        assert set(emitted.keys()) == expected_keys


def test_emit_stderr_summary_silent_on_empty(capsys) -> None:
    """No violations → no stderr chatter. The clean-corpus path is quiet."""
    emit_stderr_summary([])
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_emit_stderr_summary_one_line_per_class(capsys) -> None:
    """Non-empty violations emit one ``[warn]`` line per non-zero class."""
    violations = [
        ValidationViolation(
            severity="error",
            validator_class="axis_conformance",
            message="m1",
        ),
        ValidationViolation(
            severity="error",
            validator_class="axis_conformance",
            message="m2",
        ),
        ValidationViolation(
            severity="warning",
            validator_class="cross_field",
            message="m3",
        ),
    ]
    emit_stderr_summary(violations)
    captured = capsys.readouterr()
    lines = [line for line in captured.err.splitlines() if line.strip()]
    assert len(lines) == 2
    assert any("2 axis_conformance" in line for line in lines)
    assert any("1 cross_field" in line for line in lines)
    for line in lines:
        assert line.startswith("[warn]")
        assert "validation_report" in line


def test_validation_violation_is_frozen() -> None:
    """ValidationViolation is observation data; downstream must not mutate it.

    If a downstream caller tries to edit a violation, the right answer is
    to fix the validator emitter or filter the violation out before passing
    it on — never to silently rewrite the observation.
    """
    import dataclasses

    v = ValidationViolation(
        severity="error",
        validator_class="axis_conformance",
        message="x",
    )
    try:
        v.severity = "warning"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("ValidationViolation should be frozen")


def test_emit_stderr_summary_uses_stderr_not_stdout(capsys) -> None:
    """Sanity: the summary lands on stderr, not stdout.

    Stdout is the data channel for many CLI subcommands; stderr is the
    chatter channel. The validator must not pollute stdout because
    consumers parsing ``hypergumbo run`` output as JSON would break.
    """
    violations = [
        ValidationViolation(
            severity="error",
            validator_class="axis_conformance",
            message="m1",
        ),
    ]
    emit_stderr_summary(violations)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err  # non-empty
