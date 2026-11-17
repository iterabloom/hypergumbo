# SPDX-License-Identifier: AGPL-3.0-or-later
"""``test_rct_public_api_pinned`` must not free-ride on a sibling's sys.path.

INV-vazuh. The RCT API-pinning tests import ``hypergumbo_core``, but the suite
they live in (top-level ``tests/``, run by the cron full-suite's
``test-agent-infra`` step) deliberately installs no hypergumbo package. They
passed anyway, for a reason that was not a reason: fourteen ``scripts/check-*``
and ``generate-*`` scripts do ``sys.path.insert(0, .../hypergumbo-core/src)`` at
module level, and sibling tests execute those scripts through
``SourceFileLoader``. The insert lands in the shared interpreter, so whether the
pinning tests could import anything came down to whether such a sibling happened
to run first in the same xdist worker.

Measured in a clean venv carrying only the four packages that step installs:

    pytest tests/                              ->  2 failed  (imports resolve)
    pytest tests/test_rct_public_api_pinned.py ->  8 failed  (they do not)

Same tree, same interpreter. That is a test suite whose result depends on
sharding, and it is exactly the shape that let a gate sit RED since 2026-08-11
while the tree was green everywhere else.

WHY THIS GUARD RUNS A SUBPROCESS. On a developer box every ``packages/*/src`` is
a literal ``sys.path`` entry from the editable install, so the pinning tests
import cleanly no matter what and an in-process assertion here would pass
whether or not the fix exists — a discriminator not unique to the change is not
a control. Scrubbing those entries in a child interpreter reproduces the CI
condition on a machine that is not CI, so this test fails if the declaration is
ever deleted.

THE SAME HOLDS FOR EVERY TEST IN THIS SUITE THAT IMPORTS hypergumbo_core. A
sweep of ``tests/`` on 2026-11-16 found six more that imported it from a test
body and declared nothing (each passed in the cron only because four sibling
modules put the core source on ``sys.path`` at collection time, in every
worker). They now declare it through ``tests/helpers_package_src.py``, and
each is run here the same way, by node id so only that test executes.
"""

# covers: tests/test_rct_public_api_pinned.py, tests/helpers_package_src.py, tests/test_check_allowlist_commands.py, tests/test_check_test_only_reachability_ci_wiring.py, tests/test_prepare_release_version_history.py, tests/test_refresh_stdlib_modules.py, tests/test_yaml_catalog_index_channels.py, tests/test_cohort_ledger.py

from __future__ import annotations

import subprocess  # nosec B404 - runs this repo's own pytest, no untrusted input
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET = "tests/test_rct_public_api_pinned.py"

#: Each test of this suite that imports hypergumbo_core from its body, as the
#: sweep found them; the module's other tests are not run.
SWEPT = (
    "tests/test_check_allowlist_commands.py"
    "::test_verify_claims_counts_as_run_on_its_verdict_codes_not_on_bad_input",
    "tests/test_check_test_only_reachability_ci_wiring.py"
    "::test_the_map_is_built_with_the_scip_python_backend",
    "tests/test_prepare_release_version_history.py"
    "::test_schema_read_matches_the_real_constant",
    "tests/test_refresh_stdlib_modules.py"
    "::test_python_writes_the_union_over_supported_interpreters",
    "tests/test_yaml_catalog_index_channels.py"
    "::test_internal_families_state_a_reason_a_human_can_read",
    "tests/test_cohort_ledger.py"
    "::test_run_cli_json_real_cli_missing_repo_is_absent_not_empty",
)


def _child(target: str) -> str:
    """Strip every in-repo package source root, then hand control to pytest.
    The target module is expected to put back the ONE root it actually needs."""
    return (
        "import sys;"
        "sys.path[:] = [p for p in sys.path if '/packages/' not in p];"
        "import pytest;"
        f"sys.exit(pytest.main(['-q', '-o', 'addopts=', '-p', 'no:cacheprovider', {target!r}]))"
    )


def _run_alone(target: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _child(target)],  # nosec B603 - fixed argv, no shell
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    combined = proc.stdout + proc.stderr
    assert "ModuleNotFoundError: No module named 'hypergumbo_core'" not in combined, (
        f"{target} could not import hypergumbo_core with the package "
        "source roots scrubbed — it is relying on another test having "
        "inserted the path first, which is not something it can rely on:\n"
        + combined[-3000:]
    )
    assert proc.returncode == 0, (
        f"{target} must pass run on its own (rc={proc.returncode}):\n"
        + combined[-3000:]
    )


def test_pinned_api_tests_import_without_a_sibling_having_run() -> None:
    _run_alone(TARGET)


@pytest.mark.parametrize("target", SWEPT)
def test_a_swept_test_imports_core_without_a_sibling_having_run(target: str) -> None:
    _run_alone(target)
