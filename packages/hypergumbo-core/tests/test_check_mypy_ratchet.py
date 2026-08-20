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


class TestOutputReconciliation:
    """The parse must account for mypy's own tally, or refuse to rule.

    Found 2026-08-05, live: with ``FORCE_COLOR`` in the environment mypy
    colorizes its output, the trailing-``[code]`` regex matches nothing, and
    the ratchet reported ``0 (baseline 672, delta -672)`` with exit 0 — a
    fail-OPEN, complete with advice to run ``--update-baseline``, which would
    have written 0 into the baseline and permanently disabled the gate. The
    remedy is default-deny (L54): enumerate the two readable shapes — a
    ``Found N errors`` tally matching the parsed count, or a ``Success`` line
    with a parsed count of zero — and exit 2 (infrastructure, never a verdict)
    on everything else. ANSI escapes are stripped before parsing so the
    colorized form is READ rather than refused.
    """

    @staticmethod
    def _colorized(codes: dict[str, int]) -> str:
        """Fabricate mypy stdout exactly as it looks under FORCE_COLOR.

        Mirrors the live capture: the ``error:`` token and the trailing
        ``[code]`` are each wrapped in SGR sequences, and every line ends
        with ``ESC ( B ESC [ m`` — the charset-reset + reset pair that
        defeats a ``\\[([a-z-]+)\\]\\s*$`` anchor.
        """
        lines: list[str] = []
        n = 0
        for code, count in codes.items():
            for _ in range(count):
                n += 1
                lines.append(
                    f"pkg/mod.py:{n}: \x1b[1m\x1b[31merror:\x1b(B\x1b[m "
                    f"something is wrong  \x1b[33m[{code}]\x1b(B\x1b[m"
                )
        lines.append(
            f"\x1b[1m\x1b[31mFound {n} errors in 1 file "
            f"(checked 1 source file)\x1b(B\x1b[m"
        )
        return "\n".join(lines) + "\n"

    def _run_raw(self, tmp_path, raw: str, baseline_codes, mode="strict"):
        out_file = tmp_path / "mypy.txt"
        out_file.write_text(raw)
        base_file = tmp_path / "baseline.json"
        base_file.write_text(json.dumps(
            {"total": sum(baseline_codes.values()),
             "by_code": dict(baseline_codes)},
        ))
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(out_file),
             "--baseline", str(base_file), "--mode", mode],
            capture_output=True, text=True,
        )

    def test_colorized_output_is_read_not_zeroed(self, tmp_path):
        """The exact live failure: colorized errors must still count."""
        proc = self._run_raw(
            tmp_path, self._colorized({"arg-type": 2}), {"arg-type": 2},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "delta +0" in proc.stdout

    def test_colorized_regression_still_blocks(self, tmp_path):
        """Colour must not weaken the gate in the blocking direction either."""
        proc = self._run_raw(
            tmp_path, self._colorized({"arg-type": 3}), {"arg-type": 2},
        )
        assert proc.returncode == 1
        assert "REGRESSION" in proc.stderr

    def test_tally_mismatch_is_infra_error(self, tmp_path):
        """A parse that disagrees with mypy's own tally is a broken
        instrument, not a surface reading — exit 2, never a verdict."""
        raw = (
            "pkg/mod.py:1: error: something is wrong  [arg-type]\n"
            "Found 7 errors in 1 file (checked 1 source file)\n"
        )
        proc = self._run_raw(tmp_path, raw, {"arg-type": 1})
        assert proc.returncode == 2
        assert "7" in proc.stderr and "1" in proc.stderr

    def test_output_without_tally_or_success_is_infra_error(self, tmp_path):
        """Truncated / crashed / unrecognizable output must refuse, not
        read as zero errors (the fail-open's other route: rc=2 with empty
        or partial stdout)."""
        proc = self._run_raw(tmp_path, "mypy: error: unrecognized crash\n", {})
        assert proc.returncode == 2

    def test_empty_output_is_infra_error(self, tmp_path):
        proc = self._run_raw(tmp_path, "", {})
        assert proc.returncode == 2

    def test_genuine_success_line_reads_as_zero(self, tmp_path):
        """The eventual INV-zogud end state must stay readable: a real
        'Success' line with nothing parsed is a legitimate 0."""
        proc = self._run_raw(
            tmp_path, "Success: no issues found in 324 source files\n", {},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "errors = 0" in proc.stdout

    def test_success_line_with_error_lines_is_infra_error(self, tmp_path):
        """Contradictory output (a Success line above parsed errors) is
        unreadable — refuse rather than pick a side."""
        raw = (
            "Success: no issues found in 1 source file\n"
            "pkg/mod.py:1: error: something is wrong  [arg-type]\n"
        )
        proc = self._run_raw(tmp_path, raw, {"arg-type": 1})
        assert proc.returncode == 2

    def test_update_baseline_refuses_unreconciled_output(self, tmp_path):
        """The poisoning route: --update-baseline over unparseable output
        would write total 0 and disable the gate permanently."""
        out_file = tmp_path / "mypy.txt"
        out_file.write_text("garbage that parses to zero errors\n")
        base_file = tmp_path / "baseline.json"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(out_file),
             "--baseline", str(base_file), "--update-baseline"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 2
        assert not base_file.exists(), "a baseline was written from garbage"


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
