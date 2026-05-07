# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parser and validator for the audit-findings document format.

Audit-findings documents live at ``docs/audits/<NN>-<topic>.md`` and
record the per-value verdicts produced by applying the Fundamental
Concept Audit playbook to a specific scope (typically a family of
values on an axis declared by an ADR). They are filed separately from
ADRs (which carry load-bearing decisions) per the filing rubric in
``docs/adr/README.md``.

Each audit-findings doc has a fenced YAML block immediately following
a ``## Verdicts`` section heading. Both the heading and the in-block
``kind: audit_verdicts`` key are required so the parser can locate
the block from either direction (Q3 belt-and-suspenders).

The YAML block declares the axis it covers and a list of verdict
rows. Each row carries enough structure that its current state can
be checked against the live registry (the mechanical-check predicate
per row's ``status``). The ``diagnostic_test`` field is structured
as ``{cmd, expect}`` so that a future iteration can execute it and
assert the result; today the parser only validates the structural
shape.

The three-state lifecycle (UNRESOLVED / PRELIM_RESOLVED / RESOLVED)
is orthogonal to bakeoff validation, which lives on the migration's
tracker item via the ``awaits_bakeoff_validation`` tag.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Iterable, Protocol

import yaml

from hypergumbo_core.edge_types import (
    AXIS_ENDPOINT_SHAPE as _EDGE_TYPE_AXIS_ENDPOINT_SHAPE,
    AXIS_RELATIONSHIP,
    EDGE_TYPES,
)
from hypergumbo_core.evidence_types import (
    AXIS_ENDPOINT_SHAPE as _EVIDENCE_TYPE_AXIS_ENDPOINT_SHAPE,
    AXIS_INFERENCE_PATHWAY,
    EVIDENCE_TYPES,
)
from hypergumbo_core.symbol_kinds import (
    AXIS_ENDPOINT_SHAPE as _SYMBOL_KIND_AXIS_ENDPOINT_SHAPE,
    AXIS_LANGUAGE_CONSTRUCT,
    SYMBOL_KINDS,
)


class _AxisSpec(Protocol):
    """Duck-type protocol satisfied by EdgeTypeSpec / SymbolKindSpec / EvidenceTypeSpec.

    The validator's mechanical-check predicates only consume ``name`` and
    ``axis`` from a spec, so a structural protocol lets one validator
    function serve all three registries without the dataclass types being
    a sealed sum.
    """

    @property
    def name(self) -> str: ...

    @property
    def axis(self) -> str: ...


# --- Allowed enums ---

VERDICT_CANONICAL: Final[str] = "CANONICAL"
VERDICT_FOLD: Final[str] = "FOLD"
VERDICT_DEPRECATE_NO_FOLD: Final[str] = "DEPRECATE-NO-FOLD"
VALID_VERDICTS: Final[frozenset[str]] = frozenset({
    VERDICT_CANONICAL,
    VERDICT_FOLD,
    VERDICT_DEPRECATE_NO_FOLD,
})

STATUS_UNRESOLVED: Final[str] = "UNRESOLVED"
STATUS_PRELIM_RESOLVED: Final[str] = "PRELIM_RESOLVED"
STATUS_RESOLVED: Final[str] = "RESOLVED"
VALID_STATUSES: Final[frozenset[str]] = frozenset({
    STATUS_UNRESOLVED,
    STATUS_PRELIM_RESOLVED,
    STATUS_RESOLVED,
})

EXPECT_EMPTY: Final[str] = "empty"
EXPECT_NONEMPTY: Final[str] = "nonempty"
_EXIT_CODE_RE: Final[re.Pattern[str]] = re.compile(r"^exit_code:\d+$")

REQUIRED_KIND: Final[str] = "audit_verdicts"
AXIS_EDGE_EDGE_TYPE: Final[str] = "Edge.edge_type"
AXIS_SYMBOL_KIND: Final[str] = "Symbol.kind"
AXIS_EDGE_EVIDENCE_TYPE: Final[str] = "Edge.evidence_type"


@dataclass(frozen=True)
class _AxisRegistry:
    """Per-axis binding for the validator's mechanical-check predicates.

    Each declared axis pairs (a) the live registry tuple it covers with
    (b) the axis-name string that registry uses to mark *canonical*
    rows and (c) the axis-name string it uses for the deprecation-window
    *endpoint_shape* axis. Registries differ on (b) — Edge.edge_type
    canonicals live on ``relationship``; Symbol.kind canonicals live on
    ``language_construct``; Edge.evidence_type canonicals live on
    ``inference_pathway`` — so the predicates parameterise over these
    instead of hard-coding ``relationship``.
    """

    specs: tuple[_AxisSpec, ...]
    canonical_axis: str
    endpoint_axis: str


# Mapping from axis identifier (declared in the YAML block) to the
# per-axis binding. Each ADR that declares a multi-value field axis
# (ADR-0023 Edge.edge_type, ADR-0027 Symbol.kind, ADR-0028
# Edge.evidence_type) lands its registry import + entry here so the
# audit-findings format can carry per-value verdicts on that axis.
_REGISTRIES: Final[dict[str, _AxisRegistry]] = {
    AXIS_EDGE_EDGE_TYPE: _AxisRegistry(
        specs=EDGE_TYPES,
        canonical_axis=AXIS_RELATIONSHIP,
        endpoint_axis=_EDGE_TYPE_AXIS_ENDPOINT_SHAPE,
    ),
    AXIS_SYMBOL_KIND: _AxisRegistry(
        specs=SYMBOL_KINDS,
        canonical_axis=AXIS_LANGUAGE_CONSTRUCT,
        endpoint_axis=_SYMBOL_KIND_AXIS_ENDPOINT_SHAPE,
    ),
    AXIS_EDGE_EVIDENCE_TYPE: _AxisRegistry(
        specs=EVIDENCE_TYPES,
        canonical_axis=AXIS_INFERENCE_PATHWAY,
        endpoint_axis=_EVIDENCE_TYPE_AXIS_ENDPOINT_SHAPE,
    ),
}

VERDICTS_HEADING_RE: Final[re.Pattern[str]] = re.compile(
    r"^##\s+Verdicts\s*$", re.MULTILINE,
)
_FENCED_YAML_RE: Final[re.Pattern[str]] = re.compile(
    r"```ya?ml\s*\n(.*?)\n```", re.DOTALL,
)


# --- Data model ---

@dataclass(frozen=True)
class DiagnosticTest:
    cmd: str
    expect: str


@dataclass(frozen=True)
class VerdictRow:
    value: str
    verdict: str
    fold_target: str | None
    status: str
    diagnostic_test: DiagnosticTest
    rationale: str


@dataclass(frozen=True)
class AuditFindings:
    path: Path
    axis: str
    verdicts: tuple[VerdictRow, ...]


class AuditFindingsError(ValueError):
    """Raised when an audit-findings doc is malformed."""


# --- Parsing ---

def parse_audit_findings(md_path: Path) -> AuditFindings:
    """Parse an audit-findings markdown file.

    Raises AuditFindingsError if the doc is malformed (missing
    heading, missing fenced YAML block after the heading, missing
    or wrong ``kind`` key, missing required row fields, invalid
    enums, etc.).
    """
    text = md_path.read_text()

    heading_match = VERDICTS_HEADING_RE.search(text)
    if heading_match is None:
        raise AuditFindingsError(
            f"{md_path}: missing required '## Verdicts' section heading",
        )

    after_heading = text[heading_match.end():]
    yaml_match = _FENCED_YAML_RE.search(after_heading)
    if yaml_match is None:
        raise AuditFindingsError(
            f"{md_path}: '## Verdicts' heading not followed by a "
            "fenced ```yaml ... ``` block",
        )

    block_text = yaml_match.group(1)
    try:
        data = yaml.safe_load(block_text)
    except yaml.YAMLError as exc:
        raise AuditFindingsError(
            f"{md_path}: verdicts YAML failed to parse: {exc}",
        ) from exc

    if not isinstance(data, dict):
        raise AuditFindingsError(
            f"{md_path}: verdicts YAML block must be a mapping at "
            f"the top level, got {type(data).__name__}",
        )

    if data.get("kind") != REQUIRED_KIND:
        raise AuditFindingsError(
            f"{md_path}: verdicts YAML must declare 'kind: "
            f"{REQUIRED_KIND}' at the top level",
        )

    axis = data.get("axis")
    if not isinstance(axis, str) or not axis:
        raise AuditFindingsError(
            f"{md_path}: verdicts YAML must declare an 'axis' "
            "string at the top level",
        )

    raw_verdicts = data.get("verdicts")
    if not isinstance(raw_verdicts, list) or not raw_verdicts:
        raise AuditFindingsError(
            f"{md_path}: verdicts YAML must declare a non-empty "
            "'verdicts' list",
        )

    verdicts = tuple(_parse_row(md_path, row) for row in raw_verdicts)

    return AuditFindings(path=md_path, axis=axis, verdicts=verdicts)


def _parse_row(md_path: Path, row: Any) -> VerdictRow:
    if not isinstance(row, dict):
        raise AuditFindingsError(
            f"{md_path}: each verdicts row must be a mapping, got "
            f"{type(row).__name__}",
        )

    for key in ("value", "verdict", "status", "diagnostic_test", "rationale"):
        if key not in row:
            raise AuditFindingsError(
                f"{md_path}: row {row!r} missing required key '{key}'",
            )

    value = row["value"]
    if not isinstance(value, str) or not value:
        raise AuditFindingsError(
            f"{md_path}: row 'value' must be a non-empty string, "
            f"got {value!r}",
        )

    verdict = row["verdict"]
    if verdict not in VALID_VERDICTS:
        raise AuditFindingsError(
            f"{md_path}: row {value!r} has invalid verdict "
            f"{verdict!r}; must be one of {sorted(VALID_VERDICTS)}",
        )

    status = row["status"]
    if status not in VALID_STATUSES:
        raise AuditFindingsError(
            f"{md_path}: row {value!r} has invalid status "
            f"{status!r}; must be one of {sorted(VALID_STATUSES)}",
        )

    fold_target = row.get("fold_target")
    if verdict == VERDICT_FOLD and not (
        isinstance(fold_target, str) and fold_target
    ):
        raise AuditFindingsError(
            f"{md_path}: row {value!r} has verdict FOLD but no "
            "non-empty 'fold_target' string",
        )
    if verdict != VERDICT_FOLD and fold_target is not None:
        raise AuditFindingsError(
            f"{md_path}: row {value!r} has verdict {verdict} but a "
            "non-null 'fold_target'; only FOLD rows may have one",
        )

    rationale = row["rationale"]
    if not isinstance(rationale, str) or not rationale:
        raise AuditFindingsError(
            f"{md_path}: row {value!r} must have a non-empty "
            "'rationale' string",
        )

    diagnostic_test = _parse_diagnostic_test(md_path, value, row["diagnostic_test"])

    return VerdictRow(
        value=value,
        verdict=verdict,
        fold_target=fold_target if verdict == VERDICT_FOLD else None,
        status=status,
        diagnostic_test=diagnostic_test,
        rationale=rationale,
    )


def _parse_diagnostic_test(
    md_path: Path, value: str, raw: Any,
) -> DiagnosticTest:
    if not isinstance(raw, dict):
        raise AuditFindingsError(
            f"{md_path}: row {value!r} 'diagnostic_test' must be a "
            f"mapping with 'cmd' and 'expect' keys, got "
            f"{type(raw).__name__}",
        )
    cmd = raw.get("cmd")
    expect = raw.get("expect")
    if not isinstance(cmd, str) or not cmd:
        raise AuditFindingsError(
            f"{md_path}: row {value!r} 'diagnostic_test.cmd' must be "
            "a non-empty string",
        )
    if not isinstance(expect, str) or not _is_valid_expect(expect):
        raise AuditFindingsError(
            f"{md_path}: row {value!r} 'diagnostic_test.expect' must "
            f"be one of '{EXPECT_EMPTY}', '{EXPECT_NONEMPTY}', or "
            "'exit_code:N' (N a non-negative integer); got "
            f"{expect!r}",
        )
    return DiagnosticTest(cmd=cmd, expect=expect)


def _is_valid_expect(value: str) -> bool:
    if value in (EXPECT_EMPTY, EXPECT_NONEMPTY):
        return True
    return bool(_EXIT_CODE_RE.match(value))


# --- Validation against the live registry ---

def validate_against_registry(findings: AuditFindings) -> list[str]:
    """Return a list of human-readable error strings.

    Each row's ``status`` must agree with the registry's current
    state per the mechanical-check predicates defined in
    ``docs/audits/README.md``:

    - RESOLVED + verdict CANONICAL  → value present in registry as relationship.
    - RESOLVED + verdict FOLD or DEPRECATE-NO-FOLD → value absent from registry.
    - PRELIM_RESOLVED                → value present in registry as endpoint_shape.
    - UNRESOLVED                     → value present in registry (any axis).

    An empty list means all rows agree with the registry.
    """
    if findings.axis not in _REGISTRIES:
        return [
            f"{findings.path}: declared axis {findings.axis!r} has no "
            f"registry mapping; known axes: {sorted(_REGISTRIES)}",
        ]

    registry = _REGISTRIES[findings.axis]
    by_name = {spec.name: spec for spec in registry.specs}
    errors: list[str] = []

    for row in findings.verdicts:
        spec = by_name.get(row.value)
        in_registry = spec is not None

        if row.status == STATUS_RESOLVED:
            if row.verdict == VERDICT_CANONICAL:
                if not (in_registry and spec.axis == registry.canonical_axis):
                    errors.append(
                        f"{findings.path}: row {row.value!r} verdict "
                        f"CANONICAL + status RESOLVED requires the "
                        f"value to be in the registry on the "
                        f"{registry.canonical_axis!r} axis; current state: "
                        f"{_describe_state(spec)}",
                    )
            else:
                if in_registry:
                    errors.append(
                        f"{findings.path}: row {row.value!r} verdict "
                        f"{row.verdict} + status RESOLVED requires "
                        f"the value to be ABSENT from the registry; "
                        f"currently present on axis {spec.axis!r}",
                    )
        elif row.status == STATUS_PRELIM_RESOLVED:
            if not (in_registry and spec.axis == registry.endpoint_axis):
                errors.append(
                    f"{findings.path}: row {row.value!r} status "
                    f"PRELIM_RESOLVED requires the value to be in "
                    f"the registry on the {registry.endpoint_axis!r} axis; "
                    f"current state: {_describe_state(spec)}",
                )
        else:  # UNRESOLVED
            if not in_registry:
                errors.append(
                    f"{findings.path}: row {row.value!r} status "
                    f"UNRESOLVED requires the value to be in the "
                    f"registry; currently absent",
                )

    return errors


def _describe_state(spec: _AxisSpec | None) -> str:
    if spec is None:
        return "absent from registry"
    return f"present on axis {spec.axis!r}"


# --- DEPRECATE-NO-FOLD producer-existence regression guard ---

def find_zero_producer_violations(
    repo_root: Path,
    findings_list: Iterable[AuditFindings],
) -> list[str]:
    """For every DEPRECATE-NO-FOLD verdict, assert no producer emits the value.

    The Fundamental Concept Audit playbook §"Step 4.5 — Indirection-aware
    producer trace" requires that any verdict claiming "no producer"
    (DEPRECATE-NO-FOLD) be backed by an indirection-aware grep at audit-
    write time. This function is the corresponding **regression guard** at
    every-commit time: if a producer is added later for a value flagged
    DEPRECATE-NO-FOLD without updating the verdict, this function returns
    a violation entry naming the file:line emit sites.

    The producer enumeration covers the literal-kwarg + assignment-form-to-
    Name shapes (Step 4.5 shapes #1 and #3). The remaining shapes — helper-
    call indirection (#2), f-string interpolation (#4), and dict-subscript-
    target assignment (#5) — are not enumerated here; the playbook's
    per-value manual grep at audit-write time is the compensating control
    for those, with WI-nubuv ext B / ext C planned as the structural
    backstops.

    Returns a list of human-readable error strings (one entry per
    DEPRECATE-NO-FOLD value that has at least one producer emit site).
    Empty list means all DEPRECATE-NO-FOLD verdicts are intact for the
    shapes this guard covers.
    """
    from hypergumbo_core.producer_coherence import (
        find_emitted_edge_types,
        find_emitted_evidence_types,
        find_emitted_symbol_kinds,
    )

    deprecate_by_axis: dict[str, dict[str, Path]] = {}
    for findings in findings_list:
        if findings.axis not in _REGISTRIES:
            continue
        for row in findings.verdicts:
            if row.verdict != VERDICT_DEPRECATE_NO_FOLD:
                continue
            deprecate_by_axis.setdefault(findings.axis, {})[row.value] = findings.path

    if not deprecate_by_axis:
        return []

    axis_emitters = {
        AXIS_SYMBOL_KIND: find_emitted_symbol_kinds,
        AXIS_EDGE_EVIDENCE_TYPE: find_emitted_evidence_types,
        AXIS_EDGE_EDGE_TYPE: find_emitted_edge_types,
    }

    errors: list[str] = []
    for axis, deprecated_values in deprecate_by_axis.items():
        emit_sites = axis_emitters[axis](repo_root)
        for value, source_doc in sorted(deprecated_values.items()):
            sites = emit_sites.get(value)
            if sites:
                errors.append(
                    f"{source_doc}: row {value!r} verdict DEPRECATE-NO-FOLD "
                    f"on axis {axis} contradicts {len(sites)} producer "
                    f"emit site(s) found via literal-kwarg / assignment-form "
                    f"trace: {', '.join(sites)}",
                )

    return errors


# --- Tree walker ---

def find_audit_findings_docs(repo_root: Path) -> list[Path]:
    """Return all audit-findings .md files under ``docs/audits/``.

    Excludes ``README.md`` (the format spec, not an audit). Returns
    an empty list if ``docs/audits/`` does not exist (e.g., the
    package is being tested in isolation outside the repo).
    """
    audits_dir = repo_root / "docs" / "audits"
    if not audits_dir.is_dir():
        return []
    return sorted(
        p for p in audits_dir.glob("*.md") if p.name != "README.md"
    )
