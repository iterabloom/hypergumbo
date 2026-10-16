# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static audit of explicit ``Edge.create(confidence=...)`` sites (WI-vosif).

ADR-0039 derives an analyzer edge's confidence from its inference pathway
(ruling 1), with a resolver's match quality interpolated inside the pathway
band (ruling 6), and judges a linker edge by the spec §12 linker range
(ruling 7). The runtime band check (``spec_validator._check_confidence_range``)
only sees the edges a corpus happens to exercise: on 2026-10-15 the
self-survey reached 2 of 138 risky producer sites, and the JS/TS scope-bound
0.90 went unnoticed for twelve days. This module reads the producers instead.

How it works. Every ``Edge.create(...)`` call under ``packages/*/src`` that
passes an explicit ``confidence=`` is classified:

- *Producer category* is read from the file's place in the tree: modules under
  ``hypergumbo_core/linkers/`` are linkers, everything else is an analyzer.
  That is the static counterpart of ruling 7's runtime rule (the producing
  pass is a registered linker); every registered linker lives there.
- *Evidence types* are read from the ``evidence_type=`` argument when it is a
  string literal or a conditional between literals; an omitted argument is
  ``Edge.create``'s default, ``ast_call_direct``; anything else is unknown.
- *The value* is a literal, a conditional between literals, or computed.

Findings, each with a stable key (path, kind, evidence types, the value's
source text) so the baseline survives line drift:

``literal_out_of_band``
    A literal (or a literal branch) outside the band: the analyzer band of a
    seeded pathway, or the linker range 0.40-0.95.
``computed_uncommented``
    An analyzer site on a seeded pathway that computes its value (a variable,
    an expression, or a conditional between literals) and does not carry ``# ADR-0039 caller-valued: <reason>`` on a line of the call or in the
    comment block directly above it. Ruling 6 says such a site either passes nothing, passes a
    ``resolution_score``, or says why it cannot.

Linker sites that compute their value, and analyzer sites on unseeded or
unknown pathways, are not findings: a linker's match quality has no static
band, and an unseeded pathway has no band at all. The runtime check covers
the former.

The gate is shrink-only (``.ci/confidence-sites-baseline.json``): a finding
not in the baseline fails, and so does a baseline entry that no longer
occurs, so the file can only shrink. ``scripts/check-confidence-sites`` is the
thin CLI; ``tests/test_confidence_sites.py::test_live_tree_matches_baseline``
is the CI gate.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

from hypergumbo_core.confidence import LINKER_CONFIDENCE_RANGE, confidence_within_band
from hypergumbo_core.evidence_types import find_evidence_type

#: The justification marker a caller-valued analyzer site carries.
CALLER_VALUED_MARKER = "# ADR-0039 caller-valued:"

#: Path fragment that marks a linker module.
_LINKER_DIR = "hypergumbo_core/linkers/"

_DEFAULT_EVIDENCE_TYPE = "ast_call_direct"

BASELINE_PATH = Path(".ci/confidence-sites-baseline.json")


@dataclass(frozen=True, order=True)
class ConfidenceSiteFinding:
    """One out-of-contract ``confidence=`` site.

    ``kind`` is ``literal_out_of_band`` or ``computed_uncommented`` (see the
    module docstring); ``key`` omits the line so a baseline survives edits
    elsewhere in the file.
    """

    path: str
    line: int
    kind: str
    evidence_types: tuple[str, ...]
    value: str

    @property
    def key(self) -> str:
        return f"{self.path}|{self.kind}|{','.join(self.evidence_types)}|{self.value}"

    def render(self) -> str:
        return (
            f"{self.path}:{self.line}: {self.kind} "
            f"[{', '.join(self.evidence_types)}] confidence={self.value}"
        )


def _literal_number(node: ast.expr) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    return None


def _literal_branches(node: ast.expr) -> list[float] | None:
    """Return the numbers of a literal or of a conditional between literals."""
    value = _literal_number(node)
    if value is not None:
        return [value]
    if isinstance(node, ast.IfExp):
        body, orelse = _literal_branches(node.body), _literal_branches(node.orelse)
        if body is not None and orelse is not None:
            return body + orelse
    return None


def _evidence_types(call: ast.Call) -> tuple[str, ...] | None:
    """Return the call's evidence types, or ``None`` when not statically known."""
    for kw in call.keywords:
        if kw.arg != "evidence_type":
            continue
        if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return (kw.value.value,)
        if isinstance(kw.value, ast.IfExp):
            names = [
                p.value for p in (kw.value.body, kw.value.orelse)
                if isinstance(p, ast.Constant) and isinstance(p.value, str)
            ]
            if len(names) == 2:
                return tuple(sorted(set(names)))
        return None
    return (_DEFAULT_EVIDENCE_TYPE,)


def _is_seeded(evidence_type: str) -> bool:
    spec = find_evidence_type(evidence_type)
    return spec is not None and spec.base_confidence is not None


def _in_contract(value: float, evidence_type: str, *, linker: bool) -> bool:
    if linker:
        low, high = LINKER_CONFIDENCE_RANGE
        return low - 1e-9 <= value <= high + 1e-9
    return confidence_within_band(evidence_type, value)


def _has_marker(lines: list[str], call: ast.Call) -> bool:
    """True when the marker sits in the call or the comment block just above it."""
    first = call.lineno - 1
    while first > 0 and lines[first - 1].lstrip().startswith("#"):
        first -= 1
    last = call.end_lineno or call.lineno
    return any(CALLER_VALUED_MARKER in ln for ln in lines[first:last])


def _is_edge_create(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "create"
        and isinstance(func.value, ast.Name)
        and func.value.id == "Edge"
    )


def scan_file(path: Path, rel: str) -> list[ConfidenceSiteFinding]:
    """Return the findings for one producer module."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - the tree is valid Python
        return []
    lines = source.splitlines()
    linker = _LINKER_DIR in rel
    findings: list[ConfidenceSiteFinding] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_edge_create(node)):
            continue
        conf = next((kw.value for kw in node.keywords if kw.arg == "confidence"), None)
        if conf is None or (isinstance(conf, ast.Constant) and conf.value is None):
            continue
        evs = _evidence_types(node)
        text = ast.unparse(conf)
        literals = _literal_branches(conf)
        if literals is not None:
            if linker:
                bad = any(not _in_contract(v, "", linker=True) for v in literals)
            else:
                bad = evs is not None and any(
                    not _in_contract(v, ev, linker=False)
                    for v in literals for ev in evs
                )
            if bad:
                findings.append(ConfidenceSiteFinding(
                    rel, node.lineno, "literal_out_of_band", evs or ("?",), text,
                ))
                continue
            if len(literals) == 1:
                continue
            # A conditional between literals is a branch-tier value: in band,
            # but still a choice ruling 6 asks the producer to justify.
        if linker or evs is None or not any(_is_seeded(ev) for ev in evs):
            continue
        if not _has_marker(lines, node):
            findings.append(ConfidenceSiteFinding(
                rel, node.lineno, "computed_uncommented", evs, text,
            ))
    return findings


def scan_tree(repo_root: Path) -> list[ConfidenceSiteFinding]:
    """Return every finding under ``packages/*/src``, sorted."""
    findings: list[ConfidenceSiteFinding] = []
    for path in sorted(repo_root.glob("packages/*/src/**/*.py")):
        findings.extend(scan_file(path, path.relative_to(repo_root).as_posix()))
    return sorted(findings)


def load_baseline(repo_root: Path) -> list[str]:
    """Return the baseline's keys (empty when the file is absent)."""
    path = repo_root / BASELINE_PATH
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("findings", []))


def compare_to_baseline(
    findings: list[ConfidenceSiteFinding], baseline: list[str],
) -> tuple[list[ConfidenceSiteFinding], list[str]]:
    """Return ``(new findings, stale baseline keys)``; both empty means pass."""
    remaining = list(baseline)
    new: list[ConfidenceSiteFinding] = []
    for finding in findings:
        if finding.key in remaining:
            remaining.remove(finding.key)
        else:
            new.append(finding)
    return new, remaining


def baseline_document(findings: list[ConfidenceSiteFinding]) -> str:
    """Serialize findings as a baseline file."""
    return json.dumps(
        {
            "description": (
                "Shrink-only baseline for scripts/check-confidence-sites "
                "(ADR-0039 rulings 6-7, WI-vosif). Entries may only be removed."
            ),
            "findings": [f.key for f in findings],
        },
        indent=2,
    ) + "\n"
