# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kalub Step 4: `check-package-coverage` must check all six packages.

`scripts/check-package-coverage` mimics CI's per-package coverage isolation (the
same bar the full-suite `last-green-sha` marker applies: every package at exactly
100% in isolation). It declares `rust-analyzer` in its `PACKAGES` map, but the
option-b investigation found the default target list and the arg parser both
omitted it — so the local parity tool silently skipped one of the six
marker-required packages, and a rust-analyzer coverage gap would not surface
locally before pushing. These static guards pin that rust-analyzer is both
runnable by default and selectable by name.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-package-coverage"


def _text() -> str:
    return SCRIPT.read_text()


def test_script_present() -> None:
    assert SCRIPT.is_file(), f"check-package-coverage not found at {SCRIPT}"


def test_rust_analyzer_in_packages_map() -> None:
    """Positive lock: the PACKAGES map declares rust-analyzer (its test/src dirs)."""
    assert re.search(r'\["rust-analyzer"\]=', _text()), (
        "the PACKAGES map no longer declares rust-analyzer"
    )


def test_rust_analyzer_in_default_targets() -> None:
    """The default 'all packages' TARGETS list must include rust-analyzer, so a
    bare `check-package-coverage` verifies all six marker-required packages."""
    m = re.search(r'TARGETS=\("core"[^)]*\)', _text())
    assert m, 'could not find the default TARGETS=("core" …) list'
    assert "rust-analyzer" in m.group(0), (
        "check-package-coverage's default TARGETS omits rust-analyzer, so the local "
        "per-package parity check silently skips one of the six packages the "
        "full-suite last-green marker requires. Add it to the default list "
        f"(WI-kalub Step 4). Default list found:\n  {m.group(0)}"
    )


def test_rust_analyzer_accepted_as_arg() -> None:
    """The arg parser must accept `rust-analyzer` as a package selector (else it
    hits the `Unknown argument; exit 1` catch-all)."""
    accept_lines = [ln for ln in _text().splitlines() if 'TARGETS+=("$arg")' in ln]
    assert accept_lines, 'no `TARGETS+=("$arg")` accept line found'
    assert any("rust-analyzer" in ln for ln in accept_lines), (
        "check-package-coverage's arg parser does not accept `rust-analyzer` as a "
        "package selector — `check-package-coverage rust-analyzer` would fail with "
        "`Unknown argument`. Add it to the case alternation (WI-kalub Step 4). "
        "Accept line(s):\n  " + "\n  ".join(accept_lines)
    )


def _extract(name: str) -> str:
    lines = _text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"{name}() {{"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1])


def test_a_package_run_that_collected_nothing_is_not_called_a_gap() -> None:
    """INV-tazur analogue: exit 5 ("no tests ran") is not a coverage gap.

    The real ``report_failure`` is extracted and driven against a log of the
    shape pytest writes when it collects nothing. It used to print "Coverage
    gaps for <pkg>:" over an empty list, followed by advice to move tests
    between packages.
    """
    import subprocess
    import uuid

    pkg = f"inv-tazur-{uuid.uuid4().hex[:8]}"
    log = Path(f"/tmp/pkg-cov-{pkg}.log")
    log.write_text("\nno tests ran in 0.42s\n")
    try:
        script = (
            _extract("collected_nothing") + "\n"
            + _extract("report_failure") + "\n"
            + f"report_failure {pkg}\n"
        )
        out = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=True,
        ).stdout
    finally:
        log.unlink()
    assert "collected 0 tests" in out, out
    assert "Coverage gaps" not in out, out
