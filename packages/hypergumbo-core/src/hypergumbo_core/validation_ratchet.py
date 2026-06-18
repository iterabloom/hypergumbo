# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shrink-only ratchet comparison for validation-report violation matrices.

The spec-validator gate (G1) is a **shrink-only ratchet**: the violation count
for a given ``(validator_class, severity)`` cell on a substrate may shrink below
its committed baseline but never grow. This module is the pure comparison
primitive shared by two enforcement loci (WI-jigup):

- the per-PR fixture ratchet test (``test_validation_report_empty.py``), which
  ratchets the four fixture substrates; and
- the full-suite **self-tree gate** (a step in ``.github/workflows/full-suite.yml``)
  that runs ``hypergumbo run .`` on hypergumbo's own tree and ratchets the
  resulting violation matrix against a committed baseline.

**Why a per-(class, severity) matrix and not a single lumped total.** A scalar
total permits *signed cancellation*: a ``+1`` regression in one warning class
can hide behind a ``-1`` shrink in another, so the total stays flat while a real
regression lands. A per-cell matrix cannot cancel — every class/severity cell is
ratcheted independently, so a regression in any one of them trips the gate. This
is the structural reason ~11 warning-severity checks were detection-*advisory*
(only feeding the lumped total): see WI-jigup. The matrix also catches a wholly
*new* cell (a class/severity that was previously absent), because an unbaselined
cell defaults to a ceiling of zero.

The functions here are intentionally side-effect-free and substrate-agnostic so
the same logic guards both the fixture corpus and the live self-tree.
"""
from __future__ import annotations

from collections import Counter
from typing import Any


def violation_matrix(report: dict[str, Any]) -> dict[str, int]:
    """Return the ``{"<validator_class>|<severity>": count}`` matrix for a
    behavior-map ``validation_report`` dict.

    A report with no ``violations`` (or an absent key) yields an empty matrix.
    """
    counts: Counter[str] = Counter(
        f"{v.get('validator_class')}|{v.get('severity')}"
        for v in report.get("violations", [])
    )
    return dict(counts)


def matrix_breaches(
    report: dict[str, Any], baselines: dict[str, int]
) -> list[str]:
    """Return human-readable breach descriptions where an observed
    ``(class|severity)`` cell **exceeds** its shrink-only baseline.

    A cell observed above ``baselines.get(cell, 0)`` is a breach — including a
    cell that is absent from ``baselines`` (a new class/severity, ceiling 0).
    A cell observed *below* its baseline is a legal shrink and is **not**
    reported (ratchet the baseline down separately). Returns ``[]`` when the
    report is within every baseline cell.
    """
    observed = violation_matrix(report)
    breaches: list[str] = []
    for cell in sorted(observed):
        count = observed[cell]
        allowed = baselines.get(cell, 0)
        if count > allowed:
            breaches.append(f"{cell}: {count} > baseline {allowed}")
    return breaches
