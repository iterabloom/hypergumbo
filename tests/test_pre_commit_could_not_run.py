# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-ziguk: the pre-commit hook tells "out of date" from "could not run".

The hook ran each doc generator as ``--check >/dev/null 2>&1`` and printed
"<doc> is out of date" on any non-zero exit, so an import failure read as a
stale doc. These tests drive the hook's two helpers, extracted from the
shipped file, against stub scripts with known exit codes. Driving the whole
hook would run every other gate in it, and none of them is the subject here.
"""

# covers: .githooks/pre-commit
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / ".githooks" / "pre-commit"


def _function(name: str) -> str:
    text = HOOK.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
    assert match, f"{name} is gone from the hook"
    return match.group(0)


def _drive(tmp_path: Path, call: str, stub_rc: int = 0, stub_out: str = "") -> subprocess.CompletedProcess:
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    stub = scripts / "generate-x"
    stub.write_text(f"#!/usr/bin/env bash\necho '{stub_out}' >&2\nexit {stub_rc}\n")
    stub.chmod(0o755)
    script = "\n".join([
        "set -euo pipefail",
        "RED=''; GREEN=''; YELLOW=''; NC=''",
        _function("check_generated"),
        _function("require_module"),
        call,
        "echo AFTER",
    ])
    return subprocess.run(["bash", "-c", script], cwd=tmp_path,
                          capture_output=True, text=True)


def test_fresh_passes_and_the_hook_continues(tmp_path: Path) -> None:
    done = _drive(tmp_path, 'check_generated "Doc" generate-x')
    assert done.returncode == 0 and "AFTER" in done.stdout


def test_stale_blocks_as_out_of_date(tmp_path: Path) -> None:
    done = _drive(tmp_path, 'check_generated "Doc" generate-x', 1,
                  "ERROR: docs/x is out of date")
    assert done.returncode == 1 and "AFTER" not in done.stdout
    assert "Out of date" in done.stdout
    assert "docs/x is out of date" in done.stdout


@pytest.mark.parametrize("rc", [2, 3])
def test_could_not_run_blocks_without_claiming_staleness(tmp_path: Path, rc: int) -> None:
    done = _drive(tmp_path, 'check_generated "Doc" generate-x', rc,
                  "ModuleNotFoundError: No module named hypergumbo_core")
    assert done.returncode == 1 and "AFTER" not in done.stdout
    assert f"could not run (exit {rc})" in done.stdout
    assert "freshness is unknown" in done.stdout
    assert "No module named hypergumbo_core" in done.stdout
    assert "Out of date" not in done.stdout


def test_require_module_passes_an_importable_module(tmp_path: Path) -> None:
    done = _drive(tmp_path, 'require_module json "A check"')
    assert done.returncode == 0 and "AFTER" in done.stdout


def test_require_module_stops_with_the_real_reason(tmp_path: Path) -> None:
    done = _drive(tmp_path, 'require_module no_such_module_ziguk "A check"')
    assert done.returncode == 1 and "AFTER" not in done.stdout
    assert "A check cannot run: python3 cannot import no_such_module_ziguk" in done.stdout
    assert "source .venv/bin/activate" in done.stdout


def test_every_generator_check_goes_through_the_helper() -> None:
    text = HOOK.read_text(encoding="utf-8")
    for script in ("generate-schema", "generate-concept-axes", "generate-concepts"):
        assert re.search(rf'^check_generated "[^"]+" {script}$', text, re.MULTILINE), script
    assert not re.search(r"generate-[a-z-]+ --check >/dev/null", text), (
        "a generator check discards its output again, so 'stale' and "
        "'could not run' read alike"
    )


def test_the_module_guard_runs_only_where_its_check_runs() -> None:
    """Hook-wide, it would block commits that stage nothing needing the venv."""
    text = HOOK.read_text(encoding="utf-8")
    for module, check in (("hypergumbo_tracker", "scripts/tracker validate"),
                          ("hypergumbo_core", "scripts/check-confidence-sites")):
        guard = text.index(f"require_module {module} ")
        assert guard < text.index(check), module
        staged_test = text.rfind("if ", 0, guard)
        assert "--cached" in text[staged_test:guard] or "STAGED" in text[staged_test:guard], (
            f"require_module {module} is not inside its check's staged-files branch"
        )
