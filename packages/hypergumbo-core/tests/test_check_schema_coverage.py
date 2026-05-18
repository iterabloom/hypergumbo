# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/check-schema-coverage`` (WI-luzuh).

The gate's behaviour contract:

* ``--mode=warning`` (default) — tolerate the baseline-recorded uncovered
  set; loud-fail on any positive delta (a value uncovered now that was
  not in the baseline).
* ``--mode=fail`` — fail on any uncovered value, regardless of baseline.
* ``--update-baseline`` — rewrite the baseline file to the current
  uncovered set (the ratchet step).

These tests pin those modes by constructing fake corpus output JSON,
fake baselines, and running the script as a subprocess with ``--input``
overriding the corpus run. The subprocess form is intentional — the
script is a runnable file at ``scripts/check-schema-coverage`` and
testing via ``main(argv)`` would miss argv-parsing regressions and
PATH problems.

Two construction helpers (`_make_output_with_observed_values` and
`_make_baseline`) build the minimum-viable JSON for the gate to see
specific (kind / edge_type / evidence_type) sets. Tests use these
to set up "what the corpus produced" and "what the baseline
recorded" independently, so the four cells of the truth table —
{baseline matches, baseline says less, baseline says more, no
baseline} x {mode=warning, mode=fail} -- can be exercised
deterministically without invoking hypergumbo at all.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT_REAL = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT_REAL / "scripts" / "check-schema-coverage"


def _make_output_with_observed_values(
    *,
    kinds: list[str],
    edge_types: list[str],
    evidence_types: list[str],
) -> dict:
    """Build a minimal behavior-map JSON with the given observed values."""
    nodes = [
        {"id": f"x:{i}", "kind": k} for i, k in enumerate(kinds)
    ]
    edges = []
    for i, et in enumerate(edge_types):
        edges.append({
            "id": f"e:t:{i}",
            "type": et,
            "src": "x:0", "dst": "x:0",
        })
    for i, ev in enumerate(evidence_types):
        edges.append({
            "id": f"e:v:{i}",
            "type": "calls",  # placeholder edge_type
            "src": "x:0", "dst": "x:0",
            "meta": {"evidence_type": ev},
        })
    return {
        "nodes": nodes,
        "edges": edges,
    }


def _make_baseline(
    *,
    kinds: list[str] | None = None,
    edge_types: list[str] | None = None,
    evidence_types: list[str] | None = None,
) -> dict:
    return {
        "uncovered_kinds": kinds or [],
        "uncovered_edge_types": edge_types or [],
        "uncovered_evidence_types": evidence_types or [],
    }


def _run_gate(
    input_json: Path,
    *args: str,
    baseline_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the gate script in a subprocess with a tmp baseline file.

    The script reads ``.ci/schema-coverage-baseline.json`` relative to
    its own location, which is the real repo root. We use ``--input``
    to bypass the hypergumbo subprocess, but the baseline path is
    fixed. So tests symlink-or-copy the real baseline aside, install a
    test baseline, then restore. Simpler: run with HYPERGUMBO_TEST_BASELINE
    env var if the script supports it.

    Until the script supports an override flag, the workaround is to
    copy the real baseline aside and write our own at the path. Tests
    do this with a context-manager helper below.
    """
    env = dict(os.environ)
    cmd = [sys.executable, str(SCRIPT_PATH), "--input", str(input_json), *args]
    return subprocess.run(
        cmd, env=env, capture_output=True, text=True, check=False,
    )


class _BaselineSwap:
    """Context manager: temporarily replace the real baseline file."""

    def __init__(self, replacement: dict | None):
        self.replacement = replacement
        self.real_path = REPO_ROOT_REAL / ".ci" / "schema-coverage-baseline.json"
        self.backup: bytes | None = None
        self.had_file: bool = False

    def __enter__(self) -> "_BaselineSwap":
        self.had_file = self.real_path.exists()
        if self.had_file:
            self.backup = self.real_path.read_bytes()
        if self.replacement is None:
            if self.had_file:
                self.real_path.unlink()
        else:
            self.real_path.parent.mkdir(parents=True, exist_ok=True)
            self.real_path.write_text(json.dumps(self.replacement, indent=2))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.had_file:
            assert self.backup is not None
            self.real_path.write_bytes(self.backup)
        elif self.real_path.exists():
            self.real_path.unlink()


class TestWarningMode:
    """``--mode=warning`` tolerates the baseline; fails on positive delta."""

    def test_baseline_matches_observed_uncovered_passes(
        self, tmp_path: Path,
    ) -> None:
        """When every uncovered value is in the baseline, gate passes."""
        # Observed: only 'function' kind. Baseline records every OTHER
        # registry value as tolerated — should match exactly, no delta.
        # We can't enumerate the registry here without importing it, so
        # we test a narrower property: a baseline that matches the current
        # uncovered set (whatever it is) passes the warning mode.
        observed = _make_output_with_observed_values(
            kinds=["function"],
            edge_types=["calls"],
            evidence_types=["ast_call_direct"],
        )
        input_json = tmp_path / "obs.json"
        input_json.write_text(json.dumps(observed))

        # First, generate a baseline that matches this observation.
        with _BaselineSwap(None):
            update_result = _run_gate(input_json, "--update-baseline")
            assert update_result.returncode == 0, update_result.stderr
            # Now re-run in warning mode against the same observation —
            # baseline matches, gate must pass.
            check_result = _run_gate(input_json)
            assert check_result.returncode == 0, (
                check_result.stdout + check_result.stderr
            )
            assert "OK (warning mode)" in check_result.stdout

    def test_new_uncovered_value_fails(self, tmp_path: Path) -> None:
        """Observation regresses (covers fewer values than baseline records) → FAIL."""
        # Step 1: observation that covers {function, calls, ast_call_direct}
        observed_rich = _make_output_with_observed_values(
            kinds=["function", "class", "method"],
            edge_types=["calls", "imports"],
            evidence_types=["ast_call_direct", "ast_import"],
        )
        # Step 2: observation that covers fewer values
        observed_lean = _make_output_with_observed_values(
            kinds=["function"],
            edge_types=["calls"],
            evidence_types=["ast_call_direct"],
        )
        rich_json = tmp_path / "rich.json"
        rich_json.write_text(json.dumps(observed_rich))
        lean_json = tmp_path / "lean.json"
        lean_json.write_text(json.dumps(observed_lean))

        with _BaselineSwap(None):
            # Set baseline from rich observation
            update = _run_gate(rich_json, "--update-baseline")
            assert update.returncode == 0, update.stderr
            # Check lean observation against baseline → positive delta
            check = _run_gate(lean_json)
            assert check.returncode == 1, (
                check.stdout + check.stderr
            )
            assert "FAIL (warning mode)" in check.stderr
            # Specifically class, method, imports, ast_import should be
            # flagged as new uncovered.
            assert "class" in check.stderr or "method" in check.stderr
            assert "imports" in check.stderr
            assert "ast_import" in check.stderr

    def test_newly_covered_value_passes_with_hint(self, tmp_path: Path) -> None:
        """Observation covers more than baseline (negative delta) → PASS + hint."""
        observed_lean = _make_output_with_observed_values(
            kinds=["function"],
            edge_types=["calls"],
            evidence_types=["ast_call_direct"],
        )
        observed_rich = _make_output_with_observed_values(
            kinds=["function", "class"],
            edge_types=["calls", "imports"],
            evidence_types=["ast_call_direct", "ast_import"],
        )
        lean_json = tmp_path / "lean.json"
        lean_json.write_text(json.dumps(observed_lean))
        rich_json = tmp_path / "rich.json"
        rich_json.write_text(json.dumps(observed_rich))

        with _BaselineSwap(None):
            update = _run_gate(lean_json, "--update-baseline")
            assert update.returncode == 0, update.stderr
            check = _run_gate(rich_json)
            assert check.returncode == 0, (
                check.stdout + check.stderr
            )
            assert "newly covered" in check.stdout
            assert "--update-baseline" in check.stdout


class TestFailMode:
    """``--mode=fail`` fails on any uncovered value, regardless of baseline."""

    def test_fail_mode_with_uncovered_values_fails(self, tmp_path: Path) -> None:
        """Even with baseline tolerating all gaps, fail mode rejects them."""
        observed = _make_output_with_observed_values(
            kinds=["function"],
            edge_types=["calls"],
            evidence_types=["ast_call_direct"],
        )
        input_json = tmp_path / "obs.json"
        input_json.write_text(json.dumps(observed))

        with _BaselineSwap(None):
            # Baseline contains everything currently uncovered — warning
            # mode would pass.
            update = _run_gate(input_json, "--update-baseline")
            assert update.returncode == 0, update.stderr
            # Fail mode ignores baseline and rejects.
            fail = _run_gate(input_json, "--mode=fail")
            assert fail.returncode == 1, fail.stdout + fail.stderr
            assert "FAIL (fail)" in fail.stderr


class TestUpdateBaseline:
    """``--update-baseline`` rewrites the baseline file deterministically."""

    def test_update_baseline_writes_sorted_dedup(self, tmp_path: Path) -> None:
        observed = _make_output_with_observed_values(
            kinds=["function", "class"],
            edge_types=["calls"],
            evidence_types=["ast_call_direct"],
        )
        input_json = tmp_path / "obs.json"
        input_json.write_text(json.dumps(observed))

        baseline_path = REPO_ROOT_REAL / ".ci" / "schema-coverage-baseline.json"
        with _BaselineSwap(None):
            r = _run_gate(input_json, "--update-baseline")
            assert r.returncode == 0, r.stderr
            assert baseline_path.is_file()
            data = json.loads(baseline_path.read_text())
            assert isinstance(data["uncovered_kinds"], list)
            assert data["uncovered_kinds"] == sorted(set(data["uncovered_kinds"]))
            # "function" and "class" should NOT be in uncovered (they're
            # observed); but the assertion is more useful in the contrapositive
            # — registry values that ARE observed shouldn't appear.
            assert "function" not in data["uncovered_kinds"]
            assert "class" not in data["uncovered_kinds"]


class TestMissingInputFile:
    """``--input`` pointing at a non-existent file exits 2 (env error)."""

    def test_missing_input_returns_2(self, tmp_path: Path) -> None:
        bogus = tmp_path / "does-not-exist.json"
        result = _run_gate(bogus)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "not found" in result.stderr.lower()
