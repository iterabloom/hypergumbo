# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the audit-findings document format parser and validator.

Includes a live-tree property test that walks ``docs/audits/`` and
asserts every doc parses cleanly and every row's status agrees with
the live registry. The unit tests cover the parser's error paths and
the validator's mechanical-check predicates with synthetic registry
state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.audit_findings import (
    AXIS_EDGE_EDGE_TYPE,
    AXIS_EDGE_EVIDENCE_TYPE,
    AXIS_SYMBOL_KIND,
    AuditFindings,
    AuditFindingsError,
    DiagnosticTest,
    STATUS_PRELIM_RESOLVED,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    VERDICT_CANONICAL,
    VERDICT_DEPRECATE_NO_FOLD,
    VERDICT_FOLD,
    VerdictRow,
    _is_valid_expect,
    find_audit_findings_docs,
    find_readme_index_drift,
    find_zero_producer_violations,
    parse_audit_findings,
    validate_against_registry,
)


# --- Helpers ---

def _good_doc(value: str = "calls") -> str:
    """Minimal well-formed audit-findings doc for a single CANONICAL row.

    Uses ``calls`` because it is unambiguously present on the
    relationship axis in the live registry, so the validator's
    RESOLVED-CANONICAL predicate passes.
    """
    return f"""# Some audit

## Verdicts

```yaml
kind: audit_verdicts
axis: {AXIS_EDGE_EDGE_TYPE}
verdicts:
  - value: {value}
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "true"
      expect: exit_code:0
    rationale: "Canonical relationship-shaped value."
```
"""


def _write(tmp_path: Path, content: str) -> Path:
    md = tmp_path / "audit.md"
    md.write_text(content)
    return md


# --- Parser: happy path ---

def test_parse_minimal_well_formed_doc(tmp_path: Path):
    md = _write(tmp_path, _good_doc())
    findings = parse_audit_findings(md)

    assert findings.path == md
    assert findings.axis == AXIS_EDGE_EDGE_TYPE
    assert len(findings.verdicts) == 1
    row = findings.verdicts[0]
    assert row.value == "calls"
    assert row.verdict == VERDICT_CANONICAL
    assert row.fold_target is None
    assert row.status == STATUS_RESOLVED
    assert row.diagnostic_test == DiagnosticTest(cmd="true", expect="exit_code:0")
    assert row.rationale == "Canonical relationship-shaped value."


def test_parse_accepts_yml_fence_alias(tmp_path: Path):
    text = _good_doc().replace("```yaml", "```yml")
    md = _write(tmp_path, text)
    findings = parse_audit_findings(md)
    assert findings.verdicts[0].value == "calls"


def test_parse_accepts_fold_row_with_fold_target(tmp_path: Path):
    text = """# Audit

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: routes_to
    verdict: FOLD
    fold_target: dispatches_to
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -l routes_to"
      expect: empty
    rationale: "HTTP routing IS dispatch via path matching."
```
"""
    md = _write(tmp_path, text)
    findings = parse_audit_findings(md)
    row = findings.verdicts[0]
    assert row.verdict == VERDICT_FOLD
    assert row.fold_target == "dispatches_to"


def test_parse_accepts_deprecate_no_fold_row(tmp_path: Path):
    text = """# Audit

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: legacy_marker
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "git grep -l legacy_marker"
      expect: empty
    rationale: "Producer rewritten without fold."
```
"""
    md = _write(tmp_path, text)
    findings = parse_audit_findings(md)
    row = findings.verdicts[0]
    assert row.verdict == VERDICT_DEPRECATE_NO_FOLD
    assert row.fold_target is None


# --- Parser: error paths ---

def test_parse_missing_verdicts_heading(tmp_path: Path):
    md = _write(tmp_path, "# Just a title\n\nNo verdicts here.\n")
    with pytest.raises(AuditFindingsError, match="missing required"):
        parse_audit_findings(md)


def test_parse_heading_without_following_yaml_block(tmp_path: Path):
    md = _write(tmp_path, "# Title\n\n## Verdicts\n\nProse only, no block.\n")
    with pytest.raises(AuditFindingsError, match="not followed by a"):
        parse_audit_findings(md)


def test_parse_yaml_syntax_error(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: [unclosed
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="failed to parse"):
        parse_audit_findings(md)


def test_parse_top_level_not_a_mapping(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
- just a list
- not a mapping
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="must be a mapping"):
        parse_audit_findings(md)


def test_parse_missing_kind_key(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
axis: Edge.edge_type
verdicts: []
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="kind: audit_verdicts"):
        parse_audit_findings(md)


def test_parse_missing_axis_key(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
verdicts: []
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="must declare an 'axis'"):
        parse_audit_findings(md)


def test_parse_axis_wrong_type(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: 123
verdicts: []
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="must declare an 'axis'"):
        parse_audit_findings(md)


def test_parse_empty_verdicts_list(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts: []
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="'verdicts' list"):
        parse_audit_findings(md)


def test_parse_verdicts_not_a_list(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts: "not a list"
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="non-empty"):
        parse_audit_findings(md)


def test_parse_row_not_a_mapping(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - "just a string row"
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="row must be a mapping"):
        parse_audit_findings(md)


@pytest.mark.parametrize(
    "missing_key",
    ["value", "verdict", "status", "diagnostic_test", "rationale"],
)
def test_parse_row_missing_required_key(tmp_path: Path, missing_key: str):
    base = {
        "value": "calls",
        "verdict": "CANONICAL",
        "status": "RESOLVED",
        "diagnostic_test": {"cmd": "true", "expect": "empty"},
        "rationale": "ok",
    }
    base.pop(missing_key)
    rendered = "\n".join(f"    {k}: {v!r}" for k, v in base.items())
    text = f"""# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - {rendered.lstrip()}
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match=f"missing required key '{missing_key}'"):
        parse_audit_findings(md)


def test_parse_row_value_wrong_type(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: 123
    verdict: CANONICAL
    status: RESOLVED
    diagnostic_test: {cmd: "true", expect: empty}
    rationale: "ok"
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="non-empty string"):
        parse_audit_findings(md)


def test_parse_row_invalid_verdict(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: foo
    verdict: PROBABLY_FINE
    status: RESOLVED
    diagnostic_test: {cmd: "true", expect: empty}
    rationale: "ok"
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="invalid verdict"):
        parse_audit_findings(md)


def test_parse_row_invalid_status(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: foo
    verdict: CANONICAL
    status: MAYBE_FIXED
    diagnostic_test: {cmd: "true", expect: empty}
    rationale: "ok"
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="invalid status"):
        parse_audit_findings(md)


def test_parse_fold_row_missing_fold_target(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: foo
    verdict: FOLD
    fold_target: null
    status: RESOLVED
    diagnostic_test: {cmd: "true", expect: empty}
    rationale: "ok"
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="FOLD but no"):
        parse_audit_findings(md)


def test_parse_canonical_row_with_stray_fold_target(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: calls
    verdict: CANONICAL
    fold_target: something
    status: RESOLVED
    diagnostic_test: {cmd: "true", expect: empty}
    rationale: "ok"
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="only FOLD rows may have"):
        parse_audit_findings(md)


def test_parse_row_rationale_wrong_type(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: foo
    verdict: CANONICAL
    status: RESOLVED
    diagnostic_test: {cmd: "true", expect: empty}
    rationale: 42
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="'rationale' string"):
        parse_audit_findings(md)


def test_parse_diagnostic_test_not_a_mapping(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: foo
    verdict: CANONICAL
    status: RESOLVED
    diagnostic_test: "just a string"
    rationale: "ok"
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match="diagnostic_test' must be a"):
        parse_audit_findings(md)


def test_parse_diagnostic_test_missing_cmd(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: foo
    verdict: CANONICAL
    status: RESOLVED
    diagnostic_test: {expect: empty}
    rationale: "ok"
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match=r"diagnostic_test\.cmd"):
        parse_audit_findings(md)


def test_parse_diagnostic_test_invalid_expect(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: foo
    verdict: CANONICAL
    status: RESOLVED
    diagnostic_test: {cmd: "true", expect: "maybe_works"}
    rationale: "ok"
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match=r"diagnostic_test\.expect"):
        parse_audit_findings(md)


def test_parse_diagnostic_test_expect_wrong_type(tmp_path: Path):
    text = """# Title

## Verdicts

```yaml
kind: audit_verdicts
axis: Edge.edge_type
verdicts:
  - value: foo
    verdict: CANONICAL
    status: RESOLVED
    diagnostic_test: {cmd: "true", expect: 42}
    rationale: "ok"
```
"""
    md = _write(tmp_path, text)
    with pytest.raises(AuditFindingsError, match=r"diagnostic_test\.expect"):
        parse_audit_findings(md)


# --- Helper predicate ---

def test_is_valid_expect_recognises_canonical_values():
    assert _is_valid_expect("empty")
    assert _is_valid_expect("nonempty")
    assert _is_valid_expect("exit_code:0")
    assert _is_valid_expect("exit_code:42")


def test_is_valid_expect_rejects_unknown_values():
    assert not _is_valid_expect("nope")
    assert not _is_valid_expect("exit_code:")
    assert not _is_valid_expect("exit_code:abc")


# --- Validator ---

def test_validate_canonical_resolved_present_relationship_passes(tmp_path: Path):
    md = _write(tmp_path, _good_doc("calls"))
    findings = parse_audit_findings(md)
    assert validate_against_registry(findings) == []


def test_validate_canonical_resolved_absent_fails(tmp_path: Path):
    md = _write(tmp_path, _good_doc("nonexistent_value_zzz"))
    findings = parse_audit_findings(md)
    errors = validate_against_registry(findings)
    assert len(errors) == 1
    assert "CANONICAL + status RESOLVED" in errors[0]
    assert "absent" in errors[0]


def test_validate_unknown_axis_returns_error(tmp_path: Path):
    text = _good_doc().replace(
        f"axis: {AXIS_EDGE_EDGE_TYPE}", "axis: Ferret.feature",
    )
    md = _write(tmp_path, text)
    findings = parse_audit_findings(md)
    errors = validate_against_registry(findings)
    assert len(errors) == 1
    assert "no\n  registry mapping" in errors[0] or "no registry mapping" in errors[0]


def test_validate_canonical_resolved_symbol_kind_passes(tmp_path: Path):
    """Symbol.kind canonicals live on the language_construct axis."""
    text = f"""# Symbol kind audit

## Verdicts

```yaml
kind: audit_verdicts
axis: {AXIS_SYMBOL_KIND}
verdicts:
  - value: function
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "true"
      expect: exit_code:0
    rationale: "Cluster A canonical language construct."
```
"""
    md = _write(tmp_path, text)
    findings = parse_audit_findings(md)
    assert validate_against_registry(findings) == []


def test_validate_canonical_resolved_symbol_kind_off_axis_fails(tmp_path: Path):
    """A Symbol.kind value present on endpoint_shape (not language_construct)
    fails the CANONICAL+RESOLVED predicate; the failure message names the
    axis-specific canonical axis (language_construct), not the edge-type
    default (relationship).
    """
    text = f"""# Symbol kind audit

## Verdicts

```yaml
kind: audit_verdicts
axis: {AXIS_SYMBOL_KIND}
verdicts:
  - value: event_publisher
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "true"
      expect: exit_code:0
    rationale: "Wrong axis — should fail."
```
"""
    md = _write(tmp_path, text)
    findings = parse_audit_findings(md)
    errors = validate_against_registry(findings)
    assert len(errors) == 1
    assert "language_construct" in errors[0]


def test_validate_canonical_resolved_evidence_type_passes(tmp_path: Path):
    """Edge.evidence_type canonicals live on the inference_pathway axis."""
    text = f"""# Evidence type audit

## Verdicts

```yaml
kind: audit_verdicts
axis: {AXIS_EDGE_EVIDENCE_TYPE}
verdicts:
  - value: ast_call
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "true"
      expect: exit_code:0
    rationale: "Cluster A canonical inference pathway."
```
"""
    md = _write(tmp_path, text)
    findings = parse_audit_findings(md)
    assert validate_against_registry(findings) == []


def test_validate_fold_resolved_absent_passes():
    findings = AuditFindings(
        path=Path("synthetic.md"),
        axis=AXIS_EDGE_EDGE_TYPE,
        verdicts=(
            VerdictRow(
                value="absent_value_xyz",
                verdict=VERDICT_FOLD,
                fold_target="calls",
                status=STATUS_RESOLVED,
                diagnostic_test=DiagnosticTest(cmd="true", expect="empty"),
                rationale="ok",
            ),
        ),
    )
    assert validate_against_registry(findings) == []


def test_validate_fold_resolved_present_fails():
    findings = AuditFindings(
        path=Path("synthetic.md"),
        axis=AXIS_EDGE_EDGE_TYPE,
        verdicts=(
            VerdictRow(
                value="calls",
                verdict=VERDICT_FOLD,
                fold_target="dispatches_to",
                status=STATUS_RESOLVED,
                diagnostic_test=DiagnosticTest(cmd="true", expect="empty"),
                rationale="ok",
            ),
        ),
    )
    errors = validate_against_registry(findings)
    assert len(errors) == 1
    assert "FOLD + status RESOLVED" in errors[0]
    assert "ABSENT" in errors[0]


def test_validate_deprecate_no_fold_resolved_present_fails():
    findings = AuditFindings(
        path=Path("synthetic.md"),
        axis=AXIS_EDGE_EDGE_TYPE,
        verdicts=(
            VerdictRow(
                value="calls",
                verdict=VERDICT_DEPRECATE_NO_FOLD,
                fold_target=None,
                status=STATUS_RESOLVED,
                diagnostic_test=DiagnosticTest(cmd="true", expect="empty"),
                rationale="ok",
            ),
        ),
    )
    errors = validate_against_registry(findings)
    assert len(errors) == 1
    assert "DEPRECATE-NO-FOLD + status RESOLVED" in errors[0]


def test_validate_prelim_resolved_endpoint_shape_passes():
    # Pick any value currently on the endpoint_shape axis.
    from hypergumbo_core.edge_types import (
        AXIS_ENDPOINT_SHAPE,
        EDGE_TYPES,
    )

    endpoint_value = next(
        spec.name for spec in EDGE_TYPES if spec.axis == AXIS_ENDPOINT_SHAPE
    )
    findings = AuditFindings(
        path=Path("synthetic.md"),
        axis=AXIS_EDGE_EDGE_TYPE,
        verdicts=(
            VerdictRow(
                value=endpoint_value,
                verdict=VERDICT_FOLD,
                fold_target="calls",
                status=STATUS_PRELIM_RESOLVED,
                diagnostic_test=DiagnosticTest(cmd="true", expect="empty"),
                rationale="ok",
            ),
        ),
    )
    assert validate_against_registry(findings) == []


def test_validate_prelim_resolved_relationship_fails():
    findings = AuditFindings(
        path=Path("synthetic.md"),
        axis=AXIS_EDGE_EDGE_TYPE,
        verdicts=(
            VerdictRow(
                value="calls",
                verdict=VERDICT_FOLD,
                fold_target="dispatches_to",
                status=STATUS_PRELIM_RESOLVED,
                diagnostic_test=DiagnosticTest(cmd="true", expect="empty"),
                rationale="ok",
            ),
        ),
    )
    errors = validate_against_registry(findings)
    assert len(errors) == 1
    assert "PRELIM_RESOLVED" in errors[0]


def test_validate_prelim_resolved_absent_fails():
    findings = AuditFindings(
        path=Path("synthetic.md"),
        axis=AXIS_EDGE_EDGE_TYPE,
        verdicts=(
            VerdictRow(
                value="absent_value_xyz",
                verdict=VERDICT_FOLD,
                fold_target="calls",
                status=STATUS_PRELIM_RESOLVED,
                diagnostic_test=DiagnosticTest(cmd="true", expect="empty"),
                rationale="ok",
            ),
        ),
    )
    errors = validate_against_registry(findings)
    assert len(errors) == 1
    assert "PRELIM_RESOLVED" in errors[0]
    assert "absent" in errors[0]


def test_validate_unresolved_present_passes():
    findings = AuditFindings(
        path=Path("synthetic.md"),
        axis=AXIS_EDGE_EDGE_TYPE,
        verdicts=(
            VerdictRow(
                value="calls",
                verdict=VERDICT_CANONICAL,
                fold_target=None,
                status=STATUS_UNRESOLVED,
                diagnostic_test=DiagnosticTest(cmd="true", expect="empty"),
                rationale="ok",
            ),
        ),
    )
    assert validate_against_registry(findings) == []


def test_validate_unresolved_absent_fails():
    findings = AuditFindings(
        path=Path("synthetic.md"),
        axis=AXIS_EDGE_EDGE_TYPE,
        verdicts=(
            VerdictRow(
                value="absent_value_xyz",
                verdict=VERDICT_CANONICAL,
                fold_target=None,
                status=STATUS_UNRESOLVED,
                diagnostic_test=DiagnosticTest(cmd="true", expect="empty"),
                rationale="ok",
            ),
        ),
    )
    errors = validate_against_registry(findings)
    assert len(errors) == 1
    assert "UNRESOLVED" in errors[0]


# --- Tree walker ---

def test_find_audit_findings_docs_returns_empty_for_missing_dir(tmp_path: Path):
    assert find_audit_findings_docs(tmp_path) == []


def test_find_audit_findings_docs_finds_md_files(tmp_path: Path):
    audits = tmp_path / "docs" / "audits"
    audits.mkdir(parents=True)
    (audits / "0001-foo.md").write_text("x")
    (audits / "0002-bar.md").write_text("x")
    (audits / "README.md").write_text("x")
    (audits / "ignored.txt").write_text("x")

    found = find_audit_findings_docs(tmp_path)
    assert [p.name for p in found] == ["0001-foo.md", "0002-bar.md"]


# --- Live-tree property test ---

def test_live_tree_audit_findings_docs_parse_and_validate():
    """Walks the live ``docs/audits/`` tree and asserts every doc
    parses cleanly and every row's status agrees with the live
    registry. Skips when the tree is absent (isolated package run).
    """
    repo_root = Path(__file__).resolve().parents[3]
    docs = find_audit_findings_docs(repo_root)
    if not docs:
        pytest.skip("docs/audits/ not present (isolated package run)")

    all_errors: list[str] = []
    for md in docs:
        findings = parse_audit_findings(md)
        all_errors.extend(validate_against_registry(findings))

    assert not all_errors, (
        "Audit-findings docs disagree with the live registry:\n"
        + "\n".join(all_errors)
    )


# --- DEPRECATE-NO-FOLD-zero-producer regression guard ---


def _write_audit_with_deprecate_row(
    tmp_path: Path, value: str, axis: str = AXIS_SYMBOL_KIND,
) -> Path:
    audits = tmp_path / "docs" / "audits"
    audits.mkdir(parents=True)
    md = audits / "0099-test.md"
    md.write_text(f"""# Test audit

## Verdicts

```yaml
kind: audit_verdicts
axis: {axis}
verdicts:
  - value: {value}
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "true"
      expect: exit_code:0
    rationale: "Dead vocabulary, no producer."
```
""")
    return md


def test_zero_producer_violations_clean_when_no_emits(tmp_path: Path):
    """A DEPRECATE-NO-FOLD row whose value no producer emits returns no
    violation. The fixture ships an empty packages/ tree so the literal-
    kwarg + assignment-form trace finds zero emit sites for the
    deprecated value."""
    md = _write_audit_with_deprecate_row(tmp_path, "obviously_unused_kind_xyzqq")
    findings = parse_audit_findings(md)

    pkg_dir = tmp_path / "packages" / "fake" / "src"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "noop.py").write_text("# no producer emits anything here\n")

    errors = find_zero_producer_violations(tmp_path, [findings])
    assert errors == []


def test_zero_producer_violations_flags_literal_kwarg_emit(tmp_path: Path):
    """A DEPRECATE-NO-FOLD row whose value a producer emits via literal
    kwarg returns a violation entry naming the file:line. This is
    Step 4.5 producer-shape #1 (literal kwarg)."""
    md = _write_audit_with_deprecate_row(tmp_path, "leaked_kind_alpha")
    findings = parse_audit_findings(md)

    pkg_dir = tmp_path / "packages" / "fake" / "src"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "leak.py").write_text(
        'from foo import Symbol\n'
        'def emit():\n'
        '    return Symbol(kind="leaked_kind_alpha", id="x")\n'
    )

    errors = find_zero_producer_violations(tmp_path, [findings])
    assert len(errors) == 1
    assert "leaked_kind_alpha" in errors[0]
    assert "DEPRECATE-NO-FOLD" in errors[0]
    assert "leak.py:3" in errors[0]


def test_zero_producer_violations_flags_assignment_form_emit(tmp_path: Path):
    """A DEPRECATE-NO-FOLD row whose value a producer emits via
    function-local assignment-form-to-Name returns a violation. This is
    Step 4.5 producer-shape #3 (assignment-form to Name) — exactly the
    shape that the Wave 6 PR 4 reclassification surfaced as a literal-grep
    blind spot."""
    md = _write_audit_with_deprecate_row(tmp_path, "leaked_kind_beta")
    findings = parse_audit_findings(md)

    pkg_dir = tmp_path / "packages" / "fake" / "src"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "leak.py").write_text(
        'from foo import Symbol\n'
        'def emit():\n'
        '    k = "leaked_kind_beta"\n'
        '    return Symbol(kind=k, id="x")\n'
    )

    errors = find_zero_producer_violations(tmp_path, [findings])
    assert len(errors) == 1
    assert "leaked_kind_beta" in errors[0]
    assert "leak.py:4" in errors[0]


def test_zero_producer_violations_ignores_non_deprecate_rows(tmp_path: Path):
    """CANONICAL and FOLD verdicts are out of scope; the regression guard
    does not flag producers for those rows even when the producer exists.
    Their lifecycle is enforced by ``validate_against_registry``."""
    audits = tmp_path / "docs" / "audits"
    audits.mkdir(parents=True)
    md = audits / "0098-test.md"
    md.write_text(f"""# Test audit

## Verdicts

```yaml
kind: audit_verdicts
axis: {AXIS_SYMBOL_KIND}
verdicts:
  - value: function
    verdict: CANONICAL
    fold_target: null
    status: RESOLVED
    diagnostic_test:
      cmd: "true"
      expect: exit_code:0
    rationale: "Canonical language construct."
```
""")
    findings = parse_audit_findings(md)

    pkg_dir = tmp_path / "packages" / "fake" / "src"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "emit.py").write_text(
        'from foo import Symbol\n'
        'def f(): return Symbol(kind="function", id="x")\n'
    )

    errors = find_zero_producer_violations(tmp_path, [findings])
    assert errors == []


def test_zero_producer_violations_ignores_unknown_axes(tmp_path: Path):
    """Audit docs declaring an axis without a registry binding contribute
    no violations — the regression guard is silent for axes outside the
    three currently-registered ones (Symbol.kind / Edge.edge_type /
    Edge.evidence_type), since enumerating producers requires per-axis
    knowledge."""
    audits = tmp_path / "docs" / "audits"
    audits.mkdir(parents=True)
    md = audits / "0097-test.md"
    md.write_text("""# Test audit

## Verdicts

```yaml
kind: audit_verdicts
axis: SomeFutureAxis.field
verdicts:
  - value: anything
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "true"
      expect: exit_code:0
    rationale: "Future axis."
```
""")
    findings = parse_audit_findings(md)
    errors = find_zero_producer_violations(tmp_path, [findings])
    assert errors == []


def test_zero_producer_violations_handles_empty_findings(tmp_path: Path):
    """No findings → no violations, no producer scan triggered. Guards
    against the regression where the empty-input path crashes on the
    later axis-emitter dispatch."""
    errors = find_zero_producer_violations(tmp_path, [])
    assert errors == []


def test_zero_producer_violations_distinguishes_axes(tmp_path: Path):
    """A DEPRECATE-NO-FOLD on Symbol.kind axis must not flag an
    Edge.evidence_type producer that happens to use the same string,
    and vice versa. This catches the cross-axis false-positive class."""
    audits = tmp_path / "docs" / "audits"
    audits.mkdir(parents=True)
    md = audits / "0096-test.md"
    md.write_text(f"""# Test audit

## Verdicts

```yaml
kind: audit_verdicts
axis: {AXIS_SYMBOL_KIND}
verdicts:
  - value: same_string_xyz
    verdict: DEPRECATE-NO-FOLD
    fold_target: null
    status: PRELIM_RESOLVED
    diagnostic_test:
      cmd: "true"
      expect: exit_code:0
    rationale: "Dead Symbol.kind vocabulary."
```
""")
    findings = parse_audit_findings(md)

    pkg_dir = tmp_path / "packages" / "fake" / "src"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "leak.py").write_text(
        'from foo import Edge\n'
        'def emit():\n'
        '    return Edge(evidence_type="same_string_xyz", id="x")\n'
    )

    errors = find_zero_producer_violations(tmp_path, [findings])
    assert errors == []


def test_live_tree_deprecate_no_fold_has_zero_producers():
    """Walks the live ``docs/audits/`` tree, collects every
    DEPRECATE-NO-FOLD verdict across the three registered axes
    (``Symbol.kind``, ``Edge.edge_type``, ``Edge.evidence_type``), and
    asserts no producer call site emits the value via literal kwarg or
    assignment-form-to-Name.

    This is the regression guard called for in
    ``~/hypergumbo_lab_notebook/notebookjournal_05072026_0316.md``
    §"High-value (file as tracker items) #3" — closes the gap demonstrated
    by Wave 6 PR 4 (theorem/inductive/message reclassification) where
    DEPRECATE-NO-FOLD verdicts shipped while real producers existed.
    Promoted from the smoke check that landed alongside this regression
    guard's machinery in PR #189 (WI-viluk) once the verdict-correctness
    re-audit on 2026-05-07 absorbed the two leaks the new assertion
    surfaced (`reference` at swift_objc.py:167 from Wave 5;
    `import` at wasm_bindgen.py:266 from Wave 6 PR 3).

    Coverage gap, by design: helper-call indirection (e.g.
    ``add_symbol(node, name, "<value>")``), f-string interpolation, and
    dict-subscript-target assignment are not enumerated here; the
    Fundamental Concept Audit playbook §"Step 4.5" requires manual greps
    for those at audit-write time. WI-nubuv ext B / ext C are the
    structural backstops.

    Skips when ``docs/audits/`` is absent (isolated package run).
    """
    repo_root = Path(__file__).resolve().parents[3]
    docs = find_audit_findings_docs(repo_root)
    if not docs:
        pytest.skip("docs/audits/ not present (isolated package run)")

    findings_list = [parse_audit_findings(md) for md in docs]
    errors = find_zero_producer_violations(repo_root, findings_list)
    assert not errors, (
        "DEPRECATE-NO-FOLD verdicts contradicted by live producer "
        "emit sites:\n" + "\n".join(errors)
    )


# --- README index sync regression guard ---


def _make_audit_doc(
    audits: Path,
    nn: str,
    rows: list[tuple[str, str, str]],
    axis: str = AXIS_SYMBOL_KIND,
) -> Path:
    """Write an audit-findings doc with rows of (value, verdict, status)."""
    rows_yaml = "\n".join(
        f"""  - value: {value}
    verdict: {verdict}
    fold_target: {'"target"' if verdict == VERDICT_FOLD else 'null'}
    status: {status}
    diagnostic_test:
      cmd: "true"
      expect: exit_code:0
    rationale: "Test row."
"""
        for value, verdict, status in rows
    )
    md = audits / f"{nn}-test.md"
    md.write_text(f"""# Test audit {nn}

## Verdicts

```yaml
kind: audit_verdicts
axis: {axis}
verdicts:
{rows_yaml}
```
""")
    return md


def _make_readme(audits: Path, rows: list[tuple[str, str]]) -> None:
    """Write a README.md with index rows of (NN, status_cell)."""
    body_lines = [
        "# Audit-Findings Documents",
        "",
        "## Index",
        "",
        "| ID | Title | Axis | Status |",
        "|----|-------|------|--------|",
    ]
    for nn, status_cell in rows:
        body_lines.append(
            f"| [{nn}]({nn}-test.md) | Title | `Symbol.kind` | {status_cell} |",
        )
    (audits / "README.md").write_text("\n".join(body_lines) + "\n")


def test_readme_drift_clean_when_explicit_counts_match(tmp_path: Path):
    audits = tmp_path / "docs" / "audits"
    audits.mkdir(parents=True)
    md = _make_audit_doc(
        audits, "0099",
        [("a", "CANONICAL", "RESOLVED"),
         ("b", "CANONICAL", "RESOLVED"),
         ("c", "FOLD", "PRELIM_RESOLVED")],
    )
    _make_readme(audits, [("0099", "Mixed (2 RESOLVED, 1 PRELIM_RESOLVED)")])
    findings = parse_audit_findings(md)
    assert find_readme_index_drift(tmp_path, [findings]) == []


def test_readme_drift_flags_count_mismatch(tmp_path: Path):
    audits = tmp_path / "docs" / "audits"
    audits.mkdir(parents=True)
    md = _make_audit_doc(
        audits, "0098",
        [("a", "CANONICAL", "RESOLVED"),
         ("b", "FOLD", "PRELIM_RESOLVED"),
         ("c", "FOLD", "PRELIM_RESOLVED")],
    )
    _make_readme(audits, [("0098", "Mixed (2 RESOLVED, 1 PRELIM_RESOLVED)")])
    findings = parse_audit_findings(md)
    errors = find_readme_index_drift(tmp_path, [findings])
    assert len(errors) == 1
    assert "Mixed (2 RESOLVED, 1 PRELIM_RESOLVED)" in errors[0]
    assert "{'RESOLVED': 1, 'PRELIM_RESOLVED': 2}" in errors[0]


def test_readme_drift_clean_when_all_resolved_marker_matches(tmp_path: Path):
    audits = tmp_path / "docs" / "audits"
    audits.mkdir(parents=True)
    md = _make_audit_doc(
        audits, "0097",
        [("a", "CANONICAL", "RESOLVED"),
         ("b", "CANONICAL", "RESOLVED")],
    )
    _make_readme(audits, [("0097", "All RESOLVED")])
    findings = parse_audit_findings(md)
    assert find_readme_index_drift(tmp_path, [findings]) == []


def test_readme_drift_clean_when_all_resolved_with_trailing_prose(tmp_path: Path):
    """Trailing prose (e.g., a relocation date) after the All marker
    is allowed — only the marker substring needs to be present."""
    audits = tmp_path / "docs" / "audits"
    audits.mkdir(parents=True)
    md = _make_audit_doc(
        audits, "0096",
        [("a", "CANONICAL", "RESOLVED")],
    )
    _make_readme(audits, [("0096", "All RESOLVED at relocation (2026-05-02)")])
    findings = parse_audit_findings(md)
    assert find_readme_index_drift(tmp_path, [findings]) == []


def test_readme_drift_flags_missing_all_marker(tmp_path: Path):
    audits = tmp_path / "docs" / "audits"
    audits.mkdir(parents=True)
    md = _make_audit_doc(
        audits, "0095",
        [("a", "FOLD", "PRELIM_RESOLVED")],
    )
    # Cell says wrong marker
    _make_readme(audits, [("0095", "All RESOLVED")])
    findings = parse_audit_findings(md)
    errors = find_readme_index_drift(tmp_path, [findings])
    assert len(errors) == 1
    assert "PRELIM_RESOLVED" in errors[0]


def test_readme_drift_flags_no_count_marker_with_mixed_yaml(tmp_path: Path):
    """When the YAML has mixed statuses, the README cell must carry
    explicit counts — a bare 'All <X>' marker is insufficient."""
    audits = tmp_path / "docs" / "audits"
    audits.mkdir(parents=True)
    md = _make_audit_doc(
        audits, "0094",
        [("a", "CANONICAL", "RESOLVED"),
         ("b", "FOLD", "PRELIM_RESOLVED")],
    )
    _make_readme(audits, [("0094", "All RESOLVED")])
    findings = parse_audit_findings(md)
    errors = find_readme_index_drift(tmp_path, [findings])
    assert len(errors) == 1
    assert "no explicit counts but YAML has mixed statuses" in errors[0]


def test_readme_drift_flags_doc_missing_from_index(tmp_path: Path):
    audits = tmp_path / "docs" / "audits"
    audits.mkdir(parents=True)
    md = _make_audit_doc(
        audits, "0093",
        [("a", "CANONICAL", "RESOLVED")],
    )
    _make_readme(audits, [("0099", "All RESOLVED")])
    findings = parse_audit_findings(md)
    errors = find_readme_index_drift(tmp_path, [findings])
    assert len(errors) == 1
    assert "not listed in" in errors[0]
    assert "0093" in errors[0]


def test_readme_drift_handles_decorative_prose_around_counts(tmp_path: Path):
    """Audit 0007's 'All rows resolved (57 RESOLVED, 3 PRELIM_RESOLVED)'
    shape is allowed: the explicit counts win over the decorative
    'All rows resolved' prefix."""
    audits = tmp_path / "docs" / "audits"
    audits.mkdir(parents=True)
    rows = (
        [("v" + str(i), "CANONICAL", "RESOLVED") for i in range(57)]
        + [("p" + str(i), "FOLD", "PRELIM_RESOLVED") for i in range(3)]
    )
    md = _make_audit_doc(audits, "0092", rows)
    _make_readme(
        audits, [("0092", "All rows resolved (57 RESOLVED, 3 PRELIM_RESOLVED)")],
    )
    findings = parse_audit_findings(md)
    assert find_readme_index_drift(tmp_path, [findings]) == []


def test_live_tree_readme_index_in_sync_with_audit_docs():
    """Walks the live ``docs/audits/`` tree and asserts the README index
    Status column agrees with each doc's verdict YAML row counts.

    Closes the README-index-drift gap called for in
    ``~/hypergumbo_lab_notebook/notebookjournal_05072026_0316.md``
    §"High-value (file as tracker items) #4". Wave 6 PR 5 demonstrated
    the recurrence pattern (four index rows fell behind their per-doc
    Status fields after Wave 5 + Wave 6 advances landed); this test
    blocks the next recurrence at every-commit time.

    Skips when ``docs/audits/`` is absent (isolated package run).
    """
    repo_root = Path(__file__).resolve().parents[3]
    docs = find_audit_findings_docs(repo_root)
    if not docs:
        pytest.skip("docs/audits/ not present (isolated package run)")

    findings_list = [parse_audit_findings(md) for md in docs]
    errors = find_readme_index_drift(repo_root, findings_list)
    assert not errors, (
        "README index Status column has drifted from audit-findings "
        "verdict YAMLs:\n" + "\n".join(errors)
    )
