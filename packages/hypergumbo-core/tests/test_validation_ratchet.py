# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the shrink-only validation-matrix ratchet primitive (WI-jigup)."""
from __future__ import annotations

from hypergumbo_core.validation_ratchet import matrix_breaches, violation_matrix


def _report(*cells: tuple[str, str, int]):
    """Build a minimal validation_report dict from (class, severity, count)."""
    violations = []
    for cls, sev, count in cells:
        for _ in range(count):
            violations.append({"validator_class": cls, "severity": sev})
    return {"violations": violations}


def test_violation_matrix_empty_report() -> None:
    assert violation_matrix({}) == {}
    assert violation_matrix({"violations": []}) == {}


def test_violation_matrix_counts_by_class_and_severity() -> None:
    rep = _report(("id_format", "warning", 3), ("cross_field", "error", 1))
    assert violation_matrix(rep) == {
        "id_format|warning": 3,
        "cross_field|error": 1,
    }


def test_matrix_breaches_within_baseline_is_clean() -> None:
    rep = _report(("id_format", "warning", 11))
    assert matrix_breaches(rep, {"id_format|warning": 11}) == []


def test_matrix_breaches_below_baseline_is_a_legal_shrink() -> None:
    # 5 < baseline 11 — a shrink, not a breach.
    rep = _report(("id_format", "warning", 5))
    assert matrix_breaches(rep, {"id_format|warning": 11}) == []


def test_matrix_breaches_over_baseline_is_reported() -> None:
    rep = _report(("id_format", "warning", 12))
    breaches = matrix_breaches(rep, {"id_format|warning": 11})
    assert breaches == ["id_format|warning: 12 > baseline 11"]


def test_matrix_breaches_new_cell_defaults_to_zero_ceiling() -> None:
    # A class/severity absent from the baseline has ceiling 0 — any count trips.
    rep = _report(("cross_field", "warning", 1))
    breaches = matrix_breaches(rep, {"id_format|warning": 11})
    assert breaches == ["cross_field|warning: 1 > baseline 0"]


def test_matrix_breaches_empty_report_is_clean() -> None:
    assert matrix_breaches({}, {"id_format|warning": 11}) == []
