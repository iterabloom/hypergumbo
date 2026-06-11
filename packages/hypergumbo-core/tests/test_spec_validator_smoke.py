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


def test_writer_contract_flags_default_config_fingerprint_on_dict_shaped_runs() -> None:
    """Production feeds AnalysisRuns to the validator as serialized dicts,
    not dataclass instances: the orchestrator accumulates
    ``analysis_runs.append(linker_result.run.to_dict())`` (cli.py). The
    writer-contract sub-pattern-2 check must therefore read through the
    dict-or-attribute ``_read`` helper — bare ``getattr`` on a dict
    silently returns the default, never matches the sentinel, and the
    check no-ops in production while passing the object-shaped tests
    above. This is the regression guard for the declared-fields:F1(a)
    resurrection (INV-luhur). The dict-shaped run also exercises the
    ``record_id`` (``example_id``) extraction, which must surface the
    offending run's ``execution_id`` rather than ``None``.
    """
    from hypergumbo_core.ir import _default_config_fingerprint

    default_fp = _default_config_fingerprint()
    # dict-shaped, exactly as cli.py:7905 accumulates them.
    runs = [
        {
            "execution_id": f"run:{i}",
            "pass_id": "py_v1",
            "config_fingerprint": default_fp,
        }
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
    # The violation must point at the offending run, read dict-aware.
    assert matched[0].record_id == "run:0"


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
# Phase 6 PR2 — Writer-contract sub-pattern 1 helper tests
# ----------------------------------------------------------------------


def test_is_truthy_none_is_false() -> None:
    """Phase 6 PR2: ``_is_truthy(record, field)`` returns False for None."""
    from hypergumbo_core.spec_validator import _is_truthy

    obj = _FakeSym(field_a=None)
    assert _is_truthy(obj, "field_a") is False


def test_is_truthy_empty_collection_is_false() -> None:
    """Empty list/dict/str returns False."""
    from hypergumbo_core.spec_validator import _is_truthy

    obj = _FakeSym(field_list=[], field_dict={}, field_str="")
    assert _is_truthy(obj, "field_list") is False
    assert _is_truthy(obj, "field_dict") is False
    assert _is_truthy(obj, "field_str") is False


def test_is_truthy_populated_collection_is_true() -> None:
    """Non-empty list/dict/str returns True."""
    from hypergumbo_core.spec_validator import _is_truthy

    obj = _FakeSym(field_list=[1], field_dict={"k": "v"}, field_str="x")
    assert _is_truthy(obj, "field_list") is True
    assert _is_truthy(obj, "field_dict") is True
    assert _is_truthy(obj, "field_str") is True


def test_is_truthy_scalar_non_none_is_true() -> None:
    """A non-None scalar (e.g., int 0 is False per Python convention) —
    we only treat None and empty containers as 'unpopulated'; other
    scalars are populated."""
    from hypergumbo_core.spec_validator import _is_truthy

    obj = _FakeSym(field_int=0, field_bool=False, field_float=0.0)
    assert _is_truthy(obj, "field_int") is True
    assert _is_truthy(obj, "field_bool") is True
    assert _is_truthy(obj, "field_float") is True


def test_is_truthy_reads_dict_shaped_record() -> None:
    """``_is_truthy`` must honour the dict shape too: sub-pattern-1 may be
    registered for AnalysisRun fields, which reach the validator as
    serialized dicts. Bare ``getattr`` on a dict returns None (→ False)
    regardless of the dict's contents; the dict-aware ``_read`` path makes
    a populated dict field read as truthy. (declared-fields:F1(a) class.)"""
    from hypergumbo_core.spec_validator import _is_truthy

    assert _is_truthy({"field_a": "value"}, "field_a") is True
    assert _is_truthy({"field_list": [1]}, "field_list") is True
    assert _is_truthy({"field_a": ""}, "field_a") is False
    assert _is_truthy({}, "field_missing") is False


def test_check_sub_pattern_1_never_populated_empty_table_is_no_op() -> None:
    """The Phase 6 PR2 sub-pattern-1 table is empty (every documented
    field is now wired). The helper must return [] when the table is
    empty."""
    from hypergumbo_core.spec_validator import (
        _check_sub_pattern_1_never_populated,
    )

    assert _check_sub_pattern_1_never_populated([], [], []) == []


# ----------------------------------------------------------------------
# Phase 3 PR3 — Cross-field coherence validator class tests
# ----------------------------------------------------------------------


def test_cross_field_class_b_coherent_passes() -> None:
    """Class B synthetic stand-in (language=None, protocol_origin
    populated) passes the cross-field check.

    The fixture is fully stamped (stable_id / fingerprint /
    discovery_language / non-empty origin): since the WI-kufib canary
    relocation, an UNSTAMPED Class B stand-in is no longer "coherent" —
    see the Class B stamping canary tests below.
    """
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(k for k in all_symbol_kind_names() if k != "file")
    sym = _FakeSym(
        id="python:test/fake.py:1-1:class-b:function",
        kind=a_kind,
        language=None,
        discovery_language="python",
        protocol_origin="websocket",
        display_label=None,
        origin=["websocket-linker"],
        stable_id="sha256:" + "0" * 16,
        fingerprint="hgfp2:" + "0" * 16,
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
# Phase 6 PR3 — INV-bazij stable_id collision umbrella check
# ----------------------------------------------------------------------


def _make_sym_with_stable_id(idx: int, sid: str, name: str = "f") -> _FakeSym:
    """Build a minimal Symbol stand-in with the given stable_id."""
    return _FakeSym(
        id=f"python:test/fake.py:{idx}-{idx}:sym{idx}:function",
        kind="function",
        language="python",
        discovery_language=None,
        protocol_origin=None,
        display_label=None,
        origin=[],
        qualified_name=None,
        stable_id=sid,
        name=name,
        dst_ref=None,
        dst=None,
    )


def test_cross_field_stable_id_collisions_below_threshold_pass() -> None:
    """A small number of collisions (under the 5% threshold) does not
    emit the umbrella violation — the validator only flags when the rate
    crosses the threshold."""
    # 100 distinct stable_ids + 1 collision (1/101 ≈ 1%, under 5%).
    syms: list[_FakeSym] = []
    for i in range(100):
        syms.append(_make_sym_with_stable_id(i, f"sha256:{i:016x}"))
    # Add one collision against the first stable_id (sha256:{0:016x})
    syms.append(_make_sym_with_stable_id(100, "sha256:0000000000000000", name="dup"))
    syms.append(_make_sym_with_stable_id(101, "sha256:0000000000000000", name="dup2"))
    violations = validate_ir(syms, [], [])
    assert not any(
        v.validator_class == "cross_field"
        and v.field_name == "Symbol.stable_id"
        for v in violations
    )


def test_cross_field_stable_id_collisions_above_threshold_flags() -> None:
    """When >5% of Symbols share stable_id, the umbrella violation fires
    with the collision rate and top-3 collision groups described in the
    message — and exactly ONE violation, not one-per-Symbol."""
    syms: list[_FakeSym] = []
    # 10 distinct stable_ids
    for i in range(10):
        syms.append(_make_sym_with_stable_id(i, f"sha256:{i:016x}"))
    # 5 colliding symbols (5/15 = 33%, well above 5%)
    for i in range(5):
        syms.append(_make_sym_with_stable_id(
            100 + i, "sha256:cccccccccccccccc", name=f"colliding_{i}",
        ))
    violations = validate_ir(syms, [], [])
    matched = [
        v for v in violations
        if v.validator_class == "cross_field"
        and v.field_name == "Symbol.stable_id"
    ]
    # Exactly one umbrella, regardless of collision-group size.
    assert len(matched) == 1
    v = matched[0]
    assert v.severity == "warning"
    assert v.record_id is None
    assert "33.3%" in (v.observed or "")
    assert "sha256:cccccccccccccccc" in (v.message or "")
    assert "colliding_0" in (v.message or "")


def test_cross_field_stable_id_collision_discloses_none_cohort() -> None:
    """WI-niluv: when the collision umbrella fires, the violation must
    DISCLOSE its denominator scope — how many Symbols carry stable_id=None
    and are EXCLUDED from the (collided/total) rate — so the encoding is
    biconditional. A low reported non-null rate must not silently hide an
    even more ambiguous no-stable_id-at-all cohort (the original bug:
    178/757 reported vs 178/893 population, 136 None-cohort Symbols
    vanished from the denominator with no disclosure).
    """
    syms: list[_FakeSym] = []
    # 10 distinct stable_ids + 5 colliding == 5/15 = 33.3% > 5% threshold.
    for i in range(10):
        syms.append(_make_sym_with_stable_id(i, f"sha256:{i:016x}"))
    for i in range(5):
        syms.append(_make_sym_with_stable_id(
            100 + i, "sha256:cccccccccccccccc", name=f"colliding_{i}",
        ))
    # 3 Symbols with stable_id=None — the silently-dropped cohort.
    for i in range(3):
        syms.append(_FakeSym(
            id=f"python:test/fake.py:{200 + i}-{200 + i}:nosid{i}:function",
            kind="function", language="python", discovery_language=None,
            protocol_origin=None, display_label=None, origin=[],
            qualified_name=None, stable_id=None, name=f"nosid{i}",
            dst_ref=None, dst=None,
        ))
    violations = validate_ir(syms, [], [])
    matched = [
        v for v in violations
        if v.validator_class == "cross_field"
        and v.field_name == "Symbol.stable_id"
    ]
    assert len(matched) == 1
    observed = matched[0].observed or ""
    # Back-compat: the non-null collision rate is still surfaced.
    assert "33.3%" in observed
    # WI-niluv disclosure: explicit scope + None cohort over full population.
    assert "denominator_scope=non_null" in observed
    assert "3/18" in observed  # 3 None of (15 non-null + 3 None) = 18 population
    assert "stable_id=None" in observed


# ----------------------------------------------------------------------
# WI-falum — fingerprint degeneracy umbrella check
# ----------------------------------------------------------------------
#
# The structural fingerprint hashes shape + identifiers + literals, so
# symbols with DISTINCT names should virtually never share one value en
# masse. When they do (76 TOML dependency nodes / 67 distinct package
# names / ONE fingerprint — the WI-falum 6.0.0 regression), the producer
# has lost content discrimination and the value is degenerate. Sibling
# of the INV-bazij stable_id collision-rate umbrella above.


def _make_sym_with_fingerprint(idx: int, fp: str | None, name: str) -> _FakeSym:
    """Build a minimal Symbol stand-in with the given fingerprint."""
    return _FakeSym(
        id=f"toml:test/pyproject.toml:{idx}-{idx}:{name}:dependency",
        kind="dependency",
        language="toml",
        discovery_language=None,
        protocol_origin=None,
        display_label=None,
        origin=[],
        qualified_name=None,
        stable_id=f"sha256:{idx:016x}",
        fingerprint=fp,
        name=name,
        dst_ref=None,
        dst=None,
    )


def test_cross_field_fingerprint_degeneracy_flags() -> None:
    """One fingerprint value shared by >= 10 distinctly-named symbols
    fires exactly ONE umbrella violation naming the degenerate value."""
    syms = [
        _make_sym_with_fingerprint(i, "hgfp2:deaddeaddeaddead", f"pkg_{i}")
        for i in range(12)
    ]
    violations = validate_ir(syms, [], [])
    matched = [
        v for v in violations
        if v.validator_class == "cross_field"
        and v.field_name == "Symbol.fingerprint"
    ]
    assert len(matched) == 1
    v = matched[0]
    assert v.severity == "warning"
    assert v.record_id is None
    assert "hgfp2:deaddeaddeaddead" in (v.message or "")
    assert "12" in (v.observed or "")
    assert "pkg_0" in (v.message or "")


def test_cross_field_fingerprint_degeneracy_discloses_none_cohort() -> None:
    """WI-niluv (structural twin): the fingerprint-degeneracy umbrella must
    likewise disclose how many Symbols carry fingerprint=None and are
    excluded from the degeneracy scan, so the report's denominator is
    explicit rather than silent (same disease as the stable_id collision
    check: ``if fp is None: continue`` dropped the cohort with no tally)."""
    syms = [
        _make_sym_with_fingerprint(i, "hgfp2:deaddeaddeaddead", f"pkg_{i}")
        for i in range(12)
    ]
    # 4 Symbols with fingerprint=None — silently excluded from the scan today.
    for i in range(4):
        syms.append(_make_sym_with_fingerprint(100 + i, None, f"nofp_{i}"))
    violations = validate_ir(syms, [], [])
    matched = [
        v for v in violations
        if v.validator_class == "cross_field"
        and v.field_name == "Symbol.fingerprint"
    ]
    assert len(matched) == 1
    observed = matched[0].observed or ""
    assert "denominator_scope=non_null" in observed
    assert "4/16" in observed  # 4 None of (12 non-null + 4 None) = 16 population
    assert "fingerprint=None" in observed


def test_cross_field_fingerprint_shared_by_same_name_passes() -> None:
    """Many symbols sharing a fingerprint under FEW distinct names is
    legitimate duplicate code (the design intent), not degeneracy."""
    syms = [
        _make_sym_with_fingerprint(i, "hgfp2:feedfeedfeedfeed", "__init__")
        for i in range(12)
    ]
    violations = validate_ir(syms, [], [])
    assert not any(
        v.validator_class == "cross_field"
        and v.field_name == "Symbol.fingerprint"
        for v in violations
    )


def test_cross_field_fingerprint_qualified_same_simple_name_passes() -> None:
    """Identical methods across many classes share a hash under many
    QUALIFIED names but one simple name — legitimate duplicate code
    (e.g. a pytest fixture method repeated per test class), not
    degeneracy. Names compare by simple form."""
    syms = [
        _make_sym_with_fingerprint(
            i, "hgfp2:beefbeefbeefbeef", f"TestClass{i}.tracker_set",
        )
        for i in range(12)
    ]
    violations = validate_ir(syms, [], [])
    assert not any(
        v.validator_class == "cross_field"
        and v.field_name == "Symbol.fingerprint"
        for v in violations
    )


def test_cross_field_fingerprint_distinct_values_pass() -> None:
    """Distinct fingerprints (the healthy state) emit no umbrella."""
    syms = [
        _make_sym_with_fingerprint(i, f"hgfp2:{i:016x}", f"pkg_{i}")
        for i in range(12)
    ]
    violations = validate_ir(syms, [], [])
    assert not any(
        v.validator_class == "cross_field"
        and v.field_name == "Symbol.fingerprint"
        for v in violations
    )


def test_cross_field_fingerprint_none_ignored() -> None:
    """Null fingerprints never count toward degeneracy groups."""
    syms = [
        _make_sym_with_fingerprint(i, None, f"pkg_{i}")
        for i in range(12)
    ]
    violations = validate_ir(syms, [], [])
    assert not any(
        v.validator_class == "cross_field"
        and v.field_name == "Symbol.fingerprint"
        for v in violations
    )


def test_cross_field_stable_id_empty_inputs_pass() -> None:
    """Zero symbols → no collision umbrella violation (division by zero
    avoided)."""
    violations = validate_ir([], [], [])
    assert not any(
        v.validator_class == "cross_field"
        and v.field_name == "Symbol.stable_id"
        for v in violations
    )


def test_cross_field_stable_id_none_values_skipped() -> None:
    """Symbols whose stable_id is None do not contribute to the
    collision count (they predate the universal stable_id stamping per
    INV-hunup) — only populated ones are scored."""
    syms = [
        _make_sym_with_stable_id(0, "sha256:0000000000000000"),
        _make_sym_with_stable_id(1, "sha256:1111111111111111"),
        # stable_id=None — must not crash the validator
        _FakeSym(
            id="python:test/fake.py:2-2:nosid:function",
            kind="function",
            language="python",
            discovery_language=None,
            protocol_origin=None,
            display_label=None,
            origin=[],
            qualified_name=None,
            stable_id=None,
            name="nosid",
            dst_ref=None,
            dst=None,
        ),
    ]
    violations = validate_ir(syms, [], [])
    assert not any(
        v.validator_class == "cross_field"
        and v.field_name == "Symbol.stable_id"
        for v in violations
    )


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


# ---------------------------------------------------------------------------
# Class B stamping canary (WI-kufib implementer caveat / META-niguz)
# ---------------------------------------------------------------------------
#
# Before schema 0.14.0, the 262 `language: None is not of type 'string'`
# whole-document validation errors were the de-facto canary for the
# under-stamped synthetic stand-in population. Relaxing the schema's
# nullability (the WI-kufib fix) silences that signal, so the canary
# moves here: a Class B stand-in (language=None, protocol_origin
# populated) must carry the identity fields that SHOULD be non-null.


def _class_b_sym(**overrides):
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(k for k in all_symbol_kind_names() if k != "file")
    fields = {
        "id": "python:test/fake.py:1-1:class-b:function",
        "kind": a_kind,
        "language": None,
        "discovery_language": "python",
        "protocol_origin": "websocket",
        "display_label": None,
        "origin": [],
        "qualified_name": None,
        "stable_id": None,
        "fingerprint": None,
        "dst_ref": None,
        "dst": None,
    }
    fields.update(overrides)
    return _FakeSym(**fields)


def test_cross_field_class_b_unstamped_emits_canary() -> None:
    """An unstamped Class B stand-in emits one umbrella violation per
    missing identity field (stable_id, fingerprint, discovery_language,
    origin)."""
    sym = _class_b_sym(
        stable_id=None, fingerprint=None, discovery_language=None, origin=[],
    )
    violations = validate_ir([sym], [], [])
    canary = [
        v for v in violations
        if v.validator_class == "cross_field" and "Class B" in (v.message or "")
        and "stand-in" in (v.message or "")
    ]
    flagged_fields = {v.field_name for v in canary}
    assert flagged_fields == {
        "Symbol.stable_id",
        "Symbol.fingerprint",
        "Symbol.discovery_language",
        "Symbol.origin",
    }


def test_cross_field_class_b_canary_is_umbrella_not_per_record() -> None:
    """Three unstamped Class B stand-ins missing the same field produce
    ONE umbrella violation for that field (writer-contract style), not
    three copies."""
    syms = [
        _class_b_sym(
            id=f"python:test/fake.py:{i}-{i}:class-b-{i}:function",
            stable_id=None,
            fingerprint="hgfp2:" + "0" * 16,
            discovery_language="python",
            origin=["websocket-linker"],
        )
        for i in range(3)
    ]
    violations = validate_ir(syms, [], [])
    stable_id_canary = [
        v for v in violations
        if v.field_name == "Symbol.stable_id" and "Class B" in (v.message or "")
    ]
    assert len(stable_id_canary) == 1
    assert "3" in stable_id_canary[0].observed


def test_cross_field_class_b_fully_stamped_passes_canary() -> None:
    """A fully-stamped Class B stand-in emits no canary violations."""
    sym = _class_b_sym(
        stable_id="sha256:" + "0" * 16,
        fingerprint="hgfp2:" + "0" * 16,
        discovery_language="python",
        origin=["websocket-linker"],
    )
    violations = validate_ir([sym], [], [])
    assert not any(
        "Class B" in (v.message or "") and "stand-in" in (v.message or "")
        for v in violations
        if v.validator_class == "cross_field"
    )


def test_cross_field_class_a_symbols_ignored_by_canary() -> None:
    """Class A real-source declarations are not subject to the Class B
    stamping canary even when their identity fields are None."""
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
        stable_id=None,
        fingerprint=None,
        dst_ref=None,
        dst=None,
    )
    violations = validate_ir([sym], [], [])
    assert not any(
        "Class B" in (v.message or "") and "stand-in" in (v.message or "")
        for v in violations
        if v.validator_class == "cross_field"
    )
