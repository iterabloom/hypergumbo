# SPDX-License-Identifier: AGPL-3.0-or-later
"""A test the cron's agent-infra step skips must still run somewhere.

``test-agent-infra`` runs the top-level ``tests/`` with NO hypergumbo package
installed, on purpose: that is how it caught tests importing hypergumbo_core
off a ``sys.path`` entry a sibling test leaked (INV-vazuh, in the step's own
comment). A top-level test whose subject needs the installed packages
(``tests/test_score_readability.py`` loads a script that imports tree_sitter
and hypergumbo_core) cannot pass there, and installing the packages into the
step would retire the premise. So such a test is ``--ignore``d there and run
by ``test-all-packages``, which installs everything.

The guard: every ``--ignore`` on the agent-infra pytest names a file that
``test-all-packages`` runs. An ignore without the matching run would drop the
test from the cron silently.
"""

# covers: .woodpecker/full-suite.yml
from __future__ import annotations

import re
from pathlib import Path

# A HARD import: importorskip could only make this guard skip silently.
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
FULL_SUITE = REPO_ROOT / ".woodpecker" / "full-suite.yml"


def _commands(step_name: str) -> str:
    steps = yaml.safe_load(FULL_SUITE.read_text())["steps"]
    step = next(s for s in steps if s["name"] == step_name)
    return "\n".join(step["commands"])


def _ignored() -> list[str]:
    return re.findall(r"--ignore=(\S+)", _commands("test-agent-infra"))


def test_the_readability_test_is_ignored_where_nothing_is_installed() -> None:
    assert "tests/test_score_readability.py" in _ignored()


def test_every_ignored_test_runs_where_the_packages_are_installed() -> None:
    packages = _commands("test-all-packages")
    assert "pip install -e packages/hypergumbo-core" in packages
    runs = re.findall(r"^\s*pytest\b.*$", packages, re.MULTILINE)
    for path in _ignored():
        assert (REPO_ROOT / path).is_file(), f"--ignore names a missing file: {path}"
        assert any(path in line for line in runs), f"{path} is ignored by test-agent-infra and run nowhere"
