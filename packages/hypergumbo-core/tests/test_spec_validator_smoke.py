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
    compute_stable_id_stats,
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
    # ADR-0035 §5: stats are None on a bare call (no symbols supplied);
    # the production finalize path always supplies them.
    assert report["stable_id_stats"] is None


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
        # id-format:F3 round-trip: the kind-slot must equal Symbol.kind for a
        # fully-conformant symbol, so build it from a_kind rather than a
        # hardcoded "function".
        id=f"python:test/fake.py:1-1:sym:{a_kind}",
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


def test_axis_conformance_confidence_source_bounded_enum() -> None:
    """ADR-0039 R2: Edge.confidence_source is checked against the bounded enum."""
    from hypergumbo_core.catalog import all_known_pass_ids
    from hypergumbo_core.evidence_types import all_evidence_type_names

    a_pass = next(iter(all_known_pass_ids()))
    an_evidence = next(iter(all_evidence_type_names()))

    good = _FakeSym(
        id="edge:good", edge_type="calls", evidence_type=an_evidence,
        evidence_lang=None, origin=[a_pass], confidence_source="evidence_derived",
    )
    bad = _FakeSym(
        id="edge:bad", edge_type="calls", evidence_type=an_evidence,
        evidence_lang=None, origin=[a_pass], confidence_source="bogus_source",
    )
    violations = validate_ir([], [good, bad], [])
    matched = [v for v in violations if v.field_name == "Edge.confidence_source"]
    assert len(matched) == 1
    assert matched[0].record_id == "edge:bad"
    assert matched[0].axis == "bounded-enum"


def test_confidence_source_bounded_enum_matches_ir_vocabulary() -> None:
    """Drift guard: the validator's bounded-enum set == ir.VALID_CONFIDENCE_SOURCES."""
    from hypergumbo_core.ir import VALID_CONFIDENCE_SOURCES
    from hypergumbo_core.spec_validator import _BOUNDED_ENUMS

    assert _BOUNDED_ENUMS[("Edge", "confidence_source")] == VALID_CONFIDENCE_SOURCES


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
        # synthetic:F2: a coherent (fully-stamped) Class-B stand-in now also
        # carries display_label (the affirmative half of the biconditional).
        display_label="websocket:class-b",
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

    # Deterministic non-exempt kind: sorted() avoids frozenset hash-order
    # flakiness, and the exemptions (file/external_symbol, line ~839) must be
    # excluded or the check correctly emits 0 for those kinds.
    a_kind = next(
        k for k in sorted(all_symbol_kind_names())
        if k not in ("file", "external_symbol")
    )
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
# ADR-0037 ruling 5 — is_resolved ⇒ first-party FK predicate
# ----------------------------------------------------------------------
_FK_FIELD = "Edge.is_resolved / Edge.dst"


def _ext_node(node_id: str) -> _FakeSym:
    """An external_symbol placeholder node (not a first-party target)."""
    return _FakeSym(
        id=node_id, kind="external_symbol", language=None,
        protocol_origin=None, discovery_language="python", display_label="x",
    )


def _fp_node(node_id: str) -> _FakeSym:
    """A first-party (in-repo) function node."""
    return _FakeSym(
        id=node_id, kind="function", language="python",
        protocol_origin=None, discovery_language=None, display_label=None,
    )


def _res_edge(dst: str, *, is_resolved: bool) -> _FakeSym:
    return _FakeSym(
        id=f"edge:{dst}", edge_type="calls", evidence_type="ast_call",
        evidence_lang=None, origin=[], dst=dst, dst_ref=None,
        is_resolved=is_resolved,
    )


def test_cross_field_flags_resolved_edge_to_external_placeholder() -> None:
    """is_resolved=True pointing at an external_symbol placeholder is the WI-kukuk
    contradiction (resolution names in-repo-ness, ADR-0037 ruling 1) — one error."""
    node = _ext_node("python:os.path:0-0:join:external_symbol")
    edge = _res_edge("python:os.path:0-0:join:external_symbol", is_resolved=True)
    violations = validate_ir([node], [edge], [])
    matched = [v for v in violations if v.field_name == _FK_FIELD]
    assert len(matched) == 1
    assert matched[0].severity == "error"
    assert matched[0].validator_class == "cross_field"


def test_cross_field_resolved_edge_to_first_party_passes() -> None:
    """is_resolved=True pointing at a real in-repo node is correct — no violation."""
    node = _fp_node("m.py:1-1:f:function")
    edge = _res_edge("m.py:1-1:f:function", is_resolved=True)
    violations = validate_ir([node], [edge], [])
    assert not any(v.field_name == _FK_FIELD for v in violations)


def test_cross_field_unresolved_edge_to_external_placeholder_passes() -> None:
    """is_resolved=False pointing at an external placeholder is the normal external
    case — the converse is deliberately not enforced (ADR-0037 ruling 5)."""
    node = _ext_node("python:os.path:0-0:join:external_symbol")
    edge = _res_edge("python:os.path:0-0:join:external_symbol", is_resolved=False)
    violations = validate_ir([node], [edge], [])
    assert not any(v.field_name == _FK_FIELD for v in violations)


def test_cross_field_resolved_edge_dst_absent_not_fk_flagged() -> None:
    """is_resolved=True with a dst absent from the node set is the dangling case,
    owned by the sibling endpoint-closure work — NOT this predicate (so isolated
    unit fixtures that point outside their symbol set don't trip it)."""
    edge = _res_edge("missing:target:id", is_resolved=True)
    violations = validate_ir([], [edge], [])
    assert not any(v.field_name == _FK_FIELD for v in violations)


# ----------------------------------------------------------------------
# validator:F2 — origin_run_id -> analysis_runs FK predicate
# (WI-moriz keystone; regression guard for WI-mosil + synthetic:F1)
# ----------------------------------------------------------------------
_ORIGIN_FK_FIELDS = ("Symbol.origin_run_id", "Edge.origin_run_id")


def _run(execution_id: str) -> dict:
    """An AnalysisRun stand-in. Runs reach the validator as dicts in
    production (cli.py accumulates ``run.to_dict()``), so the FK set is
    built via the dict-aware ``_read`` helper — model that here. Only
    ``execution_id`` is read by the FK predicate; ``"pass"`` is included
    for realism."""
    return {"execution_id": execution_id, "pass": "analyze"}


def _origin_fk(violations: list) -> list:
    return [v for v in violations if v.field_name in _ORIGIN_FK_FIELDS]


def test_origin_fk_content_gated_skips_without_runs() -> None:
    """No analysis_runs => the FK predicate is inert. Every isolated unit
    fixture in this suite calls validate_ir with analysis_runs=[]; gating on a
    non-empty run set keeps the check off them with zero collateral, and a
    degenerate no-runs corpus has nothing to validate against."""
    sym = _FakeSym(id="m.py:1-1:f:function", origin_run_id="ghost-run")
    assert _origin_fk(validate_ir([sym], [], [])) == []


def test_origin_fk_passes_when_origin_matches_a_run() -> None:
    """origin_run_id naming an existing AnalysisRun.execution_id is correct —
    the node->AnalysisRun provenance join resolves; no violation."""
    sym = _FakeSym(id="m.py:1-1:f:function", origin_run_id="run-1")
    assert _origin_fk(validate_ir([sym], [], [_run("run-1")])) == []


def test_origin_fk_flags_empty_origin_when_runs_exist() -> None:
    """Empty origin_run_id with a run set present is the WI-mosil regression (a
    node whose node->AnalysisRun provenance join is broken) — one error."""
    sym = _FakeSym(id="m.py:1-1:f:function", origin_run_id="")
    matched = _origin_fk(validate_ir([sym], [], [_run("run-1")]))
    assert len(matched) == 1
    assert matched[0].severity == "error"
    assert matched[0].validator_class == "cross_field"
    assert matched[0].field_name == "Symbol.origin_run_id"


def test_origin_fk_flags_dangling_origin() -> None:
    """A non-empty origin_run_id matching no AnalysisRun is a dangling
    provenance FK — one error."""
    sym = _FakeSym(id="m.py:1-1:f:function", origin_run_id="run-GONE")
    matched = _origin_fk(validate_ir([sym], [], [_run("run-1")]))
    assert len(matched) == 1
    assert matched[0].severity == "error"
    assert "no matching AnalysisRun" in matched[0].message


def test_origin_fk_exempts_legacy_deserialized_sentinel() -> None:
    """The from_dict legacy-rehydration sentinel is a deserialization marker,
    not a producer defect — exempt from the FK predicate."""
    from hypergumbo_core.ir import LEGACY_DESERIALIZED_SENTINEL

    sym = _FakeSym(id="m.py:1-1:f:function",
                   origin_run_id=LEGACY_DESERIALIZED_SENTINEL)
    assert _origin_fk(validate_ir([sym], [], [_run("run-1")])) == []


def test_origin_fk_checks_edges() -> None:
    """The predicate covers edges as well as symbols (Edge.origin_run_id)."""
    edge = _FakeSym(id="edge:1", origin_run_id="run-GONE")
    matched = _origin_fk(validate_ir([], [edge], [_run("run-1")]))
    assert len(matched) == 1
    assert matched[0].field_name == "Edge.origin_run_id"


def test_origin_fk_flags_empty_edge_origin() -> None:
    """Edge with empty origin_run_id + runs present => one error. Edge's
    __post_init__ blocks empty at construction (WI-higap), so this branch
    guards the deserialized/dict path that bypasses the constructor."""
    edge = _FakeSym(id="edge:1", origin_run_id="")
    matched = _origin_fk(validate_ir([], [edge], [_run("run-1")]))
    assert len(matched) == 1
    assert matched[0].field_name == "Edge.origin_run_id"


# ----------------------------------------------------------------------
# WI-mujor — dangling-endpoint referential-integrity predicate
# (the dst-absent half deferred from the ADR-0037 is_resolved<->dst FK)
# ----------------------------------------------------------------------
_DANGLING_FIELDS = ("Edge.src", "Edge.dst")


def _dangling(violations: list) -> list:
    return [v for v in violations if v.field_name in _DANGLING_FIELDS]


def test_dangling_content_gated_skips_without_runs() -> None:
    """No analysis_runs => the predicate is inert. Every isolated unit fixture
    points edges outside its tiny symbol set; gating on a non-empty run set
    keeps the check off them with zero collateral (mirrors origin_run_id_fk)."""
    edge = _FakeSym(id="e:1", src="ghost-src", dst="ghost-dst")
    assert _dangling(validate_ir([], [edge], [])) == []


def test_dangling_passes_when_both_endpoints_resolve() -> None:
    """An edge whose src and dst are both present in the symbol set is
    endpoint-closed; no violation."""
    a = _FakeSym(id="m.py:1-1:a:function", src=None, dst=None)
    b = _FakeSym(id="m.py:2-2:b:function", src=None, dst=None)
    edge = _FakeSym(id="e:1", src="m.py:1-1:a:function", dst="m.py:2-2:b:function")
    assert _dangling(validate_ir([a, b], [edge], [_run("run-1")])) == []


def test_dangling_flags_absent_src_when_runs_exist() -> None:
    """A non-empty Edge.src naming no node is a dangling endpoint — the
    WI-mujor regression (the 23 tier-filtered src-dangling edges) surfacing as
    one error instead of passing the validator silently."""
    b = _FakeSym(id="m.py:2-2:b:function", src=None, dst=None)
    edge = _FakeSym(id="e:1", src="m.py:9-9:gone:function", dst="m.py:2-2:b:function")
    matched = _dangling(validate_ir([b], [edge], [_run("run-1")]))
    assert len(matched) == 1
    assert matched[0].severity == "error"
    assert matched[0].validator_class == "cross_field"
    assert matched[0].field_name == "Edge.src"
    assert "dangling" in matched[0].message


def test_dangling_flags_absent_dst_when_runs_exist() -> None:
    """The dst side too: a dst naming no node (and no placeholder) is dangling.
    Post-boundary-synthesis externals resolve to placeholder nodes, so a truly
    absent dst is a finalization/filter regression."""
    a = _FakeSym(id="m.py:1-1:a:function", src=None, dst=None)
    edge = _FakeSym(id="e:1", src="m.py:1-1:a:function", dst="m.py:9-9:gone:function")
    matched = _dangling(validate_ir([a], [edge], [_run("run-1")]))
    assert len(matched) == 1
    assert matched[0].field_name == "Edge.dst"


def test_dangling_flags_both_endpoints() -> None:
    """An edge with BOTH endpoints absent emits two violations (one per slot)."""
    edge = _FakeSym(id="e:1", src="gone-a", dst="gone-b")
    matched = _dangling(validate_ir([], [edge], [_run("run-1")]))
    assert {v.field_name for v in matched} == {"Edge.src", "Edge.dst"}
    assert len(matched) == 2


def test_dangling_ignores_empty_endpoint() -> None:
    """An empty/None endpoint is not 'dangling' (that's the dst_ref<->dst
    coherence predicate's job); only a non-empty-but-unresolved ref trips this
    check, so the two predicates don't double-count."""
    a = _FakeSym(id="m.py:1-1:a:function", src=None, dst=None)
    edge = _FakeSym(id="e:1", src="m.py:1-1:a:function", dst="")
    assert _dangling(validate_ir([a], [edge], [_run("run-1")])) == []


# ----------------------------------------------------------------------
# WI-vudul — fingerprint output-boundary format guard
# ----------------------------------------------------------------------
_FP_FIELD = "Symbol.fingerprint"


def _fp_fmt(violations: list) -> list:
    return [v for v in violations if v.field_name == _FP_FIELD]


def _fp_sym(**kw):
    base = {"id": "m.py:1-1:f:function", "language": "python", "fingerprint": None}
    base.update(kw)
    return _FakeSym(**base)


def test_fingerprint_format_content_gated_skips_without_runs() -> None:
    """No analysis_runs => inert. Some unit fixtures carry bare placeholder
    fingerprints (fingerprint='fp1') on language-typed Symbols; gating on a
    non-empty run set keeps the check off them."""
    sym = _fp_sym(fingerprint="deadbeef")
    assert _fp_fmt(validate_ir([sym], [], [])) == []


def test_fingerprint_format_passes_canonical_hgfp2() -> None:
    sym = _fp_sym(fingerprint="hgfp2:deadbeefdeadbeef")
    assert _fp_fmt(validate_ir([sym], [], [_run("run-1")])) == []


def test_fingerprint_format_passes_none() -> None:
    """A None fingerprint (synthetic external_symbol / file null) is fine."""
    sym = _fp_sym(fingerprint=None)
    assert _fp_fmt(validate_ir([sym], [], [_run("run-1")])) == []


def test_fingerprint_format_flags_bare_hex_on_source_node() -> None:
    """A bare hex fingerprint on a real source node (language is not None) is a
    producer leak that survived to output — one error."""
    sym = _fp_sym(fingerprint="deadbeefdeadbeef")
    matched = _fp_fmt(validate_ir([sym], [], [_run("run-1")]))
    assert len(matched) == 1
    assert matched[0].severity == "error"
    assert matched[0].validator_class == "id_format"
    assert "non-canonical" in matched[0].message


def test_fingerprint_format_exempts_class_b_language_none() -> None:
    """Class-B synthetic stand-ins (language is None) carry an identity-hash
    second shape, not hgfp2 — exempt (ADR-0031 / WI-lisog)."""
    sym = _fp_sym(language=None, fingerprint="deadbeefdeadbeef")
    assert _fp_fmt(validate_ir([sym], [], [_run("run-1")])) == []


# ----------------------------------------------------------------------
# validator:F2 (WI-moriz) — wired-checks disclosure manifest
# ----------------------------------------------------------------------
def test_wired_checks_manifest_present_in_report() -> None:
    """build_validation_report discloses the wired predicate set so a 0 class
    count reads as '0 instances of these named checks', not '0 defects of any
    kind' — the structural answer to WI-moriz's false-all-clear complaint."""
    from hypergumbo_core.spec_validator import _VALIDATOR_CLASSES

    report = build_validation_report([])
    manifest = report["wired_checks"]
    assert isinstance(manifest, list) and manifest
    for entry in manifest:
        assert set(entry) == {"check", "validator_class", "description"}
        assert entry["validator_class"] in set(_VALIDATOR_CLASSES)
    # Every published violation class is backed by at least one wired check.
    assert {e["validator_class"] for e in manifest} == set(_VALIDATOR_CLASSES)


def test_wired_checks_manifest_matches_validate_ir() -> None:
    """Structural drift guard (the WI-moriz root cause made un-reproducible):
    the disclosed check set must equal the set of ``_check_*`` predicates
    actually wired into ``validate_ir``. You cannot wire a new check without
    disclosing it, nor disclose one you did not wire — so the report can never
    silently imply a class is checked when it is not (or vice versa)."""
    import inspect
    import re

    from hypergumbo_core import spec_validator

    src = inspect.getsource(spec_validator.validate_ir)
    wired_in_code = set(re.findall(r"_check_(\w+)\(", src))
    wired_in_manifest = {c["check"] for c in spec_validator._WIRED_CHECKS}
    assert wired_in_code == wired_in_manifest, (
        "wired-checks manifest drift: wired-but-undisclosed="
        f"{wired_in_code - wired_in_manifest}; disclosed-but-unwired="
        f"{wired_in_manifest - wired_in_code}"
    )


# ----------------------------------------------------------------------
# INV-fahub — no un-demoted harmful receiver-blind magnet survives
# ----------------------------------------------------------------------

def _magnet_edge(src, dst):
    from types import SimpleNamespace
    return SimpleNamespace(
        id="edge:1", src=src, dst=dst, edge_type="calls", is_resolved=True,
        evidence_type="ast_call", confidence=0.85, meta={"call_construct": "function"},
    )


def test_harmful_magnet_survivor_is_flagged() -> None:
    # A production->test-helper magnet that was NOT demoted (simulating a
    # demotion regression) is caught by the durable gate.
    from hypergumbo_core.spec_validator import _check_no_harmful_receiver_blind_magnets
    src = _FakeSym(id="s", name="App.run", kind="method", path="app/main.go", language="go")
    dst = _FakeSym(id="d", name="Collector.Add", kind="method",
                   path="test/testutils/collector.go", language="go")
    edges = [_magnet_edge("s", "d")]
    violations = _check_no_harmful_receiver_blind_magnets([src, dst], edges)
    assert len(violations) == 1
    assert violations[0].severity == "error"
    assert violations[0].validator_class == "cross_field"
    assert violations[0].record_id == "edge:1"


def test_kept_correct_but_unprovable_bind_is_not_flagged() -> None:
    # The correct-but-unprovable trait-dispatch residual (ADR-0012 scope) is a
    # receiver-blind magnet but NOT harmful — the durable gate must not flag it.
    from hypergumbo_core.spec_validator import _check_no_harmful_receiver_blind_magnets
    src = _FakeSym(id="s", name="App.run", kind="method", path="src/lib.rs", language="rust")
    dst = _FakeSym(id="d", name="Red::next", kind="method",
                   path="src/source/noise.rs", language="rust")
    edges = [_magnet_edge("s", "d")]
    assert _check_no_harmful_receiver_blind_magnets([src, dst], edges) == []


def test_no_harmful_magnet_check_empty_is_noop() -> None:
    from hypergumbo_core.spec_validator import _check_no_harmful_receiver_blind_magnets
    assert _check_no_harmful_receiver_blind_magnets([], []) == []


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


def test_cross_field_stable_id_zero_collisions_pass() -> None:
    """All-distinct stable_ids → no corpus umbrella. The ~0 threshold
    (ADR-0035 §5) does not fire when there is genuinely no collision."""
    syms = [_make_sym_with_stable_id(i, f"sha256:{i:016x}") for i in range(100)]
    violations = validate_ir(syms, [], [])
    assert not any(
        v.validator_class == "cross_field"
        and v.field_name == "Symbol.stable_id"
        for v in violations
    )


def test_cross_field_stable_id_any_collision_flags() -> None:
    """ADR-0035 §5: the corpus threshold drops from 5% to ~0 — even a
    single collision (well under the old 5% alarm line) now fires the
    umbrella. v6's contract is collision-free BY DESIGN, not collision-rare.
    """
    # 100 distinct + one collision group of 2 == 2/101 ≈ 2% (was below 5%).
    syms = [_make_sym_with_stable_id(i, f"sha256:{i + 1:016x}") for i in range(100)]
    syms.append(_make_sym_with_stable_id(100, "sha256:0000000000000001", name="dup"))
    violations = validate_ir(syms, [], [])
    matched = [
        v for v in violations
        if v.validator_class == "cross_field"
        and v.field_name == "Symbol.stable_id"
        and v.severity == "warning"  # the corpus umbrella (not the per-file error)
    ]
    assert len(matched) == 1, "a single collision must fire the ~0-threshold umbrella"


def test_cross_field_stable_id_collisions_flags_rate_and_top_groups() -> None:
    """The corpus umbrella fires exactly ONE warning naming the rate
    (over an all-Symbols denominator) and the top collision groups."""
    syms: list[_FakeSym] = []
    # 10 distinct stable_ids + 5 colliding == 5/15 = 33.3% of all Symbols.
    for i in range(10):
        syms.append(_make_sym_with_stable_id(i, f"sha256:{i:016x}"))
    for i in range(5):
        syms.append(_make_sym_with_stable_id(
            100 + i, "sha256:cccccccccccccccc", name=f"colliding_{i}",
        ))
    violations = validate_ir(syms, [], [])
    matched = [
        v for v in violations
        if v.validator_class == "cross_field"
        and v.field_name == "Symbol.stable_id"
        and v.severity == "warning"
    ]
    # Exactly one umbrella, regardless of collision-group size.
    assert len(matched) == 1
    v = matched[0]
    assert v.severity == "warning"
    assert v.record_id is None
    assert "33.3%" in (v.observed or "")  # 5/15 over the all-Symbols denominator
    assert "sha256:cccccccccccccccc" in (v.message or "")
    assert "colliding_0" in (v.message or "")


def test_cross_field_stable_id_collision_discloses_none_cohort() -> None:
    """WI-niluv / ADR-0035 §5: the collision rate is computed over an
    ALL-Symbols denominator (the None-stable_id cohort INCLUDED), and the
    None cohort is disclosed in the same line, so a clean non-null rate can
    never silently hide a large no-stable_id cohort (the 2026-06-01 false
    all-clear shape: 178/757 reported while 136 None-cohort Symbols
    vanished from the denominator).
    """
    syms: list[_FakeSym] = []
    # 10 distinct + 5 colliding + 3 None == 5 collided / 18 population = 27.8%.
    for i in range(10):
        syms.append(_make_sym_with_stable_id(i, f"sha256:{i:016x}"))
    for i in range(5):
        syms.append(_make_sym_with_stable_id(
            100 + i, "sha256:cccccccccccccccc", name=f"colliding_{i}",
        ))
    # 3 Symbols with stable_id=None — now COUNTED in the denominator.
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
        and v.severity == "warning"
    ]
    assert len(matched) == 1
    observed = matched[0].observed or ""
    # Rate is now over the full population (5/18 = 27.8%), not 5/15 = 33.3%.
    assert "5/18" in observed
    assert "27.8%" in observed
    # None cohort disclosed in the same line; the old non-null wording is gone.
    assert "none_cohort=3/18" in observed
    assert "stable_id=None" in observed
    assert "denominator_scope=non_null" not in observed


# ----------------------------------------------------------------------
# ADR-0035 §5 — per-file stable_id uniqueness (HARD error) + stats
# ----------------------------------------------------------------------


def _make_sym_pathed(
    idx: int, sid: str, path: str, name: str = "f", kind: str = "function",
) -> _FakeSym:
    """A Symbol stand-in carrying a ``path`` (needed for the per-file check)."""
    return _FakeSym(
        id=f"python:{path}:{idx}-{idx}:{name}:{kind}",
        kind=kind, language="python", discovery_language=None,
        protocol_origin=None, display_label=None, origin=[],
        qualified_name=None, stable_id=sid, name=name, path=path,
        dst_ref=None, dst=None,
    )


def _per_file_errors(violations) -> list:
    """The error-severity per-file stable_id violations (not the warning umbrella)."""
    return [
        v for v in violations
        if v.validator_class == "cross_field"
        and v.field_name == "Symbol.stable_id"
        and v.severity == "error"
    ]


def test_per_file_stable_id_collision_flags_error() -> None:
    """Two Symbols in ONE file sharing a stable_id is a HARD error (ADR-0035
    §5 per-file uniqueness), one violation per colliding (path, id) group."""
    syms = [
        _make_sym_pathed(1, "sha256:aaaaaaaaaaaaaaaa", "a.py", name="alpha"),
        _make_sym_pathed(2, "sha256:aaaaaaaaaaaaaaaa", "a.py", name="beta"),
    ]
    errors = _per_file_errors(validate_ir(syms, [], []))
    assert len(errors) == 1
    v = errors[0]
    assert v.severity == "error"
    assert "a.py" in (v.observed or "")
    assert "sha256:aaaaaaaaaaaaaaaa" in (v.observed or "")
    assert "alpha" in (v.observed or "") and "beta" in (v.observed or "")


def test_per_file_stable_id_same_id_distinct_files_no_error() -> None:
    """The SAME stable_id in DIFFERENT files is NOT a per-file error (the
    corpus umbrella still warns about it, but the hard check is per-file)."""
    syms = [
        _make_sym_pathed(1, "sha256:bbbbbbbbbbbbbbbb", "a.py"),
        _make_sym_pathed(1, "sha256:bbbbbbbbbbbbbbbb", "b.py"),
    ]
    violations = validate_ir(syms, [], [])
    assert _per_file_errors(violations) == []
    # ...but the cross-file collision IS surfaced by the soft umbrella.
    assert any(
        v.severity == "warning" and v.field_name == "Symbol.stable_id"
        for v in violations
    )


def test_per_file_pathless_symbols_skipped() -> None:
    """Pathless symbols are out of scope for the per-file check (no file to
    be 'within'); the corpus umbrella covers them instead."""
    syms = [
        _make_sym_with_stable_id(1, "sha256:cccccccccccccccc"),
        _make_sym_with_stable_id(2, "sha256:cccccccccccccccc"),
    ]
    # _make_sym_with_stable_id sets no ``path`` attribute.
    assert _per_file_errors(validate_ir(syms, [], [])) == []


def test_compute_stable_id_stats_counts_population_and_collisions() -> None:
    """ADR-0035 §5 stats: population includes the None cohort; collision
    rate is over ALL symbols; per-file groups are counted separately. The
    fixture exercises all three branches: pathed, stable_id-but-no-path, None.
    """
    syms = [
        _make_sym_pathed(1, "sha256:1111111111111111", "a.py"),
        _make_sym_pathed(2, "sha256:2222222222222222", "a.py"),
        # per-file collision in a.py (shares sha256:1111... with sym 1)
        _make_sym_pathed(3, "sha256:1111111111111111", "a.py", name="dup"),
        # stable_id but NO path — exercises the `if path` false branch.
        _make_sym_with_stable_id(4, "sha256:4444444444444444"),
        # None cohort: no stable_id (and no path).
        _FakeSym(
            id="python:x.py:5-5:n:function", kind="function", language="python",
            discovery_language=None, protocol_origin=None, display_label=None,
            origin=[], qualified_name=None, stable_id=None, name="n",
            dst_ref=None, dst=None,
        ),
    ]
    stats = compute_stable_id_stats(syms)
    assert stats["total_symbols"] == 5
    assert stats["non_null"] == 4
    assert stats["none_cohort"] == 1
    assert stats["collision_groups"] == 1  # only sha256:1111... is shared
    assert stats["collided_symbols"] == 2
    assert stats["per_file_collision_groups"] == 1
    assert stats["none_cohort_pct"] == 20.0  # 1/5
    assert stats["collision_rate_pct"] == 40.0  # 2/5


def test_compute_stable_id_stats_empty_is_zeroed() -> None:
    """No symbols → all-zero stats, no ZeroDivision."""
    stats = compute_stable_id_stats([])
    assert stats["total_symbols"] == 0
    assert stats["none_cohort_pct"] == 0.0
    assert stats["collision_rate_pct"] == 0.0


def test_per_file_collision_record_id_is_order_deterministic() -> None:
    """ADR-0043 §6: the per-file violation's record_id must not depend on
    symbol-iteration order — it is the lexicographically-smallest id in the
    group, not the first-encountered (which would leak ranked-vs-append order).
    """
    a = _make_sym_pathed(1, "sha256:dddddddddddddddd", "a.py", name="alpha")
    b = _make_sym_pathed(2, "sha256:dddddddddddddddd", "a.py", name="beta")
    rid_ab = _per_file_errors(validate_ir([a, b], [], []))[0].record_id
    rid_ba = _per_file_errors(validate_ir([b, a], [], []))[0].record_id
    assert rid_ab == rid_ba
    assert rid_ab == min(a.id, b.id)


def test_corpus_umbrella_message_is_order_deterministic() -> None:
    """ADR-0043 §6: equal-size collision groups are tie-broken on the
    stable_id, so the umbrella message is byte-stable across iteration orders.
    """
    def build() -> list[_FakeSym]:
        return [
            _make_sym_with_stable_id(1, "sha256:aaaaaaaaaaaaaaaa", name="a1"),
            _make_sym_with_stable_id(2, "sha256:aaaaaaaaaaaaaaaa", name="a2"),
            _make_sym_with_stable_id(3, "sha256:bbbbbbbbbbbbbbbb", name="b1"),
            _make_sym_with_stable_id(4, "sha256:bbbbbbbbbbbbbbbb", name="b2"),
        ]

    def umbrella_msg(syms) -> str:
        vs = [
            v for v in validate_ir(syms, [], [])
            if v.field_name == "Symbol.stable_id" and v.severity == "warning"
        ]
        return vs[0].message

    assert umbrella_msg(build()) == umbrella_msg(list(reversed(build())))


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

    out = make_entry_stable_id("vertex", "main", "pkg/main.py")
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
        # synthetic:F2: display_label is part of the Class-B contract now, so a
        # "Class-B sym" fixture is fully-stamped by default; the unstamped-canary
        # test overrides this back to None explicitly.
        "display_label": "websocket:class-b",
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
    origin, and — since synthetic:F2 — display_label)."""
    sym = _class_b_sym(
        stable_id=None, fingerprint=None, discovery_language=None, origin=[],
        display_label=None,
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
        "Symbol.display_label",
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


def test_class_b_missing_display_label_is_flagged() -> None:
    """META-huvuh validator half (synthetic:F2): a Class-B synthetic stand-in
    (language=None, protocol_origin set) with display_label=None emits the
    affirmative Symbol.display_label canary umbrella. The other Class-B fields
    are stamped so ONLY the display_label gap is exercised."""
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(iter(all_symbol_kind_names()))
    sym = _FakeSym(
        id="javascript:app.js:5-5:ipc:function",
        kind=a_kind, language=None, discovery_language="javascript",
        protocol_origin="websocket", origin=["x"], qualified_name=None,
        stable_id="sha256:aaaabbbbccccdddd", fingerprint="1111222233334444",
        display_label=None,
    )
    violations = validate_ir([sym], [], [])
    assert any(v.field_name == "Symbol.display_label" for v in violations)


def test_class_b_with_display_label_not_flagged() -> None:
    """A fully-stamped Class-B stand-in (display_label set) emits no
    Symbol.display_label canary umbrella (and the Class-A contrapositive does
    not fire either, since language is None)."""
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(iter(all_symbol_kind_names()))
    sym = _FakeSym(
        id="javascript:app.js:5-5:ipc:function",
        kind=a_kind, language=None, discovery_language="javascript",
        protocol_origin="websocket", origin=["x"], qualified_name=None,
        stable_id="sha256:aaaabbbbccccdddd", fingerprint="1111222233334444",
        display_label="websocket:ipc:request:chan",
    )
    violations = validate_ir([sym], [], [])
    assert not any(v.field_name == "Symbol.display_label" for v in violations)


# ----------------------------------------------------------------------
# id-format:F3 — Symbol.id round-trip canary sub-checks
# ----------------------------------------------------------------------
#
# These exercise ``_check_id_roundtrip``, which runs ONLY on ids that
# already pass the shape-only ``_CANONICAL_ID_PATTERN`` (shape failures
# are owned by ``_check_id_format``). It then parses the last-3 colon-free
# tokens (span, name, kind) per ADR-0036 Ruling 1 and enforces: kind-slot
# registry membership (advisory), kind-slot == Symbol.kind (advisory),
# non-empty name-slot (advisory), and span start<=end (error). The advisory
# checks land at ``warning`` until the Wave-2 id-changing folds (WI-pubiv
# external_symbol kind-slot, WI-kugaj route/event role, audit-findings 0005
# tsconfig) clear the known backlog, at which point they promote to error.


def test_id_roundtrip_passes_on_registered_matching_canonical_id() -> None:
    """A canonical id whose kind-slot is a registered kind equal to
    Symbol.kind, with a non-empty name and start<=end span, is clean."""
    from hypergumbo_core.spec_validator import _check_id_roundtrip

    sym = _FakeSym(id="python:pkg/foo.py:10-12:do_thing:function", kind="function")
    assert _check_id_roundtrip([sym]) == []


def test_id_roundtrip_flags_unregistered_kind_slot_warning() -> None:
    """An id kind-slot that is not a registered symbol-kind is flagged at
    advisory (warning) severity — the net value over the shape-only canonical
    pattern. kind-slot == Symbol.kind here, isolating membership from the
    mismatch check."""
    from hypergumbo_core.spec_validator import _check_id_roundtrip

    sym = _FakeSym(
        id="python:pkg/foo.py:1-1:name:zzznotarealkind", kind="zzznotarealkind"
    )
    violations = _check_id_roundtrip([sym])
    assert len(violations) == 1
    v = violations[0]
    assert v.severity == "warning"
    assert v.validator_class == "id_format"
    assert v.field_name == "Symbol.id"
    assert v.observed == "zzznotarealkind"
    assert "registered symbol-kind" in v.message


def test_id_roundtrip_flags_kind_slot_mismatch_warning() -> None:
    """The id kind-slot must equal Symbol.kind. A divergence (both kinds
    registered) is flagged at advisory severity."""
    from hypergumbo_core.spec_validator import _check_id_roundtrip

    sym = _FakeSym(id="python:pkg/foo.py:1-1:name:function", kind="method")
    violations = _check_id_roundtrip([sym])
    assert len(violations) == 1
    v = violations[0]
    assert v.severity == "warning"
    assert v.field_name == "Symbol.id"
    assert "does not match Symbol.kind" in v.message


def test_id_roundtrip_catches_tsconfig_divergence_invisible_to_axis_conformance() -> None:
    """The tsconfig node (id kind-slot 'tsconfig', Symbol.kind 'file') is the
    motivating case: Symbol.kind='file' is registered, so axis_conformance is
    blind to the divergence, but the round-trip check catches both the
    unregistered kind-slot and the kind-slot != Symbol.kind mismatch."""
    from hypergumbo_core.spec_validator import _check_id_roundtrip

    sym = _FakeSym(
        id="json:tsconfig.json:1-8:tsconfig.json:tsconfig",
        kind="file",
        language="json",
        discovery_language=None,
        protocol_origin=None,
        origin=[],
        qualified_name=None,
    )
    # axis_conformance does NOT flag Symbol.kind (file is a registered kind)...
    full = validate_ir([sym], [], [])
    assert not any(v.field_name == "Symbol.kind" for v in full)
    # ...but the round-trip check catches the divergence.
    rt = _check_id_roundtrip([sym])
    assert any("does not match Symbol.kind" in v.message for v in rt)
    assert any("registered symbol-kind" in v.message for v in rt)
    assert all(v.severity == "warning" for v in rt)


def test_id_roundtrip_flags_empty_name_slot_warning() -> None:
    """An empty name-slot is flagged at advisory severity."""
    from hypergumbo_core.spec_validator import _check_id_roundtrip

    sym = _FakeSym(id="python:pkg/foo.py:1-1::function", kind="function")
    violations = _check_id_roundtrip([sym])
    assert len(violations) == 1
    assert violations[0].severity == "warning"
    assert "name-slot" in violations[0].message


def test_id_roundtrip_flags_span_start_after_end_error() -> None:
    """A span whose start exceeds its end is a malformation flagged at ERROR
    severity (no known T1 backlog, unlike membership/mismatch)."""
    from hypergumbo_core.spec_validator import _check_id_roundtrip

    sym = _FakeSym(id="python:pkg/foo.py:5-3:name:function", kind="function")
    violations = _check_id_roundtrip([sym])
    assert len(violations) == 1
    v = violations[0]
    assert v.severity == "error"
    assert "start" in v.message and "end" in v.message


def test_id_roundtrip_accepts_sentinel_and_equal_spans() -> None:
    """The synthetic 0-0 and file 1-1 sentinel spans satisfy start<=end."""
    from hypergumbo_core.spec_validator import _check_id_roundtrip

    for span in ("0-0", "1-1"):
        sym = _FakeSym(id=f"python:pkg/foo.py:{span}:name:function", kind="function")
        assert _check_id_roundtrip([sym]) == []


def test_id_roundtrip_skips_noncanonical_id() -> None:
    """Shape failures are owned by _check_id_format; the round-trip check
    skips ids that fail the canonical pattern (it can't safely parse them)."""
    from hypergumbo_core.spec_validator import _check_id_roundtrip

    sym = _FakeSym(id="packages/foo/bar.py::http_client::42", kind="call_site")
    assert _check_id_roundtrip([sym]) == []


def test_id_roundtrip_skips_when_kind_attr_absent() -> None:
    """A Symbol with no kind attribute skips the kind-slot==Symbol.kind check
    (required-field presence is axis_conformance's job); membership still
    applies (function is registered, so still clean)."""
    from hypergumbo_core.spec_validator import _check_id_roundtrip

    sym = _FakeSym(id="python:pkg/foo.py:1-1:bar:function")  # no kind attr
    assert _check_id_roundtrip([sym]) == []


def test_id_roundtrip_skips_none_and_nonstr_id() -> None:
    """None / non-str ids are skipped (axis_conformance owns presence)."""
    from hypergumbo_core.spec_validator import _check_id_roundtrip

    assert _check_id_roundtrip([_FakeSym(id=None, kind="function")]) == []
    assert _check_id_roundtrip([_FakeSym(id=123, kind="function")]) == []


def test_id_roundtrip_wired_into_validate_ir() -> None:
    """validate_ir runs the round-trip check alongside the other classes."""
    sym = _FakeSym(
        id="python:pkg/foo.py:5-3:name:function",  # start>end -> error
        kind="function",
        language="python",
        discovery_language=None,
        protocol_origin=None,
        origin=[],
        qualified_name=None,
    )
    violations = validate_ir([sym], [], [])
    assert any(
        v.validator_class == "id_format"
        and v.field_name == "Symbol.id"
        and "start" in v.message
        for v in violations
    )


def test_confidence_range_flags_out_of_band_edge() -> None:
    """WI-nurun step 4: an edge whose confidence is outside its evidence
    pathway's derived band emits an advisory cross_field/info violation."""
    from hypergumbo_core.ir import Edge
    # ast_import band is [0.30, 0.95]; 1.0 breaches the reserved ceiling.
    edge = Edge.create(
        src="python:a.py:1-1:f:function", dst="python:b.py:1-1:g:function",
        edge_type="imports", line=1, evidence_type="ast_import",
        confidence=1.0, origin="test", origin_run_id="test",
    )
    conf = [
        v for v in validate_ir([], [edge], [])
        if v.field_name == "confidence"
    ]
    assert len(conf) == 1
    assert conf[0].validator_class == "cross_field"
    assert conf[0].severity == "info"
    assert conf[0].record_id == edge.id
    assert conf[0].observed == "1.0"


def test_confidence_range_clean_for_in_band_edge() -> None:
    """An in-band (derived) confidence emits no range violation."""
    from hypergumbo_core.ir import Edge
    edge = Edge.create(
        src="python:a.py:1-1:f:function", dst="python:b.py:1-1:g:function",
        edge_type="imports", line=1, evidence_type="ast_import",
        confidence=0.95, origin="test", origin_run_id="test",
    )
    assert [
        v for v in validate_ir([], [edge], [])
        if v.field_name == "confidence"
    ] == []


# ----------------------------------------------------------------------
# INV-vokak — route-marker single-home coherence check
# ----------------------------------------------------------------------


def test_route_marker_single_home_flags_dual_carry() -> None:
    """A route marker (framework_role=='route') that ALSO carries a redundant
    path-less concept=route is the INV-vokak dual-carry — one violation."""
    from hypergumbo_core import spec_validator

    sym = _FakeSym(
        id="ruby:app/users.rb:10-20:index:function",
        meta={
            "framework_role": "route",
            "route_path": "/users",
            "http_method": "GET",
            "concepts": [{"concept": "route", "framework": "rails"}],
        },
    )
    violations = spec_validator._check_route_marker_single_home([sym])
    assert len(violations) == 1
    v = violations[0]
    assert v.severity == "error"
    assert v.validator_class == "cross_field"
    assert v.field_name == "meta.concepts"
    assert v.record_id == "ruby:app/users.rb:10-20:index:function"


def test_route_marker_single_home_clean_on_single_homed_marker() -> None:
    """A route marker whose framework lives on route_framework (no redundant
    concept), and a marker carrying only a *pathed* route concept, are both
    coherent — no violation."""
    from hypergumbo_core import spec_validator

    single_home = _FakeSym(
        id="ruby:app/users.rb:10-20:index:function",
        meta={
            "framework_role": "route",
            "route_path": "/users",
            "http_method": "GET",
            "route_framework": "rails",
        },
    )
    pathed_concept = _FakeSym(
        id="python:app.py:1-3:home:function",
        meta={
            "framework_role": "route",
            "concepts": [{"concept": "route", "framework": "flask", "path": "/x"}],
        },
    )
    assert spec_validator._check_route_marker_single_home(
        [single_home, pathed_concept],
    ) == []


def test_route_marker_single_home_ignores_non_marker_symbols() -> None:
    """A symbol WITHOUT the route marker is out of scope even if it carries a
    path-less route concept (that is the legitimate manifest-gated upstream
    projection, not a dual-carry)."""
    from hypergumbo_core import spec_validator

    non_marker = _FakeSym(
        id="python:app.py:1-3:home:function",
        meta={"concepts": [{"concept": "route", "framework": "flask"}]},
    )
    assert spec_validator._check_route_marker_single_home([non_marker]) == []


def test_route_marker_single_home_wired_into_validate_ir() -> None:
    """The predicate flows through validate_ir (so the ratchet gate sees it):
    a fully axis-conformant symbol carrying the dual-carry meta surfaces the
    meta.concepts violation among validate_ir's output."""
    from hypergumbo_core.catalog import all_known_languages
    from hypergumbo_core.symbol_kinds import all_symbol_kind_names

    a_kind = next(iter(all_symbol_kind_names()))
    a_lang = next(iter(all_known_languages()))
    sym = _FakeSym(
        id=f"python:test/fake.py:1-1:sym:{a_kind}",
        kind=a_kind,
        language=a_lang,
        discovery_language=None,
        protocol_origin=None,
        origin=[],
        qualified_name=None,
        meta={
            "framework_role": "route",
            "concepts": [{"concept": "route", "framework": "rails"}],
        },
    )
    dual_carry = [
        v for v in validate_ir([sym], [], []) if v.field_name == "meta.concepts"
    ]
    assert len(dual_carry) == 1
