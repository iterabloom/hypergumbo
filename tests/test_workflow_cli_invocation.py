# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard: CI workflow jobs must not invoke the bare ``hypergumbo`` console command.

The ``hypergumbo`` console-script entry point lives in the **meta-package**
(``packages/hypergumbo/pyproject.toml`` → ``[project.scripts]``), which CI
never installs because it pulls PyTorch/CUDA. Every CI job installs the
component packages individually (``pip install -e packages/hypergumbo-core …``),
so the bare ``hypergumbo`` command is **not on PATH** — invoking it fails with
exit 127 ("command not found").

This bit the ``full-suite.yml`` self-tree-validation ratchet (WI-jigup), whose
``hypergumbo run .`` generation step 127'd. The fix — and the established
pattern (``scripts/check-schema-coverage`` runs
``sys.executable -m hypergumbo_core run``) — is to invoke via
``python -m hypergumbo_core``.

This test greps every workflow for a shell line that starts with the bare
``hypergumbo`` command (e.g. ``hypergumbo run .``) and fails if any exist, so
the class of regression cannot silently reappear in a periodic-only job that
push CI never exercises.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# A shell command line whose FIRST token is the bare `hypergumbo` console
# command followed by a subcommand/flag. `^\s*hypergumbo\s+\S` matches
# `hypergumbo run .` but NOT:
#   - `python -m hypergumbo_core run .`  (line starts with `python`)
#   - `hypergumbo-core` / `hypergumbo_core`  (no whitespace after `hypergumbo`)
#   - `# runs hypergumbo against …`  (comment line starts with `#`)
#   - `pip install -e packages/hypergumbo`  (line starts with `pip`)
_BARE_HYPERGUMBO_CMD = re.compile(r"^\s*hypergumbo\s+\S", re.MULTILINE)


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))


def test_workflows_dir_exists_and_nonempty() -> None:
    """Sanity: the guard has workflows to scan."""
    assert _workflow_files(), f"no workflow files under {WORKFLOWS_DIR}"


def test_no_workflow_invokes_bare_hypergumbo_command() -> None:
    """No CI job may call the bare ``hypergumbo`` command (meta-pkg not installed)."""
    offenders: list[str] = []
    for wf in _workflow_files():
        text = wf.read_text()
        for m in _BARE_HYPERGUMBO_CMD.finditer(text):
            line = text[m.start():text.find("\n", m.start())].strip()
            line_no = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{wf.name}:{line_no}: {line}")
    assert not offenders, (
        "CI jobs must invoke the CLI via `python -m hypergumbo_core` (the bare "
        "`hypergumbo` console script is unavailable — the meta-package is never "
        "installed in CI). Offending lines:\n  " + "\n  ".join(offenders)
    )


def test_self_tree_validation_uses_module_invocation() -> None:
    """Positive lock: the WI-jigup self-tree job uses `python -m hypergumbo_core`."""
    full_suite = WORKFLOWS_DIR / "full-suite.yml"
    text = full_suite.read_text()
    assert "Self-tree validation ratchet" in text, "WI-jigup job missing"
    assert "python -m hypergumbo_core run ." in text, (
        "self-tree-validation must generate its behavior map via "
        "`python -m hypergumbo_core run .`"
    )
