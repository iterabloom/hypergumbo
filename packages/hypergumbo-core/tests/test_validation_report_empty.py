# SPDX-License-Identifier: AGPL-3.0-or-later
"""validator:F1 / G1 — the spec-validator ratchet gate (WI-kafar, WI-himoj).

ADR-0033 named a CI gate ``tests/test_validation_report_empty.py`` that
"runs the self-analysis corpus and fails when ``validation_report.violations``
is non-empty." Two facts make the literal "assert empty" form impossible:

1. The corpus is *not* at zero violations (the validator surfaces real,
   currently-open defects — e.g. the INV-bazij stable_id collision rate on a
   small corpus, and non-canonical markdown section stable_ids under
   ``--include-docs``). An assert-empty gate would be permanently red.
2. A default-substrate-only gate lets flag-gated writer paths escape
   (``--frameworks all``, ``--include-docs``, ``--max-tier`` — WI-himoj).

So the realized gate is a **shrink-only, per-substrate RATCHET** over a
four-substrate matrix run against the multi-language ``schema-coverage-corpus``
fixture tree. For each substrate, the spec-validator violation total (and,
as a separate co-ratcheted dimension, the ADR-0023 §3 ``runtime_coherence``
offender count) may **shrink** below its committed baseline — ratchet the
baseline down when it does — but may never **grow**: a regression that adds
a violation trips CI rather than accumulating silently. This is the honest
realization of WI-kafar (lock in the state) + WI-himoj (exercise every
writer path including flag-gated ones).

The gate is the future HOST for the integrity predicates added in later
waves (dangling-endpoint, origin->analysis_runs FK, is_resolved<->dst
coherence, kind-slot round-trip, per-file stable_id uniqueness, …); each
such predicate, as it lands, shrinks the relevant substrate's baseline.

Vehicle notes:
* In-process ``run_behavior_map`` (not subprocess) so the analysis pipeline
  contributes to hypergumbo-core coverage.
* The fixture corpus (~5s/substrate) keeps the per-PR run well under the
  5-minute budget; the heavy self-analysis variant is future work for
  full-suite.yml ("fixture corpus per-PR; self-tree in full-suite").
* One analysis run per substrate, both dimensions checked off it — the
  orphan-audit anti-redundancy lesson (don't re-run analysis per assertion
  under pytest-xdist).
* A shrink-only ceiling alone passes vacuously if the corpus ever goes
  missing/empty (0 violations <= baseline). So the test also asserts a
  LIVENESS FLOOR: the corpus exists, is non-empty, and the run produced a
  non-trivial node count. The floor is deliberately low (it proves the
  substrate was analyzed, not its exact size) so it survives a
  reduced-analyzer CI environment while still catching a vacuous pass.
* On the current corpus only ``default`` and ``include_docs`` are
  distinguishing (``frameworks_all`` and ``max_tier_4`` produce byte-identical
  output — the fixtures carry no framework code and no tier>1 third-party
  symbols). The latter two are retained as FORWARD GUARDS for when the corpus
  grows to exercise those writer paths.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.runtime_coherence import (
    filter_by_allowlist,
    find_offenders,
    load_allowlist,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS = REPO_ROOT / "tests" / "fixtures" / "schema-coverage-corpus"
ALLOWLIST = REPO_ROOT / "docs" / "edge-type-runtime-allowlist.yaml"

# substrate name -> run_behavior_map kwargs that produce that writer-path
# substrate. The four substrates of the G1 matrix.
SUBSTRATES: dict[str, dict[str, Any]] = {
    "default": {},
    "frameworks_all": {"frameworks": "all"},
    "include_docs": {"include_docs": True},
    "max_tier_4": {"max_tier": 4},
}

# Shrink-only spec-validator baselines. Measured 2026-06-11 on the
# schema-coverage-corpus. A substrate's total may shrink below its baseline
# (ratchet it down here when it does) but never exceed it.
#
# What the non-zero baselines pin today:
#   * every substrate: 1 cross_field stable_id collision (INV-bazij; the
#     small corpus's 4/42 = 9.5% rate sits just over the 5% threshold).
#   * include_docs: +5 id_format violations on markdown README section
#     stable_ids (non-canonical ``markdown:README.md:...:section`` shape) —
#     exactly the flag-gated producer defects WI-himoj exists to surface.
#     Pinned here; fixed by later producer PRs that then ratchet this to 1.
#
# These counts are corpus-coupled in BOTH directions, not just by code: the
# default '1' is threshold-adjacent (4/42 = 9.5% vs the 5% INV-bazij
# threshold), and the include_docs id_format count tracks the README's
# section-header count one-for-one. So a *fixture* edit (e.g. adding a README
# heading, or changing the corpus symbol count) can move these numbers with no
# code change. On a red gate, re-measure before assuming a code regression;
# ratchet DOWN on a genuine shrink, never UP.
VALIDATION_BASELINES: dict[str, int] = {
    "default": 1,
    "frameworks_all": 1,
    "include_docs": 6,
    "max_tier_4": 1,
}

# Shrink-only runtime_coherence (ADR-0023 §3 edge-type partition coherence)
# baselines, co-ratcheted as a separate dimension. One un-allow-listed
# offender today on every substrate: the (file/rust -> external_symbol/rust)
# partition carries both ``imports`` and ``module_attr_ref``. Pinned in-test
# rather than allow-listed, to avoid a governance-touching ADR-0023
# allow-list amendment in this PR.
RUNTIME_COHERENCE_BASELINES: dict[str, int] = {
    "default": 1,
    "frameworks_all": 1,
    "include_docs": 1,
    "max_tier_4": 1,
}

# Liveness floor (NOT a ratchet): the smallest node count that still proves the
# corpus was found and analyzed. Deliberately far below the observed ~66, so a
# reduced-analyzer CI environment passes while a vacuous (missing/empty corpus,
# mis-resolved parents[3]) run — which yields a well-formed but empty report —
# fails loudly instead of going green on 0 <= baseline.
_LIVENESS_MIN_NODES = 10


def _run_substrate(substrate: str, out_path: Path) -> dict[str, Any]:
    """Run the corpus through ``run_behavior_map`` in-process for *substrate*
    and return the parsed behavior-map dict."""
    run_behavior_map(
        repo_root=CORPUS,
        out_path=out_path,
        include_sketch_precomputed=False,
        progress=False,
        **SUBSTRATES[substrate],
    )
    return json.loads(out_path.read_text())


@pytest.mark.parametrize("substrate", sorted(SUBSTRATES))
def test_substrate_within_ratchet_baseline(
    substrate: str, tmp_path: Path,
) -> None:
    """Shrink-only ratchet for one substrate, checking both dimensions off a
    single analysis run.

    Floor: the ``validation_report`` is present and well-formed (its
    by-class counter sums to the violations list length) — guards against
    the report silently disappearing from the artifact.

    Ratchet: neither the spec-validator violation total nor the
    runtime_coherence un-allow-listed offender count may exceed this
    substrate's committed baseline.
    """
    bm = _run_substrate(substrate, tmp_path / f"{substrate}.json")

    # --- Liveness floor: prove the substrate was actually analyzed. -------
    assert CORPUS.is_dir() and any(CORPUS.iterdir()), (
        f"corpus fixture missing or empty at {CORPUS}"
    )
    n_nodes = len(bm.get("nodes", []))
    assert n_nodes >= _LIVENESS_MIN_NODES, (
        f"[{substrate}] only {n_nodes} nodes (< {_LIVENESS_MIN_NODES}); the "
        "corpus was not analyzed — a shrink-only gate would pass vacuously"
    )

    # --- Structural floor: report present and internally consistent. -----
    report = bm.get("validation_report")
    assert report is not None, (
        f"[{substrate}] validation_report missing from the artifact"
    )
    assert "schema_version" in report, f"[{substrate}] no schema_version"
    by_class = report.get("violations_by_class", {})
    violations = report.get("violations", [])
    assert isinstance(violations, list)
    assert sum(by_class.values()) == len(violations), (
        f"[{substrate}] by-class counter {by_class} disagrees with "
        f"{len(violations)} violations"
    )

    # --- Shrink-only ratchet across both dimensions. ---------------------
    failures: list[str] = []

    total = sum(by_class.values())
    base = VALIDATION_BASELINES[substrate]
    if total > base:
        offending = [
            f"[{v.get('validator_class')}] {v.get('field_name')}: "
            f"{(v.get('observed') or v.get('message') or '')[:160]}"
            for v in violations
        ]
        failures.append(
            f"validation_report: {total} violations > baseline {base} "
            f"(by_class={by_class}). Violations:\n      "
            + "\n      ".join(offending)
        )

    offenders = find_offenders(bm)
    remaining, _allowlisted = filter_by_allowlist(
        offenders, load_allowlist(ALLOWLIST),
    )
    rc_base = RUNTIME_COHERENCE_BASELINES[substrate]
    if len(remaining) > rc_base:
        rc_desc = [
            f"{o.partition_key} types={sorted(o.edge_types)} "
            f"edges={o.edge_count}"
            for o in remaining
        ]
        failures.append(
            f"runtime_coherence: {len(remaining)} un-allow-listed offenders "
            f"> baseline {rc_base}:\n      " + "\n      ".join(rc_desc)
        )

    assert not failures, (
        f"[{substrate}] G1 ratchet breached (shrink-only baselines live in "
        f"{Path(__file__).name}; ratchet DOWN on a shrink, never UP):\n"
        + "\n".join(failures)
    )
