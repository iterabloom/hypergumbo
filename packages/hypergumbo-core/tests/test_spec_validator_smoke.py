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


def test_validate_ir_empty_inputs_returns_empty() -> None:
    """No records → no violations. Floor invariant across all phases."""
    violations = validate_ir([], [], [])
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


# ----------------------------------------------------------------------
# Phase 3 PR1 — Axis-conformance validator class tests
# ----------------------------------------------------------------------


class _FakeSym:
    """Minimal stand-in for Symbol for axis-conformance unit testing.

    The validator reads attributes by ``getattr(record, name, None)``, so
    a dict-with-attribute-access via SimpleNamespace would also work — but
    a class with the named attributes makes the intent of each test
    fixture more readable.
    """

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_axis_conformance_passes_on_catalog_conformant_symbol() -> None:
    """A Symbol with every axis-tagged field in its catalog produces no
    violations.

    Uses ``catalog.all_known_languages`` / ``symbol_kinds.all_symbol_kind_names``
    to pick known-valid values rather than hardcoding; this couples the
    test to the production catalogs intentionally so the test surfaces
    catalog changes as a deliberate breakage.
    """
    from hypergumbo_core.catalog import all_known_languages
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(iter(all_symbol_kind_names()))
    a_lang = next(iter(all_known_languages()))

    sym = _FakeSym(
        id="sym:1",
        kind=a_kind,
        language=a_lang,
        discovery_language=None,
        protocol_origin=None,
        origin=[],
        qualified_name=None,
    )
    violations = validate_ir([sym], [], [])
    assert violations == []


def test_axis_conformance_flags_invalid_symbol_kind() -> None:
    """Symbol.kind not in the symbol-kind catalog emits an axis_conformance
    violation."""
    from hypergumbo_core.catalog import all_known_languages

    a_lang = next(iter(all_known_languages()))
    sym = _FakeSym(
        id="sym:bad-kind",
        kind="totally-not-a-real-kind",
        language=a_lang,
        discovery_language=None,
        protocol_origin=None,
        origin=[],
        qualified_name=None,
    )
    violations = validate_ir([sym], [], [])
    matched = [
        v for v in violations
        if v.field_name == "Symbol.kind" and v.observed == "totally-not-a-real-kind"
    ]
    assert len(matched) == 1
    assert matched[0].severity == "error"
    assert matched[0].axis == "symbol-kind"


def test_axis_conformance_optional_language_accepts_none() -> None:
    """Class B synthetic stand-ins have ``language=None`` per ADR-0031;
    the validator must accept that for Optional axis-tagged fields."""
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(iter(all_symbol_kind_names()))
    sym = _FakeSym(
        id="sym:class-b",
        kind=a_kind,
        language=None,  # Class B
        discovery_language="python",  # discovery context
        protocol_origin="websocket",  # protocol identity
        origin=[],
        qualified_name=None,
    )
    violations = validate_ir([sym], [], [])
    # No language violation (None is legal for Optional language).
    assert not any(v.field_name == "Symbol.language" for v in violations)


def test_axis_conformance_flags_invalid_protocol_origin() -> None:
    """Symbol.protocol_origin not in the protocol-origin catalog emits
    a violation."""
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(iter(all_symbol_kind_names()))
    sym = _FakeSym(
        id="sym:bad-protocol",
        kind=a_kind,
        language=None,
        discovery_language="python",
        protocol_origin="not-a-known-protocol-family",
        origin=[],
        qualified_name=None,
    )
    violations = validate_ir([sym], [], [])
    matched = [
        v for v in violations
        if v.field_name == "Symbol.protocol_origin"
    ]
    assert len(matched) == 1
    assert matched[0].axis == "protocol-origin"


def test_axis_conformance_flags_invalid_origin_list_element() -> None:
    """Each element of Symbol.origin is checked for pass-id membership.
    A single bad element produces a single violation."""
    from hypergumbo_core.catalog import all_known_languages, all_known_pass_ids
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(iter(all_symbol_kind_names()))
    a_lang = next(iter(all_known_languages()))
    a_pass = next(iter(all_known_pass_ids()))

    sym = _FakeSym(
        id="sym:mixed-origin",
        kind=a_kind,
        language=a_lang,
        discovery_language=None,
        protocol_origin=None,
        origin=[a_pass, "not-a-real-pass-id"],  # one good, one bad
        qualified_name=None,
    )
    violations = validate_ir([sym], [], [])
    matched = [
        v for v in violations
        if v.field_name == "Symbol.origin"
        and v.observed == "not-a-real-pass-id"
    ]
    assert len(matched) == 1
    assert matched[0].axis == "pass-id"


def test_axis_conformance_qualified_name_separator_mismatch() -> None:
    """Symbol.qualified_name using the wrong separator for the
    Symbol.language is flagged as a warning."""
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(iter(all_symbol_kind_names()))
    sym = _FakeSym(
        id="sym:wrong-sep",
        kind=a_kind,
        language="python",  # Python uses "."
        discovery_language=None,
        protocol_origin=None,
        origin=[],
        qualified_name="module::Class::method",  # Rust-style separator
    )
    violations = validate_ir([sym], [], [])
    matched = [
        v for v in violations
        if v.field_name == "Symbol.qualified_name"
    ]
    assert len(matched) == 1
    assert matched[0].severity == "warning"
    assert matched[0].axis == "qualified-name"


def test_axis_conformance_qualified_name_unqualified_is_legal() -> None:
    """A single-segment qualified_name (no separator) is legal — the
    name is unqualified, which is a permitted state."""
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(iter(all_symbol_kind_names()))
    sym = _FakeSym(
        id="sym:unqual",
        kind=a_kind,
        language="python",
        discovery_language=None,
        protocol_origin=None,
        origin=[],
        qualified_name="just_a_name",
    )
    violations = validate_ir([sym], [], [])
    assert not any(v.field_name == "Symbol.qualified_name" for v in violations)


def test_axis_conformance_flags_invalid_edge_type() -> None:
    """Edge.edge_type not in the edge-type catalog emits a violation."""
    from hypergumbo_core.catalog import all_known_pass_ids
    from hypergumbo_core.evidence_types import all_evidence_type_names

    a_pass = next(iter(all_known_pass_ids()))
    an_evidence = next(iter(all_evidence_type_names()))

    edge = _FakeSym(
        id="edge:bad",
        edge_type="not-a-real-edge-type",
        evidence_type=an_evidence,
        evidence_lang=None,
        origin=[a_pass],
    )
    violations = validate_ir([], [edge], [])
    matched = [v for v in violations if v.field_name == "Edge.edge_type"]
    assert len(matched) == 1
    assert matched[0].axis == "edge-type"


def test_axis_conformance_run_pass_id_required() -> None:
    """AnalysisRun.pass_id not in the pass-id catalog emits a violation
    (required field, allow_none=False)."""
    run = _FakeSym(execution_id="run:bad-pass", pass_id="not-a-pass")
    violations = validate_ir([], [], [run])
    matched = [v for v in violations if v.field_name == "AnalysisRun.pass_id"]
    assert len(matched) == 1
    assert matched[0].axis == "pass-id"


def test_axis_conformance_none_for_required_field_emits_violation() -> None:
    """A required (allow_none=False) axis-tagged field being None emits
    a violation. Symbol.kind is the canonical example — None is illegal
    because Symbol's spec declares kind as a required str."""
    sym = _FakeSym(
        id="sym:none-kind",
        kind=None,  # illegal — required field
        language=None,
        discovery_language=None,
        protocol_origin=None,
        origin=[],
        qualified_name=None,
    )
    violations = validate_ir([sym], [], [])
    matched = [
        v for v in violations
        if v.field_name == "Symbol.kind" and v.observed is None
    ]
    assert len(matched) == 1
    assert matched[0].severity == "error"
    assert "non-None" in matched[0].expected


def test_axis_conformance_origin_none_is_skipped() -> None:
    """Symbol.origin=None (rather than empty list) skips per-element
    checks gracefully — the field is documented as a list but consumers
    may construct partial records during testing or migration."""
    from hypergumbo_core.catalog import all_known_languages
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(iter(all_symbol_kind_names()))
    a_lang = next(iter(all_known_languages()))

    sym = _FakeSym(
        id="sym:no-origin",
        kind=a_kind,
        language=a_lang,
        discovery_language=None,
        protocol_origin=None,
        origin=None,  # None rather than []
        qualified_name=None,
    )
    violations = validate_ir([sym], [], [])
    assert not any(v.field_name == "Symbol.origin" for v in violations)


def test_axis_conformance_edge_origin_bad_element_flagged() -> None:
    """Edge.origin (list of pass-ids) flags bad elements just like
    Symbol.origin does."""
    from hypergumbo_core.evidence_types import all_evidence_type_names

    an_evidence = next(iter(all_evidence_type_names()))
    edge = _FakeSym(
        id="edge:bad-origin",
        edge_type="calls",
        evidence_type=an_evidence,
        evidence_lang=None,
        origin=["not-a-real-pass-id"],
    )
    violations = validate_ir([], [edge], [])
    matched = [
        v for v in violations
        if v.field_name == "Edge.origin"
        and v.observed == "not-a-real-pass-id"
    ]
    assert len(matched) == 1


def test_axis_conformance_qualified_name_unknown_language_is_skipped() -> None:
    """When the Symbol's language has no declared qualified-name separator
    policy, the structural check is skipped (no violation, no false
    positive)."""
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(iter(all_symbol_kind_names()))
    sym = _FakeSym(
        id="sym:unknown-lang-sep",
        kind=a_kind,
        # A language with no entry in QUALIFIED_NAME_SEPARATORS.
        language="brainfuck",
        discovery_language=None,
        protocol_origin=None,
        origin=[],
        qualified_name="weird::format.thing",
    )
    violations = validate_ir([sym], [], [])
    assert not any(v.field_name == "Symbol.qualified_name" for v in violations)
