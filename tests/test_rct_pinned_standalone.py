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
"""

from __future__ import annotations

import subprocess  # nosec B404 - runs this repo's own pytest, no untrusted input
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET = "tests/test_rct_public_api_pinned.py"

# Strip every in-repo package source root, then hand control to pytest. The
# target module is expected to put back the ONE root it actually needs.
_CHILD = (
    "import sys;"
    "sys.path[:] = [p for p in sys.path if '/packages/' not in p];"
    "import pytest;"
    f"sys.exit(pytest.main(['-q', '-o', 'addopts=', '-p', 'no:cacheprovider', {TARGET!r}]))"
)


def test_pinned_api_tests_import_without_a_sibling_having_run() -> None:
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _CHILD],  # nosec B603 - fixed argv, no shell
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    combined = proc.stdout + proc.stderr
    assert "ModuleNotFoundError: No module named 'hypergumbo_core'" not in combined, (
        "the pinning tests could not import hypergumbo_core with the package "
        "source roots scrubbed — they are relying on another test having "
        "inserted the path first, which is not something they can rely on:\n"
        + combined[-3000:]
    )
    assert proc.returncode == 0, (
        f"{TARGET} must pass run on its own (rc={proc.returncode}):\n"
        + combined[-3000:]
    )
