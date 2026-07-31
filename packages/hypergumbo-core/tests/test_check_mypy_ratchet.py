# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/check-mypy-ratchet`` (WI-rokup / INV-zogud).

The whole-tree strict ratchet's behaviour contract:

* ``--mode=warning`` (default) — report current vs baseline; exit 0 even on
  a regression (the non-blocking CI rung).
* ``--mode=strict`` — exit 1 on a regression: the total grew, an existing
  error code's count grew, or a NEW error code appeared. WI-rabum flips CI
  to this once the surface drains.
* ``--update-baseline`` — rewrite the baseline to the current run.

These pin the truth table by feeding fabricated mypy output via ``--input``
and a fabricated baseline via ``--baseline``, running the script as a
subprocess. The subprocess + ``--input`` form (mirroring
``test_check_schema_coverage``) exercises argv parsing and the ratchet
logic deterministically WITHOUT invoking a real (slow) mypy type-check.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check-mypy-ratchet"


def _mypy_output(codes: dict[str, int]) -> str:
    """Fabricate mypy stdout with ``count`` error lines per ``[code]``."""
    lines: list[str] = []
    n = 0
    for code, count in codes.items():
        for _ in range(count):
            n += 1
            lines.append(f"pkg/mod.py:{n}: error: something is wrong [{code}]")
    lines.append(f"Found {n} errors in 1 file (checked 1 source file)")
    return "\n".join(lines) + "\n"


def _run(
    tmp_path, *, codes, baseline_codes, mode="warning", update=False,
    baseline_version=None,
):
    out_file = tmp_path / "mypy.txt"
    out_file.write_text(_mypy_output(codes))
    base_file = tmp_path / "baseline.json"
    body = {"total": sum(baseline_codes.values()), "by_code": dict(baseline_codes)}
    if baseline_version is not None:
        body["mypy_version"] = baseline_version
    base_file.write_text(json.dumps(body))
    argv = [
        sys.executable,
        str(SCRIPT),
        "--input",
        str(out_file),
        "--baseline",
        str(base_file),
    ]
    if update:
        argv.append("--update-baseline")
    else:
        argv += ["--mode", mode]
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc, base_file


def test_clean_matches_baseline(tmp_path):
    proc, _ = _run(
        tmp_path, codes={"arg-type": 5, "type-arg": 3},
        baseline_codes={"arg-type": 5, "type-arg": 3},
    )
    assert proc.returncode == 0
    assert "delta +0" in proc.stdout


def test_shrink_is_reported_and_passes(tmp_path):
    proc, _ = _run(tmp_path, codes={"arg-type": 2}, baseline_codes={"arg-type": 5})
    assert proc.returncode == 0
    assert "shrank" in proc.stdout


def test_regression_warning_mode_exits_zero(tmp_path):
    proc, _ = _run(
        tmp_path, codes={"arg-type": 7}, baseline_codes={"arg-type": 5}, mode="warning"
    )
    assert proc.returncode == 0
    assert "REGRESSION" in proc.stderr
    assert "arg-type: 5 -> 7" in proc.stderr


def test_regression_strict_mode_exits_one(tmp_path):
    proc, _ = _run(
        tmp_path, codes={"arg-type": 7}, baseline_codes={"arg-type": 5}, mode="strict"
    )
    assert proc.returncode == 1
    assert "REGRESSION" in proc.stderr


def test_new_error_code_is_a_regression(tmp_path):
    proc, _ = _run(
        tmp_path,
        codes={"arg-type": 5, "union-attr": 1},
        baseline_codes={"arg-type": 5},
        mode="strict",
    )
    assert proc.returncode == 1
    assert "union-attr" in proc.stderr
    assert "new code" in proc.stderr


def test_update_baseline_writes_current_run(tmp_path):
    proc, base_file = _run(
        tmp_path, codes={"arg-type": 4, "index": 2},
        baseline_codes={"arg-type": 9}, update=True,
    )
    assert proc.returncode == 0
    written = json.loads(base_file.read_text())
    assert written["total"] == 6
    assert written["by_code"] == {"arg-type": 4, "index": 2}


def test_missing_baseline_is_infra_error_not_regression(tmp_path):
    out_file = tmp_path / "mypy.txt"
    out_file.write_text(_mypy_output({"arg-type": 1}))
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--input", str(out_file),
            "--baseline", str(tmp_path / "does-not-exist.json"), "--mode", "strict",
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2


def test_invalid_baseline_json_is_infra_error(tmp_path):
    out_file = tmp_path / "mypy.txt"
    out_file.write_text(_mypy_output({"arg-type": 1}))
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not valid json")
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--input", str(out_file),
            "--baseline", str(bad), "--mode", "strict",
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2


class TestInstrumentVersionIsPartOfTheMeasurement:
    """WI-rabum: a shrink-only baseline is only meaningful against a FIXED mypy.

    The counts in the baseline were produced by one mypy version. Comparing
    them against counts from a different version is not a ratchet reading at
    all — a categorisation change moves numbers with no code change, and the
    ratchet would report it as several unrelated error codes "growing", which
    is both wrong and the single most confusing way to fail.

    So the version is recorded with the counts and checked on every read. The
    check is skipped when the baseline carries no version, so baselines
    written before this existed keep working rather than hard-failing.
    """

    def test_update_baseline_records_the_mypy_version(self, tmp_path):
        """The instrument is part of the measurement, so it is written down."""
        proc, base_file = _run(
            tmp_path, codes={"arg-type": 2}, baseline_codes={}, update=True,
        )
        assert proc.returncode == 0, proc.stderr
        body = json.loads(base_file.read_text())
        assert body["mypy_version"], "baseline must record the mypy that produced it"

    def test_matching_version_compares_normally(self, tmp_path):
        """A version match is silent — it is the ordinary case."""
        import mypy.version

        proc, _ = _run(
            tmp_path,
            codes={"arg-type": 2},
            baseline_codes={"arg-type": 5},
            mode="strict",
            baseline_version=mypy.version.__version__,
        )
        assert proc.returncode == 0, proc.stderr
        assert "instrument" not in proc.stderr.lower()

    def test_version_mismatch_is_reported_in_warning_mode(self, tmp_path):
        """Non-blocking rung still says the comparison is invalid."""
        proc, _ = _run(
            tmp_path,
            codes={"arg-type": 2},
            baseline_codes={"arg-type": 5},
            mode="warning",
            baseline_version="0.0.1-not-a-real-version",
        )
        assert proc.returncode == 0, proc.stderr
        combined = proc.stdout + proc.stderr
        assert "0.0.1-not-a-real-version" in combined
        assert "--update-baseline" in combined

    def test_version_mismatch_blocks_in_strict_mode(self, tmp_path):
        """Strict mode refuses to render a verdict from two instruments.

        It fails even though the counts SHRANK (2 vs 5): a shrink measured by
        a different mypy is not evidence of progress, and silently accepting
        it would let a categorisation change be banked as a drain.
        """
        proc, _ = _run(
            tmp_path,
            codes={"arg-type": 2},
            baseline_codes={"arg-type": 5},
            mode="strict",
            baseline_version="0.0.1-not-a-real-version",
        )
        assert proc.returncode == 1
        assert "0.0.1-not-a-real-version" in proc.stderr

    def test_versionless_baseline_still_compares(self, tmp_path):
        """Backward compatibility: no recorded version means no check."""
        proc, _ = _run(
            tmp_path,
            codes={"arg-type": 2},
            baseline_codes={"arg-type": 5},
            mode="strict",
        )
        assert proc.returncode == 0, proc.stderr
