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

It scans BOTH the live pipelines (``.woodpecker/``) and the dormant GitHub
workflows. Until INV-hokin it scanned only ``.github/workflows/`` while its
``covers:`` line named ``.woodpecker/*.yml``, and its positive lock read the
dormant GitHub full-suite: the live self-tree step was pinned by nothing.
"""

# covers: .woodpecker/*.yml, .github/workflows/*.yml

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
WOODPECKER_DIR = REPO_ROOT / ".woodpecker"

# A shell command line whose FIRST token is the bare `hypergumbo` console
# command followed by a subcommand/flag, optionally as a YAML list item
# (`- hypergumbo run .`: the form every Woodpecker `commands:` entry takes; a
# pattern without the `- ` arm reads every one of them as clean). It matches
# `hypergumbo run .` and `- hypergumbo run .` but NOT:
#   - `python -m hypergumbo_core run .`  (line starts with `python`)
#   - `hypergumbo-core` / `hypergumbo_core`  (no whitespace after `hypergumbo`)
#   - `# runs hypergumbo against …`  (comment line starts with `#`)
#   - `pip install -e packages/hypergumbo`  (line starts with `pip`)
_BARE_HYPERGUMBO_CMD = re.compile(r"^\s*(?:-\s+)?hypergumbo\s+\S", re.MULTILINE)


def _workflow_files() -> list[Path]:
    return [
        f for d in (WOODPECKER_DIR, WORKFLOWS_DIR)
        for f in sorted(d.glob("*.yml")) + sorted(d.glob("*.yaml"))
    ]


def test_workflows_dir_exists_and_nonempty() -> None:
    """Sanity: the guard has workflows to scan -- the LIVE ones included."""
    files = _workflow_files()
    assert WOODPECKER_DIR / "full-suite.yml" in files, (
        f"the live cron pipeline is not among the scanned files: {files}"
    )
    assert any(f.parent == WORKFLOWS_DIR for f in files), (
        f"no workflow files under {WORKFLOWS_DIR}"
    )


def test_no_workflow_invokes_bare_hypergumbo_command() -> None:
    """No CI job may call the bare ``hypergumbo`` command (meta-pkg not installed)."""
    offenders: list[str] = []
    for wf in _workflow_files():
        text = wf.read_text()
        for m in _BARE_HYPERGUMBO_CMD.finditer(text):
            line = text[m.start():text.find("\n", m.start())].strip()
            line_no = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{wf.relative_to(REPO_ROOT)}:{line_no}: {line}")
    assert not offenders, (
        "CI jobs must invoke the CLI via `python -m hypergumbo_core` (the bare "
        "`hypergumbo` console script is unavailable — the meta-package is never "
        "installed in CI). Offending lines:\n  " + "\n  ".join(offenders)
    )


def test_self_tree_validation_uses_module_invocation() -> None:
    """Positive lock: the LIVE WI-jigup self-tree step uses
    `python -m hypergumbo_core` (its install set has no meta-package)."""
    steps = {
        s["name"]: s
        for s in yaml.safe_load((WOODPECKER_DIR / "full-suite.yml").read_text())["steps"]
    }
    assert "self-tree-validation" in steps, "WI-jigup step missing from the live cron"
    commands = steps["self-tree-validation"].get("commands") or []
    assert any(c.startswith("python -m hypergumbo_core run .") for c in commands), (
        "self-tree-validation must generate its behavior map via "
        f"`python -m hypergumbo_core run .`; commands: {commands}"
    )
