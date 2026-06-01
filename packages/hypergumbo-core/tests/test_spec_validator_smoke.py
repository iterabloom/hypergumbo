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
        "id_format": 0,
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
        "id_format": 0,
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
        id="python:test/fake.py:1-1:sym:function",
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
        id="python:test/fake.py:1-1:bad-kind:function",
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
        id="python:test/fake.py:1-1:class-b:function",
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
        id="python:test/fake.py:1-1:bad-protocol:function",
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
        id="python:test/fake.py:1-1:mixed-origin:function",
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
        id="python:test/fake.py:1-1:wrong-sep:function",
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
        id="python:test/fake.py:1-1:unqual:function",
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
        id="python:test/fake.py:1-1:none-kind:function",
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
        id="python:test/fake.py:1-1:no-origin:function",
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


# ----------------------------------------------------------------------
# Phase 3 PR2 — Writer-contract validator class tests
# ----------------------------------------------------------------------


def test_writer_contract_flags_all_default_config_fingerprint() -> None:
    """All AnalysisRun records sharing the default config_fingerprint
    (`sha256:44136fa355b3678a` = sha256 of '{}') trigger a writer-
    contract violation per INV-luhur sub-pattern 2 ("Default-only
    initializer never overridden")."""
    from hypergumbo_core.ir import _default_config_fingerprint

    default_fp = _default_config_fingerprint()
    runs = [
        _FakeSym(execution_id=f"run:{i}", pass_id="py_v1", config_fingerprint=default_fp)
        for i in range(3)
    ]
    violations = validate_ir([], [], runs)
    matched = [
        v for v in violations
        if v.validator_class == "writer_contract"
        and v.field_name == "AnalysisRun.config_fingerprint"
    ]
    assert len(matched) == 1
    assert matched[0].severity == "warning"
    assert "Default-only" in matched[0].message


def test_writer_contract_silent_when_at_least_one_run_overrides() -> None:
    """If any AnalysisRun overrides the default, the writer-contract
    check passes (the writer IS populating the field; the gap is
    elsewhere)."""
    from hypergumbo_core.ir import _default_config_fingerprint

    default_fp = _default_config_fingerprint()
    runs = [
        _FakeSym(execution_id="run:1", pass_id="py_v1", config_fingerprint=default_fp),
        _FakeSym(execution_id="run:2", pass_id="py_v1", config_fingerprint="sha256:differentvalue"),
    ]
    violations = validate_ir([], [], runs)
    assert not any(
        v.validator_class == "writer_contract"
        and v.field_name == "AnalysisRun.config_fingerprint"
        for v in violations
    )


def test_writer_contract_silent_for_single_run() -> None:
    """A single run carrying the default isn't enough evidence — N=1
    can't distinguish "writer never overrode" from "this one analyzer
    legitimately has no config." Requires N>=2 of the same default."""
    from hypergumbo_core.ir import _default_config_fingerprint

    default_fp = _default_config_fingerprint()
    runs = [
        _FakeSym(execution_id="run:1", pass_id="py_v1", config_fingerprint=default_fp),
    ]
    violations = validate_ir([], [], runs)
    assert not any(
        v.validator_class == "writer_contract"
        and v.field_name == "AnalysisRun.config_fingerprint"
        for v in violations
    )


def test_writer_contract_silent_with_no_runs() -> None:
    """Zero records skip the check (no signal possible)."""
    violations = validate_ir([], [], [])
    assert not any(v.validator_class == "writer_contract" for v in violations)


# ----------------------------------------------------------------------
# Phase 3 PR3 — Cross-field coherence validator class tests
# ----------------------------------------------------------------------


def test_cross_field_class_b_coherent_passes() -> None:
    """Class B synthetic stand-in (language=None, protocol_origin
    populated) passes the cross-field check."""
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(k for k in all_symbol_kind_names() if k != "file")
    sym = _FakeSym(
        id="python:test/fake.py:1-1:class-b:function",
        kind=a_kind,
        language=None,
        discovery_language="python",
        protocol_origin="websocket",
        display_label=None,
        origin=[],
        qualified_name=None,
        dst_ref=None,
        dst=None,
    )
    violations = validate_ir([sym], [], [])
    assert not any(v.validator_class == "cross_field" for v in violations)


def test_cross_field_class_a_coherent_passes() -> None:
    """Class A real-source declaration (language populated,
    protocol_origin None) passes."""
    from hypergumbo_core.catalog import all_known_languages
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(k for k in all_symbol_kind_names() if k != "file")
    a_lang = next(iter(all_known_languages()))
    sym = _FakeSym(
        id="python:test/fake.py:1-1:class-a:function",
        kind=a_kind,
        language=a_lang,
        discovery_language=None,
        protocol_origin=None,
        display_label=None,
        origin=[],
        qualified_name=None,
        dst_ref=None,
        dst=None,
    )
    violations = validate_ir([sym], [], [])
    assert not any(v.validator_class == "cross_field" for v in violations)


def test_cross_field_flags_class_a_with_protocol_origin() -> None:
    """A Symbol with both language AND protocol_origin populated is
    incoherent per ADR-0031 — emits a warning."""
    from hypergumbo_core.catalog import all_known_languages
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(k for k in all_symbol_kind_names() if k != "file")
    a_lang = next(iter(all_known_languages()))
    sym = _FakeSym(
        id="python:test/fake.py:1-1:incoherent-both:function",
        kind=a_kind,
        language=a_lang,
        discovery_language=None,
        protocol_origin="websocket",
        display_label=None,
        origin=[],
        qualified_name=None,
        dst_ref=None,
        dst=None,
    )
    violations = validate_ir([sym], [], [])
    matched = [
        v for v in violations
        if v.validator_class == "cross_field"
        and v.field_name == "Symbol.language / Symbol.protocol_origin"
    ]
    assert len(matched) == 1
    assert matched[0].severity == "warning"


def test_cross_field_file_kind_exempt_from_class_b_check() -> None:
    """File Symbols (kind='file') keep both language and no protocol_origin
    per ADR-0031 Class A — exempt from the Class-B coherence check."""
    from hypergumbo_core.catalog import all_known_languages

    a_lang = next(iter(all_known_languages()))
    sym = _FakeSym(
        id="file:main.py",
        kind="file",
        language=a_lang,
        discovery_language=None,
        protocol_origin=None,
        display_label=None,
        origin=[],
        qualified_name=None,
        dst_ref=None,
        dst=None,
    )
    violations = validate_ir([sym], [], [])
    assert not any(v.validator_class == "cross_field" for v in violations)


def test_cross_field_flags_class_a_with_display_label() -> None:
    """display_label on a Class-A real-source declaration is a smell
    per ADR-0032 — emits a warning."""
    from hypergumbo_core.catalog import all_known_languages
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(k for k in all_symbol_kind_names() if k != "file")
    a_lang = next(iter(all_known_languages()))
    sym = _FakeSym(
        id="python:test/fake.py:1-1:label-on-class-a:function",
        kind=a_kind,
        language=a_lang,
        discovery_language=None,
        protocol_origin=None,
        display_label="some_display_label",
        origin=[],
        qualified_name=None,
        dst_ref=None,
        dst=None,
    )
    violations = validate_ir([sym], [], [])
    matched = [
        v for v in violations
        if v.validator_class == "cross_field"
        and v.field_name == "Symbol.display_label"
    ]
    assert len(matched) == 1
    assert matched[0].severity == "warning"


def test_cross_field_flags_dst_ref_without_dst() -> None:
    """An Edge with dst_ref populated but dst empty violates the
    back-compat contract per make_unresolved_edge docstring."""
    edge = _FakeSym(
        id="edge:dst-mismatch",
        edge_type="calls",
        evidence_type="ast_call",
        evidence_lang=None,
        origin=[],
        dst="",  # empty — should also be populated
        dst_ref=object(),  # truthy ExternalRef stand-in
    )
    violations = validate_ir([], [edge], [])
    matched = [
        v for v in violations
        if v.validator_class == "cross_field"
        and v.field_name == "Edge.dst / Edge.dst_ref"
    ]
    assert len(matched) == 1
    assert matched[0].severity == "error"


def test_cross_field_dst_ref_with_dst_passes() -> None:
    """When both dst_ref and dst are populated, the back-compat
    invariant is satisfied — no violation."""
    edge = _FakeSym(
        id="edge:coherent",
        edge_type="calls",
        evidence_type="ast_call",
        evidence_lang=None,
        origin=[],
        dst="external:python:os.path:join",
        dst_ref=object(),  # truthy ExternalRef stand-in
    )
    violations = validate_ir([], [edge], [])
    assert not any(v.field_name == "Edge.dst / Edge.dst_ref" for v in violations)


def test_cross_field_dst_ref_none_passes() -> None:
    """When dst_ref is None (in-repo dst), the back-compat check is
    skipped."""
    edge = _FakeSym(
        id="edge:in-repo",
        edge_type="calls",
        evidence_type="ast_call",
        evidence_lang=None,
        origin=[],
        dst="real-symbol-id",
        dst_ref=None,
    )
    violations = validate_ir([], [edge], [])
    assert not any(v.field_name == "Edge.dst / Edge.dst_ref" for v in violations)


# ----------------------------------------------------------------------
# Phase 3 PR4 — Verdict-enum completeness validator tests
# ----------------------------------------------------------------------


def test_verdict_enum_validator_passes_with_inconclusive_documented() -> None:
    """The validator passes when every registered verdict dataclass's
    docstring mentions the required ``inconclusive`` value. With
    ClaimVerdict's docstring updated in Phase 3 PR4, this should be the
    happy path on the live tree."""
    violations = validate_ir([], [], [])
    assert not any(v.validator_class == "verdict_enum" for v in violations)


def test_verdict_enum_validator_flags_missing_inconclusive(
    monkeypatch,
) -> None:
    """A verdict-emitting dataclass whose docstring does NOT mention
    ``inconclusive`` triggers a verdict_enum violation. Simulated via
    monkeypatch on `_VERDICT_DATACLASSES` to a test dataclass that
    lacks the required value."""
    import hypergumbo_core.spec_validator as sv

    fake_table = (
        # Point at a stdlib class whose docstring doesn't mention
        # "inconclusive" — `pathlib.PurePath` is a stable choice.
        ("pathlib", "PurePath", "verdict", frozenset({"inconclusive"})),
    )
    monkeypatch.setattr(sv, "_VERDICT_DATACLASSES", fake_table)
    violations = validate_ir([], [], [])
    matched = [
        v for v in violations
        if v.validator_class == "verdict_enum"
    ]
    assert len(matched) == 1
    assert matched[0].severity == "error"
    assert "inconclusive" in matched[0].message


def test_axis_conformance_qualified_name_unknown_language_is_skipped() -> None:
    """When the Symbol's language has no declared qualified-name separator
    policy, the structural check is skipped (no violation, no false
    positive)."""
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(iter(all_symbol_kind_names()))
    sym = _FakeSym(
        id="python:test/fake.py:1-1:unknown-lang-sep:function",
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


# ----------------------------------------------------------------------
# Phase 5 PR1 — ID-format validator tests (ADR-0034)
# ----------------------------------------------------------------------


def test_id_format_validator_passes_on_canonical_make_symbol_id_output() -> None:
    """A Symbol.id built via ``make_symbol_id`` produces no id_format violation."""
    from hypergumbo_core.analyze.base import make_symbol_id
    from hypergumbo_core.spec_validator import _check_id_format

    canonical = make_symbol_id("python", "pkg/foo.py", 10, 12, "do_thing", "function")
    sym = _FakeSym(id=canonical)
    violations = _check_id_format([sym])
    assert violations == []


def test_id_format_validator_flags_inv_sadiv_double_colon_pattern() -> None:
    """The historical INV-sadiv path-prefix ``::``-separated shape is flagged."""
    from hypergumbo_core.spec_validator import _check_id_format

    sym = _FakeSym(id="packages/foo/bar.py::http_client::42")
    violations = _check_id_format([sym])
    assert len(violations) == 1
    v = violations[0]
    assert v.validator_class == "id_format"
    assert v.field_name == "Symbol.id"
    assert "double_colon_separator" in v.message
    assert "INV-sadiv" in v.message
    assert v.expected.startswith("<language>:")


def test_id_format_validator_flags_wrong_field_count() -> None:
    """An id with the wrong number of colon-separated fields is flagged."""
    from hypergumbo_core.spec_validator import _check_id_format

    sym = _FakeSym(id="python:pkg/foo.py:42-42:name")
    violations = _check_id_format([sym])
    assert len(violations) == 1
    assert "wrong_field_count" in violations[0].message


def test_id_format_validator_flags_non_canonical_language_prefix() -> None:
    """A first segment that isn't a lowercase identifier is flagged."""
    from hypergumbo_core.spec_validator import _check_id_format

    sym = _FakeSym(id="123badlang:pkg/foo.py:42-42:name:function")
    violations = _check_id_format([sym])
    assert len(violations) == 1
    assert "non_canonical_language_prefix" in violations[0].message


def test_id_format_validator_flags_malformed_span() -> None:
    """The span segment must match digit-digit shape."""
    from hypergumbo_core.spec_validator import _check_id_format

    sym = _FakeSym(id="python:pkg/foo.py:line42:name:function")
    violations = _check_id_format([sym])
    assert len(violations) == 1
    assert "malformed_span_segment" in violations[0].message


def test_id_format_validator_flags_non_canonical_kind_suffix() -> None:
    """The final kind segment must be a lowercase identifier."""
    from hypergumbo_core.spec_validator import _check_id_format

    sym = _FakeSym(id="python:pkg/foo.py:42-42:name:FUNCTION")
    violations = _check_id_format([sym])
    assert len(violations) == 1
    assert "non_canonical_kind_suffix" in violations[0].message


def test_id_format_validator_skips_symbols_with_none_id() -> None:
    """A None id is not an id_format issue (axis_conformance owns that)."""
    from hypergumbo_core.spec_validator import _check_id_format

    sym = _FakeSym(id=None)
    violations = _check_id_format([sym])
    assert violations == []


def test_id_format_violation_appears_in_validate_ir_report() -> None:
    """``validate_ir`` wires the id_format check in alongside the others."""
    sym = _FakeSym(
        id="packages/foo/bar.py::http_client::42",
        kind="call_site",
        language=None,
        discovery_language="python",
        protocol_origin="http",
        origin=[],
        qualified_name=None,
    )
    violations = validate_ir([sym], [], [])
    assert any(v.validator_class == "id_format" for v in violations)


def test_build_validation_report_counts_id_format_violations() -> None:
    """The report counter tallies id_format violations."""
    violations = [
        ValidationViolation(
            severity="error",
            validator_class="id_format",
            message="example id-format violation",
        ),
    ]
    report = build_validation_report(violations)
    assert report["violations_by_class"]["id_format"] == 1


# ----------------------------------------------------------------------
# Phase 6 PR1 — Stable-ID-format sub-check tests (INV-hunup closure)
# ----------------------------------------------------------------------


def test_stable_id_format_validator_passes_on_canonical_short_sha256() -> None:
    """A Symbol.stable_id built via ``_short_sha256`` passes the check."""
    from hypergumbo_core.analyze.base import make_file_stable_id
    from hypergumbo_core.spec_validator import _check_stable_id_format

    canonical = make_file_stable_id("python", "pkg/foo.py")
    sym = _FakeSym(id="python:pkg/foo.py:1-1:file:file", stable_id=canonical)
    violations = _check_stable_id_format([sym])
    assert violations == []


def test_stable_id_format_validator_flags_raw_64_char_hex_without_prefix() -> None:
    """A raw 64-char hexdigest (no ``sha256:`` prefix) is flagged.

    Pre-Phase-6 ``make_route_stable_id`` and the HTTP linker's call_site
    factory emitted this shape; the validator catches the regression.
    """
    from hypergumbo_core.spec_validator import _check_stable_id_format

    raw_hex = "a" * 64
    sym = _FakeSym(id="python:pkg/foo.py:1-1:bar:function", stable_id=raw_hex)
    violations = _check_stable_id_format([sym])
    assert len(violations) == 1
    v = violations[0]
    assert v.validator_class == "id_format"
    assert v.field_name == "Symbol.stable_id"
    assert "raw_hex_no_prefix" in v.message
    assert v.expected == "sha256:<16hex>"


def test_stable_id_format_validator_flags_bare_name_no_prefix() -> None:
    """A colon-free bare name (e.g. ``dispatch``) is flagged.

    Pre-Phase-6 ``event_sourcing`` and ``graphql_resolver`` linkers
    emitted this shape.
    """
    from hypergumbo_core.spec_validator import _check_stable_id_format

    sym = _FakeSym(id="python:pkg/foo.py:1-1:bar:function", stable_id="dispatch")
    violations = _check_stable_id_format([sym])
    assert len(violations) == 1
    assert "bare_name_no_prefix" in violations[0].message


def test_stable_id_format_validator_flags_sha256_wrong_length() -> None:
    """``sha256:`` prefix with the wrong-length hex suffix is flagged."""
    from hypergumbo_core.spec_validator import _check_stable_id_format

    sym = _FakeSym(
        id="python:pkg/foo.py:1-1:bar:function",
        stable_id="sha256:deadbeef",  # 8 chars, not 16
    )
    violations = _check_stable_id_format([sym])
    assert len(violations) == 1
    assert "sha256_prefix_wrong_length" in violations[0].message


def test_stable_id_format_validator_flags_sha256_non_hex_suffix() -> None:
    """``sha256:`` prefix with a non-hex suffix is flagged."""
    from hypergumbo_core.spec_validator import _check_stable_id_format

    sym = _FakeSym(
        id="python:pkg/foo.py:1-1:bar:function",
        stable_id="sha256:not-hex-chars-here",
    )
    violations = _check_stable_id_format([sym])
    assert len(violations) == 1
    assert "sha256_prefix_with_non_hex_suffix" in violations[0].message


def test_stable_id_format_validator_flags_composite_form() -> None:
    """A composite ``foo:bar``-style stable_id (no sha256: prefix) is flagged.

    Pre-Phase-6 ``database_query`` (1-colon) and ``message_queue``
    (2-colon) linkers emitted this shape.
    """
    from hypergumbo_core.spec_validator import _check_stable_id_format

    sym = _FakeSym(
        id="python:pkg/foo.py:1-1:bar:function",
        stable_id="DELETE:sessions",
    )
    violations = _check_stable_id_format([sym])
    assert len(violations) == 1
    assert "composite_no_sha_prefix" in violations[0].message
    assert "colon_count=1" in violations[0].message


def test_stable_id_format_validator_skips_none_stable_id() -> None:
    """``stable_id=None`` is allowed (some Symbols legitimately omit it)."""
    from hypergumbo_core.spec_validator import _check_stable_id_format

    sym = _FakeSym(id="python:pkg/foo.py:1-1:bar:function", stable_id=None)
    violations = _check_stable_id_format([sym])
    assert violations == []


def test_stable_id_format_violation_appears_in_validate_ir_report() -> None:
    """``validate_ir`` wires the stable_id_format check alongside the others."""
    sym = _FakeSym(
        id="python:pkg/foo.py:1-1:bar:function",
        stable_id="a" * 64,  # raw_hex_no_prefix
        kind="function",
        language="python",
        discovery_language=None,
        protocol_origin=None,
        origin=[],
        qualified_name=None,
    )
    violations = validate_ir([sym], [], [])
    stable_id_violations = [
        v for v in violations
        if v.validator_class == "id_format" and v.field_name == "Symbol.stable_id"
    ]
    assert len(stable_id_violations) == 1


def test_make_route_stable_id_emits_canonical_shape() -> None:
    """Phase 6 PR1: ``make_route_stable_id`` returns ``sha256:<16hex>``."""
    from hypergumbo_core.analyze.base import make_route_stable_id
    from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN

    out = make_route_stable_id("GET", "/users")
    assert _CANONICAL_STABLE_ID_PATTERN.match(out)


def test_make_entry_stable_id_emits_canonical_shape() -> None:
    """Phase 6 PR1: ``make_entry_stable_id`` returns ``sha256:<16hex>``."""
    from hypergumbo_core.analyze.base import make_entry_stable_id
    from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN

    out = make_entry_stable_id("vertex", "main")
    assert _CANONICAL_STABLE_ID_PATTERN.match(out)


def test_make_protocol_stable_id_emits_canonical_shape() -> None:
    """Phase 6 PR1: ``make_protocol_stable_id`` returns ``sha256:<16hex>``.

    Sanity check on the new factory the four linkers
    (database_query / message_queue / event_sourcing / graphql_resolver)
    migrated to.
    """
    from hypergumbo_core.analyze.base import make_protocol_stable_id
    from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN

    out = make_protocol_stable_id("db_query", "SELECT", "users,orders")
    assert _CANONICAL_STABLE_ID_PATTERN.match(out)
