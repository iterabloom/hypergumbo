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
        f"axis: {AXIS_EDGE_EDGE_TYPE}", "axis: Symbol.kind",
    )
    md = _write(tmp_path, text)
    findings = parse_audit_findings(md)
    errors = validate_against_registry(findings)
    assert len(errors) == 1
    assert "no\n  registry mapping" in errors[0] or "no registry mapping" in errors[0]


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
